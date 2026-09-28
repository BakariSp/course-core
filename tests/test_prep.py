"""备课 = 课程计划的产出循环（PRD_V2 阶段 A，D-035 第 3 步）：生成 → 检验 → 定点修复 → 自动发布，不经过导师。
生成是分步的（阶段 B，D-038）：大纲 → 一节一节写 → 拼成课程计划。

agent loop 和评审模型是假的（FakeRuntime）；lab verify 在真实 bash 里跑。
"""
import copy
import dataclasses
import json
import time

import pytest

from studykit.domain.artifact import Finding, repair_scope, repair_unit
from studykit.domain.errors import CourseError
from studykit.domain.harness import Grade, Run, parse_review, prep_stage
from studykit.domain.plan import assemble, fill_stub, plan_addresses, plan_parts, replace_part, sections_of
from studykit.domain.plan_check import PlanLimits, outline_findings, section_findings
from tests.conftest import LAB
from tests.test_cs_practice import needs_bash
from tests.test_harness import OTHER, SRC, checkpoint, plan_of, section

UNIT = "tools-01-shell"


def lab_cp(i, value="2", solution=None):
    return checkpoint(type="lab", prompt=f"把 ERROR 的行数写进 r{i}.txt", concept="tools.shell.cwd",
                      checks=[{"file": f"r{i}.txt", "equals": value, "desc": f"r{i}.txt 里是 ERROR 的行数"}],
                      solution=solution or [f"echo 2 > r{i}.txt"])


