"""SQLite 存储：只追加由数据库强制；两个进程同时写不丢不坏（D-021）。"""
import sqlite3
import subprocess
import sys
import textwrap

import pytest

from studykit.adapters.sqlite import MIGRATIONS, SqliteStore
from studykit.domain.evidence import SYSTEM, Actor, Evidence, learner_actor
from studykit.domain.harness import Grade, Run, RunStep, Variant

ME = learner_actor("me")


def e(i, verb="opened", ts="2026-09-26T10:00:00", **kw):
    base = dict(id=f"e{i}", ts=ts, learner="me", actor=ME, verb=verb, object_type="section", object_id="u#0",
                unit="u", plan="p", section=0)
    base.update(kw)
    return Evidence(**base)


@pytest.fixture
def store(tmp_path):
    return SqliteStore(tmp_path / "study.db")


def test_roundtrip_filters_and_order(store):
    store.append(e(1, ts="2026-09-26T10:05:00"), e(2, nodes=("t.b", "t.a"), payload={"x": [1, "中"]}),
                 e(3, unit="v", actor=Actor("agent", "tutor", "tutor", "abc")))
    got = store.query("me", unit="u")
    assert [x.id for x in got] == ["e2", "e1"]                        # 按时间，同时间按写入顺序
    assert got[0].nodes == ("t.b", "t.a") and got[0].payload == {"x": [1, "中"]}   # 节点顺序保留
    assert store.get("e3").actor == Actor("agent", "tutor", "tutor", "abc")
    assert [x.id for x in store.query("me", verbs=["opened"], object_id="u#0")] == ["e2", "e3", "e1"]
    assert store.query("someone-else") == []


def test_evidence_is_append_only(store, tmp_path):
    store.append(e(1, nodes=("t.a",)))
    with sqlite3.connect(tmp_path / "study.db") as c:
        for sql in ("UPDATE evidence SET score = 1", "DELETE FROM evidence", "DELETE FROM evidence_node"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                c.execute(sql)


def test_a_batch_is_one_transaction(store):
    with pytest.raises(sqlite3.IntegrityError):
        store.append(e(1), e(1))                                        # 第二条主键冲突
    assert store.query("me") == []                                      # 第一条也没写进去


def test_one_grade_per_answer_per_grader(store):
    a = e(1, verb="answered", object_type="question", object_id="t/01-x#q2", pending=True, payload={"response": "x"})
    grade = lambda i, who="tutor": e(i, verb="graded", object_type="question", object_id="t/01-x#q2",  # noqa: E731
                                     caused_by="e1", score=0.5, actor=Actor("agent", who))
    store.append(a, grade(2, "short-grader"))
    store.append(grade(3))                                              # 导师可以覆盖 LLM 的批改（D-031）
    with pytest.raises(sqlite3.IntegrityError):
        store.append(grade(4))                                          # 同一个批改者不能批两次
    with pytest.raises(sqlite3.IntegrityError):                         # 不能指向不存在的作答
        store.append(e(4, verb="graded", caused_by="nope", score=1.0))


def test_two_processes_writing_at_once_lose_nothing(tmp_path):
    path = tmp_path / "study.db"
    SqliteStore(path)
    script = textwrap.dedent(f"""
        import sys
        from studykit.adapters.sqlite import SqliteStore
        from studykit.domain.evidence import Evidence, learner_actor
        s = SqliteStore(__import__("pathlib").Path({str(path)!r}))
        for i in range(150):
            s.append(Evidence(f"{{sys.argv[1]}}-{{i}}", "2026-09-26T10:00:00", "me", learner_actor("me"),
                              "opened", "section", "u#0", payload={{"blob": "x" * 5000}}))
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script, tag]) for tag in ("a", "b")]
    assert [p.wait(timeout=120) for p in procs] == [0, 0]
    rows = SqliteStore(path).query("me")
    assert len(rows) == 300 and all(len(r.payload["blob"]) == 5000 for r in rows)


def test_plans_are_versioned_and_current_is_the_latest_publication(store):
    store.publish("u", {"provenance": {"run": "r1"}, "title": "v1"}, "2026-09-25T10:00:00", SYSTEM)
    store.publish("u", {"provenance": {"run": "r2"}, "title": "v2"}, "2026-09-26T10:00:00", SYSTEM)
    assert store.current("u")["title"] == "v2" and store.version("u", "r1")["title"] == "v1"
    store.publish("u", {"provenance": {"run": "r1"}, "title": "v1"}, "2026-09-27T10:00:00", SYSTEM)   # 回滚到 v1
    assert store.current("u")["title"] == "v1" and store.units() == ["u"]
    assert store.current("nope") is None
    with pytest.raises(ValueError):
        store.publish("u", {"title": "没有版本号"}, "2026-09-27T10:00:00", SYSTEM)


def test_runs_and_grades(store, tmp_path):
    v = Variant("tutor-prep", {"model": "m"})
    run = Run("20260926-100000-u", "tutor-prep", v.id, "u", "me", "batch", "2026-09-26T10:00:00", {"known_terms": ["pwd"]},
              tokens=5, tool_calls={"fetch_url": {"calls": 1, "errors": 0}})
    store.add_run(run, v, [RunStep(0, "tool", "fetch_url", summary="ok", detail={"args": {"url": "x"}})])
    assert store.run(run.id) == run and store.runs("tutor-prep") == [run] and store.variant(v.id) == v
    assert store.steps(run.id)[0].detail == {"args": {"url": "x"}}
    store.add_grade(Grade("g1", "2026-09-26T10:01:00", run.id, "check", "c1", "system", 1.0, "pass", {"a": {"score": 1.0}}))
    assert [g.id for g in store.grades(agent="tutor-prep")] == ["g1"] and store.grades(run.id)[0].dims == {"a": {"score": 1.0}}
    with sqlite3.connect(tmp_path / "study.db") as c:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            c.execute("UPDATE grade SET score = 0")


def test_refuses_a_database_newer_than_the_code(tmp_path):
    with sqlite3.connect(tmp_path / "study.db") as c:
        c.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    with pytest.raises(RuntimeError, match="比代码新"):
        SqliteStore(tmp_path / "study.db")


def test_blobs_and_variants_are_content_addressed_and_immutable(store):
    """D-033：每个版本组成的内容按哈希存一次，之后永远能取回；同样的内容再存不会变。"""
    store.put_blobs({"h1": "第一版规则", "h2": "b"})
    store.put_blobs({"h1": "第一版规则"})
    assert store.blob("h1") == "第一版规则" and store.blob("nope") is None
    v = Variant("a", {"prompt:role": "h1", "model": "m"})
    store.add_variant(v, "2026-09-26T10:00:00")
    store.add_variant(v, "2026-09-26T11:00:00")                        # 第二次见到：first_seen 不变
    assert store.variants("a") == [(v, "2026-09-26T10:00:00")]
    with sqlite3.connect(store.path) as c, pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE blob SET content = 'x' WHERE hash = 'h1'")
