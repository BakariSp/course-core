"""单元进度（D-013）、检查点结果、预测负荷（D-017）：都是证据的投影，不存。

INVARIANT: 进度只由这一版课程的证据算出，重新发布课程后旧版的进度不会串到新版的小节上。
"""
from __future__ import annotations

from studykit.domain.evidence import Evidence
from studykit.domain.plan import section_terms, sections_of

LOAD_LEVELS = [(2, "低"), (4, "中"), (10 ** 6, "高")]     # 新词数 ≤2 低，≤4 中，其余高（D-017）


def _cp(cps: dict, s, idx) -> dict:
    return cps.setdefault(str(s), {}).setdefault(str(idx), {"ok": False, "attempts": 0, "first_try": None, "hints": 0})


def project(plan: dict, pid: str, evidence: list[Evidence], spent: dict[int, float]) -> dict:
    """evidence = 这一版课程的证据（按时间顺序）；spent = 每节实际分钟数（timeline.section_minutes）。"""
    sections = sections_of(plan)
    done: dict[int, bool] = {}
    skipped, ratings, cps, terms = set(), {}, {}, {}
    last, last_at = None, None
    for e in evidence:
        s, v = e.section, e.verb
        if v == "completed":
            done[s] = True
        elif v == "uncompleted":
            done[s] = False
        elif v == "skipped":
            skipped.add(s)
        elif v == "rated_load":
            ratings[str(s)] = e.payload["rating"]
        elif v == "answered" and e.object_type == "checkpoint":
            c = _cp(cps, s, e.payload["idx"])
            c["attempts"] += 1
            c["ok"] = c["ok"] or bool(e.ok)
            if c["first_try"] is None:
                c["first_try"] = bool(e.ok)
            if "response" in e.payload:        # 最近一次作答，页面用它把答案填回去
                c["response"] = e.payload["response"]
        elif v == "requested_hint":
            c = _cp(cps, s, e.payload["idx"])
            c["hints"] = max(c["hints"], e.payload.get("level", 0))
        elif v == "voted_term":
            terms[e.object_id] = e.payload["vote"]
        if v in ("opened", "completed", "answered", "skipped") and isinstance(s, int):
            last, last_at = s, max(last_at or "", e.ts)
    passed = []
    for i, sec in enumerate(sections):
        items = sec.get("checkpoint") or []
        if items:
            if all(cps.get(str(i), {}).get(str(j), {}).get("ok") for j in range(len(items))):
                passed.append(i)
        elif done.get(i):                       # 没有检查点的小节：自己点「学完了」
            passed.append(i)
    # 走过 = 通过、自己点了学完、或跳过（D-064）：各节都走过就算学完这个单元；没通过的检查点照旧是证据，进复习
    walked = sorted(set(passed) | {i for i in skipped | {k for k, d in done.items() if d} if 0 <= i < len(sections)})
    return {"plan": pid, "passed": passed, "done": passed, "walked": walked,
            "skipped": sorted(s for s in skipped if s not in passed),
            "checkpoints": cps, "ratings": ratings, "terms": terms,
            "spent_minutes": {str(k): round(v, 1) for k, v in sorted(spent.items())},
            "total_spent": round(sum(spent.values()), 1), "last_section": last, "last_at": last_at}


def hints_used(evidence: list[Evidence], section: int, idx: int) -> int:
    return sum(1 for e in evidence if e.verb == "requested_hint" and e.section == section and e.payload.get("idx") == idx)


def last_hint(evidence: list[Evidence], section: int, idx: int) -> str | None:
    """这道检查点最近一次要的提示：接下来的作答 caused_by 它（D-021，G2）。"""
    ids = [e.id for e in evidence if e.verb == "requested_hint" and e.section == section and e.payload.get("idx") == idx]
    return ids[-1] if ids else None


def checkpoint_outcome(plan: dict, before: dict, section: int, idx: int, ok: bool) -> dict:
    """一次检查点作答之后：第几次作答、这一节是否（第一次）整节通过、通过时哪些新词算"会用了"。

    before = 作答前的 project() 结果。整节第一次通过时，这一节的新词也有了"会用"的证据（D-017）。
    """
    was_passed = section in before["passed"]
    done = before["checkpoints"].get(str(section), {})
    attempt = done.get(str(idx), {}).get("attempts", 0) + 1
    items = sections_of(plan)[section].get("checkpoint") or []
    section_passed = ok and all(done.get(str(j), {}).get("ok") for j in range(len(items)) if j != idx)
    first_pass = section_passed and not was_passed
    return {"attempt": attempt, "section_passed": section_passed or was_passed, "first_pass": first_pass,
            "terms": section_terms(plan, section) if first_pass else []}


def predicted_load(plan: dict, known: set[str]) -> list[dict]:
    """每节的预测负荷（D-017）：这一节的新词里，学习者还没掌握的有几个。已知词表变大，同一节的负荷会下降。"""
    out = []
    for s in sections_of(plan):
        new = [t for t in s.get("terms") or [] if t.get("id") not in known and t.get("term", "").lower() not in known]
        n = len(new)
        out.append({"new_terms": n, "per_10min": round(10 * n / max(1, int(s.get("minutes") or 1)), 1),
                    "level": next(name for limit, name in LOAD_LEVELS if n <= limit)})
    return out
