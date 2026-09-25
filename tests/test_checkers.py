from pathlib import Path

import pytest

from studykit import checkers, store
from studykit.checkers import code as code_checker
from studykit.checkers.terminal import Sandbox

CTX = checkers.Ctx("t/01-x", Path("."), Path("."))


# ---------- choice ----------

@pytest.mark.parametrize("multi,answer,response,expected", [
    (False, "B", ["b"], 1.0),
    (False, "B", ["A"], 0.0),
    (True, ["A", "C"], ["C", "A"], 1.0),
    (True, ["A", "C"], ["A"], 0.5),
    (True, ["A", "C"], ["A", "B"], 0.0),          # 选对一个、选错一个，相互抵消
    (True, ["A", "C"], ["A", "B", "C", "D"], 0.0),  # 全选拿不到分
])
def test_choice(multi, answer, response, expected):
    q = {"options": ["a", "b", "c", "d"], "multi": multi}
    assert checkers.get("choice").check(q, {"answer": answer}, CTX, response).score == expected


# ---------- fill ----------

def test_fill_multiple_blanks_partial_credit():
    q = {"prompt": "记忆、____、应用、____"}
    key = {"blanks": [["理解"], ["分析"]]}
    fill = checkers.get("fill")
    assert fill.view(q, CTX) == {"blanks": 2}
    assert fill.check(q, key, CTX, ["理解", "分析"]).score == 1.0
    assert fill.check(q, key, CTX, [" 理解 ", "综合"]).score == 0.5


def test_fill_single_blank_and_regex():
    fill = checkers.get("fill")
    assert fill.check({"prompt": "____"}, {"accept": ["Race Condition"]}, CTX, "race  condition").score == 1.0
    assert fill.check({"prompt": "____"}, {"accept": [r"o\(n\^?2\)"], "regex": True}, CTX, ["O(n^2)"]).score == 1.0


# ---------- short ----------

def test_short_is_pending():
    v = checkers.get("short").check({}, {}, CTX, "我的回答")
    assert v.score is None


# ---------- code ----------

def _code_lesson(tmp_path, body):
    code = tmp_path / "code"
    code.mkdir()
    (code / "ex1.py").write_text(body, encoding="utf-8")
    (code / "test_ex1.py").write_text(
        "from ex1 import f\n"
        "def test_a():\n    assert f(1) == 1\n"
        "def test_b():\n    assert f(2) == 2\n", encoding="utf-8")
    return checkers.Ctx("t/01-x", tmp_path, tmp_path / "sb")


def test_solution_lines_ignore_test_file():
    text = ("lessons\\t\\code\\test_ex1.py:12: in test_a\n"
            "lessons\\t\\code\\ex1.py:3: in f\n"
            'File "C:\\x\\code\\ex1.py", line 7\n'
            "other_ex1.py:9: nope\n")
    assert code_checker.solution_lines(text, "ex1.py") == [3, 7]


# 真实的 pytest --tb=short 输出（Windows 路径）
RUNTIME_TB = """code\\test_ex1.py:8: in test_only_last_window_counts
    assert recent_average([0, 1, 1, 1]) == 1.0
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
code\\ex1.py:15: in recent_average
    total_score += scores[scores_len - n]
                   ^^^^^^^^^^^^^^^^^^^^^^
E   IndexError: list index out of range
"""
ASSERT_TB = """code\\test_ex1.py:8: in test_only_last_window_counts
    assert recent_average([0, 1, 1, 1]) == 1.0
E   assert 0.25 == 1.0
E    +  where 0.25 = recent_average([0, 1, 1, 1])
"""


def test_parse_failure_runtime_error():
    p = code_checker.parse_failure(RUNTIME_TB, "ex1.py", "test_ex1.py")
    assert p["call"] == {"line": 8, "src": "assert recent_average([0, 1, 1, 1]) == 1.0"}
    assert p["error"] == "IndexError: list index out of range"
    assert p["lines"] == [15]


def test_parse_failure_wrong_value_keeps_expected_and_actual():
    p = code_checker.parse_failure(ASSERT_TB, "ex1.py", "test_ex1.py")
    assert p["call"]["src"] == "assert recent_average([0, 1, 1, 1]) == 1.0"
    assert p["error"] == "assert 0.25 == 1.0\n +  where 0.25 = recent_average([0, 1, 1, 1])"
    assert p["lines"] == []


