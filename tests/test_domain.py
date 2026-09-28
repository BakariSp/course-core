"""领域层：纯函数，不碰文件和数据库。"""
import datetime as dt

import pytest

from studykit.domain import artifact, course_eval, curriculum, knowledge, mastery, plan, plan_check, profile, progress, timeline
from studykit.domain.assessment import (CORE_CHECKERS, grade_choice_checkpoint, grade_fill_checkpoint, rubric_units,
                                        score_parts)
from studykit.domain.errors import CourseError, InvalidEvidence, InvalidId
from studykit.domain.evidence import Actor, Evidence, Verb, VerbRegistry, learner_actor
from studykit.domain.harness import (Run, Variant, assemble_prompt, parse_judge, part_diffs, prep_stage,
                                     rubric_ids, verify_claims)
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
    nodes = {"t.a": knowledge.Node("t.a", "A"), "t.b": knowledge.Node("t.b", "B", [knowledge.Edge("t.a")])}
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
    E = knowledge.Edge
    nodes = {"t.a": knowledge.Node("t.a", "A", edges=[E("t.b")]), "t.b": knowledge.Node("t.b", "B", edges=[E("t.a", "helpful")]),
             "Bad": knowledge.Node("Bad", "x", kind="nope", edges=[E("t.zz"), E("t.a", "maybe")])}
    errors = "\n".join(knowledge.validate(nodes))
    assert "格式不对：Bad" in errors and "kind" in errors and "t.zz 不存在" in errors
    assert "有环" in errors                                  # 更好的先修也算进环（D-047）
    assert "t.a 的 kind 只能是 required/helpful" in errors
    assert nodes["t.a"].requires == ["t.b"] and nodes["t.b"].requires == [] and nodes["t.b"].prerequisites == ["t.a"]


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
    (0, "resume", {"away_start": "昨天", "counted": True}),                       # 暂停后回来（D-027）
    (0, "resume", {"away_start": "2026-09-26T10:05:00", "counted": "yes"}),
])
def test_client_events_are_validated(section, event, extra):
    with pytest.raises(CourseError):
        plan.client_event(plan_v2(), "tools-01-shell", section, event, **extra)


def test_client_events_map_to_verbs():
    p = plan_v2()
    assert plan.client_event(p, "u", None, "activity", kind="video").verb == "pinged"
    done = plan.client_event(p, "u", 0, "done", minutes=9999)
    assert (done.verb, done.object_id, done.payload) == ("completed", "u#0", {"minutes": 600.0})
    back = plan.client_event(p, "u", 0, "resume", away_start="2026-09-26T10:05:00", counted=False)
    assert (back.verb, back.object_id, back.payload) == ("resumed", "u#0", {"away_start": "2026-09-26T10:05:00", "counted": False})
    assert plan.client_event(p, "u", None, "resume", away_start="2026-09-26T10:05:00", counted=True).object_type == "unit"
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
    assert r["checkpoints"]["0"]["0"] == {"ok": True, "attempts": 2, "first_try": False, "hints": 2, "response": "x"}
    assert "response" not in r["checkpoints"]["0"].get("9", {})   # 没作答过的题没有 response
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


# ---------- 得分点（D-056 第 1 步）：每个选项 / 空 / 评分点 / 检查项挂知识点和误解 ----------

NET_Q = {"id": "q1", "checker": "choice", "concept": "net.internet", "level": 1, "multi": True, "options": list("abcd")}
NET_KEY = {"answer": ["A", "D"], "parts": [
    {"concept": "net.internet"}, {"concept": "net.internet", "misconception": "composition_vs_usage"},
    {"concept": "net.internet", "misconception": "composition_vs_usage"}, {"concept": "net.api", "level": 2}]}


