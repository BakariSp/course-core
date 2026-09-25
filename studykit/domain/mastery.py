"""掌握度规则（D-003）：每个概念的掌握等级 = 最近几次平均分达标的最高一级（0-4）。

算分的证据：answered（练习题）、graded（批改，替代它指向的待批改作答）、logged_practice（课外练习）。
"今天"由调用方给出。
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from studykit.domain.evidence import Evidence, split_question

LEVELS = {1: "记忆", 2: "理解", 3: "应用", 4: "分析"}
PASS_SCORE = 0.7    # 某一级最近几次的平均分达到这个值，算这一级通过
WINDOW = 3          # 只看每一级最近 3 次
STALE_DAYS = 14     # 通过后超过这么多天没再练，标记为需复习


def result_of(score: float | None) -> str:
    if score is None:
        return "pending"
    if score >= PASS_SCORE:
        return "pass"
    return "partial" if score > 0 else "fail"


@dataclass(frozen=True)
class Scored:
    """一次有分数的练习：掌握度只看这些。"""
    concept: str
    level: int
    score: float
    ts: str
    ref: str                   # 题目（<课时>#<题号>）或 external
    note: str = ""
    feedback: tuple[str, ...] = ()


def scored(evidence: list[Evidence]) -> tuple[list[Scored], list[Evidence]]:
    """返回（有分数的练习, 还在等批改的作答）。"""
    answers = {e.id: e for e in evidence if e.verb == "answered" and e.object_type == "question"}
    graded_ids = {e.caused_by for e in evidence if e.verb == "graded"}
    out, pending = [], []
    for e in evidence:
        if e.verb == "answered" and e.object_type == "question":
            if e.pending:
                if e.id not in graded_ids:
                    pending.append(e)
                continue
            out.append(Scored(e.nodes[0], int(e.payload.get("level", 1)), e.score, e.ts, e.object_id,
                              "", tuple(e.payload.get("feedback") or ())))
        elif e.verb == "graded" and e.caused_by in answers:
            a = answers[e.caused_by]
            out.append(Scored(a.nodes[0], int(a.payload.get("level", 1)), e.score, e.ts, a.object_id,
                              e.payload.get("note", "")))
        elif e.verb == "logged_practice":
            out.append(Scored(e.nodes[0], int(e.payload["level"]), e.score, e.ts, "external", e.payload.get("note", "")))
    return out, pending


def concept_stats(evidence: list[Evidence], today: dt.date) -> dict[str, dict]:
    """每个概念的掌握度 = 最近几次平均分达标的最高一级（0-4）。有一级没达标或太久没练，就算薄弱。"""
    items, _ = scored(evidence)
    by: dict[str, dict[int, list[Scored]]] = defaultdict(lambda: defaultdict(list))
    for s in items:
        by[s.concept][s.level].append(s)

    stats = {}
    for concept, levels in by.items():
        passed, failing, last_pass = [], [], None
        for level, recs in levels.items():
            recs.sort(key=lambda r: r.ts)
            recent = recs[-WINDOW:]
            if sum(r.score for r in recent) / len(recent) >= PASS_SCORE:
                passed.append(level)
                ts = max(r.ts for r in recs if r.score >= PASS_SCORE)
                last_pass = max(last_pass or ts, ts)
            else:
                failing.append(level)
        all_recs = sorted((r for recs in levels.values() for r in recs), key=lambda r: r.ts)
        mastery = max(passed, default=0)
        stale = bool(last_pass) and (today - dt.date.fromisoformat(last_pass[:10])).days > STALE_DAYS
        stats[concept] = {
            "concept": concept,
            "topic": concept.split(".")[0],
            "mastery": mastery,
            "target_level": min(4, min(failing) if failing else mastery + 1),
            "failing_levels": sorted(failing),
            "attempts": len(all_recs),
            "last": all_recs[-1].ts[:10],
            "stale": stale,
            "weak": bool(failing) or stale,
            "recent_mistakes": [{"ref": r.ref, "level": r.level, "score": r.score, "note": r.note,
                                 "feedback": list(r.feedback)} for r in all_recs if r.score < PASS_SCORE][-3:],
        }
    return stats


def practice_summary(evidence: list[Evidence]) -> list[dict]:
    """每道代码题的练习过程：跑了几次、第几次第一次全部通过（跑了很多次才过的，说明还不熟）。"""
    by: dict[str, list[Evidence]] = defaultdict(list)
    for e in evidence:
        if e.verb == "ran_tests":
            by[e.object_id].append(e)
    out = []
    for oid, rs in by.items():
        rs.sort(key=lambda r: r.ts)
        first = next((i + 1 for i, r in enumerate(rs) if r.payload.get("total") and r.payload["passed"] == r.payload["total"]), None)
        lesson, qid = split_question(oid)
        last = rs[-1].payload
        out.append({"lesson": lesson, "qid": qid, "concept": rs[-1].nodes[0] if rs[-1].nodes else None,
                    "runs": len(rs), "first_full_pass_at_run": first, "last": rs[-1].ts[:16],
                    "last_result": f"{last.get('passed', 0)}/{last.get('total', 0)}"})
    return sorted(out, key=lambda s: s["last"], reverse=True)
