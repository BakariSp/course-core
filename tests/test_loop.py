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


def test_round_cost_includes_the_reviewers_model_calls_and_is_itemized():
    gen = Gen(plan({"name": "a", "bad": True}), fix_addressed)
    k = kind(gen)
    k.review_cost = lambda a: 0.05
    r = run_loop(k, {})
    assert [x.costs for x in r.rounds] == [{"生成": 0.2, "评审": 0.05}, {"生成": 0.2, "评审": 0.05}]
    assert r.spent == 0.5


def test_review_cost_counts_against_the_budget():
    gen = Gen(plan({"name": "a", "bad": True}), lambda a, f: a, cost=0.1)   # 永远修不好
    k = kind(gen)
    k.review_cost = lambda a: 0.5
    r = run_loop(k, {}, Budget(rounds=5, usd=1.0))
    assert r.status == "escalated" and len(r.rounds) == 2                    # 只算生成的话会跑满 5 轮


def moving_problem(fixed_after):
    """第 n 轮报第 n 节有问题（每一轮都是新问题），第 fixed_after 轮之后没问题了。"""
    def check(attempt):
        n = int(attempt.run.split("-")[1])
        return [] if n > fixed_after else [Finding(f"/sections/{n - 1}", "新问题", "static")]
    return check


def test_past_the_budget_extend_decides_whether_to_keep_going():
    """D-063：轮数用完时问 extend；返回空串就接着修，返回原因就停下来，原因记在结果里。"""
    same = Gen(plan(*[{"name": str(i)} for i in range(6)]), lambda a, f: a)
    r = run_loop(kind(same, Evaluator("static", moving_problem(4))), {}, Budget(rounds=3), extend=lambda r: "")
    assert (r.status, len(r.rounds), r.stopped) == ("accepted", 5, "")
    same = Gen(plan(*[{"name": str(i)} for i in range(6)]), lambda a, f: a)
    asked = []
    r = run_loop(kind(same, Evaluator("static", moving_problem(4))), {}, Budget(rounds=3),
                 extend=lambda r: asked.append(len(r.rounds)) or "stuck")
    assert (r.status, len(r.rounds), r.stopped, asked) == ("escalated", 3, "stuck", [3])
    r = run_loop(kind(Gen(plan({"bad": True}), lambda a, f: a)), {}, Budget(rounds=2))
    assert r.stopped == "budget"                                             # 不给 extend：和原来一样，用完就停


def test_keep_going_stops_when_a_repair_leaves_the_same_problem_or_the_cap_is_reached():
    """D-063：同一处、同一个检验器的阻断修了一轮还在 = 修不动，交给开发者；花到上限也停；换了新问题就接着修。"""
    from studykit.domain.artifact import keep_going
    a, b = Finding("/sections/1", "x", "reviewer"), Finding("/sections/2", "y", "reviewer")
    assert keep_going([[a], [b]], spent=1.0, cap=3.0) == ""
    assert keep_going([[a], [Finding("/sections/1", "换了说法", "reviewer")]], spent=1.0, cap=3.0) == "stuck"
    assert keep_going([[a], [Finding("/sections/1", "x", "lab_verify")]], spent=1.0, cap=3.0) == ""   # 不同检验器是不同问题
    assert keep_going([[a], [b]], spent=3.0, cap=3.0) == "cap"
    assert keep_going([[a]], spent=0.5, cap=3.0) == ""
