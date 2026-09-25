"""agent loop 的 pi 实现（npm 包 @earendil-works/pi-coding-agent，不改它的源码）。实现 app.ports.AgentRuntime。

INVARIANT: pi 以最小权限运行——不加载任何用户级配置、上下文文件、技能和扩展，关闭全部内置工具，
只开放 agent.yaml 里声明的环境工具（agents/_pi/env_bridge.ts 把调用转发给 python -m studykit.agent_tools）。
INVARIANT: pi 的原始事件流只存进工作目录的 raw/（可以过期清理）；交回去的是和 pi 无关的 RunStep。
WHY: 原始事件流一次 45 MB，99% 是逐字流式输出（message_update）；有用的步骤不到 50 条。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from studykit.app.ports import AgentDef, RawRun
from studykit.domain.errors import DomainError
from studykit.domain.harness import RunStep


class AgentError(DomainError):
    pass


def load_local_env(path: Path) -> dict[str, str]:
    """读 local.env 里的 KEY=VALUE。INVARIANT: 值只放进子进程环境，不写进任何日志或文件。"""
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m[1]] = m[2].strip("\"'")
    return out


def pi_cli() -> Path:
    """直接用 node 跑 pi 的入口脚本。WHY: Windows 上 pi 是 .cmd 包装，经 cmd.exe 转发时参数里的中文和引号会被改写。"""
    npm_root = subprocess.run([shutil.which("npm") or "npm", "root", "-g"], capture_output=True, text=True,
                              shell=os.name == "nt").stdout.strip()
    return Path(npm_root) / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"


def _text(content) -> str:
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def steps_from_events(lines: list[str]) -> list[RunStep]:
    """pi 的事件流 → 统一的步骤：每次模型输出一步，每次工具执行一步。"""
    steps: list[RunStep] = []
    args: dict[str, dict] = {}
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "message_end" and (ev.get("message") or {}).get("role") == "assistant":
            msg = ev["message"]
            usage = msg.get("usage") or {}
            error = msg.get("errorMessage") or ("stopReason=error" if msg.get("stopReason") == "error" else "")
            calls = [b.get("name") for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "toolCall"]
            steps.append(RunStep(len(steps), "error" if error else "model", ok=not error,
                                 tokens=int(usage.get("totalTokens") or 0),
                                 cost=float((usage.get("cost") or {}).get("total") or 0),
                                 summary=error or _text(msg.get("content")), detail={"tool_calls": calls}))
        elif t == "tool_execution_start":
            args[ev.get("toolCallId", "")] = ev.get("args") or {}
        elif t == "tool_execution_end":
            result = _text((ev.get("result") or {}).get("content"))
            steps.append(RunStep(len(steps), "tool", ev.get("toolName", "?"), ok=not ev.get("isError"),
                                 summary=result[:300], detail={"args": args.get(ev.get("toolCallId", ""), {})}))
        elif t == "extension_error":
            steps.append(RunStep(len(steps), "error", ok=False, summary=str(ev.get("error"))))
    return steps


class PiRuntime:
    def __init__(self, bridge: Path, home: Path, local_env: Path, python: str = sys.executable, root: Path | None = None):
        self.bridge, self.home, self.local_env, self.python, self.root = bridge, home, local_env, python, root

    @property
    def version(self) -> str:
        """运行时版本 = 桥接代码 + pi 的版本。换了 loop 或升级了 pi，Variant 就不同。"""
        h = hashlib.sha256(self.bridge.read_bytes() if self.bridge.exists() else b"")
        pkg = pi_cli().parent.parent.parent / "package.json"
        if pkg.exists():
            h.update(json.loads(pkg.read_text(encoding="utf-8")).get("version", "").encode())
        return h.hexdigest()[:10]

    def _cmd(self) -> list[str]:
        node = shutil.which("node")
        if not node:
            raise AgentError("找不到 node，先安装 Node.js")
        cli = pi_cli()
        if not cli.exists():
            raise AgentError("找不到 pi，先运行 npm install -g @earendil-works/pi-coding-agent")
        return [node, str(cli)]

    def _env(self, extra: dict) -> dict:
        self.home.mkdir(parents=True, exist_ok=True)
        return {**os.environ, **load_local_env(self.local_env), "PI_CODING_AGENT_DIR": str(self.home),
                "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0", **extra}

    def run(self, agent: AgentDef, workspace: Path, model: dict, tools: list[str], timeout: int) -> RawRun:
        cmd = self._cmd() + [
            "--mode", "json", "--no-session",
            "--no-context-files", "--no-skills", "--no-prompt-templates", "--no-extensions", "--no-approve",
            "--no-builtin-tools", "-e", str(self.bridge), "--tools", ",".join(tools),
            "--provider", model["provider"], "--model", model["id"],
            *(["--thinking", model["thinking"]] if model.get("thinking") else []),
            "--system-prompt", str(agent.file("system_prompt")),
            "@brief.md", "按简报完成任务，最后提交。",
        ]
        env = self._env({"STUDY_PYTHON": self.python, "STUDY_ROOT": str(self.root or Path.cwd()),
                         "STUDY_RUN_DIR": str(workspace), "STUDY_TOOLS": ",".join(tools)})
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=workspace, env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr, code = f"超过 {timeout} 秒，已中止", None
        raw = workspace / "raw"
        raw.mkdir(exist_ok=True)
        (raw / "pi-events.jsonl").write_text(stdout, encoding="utf-8")
        (raw / "stderr.log").write_text(stderr, encoding="utf-8")
        return RawRun(steps_from_events(stdout.splitlines()), code, round(time.monotonic() - t0, 1))

    def complete(self, model: dict, system: str, prompt_file: Path, workspace: Path, timeout: int) -> str:
        """不带工具的一问一答（评分模型用）。"""
        cmd = self._cmd() + [
            "--print", "--no-session", "--no-context-files", "--no-skills", "--no-prompt-templates",
            "--no-extensions", "--no-approve", "--no-tools",
            "--provider", model["provider"], "--model", model["id"], "--system-prompt", system,
            f"@{prompt_file.name}", "按评分标准评估上面的内容，只输出 JSON。",
        ]
        proc = subprocess.run(cmd, cwd=workspace, env=self._env({}), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        (workspace / "raw").mkdir(exist_ok=True)
        (workspace / "raw" / f"{prompt_file.stem}-reply.txt").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr,
                                                                         encoding="utf-8")
        return proc.stdout
