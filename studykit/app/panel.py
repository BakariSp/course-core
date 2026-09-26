"""备课控制面板（D-032）：课程清单 × 学习进度 × 备课流水线 × agent 看到的上下文。

INVARIANT: 这里只读已有的记录（syllabus、课程计划、证据、Run、Grade）并投影出来，不存任何东西；
写操作只有两个：prepare()——在后台跑一次备课（产出循环：生成 → 检验 → 定点修复 → 通过就发布，见 app/prep.py），
和 publish()——学习者确认"已经学过的单元换成新版本"。
"""
from __future__ import annotations

from collections import defaultdict

from studykit.app.harness import Harness
from studykit.app.learning import Course
from studykit.app.prep import CoursePrep
from studykit.app.ports import Content, EvidenceStore, JobRunner, PlanStore, RunStore
from studykit.domain import harness as h
from studykit.domain.errors import DomainError
from studykit.domain.evidence import split_question

AGENT = "tutor-prep"

# agent 输入里的每一块：（key，标题，从哪来，学习者能不能直接改，怎么改）
BLOCKS = [
    ("unit_notes", "单元备注", "progress/syllabus.yaml", True, "改这个单元的 notes 字段：写给 agent 的备课要求"),
    ("learner", "学习者画像", "progress/learner.md", True, "直接改：电脑、背景、对你有效的讲法都写在这里"),
    ("curriculum_row", "课程清单里这一学科的一行", "curriculum.md", True, "主课、看哪部分、学到什么程度；顺序和取舍由你决定"),
    ("known_titles", "已经掌握（讲解里可以直接用）", "知识图 + 证据（自动算）", False,
     "不能手改：做对题目，或在新词上点「我早就知道」，它就会变"),
    ("related", "和这个单元相关的薄弱点、先修", "知识图 + 证据（自动算）", False, "导师用 kg observe 记录你说没懂的地方"),
    ("existing_nodes", "知识图里已有的节点", "knowledge/<学科>.yaml", True, "新概念 id 加在这里"),
    ("budget", "时间预算", "progress/settings.yaml", True, "unit_budget_minutes / session_minutes / max_new_terms"),
    ("hosts", "能打开的网站（白名单）", "curriculum.md 里出现过的链接", True, "在课程清单里加链接，这个网站就能打开"),
]
# 调用 LLM 的地方（D-033）：每个都有自己的版本历史
CALL_SITES = [(AGENT, "备课 agent（生成 + 定点修复）"), (f"{AGENT}#reviewer", "备课的评审模型（安全闸门 + 质量，D-035）"),
              (f"{AGENT}#judge", "备课的评分模型（打分，只进统计）"), ("short-grader", "简答题批改")]
BUDGET_KEYS =("unit_budget_minutes", "session_minutes", "max_new_terms")


