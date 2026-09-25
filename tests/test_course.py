import json

import pytest

from studykit import course, knowledge, lab, server, store


def sec(title, minutes, **kw):
    s = {"title": title, "minutes": minutes, "goal": "g", "explain": "e", "try": []}
    s.update(kw)
    return s


CHOICE = {"type": "choice", "prompt": "pwd 打印什么？", "options": ["上一个目录", "当前目录"], "answer": "B",
          "concept": "tools.shell.cwd", "hints": ["方向", "关键概念", "接近答案"], "explain": "pwd = print working directory",
          "traps": [{"when": "A", "symptom": "以为是上一个目录", "cause": "和 cd - 混了", "fix": "看 pwd 的全称"}]}
FILL = {"type": "fill", "prompt": "回到上一级目录：cd ____", "accept": [[".."]], "hints": ["a", "b", "c"], "explain": "..",
        "traps": [{"when": r"^\.$", "symptom": "cd . 没动", "cause": ". 是当前目录", "fix": "用 .."}]}
LAB_TASK = {"type": "lab", "prompt": "把 ERROR 的行数写进 report.txt", "concept": "tools.cmd.grep",
            "checks": [{"file": "report.txt", "equals": "2", "desc": "report.txt 里是 ERROR 的行数"}],
            "solution": ["grep -c ERROR logs/app.log > report.txt"], "hints": ["a", "b", "c"], "explain": "grep -c 数行数"}
LAB = {"story": "测试服务器出问题了", "files": [{"path": "logs/app.log", "content": "INFO ok\nERROR a\nERROR b\n"},
                                          {"path": "config/", "content": ""}]}


