"""单元题由出题 agent 出（D-041，PRD_V2 阶段 C）：产出物、静态检查、实跑检验、产出循环、发布、触发。agent 和评审模型是假的。"""
import copy
import json

import pytest

from studykit.domain.artifact import Finding, repair_unit
from studykit.domain.quiz import quiz_findings, quiz_parts, replace_question, split
from studykit.domain.quiz import test_file as judge_file
from studykit.specs.cs_practice.code import Code
from studykit.specs.cs_practice.terminal import Terminal
from tests.test_cs_practice import needs_bash

UNIT = "tools-01-shell"
CHECKERS = {"choice", "fill", "code", "terminal", "short"}
NODES = {"tools.shell.cwd", "tools.cmd.pwd", "t.git"}

CODE = {"id": "q3", "checker": "code", "concept": "tools.cmd.pwd", "level": 3, "prompt": "实现 f：返回 x 的两倍", "file": "code/ex1.py",
        "starter": "def f(x):\n    raise NotImplementedError\n",
        "tests": "from ex1 import f\n\ndef test_double():\n    assert f(2) == 4\n\ndef test_zero():\n    assert f(0) == 0\n",
        "key": {"solution": "def f(x):\n    return 2 * x\n", "explain": "乘 2"}}
TERM = {"id": "q4", "checker": "terminal", "concept": "t.git", "level": 3, "prompt": "新建 dev 分支并切过去",
        "setup": ["git init", "echo hi > a.txt", "git add a.txt", "git commit -m init"],
        "key": {"checks": [{"run": "git branch --show-current", "equals": "dev", "desc": "在 dev 分支上"}],
                "solution": ["git switch -c dev"], "explain": "git switch -c dev", "parts": [{"concept": "t.git"}]}}


def good_quiz():
    return copy.deepcopy({"title": "Shell 单元题", "questions": [
        {"id": "q1", "checker": "choice", "concept": "tools.shell.cwd", "level": 1, "prompt": "pwd 打印什么？",
         "options": ["上一个目录", "当前目录"], "key": {"answer": "B", "explain": "print working directory",
                                                    "parts": [{"concept": "tools.shell.cwd", "misconception": "parent"},
                                                              {"concept": "tools.shell.cwd"}]}},
        {"id": "q2", "checker": "fill", "concept": "tools.cmd.pwd", "level": 2, "prompt": "回到上一级：cd ____",
         "key": {"blanks": [[".."]], "explain": ".. 是上一级", "parts": [{"concept": "tools.cmd.pwd"}]}},
        CODE, TERM,
        {"id": "q5", "checker": "short", "concept": "tools.shell.cwd", "level": 4, "prompt": "这段脚本哪里错了？",
         "key": {"rubric": ["指出 cd 失败没有停（0.5）", "说出后果（0.5）"], "explain": "set -e",
                 "parts": [{"concept": "tools.shell.cwd"}, {"concept": "tools.cmd.pwd", "level": 3}]}},
        {"id": "q6", "checker": "choice", "concept": "tools.cmd.pwd", "level": 2, "prompt": "哪个命令打印当前目录？",
         "options": ["pwd", "ls"], "key": {"answer": "A", "explain": "pwd",
                                           "parts": [{"concept": "tools.cmd.pwd"}, {"concept": "tools.cmd.pwd"}]}},
    ], "misconceptions": {"tools.shell.cwd": {"parent": "把当前目录当成上一级目录"}}})


# ---------- 产出物 ----------

def test_a_good_quiz_passes_the_static_checks_and_splits_into_the_lesson_files():
    q = good_quiz()
    assert quiz_findings(q, NODES, CHECKERS) == []
    quiz_yaml, key_yaml, files = split(q, UNIT)
    assert quiz_yaml["unit"] == UNIT and [x["id"] for x in quiz_yaml["questions"]] == ["q1", "q2", "q3", "q4", "q5", "q6"]
    assert "key" not in json.dumps(quiz_yaml["questions"]) and "starter" not in quiz_yaml["questions"][2]   # 答案只在 key
    assert key_yaml["answers"]["q1"]["answer"] == "B" and len(key_yaml["answers"]["q1"]["parts"]) == 2
    assert key_yaml["answers"]["q3"]["solution"].startswith("def f")
    assert files == {"code/ex1.py": CODE["starter"], "code/test_ex1.py": CODE["tests"]}
    assert judge_file("code/ex2.sh") == "code/test_ex2.py"


