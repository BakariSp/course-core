"""判分（D-001）：所有题型共用一个模型，只有界面不同。

    view()   → 网页显示需要的数据（永远不含答案）
    act()    → 作答过程中的交互（跑测试、执行命令……）
    check()  → 判分，返回 Verdict；score 为 None 表示需要导师批改

检验器不写存储：判分过程中值得记下的事（比如每次跑测试）放进 records，由服务层写成证据。
这里只有纯检验器（choice / fill / short）；要起进程的（code、terminal）由学科 spec 提供。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from studykit.domain.errors import LessonError


@dataclass(frozen=True)
class Record:
    """检验器要求记下的一条证据（verb + 分数 + payload），服务层补上信封的其余部分。"""
    verb: str
    score: float | None
    payload: dict


@dataclass
class Verdict:
    score: float | None                                  # 0–1；None = 等导师批改
    feedback: list[str] = field(default_factory=list)    # 给学习者看的逐条反馈
    detail: dict = field(default_factory=dict)           # 给导师看的附加信息（测试输出等）
    records: list[Record] = field(default_factory=list)


@dataclass
class ActResult:
    data: dict
    records: list[Record] = field(default_factory=list)


class Checker(Protocol):
    kind: str
    auto: bool                   # False = 需要导师批改

    def view(self, q: dict, ctx: Any) -> dict: ...
    def act(self, q: dict, key: dict, ctx: Any, action: dict) -> ActResult: ...
    def check(self, q: dict, key: dict, ctx: Any, response) -> Verdict: ...


@dataclass
class Lesson:
    """一套题。quiz 是题面（可以给页面），key 是答案（永远不给页面）。"""
    ref: str
    quiz: dict
    key: dict
    unit: str | None = None

    @property
    def title(self) -> str:
        return self.quiz.get("title", self.ref)

    def questions(self) -> list[dict]:
        return self.quiz.get("questions", [])

    def question(self, qid: str) -> dict:
        for q in self.questions():
            if q["id"] == qid:
                return q
        raise LessonError(f"{self.ref} 里没有题目 {qid}")

    def answer_key(self, qid: str) -> dict:
        return (self.key.get("answers") or {}).get(qid) or {}

    @property
    def exam(self) -> bool:
        """单元题（quiz.yaml 带 unit:）默认整卷一次交（D-031）；`mode: practice` 可以改回一题一交。"""
        return self.quiz.get("mode", "exam" if self.unit else "practice") == "exam"

    def max_runs(self, q: dict) -> int | None:
        """整卷模式下代码题最多运行几次测试（D-031）。None = 不限。"""
        if not self.exam:
            return None
        return int(q.get("max_runs", self.quiz.get("max_runs", DEFAULT_MAX_RUNS)))


DEFAULT_MAX_RUNS = 5


# ---------- 纯检验器 ----------

class PureChecker:
    kind = ""
    auto = True

    def view(self, q, ctx) -> dict:
        return {}

    def act(self, q, key, ctx, action) -> ActResult:
        raise LessonError(f"检验器 {self.kind} 没有交互动作")


def _letters(value) -> set[str]:
    items = value if isinstance(value, list) else [value]
    return {str(v).strip().upper() for v in items if str(v).strip()}


class Choice(PureChecker):
    """单选 / 多选。response 是选中的字母列表，如 ["A", "C"]。"""
    kind = "choice"

    def view(self, q, ctx):
        return {"options": q["options"], "multi": bool(q.get("multi"))}

    def check(self, q, key, ctx, response):
        want, got = _letters(key["answer"]), _letters(response or [])
        if not q.get("multi"):
            score = 1.0 if got == want else 0.0
        else:
            # WHY: 多选按"选对的减去选错的"给部分分，防止全选也能拿分。
            score = max(0.0, (len(got & want) - len(got - want)) / len(want))
        return Verdict(score, [f"正确答案：{'、'.join(sorted(want))}"])


BLANK = "____"


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


class Fill(PureChecker):
    """填空，一题可以有多个空（题干里每个 ____ 是一个空）。response 是每个空的答案列表。"""
    kind = "fill"

    def view(self, q, ctx):
        return {"blanks": max(1, q["prompt"].count(BLANK))}

    def check(self, q, key, ctx, response):
        answers = response if isinstance(response, list) else [response]
        blanks = [b if isinstance(b, list) else [b] for b in key["blanks"]] if "blanks" in key else [key.get("accept", [])]
        feedback, right = [], 0
        for i, accept in enumerate(blanks):
            got = answers[i] if i < len(answers) else ""
            if key.get("regex"):
                ok = any(re.fullmatch(p, str(got).strip(), re.IGNORECASE) for p in accept)
            else:
                ok = _norm(got) in {_norm(a) for a in accept}
            right += ok
            feedback.append(f"第 {i + 1} 空：{'✓' if ok else '✗'} 你填的是「{got}」")
        return Verdict(right / len(blanks), feedback)


class Short(PureChecker):
    """简答题（解释、找 bug、项目映射）。只记录回答，由导师按 key.yaml 里的 rubric 批改。"""
    kind = "short"
    auto = False

    def check(self, q, key, ctx, response):
        return Verdict(None, ["已提交，等导师批改。"])


CORE_CHECKERS = (Choice(), Fill(), Short())


# ---------- 简答题的 LLM 批改（D-031）：拼 prompt、解析回复。调用模型在 app 层 ----------

_WEIGHT = re.compile(r"[（(]\s*(\d*\.?\d+)\s*[）)]\s*$")


def rubric_items(key: dict) -> list[dict]:
    """rubric 每条末尾的（0.5）是这一条的满分；没写分值的是加分项（可替代其他条，总分封顶 1）。"""
    out = []
    for i, text in enumerate(key.get("rubric") or [], 1):
        m = _WEIGHT.search(str(text))
        out.append({"id": i, "text": str(text), "max": float(m[1]) if m else None})
    return out


def short_grading_prompt(q: dict, key: dict, response: str) -> str:
    rubric = "\n".join(f"{it['id']}. {it['text']}" + ("" if it["max"] is not None else "（加分项：可以替代其他条目的分数，总分封顶 1）")
                       for it in rubric_items(key))
    fmt = json.dumps({"items": [{"id": 1, "score": 0.25, "why": "回答里哪句话对上了这条 / 缺了什么（引用学习者原话）"}],
                      "feedback": "给学习者的一两句话：错在哪个概念、正确思路是什么"}, ensure_ascii=False)
    return "\n\n".join([
        "# 题目\n\n" + q["prompt"].strip(),
        "# 评分标准（rubric）\n\n" + rubric,
        "# 参考解析（只给你看，用来理解评分标准）\n\n" + str(key.get("explain") or "（无）").strip(),
        "# 学习者的回答\n\n" + (response or "（空）"),
        "# 输出格式\n\n只输出一个 JSON 对象，items 里每条评分标准一项（加分项没对上可以不写）：\n" + fmt,
    ])


def parse_short_grading(text: str, key: dict) -> dict:
    """解析模型的批改。每条不能超过这一条的满分；总分 = 各条之和，封顶 1。格式不对就抛错（交给导师批改）。"""
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("批改模型没有输出 JSON")
    data = json.loads(m.group(0))
    items = {it["id"]: it for it in rubric_items(key)}
    got = {int(x.get("id")): x for x in data.get("items") or [] if str(x.get("id", "")).isdigit()}
    missing = [i for i, it in items.items() if it["max"] is not None and i not in got]
    if missing:
        raise ValueError(f"批改缺少这些评分条目：{missing}")
    out = []
    for i, x in sorted(got.items()):
        if i not in items:
            raise ValueError(f"没有第 {i} 条评分标准")
        score, cap = x.get("score"), items[i]["max"]
        if not isinstance(score, (int, float)) or score < 0 or (cap is not None and score > cap + 1e-9):
            raise ValueError(f"第 {i} 条的分数不对：{score}（满分 {cap}）")
        out.append({"id": i, "score": float(score), "max": cap, "why": str(x.get("why") or "")})
    return {"score": round(min(1.0, sum(x["score"] for x in out)), 3), "items": out,
            "feedback": str(data.get("feedback") or "")}


# ---------- 课程页检查点：choice / fill（lab 型由学科 spec 提供） ----------

@dataclass
class CheckpointResult:
    score: float
    feedback: list[str]
    trap: dict | None = None
    checks: list[dict] | None = None     # lab 型：每个检查项过没过（给页面）
    reveal: dict = field(default_factory=dict)   # 做对之后才给的东西（如参考做法）


def grade_choice_checkpoint(item: dict, response) -> CheckpointResult:
    v = Choice().check({"multi": bool(item.get("multi"))}, {"answer": item["answer"]}, None, response)
    picked = _letters(response if isinstance(response, list) else [response])
    trap = next((t for t in item.get("traps") or [] if str(t.get("when", "")).upper() in picked), None)
    return CheckpointResult(v.score, [], trap)          # 不回显正确答案：做错了还要能再试


def grade_fill_checkpoint(item: dict, response) -> CheckpointResult:
    v = Fill().check({"prompt": item["prompt"]}, {"blanks": item["accept"], "regex": item.get("regex")}, None, response)
    answers = [str(a) for a in (response if isinstance(response, list) else [response])]
    trap = next((t for t in item.get("traps") or []
                 if any(re.search(str(t.get("when", "")), a, re.IGNORECASE) for a in answers)), None)
    return CheckpointResult(v.score, [f.split("你填的是")[0].strip() for f in v.feedback], trap)


# 端口的签名是 (单元, 题, 作答)：学科题型（如练习场）要知道是哪个单元；choice / fill 用不到单元。
CORE_CHECKPOINTS = {"choice": lambda unit, item, response: grade_choice_checkpoint(item, response),
                    "fill": lambda unit, item, response: grade_fill_checkpoint(item, response)}
