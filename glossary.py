"""专有名词表：glossary.txt。"""
from __future__ import annotations

from pathlib import Path

PATH = Path(__file__).with_name("glossary.txt")


def load() -> tuple[list[str], list[tuple[str, str]]]:
    terms, fixes = [], []
    try:
        lines = PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return terms, fixes
    for ln in lines:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "=>" in ln:
            a, b = (x.strip() for x in ln.split("=>", 1))
            if a and b:
                fixes.append((a, b))
                if b not in terms:
                    terms.append(b)
        elif ln not in terms:
            terms.append(ln)
    return terms, fixes


def hotwords(limit_chars: int = 200) -> str:
    out = ""
    for t in load()[0]:
        if len(out) + len(t) + 1 > limit_chars:
            break
        out += ("、" if out else "") + t
    return out


def apply(segments: list[dict]) -> list[dict]:
    fixes = load()[1]
    if fixes:
        for s in segments:
            for a, b in fixes:
                s["text"] = s["text"].replace(a, b)
    return segments
