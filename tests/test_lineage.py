"""流程看板（D-024）：看板上"被谁用"必须和代码一致，新记录要能实时推出去。"""
import http.client
import inspect
import json
import re
import socket
import threading
from http.server import ThreadingHTTPServer

import pytest

from studykit import web
from studykit.app.observe import LINKS, NODES
from studykit.domain import course_eval, knowledge, mastery, profile, progress
from studykit.domain.evidence import CORE_VERBS
from studykit.specs.cs_practice import VERBS as CS_VERBS

# 每个投影由哪些函数实现：声明"verb X 喂给投影 P"，P 的代码里就必须真的读 X
READERS = {
    "mastery": [mastery.scored, mastery.practice_summary],
    "node_state": [knowledge.signals],
    "progress": [progress.project, progress.hints_used, progress.last_hint],
    "course_eval": [course_eval.evaluate],
    "profile": [profile.observations],
}


def reads(source: str, verb: str) -> bool:
    """代码里真的在比较 verb：`== "x"` 或 `in ("x", ...)`（只搜字符串会被同名的字典键骗到）。"""
    return bool(re.search(rf'==\s*"{verb}"|\bin\s*\([^)]*"{verb}"', source))


@pytest.mark.parametrize("verb", [*CORE_VERBS, *CS_VERBS], ids=lambda v: v.name)
def test_declared_feeds_match_the_code(verb):
    assert verb.title, f"{verb.name} 没有给人看的名字"
    for f in verb.feeds:
        assert f.projection in READERS, f"{verb.name} 声明喂给 {f.projection}，但看板上没有这个投影"
        source = "".join(inspect.getsource(fn) for fn in READERS[f.projection])
        assert reads(source, verb.name), f"{verb.name} 声明喂给 {f.projection}，但 {f.projection} 的代码里没有读它"


def test_every_projection_that_reads_a_verb_says_so():
    """反过来：投影的代码里出现的 verb，都要在 feeds 里声明（否则看板会漏画）。"""
    declared = {(v.name, f.projection) for v in (*CORE_VERBS, *CS_VERBS) for f in v.feeds}
    for proj, fns in READERS.items():
        source = "".join(inspect.getsource(fn) for fn in fns)
        for v in (*CORE_VERBS, *CS_VERBS):
            if reads(source, v.name):
                assert (v.name, proj) in declared, f"{proj} 读了 {v.name}，但 {v.name} 的 feeds 里没有声明"


def test_graph_is_closed(app):
    g = app.observer.graph()
    for a, b, _ in LINKS:
        assert a in NODES and b in NODES
    for v in g["verbs"]:
        for f in v["feeds"]:
            assert f["node"] in NODES


def test_describe_paths_and_feeds(app, unit, clock):
    app.assessment.submit("t/01-x", "q1", ["B"])
    clock.tick()
    app.learner.observe("tools.cmd.pwd", "weak", "分不清 pwd 和 cd")
    clock.tick()
    app.course.check(unit, 0, 0, ["B"])
    app.course.check(unit, 0, 1, [".."])
    evs = {(e["verb"], e["path"][1] if len(e["path"]) > 2 else e["path"][0]): e
           for e in app.observer.since(None)["events"] if e["kind"] == "evidence"}
    answer = evs[("answered", "quiz")]
    assert answer["path"] == ["learner", "quiz", "evidence"]
    assert {f["node"] for f in answer["feeds"]} == {"mastery", "timeline"}
    assert answer["recorded"]["score"] == 1.0 and answer["recorded"]["nodes"] == ["t.a"]
    obs = evs[("observed", "cli")]
    assert obs["path"] == ["tutor", "cli", "evidence"] and [f["node"] for f in obs["feeds"]] == ["node_state"]   # 不算学习时长
    cp = evs[("answered", "course_page")]
    assert {f["node"] for f in cp["feeds"]} == {"node_state", "progress", "course_eval", "timeline"}
    passed = evs[("passed_section", "system")]
    assert passed["path"] == ["system", "evidence"] and passed["lane"] == "model"
    for e in app.observer.since(None)["events"]:
        assert set(e["path"]) <= set(NODES), e["path"]


def test_cursor_only_returns_new_rows(app, root, tmp_path, clock):
    first = app.observer.since(None)
    assert first["events"] == []
    app.assessment.log_practice("dsa.hash", 3, 1.0, "LeetCode 1")
    # 另一个 App（相当于另一个进程里的命令行）写的，也能按游标拿到
    from studykit.bootstrap import Config, build
    other = build(Config(root=root, data=tmp_path / "data"), clock=clock)
    other.learner.observe("dsa.hash", "ok", "会了")
    new = app.observer.since(first["cursor"])
    assert [e["verb"] for e in new["events"]] == ["logged_practice", "observed"]
    assert app.observer.since(new["cursor"])["events"] == []


def test_stream_pushes_new_rows(app, monkeypatch):
    monkeypatch.setattr(web, "STREAM_POLL", 0.05)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", port), web.make_handler(app, "tok", port))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/flow/stream")
        r = c.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        assert r.fp.readline() == b"retry: 2000\n"
        app.assessment.submit("t/01-x", "q1", ["A"])
        lines = []
        while not any(l.startswith(b"data:") for l in lines):
            lines.append(r.fp.readline())
        event = json.loads(next(l for l in lines if l.startswith(b"data:"))[5:])
        cursor = json.loads(next(l for l in lines if l.startswith(b"id:"))[3:])
        assert event["verb"] == "answered" and event["subtitle"].startswith("得分 0")
        assert cursor["evidence"] >= 1                          # 断线重连时浏览器带着它，接着推
        c.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_flow_page_and_history_routes(app, unit):
    app.assessment.submit("t/01-x", "q1", ["B"])
    handler = web.make_handler(app, "tok", 0)
    assert handler                                               # 路由见 test_web；这里只检查数据
    hist = app.observer.since(None, 5)
    assert any(e["title"].startswith("作答") for e in hist["events"])
    assert set(hist["cursor"]) == {"evidence", "run", "grade", "publication"}
    assert any(e["kind"] == "publication" for e in app.observer.since(None, 50)["events"])
