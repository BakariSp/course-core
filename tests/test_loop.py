"""产出循环（D-035 第 2 步）：生成 → 检验（便宜的先跑）→ 定点修复 → 通过闸门或升级。生成者和检验器都是假的。"""
import pytest

from studykit.app.loop import Attempt, Budget, Evaluator, Kind, run_loop
from studykit.domain.artifact import Finding, unexpected_changes


def plan(*sections):
    return {"title": "t", "sections": [dict(s) for s in sections]}


def parts(p):
    return {"/title": p["title"], **{f"/sections/{i}": s for i, s in enumerate(p["sections"])}}


def bad_sections(attempt):
    p = attempt.artifact
    return [Finding(f"/sections/{i}", f"第 {i + 1} 节还是坏的", "static") for i, s in enumerate(p["sections"]) if s.get("bad")]


class Gen:
    """第一次生成给出 initial；每次修复按 fix(artifact, findings) 改。记下被调用的情况。"""

    def __init__(self, initial, fix, cost=0.2):
        self.initial, self.fix, self.cost, self.repairs = initial, fix, cost, []

    def generate(self, inp):
        return Attempt(self.initial, "run-1", self.cost)

    def repair(self, attempt, findings, inp):
        self.repairs.append([f.address for f in findings])
        return Attempt(self.fix(attempt.artifact, findings), f"run-{len(self.repairs) + 1}", self.cost)


def fix_addressed(artifact, findings):
    out = plan(*artifact["sections"])
    for f in findings:
        out["sections"][int(f.address.split("/")[2])] = {"name": "修好了"}
    return out


def kind(gen, *evaluators):
    return Kind("plan", gen.generate, gen.repair, list(evaluators) or [Evaluator("static", bad_sections)], parts)


def test_accepted_on_the_first_round_when_nothing_blocks():
    r = run_loop(kind(Gen(plan({"name": "a"}), fix_addressed)), {})
    assert (r.status, len(r.rounds), r.spent) == ("accepted", 1, 0.2)


def test_repair_only_rewrites_what_the_findings_point_at():
    gen = Gen(plan({"name": "a"}, {"name": "b", "bad": True}), fix_addressed)
    r = run_loop(kind(gen), {})
    assert r.status == "accepted" and len(r.rounds) == 2
    assert gen.repairs == [["/sections/1"]]                               # 只把被指出的部分交给修复
    assert r.artifact["sections"] == [{"name": "a"}, {"name": "修好了"}]
    assert [x.run for x in r.rounds] == ["run-1", "run-2"]                # 每一轮是一次运行，可以回放


def test_a_repair_that_touches_other_parts_is_blocked():
    def sloppy(artifact, findings):                                      # 修第 2 节时顺手重写了第 1 节
        return plan({"name": "被改了"}, {"name": "修好了"})
    r = run_loop(kind(Gen(plan({"name": "a"}, {"name": "b", "bad": True}), sloppy)), {}, Budget(rounds=2))
    frozen = [f for f in r.rounds[1].findings if f.evaluator == "freeze"]
    assert [f.address for f in frozen] == ["/sections/0"] and r.status == "escalated"


def test_cheap_evaluators_run_first_and_a_block_stops_the_rest():
    calls = []

    def expensive(attempt):
        calls.append("lab")
        return []
    k = kind(Gen(plan({"bad": True}), fix_addressed), Evaluator("static", bad_sections), Evaluator("lab_verify", expensive))
    r = run_loop(k, {})
    assert calls == ["lab"] and len(r.rounds) == 2                         # 第 1 轮静态检查就阻断了，没跑实跑


def test_warnings_do_not_block():
    def warn(attempt):
        return [Finding("/sections/0", "可以更好", "reviewer", "warn")]
    r = run_loop(kind(Gen(plan({"name": "a"}), fix_addressed), Evaluator("reviewer", warn)), {})
    assert r.status == "accepted" and r.rounds[0].findings[0].severity == "warn"


@pytest.mark.parametrize("budget, rounds", [(Budget(rounds=3, usd=10), 3), (Budget(rounds=9, usd=0.5), 3)])
def test_stops_and_escalates_when_the_budget_runs_out(budget, rounds):
    stubborn = Gen(plan({"bad": True}), lambda a, f: plan({"bad": True}))      # 每次都修不好
    r = run_loop(kind(stubborn), {}, budget)
    assert r.status == "escalated" and len(r.rounds) == rounds
    assert r.blocking and r.blocking[0].address == "/sections/0"          # 升级时说清楚还卡在哪


def test_unexpected_changes_respects_address_nesting():
    before = {"/sections/0": 1, "/sections/1": 1, "/lab": 1}
    after = {"/sections/0": 2, "/sections/1": 2, "/lab": 1, "/sections/2": 1}
    # 一个检查点的发现允许改它所在的小节；新增的小节不在允许范围内
    assert unexpected_changes(before, after, ["/sections/1/checkpoint/0"]) == ["/sections/0", "/sections/2"]
    assert unexpected_changes(before, after, [""]) == []                  # 整份的发现：哪里都可以改
