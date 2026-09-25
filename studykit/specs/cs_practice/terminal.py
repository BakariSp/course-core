"""终端检验器：给学习者一个受限的练习终端，在沙箱目录里跑真实的 git，最后检查仓库状态。

题目（quiz.yaml）里写 setup：搭好场景的命令；答案（key.yaml）里写 checks：结束时仓库应该是什么状态。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from studykit.domain.assessment import ActResult, Verdict

GIT_ALLOWED = {
    "init", "status", "add", "commit", "log", "diff", "show", "branch", "checkout", "switch",
    "restore", "reset", "revert", "merge", "rebase", "stash", "tag", "rm", "mv", "cherry-pick",
    "reflog", "bisect", "blame", "shortlog", "rev-parse", "ls-files", "cat-file", "help",
}
# WHY: 这些参数能让 git 执行任意命令或读写沙箱外的文件。
GIT_BLOCKED_ARGS = {"--exec", "--no-index", "--upload-pack", "--receive-pack", "--git-dir", "--work-tree"}

HELP = """可用命令：
  git <子命令> ...        支持：""" + " ".join(sorted(GIT_ALLOWED)) + """
  ls [路径]               列出文件
  cat <文件>              查看文件内容
  echo 文本 > 文件        写入文件（>> 表示追加）
  touch / rm / mkdir      新建文件 / 删除文件 / 新建目录
  pwd, help, clear
