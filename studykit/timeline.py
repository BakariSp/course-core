"""学习时间（D-016）：从所有事件日志算出一次次"学习会话"，不单独存会话。

事件来源（都只追加）：
    progress/study_log.jsonl   课程页：打开小节、心跳、终端命令、检查点……；手动补录（manual）
    progress/attempts.jsonl    提交练习题
    progress/runs.jsonl        代码题的每次运行

INVARIANT: 会话是派生数据。相邻两个事件间隔不超过 GAP 分钟就算同一次学习；
一个间隔算给前一个事件所在的小节（或练习题）。改规则只改这里，历史数据会按新规则重新算。
WHY: 学习发生在视频、终端和课程页之间（F-029）。只算页面在前台的时间，第 1-3 节 48 分钟只记下 4 分钟。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from studykit import store

GAP = 15           # 分钟：超过这么久没有任何活动，就算这次学习结束了
TAIL = 1           # 分钟：会话里最后一个事件之后算多少时间


@dataclass
class Event:
    ts: dt.datetime
    label: str                  # 这段时间在学什么，如 "tools-01-shell 第 4 节"、"练习题 tools/01-shell"
    unit: str | None = None
    section: int | None = None
    plan: str | None = None


@dataclass
class Session:
    start: dt.datetime
    end: dt.datetime
    minutes: float
    by_label: dict[str, float] = field(default_factory=dict)
    manual: bool = False

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(timespec="minutes"), "end": self.end.isoformat(timespec="minutes"),
                "minutes": round(self.minutes, 1), "manual": self.manual,
                "by_label": {k: round(v, 1) for k, v in self.by_label.items()}}


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s[:19])


def section_label(unit: str, section) -> str:
    return f"{unit} 第 {int(section) + 1} 节" if isinstance(section, int) else unit


def collect(plan_of=None) -> tuple[list[Event], list[dict]]:
    """所有带时间的事件（按时间排序），以及手动补录的记录。plan_of(unit, event) 用来给旧事件补上课程版本。"""
    events, manual = [], []
    for e in store.load_study_log():
        if e.get("event") == "manual":
            manual.append(e)
            continue
        unit, section = e.get("unit"), e.get("section")
        plan = e.get("plan") or (plan_of(unit, e) if plan_of and unit else None)
        events.append(Event(_ts(e["ts"]), section_label(unit, section) if unit else "课程页", unit,
                             section if isinstance(section, int) else None, plan))
    for a in store.load_attempts():
        if a.get("quiz") and a.get("quiz") != "external" and a.get("grader") != "agent":
            events.append(Event(_ts(a["ts"]), f"练习题 {a['quiz']}"))
    for r in store.load_runs():
        events.append(Event(_ts(r["ts"]), f"练习题 {r.get('quiz')}"))
    events.sort(key=lambda e: e.ts)
    return events, manual


def sessions(events: list[Event], manual: list[dict] | None = None, gap: int = GAP) -> list[Session]:
    out: list[Session] = []
    cur: list[Event] = []

    def close():
        if not cur:
            return
        by: dict[str, float] = defaultdict(float)
        for a, b in zip(cur, cur[1:]):
            by[a.label] += (b.ts - a.ts).total_seconds() / 60
        by[cur[-1].label] += TAIL
        end = cur[-1].ts + dt.timedelta(minutes=TAIL)
        out.append(Session(cur[0].ts, end, (end - cur[0].ts).total_seconds() / 60, dict(by)))

    for e in events:
        if cur and (e.ts - cur[-1].ts).total_seconds() / 60 > gap:
            close()
            cur = []
        cur.append(e)
    close()
    for m in manual or []:
        start = _ts(m.get("start") or m["ts"])
        minutes = float(m.get("minutes") or 0)
        label = m.get("note") or "手动补录"
        out.append(Session(start, start + dt.timedelta(minutes=minutes), minutes, {label: minutes}, manual=True))
    return sorted(out, key=lambda s: s.start)


def section_minutes(events: list[Event], unit: str, plan: str | None, gap: int = GAP) -> dict[int, float]:
    """某个单元（某个课程版本）每一节实际花了多少分钟：按会话里的间隔归属算。"""
    spent: dict[int, float] = defaultdict(float)
    prev: Event | None = None
    def mine(e: Event | None) -> bool:
        return e is not None and e.unit == unit and e.section is not None and (plan is None or e.plan == plan)

    for e in events:
        if mine(prev):
            d = (e.ts - prev.ts).total_seconds() / 60
            # 和 sessions() 一致：会话中间的间隔算给前一个事件；会话结束时收尾算 TAIL 分钟。
            spent[prev.section] += d if d <= gap else TAIL
        prev = e
    if mine(prev):
        spent[prev.section] += TAIL
    return dict(spent)


def daily(sess: list[Session]) -> list[dict]:
    days: dict[str, list[Session]] = defaultdict(list)
    for s in sess:
        days[s.start.date().isoformat()].append(s)
    return [{"date": d, "minutes": round(sum(s.minutes for s in ss), 1), "sessions": [s.as_dict() for s in ss]}
            for d, ss in sorted(days.items())]


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


def report(days: int | None = None, plan_of=None) -> str:
    events, manual = collect(plan_of)
    rows = daily(sessions(events, manual))
    if days:
        cutoff = (store.now().date() - dt.timedelta(days=days - 1)).isoformat()
        rows = [r for r in rows if r["date"] >= cutoff]
    if not rows:
        return "还没有学习记录。"
    lines = []
    week: dict[str, float] = defaultdict(float)
    for r in rows:
        lines.append(f"{r['date']}  合计 {fmt_minutes(r['minutes'])}")
        for s in r["sessions"]:
            what = "、".join(f"{k} {fmt_minutes(v)}" for k, v in sorted(s["by_label"].items(), key=lambda kv: -kv[1]))
            tag = "（补录）" if s["manual"] else ""
            lines.append(f"  {s['start'][11:]}–{s['end'][11:]}  {fmt_minutes(s['minutes'])}{tag}  {what}")
        y, w, _ = dt.date.fromisoformat(r["date"]).isocalendar()
        week[f"{y}-W{w:02d}"] += r["minutes"]
    lines += ["", "每周合计：" + "；".join(f"{k} {fmt_minutes(v)}" for k, v in sorted(week.items()))]
    return "\n".join(lines)
