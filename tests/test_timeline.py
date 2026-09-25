import datetime as dt

from studykit import store, timeline
from studykit.timeline import Event


def T(hm):
    return dt.datetime.fromisoformat(f"2026-09-25T{hm}:00")


def test_gap_splits_sessions_and_tail_is_counted():
    evs = [Event(T("16:00"), "a"), Event(T("16:10"), "a"), Event(T("16:40"), "b")]   # 30 分钟空档 > 15
    ss = timeline.sessions(evs)
    assert [(s.start.strftime("%H:%M"), round(s.minutes)) for s in ss] == [("16:00", 11), ("16:40", 1)]


def test_interval_goes_to_the_earlier_event():
    # WHY: 16:00 打开第 1 节，16:12 做检查点——这 12 分钟是花在第 1 节上的。
    evs = [Event(T("16:00"), "s1", "u", 0, "p"), Event(T("16:12"), "s2", "u", 1, "p"), Event(T("16:20"), "x")]
    assert timeline.section_minutes(evs, "u", "p") == {0: 12.0, 1: 8.0}
    assert timeline.section_minutes(evs, "u", "other") == {}


def test_video_heartbeats_keep_the_session_alive(env):
    import json
    store.STUDY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with store.STUDY_LOG.open("w", encoding="utf-8") as f:
        for hm in ("16:00", "16:14", "16:28", "16:42"):
            f.write(json.dumps({"ts": f"2026-09-25T{hm}:00", "unit": "u", "section": 0, "event": "activity",
                                "kind": "video", "plan": "p"}) + "\n")
    evs, _ = timeline.collect()
    ss = timeline.sessions(evs)
    assert len(ss) == 1 and round(ss[0].minutes) == 43
    assert timeline.section_minutes(evs, "u", "p") == {0: 43.0}


def test_attempts_and_manual_entries_count(env):
    store.record(quiz="t/01-x", qid="q1", concept="t.a", level=1, score=1, result="pass", grader="auto")
    store.record_study(event="manual", start="2026-09-25T15:00:00", minutes=48, note="视频第 1 讲")
    evs, manual = timeline.collect()
    ss = timeline.sessions(evs, manual)
    assert any(s.manual and s.minutes == 48 for s in ss)
    assert any("练习题 t/01-x" in s.by_label for s in ss)
    assert "视频第 1 讲" in timeline.report()
