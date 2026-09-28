"""课程定义（D-047）：阶段 → 学科 → 单元，一份结构化数据（progress/course.yaml）。

INVARIANT: 这里只有"学什么、按什么顺序、看哪些材料、学习者提了什么要求"。学没学完、备没备好都不在这里，
由证据和备课记录算出（单元状态见 unit_state）。列表顺序就是学习顺序。
"""
from __future__ import annotations

from dataclasses import dataclass
from studykit.domain.errors import DomainError
from studykit.domain.ids import UnitId

SCHEMA_VERSION = 2
SOURCE_KINDS = ("course", "video", "page", "book", "exercise")


class CourseDefError(DomainError):
    pass


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    url: str
    kind: str = "page"


@dataclass(frozen=True)
class UnitSource:
    """这个单元看主课的哪一部分：ref 指向学科的 sources，url 是具体那一页（可以省略）。"""
    ref: str
    url: str = ""
    note: str = ""


@dataclass(frozen=True)
class Unit:
    id: str
    title: str
    sources: tuple[UnitSource, ...] = ()
    requests: tuple[str, ...] = ()        # 学习者对这个单元的要求（给备课老师）
    scope_open: bool = False              # 范围还没定（一整门讲座要拆）：不备课、不预备


@dataclass(frozen=True)
class ProjectLink:
    where: str                            # 在项目里哪里用到
    concept: str                          # 背后的知识点


@dataclass(frozen=True)
class Subject:
    id: str
    title: str
    priority: str
    stage: str
    goal: str
    sources: tuple[Source, ...]
    units: tuple[Unit, ...]
    project_links: tuple[ProjectLink, ...] = ()
    scope: str = ""                       # 看哪部分（整个学科的说明，具体到单元的在 Unit.sources）

    def source(self, ref: str) -> Source | None:
        return next((s for s in self.sources if s.id == ref), None)


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    weeks: tuple[int, int]
    pass_: str
    practice: str = ""


@dataclass(frozen=True)
class Capability:
    """终点的一条能力（D-048）：学完能做到什么 + 怎么验收；units 是主要服务它的单元。"""
    id: str
    can: str
    accept: str
    units: tuple[str, ...] = ()


@dataclass(frozen=True)
class Phase:
    """路线上的一个阶段（D-064）：一组连着学的单元，fills 是它补终点地图的哪几段。"""
    title: str
    units: tuple[str, ...]
    fills: tuple[str, ...] = ()


@dataclass(frozen=True)
class CourseDef:
    goal: str
    stages: tuple[Stage, ...]
    subjects: tuple[Subject, ...]
    destination: tuple[Capability, ...] = ()
    phases: tuple[Phase, ...] = ()        # 学习路线（D-048、D-064）：按阶段分组，可以跨学科

    @property
    def path(self) -> tuple[str, ...]:
        """路线上的单元，按学习顺序（各阶段首尾相接）。"""
        return tuple(u for p in self.phases for u in p.units)

    def units(self) -> list[tuple[Subject, Unit]]:
        """全部单元，按学习顺序：路径里的按路径顺序在前，没进路径的按学科顺序排在后面。"""
        by_subject = [(s, u) for s in self.subjects for u in s.units]
        rank = {uid: i for i, uid in enumerate(self.path)}
        return sorted(by_subject, key=lambda su: rank.get(su[1].id, len(rank)))

    def serves(self, unit: str) -> list[str]:
        """这个单元服务终点的哪几条能力。"""
        return [c.id for c in self.destination if unit in c.units]

    def unit_ids(self) -> list[str]:
        return [u.id for _, u in self.units()]

    def find(self, unit: str) -> tuple[Subject, Unit]:
        for s, u in self.units():
            if u.id == unit:
                return s, u
        raise CourseDefError(f"课程里没有单元 {unit}")

    def subject(self, sid: str) -> Subject:
        for s in self.subjects:
            if s.id == sid:
                return s
        raise CourseDefError(f"课程里没有学科 {sid}")

    def stage(self, sid: str) -> Stage | None:
        return next((s for s in self.stages if s.id == sid), None)

    def hosts(self) -> set[str]:
        """agent 能打开的网站 = 课程里所有材料的域名（D-047：白名单跟着材料走）。"""
        urls = [x.url for s in self.subjects for x in s.sources] + [x.url for _, u in self.units() for x in u.sources]
        return {host_of(u) for u in urls if u}


def host_of(url: str) -> str:
    """网址的域名（去掉 www. 和端口）。domain 层不用 urllib（test_architecture），按 scheme://netloc/... 切。"""
    rest = url.split("://", 1)[1] if "://" in url else ""
    return norm_host(rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0])


def norm_host(netloc: str) -> str:
    host = netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host