def test_choice_scores_each_option_and_tags_the_misconception_of_the_wrong_ones():
    v = CORE_CHECKERS[0].check(NET_Q, NET_KEY, None, ["A", "C"])
    assert v.score == 0.0 and v.units == [1.0, 1.0, 0.0, 0.0]        # A 选对、B 没选对、C 选错、D 漏选
    assert score_parts(NET_Q, NET_KEY, v.units) == [
        {"concept": "net.internet", "level": 1, "score": 1.0},
        {"concept": "net.internet", "level": 1, "score": 1.0},
        {"concept": "net.internet", "level": 1, "score": 0.0, "misconception": "composition_vs_usage"},
        {"concept": "net.api", "level": 2, "score": 0.0}]


def test_fill_rubric_and_untagged_questions():
    q = {"concept": "net.delay", "level": 2}
    key = {"blanks": [["0.4"], ["15"]], "parts": [{"concept": "net.tx"}, {"concept": "net.prop", "misconception": "unit"}]}
    v = CORE_CHECKERS[1].check(q, key, None, ["0.4", "0.0067"])
    assert v.units == [1.0, 0.0]
    assert score_parts(q, key, v.units)[1] == {"concept": "net.prop", "level": 2, "score": 0.0, "misconception": "unit"}
    # 简答题：每条评分点按"得分 / 满分"；加分项没拿到就不算这一个得分点
    rkey = {"rubric": ["a（0.5）", "b（0.5）", "加分"], "parts": [{"concept": "net.a"}, {"concept": "net.b"}, {"concept": "net.c"}]}
    units = rubric_units(rkey, [{"id": 1, "score": 0.5}, {"id": 2, "score": 0.1}])
    assert units == [1.0, 0.2, None]
    assert [p["concept"] for p in score_parts(q, rkey, units)] == ["net.a", "net.b"]
    assert score_parts(q, {"blanks": [["x"]]}, [1.0]) == []                    # 没标得分点：照旧按整题
    assert score_parts(q, key, [1.0]) == []                                   # 个数对不上：不猜


def test_mastery_reads_each_scoring_point_when_the_answer_has_them():
    parts = [{"concept": "net.internet", "level": 1, "score": 1.0}, {"concept": "net.internet", "level": 1, "score": 0.0,
             "misconception": "composition_vs_usage"}, {"concept": "net.api", "level": 2, "score": 1.0}]
    a = ev("answered", score=0.0, nodes=("net.internet", "net.api"), payload={"level": 1, "response": "x", "parts": parts})
    stats = mastery.concept_stats([a], dt.date(2026, 9, 1))
    assert stats["net.api"]["mastery"] == 2 and stats["net.internet"]["failing_levels"] == [1]
    assert stats["net.internet"]["recent_mistakes"][0]["misconceptions"] == ["composition_vs_usage"]
    # 简答题：得分点在批改里
    p = answer(None, concept="net.x", level=4)
    g = ev("graded", actor=TUTOR, score=0.5, caused_by=p.id, nodes=("net.x", "net.y"), payload={"note": "", "parts": [
        {"concept": "net.x", "level": 4, "score": 1.0}, {"concept": "net.y", "level": 4, "score": 0.0}]})
    stats = mastery.concept_stats([p, g], dt.date(2026, 9, 1))
    assert stats["net.x"]["mastery"] == 4 and stats["net.y"]["failing_levels"] == [4]


def test_misconceptions_are_facts_on_the_node():
    nodes = {"t.a": knowledge.Node("t.a", "A", misconceptions={"mixes_up": "把 x 当成 y", "Bad Id": "x"})}
    assert "误解 id 格式不对：t.a/Bad Id" in "\n".join(knowledge.validate(nodes))


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


