"""转录：优先 RTX Worker（GPU，开机时），否则本机 CPU faster-whisper。"""
from __future__ import annotations

import os
import threading
from pathlib import Path

import requests

import glossary

_model = None
_model_key = None
_lock = threading.Lock()

ZH_PROMPT = "以下是普通话的句子，使用简体中文和标点符号。"


def _t2s(segments: list[dict]) -> list[dict]:
    try:
        from opencc import OpenCC
        cc = OpenCC("t2s")
    except Exception:  # noqa: BLE001
        return segments
    for s in segments:
        s["text"] = cc.convert(s["text"])
    return segments


def rtx_check(base: str) -> tuple[bool, str]:
    """检查 RTX Worker，返回 (能不能用, 不能用的原因)。
    2026-11-06：原来不能用时直接改用 CPU，日志不写原因，看不出为什么变慢。"""
    if not base:
        return False, "config.json 没有设置 rtx_worker_url"
    url = base.rstrip("/") + "/health"
    try:
        r = requests.get(url, timeout=3)
    except requests.Timeout:
        return False, f"{url} 3 秒内没有响应（RTX 电脑可能关机、睡眠，或 Tailscale 没连上）"
    except requests.ConnectionError:
        return False, f"连不上 {url}（RTX 电脑可能关机、睡眠，或 Worker 没启动）"
    except Exception as exc:  # noqa: BLE001
        return False, f"检查 {url} 出错：{type(exc).__name__}: {exc}"
    if not r.ok:
        return False, f"{url} 返回 HTTP {r.status_code}"
    try:
        ready = bool(r.json().get("ready"))
    except Exception:  # noqa: BLE001
        return False, f"{url} 返回的不是正常的 JSON：{(r.text or '')[:100]}"
    if not ready:
        return False, f"Worker 在线但还没准备好（ready=false，可能在加载模型）：{(r.text or '')[:150]}"
    return True, ""


def rtx_ready(base: str) -> bool:
    return rtx_check(base)[0]


AUDIO_EXT = {".wav", ".flac", ".mp3", ".m4a", ".ogg", ".opus"}


def _audio_for_rtx(path: Path) -> tuple[Path, bool]:
    """视频先抽成 16 kHz 单声道 WAV 再发给 RTX：上传更小，也绕开 RTX 端解码视频失败（2026-11-04 mp4 稳定 500，wav 正常）。
    返回 (要上传的文件, 是否为临时文件)；抽取失败就退回原文件。"""
    if path.suffix.lower() in AUDIO_EXT:
        return path, False
    import subprocess
    import media
    out = path.with_name(path.stem + ".rtx16k.wav")
    try:
        subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-vn",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)],
                       check=True, capture_output=True, timeout=600,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out, True
    except Exception:  # noqa: BLE001
        out.unlink(missing_ok=True)
        return path, False


def transcribe_rtx(path: Path, base: str, language: str | None) -> dict:
    up, temp = _audio_for_rtx(path)
    try:
        with up.open("rb") as fh:
            data = {"task": "transcribe"}
            if language:
                data["language"] = language
            r = requests.post(base.rstrip("/") + "/transcribe", files={"file": (up.name, fh)}, data=data, timeout=7200)
    finally:
        if temp:
            up.unlink(missing_ok=True)
    if r.status_code == 409:
        raise RuntimeError("RTX Worker 正忙（409）")
    if not r.ok:  # 把 RTX 返回的错误内容带进日志，方便排查
        raise RuntimeError(f"RTX Worker {r.status_code}（上传 {up.suffix}）：{(r.text or '')[:300]}")
    res = r.json()
    return {
        "engine": "RTX Worker faster-whisper（GPU）",
        "language": res.get("language"),
        "duration": res.get("duration"),
        "segments": res.get("segments") or [],
    }


def _get_model(name: str, threads: int):
    global _model, _model_key
    from faster_whisper import WhisperModel
    key = (name, threads)
    if _model is None or _model_key != key:
        _model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        _model_key = key
    return _model


def transcribe_local(path: Path, model_name: str, language: str | None, progress=None, log=print) -> dict:
    """本机转录；每 20 段存一次进度，电脑睡眠/重启后从断点继续。"""
    import json
    threads = max(1, (os.cpu_count() or 4))
    part = path.with_suffix(path.suffix + ".partial.json")
    segments: list[dict] = []
    if part.exists():
        try:
            segments = json.loads(part.read_text(encoding="utf-8"))["segments"]
        except Exception:  # noqa: BLE001
            segments = []
    # 续转：自己切掉已转部分再转（保留 VAD）。用 clip_timestamps 会关掉 VAD，
    # 实测断点后几秒会把提示词（专有名词表）当成转录内容输出。
    offset = 0.0
    audio_in = str(path)
    if segments:
        from faster_whisper.audio import decode_audio
        offset = float(segments[-1]["end"])
        audio_in = decode_audio(str(path), sampling_rate=16000)[int(offset * 16000):]
        log(f"发现上次进度，从 {int(offset)} 秒处继续")
        if len(audio_in) < 16000:  # 剩下不到 1 秒
            part.unlink(missing_ok=True)
            return {"engine": f"本机 faster-whisper {model_name}（CPU int8）", "language": language,
                    "duration": offset, "segments": segments}
    with _lock:
        model = _get_model(model_name, threads)
        seg_iter, info = model.transcribe(
            audio_in,
            language=language,
            beam_size=5,
            vad_filter=True,
            initial_prompt=(ZH_PROMPT + ("可能出现的词：" + glossary.hotwords(120) + "。" if glossary.hotwords(120) else ""))
            if (language or "").startswith("zh") else None,
            hotwords=glossary.hotwords() or None,
            condition_on_previous_text=False,
        )
        total = offset + float(getattr(info, "duration", 0) or 0)
        n = 0
        for s in seg_iter:
            segments.append({"start": round(s.start + offset, 2), "end": round(s.end + offset, 2),
                             "text": s.text.strip()})
            n += 1
            if progress and total:
                progress(min(99, int((s.end + offset) / total * 100)))
            if n % 20 == 0:
                part.write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
    part.unlink(missing_ok=True)
    return {
        "engine": f"本机 faster-whisper {model_name}（CPU int8）",
        "language": getattr(info, "language", language),
        "duration": total,
        "segments": segments,
    }


def transcribe(path: Path, cfg: dict, log=print, progress=None) -> dict:
    lang = cfg.get("language") or None
    mode = cfg.get("engine", "auto")
    base = cfg.get("rtx_worker_url", "")
    ok, why = rtx_check(base) if mode in ("auto", "rtx") else (False, f"config.json 的 engine 是 {mode}，不用 RTX")
    if ok:
        try:
            log("RTX Worker 在线，使用 GPU 转录")
            res = transcribe_rtx(path, base, lang)
            res["segments"] = glossary.apply(_t2s(res["segments"]))
            return res
        except Exception as exc:  # noqa: BLE001
            if mode == "rtx":
                raise
            log(f"RTX 转录失败，改用本机：{exc}")
    elif mode == "rtx":
        raise RuntimeError(f"RTX Worker 不能用：{why}")
    else:
        log(f"RTX 不能用，改用本机 CPU：{why}")
    log(f"本机 CPU 转录（模型 {cfg.get('whisper_model', 'small')}，首次使用会先下载模型）")
    res = transcribe_local(path, cfg.get("whisper_model", "small"), lang, progress, log)
    res["segments"] = glossary.apply(_t2s(res["segments"]))
    return res
