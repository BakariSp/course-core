import textwrap

import pytest

from studykit import store


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把所有数据路径指到临时目录，并放一套最小的课时 t/01-x。"""
    monkeypatch.setattr(store, "PROGRESS_DIR", tmp_path / "progress")
    monkeypatch.setattr(store, "ATTEMPTS", tmp_path / "progress" / "attempts.jsonl")
    monkeypatch.setattr(store, "RUNS", tmp_path / "progress" / "runs.jsonl")
    monkeypatch.setattr(store, "STUDY_LOG", tmp_path / "progress" / "study_log.jsonl")
    monkeypatch.setattr(store, "SYLLABUS", tmp_path / "progress" / "syllabus.yaml")
    monkeypatch.setattr(store, "PROGRESS_MD", tmp_path / "progress.md")
    monkeypatch.setattr(store, "SANDBOX", tmp_path / ".sandbox")
    monkeypatch.setattr(store, "LESSONS", tmp_path / "lessons")
    lesson = tmp_path / "lessons" / "t" / "01-x"
    (lesson / "code").mkdir(parents=True)
    (lesson / "quiz.yaml").write_text(textwrap.dedent("""
        title: 测试课时
        questions:
          - {id: q1, checker: choice, concept: t.a, level: 1, prompt: 选 B, options: [x, y]}
          - {id: q2, checker: short, concept: t.b, level: 2, prompt: 解释一下}
          - {id: q3, checker: terminal, concept: t.git, level: 3, prompt: 切到 dev,
             setup: ["git init", "echo hi > a.txt", "git add a.txt", "git commit -m init"]}
          - {id: q4, checker: code, concept: t.code, level: 3, prompt: 实现 f, file: code/ex1.py}
    """), encoding="utf-8")
    (lesson / "code" / "ex1.py").write_text("def f(x):\n    raise NotImplementedError\n", encoding="utf-8")
    (lesson / "code" / "test_ex1.py").write_text(
        "from ex1 import f\n"
        "def test_one():\n    assert f(1) == 1\n"
        "def test_two():\n    assert f(2) == 2\n", encoding="utf-8")
    (lesson / "key.yaml").write_text(textwrap.dedent("""
        answers:
          q1: {answer: B, explain: 因为 B}
          q2: {rubric: [要点]}
          q3:
            checks:
              - {run: "git branch --show-current", equals: dev, desc: 在 dev 分支}
    """), encoding="utf-8")
    return tmp_path