def test_static_checks_point_at_the_broken_question():
    q = good_quiz()
    q["questions"][1] = {**q["questions"][1], "prompt": "HEAD 指向当前分支。它叫 ____", "key": {"blanks": [["HEAD"]], "explain": "x"}}   # 答案漏进题干
    q["questions"][2]["key"]["solution"] = q["questions"][2]["starter"]               # 初始代码就是参考实现
    q["questions"][3]["key"]["solution"] = []
    q["questions"][4]["key"]["rubric"] = ["指出 cd 失败（0.5）"]                        # 分值加起来不是 1
    q["questions"][5]["concept"] = "tools.nope"
    found = {(f.address, f.what.split("：")[1][:6]) for f in quiz_findings(q, NODES, CHECKERS)}
    assert {a for a, _ in found} == {"/questions/1", "/questions/2", "/questions/3", "/questions/4", "/questions/5"}
    few = {"title": "x", "questions": good_quiz()["questions"][:2]}
    whats = [f.what for f in quiz_findings(few, NODES, CHECKERS) if f.address == ""]
    assert any("6–8" in w for w in whats) and any("3 种题型" in w for w in whats) and any("4 级" in w for w in whats)


def test_every_scoring_point_names_its_concept_and_misconceptions_exist_or_are_proposed():
    """D-056 第 1 步：选项 / 空 / 评分点 / 检查项各挂一个知识点；错项的误解要么知识图里已有，要么这套题里提出。"""
    known = {"tools.cmd.pwd": {"relative"}}
    assert quiz_findings(good_quiz(), NODES, CHECKERS, misconceptions=known) == []
    q = good_quiz()
    del q["questions"][0]["key"]["parts"]                                         # 没标
    q["questions"][1]["key"]["parts"] = []                                        # 个数不对
    q["questions"][3]["key"]["parts"] = [{"concept": "tools.nope"}]               # 知识点不在图里
    q["questions"][4]["key"]["parts"][1]["misconception"] = "relative"            # 已有的误解：可以
    q["questions"][5]["key"]["parts"][1]["misconception"] = "ghost"               # 没有、也没提出
    q["misconceptions"]["t.nope"] = {"x": "y"}                                    # 提在不存在的知识点上
    q["misconceptions"]["tools.cmd.pwd"] = {"Bad Id": "y"}
    found = {(f.address, f.what) for f in quiz_findings(q, NODES, CHECKERS, misconceptions=known)}
    assert {a for a, _ in found} == {"/questions/0", "/questions/1", "/questions/3", "/questions/5", "/misconceptions"}
    whats = "\n".join(w for _, w in found)
    assert "key.parts" in whats and "ghost" in whats and "t.nope" in whats and "Bad Id" in whats
    code_only = good_quiz()
    code_only["questions"][2]["key"]["parts"] = [{"concept": "tools.cmd.pwd"}]   # 代码题暂时整题算，不标
    assert any("代码题" in f.what for f in quiz_findings(code_only, NODES, CHECKERS))


def test_repair_replaces_one_question_and_the_rest_stays():
    q = good_quiz()
    assert repair_unit("/questions/3/key/checks/0") == "/questions/3"
    r = replace_question(q, "/questions/3", {**TERM, "prompt": "改过"})
    changed = [a for a in quiz_parts(q) if quiz_parts(q)[a] != quiz_parts(r)[a]]
    assert changed == ["/questions/3"] and q["questions"][3]["prompt"] == "新建 dev 分支并切过去"


# ---------- 实跑检验 ----------

def test_code_questions_must_be_red_before_and_green_with_the_reference(tmp_path):
    files = {"code/ex1.py": CODE["starter"], "code/test_ex1.py": CODE["tests"]}
    assert Code().verify(CODE, CODE["key"], files, tmp_path / "a") == []
    wrong = {**CODE["key"], "solution": "def f(x):\n    return x + x + 1\n"}
    [(what, evidence)] = Code().verify(CODE, wrong, files, tmp_path / "b")
    assert "参考实现没通过" in what and "test_double" in what and evidence
    trivial = {"code/ex1.py": CODE["key"]["solution"], "code/test_ex1.py": CODE["tests"]}
    assert "初始代码就能通过" in Code().verify(CODE, CODE["key"], trivial, tmp_path / "c")[0][0]


def test_terminal_questions_run_the_reference_in_the_learners_restricted_terminal(tmp_path):
    assert Terminal().verify(TERM, TERM["key"], {}, tmp_path / "a") == []
    piped = {**TERM["key"], "solution": ["git branch dev | cat", "cd x"]}             # 练习终端没有管道、cd
    [(what, evidence)] = Terminal().verify(TERM, piped, {}, tmp_path / "b")
    assert "按参考做法做完仍然没通过" in what and "不支持" in evidence
    already = {**TERM, "setup": TERM["setup"] + ["git switch -c dev"]}
    assert "什么都不做" in Terminal().verify(already, TERM["key"], {}, tmp_path / "c")[0][0]