def test_pause_answer_decides_whether_the_away_time_counts():
    """D-027：5 分钟没操作就暂停；回来时学习者说这段在学（算，哪怕超过 GAP）或没在学（不算，并切成两次）。"""
    at = lambda hm, verb="pinged", **kw: ev(verb, "section", "u#0", ts=f"2026-09-26T{hm}:00", unit="u", section=0,  # noqa: E731
                                            plan="p", **kw)
    studied = [at("10:00", "opened"), at("10:04", payload={"kind": "page"}),
               at("10:50", "resumed", payload={"away_start": "2026-09-26T10:05:00", "counted": True})]
    idle = [at("14:00", "opened"), at("14:04", payload={"kind": "page"}),
            at("14:06", payload={"kind": "page"}),                                  # 暂停前那几分钟的心跳也不算
            at("14:12", "resumed", payload={"away_start": "2026-09-26T14:05:00", "counted": False}),
            at("14:15", payload={"kind": "page"})]
    ticks, manual = timeline.ticks(studied + idle)
    sess = timeline.sessions(ticks, manual)
    assert [(s.start.strftime("%H:%M"), round(s.minutes)) for s in sess] == [("10:00", 51), ("14:00", 5), ("14:12", 4)]
    assert timeline.section_minutes(ticks, "u", "p") == {0: 60.0}


def test_journal_lists_what_each_session_produced():
    at = lambda hm: f"2026-09-26T{hm}:00"  # noqa: E731
    evs = [ev("opened", "section", "u#0", ts=at("10:00"), unit="u", section=0, plan="p"),
           ev("answered", "checkpoint", "u#0.0", ts=at("10:05"), unit="u", section=0, plan="p", score=0.0, ok=False,
              payload={"idx": 0, "response": "x"}),
           ev("answered", "checkpoint", "u#0.0", ts=at("10:06"), unit="u", section=0, plan="p", score=1.0, ok=True,
              payload={"idx": 0, "response": "y"}),
           ev("passed_section", "section", "u#0", ts=at("10:06"), actor=Actor("system", "system"), unit="u", section=0,
              plan="p", nodes=("tools.cmd.pwd",)),
           ev("passed_section", "section", "u#0", ts=at("10:07"), actor=Actor("system", "system"), unit="u", section=0,
              plan="p2", nodes=("tools.cmd.pwd",)),                               # 同一个词在两版课程里都学到：只列一次
           answer(0.5, ts=at("10:10")),
           ev("opened", "section", "u#1", ts=at("20:00"), unit="u", section=1, plan="p")]
    [day] = timeline.journal(evs)
    first, second = day["sessions"]
    assert day["date"] == "2026-09-26" and day["minutes"] == 12
    assert first["passed"] == ["u 第 1 节"] and first["terms"] == ["tools.cmd.pwd"]
    assert first["checkpoints"] == {"tried": 1, "ok": 1, "attempts": 2}
    assert first["questions"] == [{"id": "t/01-x#q1", "score": 0.5}]
    assert second["passed"] == [] and second["checkpoints"] == {"tried": 0, "ok": 0, "attempts": 0}


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


# ---------- 简答题的 LLM 批改（D-031） ----------

KEY = {"rubric": ["cd 失败不停（0.5）", "glob 提前展开 (0.5)", "加分项：成功提示不可信"]}


def test_rubric_weights_and_bonus_items():
    from studykit.domain.assessment import rubric_items
    assert [it["max"] for it in rubric_items(KEY)] == [0.5, 0.5, None]


def test_parse_short_grading_sums_and_caps():
    from studykit.domain.assessment import parse_short_grading
    r = parse_short_grading('好的：{"items": [{"id": 1, "score": 0.5, "why": "a"}, {"id": 2, "score": 0.5}, '
                            '{"id": 3, "score": 0.3}], "feedback": "f"}', KEY)
    assert r["score"] == 1.0 and r["feedback"] == "f" and len(r["items"]) == 3


@pytest.mark.parametrize("reply", [
    "不是 JSON",
    '{"items": [{"id": 1, "score": 0.5}]}',                                   # 缺第 2 条
    '{"items": [{"id": 1, "score": 0.9}, {"id": 2, "score": 0}]}',            # 超过这一条的满分
    '{"items": [{"id": 1, "score": 0.5}, {"id": 2, "score": 0}, {"id": 7, "score": 0}]}',   # 没有第 7 条
])
def test_parse_short_grading_rejects_bad_replies(reply):
    from studykit.domain.assessment import parse_short_grading
    with pytest.raises(ValueError):
        parse_short_grading(reply, KEY)


