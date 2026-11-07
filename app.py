"""视频 → 文字 → Obsidian：本地网页工具。运行：启动.bat 或 python app.py"""
from __future__ import annotations

import json
import os
import shutil
import re
import socket
import subprocess
import sys
import queue
import threading
import time
import traceback
import uuid
import webbrowser
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi import Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import fetchers
import glossary
import media
import notes
import summarizer
import transcriber

ROOT = Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.json"
MEDIA = ROOT / "data" / "media"
PROFILE = ROOT / "data" / "browser-profile"
LOGS = ROOT / "logs"
HISTORY = ROOT / "data" / "history.jsonl"
for d in (MEDIA, PROFILE, LOGS):
    d.mkdir(parents=True, exist_ok=True)
if sys.stdout is None or sys.stderr is None:  # pythonw（后台运行）没有控制台
    _out = open(LOGS / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _out
    sys.stderr = sys.stderr or _out


def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


JOBS: dict[str, dict] = {}
Q: "queue.Queue[str]" = queue.Queue()


def _log_file(msg: str) -> None:
    with (LOGS / "app.log").open("a", encoding="utf-8") as fh:
        fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


QFILE = ROOT / "data" / "queue.json"
_qlock = threading.Lock()


def save_queue() -> None:
    """没做完的任务存到硬盘：电脑重启/服务重启后自动接着做。"""
    with _qlock:
        pending = [{k: j[k] for k in ("id", "kind", "payload", "created", "attempts")}
                   for j in JOBS.values() if j["stage"] not in ("done", "error")]
        try:
            QFILE.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass


def restore_queue() -> None:
    try:
        items = json.loads(QFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for it in items:
        job = {"id": it["id"], "kind": it["kind"], "payload": it["payload"], "status": "排队中（重启后恢复）",
               "stage": "queued", "progress": 0, "log": [time.strftime("%H:%M:%S ") + "服务重启后自动恢复这个任务"],
               "created": it.get("created", ""), "result": None, "error": None, "attempts": it.get("attempts", 0)}
        JOBS[job["id"]] = job
        Q.put(job["id"])


def new_job(kind: str, payload: dict) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "kind": kind, "payload": payload, "status": "排队中", "stage": "queued", "attempts": 0,
           "progress": 0, "log": [], "created": time.strftime("%H:%M:%S"), "result": None, "error": None}
    JOBS[jid] = job
    done = [k for k, j in JOBS.items() if j["stage"] in ("done", "error")]
    for k in done[:-50]:  # 内存里只保留最近 50 个已完成任务
        JOBS.pop(k, None)
    Q.put(jid)
    save_queue()
    return job


def run_job(job: dict) -> None:
    cfg = load_cfg()
    vault = Path(cfg["vault_path"])
    if not vault.exists():
        raise RuntimeError(f"找不到 Obsidian 库：{vault}（改 config.json 的 vault_path）")

    def log(msg: str) -> None:
        job["log"].append(time.strftime("%H:%M:%S ") + msg)
        _log_file(f"[{job['id']}] {msg}")

    p = job["payload"]
    if job["kind"] == "resum":
        note = Path(p["note"])
        job.update(stage="summarize", status="AI 摘要中", title=note.stem)
        body = note.read_text(encoding="utf-8")
        m = re.search(r"raw evidence: `([^`]+)\.txt`", body)
        if not m:
            raise RuntimeError("笔记里找不到原始转录路径")
        raw = json.loads((vault / (m.group(1) + ".json")).read_text(encoding="utf-8"))
        info, tr = raw["source"], raw["transcript"]
        text = notes.timestamped_text(tr["segments"])
        summary, model = summarizer.summarize(info.get("title", ""), info.get("creator", ""), text, cfg, log=log,
                                              duration=notes._mmss(info.get("duration") or tr.get("duration") or 0))
        tail = body.split("## My Takeaways", 1)[1] if "## My Takeaways" in body else None
        note = notes.write_note(vault, cfg, info, tr, summary, model, path=note, tail=tail)
        log(f"摘要已写入（{model}）")
        job["result"] = {"note": str(note), "uri": notes.obsidian_uri(vault, note), "title": note.stem}
        return
    # 1. 获取视频
    job.update(stage="fetch", status="获取视频中")
    if job["kind"] == "url":
        url = fetchers.extract_url(p["text"])
        if fetchers.is_douyin(url) and not p.get("only_drive"):
            vid = fetchers.douyin_id(url)
            ex = notes.find_existing(vault, cfg["notes_dir"], vid)
            own = (vault / cfg["notes_dir"]).resolve()
            if ex and p.get("force") and own not in ex.resolve().parents:
                log(f"这条视频已有手工整理的旧笔记：{ex.name}，为保护它不覆盖")
                job["result"] = {"note": str(ex), "uri": notes.obsidian_uri(vault, ex), "title": ex.stem, "skipped": True}
                return
            if ex and not p.get("force"):
                log(f"这条视频已经有笔记了：{ex.name}（勾选「重新处理」可覆盖）")
                job["result"] = {"note": str(ex), "uri": notes.obsidian_uri(vault, ex), "title": ex.stem, "skipped": True}
                return
        info = fetchers.fetch(p["text"], MEDIA, PROFILE, cfg, log=log, hd=p.get("hd"), video=bool(p.get("only_drive")))
    else:
        mp = Path(p["path"])
        info = fetchers.VideoInfo(platform="本地文件", source_id=p.get("sid", mp.stem), url="",
                                  title=p.get("title") or mp.stem, media_path=str(mp), method="上传")
    log(f"已获取：{info.title}｜{info.creator}｜{info.method}" + (f"｜{info.quality}" if info.quality else ""))
    job["title"] = info.title

    # 只存 Drive：不转录、不摘要、不写笔记（2026-11-04 加）
    if p.get("only_drive"):
        job.update(stage="write", status="存到 Google Drive")
        if not drive_ready(cfg):
            raise RuntimeError(f"找不到 Google Drive 文件夹（{cfg.get('drive_dir') or '未设置'}）：Drive 电脑版装好并登录后再试")
        src = Path(info.media_path)
        pub = (info.published or "")[:10] or time.strftime("%Y-%m-%d")
        stem = f"{pub} - {notes._safe(info.title, 50)} - {notes._safe(info.creator or info.platform, 20)}"
        dd = Path(cfg["drive_dir"])
        dd.mkdir(parents=True, exist_ok=True)
        dest = dd / (stem + src.suffix)
        shutil.copy2(src, dest)
        log(f"视频已放进 Google Drive 文件夹（会自动同步上传）：{dest.name}（{dest.stat().st_size / 1048576:.0f} MB）")
        job["drive"] = str(dest)
        if not cfg.get("keep_media", False) and job["kind"] == "url":
            try:
                src.unlink(missing_ok=True)
            except OSError:
                pass
        job["result"] = {"title": info.title, "drive": str(dest)}
        return

    # 2. 转录
    job.update(stage="transcribe", status="转文字中", progress=0)

    t0 = time.time()

    def prog(x: int) -> None:
        job["progress"] = x
        if x >= 3:
            job["eta_min"] = max(1, round((time.time() - t0) * (100 - x) / x / 60))

    tcfg = cfg
    if info.method == "yt-dlp" and info.platform != "抖音":  # 2026-11-06 非抖音自动识别语言
        tcfg = dict(cfg, language=None)
        log("非抖音视频：转录语言自动识别")
    tr = transcriber.transcribe(Path(info.media_path), tcfg, log=log, progress=prog)
    job["progress"] = 100
    log(f"转录完成：{len(tr['segments'])} 段（{tr['engine']}）")
    src = Path(info.media_path)
    video_ok = src.exists() and media.has_video(src)
    dur = float(tr.get("duration") or info.duration or 0)
    if cfg.get("ocr_fallback", True) and video_ok and media.speech_is_thin(tr["segments"], dur):
        job.update(status="识别画面字幕")
        log("语音内容很少，改用画面字幕识别（OCR）")
        try:
            segs = glossary.apply(media.ocr_subtitles(src, log=log))
            if sum(len(x["text"]) for x in segs) > sum(len(x["text"]) for x in tr["segments"]):
                tr = {"engine": "画面字幕 OCR（RapidOCR）", "language": "zh", "duration": dur, "segments": segs}
                log(f"画面字幕识别完成：{len(segs)} 段")
            else:
                log("画面字幕也很少，保留语音转录")
        except Exception as exc:  # noqa: BLE001
            log(f"画面字幕识别失败：{exc}")

    # 3. 摘要（可选）
    summary, model = None, ""
    if p.get("summarize", cfg.get("summarize_default", True)):
        job.update(stage="summarize", status="AI 摘要中")
        text = notes.timestamped_text(tr["segments"])
        if len(text) < 60:
            log("转录太短，跳过摘要")
        else:
            try:
                summary, model = summarizer.summarize(info.title, info.creator, text, cfg, log=log,
                                                      duration=notes._mmss(info.duration or tr.get("duration") or 0))
                log(f"AI 摘要完成（{model}）")
            except Exception as exc:  # noqa: BLE001
                log(f"AI 摘要失败，跳过（只保存转录）：{exc}")

    # 3.5 关键画面截图（按大纲时间点）
    if cfg.get("keyframes", True) and video_ok:
        try:
            secs = notes.outline_seconds(notes.link_summary(summary, tr["segments"])) if summary else []
            if not secs and dur > 0:
                secs = [dur * i / 7 for i in range(1, 7)]
            out = vault / cfg.get("frames_dir", "AI_Learning_System/00_System/视频截图") / f"{info.platform}_{info.source_id}"
            got = media.keyframes(src, secs[:10], out)
            log(f"关键画面：{len(got)} 张")
        except Exception as exc:  # noqa: BLE001
            log(f"截图失败，跳过：{exc}")

    # 4. 写入 Obsidian
    job.update(stage="write", status="写入 Obsidian")
    note = notes.write_note(vault, cfg, asdict(info), tr, summary, model)
    log(f"已写入：{note.relative_to(vault).as_posix()}")
    if p.get("drive"):
        job.update(stage="write", status="存到 Google Drive")
        dd = Path(cfg.get("drive_dir") or "")
        if not drive_ready(cfg):
            log(f"找不到 Google Drive 文件夹（{cfg.get('drive_dir') or '未设置'}）：Drive 电脑版装好并登录后再试，视频未上传")
        else:
            try:
                dd.mkdir(parents=True, exist_ok=True)
                src = Path(info.media_path)
                dest = dd / (note.stem + src.suffix)
                shutil.copy2(src, dest)
                log(f"视频已放进 Google Drive 文件夹（会自动同步上传）：{dest.name}")
                body = note.read_text(encoding="utf-8")
                line = f"- Video file: Google Drive `{dest}`"
                if line not in body:
                    body = body.replace("\n- Source:", f"\n{line}\n- Source:", 1)
                    note.write_text(body, encoding="utf-8")
                job["drive"] = str(dest)
            except Exception as exc:  # noqa: BLE001
                log(f"存到 Google Drive 失败：{exc}")
    if not cfg.get("keep_media", False) and job["kind"] in ("url", "file"):
        try:
            Path(info.media_path).unlink(missing_ok=True)
        except OSError:
            pass
    job["result"] = {"note": str(note), "uri": notes.obsidian_uri(vault, note), "title": info.title}
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M"), "title": info.title,
                             "url": info.url, "note": str(note)}, ensure_ascii=False) + "\n")


