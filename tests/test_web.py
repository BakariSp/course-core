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
    assert isinstance(req("GET", "/api/log")[1]["days"], list)
    assert [x["agent"] for x in req("GET", "/api/panel/versions")[1]["agents"]][0] == "tutor-prep"   # D-033                  # 学习记录（D-027）
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
    status, again = req("GET", f"/api/exam?lesson=t/02-exam&exam={r['exam']}")     # 页面轮询批改结果（D-049）
    assert status == 200 and again["questions"]["q1"]["result"] == "pass" and again["grading"] == []


def test_opening_a_section_starts_preparing_the_next_unit(req, app, root):
    """D-040 ②：学第 N 单元时，第 N+1 单元在后台备好。"""
    from tests.test_panel import _add_unit
    _add_unit(root)
    started = []
    app.prep.prepare = lambda u, **kw: started.append(u)
    status, r = req("POST", "/api/unit/progress", {"unit": "tools-01-shell", "section": 0, "event": "open"})
    assert status == 200 and r["prefetch"] == "tools-02-git" and started == ["tools-02-git"]
    status, r = req("POST", "/api/unit/progress", {"unit": "tools-01-shell", "section": 1, "event": "done"})
    assert "prefetch" not in r                                  # 只有打开时才看要不要预备


def test_opening_the_last_section_starts_the_unit_quiz(req, app):
    """D-041：学完之前单元题就在出了；课程页能看到出到哪。"""
    started = []
    app.quizzes.state = lambda u: {"stage": "todo"}
    app.quizzes.start = lambda u: started.append(u)
    assert req("GET", "/api/unit?id=tools-01-shell")[1]["quiz_state"] == {"stage": "todo"}
    status, r = req("POST", "/api/unit/progress", {"unit": "tools-01-shell", "section": 2, "event": "open"})   # 最后一节
    assert status == 200 and r["quiz"] == "preparing" and started == ["tools-01-shell"]


def test_view_another_version_and_switch_through_http(req, app):
    """D-044：?plan= 预览另一版（只读），/api/unit/switch 改用它。"""
    import copy
    v2 = copy.deepcopy(app.course.plan("tools-01-shell"))
    v2["provenance"] = {**v2.get("provenance", {}), "run": "v2-run"}
    app.course.offer(v2)
    status, page = req("GET", "/api/unit?id=tools-01-shell&plan=v2-run")
    assert status == 200 and page["preview"] and len(page["versions"]) == 2
    assert not req("GET", "/api/unit?id=tools-01-shell")[1]["preview"]
    assert req("POST", "/api/unit/switch", {"unit": "tools-01-shell", "plan": "v2-run"}) == (200, {"unit": "tools-01-shell", "current": "v2-run"})
    assert req("GET", "/api/unit?id=tools-01-shell")[1]["plan"]["provenance"]["run"] == "v2-run"
    assert req("POST", "/api/unit/switch", {"unit": "tools-01-shell", "plan": "nope"})[0] == 400


def test_new_app_is_served_with_the_token_and_its_static_files(req):
    """D-046：新界面在 /app，文件在 web/app/ 下；页面带 token（换版本要 POST）。"""
    status, page = req("GET", "/app")
    assert status == 200 and b"__TOKEN__" not in page and b"tok" in page
    status, css = req("GET", "/app/css/tokens.css")
    assert status == 200 and b"--accent" in css
    status, js = req("GET", "/app/js/main.js")
    assert status == 200 and b"import" in js


@pytest.mark.parametrize("path", ["/app/../study.py", "/app/%2e%2e/%2e%2e/study.py", "/app/css/nope.css", "/app/js/../../index.html"])
def test_app_static_files_cannot_escape_the_folder(req, path):
    assert req("GET", path)[0] == 404


def test_course_for_the_learner_has_states_next_step_and_curriculum(req, app, root):
    """D-046、D-047：学习者看到的课程——阶段、学科（目标、主课）、单元状态已经算好、下一步带理由。"""
    from tests.conftest import add_unit
    add_unit(root, "tools-02-git", "Git")
    add_unit(root, "tools-03-kleppmann", "Kleppmann 讲座", scope="open")
    app.course.record("tools-01-shell", 2, "done")
    status, c = req("GET", "/api/course")
    assert status == 200 and c["stages"][0]["title"] == "能验证"
    tools = c["subjects"][0]
    assert tools["goal"] == "能自己 git bisect" and tools["sources"][0]["title"] == "讲义"
    states = {u["id"]: u["state"]["key"] for u in tools["units"]}
    assert states == {"tools-01-shell": "learning", "tools-02-git": "queued", "tools-03-kleppmann": "ask"}
    git = tools["units"][1]
    assert git["queued_after"] == "tools-01-shell" and git["requests"] == ["想学 bisect"]
    shell = tools["units"][0]
    assert shell["sources"][0]["url"].endswith("course-shell/")
    [nxt] = c["next"]
    assert nxt["unit"] == "tools-01-shell" and nxt["kind"] == "continue" and nxt["reason"]
    assert nxt["section"]["index"] == 0 and nxt["section"]["minutes"] == 20      # 第 1 节还没过