def test_later_grade_replaces_earlier_one():
    a = answer(None, concept="t.b", level=4)
    llm = ev("graded", actor=Actor("agent", "short-grader", "grader"), caused_by=a.id, score=0.2, payload={"note": ""})
    tutor = ev("graded", actor=TUTOR, caused_by=a.id, score=0.8, payload={"note": ""})
    items, pending = mastery.scored([a, llm, tutor])
    assert [s.score for s in items] == [0.8] and pending == []


# ---------- 备课阶段（D-032） ----------

def _run(rid, submitted=True):
    return Run(rid, "tutor-prep", "v", "u", "me", "batch", rid, {}, submitted=submitted)


# 各阶段的判断见 tests/test_prep.py（PRD_V2：备课全程不经过导师）


def test_prep_stage_follows_the_latest_run_and_notes_the_older_published_one():
    runs = [_run("20260926-100000-u"), _run("20260926-120000-u", submitted=False)]
    s = prep_stage(runs, {}, "20260926-100000-u", False)
    assert s["stage"] == "interrupted" and s["run"] == "20260926-120000-u"
    assert s["published_run"] == "20260926-100000-u" and s["behind"]            # 学习者在用的是旧的那一版


# ---------- prompt 版本（D-033） ----------

def test_prompt_parts_assemble_verbatim_and_versions_diff_part_by_part():
    assert assemble_prompt([("role", "你是助教\n"), ("rules", "# 规则\n- 一\n")]) == "你是助教\n# 规则\n- 一\n"
    blobs = {"r1": "# 规则\n- 一\n", "r2": "# 规则\n- 一\n- 二\n", "x": "你是助教\n"}
    a = Variant("t", {"prompt:role": "x", "prompt:rules": "r1", "model": "m1", "tools": "gone"})
    b = Variant("t", {"prompt:role": "x", "prompt:rules": "r2", "model": "m2", "prompt:examples": "x", "tools": "gone2"})
    d = {x["part"]: x for x in part_diffs(a, b, blobs.get)}
    assert set(d) == {"prompt:rules", "model", "prompt:examples", "tools"}           # 没变的部件不列
    assert d["prompt:rules"]["change"] == "changed" and "+- 二" in d["prompt:rules"]["diff"]
    assert d["model"]["diff"] == "- m1\n+ m2"                                        # 不是存下来的文本：直接比值
    assert d["prompt:examples"]["change"] == "added"
    assert "没有存下来" in d["tools"]["diff"]                                           # D-033 之前的版本只有哈希


# ---------- 产出循环：带地址的发现（D-035） ----------

def test_finding_addresses_point_into_the_artifact():
    assert artifact.section_address(4) == "/sections/4"
    assert artifact.checkpoint_address(4, 1) == "/sections/4/checkpoint/1"
    assert artifact.within("/sections/4/checkpoint/1", "/sections/4")
    assert artifact.within("/sections/4", "")                                # 空地址 = 整份产出物
    assert not artifact.within("/sections/40", "/sections/4")                # 不能按字符串前缀误判
    f = artifact.Finding("/sections/0", "minutes 太大", "plan_check", evidence="60 > 45")
    assert artifact.Finding.from_dict(f.as_dict()) == f and f.severity == "block"
    with pytest.raises(ValueError):
        artifact.Finding("/x", "y", "z", severity="maybe")


# ---------- 课程定义、学习者资料（D-047） ----------

