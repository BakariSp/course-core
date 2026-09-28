"""备课（PRD_V2 阶段 A）：课程计划注册成产出循环（D-035）里的一种产出物，从生成走到发布，全程不需要导师。

    生成        分步：知识库（和学习者无关，已有就复用，D-040）→ 大纲（按学习者挑选、切节、定约定，D-038）
                → 各节同时写（每节提交时就检查）→ 拼成课程计划
    检验器链    便宜的先跑，前面阻断就不跑后面的：
                  1. check          确定性：schema、预算、学科规则（含命令安全的固定规则）、评测用例
                  2. safety         评审模型读一遍会被执行的命令（D-030）；它放行，才轮到 3
                  3. lab_verify     在临时目录里把练习场从头走一遍（会在本机执行命令）
                  4. quality        评审模型按评分标准给出带地址的发现（和 2 是同一次模型调用）
    修复        tutor-prep 的修复模式：只重写发现指向的小节 / 字段（submit_repair）
    闸门        没有阻断 → 第一次学的单元直接发布；已经开始学的单元，新版本等学习者确认（PRD_V2 §6）

INVARIANT: 每一轮是一次 Run，每个检验器的结论是这次 Run 的一条 Grade；循环的结论是最后一轮的 loop Grade。
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from studykit.app.harness import Harness
from studykit.app.learning import Course
from studykit.app.loop import Attempt, Budget, Evaluator, Kind, LoopResult, run_loop
from studykit.app.ports import PlanStore, PrepStatus
from studykit.domain import harness as h
from studykit.domain.artifact import Finding, blocking, keep_going, repair_scope
from studykit.domain.errors import DomainError
from studykit.domain.plan import plan_parts, stubs_of

AGENT = "tutor-prep"
CAP_USD = 3.0            # 一次备课（含自动接着修）最多花多少；progress/settings.yaml 的 prep_budget_usd 可以改（D-063）


class Progress:
    """一次备课走到哪了（D-040：学习者在课程页上看得见）。只在内存里，每次变化都交给 report；真正的结果照常写进 Run / Grade。
    各节并行写，所以要加锁。"""

    def __init__(self, report: Callable[[dict], None] | None = None):
        self._report, self._lock = report or (lambda p: None), threading.Lock()
        self.state: dict = {"step": "outline", "label": "查资料、写大纲", "round": 1, "sections": None}

    def set(self, **kw) -> None:
        with self._lock:
            self.state.update(kw)
            self._report(dict(self.state))

    def sections(self, total: int) -> None:
        self.set(step="sections", sections={"total": total, "done": 0}, label=f"各节同时在写：0/{total} 节写好")

    def section_done(self) -> None:
        with self._lock:
            s = self.state["sections"]
            s["done"] += 1
            self.state["label"] = f"各节同时在写：{s['done']}/{s['total']} 节写好"
            self._report(dict(self.state, sections=dict(s)))

    def checking(self, what: str) -> None:
        self.set(step="check", label=f"第 {self.state['round']} 轮检验：{what}")

    def repairing(self, addresses: list[str]) -> None:
        self.set(step="repair", round=self.state["round"] + 1,
                 label=f"只修被指出的部分：{'、'.join(a or '整份' for a in addresses)}")


def _findings(g: h.Grade | None, evaluators: tuple[str, ...] | None = None) -> list[Finding]:
    fs = [Finding.from_dict(d) for d in (g.detail.get("findings") or [] if g else [])]
    return [f for f in fs if evaluators is None or f.evaluator in evaluators]


class CoursePrep:
    def __init__(self, harness: Harness, course: Course, plans: PlanStore, status: PrepStatus, budget: Budget = Budget(),
                 workers: int = 8):
        self.harness, self.course, self.plans, self.budget, self.workers = harness, course, plans, budget, workers
        self.status = status
        self.runs = harness.runs

    # ---------- 产出物种类 ----------

    def kind(self, model: str | None = None, progress: Progress | None = None) -> Kind:
        hs, progress = self.harness, progress or Progress()

        def attempt(run: h.Run) -> Attempt:
            out = hs.dir(run.agent, run.id) / "output.json"
            return Attempt(json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}, run.id, run.cost_usd)

        def generate(inp: dict) -> Attempt:
            if inp.get("from"):                                # 从已有的一次运行接着修（不花生成的钱）
                return attempt(hs.resolve(AGENT, inp["from"]))
            return self.generate(inp["unit"], model, progress=progress)

        def repair(prev: Attempt, findings: list[Finding], inp: dict) -> Attempt:
            if not prev.artifact:                              # 上一次没拼出课程计划：没有可修的，重新分步生成
                progress.set(round=progress.state["round"] + 1)
                return self.generate(inp["unit"], model, progress=progress)
            progress.repairing(repair_scope(findings))
            a = attempt(hs.repair(self._run(prev), findings, model=model))
            a.costs = {"修复": a.cost}
            return a

        def check(a: Attempt) -> list[Finding]:
            run = self._run(a)
            if not a.artifact:
                return [Finding("", "没有提交课程计划（运行出错或超时）", "check")]
            progress.checking("自动检查")
            # WHY: 每一轮都重新检查，不用旧的评分：旧流程的评分里没有带地址的发现，拿来用会把"没过"当成"没有问题"
            return _findings(hs._check(run, hs.agent(AGENT), hs.dir(AGENT, run.id)))

        def safety(a: Attempt) -> list[Finding]:
            progress.checking("评审模型读命令、看质量")
            return _findings(hs.reviewer(self._run(a)), ("reviewer_safety",))

        def lab(a: Attempt) -> list[Finding]:
            if not a.artifact.get("lab"):
                return []
            progress.checking("在临时目录里把练习场从头跑一遍")
            found = [Finding.from_dict(f) for f in hs.verify_lab(self._run(a))["findings"]]
            if blocking(found):
                # WHY: 质量结论和安全结论是同一次评审调用给的，已经付过钱。练习场先阻断时一起交给这一轮修复，
                # 不然要等下一轮评审再碰运气（2026-09-27 test-01 第 1 轮就这样漏掉了一个真问题）
                found += _findings(hs.reviewer(self._run(a)), ("reviewer",))
            return found

        def quality(a: Attempt) -> list[Finding]:
            return _findings(hs.reviewer(self._run(a)), ("reviewer",))

        return Kind("course_plan", generate, repair,
                    [Evaluator("check", check), Evaluator("safety", safety), Evaluator("lab_verify", lab),
                     Evaluator("quality", quality)],
                    plan_parts, review_cost=lambda a: hs.model_cost(a.run))

    # ---------- 分步生成（D-038） ----------

    def generate(self, unit: str, model: str | None = None, tries: int = 2, progress: Progress | None = None) -> Attempt:
        """大纲 → 各节同时写 → 拼成课程计划。每一步没交出来就重跑一次；还不行就停，交给循环按"没有提交"处理。
        返回的 cost 是这几步加起来的花费（算进产出循环的预算）。
        WHY: 各节只依赖大纲，所以并行：一个单元的生成时间从"大纲 + 各节之和"降到"大纲 + 最慢的一节"。"""
        hs, progress = self.harness, progress or Progress()
        kb, spent, last = self.knowledge(unit, model, tries, progress)
        costs = {"调研": spent}                             # 知识库已有时是 0（复用，不花钱）
        if kb is None:                                      # 调研交不出知识库：交给循环按"没有提交"处理
            return Attempt({}, last.id, spent, costs)
        progress.set(step="outline", label="按你的情况从知识库编大纲", sections=None)

        def step(fn) -> tuple[h.Run, bool, float]:
            spent = 0.0
            for _ in range(tries):
                run = fn()
                spent += run.cost_usd
                if run.submitted:
                    return run, True, spent
            return run, False, spent

        outline, ok, cost = step(lambda: hs.outline(AGENT, unit, model=model, kb_run=kb))
        spent += cost
        costs["大纲"] = cost
        if not ok:
            return Attempt({}, outline.id, spent, costs)
        n = len(stubs_of(self._output(outline)))
        progress.sections(n)

        def write(i: int):
            r = step(lambda: hs.section(outline, i, model=model))
            if r[1]:
                progress.section_done()
            return r

        with ThreadPoolExecutor(max_workers=max(1, min(n, self.workers))) as pool:
            results = list(pool.map(write, range(n)))
        costs["各节"] = sum(c for _, _, c in results)
        spent += costs["各节"]
        failed = [run for run, ok, _ in results if not ok]
        if failed:
            return Attempt({}, failed[0].id, spent, costs)
        plan = hs.assemble(outline, [run for run, _, _ in results])        # 按节的顺序拼，和谁先写完无关
        return Attempt(self._output(plan), plan.id, spent, costs)

    def knowledge(self, unit: str, model: str | None = None, tries: int = 2,
                  progress: Progress | None = None, redo: bool = False) -> tuple[h.Run | None, float, h.Run | None]:
        """单元知识库（D-040 ③）：已经有就直接用（和学习者无关，不用重做）；没有就调研一次。
        redo：调研方法变了（如 D-043 开始读视频字幕）时重做，新的一份成为这个单元的知识库。
        返回（知识库运行或 None，花费，最后一次调研运行）。"""
        kb = None if redo else self.harness.knowledge(AGENT, unit)
        if kb is not None:
            return kb, 0.0, kb
        (progress or Progress()).set(step="research", label="调研：读讲义、整理知识点（只做一次，以后复用）", sections=None)
        spent, run = 0.0, None
        for _ in range(tries):
            run = self.harness.research(AGENT, unit, model=model)
            spent += run.cost_usd
            if run.submitted:
                return run, spent, run
        return None, spent, run

    def build_knowledge(self, units: list[str], model: str | None = None, redo: bool = False) -> dict[str, str]:
        """为一批单元并行备好知识库（curriculum 同意后就可以做，D-040）。返回 单元 → 知识库运行 id（失败是 ""）。
        redo：已有的也重做。"""
        todo = [u for u in units if redo or self.harness.knowledge(AGENT, u) is None]
        with ThreadPoolExecutor(max_workers=max(1, min(len(todo), self.workers))) as pool:
            done = list(pool.map(lambda u: (u, self.knowledge(u, model, redo=redo)[0]), todo)) if todo else []
        return {u: (r.id if r else "") for u, r in done}

    def _output(self, run: h.Run) -> dict:
        return json.loads((self.harness.dir(run.agent, run.id) / "output.json").read_text(encoding="utf-8"))

    # ---------- 用例 ----------

    def prepare(self, unit: str, start_from: str | None = None, model: str | None = None,
                report: Callable[[dict], None] | None = None) -> dict:
        """备课：生成（或从 start_from 那次运行接着）→ 检验 → 定点修复 → 通过就发布。返回这次循环的摘要。
        report：每走一步报告一次进度（课程页、命令行显示用）。"""
        self.harness.build_input(unit)                          # 单元不存在就在这里报错
        if start_from and self.harness.resolve(AGENT, start_from).unit != unit:
            raise DomainError(f"{start_from} 不是 {unit} 的运行")
        if not self.status.begin(unit):
            raise DomainError(f"{unit} 已经在备课了（另一个窗口或命令行），等它跑完")

        def both(p: dict) -> None:
            self.status.update(unit, p)
            if report:
                report(p)
        try:
            result = self._prepare(unit, start_from, model, Progress(both))
        except BaseException as e:
            self.status.end(unit, f"{type(e).__name__}: {e}")
            raise
        self.status.end(unit)
        return result

    def _prepare(self, unit: str, start_from: str | None, model: str | None, progress: Progress) -> dict:
        cap = self.cap()
        # D-063：3 轮 / $1 用完还有阻断，只要每一轮修出的是新问题就自动接着修；同一个问题修不动、或花到上限才停
        extend = lambda r: keep_going([x.findings for x in r.rounds], r.spent, cap)   # noqa: E731
        result = run_loop(self.kind(model, progress), {"unit": unit, "from": start_from}, self.budget, extend)
        final = self.runs.run(result.rounds[-1].run)
        verdict = result.status
        self.harness._grade(final, "loop", "d-035", "system", verdict=verdict, detail=self._summary(result))
        published = False
        if verdict == "accepted" and self.can_autopublish(unit):
            self.harness.publish(final)
            published = True
        elif verdict == "accepted":                 # 已经开始学：存成可选版本，学习者在课程页上看过再选（D-044）
            self.harness.offer(final)
        return {"unit": unit, "status": verdict, "run": final.id, "published": published, **self._summary(result)}

    def cap(self) -> float:
        return float(self.harness.content.settings().get("prep_budget_usd") or CAP_USD)

    def can_autopublish(self, unit: str) -> bool:
        """第一次学（还没有发布过，或者一节都没学过）→ 直接发布；学过的单元换版本要学习者确认。"""
        if self.plans.current(unit) is None:
            return True
        return not self.course.page(unit)["progress"]["passed"]

    def publish(self, unit: str) -> list[str]:
        """学习者确认：把这个单元最近一次通过循环的运行发布出去（"新版本等你确认"那一步）。"""
        runs = [r for r in self.runs.runs(AGENT) if r.unit == unit]
        for r in reversed(runs):
            loop = self.harness._latest(r, "loop")
            if loop is not None:
                if loop.verdict != "accepted":
                    raise DomainError(f"{unit} 最近一次备课没有通过检验，不能发布")
                return self.harness.publish(r)
        raise DomainError(f"{unit} 还没有通过检验的版本")

    # ---------- 内部 ----------

    def _run(self, a: Attempt) -> h.Run:
        run = self.runs.run(a.run)
        if run is None:
            raise DomainError(f"找不到运行 {a.run}")
        return run

    @staticmethod
    def _summary(result: LoopResult) -> dict:
        return {"spent_usd": result.spent, "stopped": result.stopped,
                "rounds": [{"run": r.run, "cost_usd": round(r.cost, 4), "costs": r.costs,
                            "findings": [f.as_dict() for f in r.findings]} for r in result.rounds],
                "blocking": [f.as_dict() for f in result.blocking]}
