"""视频字幕（D-043）：WebVTT → 带时间标记的纯文本；按时间段切出一节要依据的那一段。纯函数，不做 IO。

文本格式：每段一行，以 [mm:ss] 或 [h:mm:ss] 开头，例如 "[12:30] so the first thing we can do is ..."。
备课 agent 读到的、section 简报里切出来的、页面上"对应老师视频 12:30–18:40"用的都是这个时间。
"""
from __future__ import annotations

import re

_CUE = re.compile(r"^(\d{1,2}:)?(\d{1,2}):(\d{2})\.\d{3}\s+-->")
_TAG = re.compile(r"<[^>]+>")
_MARK = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\]")
_RANGE = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2})\s*[-–~]\s*((?:\d+:)?\d{1,2}:\d{2})\s*$")


def clock(seconds: int) -> str:
    h, rest = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def parse_clock(text: str) -> int:
    """"12:30" / "1:02:03" → 秒。格式不对抛 ValueError。"""
    parts = [int(x) for x in text.strip().split(":")]
    if not 2 <= len(parts) <= 3 or any(p < 0 for p in parts) or parts[-1] >= 60:
        raise ValueError(text)
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def parse_range(text: str) -> tuple[int, int] | None:
    """"12:30-18:40" → (750, 1120)；格式不对或起点不早于终点返回 None。"""
    m = _RANGE.match(text or "")
    if not m:
        return None
    try:
        a, b = parse_clock(m[1]), parse_clock(m[2])
    except ValueError:
        return None
    return (a, b) if a < b else None


def vtt_to_text(vtt: str, every: int = 30) -> str:
    """WebVTT → 每 every 秒左右一段、段首带 [mm:ss] 的纯文本。
    自动字幕会把上一条滚动重复一遍，连续重复的行只留一次。"""
    paras: list[tuple[int, list[str]]] = []
    start, last = None, ""
    for line in vtt.splitlines():
        m = _CUE.match(line)
        if m:
            start = int((m[1] or "0:")[:-1]) * 3600 + int(m[2]) * 60 + int(m[3])
            continue
        text = " ".join(_TAG.sub("", line).split())
        if start is None or not text or text == last or text.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        last = text
        if not paras or start - paras[-1][0] >= every:
            paras.append((start, []))
        paras[-1][1].append(text)
    return "\n".join(f"[{clock(t)}] {' '.join(words)}" for t, words in paras)


def duration(text: str) -> int:
    """字幕文本最后一个时间标记（秒），没有标记返回 0。"""
    marks = [_mark(l) for l in text.splitlines()]
    return max([m for m in marks if m is not None], default=0)


def slice_text(text: str, start: int, end: int, pad: int = 30) -> str:
    """只留 [start - pad, end + pad] 秒之间的段落（写一节时只给这一节对应的那段视频）。"""
    out = []
    for line in text.splitlines():
        t = _mark(line)
        if t is not None and start - pad <= t <= end + pad:
            out.append(line)
    return "\n".join(out)


def _mark(line: str) -> int | None:
    m = _MARK.match(line)
    return None if not m else int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])


# WHY: domain 不 import urllib（依赖规则把它算作 IO 模块），网址用正则拆
_YT = re.compile(r"^https?://(?:www\.|m\.)?(?P<host>youtube\.com|youtu\.be)(?P<path>/[^?#]*)(?:\?(?P<query>[^#]*))?")
_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def youtube_target(url: str) -> tuple[str, str] | None:
    """("video", id) / ("playlist", id)；不是 YouTube 的视频或播放列表返回 None。"""
    m = _YT.match((url or "").strip())
    if not m:
        return None
    q = dict(kv.split("=", 1) for kv in (m["query"] or "").split("&") if "=" in kv)
    if m["host"] == "youtu.be":
        vid = m["path"].strip("/")
        return ("video", vid) if _ID.match(vid) else None
    if m["path"] == "/watch" and _ID.match(q.get("v", "")):
        return "video", q["v"]
    if m["path"] == "/playlist" and re.match(r"^[A-Za-z0-9_-]+$", q.get("list", "")):
        return "playlist", q["list"]
    return None
