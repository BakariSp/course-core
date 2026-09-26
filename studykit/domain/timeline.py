"""学习时间（D-016）：从学习者的证据算出一次次"学习会话"，不单独存会话。

INVARIANT: 会话是派生数据。相邻两条证据间隔不超过 GAP 分钟就算同一次学习；
一个间隔算给前一条证据所在的小节（或练习题）。改规则只改这里，历史数据按新规则重新算。
只算学习者自己做的事：导师的批改、观察，系统记的"整节通过"都不算学习时间。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from studykit.domain.evidence import Evidence, split_question

GAP = 15           # 分钟：超过这么久没有任何活动，就算这次学习结束了
MAX_AWAY = 120     # 分钟：暂停后说"在学"，最多补算这么久（D-027，和看视频的上限一致）
TAIL = 1           # 分钟：会话里最后一个事件之后算多少时间
NOT_ACTIVITY = {"logged_practice", "logged_time"}   # 课外练习、补录：没有发生在这里的时间点


@dataclass
class Tick:
    ts: dt.datetime
    label: str                  # 这段时间在学什么，如 "tools-01-shell 第 4 节"、"练习题 tools/01-shell"
    unit: str | None = None
    section: int | None = None
    plan: str | None = None
    brk: bool = False           # 学习者说离开的这段没在学：从这里另起一次学习（D-027）


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


def parse_ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s[:19])


def section_label(unit: str, section) -> str:
    return f"{unit} 第 {int(section) + 1} 节" if isinstance(section, int) else unit


def counts_as_study(e: Evidence) -> bool:
    """这条证据会不会算进学习时长（补录的时间段另算）。流程看板也用它，规则只有这一份。"""
    return e.actor.type == "learner" and e.verb not in NOT_ACTIVITY


def ticks(evidence: list[Evidence]) -> tuple[list[Tick], list[Evidence]]:
    """（学习者活动的时间点，按时间排序；补录的时间段）。

    暂停后回来（resumed，D-027）：说"在学"就在离开的那段里补时间点，把它连成同一次学习（最多 MAX_AWAY 分钟）；
    说"没在学"就去掉那段里的时间点（暂停前的心跳），并从回来的那一刻另起一次学习。
    """
    out, manual, cuts = [], [], []
    for e in evidence:
        if e.verb == "logged_time":
            manual.append(e)
            continue
        if not counts_as_study(e):
            continue
        if e.object_type == "question":
            label = f"练习题 {split_question(e.object_id)[0]}"
        elif e.unit:
            label = section_label(e.unit, e.section)
        else:
            label = "课程页"
        ts, brk = parse_ts(e.ts), False
        if e.verb == "resumed":
            away = parse_ts(e.payload["away_start"])
            if not e.payload.get("counted"):
                cuts.append((away, ts))
                brk = True
            elif (ts - away).total_seconds() / 60 <= MAX_AWAY:
                t = away
                while t < ts:
                    out.append(Tick(t, label, e.unit, e.section, e.plan))
                    t += dt.timedelta(minutes=GAP)
            else:
                brk = True
        out.append(Tick(ts, label, e.unit, e.section, e.plan, brk))
    out = [t for t in out if not any(a < t.ts < b for a, b in cuts)]
    out.sort(key=lambda t: t.ts)
    return out, manual


def sessions(ts: list[Tick], manual: list[Evidence] = (), gap: int = GAP) -> list[Session]:
    out: list[Session] = []
    cur: list[Tick] = []

    def close():
        if not cur:
            return
        by: dict[str, float] = defaultdict(float)
        for a, b in zip(cur, cur[1:]):
            by[a.label] += (b.ts - a.ts).total_seconds() / 60
        by[cur[-1].label] += TAIL
        end = cur[-1].ts + dt.timedelta(minutes=TAIL)
        out.append(Session(cur[0].ts, end, (end - cur[0].ts).total_seconds() / 60, dict(by)))

    for t in ts:
        if cur and (t.brk or (t.ts - cur[-1].ts).total_seconds() / 60 > gap):
            close()
            cur = []
        cur.append(t)
    close()
    for m in manual:
        start = parse_ts(m.payload["start"])
        minutes = float(m.payload["minutes"])
        label = m.payload.get("note") or "手动补录"
        out.append(Session(start, start + dt.timedelta(minutes=minutes), minutes, {label: minutes}, manual=True))
    return sorted(out, key=lambda s: s.start)


def section_minutes(ts: list[Tick], unit: str, plan: str | None, gap: int = GAP) -> dict[int, float]:
    """某个单元（某个课程版本）每一节实际花了多少分钟：按会话里的间隔归属算。"""
    spent: dict[int, float] = defaultdict(float)
    prev: Tick | None = None

    def mine(t: Tick | None) -> bool:
        return t is not None and t.unit == unit and t.section is not None and (plan is None or t.plan == plan)

    for t in ts:
        if mine(prev):
            d = (t.ts - prev.ts).total_seconds() / 60
            # 和 sessions() 一致：会话中间的间隔算给前一个时间点；会话结束时收尾算 TAIL 分钟。
            spent[prev.section] += d if d <= gap and not t.brk else TAIL
        prev = t
    if mine(prev):
        spent[prev.section] += TAIL
    return dict(spent)


def daily(sess: list[Session]) -> list[dict]:
    days: dict[str, list[Session]] = defaultdict(list)
    for s in sess:
        days[s.start.date().isoformat()].append(s)
    return [{"date": d, "minutes": round(sum(s.minutes for s in ss), 1), "sessions": [s.as_dict() for s in ss]}
            for d, ss in sorted(days.items())]


def journal(evidence: list[Evidence]) -> list[dict]:
    """学习记录页（D-027）：按天列出每次学习，以及这次学习里做成了什么（通过的小节、学会的新词、检查点、练习题）。"""
    sess = sessions(*ticks(evidence))
    for day in (days := daily(sess)):
        for d in day["sessions"]:
            start, end = parse_ts(d["start"]), parse_ts(d["end"])
            inside = [] if d["manual"] else [e for e in evidence if start <= parse_ts(e.ts) <= end]
            cps = [e for e in inside if e.verb == "answered" and e.object_type == "checkpoint"]
            passed = [e for e in inside if e.verb == "passed_section"]
            d.update(passed=list(dict.fromkeys(section_label(e.unit, e.section) for e in passed)),
                     terms=list(dict.fromkeys(n for e in passed for n in e.nodes)),
                     checkpoints={"tried": len({e.object_id for e in cps}), "ok": len({e.object_id for e in cps if e.ok}),
                                  "attempts": len(cps)},
                     questions=[{"id": e.object_id, "score": e.score} for e in inside
                                if e.verb == "answered" and e.object_type == "question"])
    return days


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    return f"{m // 60} 小时 {m % 60} 分钟" if m >= 60 else f"{m} 分钟"


def report(sess: list[Session], since: str | None = None) -> str:
    rows = [r for r in daily(sess) if since is None or r["date"] >= since]
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
