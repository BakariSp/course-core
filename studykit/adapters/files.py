"""人写的内容（在 git 里审）：题、知识图、课程清单、设置。实现 app.ports.Content。

    lessons/<学科>/<课时>/quiz.yaml · key.yaml · code/
    knowledge/<学科>.yaml        知识图：节点 + 先修（状态不在这里，由证据算出）
    progress/syllabus.yaml       单元和学习状态
    progress/settings.yaml       时间预算、练习场位置等
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import yaml

from studykit.app.paths import safe_path
from studykit.domain.assessment import Lesson
from studykit.domain.errors import LessonError
from studykit.domain.ids import LessonRef
from studykit.domain.knowledge import Node


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class FileContent:
    def __init__(self, root: Path):
        self.root = root
        self.lessons_dir = root / "lessons"
        self.knowledge_dir = root / "knowledge"
        self.progress_dir = root / "progress"

    # ---------- 题 ----------

    def lesson_dir(self, ref: str) -> Path:
        LessonRef(ref)
        return safe_path(self.lessons_dir, ref)

    def lesson(self, ref: str) -> Lesson:
        d = self.lesson_dir(ref)
        if not (d / "quiz.yaml").exists():
            raise LessonError(f"找不到 {ref}/quiz.yaml")
        quiz = load_yaml(d / "quiz.yaml")
        return Lesson(ref, quiz, load_yaml(d / "key.yaml"), quiz.get("unit"))

    def lessons(self) -> list[Lesson]:
        return [self.lesson(f"{q.parent.parent.name}/{q.parent.name}")
                for q in sorted(self.lessons_dir.glob("*/*/quiz.yaml"))]

    # ---------- 知识图 ----------

    def graph(self) -> dict[str, Node]:
        nodes: dict[str, Node] = {}
        for path in sorted(self.knowledge_dir.glob("*.yaml")):
            for nid, n in (load_yaml(path).get("nodes") or {}).items():
                nodes[nid] = Node(nid, n.get("title") or nid, n.get("desc") or "", n.get("kind") or "concept",
                                  list(n.get("requires") or []), list(n.get("units") or []), list(n.get("aliases") or []))
        return nodes

    def add_nodes(self, proposed: list[dict]) -> list[str]:
        """把新节点写进 knowledge/<学科>.yaml。已有的节点不覆盖（审阅过的内容优先）。返回新增的 id。"""
        existing = self.graph()
        by_topic: dict[str, list[dict]] = defaultdict(list)
        for n in proposed:
            if n["id"] not in existing:
                by_topic[n["id"].split(".")[0]].append(n)
        added = []
        for topic, items in by_topic.items():
            path = self.knowledge_dir / f"{topic}.yaml"
            data = load_yaml(path) or {"topic": topic, "nodes": {}}
            data.setdefault("nodes", {})
            for n in items:
                # WHY: 值可能是 str 的子类（如 UnitId），safe_dump 不认，先转成普通类型。
                data["nodes"][str(n["id"])] = json.loads(json.dumps(
                    {k: n[k] for k in ("title", "desc", "kind", "requires", "units", "aliases") if n.get(k)}, ensure_ascii=False))
                added.append(n["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            header = "# 知识图（D-020）：节点 + 先修关系。状态不写在这里，由证据算出（study.py kg）。\n"
            path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
        return added

    # ---------- 课程清单、设置 ----------

    def syllabus(self) -> dict:
        return load_yaml(self.progress_dir / "syllabus.yaml")

    def settings(self) -> dict:
        return load_yaml(self.progress_dir / "settings.yaml")
