"""课程计划（D-010）：单元 → 部分 → 小节。结构、时长、答案剃除、课程页事件的校验。

INVARIANT: 课程计划发布后只读；改 = 发布新版本（provenance.run 是版本 id）。每条证据记着它属于哪一版。
INVARIANT: 检查点的答案、提示、参考做法只在服务器上；页面拿到的是 public_view()。
INVARIANT: 页面只能发 CLIENT_EVENTS 里的事件；检查点、提示、整节通过由服务器自己记，学习者不能伪造。
"""
from __future__ import annotations

import copy
import datetime as dt
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


# ---------- 大纲先行、分节生成（PRD_V2 阶段 B，D-038） ----------

# 大纲里每节的桩才有的字段：检查点检验什么、写这一节要读哪几页、这一节讲清哪几点、做完后练习场满足什么
STUB_ONLY_KEYS = ("check", "reading", "teaches", "state_after")


def stubs_of(outline: dict) -> list[dict]:
    """大纲里的小节桩，全书顺序（和课程计划的 /sections/i 一一对应）。"""
    return sections_of(outline)


def fill_stub(stub: dict, section: dict) -> dict:
    """写好的一节 + 大纲里它的桩 → 课程计划里的一节。

    INVARIANT: 大纲是各节之间的接口约定（D-038），写一节的人不能改：
      - 标题、分钟数、目标、任务，以及新词和它的定义（terms），以大纲为准；
      - 这一节做完后练习场要满足的断言（state_after）并进大纲指定题型的那道检查点的 checks——
        练习场实跑时，没兑现约定的是这一节自己，而不是等到依赖它的下一节才出错。
    """
    out = {k: copy.deepcopy(v) for k, v in section.items() if k not in STUB_ONLY_KEYS}
    out.update({k: copy.deepcopy(stub[k]) for k in ("title", "minutes", "goal", "mission", "terms") if k in stub})
    contract = stub.get("state_after") or []
    want = (stub.get("check") or {}).get("type")
    target = next((c for c in out.get("checkpoint") or [] if c.get("type") == want), None)
    if contract and target is not None:
        have = {str(k.get("desc")) for k in target.get("checks") or []}
        target["checks"] = list(target.get("checks") or []) + [
            {**k, "contract": True} for k in contract if str(k.get("desc")) not in have]
    return out


def with_section(outline: dict, index: int, section: dict) -> dict:
    """大纲里只把第 index 节换成写好的内容，其余仍是桩。各节并行写时，用它检查"这一节放进大纲里对不对"：
    前面几节的新词（桩里有）算已经讲过，后面的节不影响这一节。"""
    return replace_part(outline, f"/sections/{index}", fill_stub(stubs_of(outline)[index], section))


def assemble(outline: dict, sections: list[dict]) -> dict:
    """大纲 + 前 len(sections) 节写好的内容 → 课程计划。还没写的节不放进去。"""
    out = {k: copy.deepcopy(v) for k, v in outline.items() if k != "parts"}
    parts, i = [], 0
    for p in outline.get("parts") or []:
        secs = []
        for stub in p.get("sections") or []:
            if i < len(sections):
                secs.append(fill_stub(stub, sections[i]))
            i += 1
        if secs:
            parts.append({**{k: v for k, v in p.items() if k != "sections"}, "sections": secs})
    out["parts"] = parts
    return out


# ---------- 按地址切分和替换（D-035：定点修复） ----------

def plan_parts(plan: dict) -> dict[str, object]:
    """课程计划切成可比较的"部分"：每一节 /sections/i（全书顺序）、练习场 /lab、其余每个顶层字段 /<字段>。
    parts（部分的标题和小节归属）算作 /parts，小节内容不在里面。"""
    out: dict[str, object] = {f"/sections/{i}": s for i, s in enumerate(sections_of(plan))}
    for k, v in plan.items():
        if k == "parts":
            out["/parts"] = [{**{kk: vv for kk, vv in p.items() if kk != "sections"}, "sections": len(p.get("sections") or [])}
                             for p in v or []]
        elif k != "provenance":
            out[f"/{k}"] = v
    return out


def plan_addresses(plan: dict) -> set[str]:
    """课程计划里所有合法的地址（检验器只能指这些地方）。"""
    out = {"", *plan_parts(plan)}
    for i, s in enumerate(sections_of(plan)):
        out |= {f"/sections/{i}/checkpoint/{j}" for j in range(len(s.get("checkpoint") or []))}
    return out


def replace_part(plan: dict, address: str, value) -> dict:
    """返回替换了一个部分的新计划（不改原计划）。地址是 repair_unit 给出的修复单位：""、/sections/i 或 /<顶层字段>。"""
    if address == "":
        if not isinstance(value, dict):
            raise CourseError("整份替换要给一个完整的课程计划对象")
        return copy.deepcopy(value)
    out = copy.deepcopy(plan)
    segs = address.strip("/").split("/")
    if segs[0] == "sections" and len(segs) == 2 and segs[1].isdigit():
        i = int(segs[1])
        for p in out.get("parts") or []:
            n = len(p.get("sections") or [])
            if i < n:
                if not isinstance(value, dict):
                    raise CourseError(f"{address} 要是一个小节对象")
                p["sections"][i] = copy.deepcopy(value)
                return out
            i -= n
        raise CourseError(f"没有这一节：{address}")
    if len(segs) == 1 and segs[0] and segs[0] not in ("parts", "provenance", "sections"):
        out[segs[0]] = copy.deepcopy(value)
        return out
    raise CourseError(f"不能按这个地址替换：{address}")


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

CLIENT_EVENTS = {"open", "done", "undone", "skip", "activity", "resume", "term", "term_miss", "load_rating"}


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
    if event == "resume":                        # 暂停后回来：离开的这段算不算学习，由学习者说（D-027）
        payload = _resume_payload(extra)
        if index is None:
            return ClientEvent("resumed", "unit", unit, None, payload=payload)
        section(plan, index)
        return ClientEvent("resumed", "section", section_id(unit, index), index, payload=payload)
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


def _resume_payload(extra: dict) -> dict:
    start, counted = extra.get("away_start"), extra.get("counted")
    try:
        dt.datetime.fromisoformat(str(start))
    except ValueError:
        raise CourseError("away_start 要是 ISO 时间，如 2026-09-26T10:05:00") from None
    if not isinstance(counted, bool):
        raise CourseError("counted 要是 true 或 false")
    return {"away_start": str(start)[:19], "counted": counted}


def _activity_kind(extra: dict) -> str:
    kind = extra.get("kind") or "page"
    if kind not in ("page", "video"):
        raise CourseError("activity 的 kind 只能是 page 或 video")
    return kind
