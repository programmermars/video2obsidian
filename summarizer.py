"""AI 摘要：优先用电脑上的 AgentChat skill（你已登录的网页 AI，免 API），其次本地 OpenAI 兼容接口（RTX qwen）。失败就跳过。"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import requests

PROMPT = """你是严谨的学习笔记编辑。下面是一条视频的【机器语音转录】，带时间戳，存在同音字、专有名词识别错误（例如英文产品名被听成别的词）。

工作要求：
1. 先结合上下文和常识，在心里把明显的识别错误还原成正确词（例如 "Cloud Code" 在讲 AI 编程工具时多半是 "Claude Code"）；正文一律用还原后的词，并在「转录疑似错误」里列出。拿不准的保留原词并标注（存疑）。你的知识有截止日期：转录里清楚出现的模型名、版本号、产品名（例如 Opus 5.5、某个你没听过的新模型或工具），不要按你的知识改成旧版本或别的产品；只修正明显的同音错字，不认识的保留原词并标（存疑）。专有名词参考表里的写法优先。
2. 只写视频里真正讲到的内容，不补充视频外的知识。严禁用你自己的知识判断视频说法的真假（你的知识可能过时）：不要写"实际上…""事实是…""这是错误的"。区分「作者的事实陈述」和「作者的观点/建议」，观点才写"作者认为/建议"。作者给出的数字、比例、收入/价格、以及"绝大多数""一定""很正常"这类普遍性判断，一律写成"作者称…"，并且同时列进「需要核实的说法」。
3. 每条要点要具体：写出结论 + 理由或例子，不要空泛概括；尽量在句末标注出处时间，如 [12:30]。
4. 用简体中文，直接输出下面的结构，不要开场白和结尾客套。每个条目都必须以「◆」开头（这个符号用来分隔条目，不能省略）。

### 分类
◆ 领域：只能从「AI工具、编程开发、健身、财经、商业、社会人文、网络安全、职业发展、学习方法、生活、其他」里选一个
◆ 主题：不超过 12 个字的具体主题

### 一句话核心
（不超过 50 字，说清这条视频到底解决什么问题、给出什么结论）

### 内容大纲
◆ [mm:ss] 章节主题：一句话说明（按时间顺序，4–10 条，每条必须以转录里的时间戳开头）

### 关键要点
◆ （5–10 条，按重要性排序，句末标注出处时间如 [12:30]）

### 可执行的行动
◆ （读者看完可以照做的具体步骤，0–6 条；没有就写"无"）

### 转录疑似错误
◆ 原文「…」→ 应为「…」（理由）（没有就写"无"）

### 需要核实的说法
◆ [mm:ss] 作者的原话或说法 —— 需要核实的类型（数据 / 价格 / 时间 / 版本 / 医学 / 财务 / 因果推断），只说明应去哪里查证，不下结论（没有就写"无"）

专有名词参考（转录里的近音词多半是这些）：{glossary}

视频标题：{title}
作者：{creator}
时长：{duration}