def good_plan():
    s0 = section("认识练习场", explain="打开 Git Bash，确认终端能用。" * 10, mission="认领练习场")
    terms = [{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"},
             {"id": "tools.cmd.echo", "term": "echo", "explain": "打印参数"}]
    return plan_of(s0, *[section(f"s{i}", terms=terms, checkpoint=[lab_cp(i)], mission=f"任务 {i}") for i in (1, 2, 3)], lab=LAB,
                   nodes=[{"id": "tools.cmd.pwd", "title": "pwd", "desc": "d", "kind": "term"},
                          {"id": "tools.cmd.echo", "title": "echo", "desc": "d", "kind": "term"},
                          {"id": "tools.shell.cwd", "title": "当前目录", "desc": "d", "kind": "concept"}])


def generate(plan):
    return [("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}), ("submit_plan", {"plan": plan})]


def repair_with(parts):
    return [("submit_repair", {"parts": parts})]


def stub_of(sec):
    cp = sec["checkpoint"][0]
    stub = {"title": sec["title"], "minutes": sec["minutes"], "goal": sec["goal"], "mission": sec.get("mission", "m"),
            "terms": sec.get("terms") or [], "teaches": [f"{sec['title']} 的要点"],
            "check": {"type": cp["type"], "what": "做到 X", "why": f"这一节练的能力用 {cp['type']} 最能检验"}, "reading": [SRC]}
    if cp["type"] == "lab":                    # 约定：做完这一节，练习场满足它的检查点
        stub["state_after"] = [{k: v for k, v in c.items() if k != "trap"} for c in cp["checks"]]
    return stub


def outline_of(plan):
    o = copy.deepcopy(plan)
    for p in o["parts"]:
        p["sections"] = [stub_of(sec) for sec in p["sections"]]
    return o


def kb_of(plan):
    """知识库：和学习者无关的知识点（D-040 ③）。"""
    return {"summary": "讲 shell。", "sources": plan["sources"],
            "points": [{"id": n["id"], "term": n["title"], "kind": n["kind"], "explain": n["desc"],
                        "teaches": [f"{n['title']} 的要点"], "reading": [SRC]} for n in plan["nodes"]],
            "exercises": [{"title": "数 ERROR", "what": "数日志里的 ERROR 行"}]}


def staged(runtime, plan, research=True):
    """分步备课的脚本：调研（知识库）→ 大纲 → 每节一次（各节并行，按节序号取脚本）。"""
    if research:
        runtime.scripts.append([("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}),
                                ("submit_research", {"knowledge": kb_of(plan)})])
    runtime.scripts.append([("submit_outline", {"outline": outline_of(plan)})])     # 读过的页面从知识库继承，不用再读
    runtime.sections.update({i: [("submit_section", {"section": sec})] for i, sec in enumerate(sections_of(plan))})


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
    ([_g("r1", "check", "fail")], None, False, "interrupted"),          # 没走完循环（旧流程 / 中途出错），不是"检验没通过"
    ([_g("r1", "loop", "accepted")], "r1", False, "published"),
    (None, "手工发布", False, "published"),
])
def test_prep_stage_never_waits_for_a_tutor(grades, published, running, stage):
    s = prep_stage([] if grades is None else [_run("r1")], {"r1": grades or []}, published, running)
    assert s["stage"] == stage and s["who"] in ("learner", "agent", "developer") and s["next"]
    if stage == "escalated" and grades and grades[0].grader == "loop":
        assert s["stuck"] == ["第 2 节跑不通"] and s["stopped"] == "budget"      # 旧结论没记原因：当时是用完即停


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
    staged(runtime, broken)
    runtime.scripts.append(repair_with([{"address": "/sections/2", "value": fixed}]))
    r = app.prep.prepare(UNIT)
    assert r["status"] == "accepted" and r["published"] and len(r["rounds"]) == 2
    [block] = [f for f in r["rounds"][0]["findings"] if f["severity"] == "block"]
    assert (block["evaluator"], block["address"]) == ("lab_verify", "/sections/2/checkpoint/0")
    assert runtime.runs == [["fetch_url", "submit_research"], ["fetch_url", "submit_outline"]] + \
           [["fetch_url", "submit_section"]] * 4 + [["fetch_url", "submit_repair"]]   # 知识库 → 大纲 → 4 节 → 只修第 3 节
    assert r["rounds"][0]["run"].endswith("-a")                         # 第 1 轮检验的是拼好的课程计划
    assert len(runtime.reviews) == 2 and "## /sections/2 · 第 3 节 s2" in runtime.reviews[0]   # 评审模型每轮看一次，按地址
    # 每一轮按步骤记账，评审模型的钱也算进去（假 runtime：每次运行 $0.01，每次评审 $0.002）
    assert r["rounds"][0]["costs"] == {"调研": 0.01, "大纲": 0.01, "各节": 0.04, "评审": 0.002}
    assert r["rounds"][1]["costs"] == {"修复": 0.01, "评审": 0.002}
    assert r["spent_usd"] == round(0.062 + 0.012, 4)
    page = app.course.page(UNIT)
    assert page["plan"]["provenance"]["run"] == r["run"] and r["run"].endswith("-r1")
    stage = app.panel.unit(UNIT)["prep"]
    assert stage["stage"] == "published" and stage["who"] == "learner"


@needs_bash
def test_an_unsafe_verdict_stops_the_loop_before_anything_runs(app, runtime, pages, tmp_path):
    staged(runtime, good_plan())
    runtime.script = repair_with([{"address": "/sections/1", "value": sections_of(good_plan())[1]}])   # 修了等于没修
    runtime.review_reply = json.dumps({"unsafe": [{"address": "/sections/1/checkpoint/0", "command": "echo 2 > r1.txt",
                                                   "why": "假装危险"}], "findings": []})
    r = app.prep.prepare(UNIT)
    assert r["status"] == "escalated" and not r["published"] and len(r["rounds"]) == 3
    assert {f["evaluator"] for rd in r["rounds"] for f in rd["findings"]} == {"reviewer_safety"}
    assert not app.store.grades(r["run"]) or all(g.grader != "practice_verify" for g in app.store.grades(r["run"]))  # 没被放行，就没在本机跑
    stage = app.panel.unit(UNIT)["prep"]
    assert stage["stage"] == "escalated" and "假装危险" in stage["stuck"][0]
    # D-063：同一处的阻断修了还在 = 修不动，不自动接着修，交给开发者
    assert r["stopped"] == "stuck" and stage["stopped"] == "stuck" and stage["who"] == "developer"


def test_a_reviewer_failure_counts_as_not_passed(app, runtime, pages):
    staged(runtime, good_plan())
    runtime.script = repair_with([{"address": "", "value": good_plan()}])
    runtime.review_reply = "（超时，没有输出）"
    r = app.prep.prepare(UNIT)
    assert r["status"] == "escalated" and "评审模型没有给出可用的结论" in r["blocking"][0]["what"]
    assert r["stopped"] == "stuck" and len(r["rounds"]) == 3


def test_the_spending_cap_comes_from_settings(app, root):
    """D-063：一次备课（含自动接着修）最多花多少，学习者在 progress/settings.yaml 里定，默认 $3。"""
    assert app.prep.cap() == 3.0
    (root / "progress" / "settings.yaml").write_text("schema_version: 1\nprep_budget_usd: 1.5\n", encoding="utf-8")
    assert app.prep.cap() == 1.5


@needs_bash
def test_a_unit_the_learner_already_started_waits_for_confirmation(app, runtime, pages, clock):
    staged(runtime, good_plan())
    first = app.prep.prepare(UNIT)
    assert first["published"]
    app.course.check(UNIT, 0, 0, ["B"])                                           # 学习者学完了第 1 节
    clock.tick(60)
    again = good_plan()
    again["nodes"] = []                                                          # 节点第一次发布时已经并入知识图
    staged(runtime, again)
    r = app.prep.prepare(UNIT)
    assert r["status"] == "accepted" and not r["published"]
    assert app.course.page(UNIT)["plan"]["provenance"]["run"] == first["run"]    # 课程页还是学习者在学的那一版
    assert app.panel.unit(UNIT)["prep"]["stage"] == "ready"
    app.panel.publish(UNIT)
    assert app.course.page(UNIT)["plan"]["provenance"]["run"] == r["run"]


@needs_bash
def test_a_new_version_can_be_previewed_and_switched_back_and_forth(app, runtime, pages, clock):
    """D-034、D-044：新版本通过检验后存成可选版本；先预览再换，换过去、换回来，两版的进度都在。"""
    staged(runtime, good_plan())
    old = app.prep.prepare(UNIT)["run"]
    app.course.check(UNIT, 0, 0, ["B"])                                           # 在旧版学完第 1 节
    clock.tick(60)
    again = good_plan()
    again["nodes"] = []
    staged(runtime, again)
    new = app.prep.prepare(UNIT)["run"]
    vs = {v["plan_id"]: v for v in app.course.page(UNIT)["versions"]}
    assert vs[old]["current"] and vs[old]["passed"] == 1
    assert not vs[new]["current"] and not vs[new]["published"] and vs[new]["passed"] == 0
    preview = app.course.page(UNIT, new)
    assert preview["preview"] and preview["plan"]["provenance"]["run"] == new and not app.course.page(UNIT)["preview"]
    app.course.switch(UNIT, new)
    assert app.course.page(UNIT)["plan"]["provenance"]["run"] == new and app.course.page(UNIT)["progress"]["passed"] == []
    app.course.switch(UNIT, old)                                                  # 换回来：旧版的进度还在
    page = app.course.page(UNIT)
    assert page["plan"]["provenance"]["run"] == old and page["progress"]["passed"] == [0]
    assert {v["plan_id"]: v["published"] for v in page["versions"]} == {old: True, new: True}
    with pytest.raises(Exception, match="没有这一版"):
        app.course.page(UNIT, "nope")


def test_publish_needs_an_accepted_loop(app, runtime, pages):
    runtime.script = generate(good_plan())
    run = app.harness.run("tutor-prep", UNIT)
    from studykit.domain.errors import DomainError
    with pytest.raises(DomainError, match="没有通过产出循环"):
        app.harness.publish(run)


# ---------- 分步生成（D-038）：大纲 → 一节一节写 → 拼起来 ----------

def _limits():
    return PlanLimits(UNIT, 180, 45, 5, set(), {}, {SRC.rstrip("/")}, ("choice", "fill", "lab"))


def test_outline_is_checked_before_any_section_is_written():
    o = outline_of(good_plan())
    assert outline_findings(o, _limits()) == []
    bad = copy.deepcopy(o)
    bad["parts"][0]["sections"][1]["check"] = {"type": "choice", "what": ""}
    bad["parts"][0]["sections"][2]["check"]["type"] = "choice"
    bad["parts"][0]["sections"][3]["reading"] = []
    whats = {(f.address, f.what.split("：")[-1][:12]) for f in outline_findings(bad, _limits())}
    assert ("/sections/1", "check.what") in {(a, w[:10]) for a, w in whats}
    assert not any("至少一半" in f.what for f in outline_findings(bad, _limits()))   # D-043：题型跟着内容走，不再要求一半动手
    assert any(a == "/sections/3" and "reading" in w for a, w in {(f.address, f.what) for f in outline_findings(bad, _limits())})


def test_assemble_fills_stubs_and_the_outline_wins_on_title_and_minutes():
    o, plan = outline_of(good_plan()), good_plan()
    sec = {**sections_of(plan)[0], "title": "我自己改的标题", "minutes": 99}
    filled = fill_stub(sections_of(o)[0], sec)
    assert (filled["title"], filled["minutes"]) == ("认识练习场", 20) and "check" not in filled
    partial = assemble(o, [filled])
    assert len(sections_of(partial)) == 1 and partial["lab"] == LAB and partial["sources"] == plan["sources"]
    full = assemble(o, sections_of(plan))
    assert all(s["check_why"] for s in sections_of(full))                          # 为什么这样练：从大纲带进课程计划（D-052）
    for s in sections_of(full):
        s.pop("check_why")
    assert plan_parts(full) == plan_parts(plan)                                    # 除此之外，全写完 = 原来的课程计划
    # 新词和定义归大纲：写节的人交来的 terms 不算数
    mine = fill_stub(sections_of(o)[0], {**sections_of(plan)[0], "terms": [{"id": "tools.cmd.ls", "term": "ls", "explain": "x"}]})
    assert mine["terms"] == sections_of(plan)[0]["terms"]
    # 约定的断言并进这一节指定题型的检查点；已经有同样 desc 的不重复加
    stub = {**sections_of(o)[1], "state_after": sections_of(o)[1]["state_after"] + [{"file": "notes.txt", "contains": "x", "desc": "记了笔记"}]}
    checks = fill_stub(stub, sections_of(plan)[1])["checkpoint"][0]["checks"]
    assert [c["desc"] for c in checks] == ["r1.txt 里是 ERROR 的行数", "记了笔记"] and checks[1]["contract"]
    no_lab = {**sections_of(plan)[1], "checkpoint": [checkpoint()]}
    assert "检查点是 lab 题" in section_findings(no_lab, stub, 1)[0].what


def test_a_section_run_gets_only_the_outline_and_the_pages_it_should_read(app, runtime, pages):
    plan = good_plan()
    staged(runtime, plan)
    kb = app.harness.research("tutor-prep", UNIT)
    o = app.harness.outline("tutor-prep", UNIT, kb_run=kb)
    assert o.submitted and o.id.endswith("-o") and o.input["stage"] == "outline"
    assert "# 这一步：写大纲" in (app.harness.dir("tutor-prep", o.id) / "brief.md").read_text(encoding="utf-8")
    # 第 2 节先写（不等第 1 节）；第一次交的没有大纲说的练习场任务，被退回
    runtime.sections = {1: [("submit_section", {"section": {**sections_of(plan)[1], "checkpoint": [checkpoint()]}}),
                            ("submit_section", {"section": {k: v for k, v in sections_of(plan)[1].items() if k != "terms"}})]}
    s2 = app.harness.section(o, 1)
    assert s2.submitted and s2.id.endswith("-s2") and s2.input["index"] == 1 and s2.tool_calls["submit_section"]["errors"] == 1
    brief = (app.harness.dir("tutor-prep", s2.id) / "brief.md").read_text(encoding="utf-8")
    assert "# 这一步：写第 2 节「s1」" in brief and "### 第 2 节「s1」（👉 这一节）" in brief
    assert "- 讲清的要点：认识练习场 的要点" in brief and "- 新词 pwd：打印当前目录" in brief   # 前面几节的约定：要点、新词定义
    assert "做完后练习场满足：r1.txt 里是 ERROR 的行数" in brief                        # 这一节要兑现的约定
    assert "- 讲清的要点：s3 的要点" in brief and "做完后练习场满足：r3.txt" not in brief  # 后面的节只看要点（不抢讲）
    assert "打开 Git Bash" not in brief                                              # 看不到别的节的正文
    assert "shell 讲义正文" in brief and "logs/app.log" in brief                     # 依据页面的原文、练习场文件
    assert app.harness.limits(app.harness.dir("tutor-prep", s2.id)).grounded         # 大纲时打开过的页面仍然算出处


def test_each_lab_verify_gets_its_own_directory(app, runtime, pages):
    """两次检验同时跑（比如面板和命令行各开了一次）时，不能共用、互删同一个练习场目录。"""
    class Recorder:
        def __init__(self):
            self.dirs = []

        def verify(self, plan, workdir):
            self.dirs.append(workdir)
            return {"ok": True, "findings": [], "sections": []}
    runtime.scripts = [generate(good_plan())]
    run = app.harness.run("tutor-prep", UNIT)
    rec = Recorder()
    app.harness.specs = [dataclasses.replace(app.harness.specs[0], practice=rec)]
    app.harness.verify_lab(run)
    app.harness.verify_lab(run)
    assert len(set(rec.dirs)) == 2 and all(d.parent.name == "raw" for d in rec.dirs)


def test_sections_are_written_at_the_same_time_and_assembled_in_order(app, runtime, pages):
    plan = good_plan()
    staged(runtime, plan)
    runtime.delay = {0: 0.6, 1: 0.6, 2: 0.6, 3: 0.6}                             # 每节"写" 0.6 秒
    t0 = time.monotonic()
    a = app.prep.generate(UNIT)
    assert time.monotonic() - t0 < 1.5                                            # 串行要 2.4 秒以上
    assert [s["title"] for s in sections_of(a.artifact)] == ["认识练习场", "s1", "s2", "s3"]   # 按节的顺序拼
    assert a.run.endswith("-a") and a.cost == pytest.approx(0.06)                # 调研 + 大纲 + 4 节的花费都算上


def test_one_section_that_never_submits_stops_the_generation(app, runtime, pages):
    staged(runtime, good_plan())
    runtime.sections[2] = []
    runtime.script = []                                                           # 重跑一次也交不出来
    a = app.prep.generate(UNIT)
    assert a.artifact == {} and "-s3" in a.run


def test_outline_must_spell_out_the_contract_between_sections():
    o = outline_of(good_plan())
    bad = copy.deepcopy(o)
    bad["parts"][0]["sections"][1].pop("state_after")
    bad["parts"][0]["sections"][2]["teaches"] = []
    bad["parts"][0]["sections"][3]["terms"] = [{"id": "tools.cmd.pwd", "term": "pwd"}]
    bad["parts"][0]["sections"][3]["state_after"] = [{"desc": "只有描述"}]
    from studykit.specs.cs_practice import plan_rules
    fs = [(f.address, f.what) for f in outline_findings(bad, _limits(), plan_rules.RULES)]
    assert any(a == "/sections/1" and "state_after" in w for a, w in fs)
    assert any(a == "/sections/2" and "teaches" in w for a, w in fs)
    assert any(a == "/sections/3" and "explain" in w for a, w in fs)
    assert any(a == "/sections/3" and "要有 run 或 file" in w for a, w in fs)


@needs_bash
def test_a_broken_contract_is_blamed_on_the_section_that_broke_it(tmp_path):
    """第 2 节约定"做完后有 notes.txt"，第 3 节依赖它；第 2 节的参考做法没写 notes.txt。
    实跑要指出第 2 节（没兑现约定），而不是第 3 节（按约定接着做）。"""
    from studykit.specs.cs_practice.practice import BashPractice
    plan = good_plan()
    o = outline_of(plan)
    o["parts"][0]["sections"][1]["state_after"].append({"file": "notes.txt", "contains": "ok", "desc": "notes.txt 里记了 ok"})
    s3 = sections_of(plan)[2]
    s3["checkpoint"] = [lab_cp(2, solution=["cat notes.txt > /dev/null && echo 2 > r2.txt"])]
    plan_ = assemble(o, [sections_of(plan)[0], sections_of(plan)[1], s3, sections_of(plan)[3]])
    r = BashPractice(tmp_path / "labs", tmp_path / "state").verify(plan_, tmp_path / "v")
    blocks = [f for f in r["findings"] if f["severity"] == "block"]
    assert blocks and all(f["address"] == "/sections/1/checkpoint/0" for f in blocks)
    assert "notes.txt 里记了 ok" in blocks[0]["what"]
    assert any(f["address"] == "/sections/2/checkpoint/0" and f["severity"] == "warn" and "第 2 节没兑现约定" in f["what"]
               for f in r["findings"])                                        # 下游的失败留着，标成可能是连带的


# ---------- 备课进度看得见（D-040） ----------

@needs_bash
def test_prepare_reports_each_step_so_the_course_page_can_show_it(app, runtime, pages):
    broken = good_plan()
    broken["parts"][0]["sections"][2]["checkpoint"][0] = lab_cp(2, solution=["echo 3 > r2.txt"])
    staged(runtime, broken)
    runtime.scripts.append(repair_with([{"address": "/sections/2", "value": good_plan()["parts"][0]["sections"][2]}]))
    seen = []
    app.prep.prepare(UNIT, report=lambda p: seen.append(p["label"]))
    labels = list(dict.fromkeys(seen))
    assert labels[:3] == ["调研：读讲义、整理知识点（只做一次，以后复用）", "按你的情况从知识库编大纲", "各节同时在写：0/4 节写好"]
    assert "各节同时在写：4/4 节写好" in labels
    i = labels.index("第 1 轮检验：自动检查")
    assert labels[i:i + 3] == ["第 1 轮检验：自动检查", "第 1 轮检验：评审模型读命令、看质量", "第 1 轮检验：在临时目录里把练习场从头跑一遍"]
    assert "只修被指出的部分：/sections/2" in labels and labels[-1].startswith("第 2 轮检验")


def test_progress_is_visible_from_any_process_and_locks_the_unit(app, root):
    """命令行在备课时，网页（另一个进程）也看得见进度；同一个单元不能同时备两次（D-040）。"""
    from studykit.adapters.prep_status import FilePrepStatus
    from studykit.domain.errors import DomainError
    from tests.test_panel import _add_unit
    _add_unit(root)
    other = FilePrepStatus(app.config.data / "prep")          # 另一个进程：同一个目录
    assert other.begin("tools-02-git")
    other.update("tools-02-git", {"step": "sections", "label": "各节同时在写：2/5 节写好", "sections": {"total": 5, "done": 2}})
    git = lambda: next(u for t in app.panel.overview()["topics"] for u in t["units"] if u["id"] == "tools-02-git")  # noqa: E731
    assert git()["prep"]["stage"] == "preparing" and git()["prep"]["progress"]["label"] == "各节同时在写：2/5 节写好"
    with pytest.raises(DomainError, match="已经在备课了"):
        app.panel.prepare("tools-02-git")
    with pytest.raises(DomainError, match="已经在备课了"):
        app.prep.prepare("tools-02-git")
    other.end("tools-02-git")
    assert git()["prep"]["stage"] == "todo"


def test_a_prep_whose_process_died_is_not_stuck_in_preparing(app, root):
    import json as _json
    from tests.test_panel import _add_unit
    _add_unit(root)
    d = app.config.data / "prep"
    d.mkdir(parents=True, exist_ok=True)
    (d / "tools-02-git.json").write_text(_json.dumps({"state": "running", "pid": 2 ** 30, "progress": {"label": "x"}}), encoding="utf-8")
    [git] = [u for t in app.panel.overview()["topics"] for u in t["units"] if u["id"] == "tools-02-git"]
    assert git["prep"]["stage"] == "todo" and "被中断" in git["prep"]["job_error"]
    assert app.prep.status.begin("tools-02-git")                  # 死掉的进程不占锁


# ---------- 预备：学第 N 单元时备第 N+1 单元（D-040 ②） ----------

def test_opening_a_unit_prepares_the_next_one_in_the_background(app, root, unit):
    from tests.test_panel import _add_unit
    _add_unit(root)                                           # 课程清单：Shell → Git
    started = []
    app.prep.prepare = lambda u, **kw: started.append(u)
    assert app.panel.prefetch_after("tools-01-shell") == "tools-02-git"
    assert started == ["tools-02-git"]
    assert app.panel.prefetch_after("tools-02-git") is None                   # 最后一个单元：没有下一个


def test_prefetch_never_retries_or_duplicates(app, root, unit):
    from tests.test_panel import _add_unit
    _add_unit(root)
    started = []
    app.prep.prepare = lambda u, **kw: started.append(u)
    assert app.prep.status.begin("tools-02-git")             # 已经在备（别的进程）
    assert app.panel.prefetch_after("tools-01-shell") is None
    app.prep.status.end("tools-02-git", "RuntimeError: x")
    runs = app.panel._runs_by_unit
    app.panel._runs_by_unit = lambda: {"tools-02-git": [Run("r1", "tutor-prep", "v", "tools-02-git", "me", "batch", "t", {})]}
    assert app.panel.prefetch_after("tools-01-shell") is None                 # 备过、没走完：不自动重试（免得反复花钱）
    app.panel._runs_by_unit = runs
    p = root / "progress" / "settings.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "prefetch_next: false\n", encoding="utf-8")
    assert app.panel.prefetch_after("tools-01-shell") is None and started == []



# ---------- 单元知识库（D-040 ③）：和学习者无关，只做一次 ----------

def test_research_sees_no_learner_information(app, runtime, pages):
    runtime.scripts = [[("fetch_url", {"url": SRC}), ("submit_research", {"knowledge": {"summary": "x", "sources": [], "points": []}}),
                        ("fetch_url", {"url": OTHER}), ("submit_research", {"knowledge": kb_of(good_plan())})]]
    kb = app.harness.research("tutor-prep", UNIT)
    assert kb.submitted and kb.id.endswith("-k") and kb.tool_calls["submit_research"]["errors"] == 1   # 空的知识库被退回
    brief = (app.harness.dir("tutor-prep", kb.id) / "brief.md").read_text(encoding="utf-8")
    assert "产品经理" not in brief and "零基础" not in brief                 # 学习者画像、单元备注都不给
    assert kb.input["known_terms"] == [] and "learner" not in kb.input and "related" not in kb.input
    assert app.harness.knowledge("tutor-prep", UNIT).id == kb.id


def test_the_outline_starts_from_the_knowledge_base_and_inherits_its_pages(app, runtime, pages):
    staged(runtime, good_plan())
    kb = app.harness.research("tutor-prep", UNIT)
    o = app.harness.outline("tutor-prep", UNIT, kb_run=kb)
    assert o.submitted and o.input["kb_run"] == kb.id and "fetch_url" not in o.tool_calls   # 不用再读讲义
    brief = (app.harness.dir("tutor-prep", o.id) / "brief.md").read_text(encoding="utf-8")
    assert "这个单元的知识库" in brief and "## /points" in brief and "产品经理" in brief     # 知识库 + 学习者信息


def test_a_second_preparation_reuses_the_knowledge_base(app, runtime, pages, clock):
    staged(runtime, good_plan())
    runtime.review_reply = "（超时）"                                        # 评审失败：第一次备课停在需要你决定
    runtime.script = repair_with([{"address": "", "value": good_plan()}])
    app.prep.prepare(UNIT)
    clock.tick(60)
    before = len([t for t in runtime.runs if "submit_research" in t])
    staged(runtime, good_plan(), research=False)
    app.prep.prepare(UNIT)
    assert len([t for t in runtime.runs if "submit_research" in t]) == before == 1     # 第二次备课没有再调研


def test_build_knowledge_for_many_units_at_once(app, runtime, pages, root):
    from tests.test_panel import _add_unit
    _add_unit(root)
    runtime.script = [("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}), ("submit_research", {"knowledge": kb_of(good_plan())})]
    done = app.prep.build_knowledge([UNIT, "tools-02-git"])
    assert set(done) == {UNIT, "tools-02-git"} and all(done.values())
    assert app.prep.build_knowledge([UNIT, "tools-02-git"]) == {}             # 已经有的不再做


def test_redo_replaces_the_units_knowledge_base(app, runtime, pages):
    runtime.script = [("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}), ("submit_research", {"knowledge": kb_of(good_plan())})]
    first = app.prep.build_knowledge([UNIT])[UNIT]
    again = app.prep.build_knowledge([UNIT], redo=True)[UNIT]                # 调研方法变了：重做一份
    assert again and again != first and app.harness.knowledge("tutor-prep", UNIT).id == again


@needs_bash
def test_quality_findings_already_paid_for_are_not_dropped_when_the_lab_blocks(app, runtime, pages):
    """评审模型一次调用同时给出安全和质量结论；练习场实跑先阻断时，质量问题也要进这一轮的修复（不能丢掉、等下一轮碰运气）。"""
    broken = good_plan()
    broken["parts"][0]["sections"][2]["checkpoint"][0] = lab_cp(2, solution=["echo 3 > r2.txt"])
    staged(runtime, broken)
    runtime.review_reply = json.dumps({"unsafe": [], "findings": [
        {"address": "/sections/3/checkpoint/0", "severity": "block", "what": "正则漏了数字", "rubric": "checkable"}]})
    runtime.script = []
    r = app.prep.prepare(UNIT)
    first = {(f["evaluator"], f["address"]) for f in r["rounds"][0]["findings"] if f["severity"] == "block"}
    assert first == {("lab_verify", "/sections/2/checkpoint/0"), ("reviewer", "/sections/3/checkpoint/0")}
