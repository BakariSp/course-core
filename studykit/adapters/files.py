"""人写的内容（在 git 里审）：题、知识图、课程定义、学习者资料、设置。实现 app.ports.Content。

    lessons/<学科>/<课时>/quiz.yaml · key.yaml · code/
    knowledge/<学科>.yaml        知识图：节点 + 带类型的先修（状态不在这里，由证据算出）
    progress/course.yaml         课程定义：阶段 → 学科 → 单元（D-047）
    progress/profile.yaml        学习者自己写的资料和时间偏好（D-047）
    progress/settings.yaml       系统设置：练习场位置、要不要预备下一单元
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
from studykit.domain.curriculum import CourseDef, parse_course
from studykit.domain.knowledge import Edge, Node
from studykit.domain.profile import Profile, parse_profile


_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    # WHY: 用 libyaml 的 C 解析器（有就用）：纯 Python 的 safe_load 在课程首页要解析几十个文件，占了一大半时间。
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}


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

    def write_lesson(self, ref: str, quiz: dict, key: dict, files: dict[str, str]) -> None:
        d = self.lesson_dir(ref)
        if (d / "quiz.yaml").exists():
            raise LessonError(f"{ref} 已经有题了，不覆盖（学习者可能已经在做）")
        dump = lambda data: yaml.safe_dump(json.loads(json.dumps(data, ensure_ascii=False)),  # noqa: E731
                                           allow_unicode=True, sort_keys=False, width=120)
        for rel, text in files.items():
            p = safe_path(d, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        d.mkdir(parents=True, exist_ok=True)
        (d / "key.yaml").write_text("# 答案、检查项、rubric：只在服务器端读，不会发给网页\n" + dump(key), encoding="utf-8")
        (d / "quiz.yaml").write_text(dump(quiz), encoding="utf-8")     # 最后写：有 quiz.yaml 才算这套题存在

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
                edges = [Edge(str(e["id"]), str(e.get("kind") or "required"), str(e.get("by") or "")) for e in n.get("requires") or []]
                ms = {str(k): str(v.get("desc", "") if isinstance(v, dict) else v)
                      for k, v in (n.get("misconceptions") or {}).items()}
                nodes[nid] = Node(nid, n.get("title") or nid, n.get("desc") or "", n.get("kind") or "concept",
                                  edges, list(n.get("aliases") or []), ms)
        return nodes

    def add_nodes(self, proposed: list[dict], by: str = "") -> list[str]:
        """把新节点写进 knowledge/<学科>.yaml。已有的节点不覆盖（审阅过的内容优先）。返回新增的 id。

        proposed 是 agent 交的形状：requires = 必须先会的 id，helpful = 先会更好的 id；by = 谁提的（运行 id）。
        """
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
                edges = [{"id": r, "kind": "required", "by": by} for r in n.get("requires") or []] +                         [{"id": r, "kind": "helpful", "by": by} for r in n.get("helpful") or []]
                data["nodes"][str(n["id"])] = json.loads(json.dumps(
                    {**{k: n[k] for k in ("title", "desc", "kind") if n.get(k)}, **({"requires": edges} if edges else {}),
                     **({"aliases": n["aliases"]} if n.get("aliases") else {})}, ensure_ascii=False))
                added.append(n["id"])
            self._write_graph(path, data)
        return added

    def add_misconceptions(self, proposed: dict[str, dict[str, str]], by: str = "") -> list[str]:
        existing = self.graph()
        added = []
        for topic in sorted({nid.split(".")[0] for nid in proposed if nid in existing}):
            path = self.knowledge_dir / f"{topic}.yaml"
            data = load_yaml(path)
            for nid, ms in proposed.items():
                if nid not in existing or nid.split(".")[0] != topic:
                    continue
                for mid, desc in ms.items():
                    if mid not in existing[nid].misconceptions:
                        data["nodes"][nid].setdefault("misconceptions", {})[str(mid)] = {"desc": str(desc), "by": str(by)}
                        added.append(f"{nid}/{mid}")
            self._write_graph(path, data)
        return added

    @staticmethod
    def _write_graph(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = ("# 知识图（D-020、D-047）：节点 + 先修关系。状态不写在这里，由证据算出（study.py kg）。\n"
                  "# requires 每条：id、kind（required 必须先会 / helpful 先会更好）、by（谁说的：运行 id 或 learner）。\n"
                  "# misconceptions：常见误解 id → desc（说明）、by（谁提的）；题目的得分点用它说明错在哪（D-056）。\n")
        path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")

    # ---------- 课程清单、设置 ----------

    def course(self) -> CourseDef:
        return parse_course(load_yaml(self.progress_dir / "course.yaml"))

    def profile(self) -> Profile:
        return parse_profile(load_yaml(self.progress_dir / "profile.yaml"))

    def settings(self) -> dict:
        return load_yaml(self.progress_dir / "settings.yaml")