def test_course_follows_the_learning_path_across_subjects(req, root):
    """D-048：路径跨学科排顺序，排队、下一步都按路径走；终点的每条能力列出来，单元标出服务哪条。"""
    import yaml
    from tests.conftest import add_unit
    add_unit(root, "tools-02-git", "Git")
    p = root / "progress" / "course.yaml"
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    data["subjects"].append({"id": "sec", "title": "安全", "units": [{"id": "sec-01-access", "title": "访问控制"}]})
    data["path"] = ["tools-01-shell", "sec-01-access", "tools-02-git"]
    data["destination"] = [{"id": "C5", "can": "审多租户隔离", "accept": "审一个接口", "units": ["sec-01-access"]}]
    p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    c = req("GET", "/api/course")[1]
    assert c["path"] == ["tools-01-shell", "sec-01-access", "tools-02-git"]
    assert c["destination"] == [{"id": "C5", "can": "审多租户隔离", "accept": "审一个接口", "units": ["sec-01-access"]}]
    units = {u["id"]: u for s in c["subjects"] for u in s["units"]}
    assert units["sec-01-access"]["queued_after"] == "tools-01-shell" and units["tools-02-git"]["queued_after"] == "sec-01-access"
    assert units["sec-01-access"]["serves"] == ["C5"] and units["tools-02-git"]["serves"] == []


def test_unit_page_has_readiness(req):
    r = req("GET", "/api/unit?id=tools-01-shell")[1]["readiness"]
    assert set(r) == {"required", "helpful", "learn"}


def test_profile_and_refuting_an_observation_through_http(req, app):
    o = app.learner.note_strategy("一节塞太多命令会过载")
    status, p = req("GET", "/api/profile")
    assert status == 200 and [x["id"] for x in p["observations"]] == [o.id]
    assert req("POST", "/api/profile/refute", {"id": o.id, "note": "现在不会了"})[0] == 200
    assert req("GET", "/api/profile")[1]["observations"] == []
    assert req("POST", "/api/profile/refute", {"id": o.id})[0] == 400


def test_lab_files_through_http(req, unit):
    """D-057：页面上的「文件」经 /api/unit/lab 读写练习场里的文件；路径出了练习场是 400。"""
    from tests.test_cs_practice import needs_bash
    if needs_bash.args[0]:
        pytest.skip("没有 bash")
    assert req("POST", "/api/unit/lab", {"unit": unit, "op": "write", "section": 0, "path": "a.txt", "content": "hi\n"})[1]["saved"]
    assert req("POST", "/api/unit/lab", {"unit": unit, "op": "read", "section": 0, "path": "a.txt"})[1]["content"] == "hi\n"
    assert "a.txt" in req("POST", "/api/unit/lab", {"unit": unit, "op": "files", "section": 0})[1]["files"]
    assert req("POST", "/api/unit/lab", {"unit": unit, "op": "write", "section": 0, "path": "../x", "content": ""})[0] == 400


def test_recall_answer_through_http(req, app):
    """D-051：页面上点「记得 / 不记得了」，记一条 recalled。"""
    from tests.conftest import plan_v2
    p = plan_v2(run="run-3")                       # 同一个 run 的版本已经存过，不会被覆盖
    p["parts"][0]["sections"][0]["recall"] = [{"id": "tools.cmd.ls", "term": "ls", "explain": "列出目录里的文件名"}]
    app.course.publish(p)
    r = req("POST", "/api/unit/progress", {"unit": "tools-01-shell", "section": 0, "event": "recall",
                                           "node": "tools.cmd.ls", "remembered": False})
    assert r[0] == 200, r
    [e] = app.store.query("me", verbs=["recalled"])
    assert (e.object_id, e.payload, e.section) == ("tools.cmd.ls", {"remembered": False}, 0)
