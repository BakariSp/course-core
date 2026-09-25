import json

import pytest

from studykit.agent_env import evaluate, runner, tools
from studykit.agent_env import __main__ as bridge

HOSTS = {"missing.csail.mit.edu", "youtube.com"}


def page(body: str, title: str = "T", links: str = "") -> str:
    return f"<html><head><title>{title}</title><style>x{{}}</style></head><body>{links}<p>{body}</p></body></html>"


@pytest.fixture
def ctx(tmp_path):
    return tools.RunContext(tmp_path, HOSTS, ["学什么", "资源"])


def fake_get(pages: dict):
    def get(url, timeout=20):
        if url not in pages:
            import urllib.error
            raise urllib.error.HTTPError(url, 404, "nf", {}, None)
        return 200, url, pages[url]
    return get


# ---------- 白名单 ----------

def test_allowlist_comes_from_curriculum(tmp_path):
    cur = tmp_path / "c.md"
    cur.write_text("[a](https://www.youtube.com/playlist?list=1) and https://missing.csail.mit.edu/2026/", encoding="utf-8")
    assert tools.allowed_hosts(cur) == {"youtube.com", "missing.csail.mit.edu"}


@pytest.mark.parametrize("url,ok", [
    ("https://missing.csail.mit.edu/2026/", True),
    ("https://www.youtube.com/watch?v=1", True),
    ("https://evil.example/", False),
    ("file:///etc/passwd", False),
    ("http://missing.csail.mit.edu.evil.example/", False),
])
def test_is_allowed(url, ok):
    assert tools.is_allowed(url, HOSTS) is ok


# ---------- fetch_url ----------

def test_fetch_returns_text_and_allowlisted_links_and_logs(ctx):
    html = page("hello shell", "Shell", '<a href="/2026/git/">Git</a><a href="https://evil.example/">x</a>')
    out = tools.fetch_url(ctx, "https://missing.csail.mit.edu/2026/", _get=fake_get({"https://missing.csail.mit.edu/2026/": html}))
    assert "# Shell" in out and "hello shell" in out and "x{}" not in out
    assert "Git → https://missing.csail.mit.edu/2026/git/" in out and "evil.example" not in out
    log = ctx.fetched()[0]
    assert log["ok"] and log["links"] == ["https://missing.csail.mit.edu/2026/git/"]


def test_fetch_rejects_non_allowlisted(ctx):
    with pytest.raises(tools.ToolError, match="白名单"):
        tools.fetch_url(ctx, "https://evil.example/", _get=fake_get({}))


def test_fetch_404_is_tool_error_and_logged(ctx):
    with pytest.raises(tools.ToolError, match="404"):
        tools.fetch_url(ctx, "https://missing.csail.mit.edu/nope/", _get=fake_get({}))
    assert ctx.fetched()[0]["ok"] is False


def test_long_page_can_be_read_in_parts(ctx):
    body = "A" * tools.MAX_TEXT + "TAIL-EXERCISES"
    get = fake_get({"https://missing.csail.mit.edu/2026/x/": page(body)})
    first = tools.fetch_url(ctx, "https://missing.csail.mit.edu/2026/x/", _get=get)
    assert "TAIL-EXERCISES" not in first
    assert "start=" in first                      # 告诉模型怎么继续读
    nxt = int(first.split("start=")[1].split(")")[0])
    second = tools.fetch_url(ctx, "https://missing.csail.mit.edu/2026/x/", start=nxt, _get=get)
    assert "TAIL-EXERCISES" in second


def test_fragment_is_same_page_and_repeat_read_is_short(ctx):
    url = "https://missing.csail.mit.edu/2026/x/"
    get = fake_get({url: page("content " * 50)})
    tools.fetch_url(ctx, url, _get=get)
    again = tools.fetch_url(ctx, url + "#exercises", _get=get)
    assert "已经读过" in again and len(again) < 300


# ---------- submit ----------

def test_submit_requires_sections_and_grounded_links(ctx):
    url = "https://missing.csail.mit.edu/2026/"
    tools.fetch_url(ctx, url, _get=fake_get({url: page("x", links='<a href="/2026/git/">g</a>')}))
    bad = "## 学什么\n见 https://missing.csail.mit.edu/2026/made-up/"
    errors = tools.check_submission(ctx, bad)
    assert any("资源" in e for e in errors) and any("made-up" in e for e in errors)
    tools.fetch_url(ctx, "https://missing.csail.mit.edu/2026/git/",
                    _get=fake_get({"https://missing.csail.mit.edu/2026/git/": page("git")}))
    good = ("## 学什么\nx https://missing.csail.mit.edu/2026/\n## 资源\n- https://missing.csail.mit.edu/2026/git/\n"
            "- 练习：https://missing.csail.mit.edu/2026/#exercises")          # 锚点指向读过的页面，也算有出处
    assert tools.check_submission(ctx, good) == []
    assert "通过" in tools.submit(ctx, good)
    assert (ctx.run_dir / "output.md").read_text(encoding="utf-8") == good


