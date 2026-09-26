"""harness（D-022）：agent 的工具、运行记录、评测、发布闸门、按版本对比。agent loop 用假的（FakeRuntime）。"""
import json

import pytest

from studykit import agent_tools
from studykit.adapters.pi import steps_from_events
from studykit.app.harness import ToolError, allowed_hosts, is_allowed
from studykit.domain.errors import DomainError
from studykit.domain.harness import Grade

SRC = "https://missing.csail.mit.edu/2026/"
OTHER = "https://missing.csail.mit.edu/2026/shell/"


@pytest.fixture
def ws(app, fetcher):
    """一个运行目录（和 run() 建的一样：input.json 是冻结的输入）。"""
    fetcher.pages = {SRC: "shell 讲义正文 " * 10, OTHER: "x" * 40000}
    d = app.harness.dir("tutor-prep", "20260926-100000-tools-01-shell")
    d.mkdir(parents=True)
    (d / "input.json").write_text(json.dumps(app.harness.build_input("tools-01-shell"), ensure_ascii=False), encoding="utf-8")
    return d


def checkpoint(**kw):
    c = {"type": "choice", "prompt": "pwd 打印的是什么？", "options": ["上一个目录", "当前目录"], "answer": "B",
         "concept": "tools.shell.cwd", "hints": ["方向", "关键概念", "接近答案"], "explain": "pwd = print working directory",
         "traps": [{"when": "A", "symptom": "s", "cause": "c", "fix": "f"}]}
    c.update(kw)
    return c


