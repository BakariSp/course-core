"""领域层：纯函数，不碰文件和数据库。"""
import datetime as dt

import pytest

from studykit.domain import course_eval, knowledge, mastery, plan, plan_check, progress, timeline
from studykit.domain.assessment import CORE_CHECKERS, grade_choice_checkpoint, grade_fill_checkpoint
from studykit.domain.errors import CourseError, InvalidEvidence, InvalidId
from studykit.domain.evidence import Actor, Evidence, Verb, VerbRegistry, learner_actor
from studykit.domain.harness import Variant, verify_claims, parse_judge, rubric_ids
from studykit.domain.ids import LessonRef, NodeId, UnitId

from tests.conftest import plan_v2

ME = learner_actor("me")
TUTOR = Actor("agent", "tutor", "tutor")
N = iter(range(10 ** 6))


def ev(verb, object_type="question", object_id="t/01-x#q1", ts="2026-09-01T10:00:00", actor=ME, **kw):
    return Evidence(id=f"e{next(N)}", ts=ts, learner="me", actor=actor, verb=verb, object_type=object_type,
                    object_id=object_id, **kw)


def answer(score, concept="t.a", level=1, ts="2026-09-01T10:00:00", **kw):
    return ev("answered", ts=ts, score=score, pending=score is None, nodes=(concept,),
              payload={"level": level, "response": "x", **kw})


# ---------- id ----------

@pytest.mark.parametrize("cls, good, bad", [
    (NodeId, "tools.shell.glob", ["tools", "Tools.x", "../x", ""]),
    (UnitId, "tools-01-shell", ["../x", "a/b", "Tools-01", "tools-01-shell/../../etc", ""]),
    (LessonRef, "tools/01-shell", ["tools", "../x/y", "a/b/c", ""]),
])
def test_ids_validate_on_construction(cls, good, bad):
    assert cls(good) == good
    for b in bad:
        with pytest.raises(InvalidId):
            cls(b)


# ---------- 证据信封 ----------

@pytest.mark.parametrize("e, msg", [
    (ev("teleported"), "没有注册"),
    (ev("answered", object_type="node", score=1.0, payload={"response": 1}), "对象只能是"),
    (ev("answered", actor=TUTOR, score=1.0, payload={"response": 1}), "只能由"),
    (ev("graded", actor=TUTOR, score=1.0, payload={"note": ""}), "caused_by"),
    (ev("answered", score=None, payload={"response": 1}), "pending"),           # 没分数却没标 pending
    (ev("answered", score=1.5, payload={"response": 1}), "0-1"),
    (ev("observed", "node", "t.a", actor=TUTOR, nodes=("Bad Id",), payload={"polarity": "weak", "note": ""}), "格式"),
    (ev("rated_load", "section", "u#0"), "缺少字段"),
])
def test_evidence_is_validated(e, msg):
    with pytest.raises(InvalidEvidence, match=msg):
        VerbRegistry().validate(e)


def test_spec_verbs_register_once():
    reg = VerbRegistry()
    v = Verb("ran_tests", "cs-practice", ("question",))
    reg.register(v)
    reg.register(v)                                    # 同一个定义注册两次没关系
    with pytest.raises(InvalidEvidence):
        reg.register(Verb("ran_tests", "other", ("question",)))


# ---------- 掌握度 ----------

def test_mastery_is_highest_passed_level_over_recent_window():
    evs = [answer(0.0, level=1, ts="2026-09-01T09:00:00")]       # 更早的错误在窗口外（每级只看最近 3 次）
    evs += [answer(1.0, level=1, ts=f"2026-09-01T10:0{i}:00") for i in range(3)]
    evs += [answer(1.0, level=2), answer(0.0, level=3)]
    s = mastery.concept_stats(evs, dt.date(2026, 9, 2))["t.a"]
    assert s["mastery"] == 2 and s["failing_levels"] == [3] and s["target_level"] == 3 and s["weak"]


def test_stale_after_two_weeks():
    evs = [answer(1.0)]
    assert not mastery.concept_stats(evs, dt.date(2026, 9, 10))["t.a"]["stale"]
    assert mastery.concept_stats(evs, dt.date(2026, 9, 20))["t.a"]["stale"]