COURSE = {
    "schema_version": 2, "goal": "能验证 AI 代码",
    "stages": [{"id": "verify", "title": "能验证", "weeks": [1, 8], "pass": "修一个 bug"}],
    "subjects": [{"id": "tools", "title": "开发工具", "priority": "P0", "stage": "verify", "goal": "会 bisect",
                  "sources": [{"id": "ms", "title": "Missing Semester", "url": "https://www.missing.csail.mit.edu/2026/", "kind": "course"}],
                  "project_links": [{"where": "atomic_write_text", "concept": "原子写"}],
                  "units": [{"id": "tools-01-shell", "title": "Shell", "requests": ["零基础"],
                             "sources": [{"ref": "ms", "url": "https://missing.csail.mit.edu/2026/course-shell/", "note": "第 1 讲"}]},
                            {"id": "tools-02-git", "title": "Git"}]},
                 {"id": "dist", "title": "分布式", "units": [{"id": "dist-01", "title": "Kleppmann", "scope": "open"}]}],
}


def test_course_definition_is_structured_and_ordered():
    c = curriculum.parse_course(COURSE)
    assert c.unit_ids() == ["tools-01-shell", "tools-02-git", "dist-01"]
    s, u = c.find("tools-01-shell")
    assert (s.id, u.requests, u.scope_open) == ("tools", ("零基础",), False) and c.find("dist-01")[1].scope_open
    assert c.hosts() == {"missing.csail.mit.edu"}                          # 白名单跟着材料走，www. 去掉
    brief = curriculum.render_for_brief(c, "tools-01-shell")
    assert "会 bisect" in brief and "能验证" in brief and "course-shell" in brief and "第 1 讲" in brief and "atomic_write_text" in brief
    with pytest.raises(curriculum.CourseDefError):
        c.find("tools-99-x")


DEST = [{"id": "C1", "can": "读路径", "accept": "走一遍提交答案", "units": ["tools-02-git"]}]
phase = lambda title, *units, fills=(): {"title": title, "fills": list(fills), "units": list(units)}  # noqa: E731


def test_path_sets_learning_order_across_subjects_and_destination_names_capabilities():
    """D-048、D-064：终点能力 + 分阶段的路线。路线上的单元按阶段顺序在前，没进路线的按学科顺序排在后面。"""
    c = curriculum.parse_course({**COURSE, "destination": DEST,
                                 "path": [phase("先分布式", "dist-01", fills=["C1"]), phase("再 shell", "tools-01-shell")]})
    assert c.unit_ids() == ["dist-01", "tools-01-shell", "tools-02-git"]
    assert c.path == ("dist-01", "tools-01-shell")                         # 各阶段首尾相接
    assert c.phases[0] == curriculum.Phase("先分布式", ("dist-01",), ("C1",))
    assert c.destination == (curriculum.Capability("C1", "读路径", "走一遍提交答案", ("tools-02-git",)),)
    assert c.serves("tools-02-git") == ["C1"] and c.serves("dist-01") == []
    assert curriculum.parse_course(COURSE).path == ()                       # 没写路线 = 学科顺序


def test_current_phase_is_the_first_with_an_unfinished_unit():
    ps = (curriculum.Phase("a", ("u1", "u2")), curriculum.Phase("b", ("u3",)))
    assert curriculum.current_phase(ps, {"u1": "done", "u2": "learning", "u3": "learning"}) == 0   # 后面阶段在学的不算
    assert curriculum.current_phase(ps, {"u1": "done", "u2": "done"}) == 1
    assert curriculum.current_phase(ps, {"u1": "done", "u2": "done", "u3": "done"}) is None


@pytest.mark.parametrize("patch, msg", [
    ({"path": [phase("a", "tools-99-x")]}, "路径"),
    ({"path": [phase("a", "tools-01-shell"), phase("b", "tools-01-shell")]}, "路径"),   # 跨阶段也不能重复
    ({"path": ["tools-01-shell"]}, "阶段"),                                              # 旧格式（单元列表）要改成阶段
    ({"path": [phase("a")]}, "没有单元"),
    ({"path": [{"units": ["tools-01-shell"]}]}, "title"),
    ({"path": [phase("a", "tools-01-shell", fills=["C9"])]}, "fills"),
    ({"destination": [{"id": "C1", "can": "x", "accept": "y", "units": ["nope-01-x"]}]}, "C1"),
    ({"destination": [{"id": "C1", "can": "x", "accept": "y"}, {"id": "C1", "can": "z", "accept": "w"}]}, "重复"),
    ({"destination": [{"id": "C1", "can": "x"}]}, "accept"),
])
def test_path_and_destination_reject_mistakes(patch, msg):
    with pytest.raises(curriculum.CourseDefError, match=msg):
        curriculum.parse_course({**COURSE, **patch})


