"""备课 = 课程计划的产出循环（PRD_V2 阶段 A，D-035 第 3 步）：生成 → 检验 → 定点修复 → 自动发布，不经过导师。

agent loop 和评审模型是假的（FakeRuntime）；lab verify 在真实 bash 里跑。
"""
import json

import pytest

from studykit.domain.artifact import Finding, repair_scope, repair_unit
from studykit.domain.errors import CourseError
from studykit.domain.harness import Grade, Run, parse_review, prep_stage
from studykit.domain.plan import plan_addresses, plan_parts, replace_part
from tests.conftest import LAB
from tests.test_cs_practice import needs_bash
from tests.test_harness import OTHER, SRC, checkpoint, plan_of, section

UNIT = "tools-01-shell"


def lab_cp(i, value="2", solution=None):
    return checkpoint(type="lab", prompt=f"把 ERROR 的行数写进 r{i}.txt", concept="tools.shell.cwd",
                      checks=[{"file": f"r{i}.txt", "equals": value, "desc": f"r{i}.txt 里是 ERROR 的行数"}],
                      solution=solution or [f"echo 2 > r{i}.txt"])


def good_plan():
    s0 = section("认识练习场", explain="打开 Git Bash，确认终端能用。" * 10)
    terms = [{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"},
             {"id": "tools.cmd.echo", "term": "echo", "explain": "打印参数"}]
    return plan_of(s0, *[section(f"s{i}", terms=terms, checkpoint=[lab_cp(i)]) for i in (1, 2, 3)], lab=LAB,
                   nodes=[{"id": "tools.cmd.pwd", "title": "pwd", "desc": "d", "kind": "term"},
                          {"id": "tools.cmd.echo", "title": "echo", "desc": "d", "kind": "term"},
                          {"id": "tools.shell.cwd", "title": "当前目录", "desc": "d", "kind": "concept"}])


def generate(plan):
    return [("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}), ("submit_plan", {"plan": plan})]


def repair_with(parts):
    return [("submit_repair", {"parts": parts})]


@pytest.fixture
def pages(fetcher):
    fetcher.pages = {SRC: "shell 讲义正文 " * 10, OTHER: "第二页 " * 10}


# ---------- 领域：地址、修复范围、评审模型的回答 ----------

def test_repair_units_and_scope():
    assert repair_unit("/sections/3/checkpoint/1") == "/sections/3" and repair_unit("/lab/files/0") == "/lab"
    assert repair_unit("") == "" and repair_unit("/sources") == "/sources"
    f = lambda a: Finding(a, "x", "e")  # noqa: E731
    assert repair_scope([f("/sections/3/checkpoint/1"), f("/sections/3"), f("/lab")]) == ["/lab", "/sections/3"]
    assert repair_scope([f("/sections/1"), f("")]) == [""]                    # 有整份的发现，就是整份


def test_plan_parts_and_replace_part_follow_global_section_order():
    p = good_plan()
    p["parts"] = [{"title": "A", "sections": p["parts"][0]["sections"][:2]}, {"title": "B", "sections": p["parts"][0]["sections"][2:]}]
    parts = plan_parts(p)
    assert parts["/sections/2"]["title"] == "s2" and parts["/parts"] == [{"title": "A", "sections": 2}, {"title": "B", "sections": 2}]
    q = replace_part(p, "/sections/2", {"title": "新的"})
    assert q["parts"][1]["sections"][0] == {"title": "新的"} and p["parts"][1]["sections"][0]["title"] == "s2"   # 不改原计划
    assert replace_part(p, "/sources", [])["sources"] == []
    for bad in ("/sections/9", "/parts", "/sections/1/checkpoint/0"):
        with pytest.raises(CourseError):
            replace_part(p, bad, {})
    assert {"", "/sections/3/checkpoint/0", "/lab", "/sources"} <= plan_addresses(p)


def test_parse_review_blocks_unsafe_commands_and_falls_back_to_a_valid_address():
    addrs = plan_addresses(good_plan())
    fs = parse_review('好的：{"unsafe": [{"address": "/sections/1/checkpoint/0", "command": "rm -rf ~", "why": "删家目录"}],'
                      ' "findings": [{"address": "/sections/2/checkpoint/7", "severity": "block", "what": "题干和答案不一致", "rubric": "checkable"},'
                      ' {"address": "/nope", "severity": "meh", "what": "可以更好"}]}', addrs)
    assert [(f.address, f.evaluator, f.severity) for f in fs] == [
        ("/sections/1/checkpoint/0", "reviewer_safety", "block"),
        ("/sections/2", "reviewer", "block"),                          # 检查点 7 不存在 → 退到第 3 节
        ("", "reviewer", "warn")]                                      # 地址不合法 → 整份；严重程度不合法 → 警告
    assert fs[1].what == "[checkable] 题干和答案不一致"
    with pytest.raises(ValueError):
        parse_review("我觉得挺好", addrs)


def _run(rid):
    return Run(rid, "tutor-prep", "v", "u", "me", "batch", rid, {})


def _g(rid, grader, verdict="", **kw):
    return Grade(f"{rid}-{grader}", "t", rid, grader, "1", "x", verdict=verdict, **kw)


@pytest.mark.parametrize("grades, published, running, stage", [
    (None, None, False, "todo"),
    ([], None, True, "preparing"),
    ([_g("r1", "loop", "accepted")], None, False, "ready"),              # 通过了但没发布：学过的单元等学习者确认
    ([_g("r1", "loop", "escalated", detail={"blocking": [{"what": "第 2 节跑不通"}]})], None, False, "escalated"),
    ([_g("r1", "check", "fail")], None, False, "escalated"),            # 没走完循环（旧流程 / 中途出错）
    ([_g("r1", "loop", "accepted")], "r1", False, "published"),
    (None, "手工发布", False, "published"),
])
def test_prep_stage_never_waits_for_a_tutor(grades, published, running, stage):
    s = prep_stage([] if grades is None else [_run("r1")], {"r1": grades or []}, published, running)
    assert s["stage"] == stage and s["who"] in ("learner", "agent") and s["next"]
    if stage == "escalated" and grades and grades[0].grader == "loop":
        assert s["stuck"] == ["第 2 节跑不通"]


# ---------- 修复工具：结构上只能改被指出的部分 ----------

def _repair_ws(app, runtime, plan, findings):
    runtime.scripts = [generate(plan), []]
    first = app.harness.run("tutor-prep", UNIT)
    run = app.harness.repair(first, findings)
    return first, run, app.harness.dir("tutor-prep", run.id)


def test_submit_repair_only_accepts_the_addressed_parts(app, runtime, pages):
    plan = good_plan()
    first, run, ws = _repair_ws(app, runtime, plan, [Finding("/sections/2/checkpoint/0", "跑不通", "lab_verify")])
    assert run.id.endswith("-r1") and run.input["repair"] == {
        "of": first.id, "root": first.id, "round": 1, "allowed": ["/sections/2"],
        "findings": [Finding("/sections/2/checkpoint/0", "跑不通", "lab_verify").as_dict()]}
    assert runtime.runs[-1] == ["fetch_url", "submit_repair"]                   # 修复模式换了提交工具
    brief = (ws / "brief.md").read_text(encoding="utf-8")
    assert "`/sections/2`（第 3 节 s2）" in brief and "## /sections/2 · 第 3 节 s2" in brief and "跑不通" in brief
    call = lambda parts: app.harness.call_tool(ws, "submit_repair", {"parts": parts})  # noqa: E731
    from studykit.app.harness import ToolError
    with pytest.raises(ToolError, match="不在要重写的部分里"):
        call([{"address": "/sections/1", "value": plan["parts"][0]["sections"][1]}])
    with pytest.raises(ToolError, match="非空数组"):
        call([])
    fixed = {**plan["parts"][0]["sections"][2], "title": "s2 改过"}
    fixed_bad = {**fixed, "try": [{"command": "sudo ls", "expect": "x"}]}
    with pytest.raises(ToolError, match="sudo"):                                  # 修改过的部分也要过固定的安全规则
        call([{"address": "/sections/2", "value": fixed_bad}])
    assert "检查通过" in call([{"address": "/sections/2", "value": fixed}])
    out = json.loads((ws / "output.json").read_text(encoding="utf-8"))
    assert out["parts"][0]["sections"][2]["title"] == "s2 改过"
    assert {k: v for k, v in plan_parts(out).items() if k != "/sections/2"} == \
           {k: v for k, v in plan_parts(plan).items() if k != "/sections/2"}      # 其余部分逐字不变
    assert app.harness.limits(ws).grounded                                        # 上一轮打开过的页面仍然算出处


# ---------- 整个循环 ----------

@needs_bash
def test_prepare_repairs_only_the_broken_section_and_publishes(app, runtime, pages):
    broken = good_plan()
    broken["parts"][0]["sections"][2]["checkpoint"][0] = lab_cp(2, solution=["echo 3 > r2.txt"])   # 参考做法做完也不通过
    fixed = good_plan()["parts"][0]["sections"][2]
    runtime.scripts = [generate(broken), repair_with([{"address": "/sections/2", "value": fixed}])]
    r = app.prep.prepare(UNIT)
    assert r["status"] == "accepted" and r["published"] and len(r["rounds"]) == 2
    [block] = [f for f in r["rounds"][0]["findings"] if f["severity"] == "block"]
    assert (block["evaluator"], block["address"]) == ("lab_verify", "/sections/2/checkpoint/0")
    assert runtime.runs == [["fetch_url", "submit_plan"], ["fetch_url", "submit_repair"]]
    assert len(runtime.reviews) == 2 and "## /sections/2 · 第 3 节 s2" in runtime.reviews[0]   # 评审模型每轮看一次，按地址
    page = app.course.page(UNIT)
    assert page["plan"]["provenance"]["run"] == r["run"] and r["run"].endswith("-r1")
    stage = app.panel.unit(UNIT)["prep"]
    assert stage["stage"] == "published" and stage["who"] == "learner"


@needs_bash
def test_an_unsafe_verdict_stops_the_loop_before_anything_runs(app, runtime, pages, tmp_path):
    runtime.script = generate(good_plan())
    runtime.review_reply = json.dumps({"unsafe": [{"address": "/sections/1/checkpoint/0", "command": "echo 2 > r1.txt",
                                                   "why": "假装危险"}], "findings": []})
    r = app.prep.prepare(UNIT)
    assert r["status"] == "escalated" and not r["published"] and len(r["rounds"]) == 3
    assert {f["evaluator"] for rd in r["rounds"] for f in rd["findings"]} == {"reviewer_safety"}
    assert not app.store.grades(r["run"]) or all(g.grader != "practice_verify" for g in app.store.grades(r["run"]))  # 没被放行，就没在本机跑
    stage = app.panel.unit(UNIT)["prep"]
    assert stage["stage"] == "escalated" and "假装危险" in stage["stuck"][0]


def test_a_reviewer_failure_counts_as_not_passed(app, runtime, pages):
    runtime.script = generate(good_plan())
    runtime.review_reply = "（超时，没有输出）"
    r = app.prep.prepare(UNIT)
    assert r["status"] == "escalated" and "评审模型没有给出可用的结论" in r["blocking"][0]["what"]


@needs_bash
def test_a_unit_the_learner_already_started_waits_for_confirmation(app, runtime, pages, clock):
    runtime.script = generate(good_plan())
    first = app.prep.prepare(UNIT)
    assert first["published"]
    app.course.check(UNIT, 0, 0, ["B"])                                           # 学习者学完了第 1 节
    clock.tick(60)
    again = good_plan()
    again["nodes"] = []                                                          # 节点第一次发布时已经并入知识图
    runtime.script = generate(again)
    r = app.prep.prepare(UNIT)
    assert r["status"] == "accepted" and not r["published"]
    assert app.course.page(UNIT)["plan"]["provenance"]["run"] == first["run"]    # 课程页还是学习者在学的那一版
    assert app.panel.unit(UNIT)["prep"]["stage"] == "ready"
    app.panel.publish(UNIT)
    assert app.course.page(UNIT)["plan"]["provenance"]["run"] == r["run"]


def test_publish_needs_an_accepted_loop(app, runtime, pages):
    runtime.script = generate(good_plan())
    run = app.harness.run("tutor-prep", UNIT)
    from studykit.domain.errors import DomainError
    with pytest.raises(DomainError, match="没有通过产出循环"):
        app.harness.publish(run)
