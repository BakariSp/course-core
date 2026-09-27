"""组合根：全仓唯一同时知道"端口"和"实现"的地方。网页和命令行从这里拿到组装好的服务。

    数据目录 data/（不进 git）：
        study.db        证据、课程计划版本（SQLite，D-021）
        sandbox/        终端题、代码题的沙箱，随时可以重建
        labs/           练习场的快照和状态。**不可重建**：删了就回不到"第 N 节开始时"
        runs/           agent 每次运行的工作目录（简报、输入、产出；raw/ 下的原始日志可以清理）
        prep/           每个单元的备课现在走到哪、哪个进程在跑（D-040；随时可以删）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from studykit.adapters.files import FileContent
from studykit.adapters.jobs import ThreadJobs
from studykit.adapters.pi import PiRuntime
from studykit.adapters.prep_status import FilePrepStatus
from studykit.adapters.sqlite import SqliteStore
from studykit.adapters.system import SystemClock, UuidIds
from studykit.adapters.web_fetch import UrllibFetcher
from studykit.app.grading import LlmShortGrader
from studykit.app.harness import Harness
from studykit.app.learning import Assessment, Course, Deps, LearnerModel
from studykit.app.observe import Observer
from studykit.app.panel import Panel
from studykit.app.prep import CoursePrep
from studykit.app.ports import AgentRuntime, Clock, Fetcher, IdGen, JobRunner, PracticeEnv, Spec
from studykit.domain.assessment import CORE_CHECKERS, CORE_CHECKPOINTS
from studykit.domain.evidence import VerbRegistry
from studykit.specs import cs_practice

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    root: Path = ROOT
    data: Path = field(default=None)          # 默认 <root>/data
    learner: str = "me"

    def __post_init__(self):
        if self.data is None:
            object.__setattr__(self, "data", self.root / "data")


@dataclass
class App:
    config: Config
    store: SqliteStore
    content: FileContent
    assessment: Assessment
    course: Course
    learner: LearnerModel
    harness: Harness
    observer: Observer
    practice: PracticeEnv | None
    prep: CoursePrep
    panel: Panel

    def close(self) -> None:
        if self.practice:
            self.practice.stop_all()


def specs(content: FileContent, data: Path) -> list[Spec]:
    """启用的学科 spec。加一个学科 = 在这里多写一行。"""
    s = content.settings()
    lab_root = Path(os.path.expanduser(str(s.get("lab_root") or "~/cs-study-lab")))
    return [cs_practice.spec(lab_root, data / "labs", s.get("bash_path"))]


def build(config: Config | None = None, *, clock: Clock | None = None, ids: IdGen | None = None,
          runtime: AgentRuntime | None = None, fetcher: Fetcher | None = None, jobs: JobRunner | None = None) -> App:
    config = config or Config()
    content = FileContent(config.root)
    store = SqliteStore(config.data / "study.db")
    enabled = specs(content, config.data)
    verbs = VerbRegistry()
    for s in enabled:
        for v in s.verbs:
            verbs.register(v)
    checkers = {c.kind: c for c in (*CORE_CHECKERS, *(c for s in enabled for c in s.checkers))}
    checkpoints = {**CORE_CHECKPOINTS, **{k: g for s in enabled for k, g in s.checkpoints.items()}}
    practice = next((s.practice for s in enabled if s.practice), None)
    clock, ids = clock or SystemClock(), ids or UuidIds()
    deps = Deps(store, store, content, clock, ids, verbs, config.learner)
    learner = LearnerModel(deps)
    course = Course(deps, learner, checkpoints, practice)
    runtime = runtime or PiRuntime(config.root / "agents" / "_pi" / "env_bridge.ts", config.root / "agents" / "_pi" / "home",
                                   config.root / "local.env", root=config.root)
    harness = Harness(runs=store, runtime=runtime, fetcher=fetcher or UrllibFetcher(), content=content, course=course,
                      learner=learner, specs=enabled, clock=clock, ids=ids, root=config.root,
                      workspace=config.data / "runs", learner_id=config.learner)
    grader = LlmShortGrader(runtime, config.root, config.data / "runs", clock, harness.versions)
    prep = CoursePrep(harness, course, store, FilePrepStatus(config.data / "prep"))
    panel = Panel(content=content, harness=harness, prep=prep, course=course, runs=store, plans=store, evidence=store,
                  jobs=jobs or ThreadJobs(), learner_id=config.learner)
    return App(config, store, content, Assessment(deps, checkers, config.data / "sandbox", config.root, grader),
               course, learner, harness, Observer(store, verbs, config.learner), practice, prep, panel)
