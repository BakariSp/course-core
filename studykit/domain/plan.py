"""课程计划（D-010）：单元 → 部分 → 小节。结构、时长、答案剃除、课程页事件的校验。

INVARIANT: 课程计划发布后只读；改 = 发布新版本（provenance.run 是版本 id）。每条证据记着它属于哪一版。
INVARIANT: 检查点的答案、提示、参考做法只在服务器上；页面拿到的是 public_view()。
INVARIANT: 页面只能发 CLIENT_EVENTS 里的事件；检查点、提示、整节通过由服务器自己记，学习者不能伪造。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from studykit.domain.errors import CourseError
from studykit.domain.evidence import section_id

PLAN_SCHEMA_VERSION = 2
SECRET_CHECKPOINT_KEYS = ("answer", "accept", "regex", "checks", "solution", "hints", "traps", "explain")


def sections_of(plan: dict) -> list[dict]:
    return [s for p in plan.get("parts") or [] for s in p.get("sections") or []]


def plan_minutes(plan: dict) -> int:
    return sum(int(s.get("minutes") or 0) for s in sections_of(plan))


def plan_sessions(plan: dict, session_minutes: int) -> list[list[int]]:
    """按顺序把小节装进一次次学习（每次不超过 session_minutes），返回每次包含的小节序号。"""
    sessions, cur, used = [], [], 0
    for i, s in enumerate(sections_of(plan)):
        m = int(s.get("minutes") or 0)
        if cur and used + m > session_minutes:
            sessions.append(cur)
            cur, used = [], 0
        cur.append(i)
        used += m
    if cur:
        sessions.append(cur)
    return sessions


def plan_id(plan: dict) -> str:
    return (plan.get("provenance") or {}).get("run") or "draft"


def section(plan: dict, index) -> dict:
    sections = sections_of(plan)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(sections):
        raise CourseError(f"没有第 {index} 节")
    return sections[index]


def checkpoint_item(plan: dict, index: int, idx) -> dict:
    items = section(plan, index).get("checkpoint") or []
    if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(items):
        raise CourseError(f"第 {index + 1} 节没有第 {idx} 道检查点题")
    return items[idx]


def section_terms(plan: dict, index: int) -> list[str]:
    return [t["id"] for t in section(plan, index).get("terms") or [] if t.get("id")]


def public_view(plan: dict) -> dict:
    """给页面的课程计划：去掉检查点的答案、提示、参考做法、陷阱，以及练习场文件内容。"""
    p = copy.deepcopy(plan)
    for s in sections_of(p):
        for item in s.get("checkpoint") or []:
            for k in SECRET_CHECKPOINT_KEYS:
                item.pop(k, None)
    if p.get("lab"):
        p["lab"] = {"story": p["lab"].get("story", ""), "files": len(p["lab"].get("files") or [])}
    return p


# ---------- 课程页发来的事件 → 证据 ----------

CLIENT_EVENTS = {"open", "done", "undone", "skip", "activity", "term", "term_miss", "load_rating"}


@dataclass
class ClientEvent:
    """校验过的页面事件，服务层据此写一条证据。"""
    verb: str
    object_type: str
    object_id: str
    section: int | None
    nodes: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict)


def client_event(plan: dict, unit: str, index, event: str, minutes: float | None = None, **extra) -> ClientEvent:
    if event not in CLIENT_EVENTS:
        raise CourseError(f"不认识的事件：{event}")
    if event == "activity" and index is None:
        return ClientEvent("pinged", "unit", unit, None, payload={"kind": _activity_kind(extra)})
    section(plan, index)
    sid = section_id(unit, index)
    if event == "open":
        return ClientEvent("opened", "section", sid, index)
    if event == "done":
        payload = {} if minutes is None else {"minutes": round(max(0.0, min(float(minutes), 600.0)), 1)}
        return ClientEvent("completed", "section", sid, index, payload=payload)
    if event == "undone":
        return ClientEvent("uncompleted", "section", sid, index)
    if event == "skip":
        return ClientEvent("skipped", "section", sid, index)
    if event == "activity":
        return ClientEvent("pinged", "section", sid, index, payload={"kind": _activity_kind(extra)})
    if event == "term":
        node, action = extra.get("node"), extra.get("action")
        if action not in ("open", "known", "unknown"):
            raise CourseError("term 的 action 只能是 open / known / unknown")
        if node not in section_terms(plan, index):
            raise CourseError(f"这一节没有新词 {node}")
        if action == "open":
            return ClientEvent("viewed", "node", node, index, (node,))
        return ClientEvent("voted_term", "node", node, index, (node,), {"vote": action})
    if event == "term_miss":
        text = str(extra.get("text") or "").strip()
        if not 0 < len(text) <= 80:
            raise CourseError("选中的文字要在 1-80 字之间")
        return ClientEvent("flagged_unexplained", "section", sid, index, payload={"text": text})
    rating = extra.get("rating")                  # load_rating
    if rating not in (1, 2, 3, 4, 5) or isinstance(rating, bool):
        raise CourseError("费劲程度是 1-5")
    return ClientEvent("rated_load", "section", sid, index, payload={"rating": rating})


def _activity_kind(extra: dict) -> str:
    kind = extra.get("kind") or "page"
    if kind not in ("page", "video"):
        raise CourseError("activity 的 kind 只能是 page 或 video")
    return kind
