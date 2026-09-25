"""课程页（D-010）：读取已发布的课程计划，记录学习进度。

    preps/<unit>.json            助教 agent 产出、导师审阅后发布的课程计划
    progress/study_log.jsonl     学习记录：打开了哪一节、学完了哪一节、实际用了多久（只追加）

INVARIANT: 课程计划只读；进度只追加，由事件算出当前状态。和答题记录（attempts.jsonl）是同一个模式。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from studykit import store
from studykit.agent_env import tools

PREPS = store.ROOT / "preps"
# WHY: 单元 id 来自网页请求，只允许小写字母、数字和连字符，防止用 ../ 读到别的文件。
_UNIT = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class CourseError(ValueError):
    pass


def _plan_path(unit: str) -> Path:
    if not _UNIT.match(unit or ""):
        raise CourseError(f"单元 id 格式不对：{unit!r}")
    path = PREPS / f"{unit}.json"
    if not path.exists():
        raise CourseError(f"单元 {unit} 还没有课程页。对导师说「准备学 {unit}」生成一份。")
    return path


def load_plan(unit: str) -> dict:
    return json.loads(_plan_path(unit).read_text(encoding="utf-8"))


def settings() -> dict:
    s = store.load_yaml(store.PROGRESS_DIR / "settings.yaml")
    return {"unit_budget_minutes": int(s.get("unit_budget_minutes") or 180),
            "session_minutes": int(s.get("session_minutes") or 45)}


def record(unit: str, section: int, event: str, minutes: float | None = None) -> dict:
    plan = load_plan(unit)
    n = len(tools.sections_of(plan))
    if not isinstance(section, int) or not 0 <= section < n:
        raise CourseError(f"没有第 {section} 节")
    if event not in ("open", "done", "undone"):
        raise CourseError(f"不认识的事件：{event}")
    fields = {"unit": unit, "section": section, "event": event}
    if minutes is not None:
        fields["minutes"] = round(max(0.0, min(float(minutes), 600.0)), 1)
    return store.record_study(**fields)


def progress(unit: str) -> dict:
    """由事件算出当前进度：每节学完没有、实际用了多少分钟、上次学到哪一节。"""
    done: dict[int, bool] = {}
    spent: dict[int, float] = {}
    last = None
    for e in store.load_study_log(unit):
        s = e["section"]
        if e["event"] == "done":
            done[s] = True
            spent[s] = spent.get(s, 0) + (e.get("minutes") or 0)
        elif e["event"] == "undone":
            done[s] = False
        last = s
    return {"done": sorted(s for s, v in done.items() if v),
            "spent_minutes": {str(k): round(v, 1) for k, v in spent.items()},
            "total_spent": round(sum(spent.values()), 1), "last_section": last}


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
    return {
        "unit": unit, "plan": plan, "settings": cfg,
        "planned_minutes": tools.plan_minutes(plan),
        "sessions": tools.plan_sessions(plan, cfg["session_minutes"]),
        "section_count": len(sections),
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
                    "sections": len(tools.sections_of(plan)), "done": len(progress(unit)["done"])})
    return out
