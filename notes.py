"""把转录写成 Obsidian 笔记（沿用 AI_Learning_System 的 Source Video Note 结构）。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote


def _safe(name: str, limit: int = 60) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n\t#^\[\]]', " ", name or "")
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit].rstrip(" .")) or "未命名"


def _mmss(sec: float) -> str:
    sec = int(sec or 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _yaml(v) -> str:
    return json.dumps(v if v is not None else "", ensure_ascii=False)


def paragraphs(segments: list[dict], every: float = 30.0) -> list[tuple[float, str]]:
    """把短句合并成约 30 秒一段，带起始时间戳，方便阅读。"""
    out, buf, start = [], [], None
    for s in segments:
        t = (s.get("text") or "").strip()
        if not t:
            continue
        if start is None:
            start = float(s.get("start", 0))
        buf.append(t)
        if float(s.get("end", 0)) - start >= every:
            out.append((start, _join(buf)))
            buf, start = [], None
    if buf:
        out.append((start or 0.0, _join(buf)))
    return out


def _join(parts: list[str]) -> str:
    if parts and not re.search(r"[\u4e00-\u9fff]", "".join(parts)):
        # 2026-11-06：没有中文（英文等）时句间用空格、结尾补「.」。原来按中文规则拼，
        # 英文变成「$1000.Everyone wanted…」并在结尾多一个「。」。有中文时保持原来的逻辑不变。
        res = " ".join(p.strip() for p in parts if p.strip())
        return res + ("" if not res or re.search(r"[.!?…]$", res) else ".")
    res = ""
    for p in parts:
        p = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "，", p.strip())
        if res and not re.search(r"[，。！？、；：,.!?;:…]$", res):
            res += "，"
        res += p
    if res and not re.search(r"[。！？!?…]$", res):
        res += "。"
    return res


def _bid(start: float) -> str:
    return f"t{int(start)}"


def transcript_lines(segments: list[dict]) -> list[str]:
    """转录全文：每段带 Obsidian 块 ID（^t秒数），摘要里的时间戳可以点击跳过来。"""
    out = []
    for start, para in paragraphs(segments):
        out += [f"**[{_mmss(start)}]** {para} ^{_bid(start)}", ""]
    return out or ["（没有识别到语音）", ""]


def _parse_ts(t: str) -> float:
    parts = [int(x) for x in t.split(":")]
    return parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]


def _grams(t: str) -> set:
    t = re.sub(r"[\s，。、：:；;！？!?（）()“”\"'—\-\[\]]", "", t.lower())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def link_summary(summary: str, segments: list[dict]) -> str:
    """1) 大纲里没有时间戳的条目：按内容在转录里找最像的段落，补上时间（按顺序往后找）。
    2) 所有 [mm:ss] 变成可点击链接，跳到转录全文对应段落。时间由程序计算，不信任 AI。"""
    paras = paragraphs(segments)
    if not paras:
        return summary
    starts = [p[0] for p in paras]
    grams = [_grams(p[1]) for p in paras]
    lines, in_outline, cursor = summary.splitlines(), False, 0
    for i, ln in enumerate(lines):
        if ln.startswith("### "):
            in_outline = "大纲" in ln
            continue
        if in_outline and ln.startswith("- ") and not re.match(r"- \[?\d{1,2}:\d{2}", ln):
            g = _grams(ln[2:])
            best, score = None, 0
            for j in range(cursor, len(paras)):
                sc = len(g & grams[j]) / (len(g) ** 0.5 + 1)
                if sc > score:
                    best, score = j, sc
            if best is not None and score > 0.3:
                cursor = best
                lines[i] = f"- [{_mmss(starts[best])}] " + ln[2:]

    def repl(m):
        t = _parse_ts(m.group(1))
        idx = max([k for k, st in enumerate(starts) if st <= t + 1] or [0])
        return f"[[#^{_bid(starts[idx])}|{m.group(1)}]]"

    return re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", repl, "\n".join(lines))


def outline_seconds(linked_summary: str) -> list[float]:
    """从已链接的摘要里取「内容大纲」各条的秒数（给截图用）。"""
    m = re.search(r"### 内容大纲\n(.*?)(?=\n### |\Z)", linked_summary or "", re.S)
    return [float(x) for x in re.findall(r"\[\[#\^t(\d+)\|", m.group(1))] if m else []


def frames_section(vault: Path, cfg: dict, info: dict) -> list[str]:
    d = vault / cfg.get("frames_dir", "AI_Learning_System/00_System/视频截图") / f"{info.get('platform', 'video')}_{info.get('source_id', '')}"
    if not d.exists():
        return []
    imgs = sorted(d.glob("t*.jpg"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0))
    if not imgs:
        return []
    out = ["## 关键画面", ""]
    for im in imgs:
        sec = int(re.sub(r"\D", "", im.stem) or 0)
        rel = im.relative_to(vault).as_posix()
        out += [f"[{_mmss(sec)}] ![[{rel}|360]]", ""]
    return out


def timestamped_text(segments: list[dict]) -> str:
    """给 AI 摘要用：每约 30 秒一段，前面带 [mm:ss]。"""
    return "\n".join(f"[{_mmss(st)}] {para}" for st, para in paragraphs(segments))


def find_existing(vault: Path, notes_dir: str, source_id: str) -> Path | None:
    """按视频 ID 去重：搜整个来源目录（notes_dir 的上一级，例如 01_Sources），
    兼容 source_id: "123" 和 source_id: "douyin:123" 两种写法，避免和旧笔记重复。"""
    if not source_id:
        return None
    root = (vault / notes_dir).parent
    pat = re.compile(rf'^source_id:\s*"?(?:\w+:)?{re.escape(source_id)}"?\s*$', re.M)
    own = vault / notes_dir
    hits = []
    for p in root.rglob("*.md"):
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
        except OSError:
            continue
        if pat.search(head):
            hits.append(p)
    hits.sort(key=lambda x: (x.parent != own, str(x)))  # 优先返回本工具目录里的
    return hits[0] if hits else None


def write_raw(vault: Path, raw_dir: str, key: str, info: dict, tr: dict) -> dict:
    d = vault / raw_dir
    d.mkdir(parents=True, exist_ok=True)
    stem = _safe(key, 80)
    js = d / f"{stem}.transcript.json"
    txt = d / f"{stem}.transcript.txt"
    js.write_text(json.dumps({"source": info, "transcript": tr}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"[{_mmss(s['start'])} -> {_mmss(s['end'])}] {s['text']}" for s in tr["segments"]]
    txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": js, "txt": txt}


# 中文名 -> (slug, 关键词)。TAXO：中文分类 -> knowledge 库 Taxonomy.md 里已有的 Domain 词。
# 2026-11-06：只用 Taxonomy 现有词（原来的 Developer Tools、Cybersecurity、Society、Life、Other 不在 Taxonomy 里）；
# 没有合适的词留空，笔记写 domain: []（Taxonomy 规定：没有值 = 未记录，不等于低质量）。
TAXO = {"AI工具": "AI", "编程开发": "Productivity", "健身": "Fitness", "财经": "Finance", "网络安全": "Risk Management",
        "职业发展": "Career", "商业": "Business", "社会人文": "Learning", "学习方法": "Learning", "生活": "", "其他": ""}
# 旧笔记没有 domain_zh 时，按 domain 列表找索引分组：只放一一对应的词，其余按文件名关键词猜
ZH_OF = {"ai": "AI工具", "fitness": "健身", "men's physique": "健身", "finance": "财经", "career": "职业发展",
         "business": "商业", "learning": "学习方法"}
DOMAINS = {
    "AI工具": ("ai-tools", ["AI", "Codex", "Claude", "GPT", "Gemini", "Agent", "大模型", "提示词", "MCP", "Obsidian", "n8n", "vibecoding"]),
    "编程开发": ("dev", ["编程", "代码", "Python", "前端", "后端", "GitHub", "开发"]),
    "健身": ("fitness", ["健身", "健体", "备赛", "增肌", "减脂", "体脂", "训练", "蛋白"]),
    "财经": ("finance", ["金融", "美股", "黄金", "经济", "美债", "投资", "财经", "通胀", "股"]),
    "网络安全": ("cybersecurity", ["渗透", "漏洞", "黑客", "Kali", "CSRF", "XSS", "网安", "安全"]),
    "职业发展": ("career", ["求职", "面试", "简历", "职业", "工作", "副业", "自由职业"]),
    "商业": ("business", ["商业", "成交", "客户", "企业", "营销", "销售", "老板"]),
    "社会人文": ("society", ["社会", "哲学", "历史", "政治", "马克思", "恩格斯", "性别", "婚姻", "家庭", "文化", "心理"]),
    "学习方法": ("learning", ["学习", "记忆", "笔记", "读书"]),
    "生活": ("life", ["生活", "旅行", "美食"]),
    "其他": ("other", []),
}


def _guess_domain(text: str) -> str:
    best, score = "其他", 0
    for name, (_, kws) in DOMAINS.items():
        sc = sum(text.lower().count(k.lower()) for k in kws)
        if sc > score:
            best, score = name, sc
    return best


def extract_meta(summary: str | None, info: dict) -> tuple[str | None, str, str]:
    """从 AI 摘要的「### 分类」取领域/主题，并把这一节从摘要正文里拿掉；没有就按关键词猜。"""
    domain, topic = "", ""
    if summary:
        m = re.search(r"### 分类\n(.*?)(?=\n### |\Z)", summary, re.S)
        if m:
            sec = m.group(1)
            d = re.search(r"领域[：:]\s*([^\n；;，,]+)", sec)
            t = re.search(r"主题[：:]\s*([^\n]+)", sec)
            domain = (d.group(1).strip() if d else "")
            topic = (t.group(1).strip(" 。") if t else "")[:20]
            summary = (summary[:m.start()] + summary[m.end():]).strip()
    if domain not in DOMAINS:
        domain = _guess_domain(" ".join([info.get("title", ""), info.get("desc", ""), " ".join(info.get("hashtags") or [])]))
    return summary, domain, topic or (info.get("title") or "")[:20]


