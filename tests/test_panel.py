"""备课控制面板（D-032）：课程清单 × 学习进度 × 备课流水线 × agent 看到的上下文。数据全是已有记录的投影。"""
import pytest

from studykit.domain.errors import DomainError
from studykit.domain.harness import Grade
from tests.test_harness import SRC, JUDGE, plan_of, section


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


def test_prepare_runs_the_agent_in_the_background_and_the_stage_follows(app, root, runtime, fetcher):
    _add_unit(root)
    fetcher.pages = {SRC: "讲义 " * 10}
    runtime.script = [("fetch_url", {"url": SRC}), ("submit_plan", {"plan": plan_of(*[section(f"s{i}") for i in range(4)])})]
    runtime.judge_reply = JUDGE
    job = app.panel.prepare("tools-02-git")
    assert job["state"] == "done"
    detail = app.panel.unit("tools-02-git")
    [run] = detail["runs"]
    assert run["submitted"] and run["check"]["verdict"] in ("pass", "fail") and run["judge"]["avg"] == 4
    assert detail["prep"]["stage"] in ("review", "failed") and detail["prep"]["who"] == "tutor"
    app.store.add_grade(Grade("rv", "t", run["id"], "review", "tutor", "tutor", None, "revise",
                              issues=[{"layer": "context", "what": "已知词表只有标题"}], detail={"note": "改一处"}))
    detail = app.panel.unit("tools-02-git")
    assert detail["prep"]["stage"] == "revise"
    assert detail["open_issues"] == {"context": ["已知词表只有标题"]}   # 最近一次审阅里没解决的问题，按层分组
    with pytest.raises(DomainError):
        app.panel.prepare("tools-99-nope")


def test_context_shows_every_input_block_where_it_comes_from_and_the_exact_brief(app, unit):
    ctx = app.panel.context("tools-01-shell")
    blocks = {b["key"]: b for b in ctx["blocks"]}
    assert blocks["unit_notes"]["value"] == "零基础" and blocks["unit_notes"]["source"] == "progress/syllabus.yaml"
    assert blocks["learner"]["source"] == "progress/learner.md" and "产品经理" in blocks["learner"]["value"]
    assert blocks["known_titles"]["editable"] is False                   # 从证据算出来的，不能手改
    assert "零基础" in ctx["brief"] and "产品经理" in ctx["brief"]         # agent 实际读到的完整简报
    assert {p["source"] for p in ctx["prompts"]} == {"agents/tutor-prep/SYSTEM.md", "agents/tutor-prep/task.md"}
