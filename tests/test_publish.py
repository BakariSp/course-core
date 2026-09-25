import json

import pytest

from studykit import course, course_eval, knowledge, lab, store
from studykit.agent_env import evaluate, runner
from tests.test_course import LAB, plan_v2, needs_bash


@pytest.fixture
def run_dir(env, monkeypatch):
    monkeypatch.setattr(store, "ROOT", env)
    monkeypatch.setattr(knowledge, "KNOWLEDGE", env / "knowledge")
    d = env / "runs" / "agents" / "tutor-prep" / "run-9"
    d.mkdir(parents=True)
    plan = plan_v2()
    plan.pop("provenance")
    plan["nodes"] = [{"id": "tools.cmd.grep", "title": "grep", "desc": "按内容找行", "kind": "term", "requires": []}]
    (d / "output.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    meta = {"agent": "tutor-prep", "unit": "tools-01-shell", "run_id": "run-9", "prompt_version": "p", "env_version": "e",
            "provider": "x", "model": "m"}
    (d / "eval.json").write_text(json.dumps({"meta": meta, "checks": [{"desc": "d", "ok": True}]}), encoding="utf-8")
    return d


def test_publish_requires_lab_verification(run_dir):
    with pytest.raises(runner.AgentError, match="lab verify"):
        evaluate.publish(run_dir)
    (run_dir / "lab_verify.json").write_text(json.dumps({"ok": False}), encoding="utf-8")
    with pytest.raises(runner.AgentError):
        evaluate.publish(run_dir)


def test_publish_archives_version_and_merges_nodes(run_dir, env):
    (run_dir / "lab_verify.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    dest = evaluate.publish(run_dir)
    published = json.loads(dest.read_text(encoding="utf-8"))
    assert published["schema_version"] == 2 and published["provenance"]["run"] == "run-9"
    assert (env / "preps" / "history" / "tools-01-shell" / "run-9.json").exists()
    node = knowledge.load_graph()["tools.cmd.grep"]
    assert node.units == ["tools-01-shell"] and node.desc == "按内容找行"


@needs_bash
def test_lab_verify_replays_the_whole_lab(env):
    ok = lab.verify(plan_v2(), env / ".sandbox" / "v1")
    assert ok["ok"], ok["problems"]
    broken = plan_v2()
    task = broken["parts"][0]["sections"][1]["checkpoint"][0]
    task["solution"] = ["echo 7 > report.txt"]
    r = lab.verify(broken, env / ".sandbox" / "v2")
    assert not r["ok"] and "仍然没通过" in r["problems"][0]
    free = plan_v2()
    free["lab"] = {**LAB, "files": LAB["files"] + [{"path": "report.txt", "content": "2\n"}]}
    assert "本来就满足" in lab.verify(free, env / ".sandbox" / "v3")["problems"][0]
    giveaway = plan_v2()
    giveaway["parts"][0]["sections"][1]["try"] = [{"command": "grep -c ERROR logs/app.log > report.txt", "expect": "x"}]
    assert "照着动手步骤敲完就已经通过了" in lab.verify(giveaway, env / ".sandbox" / "v5")["problems"][0]
    tries = plan_v2()
    tries["parts"][0]["sections"][0]["try"] = [{"command": "tree", "expect": "x"}]      # Git Bash 里没有 tree
    assert any("tree" in w for w in lab.verify(tries, env / ".sandbox" / "v4")["warnings"])


def test_course_eval_pairs_predictions_with_what_happened(env, monkeypatch):
    preps = env / "preps"
    preps.mkdir()
    monkeypatch.setattr(course, "PREPS", preps)
    monkeypatch.setattr(knowledge, "KNOWLEDGE", env / "knowledge")
    monkeypatch.setattr(store, "ROOT", env)
    course._VERSIONS.clear()
    unit = "tools-01-shell"
    (preps / f"{unit}.json").write_text(json.dumps(plan_v2()), encoding="utf-8")
    course.record(unit, 0, "open")
    course.record(unit, 0, "term", node="tools.cmd.pwd", action="open")
    course.record(unit, 0, "term", node="tools.cmd.pwd", action="unknown")
    course.record(unit, 0, "term", node="tools.cmd.cd", action="known")
    course.record(unit, 0, "term_miss", text="工作目录")
    course.check(unit, 0, 0, ["A"])
    course.check(unit, 0, 0, ["B"])
    course.check(unit, 0, 1, [".."])
    course.hint(unit, 0, 0)
    course.record(unit, 0, "load_rating", rating=3)
    r = course_eval.evaluate(unit)
    row = r["rows"][0]
    assert (row["opened"], row["unknown"], row["known"], row["misses"]) == (1, 1, 1, ["工作目录"])
    assert row["first_try"] == [False, True] and row["attempts"] == [2, 1] and row["max_hint"] == 1
    assert row["rating"] == 3 and row["predicted_new_terms"] == 2 and row["passed"]
    s = r["summary"]
    assert s["term_precision"] == 0.5 and s["first_try_rate"] == 0.5
    assert any("第一次就做对的只有 50%" in a for a in s["advice"])
    assert "| 1. a |" in course_eval.format_report(r)


def test_spearman():
    assert course_eval._spearman([1, 2, 3], [1, 2, 3]) == 1.0
    assert course_eval._spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert course_eval._spearman([1, 2], [1, 2]) is None
