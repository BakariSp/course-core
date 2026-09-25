"""harness 域（D-022）：agent 的每次工作都能被评测、被归因、被迭代。

    Variant   一个可运行的版本组合：岗位说明 + 任务模板 + 上下文配方 + 模型 + 工具 + 运行时，整体一个哈希
    Run       一个 Variant 在一个输入上跑一次：冻结的输入、统一格式的步骤、产出、用量
    RunStep   一步：模型输出一次 / 调用一次工具（和具体的 agent loop 无关）
    Grade     对一次 Run 的一个评分：check / judge / claim_check / practice_verify / review / outcome

INVARIANT: Run 和 Grade 只追加。重新评测 = 新的一条 Grade，不覆盖旧的。
INVARIANT: 每个 Grade 记着评分器的版本；评分标准变了，分数就不能直接和旧的比。
这里不 import 学习域：outcome 评分引用学习证据只用 id（refs）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

GRADERS = ("check", "judge", "claim_check", "practice_verify", "review", "outcome")


def content_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:10]


@dataclass(frozen=True)
class Variant:
    agent: str
    parts: dict                  # 组成 → 内容哈希（或模型 id），如 {"system_prompt": "1a8d…", "model": "deepseek/flash"}

    @property
    def id(self) -> str:
        return content_hash(self.agent, self.parts)

    def diff(self, other: "Variant") -> list[str]:
        """两个版本差在哪些组成上（"一次只改一层"的检验）。"""
        return sorted(k for k in set(self.parts) | set(other.parts) if self.parts.get(k) != other.parts.get(k))


@dataclass
class RunStep:
    idx: int
    kind: str                    # model / tool / error
    tool: str = ""
    ok: bool = True
    tokens: int = 0
    cost: float = 0.0
    summary: str = ""            # 给人看的一行
    detail: dict = field(default_factory=dict)


@dataclass
class Run:
    id: str
    agent: str
    variant: str
    unit: str
    learner: str
    mode: str                    # batch / interactive
    started: str
    input: dict                  # 冻结的输入数据（生成时学习者会什么、预算……），永久保留
    seconds: float = 0.0
    exit_code: int | None = None
    submitted: bool = False
    tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    final_text: str = ""


def summarize(steps: list[RunStep]) -> dict:
    tool_calls: dict[str, dict] = {}
    for s in steps:
        if s.kind == "tool":
            c = tool_calls.setdefault(s.tool, {"calls": 0, "errors": 0})
            c["calls"] += 1
            c["errors"] += not s.ok
    finals = [s.summary for s in steps if s.kind == "model" and s.summary]
    return {"tokens": sum(s.tokens for s in steps), "cost_usd": round(sum(s.cost for s in steps), 6),
            "tool_calls": tool_calls, "errors": [s.summary for s in steps if s.kind == "error"],
            "final_text": finals[-1][-2000:] if finals else ""}


@dataclass
class Grade:
    id: str
    ts: str
    run: str
    grader: str                  # 见 GRADERS
    grader_version: str
    actor: str                   # 谁打的分：system / 评分模型 id / tutor / learner
    score: float | None = None   # 0–1（judge 的 1-5 分换算过来）；有些评分只有结论没有分数
    verdict: str = ""            # publish / revise / reject / pass / fail
    dims: dict = field(default_factory=dict)      # 维度 → {score, reason}
    issues: list = field(default_factory=list)    # [{layer: prompt|context|tools|model|eval, what}]
    refs: list = field(default_factory=list)      # 引用的学习证据 id / 另一条 Grade
    detail: dict = field(default_factory=dict)


# ---------- 自动检查（确定性） ----------

def check_results(items: list[tuple[str, str, bool, str]]) -> tuple[float, dict]:
    """[(id, 描述, 通过?, 细节)] → (通过比例, dims)。"""
    dims = {i: {"score": 1.0 if ok else 0.0, "reason": desc + (f" —— {detail}" if detail and not ok else "")}
            for i, desc, ok, detail in items}
    return (sum(ok for _, _, ok, _ in items) / len(items) if items else 0.0), dims


# ---------- 评分模型（judge） ----------

JUDGE_SYSTEM = """你是一名严格的评审，评估一份由 AI 助教写的课程计划。
按给定的评分标准逐项打 1-5 分（5 最好），每项用一句话说明理由，指出最该改进的一处。
只输出一个 JSON 对象，不要任何其他文字，格式：
{"scores": {"<标准 id>": {"score": <1-5>, "reason": "<一句话>"}}, "top_issue": "<最大的问题>", "suggestion": "<对助教的 prompt 最值得做的一处修改>",
 "suspect_claims": [{"claim": "<你怀疑没有出处的说法>", "quote": "<从计划里原样复制的一小段文字，10-40 字>"}]}
你看不到助教读过的页面正文。怀疑某个具体事实（版本号、章节名、练习内容、数字）没有出处时，把它列进 suspect_claims，
程序会拿它去核对助教实际读到的页面；不要仅凭怀疑就在 grounded 上扣分。"""


def rubric_ids(rubric: str) -> list[str]:
    return re.findall(r"^- `([a-z_]+)`", rubric, re.MULTILINE)


def parse_judge(text: str, ids: list[str]) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("评分模型没有输出 JSON")
    data = json.loads(m.group(0))
    scores = data.get("scores") or {}
    missing = [i for i in ids if i not in scores]
    if missing:
        raise ValueError(f"评分缺少这些标准：{missing}")
    for i in ids:
        s = scores[i].get("score")
        if not isinstance(s, (int, float)) or not 1 <= s <= 5:
            raise ValueError(f"{i} 的分数不在 1-5：{s}")
    data["avg"] = round(sum(scores[i]["score"] for i in ids) / len(ids), 2)
    return data


def _squash(s: str) -> str:
    return re.sub(r"[\s`*_]+", "", s or "").lower()


def verify_claims(claims: list[dict], pages: str) -> list[dict]:
    """评分模型提出的疑点，逐条到 agent 读过的页面里找原文。WHY: 评分模型看不到页面，只能猜；核对交给程序。"""
    corpus = _squash(pages)
    out = []
    for c in claims:
        quote = str(c.get("quote") or "")
        # 引文里可能夹着计划自己的中文说明，只要其中的"事实部分"（英文、数字、代码）在页面里出现就算找到
        facts = [f for f in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*(?:\s+[A-Za-z0-9][A-Za-z0-9._/:+-]*)*", quote) if len(f) >= 3]
        needles = [_squash(f) for f in facts] or [_squash(quote)]
        found = bool(needles) and all(n and n in corpus for n in needles)
        out.append({**c, "found_in_pages": found})
    return out