# ---------- 整个循环：出题 → 检验 → 只修被指出的题 → 发布 ----------

GIT = "tools-02-git"


@pytest.fixture
def git_unit(app, root):
    """一个学完了、还没有单元题的单元（课程计划里有这几个知识点）。"""
    from tests.conftest import plan_v2
    from tests.test_panel import _add_unit
    _add_unit(root)
    plan = {**plan_v2("git-run"), "unit": GIT, "outcomes": ["能开分支", "能读懂 git log", "能解决冲突"],
            "nodes": [{"id": "tools.shell.cwd", "title": "当前目录", "desc": "d", "kind": "concept"},
                      {"id": "tools.cmd.pwd", "title": "pwd", "desc": "d", "kind": "term"},
                      {"id": "t.git", "title": "git", "desc": "d", "kind": "term"}]}
    app.course.publish(plan)
    return GIT


def tools_quiz():
    q = good_quiz()
    q["questions"][3]["concept"] = "tools.cmd.pwd"            # 只能用这个学科知识图里的节点
    q["questions"][3]["key"]["parts"] = [{"concept": "tools.cmd.pwd"}]
    return q


def test_the_brief_has_the_goals_the_checkpoints_and_how_the_learner_did(app, runtime, git_unit):
    app.course.check(git_unit, 0, 0, ["A"])                   # 第 1 节检查点第一次答错
    data = app.quizzes.build_input(git_unit)
    agent = app.harness.agent("quiz-maker")
    brief = app.quizzes.render_brief(agent.file("task").read_text(encoding="utf-8"), data)
    assert "1. 能开分支" in brief and "检查点（choice，tools.shell.cwd）：pwd 打印什么？" in brief
    assert "第一次就对：否" in brief and "`tools.cmd.pwd` pwd" in brief and "产品经理" in brief


def test_prepare_repairs_only_the_broken_question_and_publishes_the_lesson(app, runtime, git_unit):
    broken = tools_quiz()
    broken["questions"][2]["key"]["solution"] = "def f(x):\n    return x + x + 1\n"      # 参考实现是错的
    fixed = tools_quiz()["questions"][2]
    runtime.scripts = [[("submit_quiz", {"quiz": {"title": "x", "questions": []}}), ("submit_quiz", {"quiz": broken})],
                       [("submit_quiz_repair", {"parts": [{"address": "/questions/2", "value": fixed}]})]]
    seen = []
    r = app.quizzes.prepare(git_unit, report=lambda p: seen.append(p["label"]))
    assert r["status"] == "accepted" and len(r["rounds"]) == 2 and r["lesson"] == "tools/02-git"
    [block] = [f for f in r["rounds"][0]["findings"] if f["severity"] == "block"]
    assert (block["evaluator"], block["address"]) == ("quiz_verify", "/questions/2") and "参考实现没通过" in block["what"]
    assert runtime.runs == [["submit_quiz"], ["submit_quiz_repair"]]
    assert "第 1 轮检验：试跑代码题和终端题" in seen and "只修被指出的部分：/questions/2" in seen
    lesson = app.content.lesson("tools/02-git")
    assert lesson.unit == git_unit and lesson.exam and len(lesson.questions()) == 6
    assert lesson.answer_key("q1")["answer"] == "B" and "solution" not in json.dumps(lesson.quiz)       # 答案只在 key.yaml
    assert (app.content.lesson_dir("tools/02-git") / "code" / "test_ex1.py").exists()
    assert app.quizzes.state(git_unit) == {"stage": "ready", "lesson": "tools/02-git"}
    assert app.content.graph()["tools.shell.cwd"].misconceptions == {"parent": "把当前目录当成上一级目录"}   # 提出的误解进知识图
    with pytest.raises(Exception, match="已经有单元题"):
        app.quizzes.prepare(git_unit)


def test_dangerous_code_in_a_question_is_rejected_on_submit(app, runtime, git_unit):
    bad = tools_quiz()
    bad["questions"][2]["tests"] += "\nimport shutil\nshutil.rmtree('/')\n"
    runtime.scripts = [[("submit_quiz", {"quiz": bad})]]
    run = app.quizzes.make(git_unit)
    assert not run.submitted and "删除文件或目录" in app.store.steps(run.id)[0].summary


def test_opening_the_last_section_starts_the_quiz_in_the_background(app, git_unit):
    started = []
    app.quizzes.start = lambda u: started.append(u)
    assert not app.quizzes.prefetch(git_unit, 0)                                 # 不是最后一节
    assert app.quizzes.prefetch(git_unit, 2) and started == [git_unit]           # plan_v2 一共 3 节
    assert app.quizzes.state(git_unit) == {"stage": "todo"}