@pytest.mark.parametrize("patch, msg", [
    ({"schema_version": 1}, "schema_version"),
    ({"subjects": [{"id": "a", "units": [{"id": "a-01-x"}, {"id": "a-01-x"}]}]}, "单元 id 重复"),
    ({"subjects": [{"id": "a", "units": [{"id": "a-01-x", "sources": [{"ref": "nope"}]}]}]}, "ref"),
    ({"subjects": [{"id": "a", "stage": "later", "units": []}]}, "stage"),
    ({"subjects": [{"id": "a", "units": [{"id": "a-01-x", "scope": "maybe"}]}]}, "scope"),
])
def test_course_definition_rejects_mistakes(patch, msg):
    with pytest.raises(curriculum.CourseDefError, match=msg):
        curriculum.parse_course({**COURSE, **patch})


@pytest.mark.parametrize("kw, key, tags", [
    (dict(scope_open=True, prep_stage="todo", passed=None, sections=None), "ask", []),
    (dict(scope_open=False, prep_stage="todo", passed=None, sections=None), "queued", []),
    (dict(scope_open=False, prep_stage="preparing", passed=None, sections=None), "preparing", []),
    (dict(scope_open=False, prep_stage="escalated", passed=None, sections=None), "blocked", []),
    (dict(scope_open=False, prep_stage="interrupted", passed=None, sections=None), "blocked", []),
    (dict(scope_open=False, prep_stage="published", passed=0, sections=7), "ready", []),
    (dict(scope_open=False, prep_stage="ready", passed=6, sections=7), "learning", ["new_version"]),
    (dict(scope_open=False, prep_stage="published", passed=7, sections=7, quiz_answered=0, quiz_questions=8), "quiz", []),
    (dict(scope_open=False, prep_stage="published", passed=7, sections=7, quiz_answered=8, quiz_questions=8), "done", []),
    (dict(scope_open=False, prep_stage="preparing", passed=2, sections=7), "learning", []),   # 能学的单元在备新版本：还是能学
])
def test_unit_state_merges_prep_and_learning(kw, key, tags):
    assert curriculum.unit_state(**kw) == {"key": key, "tags": tags}


def test_next_steps_finish_what_you_started_then_quiz_then_start():
    st = lambda k: {"key": k, "tags": []}  # noqa: E731
    units = [{"id": "a", "state": st("done")}, {"id": "b", "state": st("quiz")}, {"id": "c", "state": st("learning")},
             {"id": "d", "state": st("ready")}]
    assert [(n["unit"], n["kind"]) for n in curriculum.next_steps(units)] == [("c", "continue"), ("b", "quiz")]
    assert all(n["reason"] for n in curriculum.next_steps(units))
    assert [n["unit"] for n in curriculum.next_steps([{"id": "d", "state": st("ready")}])] == ["d"]
    assert curriculum.queued_after(["a", "b"], "b") == "a" and curriculum.queued_after(["a"], "a") is None


def test_profile_only_has_what_the_learner_wrote():
    p = profile.parse_profile({"schema_version": 1, "about": {"identity": "产品经理", "goal": "验证 AI 代码"}, "time": {"session_minutes": 30}})
    assert p.time == {"session_minutes": 30, "unit_budget_minutes": 180, "max_new_terms": 5}
    assert p.render() == "- 身份：产品经理\n- 学习目标：验证 AI 代码"
    with pytest.raises(profile.ProfileError, match="不认识"):
        profile.parse_profile({"schema_version": 1, "about": {"observations": "他会……"}})


