"""检验器：所有题型共用一个模型，只有界面不同。

一道题的生命周期对每种检验器都一样：

    view()   → 网页显示需要的数据（永远不含答案）
    act()    → 作答过程中的交互（跑测试、执行命令……），不记录
    check()  → 判分，返回 Verdict；score 为 None 表示需要导师批改

记录答题、计算掌握度、生成 progress.md 都只依赖 Verdict，不关心是哪种检验器。
新增检验器 = 这里一个子类 + web/index.html 里一个同名的渲染函数 + docs/question-format.md 里一节说明。
REF: docs/question-format.md
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Verdict:
    score: float | None          # 0–1；None = 等导师批改
    feedback: list[str] = field(default_factory=list)   # 给学习者看的逐条反馈
    detail: dict = field(default_factory=dict)          # 给导师看的附加信息（测试输出等）


@dataclass
class Ctx:
    lesson_ref: str
    lesson_dir: Path
    workdir: Path                # 这道题专属的沙箱目录（终端题用）


class Checker:
    kind = ""
    auto = True                  # False = 需要导师批改

    def view(self, q: dict, ctx: Ctx) -> dict:
        return {}

    def act(self, q: dict, key: dict, ctx: Ctx, action: dict) -> dict:
        raise ValueError(f"检验器 {self.kind} 没有交互动作")

    def check(self, q: dict, key: dict, ctx: Ctx, response) -> Verdict:
        raise NotImplementedError


REGISTRY: dict[str, Checker] = {}


def register(cls: type[Checker]) -> type[Checker]:
    REGISTRY[cls.kind] = cls()
    return cls


def get(kind: str) -> Checker:
    if kind not in REGISTRY:
        raise ValueError(f"未知检验器：{kind}（可用：{', '.join(sorted(REGISTRY))}）")
    return REGISTRY[kind]


from studykit.checkers import choice, code, fill, short, terminal  # noqa: E402,F401  注册所有检验器