def _awake(on: bool) -> None:
    """处理任务时阻止 Windows 睡眠（不影响屏幕关闭）。"""
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:  # noqa: BLE001
        pass


def worker() -> None:
    while True:
        jid = Q.get()
        job = JOBS[jid]
        _awake(True)
        try:
            job.update(stage="fetch", status="开始处理")
            if job.get("attempts"):
                job["log"].append(time.strftime("%H:%M:%S ") + f"第 {job['attempts']} 次自动重试")
                _log_file(f"[{jid}] 第 {job['attempts']} 次自动重试")
            run_job(job)
            job.update(status="完成", stage="done")
        except Exception as exc:  # noqa: BLE001
            _log_file(f"[{jid}] ERROR {exc}\n{traceback.format_exc()}")
            job["log"].append(time.strftime("%H:%M:%S ") + "错误：" + str(exc))
            retryable = not any(k in str(exc) for k in ("没有在输入里找到链接", "找不到 Obsidian 库", "图文"))
            if retryable and job.get("attempts", 0) < int(load_cfg().get("retry_times", 1)):
                job["attempts"] = job.get("attempts", 0) + 1
                job.update(status=f"失败，2 分钟后自动重试（第 {job['attempts']} 次）", stage="queued")
                threading.Timer(120, lambda j=jid: Q.put(j)).start()
            else:
                job.update(status="失败", stage="error", error=str(exc))
        finally:
            save_queue()
            if Q.empty():
                _awake(False)


