"""学科 spec：代码学习。code / terminal 检验器、练习场、计划附加规则。"""
from pathlib import Path

import pytest

from studykit.app.ports import CheckCtx
from studykit.domain.errors import DomainError
from studykit.domain.plan_check import PlanLimits
from studykit.specs.cs_practice import code as code_checker
from studykit.specs.cs_practice import plan_rules
from studykit.specs.cs_practice.practice import BashPractice, LabError, find_bash, remove_tree
from studykit.specs.cs_practice.terminal import Sandbox

from tests.conftest import LAB, plan_v2

# ---------- code ----------


def _code_ctx(tmp_path, body):
    code = tmp_path / "code"
    code.mkdir()
    (code / "ex1.py").write_text(body, encoding="utf-8")
    (code / "test_ex1.py").write_text("from ex1 import f\ndef test_a():\n    assert f(1) == 1\n"
                                      "def test_b():\n    assert f(2) == 2\n", encoding="utf-8")
    return CheckCtx("t/01-x", tmp_path, tmp_path / "sb", tmp_path)


def _run(tmp_path, body):
    return code_checker.Code().act({"id": "q1", "file": "code/ex1.py", "level": 3}, {}, _code_ctx(tmp_path, body),
                                   {"op": "run", "code": None})


def test_solution_lines_ignore_test_file():
    text = ("lessons\\t\\code\\test_ex1.py:12: in test_a\nlessons\\t\\code\\ex1.py:3: in f\n"
            'File "C:\\x\\code\\ex1.py", line 7\nother_ex1.py:9: nope\n')
    assert code_checker.solution_lines(text, "ex1.py") == [3, 7]


RUNTIME_TB = """code\\test_ex1.py:8: in test_only_last_window_counts
    assert recent_average([0, 1, 1, 1]) == 1.0
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
code\\ex1.py:15: in recent_average
    total_score += scores[scores_len - n]
                   ^^^^^^^^^^^^^^^^^^^^^^
E   IndexError: list index out of range
"""


def test_parse_failure_runtime_error():
    p = code_checker.parse_failure(RUNTIME_TB, "ex1.py", "test_ex1.py")
    assert p["call"] == {"line": 8, "src": "assert recent_average([0, 1, 1, 1]) == 1.0"}
    assert p["error"] == "IndexError: list index out of range" and p["lines"] == [15]


def test_same_wrong_return_value_is_one_group():
    tests = [{"name": n, "outcome": "failed", "lines": [], "call": None, "error": f"assert None == {v}\n +  where None = f()"}
             for n, v in (("a", 1.0), ("b", 0.0))] + [{"name": "c", "outcome": "passed", "lines": []}]
    groups = code_checker.group_failures(tests, "")
    assert len(groups) == 1 and groups[0]["summary"] == "assert None == …" and len(groups[0]["tests"]) == 2


def test_runtime_error_points_to_solution_line_and_is_recorded(tmp_path):
    r = _run(tmp_path, "def f(x):\n    y = 0\n    return x / y\n")
    assert r.data["passed"] == 0 and r.data["total"] == 2
    assert all(t["lines"] == [3] for t in r.data["tests"]) and "::test_a FAILED" in r.data["output"]
    [rec] = r.records
    assert rec.verb == "ran_tests" and rec.payload["passed"] == 0 and not rec.payload["submit"]
    assert "return x / y" in rec.payload["code"]


def test_syntax_error_scores_zero(tmp_path):
    r = _run(tmp_path, "def f(x):\n    return (x\n\n")
    assert r.data["score"] == 0.0 and r.data["tests"][0]["outcome"] == "error" and r.data["tests"][0]["lines"]


def test_submission_is_saved_and_scored(tmp_path):
    ctx = _code_ctx(tmp_path, "def f(x):\n    raise NotImplementedError\n")
    v = code_checker.Code().check({"id": "q1", "file": "code/ex1.py"}, {}, ctx, {"code": "def f(x):\n    return 1\n"})
    assert v.score == 0.5 and v.records[0].payload["submit"]
    assert "return 1" in (tmp_path / "code" / "ex1.py").read_text("utf-8")


