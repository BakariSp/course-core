"""简答题的 LLM 批改（D-031）：harness 层的一次模型调用，不是一个带工具的 agent。

    agents/short-grader/grader.yaml   用哪个模型
    agents/short-grader/SYSTEM.md     批改员的岗位说明（grader.yaml 的 prompt 部件列表里引用；D-033）
每条批改记着版本号（prompt 部件 + 模型）；版本的全部内容存在数据库里，改了 prompt 也能取回旧版比 diff。

每次调用的输入和模型原始回复留在 data/runs/short-grader/<时间>-<题>/，导师抽查时对着看。
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from studykit.app.ports import AgentRuntime, Clock
from studykit.app.versions import Versions, load_prompt, prompt_texts
from studykit.domain.harness import assemble_prompt
from studykit.domain.assessment import parse_short_grading, short_grading_prompt

NAME = "short-grader"


class LlmShortGrader:
    def __init__(self, runtime: AgentRuntime, root: Path, workspace: Path, clock: Clock, versions: Versions,
                 timeout: int = 300):
        self.runtime, self.dir, self.workspace, self.clock, self.timeout = runtime, root / "agents" / NAME, workspace, clock, timeout
        self.versions = versions

    def _spec(self) -> tuple[dict, list[tuple[str, str]]]:
        spec = yaml.safe_load((self.dir / "grader.yaml").read_text(encoding="utf-8"))
        return spec["model"], load_prompt(self.dir, spec)

    def version(self) -> str:
        model, parts = self._spec()
        return self.versions.record(NAME, prompt_texts(parts), {"model": f"{model['provider']}/{model['id']}"}).id

    def grade(self, object_id: str, q: dict, key: dict, response: str) -> dict:
        """返回 {score, items, feedback, model, version, workspace}；模型出错或格式不对就抛异常。"""
        model, parts = self._spec()
        system = assemble_prompt(parts)
        slug = re.sub(r"[^\w.-]+", "-", object_id).strip("-")
        ws = self.workspace / NAME / f"{self.clock.now():%Y%m%d-%H%M%S}-{slug}"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "input.md").write_text(short_grading_prompt(q, key, response), encoding="utf-8")
        reply = self.runtime.complete(model, system, ws / "input.md", ws, self.timeout)
        (ws / "reply.txt").write_text(reply.text or "", encoding="utf-8")
        data = parse_short_grading(reply.text, key)
        return {**data, "model": f"{model['provider']}/{model['id']}", "version": self.version(), "workspace": ws.name,
                "cost_usd": reply.cost_usd, "tokens": reply.tokens}
