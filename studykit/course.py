"""课程页（D-010，D-013 ~ D-017）：读取已发布的课程计划，记录学习事件，算出进度。

    preps/<unit>.json                    当前发布的课程计划
    preps/history/<unit>/<run>.json      发布过的每一版（对比两版、给旧事件找到它属于哪一版）
    progress/study_log.jsonl             学习事件（只追加）：打开小节、心跳、检查点、提示、跳过、终端命令、新词反馈……

INVARIANT: 课程计划只读；进度只由事件算出。每个事件记着它属于哪一版课程（plan），
重新生成课程后，旧版的进度不会串到新版的小节上。
INVARIANT: 检查点的答案、提示、参考做法只在服务器上；页面拿到的计划里没有这些（和练习题的 key.yaml 同一个原则）。
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from studykit import knowledge, lab, store, timeline
from studykit.agent_env import tools

PREPS = store.ROOT / "preps"
# WHY: 单元 id 来自网页请求，只允许小写字母、数字和连字符，防止用 ../ 读到别的文件。
_UNIT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
CLIENT_EVENTS = {"open", "done", "undone", "skip", "activity", "term", "term_miss", "load_rating"}
LOAD_LEVELS = [(2, "低"), (4, "中"), (10 ** 6, "高")]     # 新词数 ≤2 低，≤4 中，其余高（D-017）


class CourseError(ValueError):
    pass


# ---------- 课程计划和版本 ----------

def _plan_path(unit: str) -> Path:
    if not _UNIT.match(unit or ""):
        raise CourseError(f"单元 id 格式不对：{unit!r}")
    path = PREPS / f"{unit}.json"
    if not path.exists():
        raise CourseError(f"单元 {unit} 还没有课程页。对导师说「准备学 {unit}」生成一份。")
    return path


def load_plan(unit: str) -> dict:
    return json.loads(_plan_path(unit).read_text(encoding="utf-8"))


def plan_id(plan: dict) -> str:
    return (plan.get("provenance") or {}).get("run") or "draft"


def history(unit: str) -> list[dict]:
    """发布过的各版课程，按发布时间排序。"""
    d = PREPS / "history" / unit
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        plan = json.loads(p.read_text(encoding="utf-8"))
        out.append({"id": plan_id(plan), "published": (plan.get("provenance") or {}).get("published", ""), "plan": plan})
    return sorted(out, key=lambda h: h["published"])


_VERSIONS: dict[str, tuple[float, list]] = {}


def _versions(unit: str) -> list[tuple[str, str]]:
    # WHY: 每个事件都要查一次，按历史目录的修改时间缓存；发布新版后目录变了，缓存自动失效。
    d = PREPS / "history" / unit
    mtime = d.stat().st_mtime if d.exists() else 0.0
    hit = _VERSIONS.get(unit)
    if not hit or hit[0] != mtime:
        _VERSIONS[unit] = (mtime, [(h["published"], h["id"]) for h in history(unit)])
    return _VERSIONS[unit][1]


def plan_of_event(unit: str, e: dict) -> str | None:
    """事件属于哪一版课程。新事件自己带着 plan；旧事件按时间找当时发布的那一版。"""
    if e.get("plan"):
        return e["plan"]
    versions = _versions(unit)
    before = [pid for pub, pid in versions if pub and pub <= e["ts"]]
    if before:
        return before[-1]
    try:
        return plan_id(load_plan(unit))
    except CourseError:
        return None


def load_version(unit: str, pid: str) -> dict:
    for h in history(unit):
        if h["id"] == pid:
            return h["plan"]
    plan = load_plan(unit)
    if plan_id(plan) == pid:
        return plan
    raise CourseError(f"找不到 {unit} 的版本 {pid}")


def settings() -> dict:
    s = store.load_yaml(store.PROGRESS_DIR / "settings.yaml")
    return {"unit_budget_minutes": int(s.get("unit_budget_minutes") or 180),
            "session_minutes": int(s.get("session_minutes") or 45),
            "max_new_terms": int(s.get("max_new_terms") or 5)}


def _section(plan: dict, section) -> dict:
    sections = tools.sections_of(plan)
    if not isinstance(section, int) or isinstance(section, bool) or not 0 <= section < len(sections):
        raise CourseError(f"没有第 {section} 节")
    return sections[section]


def _item(plan: dict, section: int, idx) -> dict:
    items = _section(plan, section).get("checkpoint") or []
    if not isinstance(idx, int) or not 0 <= idx < len(items):
        raise CourseError(f"第 {section + 1} 节没有第 {idx} 道检查点题")
    return items[idx]


# ---------- 事件 ----------

def _log(unit: str, plan: dict, **fields) -> dict:
    return store.record_study(unit=unit, plan=plan_id(plan), **fields)


def record(unit: str, section, event: str, minutes: float | None = None, **extra) -> dict:
    """页面发来的事件。检查点、提示、终端命令由服务器自己记，不走这里。"""
    plan = load_plan(unit)
    if event not in CLIENT_EVENTS:
        raise CourseError(f"不认识的事件：{event}")
    fields: dict = {"event": event}
    if section is not None or event not in ("activity",):
        _section(plan, section)
        fields["section"] = section
    if minutes is not None and event == "done":
        fields["minutes"] = round(max(0.0, min(float(minutes), 600.0)), 1)
    if event == "activity":
        kind = extra.get("kind") or "page"
        if kind not in ("page", "video"):
            raise CourseError("activity 的 kind 只能是 page 或 video")
        fields["kind"] = kind
    elif event == "term":
        node, action = extra.get("node"), extra.get("action")
        if action not in ("open", "known", "unknown"):
            raise CourseError("term 的 action 只能是 open / known / unknown")
        if node not in {t.get("id") for t in _section(plan, section).get("terms") or []}:
            raise CourseError(f"这一节没有新词 {node}")
        fields.update(node=node, action=action)
    elif event == "term_miss":
        text = str(extra.get("text") or "").strip()
        if not 0 < len(text) <= 80:
            raise CourseError("选中的文字要在 1-80 字之间")
        fields["text"] = text
    elif event == "load_rating":
        rating = extra.get("rating")
        if rating not in (1, 2, 3, 4, 5):
            raise CourseError("费劲程度是 1-5")
        fields["rating"] = rating
    rec = _log(unit, plan, **fields)
    if event == "open" and plan.get("lab"):
        lab.ensure(unit, plan["lab"].get("files") or [])
        lab.snapshot_once(unit, plan_id(plan), section)
    return rec


def events(unit: str, pid: str | None = None) -> list[dict]:
    return [e for e in store.load_study_log(unit) if pid is None or plan_of_event(unit, e) == pid]


# ---------- 检查点（D-013） ----------

def _hints_used(evs: list[dict], section: int, idx: int) -> int:
    return sum(1 for e in evs if e.get("event") == "hint" and e.get("section") == section and e.get("idx") == idx)


def check(unit: str, section: int, idx: int, response) -> dict:
    from studykit import checkers
    plan = load_plan(unit)
    item = _item(plan, section, idx)
    kind = item.get("type")
    trap, checks_view = None, None
    if kind == "choice":
        v = checkers.get("choice").check({"multi": bool(item.get("multi"))}, {"answer": item["answer"]}, None, response)
        score, feedback = v.score, []            # 不回显正确答案：做错了还要能再试
        picked = {str(x).upper() for x in (response if isinstance(response, list) else [response])}
        trap = next((t for t in item.get("traps") or [] if str(t.get("when", "")).upper() in picked), None)
    elif kind == "fill":
        v = checkers.get("fill").check({"prompt": item["prompt"]}, {"blanks": item["accept"], "regex": item.get("regex")},
                                       None, response)
        score = v.score
        feedback = [f.split("你填的是")[0].strip() for f in v.feedback]
        answers = [str(a) for a in (response if isinstance(response, list) else [response])]
        trap = next((t for t in item.get("traps") or []
                     if any(re.search(str(t.get("when", "")), a, re.IGNORECASE) for a in answers)), None)
    elif kind == "lab":
        results = lab.check(unit, item.get("checks") or [])
        score = sum(ok for ok, _, _ in results) / max(1, len(results))
        checks_view = [{"ok": ok, "desc": desc} for ok, desc, _ in results]
        feedback = [f"{'✓' if ok else '✗'} {desc}" for ok, desc, _ in results]
        trap = next((c.get("trap") for ok, _, c in results if not ok and c.get("trap")), None)
    else:
        raise CourseError(f"不认识的检查点题型：{kind}")
    ok = score >= 0.999
    before = progress(unit)
    was_passed = section in before["passed"]
    attempts = before["checkpoints"].get(str(section), {}).get(str(idx), {}).get("attempts", 0) + 1
    items = _section(plan, section).get("checkpoint") or []
    others_ok = all(before["checkpoints"].get(str(section), {}).get(str(j), {}).get("ok") for j in range(len(items)) if j != idx)
    section_passed = ok and others_ok
    nodes = [n for n in [item.get("concept")] if n]
    if section_passed and not was_passed:
        # 整节做对了：这一节的新词也算有了"会用"的证据（D-017 的已知词表由此更新）。
        nodes += [t["id"] for t in _section(plan, section).get("terms") or [] if t.get("id")]
    _log(unit, plan, event="checkpoint", section=section, idx=idx, ok=ok, score=round(score, 3),
         response=_clip(response), attempt=attempts, nodes=nodes)
    out = {"ok": ok, "score": score, "feedback": feedback, "attempt": attempts,
           "trap": trap and {k: trap.get(k) for k in ("symptom", "cause", "fix")},
           "section_passed": section_passed or was_passed, "checks": checks_view}
    if ok:
        out["explain"] = item.get("explain", "")
        if kind == "lab":
            out["solution"] = item.get("solution") or []
    out["progress"] = progress(unit)
    return out


def _clip(response):
    s = json.dumps(response, ensure_ascii=False)
    return response if len(s) <= 2000 else s[:2000]


def hint(unit: str, section: int, idx: int) -> dict:
    """三级提示：方向 → 关键概念 → 接近答案。每要一次记一条事件（D-019 用它算检查点难度）。"""
    plan = load_plan(unit)
    item = _item(plan, section, idx)
    hints = item.get("hints") or []
    if not hints:
        raise CourseError("这道题没有提示")
    used = _hints_used(events(unit, plan_id(plan)), section, idx)
    level = min(used + 1, len(hints))
    if used < len(hints):
        _log(unit, plan, event="hint", section=section, idx=idx, level=level)
    return {"level": level, "total": len(hints), "hints": hints[:level]}


# ---------- 练习场（D-014） ----------

def lab_action(unit: str, op: str, section: int | None = None, cmd: str = "") -> dict:
    plan = load_plan(unit)
    spec = plan.get("lab")
    if not spec:
        raise CourseError("这个单元没有练习场")
    files = spec.get("files") or []
    pid = plan_id(plan)
    if op == "open":
        lab.ensure(unit, files)
        if isinstance(section, int):
            _section(plan, section)
            lab.snapshot_once(unit, pid, section)
        bash = _bash_or_error()
        sh = lab.SHELLS.get(unit)
        if not bash.startswith("error"):
            sh.start()
        return {"lab": str(lab.lab_dir(unit)), "lab_posix": sh.lab_posix(), "cwd": sh.cwd if sh.proc else sh.lab_posix(),
                "bash": bash}
    if op == "run":
        cmd = str(cmd or "")
        if not cmd.strip():
            return {"output": "", "rc": 0}
        if len(cmd) > 4000:
            raise CourseError("命令太长")
        lab.ensure(unit, files)
        res = lab.SHELLS.get(unit).run(cmd)
        _log(unit, plan, event="lab", op="run", section=section, cmd=cmd, rc=res["rc"], outside=res["outside"])
        return res
    if op == "restore":
        _section(plan, section)
        lab.restore(unit, pid, section)
        _log(unit, plan, event="lab", op="restore", section=section)
        return {"output": f"练习场已还原到第 {section + 1} 节开始时的样子。", "cwd": lab.SHELLS.get(unit).lab_posix()}
    if op == "reset":
        lab.reset(unit, files)
        _log(unit, plan, event="lab", op="reset", section=section)
        if isinstance(section, int):
            lab.snapshot_once(unit, pid, section)
        return {"output": "练习场已重置为单元开始时的样子（所有改动都清掉了）。", "cwd": lab.SHELLS.get(unit).lab_posix()}
    if op == "fill":
        # 跳过一节时，用参考做法把练习场补到"这一节做完"的样子，下一节才能接着做。
        _section(plan, section)
        outs = []
        for item in _section(plan, section).get("checkpoint") or []:
            if item.get("type") == "lab":
                for c in item.get("solution") or []:
                    rc, out = lab.run_once(unit, c)
                    outs.append(f"$ {c}" + (f"\n{out.rstrip()}" if out.strip() else ""))
        _log(unit, plan, event="lab", op="fill", section=section)
        return {"output": "\n".join(outs) or "这一节没有要补的练习场操作。", "cwd": lab.SHELLS.get(unit).lab_posix()}
    raise CourseError(f"不认识的练习场操作：{op}")


def _bash_or_error() -> str:
    try:
        return lab.find_bash()
    except lab.LabError as e:
        return f"error: {e}"


# ---------- 进度（由事件算出） ----------

def progress(unit: str) -> dict:
    plan = load_plan(unit)
    pid = plan_id(plan)
    sections = tools.sections_of(plan)
    evs = events(unit, pid)
    legacy_done: dict[int, bool] = {}
    skipped, ratings, cps, terms = set(), {}, {}, {}
    last = None
    for e in evs:
        s, kind = e.get("section"), e.get("event")
        if kind == "done":
            legacy_done[s] = True
        elif kind == "undone":
            legacy_done[s] = False
        elif kind == "skip":
            skipped.add(s)
        elif kind == "load_rating":
            ratings[str(s)] = e["rating"]
        elif kind == "checkpoint":
            c = cps.setdefault(str(s), {}).setdefault(str(e["idx"]), {"ok": False, "attempts": 0, "first_try": None, "hints": 0})
            c["attempts"] += 1
            c["ok"] = c["ok"] or bool(e.get("ok"))
            if c["first_try"] is None:
                c["first_try"] = bool(e.get("ok"))
        elif kind == "hint":
            c = cps.setdefault(str(s), {}).setdefault(str(e["idx"]), {"ok": False, "attempts": 0, "first_try": None, "hints": 0})
            c["hints"] = max(c["hints"], e.get("level", 0))
        elif kind == "term" and e.get("action") in ("known", "unknown"):
            terms[e["node"]] = e["action"]
        if kind in ("open", "done", "checkpoint") and isinstance(s, int):
            last = s
    passed = []
    for i, sec in enumerate(sections):
        items = sec.get("checkpoint") or []
        if items:
            if all(cps.get(str(i), {}).get(str(j), {}).get("ok") for j in range(len(items))):
                passed.append(i)
        elif legacy_done.get(i):
            passed.append(i)
    evs_all, _ = timeline.collect(plan_of_event)
    spent = timeline.section_minutes(evs_all, unit, pid)
    return {"plan": pid, "passed": passed, "done": passed,
            "skipped": sorted(s for s in skipped if s not in passed),
            "checkpoints": cps, "ratings": ratings, "terms": terms,
            "spent_minutes": {str(k): round(v, 1) for k, v in sorted(spent.items())},
            "total_spent": round(sum(spent.values()), 1), "last_section": last}


def predicted_load(plan: dict, known: set[str]) -> list[dict]:
    """每节的预测负荷（D-017）：这一节的新词里，学习者还没掌握的有几个。随着已知词表变大，同一节的负荷会下降。"""
    out = []
    for s in tools.sections_of(plan):
        new = [t for t in s.get("terms") or [] if t.get("id") not in known and t.get("term", "").lower() not in known]
        n = len(new)
        out.append({"new_terms": n, "per_10min": round(10 * n / max(1, int(s.get("minutes") or 1)), 1),
                    "level": next(name for limit, name in LOAD_LEVELS if n <= limit)})
    return out


def public_plan(plan: dict) -> dict:
    """给页面的课程计划：去掉检查点的答案、提示、参考做法、陷阱，以及练习场文件内容。"""
    p = copy.deepcopy(plan)
    for s in tools.sections_of(p):
        for item in s.get("checkpoint") or []:
            for k in ("answer", "accept", "regex", "checks", "solution", "hints", "traps", "explain"):
                item.pop(k, None)
    if p.get("lab"):
        p["lab"] = {"story": p["lab"].get("story", ""), "files": len(p["lab"].get("files") or [])}
    return p


def unit_sources(plan: dict) -> list[dict]:
    """出处放在单元一级（D-015）。旧版计划的出处在每一节里，合并去重。"""
    seen, out = set(), []
    for x in (plan.get("sources") or []) + [x for s in tools.sections_of(plan) for x in s.get("sources") or []]:
        if x.get("url") and x["url"] not in seen:
            seen.add(x["url"])
            out.append({"title": x.get("title", x["url"]), "url": x["url"]})
    return out


def quiz_for(unit: str) -> str | None:
    """这个单元的练习题：quiz.yaml 里写了 `unit: <单元 id>` 的那套题。"""
    for quiz in sorted(store.LESSONS.glob("*/*/quiz.yaml")):
        if (store.load_yaml(quiz).get("unit") or "") == unit:
            return f"{quiz.parent.parent.name}/{quiz.parent.name}"
    return None


def payload(unit: str) -> dict:
    plan = load_plan(unit)
    cfg = settings()
    sections = tools.sections_of(plan)
    topic = unit.split("-")[0]
    return {
        "unit": unit, "plan": public_plan(plan), "settings": cfg,
        "planned_minutes": tools.plan_minutes(plan),
        "sessions": tools.plan_sessions(plan, cfg["session_minutes"]),
        "section_count": len(sections),
        "load": predicted_load(plan, knowledge.known_titles(topic)),
        "sources": unit_sources(plan),
        "progress": progress(unit),
        "quiz": quiz_for(unit),
    }


def list_units() -> list[dict]:
    out = []
    for path in sorted(PREPS.glob("*.json")) if PREPS.exists() else []:
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        unit = path.stem
        if not _UNIT.match(unit):
            continue
        out.append({"unit": unit, "title": plan.get("title", unit), "minutes": tools.plan_minutes(plan),
                    "sections": len(tools.sections_of(plan)), "done": len(progress(unit)["passed"])})
    return out