转录：
{text}
"""

RECEIPT = re.compile(r"\[receipt\] AGENTCHAT_RUN (\{.*\})")


def _headers(cfg: dict) -> dict:
    key = cfg.get("llm_api_key")
    return {"Authorization": f"Bearer {key}"} if key else {}


# ---------------------------------------------------------------- AgentChat
def agentchat_ok(cfg: dict) -> bool:
    node, entry = cfg.get("agentchat_node", ""), cfg.get("agentchat_entry", "")
    return bool(node and entry and Path(node).is_file() and Path(entry).is_file())


def via_agentchat(prompt: str, cfg: dict) -> tuple[str, str]:
    entry = Path(cfg["agentchat_entry"])
    timeout = int(cfg.get("agentchat_timeout", 300))
    cmd = [cfg["agentchat_node"], str(entry), f"--timeout={timeout * 1000}",
           f"--timeout-per-provider={int(cfg.get('agentchat_provider_timeout', 420)) * 1000}", "--ephemeral-tab", "--no-download-images",
           f"--from={cfg.get('agentchat_provider', 'gemini')}"]
    env = dict(os.environ)
    if cfg.get("agentchat_gemini_strict"):
        env["AGENTCHAT_GEMINI_MODEL_POLICY"] = "strict"
    p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(entry.parents[2]), env=env, timeout=timeout + 60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    receipt = {}
    m = RECEIPT.findall(p.stderr or "")
    if m:
        try:
            receipt = json.loads(m[-1])
        except json.JSONDecodeError:
            pass
    try:
        Path(__file__).with_name("logs").joinpath("agentchat_last.log").write_text(
            f"exit={p.returncode}\n" + (p.stderr or ""), encoding="utf-8")
    except OSError:
        pass
    if p.returncode != 0 or not (p.stdout or "").strip():
        raise RuntimeError(f"AgentChat 失败 exit={p.returncode} {receipt.get('error') or (p.stderr or '')[-300:]}")
    used = receipt.get("provider_used") or "web AI"
    err = p.stderr or ""
    if used == "Gemini":
        if "Pro Extended Thinking active" in err or "Pro Extended Thinking already active" in err:
            used = "Gemini Pro（扩展思考）"
        elif "keeping Pro" in err:
            used = "Gemini Pro"
        elif "Flash model active" in err:
            used = "Gemini Flash（Pro 不可用时的降级）"
        elif "DEFAULT model" in err:
            used = "Gemini（默认模型，切换 Pro 失败）"
    return p.stdout.strip(), f"AgentChat → {used}（run_id {receipt.get('run_id', '?')}）"


# ---------------------------------------------------------------- OpenAI 兼容（RTX qwen）
def openai_ok(cfg: dict) -> bool:
    base = cfg.get("llm_base_url")
    if not base:
        return False
    try:
        return requests.get(base.rstrip("/") + "/models", timeout=3, headers=_headers(cfg)).ok
    except Exception:  # noqa: BLE001
        return False


def via_openai(prompt: str, cfg: dict) -> tuple[str, str, str]:
    """返回 (文字, 模型名, finish_reason)；finish_reason == "length" 表示写到输出上限被截断。
    2026-11-04：18:43 那次本地 qwen 写到一半就断了，以前不设上限也不看 finish_reason，半截照样写进笔记。"""
    body = {"model": cfg.get("llm_model", "qwen3.5:4b"), "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2, "stream": False, "max_tokens": int(cfg.get("llm_max_tokens", 8192))}
    url = cfg["llm_base_url"].rstrip("/") + "/chat/completions"
    kw = dict(headers=_headers(cfg), timeout=int(cfg.get("llm_timeout", 600)))
    r = requests.post(url, json=body, **kw)
    if r.status_code == 400:  # 个别网关不认 max_tokens：去掉再试一次，别因为这个参数丢了摘要
        body.pop("max_tokens")
        r = requests.post(url, json=body, **kw)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    out = choice["message"]["content"] or ""
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    out = re.sub(r"<think>.*\Z", "", out, flags=re.S)  # 思考到一半被截断，没有 </think>
    return out.strip(), cfg.get("llm_model", "local"), str(choice.get("finish_reason") or "")


SECTIONS = ["分类", "一句话核心", "内容大纲", "关键要点", "可执行的行动", "转录疑似错误", "需要核实的说法"]


_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>", re.I)
_CREATED = re.compile(r"(?:分类\s*)?创建于\s*\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}")
_OFFER = re.compile(r"(需要我|要不要我|如果你需要|如果需要的话|我可以帮你|你可以告诉我)[^\n]*$")


def _strip_web_ui(text: str) -> str:
    """去掉网页 AI 界面跟在回答后面的东西：回复里的图片、「创建于」时间戳、结尾的推荐追问。
    2026-11-04：Qwen 网页把「分类创建于 11-04 11:30需要我帮你…？好的，帮我整理13篇来源」和一张图片抓进了摘要。"""
    t = _IMG.sub("", text or "")
    m = _CREATED.search(t)
    if m:
        t = t[:m.start()]
    tail = max(0, len(t) - 400)          # 只看结尾，避免误删正文里引用的话
    m = _OFFER.search(t, tail)
    if m:
        t = t[:m.start()]
    return t.rstrip()


def _strip_md(text: str) -> str:
    """去掉网页 AI 回答里的 Markdown 记号，正文里的 # 和 * 保留（C#、2*3）。
    2026-11-04 之前是把所有 # 和 * 都删掉，「C#」会变成「C」。"""
    t = text or ""
    t = re.sub(r"#{2,6}[ \t]*", "", t)                         # ## / ### 标题（网页抓回来常常不在行首）
    t = re.sub(r"(?m)^[ \t]*#[ \t]+", "", t)                   # 行首的 # 标题
    t = re.sub(r"(?<!\S)#(?=\s)", "", t)                       # 单独一个 #
    t = re.sub(r"(?<![\w&/])#(?=[^\s#])", "", t)               # #话题：去掉 #，免得 Obsidian 当成标签
    t = t.replace("**", "")                                     # 粗体
    t = re.sub(r"(?m)^([ \t]*)\*[ \t]+", r"\1- ", t)            # 「* 」开头的列表 → 「- 」
    t = re.sub(r"(?<![A-Za-z0-9*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![A-Za-z0-9*])", r"\1", t)  # *斜体*
    return t.strip()


_LEAD = re.compile(r"^(?:[◆•·]+[ \t]*|[-*][ \t]+)+")


def _clean_line(s: str) -> str:
    """去掉开头的列表记号。「-」「*」后面要有空格才算记号，免得把「-3%」的负号删掉。"""
    return _LEAD.sub("", s.strip()).strip()


def _bullet(item: str) -> str:
    """一条要点里带换行（Claude 常写「小标题 + 几段解释」）时，第一行做要点、其余做缩进子项，
    不然 Markdown 列表会被空行截断，后面几段变成散落的正文。2026-11-04 加。"""
    lines = [_clean_line(ln) for ln in item.split("\n")]
    lines = [ln for ln in lines if ln]
    if not lines:
        return "- 无"
    return "- " + lines[0] + "".join(f"\n  - {ln}" for ln in lines[1:])


