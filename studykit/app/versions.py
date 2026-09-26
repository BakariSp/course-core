"""prompt 与版本（D-033）：所有调用 LLM 的地方都走这里记版本。

    tutor-prep（agent loop）  prompt 各部件 + 任务模板 + 工具代码 + 上下文配方代码 + 模型 + 运行时
    judge（评分模型）          评分 system + rubric + 模型
    short-grader（简答题批改） prompt 各部件 + 模型

一个版本 = 每个组成的内容哈希（模型、运行时记名字）。文本组成的内容按哈希另存一份（blob），
所以任意两个版本都能逐部件比出 diff——哪怕 prompt 文件早就被改掉、也没提交过 git。

INVARIANT: 先把内容存进 blob，再记版本；记下的版本号一定取得回内容。
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from studykit.app.ports import Clock, RunStore
from studykit.domain import harness as h
from studykit.domain.errors import DomainError


def load_prompt(agent_dir: Path, spec: dict) -> list[tuple[str, str]]:
    """agent.yaml / grader.yaml 里的 `prompt:` 部件列表 → [(部件名, 文本)]，按列出的顺序。"""
    items = spec.get("prompt") or []
    if not items:
        raise DomainError(f"{agent_dir.name}：没有 prompt 部件列表（D-033：写成 prompt: [{{part, file}}, …]）")
    names = [i["part"] for i in items]
    if len(set(names)) != len(names):
        raise DomainError(f"{agent_dir.name}：prompt 部件重名：{names}")
    return [(i["part"], (agent_dir / i["file"]).read_text(encoding="utf-8")) for i in items]


def prompt_texts(parts: list[tuple[str, str]]) -> dict[str, str]:
    """部件 → 版本里的组成名（prompt:<部件>）。"""
    return {f"prompt:{name}": text for name, text in parts}


class Versions:
    def __init__(self, runs: RunStore, clock: Clock):
        self.runs, self.clock = runs, clock

    def record(self, agent: str, texts: dict[str, str], labels: dict[str, str]) -> h.Variant:
        """texts：要存内容的组成（按哈希存）；labels：只记名字的组成（模型、运行时）。返回这个版本。"""
        hashes = {k: h.content_hash(v) for k, v in texts.items()}
        self.runs.put_blobs({hashes[k]: v for k, v in texts.items()})
        variant = h.Variant(agent, {**hashes, **labels})
        self.runs.add_variant(variant, self.clock.now().isoformat(timespec="seconds"))
        return variant

    def resolve(self, agent: str, ref: str) -> h.Variant:
        """版本号，可以只写前几位（至少 4 位）。"""
        hits = [v for v, _ in self.runs.variants(agent) if v.id.startswith(ref)] if len(ref) >= 4 else []
        if len(hits) != 1:
            raise DomainError(f"{agent} 没有唯一的版本 {ref}（找到 {len(hits)} 个）")
        return hits[0]

    def diff(self, a: str, b: str, agent: str | None = None) -> dict:
        va = self.resolve(agent, a) if agent else self.runs.variant(a)
        vb = self.resolve(agent, b) if agent else self.runs.variant(b)
        if va is None or vb is None:
            raise DomainError(f"没有这个版本：{a if va is None else b}")
        return {"a": va.id, "b": vb.id, "parts": h.part_diffs(va, vb, self.runs.blob)}

    def history(self, agent: str) -> list[dict]:
        """按第一次出现的顺序：每个版本跑了几次、和上一版比改了哪些组成（带 diff）。"""
        runs = Counter(r.variant for r in self.runs.runs(agent))
        out, prev = [], None
        for v, first_seen in self.runs.variants(agent):
            out.append({"variant": v.id, "first_seen": first_seen, "runs": runs.get(v.id, 0),
                        "parts": list(v.parts),
                        "changed": h.part_diffs(prev, v, self.runs.blob) if prev else []})
            prev = v
        return out

    def text(self, variant: str, part: str) -> str | None:
        v = self.runs.variant(variant)
        key = v.parts.get(part) if v else None
        return self.runs.blob(key) if key else None
