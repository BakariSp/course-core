"""agent 框架调用工具的入口（pi 的适配器 agents/_pi/env_bridge.ts 用它）。

    python -m studykit.agent_env schema                  → 列出工具（名字、说明、JSON Schema）
    python -m studykit.agent_env call <tool> <run_dir>   → stdin 读参数 JSON，stdout 写 {"ok", "text"}

INVARIANT: 这里的 stdout 只能输出一个 JSON 对象，适配器按 JSON 解析。
"""
import json
import sys
from pathlib import Path

from studykit.agent_env import tools


def load_context(run_dir: Path) -> tools.RunContext:
    spec = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
    return tools.RunContext(run_dir, set(spec["hosts"]), spec.get("required_sections") or [],
                            spec.get("unit_budget_minutes", 180), spec.get("session_minutes", 45))


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["schema"]:
        allow = set(argv[1].split(",")) if len(argv) > 1 else set(tools.TOOLS)
        print(json.dumps([{"name": n, "description": t["description"], "parameters": t["schema"]}
                          for n, t in tools.TOOLS.items() if n in allow], ensure_ascii=False))
        return 0
    if len(argv) == 3 and argv[0] == "call":
        name, run_dir = argv[1], Path(argv[2])
        try:
            if name not in tools.TOOLS:
                raise tools.ToolError(f"没有这个工具：{name}")
            args = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
            text = tools.TOOLS[name]["fn"](load_context(run_dir), args)
            print(json.dumps({"ok": True, "text": text}, ensure_ascii=False))
        except tools.ToolError as e:
            print(json.dumps({"ok": False, "text": str(e)}, ensure_ascii=False))
        return 0
    print(json.dumps({"ok": False, "text": "用法：schema | call <tool> <run_dir>"}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
