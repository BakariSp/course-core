"""读取一套题。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from studykit import store

# INVARIANT: lesson 引用只能是 <topic>/<slug>，并且解析后必须落在 lessons/ 里面。
# WHY: 引用来自网页请求，不校验就能用 ../ 读写仓库外的文件（路径穿越）。
_REF = re.compile(r"^[a-z0-9_-]+/[a-z0-9_.-]+$")


class LessonError(ValueError):
    pass


@dataclass
class Lesson:
    ref: str
    dir: Path
    quiz: dict
    key: dict

    def question(self, qid: str) -> dict:
        for q in self.quiz.get("questions", []):
            if q["id"] == qid:
                return q
        raise LessonError(f"{self.ref} 里没有题目 {qid}")

    def answer_key(self, qid: str) -> dict:
        return (self.key.get("answers") or {}).get(qid) or {}


def lesson_dir(ref: str) -> Path:
    if not _REF.match(ref or ""):
        raise LessonError(f"课时引用格式不对：{ref!r}，应为 <学科>/<课时>")
    d = (store.LESSONS / ref).resolve()
    if not d.is_relative_to(store.LESSONS.resolve()):
        raise LessonError(f"课时路径越界：{ref!r}")
    return d


def load(ref: str) -> Lesson:
    d = lesson_dir(ref)
    if not (d / "quiz.yaml").exists():
        raise LessonError(f"找不到 {ref}/quiz.yaml")
    return Lesson(ref, d, store.load_yaml(d / "quiz.yaml"), store.load_yaml(d / "key.yaml"))


def list_all() -> list[dict]:
    out = []
    for quiz in sorted(store.LESSONS.glob("*/*/quiz.yaml")):
        ref = f"{quiz.parent.parent.name}/{quiz.parent.name}"
        data = store.load_yaml(quiz)
        out.append({"ref": ref, "title": data.get("title", ref), "count": len(data.get("questions", []))})
    return out