def plan_v2(run="run-2", published="2026-09-26T10:00:00"):
    return {"schema_version": 2, "unit": "tools-01-shell", "title": "Shell", "summary": "s",
            "provenance": {"run": run, "published": published},
            "lab": LAB, "sources": [{"title": "讲义", "url": "https://missing.csail.mit.edu/2026/course-shell/"}],
            "parts": [{"title": "P1", "sections": [
                sec("a", 20, terms=[{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"},
                                    {"id": "tools.cmd.cd", "term": "cd", "explain": "换目录"}],
                    checkpoint=[CHOICE, FILL]),
                sec("b", 20, terms=[{"id": "tools.cmd.grep", "term": "grep", "explain": "找行"}], checkpoint=[LAB_TASK])]},
                {"title": "P2", "sections": [sec("c", 30)]}],
            "outcomes": ["x", "y", "z"]}


@pytest.fixture
def unit(env, monkeypatch):
    preps = env / "preps"
    preps.mkdir()
    monkeypatch.setattr(course, "PREPS", preps)
    monkeypatch.setattr(knowledge, "KNOWLEDGE", env / "knowledge")
    course._VERSIONS.clear()
    (env / "progress").mkdir(exist_ok=True)
    (env / "progress" / "settings.yaml").write_text(f"lab_root: '{(env / 'labs').as_posix()}'\n", encoding="utf-8")
    (preps / "tools-01-shell.json").write_text(json.dumps(plan_v2()), encoding="utf-8")
    yield "tools-01-shell"
    lab.SHELLS.stop_all()


def write_log(*events):
    store.STUDY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with store.STUDY_LOG.open("a", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


@pytest.mark.parametrize("bad", ["../x", "a/b", "Tools-01", "", "tools-01-shell/../../etc"])
def test_unit_id_is_validated(unit, bad):
    with pytest.raises(course.CourseError):
        course.load_plan(bad)


def test_missing_plan_says_how_to_get_one(unit):
    with pytest.raises(course.CourseError, match="准备学"):
        course.load_plan("tools-02-git")


def test_payload_hides_answers_and_lab_files(unit):
    # INVARIANT: 检查点的答案、提示、参考做法只在服务器上（D-013）
    p = course.payload(unit)
    text = json.dumps(p["plan"], ensure_ascii=False)
    for secret in ('"answer"', '"accept"', '"solution"', '"hints"', '"traps"', "grep -c ERROR", "pwd = print"):
        assert secret not in text
    assert p["plan"]["lab"] == {"story": "测试服务器出问题了", "files": 2}
    assert p["planned_minutes"] == 70 and p["sessions"] == [[0, 1], [2]]
    assert p["sources"][0]["title"] == "讲义"


def test_predicted_load_drops_as_terms_become_known(unit):
    assert [x["new_terms"] for x in course.payload(unit)["load"]] == [2, 1, 0]
    store.record_study(event="term", unit=unit, section=0, node="tools.cmd.pwd", action="known")
    knowledge.add_nodes([{"id": "tools.cmd.pwd", "title": "pwd", "kind": "term"}])
    load = course.payload(unit)["load"]
    assert load[0]["new_terms"] == 1 and load[0]["level"] == "低"


def test_choice_checkpoint_shows_trap_then_passes(unit):
    wrong = course.check(unit, 0, 0, ["A"])
    assert not wrong["ok"] and wrong["trap"]["cause"] == "和 cd - 混了"
    assert "explain" not in wrong and "B" not in json.dumps(wrong["feedback"], ensure_ascii=False)
    right = course.check(unit, 0, 0, ["B"])
    assert right["ok"] and right["explain"].startswith("pwd") and right["attempt"] == 2
    assert not right["section_passed"]                        # 还有一道填空没做


def test_section_passes_when_all_checkpoints_pass_and_terms_get_evidence(unit):
    knowledge.add_nodes([{"id": "tools.cmd.cd", "title": "cd", "kind": "term"}])
    assert course.check(unit, 0, 1, ["."])["trap"]["fix"] == "用 .."
    course.check(unit, 0, 0, ["B"])
    r = course.check(unit, 0, 1, [".."])
    assert r["section_passed"] and r["progress"]["passed"] == [0]
    cp = r["progress"]["checkpoints"]["0"]
    assert cp["0"] == {"ok": True, "attempts": 1, "first_try": True, "hints": 0}
    assert cp["1"]["first_try"] is False and cp["1"]["attempts"] == 2
    assert knowledge.states()["tools.cmd.cd"].state == "mastered"


def test_hints_are_given_one_level_at_a_time(unit):
    assert course.hint(unit, 0, 0) == {"level": 1, "total": 3, "hints": ["方向"]}
    course.hint(unit, 0, 0)
    assert course.hint(unit, 0, 0)["hints"] == ["方向", "关键概念", "接近答案"]
    assert course.hint(unit, 0, 0)["level"] == 3               # 到顶了不再增加
    assert course.progress(unit)["checkpoints"]["0"]["0"]["hints"] == 3


@pytest.mark.parametrize("section,event,extra", [
    (3, "open", {}), (-1, "open", {}), ("0", "open", {}), (0, "hack", {}),
    (0, "term", {"node": "tools.cmd.grep", "action": "known"}),        # grep 不是第 1 节的新词
    (0, "term", {"node": "tools.cmd.pwd", "action": "love"}),
    (0, "load_rating", {"rating": 9}), (0, "term_miss", {"text": ""}),
    (None, "activity", {"kind": "sleep"}),
])
def test_bad_events_rejected(unit, section, event, extra):
    with pytest.raises(course.CourseError):
        course.record(unit, section, event, **extra)


def test_skip_and_ratings(unit):
    course.record(unit, 0, "skip")
    course.record(unit, 0, "load_rating", rating=4)
    course.record(unit, None, "activity", kind="video")
    p = course.progress(unit)
    assert p["skipped"] == [0] and p["ratings"] == {"0": 4} and p["passed"] == []


def test_old_events_belong_to_the_version_published_before_them(unit, env):
    # v1 在 16:15 发布，v2 在第二天发布；v1 时期的事件没有 plan 字段。
    hist = env / "preps" / "history" / unit
    hist.mkdir(parents=True)
    old = plan_v2("run-1", "2026-09-25T16:15:21")
    (hist / "run-1.json").write_text(json.dumps(old), encoding="utf-8")
    (hist / "run-2.json").write_text(json.dumps(plan_v2()), encoding="utf-8")
    write_log({"ts": "2026-09-25T16:20:00", "unit": unit, "section": 2, "event": "open"},
              {"ts": "2026-09-25T16:30:00", "unit": unit, "section": 2, "event": "done"})
    assert course.plan_of_event(unit, {"ts": "2026-09-25T16:20:00"}) == "run-1"
    assert course.progress(unit)["passed"] == []               # v1 的"学完"不算到 v2 上
    assert course.events(unit, "run-1")[0]["section"] == 2


def test_section_time_comes_from_event_timestamps(unit):
    write_log({"ts": "2026-09-26T10:00:00", "unit": unit, "section": 0, "event": "open", "plan": "run-2"},
              {"ts": "2026-09-26T10:12:00", "unit": unit, "section": 0, "event": "activity", "kind": "video", "plan": "run-2"},
              {"ts": "2026-09-26T10:20:00", "unit": unit, "section": 1, "event": "open", "plan": "run-2"},
              {"ts": "2026-09-26T11:00:00", "unit": unit, "section": 1, "event": "activity", "kind": "page", "plan": "run-2"})
    p = course.progress(unit)
    assert p["spent_minutes"] == {"0": 20.0, "1": 2.0}        # 10:20 → 11:00 空了 40 分钟，不算；两次各记 1 分钟收尾
    assert p["total_spent"] == 22.0


def test_list_units(unit):
    assert course.list_units() == [{"unit": unit, "title": "Shell", "minutes": 70, "sections": 3, "done": 0}]


# ---------- 练习场（需要 bash） ----------

def _has_bash():
    try:
        lab.find_bash()
        return True
    except lab.LabError:
        return False


needs_bash = pytest.mark.skipif(not _has_bash(), reason="没有 bash")


@needs_bash
def test_lab_terminal_keeps_state_and_warns_outside(unit, env):
    info = course.lab_action(unit, "open", 0)
    assert (env / "labs" / unit / "logs" / "app.log").exists()
    assert course.lab_action(unit, "run", 0, "cd logs && x=5")["rc"] == 0
    r = course.lab_action(unit, "run", 0, "echo $x; pwd")
    assert r["output"].splitlines()[0] == "5" and r["cwd"].endswith("/logs") and not r["outside"]
    assert course.lab_action(unit, "run", 0, "cd /")["outside"] is True
    assert course.lab_action(unit, "run", 0, "cat")["rc"] == 0          # 读输入的命令不会卡住
    bad = course.lab_action(unit, "run", 0, "echo 'unclosed")
    assert bad["rc"] != 0 and "cmd.sh" not in bad["output"]
    gone = course.lab_action(unit, "run", 0, "exit 3")
    assert gone["note"] and "重新开了一个 shell" in gone["note"]
    assert course.lab_action(unit, "run", 0, "pwd")["cwd"] == info["lab_posix"]
    logged = [e for e in store.load_study_log(unit) if e["event"] == "lab"]
    assert logged[0]["cmd"] == "cd logs && x=5" and logged[0]["plan"] == "run-2"


@needs_bash
def test_lab_checkpoint_restore_and_fill(unit, env):
    course.record(unit, 1, "open")                              # 第一次打开第 2 节时存快照
    assert not course.check(unit, 1, 0, None)["ok"]            # 什么都不做不能通过
    course.lab_action(unit, "run", 1, "echo 3 > report.txt; rm logs/app.log")
    assert course.check(unit, 1, 0, None)["checks"] == [{"ok": False, "desc": "report.txt 里是 ERROR 的行数"}]
    course.lab_action(unit, "restore", 1)
    assert (env / "labs" / unit / "logs" / "app.log").exists() and not (env / "labs" / unit / "report.txt").exists()
    course.lab_action(unit, "fill", 1)                          # 跳过时用参考做法补齐
    r = course.check(unit, 1, 0, None)
    assert r["ok"] and r["solution"] == ["grep -c ERROR logs/app.log > report.txt"]
    course.lab_action(unit, "reset", 1)
    assert not (env / "labs" / unit / "report.txt").exists()


def test_lab_never_deletes_outside_its_root(unit, env):
    with pytest.raises(lab.LabError):
        lab._remove(env / "preps")


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
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
            c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
            r = c.getresponse()
            return r.status, json.loads(r.read() or b"null")

        tok = {"X-Study-Token": "tok"}
        assert req("GET", "/api/units")[1][0]["unit"] == unit
        assert req("GET", f"/api/unit?id={unit}")[1]["planned_minutes"] == 70
        assert req("GET", "/api/unit?id=../x")[0] == 400
        body = {"unit": unit, "section": 0, "event": "open"}
        assert req("POST", "/api/unit/progress", body)[0] == 403              # 没有 token
        assert req("POST", "/api/unit/progress", body, tok)[0] == 200
        status, r = req("POST", "/api/unit/check", {"unit": unit, "section": 0, "idx": 0, "response": ["B"]}, tok)
        assert status == 200 and r["ok"]
        assert req("POST", "/api/unit/check", {"unit": unit, "section": 0, "idx": 0, "response": ["B"]})[0] == 403
        assert req("POST", "/api/unit/hint", {"unit": unit, "section": 0, "idx": 5}, tok)[0] == 400
        assert req("POST", "/api/unit/lab", {"unit": unit, "op": "run", "cmd": "echo hi"})[0] == 403
        assert req("GET", "/api/kg?topic=tools")[1]["nodes"] == []
    finally:
        httpd.shutdown()
        httpd.server_close()