def test_pending_counts_only_after_grading():
    a = answer(None, concept="t.b", level=2)
    assert mastery.concept_stats([a], dt.date(2026, 9, 2)) == {}
    assert mastery.scored([a])[1] == [a]
    g = ev("graded", actor=TUTOR, ts="2026-09-02T10:00:00", caused_by=a.id, score=0.5, payload={"note": "漏了要点"})
    items, pending = mastery.scored([a, g])
    assert pending == [] and items[0].score == 0.5 and items[0].level == 2 and items[0].note == "漏了要点"


def test_practice_summary_counts_runs_until_first_full_pass():
    runs = [ev("ran_tests", ts=f"2026-09-01T10:0{i}:00", score=p / 2, nodes=("t.code",),
               payload={"code": "", "passed": p, "total": 2}) for i, p in enumerate([0, 1, 2, 2])]
    s = mastery.practice_summary(runs)[0]
    assert (s["lesson"], s["qid"], s["runs"], s["first_full_pass_at_run"]) == ("t/01-x", "q1", 4, 3)


# ---------- 节点状态 ----------

def test_node_state_from_evidence():
    nodes = {"t.a": knowledge.Node("t.a", "A"), "t.b": knowledge.Node("t.b", "B", requires=["t.a"])}
    evs = [ev("observed", "node", "t.a", actor=TUTOR, nodes=("t.a",), payload={"polarity": "weak", "note": "跟不上"}),
           ev("voted_term", "node", "t.b", nodes=("t.b",), payload={"vote": "known"}),
           ev("answered", "checkpoint", "u#0.0", ts="2026-09-02T10:00:00", score=1.0, ok=True, nodes=("t.a",),
              payload={"idx": 0, "response": "B"})]
    sts = knowledge.derive_states(nodes, {}, knowledge.signals(evs))
    assert sts["t.a"].state == "mastered"                   # 最近一条有方向的证据说了算
    assert sts["t.b"].state == "mastered"
    evs.append(ev("observed", "node", "t.a", ts="2026-09-03T10:00:00", actor=TUTOR, nodes=("t.a",),
                  payload={"polarity": "weak", "note": "又卡住了"}))
    sts = knowledge.derive_states(nodes, {}, knowledge.signals(evs))
    assert sts["t.a"].state == "weak" and sts["t.a"].reasons == ["又卡住了（tutor）"]
    assert knowledge.known_titles(sts) == {"t.b", "b"}


def test_answers_override_with_mastery_rules_and_orphans_are_reported():
    nodes = {"t.a": knowledge.Node("t.a", "A")}
    evs = [answer(0.2, concept="t.a"), answer(1.0, concept="t.typo")]
    stats = mastery.concept_stats(evs, dt.date(2026, 9, 2))
    sts = knowledge.derive_states(nodes, stats, knowledge.signals(evs))
    assert sts["t.a"].state == "weak" and "第 1 级有错" in sts["t.a"].reasons
    assert knowledge.orphans(nodes, sts) == ["t.typo"]


def test_graph_validation():
    nodes = {"t.a": knowledge.Node("t.a", "A", requires=["t.b"]), "t.b": knowledge.Node("t.b", "B", requires=["t.a"]),
             "Bad": knowledge.Node("Bad", "x", kind="nope", requires=["t.zz"])}
    errors = "\n".join(knowledge.validate(nodes))
    assert "格式不对：Bad" in errors and "kind" in errors and "t.zz 不存在" in errors and "有环" in errors


# ---------- 课程计划、进度 ----------

def test_public_view_strips_secrets_without_touching_the_original():
    p = plan_v2()
    view = plan.public_view(p)
    assert not set(plan.SECRET_CHECKPOINT_KEYS) & set(plan.sections_of(view)[0]["checkpoint"][0])
    assert view["lab"] == {"story": "测试服务器出问题了", "files": 2}
    assert "answer" in plan.sections_of(p)[0]["checkpoint"][0]
    assert plan.plan_sessions(p, 45) == [[0, 1], [2]] and plan.plan_minutes(p) == 70