def test_same_error_same_line_is_grouped_with_each_triggering_call():
    code = "\n" * 14 + "    total_score += scores[i]\n"
    same = {"outcome": "failed", "error": "IndexError: x", "lines": [15]}
    tests = [
        {"name": "test_a", **same, "call": {"line": 8, "src": "assert f([1])"}},
        {"name": "test_b", **same, "call": {"line": 12, "src": "assert f([2])"}},
        {"name": "test_c", "outcome": "failed", "error": "assert 1 == 2", "lines": [], "call": None},
        {"name": "test_d", "outcome": "passed", "lines": []},
    ]
    groups = code_checker.group_failures(tests, code)
    assert len(groups) == 2
    assert groups[0]["lines"] == [{"line": 15, "src": "total_score += scores[i]"}]
    assert [t["call"]["src"] for t in groups[0]["tests"]] == ["assert f([1])", "assert f([2])"]
    assert [t["name"] for t in groups[1]["tests"]] == ["test_c"]


def test_same_wrong_return_value_is_one_group_but_keeps_each_expectation():
    tests = [
        {"name": "test_a", "outcome": "failed", "lines": [], "call": {"line": 8, "src": "assert f([1]) == 1.0"},
         "error": "assert None == 1.0\n +  where None = f([1])"},
        {"name": "test_b", "outcome": "failed", "lines": [], "call": {"line": 12, "src": "assert f([]) == 0.0"},
         "error": "assert None == 0.0\n +  where None = f([])"},
        {"name": "test_c", "outcome": "failed", "lines": [], "call": None,
         "error": "assert 0.5 == 0.0\n +  where 0.5 = f([1, 0])"},
    ]
    groups = code_checker.group_failures(tests, "")
    assert len(groups) == 2
    assert groups[0]["summary"] == "assert None == …"
    assert [t["error"].splitlines()[0] for t in groups[0]["tests"]] == ["assert None == 1.0", "assert None == 0.0"]
    assert groups[1]["summary"] == "assert 0.5 == …"


def test_raw_output_is_verbose_and_groups_repeated_errors(tmp_path, env):
    r = _run(tmp_path, "def f(x):\n    y = 0\n    return x / y\n")
    assert "::test_a FAILED" in r["output"]      # -v：每个测试一行结果
    assert "generated xml file" not in r["output"]
    assert len(r["groups"]) == 1 and len(r["groups"][0]["tests"]) == 2
    assert r["groups"][0]["lines"] == [{"line": 3, "src": "return x / y"}]


def _run(tmp_path, body):
    ctx = _code_lesson(tmp_path, body)
    return checkers.get("code").act({"id": "q1", "file": "code/ex1.py"}, {}, ctx, {"op": "run", "code": None})


def test_runtime_error_points_to_solution_line(tmp_path, env):
    r = _run(tmp_path, "def f(x):\n    y = 0\n    return x / y\n")
    assert r["passed"] == 0 and r["total"] == 2
    assert all(t["outcome"] == "failed" and t["lines"] == [3] for t in r["tests"])
    assert "ZeroDivisionError" in r["tests"][0]["message"]


def test_wrong_value_names_the_test_without_solution_lines(tmp_path, env):
    r = _run(tmp_path, "def f(x):\n    return 1\n")
    by_name = {t["name"]: t for t in r["tests"]}
    assert by_name["test_a"]["outcome"] == "passed"
    assert by_name["test_b"]["outcome"] == "failed"
    assert by_name["test_b"]["lines"] == []
    assert "assert 1 == 2" in by_name["test_b"]["message"]


def test_syntax_error_scores_zero_and_points_to_line(tmp_path, env):
    r = _run(tmp_path, "def f(x):\n    return (x\n\n")
    assert r["score"] == 0.0 and r["total"] >= 1
    assert r["tests"][0]["outcome"] == "error"
    assert r["tests"][0]["lines"], r["tests"][0]


