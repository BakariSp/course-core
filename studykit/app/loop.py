"""产出循环（D-035）：生成 → 检验（便宜的先跑）→ 定点修复 → 通过闸门，或者超出预算时升级给学习者。

一台机器，多种产出物：课程计划、单元题、简答题批改都注册成一个 Kind。机器不认识任何一种产出物的内容，
只认识 Finding（带地址的发现）和 Kind 切出来的"部分"。

INVARIANT: 修复只能改被指出的部分。修复之后，没被指出的部分变了，就由"冻结"检验器给出阻断级发现——
修一处、坏一处的问题（今天 Git 课程四轮都有）在机器层面被挡住，而不是靠 prompt 里的规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from studykit.domain.artifact import Finding, blocking, unexpected_changes


@dataclass
class Attempt:
    """生成者的一次产出。run 是这次运行的 id（每一轮都能回放），cost 是美元；
    costs 是 cost 按步骤拆开的明细（如 {"大纲": 0.02, "各节": 0.1}），不给就整笔记成"生成"。"""
    artifact: dict
    run: str
    cost: float = 0.0
    costs: dict[str, float] = field(default_factory=dict)


@dataclass
class Evaluator:
    """检验器：一次产出 → 发现。按成本从低到高排在 Kind 里；某一个给出阻断级发现，后面更贵的就不跑了。
    拿到的是整个 Attempt（不只是产出物）：检验结果要记成这一轮那次运行的评分。"""
    name: str
    check: Callable[[Attempt], list[Finding]]


@dataclass
class Kind:
    """一种产出物：生成者、修复者、检验器链、以及把产出物切成可比较的"部分"（地址 → 内容）。"""
    name: str
    generate: Callable[[dict], Attempt]
    repair: Callable[[Attempt, list[Finding], dict], Attempt]   # (当前这一轮, 阻断级发现, 输入) → 新的产出
    evaluators: list[Evaluator]
    parts: Callable[[dict], dict[str, object]]
    review_cost: Callable[[Attempt], float] = lambda a: 0.0   # 检验器调模型花的钱（评审模型），这一轮检验完后查


@dataclass(frozen=True)
class Budget:
    rounds: int = 3            # D-034：学习者选定 3 轮 / $1
    usd: float = 1.0


@dataclass
class Round:
    run: str
    findings: list[Finding]
    cost: float                                              # 这一轮一共花的：生成或修复 + 检验
    costs: dict[str, float] = field(default_factory=dict)    # 按步骤的明细，加起来等于 cost


@dataclass
class LoopResult:
    status: str                # accepted：可以进发布闸门；escalated：停下来，需要学习者决定
    artifact: dict
    rounds: list[Round] = field(default_factory=list)

    @property
    def spent(self) -> float:
        return round(sum(r.cost for r in self.rounds), 4)

    @property
    def blocking(self) -> list[Finding]:
        return blocking(self.rounds[-1].findings) if self.rounds else []


def evaluate(kind: Kind, attempt: Attempt, frozen: list[Finding] = ()) -> list[Finding]:
    """跑检验器链，便宜的先跑；遇到阻断就停。冻结检验器的发现排在最前面。"""
    found = list(frozen)
    if blocking(found):
        return found
    for ev in kind.evaluators:
        fs = ev.check(attempt)
        found += fs
        if blocking(fs):
            break
    return found


def run_loop(kind: Kind, inp: dict, budget: Budget = Budget()) -> LoopResult:
    attempt = kind.generate(inp)
    result = LoopResult("accepted", attempt.artifact)
    frozen: list[Finding] = []
    while True:
        findings = evaluate(kind, attempt, frozen)
        review = kind.review_cost(attempt)
        costs = {k: round(v, 6) for k, v in {**(attempt.costs or {"生成": attempt.cost}), "评审": review}.items()}
        result.rounds.append(Round(attempt.run, findings, attempt.cost + review, costs))
        result.artifact = attempt.artifact
        todo = blocking(findings)
        if not todo:
            return result
        if len(result.rounds) >= budget.rounds or result.spent >= budget.usd:
            result.status = "escalated"
            return result
        before = kind.parts(attempt.artifact)
        attempt = kind.repair(attempt, todo, inp)
        frozen = [Finding(a, f"没被指出的部分被改了：{a}。修复只能改发现指向的地方，这一处要恢复原样", "freeze")
                  for a in unexpected_changes(before, kind.parts(attempt.artifact), [f.address for f in todo])]