# ---------- 读：dict（YAML）→ CourseDef，所有检查都在这里 ----------

def parse_course(data: dict) -> CourseDef:
    if (data or {}).get("schema_version") != SCHEMA_VERSION:
        raise CourseDefError(f"course.yaml 的 schema_version 要是 {SCHEMA_VERSION}")
    stages = tuple(_stage(x) for x in data.get("stages") or [])
    stage_ids = {s.id for s in stages}
    subjects, seen_units, seen_subjects = [], set(), set()
    for s in data.get("subjects") or []:
        sid = _req(s, "id", "学科")
        if sid in seen_subjects:
            raise CourseDefError(f"学科 id 重复：{sid}")
        seen_subjects.add(sid)
        if s.get("stage") and s["stage"] not in stage_ids:
            raise CourseDefError(f"学科 {sid} 的 stage {s['stage']} 不在 stages 里")
        sources = tuple(_source(x, sid) for x in s.get("sources") or [])
        refs = {x.id for x in sources}
        units = []
        for u in s.get("units") or []:
            uid = str(UnitId(_req(u, "id", f"学科 {sid} 的单元")))
            if uid in seen_units:
                raise CourseDefError(f"单元 id 重复：{uid}")
            seen_units.add(uid)
            usrc = tuple(UnitSource(str(x.get("ref") or ""), str(x.get("url") or ""), str(x.get("note") or ""))
                         for x in u.get("sources") or [])
            for x in usrc:
                if x.ref not in refs:
                    raise CourseDefError(f"单元 {uid} 的材料 ref {x.ref!r} 不在学科 {sid} 的 sources 里")
            scope = u.get("scope", "set")
            if scope not in ("set", "open"):
                raise CourseDefError(f"单元 {uid} 的 scope 只能是 set 或 open")
            units.append(Unit(uid, str(u.get("title") or uid), usrc, tuple(str(r) for r in u.get("requests") or []), scope == "open"))
        links = tuple(ProjectLink(str(x.get("where", "")), str(x.get("concept", ""))) for x in s.get("project_links") or [])
        subjects.append(Subject(sid, str(s.get("title") or sid), str(s.get("priority") or ""), str(s.get("stage") or ""),
                                str(s.get("goal") or ""), sources, tuple(units), links, str(s.get("scope") or "")))
    destination = _destination(data, seen_units)
    phases = _phases(data, seen_units, {c.id for c in destination})
    return CourseDef(str(data.get("goal") or ""), stages, tuple(subjects), destination, phases)


def _phases(data: dict, units: set[str], caps: set[str]) -> tuple[Phase, ...]:
    out = []
    for i, x in enumerate(data.get("path") or []):
        if not isinstance(x, dict):
            raise CourseDefError(f"路径的第 {i + 1} 项要是一个阶段（title、fills、units），不是 {x!r}")
        title = _req(x, "title", f"路径的第 {i + 1} 个阶段")
        pu = tuple(str(u) for u in x.get("units") or [])
        if not pu:
            raise CourseDefError(f"阶段「{title}」没有单元")
        fills = tuple(str(f) for f in x.get("fills") or [])
        if any(f not in caps for f in fills):
            raise CourseDefError(f"阶段「{title}」的 fills 里有终点没有的一项：{[f for f in fills if f not in caps]}")
        out.append(Phase(title, pu, fills))
    path = [u for p in out for u in p.units]
    bad = [x for x in path if x not in units]
    if bad or len(set(path)) != len(path):
        raise CourseDefError(f"路径里有不存在或重复的单元：{bad or path}")
    return tuple(out)


def _destination(data: dict, units: set[str]) -> tuple[Capability, ...]:
    caps, seen = [], set()
    for x in data.get("destination") or []:
        cid = _req(x, "id", "终点能力")
        if cid in seen:
            raise CourseDefError(f"终点能力 id 重复：{cid}")
        seen.add(cid)
        cu = tuple(str(u) for u in x.get("units") or [])
        if any(u not in units for u in cu):
            raise CourseDefError(f"终点能力 {cid} 的 units 里有课程里没有的单元")
        caps.append(Capability(cid, _req(x, "can", f"终点能力 {cid}"), _req(x, "accept", f"终点能力 {cid}"), cu))
    return tuple(caps)


def _req(d: dict, key: str, what: str) -> str:
    v = (d or {}).get(key)
    if not v:
        raise CourseDefError(f"{what}缺少 {key}")
    return str(v)


def _stage(x: dict) -> Stage:
    weeks = x.get("weeks") or [0, 0]
    return Stage(_req(x, "id", "阶段"), str(x.get("title") or x["id"]), (int(weeks[0]), int(weeks[1])),
                 str(x.get("pass") or ""), str(x.get("practice") or ""))


