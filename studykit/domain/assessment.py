"""判分（D-001）：所有题型共用一个模型，只有界面不同。

    view()   → 网页显示需要的数据（永远不含答案）
    act()    → 作答过程中的交互（跑测试、执行命令……）
    check()  → 判分，返回 Verdict；score 为 None 表示需要导师批改

检验器不写存储：判分过程中值得记下的事（比如每次跑测试）放进 records，由服务层写成证据。
这里只有纯检验器（choice / fill / short）；要起进程的（code、terminal）由学科 spec 提供。
"""
from __future__ import annotations

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
