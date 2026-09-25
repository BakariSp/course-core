"""证据信封（D-021，docs/data-model.md §1）：所有"发生过的事"用同一个形状。

    谁（actor）· 关于谁（learner）· 做了什么（verb）· 对什么（object + 版本）
    · 在哪（unit / plan / section）· 因为什么（caused_by）· 结果（score / ok / pending）
    · 影响哪些知识节点（nodes）· 其余字段（payload，形状由 verb 决定）

INVARIANT: 证据只追加，永不修改。批改 = 追加一条 graded，caused_by 指向被批改的 answered。
INVARIANT: 信封是不变层；新功能 = 在 VerbRegistry 里注册一个新 verb，不改信封、不改存储。
WHY: 原来三份日志各有形状（attempts / runs / study_log），每加一种功能就多一种形状，
投影（掌握度、时长、节点状态）要分别读三份；"谁观察的""属于哪次提交""因为哪次提示"都没地方放。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from studykit.domain.errors import InvalidEvidence
from studykit.domain.ids import NODE_ID_RE

ACTOR_TYPES = ("learner", "agent", "system")


@dataclass(frozen=True)
class Actor:
    type: str                  # learner / agent / system
    id: str                    # me / tutor / tutor-prep / studykit
    role: str = ""             # agent 的角色（tutor、tutor-prep、explainer…），权限按角色分（D-022）
    variant: str = ""          # agent 用的是哪个版本组合（Variant 哈希）


SYSTEM = Actor("system", "studykit")


def learner_actor(learner: str) -> Actor:
    return Actor("learner", learner)


@dataclass(frozen=True)
class Evidence:
    id: str
    ts: str                    # ISO 时间，精确到秒；字符串比较就是时间比较
    learner: str
    actor: Actor
    verb: str
    object_type: str
    object_id: str
    object_version: str = ""
    unit: str | None = None
    plan: str | None = None
    section: int | None = None
    caused_by: str | None = None
    run: str | None = None     # 由哪次 agent 运行产生（D-022）
    score: float | None = None
    ok: bool | None = None
    pending: bool = False
    nodes: tuple[str, ...] = ()
    payload: dict = field(default_factory=dict, hash=False, compare=False)


# ---------- 对象 id 的写法 ----------

def question_id(lesson: str, qid: str) -> str:
    return f"{lesson}#{qid}"


def split_question(object_id: str) -> tuple[str, str]:
    lesson, _, qid = object_id.partition("#")
    return lesson, qid


def section_id(unit: str, section: int) -> str:
    return f"{unit}#{section}"


def checkpoint_id(unit: str, section: int, idx: int) -> str:
    return f"{unit}#{section}.{idx}"


# ---------- verb 注册表 ----------

@dataclass(frozen=True)
class Verb:
    name: str
    spec: str                          # core 或学科 spec 的名字（D-023）
    objects: tuple[str, ...]           # 允许的 object_type
    required: tuple[str, ...] = ()     # payload 必填字段
    actors: tuple[str, ...] = ACTOR_TYPES
    needs_cause: bool = False          # 必须有 caused_by（如批改指向被批改的作答）


CORE_VERBS = (
    Verb("answered", "core", ("question", "checkpoint"), ("response",), ("learner",)),
    Verb("graded", "core", ("question",), ("note",), ("agent", "learner"), needs_cause=True),
    Verb("logged_practice", "core", ("external",), ("level", "note"), ("learner", "agent")),
    Verb("opened", "core", ("section",), actors=("learner",)),
    Verb("completed", "core", ("section",), actors=("learner",)),
    Verb("uncompleted", "core", ("section",), actors=("learner",)),
    Verb("skipped", "core", ("section",), actors=("learner",)),
    Verb("pinged", "core", ("section", "unit"), ("kind",), ("learner",)),
    Verb("viewed", "core", ("node",), actors=("learner",)),
    Verb("voted_term", "core", ("node",), ("vote",), ("learner",)),
    Verb("flagged_unexplained", "core", ("section",), ("text",), ("learner",)),
    Verb("rated_load", "core", ("section",), ("rating",), ("learner",)),
    Verb("requested_hint", "core", ("checkpoint",), ("level",), ("learner",)),
    Verb("passed_section", "core", ("section",), actors=("system",)),
    Verb("practiced", "core", ("lab",), ("op",), ("learner",)),     # 练习环境里的操作：run / restore / reset / fill
    Verb("observed", "core", ("node",), ("polarity", "note"), ("agent", "learner")),
    Verb("logged_time", "core", ("time",), ("start", "minutes"), ("learner", "agent")),
)


class VerbRegistry:
    def __init__(self, verbs=CORE_VERBS):
        self._by: dict[str, Verb] = {}
        for v in verbs:
            self.register(v)

    def register(self, verb: Verb) -> None:
        if verb.name in self._by and self._by[verb.name] != verb:
            raise InvalidEvidence(f"verb {verb.name} 已经被 {self._by[verb.name].spec} 注册过")
        self._by[verb.name] = verb

    def __contains__(self, name: str) -> bool:
        return name in self._by

    def validate(self, e: Evidence) -> None:
        v = self._by.get(e.verb)
        if v is None:
            raise InvalidEvidence(f"没有注册的 verb：{e.verb}")
        if e.object_type not in v.objects:
            raise InvalidEvidence(f"{e.verb} 的对象只能是 {'/'.join(v.objects)}，不能是 {e.object_type}")
        if e.actor.type not in v.actors:
            raise InvalidEvidence(f"{e.verb} 只能由 {'/'.join(v.actors)} 写，不能是 {e.actor.type}")
        missing = [k for k in v.required if k not in e.payload]
        if missing:
            raise InvalidEvidence(f"{e.verb} 缺少字段：{', '.join(missing)}")
        if v.needs_cause and not e.caused_by:
            raise InvalidEvidence(f"{e.verb} 必须指明 caused_by")
        if e.pending != (e.verb == "answered" and e.score is None):
            raise InvalidEvidence("只有还没判分的作答才是 pending，pending 的作答不能有分数")
        if e.score is not None and not 0 <= e.score <= 1:
            raise InvalidEvidence(f"分数要在 0-1 之间：{e.score}")
        bad = [n for n in e.nodes if not NODE_ID_RE.match(n)]
        if bad:
            raise InvalidEvidence(f"知识节点 id 格式不对：{', '.join(bad)}")
