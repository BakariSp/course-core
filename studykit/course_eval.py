"""课程评测报告（D-019）：每个预测都对上一个实际值。

| 预测（agent 生成课程时）     | 实际（学习者用的时候）                  |
|---|---|
| 每节计划分钟数               | 会话时间（D-016）                       |
| 预测负荷（新词数，D-017）    | 学完后自评的费劲程度 1-5                 |
| 新词清单                     | 点开、👍 确实不懂、👎 早就知道、漏报     |
| 检查点难度                   | 第一次就做对的比例、尝试次数、用到第几级提示 |

INVARIANT: 只读事件日志和课程计划，不写任何东西（--write 时把报告存进 agent 的运行目录）。
"""
from __future__ import annotations

import json

from studykit import course, store, timeline
from studykit.agent_env import tools

FIRST_TRY_BAND = (0.6, 0.85)      # 第一次就做对的比例：低于它讲解没讲清，高于它题太浅


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return round(cov / (sx * sy), 2) if sx and sy else None


def _known_at_generation(pid: str) -> set[str] | None:
    """生成课程时的已知词表（记在 agent 运行目录的 context.json 里）。找不到就返回 None。"""
    ctx = store.ROOT / "runs" / "agents" / "tutor-prep" / pid / "context.json"
    if not ctx.exists():
        return None
    return {t.lower() for t in json.loads(ctx.read_text(encoding="utf-8")).get("known_terms") or []}


def evaluate(unit: str, pid: str | None = None) -> dict:
    plan = course.load_plan(unit) if pid is None else course.load_version(unit, pid)
    pid = course.plan_id(plan)
    sections = tools.sections_of(plan)
    evs = course.events(unit, pid)
    all_events, _ = timeline.collect(course.plan_of_event)
    spent = timeline.section_minutes(all_events, unit, pid)
    known = _known_at_generation(pid)
    load = course.predicted_load(plan, known if known is not None else set())
    rows = []
    for i, s in enumerate(sections):
        mine = [e for e in evs if e.get("section") == i]
        term_ids = [t.get("id") for t in s.get("terms") or []]
        votes: dict[str, str] = {}
        opened = set()
        for e in mine:
            if e.get("event") == "term":
                if e.get("action") == "open":
                    opened.add(e["node"])
                else:
                    votes[e["node"]] = e["action"]
        cps: dict[int, list[dict]] = {}
        for e in mine:
            if e.get("event") == "checkpoint":
                cps.setdefault(e["idx"], []).append(e)
        hints = {}
        for e in mine:
            if e.get("event") == "hint":
                hints[e["idx"]] = max(hints.get(e["idx"], 0), e.get("level", 0))
        rating = next((e["rating"] for e in reversed(mine) if e.get("event") == "load_rating"), None)
        rows.append({
            "section": i + 1, "title": s.get("title", ""),
            "planned": int(s.get("minutes") or 0), "actual": round(spent.get(i, 0.0), 1),
            "predicted_new_terms": load[i]["new_terms"], "predicted_load": load[i]["level"], "rating": rating,
            "terms": len(term_ids), "opened": len(opened & set(term_ids)),
            "unknown": sum(1 for v in votes.values() if v == "unknown"),
            "known": sum(1 for v in votes.values() if v == "known"),
            "misses": [e["text"] for e in mine if e.get("event") == "term_miss"],
            "checkpoints": len(s.get("checkpoint") or []),
            "first_try": [bool(v[0].get("ok")) for _, v in sorted(cps.items())],
            "attempts": [len(v) for _, v in sorted(cps.items())],
            "max_hint": max(hints.values(), default=0),
            "skipped": any(e.get("event") == "skip" for e in mine),
            "passed": i in course.progress(unit)["passed"] if pid == course.plan_id(course.load_plan(unit)) else None,
        })
    return {"unit": unit, "plan": pid, "known_at_generation": known is not None, "rows": rows,
            "summary": _summary(rows)}