def test_taught_nodes_come_from_plan_nodes_and_section_terms():
    p = plan_v2()
    p["nodes"] = [{"id": "tools.cmd.grep"}, {"id": "tools.shell.x"}]
    assert plan.taught_nodes(p) == ["tools.cmd.grep", "tools.shell.x", "tools.cmd.pwd", "tools.cmd.cd"]



# ---------- 路线上学过的、回顾（D-051） ----------

def _state(nid, st):
    return knowledge.NodeState(knowledge.Node(nid, nid.split(".")[-1], "", "term"), st, [])


def test_studied_is_what_earlier_units_taught_minus_mastered_and_weak():
    states = {"tools.cmd.ls": _state("tools.cmd.ls", "learning"), "tools.cmd.cd": _state("tools.cmd.cd", "mastered"),
              "tools.cmd.rm": _state("tools.cmd.rm", "weak")}
    taught_by = {"tools.cmd.ls": ["tools-01-shell"], "tools.cmd.cd": ["tools-01-shell"], "tools.cmd.rm": ["tools-01-shell"],
                 "tools.cmd.cat": ["tools-01-shell"], "git.commit": ["tools-02-git"]}
    assert knowledge.studied(states, taught_by, ["tools-01-shell"]) == ["tools.cmd.ls", "tools.cmd.cat"]  # 按教的顺序；没证据的也算学过


def test_recall_rules_and_studied_terms_are_not_new():
    from tests.conftest import plan_v2
    limits = plan_check.PlanLimits("tools-02-git", max_new_terms=1, studied={"tools.cmd.cd", "tools.cmd.ls"},
                                   existing_nodes={"tools.cmd.cd": "cd", "tools.cmd.ls": "ls"})
    p = plan_v2()
    s0 = p["parts"][0]["sections"][0]                      # 两个词：pwd（新）、cd（学过）→ 新词只算 1 个
    at0 = lambda: [f.what for f in plan_check.plan_findings(p, limits) if f.address == "/sections/0"]  # noqa: E731
    assert not any("新词" in w and "超过" in w for w in at0())
    s0["recall"] = [{"id": "tools.cmd.ls", "term": "ls", "explain": "列出目录里的文件名"}]
    assert not any("回顾" in w for w in at0())
    s0["recall"] = [{"id": "tools.cmd.pwd", "term": "pwd", "explain": "x"}]
    assert any("回顾" in w and "学过" in w for w in at0())
    s0["recall"] = [{"id": "tools.cmd.ls", "term": "ls", "explain": "x"}] * 3
    assert any("回顾" in w and "2" in w for w in at0())
    s0["recall"] = [{"id": "tools.cmd.ls", "term": "ls"}]
    assert any("回顾" in w and "explain" in w for w in at0())


def test_recall_belongs_to_the_outline_and_is_recorded_as_evidence():
    from studykit.domain.plan import client_event, fill_stub
    stub = {"title": "t", "minutes": 10, "goal": "g", "mission": "m", "terms": [],
            "recall": [{"id": "tools.cmd.ls", "term": "ls", "explain": "列文件"}]}
    assert fill_stub(stub, {"explain": "e", "recall": []})["recall"] == stub["recall"]       # 写节的人改不了
    assert "recall" not in plan_check.section_schema({"choice": ""}, {}, {})["properties"]
    plan = {"parts": [{"title": "P", "sections": [{**stub, "explain": "e"}]}]}
    ev = client_event(plan, "tools-02-git", 0, "recall", node="tools.cmd.ls", remembered=False)
    assert (ev.verb, ev.object_id, ev.nodes, ev.payload) == ("recalled", "tools.cmd.ls", ("tools.cmd.ls",), {"remembered": False})
    with pytest.raises(CourseError):
        client_event(plan, "tools-02-git", 0, "recall", node="tools.cmd.pwd", remembered=True)
    with pytest.raises(CourseError):
        client_event(plan, "tools-02-git", 0, "recall", node="tools.cmd.ls", remembered="yes")