def normalize(text: str) -> str:
    """网页 AI 抓回来的文字常丢了换行和 Markdown；按固定小标题重新排版。"""
    text = _strip_web_ui(text)
    t = _strip_md(text)
    pos = []
    for name in SECTIONS:
        m = re.search(re.escape(name), t)
        if m:
            pos.append((m.start(), m.end(), name))
    if len(pos) < 2:
        return text.strip()
    pos.sort()
    out = []
    for i, (st, en, name) in enumerate(pos):
        body = t[en: pos[i + 1][0] if i + 1 < len(pos) else len(t)].strip(" ：:\n")
        out.append(f"### {name}")
        if name == "一句话核心":
            out.append(re.sub(r"\s*\n\s*", "", body).strip(" ◆"))
        else:
            if "◆" in body:
                parts = body.split("◆")
            elif re.search(r"\n\s*[-•*]", "\n" + body):
                parts = re.split(r"\n+", body)
            elif name == "内容大纲":
                parts = re.split(r"\n+|(?=\[\d{1,2}:\d{2})|(?<=[。])", body)
            elif name == "转录疑似错误":
                parts = re.split(r"\n+|(?=原文)", body)
            else:
                parts = re.split(r"\n+|(?<=[。！？])", body)
            items = [_clean_line(x) for x in parts]
            items = [x for x in items if x and x not in ("。",)]
            out += [_bullet(x) for x in items] or ["- 无"]
        out.append("")
    return "\n".join(out).strip()


INCOMPLETE_MARK = "⚠ 不完整"


class Incomplete(RuntimeError):
    """摘要写到一半就断了。先留着：后面的后端都给不出完整的，就保存最长的一份，标题注明不完整。"""

    def __init__(self, why: str, text: str = "", model: str = ""):
        super().__init__(why)
        self.text, self.model = text, model


def _check_complete(summary: str, model: str = "", why: str = "") -> None:
    """网页 AI 超时会把写到一半的回答当成功交回来（2026-11-04：Claude 写到「对标可以是电」就断了）。
    最后两节都没有（或模型自己说写到了上限）就算不完整，先换下一个后端。"""
    tail = SECTIONS[-2:]  # 转录疑似错误、需要核实的说法
    if why:
        raise Incomplete(f"摘要不完整（{why}，{len(summary)} 字）", summary, model)
    if not any(f"### {name}" in summary for name in tail):
        raise Incomplete(f"摘要不完整（缺「{'」「'.join(tail)}」，疑似超时被截断，{len(summary)} 字）", summary, model)


# ---------------------------------------------------------------- 入口
def available(cfg: dict) -> bool:
    return agentchat_ok(cfg) or openai_ok(cfg)


def summarize(title: str, creator: str, text: str, cfg: dict, log=print, duration: str = "") -> tuple[str, str]:
    limit = int(cfg.get("llm_max_chars", 20000))
    note = ""
    if len(text) > limit:
        note = f"\n\n> 注：转录共 {len(text)} 字，摘要只基于前 {limit} 字。"
        text = text[:limit]
    import glossary
    prompt = PROMPT.format(title=title, creator=creator, text=text, duration=duration or "未知",
                           glossary="、".join(glossary.load()[0]) or "无")
    order = cfg.get("summary_backends", ["agentchat", "openai"])
    errors, partials = [], []
    for b in order:
        try:
            if b == "agentchat" and agentchat_ok(cfg):
                log(f"AI 摘要：AgentChat（从 {cfg.get('agentchat_provider', 'gemini')} 开始，自动降级）")
                out, model = via_agentchat(prompt, cfg)
                out = normalize(out)
                _check_complete(out, model)
                return out + note, model
            if b == "openai" and openai_ok(cfg):
                log("AI 摘要：本地 qwen（RTX）")
                out, model, finish = via_openai(prompt, cfg)
                out = normalize(out)
                _check_complete(out, model, "写到输出上限被截断" if finish == "length" else "")
                return out + note, model
        except Incomplete as exc:
            if len(exc.text) >= 200:
                partials.append((exc.text, exc.model))
            errors.append(f"{b}: {exc}")
            log(f"  {b} {exc}，换下一个")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{b}: {exc}")
            log(f"  {b} 失败：{exc}")
    if partials:
        text, model = max(partials, key=lambda p: len(p[0]))
        log("  没有拿到完整的摘要，先保存最长的一份（标题注明不完整；网页笔记列表里点「补摘要」可重做）")
        warn = ("> ⚠ 这份摘要不完整：AI 写到一半断了，后面几节缺失。"
                "在 video2obsidian 网页的笔记列表里点「补摘要」可以重做（不用重新下载和转录）。")
        return text + "\n\n" + warn + note, f"{model}，{INCOMPLETE_MARK}"
    raise RuntimeError("没有可用的摘要后端；" + " | ".join(errors))