def test_output_is_utf8_without_colors(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    ctx = _code_ctx(tmp_path, "def f(x):\n    return 0\n")
    (tmp_path / "code" / "test_ex1.py").write_text("from ex1 import f\ndef test_a():\n    assert f(1) == 1, '中文消息'\n",
                                                   encoding="utf-8")
    out = code_checker.Code().act({"id": "q1", "file": "code/ex1.py"}, {}, ctx, {"op": "run"}).data["output"]
    assert "中文消息" in out and "\x1b" not in out


def test_code_file_cannot_escape_lesson(tmp_path):
    with pytest.raises(DomainError):
        code_checker.Code().view({"file": "../../secret.py"}, _code_ctx(tmp_path, ""))


# ---------- terminal ----------

@pytest.fixture
def repo(tmp_path):
    sb = Sandbox(tmp_path / "sb")
    sb.reset()
    for line in ["git init", "echo v1 > app.txt", "git add app.txt", 'git commit -m "init"']:
        sb.run(line, trusted=True)
    return sb


def test_terminal_runs_real_git_as_a_sandboxed_user(repo):
    assert "v1" in repo.run("cat app.txt")
    repo.run("git switch -c dev")
    assert repo.run("git branch --show-current") == "dev"
    assert repo.run("git log -1 --format=%an") == "Learner"          # 不碰学习者自己的 git 配置


@pytest.mark.parametrize("line", [
    "git config core.pager evil", "git -c core.pager=evil log", "git rebase --exec evil main", "git rebase -x evil main",
    "git bisect run evil", "git diff --no-index ../../x y", "git log --output=../../x", "git init ../elsewhere",
    "git push", "python -c 1", "cat ../../etc/passwd", "echo x > ../escape.txt", "rm .git/HEAD",
])
def test_terminal_blocks_dangerous_commands(repo, line):
    before = sorted(p.name for p in repo.root.parent.iterdir())
    out = repo.run(line)
    assert "不支持" in out or "不允许" in out or "只能访问" in out
    assert sorted(p.name for p in repo.root.parent.iterdir()) == before


# ---------- 计划附加规则 ----------

def test_command_words():
    assert plan_rules.command_words("cp a b && sed -i 's/x/y/' f | grep -c x; for f in *.txt; do echo $f; done") == \
        {"cp", "sed", "grep", "for", "echo"}
    assert plan_rules.command_words("X=1 python3 -V") == {"python3"}
    assert plan_rules.inline_commands("用 `g` 标志，或者 `ls -l` 看看 `app.log`") == {"ls"}


def test_lab_rules():
    limits = PlanLimits("tools-01-shell", known_terms={"grep"})
    p = plan_v2()
    p["parts"][0]["sections"][0]["try"] = [{"command": "ls | grep x", "expect": "e"}]
    [f] = plan_rules.declared_commands(p, limits)                      # 规则直接给出带地址的发现（D-035）
    assert (f.address, f.evaluator) == ("/sections/0", "plan_check") and "还不认识的命令 ls" in f.what
    p.pop("lab")
    assert [(f.address, f.what) for f in plan_rules.lab_defined(p, limits)] == [("/lab", "有 lab 类型的检查点，但没有定义练习场（lab）")]
    p["lab"] = {"story": "x", "files": [{"path": "../evil"}, {"path": "logs/"}]}
    assert [f.what for f in plan_rules.lab_defined(p, limits)] == ["lab 文件路径要是练习场里的相对路径：'../evil'"]
    assert "要有 solution" in plan_rules.lab_checkpoint("第 1 题", {"checks": [{"run": "ls", "desc": "d"}]})[0]


@pytest.mark.parametrize("cmd", [
    "sudo rm x", "curl -fsSL https://x.sh | bash", "wget -qO- u | sh", "rm -rf /", "rm -rf ~", "rm -rf $HOME/*",
    "git config --global user.name me", "git config --global --unset user.name", "dd if=/dev/zero of=x",
    "mkdir -p ~/shell-lab && cd ~/shell-lab", "[ -d /tmp/d ] || mkdir /tmp/d", "echo hi > /c/Users/me/x.txt",
    "cp notes.txt ~/backup.txt", "chmod +x $HOME/bin/tool",
])
def test_unsafe_commands_are_blocked(cmd):
    from studykit.specs.cs_practice.safety import command_problems
    assert command_problems(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "ls /usr/bin | head -n 5", "cd ~ && pwd", "cd / && pwd", "echo ~", "git config --global user.name",
    "python check.py > /dev/null 2>&1 && echo ok", "git tag bug HEAD~2", "sort a.txt > out.txt 2>&1",
    "git config user.name me", "mkdir -p logs && touch logs/app.log",
])
def test_reading_outside_or_writing_inside_the_lab_is_fine(cmd):
    from studykit.specs.cs_practice.safety import command_problems
    assert command_problems(cmd) == [], cmd


