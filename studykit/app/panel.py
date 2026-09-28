"""备课控制面板（D-032）：课程清单 × 学习进度 × 备课流水线 × agent 看到的上下文。

INVARIANT: 这里只读已有的记录（课程定义、课程计划、证据、Run、Grade）并投影出来，不存任何东西；
写操作只有两个：prepare()——在后台跑一次备课（产出循环：生成 → 检验 → 定点修复 → 通过就发布，见 app/prep.py），
和 publish()——学习者确认"已经学过的单元换成新版本"。
"""
from __future__ import annotations

from collections import defaultdict

from studykit.app.harness import Harness
from studykit.app.learning import Course
from studykit.app.prep import CoursePrep
from studykit.app.ports import Content, EvidenceStore, JobRunner, PlanStore, RunStore
from studykit.domain import harness as h, plan as plans
from studykit.domain.errors import DomainError
from studykit.domain.evidence import split_question

AGENT = "tutor-prep"
KB_JOB = "knowledge:all"

# agent 输入里的每一块：（key，标题，从哪来，学习者能不能直接改，怎么改）
BLOCKS = [
    ("unit_requests", "学习者对这个单元的要求", "progress/course.yaml", True, "这个单元的 requests：写给备课老师的要求"),
    ("learner", "学习者资料 + 老师的观察", "progress/profile.yaml + 证据", True,
     "资料直接改 profile.yaml；观察是证据（proposed_strategy），学习者可以推翻（refuted_strategy）"),
    ("course_info", "课程定义里这个学科和单元", "progress/course.yaml", True, "目标、主课、这个单元看哪部分、在项目里哪里用到"),
    ("known_titles", "已经掌握（讲解里可以直接用）", "知识图 + 证据（自动算）", False,
     "不能手改：做对题目，或在新词上点「我早就知道」，它就会变"),
    ("related", "和这个单元相关的薄弱点、先修", "知识图 + 证据（自动算）", False, "导师用 kg observe 记录你说没懂的地方"),
    ("existing_nodes", "知识图里已有的节点", "knowledge/<学科>.yaml", True, "新概念 id 加在这里"),
    ("budget", "时间预算", "progress/profile.yaml 的 time", True, "unit_budget_minutes / session_minutes / max_new_terms"),
    ("hosts", "能打开的网站（白名单）", "progress/course.yaml 里所有材料的域名", True, "在课程定义里加材料，这个网站就能打开"),
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
        for s in self.content.course().subjects:
            units = []
            for u in s.units:
                uid = u.id
                units.append({
                    "id": uid, "title": u.title, "requests": list(u.requests), "scope_open": u.scope_open,
                    "course": self._course(uid),
                    "quizzes": [{"ref": q.ref, "title": q.title, "questions": len(q.questions()),
                                 "answered": len(answered.get(q.ref, set()))} for q in lessons.get(uid, [])],
                    "prep": self._prep(uid, runs.get(uid, []), grades),
                    "knowledge": self._knowledge(runs.get(uid, [])),
                })
            topics.append({"id": s.id, "title": s.title, "units": units})
        return {"topics": topics, "knowledge_job": self.jobs.status(KB_JOB)}

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
        if self.content.course().find(unit)[1].scope_open:
            raise DomainError(f"{unit} 的范围还没定（一整门讲座还没拆），不能备课")
        key = self._key(unit)

        if (self.prep.status.get(unit) or {}).get("alive"):
            raise DomainError(f"{unit} 已经在备课了（另一个窗口或命令行），等它跑完")
        if not self.jobs.start(key, lambda: self.prep.prepare(unit)):
            raise DomainError(f"{unit} 已经在生成了，等这一次跑完")
        return self.jobs.status(key) or {}

    def resume_interrupted(self) -> list[str]:
        """服务启动时调一次（D-063）：进程没了（服务重启、关机）或中途报错的备课，自动重新备。返回重新开始的单元。
        知识库已经有的直接复用，重备只花大纲和各节的钱。通过或卡住（escalated）是正常结束，不在这里重来。"""
        out = []
        for _, u in self.content.course().units():
            st = self.prep.status.get(u.id) or {}
            if u.scope_open or not (st.get("state") == "error" or (st.get("state") == "running" and not st.get("alive"))):
                continue
            try:
                self.prepare(u.id)
            except DomainError:                               # 已经在备了之类：跳过这一个
                continue
            out.append(u.id)
        return out

    def prefetch_after(self, unit: str) -> str | None:
        """预备（D-040 ②）：学习者在学 unit 时，把课程清单里的下一个单元放到后台去备。返回开始备的单元，没有就是 None。

        只备从来没备过的单元：备过但没走完、卡住了的不自动重试（免得反复花钱）；已经在备的不重复开。
        progress/settings.yaml 里 prefetch_next: false 可以关掉。范围没定（scope: open）的单元不备。
        """
        if self.content.settings().get("prefetch_next") is False:
            return None
        order = self.content.course().units()
        ids = [u.id for _, u in order]
        if unit not in ids or ids.index(unit) + 1 >= len(ids):
            return None
        nxt_unit = order[ids.index(unit) + 1][1]
        if nxt_unit.scope_open:                              # 范围没定的单元不备（D-047）
            return None
        nxt = nxt_unit.id
        if self.plans.current(nxt) is not None or self._runs_by_unit().get(nxt):
            return None
        if (self.prep.status.get(nxt) or {}).get("alive") or (self.jobs.status(self._key(nxt)) or {}).get("state") == "running":
            return None
        self.prepare(nxt)
        return nxt

    def build_knowledge(self) -> dict:
        """为课程里所有还没有知识库的单元，在后台并行调研（D-040 ③：和学习者无关，课程定了就能做）。范围没定的单元跳过。"""
        units = [u.id for _, u in self.content.course().units() if not u.scope_open]
        todo = [u for u in units if self.harness.knowledge(AGENT, u) is None]
        if not todo:
            raise DomainError("所有单元都已经有知识库了")
        if not self.jobs.start(KB_JOB, lambda: self.prep.build_knowledge(todo)):
            raise DomainError("已经在备知识库了，等这一次跑完")
        return {**(self.jobs.status(KB_JOB) or {}), "units": todo}

    def publish(self, unit: str) -> dict:
        """学习者确认：已经开始学的单元，换成最近一次通过检验的新版本。"""
        return {"added_nodes": self.prep.publish(unit)}

    # ---------- 内部 ----------

    @staticmethod
    def _knowledge(runs: list[h.Run]) -> dict | None:
        kb = [r for r in runs if r.input.get("stage") == "research" and r.submitted]
        return {"run": kb[-1].id} if kb else None

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
        st = self.prep.status.get(unit) or {}              # 任何进程发起的备课都看得见（D-040）
        running = job.get("state") == "running" or bool(st.get("alive"))
        s = h.prep_stage(runs, grades, self._published_run(unit), running)
        if running:
            s["progress"] = st.get("progress") or {"step": "outline", "label": "查资料、写大纲"}
        elif job.get("state") == "error" or st.get("state") == "error":
            s["job_error"] = job.get("error") or st.get("error", "")
        elif st.get("state") == "running":                 # 记着在跑、进程却不在了：被中断的备课
            s["job_error"] = "上次备课被中断了（进程已经退出），可以重新备课"
        return s

    def _course(self, unit: str) -> dict | None:
        if self.plans.current(unit) is None:
            return None
        # WHY: 只要两个数，不调 page()——它还会算知识图、预测负担、列版本，课程首页每个单元都算一遍很慢。
        return {"passed": len(self.course.progress(unit)["passed"]), "sections": len(plans.sections_of(self.course.plan(unit)))}

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
            "stage": "repair" if run.input.get("repair") else run.input.get("stage") or "plan",   # 备课分步（D-038）
            "index": run.input.get("index"),
            "check": check and {"verdict": check.verdict, "problems": blocks(check)},
            "reviewer": reviewer and {"verdict": reviewer.verdict, "problems": blocks(reviewer),
                                      "warnings": [f["what"] for f in reviewer.detail.get("findings") or [] if f["severity"] == "warn"]},
            "verify": verify and {"verdict": verify.verdict, "problems": blocks(verify)},
            "loop": loop and {"verdict": loop.verdict, "spent_usd": loop.detail.get("spent_usd"),
                              "rounds": len(loop.detail.get("rounds") or [])},
        }