def _fm_value(text: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.*)$", text, re.M)
    return m.group(1).strip().strip('"') if m else ""


def _fm_domains(text: str) -> list[str]:
    """读 frontmatter 的 domain（单值或 YAML 列表）。2026-11-06：原来的正则把列表读成「- Fitness」，
    查不到分类只能按文件名猜，「脱水与充碳」「高钠与零钠」两篇健身笔记因此被分进「其他」。"""
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return []
    try:
        import yaml
        v = (yaml.safe_load(m.group(1)) or {}).get("domain")
    except Exception:  # noqa: BLE001
        return []
    vals = v if isinstance(v, list) else [v]
    return [str(x).strip() for x in vals if x not in (None, "")]


def rebuild_index(vault: Path, cfg: dict) -> Path:
    """00_System/视频学习索引.md：按领域列出全部视频笔记（每次写笔记后自动重建）。"""
    d = (vault / cfg["notes_dir"]).parent  # 整个 01_Sources：包括旧的「健体备赛视频笔记」「逐条视频笔记」
    groups: dict[str, list] = {}
    for p in sorted(d.rglob("*.md"), key=lambda x: x.name, reverse=True):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if not re.search(r"^source_type:\s*video\s*$", t[:2000], re.M):
            continue
        dom = _fm_value(t, "domain_zh")
        if not dom:  # 旧笔记没有 domain_zh：按 domain 列表依次找一一对应的分组，都没有再按文件名关键词猜
            doms = _fm_domains(t)
            dom = next((ZH_OF[x.lower()] for x in doms if x.lower() in ZH_OF), "") or _guess_domain(p.stem + " " + " ".join(doms))
        core = re.search(r"### 一句话核心\n(.+)", t)
        h1 = re.search(r"^# (.+)$", t, re.M)
        groups.setdefault(dom, []).append((p.stem, _fm_value(t, "title") or (h1.group(1).strip() if h1 else p.stem), _fm_value(t, "creator"),
                                          _fm_value(t, "published")[:10], _fm_value(t, "duration"),
                                          _fm_value(t, "human_reviewed").lower(), core.group(1).strip() if core else ""))
    order = list(DOMAINS) + ["未分类"]
    total = sum(len(v) for v in groups.values())
    out = ["---", "type: moc", "domain: Knowledge Management", "topic: 视频学习索引", "status: active",
           f"updated: {time.strftime('%Y-%m-%d')}", "tags:", "  - system/index", "  - knowledge/moc", "  - source/video",
           "aliases:", "  - 视频索引", "  - Video Index", "---", "", "# 视频学习索引", "",
           f"> 由 video2obsidian 自动生成，请勿手改（会被覆盖）。共 {total} 条。表格视图见 [[视频库.base]]。", "",
           "🟡 = 机器转录 + AI 摘要，还没人工看过。看过、改过后把笔记属性 `human_reviewed` 改成 `true`。", ""]
    for dom in sorted(groups, key=lambda x: order.index(x) if x in order else 99):
        out += [f"## {dom}（{len(groups[dom])}）", ""]
        for stem, title, creator, pub, dur, rv, core in groups[dom]:
            flag = " 🟡" if rv == "false" else ""
            out.append(f"- [[{stem}|{title}]]{flag} — {creator} · {pub} · {dur}")
            if core:
                out.append(f"    - {core[:80]}")
        out.append("")
    p = vault / cfg.get("index_note", "00_System/视频学习索引.md")
    p.write_text("\n".join(out), encoding="utf-8")
    return p


