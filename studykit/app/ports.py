"""端口：应用层需要外界提供的能力。只有接口（Protocol），实现在 adapters/ 和 specs/，由 bootstrap 注入。"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

from studykit.domain.assessment import Checker, CheckpointResult, Lesson
from studykit.domain.evidence import Actor, Evidence, Verb
from studykit.domain.harness import Grade, Run, RunStep, Variant
from studykit.domain.curriculum import CourseDef
from studykit.domain.knowledge import Node
from studykit.domain.profile import Profile
from studykit.domain.plan_check import PlanRule, PracticeModule


class Clock(Protocol):
    def now(self) -> dt.datetime: ...


class IdGen(Protocol):
    def new(self) -> str: ...


class EvidenceStore(Protocol):
    """证据：只追加。append 的多条证据在一个事务里，要么全写进去，要么都不写。"""

    def append(self, *evidence: Evidence) -> None: ...

    def query(self, learner: str, *, unit: str | None = None, plan: str | None = None,
              verbs: Iterable[str] | None = None, object_type: str | None = None,
              object_id: str | None = None) -> list[Evidence]:
        """按时间顺序（同一时间按写入顺序）。"""

    def get(self, evidence_id: str) -> Evidence | None: ...

    def last_seq(self, learner: str) -> int:
        """这个学习者最后一条证据的写入序号（没有就是 0）。证据只追加：序号没变，证据就没变。"""


class PlanStore(Protocol):
    """发布过的课程计划：每一版不可变；某个单元的当前版本 = 最近一次发布的那一版。"""

    def publish(self, unit: str, plan: dict, ts: str, actor: Actor) -> None: ...
    def add_version(self, unit: str, plan: dict, ts: str) -> None: ...
    def versions(self, unit: str) -> list[dict]: ...
    def current(self, unit: str) -> dict | None: ...
    def version(self, unit: str, plan_id: str) -> dict | None: ...
    def units(self) -> list[str]: ...


class Content(Protocol):
    """人写、在 git 里审的内容：题、知识图、课程定义、学习者资料、设置。"""

    def lesson(self, ref: str) -> Lesson: ...
    def lessons(self) -> list[Lesson]: ...
    def lesson_dir(self, ref: str) -> Path: ...
    def write_lesson(self, ref: str, quiz: dict, key: dict, files: dict[str, str]) -> None:
        """发布一套题（D-041）：quiz.yaml、key.yaml、code/ 下的文件。已经有这套题时报错（学习者可能已经在做）。"""
    def graph(self) -> dict[str, Node]: ...
    def add_nodes(self, nodes: list[dict], by: str = "") -> list[str]: ...
    def add_misconceptions(self, proposed: dict[str, dict[str, str]], by: str = "") -> list[str]:
        """把出题 agent 提出的误解写进知识图（D-056）。已有的不覆盖；返回新增的「知识点/误解」。"""
    def course(self) -> CourseDef: ...
    def profile(self) -> Profile: ...
    def settings(self) -> dict: ...


@dataclass(frozen=True)
class CheckCtx:
    """检验器执行时的环境（code / terminal 要用到目录）。"""
    lesson_ref: str
    lesson_dir: Path
    workdir: Path              # 这道题专属的沙箱目录
    root: Path                 # 仓库根目录


class PracticeEnv(Protocol):
    """练习环境（学科 spec 提供；cs-practice 是一个真实的 bash 练习场，D-014）。"""

    def open(self, unit: str, files: list[dict]) -> dict: ...
    def home(self, unit: str) -> str: ...
    def ensure(self, unit: str, files: list[dict]) -> Path:
        """练习场目录不存在就按单元开始时的样子建出来。"""
    def run(self, unit: str, files: list[dict], cmd: str) -> dict: ...
    def snapshot_once(self, unit: str, plan: str, section: int, files: list[dict]) -> None: ...
    def restore(self, unit: str, plan: str, section: int) -> None: ...
    def reset(self, unit: str, files: list[dict]) -> None: ...
    def run_once(self, unit: str, cmd: str) -> tuple[int, str]: ...
    def files(self, unit: str) -> list[str]:
        """练习场里的文件（相对路径），给页面上的「文件」列出来（D-057）。"""
    def read(self, unit: str, rel: str) -> str: ...
    def write(self, unit: str, rel: str, content: str) -> None:
        """只能写练习场里的文本文件；路径出了练习场、内容太长就报错。"""
    def check(self, unit: str, checks: list[dict]) -> list[tuple[bool, str, dict]]: ...
    def verify(self, plan: dict, workdir: Path) -> dict:
        """把一份计划的练习从头走一遍（发布前的闸门，D-014）。"""
    def stop_all(self) -> None: ...


CheckpointGrader = Callable[[str, dict, object], CheckpointResult]     # (unit, item, response)


@dataclass
class Spec:
    """学科 spec（D-023）：core 只通过这些扩展点认识一个学科。"""
    name: str
    verbs: tuple[Verb, ...] = ()
    checkers: tuple[Checker, ...] = ()
    modules: tuple[PracticeModule, ...] = ()                                      # 练习形式（D-052）：检查点题型 + 说明 + 写法 + 判分
    practice: PracticeEnv | None = None
    plan_rules: tuple[PlanRule, ...] = ()                                         # 课程计划的附加检查
    plan_fields: dict = field(default_factory=dict)                               # 课程计划 schema 的附加字段


# ---------- harness（D-022） ----------

@dataclass(frozen=True)
class AgentDef:
    """一个 agent 的定义（agents/<name>/，进 git）。"""
    name: str
    dir: Path
    spec: dict                 # agent.yaml

    def file(self, key: str) -> Path:
        return self.dir / self.spec[key]


@dataclass
class RawRun:
    """agent loop 跑完交回来的东西，已经换成和 loop 无关的步骤格式。"""
    steps: list[RunStep]
    exit_code: int | None
    seconds: float


@dataclass
class Completion:
    """不带工具的一问一答的结果（评审、评分、批改）：回答文字 + 用量和费用（美元），费用要记账。"""
    text: str
    tokens: int = 0
    cost_usd: float = 0.0


class AgentRuntime(Protocol):
    """agent loop（现在借用 pi）。换 loop = 换一个实现，运行记录的格式不变。"""
    version: str

    def run(self, agent: AgentDef, workspace: Path, model: dict, tools: list[str], timeout: int,
            system_prompt: Path) -> RawRun:
        """system_prompt：harness 按 prompt 部件拼好、存在运行目录里的那一份（D-033），agent 读的就是它。"""
    def complete(self, model: dict, system: str, prompt_file: Path, workspace: Path, timeout: int) -> Completion: ...


@dataclass
class Page:
    status: int
    final_url: str
    title: str
    text: str
    links: list[tuple[str, str]]       # (网址, 链接文字)


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, reason: str = ""):
        super().__init__(f"HTTP {status}：{url}" if status else f"访问失败：{url}（{reason}）")
        self.url, self.status = url, status


class Fetcher(Protocol):
    def fetch(self, url: str, timeout: float = 20.0) -> Page: ...


class RunStore(Protocol):
    """Run、步骤、评分：只追加。"""

    def add_run(self, run: Run, variant: Variant, steps: list[RunStep]) -> None: ...
    def run(self, run_id: str) -> Run | None: ...
    def runs(self, agent: str) -> list[Run]: ...
    def steps(self, run_id: str) -> list[RunStep]: ...
    def variant(self, variant_id: str) -> Variant | None: ...
    def add_variant(self, variant: Variant, first_seen: str) -> None:
        """同一个版本只记第一次见到的时间（不是跑 agent 的调用点也要记版本：评分模型、简答题批改，D-033）。"""
    def variants(self, agent: str) -> list[tuple[Variant, str]]:
        """（版本，第一次见到的时间），按时间顺序。"""
    def put_blobs(self, blobs: dict[str, str]) -> None:
        """版本组成的内容，哈希 → 文本；同一个哈希只存一次，存了就不能改（D-033）。"""
    def blob(self, key: str) -> str | None: ...
    def add_grade(self, grade: Grade) -> None: ...
    def grades(self, run_id: str | None = None, agent: str | None = None) -> list[Grade]: ...


class JobRunner(Protocol):
    """后台任务（D-032）：同一个 key 同时只跑一个。状态只在这个进程里，服务器重启就忘了——
    真正的结果（Run、Grade）已经写进数据库，这里只回答"现在是不是正在跑"。"""

    def start(self, key: str, fn: Callable[[], object]) -> bool:
        """开始跑；同一个 key 已经在跑时不再开，返回 False。"""
    def status(self, key: str) -> dict | None:
        """{"state": running | done | error, "started": ISO 时间, "error": 出错信息}；没跑过是 None。"""


class PrepStatus(Protocol):
    """一个单元的备课现在走到哪（D-040）。跨进程可见（网页线程、命令行都能发起备课），也是锁：同一个单元同时只备一次。"""

    def begin(self, unit: str) -> bool:
        """开始备课；已经有活着的进程在备这个单元时返回 False。"""
    def update(self, unit: str, progress: dict) -> None: ...
    def end(self, unit: str, error: str = "") -> None: ...
    def get(self, unit: str) -> dict | None:
        """{"state": running | done | error, "alive": 进程还在且在跑, "progress": {...}, "error", "started", "updated"}；没备过是 None。"""


class ActivityFeed(Protocol):
    """流程看板（D-024）：after 游标之后新写入的证据 / 运行 / 评分 / 发布，按时间排序。

    after 为空时给最近 limit 条（看板打开时的历史）。返回 ([(种类, 对象)], 新游标)。
    """

    def activity(self, after: dict | None, limit: int) -> tuple[list[tuple[str, object]], dict]: ...
