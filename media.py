"""视频画面处理：关键画面截图、画面字幕 OCR（纯字幕/无人声视频）。"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg") or "ffmpeg"


def has_video(src: Path) -> bool:
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NOWIN)
    return " Video:" in (p.stderr or "")


def has_audio(src: Path) -> bool:
    """有没有音轨（2026-11-04：低码率下载后检查，抖音个别版本只有画面没有声音）。"""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(src)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=NOWIN)
    return " Audio:" in (p.stderr or "")


def keyframes(src: Path, seconds: list[float], out_dir: Path, width: int = 720) -> list[tuple[float, Path]]:
    """在给定时间点各截一张 jpg（时间点 +2 秒，避开转场）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = []
    for t in sorted(set(int(x) for x in seconds)):
        dest = out_dir / f"t{t}.jpg"
        if not dest.exists():
            subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-ss", str(t + 2), "-i", str(src),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "5", str(dest)],
                           capture_output=True, creationflags=NOWIN)
        if dest.exists() and dest.stat().st_size > 2000:
            res.append((float(t), dest))
    return res


def _similar(a: str, b: str) -> bool:
    return bool(a and b) and (a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.72)


def ocr_subtitles(src: Path, every: float = 1.5, log=print) -> list[dict]:
    """抽帧（画面下半部分）→ RapidOCR → 去掉重复和常驻水印 → 带时间戳的字幕段。"""
    from rapidocr_onnxruntime import RapidOCR
    import numpy as np
    from PIL import Image

    engine = RapidOCR()
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf",
                        f"fps=1/{every},crop=iw:ih*0.55:0:ih*0.40,scale=900:-2", str(Path(td) / "%05d.png")],
                       capture_output=True, creationflags=NOWIN)
        frames = sorted(Path(td).glob("*.png"))
        log(f"画面字幕识别：{len(frames)} 帧")
        rows = []
        for i, fp in enumerate(frames):
            with Image.open(fp) as im:
                result, _ = engine(np.asarray(im.convert("RGB")))
            items = []
            for box, text, score in result or []:
                if float(score) < 0.5:
                    continue
                t = re.sub(r"\s+", "", text).strip("|_-—=·.。")
                if len(t) >= 2:
                    items.append((min(p[1] for p in box), min(p[0] for p in box), t))
            items.sort()
            rows.append((i * every, [x[2] for x in items]))
    if not rows:
        return []
    # 出现在 60% 以上帧里的文字视为水印/常驻标题，去掉
    freq: dict[str, int] = {}
    for _, lines in rows:
        for ln in set(lines):
            freq[ln] = freq.get(ln, 0) + 1
    sticky = {ln for ln, n in freq.items() if n >= max(3, 0.6 * len(rows))}
    segs: list[dict] = []
    for t, lines in rows:
        text = "".join(ln for ln in lines if ln not in sticky)
        if not text:
            continue
        if segs and _similar(segs[-1]["text"], text):
            segs[-1]["end"] = t + every
            if len(text) > len(segs[-1]["text"]):
                segs[-1]["text"] = text
            continue
        segs.append({"start": round(t, 2), "end": round(t + every, 2), "text": text})
    return segs


def speech_is_thin(segments: list[dict], duration: float) -> bool:
    """语音转出的字太少（纯音乐 + 字幕的视频）：每分钟少于 40 字，或总共不到 30 字。"""
    chars = sum(len(s.get("text", "")) for s in segments)
    minutes = max(duration / 60, 0.25)
    return chars < 30 or chars / minutes < 40