def test_every_run_and_submit_is_recorded_with_code(tmp_path, env):
    ctx = _code_lesson(tmp_path, "def f(x):\n    raise NotImplementedError\n")
    code = checkers.get("code")
    q = {"id": "q1", "concept": "t.code", "level": 3, "file": "code/ex1.py"}
    code.act(q, {}, ctx, {"op": "run", "code": "def f(x):\n    return 1\n"})
    code.check(q, {}, ctx, {"code": "def f(x):\n    return x\n"})
    runs = store.load_runs("t/01-x", "q1")
    assert [(r["kind"], r["passed"], r["total"]) for r in runs] == [("run", 1, 2), ("submit", 2, 2)]
    assert "return 1" in runs[0]["code"] and runs[0]["failures"][0]["name"] == "test_b"
    assert store.practice_summary(runs)[0]["first_full_pass_at_run"] == 2


def test_code_scores_pass_ratio_and_saves_submission(tmp_path, env):
    ctx = _code_lesson(tmp_path, "def f(x):\n    raise NotImplementedError\n")
    code = checkers.get("code")
    q = {"id": "q1", "file": "code/ex1.py"}
    assert code.check(q, {}, ctx, {"code": None}).score == 0.0
    v = code.check(q, {}, ctx, {"code": "def f(x):\n    return 1\n"})
    assert v.score == 0.5
    assert "return 1" in (tmp_path / "code" / "ex1.py").read_text("utf-8")


def test_code_output_is_readable_utf8_without_colors(tmp_path, monkeypatch, env):
    # 服务器由别的程序启动时可能带着 FORCE_COLOR，且 Windows 管道默认用 GBK 编码。
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    ctx = _code_lesson(tmp_path, "def f(x):\n    return 0\n")
    (tmp_path / "code" / "test_ex1.py").write_text(
        "from ex1 import f\ndef test_a():\n    assert f(1) == 1, '中文消息'\n", encoding="utf-8")
    act = checkers.get("code").act({"id": "q1", "file": "code/ex1.py"}, {}, ctx, {"op": "run", "code": None})
    assert "中文消息" in act["output"]
    assert "\x1b" not in act["output"]


def test_code_file_cannot_escape_lesson(tmp_path):
    ctx = _code_lesson(tmp_path, "")
    with pytest.raises(ValueError):
        checkers.get("code").view({"file": "../../secret.py"}, ctx)


# ---------- terminal ----------

@pytest.fixture
def repo(tmp_path):
    sb = Sandbox(tmp_path / "sb")
    sb.reset()
    for line in ["git init", "echo v1 > app.txt", "git add app.txt", 'git commit -m "init"']:
        sb.run(line, trusted=True)
    return sb


def test_terminal_runs_real_git(repo):
    assert "v1" in repo.run("cat app.txt")
    repo.run("git switch -c dev")
    assert repo.run("git branch --show-current") == "dev"


def test_terminal_checks_repo_state(tmp_path):
    ctx = checkers.Ctx("t/01-x", tmp_path, tmp_path / "sb")
    term = checkers.get("terminal")
    q = {"setup": ["git init", "echo hi > a.txt", "git add a.txt", "git commit -m init"]}
    key = {"checks": [
        {"run": "git branch --show-current", "equals": "dev", "desc": "在 dev"},
        {"file": "a.txt", "contains": "hi", "desc": "a.txt 没被改坏"},
    ]}
    term.act(q, key, ctx, {"op": "reset"})
    assert term.check(q, key, ctx, {}).score == 0.5
    term.act(q, key, ctx, {"op": "exec", "cmd": "git switch -c dev"})
    assert term.check(q, key, ctx, {}).score == 1.0


@pytest.mark.parametrize("line", [
    "git config core.pager evil",           # 改配置可以让 git 执行任意程序
    "git -c core.pager=evil log",           # 同上，走命令行参数
    "git rebase --exec evil main",
    "git rebase -x evil main",
    "git bisect run evil",
    "git diff --no-index ../../x y",
    "git log --output=../../x",
    "git init ../elsewhere",
    "git push",
    "python -c 1",
    "cat ../../etc/passwd",
    "echo x > ../escape.txt",
    "rm .git/HEAD",
])
def test_terminal_blocks_dangerous_commands(repo, line):
    before = sorted(p.name for p in repo.root.parent.iterdir())
    out = repo.run(line)
    assert "不支持" in out or "不允许" in out or "只能访问" in out
    assert sorted(p.name for p in repo.root.parent.iterdir()) == before


def test_terminal_does_not_touch_user_git_config(repo):
    assert repo.run("git log -1 --format=%an") == "Learner"
