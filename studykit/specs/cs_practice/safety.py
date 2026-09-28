"""命令安全的固定规则（D-030）：会在本机执行的命令，先过这一关，才轮到评审模型和 lab verify。

会被执行的命令 = 每节的动手命令（try）、lab 检查点的参考做法（solution）和检查（checks[].run）。
规则只拦"明确危险"的写法，宁可误拦（agent 会收到原因、改成练习场里的写法），不做完整的 shell 解析：
    1. 永远不行：sudo、下载后直接交给 shell 执行、格式化 / 关机、删根目录或家目录、改全局 git 配置
    2. 练习场外的路径（/…、~、$HOME、C:\\…）和写操作（rm、mv、>、mkdir……）出现在同一条命令里
读练习场外的东西（`ls /usr/bin`、`cd ~ && pwd`）放行：它只读，而且 Shell 单元要讲。
WHY: 评审模型也会读命令，但模型会被读到的网页带偏；固定规则是不依赖模型的底线。
"""
from __future__ import annotations

import re

from studykit.domain.artifact import Finding, checkpoint_address, section_address
from studykit.domain.plan import sections_of

EVALUATOR = "safety"

ALWAYS = [
    (re.compile(r"\bsudo\b"), "用了 sudo"),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b"), "把下载的内容直接交给 shell 执行"),
    (re.compile(r"\b(mkfs(\.\w+)?|shutdown|reboot|halt|diskpart|format\.com)\b"), "格式化 / 关机类命令"),
    (re.compile(r"\bdd\s+[^|;&]*\bof="), "dd 写设备或文件"),
    (re.compile(r":\(\)\s*\{"), "fork 炸弹"),
    (re.compile(r"\brm\s+(-\S+\s+)*(/|~|\$HOME|\$\{HOME\})/?\*?(\s|$|[;&|])"), "删除根目录或家目录"),
    (re.compile(r"\bgit\s+config\s+(.*\s)?--(global|system)\b(?=.*(\s--(unset|add|replace-all|edit)\b|\s-e\b|\s\S+\s+\S+\s*$))"),
     "改全局 git 配置（会改到学习者自己的 ~/.gitconfig；练习场里用 git config 不加 --global）"),
]
# 练习场外的路径：行首、空白、= 或重定向符号之后的 /xxx、~、$HOME、盘符。/dev/null 放行。
OUTSIDE = re.compile(r"(?:(?<=^)|(?<=[\s=<>]))(/(?!dev/null\b)[^\s;|&)]*|~(?![\w-])[^\s;|&)]*|\$\{?HOME\}?[^\s;|&)]*|[A-Za-z]:[\\/][^\s;|&)]*)")
WRITE = re.compile(r"\b(rm|rmdir|mv|cp|touch|mkdir|chmod|chown|ln|tee|truncate|install|unzip|tar)\b|\bgit\s+(init|clone)\b"
                   r"|[0-9]?>{1,2}(?!&)(?!\s*/dev/null)")


def command_problems(command: str) -> list[str]:
    problems = [why for pat, why in ALWAYS if pat.search(command)]
    outside = [m.group(1) for m in OUTSIDE.finditer(command)]
    if outside and WRITE.search(command):
        problems.append(f"在同一条命令里用了练习场外的路径（{', '.join(dict.fromkeys(outside))}）又有写操作；"
                        "命令都在练习场根目录执行，文件路径写成练习场里的相对路径")
    return problems


def commands(plan: dict) -> list[tuple[str, str, str]]:
    """课程计划里所有会被执行的命令：(地址, 在哪, 命令)。"""
    out = []
    for i, s in enumerate(sections_of(plan)):
        out += [(section_address(i), f"第 {i + 1} 节动手命令", t["command"]) for t in s.get("try") or [] if t.get("command")]
        out += [(section_address(i), f"第 {i + 1} 节 state_after 的检查", k["run"]) for k in s.get("state_after") or [] if k.get("run")]
        for j, c in enumerate(s.get("checkpoint") or []):
            where = f"第 {i + 1} 节检查点 {j + 1}"
            out += [(checkpoint_address(i, j), f"{where}的参考做法", cmd) for cmd in c.get("solution") or []]
            out += [(checkpoint_address(i, j), f"{where}的检查", k["run"]) for k in c.get("checks") or [] if k.get("run")]
    return out


def command_safety(plan: dict, limits=None) -> list[Finding]:
    out = []
    for addr, where, cmd in commands(plan):
        problems = command_problems(cmd)
        if problems:                       # 一条命令一个发现，几条原因写在一起
            out.append(Finding(addr, f"{where}不安全（{'；'.join(problems)}）：{cmd.strip()[:120]}", EVALUATOR, evidence=cmd))
    return out