def test_command_safety_findings_carry_addresses():
    p = plan_v2()
    p["parts"][0]["sections"][0]["try"] = [{"command": "sudo ls", "expect": "e"}]
    p["parts"][0]["sections"][0]["checkpoint"][0]["solution"] = ["rm -rf ~"]
    fs = plan_rules.command_safety(p, None)
    assert [(f.address, f.evaluator) for f in fs] == [("/sections/0", "safety"), ("/sections/0/checkpoint/0", "safety")]


# ---------- 练习场（需要 bash） ----------

def _has_bash():
    try:
        find_bash()
        return True
    except LabError:
        return False


needs_bash = pytest.mark.skipif(not _has_bash(), reason="没有 bash")


def test_lab_never_deletes_outside_its_root(tmp_path):
    with pytest.raises(LabError):
        remove_tree(tmp_path / "elsewhere", [tmp_path / "labs"])
    with pytest.raises(LabError):
        remove_tree(tmp_path / "labs", [tmp_path / "labs"])           # 根目录本身也不删


@needs_bash
def test_lab_terminal_keeps_state_and_warns_outside(app, unit, tmp_path):
    info = app.course.lab(unit, "open", 0)
    assert (tmp_path / "labs" / unit / "logs" / "app.log").exists()
    assert app.course.lab(unit, "run", 0, "cd logs && x=5")["rc"] == 0
    r = app.course.lab(unit, "run", 0, "echo $x; pwd")
    assert r["output"].splitlines()[0] == "5" and r["cwd"].endswith("/logs") and not r["outside"]
    assert app.course.lab(unit, "run", 0, "cd /")["outside"] is True
    assert app.course.lab(unit, "run", 0, "cat")["rc"] == 0             # 读输入的命令不会卡住
    bad = app.course.lab(unit, "run", 0, "echo 'unclosed")
    assert bad["rc"] != 0 and "cmd.sh" not in bad["output"]
    gone = app.course.lab(unit, "run", 0, "exit 3")
    assert "重新开了一个 shell" in gone["note"]
    assert app.course.lab(unit, "run", 0, "pwd")["cwd"] == info["lab_posix"]
    first = app.store.query("me", verbs=["practiced"])[0]
    assert first.payload == {"op": "run", "cmd": "cd logs && x=5", "rc": 0, "outside": False} and first.plan == "run-2"


