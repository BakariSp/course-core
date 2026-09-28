import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from studykit.app.paths import safe_path
from studykit.domain.assessment import ActResult, Record, Verdict


def judge_file_for(q: dict) -> str:
    if q.get("test"):
        return q["test"]
    p = Path(q["file"])
    return str(p.with_name("test_" + p.name))


@dataclass
class RunResult:
    score: float
    passed: int
    total: int
    output: str                                        # pytest 的原始输出：唯一权威，界面上原样展示
    tests: list[dict] = field(default_factory=list)    # 每个测试一条：name / outcome / message / lines / call / error
    groups: list[dict] = field(default_factory=list)   # 失败按"同一个错误 + 同一处代码"分组，方便阅读
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return {"score": self.score, "passed": self.passed, "total": self.total, "seconds": self.seconds,
                "output": self.output[-20000:], "tests": self.tests, "groups": self.groups}


_FRAME = re.compile(r"^(?P<path>\S[^\n]*?):(?P<line>\d+): in (?P<func>\S+)\n(?P<src>    [^\n]*)?", re.MULTILINE)


def _basename(path: str) -> str:
    return re.split(r"[\\/]", path)[-1]


def parse_failure(text: str, solution_name: str, test_name: str) -> dict:
    """把一条 --tb=short 的报错拆成：触发它的测试调用、错误信息、学习者代码里的行号。"""
    call = None
    for m in _FRAME.finditer(text or ""):
        if _basename(m["path"]) == test_name and m["func"] != "<module>":
            call = {"line": int(m["line"]), "src": (m["src"] or "").strip()}
            break
    error_lines = [re.sub(r"^E {0,3}", "", l) for l in (text or "").splitlines() if l.startswith("E")]
    return {"call": call, "error": "\n".join(error_lines).strip(), "lines": solution_lines(text, solution_name)}


def error_summary(error: str) -> str:
    """错误的"种类"：取第一行；`assert 实际 == 期望` 只保留实际值。

    WHY: 每个测试期望的值不同，按整句分组时"都返回了 None"会被拆成好几组，看不出是同一个问题。
    """
    first = (error or "").splitlines()[0] if error else ""
    m = re.match(r"^assert (.+?) == .+$", first)
    return f"assert {m[1]} == …" if m else first


def group_failures(tests: list[dict], code: str) -> list[dict]:
    """同一种错误发生在同一处代码的测试合成一组。每组带上出错那几行代码的原文，每个测试保留自己的完整报错。"""
    code_lines = code.splitlines()
    groups: dict[tuple, dict] = {}
    for t in tests:
        if t["outcome"] == "passed":
            continue
        error = t.get("error") or t.get("message") or ""
        summary = error_summary(error)
        key = (t["outcome"], summary, tuple(t.get("lines") or []))
        if key not in groups:
            groups[key] = {
                "outcome": t["outcome"], "summary": summary, "error": error,
                "lines": [{"line": n, "src": code_lines[n - 1].strip() if 0 < n <= len(code_lines) else ""}
                          for n in t.get("lines") or []],
                "tests": [],
            }
        groups[key]["tests"].append({"name": t["name"], "call": t.get("call"), "error": error})
    return list(groups.values())


def solution_lines(text: str, filename: str) -> list[int]:
    """从报错信息里找出学习者代码文件（不是测试文件）的行号。"""
    # WHY: 前面不能是字母数字、下划线或点，否则 test_ex1.py:12 也会被当成 ex1.py:12。
    name = re.escape(filename)
    patterns = [rf"(?<![\w.]){name}:(\d+)", rf"(?<![\w.]){name}\", line (\d+)"]
    return sorted({int(m) for p in patterns for m in re.findall(p, text or "")})


def parse_junit(report: Path, filename: str, test_filename: str) -> list[dict]:
    root = ET.parse(report).getroot()
    tests = []
    for case in root.iter("testcase"):
        outcome, message, text = "passed", "", ""
        for tag in ("failure", "error", "skipped"):
            node = case.find(tag)
            if node is not None:
                outcome = {"failure": "failed"}.get(tag, tag)
                message = node.get("message", "")
                text = node.text or ""
                break
        parsed = parse_failure(text, filename, test_filename) if outcome != "passed" else {}
        tests.append({
            "name": case.get("name", ""),
            "outcome": outcome,
            "message": "\n".join(message.strip().splitlines()[:6])[:600],
            "lines": solution_lines(text + "\n" + message, filename),
            "call": parsed.get("call"),
            "error": (parsed.get("error") or message.strip())[:1500],
        })
    return tests