class Panel:
    def __init__(self, *, content: Content, harness: Harness, prep: CoursePrep, course: Course, runs: RunStore,
                 plans: PlanStore, evidence: EvidenceStore, jobs: JobRunner, learner_id: str):
        self.content, self.harness, self.prep, self.course = content, harness, prep, course
        self.runs, self.plans, self.evidence, self.jobs = runs, plans, evidence, jobs
        self.learner_id = learner_id

    # ---------- 总览 ----------

    def overview(self) -> dict:
        runs, grades = self._runs_by_unit(), self._grades_by_run()
        lessons: dict[str, list] = defaultdict(list)
        for l in self.content.lessons():
            if l.unit:
                lessons[l.unit].append(l)
        answered = self._answered()
        topics = []
        for tid, topic in (self.content.syllabus().get("topics") or {}).items():
            units = []
            for u in topic.get("units") or []:
                uid = u.get("id")
                units.append({
                    "id": uid, "title": u.get("title", uid), "status": u.get("status", "todo"),
                    "done_on": str(u.get("done_on") or ""), "notes": u.get("notes") or "",
                    "course": self._course(uid),
                    "quizzes": [{"ref": q.ref, "title": q.title, "questions": len(q.questions()),
                                 "answered": len(answered.get(q.ref, set()))} for q in lessons.get(uid, [])],
                    "prep": self._prep(uid, runs.get(uid, []), grades),
                })
            topics.append({"id": tid, "title": topic.get("title", tid), "units": units})
        return {"topics": topics}

    # ---------- 单元详情：每次运行的评测和审阅 ----------

    def unit(self, unit: str) -> dict:
        self.harness.build_input(unit)                        # 顺便校验单元存在
        runs, grades = self._runs_by_unit().get(unit, []), self._grades_by_run()
        prep = self._prep(unit, runs, grades)
        rows = [self._run_row(r, grades.get(r.id, []), prep["published_run"]) for r in reversed(runs)]
        return {"unit": unit, "prep": prep, "job": self.jobs.status(self._key(unit)), "runs": rows}

    # ---------- agent 看到的上下文 ----------

    def context(self, unit: str) -> dict:
        data = self.harness.build_input(unit)
        agent = self.harness.agent(AGENT)
        task = agent.file("task").read_text(encoding="utf-8")
        values = {**data, "budget": {k: data[k] for k in BUDGET_KEYS}}
        blocks = [{"key": k, "title": t, "source": src, "editable": ed, "how": how, "value": values.get(k)}
                  for k, t, src, ed, how in BLOCKS]
        runs = self._runs_by_unit().get(unit, [])
        changed = []
        if runs:                                              # 和上一次运行时冻结的输入比，哪几块变了
            before = {**runs[-1].input, "budget": {k: runs[-1].input.get(k) for k in BUDGET_KEYS}}
            changed = [k for k, *_ in BLOCKS if before.get(k) != values.get(k)]
        # system prompt 的每个部件一块，按拼接顺序（D-033）；最后是任务模板
        prompts = [{"title": f"system prompt 部件 · {i['part']}", "source": f"agents/{AGENT}/{i['file']}",
                    "text": (agent.dir / i["file"]).read_text(encoding="utf-8")} for i in agent.spec.get("prompt") or []]
        prompts.append({"title": "任务模板（上面各块填进这里，就是简报）", "source": f"agents/{AGENT}/task.md", "text": task})
        return {"unit": unit, "blocks": blocks, "brief": self.harness.render_brief(task, data), "prompts": prompts,
                "changed_since_last_run": changed, "last_run": runs[-1].id if runs else None}

    # ---------- 版本（D-033）：每个调用 LLM 的地方，每一版改了什么 ----------

    def versions(self) -> dict:
        return {"agents": [{"agent": name, "title": title, "versions": self.harness.versions.history(name)}
                           for name, title in CALL_SITES]}

    def version_text(self, variant: str, part: str) -> str:
        text = self.harness.versions.text(variant, part)
        if text is None:
            raise DomainError(f"版本 {variant} 没有存下 {part} 的内容")
        return text

    # ---------- 写：开始一次备课 ----------

    def prepare(self, unit: str) -> dict:
        self.harness.build_input(unit)                        # 单元不存在就在这里报错，不开线程
        key = self._key(unit)

        if not self.jobs.start(key, lambda: self.prep.prepare(unit)):
            raise DomainError(f"{unit} 已经在生成了，等这一次跑完")
        return self.jobs.status(key) or {}

    def publish(self, unit: str) -> dict:
        """学习者确认：已经开始学的单元，换成最近一次通过检验的新版本。"""
        return {"added_nodes": self.prep.publish(unit)}

    # ---------- 内部 ----------

    @staticmethod
    def _key(unit: str) -> str:
        return f"{AGENT}:{unit}"

    def _runs_by_unit(self) -> dict[str, list[h.Run]]:
        out: dict[str, list[h.Run]] = defaultdict(list)
        for r in sorted(self.runs.runs(AGENT), key=lambda r: r.started):
            out[r.unit].append(r)
        return out

    def _grades_by_run(self) -> dict[str, list[h.Grade]]:
        out: dict[str, list[h.Grade]] = defaultdict(list)
        for g in self.runs.grades(agent=AGENT):
            out[g.run].append(g)
        return out

    def _published_run(self, unit: str) -> str | None:
        plan = self.plans.current(unit)
        if plan is None:
            return None
        return (plan.get("provenance") or {}).get("run") or "（手工发布）"

    def _prep(self, unit: str, runs: list[h.Run], grades: dict[str, list[h.Grade]]) -> dict:
        job = self.jobs.status(self._key(unit)) or {}
        s = h.prep_stage(runs, grades, self._published_run(unit), job.get("state") == "running")
        if job.get("state") == "error":
            s["job_error"] = job.get("error", "")
        return s

    def _course(self, unit: str) -> dict | None:
        if self.plans.current(unit) is None:
            return None
        p = self.course.page(unit)
        return {"passed": len(p["progress"]["passed"]), "sections": p["section_count"]}

    def _answered(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = defaultdict(set)
        for e in self.evidence.query(self.learner_id, verbs=["answered"], object_type="question"):
            ref, qid = split_question(e.object_id)
            out[ref].add(qid)
        return out

    @staticmethod
    def _run_row(run: h.Run, grades: list[h.Grade], published: str | None) -> dict:
        def last(grader):
            gs = [g for g in grades if g.grader == grader]
            return gs[-1] if gs else None

        check, reviewer, verify, loop = last("check"), last("reviewer"), last("practice_verify"), last("loop")
        blocks = lambda g: [f["what"] for f in (g.detail.get("findings") or [] if g else []) if f["severity"] == "block"]  # noqa: E731
        return {
            "id": run.id, "started": run.started, "variant": run.variant, "submitted": run.submitted,
            "minutes": round(run.seconds / 60, 1), "cost_usd": round(run.cost_usd, 3), "published": run.id == published,
            "repair_of": (run.input.get("repair") or {}).get("of"),
            "check": check and {"verdict": check.verdict, "problems": blocks(check)},
            "reviewer": reviewer and {"verdict": reviewer.verdict, "problems": blocks(reviewer),
                                      "warnings": [f["what"] for f in reviewer.detail.get("findings") or [] if f["severity"] == "warn"]},
            "verify": verify and {"verdict": verify.verdict, "problems": blocks(verify)},
            "loop": loop and {"verdict": loop.verdict, "spent_usd": loop.detail.get("spent_usd"),
                              "rounds": len(loop.detail.get("rounds") or [])},
        }
