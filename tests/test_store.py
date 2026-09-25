import datetime as dt

from studykit import store

TODAY = dt.date(2026, 9, 25)


def att(concept="os.sync.race", level=1, score=1.0, ts="2026-09-20T10:00:00", **kw):
    rec = {"id": kw.pop("id", f"{ts}-{level}-{score}"), "ts": ts, "quiz": "os/03", "qid": "q1",
           "concept": concept, "level": level, "score": score,
           "result": "pending" if score is None else store.result_of(score)}
    rec.update(kw)
    return rec


def test_mastery_is_highest_passed_level():
    s = store.concept_stats([att(level=1), att(level=2, ts="2026-09-21T00:00:00")], TODAY)["os.sync.race"]
    assert (s["mastery"], s["target_level"], s["weak"]) == (2, 3, False)


def test_only_recent_window_counts():
    old_fails = [att(score=0, ts=f"2026-09-1{i}T00:00:00") for i in range(3)]
    recent_passes = [att(score=1, ts=f"2026-09-2{i}T00:00:00") for i in range(3)]
    assert store.concept_stats(old_fails + recent_passes, TODAY)["os.sync.race"]["mastery"] == 1


def test_failing_lower_level_marks_weak_and_targets_it():
    s = store.concept_stats([att(level=3, score=1), att(level=1, score=0, ts="2026-09-21T00:00:00")],
                            TODAY)["os.sync.race"]
    assert s["mastery"] == 3
    assert s["failing_levels"] == [1]
    assert s["target_level"] == 1
    assert s["weak"]


def test_stale_after_two_weeks():
    s = store.concept_stats([att(ts="2026-09-01T00:00:00")], TODAY)["os.sync.race"]
    assert s["stale"] and s["weak"]


def test_pending_is_ignored_until_graded():
    pending = att(level=2, score=None, id="p1")
    assert store.concept_stats([pending], TODAY) == {}
    graded = att(level=2, score=0.5, id="g1", supersedes="p1", ts="2026-09-21T00:00:00")
    _, still_pending = store.split_attempts([pending, graded])
    assert still_pending == []
    assert store.concept_stats([pending, graded], TODAY)["os.sync.race"]["failing_levels"] == [2]


def test_runs_command_shows_failures_and_code_changes(env, capsys):
    import study
    fail = {"name": "test_empty", "outcome": "failed", "message": "ZeroDivisionError", "lines": [3]}
    store.record_run(quiz="t/01-x", qid="q4", kind="run", code="a = 1\n", passed=1, total=2, failures=[fail])
    store.record_run(quiz="t/01-x", qid="q4", kind="submit", code="a = 2\n", passed=2, total=2, failures=[])
    study.main(["runs", "t/01-x", "q4"])
    out = capsys.readouterr().out
    assert "#1" in out and "通过 1/2" in out and "test_empty（第 3 行）" in out
    assert "#2" in out and "提交" in out and "-a = 1" in out and "+a = 2" in out


def test_progress_md_lists_concepts_and_pending(env):
    store.record(quiz="t/01-x", qid="q1", concept="os.sync.race", level=1, score=0.0, result="fail")
    store.record(quiz="t/01-x", qid="q2", concept="os.sync.race", level=2, score=None, result="pending")
    store.write_progress_md()
    md = store.PROGRESS_MD.read_text("utf-8")
    assert "`os.sync.race`" in md and "第 1 级有错" in md
    assert "t/01-x q2" in md
