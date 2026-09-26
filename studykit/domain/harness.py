"""harness 域（D-022）：agent 的每次工作都能被评测、被归因、被迭代。

    Variant   一个可运行的版本组合：prompt 各部件 + 任务模板 + 上下文配方 + 模型 + 工具 + 运行时，整体一个哈希
              （D-033：文本组成的内容按哈希另存一份，任意两版可以逐部件比出 diff）
    Run       一个 Variant 在一个输入上跑一次：冻结的输入、统一格式的步骤、产出、用量
    RunStep   一步：模型输出一次 / 调用一次工具（和具体的 agent loop 无关）
    Grade     对一次 Run 的一个评分：check / judge / claim_check / practice_verify / review / outcome

INVARIANT: Run 和 Grade 只追加。重新评测 = 新的一条 Grade，不覆盖旧的。
INVARIANT: 每个 Grade 记着评分器的版本；评分标准变了，分数就不能直接和旧的比。
这里不 import 学习域：outcome 评分引用学习证据只用 id（refs）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from typing import Callable
from dataclasses import dataclass, field

from studykit.domain.artifact import SEVERITIES, Finding

GRADERS = ("check", "reviewer", "practice_verify", "loop", "judge", "claim_check", "review", "outcome")


def content_hash(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return h.hexdigest()[:10]


@dataclass(frozen=True)
class Variant:
    agent: str
    parts: dict                  # 组成 → 内容哈希（或模型 id），如 {"prompt:rules": "1a8d…", "model": "deepseek/flash"}

    @property
    def id(self) -> str:
        return content_hash(self.agent, self.parts)

    def diff(self, other: "Variant") -> list[str]:
        """两个版本差在哪些组成上（"一次只改一层"的检验）。"""
        return sorted(k for k in set(self.parts) | set(other.parts) if self.parts.get(k) != other.parts.get(k))


LABEL_PARTS = ("model", "runtime")     # 这两个组成记的是名字本身，其余组成记的是内容哈希


def assemble_prompt(parts: list[tuple[str, str]]) -> str:
    """prompt 部件按顺序原样拼接（D-033）。不加分隔符：部件文件自己决定怎么衔接，拼回来和写的时候一字不差。"""
    return "".join(text for _, text in parts)


def part_diffs(a: Variant, b: Variant, blob: Callable[[str], str | None]) -> list[dict]:
    """两个版本逐个组成比较（D-033）：[{part, change: added|removed|changed, diff}]，没变的不列。

    blob(哈希) 取回存下来的文本；模型、运行时（LABEL_PARTS）记的是名字，直接比值。
    D-033 之前的版本只记了哈希、没存内容，这种组成只能说"变了"。
    """
    out = []
    for k in [*a.parts, *(k for k in b.parts if k not in a.parts)]:
        old, new = a.parts.get(k), b.parts.get(k)
        if old == new:
            continue
        change = "added" if old is None else "removed" if new is None else "changed"
        if k in LABEL_PARTS:
            out.append({"part": k, "change": change, "diff": f"- {old or ''}\n+ {new or ''}"})
            continue
        ta, tb = (blob(old) if old else ""), (blob(new) if new else "")
        if ta is None or tb is None:
            diff = "（这一版的内容没有存下来，只知道它变了）"
        else:
            diff = "".join(difflib.unified_diff(ta.splitlines(keepends=True), tb.splitlines(keepends=True),
                                                "a/" + k, "b/" + k, n=2))
        out.append({"part": k, "change": change, "diff": diff})
    return out


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


# ---------- 备课阶段（D-032）：从运行和评分推出来，不存 ----------

# 阶段 → （给人看的名字，下一步做什么，该谁做）。INVARIANT: 没有"导师"——备课全程由系统跑，卡住了才找学习者（PRD_V2）。
PREP_STAGES = {
    "todo": ("还没备课", "点「备课」：系统生成、检验、定点修复，通过就发布", "learner"),
    "preparing": ("备课中", "生成 → 检验 → 定点修复（最多 3 轮 / $1），通过就发布", "agent"),
    "escalated": ("需要你决定", "修复轮数或花费用完了还有问题：看卡住的地方，决定重新备课，或者让开发助手改 prompt", "learner"),
    "ready": ("新版本等你确认", "这个单元你已经开始学了，新版本要你确认才替换", "learner"),
    "published": ("已发布", "去课程页学", "learner"),
}


def prep_stage(runs: list[Run], grades: dict[str, list[Grade]], published_run: str | None, running: bool) -> dict:
    """一个单元的备课走到哪一步。只看最近一次运行（修复也是一次运行）；学习者在用的可能是更早发布的那一版（behind）。

    runs 按时间顺序；grades = 运行 id → 它的全部评分（按时间顺序，同一种评分以最后一条为准）。
    最近一次运行没有产出循环的结论（loop），说明它没走完循环（出错中断、或者是旧流程的运行），按"需要你决定"算。
    """
    latest = runs[-1] if runs else None
    loops = [g for g in grades.get(latest.id, []) if g.grader == "loop"] if latest else []
    loop = loops[-1] if loops else None
    stuck: list[str] = []
    if running:
        stage = "preparing"
    elif latest is None:
        stage = "published" if published_run else "todo"
    elif latest.id == published_run:
        stage = "published"
    elif loop is not None and loop.verdict == "accepted":
        stage = "ready"
    else:
        stage = "escalated"
        stuck = [f["what"] for f in (loop.detail.get("blocking") if loop else []) or []]
    label, nxt, who = PREP_STAGES[stage]
    return {"stage": stage, "label": label, "next": nxt, "who": who, "run": latest.id if latest else None,
            "published_run": published_run, "behind": bool(published_run and latest and latest.id != published_run),
            "stuck": stuck}


# ---------- 评审模型（reviewer，D-035）：带地址的发现 ----------

def parse_review(text: str, addresses: set[str]) -> list[Finding]:
    """评审模型的回答 → 发现。unsafe（不安全的命令）一律阻断；地址不合法的，退到最近的合法上级（都不合法就是整份）。

    格式：{"unsafe": [{"address", "command", "why"}], "findings": [{"address", "severity", "what", "rubric"?}（rubric = 违反的评分标准 id）]}
    """
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("评审模型没有输出 JSON")
    data = json.loads(m.group(0))

    def fix(addr) -> str:
        a = str(addr or "").strip()
        while a and a not in addresses:
            a = a.rsplit("/", 1)[0]
        return a

    out = [Finding(fix(u.get("address")), f"命令不安全：{u.get('why', '')}：{str(u.get('command', ''))[:120]}",
                   "reviewer_safety", "block", str(u.get("command", "")))
           for u in data.get("unsafe") or []]
    for f in data.get("findings") or []:
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "warn"
        what = str(f.get("what") or "").strip()
        if what:
            rubric = str(f.get("rubric") or "").strip()
            out.append(Finding(fix(f.get("address")), f"[{rubric}] {what}" if rubric else what, "reviewer", sev))
    return out


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