def run_code_tests(lesson_dir: Path, q: dict, root: Path) -> RunResult:
    test_path = safe_path(lesson_dir, judge_file_for(q))
    filename = Path(q["file"]).name
    solution = safe_path(lesson_dir, q["file"])
    started = time.monotonic()
    # WHY: 子进程输出到管道时，Windows 上默认用 GBK 编码，按 UTF-8 解码会乱码；
    # 启动服务器的程序可能带着 FORCE_COLOR，会让 pytest 输出颜色控制符。两个都要显式关掉。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    env.pop("FORCE_COLOR", None)
    env.pop("PY_COLORS", None)
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        try:
            proc = subprocess.run(
                # WHY: -v 让原始输出里每个测试一行 PASSED/FAILED；--tb=short 每层调用只保留一行代码，读得过来。
                [sys.executable, "-m", "pytest", str(test_path), "-v", "--no-header", "--color=no",
                 "--tb=short", "-p", "no:cacheprovider", f"--junitxml={report}"],
                cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60,
            )
        except subprocess.TimeoutExpired:
            return RunResult(0.0, 0, 0, "运行超过 60 秒，已中止。是不是写了死循环？")
        # WHY: 去掉 junit 报告那一行（临时路径，对学习者是噪音），其余原样保留。
        output = "\n".join(l for l in (proc.stdout + proc.stderr).strip().splitlines()
                           if "generated xml file" not in l)
        seconds = round(time.monotonic() - started, 2)
        if not report.exists():
            return RunResult(0.0, 0, 0, output, seconds=seconds)
        tests = parse_junit(report, filename, test_path.name)
    # WHY: 语法错误等收集阶段的失败也会作为一条 error 出现，算进总数，这样分数是 0 而不是"0/0"。
    total = len(tests)
    passed = sum(t["outcome"] == "passed" for t in tests)
    code = solution.read_text(encoding="utf-8") if solution.exists() else ""
    return RunResult(passed / total if total else 0.0, passed, total, output, tests,
                     group_failures(tests, code), seconds)


class Code:
    """代码题：在网页里写代码（也可以用编辑器改 code/exN.py），pytest 判分，分数 = 测试通过比例。

    每次「运行测试」和「提交」都记一条 ran_tests 证据（带代码快照），作为作答过程（D-005）。
    """
    kind = "code"
    auto = True

    def view(self, q, ctx):
        path = safe_path(ctx.lesson_dir, q["file"])
        return {"file": q["file"], "starter": path.read_text(encoding="utf-8") if path.exists() else ""}

    def _run(self, q, ctx, code, submit: bool) -> tuple[RunResult, Record]:
        path = safe_path(ctx.lesson_dir, q["file"])
        if code is not None:
            path.write_text(code, encoding="utf-8")
        result = run_code_tests(ctx.lesson_dir, q, ctx.root)
        record = Record("ran_tests", result.score, {
            "submit": submit, "code": path.read_text(encoding="utf-8") if path.exists() else "",
            "passed": result.passed, "total": result.total,
            "failures": [{k: t.get(k) for k in ("name", "outcome", "message", "lines", "call", "error")}
                         for t in result.tests if t["outcome"] != "passed"]})
        return result, record

    def act(self, q, key, ctx, action):
        if action.get("op") != "run":
            raise ValueError("代码题只支持 run")
        result, record = self._run(q, ctx, action.get("code"), submit=False)
        return ActResult(result.as_dict(), [record])

    def check(self, q, key, ctx, response):
        result, record = self._run(q, ctx, (response or {}).get("code"), submit=True)
        return Verdict(result.score, [f"通过 {result.passed}/{result.total} 个测试"], result.as_dict(), [record])

    def verify(self, q: dict, key: dict, files: dict[str, str], workdir: Path) -> list[tuple[str, str]]:
        """出题 agent 出的代码题能不能用（D-041）：初始代码下测试必须是红的，换成参考实现必须全绿。返回 [(问题, 证据)]。"""
        for rel, text in files.items():
            p = safe_path(workdir, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        problems = []
        red = run_code_tests(workdir, q, workdir)
        if red.total == 0:
            problems.append(("测试一个都没跑起来（导入或收集阶段就出错了）", red.output[-1200:]))
        elif red.passed == red.total:
            problems.append(("初始代码就能通过全部测试：测试没在测学习者要写的东西", red.output[-600:]))
        safe_path(workdir, q["file"]).write_text(str(key.get("solution") or ""), encoding="utf-8", newline="\n")
        green = run_code_tests(workdir, q, workdir)
        if green.total == 0 or green.passed != green.total:
            failed = [t["name"] for t in green.tests if t["outcome"] != "passed"]
            problems.append((f"参考实现没通过全部测试（{green.passed}/{green.total}）：{'、'.join(failed) or '收集失败'}",
                             green.output[-1500:]))
        return problems