def test_link_only_seen_on_a_page_is_not_enough(ctx):
    # 页面上出现过但没打开过的链接，不能交给学习者：没读过就不知道里面是什么。
    url = "https://missing.csail.mit.edu/2026/"
    tools.fetch_url(ctx, url, _get=fake_get({url: page("x", links='<a href="/2020/">old</a>')}))
    errors = tools.check_submission(ctx, "## 学什么\n## 资源\n- https://missing.csail.mit.edu/2020/")
    assert any("/2020/" in e and "打开" in e for e in errors)


def test_submit_rejection_is_tool_error(ctx):
    with pytest.raises(tools.ToolError, match="缺少"):
        tools.submit(ctx, "no headings")
    assert not (ctx.run_dir / "output.md").exists()


# ---------- submit_plan（课程计划，D-010） ----------

SRC = "https://missing.csail.mit.edu/2026/"


def checkpoint(**kw):
    c = {"type": "choice", "prompt": "pwd 打印的是什么？", "options": ["上一个目录", "当前目录"], "answer": "B",
         "concept": "tools.shell.cwd", "hints": ["方向", "关键概念", "接近答案"], "explain": "pwd = print working directory"}
    c.update(kw)
    return c


def section(title="导航", minutes=20, **kw):
    s = {"title": title, "minutes": minutes, "goal": "能用 cd 和 pwd 在目录间移动",
         "explain": "shell 里有一个'当前目录'的概念。" * 10,
         "terms": [{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"}],
         "try": [{"command": "pwd", "expect": "打印出当前目录，如 /c/Users/you"}],
         "checkpoint": [checkpoint()]}
    s.update(kw)
    return s


NODES = [{"id": "tools.cmd.pwd", "title": "pwd", "desc": "打印当前目录", "kind": "term"},
         {"id": "tools.shell.cwd", "title": "当前目录", "desc": "shell 此刻所在的目录", "kind": "concept"}]


def plan_of(*sections, outcomes=3, **kw):
    p = {"title": "Shell", "summary": "学 shell 基础。", "parts": [{"title": "第一部分", "sections": list(sections)}],
         "nodes": NODES, "sources": [{"title": "讲义", "url": SRC}], "outcomes": [f"能做 {i}" for i in range(outcomes)]}
    p.update(kw)
    return p


@pytest.fixture
def plan_ctx(tmp_path):
    c = tools.RunContext(tmp_path, HOSTS, [], unit_budget_minutes=180, session_minutes=45, unit="tools-01-shell")
    tools.fetch_url(c, SRC, _get=fake_get({SRC: page("x")}))
    return c


def test_valid_plan_is_accepted_and_rendered(plan_ctx):
    plan = plan_of(section(), section("管道", 30))
    assert tools.check_plan(plan_ctx, plan) == []
    assert "通过" in tools.submit_plan(plan_ctx, plan)
    saved = json.loads((plan_ctx.run_dir / "output.json").read_text(encoding="utf-8"))
    assert saved["title"] == "Shell"
    md = (plan_ctx.run_dir / "output.md").read_text(encoding="utf-8")
    assert "### 2. 管道（30 分钟）" in md and "总时长 50 分钟" in md
    assert "答案：B" in md and "提示 3：接近答案" in md       # 审阅版带答案，网页版由 course.public_plan 去掉


def test_over_budget_plan_is_rejected(plan_ctx):
    # INVARIANT: 时间预算是硬约束（F-014）
    plan = plan_of(*[section(f"节{i}", 40) for i in range(5)])      # 200 分钟 > 180
    assert any("超过单元预算" in e for e in tools.check_plan(plan_ctx, plan))
    with pytest.raises(tools.ToolError):
        tools.submit_plan(plan_ctx, plan)
    assert not (plan_ctx.run_dir / "output.json").exists()


def test_section_longer_than_one_session_is_rejected(plan_ctx):
    assert any("单次学习上限" in e for e in tools.check_plan(plan_ctx, plan_of(section(minutes=60))))


@pytest.mark.parametrize("bad,expected", [
    ({"explain": "去看讲义。"}, "explain 太短"),                                   # 指路不是讲课（F-013）
    ({"try": []}, "至少要有一条动手"),
    ({"pitfalls": [{"symptom": "报错", "cause": "c", "fix": "x"}]}, "不要再写 pitfalls"),   # D-015
    ({"checkpoint": []}, "检查点要 1–3 道题"),                                     # D-013
    ({"checkpoint": [checkpoint(hints=["只有一级"])]}, "正好 3 级"),
    ({"checkpoint": [checkpoint(answer="C")]}, "answer 要是选项字母"),
    ({"checkpoint": [checkpoint(type="fill", prompt="____ 和 ____", accept=[["a"]])]}, "____ 的个数"),
    ({"checkpoint": [checkpoint(concept="tools.nope")]}, "不在知识图里"),
    ({"checkpoint": [checkpoint(type="lab", checks=[{"run": "ls", "desc": "有文件"}])]}, "要有 solution"),
    ({"try": [{"command": "ls | grep x", "expect": "e"}]}, "还不认识的命令 grep, ls"),   # D-017
    ({"explain": "用 `sed -i s/a/b/ f` 改文件。" * 20}, "还不认识的命令 sed"),
])
def test_plan_section_requirements(plan_ctx, bad, expected):
    errors = tools.check_plan(plan_ctx, plan_of(section(**bad)))
    assert any(expected in e for e in errors), errors


def test_unopened_links_are_rejected(plan_ctx):
    plan = plan_of(section(), sources=[{"title": "没打开过", "url": "https://missing.csail.mit.edu/2020/"}])
    assert any("没有打开过" in e for e in tools.check_plan(plan_ctx, plan))


def test_new_term_budget_uses_what_the_learner_already_knows(plan_ctx):
    # INVARIANT: 每节新词数是硬约束（F-019、D-017）；已经掌握的词不算新词。
    terms = [{"id": f"tools.cmd.c{i}", "term": f"c{i}", "explain": "x"} for i in range(6)]
    nodes = NODES + [{"id": t["id"], "title": t["term"], "desc": "x", "kind": "term"} for t in terms]
    plan = plan_of(section(terms=terms), nodes=nodes)
    assert any("超过每节上限 5 个" in e for e in tools.check_plan(plan_ctx, plan))
    plan_ctx.known_terms = {"c0", "tools.cmd.c1"}
    assert not any("超过每节上限" in e for e in tools.check_plan(plan_ctx, plan))


def test_terms_learned_in_earlier_sections_are_known_later(plan_ctx):
    later = section("第二节", terms=[])          # 第二节又用 pwd，但它在第一节已经是新词了
    assert tools.check_plan(plan_ctx, plan_of(section(), later)) == []


def test_knowledge_nodes_are_checked(plan_ctx):
    bad = [{"id": "Shell.X", "title": "x", "desc": "d", "kind": "term"},
           {"id": "net.http", "title": "x", "desc": "d", "kind": "concept"},
           {"id": "tools.a", "title": "a", "desc": "d", "kind": "concept", "requires": ["tools.missing"]}]
    errors = "\n".join(tools.check_plan(plan_ctx, plan_of(section(), nodes=NODES + bad)))
    assert "id 格式不对：Shell.X" in errors and "要以学科 tools. 开头" in errors and "tools.missing" in errors
    plan_ctx.existing_nodes = {"tools.cmd.pwd": "pwd"}
    assert any("已经在知识图里了" in e for e in tools.check_plan(plan_ctx, plan_of(section())))


def test_lab_is_required_and_paths_are_checked(plan_ctx):
    lab_cp = checkpoint(type="lab", checks=[{"file": "report.txt", "contains": "3", "desc": "报告"}], solution=["echo 3 > report.txt"])
    assert any("没有定义练习场" in e for e in tools.check_plan(plan_ctx, plan_of(section(checkpoint=[lab_cp]))))
    lab = {"story": "接手一个服务器", "files": [{"path": "../evil", "content": "x"}, {"path": "logs/", "content": ""}]}
    errors = tools.check_plan(plan_ctx, plan_of(section(checkpoint=[lab_cp]), lab=lab))
    assert [e for e in errors if "相对路径" in e] == ["lab 文件路径要是练习场里的相对路径：'../evil'"]


def test_command_words():
    assert tools.command_words("cp a b && sed -i 's/x/y/' f | grep -c x; for f in *.txt; do echo $f; done") == \
        {"cp", "sed", "grep", "for", "echo"}
    assert tools.command_words("X=1 python3 -V") == {"python3"}
    assert tools.inline_commands("用 `g` 标志，或者 `ls -l` 看看 `app.log`") == {"ls"}


def test_outcomes_count(plan_ctx):
    assert any("outcomes" in e for e in tools.check_plan(plan_ctx, plan_of(section(), outcomes=1)))


def test_sections_are_grouped_into_sessions():
    plan = plan_of(section(minutes=15), section(minutes=25), section(minutes=30), section(minutes=10))
    assert tools.plan_sessions(plan, 45) == [[0, 1], [2, 3]]


# ---------- 适配器入口 ----------

def test_bridge_schema_and_call(tmp_path, capsys, monkeypatch):
    bridge.main(["schema", "fetch_url,submit"])
    names = [t["name"] for t in json.loads(capsys.readouterr().out)]
    assert names == ["fetch_url", "submit"]
    (tmp_path / "context.json").write_text(json.dumps({"hosts": ["x.org"], "required_sections": []}), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", type("S", (), {"buffer": type("B", (), {"read": lambda self: b'{"url":"https://evil.example/"}'})()})())
    bridge.main(["call", "fetch_url", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "白名单" in out["text"]


# ---------- runner ----------

def test_summarize_events_counts_tools_tokens_and_final_text():
    lines = [json.dumps(e) for e in [
        {"type": "tool_execution_end", "toolName": "fetch_url", "isError": False},
        {"type": "tool_execution_end", "toolName": "fetch_url", "isError": True},
        {"type": "message_end", "message": {"role": "assistant", "usage": {"totalTokens": 100, "cost": {"total": 0.01}},
                                             "content": [{"type": "text", "text": "done"}]}},
        {"type": "message_end", "message": {"role": "user", "usage": {"totalTokens": 999}}},
    ]] + ["not json"]
    s = runner.summarize_events(lines)
    assert s["tool_calls"] == {"fetch_url": {"calls": 2, "errors": 1}}
    assert s["tokens"] == 100 and s["final_text"] == "done"


def test_brief_fills_unit_curriculum_and_learner():
    agent = runner.load_agent("tutor-prep")
    brief = runner.build_brief(agent, "tools-01-shell")
    assert "tools-01-shell" in brief and "missing.csail.mit.edu" in brief and "Windows" in brief
    assert "{" not in brief.split("# 课程安排")[0]          # 模板占位符都填上了


def test_local_env_parsing(tmp_path):
    p = tmp_path / "local.env"
    p.write_text('# c\nDEEPSEEK_API_KEY = "sk-1"\nBAD LINE\nX=2\n', encoding="utf-8")
    assert runner.load_local_env(p) == {"DEEPSEEK_API_KEY": "sk-1", "X": "2"}


def test_unknown_agent_name_rejected():
    with pytest.raises(runner.AgentError):
        runner.load_agent("../x")


# ---------- evaluate ----------

def test_parse_judge_validates_scores():
    ids = ["a", "b"]
    data = evaluate.parse_judge('前言 {"scores": {"a": {"score": 4}, "b": {"score": 5}}, "top_issue": "x"} 后记', ids)
    assert data["avg"] == 4.5
    with pytest.raises(ValueError):
        evaluate.parse_judge('{"scores": {"a": {"score": 9}, "b": {"score": 1}}}', ids)
    with pytest.raises(ValueError):
        evaluate.parse_judge('{"scores": {"a": {"score": 3}}}', ids)
    with pytest.raises(ValueError):
        evaluate.parse_judge("没有 JSON", ids)


def test_judge_suspicions_are_checked_against_pages_the_agent_read(tmp_path):
    # 评分模型看不到页面正文，它怀疑"编造"的地方要用 agent 实际读到的内容核对。
    events = [
        {"type": "tool_execution_end", "toolName": "fetch_url", "isError": False,
         "result": {"content": [{"type": "text", "text": "$ pytest --version\npytest 9.1.1\n"}]}},
        {"type": "tool_execution_end", "toolName": "submit", "isError": False,
         "result": {"content": [{"type": "text", "text": "pytest 7.0.0 appears only in the submission"}]}},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    checked = evaluate.verify_suspicions(tmp_path, [
        {"claim": "版本号是编造的", "quote": "pytest 9.1.1"},
        {"claim": "另一个版本号", "quote": "pytest 7.0.0"},
    ])
    assert [c["found_in_pages"] for c in checked] == [True, False]


def test_url_extraction_stops_at_chinese_punctuation():
    text = "见讲义（https://missing.csail.mit.edu/2026/course-shell/）。另见「https://x.org/a」，还有 https://x.org/b。"
    assert tools.extract_urls(text) == {"https://missing.csail.mit.edu/2026/course-shell/", "https://x.org/a", "https://x.org/b"}


def test_rubric_ids_read_from_markdown():
    assert evaluate.rubric_ids("- `grounded` 有据\n- `concise` 简洁\n普通行") == ["grounded", "concise"]