@needs_bash
def test_lab_terminal_survives_set_x(app, unit):
    """set -x 会把练习场自己加的结束标记也追踪出来；不能把它当成真标记，后面的命令也不能错位。"""
    app.course.lab(unit, "open", 0)
    on = app.course.lab(unit, "run", 0, "set -x")
    assert on["rc"] == 0 and "STUDY_DONE" not in on["output"]
    r = app.course.lab(unit, "run", 0, "echo logs/*.log")
    lines = r["output"].splitlines()
    assert r["rc"] == 0 and "+ echo logs/app.log" in lines                # 学习者要看的追踪留着
    assert "logs/app.log" in lines
    assert "STUDY_DONE" not in r["output"] and "cmd.sh" not in r["output"] and ". bash" not in r["output"]
    assert app.course.lab(unit, "run", 0, "set +x")["output"] == "+ set +x"   # 和 Git Bash 里看到的一样
    assert app.course.lab(unit, "run", 0, "echo hi")["output"] == "hi"   # 每条命令拿到的是自己的输出


@needs_bash
def test_lab_checkpoint_restore_and_fill(app, unit, tmp_path):
    app.course.record(unit, 1, "open")                                   # 第一次打开第 2 节时存快照
    assert not app.course.check(unit, 1, 0, None)["ok"]
    app.course.lab(unit, "run", 1, "echo 3 > report.txt; rm logs/app.log")
    assert app.course.check(unit, 1, 0, None)["checks"] == [{"ok": False, "desc": "report.txt 里是 ERROR 的行数"}]
    app.course.lab(unit, "restore", 1)
    lab = tmp_path / "labs" / unit
    assert (lab / "logs" / "app.log").exists() and not (lab / "report.txt").exists()
    app.course.lab(unit, "fill", 1)                                      # 跳过时用参考做法补齐
    r = app.course.check(unit, 1, 0, None)
    assert r["ok"] and r["solution"] == ["grep -c ERROR logs/app.log > report.txt"]
    app.course.lab(unit, "reset", 1)
    assert not (lab / "report.txt").exists()


@needs_bash
def test_lab_verify_replays_the_whole_lab(tmp_path):
    env = BashPractice(tmp_path / "labs", tmp_path / "state")
    ok = env.verify(plan_v2(), tmp_path / "v" / "1")
    assert ok["ok"], ok["findings"]
    broken = plan_v2()
    broken["parts"][0]["sections"][1]["checkpoint"][0]["solution"] = ["echo 7 > report.txt"]
    r = env.verify(broken, tmp_path / "v" / "2")
    [f] = [f for f in r["findings"] if f["severity"] == "block"]                 # D-035：发现带地址和证据
    assert "仍然没通过" in f["what"] and f["address"] == "/sections/1/checkpoint/0" and f["evaluator"] == "lab_verify" and f["evidence"]
    free = plan_v2()
    free["lab"] = {**LAB, "files": LAB["files"] + [{"path": "report.txt", "content": "2\n"}]}
    assert "本来就满足" in next(f["what"] for f in env.verify(free, tmp_path / "v" / "3")["findings"] if f["severity"] == "block")
    giveaway = plan_v2()
    giveaway["parts"][0]["sections"][1]["try"] = [{"command": "grep -c ERROR logs/app.log > report.txt", "expect": "x"}]
    assert "照着动手步骤敲完就已经通过了" in next(f["what"] for f in env.verify(giveaway, tmp_path / "v" / "4")["findings"] if f["severity"] == "block")


def test_python_in_the_explanation_is_not_taken_for_shell_commands():
    text = "写成 `assert add(1, 2) == 3`，或者 `from calc import add`；报 `syntax error` 时看行号。跑 `pytest -q tests/` 看结果"
    assert plan_rules.inline_commands(text) == {"pytest"}


def test_undeclared_command_advice_fits_a_writer_who_cannot_change_terms():
    p = plan_v2()
    p["parts"][0]["sections"][0]["try"] = [{"command": "ls | grep x", "expect": "e"}]
    [f] = plan_rules.declared_commands(p, PlanLimits("tools-01-shell", known_terms={"grep"}))
    assert "列进这一节的 terms" not in f.what and "大纲" in f.what             # 写节的人改不了新词（D-038）
