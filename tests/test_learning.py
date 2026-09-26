"""学习域的用例：通过 bootstrap 组装的 App 走一遍，数据落在临时 SQLite 里。"""
import datetime as dt
import json

import pytest

from studykit.domain.errors import CourseError, DomainError, KnowledgeError, LessonError
from studykit.domain.evidence import question_id

# ======================================================================
# 做题
# ======================================================================


def test_lesson_ref_cannot_escape(app):
    for bad in ["../x", "t/../../etc", "t/01-x/../../..", "T/01", ""]:
        with pytest.raises(DomainError):
            app.assessment.view(bad)


def test_view_never_contains_answers(app):
    text = json.dumps(app.assessment.view("t/01-x"), ensure_ascii=False)
    assert "因为 B" not in text and "rubric" not in text and "checks" not in text


def test_submit_records_evidence_and_reveals_explanation(app):
    r = app.assessment.submit("t/01-x", "q1", ["B"])
    assert r["result"] == "pass" and r["explain"] == "因为 B"
    [e] = app.store.query("me", verbs=["answered"])
    assert (e.object_id, e.nodes, e.unit, e.payload["checker"]) == ("t/01-x#q1", ("t.a",), "tools-01-shell", "choice")
    assert e.object_version                                              # 记着题目版本（G3）
    assert app.assessment.view("t/01-x")["questions"][0]["previous"]["score"] == 1.0
    assert "previous" not in app.assessment.view("t/01-x", retake=True)["questions"][0]


def test_short_answer_waits_for_grading_then_counts(app, clock):
    r = app.assessment.submit("t/01-x", "q2", "我的解释")
    assert r["result"] == "pending" and "explain" not in r            # 批改前不给解析
    assert "t/01-x q2" in app.learner.progress_md()
    assert app.learner.weak()["pending_grading"][0]["qid"] == "q2"
    clock.tick()
    app.assessment.grade("t/01-x", "q2", 0.5, "漏了关键点")
    prev = app.assessment.view("t/01-x")["questions"][1]["previous"]
    assert prev["score"] == 0.5 and prev["note"] == "漏了关键点" and prev["explain"] == "要点是……"
    assert app.learner.stats()["t.b"]["failing_levels"] == [2]
    with pytest.raises(LessonError, match="没有待批改"):
        app.assessment.grade("t/01-x", "q2", 1.0)                      # 批改过的不能再批一次
    [g] = app.store.query("me", verbs=["graded"])
    assert g.actor.id == "tutor" and g.actor.type == "agent"


def test_external_practice_counts_for_mastery(app):
    app.assessment.log_practice("dsa.hash", 3, 1.0, "LeetCode 1")
    assert app.learner.stats()["dsa.hash"]["mastery"] == 3


def test_terminal_flow(app):
    app.assessment.act("t/01-x", "q3", {"op": "open"})
    app.assessment.act("t/01-x", "q3", {"op": "exec", "cmd": "git switch -c dev"})
    assert app.assessment.submit("t/01-x", "q3", {"history": ["git switch -c dev"]})["score"] == 1.0


def test_code_runs_are_linked_to_the_submission(app, clock):
    app.assessment.act("t/01-x", "q4", {"op": "run", "code": "def f(x):\n    return 1\n"})
    clock.tick()
    r = app.assessment.submit("t/01-x", "q4", {"code": "def f(x):\n    return x\n"})
    assert r["score"] == 1.0 and len(r["tests"]) == 2
    runs = app.assessment.runs("t/01-x", "q4")
    assert [(x["kind"], x["passed"], x["total"]) for x in runs] == [("run", 1, 2), ("submit", 2, 2)]
    assert "return 1" in runs[0]["code"] and runs[0]["failures"][0]["name"] == "test_two"
    [answer] = app.store.query("me", verbs=["answered"], object_id=question_id("t/01-x", "q4"))
    assert answer.payload["runs"] == [x["id"] for x in runs]          # 这次提交是跑了几次才过的（G1）
    assert app.learner.weak()["code_practice"][0]["first_full_pass_at_run"] == 2


# ======================================================================
# 课程页
# ======================================================================

def test_missing_plan_says_how_to_get_one(app):
    with pytest.raises(CourseError, match="准备学"):
        app.course.page("tools-02-git")
    with pytest.raises(DomainError):
        app.course.page("../x")


def test_page_hides_answers_and_lab_files(app, unit):
    p = app.course.page(unit)
    text = json.dumps(p["plan"], ensure_ascii=False)
    for secret in ('"answer"', '"accept"', '"solution"', '"hints"', '"traps"', "grep -c ERROR", "pwd = print"):
        assert secret not in text
    assert p["plan"]["lab"] == {"story": "测试服务器出问题了", "files": 2}
    assert p["planned_minutes"] == 70 and p["sessions"] == [[0, 1], [2]] and p["quiz"] == "t/01-x"
    assert app.course.list() == [{"unit": unit, "title": "Shell", "minutes": 70, "sections": 3, "done": 0}]


