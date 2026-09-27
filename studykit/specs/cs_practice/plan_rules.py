"""cs-practice 对课程计划的附加规则和 schema：练习场、lab 检查点、"用到的命令必须先声明"。"""
from __future__ import annotations

import re

from studykit.domain.artifact import Finding, section_address
from studykit.domain.plan import sections_of
from studykit.domain.plan_check import EVALUATOR, PlanLimits
from studykit.specs.cs_practice.safety import command_safety

# WHY: 这些是 shell 语法里跟在别的词后面的关键字，出现在行首不代表是一个新命令。
_NOT_COMMANDS = {"then", "do", "done", "fi", "else", "elif", "esac", "in", "{", "}", "(", ")", "!"}
_CMD = re.compile(r"^[a-z][a-z0-9_+-]*$")
# WHY: 讲解里的行内代码常是 Python（`assert add(1, 2) == 3`、`from calc import add`）或普通词组（`syntax error`），不是 shell 命令
_PYTHON_KEYWORDS = {"from", "import", "assert", "def", "return", "class", "lambda", "raise", "with", "as", "print", "yield",
                    "async", "await", "try", "except", "finally", "pass", "not", "and", "or", "is", "none", "true", "false"}
_NOT_SHELL = re.compile(r"[()=:\[\]{}]|==|\bimport\b")


def command_words(text: str) -> set[str]:
    """一段 shell 命令里用到的命令名：按 | && || ; 和换行切开，取每段的第一个词。"""
    words = set()
    for seg in re.split(r"\|\||&&|[|;\n]|\$\(|`", text or ""):
        toks = seg.strip().split()
        while toks and (re.match(r"^\w+=", toks[0]) or toks[0] in _NOT_COMMANDS or toks[0] in ("sudo", "time")):
            toks = toks[1:]
        if toks and _CMD.match(toks[0]):
            words.add(toks[0])
    return words


def inline_commands(markdown: str) -> set[str]:
    """讲解里行内代码中的命令。只看至少两个词的片段：单个词（如 `g`、`-i`）多半是参数或文件名，不是命令。"""
    words = set()
    for span in re.findall(r"(?<!`)`([^`\n]+)`(?!`)", markdown or ""):
        if len(span.split()) >= 2 and not _NOT_SHELL.search(span):
            words |= {w for w in command_words(span) if w not in _PYTHON_KEYWORDS}
    # 只有像命令的词才算：两个英文单词的词组（`syntax error`）第二个词不是参数、也不是路径时，多半是普通说法
    return {w for w in words if w not in _PROSE}


_PROSE = {"syntax", "error", "the", "a", "an", "this", "that"}


def declared_commands(plan: dict, limits: PlanLimits) -> list[Finding]:
    """讲解和动手里用到的命令，要么学习者已经会，要么在本节或前面的节列进了 terms（D-017）。"""
    errors, known = [], set(limits.known_terms)
    for i, s in enumerate(sections_of(plan)):
        terms = s.get("terms") or []
        known |= {str(t.get("id")) for t in terms} | {str(t.get("term", "")).lower() for t in terms}
        used = inline_commands(s.get("explain", "")) | inline_commands(s.get("mission", ""))
        for t in s.get("try") or []:
            used |= command_words(t.get("command", ""))
        undeclared = sorted(w for w in used if w not in known)
        if undeclared:
            # 写整份计划或大纲的人能加新词；分步写一节的人不能（新词归大纲，D-038），所以两种改法都说清
            errors.append(Finding(section_address(i), f"第 {i + 1} 节「{s.get('title', '')}」：用到了学习者还不认识的命令 "
                                  f"{', '.join(undeclared)}（没掌握，也不是这一节或前面几节的新词）。换成学习者已经会的命令，"
                                  "或者只在讲解里用文字说它做什么；如果这一节确实要教它，它得是大纲分给这一节的新词",
                                  EVALUATOR))
    return errors


def lab_defined(plan: dict, limits: PlanLimits) -> list[Finding]:
    return [Finding("/lab", what, EVALUATOR) for what in _lab_problems(plan)]


