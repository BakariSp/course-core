"""备课控制面板（D-032）：课程清单 × 学习进度 × 备课流水线 × agent 看到的上下文。数据全是已有记录的投影。"""
import pytest

from studykit.domain.errors import DomainError
from tests.conftest import add_unit
from tests.test_harness import SRC, plan_of, section


def _add_unit(root, uid="tools-02-git", title="Git", **kw):
    add_unit(root, uid, title, **kw)


def _units(app):
    return {u["id"]: u for t in app.panel.overview()["topics"] for u in t["units"]}


def test_overview_maps_curriculum_to_study_and_prep_status(app, unit, root):
    _add_unit(root)
    units = _units(app)
    shell, git = units["tools-01-shell"], units["tools-02-git"]
    assert shell["requests"] == ["零基础"] and shell["course"] == {"passed": 0, "walked": 0, "skipped": 0, "sections": 3, "last_at": None}
    quizzes = {q["ref"]: q for q in shell["quizzes"]}                      # 一个单元可以有几套题
    assert quizzes["t/01-x"]["questions"] == 4 and quizzes["t/01-x"]["answered"] == 0
    assert shell["prep"]["stage"] == "published"                 # 发布过（样例计划没有对应的运行记录）
    assert git["course"] is None and git["quizzes"] == []
    assert git["prep"]["stage"] == "todo" and git["prep"]["who"] == "learner"


def test_prepare_runs_the_loop_in_the_background_and_the_stage_follows(app, root, runtime, fetcher):
    _add_unit(root)
    fetcher.pages = {SRC: "讲义 " * 10}
    runtime.script = [("fetch_url", {"url": SRC}), ("submit_plan", {"plan": plan_of(*[section(f"s{i}") for i in range(4)])})]
    job = app.panel.prepare("tools-02-git")
    assert job["state"] == "done"
    detail = app.panel.unit("tools-02-git")
    # 假 agent 只会 submit_plan，而备课第一步（调研）只开放 submit_research：每次都交不出来，同一个问题修不动，
    # 3 轮后停下来交给开发者（D-063），不是让学习者去点重新备课
    assert detail["prep"]["stage"] == "escalated" and detail["prep"]["who"] == "developer"
    assert detail["prep"]["stopped"] == "stuck"
    assert "没有提交课程计划" in detail["prep"]["stuck"][0]
    assert {r["stage"] for r in detail["runs"]} == {"research"} and len(detail["runs"]) == 6  # 每轮调研重试一次
    assert detail["runs"][0]["loop"]["verdict"] == "escalated"
    with pytest.raises(DomainError):
        app.panel.prepare("tools-99-nope")


def test_context_shows_every_input_block_where_it_comes_from_and_the_exact_brief(app, unit):
    ctx = app.panel.context("tools-01-shell")
    blocks = {b["key"]: b for b in ctx["blocks"]}
    assert blocks["unit_requests"]["value"] == "零基础" and blocks["unit_requests"]["source"] == "progress/course.yaml"
    assert "profile.yaml" in blocks["learner"]["source"] and "产品经理" in blocks["learner"]["value"]
    assert "能自己 git bisect" in blocks["course_info"]["value"] and "course-shell" in blocks["course_info"]["value"]
    assert blocks["known_titles"]["editable"] is False                   # 从证据算出来的，不能手改
    assert "零基础" in ctx["brief"] and "产品经理" in ctx["brief"]         # agent 实际读到的完整简报
    sources = [p["source"] for p in ctx["prompts"]]                     # 每个 prompt 部件一块，按拼接顺序（D-033）
    assert sources[0] == "agents/tutor-prep/prompt/role.md" and sources[-1] == "agents/tutor-prep/task.md"
    assert "agents/tutor-prep/prompt/rules.md" in sources


def test_versions_list_each_llm_call_site_with_what_changed(app, root, runtime, fetcher, clock):
    fetcher.pages = {SRC: "讲义 " * 10}
    runtime.script = [("fetch_url", {"url": SRC}), ("submit_plan", {"plan": plan_of(*[section(f"s{i}") for i in range(4)])})]
    app.panel.prepare("tools-01-shell")
    rules = root / "agents" / "tutor-prep" / "prompt" / "rules.md"
    rules.write_text(rules.read_text(encoding="utf-8") + "- 多一条\n", encoding="utf-8")
    clock.tick()
    app.panel.prepare("tools-01-shell")
    v = app.panel.versions()
    agents = {a["agent"]: a for a in v["agents"]}
    assert {"tutor-prep", "tutor-prep#reviewer", "tutor-prep#judge"} <= set(agents)   # 评审模型也是被版本管理的调用点
    last = agents["tutor-prep"]["versions"][-1]
    assert [c["part"] for c in last["changed"]] == ["prompt:rules"] and "+- 多一条" in last["changed"][0]["diff"]
    assert app.panel.version_text(last["variant"], "prompt:rules").endswith("- 多一条\n")


def test_build_knowledge_for_the_whole_curriculum_in_the_background(app, root, runtime, fetcher):
    """D-040 ③：知识库和学习者无关，curriculum 定了就能为所有单元备好。"""
    _add_unit(root)
    fetcher.pages = {SRC: "讲义 " * 10}
    from tests.test_prep import kb_of, good_plan, OTHER
    fetcher.pages[OTHER] = "第二页 " * 10
    runtime.script = [("fetch_url", {"url": SRC}), ("fetch_url", {"url": OTHER}), ("submit_research", {"knowledge": kb_of(good_plan())})]
    job = app.panel.build_knowledge()
    assert job["units"] == ["tools-01-shell", "tools-02-git"] and job["state"] == "done", job
    units = {u["id"]: u for t in app.panel.overview()["topics"] for u in t["units"]}
    assert units["tools-01-shell"]["knowledge"] and units["tools-02-git"]["knowledge"]
    with pytest.raises(DomainError, match="都已经有知识库"):
        app.panel.build_knowledge()


def test_units_whose_scope_is_open_are_never_prepared(app, root, unit):
    """D-047：范围没定（一整门讲座还没拆）的单元不备课、不自动预备、不备知识库。"""
    _add_unit(root, "tools-02-kleppmann", "Kleppmann 讲座", scope="open")
    units = _units(app)
    assert units["tools-02-kleppmann"]["scope_open"] is True
    with pytest.raises(DomainError, match="范围还没定"):
        app.panel.prepare("tools-02-kleppmann")
    assert app.panel.prefetch_after("tools-01-shell") is None


def test_preps_cut_off_by_a_restart_or_an_error_are_resumed_once(app, root):
    """D-063：备课的进程没了（服务重启、关机）或中途报错，服务起来时自动重新备，不要学习者点「重新备课」。"""
    _add_unit(root)
    started = []
    app.prep.prepare = lambda u, **kw: started.append(u)
    app.prep.status._write("tools-01-shell", {"state": "running", "pid": 0, "progress": {}, "error": ""})   # 进程已经不在
    app.prep.status._write("tools-02-git", {"state": "error", "pid": 0, "progress": {}, "error": "网络断了"})
    assert app.panel.resume_interrupted() == ["tools-01-shell", "tools-02-git"]
    assert started == ["tools-01-shell", "tools-02-git"]
    app.prep.status._write("tools-01-shell", {"state": "done", "pid": 0, "progress": {}, "error": ""})
    app.prep.status._write("tools-02-git", {"state": "done", "pid": 0, "progress": {}, "error": ""})
    assert app.panel.resume_interrupted() == []                     # 正常结束的（通过或卡住）不重来