def test_checkpoints_traps_section_pass_and_term_credit(app, unit, clock):
    app.content.add_nodes([{"id": "tools.cmd.cd", "title": "cd", "kind": "term"}])
    wrong = app.course.check(unit, 0, 0, ["A"])
    assert not wrong["ok"] and wrong["trap"]["cause"] == "和 cd - 混了" and "explain" not in wrong
    clock.tick()
    right = app.course.check(unit, 0, 0, ["B"])
    assert right["ok"] and right["attempt"] == 2 and not right["section_passed"]
    clock.tick()
    r = app.course.check(unit, 0, 1, [".."])
    assert r["section_passed"] and r["progress"]["passed"] == [0]
    [passed] = app.store.query("me", verbs=["passed_section"])
    assert passed.nodes == ("tools.cmd.pwd", "tools.cmd.cd") and passed.actor.type == "system"
    assert app.learner.states()["tools.cmd.cd"].state == "mastered"
    app.course.check(unit, 0, 1, [".."])                                # 再做一次不会重复记"整节通过"
    assert len(app.store.query("me", verbs=["passed_section"])) == 1


def test_hints_one_level_at_a_time_and_answers_point_back_to_them(app, unit, clock):
    assert app.course.hint(unit, 0, 0) == {"level": 1, "total": 3, "hints": ["方向"]}
    clock.tick()
    app.course.hint(unit, 0, 0)
    assert app.course.hint(unit, 0, 0)["hints"] == ["方向", "关键概念", "接近答案"]
    assert app.course.hint(unit, 0, 0)["level"] == 3                   # 到顶了不再增加
    assert len(app.store.query("me", verbs=["requested_hint"])) == 3
    clock.tick()
    app.course.check(unit, 0, 0, ["B"])
    [answer] = app.store.query("me", verbs=["answered"])
    last_hint = app.store.query("me", verbs=["requested_hint"])[-1]
    assert answer.caused_by == last_hint.id                            # 提示 → 作答的因果（G2）
    assert app.course.progress(unit)["checkpoints"]["0"]["0"]["hints"] == 3


def test_page_events_and_progress(app, unit, clock):
    app.course.record(unit, 0, "open")
    clock.tick(12)
    app.course.record(unit, 0, "skip")
    app.course.record(unit, 0, "load_rating", rating=4)
    app.course.record(unit, None, "activity", kind="video")
    clock.tick(40)                                                      # 空了 40 分钟：会话断开
    p = app.course.record(unit, 2, "done", minutes=30)
    assert p["skipped"] == [0] and p["ratings"] == {"0": 4} and p["passed"] == [2]
    assert p["spent_minutes"] == {"0": 12.0, "2": 1.0}      # 单元级心跳在第 1 节之后，所以第 1 节不再记收尾那 1 分钟
    with pytest.raises(CourseError):
        app.course.record(unit, 0, "checkpoint")


def test_resume_after_pause_counts_the_away_time_when_learner_says_so(app, unit, clock):
    """D-027：页面只报"离开了几分钟"，离开的起点由服务器的时钟算（避免浏览器和服务器时区不一致）。"""
    app.course.record(unit, 0, "open")
    clock.tick(30)
    p = app.course.record(unit, 0, "resume", away_minutes=29, counted=True)
    [e] = app.store.query("me", verbs=["resumed"])
    assert e.payload == {"away_start": (clock.now() - dt.timedelta(minutes=29)).isoformat(timespec="seconds"), "counted": True}
    assert p["spent_minutes"] == {"0": 31.0}                            # 离开 29 分钟 > GAP，但学习者说在学
    with pytest.raises(CourseError):
        app.course.record(unit, 0, "resume", away_minutes=-3, counted=True)


def test_progress_belongs_to_one_plan_version(app, unit):
    app.course.check(unit, 0, 0, ["B"])
    app.course.check(unit, 0, 1, [".."])
    assert app.course.progress(unit)["passed"] == [0]
    from tests.conftest import plan_v2
    app.course.publish(plan_v2("run-3"))
    assert app.course.progress(unit)["passed"] == []                   # 新版课程，旧版的进度不串过来
    ev = app.course.evaluate(unit, "run-2")
    assert ev["plan"] == "run-2" and ev["rows"][0]["passed"]