def _lab_problems(plan: dict) -> list[str]:
    has_task = any(c.get("type") == "lab" for s in sections_of(plan) for c in s.get("checkpoint") or [])
    lab = plan.get("lab")
    if has_task and not lab:
        return ["有 lab 类型的检查点，但没有定义练习场（lab）"]
    if not lab:
        return []
    errors = []
    if not str(lab.get("story") or "").strip():
        errors.append("lab 缺少 story")
    files = lab.get("files") or []
    if not files:
        errors.append("lab 至少要有一个文件或目录")
    if len(files) > 80:
        errors.append(f"lab 文件太多（{len(files)} 个，上限 80）")
    size = 0
    for f in files:
        path = str(f.get("path") or "")
        if not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) or ".." in re.split(r"[\\/]", path):
            errors.append(f"lab 文件路径要是练习场里的相对路径：{path!r}")
        size += len(str(f.get("content") or ""))
    if size > 200_000:
        errors.append(f"lab 文件总大小 {size} 字，上限 200000")
    return errors


def lab_checkpoint(where: str, c: dict) -> list[str]:
    errors = []
    checks = c.get("checks") or []
    if not checks:
        errors.append(f"{where}：lab 题至少要有一个 check")
    for k in checks:
        if not (k.get("run") or k.get("file")):
            errors.append(f"{where}：每个 check 要有 run 或 file")
        if not str(k.get("desc") or "").strip():
            errors.append(f"{where}：每个 check 要有 desc")
    if not c.get("solution"):
        errors.append(f"{where}：lab 题要有 solution（参考做法），学习者跳过时用它补齐练习场")
    return errors


def contract_checks(plan: dict, limits: PlanLimits) -> list[Finding]:
    """大纲里每节的 state_after（D-038）：每条断言要么跑一条命令（run）、要么看一个文件（file），和 lab 检查点的 checks 一样。"""
    out = []
    for i, s in enumerate(sections_of(plan)):
        for k in s.get("state_after") or []:
            if not (k.get("run") or k.get("file")):
                out.append(Finding(section_address(i), f"第 {i + 1} 节 state_after「{k.get('desc', '')}」要有 run 或 file", EVALUATOR))
    return out


RULES = [declared_commands, lab_defined, command_safety, contract_checks]
CHECKPOINT_RULES = {"lab": lab_checkpoint}

# ---------- 给 agent 看的 schema 片段 ----------

_TRAP = {"type": "object", "description": "这一项没通过时最可能的原因（symptom / cause / fix）", "properties": {
    "symptom": {"type": "string"}, "cause": {"type": "string"}, "fix": {"type": "string"}}}
LAB_CHECK = {"type": "object", "properties": {
    "run": {"type": "string", "description": "在练习场根目录执行的 bash 命令，检查它的输出"},
    "file": {"type": "string", "description": "或者：检查练习场里这个文件的内容（相对路径）"},
    "equals": {"type": "string"}, "contains": {"type": "string"}, "not_contains": {"type": "string"},
    "matches": {"type": "string", "description": "正则"}, "absent": {"type": "boolean", "description": "file 不应该存在"},
    "desc": {"type": "string", "description": "给学习者看的检查项，如「report.txt 里有 ERROR 的次数」"},
    "trap": _TRAP},
    "required": ["desc"]}
CHECKPOINT_FIELDS = {
    "checks": {"type": "array", "items": LAB_CHECK, "description": "lab：做完后练习场应该是什么状态"},
    "solution": {"type": "array", "items": {"type": "string"},
                 "description": "lab：参考做法（按顺序的命令）。学习者跳过这一节时，环境用它把练习场补齐"},
}
PLAN_FIELDS = {"lab": {"type": "object", "properties": {
    "story": {"type": "string", "description": "练习场的故事：学习者接手了什么、要一步步查清楚什么"},
    "files": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string", "description": "相对练习场根目录的路径；以 / 结尾表示空目录"},
        "content": {"type": "string"}}, "required": ["path"]}}},
    "required": ["story", "files"]}}
CHECKPOINT_TYPE_DOC = "lab 在练习场里完成一个任务，环境检查练习场的状态"