def section(title="导航", minutes=20, **kw):
    s = {"title": title, "minutes": minutes, "goal": "能用 pwd 知道自己在哪", "explain": "shell 里有一个'当前目录'的概念。" * 10,
         "terms": [{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"}],
         "try": [{"command": "pwd", "expect": "打印出当前目录"}], "checkpoint": [checkpoint()]}
    s.update(kw)
    return s


NODES = [{"id": "tools.cmd.pwd", "title": "pwd", "desc": "打印当前目录", "kind": "term"},
         {"id": "tools.shell.cwd", "title": "当前目录", "desc": "shell 此刻所在的目录", "kind": "concept"}]


def plan_of(*sections, outcomes=3, **kw):
    p = {"title": "Shell", "summary": "学 shell 基础。", "parts": [{"title": "第一部分", "sections": list(sections)}],
         "nodes": NODES, "sources": [{"title": "讲义", "url": SRC}], "outcomes": [f"能做 {i}" for i in range(outcomes)]}
    p.update(kw)
    return p


def call(app, ws, name, **args):
    return app.harness.call_tool(ws, name, args)


# ---------- fetch_url ----------

def test_allowlist_comes_from_curriculum():
    hosts = allowed_hosts("[a](https://www.youtube.com/x) 和 https://docs.pytest.org/en/")
    assert hosts == {"youtube.com", "docs.pytest.org"}
    assert is_allowed("https://youtube.com/watch", hosts) and not is_allowed("file:///etc/passwd", hosts)
    assert not is_allowed("https://evil.com/?https://youtube.com", hosts)


def test_fetch_logs_text_pages_long_ones_and_rejects_others(app, ws):
    out = call(app, ws, "fetch_url", url=SRC + "#part")
    assert "shell 讲义正文" in out and "页面里的白名单链接" in out and OTHER in out
    assert "已经读过" in call(app, ws, "fetch_url", url=SRC)               # 锚点是同一页，重复读只给短提示
    first = call(app, ws, "fetch_url", url=OTHER)
    assert "start=15000" in first
    assert "已读到结尾" in call(app, ws, "fetch_url", url=OTHER, start=30000)
    with pytest.raises(ToolError, match="白名单"):
        call(app, ws, "fetch_url", url="https://evil.com/")
    with pytest.raises(ToolError, match="404"):
        call(app, ws, "fetch_url", url=SRC + "missing/")
    log = [json.loads(l) for l in (ws / "fetches.jsonl").read_text("utf-8").splitlines()]
    assert log[0]["text"].startswith("shell 讲义正文") and log[-1] == {"url": SRC + "missing/", "status": 404, "ok": False}


# ---------- submit_plan ----------

def test_valid_plan_is_accepted_and_rendered(app, ws):
    call(app, ws, "fetch_url", url=SRC)
    assert "通过" in call(app, ws, "submit_plan", plan=plan_of(section(), section("管道", 30)))
    md = (ws / "output.md").read_text("utf-8")
    assert "### 2. 管道（30 分钟）" in md and "答案：B" in md and "提示 3：接近答案" in md


@pytest.mark.parametrize("bad,expected", [
    ({"minutes": 60}, "单次学习上限"),
    ({"explain": "去看讲义。"}, "explain 太短"),                                   # 指路不是讲课（F-013）
    ({"try": []}, "至少要有一条动手"),
    ({"pitfalls": [{"symptom": "报错"}]}, "不要再写 pitfalls"),
    ({"checkpoint": []}, "检查点要 1–3 道题"),
    ({"checkpoint": [checkpoint(hints=["只有一级"])]}, "正好 3 级"),
    ({"checkpoint": [checkpoint(answer="C")]}, "answer 要是选项字母"),
    ({"checkpoint": [checkpoint(type="fill", prompt="____ 和 ____", accept=[["a"]])]}, "____ 的个数"),
    ({"checkpoint": [checkpoint(type="essay")]}, "type 只能是 choice / fill / lab"),
    ({"checkpoint": [checkpoint(concept="tools.nope")]}, "不在知识图里"),
    ({"checkpoint": [checkpoint(concept=None)]}, "缺少 concept"),                  # 评测要求必填，环境也要拦（D-025）
    ({"checkpoint": [checkpoint(type="lab", checks=[{"run": "ls", "desc": "有文件"}])]}, "要有 solution"),   # spec 规则
    ({"try": [{"command": "ls | grep x", "expect": "e"}]}, "还不认识的命令 grep, ls"),                    # spec 规则
])
def test_plan_requirements(app, ws, bad, expected):
    call(app, ws, "fetch_url", url=SRC)
    errors = [f.what for f in app.harness.plan_findings(ws, plan_of(section(**bad)))]
    assert any(expected in e for e in errors), errors
    with pytest.raises(ToolError):
        call(app, ws, "submit_plan", plan=plan_of(section(**bad)))
    assert not (ws / "output.json").exists()


def test_plan_check_reports_findings_with_addresses(app, ws):
    """D-035：环境检查的每个问题都指向计划里的一个位置，修复时只改那一处。"""
    call(app, ws, "fetch_url", url=SRC)
    plan = plan_of(section(minutes=60), section("管道", checkpoint=[checkpoint(hints=["只有一级"])]),
                   nodes=NODES + [{"id": "tools.x", "title": "x", "desc": "", "kind": "term"}])
    found = {(f.address, f.evaluator) for f in app.harness.plan_findings(ws, plan)}
    assert ("/sections/0", "plan_check") in found                        # 超过单次上限
    assert ("/sections/1/checkpoint/0", "plan_check") in found           # 提示不是 3 级
    assert ("/nodes", "plan_check") in found                             # 提议的节点缺 desc


def test_budget_links_terms_and_nodes(app, ws):
    call(app, ws, "fetch_url", url=SRC)
    errs = lambda p: "\n".join(f.what for f in app.harness.plan_findings(ws, p))  # noqa: E731
    assert "超过单元预算" in errs(plan_of(*[section(f"节{i}", 40) for i in range(5)]))       # 200 > 180（F-014）
    assert "没有打开过" in errs(plan_of(section(), sources=[{"title": "x", "url": "https://missing.csail.mit.edu/2020/"}]))
    terms = [{"id": f"tools.cmd.c{i}", "term": f"c{i}", "explain": "x"} for i in range(6)]
    nodes = NODES + [{"id": t["id"], "title": t["term"], "desc": "x", "kind": "term"} for t in terms]
    assert "超过每节上限 5 个" in errs(plan_of(section(terms=terms), nodes=nodes))
    assert errs(plan_of(section(), section("第二节", terms=[]))) == ""      # 前面学过的词后面就算认识了
    bad = [{"id": "Shell.X", "title": "x", "desc": "d", "kind": "term"},
           {"id": "net.http", "title": "x", "desc": "d", "kind": "concept"},
           {"id": "tools.a", "title": "a", "desc": "d", "kind": "concept", "requires": ["tools.missing"]}]
    e = errs(plan_of(section(), nodes=NODES + bad))
    assert "id 格式不对：Shell.X" in e and "要以学科 tools. 开头" in e and "tools.missing" in e
    assert "outcomes" in errs(plan_of(section(), outcomes=1))


def test_schema_includes_spec_fields(app):
    fetch, submit, outline, section, repair = app.harness.tool_schemas()
    assert repair["name"] == "submit_repair" and repair["parameters"]["required"] == ["parts"]
    stub = outline["parameters"]["properties"]["outline"]["properties"]["parts"]["items"]["properties"]["sections"]["items"]
    assert {"terms", "check", "reading"} <= set(stub["required"]) and "lab" in outline["parameters"]["properties"]["outline"]["properties"]
    assert "checkpoint" in section["parameters"]["properties"]["section"]["required"]
    cp = submit["parameters"]["properties"]["plan"]["properties"]["parts"]["items"]["properties"]["sections"]["items"][
        "properties"]["checkpoint"]["items"]["properties"]
    assert cp["type"]["enum"] == ["choice", "fill", "lab"] and "solution" in cp
    required = submit["parameters"]["properties"]["plan"]["properties"]["parts"]["items"]["properties"]["sections"][
        "items"]["properties"]["checkpoint"]["items"]["required"]
    assert "concept" in required
    assert "lab" in submit["parameters"]["properties"]["plan"]["properties"] and fetch["name"] == "fetch_url"


def test_agent_tools_entry(app, ws, monkeypatch, capsys):
    monkeypatch.setattr(agent_tools, "build", lambda: app)
    agent_tools.main(["schema", "fetch_url"])
    assert [t["name"] for t in json.loads(capsys.readouterr().out)] == ["fetch_url"]
    monkeypatch.setattr("sys.stdin", type("S", (), {"buffer": type("B", (), {"read": lambda self: b'{"url": "https://evil.com/"}'})()})())
    agent_tools.main(["call", "fetch_url", str(ws)])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "白名单" in out["text"]


# ---------- 一次完整的运行 ----------

JUDGE = json.dumps({"scores": {k: {"score": 4, "reason": "r"} for k in
                               ("teaches", "grounded", "novice_readable", "personalized", "timeboxed", "checkable", "coherent")},
                    "top_issue": "太长", "suggestion": "给字数上限",
                    "suspect_claims": [{"claim": "c", "quote": "shell 讲义正文"}]}, ensure_ascii=False)


def _run(app, runtime, fetcher, plan):
    fetcher.pages = {SRC: "shell 讲义正文 " * 10}
    runtime.script = [("fetch_url", {"url": SRC}), ("submit_plan", {"plan": {"title": "坏的"}}), ("submit_plan", {"plan": plan})]
    runtime.judge_reply = JUDGE
    return app.harness.run("tutor-prep", "tools-01-shell")


def test_run_records_input_steps_and_variant(app, runtime, fetcher):
    run = _run(app, runtime, fetcher, plan_of(section(), section("b"), section("c"), section("d")))
    assert run.submitted and run.tokens == 1000 and run.tool_calls["submit_plan"] == {"calls": 2, "errors": 1}
    assert run.input["unit"] == "tools-01-shell" and "hosts" in run.input
    steps = app.store.steps(run.id)
    assert [s.tool for s in steps if s.kind == "tool"] == ["fetch_url", "submit_plan", "submit_plan"]
    assert "没有通过检查" in steps[1].summary                          # 运行中被退回、自己改了（自我修正）
    v = app.store.variant(run.variant)
    assert {"task", "tools", "context", "model", "runtime", "prompt:role", "prompt:rules"} <= set(v.parts)
    assert v.parts["runtime"] == "fake-1"
    assert app.harness.resolve("tutor-prep").id == run.id
    with pytest.raises(DomainError):
        app.harness.resolve("tutor-prep", "nope")


def test_evaluate_publish_gate_and_outcome(app, runtime, fetcher, clock):
    plan = plan_of(section(), section("b"), section("c"), section("d"))
    run = _run(app, runtime, fetcher, plan)
    grades = app.harness.evaluate(run)
    check, judge, claims = grades
    assert check.grader == "check" and check.dims["plan_valid"]["score"] == 1.0
    assert judge.detail["avg"] == 4 and judge.score == 0.75 and judge.issues == [{"layer": "prompt", "what": "给字数上限"}]
    assert claims.detail["claims"][0]["found_in_pages"]
    assert "评分" in app.harness.format_eval(grades)
    assert "Git Bash" in check.dims["mention:Git Bash"]["reason"]        # 用例要求提到 Git Bash，这份计划没提
    assert {"address": "/sections/0", "evaluator": "check"}.items() <= next(
        f for f in check.detail["findings"] if "Git Bash" in f["what"]).items()   # 没有位置的要求，指到第 1 节
    with pytest.raises(DomainError, match="没有通过产出循环"):                  # 发布闸门 = 产出循环放行（test_prep.py）
        app.harness.publish(run)
    app.store.add_grade(Grade("g-ok", clock.now().isoformat(), run.id, "loop", "v", "system", None, "accepted", {}))
    added = app.harness.publish(run)
    assert added == ["tools.cmd.pwd", "tools.shell.cwd"]
    page = app.course.page("tools-01-shell")
    assert page["plan"]["provenance"]["run"] == run.id and page["plan"]["provenance"]["variant"] == run.variant
    app.course.check("tools-01-shell", 0, 0, ["B"])
    outcome = app.harness.grade_outcome(app.course.evaluate("tools-01-shell"))
    assert outcome.grader == "outcome" and outcome.run == run.id and outcome.dims["first_try_rate"] == 1.0
    assert "| `" + run.variant + "` |" in app.harness.report("tutor-prep")


def test_review_is_structured(app, runtime, fetcher):
    run = _run(app, runtime, fetcher, plan_of(section()))
    g = app.harness.review(run, "revise", "太长", [{"layer": "prompt", "what": "给字数上限"}])
    assert g.grader == "review" and g.actor == "tutor"
    with pytest.raises(DomainError):
        app.harness.review(run, "maybe", "", [])
    with pytest.raises(DomainError):
        app.harness.review(run, "revise", "", [{"layer": "vibes", "what": "x"}])


def test_variant_changes_when_a_component_changes(app, root):
    agent = app.harness.agent("tutor-prep")
    before = app.harness.variant(agent, app.harness.model(agent))
    (root / "agents" / "tutor-prep" / "task.md").write_text("新的任务模板 {unit_id}", encoding="utf-8")
    after = app.harness.variant(agent, app.harness.model(agent))
    assert after.diff(before) == ["task"]
    assert app.harness.variant(agent, app.harness.model(agent, "other-model")).diff(before) == ["model", "task"]
    with pytest.raises(DomainError):
        app.harness.agent("../x")


# ---------- pi 适配器 ----------

def test_pi_events_become_steps():
    lines = [json.dumps(e) for e in [
        {"type": "message_update", "delta": "逐字输出"},
        {"type": "message_end", "message": {"role": "assistant", "usage": {"totalTokens": 100, "cost": {"total": 0.01}},
                                            "content": [{"type": "text", "text": "先读讲义"}, {"type": "toolCall", "name": "fetch_url"}]}},
        {"type": "tool_execution_start", "toolCallId": "c1", "args": {"url": "u"}},
        {"type": "tool_execution_end", "toolCallId": "c1", "toolName": "fetch_url", "isError": True,
         "result": {"content": [{"type": "text", "text": "HTTP 404"}]}},
        {"type": "message_end", "message": {"role": "assistant", "errorMessage": "rate limited", "content": []}},
    ]] + ["不是 JSON"]
    steps = steps_from_events(lines)
    assert [(s.kind, s.tool, s.ok) for s in steps] == [("model", "", True), ("tool", "fetch_url", False), ("error", "", False)]
    assert steps[0].tokens == 100 and steps[0].detail == {"tool_calls": ["fetch_url"]} and steps[1].detail == {"args": {"url": "u"}}


# ---------- prompt 版本（D-033） ----------

def test_every_prompt_part_is_versioned_with_its_content(app, runtime, fetcher, root, clock):
    plan = plan_of(section(), section("b"), section("c"), section("d"))
    r1 = _run(app, runtime, fetcher, plan)
    agent_dir = root / "agents" / "tutor-prep"
    v1 = app.store.variant(r1.variant)
    names = [k for k in v1.parts if k.startswith("prompt:")]
    assert names == ["prompt:role", "prompt:principles", "prompt:learner", "prompt:tools", "prompt:workflow", "prompt:rules"]
    ws = app.harness.dir("tutor-prep", r1.id)
    assert runtime.system_prompt == ws / "system.md"                  # agent 读的就是这份拼好的
    parts = "".join((agent_dir / "prompt" / f"{n.split(':')[1]}.md").read_text(encoding="utf-8") for n in names)
    assert (ws / "system.md").read_text(encoding="utf-8") == parts
    for k in ("prompt:rules", "task", "context", "tools"):
        assert app.store.blob(v1.parts[k])                            # 每个文本组成的内容都存下来了
    assert app.store.blob(v1.parts["prompt:rules"]) == (agent_dir / "prompt" / "rules.md").read_text(encoding="utf-8")

    judge = next(g for g in app.harness.evaluate(r1) if g.grader == "judge")
    jv = app.store.variant(judge.grader_version)                      # 评分模型也有版本，内容可以取回
    assert {"system", "rubric", "model"} <= set(jv.parts) and app.store.blob(jv.parts["rubric"])

    rules = agent_dir / "prompt" / "rules.md"
    rules.write_text(rules.read_text(encoding="utf-8") + "- 新规则：不写死哈希\n", encoding="utf-8")
    clock.tick()
    r2 = _run(app, runtime, fetcher, plan)
    d = app.harness.versions.diff(r1.variant, r2.variant)
    assert [p["part"] for p in d["parts"]] == ["prompt:rules"] and "+- 新规则：不写死哈希" in d["parts"][0]["diff"]
    hist = app.harness.versions.history("tutor-prep")
    assert [x["variant"] for x in hist][-2:] == [r1.variant, r2.variant] and hist[-1]["runs"] == 1
    assert [p["part"] for p in hist[-1]["changed"]] == ["prompt:rules"]  # 和上一版比改了什么
