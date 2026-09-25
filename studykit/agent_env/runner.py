"""运行一个 agent：拼简报 → 在隔离的工作目录里启动 pi → 记录整个过程。

每次运行一个目录 runs/agents/<agent>/<run_id>/：
    brief.md        给 agent 的简报（任务模板 + 单元信息 + 学习者画像）
    context.json    环境参数（白名单、必需章节），工具执行时读取
    events.jsonl    pi 的完整事件流（每次模型输出、每次工具调用）
    fetches.jsonl   agent 打开过的网页（评测时核对链接出处用）
    output.md       agent 提交的结果
    meta.json       模型、prompt 版本、耗时、token、工具调用统计

INVARIANT: pi 以最小权限运行——不加载任何用户级配置、上下文文件、技能和扩展，关闭全部内置工具，
只开放 agent.yaml 里声明的环境工具。agent 能做的事完全由 studykit/agent_env/tools.py 决定。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from studykit import store
from studykit.agent_env import tools

AGENTS = store.ROOT / "agents"
RUNS = store.ROOT / "runs" / "agents"
BRIDGE = AGENTS / "_pi" / "env_bridge.ts"
PI_HOME = AGENTS / "_pi" / "home"        # 专用的 pi 配置目录，和学习者自己的 ~/.pi 隔离
LOCAL_ENV = store.ROOT / "local.env"
LEARNER = store.PROGRESS_DIR / "learner.md"


class AgentError(Exception):
    pass


@dataclass
class Agent:
    name: str
    dir: Path
    spec: dict

    def file(self, key: str) -> Path:
        return self.dir / self.spec[key]

    def prompt_version(self) -> str:
        """prompt 版本 = 岗位说明和任务模板内容的哈希。内容不变，版本号就不变。"""
        h = hashlib.sha256()
        for key in ("system_prompt", "task"):
            h.update(self.file(key).read_bytes())
        return h.hexdigest()[:10]


def env_version() -> str:
    """环境版本 = 工具实现和 pi 适配器的哈希。效果变了，要能分清是 prompt 改了还是环境改了。"""
    h = hashlib.sha256()
    for p in (Path(tools.__file__), BRIDGE):
        h.update(p.read_bytes())
    return h.hexdigest()[:10]


def load_agent(name: str) -> Agent:
    d = AGENTS / name
    if not re.fullmatch(r"[a-z0-9-]+", name) or not (d / "agent.yaml").exists():
        raise AgentError(f"没有这个 agent：{name}")
    return Agent(name, d, store.load_yaml(d / "agent.yaml"))


def load_local_env(path: Path | None = None) -> dict[str, str]:
    """读 local.env 里的 KEY=VALUE。INVARIANT: 值只放进子进程环境，不写进任何日志或文件。"""
    path = path or LOCAL_ENV
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                out[m[1]] = m[2].strip("\"'")
    return out


# ---------- 简报 ----------

def find_unit(unit_id: str) -> tuple[str, dict, dict]:
    topics = (store.load_yaml(store.SYLLABUS).get("topics") or {})
    for tid, topic in topics.items():
        for unit in topic.get("units") or []:
            if unit.get("id") == unit_id:
                return tid, topic, unit
    raise AgentError(f"syllabus.yaml 里没有单元 {unit_id}")


def curriculum_row(topic_id: str) -> str:
    text = tools.CURRICULUM.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = next((l for l in lines if l.startswith("| id ")), "")
    row = next((l for l in lines if l.startswith(f"| `{topic_id}` ")), "")
    if not row:
        raise AgentError(f"curriculum.md 里找不到学科 {topic_id}")
    return "\n".join([header, "|" + "---|" * (header.count("|") - 1), row]) if header else row


def build_brief(agent: Agent, unit_id: str) -> str:
    tid, topic, unit = find_unit(unit_id)
    learner = LEARNER.read_text(encoding="utf-8") if LEARNER.exists() else "（没有学习者画像）"
    learner = re.sub(r"<!--.*?-->", "", learner, flags=re.DOTALL).strip()
    return agent.file("task").read_text(encoding="utf-8").format(
        unit_id=unit_id, unit_title=unit.get("title", unit_id), topic_id=tid,
        topic_title=topic.get("title", tid), unit_notes=unit.get("notes") or "无",
        curriculum_row=curriculum_row(tid), learner=learner,
    )


# ---------- 运行 ----------

def pi_command() -> list[str]:
    """直接用 node 跑 pi 的入口脚本。WHY: Windows 上 pi 是 .cmd 包装，经 cmd.exe 转发时参数里的中文和引号会被改写。"""
    node = shutil.which("node")
    if not node:
        raise AgentError("找不到 node，先安装 Node.js")
    npm_root = subprocess.run([shutil.which("npm") or "npm", "root", "-g"], capture_output=True, text=True,
                              shell=os.name == "nt").stdout.strip()
    cli = Path(npm_root) / "@earendil-works" / "pi-coding-agent" / "dist" / "bundle" / "cli.js"
    if not cli.exists():
        raise AgentError("找不到 pi，先运行 npm install -g @earendil-works/pi-coding-agent")
    return [node, str(cli)]


def summarize_events(lines: list[str]) -> dict:
    tool_calls: dict[str, dict] = {}
    tokens = cost = 0.0
    final_text = ""
    errors = []
    for line in lines:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "tool_execution_end":
            c = tool_calls.setdefault(ev.get("toolName", "?"), {"calls": 0, "errors": 0})
            c["calls"] += 1
            c["errors"] += bool(ev.get("isError"))
        elif t == "message_end" and (ev.get("message") or {}).get("role") == "assistant":
            msg = ev["message"]
            usage = msg.get("usage") or {}
            tokens += usage.get("totalTokens") or 0
            cost += (usage.get("cost") or {}).get("total") or 0
            texts = [b.get("text", "") for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
            if texts:
                final_text = "\n".join(texts)
            if msg.get("stopReason") == "error" or msg.get("errorMessage"):
                errors.append(msg.get("errorMessage") or "stopReason=error")
        elif t == "extension_error":
            errors.append(ev.get("error"))
    return {"tool_calls": tool_calls, "tokens": int(tokens), "cost_usd": round(cost, 6),
            "final_text": final_text[-2000:], "errors": errors}


def run(agent_name: str, unit_id: str, model: str | None = None, timeout: int = 900) -> Path:
    agent = load_agent(agent_name)
    brief = build_brief(agent, unit_id)
    started = dt.datetime.now()
    run_dir = RUNS / agent.name / f"{started:%Y%m%d-%H%M%S}-{unit_id}"
    run_dir.mkdir(parents=True)
    PI_HOME.mkdir(parents=True, exist_ok=True)
    (run_dir / "brief.md").write_text(brief, encoding="utf-8")
    required = agent.spec["output"]["required_sections"]
    (run_dir / "context.json").write_text(json.dumps(
        {"hosts": sorted(tools.allowed_hosts()), "required_sections": required}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    m = agent.spec["model"]
    model_id = model or m["id"]
    tool_list = ",".join(agent.spec["tools"])
    cmd = pi_command() + [
        "--mode", "json", "--no-session",
        "--no-context-files", "--no-skills", "--no-prompt-templates", "--no-extensions", "--no-approve",
        "--no-builtin-tools", "-e", str(BRIDGE), "--tools", tool_list,
        "--provider", m["provider"], "--model", model_id,
        *(["--thinking", m["thinking"]] if m.get("thinking") else []),
        "--system-prompt", str(agent.file("system_prompt")),
        "@brief.md", "按简报完成任务，最后用 submit 提交。",
    ]
    env = {**os.environ, **load_local_env(),
           "PI_CODING_AGENT_DIR": str(PI_HOME), "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
           "STUDY_PYTHON": sys.executable, "STUDY_ROOT": str(store.ROOT),
           "STUDY_RUN_DIR": str(run_dir), "STUDY_TOOLS": tool_list}
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=run_dir, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        stderr, code = f"超过 {timeout} 秒，已中止", None
    (run_dir / "events.jsonl").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    summary = summarize_events(stdout.splitlines())
    meta = {
        "schema_version": 1, "agent": agent.name, "unit": unit_id, "run_id": run_dir.name,
        "prompt_version": agent.prompt_version(), "env_version": env_version(), "provider": m["provider"], "model": model_id,
        "started": started.isoformat(timespec="seconds"), "seconds": round(time.monotonic() - t0, 1),
        "exit_code": code, "submitted": (run_dir / "output.md").exists(), **summary,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir
