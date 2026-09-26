"""测试夹具：一个临时的仓库根目录 + 临时数据目录，用 bootstrap.build() 组装出和真实环境一样的 App。

不 monkeypatch 任何全局变量：时钟、agent loop、抓网页都是注入的假实现（D-021 分层的意义）。
"""
import datetime as dt
import json
import shutil
import textwrap
from pathlib import Path

import pytest

from studykit.app.ports import AgentDef, FetchError, Page, RawRun
from studykit.bootstrap import Config, build
from studykit.domain.harness import RunStep

REPO = Path(__file__).resolve().parent.parent


class FakeClock:
    def __init__(self, start="2026-09-26T10:00:00"):
        self.t = dt.datetime.fromisoformat(start)

    def now(self):
        return self.t

    def tick(self, minutes: float = 1) -> None:
        self.t += dt.timedelta(minutes=minutes)


class FakeFetcher:
    def __init__(self, pages: dict[str, str] | None = None):
        self.pages = pages or {}

    def fetch(self, url, timeout=20.0):
        if url not in self.pages:
            raise FetchError(url, 404)
        text = self.pages[url]
        links = [(u, "链接") for u in self.pages if u != url]
        return Page(200, url, "T", text, links)


class FakeRuntime:
    """假的 agent loop：按预先写好的脚本调用工具（和真实 agent 一样走 harness.call_tool）。"""
    version = "fake-1"

    def __init__(self):
        self.script = []          # [(工具名, 参数)]
        self.scripts = []         # 每次运行一份脚本（生成、修复……），按顺序用完后再用 script
        self.judge_reply = ""
        self.review_reply = '{"unsafe": [], "findings": []}'
        self.reviews = []         # 评审模型每次拿到的输入（review_input.md）
        self.runs = []            # 每次运行开放的工具
        self.harness = None

    def run(self, agent: AgentDef, workspace: Path, model: dict, tools: list[str], timeout: int,
            system_prompt: Path | None = None) -> RawRun:
        self.system_prompt = system_prompt
        self.runs.append(tools)
        steps = []
        for name, args in (self.scripts.pop(0) if self.scripts else self.script):
            try:
                text, ok = self.harness.call_tool(workspace, name, args), True
            except Exception as e:  # noqa: BLE001
                text, ok = str(e), False
            steps.append(RunStep(len(steps), "tool", name, ok, summary=text[:300], detail={"args": args}))
        steps.append(RunStep(len(steps), "model", tokens=1000, cost=0.01, summary="完成了"))
        return RawRun(steps, 0, 1.5)

    def complete(self, model, system, prompt_file, workspace, timeout):
        if "安全闸门" in system:                  # 评审模型（agents/tutor-prep/reviewer.md）
            self.reviews.append(prompt_file.read_text(encoding="utf-8"))
            return self.review_reply
        return self.judge_reply


class InlineJobs:
    """后台任务的测试替身：start 时当场跑完（D-032）。"""

    def __init__(self):
        self.jobs = {}

    def start(self, key, fn):
        if (self.jobs.get(key) or {}).get("state") == "running":
            return False
        try:
            fn()
            self.jobs[key] = {"state": "done", "started": "t", "error": ""}
        except Exception as e:  # noqa: BLE001
            self.jobs[key] = {"state": "error", "started": "t", "error": str(e)}
        return True

    def status(self, key):
        return self.jobs.get(key)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """一个最小的仓库：一套题 t/01-x、一份 syllabus、课程清单、tutor-prep agent 的定义。"""
    r = tmp_path / "repo"
    write(r / "progress" / "settings.yaml", f"lab_root: '{(tmp_path / 'labs').as_posix()}'\n")
    write(r / "progress" / "syllabus.yaml", """
        topics:
          tools:
            title: 开发工具
            units:
              - {id: tools-01-shell, title: Shell, status: watching, notes: 零基础}
    """)
    write(r / "progress" / "learner.md", "- 身份：产品经理\n")
    write(r / "curriculum.md", """
        | id | 学科 | 主课 |
        |---|---|---|
        | `tools` | 开发工具 | [讲义](https://missing.csail.mit.edu/2026/) |
    """)
    lesson = r / "lessons" / "t" / "01-x"
    write(lesson / "quiz.yaml", """
        title: 测试课时
        unit: tools-01-shell
        mode: practice
        questions:
          - {id: q1, checker: choice, concept: t.a, level: 1, prompt: 选 B, options: [x, y]}
          - {id: q2, checker: short, concept: t.b, level: 2, prompt: 解释一下}
          - {id: q3, checker: terminal, concept: t.git, level: 3, prompt: 切到 dev,
             setup: ["git init", "echo hi > a.txt", "git add a.txt", "git commit -m init"]}
          - {id: q4, checker: code, concept: t.code, level: 3, prompt: 实现 f, file: code/ex1.py}
    """)
    write(lesson / "code" / "ex1.py", "def f(x):\n    raise NotImplementedError\n")
    write(lesson / "code" / "test_ex1.py", "from ex1 import f\ndef test_one():\n    assert f(1) == 1\n"
                                           "def test_two():\n    assert f(2) == 2\n")
    write(lesson / "key.yaml", """
        answers:
          q1: {answer: B, explain: 因为 B}
          q2: {rubric: [要点], explain: 要点是……}
          q3:
            checks:
              - {run: "git branch --show-current", equals: dev, desc: 在 dev 分支}
    """)
    exam = r / "lessons" / "t" / "02-exam"                  # 单元题：整卷一次交（D-031）
    write(exam / "quiz.yaml", """
        title: 单元题
        unit: tools-01-shell
        questions:
          - {id: q1, checker: choice, concept: t.a, level: 1, prompt: 选 B, options: [x, y]}
          - {id: q2, checker: short, concept: t.b, level: 4, prompt: 找 bug}
          - {id: q3, checker: code, concept: t.code, level: 3, prompt: 实现 f, file: code/ex1.py}
    """)
    write(exam / "code" / "ex1.py", "def f(x):\n    raise NotImplementedError\n")
    write(exam / "code" / "test_ex1.py", "from ex1 import f\ndef test_one():\n    assert f(1) == 1\n")
    write(exam / "key.yaml", """
        answers:
          q1: {answer: B, explain: 因为 B}
          q2: {rubric: ["说出 cd 失败不停（0.5）", "说出 glob 提前展开（0.5）", "加分项：成功提示不可信"], explain: 两个问题}
    """)
    shutil.copytree(REPO / "agents" / "tutor-prep", r / "agents" / "tutor-prep")
    shutil.copytree(REPO / "agents" / "short-grader", r / "agents" / "short-grader")
    (r / "agents" / "_pi").mkdir(parents=True)
    (r / "agents" / "_pi" / "env_bridge.ts").write_text("// fake", encoding="utf-8")
    return r


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def runtime():
    return FakeRuntime()


@pytest.fixture
def fetcher():
    return FakeFetcher()


@pytest.fixture
def app(root, tmp_path, clock, runtime, fetcher):
    a = build(Config(root=root, data=tmp_path / "data"), clock=clock, runtime=runtime, fetcher=fetcher, jobs=InlineJobs())
    runtime.harness = a.harness
    yield a
    a.close()


# ---------- 课程计划样例 ----------

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


def sec(title, minutes, **kw):
    s = {"title": title, "minutes": minutes, "goal": "g", "explain": "e", "try": []}
    s.update(kw)
    return s


def plan_v2(run="run-2"):
    return {"schema_version": 2, "unit": "tools-01-shell", "title": "Shell", "summary": "s",
            "provenance": {"run": run, "known_terms": []},
            "lab": LAB, "sources": [{"title": "讲义", "url": "https://missing.csail.mit.edu/2026/course-shell/"}],
            "parts": [{"title": "P1", "sections": [
                sec("a", 20, terms=[{"id": "tools.cmd.pwd", "term": "pwd", "explain": "打印当前目录"},
                                    {"id": "tools.cmd.cd", "term": "cd", "explain": "换目录"}],
                    checkpoint=[CHOICE, FILL]),
                sec("b", 20, terms=[{"id": "tools.cmd.grep", "term": "grep", "explain": "找行"}], checkpoint=[LAB_TASK])]},
                {"title": "P2", "sections": [sec("c", 30)]}],
            "outcomes": ["x", "y", "z"]}


@pytest.fixture
def unit(app):
    app.course.publish(plan_v2())
    return "tools-01-shell"
