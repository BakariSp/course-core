import pytest

from studykit import knowledge, store


@pytest.fixture
def kg(env, monkeypatch):
    monkeypatch.setattr(knowledge, "KNOWLEDGE", env / "knowledge")
    knowledge.add_nodes([
        {"id": "tools.shell.role", "title": "终端 / shell / 命令", "kind": "concept", "units": ["tools-00-intro"]},
        {"id": "tools.shell.glob", "title": "通配符", "kind": "concept", "requires": ["tools.shell.role"], "units": ["tools-01-shell"]},
        {"id": "tools.cmd.sed", "title": "sed", "kind": "term", "requires": ["tools.shell.glob"], "units": ["tools-01-shell"]},
        {"id": "tools.cmd.grep", "title": "grep", "kind": "term", "units": ["tools-01-shell"]},
    ])
    return env


def test_add_nodes_keeps_reviewed_content(kg):
    assert knowledge.add_nodes([{"id": "tools.cmd.sed", "title": "别的标题", "kind": "term"}]) == []
    assert knowledge.load_graph()["tools.cmd.sed"].title == "sed"


def test_validate_finds_bad_ids_missing_prereqs_and_cycles():
    N = knowledge.Node
    nodes = {"a.x": N("a.x", "x", requires=["a.y"]), "a.y": N("a.y", "y", requires=["a.x"]),
             "Bad": N("Bad", "b"), "a.z": N("a.z", "z", requires=["a.nope"])}
    errs = "\n".join(knowledge.validate(nodes))
    assert "有环" in errs and "Bad" in errs and "a.nope" in errs


def test_state_is_derived_from_latest_evidence(kg):
    sts = knowledge.states()
    assert sts["tools.cmd.sed"].state == "new"
    store.record_study(event="checkpoint", unit="tools-01-shell", section=4, idx=0, ok=False, nodes=["tools.cmd.sed"])
    assert knowledge.states()["tools.cmd.sed"].state == "weak"
    store.record_study(event="checkpoint", unit="tools-01-shell", section=4, idx=0, ok=True, nodes=["tools.cmd.sed"])
    assert knowledge.states()["tools.cmd.sed"].state == "mastered"


def test_term_votes_and_observations(kg):
    store.record_study(event="term", unit="u", section=0, node="tools.cmd.grep", action="unknown")
    assert knowledge.states()["tools.cmd.grep"].state == "learning"       # 学之前不认识 ≠ 薄弱
    store.record_study(event="term", unit="u", section=0, node="tools.cmd.grep", action="known")
    assert "grep" in knowledge.known_titles("tools")
    knowledge.observe("tools.shell.glob", "weak", "讲 glob 那段完全跟不上")
    s = knowledge.states()["tools.shell.glob"]
    assert s.state == "weak" and s.reasons == ["讲 glob 那段完全跟不上"]


def test_attempts_override_with_mastery_rules(kg):
    for _ in range(3):
        store.record(quiz="t/01", qid="q", concept="tools.shell.glob", level=1, score=0, result="fail", grader="auto")
    assert knowledge.states()["tools.shell.glob"].state == "weak"


def test_related_only_returns_this_units_neighbourhood(kg):
    knowledge.observe("tools.shell.role", "weak", "分不清终端和 shell")
    r = knowledge.related("tools-01-shell")
    assert {n["id"] for n in r["taught"]} == {"tools.shell.glob", "tools.cmd.sed", "tools.cmd.grep"}
    assert [n["id"] for n in r["prerequisites_not_mastered"]] == ["tools.shell.role"]
    assert [n["id"] for n in r["weak"]] == ["tools.shell.role"]
    assert knowledge.related("tools-02-git")["taught"] == []


def test_summary_and_tree(kg):
    knowledge.observe("tools.cmd.sed", "weak", "x")
    assert "sed" in knowledge.summary()
    t = knowledge.tree("tools")
    depth = {n["id"]: n["depth"] for n in t["nodes"]}
    assert depth == {"tools.shell.role": 0, "tools.shell.glob": 1, "tools.cmd.sed": 2, "tools.cmd.grep": 0}
    assert ["tools.shell.glob", "tools.cmd.sed"] in t["edges"]
    assert knowledge.show("tools.cmd.sed")["requires"][0]["id"] == "tools.shell.glob"