def _source(x: dict, sid: str) -> Source:
    kind = x.get("kind", "page")
    if kind not in SOURCE_KINDS:
        raise CourseDefError(f"学科 {sid} 的材料 kind 只能是 {'/'.join(SOURCE_KINDS)}")
    return Source(_req(x, "id", f"学科 {sid} 的材料"), str(x.get("title") or x["id"]), _req(x, "url", f"学科 {sid} 的材料"), kind)


# ---------- 给 agent 的课程信息（替代原来从 markdown 里抠出来的一行） ----------

def render_for_brief(course: CourseDef, unit: str) -> str:
    s, u = course.find(unit)
    stage = course.stage(s.stage)
    lines = [f"- 学科：{s.title}（优先级 {s.priority or '—'}）",
             f"- 学科目标（学到什么程度）：{s.goal or '—'}"]
    if stage:
        lines.append(f"- 所在阶段：{stage.title}（第 {stage.weeks[0]}–{stage.weeks[1]} 周），过关标准：{stage.pass_}")
    lines.append("- 主课：" + ("；".join(f"{x.title} {x.url}" for x in s.sources) or "—"))
    if s.scope:
        lines.append(f"- 看哪部分：{s.scope}")
    if u.sources:
        lines.append("- 这个单元看哪部分：" + "；".join(
            f"{(s.source(x.ref) or Source(x.ref, x.ref, '')).title}{' · ' + x.note if x.note else ''} {x.url}".strip() for x in u.sources))
    if s.project_links:
        lines.append("- 在学习者的项目里哪里用到：" + "；".join(f"{x.where} → {x.concept}" for x in s.project_links))
    return "\n".join(lines)


# ---------- 单元状态（学习者看到的词表，PRD_V2 §0.2） ----------
#
# 内容线（备课）和学习线（检查点）合并成一个状态。只返回 key 和事实，文字由界面决定。
#   queued 排队中 · preparing 准备中 · blocked 暂时不能学 · ask 待确认 · ready 能学 · learning 在学 · done 学完
# 附加标签：new_version（有通过检验、还没换过去的新版本）
# 学完 = 各节都走过了（通过、点学完、或跳过，D-064）。检查点对了几道、单元题几分是事实，由界面显示，不挡学完。

UNIT_STATES = ("queued", "preparing", "blocked", "ask", "ready", "learning", "done")


def unit_state(*, scope_open: bool, prep_stage: str, walked: int | None, sections: int | None) -> dict:
    """prep_stage 是备课记录算出的阶段（todo / preparing / published / ready / escalated / interrupted）；
    walked / sections 是现在这一版课程页走过几节、一共几节，没有课程页时是 None。"""
    tags = ["new_version"] if prep_stage == "ready" else []
    if scope_open:
        return {"key": "ask", "tags": []}
    if prep_stage == "preparing" and sections is None:
        return {"key": "preparing", "tags": []}
    if sections is not None:
        if walked >= sections:
            return {"key": "done", "tags": tags}
        return {"key": "learning" if walked else "ready", "tags": tags}
    if prep_stage in ("escalated", "interrupted"):
        # 学习者不需要决定什么（原则一）：自动续跑 / 换策略 / 上报开发者是系统的事
        return {"key": "blocked", "tags": []}
    if prep_stage == "preparing":
        return {"key": "preparing", "tags": []}
    return {"key": "queued", "tags": []}


def next_steps(units: list[dict]) -> list[dict]:
    """下一步学什么（推理，不存）。units 按学习顺序，每个带 id、state（unit_state 的结果）、last_at（最近一次学的时间）。
    规则（D-064）：先接着学最近一次学的单元（其余在学的按时间往后排）；再开始路线上第一个能学的。每一条带理由。"""
    learning = sorted((u for u in units if u["state"]["key"] == "learning"), key=lambda u: u.get("last_at") or "", reverse=True)
    out = [{"unit": u["id"], "kind": "continue", "reason": "你上次学到这里" if i == 0 else "这个单元学到一半"}
           for i, u in enumerate(learning)]
    first = next((u for u in units if u["state"]["key"] == "ready"), None)
    if first:
        out.append({"unit": first["id"], "kind": "start", "reason": "路线上下一个已经备好的单元"})
    return out


def current_phase(phases: tuple[Phase, ...], states: dict[str, str]) -> int | None:
    """你在第几个阶段（D-064）：第一个还有没学完单元的阶段。全学完了是 None。"""
    return next((i for i, p in enumerate(phases) if any(states.get(u) != "done" for u in p.units)), None)


def queued_after(order: list[str], unit: str) -> str | None:
    """排队中的单元什么时候开始准备：学上一个单元时系统在后台备它（D-040 ②）。返回上一个单元。"""
    i = order.index(unit)
    return order[i - 1] if i > 0 else None
