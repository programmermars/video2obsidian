"""视频获取：抖音（分享页 → 浏览器兜底）、其他平台（yt-dlp）。"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)
UA_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


class FetchError(RuntimeError):
    pass


@dataclass
class VideoInfo:
    platform: str
    source_id: str
    url: str
    title: str = ""
    desc: str = ""
    creator: str = ""
    creator_url: str = ""
    published: str = ""  # "YYYY-MM-DD HH:MM"
    duration: float = 0.0  # seconds
    hashtags: list[str] = field(default_factory=list)
    media_path: str = ""
    method: str = ""
    quality: str = ""  # 下载的清晰度，如「低码率 576p（约 19 MB）」（2026-11-04 从 method 里拆出来）


# ---------------------------------------------------------------- helpers
DOUYIN_HOSTS = ("douyin.com", "iesdouyin.com")
VIDEO_HOSTS = ("bilibili.com", "b23.tv", "youtube.com", "youtu.be", "kuaishou.com", "xiaohongshu.com",
               "xhslink.com", "tiktok.com", "weibo.com", "ixigua.com", "vimeo.com", "x.com", "twitter.com",
               "instagram.com", "facebook.com", "fb.watch", "v.qq.com", "youku.com", "iqiyi.com", "acfun.cn",
               "xiaoyuzhoufm.com", "ximalaya.com")
URL_RE = re.compile(r"https?://[^\s　-〿＀-￯，。！？、“”‘’（）]+")


def extract_url(text: str) -> str:
    """从抖音分享文案里抠出链接（"7.92 复制打开抖音… https://v.douyin.com/xxx/ …"）。"""
    text = (text or "").strip()
    urls = [m.group(0).rstrip(".,;)") for m in URL_RE.finditer(text)]
    if not urls:
        raise FetchError("没有在输入里找到链接")
    # 2026-11-04：文案里常夹着别的链接（如 GitHub 仓库），优先挑视频平台的；
    # 都不是就取最后一个（分享链接一般在文案末尾）。
    for hosts in (DOUYIN_HOSTS, VIDEO_HOSTS):
        for u in urls:
            if _host_in(u, hosts):
                return u
    return urls[-1]


def _host_in(url: str, hosts) -> bool:
    """按域名判断（2026-11-04）：以前是在整个网址里找字样，github.com/x/youtube.com-dl 这种也会被当成视频链接。"""
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(h == d or h.endswith("." + d) for d in hosts)


def is_douyin(url: str) -> bool:
    return _host_in(url, DOUYIN_HOSTS)


def is_video_url(url: str) -> bool:
    return _host_in(url, DOUYIN_HOSTS + VIDEO_HOSTS)


def job_lines(text: str) -> tuple[list[str], int]:
    """把粘贴的内容拆成任务（一行一个链接）。只要有一行是视频链接，只含别的链接（如 GitHub 仓库）的行
    就跳过，不再变成一个必然失败、还会自动重试的任务。返回 (要处理的行, 跳过的行数)。2026-11-04。"""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()] or [text or ""]
    cand = []
    for ln in lines:
        try:
            cand.append((ln, is_video_url(extract_url(ln))))
        except FetchError:
            continue
    if any(v for _, v in cand):
        keep = [ln for ln, v in cand if v]
        return keep, len(cand) - len(keep)
    return [ln for ln, _ in cand], 0


ID_PATTERNS = [
    r"/(?:video|note|slides)/(\d{15,22})",
    r"/share/(?:video|note|slides)/(\d{15,22})",
    r"[?&](?:modal_id|aweme_id|item_id|vid)=(\d{15,22})",
]


def douyin_id(url: str) -> str:
    for pat in ID_PATTERNS:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    # 短链：跟随跳转
    try:
        r = requests.get(url, headers={"User-Agent": UA_MOBILE}, allow_redirects=True, timeout=15)
        final = r.url
    except requests.RequestException as exc:
        raise FetchError(f"短链解析失败：{exc}") from exc
    for pat in ID_PATTERNS:
        m = re.search(pat, final)
        if m:
            return m.group(1)
    raise FetchError(f"无法从链接识别抖音视频 ID：{final}")


def _fmt_ts(ts: int | float | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))


def split_hashtags(desc: str) -> tuple[str, list[str]]:
    tags = re.findall(r"#\s*([^\s#@]+)", desc or "")
    title = re.sub(r"#\s*[^\s#@]+", "", desc or "")
    title = re.sub(r"@\S+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title, tags


_CJK = r"\u4e00-\u9fff"


def short_title(t: str, limit: int = 30) -> str:
    """抖音文案常把整段简介当标题：太长时只取第一句。
    空格只在「中文 空格 中文」处切；「AI 做动画」这种中英之间的空格是标题的一部分（11-04 修）。"""
    t = re.sub(r"https?://\S+", "", t or "")
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= limit:
        return t
    sep = rf"[。！？!?\n]|(?<=[{_CJK}】」）》])\s+(?=[{_CJK}【「（《])"
    first = re.split(sep, t, maxsplit=1)[0].strip()               # 先按句子 / 中文之间的空格切
    if 4 <= len(first) <= 40:
        return first
    first = re.split(r"[，,；;：:]", first, maxsplit=1)[0].strip()  # 还太长再按逗号/冒号切
    return first if 4 <= len(first) <= 40 else t[:limit]


def _pick_streams(video: dict, hd: bool = False, min_height: int = 540) -> tuple[list[list[str]], str]:
    """按清晰度排好下载地址，返回 (分组, 说明)；每组是同一个版本的几个镜像地址。
    默认低码率（转文字只需要声音，下载快几倍）：取短边不低于 min_height 的最低码率，太糊会影响画面字幕识别；
    没有这么清楚的，就从最接近的开始试。hd=True 时先试原画。
    跳过 dash/m3u8：那种只有画面没有声音，转出来是静音。"""
    pa = [u.replace("playwm", "play") for u in ((video.get("play_addr") or {}).get("url_list") or [])]
    rates = []
    for br in video.get("bit_rate") or []:
        if str(br.get("format") or "mp4").lower() in ("dash", "m3u8", "hls"):
            continue
        p = br.get("play_addr") or {}
        urls = p.get("url_list") or []
        if urls:
            # 按短边算清晰度：竖屏 720×1280 是 720p，不是 1280p（2026-11-04 修）
            w, h = int(p.get("width") or 0), int(p.get("height") or 0)
            short = min(w, h) if w and h else h
            rates.append((int(br.get("bit_rate") or 0), short, int(p.get("data_size") or 0), urls))
    if hd:
        ordered = sorted(rates, key=lambda r: -r[0])
        groups, label = [pa] + [r[3] for r in ordered], "高清（原画）"
    else:
        sharp = sorted((r for r in rates if r[1] >= min_height), key=lambda r: r[0])
        unknown = sorted((r for r in rates if r[1] == 0), key=lambda r: r[0])
        small = sorted((r for r in rates if 0 < r[1] < min_height), key=lambda r: -r[1])
        ordered = sharp + unknown + small
        groups = [r[3] for r in ordered] + [pa]
        if ordered:
            h, size = ordered[0][1], ordered[0][2]
            label = f"低码率{f' {h}p' if h else ''}{f'（约 {size / 1048576:.0f} MB）' if size else ''}"
        else:
            label = "低码率不可用，用默认画质"
    seen, out = set(), []  # 去重保序：原画地址常和某个码率的地址重复
    for g in groups:
        g2 = [u for u in g if u and u not in seen]
        seen.update(g2)
        if g2:
            out.append(g2)
    return out, label


def _item_to_info(item: dict, vid: str, method: str, hd: bool = False,
                  min_height: int = 540) -> tuple[VideoInfo, list[list[str]]]:
    desc = item.get("desc") or ""
    title, tags = split_hashtags(desc)
    title = short_title(title)
    author = item.get("author") or {}
    video = item.get("video") or {}
    groups, quality = _pick_streams(video, hd, min_height)
    dur = video.get("duration") or item.get("duration") or 0
    dur = dur / 1000 if dur > 1000 else dur
    sec_uid = author.get("sec_uid") or ""
    info = VideoInfo(
        platform="抖音",
        source_id=vid,
        url=f"https://www.douyin.com/video/{vid}",
        title=title or f"抖音视频 {vid}",
        desc=desc,
        creator=author.get("nickname") or "",
        creator_url=f"https://www.douyin.com/user/{sec_uid}" if sec_uid else "",
        published=_fmt_ts(item.get("create_time")),
        duration=float(dur or 0),
        hashtags=tags,
        method=method,
        quality=quality,
    )
    return info, groups


def _has_audio(path: Path) -> bool:
    import media  # media 不依赖 fetchers，不会循环导入
    return media.has_audio(path)


def _fetch_to(u: str, tmp: Path, headers: dict, cookies=None, log=None) -> None:
    with requests.get(u, headers=headers, cookies=cookies, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            raise FetchError(f"返回的不是视频（{ctype}）")
        size, total, step = 0, int(r.headers.get("content-length") or 0), 20 * 1024 * 1024
        if log:
            log(f"下载视频中{f'（共 {total / 1048576:.0f} MB）' if total else ''}")
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1024 * 256):
                fh.write(chunk)
                size += len(chunk)
                if log and size // step != (size - len(chunk)) // step:
                    log(f"  已下载 {size / 1048576:.0f} MB")
    if size < 50 * 1024:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"文件太小（{size} bytes），可能被拦截")


def _download(groups: list[list[str]], dest: Path, headers: dict, cookies=None, log=None, validate=None) -> Path:
    """按优先级一组一组试（一组 = 同一个版本的几个镜像地址）。
    validate(path) 返回 False 表示这个版本不能用（例如没有声音）：同组其他镜像是同一个文件，直接换下一组。
    所有版本都不能用时，保留第一个下载到的文件（真的无声的视频也不至于下载失败）。"""
    def usable(p: Path) -> bool:
        if validate is None:
            return True
        try:
            return bool(validate(p))
        except Exception:  # noqa: BLE001  检查本身出错（比如找不到 ffmpeg）就当能用，别因此丢掉视频
            return True

    if dest.exists() and dest.stat().st_size > 1024 * 1024 and usable(dest):
        return dest  # 之前已下载过（例如上次中断），直接复用
    tmp = dest.with_name(dest.stem + ".part" + dest.suffix)
    spare = dest.with_name(dest.stem + ".noaudio" + dest.suffix)
    spare.unlink(missing_ok=True)
    last, have_spare = None, False
    for urls in groups:
        for u in urls:
            try:
                _fetch_to(u, tmp, headers, cookies, log)
            except Exception as exc:  # noqa: BLE001
                last = exc
                continue
            if usable(tmp):
                tmp.replace(dest)
                spare.unlink(missing_ok=True)
                return dest
            if log:
                log("  这个版本没有声音，换下一个清晰度")
            if have_spare:
                tmp.unlink(missing_ok=True)
            else:
                tmp.replace(spare)
                have_spare = True
            break
    tmp.unlink(missing_ok=True)
    if have_spare:
        if log:
            log("  所有版本都没有声音，保留无声的视频")
        spare.replace(dest)
        return dest
    raise FetchError(f"视频下载失败：{last}")


# ---------------------------------------------------------------- 抖音：方法 1 分享页
def douyin_share_page(vid: str, media_dir: Path, min_height: int = 540) -> VideoInfo:
    r = requests.get(
        f"https://www.iesdouyin.com/share/video/{vid}/",
        headers={"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"},
        timeout=20,
    )
    r.raise_for_status()
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", r.text, re.S)
    if not m:
        raise FetchError("分享页没有 _ROUTER_DATA（可能触发了验证）")
    data = json.loads(m.group(1))
    items = None
    for v in (data.get("loaderData") or {}).values():
        if isinstance(v, dict) and v.get("videoInfoRes"):
            items = v["videoInfoRes"].get("item_list")
            if items:
                break
    if not items:
        raise FetchError("分享页里没有视频数据（可能是图文、已删除或私密）")
    info, groups = _item_to_info(items[0], vid, "iesdouyin 分享页", min_height=min_height)
    if not groups:
        raise FetchError("分享页没有播放地址（可能是图文作品）")
    dest = media_dir / f"douyin_{vid}.mp4"
    _download(groups, dest, {"User-Agent": UA_MOBILE, "Referer": "https://www.douyin.com/"}, validate=_has_audio)
    info.media_path = str(dest)
    return info


# ---------------------------------------------------------------- 抖音：方法 2 浏览器
def douyin_browser(vid: str, media_dir: Path, profile_dir: Path, headless: bool = True, log=print,
                   hd: bool = False, min_height: int = 540) -> VideoInfo:
    from playwright.sync_api import sync_playwright

    page_url = f"https://www.douyin.com/video/{vid}"
    with sync_playwright() as p:
        ctx = None
        errors = []
        for channel in ("msedge", "chrome", None):
            try:
                kwargs = dict(user_data_dir=str(profile_dir), headless=headless,
                              viewport={"width": 1280, "height": 800}, locale="zh-CN",
                              args=["--disable-blink-features=AutomationControlled"])
                if channel:
                    kwargs["channel"] = channel
                ctx = p.chromium.launch_persistent_context(**kwargs)
                log(f"浏览器已启动（{channel or 'chromium'}，headless={headless}）")
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{channel}: {exc}")
        if ctx is None:
            raise FetchError("无法启动浏览器：" + " | ".join(errors))
        try:
            captured: dict = {}

            def on_resp(resp):
                if "/aweme/v1/web/aweme/detail" in resp.url and "d" not in captured:
                    try:
                        captured["d"] = resp.json()
                    except Exception:  # noqa: BLE001
                        pass

            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.on("response", on_resp)
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            deadline = time.time() + 25
            while time.time() < deadline and "d" not in captured:
                page.wait_for_timeout(500)

            groups: list[list[str]] = []
            info = None
            if captured.get("d", {}).get("aweme_detail"):
                info, groups = _item_to_info(captured["d"]["aweme_detail"], vid, "浏览器（详情接口）", hd, min_height)
            if not groups:
                try:
                    src = page.eval_on_selector_all(
                        "video", "vs => vs.map(v => v.currentSrc || (v.querySelector('source')||{}).src).filter(Boolean)")
                    groups = [[("https:" + u) if u.startswith("//") else u] for u in src if not u.startswith("blob:")]
                except Exception:  # noqa: BLE001
                    groups = []
            if info is None:
                # RENDER_DATA 兜底取元数据
                try:
                    raw = page.eval_on_selector("#RENDER_DATA", "e => e.textContent")
                    rd = json.loads(unquote(raw))
                    for v in rd.values():
                        if isinstance(v, dict) and v.get("aweme", {}).get("detail"):
                            d = v["aweme"]["detail"]
                            info = VideoInfo(platform="抖音", source_id=vid, url=page_url,
                                             title=split_hashtags(d.get("desc", ""))[0] or f"抖音视频 {vid}",
                                             desc=d.get("desc", ""), creator=(d.get("authorInfo") or {}).get("nickname", ""),
                                             published=_fmt_ts(d.get("createTime")),
                                             duration=float((d.get("video") or {}).get("duration", 0)) / 1000,
                                             hashtags=split_hashtags(d.get("desc", ""))[1], method="浏览器（RENDER_DATA）")
                            break
                except Exception:  # noqa: BLE001
                    pass
            if info is None:
                info = VideoInfo(platform="抖音", source_id=vid, url=page_url,
                                 title=(page.title() or "").replace(" - 抖音", "") or f"抖音视频 {vid}",
                                 method="浏览器（页面）")
            if not groups:
                raise FetchError("浏览器里也拿不到播放地址：可能需要登录或滑块验证。"
                                 "请在网页上点「打开登录浏览器」登录一次抖音再试。")
            cookies = {c["name"]: c["value"] for c in ctx.cookies() if "douyin" in c.get("domain", "")}
        finally:
            ctx.close()
    dest = media_dir / f"douyin_{vid}{'_hd' if hd else ''}.mp4"
    _download(groups, dest, {"User-Agent": UA_PC, "Referer": "https://www.douyin.com/"}, cookies=cookies, log=log,
              validate=_has_audio)
    info.media_path = str(dest)
    return info


def open_login_browser(profile_dir: Path) -> None:
    """有界面打开抖音，让用户手动登录；关闭窗口后登录态保存在 profile_dir。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        for channel in ("msedge", "chrome", None):
            try:
                kw = dict(user_data_dir=str(profile_dir), headless=False, locale="zh-CN",
                          args=["--disable-blink-features=AutomationControlled"])
                if channel:
                    kw["channel"] = channel
                ctx = p.chromium.launch_persistent_context(**kw)
                break
            except Exception:  # noqa: BLE001
                ctx = None
        if ctx is None:
            raise FetchError("无法启动浏览器")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=90000)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.wait_for_event("close", timeout=15 * 60 * 1000)
        except Exception:  # noqa: BLE001
            pass
        try:
            ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- 其他平台：yt-dlp