@pytest.mark.parametrize("section, event, extra", [
    (3, "open", {}), (-1, "open", {}), ("0", "open", {}), (True, "open", {}), (0, "hack", {}),
    (0, "checkpoint", {}),                                        # 服务器事件不能从页面发
    (0, "term", {"node": "tools.cmd.grep", "action": "known"}),   # grep 不是第 1 节的新词
    (0, "term", {"node": "tools.cmd.pwd", "action": "love"}),
    (0, "load_rating", {"rating": 9}), (0, "term_miss", {"text": ""}), (None, "activity", {"kind": "sleep"}),
])
def test_client_events_are_validated(section, event, extra):
    with pytest.raises(CourseError):
        plan.client_event(plan_v2(), "tools-01-shell", section, event, **extra)


def test_client_events_map_to_verbs():
    p = plan_v2()
    assert plan.client_event(p, "u", None, "activity", kind="video").verb == "pinged"
    done = plan.client_event(p, "u", 0, "done", minutes=9999)
    assert (done.verb, done.object_id, done.payload) == ("completed", "u#0", {"minutes": 600.0})
    vote = plan.client_event(p, "u", 0, "term", node="tools.cmd.pwd", action="known")
    assert (vote.verb, vote.nodes, vote.payload) == ("voted_term", ("tools.cmd.pwd",), {"vote": "known"})


def cp(section, idx, ok, ts="2026-09-26T10:00:00"):
    return ev("answered", "checkpoint", f"u#{section}.{idx}", ts=ts, section=section, score=float(ok), ok=ok,
              payload={"idx": idx, "response": "x"})


def test_checkpoint_outcome_credits_terms_only_on_first_pass():
    p = plan_v2()
    first = progress.checkpoint_outcome(p, progress.project(p, "run-2", [], {}), 0, 0, True)
    assert first == {"attempt": 1, "section_passed": False, "first_pass": False, "terms": []}
    evs = [cp(0, 0, True)]
    second = progress.checkpoint_outcome(p, progress.project(p, "run-2", evs, {}), 0, 1, True)
    assert second["first_pass"] and second["terms"] == ["tools.cmd.pwd", "tools.cmd.cd"]
    evs.append(cp(0, 1, True))
    again = progress.checkpoint_outcome(p, progress.project(p, "run-2", evs, {}), 0, 1, True)
    assert again["section_passed"] and not again["first_pass"] and again["attempt"] == 2


def test_progress_projection():
    p = plan_v2()
    evs = [cp(0, 0, False), cp(0, 0, True), cp(0, 1, True),
           ev("requested_hint", "checkpoint", "u#0.0", section=0, payload={"idx": 0, "level": 2}),
           ev("skipped", "section", "u#2", section=2), ev("rated_load", "section", "u#0", section=0, payload={"rating": 4}),
           ev("completed", "section", "u#2", section=2)]
    r = progress.project(p, "run-2", evs, {0: 12.04})
    assert r["passed"] == [0, 2] and r["skipped"] == [] and r["ratings"] == {"0": 4}
    assert r["checkpoints"]["0"]["0"] == {"ok": True, "attempts": 2, "first_try": False, "hints": 2}
    assert r["spent_minutes"] == {"0": 12.0} and r["last_section"] == 2


def test_predicted_load_drops_as_terms_become_known():
    assert [x["new_terms"] for x in progress.predicted_load(plan_v2(), set())] == [2, 1, 0]
    assert progress.predicted_load(plan_v2(), {"tools.cmd.pwd", "cd"})[0] == {"new_terms": 0, "per_10min": 0.0, "level": "低"}


# ---------- 检查点判分 ----------

def test_checkpoint_graders_show_traps_but_not_answers():
    from tests.conftest import CHOICE, FILL
    wrong = grade_choice_checkpoint(CHOICE, ["A"])
    assert wrong.score == 0 and wrong.trap["cause"] == "和 cd - 混了" and wrong.feedback == []
    assert grade_fill_checkpoint(FILL, ["."]).trap["fix"] == "用 .."
    assert grade_fill_checkpoint(FILL, [".."]).score == 1.0


