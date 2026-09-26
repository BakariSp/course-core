"""学科 spec：代码学习（D-023）。core 只通过 app.ports.Spec 认识它。

    检验器      code（pytest 判分）、terminal（受限的 git 练习终端）
    检查点题型  lab（在练习场里完成任务，检查练习场的状态）
    练习环境    真实的 bash 练习场（practice.BashPractice）
    verb        ran_tests（代码题每跑一次测试）
"""
from __future__ import annotations

from pathlib import Path

from studykit.app.ports import Spec
from studykit.domain.assessment import CheckpointResult
from studykit.domain.evidence import Feed, Verb
from studykit.specs.cs_practice import plan_rules
from studykit.specs.cs_practice.code import Code
from studykit.specs.cs_practice.practice import BashPractice
from studykit.specs.cs_practice.terminal import Terminal

NAME = "cs-practice"
VERBS = (Verb("ran_tests", NAME, ("question",), ("code", "passed", "total"), ("learner",), title="跑测试",
              feeds=(Feed("mastery", "跑了几次才全过（练习过程，不算分）"),)),)


def spec(lab_root: Path, state_root: Path, bash_path: str | None = None) -> Spec:
    practice = BashPractice(lab_root, state_root, bash_path)

    def grade_lab(unit: str, item: dict, response) -> CheckpointResult:
        results = practice.check(unit, item.get("checks") or [])
        return CheckpointResult(
            score=sum(ok for ok, _, _ in results) / max(1, len(results)),
            feedback=[f"{'✓' if ok else '✗'} {desc}" for ok, desc, _ in results],
            trap=next((c.get("trap") for ok, _, c in results if not ok and c.get("trap")), None),
            checks=[{"ok": ok, "desc": desc} for ok, desc, _ in results],
            reveal={"solution": item.get("solution") or []})

    return Spec(NAME, VERBS, (Code(), Terminal()), {"lab": grade_lab}, {"lab": plan_rules.CHECKPOINT_TYPE_DOC}, practice,
                tuple(plan_rules.RULES), plan_rules.CHECKPOINT_RULES, plan_rules.PLAN_FIELDS, plan_rules.CHECKPOINT_FIELDS)
