"""HTTP 适配器：路由、错误码、Host 校验（防 DNS rebinding）、token（防 CSRF）。"""
import http.client
import json
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from studykit import web


@pytest.fixture
def req(app, unit):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), web.make_handler(app, "tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method, path, body=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        headers = {"X-Study-Token": "tok"} if token else {}
        if host:
            headers["Host"] = host
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw and r.getheader("Content-Type", "").startswith("application/json") else raw)

    yield call
    httpd.shutdown()
    httpd.server_close()


def test_routes(req):
    assert req("GET", "/api/units")[1][0]["unit"] == "tools-01-shell"
    assert req("GET", "/api/unit?id=tools-01-shell")[1]["planned_minutes"] == 70
    assert req("GET", "/api/lessons")[1][0]["ref"] == "t/01-x"
    assert isinstance(req("GET", "/api/log")[1]["days"], list)                  # 学习记录（D-027）
    assert "因为 B" not in json.dumps(req("GET", "/api/lesson?ref=t/01-x")[1], ensure_ascii=False)
    assert req("GET", "/api/kg?topic=tools")[1]["nodes"] == []
    assert req("GET", "/api/nope")[0] == 404
    status, page = req("GET", "/")
    assert status == 200 and b"__TOKEN__" not in page


def test_bad_input_is_400(req):
    assert req("GET", "/api/unit?id=../x")[0] == 400
    assert req("GET", "/api/lesson?ref=../../etc")[0] == 400
    assert req("POST", "/api/unit/hint", {"unit": "tools-01-shell", "section": 0, "idx": 5})[0] == 400
    assert req("POST", "/api/unit/check", {"unit": "tools-01-shell"})[0] == 400              # 缺字段


def test_posts_need_token_and_right_host(req):
    body = {"unit": "tools-01-shell", "section": 0, "event": "open"}
    assert req("POST", "/api/unit/progress", body, token=False)[0] == 403
    assert req("POST", "/api/unit/progress", body, host="evil.example:80")[0] == 403
    assert req("GET", "/api/units", host="evil.example:80")[0] == 403
    status, r = req("POST", "/api/unit/progress", body)
    assert status == 200 and r["plan"] == "run-2"


def test_submit_and_check_through_http(req):
    status, r = req("POST", "/api/submit", {"lesson": "t/01-x", "qid": "q1", "response": ["B"]})
    assert status == 200 and r["result"] == "pass"
    status, r = req("POST", "/api/unit/check", {"unit": "tools-01-shell", "section": 0, "idx": 0, "response": ["B"]})
    assert status == 200 and r["ok"]


def test_exam_submit_through_http(req):
    status, r = req("POST", "/api/submit", {"lesson": "t/02-exam", "qid": "q1", "response": ["B"]})
    assert status == 400 and "整卷" in r["error"]
    status, r = req("POST", "/api/exam/submit", {"lesson": "t/02-exam", "responses": {"q1": ["B"]}})
    assert status == 200 and r["questions"]["q1"]["result"] == "pass"
