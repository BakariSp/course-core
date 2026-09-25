"""给 agent 用的操作环境：工具、任务简报、运行记录、评测。

设计：agent 框架（pi）只是引擎，不改它的源码。这里定义 agent 能做什么（工具）、
拿到什么（简报）、交什么（提交格式）。工具用 Python 写，pi 那边只有一个很薄的适配器
（agents/_pi/env_bridge.ts）把工具调用转发过来，换别的 agent 框架也能复用。
REF: agents/README.md
"""
