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


def try_needs_lab(plan: dict, limits: PlanLimits) -> list[Finding]:
    """动手命令是在练习场终端里敲的（D-052）：没有练习场的单元，页面上没有终端，动手没处做。"""
    if plan.get("lab"):
        return []
    return [Finding(section_address(i), f"第 {i + 1} 节「{s.get('title', '')}」：写了动手（try），但这个单元没有练习场，"
                    "页面上没有终端可以敲。这一节确实要在终端里动手，就在大纲里加练习场；不需要的话去掉 try，要演示的写进 explain",
                    EVALUATOR)
            for i, s in enumerate(sections_of(plan)) if s.get("try")]


def edit_steps(plan: dict, limits: PlanLimits) -> list[Finding]:
    """改文件的动手步骤（D-057）：学习者在页面的「文件」里改，环境验证时按 content 写。一步只能是一种。"""
    out = []
    for i, s in enumerate(sections_of(plan)):
        for t in s.get("try") or []:
            if not t.get("edit"):
                continue
            path, name = str(t["edit"]), f"第 {i + 1} 节「{s.get('title', '')}」的改文件步骤 {t['edit']!r}"
            if str(t.get("command") or "").strip():
                out.append(Finding(section_address(i), f"{name}：一步只能是一种，command（敲命令）和 edit（改文件）分成两步写", EVALUATOR))
            if path.endswith(("/", "\\")):
                out.append(Finding(section_address(i), f"{name}：edit 要写一个文件，不是目录", EVALUATOR))
            elif not _rel_ok(path):
                out.append(Finding(section_address(i), f"{name}：edit 要是练习场里的相对路径", EVALUATOR))
            if "content" not in t:
                out.append(Finding(section_address(i), f"{name}：缺少 content（改完后文件的完整内容，环境验证时按它写）", EVALUATOR))
    return out


def _rel_ok(path: str) -> bool:
    return bool(path) and not path.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", path) and ".." not in re.split(r"[\\/]", path)


def _lab_problems(plan: dict) -> list[str]:
    # 课程计划看检查点，大纲看每节桩里的 check（D-038）
    has_task = any(c.get("type") == "lab" for s in sections_of(plan)
                   for c in [*(s.get("checkpoint") or []), s.get("check") or {}])
    lab = plan.get("lab")
    if has_task and not lab:
        return ["有练习场任务（lab），但没有定义练习场（lab）"]
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
        if not _rel_ok(path):
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


RULES = [declared_commands, lab_defined, try_needs_lab, edit_steps, command_safety, contract_checks]

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
# 练习场任务怎么写（D-052）：原来写在所有单元共用的 prompt 里（principles 第 4、5 条、rules 里 lab 的几条），
# 现在只给选了练习场的节。
LAB_GUIDE = """\
- **练习场是真实的 Git Bash**（Windows），终端开在练习场根目录，环境变量 `$LAB` 指向它。动手（`try`）的每一条 `command` 都是学习者要在这里敲的命令，要能直接跑通。
  依赖 Git Bash 没有的工具（`tree`、`man`、`strace`、`perf`）时，给出能跑的等价写法，或者只讲概念、放进 `later`。
  动手有两种步骤，一步只能是一种：
  - `command`：在终端里敲的命令，只放命令本身，不放操作说明。
  - `edit` + `content`：新建或修改练习场里的一个文件（学习者在页面右边的「文件」里打开、改、保存）。`content` 是改完后的**完整**内容，学习者照着改，环境验证时按它写文件。
  写代码、写测试、改配置、记笔记都用 `edit`；不要用 `python -c`、`sed -i`、`cat > f <<EOF` 这类命令替学习者改文件（参考做法 `solution` 是给环境补齐用的，不受这条限制）。
- 动手在一个对象上演示，检查点换一个对象或多一个条件，让学习者把方法用出来（动手在 `logs/app.log` 上演示，检查点用 `logs/access.log`）。
- `checks` 在练习场根目录执行，检验**结果**，不检验学习者用了哪个命令；`solution` 是按顺序执行就能通过检查的命令，学习者跳过这一节时，环境用它把练习场补齐。
- **参考做法从"学习者刚敲完本节动手步骤"的状态出发。** 环境验证的顺序：练习场补齐到本节开始 → 按顺序敲完动手 → 检查点应该还不通过 → 执行参考做法 → 检查点通过。
  所以参考做法不能依赖"现在在哪个分支 / 哪个目录"这类会被动手改掉的状态：回主线明写 `git switch main`，不写 `git switch -`。
- **检查点在本节开始时（动手之前）必须不通过**，要生成的文件不能事先就在练习场里。"撤销 / 清理 / 回到原样"这类任务的终点往往和起点一样，要么留下一个起点没有的痕迹（一次新提交、一份记录文件），要么换题。
- 检查项不能被学习者"记录答案"这个动作本身改变：用 `git log -S`、`grep` 现算正确答案时，限定在题目针对的文件上（如 `git log -S TITLE_MAX -- app/routes/tasks.py`）。
- 不要在命令里写死每个人都不一样的值（提交哈希取决于时间和作者）：让命令自己算（`git rev-list --max-parents=0 HEAD`、`git log -1 --format=%h`），或者让学习者从上一条输出里抄。
- 发布前环境会把动手命令和参考做法在 Git Bash 里真的跑一遍；评审模型会先读一遍命令，改动练习场以外东西的命令过不了。
"""
LAB_MODULE_TEXT = dict(
    title="练习场任务",
    verifies="能在终端里把一个操作真的做出来，结果留在练习场里",
    fits="这一节的技能本身就是在终端里做的操作：跑命令、用 git、调试、跑测试、读日志、真跑一次 traceroute。"
         "答案只是一个数、一个判断时，用 fill / choice，不要让学习者\"把答案写进文件\"",
    needs="这个单元要有练习场（大纲的 lab：story + files，在大纲这一步定死），页面右边会出现一个真实的 Git Bash 终端；"
          "有练习场的节才能写动手（try）。学习者还没用过终端（「已经掌握」里没有 pwd、ls 这类命令）时，先带他确认终端能用",
    cost="贵：评审要逐条读命令（安全闸门），发布前要在 Git Bash 里把动手和参考做法实跑一遍；大纲里要写 state_after 约定练习场状态",
    guide=LAB_GUIDE,
)