@pytest.mark.parametrize("multi,answer_,response,expected", [
    (False, "B", ["b"], 1.0), (False, "B", ["A"], 0.0),
    (True, ["A", "C"], ["C", "A"], 1.0), (True, ["A", "C"], ["A"], 0.5),
    (True, ["A", "C"], ["A", "B"], 0.0), (True, ["A", "C"], ["A", "B", "C", "D"], 0.0),   # 全选拿不到分
])
def test_choice(multi, answer_, response, expected):
    choice = CORE_CHECKERS[0]
    assert choice.check({"options": list("abcd"), "multi": multi}, {"answer": answer_}, None, response).score == expected


def test_fill_blanks_and_regex():
    fill = CORE_CHECKERS[1]
    assert fill.view({"prompt": "记忆、____、应用、____"}, None) == {"blanks": 2}
    assert fill.check({}, {"blanks": [["理解"], ["分析"]]}, None, [" 理解 ", "综合"]).score == 0.5
    assert fill.check({}, {"accept": [r"o\(n\^?2\)"], "regex": True}, None, ["O(n^2)"]).score == 1.0


# ---------- 学习时长 ----------

def test_sessions_split_on_gaps_and_only_count_the_learner():
    evs = [ev("opened", "section", "u#0", ts="2026-09-26T10:00:00", unit="u", section=0, plan="p"),
           ev("pinged", "section", "u#0", ts="2026-09-26T10:12:00", unit="u", section=0, plan="p", payload={"kind": "video"}),
           ev("opened", "section", "u#1", ts="2026-09-26T10:20:00", unit="u", section=1, plan="p"),
           ev("opened", "section", "u#1", ts="2026-09-26T11:00:00", unit="u", section=1, plan="p"),
           ev("observed", "node", "t.a", ts="2026-09-26T10:30:00", actor=TUTOR, nodes=("t.a",),
              payload={"polarity": "weak", "note": ""}),                              # 导师的观察不是学习时间
           ev("logged_time", "time", "x", payload={"start": "2026-09-25T20:00:00", "minutes": 48, "note": "看视频"})]
    ticks, manual = timeline.ticks(evs)
    sess = timeline.sessions(ticks, manual)
    assert [round(s.minutes) for s in sess] == [48, 21, 1]
    assert timeline.section_minutes(ticks, "u", "p") == {0: 20.0, 1: 2.0}
    assert "2026-09-25  合计 48 分钟" in timeline.report(sess)


# ---------- 课程评测 ----------

def test_spearman():
    assert course_eval.spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert course_eval.spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert course_eval.spearman([1, 2], [1, 2]) is None


# ---------- 计划检查 ----------

def test_url_extraction_stops_at_chinese_punctuation():
    assert plan_check.extract_urls("见（https://a.com/x）。和「https://b.com/y」，") == {"https://a.com/x", "https://b.com/y"}
    assert plan_check.url_key("https://a.com/x/#frag") == "https://a.com/x"


# ---------- harness ----------

def test_variant_hash_and_diff():
    a = Variant("tutor-prep", {"system_prompt": "1", "context": "2", "model": "m"})
    b = Variant("tutor-prep", {"system_prompt": "1", "context": "3", "model": "m"})
    assert a.id != b.id and a.id == Variant("tutor-prep", dict(a.parts)).id
    assert b.diff(a) == ["context"]


def test_parse_judge_validates_scores():
    rubric = "- `teaches` x\n- `grounded` y\n"
    assert rubric_ids(rubric) == ["teaches", "grounded"]
    ok = parse_judge('噪音 {"scores": {"teaches": {"score": 4}, "grounded": {"score": 5}}} 噪音', ["teaches", "grounded"])
    assert ok["avg"] == 4.5
    with pytest.raises(ValueError):
        parse_judge('{"scores": {"teaches": {"score": 9}}}', ["teaches"])


def test_claims_are_checked_against_pages_the_agent_read():
    claims = [{"claim": "a", "quote": "用 `git bisect run` 自动找"}, {"claim": "b", "quote": "Exercise 17 讲 awk"}]
    out = verify_claims(claims, "Use git  bisect run to automate. Exercises 1-12.")
    assert [c["found_in_pages"] for c in out] == [True, False]
