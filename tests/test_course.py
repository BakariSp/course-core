import json

import pytest

from studykit import course, server, store


def sec(title, minutes):
    return {"title": title, "minutes": minutes, "goal": "g", "explain": "e", "try": [], "sources": []}


@pytest.fixture
def unit(env, monkeypatch):
    preps = env / "preps"
    preps.mkdir()
    monkeypatch.setattr(course, "PREPS", preps)
    plan = {"schema_version": 1, "unit": "tools-01-shell", "title": "Shell", "summary": "s",
            "parts": [{"title": "P1", "sections": [sec("a", 20), sec("b", 20)]},
                      {"title": "P2", "sections": [sec("c", 30)]}],
            "outcomes": ["x", "y", "z"]}
    (preps / "tools-01-shell.json").write_text(json.dumps(plan), encoding="utf-8")
    return "tools-01-shell"


@pytest.mark.parametrize("bad", ["../x", "a/b", "Tools-01", "", "tools-01-shell/../../etc"])
def test_unit_id_is_validated(unit, bad):
    with pytest.raises(course.CourseError):
        course.load_plan(bad)


def test_missing_plan_says_how_to_get_one(unit):
    with pytest.raises(course.CourseError, match="准备学"):
        course.load_plan("tools-02-git")


def test_payload_has_sessions_and_minutes(unit):
    p = course.payload(unit)
    assert p["planned_minutes"] == 70 and p["section_count"] == 3
    assert p["sessions"] == [[0, 1], [2]]                  # 默认每次 45 分钟
    assert p["progress"]["done"] == [] and p["quiz"] is None


def test_progress_is_computed_from_events(unit):
    course.record(unit, 0, "open")
    course.record(unit, 0, "done", 18.26)
    course.record(unit, 1, "done", 25)
    course.record(unit, 1, "undone")
    p = course.progress(unit)
    assert p["done"] == [0]
    assert p["spent_minutes"] == {"0": 18.3, "1": 25.0}   # 撤销"学完"不抹掉已经花的时间
    assert p["last_section"] == 1
    assert len(store.load_study_log(unit)) == 4


@pytest.mark.parametrize("section,event", [(3, "done"), (-1, "done"), ("0", "done"), (0, "skip")])
def test_bad_progress_events_rejected(unit, section, event):
    with pytest.raises(course.CourseError):
        course.record(unit, section, event)


def test_quiz_is_linked_by_unit_field(unit, env):
    quiz = env / "lessons" / "t" / "01-x" / "quiz.yaml"
    quiz.write_text(quiz.read_text(encoding="utf-8") + "\nunit: tools-01-shell\n", encoding="utf-8")
    assert course.payload(unit)["quiz"] == "t/01-x"


def test_list_units(unit):
    course.record(unit, 2, "done", 30)
    assert course.list_units() == [{"unit": unit, "title": "Shell", "minutes": 70, "sections": 3, "done": 1}]


def test_http_routes(unit, env):
    import http.client
    import socket
    import threading
    from http.server import ThreadingHTTPServer
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler("tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        def req(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null")

        assert req("GET", "/api/units")[1][0]["unit"] == unit
        assert req("GET", f"/api/unit?id={unit}")[1]["planned_minutes"] == 70
        assert req("GET", "/api/unit?id=../x")[0] == 400
        body = {"unit": unit, "section": 0, "event": "done", "minutes": 12}
        assert req("POST", "/api/unit/progress", body)[0] == 403              # 没有 token
        status, prog = req("POST", "/api/unit/progress", body, {"X-Study-Token": "tok"})
        assert status == 200 and prog["done"] == [0]
    finally:
        httpd.shutdown()
        httpd.server_close()