def write_note(vault: Path, cfg: dict, info: dict, tr: dict, summary: str | None, summary_model: str = "",
               path: Path | None = None, tail: str | None = None) -> Path:
    notes_dir = vault / cfg["notes_dir"]
    notes_dir.mkdir(parents=True, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    pub_date = (info.get("published") or "")[:10] or today
    fname = f"{pub_date} - {_safe(info.get('title'), 50)} - {_safe(info.get('creator') or info.get('platform'), 20)}.md"
    existing = path or find_existing(vault, cfg["notes_dir"], info.get("source_id", ""))
    path = existing or (notes_dir / fname)
    summary, domain_zh, topic = extract_meta(summary, info)
    domain = DOMAINS[domain_zh][0]

    key = f"{info.get('platform', 'video')}_{info.get('source_id') or int(time.time())}"
    raw = write_raw(vault, cfg["raw_dir"], key, info, tr)
    raw_rel = Path(cfg["raw_dir"]) / raw["txt"].name

    tags = ["source/video", f"domain/{domain}", "status/ai-summary" if summary else "status/raw"]
    text_all = "".join(s["text"] for s in tr["segments"])
    fm = [
        "---",
        "type: source",
        "source_type: video",
        f"title: {_yaml(info.get('title'))}",
        f"platform: {_yaml(info.get('platform'))}",
        f'source_id: "{info.get("source_id", "")}"',
        f"url: {info.get('url', '')}",
        f"creator: {_yaml(info.get('creator'))}",
        f"creator_url: {info.get('creator_url', '')}",
        f"published: {_yaml(info.get('published'))}",
        f"captured: {today}",
        f"duration: {_yaml(_mmss(info.get('duration') or tr.get('duration') or 0))}",
        *(["domain:", f"  - {TAXO[domain_zh]}"] if TAXO.get(domain_zh) else ["domain: []"]),
        f"domain_zh: {domain_zh}",
        f"topic: {_yaml(topic)}",
        f"status: {'processed' if summary else 'raw'}",
        # 摘要写到一半断了（summarizer 在模型名后面加「⚠ 不完整」）：网页笔记列表据此显示「补摘要」
        *(["summary_incomplete: true"] if summary and "不完整" in (summary_model or "") else []),
        "human_reviewed: false",
        "evidence_level: C",
        "claim_type: creator-claim",
        "confidence: medium",
        f"platform_tags: {json.dumps(info.get('hashtags') or [], ensure_ascii=False)}",
        f"transcript_engine: {_yaml(tr.get('engine'))}",
        f"fetch_method: {_yaml(info.get('method'))}",
        *([f"download_quality: {_yaml(info.get('quality'))}"] if info.get("quality") else []),
        "importance:",
        "tags:",
        *[f"  - {t}" for t in dict.fromkeys(tags)],
        "---",
        "",
        f"# {info.get('title')}",
        "",
        "## Source Diagnostic",
        "",
        f"- available text: 本地音频机器转录（{tr.get('engine')}），{len(tr['segments'])} 段，约 {len(text_all)} 字。",
        f"- raw evidence: `{raw_rel.as_posix()}`（同名 .json 含时间戳原始数据）",
        "- confidence: 机器转录，未人工校对；人名、专有名词、数字可能识别错误。",
        "- limitations: 只转录音频；纯字幕/无人声视频转录会很少或为空，需另做 OCR。"
        + ("" if summary else " 本笔记暂无 AI 摘要。"),
        "",
        "## Metadata",
        "",
        f"- Creator: {info.get('creator') or '—'}" + (f"（[主页]({info['creator_url']})）" if info.get("creator_url") else ""),
        f"- Platform: {info.get('platform')}",
        f"- Duration: {_mmss(info.get('duration') or tr.get('duration') or 0)}",
        f"- Published: {info.get('published') or '—'}",
        f"- Source: {info.get('url')}",
        "",
    ]
    if info.get("desc"):
        fm += ["## 原视频文案", "", "> " + info["desc"].replace("\n", "\n> "), ""]
    if summary:
        summary = link_summary(summary, tr["segments"])
        fm += [f"## AI 摘要（{summary_model}，未核验，仅供参考）", "", summary.strip(), ""]
    fr = frames_section(vault, cfg, info)
    if fr:
        fm += link_summary("\n".join(fr), tr["segments"]).splitlines()
    fm += ["## 转录全文", ""]
    paras = paragraphs(tr["segments"])
    if not paras:
        fm += ["（没有识别到语音）", ""]
    fm += transcript_lines(tr["segments"])
    fm += [
    ]
    fm += (["## My Takeaways" + tail.rstrip(), ""] if tail else
           ["## My Takeaways", "", "-", "", "## Follow-Up Questions", "", "-", "", "## Next Actions", "", "-", ""])
    path.write_text("\n".join(fm), encoding="utf-8")
    try:
        rebuild_index(vault, cfg)
    except Exception:  # noqa: BLE001
        pass
    return path


def obsidian_uri(vault: Path, note: Path) -> str:
    rel = note.relative_to(vault).with_suffix("").as_posix()
    return f"obsidian://open?vault={quote(vault.name)}&file={quote(rel)}"
