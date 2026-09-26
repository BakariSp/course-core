"""备课（PRD_V2 阶段 A）：课程计划注册成产出循环（D-035）里的一种产出物，从生成走到发布，全程不需要导师。

    生成        tutor-prep 跑一次（查资料、写计划、submit_plan）
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

from studykit.app.harness import Harness
from studykit.app.learning import Course
from studykit.app.loop import Attempt, Budget, Evaluator, Kind, LoopResult, run_loop
from studykit.app.ports import PlanStore
from studykit.domain import harness as h
from studykit.domain.artifact import Finding
from studykit.domain.errors import DomainError
from studykit.domain.plan import plan_parts

AGENT = "tutor-prep"


def _findings(g: h.Grade | None, evaluators: tuple[str, ...] | None = None) -> list[Finding]:
    fs = [Finding.from_dict(d) for d in (g.detail.get("findings") or [] if g else [])]
    return [f for f in fs if evaluators is None or f.evaluator in evaluators]


class CoursePrep:
    def __init__(self, harness: Harness, course: Course, plans: PlanStore, budget: Budget = Budget()):
        self.harness, self.course, self.plans, self.budget = harness, course, plans, budget
        self.runs = harness.runs

    # ---------- 产出物种类 ----------

    def kind(self, model: str | None = None) -> Kind:
        hs = self.harness

        def attempt(run: h.Run) -> Attempt:
            out = hs.dir(run.agent, run.id) / "output.json"
            return Attempt(json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}, run.id, run.cost_usd)

        def generate(inp: dict) -> Attempt:
            if inp.get("from"):                                # 从已有的一次运行接着修（不花生成的钱）
                return attempt(hs.resolve(AGENT, inp["from"]))
            return attempt(hs.run(AGENT, inp["unit"], model=model))

        def repair(prev: Attempt, findings: list[Finding], inp: dict) -> Attempt:
            return attempt(hs.repair(self._run(prev), findings, model=model))

        def check(a: Attempt) -> list[Finding]:
            run = self._run(a)
            if not a.artifact:
                return [Finding("", "没有提交课程计划（运行出错或超时）", "check")]
            # WHY: 每一轮都重新检查，不用旧的评分：旧流程的评分里没有带地址的发现，拿来用会把"没过"当成"没有问题"
            return _findings(hs._check(run, hs.agent(AGENT), hs.dir(AGENT, run.id)))

        def safety(a: Attempt) -> list[Finding]:
            return _findings(hs.reviewer(self._run(a)), ("reviewer_safety",))

        def lab(a: Attempt) -> list[Finding]:
            if not a.artifact.get("lab"):
                return []
            return [Finding.from_dict(f) for f in hs.verify_lab(self._run(a))["findings"]]

        def quality(a: Attempt) -> list[Finding]:
            return _findings(hs.reviewer(self._run(a)), ("reviewer",))

        return Kind("course_plan", generate, repair,
                    [Evaluator("check", check), Evaluator("safety", safety), Evaluator("lab_verify", lab),
                     Evaluator("quality", quality)],
                    plan_parts)

    # ---------- 用例 ----------

    def prepare(self, unit: str, start_from: str | None = None, model: str | None = None) -> dict:
        """备课：生成（或从 start_from 那次运行接着）→ 检验 → 定点修复 → 通过就发布。返回这次循环的摘要。"""
        self.harness.build_input(unit)                          # 单元不存在就在这里报错
        if start_from and self.harness.resolve(AGENT, start_from).unit != unit:
            raise DomainError(f"{start_from} 不是 {unit} 的运行")
        result = run_loop(self.kind(model), {"unit": unit, "from": start_from}, self.budget)
        final = self.runs.run(result.rounds[-1].run)
        verdict = result.status
        self.harness._grade(final, "loop", "d-035", "system", verdict=verdict, detail=self._summary(result))
        published = False
        if verdict == "accepted" and self.can_autopublish(unit):
            self.harness.publish(final)
            published = True
        return {"unit": unit, "status": verdict, "run": final.id, "published": published, **self._summary(result)}

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
        return {"spent_usd": result.spent,
                "rounds": [{"run": r.run, "cost_usd": round(r.cost, 4),
                            "findings": [f.as_dict() for f in r.findings]} for r in result.rounds],
                "blocking": [f.as_dict() for f in result.blocking]}