restore_queue()
threading.Thread(target=worker, daemon=True).start()
app = FastAPI(title="视频转 Obsidian")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/jobs")
def create_job(text: str = Form(...), summarize: bool = Form(True), force: bool = Form(False),
               drive: bool = Form(False), hd: bool = Form(False), only_drive: bool = Form(False)) -> dict:
    keep, skipped = fetchers.job_lines(text)  # 有视频链接时，只含别的链接（如 GitHub）的行不当成任务
    out = [new_job("url", {"text": ln, "summarize": summarize, "force": force, "drive": drive, "hd": hd,
                           "only_drive": only_drive})["id"] for ln in keep]
    if not out:
        raise HTTPException(400, "没有找到链接")
    return {"ids": out, "skipped": skipped}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), summarize: bool = Form(True), drive: bool = Form(False)) -> dict:
    sid = uuid.uuid4().hex[:8]
    dest = MEDIA / f"upload_{sid}{Path(file.filename or 'x.mp4').suffix}"
    with dest.open("wb") as fh:
        while chunk := await file.read(1024 * 1024):
            fh.write(chunk)
    job = new_job("file", {"path": str(dest), "title": Path(file.filename or "").stem, "sid": f"upload-{sid}",
                           "summarize": summarize, "drive": drive})
    return {"ids": [job["id"]]}


