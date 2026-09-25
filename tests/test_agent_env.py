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


def test_rubric_ids_read_from_markdown():
    assert evaluate.rubric_ids("- `grounded` 有据\n- `concise` 简洁\n普通行") == ["grounded", "concise"]