def ytdlp(url: str, media_dir: Path, log=print, video: bool = False, hd: bool = False) -> VideoInfo:
    """video 参数保留兼容：2026-11-06 起转文字也下带画面的视频，和「只存 Drive」一样。"""
    import yt_dlp

    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        ffmpeg = None
    opts = {
        # 2026-11-06：转文字也下带画面的视频（关键画面截图、画面字幕 OCR 要用），原来只下音轨。
        # 不勾高清时优先「短边 ≤720p 里最大的」（yt-dlp 的 res = 宽高里较小的一边，竖屏 720×1280 也算 720p；
        # 原来「只存 Drive」用 height<=720，竖屏会被降到更低）。来源没有视频轨时退回只下音频（最后的 /ba）。
        "format": "bv*+ba/b/ba",
        "merge_output_format": "mp4",
        "outtmpl": str(media_dir / "%(extractor)s_%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
    }
    if not hd:
        opts["format_sort"] = ["res:720"]
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    with yt_dlp.YoutubeDL(opts) as ydl:
        meta = ydl.extract_info(url, download=True)
        rd = meta.get("requested_downloads") or []
        path = (rd[0].get("filepath") if rd else None) or ydl.prepare_filename(meta)
    try:
        mb = Path(path).stat().st_size / 1048576
    except OSError:
        mb = 0.0
    w, h = meta.get("width"), meta.get("height")
    res = f"{min(w, h)}p" if w and h else ("只有音频" if meta.get("vcodec") == "none" else "清晰度未知")
    quality = res + (f"（约 {mb:.1f} MB）" if mb else "")
    up = meta.get("upload_date") or ""
    published = f"{up[:4]}-{up[4:6]}-{up[6:8]}" if len(up) == 8 else ""
    if meta.get("timestamp"):
        published = _fmt_ts(meta["timestamp"])
    desc = meta.get("description") or ""
    return VideoInfo(
        platform=meta.get("extractor_key") or meta.get("extractor") or "web",
        source_id=str(meta.get("id") or ""),
        url=meta.get("webpage_url") or url,
        title=meta.get("title") or "",
        desc=desc[:2000],
        creator=meta.get("uploader") or meta.get("channel") or "",
        creator_url=meta.get("uploader_url") or meta.get("channel_url") or "",
        published=published,
        duration=float(meta.get("duration") or 0),
        hashtags=list(meta.get("tags") or [])[:10],
        media_path=path,
        method="yt-dlp",
        quality=quality,
    )


# ---------------------------------------------------------------- 入口
def fetch(text: str, media_dir: Path, profile_dir: Path, cfg: dict, log=print, hd: bool | None = None,
          video: bool = False) -> VideoInfo:
    if hd is None:
        hd = bool(cfg.get("download_hd_default", False))
    media_dir.mkdir(parents=True, exist_ok=True)
    url = extract_url(text)
    log(f"链接：{url}")
    if not is_douyin(url):
        log("非抖音链接，使用 yt-dlp 下载")
        return ytdlp(url, media_dir, log, video=video, hd=bool(hd))

    vid = douyin_id(url)
    log(f"抖音视频 ID：{vid}")
    min_height = int(cfg.get("download_min_height", 540))
    errors = []
    if cfg.get("douyin_share_page", False):  # 2026-10 起抖音分享页拿不到数据，默认跳过（省 2 秒和一条错误日志）
        try:
            log("方法 1：iesdouyin 分享页")
            return douyin_share_page(vid, media_dir, min_height)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"分享页：{exc}")
            log(f"  失败：{exc}")
    for headless in ([True, False] if cfg.get("browser_headless", True) else [False]):
        try:
            log(f"方法 2：浏览器抓取（headless={headless}）")
            return douyin_browser(vid, media_dir, profile_dir, headless=headless, log=log, hd=hd,
                                  min_height=min_height)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"浏览器(headless={headless})：{exc}")
            log(f"  失败：{exc}")
    try:
        log("方法 3：yt-dlp")
        info = ytdlp(f"https://www.douyin.com/video/{vid}", media_dir, log, video=video, hd=bool(hd))
        info.platform, info.source_id = "抖音", vid
        return info
    except Exception as exc:  # noqa: BLE001
        errors.append(f"yt-dlp：{exc}")
    raise FetchError("全部下载方法失败：\n" + "\n".join(errors))