@app.get("/api/notes")
def list_notes() -> list:
    cfg = load_cfg()
    d = Path(cfg["vault_path"]) / cfg["notes_dir"]
    out = []
    for p in sorted(d.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
        head = p.read_text(encoding="utf-8", errors="ignore")[:1500]
        out.append({"name": p.stem, "path": str(p),
                    "has_summary": "status: processed" in head and "summary_incomplete: true" not in head})
    return out


@app.post("/api/summarize-note")
def summarize_note(path: str = Form(...)) -> dict:
    cfg = load_cfg()
    p = Path(path).resolve()
    root = (Path(cfg["vault_path"]) / cfg["notes_dir"]).resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(400, "只能处理笔记目录里的文件")
    return {"ids": [new_job("resum", {"note": str(p)})["id"]]}


@app.get("/api/jobs")
def list_jobs() -> list:
    return [{k: v for k, v in j.items() if k != "payload"} for j in reversed(list(JOBS.values()))][:30]


@app.get("/api/status")
def status() -> dict:
    cfg = load_cfg()
    return {
        "vault": cfg["vault_path"], "vault_ok": Path(cfg["vault_path"]).exists(),
        "notes_dir": cfg["notes_dir"],
        "rtx": transcriber.rtx_ready(cfg.get("rtx_worker_url", "")),
        "llm": summarizer.available(cfg),
        "agentchat": summarizer.agentchat_ok(cfg),
        "model": cfg.get("whisper_model"),
        "drive_ready": drive_ready(cfg),
        "drive_default": bool(cfg.get("drive_default", False)),
    }


@app.get("/api/search")
def search(q: str, limit: int = 30) -> list:
    """在所有视频笔记的转录里搜关键词，返回命中的段落（带时间戳，可跳转）。"""
    q = q.strip()
    if len(q) < 1:
        return []
    cfg = load_cfg()
    vault = Path(cfg["vault_path"])
    root = (vault / cfg["notes_dir"]).parent
    ql = q.lower()
    out = []
    for p in sorted(root.rglob("*.md"), key=lambda x: x.name, reverse=True):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"^source_type:\s*video\s*$", t[:2000], re.M) or ql not in t.lower():
            continue
        title = notes._fm_value(t, "title") or p.stem
        hits = []
        for ln in t.splitlines():
            if ql in ln.lower() and not ln.startswith(("title:", "tags:", "  - ", "url:", "platform_tags:")):
                m = re.search(r"\^(t\d+)\s*$", ln)
                ts = re.match(r"\*\*\[(\d+:\d+(?::\d+)?)\]\*\*", ln)
                clean = re.sub(r"\[\[#\^t\d+\|([^\]]+)\]\]", r"\1", re.sub(r" \^t\d+\s*$", "", ln)).strip("-* ")
                i = clean.lower().find(ql)
                snip = ("…" if i > 40 else "") + clean[max(0, i - 40): i + len(q) + 60] + "…"
                hits.append({"bid": m.group(1) if m else "", "ts": ts.group(1) if ts else "", "text": snip})
            if len(hits) >= 5:
                break
        out.append({"path": str(p), "title": title, "hits": hits})
        if len(out) >= limit:
            break
    return out


