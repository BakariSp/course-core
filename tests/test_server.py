import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from studykit import lessons, server, store


def test_lesson_ref_cannot_escape(env):
    for bad in ["../x", "t/../../etc", "t/01-x/../../..", "T/01", ""]:
        with pytest.raises(lessons.LessonError):
            lessons.load(bad)


def test_payload_never_contains_answers(env):
    payload = json.dumps(server.lesson_payload("t/01-x"), ensure_ascii=False)
    assert "因为 B" not in payload and "rubric" not in payload and "checks" not in payload


def test_submit_records_and_reveals_explanation(env):
    r = server.submit("t/01-x", "q1", ["B"])
    assert r["result"] == "pass" and r["explain"] == "因为 B"
    assert store.load_attempts()[0]["checker"] == "choice"
    assert server.lesson_payload("t/01-x")["questions"][0]["previous"]["score"] == 1.0
    assert "previous" not in server.lesson_payload("t/01-x", retake=True)["questions"][0]


def test_short_answer_hides_explanation_until_graded(env):
    r = server.submit("t/01-x", "q2", "我的解释")
    assert r["result"] == "pending" and "explain" not in r
    assert "t/01-x q2" in store.PROGRESS_MD.read_text("utf-8")


def test_terminal_flow_through_server(env):
    server.act("t/01-x", "q3", {"op": "open"})
    server.act("t/01-x", "q3", {"op": "exec", "cmd": "git switch -c dev"})
    assert server.submit("t/01-x", "q3", {"history": ["git switch -c dev"]})["score"] == 1.0


# ---------- HTTP 层：token 和 Host 校验 ----------

@pytest.fixture
def live(env):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), server.make_handler("tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    res = conn.getresponse()
    return res.status, res.read().decode("utf-8")


def test_page_embeds_token(live):
    status, html = request(live, "GET", "/")
    assert status == 200 and '"tok"' in html


def test_post_without_token_is_rejected(live):
    body = {"lesson": "t/01-x", "qid": "q1", "response": ["B"]}
    assert request(live, "POST", "/api/submit", body)[0] == 403
    assert store.load_attempts() == []
    status, _ = request(live, "POST", "/api/submit", body, {"X-Study-Token": "tok"})
    assert status == 200


def test_foreign_host_is_rejected(live):
    assert request(live, "GET", "/api/lessons", headers={"Host": "evil.example:80"})[0] == 403


def test_bad_lesson_ref_is_400(live):
    status, _ = request(live, "GET", "/api/lesson?ref=../../etc")
    assert status == 400