def test_course_evaluation_pairs_predictions_with_what_happened(app, unit, clock):
    app.course.record(unit, 0, "open")
    app.course.record(unit, 0, "term", node="tools.cmd.pwd", action="open")
    app.course.record(unit, 0, "term", node="tools.cmd.pwd", action="unknown")
    app.course.record(unit, 0, "term", node="tools.cmd.cd", action="known")
    app.course.record(unit, 0, "term_miss", text="工作目录")
    for resp in (["A"], ["B"]):
        clock.tick()
        app.course.check(unit, 0, 0, resp)
    app.course.check(unit, 0, 1, [".."])
    app.course.hint(unit, 0, 0)
    app.course.record(unit, 0, "load_rating", rating=3)
    r = app.course.evaluate(unit)
    row = r["rows"][0]
    assert (row["opened"], row["unknown"], row["known"], row["misses"]) == (1, 1, 1, ["工作目录"])
    assert row["first_try"] == [False, True] and row["attempts"] == [2, 1] and row["max_hint"] == 1
    assert row["rating"] == 3 and row["predicted_new_terms"] == 2 and row["passed"]
    assert r["summary"]["term_precision"] == 0.5 and r["summary"]["first_try_rate"] == 0.5
    assert "| 1. a |" in app.course.format_evaluation(r)


# ======================================================================
# 学习者模型
# ======================================================================

def test_observations_and_graded_queries(app, unit):
    app.content.add_nodes([
        {"id": "tools.shell.role", "title": "终端 / shell / 命令", "kind": "concept", "units": ["tools-00-intro"]},
        {"id": "tools.shell.glob", "title": "通配符", "kind": "concept", "requires": ["tools.shell.role"], "units": [unit]},
        {"id": "tools.cmd.sed", "title": "sed", "kind": "term", "requires": ["tools.shell.glob"], "units": [unit]},
    ])
    app.learner.observe("tools.shell.glob", "weak", "glob 那段完全跟不上")
    with pytest.raises(KnowledgeError):
        app.learner.observe("Bad", "weak", "x")
    assert "通配符（`tools.shell.glob`）" in app.learner.summary()
    rel = app.learner.related(unit)
    assert [n["id"] for n in rel["taught"]] == ["tools.shell.glob", "tools.cmd.sed"]
    assert rel["prerequisites_not_mastered"] == [{"id": "tools.shell.role", "title": "终端 / shell / 命令",
                                                  "state": "new", "distance": 1}]
    assert rel["weak"][0]["why"] == ["glob 那段完全跟不上（tutor）"]
    show = app.learner.show("tools.shell.glob")
    assert show["state"] == "weak" and show["required_by"][0]["id"] == "tools.cmd.sed"
    tree = app.learner.tree("tools")
    assert ["tools.shell.role", "tools.shell.glob"] in tree["edges"]
    with pytest.raises(KnowledgeError):
        app.learner.show("tools.nope")


def test_graph_check_reports_orphans(app):
    app.assessment.submit("t/01-x", "q1", ["B"])                      # t.a 不在知识图里
    assert any("t.a" in e for e in app.learner.check_graph())


def test_weak_report_and_untested_concepts(app, unit):
    app.content.add_nodes([{"id": "tools.shell.glob", "title": "通配符", "kind": "concept", "units": [unit]}])
    w = app.learner.weak()
    assert w["studied_units"][0]["id"] == unit and w["untested_concepts"] == ["tools.shell.glob"]


def test_time_report_includes_manual_entries(app, unit):
    app.course.record(unit, 0, "open")
    app.learner.log_time("2026-09-25T20:00:00", 48, "看第 1 讲视频")
    report = app.learner.time_report()
    assert "2026-09-25  合计 48 分钟" in report and "看第 1 讲视频" in report and "2026-09-26" in report


def test_weak_report_is_json_even_with_yaml_dates(app, root):
    # syllabus.yaml 里不加引号的 done_on: 2026-09-26 会被 YAML 解析成 date 对象
    (root / "progress" / "syllabus.yaml").write_text(
        "topics:\n  tools:\n    title: 开发工具\n    units:\n"
        "      - {id: tools-01-shell, title: Shell, status: done, done_on: 2026-09-26}\n", encoding="utf-8")
    w = app.learner.weak()
    assert json.loads(json.dumps(w))["studied_units"][0]["done_on"] == "2026-09-26"


# ---------- 单元题整卷一次交（D-031） ----------

EXAM = "t/02-exam"
LLM_OK = '{"items": [{"id": 1, "score": 0.5, "why": "说了 cd"}, {"id": 2, "score": 0.25, "why": "只说了结论"}], "feedback": "glob 是 bash 展开的"}'


def test_unit_quiz_is_exam_and_rejects_single_submit(app):
    assert app.assessment.view(EXAM)["exam"] is True and app.assessment.view("t/01-x")["exam"] is False
    with pytest.raises(LessonError, match="整卷"):
        app.assessment.submit(EXAM, "q1", ["B"])
    with pytest.raises(LessonError):
        app.assessment.submit_exam("t/01-x", {})


