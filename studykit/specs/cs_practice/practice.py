"""练习场（D-014）：每个单元一个真实的目录，课程页里嵌一个真实的 bash，命令都在这里跑。

    <lab_root>/<unit>/                    练习场本身（默认 ~/cs-study-lab/<unit>），学习者也可以在 Git Bash 里 cd 进去
    <state>/<unit>/snapshots/<plan>/sN/   第 N 节开始时的快照，「还原到本节开始」用。**不可重建**，不是缓存
    <state>/<unit>/cmd.sh                 当前要执行的命令（不放进练习场，免得 ls 时看到它）

INVARIANT: 练习场的初始内容只来自课程计划里的 lab.files；之后的状态只由学习者的命令、
「还原」「重置」「用参考做法补齐」改变。
WHY: 真实 bash 能访问整台电脑，这和学习者自己打开 Git Bash 一样；这里只保证"默认在练习场里"，
离开练习场时提醒，不做沙箱隔离——这是本机学习工具，服务器只监听 127.0.0.1。
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from studykit.domain.errors import DomainError
from studykit.domain.artifact import Finding, blocking, checkpoint_address, section_address
from studykit.domain.plan import sections_of
from studykit.specs.cs_practice.terminal import TerminalError, evaluate

MARK = "__STUDY_DONE_" + uuid.uuid4().hex[:8] + "__"
TIMEOUT = 20            # 一条命令最多跑多久（秒）
MAX_OUTPUT = 60_000     # 一条命令最多返回多少字


class LabError(DomainError):
    pass


def find_bash(configured: str | None = None) -> str:
    """找 Git Bash 的 bash.exe。WHY: Windows 的 PATH 里常有 System32\\bash.exe（WSL 入口），没装发行版时一跑就报错。"""
    if configured:
        return str(configured)
    if os.name != "nt":
        return shutil.which("bash") or "/bin/bash"
    candidates = []
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parent.parent          # ...\Git\cmd\git.exe → ...\Git
        candidates += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
    candidates += [Path(os.environ.get(v, "")) / "Git" / "bin" / "bash.exe"
                   for v in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA") if os.environ.get(v)]
    for c in candidates:
        if c.is_file():
            return str(c)
    raise LabError("找不到 Git Bash（bash.exe）。装 Git for Windows，或在 progress/settings.yaml 里写 bash_path。")


def _safe_rel(rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute() or ".." in p.parts or not rel.strip():
        raise LabError(f"练习场文件路径不合法：{rel!r}")
    return p


def write_files(d: Path, files: list[dict]) -> None:
    for f in files:
        p = d / _safe_rel(f["path"])
        if f["path"].endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        # WHY: 固定用 LF。Windows 默认写成 CRLF，grep/awk 看到的每行末尾会多一个 \r，结果和讲义对不上。
        with p.open("w", encoding="utf-8", newline="\n") as fh:
            fh.write(f.get("content", ""))


def remove_tree(d: Path, allowed: list[Path]) -> None:
    """INVARIANT: 只删 allowed 里某个目录下面的子目录（不删它们本身，更不删外面的）。"""
    target = d.resolve()
    if not any(target.is_relative_to(a.resolve()) and target != a.resolve() for a in allowed):
        raise LabError(f"拒绝删除练习场以外的目录：{d}")
    if d.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(d, onexc=_force)
        else:
            shutil.rmtree(d, onerror=_force)


def _force(func, path, _exc):
    os.chmod(path, 0o700)
    func(path)


def run_in(bash: str, directory: Path, command: str, timeout: int = TIMEOUT) -> tuple[int, str]:
    """在 directory 里开一个新的 bash 跑一条命令（$LAB 指向它）。"""
    env = dict(os.environ, CHERE_INVOKING="1", LANG="C.UTF-8", LAB=str(directory), PAGER="cat", GIT_PAGER="cat")
    try:
        proc = subprocess.run([bash, "--login", "-c", 'cd "$LAB" && ' + command],
                              cwd=directory, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"命令超过 {timeout} 秒没结束"
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")


class LabView:
    """给 terminal.evaluate 用：path() 限定在练习场里，run() 在练习场里执行。"""

    def __init__(self, bash: str, directory: Path):
        self.bash, self.work = bash, directory

    def path(self, rel: str) -> Path:
        p = (self.work / rel).resolve()
        if not p.is_relative_to(self.work.resolve()):
            raise TerminalError(f"{rel}：只能检查练习场里的文件")
        return p

    def run(self, line: str, trusted: bool = True) -> str:
        return run_in(self.bash, self.work, line)[1]


def check_in(bash: str, directory: Path, checks: list[dict]) -> list[tuple[bool, str, dict]]:
    view = LabView(bash, directory)
    return [(*evaluate(view, c), c) for c in checks]


class BashPractice:
    """实现 app.ports.PracticeEnv。"""

    def __init__(self, lab_root: Path, state_root: Path, bash_path: str | None = None):
        self.lab_root, self.state_root, self.bash_path = lab_root, state_root, bash_path
        self._shells: dict[str, Shell] = {}
        self._lock = threading.Lock()

    def bash(self) -> str:
        return find_bash(self.bash_path)

    def lab_dir(self, unit: str) -> Path:
        return self.lab_root / unit

    def state_dir(self, unit: str) -> Path:
        return self.state_root / unit

    def _remove(self, d: Path) -> None:
        remove_tree(d, [self.lab_root, self.state_root])

    def ensure(self, unit: str, files: list[dict]) -> Path:
        d = self.lab_dir(unit)
        if not d.exists():
            d.mkdir(parents=True)
            write_files(d, files)
        return d

    def shell(self, unit: str) -> "Shell":
        with self._lock:
            if unit not in self._shells:
                self._shells[unit] = Shell(self, unit)
            return self._shells[unit]

    def stop(self, unit: str) -> None:
        with self._lock:
            sh = self._shells.pop(unit, None)
        if sh:
            sh.stop()

    def stop_all(self) -> None:
        for unit in list(self._shells):
            self.stop(unit)

    # ---------- PracticeEnv ----------

    def open(self, unit: str, files: list[dict]) -> dict:
        self.ensure(unit, files)
        try:
            bash = self.bash()
        except LabError as e:
            bash = f"error: {e}"
        sh = self.shell(unit)
        if not bash.startswith("error"):
            sh.start()
        return {"lab": str(self.lab_dir(unit)), "lab_posix": sh.lab_posix(),
                "cwd": sh.cwd if sh.proc else sh.lab_posix(), "bash": bash}

    def home(self, unit: str) -> str:
        return self.shell(unit).lab_posix()

    def run(self, unit: str, files: list[dict], cmd: str) -> dict:
        self.ensure(unit, files)
        return self.shell(unit).run(cmd)

    def snapshot_path(self, unit: str, plan: str, section: int) -> Path:
        return self.state_dir(unit) / "snapshots" / plan / f"s{section}"

    def snapshot_once(self, unit: str, plan: str, section: int, files: list[dict]) -> None:
        """第一次打开某一节时存一份快照。已经有了就不覆盖（"本节开始"指第一次进来的时候）。"""
        self.ensure(unit, files)
        snap = self.snapshot_path(unit, plan, section)
        if not snap.exists():
            snap.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.lab_dir(unit), snap)

    def restore(self, unit: str, plan: str, section: int) -> None:
        snap = self.snapshot_path(unit, plan, section)
        if not snap.exists():
            raise LabError("这一节还没有快照（第一次打开这一节时才会存）")
        self.stop(unit)
        self._remove(self.lab_dir(unit))
        shutil.copytree(snap, self.lab_dir(unit))

    def reset(self, unit: str, files: list[dict]) -> None:
        self.stop(unit)
        self._remove(self.lab_dir(unit))
        self.ensure(unit, files)
        self._remove(self.state_dir(unit) / "snapshots")

    def run_once(self, unit: str, cmd: str) -> tuple[int, str]:
        """判分、参考做法用：不影响学习者终端里的当前目录和变量。"""
        return run_in(self.bash(), self.lab_dir(unit), cmd)

    def check(self, unit: str, checks: list[dict]) -> list[tuple[bool, str, dict]]:
        return check_in(self.bash(), self.lab_dir(unit), checks)

    def verify(self, plan: dict, workdir: Path) -> dict:
        """把课程计划的练习场从头走一遍（导师审阅后运行，D-014）：

        每一节依次：跑动手命令（能不能在学习者的 Git Bash 里跑通）→ 检查点在做之前不应该通过
        → 跑参考做法 → 检查点必须全部通过。后面的节接着前面的结果做，和学习者的顺序一样。
        WHY: 这会执行 agent 写的命令，所以只在导师读过这些命令之后手动运行，不在 agent 提交时自动跑。
        """
        bash = self.bash()
        remove_tree(workdir, [workdir.parent])
        workdir.mkdir(parents=True)
        write_files(workdir, (plan.get("lab") or {}).get("files") or [])
        report, found = [], []
        broken: list[int] = []      # 没兑现约定（state_after，D-038）的节：后面各节的失败可能是它连带的

        def block(addr, what, evidence=""):
            if broken:              # WHY: 上游没兑现约定时，下游按约定接着做也会失败。只把源头判成阻断，修好源头后下一轮再看下游
                found.append(Finding(addr, f"{what}（第 {broken[0]} 节没兑现约定，这里可能是连带的）", "lab_verify", "warn", evidence))
            else:
                found.append(Finding(addr, what, "lab_verify", "block", evidence))
        warn = lambda addr, what, evidence="": found.append(Finding(addr, what, "lab_verify", "warn", evidence))  # noqa: E731
        for i0, s in enumerate(sections_of(plan)):
            i = i0 + 1
            sec = {"section": i, "title": s.get("title", ""), "try": [], "checkpoints": []}
            labs = [(j, c) for j, c in enumerate(s.get("checkpoint") or [], 1) if c.get("type") == "lab"]
            for j, c in labs:
                start = check_in(bash, workdir, c.get("checks") or [])
                if start and all(ok for ok, _, _ in start):
                    block(checkpoint_address(i0, j - 1), f"第 {i} 节检查点 {j}：练习场里本来就满足，什么都不做就能通过",
                          "；".join(d for _, d, _ in start))
            for t in s.get("try") or []:
                rc, out = run_in(bash, workdir, t["command"])
                sec["try"].append({"command": t["command"], "rc": rc, "output": out[-600:]})
                if rc != 0:
                    warn(section_address(i0), f"第 {i} 节动手命令退出码 {rc}：{t['command']}（如果是故意演示报错可以忽略）", out[-600:])
            for j, c in labs:
                before = check_in(bash, workdir, c.get("checks") or [])
                if before and all(ok for ok, _, _ in before):
                    # WHY: 动手步骤直接把任务做完了，检查点就只证明"照着敲了"，证明不了懂（F-025）。
                    block(checkpoint_address(i0, j - 1),
                          f"第 {i} 节检查点 {j}：照着动手步骤敲完就已经通过了——动手在演示，检查点应该换一个对象让学习者自己做")
                outs = [run_in(bash, workdir, cmd) for cmd in c.get("solution") or []]
                after = check_in(bash, workdir, c.get("checks") or [])
                failed = [d for ok, d, _ in after if not ok]
                if failed:
                    # 证据：没通过的那几条检查怎么写的 + 参考做法的输出
                    block(checkpoint_address(i0, j - 1), f"第 {i} 节检查点 {j}：按参考做法做完仍然没通过：{'；'.join(failed)}",
                          "\n".join([json.dumps(k, ensure_ascii=False) for ok, _, k in after if not ok]
                                    + [o[-300:] for _, o in outs if o.strip()]))
                    if not broken and any(k.get("contract") for ok, _, k in after if not ok):
                        broken.append(i)
                sec["checkpoints"].append({"idx": j, "passed_before": [ok for ok, _, _ in before],
                                           "passed_after": [ok for ok, _, _ in after],
                                           "solution_rc": [rc for rc, _ in outs]})
            report.append(sec)
        return {"ok": not blocking(found), "findings": [f.as_dict() for f in found], "sections": report}


# ---------- 学习者的终端：每个单元一个常驻的 bash ----------

class Shell:
    """一个常驻的 bash 进程。变量、cd、函数在命令之间保留，和真的终端一样。

    WHY: 每条命令写进 cmd.sh 再 source，而不是直接写进 stdin：
    命令里有没配对的引号时，直接写 stdin 会让 bash 一直等后续输入，把结束标记也吞掉。
    stdin 接 /dev/null：`cat` 这类读输入的命令不会把后面的命令当成输入吃掉。
    """

    def __init__(self, env: BashPractice, unit: str):
        self.env = env
        self.unit = unit
        self.cwd = str(env.lab_dir(unit))
        self.lab_path: str | None = None
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue = queue.Queue()
        self.seq = 0

    def _environ(self) -> dict:
        env = dict(os.environ)
        # CHERE_INVOKING：Git Bash 的登录脚本默认会 cd 到 home，设了它就留在当前目录。
        env.update(CHERE_INVOKING="1", LANG="C.UTF-8", LAB=str(self.env.lab_dir(self.unit)), PAGER="cat", GIT_PAGER="cat")
        return env

    def _start(self) -> None:
        self.env.state_dir(self.unit).mkdir(parents=True, exist_ok=True)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen([self.env.bash(), "--login", "-s"], cwd=self.env.lab_dir(self.unit),
                                     env=self._environ(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=flags)
        self.q = queue.Queue()
        threading.Thread(target=self._pump, args=(self.proc, self.q), daemon=True).start()
        # 把 $LAB 换成 bash 自己的路径写法（/c/Users/...），再回到上次所在的目录。
        seq = self._send('LAB="$(cygpath -u "$LAB" 2>/dev/null || echo "$LAB")"; export LAB; cd "$LAB"')
        _, rc, cwd = self._read(seq, TIMEOUT)
        # WHY: 练习场在 bash 里的路径要问 bash 自己（Git Bash 会把临时目录映射成 /tmp，自己拼会拼错）。
        if rc is not None and cwd:
            self.lab_path = cwd
            self.cwd = cwd

    def start(self) -> None:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()

    @staticmethod
    def _pump(proc, q):
        while True:
            chunk = proc.stdout.read1(4096) if hasattr(proc.stdout, "read1") else proc.stdout.read(1)
            if not chunk:
                q.put(None)
                return
            q.put(chunk)

    def _send(self, command: str) -> int:
        """写入一条命令，返回它的编号；_read 只认这个编号的结束标记。"""
        cmd_file = self.env.state_dir(self.unit) / "cmd.sh"
        with cmd_file.open("w", encoding="utf-8", newline="\n") as f:
            f.write(command + "\n")
        posix = cmd_file.as_posix()
        self.seq += 1
        line = f'. "{posix}" </dev/null 2>&1; printf "\\n{MARK}{self.seq}:%s:%s\\n" "$?" "$PWD"\n'
        self.proc.stdin.write(line.encode("utf-8"))
        self.proc.stdin.flush()
        return self.seq

    def _read(self, seq: int, timeout: float) -> tuple[str | None, int | None, str | None]:
        """读到编号为 seq 的结束标记为止。返回（输出, 退出码, 当前目录）；超时或进程退出时退出码为 None。

        WHY: 结束标记必须独占一行、退出码是数字——set -x 会把 printf 那一行原样追踪出来
        （格式里的 \\n 是两个字符，退出码是 %s），不能把它当成真标记。
        按编号认标记：上一条命令的残留（超时后晚到的输出）丢掉，下一条命令才不会错位。
        """
        buf, deadline = b"", time.monotonic() + timeout
        end = re.compile(rb"\n" + re.escape(MARK.encode()) + rb"(\d+):(\d+):([^\n]*)\n")
        while True:
            ends = list(end.finditer(buf))
            mine = next((m for m in ends if int(m.group(1)) == seq), None)
            if mine:
                start = max((m.end() for m in ends if m.end() <= mine.start()), default=0)
                text = self._untrace(buf[start:mine.start()].decode("utf-8", "replace"))
                # 结尾的换行去掉：一个是命令自己的，一个是结束标记前补的（没换行结尾的输出也能和标记分开）。
                return text.rstrip("\n"), int(mine.group(2)), mine.group(3).decode("utf-8", "replace")
            try:
                chunk = self.q.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                return buf.decode("utf-8", "replace"), None, "timeout"
            if chunk is None:
                return buf.decode("utf-8", "replace"), None, "exited"
            buf += chunk
            if len(buf) > MAX_OUTPUT * 4:
                buf = buf[-MAX_OUTPUT * 4:]

    def _untrace(self, text: str) -> str:
        """去掉 set -x 追踪出来的练习场自己的两行（source cmd.sh、printf 结束标记）；学习者命令的追踪留着。

        WHY: 命令是 source 进来的，多了一层，bash 会把追踪前缀写成 `++`。只有确实在追踪时
        （能看到 source 或 printf 那一行的追踪）才去掉一个 `+`，和学习者在 Git Bash 里看到的一样。
        """
        posix = (self.env.state_dir(self.unit) / "cmd.sh").as_posix()
        lines = text.split("\n")
        ours = [line.startswith("+") and (MARK in line or line.rstrip("'\"").endswith(posix)) for line in lines]
        tracing = any(ours)
        return "\n".join(line[1:] if tracing and line.startswith("++") else line
                         for o, line in zip(ours, lines) if not o)

    def run(self, command: str) -> dict:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            seq = self._send(command)
            out, rc, cwd = self._read(seq, TIMEOUT)
            out = out.replace((self.env.state_dir(self.unit) / "cmd.sh").as_posix(), "bash")
            if len(out) > MAX_OUTPUT:
                out = out[:MAX_OUTPUT] + f"\n…（输出太长，只显示前 {MAX_OUTPUT} 字）"
            note = None
            if rc is None:
                self.stop()
                note = (f"命令超过 {TIMEOUT} 秒没结束，已中止。" if cwd == "timeout"
                        else "shell 已退出（比如执行了 exit）。") + "已经重新开了一个 shell，回到练习场。"
                cwd = None
            else:
                self.cwd = cwd
            return {"output": out, "rc": rc, "cwd": cwd or self.lab_posix(), "note": note,
                    "outside": bool(cwd) and not self.inside(cwd)}

    def lab_posix(self) -> str:
        if self.lab_path:
            return self.lab_path
        p = self.env.lab_dir(self.unit).resolve().as_posix()
        return "/" + p[0].lower() + p[2:] if os.name == "nt" and len(p) > 1 and p[1] == ":" else p

    def inside(self, cwd: str) -> bool:
        lab = self.lab_posix().rstrip("/").lower()
        c = cwd.rstrip("/").lower()
        return c == lab or c.startswith(lab + "/")

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p is None or p.poll() is not None:
            return
        if os.name == "nt":
            # WHY: 只杀 bash 杀不掉它启动的子进程（比如卡住的 sleep），要连进程树一起杀。
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        else:
            p.kill()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