说明：这是练习用的受限终端，不支持管道、cd 和其他程序；文件都在沙箱目录里。"""


class TerminalError(Exception):
    pass


class Sandbox:
    def __init__(self, root: Path):
        self.root = root
        self.work = root / "work"
        self.home = root / "home"

    def exists(self) -> bool:
        return self.work.exists()

    def reset(self) -> None:
        if self.root.exists():
            if sys.version_info >= (3, 12):
                shutil.rmtree(self.root, onexc=_force_remove)
            else:
                shutil.rmtree(self.root, onerror=_force_remove)
        self.work.mkdir(parents=True)
        self.home.mkdir(parents=True)
        (self.home / ".gitconfig").write_text(
            "[user]\n\tname = Learner\n\temail = learner@example.com\n"
            "[init]\n\tdefaultBranch = main\n[color]\n\tui = never\n"
            "[core]\n\tautocrlf = false\n[advice]\n\tdetachedHead = false\n",
            encoding="utf-8",
        )

    def path(self, rel: str) -> Path:
        p = (self.work / rel).resolve()
        if not p.is_relative_to(self.work.resolve()):
            raise TerminalError(f"{rel}：只能访问练习目录里的文件")
        return p

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(
            HOME=str(self.home), USERPROFILE=str(self.home), XDG_CONFIG_HOME=str(self.home),
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(self.home / ".gitconfig"),
            GIT_TERMINAL_PROMPT="0", GIT_EDITOR="true", GIT_SEQUENCE_EDITOR="true",
            GIT_MERGE_AUTOEDIT="no", GIT_PAGER="cat", PAGER="cat",
            GIT_CEILING_DIRECTORIES=str(self.root),
        )
        return env

    # ---------- 执行一行命令 ----------

    def run(self, line: str, trusted: bool = False) -> str:
        try:
            argv = shlex.split(line, posix=True)
        except ValueError as e:
            return f"命令解析失败：{e}"
        if not argv:
            return ""
        cmd, args = argv[0], argv[1:]
        try:
            if cmd == "git":
                return self._git(args, trusted)
            if cmd in _BUILTINS:
                return _BUILTINS[cmd](self, args)
        except TerminalError as e:
            return str(e)
        return f"{cmd}：练习终端不支持这个命令。输入 help 查看可用命令。"

    def _git(self, args: list[str], trusted: bool) -> str:
        if not trusted:
            if not args or args[0] not in GIT_ALLOWED:
                return f"git {args[0] if args else ''}：练习终端不支持这个子命令。输入 help 查看可用命令。"
            bad = [a for a in args if a.split("=")[0] in GIT_BLOCKED_ARGS or a.startswith("--output")]
            if args[0] == "rebase" and "-x" in args:
                bad.append("-x")
            if args[0] == "bisect" and "run" in args:
                bad.append("bisect run")
            if args[0] == "init" and any(not a.startswith("-") for a in args[1:]):
                bad.append("init <路径>")
            if bad:
                return f"练习终端不允许这些参数：{' '.join(bad)}"
        if not self.work.exists():
            return "场景还没准备好，请点「重置场景」。"
        try:
            proc = subprocess.run(
                ["git", *args], cwd=self.work, env=self.env(), stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
            )
        except subprocess.TimeoutExpired:
            return "命令超过 15 秒没结束，已中止。"
        return (proc.stdout + proc.stderr).rstrip()


def _force_remove(func, path, _exc):
    # WHY: git 把对象文件设成只读，Windows 上 rmtree 删不掉，需要先去掉只读属性。
    os.chmod(path, 0o700)
    func(path)


def _redirect(args: list[str]) -> tuple[list[str], str | None, str]:
    for op in (">>", ">"):
        if op in args:
            i = args.index(op)
            if i + 1 >= len(args):
                raise TerminalError(f"{op} 后面要写文件名")
            return args[:i], args[i + 1], "a" if op == ">>" else "w"
    return args, None, "w"


def _echo(sb: Sandbox, args):
    words, target, mode = _redirect(args)
    text = " ".join(words)
    if target is None:
        return text
    p = sb.path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open(mode, encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    return ""


def _ls(sb: Sandbox, args):
    target = sb.path(args[0] if args else ".")
    if target.is_file():
        return target.name
    if not target.exists():
        raise TerminalError(f"ls：{args[0]} 不存在")
    names = sorted(p.name + ("/" if p.is_dir() else "") for p in target.iterdir())
    return "  ".join(names)


def _cat(sb: Sandbox, args):
    if not args:
        raise TerminalError("cat：要指定文件")
    out = []
    for a in args:
        p = sb.path(a)
        if not p.is_file():
            raise TerminalError(f"cat：{a} 不是文件")
        out.append(p.read_text(encoding="utf-8", errors="replace").rstrip("\n"))
    return "\n".join(out)


def _touch(sb: Sandbox, args):
    for a in args:
        p = sb.path(a)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    return ""


def _rm(sb: Sandbox, args):
    for a in [a for a in args if not a.startswith("-")]:
        p = sb.path(a)
        if p == sb.work.resolve() or ".git" in p.relative_to(sb.work.resolve()).parts:
            raise TerminalError(f"rm：不允许删除 {a}")
        if not p.is_file():
            raise TerminalError(f"rm：{a} 不是文件（练习终端只能删文件）")
        p.unlink()
    return ""


def _mkdir(sb: Sandbox, args):
    for a in [a for a in args if not a.startswith("-")]:
        sb.path(a).mkdir(parents=True, exist_ok=True)
    return ""


_BUILTINS = {
    "echo": _echo, "ls": _ls, "cat": _cat, "touch": _touch, "rm": _rm, "mkdir": _mkdir,
    "pwd": lambda sb, args: "~/work",
    "help": lambda sb, args: HELP,
}


# ---------- 检查仓库状态 ----------

def evaluate(sb: Sandbox, check: dict) -> tuple[bool, str]:
    desc = check.get("desc") or check.get("run") or check.get("file", "")
    if "file" in check:
        try:
            p = sb.path(check["file"])
        except TerminalError:
            return False, desc
        if check.get("absent"):
            return not p.exists(), desc
        if not p.is_file():
            return False, desc
        actual = p.read_text(encoding="utf-8", errors="replace").strip()
    else:
        actual = sb.run(check["run"], trusted=True).strip()
    if "equals" in check:
        ok = actual == str(check["equals"]).strip()
    elif "contains" in check:
        ok = str(check["contains"]) in actual
    elif "not_contains" in check:
        ok = str(check["not_contains"]) not in actual
    elif "matches" in check:
        ok = re.search(check["matches"], actual, re.MULTILINE) is not None
    elif "line_count" in check:
        ok = len([l for l in actual.splitlines() if l.strip()]) == int(check["line_count"])
    else:
        ok = bool(actual)
    return ok, desc


class Terminal:
    """练习终端。response 是 {"history": [命令...]}；分数 = 通过的检查项占比。"""
    kind = "terminal"
    auto = True

    def _sandbox(self, ctx) -> Sandbox:
        return Sandbox(ctx.workdir)

    def _setup(self, q, sb: Sandbox) -> str:
        sb.reset()
        log = []
        for line in q.get("setup", []):
            out = sb.run(line, trusted=True)
            if out and q.get("show_setup"):
                log.append(out)
        return "\n".join(log)

    def view(self, q, ctx):
        return {"intro": q.get("intro", "")}

    def act(self, q, key, ctx, action):
        sb = self._sandbox(ctx)
        op = action.get("op")
        if op == "open":
            if sb.exists():
                return ActResult({"output": "（已恢复上次的练习现场。想从头来就点「重置场景」。）"})
            op = "reset"
        if op == "reset":
            self._setup(q, sb)
            return ActResult({"output": "场景已就绪。输入 help 查看可用命令。"})
        if op == "exec":
            line = str(action.get("cmd", ""))
            if not sb.exists():
                self._setup(q, sb)
            return ActResult({"output": sb.run(line)})
        raise ValueError(f"终端题不支持 {op}")

    def check(self, q, key, ctx, response):
        sb = self._sandbox(ctx)
        checks = key.get("checks") or []
        if not sb.exists() or not checks:
            return Verdict(0.0, ["没有找到练习现场，或者这道题没有配置检查项。"])
        results = [evaluate(sb, c) for c in checks]
        feedback = [f"{'✓' if ok else '✗'} {desc}" for ok, desc in results]
        return Verdict(sum(ok for ok, _ in results) / len(results), feedback)
