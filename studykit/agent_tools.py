"""agent 的工具入口（接口适配器）：agents/_pi/env_bridge.ts 通过它调用 harness 里的工具。

    python -m studykit.agent_tools schema [工具,...]     工具清单（名字、说明、参数 schema）
    python -m studykit.agent_tools call <工具> <运行目录>  执行一次，参数从 stdin 读 JSON

输出永远是一行 JSON；工具失败返回 {"ok": false, "text": 原因}，模型看到原因后自己调整。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from studykit.app.harness import ToolError
from studykit.bootstrap import build


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    app = build()
    if argv[:1] == ["schema"]:
        names = argv[1].split(",") if len(argv) > 1 else None
        print(json.dumps(app.harness.tool_schemas(names), ensure_ascii=False))
        return 0
    if len(argv) == 3 and argv[0] == "call":
        try:
            args = json.loads(sys.stdin.buffer.read().decode("utf-8") or "{}")
            text = app.harness.call_tool(Path(argv[2]), argv[1], args)
            print(json.dumps({"ok": True, "text": text}, ensure_ascii=False))
        except (ToolError, ValueError) as e:
            print(json.dumps({"ok": False, "text": str(e)}, ensure_ascii=False))
        return 0
    print(json.dumps({"ok": False, "text": "用法：schema | call <tool> <run_dir>"}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
