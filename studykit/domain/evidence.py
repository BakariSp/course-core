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
class Feed:
    """这个 verb 被哪个投影读、读哪些字段（D-024）。tests/test_lineage.py 检查投影的代码里真的读了它。"""
    projection: str                    # mastery / node_state / progress / course_eval（学习时长由 timeline 的规则算，不在这里声明）
    fields: str                        # 给人看：读了哪些字段
    object_type: str = ""              # 只有这种对象才读；空 = 都读


@dataclass(frozen=True)
class Verb:
    name: str
    spec: str                          # core 或学科 spec 的名字（D-023）
    objects: tuple[str, ...]           # 允许的 object_type
    required: tuple[str, ...] = ()     # payload 必填字段
    actors: tuple[str, ...] = ACTOR_TYPES
    needs_cause: bool = False          # 必须有 caused_by（如批改指向被批改的作答）
    feeds: tuple[Feed, ...] = ()       # 被谁用
    title: str = ""                    # 给人看的名字


CORE_VERBS = (
    Verb("answered", "core", ("question", "checkpoint"), ("response",), ("learner",), title="作答", feeds=(
        Feed("mastery", "score · 题目等级 · 考的概念 · 得分点（parts）", "question"),
        Feed("node_state", "做对没有 · 考的知识点", "checkpoint"),
        Feed("progress", "做对没有 · 第几题", "checkpoint"),
        Feed("course_eval", "第一次就对了吗 · 试了几次", "checkpoint"))),
    Verb("graded", "core", ("question",), ("note",), ("agent", "learner"), needs_cause=True, title="批改",
         feeds=(Feed("mastery", "score（替代被批改的那条作答）· 备注 · 得分点（parts）"),)),
    Verb("submitted_exam", "core", ("lesson",), ("answers",), ("learner",), title="交卷（整卷一次交，D-031）"),
    Verb("logged_practice", "core", ("external",), ("level", "note"), ("learner", "agent"), title="课外练习",
         feeds=(Feed("mastery", "score · 等级 · 概念"),)),
    Verb("opened", "core", ("section",), actors=("learner",), title="打开小节",
         feeds=(Feed("progress", "最后学到哪一节"),)),
    Verb("completed", "core", ("section",), actors=("learner",), title="学完（没有检查点的节）",
         feeds=(Feed("progress", "这一节算通过"),)),
    Verb("uncompleted", "core", ("section",), actors=("learner",), title="取消学完",
         feeds=(Feed("progress", "这一节不算通过"),)),
    Verb("skipped", "core", ("section",), actors=("learner",), title="跳过",
         feeds=(Feed("progress", "跳过的节"), Feed("course_eval", "跳过了没有"))),
    Verb("pinged", "core", ("section", "unit"), ("kind",), ("learner",), title="心跳（还在学）"),
    Verb("resumed", "core", ("section", "unit"), ("away_start", "counted"), ("learner",), title="暂停后回来"),
    Verb("viewed", "core", ("node",), actors=("learner",), title="点开新词",
         feeds=(Feed("course_eval", "新词被点开的比例"),)),
    Verb("voted_term", "core", ("node",), ("vote",), ("learner",), title="新词 👍👎", feeds=(
        Feed("node_state", "「我早就知道」= 会了"), Feed("progress", "每个新词的投票"),
        Feed("course_eval", "新词预测准不准"))),
    # 回顾（D-051）：自己说记不记得。先只记下来；掌握度、复习排程怎么用它由 D-056 定
    Verb("recalled", "core", ("node",), ("remembered",), ("learner",), title="回顾：记不记得"),
    Verb("flagged_unexplained", "core", ("section",), ("text",), ("learner",), title="划出没讲的词",
         feeds=(Feed("course_eval", "漏报的新词"),)),
    Verb("rated_load", "core", ("section",), ("rating",), ("learner",), title="费劲程度",
         feeds=(Feed("progress", "自评 1-5"), Feed("course_eval", "预测负荷 vs 自评"))),
    Verb("requested_hint", "core", ("checkpoint",), ("level",), ("learner",), title="要提示",
         feeds=(Feed("progress", "用到第几级提示"), Feed("course_eval", "最高提示级别"))),
    Verb("passed_section", "core", ("section",), actors=("system",), title="整节通过",
         feeds=(Feed("node_state", "这一节的新词算会用了"),)),
    Verb("practiced", "core", ("lab",), ("op",), ("learner",), title="练习场操作"),   # op：run / restore / reset / fill
    Verb("observed", "core", ("node",), ("polarity", "note"), ("agent", "learner"), title="观察",
         feeds=(Feed("node_state", "薄弱 / 会了 · 原话 · 谁观察的"),)),
    Verb("logged_time", "core", ("time",), ("start", "minutes"), ("learner", "agent"), title="补录学习时间"),
    # 老师对学习者的观察（D-047）：什么讲法有效、在哪会过载。学习者说"不对"就推翻它
    Verb("proposed_strategy", "core", ("learner",), ("text",), ("agent", "learner"), title="老师的观察",
         feeds=(Feed("profile", "现在还有效的观察 · 出处"),)),
    Verb("refuted_strategy", "core", ("learner",), (), ("learner", "agent"), needs_cause=True, title="推翻一条观察",
         feeds=(Feed("profile", "这条观察不再给老师看"),)),
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

    def get(self, name: str) -> Verb | None:
        return self._by.get(name)

    def all(self) -> list[Verb]:
        return list(self._by.values())

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