def _summary(rows: list[dict]) -> dict:
    timed = [r for r in rows if r["actual"] > 0]
    planned = sum(r["planned"] for r in timed)
    actual = sum(r["actual"] for r in timed)
    unknown = sum(r["unknown"] for r in rows)
    known = sum(r["known"] for r in rows)
    firsts = [ok for r in rows for ok in r["first_try"]]
    rated = [(r["predicted_new_terms"], r["rating"]) for r in rows if r["rating"]]
    s = {
        "time_ratio": round(actual / planned, 2) if planned else None,
        "sections_timed": len(timed),
        "term_precision": round(unknown / (unknown + known), 2) if unknown + known else None,
        "term_votes": unknown + known,
        "term_misses": sum(len(r["misses"]) for r in rows),
        "first_try_rate": round(sum(firsts) / len(firsts), 2) if firsts else None,
        "load_rank_corr": _spearman([a for a, _ in rated], [b for _, b in rated]),
        "ratings": len(rated),
    }
    advice = []
    if s["time_ratio"] and s["time_ratio"] > 1.5:
        advice.append(f"实际用时是计划的 {s['time_ratio']} 倍：分钟数低估了，或者一节里内容太多。")
    if s["time_ratio"] and s["time_ratio"] < 0.6:
        advice.append(f"实际用时只有计划的 {s['time_ratio']} 倍：可能是跳着看，或者计时漏了（看视频时没点「我在看视频」）。")
    if s["term_precision"] is not None and s["term_precision"] < 0.5:
        advice.append("一半以上的新词学习者早就会：已知词表没传进去，或者 agent 列得太多。")
    if s["term_misses"] >= 3:
        misses = [m for r in rows for m in r["misses"]]
        advice.append(f"漏报了 {len(misses)} 个学习者不懂的词：{'、'.join(misses[:8])}。看看 prompt 里对'新词'的定义。")
    if s["first_try_rate"] is not None:
        lo, hi = FIRST_TRY_BAND
        if s["first_try_rate"] < lo:
            advice.append(f"检查点第一次就做对的只有 {s['first_try_rate']:.0%}：讲解和题目之间有断层。")
        elif s["first_try_rate"] > hi:
            advice.append(f"检查点第一次就做对的有 {s['first_try_rate']:.0%}：题太浅，检验不出懂没懂。")
    if s["load_rank_corr"] is not None and s["load_rank_corr"] < 0.3:
        advice.append(f"预测负荷和自评对不上（秩相关 {s['load_rank_corr']}）：新词数不是负荷的主要来源，要找别的因素。")
    s["advice"] = advice
    return s


def format_report(r: dict) -> str:
    s = r["summary"]
    lines = [f"# 课程评测：{r['unit']}（版本 {r['plan']}）", ""]
    if not r["known_at_generation"]:
        lines += ["> 找不到生成课程时的已知词表，预测负荷按\"什么都不认识\"算。", ""]
    lines += ["| 节 | 计划′ | 实际′ | 预测负荷 | 自评 | 新词 点开/👍/👎 | 漏报 | 检查点 首次对/尝试 | 最高提示 | 状态 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for x in r["rows"]:
        cp = " ".join(f"{'✓' if ok else '✗'}{n}" for ok, n in zip(x["first_try"], x["attempts"])) or ("—" if not x["checkpoints"] else "没做")
        state = "✓" if x["passed"] else ("跳过" if x["skipped"] else "")
        lines.append(f"| {x['section']}. {x['title'][:18]} | {x['planned']} | {x['actual'] or '—'} | "
                     f"{x['predicted_load']}（{x['predicted_new_terms']}） | {x['rating'] or '—'} | "
                     f"{x['opened']}/{x['unknown']}/{x['known']}（共 {x['terms']}） | {len(x['misses']) or ''} | {cp} | "
                     f"{x['max_hint'] or ''} | {state} |")

    def pct(v):
        return "—" if v is None else f"{v:.0%}"

    lines += ["", "## 汇总", "",
              f"- 用时：实际 / 计划 = {s['time_ratio'] or '—'}（有记录的 {s['sections_timed']} 节）",
              f"- 新词预测准确率（👍 ÷ 投票数）= {pct(s['term_precision'])}（{s['term_votes']} 票），漏报 {s['term_misses']} 个",
              f"- 检查点第一次就做对 = {pct(s['first_try_rate'])}（目标 {FIRST_TRY_BAND[0]:.0%}–{FIRST_TRY_BAND[1]:.0%}）",
              f"- 预测负荷和自评的秩相关 = {s['load_rank_corr'] if s['load_rank_corr'] is not None else '数据不够'}（{s['ratings']} 节有自评）"]
    lines += ["", "## 下一版 prompt 可以改哪里", ""] + ([f"- {a}" for a in s["advice"]] or ["- 暂时没有明显的问题（或者数据还不够）。"])
    return "\n".join(lines) + "\n"