@app.get("/api/frame")
def frame(path: str) -> FileResponse:
    cfg = load_cfg()
    vault = Path(cfg["vault_path"]).resolve()
    p = (vault / path).resolve()
    root = (vault / cfg.get("frames_dir", "AI_Learning_System/00_System/视频截图")).resolve()
    if root not in p.parents or not p.is_file() or p.suffix.lower() not in (".jpg", ".png"):
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/drive-default")
def set_drive_default(on: bool = Form(...)) -> dict:
    cfg = load_cfg()
    cfg["drive_default"] = bool(on)
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"drive_default": cfg["drive_default"]}


@app.get("/api/health")
def health() -> dict:
    """自检：给维护用，也显示在网页上。"""
    import shutil
    import urllib.request
    cfg = load_cfg()
    vault = Path(cfg["vault_path"])
    checks = {
        "vault": vault.exists(),
        "notes_dir": (vault / cfg["notes_dir"]).exists(),
        "agentchat_files": summarizer.agentchat_ok(cfg),
        "glossary_terms": len(__import__("glossary").load()[0]),
        "disk_free_gb": round(shutil.disk_usage(str(ROOT)).free / 1e9, 1),
        "queue": Q.qsize(),
        "last_error": next((j["error"] for j in reversed(list(JOBS.values())) if j.get("error")), None),
    }
    try:
        urllib.request.urlopen("http://127.0.0.1:9223/json/version", timeout=2)
        checks["agentchat_chrome"] = True
    except Exception:  # noqa: BLE001
        checks["agentchat_chrome"] = False  # 没开着也没关系，AgentChat 会自动启动
    checks["ok"] = checks["vault"] and checks["notes_dir"] and checks["agentchat_files"] and checks["disk_free_gb"] > 2
    return checks


@app.post("/api/login-browser")
def login_browser() -> dict:
    threading.Thread(target=fetchers.open_login_browser, args=(PROFILE,), daemon=True).start()
    return {"ok": True}


def drive_ready(cfg: dict) -> bool:
    """Drive 电脑版装好后会出现 G:\\我的云端硬盘（或 config 里写的根目录）。只要根目录在就算可用。"""
    d = cfg.get("drive_dir") or ""
    if not d:
        return False
    parts = Path(d).parts
    return len(parts) >= 2 and Path(parts[0], parts[1]).exists()


def _from_tailnet(req: Request) -> bool:
    return any(h.lower().startswith("tailscale-") for h in req.headers.keys())


@app.get("/api/note")
def get_note(path: str) -> dict:
    cfg = load_cfg()
    p = Path(path).resolve()
    # 2026-11-06：和 /api/search 同一范围（整个 01_Sources），否则旧文件夹里的笔记能搜到却打不开。只读，只限 .md
    root = (Path(cfg["vault_path"]) / cfg["notes_dir"]).parent.resolve()
    if root not in p.parents or not p.is_file() or p.suffix.lower() != ".md":
        raise HTTPException(400, "只能读取笔记目录里的文件")
    return {"name": p.stem, "markdown": p.read_text(encoding="utf-8"),
            "uri": notes.obsidian_uri(Path(cfg["vault_path"]), p)}


