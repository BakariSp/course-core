"""备课控制面板（D-032）：课程清单 × 学习进度 × 备课流水线 × agent 看到的上下文。数据全是已有记录的投影。"""
import pytest

from studykit.domain.errors import DomainError
from tests.test_harness import SRC, plan_of, section


def _add_unit(root, uid="tools-02-git", title="Git"):
    p = root / "progress" / "syllabus.yaml"
    line = f"      - {{id: {uid}, title: {title}, status: todo, notes: 想学 bisect}}\n"
    p.write_text(p.read_text(encoding="utf-8") + line, encoding="utf-8")


def _units(app):
    return {u["id"]: u for t in app.panel.overview()["topics"] for u in t["units"]}


def test_overview_maps_curriculum_to_study_and_prep_status(app, unit, root):
    _add_unit(root)
    units = _units(app)
    shell, git = units["tools-01-shell"], units["tools-02-git"]
    assert shell["status"] == "watching" and shell["course"] == {"passed": 0, "sections": 3}
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
    # 假 agent 只会 submit_plan，而备课第一步只开放 submit_outline（D-038）：大纲每次都交不出来，3 轮后停在"需要你决定"
    assert detail["prep"]["stage"] == "escalated" and detail["prep"]["who"] == "learner"
    assert "没有提交课程计划" in detail["prep"]["stuck"][0]
    assert {r["stage"] for r in detail["runs"]} == {"outline"} and len(detail["runs"]) == 6   # 每轮大纲重试一次
    assert detail["runs"][0]["loop"]["verdict"] == "escalated"
    with pytest.raises(DomainError):
        app.panel.prepare("tools-99-nope")


def test_context_shows_every_input_block_where_it_comes_from_and_the_exact_brief(app, unit):
    ctx = app.panel.context("tools-01-shell")
    blocks = {b["key"]: b for b in ctx["blocks"]}
    assert blocks["unit_notes"]["value"] == "零基础" and blocks["unit_notes"]["source"] == "progress/syllabus.yaml"
    assert blocks["learner"]["source"] == "progress/learner.md" and "产品经理" in blocks["learner"]["value"]
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