def test_exam_code_runs_are_limited_and_reset_after_submit(app, runtime):
    runtime.judge_reply = LLM_OK
    code = "def f(x):\n    return x\n"
    for left in (4, 3, 2, 1, 0):
        assert app.assessment.act(EXAM, "q3", {"op": "run", "code": code})["runs_left"] == left
    with pytest.raises(LessonError, match="5 次"):
        app.assessment.act(EXAM, "q3", {"op": "run", "code": code})
    assert app.assessment.view(EXAM)["questions"][2]["runs_left"] == 0
    r = app.assessment.submit_exam(EXAM, {"q3": {"code": code}})     # 交卷时的判分不算次数
    assert r["questions"]["q3"]["score"] == 1.0
    assert app.assessment.view(EXAM, retake=True)["questions"][2]["runs_left"] == 5


def test_exam_submit_grades_everything_and_llm_grades_short(app, runtime, root):
    runtime.judge_reply = LLM_OK
    r = app.assessment.submit_exam(EXAM, {"q1": ["B"], "q2": "cd 失败了还会继续 rm"})
    q = r["questions"]
    assert q["q1"]["score"] == 1.0 and q["q1"]["explain"] == "因为 B"
    assert q["q2"]["score"] == 0.75 and q["q2"]["graded_by"] == "short-grader" and q["q2"]["note"] == "glob 是 bash 展开的"
    assert q["q2"]["explain"] == "两个问题" and q["q3"]["score"] == 0.0         # 代码没写：按文件现状判
    assert q["q2"]["feedback"] == []                                        # 批完了就不再说"等导师批改"
    assert r["score"] == round((1 + 0.75 + 0) / 3, 3)
    w = app.learner.weak()
    assert w["pending_grading"] == [] and any(c["concept"] == "t.b" for c in w["concepts"])
    graded = [e for e in app.store.query("me", verbs=("graded",))]
    assert graded[0].actor.id == "short-grader" and graded[0].payload["items"][0]["why"] == "说了 cd"
    assert [e.payload["answers"]["q2"] for e in app.store.query("me", verbs=("submitted_exam",))]
    ws = app.config.data / "runs" / "short-grader"
    assert "cd 失败了还会继续 rm" in next(ws.iterdir()).joinpath("input.md").read_text(encoding="utf-8")
    view = {x["id"]: x for x in app.assessment.view(EXAM)["questions"]}
    assert view["q2"]["previous"]["graded_by"] == "short-grader"


def test_llm_failure_leaves_short_answer_for_the_tutor(app, runtime):
    runtime.judge_reply = "抱歉，我不能批改"
    r = app.assessment.submit_exam(EXAM, {"q1": ["B"], "q2": "回答"})
    assert r["questions"]["q2"]["result"] == "pending" and r["score"] is None
    assert [p["qid"] for p in app.learner.weak()["pending_grading"]] == ["q2"]
    app.assessment.grade(EXAM, "q2", 0.5, "导师批")
    assert app.learner.weak()["pending_grading"] == []


def test_tutor_can_override_llm_grade(app, runtime):
    runtime.judge_reply = LLM_OK
    app.assessment.submit_exam(EXAM, {"q1": ["B"], "q2": "回答"})
    app.assessment.grade(EXAM, "q2", 0.25, "LLM 给高了")
    view = {x["id"]: x for x in app.assessment.view(EXAM)["questions"]}
    assert view["q2"]["previous"]["score"] == 0.25 and view["q2"]["previous"]["graded_by"] == "tutor"


def test_empty_short_answer_scores_zero_without_calling_llm(app, runtime):
    runtime.judge_reply = "不该被用到"
    r = app.assessment.submit_exam(EXAM, {"q1": ["B"]})
    assert r["questions"]["q2"]["score"] == 0.0


def test_graded_short_answer_shows_reference_answer_and_rubric(app, runtime):
    # F-051：批改后要看到标准答案 + 每个评分点的原文，才能对照"我的回答哪里对、哪里缺"
    r = app.assessment.submit("t/01-x", "q2", "我的解释")
    assert "rubric" not in r and "explain" not in r                    # 批改前不给评分点
    runtime.judge_reply = LLM_OK
    q2 = app.assessment.submit_exam(EXAM, {"q1": ["B"], "q2": "回答"})["questions"]["q2"]
    assert q2["explain"] == "两个问题" and q2["rubric"][0] == "说出 cd 失败不停（0.5）"
    view = {x["id"]: x for x in app.assessment.view(EXAM)["questions"]}
    assert view["q2"]["previous"]["rubric"] == q2["rubric"]