@app.post("/api/restart")
def restart(req: Request) -> dict:
    """重启服务（加载新代码）。只允许本机调用，且没有任务在跑。"""
    if _from_tailnet(req):
        raise HTTPException(403, "手机端不能重启服务")
    busy = [j for j in JOBS.values() if j["stage"] not in ("done", "error")]
    if busy:
        raise HTTPException(409, f"还有 {len(busy)} 个任务没完成")
    env = dict(os.environ, V2O_WAIT_PORT="1", V2O_NO_BROWSER="1")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    subprocess.Popen([str(pyw if pyw.exists() else exe), str(ROOT / "app.py")], cwd=str(ROOT), env=env,
                     creationflags=flags, close_fds=True)
    threading.Timer(0.8, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/manifest.webmanifest")
def manifest() -> JSONResponse:
    return JSONResponse({"id": "/", "name": "视频转 Obsidian", "short_name": "转笔记", "start_url": "/", "scope": "/",
                         "display": "standalone", "background_color": "#f6f5f2", "theme_color": "#6b4fd8",
                         "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
                                   {"src": "/icon.png", "sizes": "512x512", "type": "image/png"}],
                         # 安卓：装成 App 后，在抖音点「分享」能直接选它
                         "share_target": {"action": "/share", "method": "GET",
                                          "params": {"title": "title", "text": "text", "url": "url"}}},
                        media_type="application/manifest+json")


@app.get("/sw.js")
def sw() -> Response:
    js = "self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>self.clients.claim());self.addEventListener('fetch',()=>{});"
    return Response(js, media_type="application/javascript")


@app.get("/icon.png")
def icon_png() -> FileResponse:
    return FileResponse(ROOT / "static" / "icon.png", media_type="image/png")


@app.get("/share")
def share(title: str = "", text: str = "", url: str = "", summarize: bool = True,
          drive: bool | None = None) -> RedirectResponse:
    """分享入口：安卓分享面板 / iPhone 快捷指令 打开 /share?text=分享文案"""
    blob = " ".join(x for x in (text, url, title) if x)
    try:
        fetchers.extract_url(blob)
    except fetchers.FetchError:
        return RedirectResponse("/?shared=nolink", status_code=303)
    if drive is None:
        drive = bool(load_cfg().get("drive_default", False))
    jid = new_job("url", {"text": blob, "summarize": summarize, "force": False, "drive": drive})["id"]
    return RedirectResponse(f"/?shared={jid}", status_code=303)


@app.get("/icon.svg")
def icon() -> FileResponse:
    return FileResponse(ROOT / "static" / "icon.svg", media_type="image/svg+xml")


def _wait_port(host: str, port: int, seconds: int = 30) -> None:
    for _ in range(seconds * 2):
        with socket.socket() as sk:
            try:
                sk.bind((host, port))
                return
            except OSError:
                time.sleep(0.5)


if __name__ == "__main__":
    import uvicorn

    cfg = load_cfg()
    host, port = cfg.get("host", "127.0.0.1"), int(cfg.get("port", 8791))
    for lf in LOGS.glob("*.log"):  # 简单日志轮转：超过 2MB 改名为 .old
        try:
            if lf.stat().st_size > 2_000_000:
                lf.replace(lf.with_suffix(".log.old"))
        except OSError:
            pass
    if os.environ.get("V2O_WAIT_PORT"):
        _wait_port(host, port)
    with socket.socket() as _sk:  # 已经有一个在运行（例如开机自启 + 手动双击），安静退出
        try:
            _sk.bind((host, port))
        except OSError:
            _log_file(f"端口 {port} 已被占用，说明服务已在运行，本次启动退出")
            sys.exit(0)
    if not os.environ.get("V2O_NO_BROWSER"):
        threading.Timer(2.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")
