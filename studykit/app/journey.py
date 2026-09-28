"""学习者看到的课程（D-046、D-047）：课程定义 × 进度 × 备课状态 → 每个单元的状态、下一步。

INVARIANT: 只读、只投影，不存任何东西。单元状态和下一步的规则在 domain/curriculum.py（纯函数）；
这里只负责把课程定义、课程页进度、单元题、备课状态凑到一起。开发者视图（备课流水线细节）在 panel.py。
"""
from __future__ import annotations

from studykit.app.learning import Course
from studykit.app.panel import Panel
from studykit.app.ports import Content
from studykit.domain import curriculum, plan as plans


class Journey:
    def __init__(self, *, content: Content, course: Course, panel: Panel):
        self.content, self.course, self.panel = content, course, panel

    def overview(self) -> dict:
        cdef = self.content.course()
        facts = {u["id"]: u for t in self.panel.overview()["topics"] for u in t["units"]}   # 进度、单元题、备课状态
        order = cdef.unit_ids()
        subjects, flat = [], []
        for s in cdef.subjects:
            units = []
            for u in s.units:
                f = facts[u.id]
                quiz = f["quizzes"][0] if f["quizzes"] else None
                state = curriculum.unit_state(
                    scope_open=u.scope_open, prep_stage=f["prep"]["stage"],
                    walked=f["course"]["walked"] if f["course"] else None, sections=f["course"]["sections"] if f["course"] else None,
                    started=bool(f["course"] and f["course"]["last_at"]))
                row = {"id": u.id, "title": u.title, "requests": list(u.requests), "scope_open": u.scope_open,
                       "sources": [{"title": (s.source(x.ref) or curriculum.Source(x.ref, x.ref, "")).title,
                                    "url": x.url or (s.source(x.ref).url if s.source(x.ref) else ""), "note": x.note}
                                   for x in u.sources],
                       "course": f["course"], "quiz": quiz, "state": state, "knowledge": bool(f["knowledge"]),
                       "prep": {k: f["prep"].get(k) for k in ("stage", "progress", "stuck", "stopped", "job_error")},
                       "queued_after": curriculum.queued_after(order, u.id) if state["key"] == "queued" else None,
                       "serves": cdef.serves(u.id), "last_at": (f["course"] or {}).get("last_at")}
                units.append(row)
                flat.append(row)
            subjects.append({"id": s.id, "title": s.title, "priority": s.priority, "stage": s.stage, "goal": s.goal, "scope": s.scope,
                             "sources": [{"title": x.title, "url": x.url, "kind": x.kind} for x in s.sources],
                             "project_links": [{"where": x.where, "concept": x.concept} for x in s.project_links],
                             "units": units})
        flat.sort(key=lambda u: order.index(u["id"]))                  # 学习顺序按路径（D-048），不按学科
        current = curriculum.current_phase(cdef.phases, {u["id"]: u["state"]["key"] for u in flat})
        return {"goal": cdef.goal, "path": list(cdef.path),
                "phases": [{"title": p.title, "fills": list(p.fills), "units": list(p.units), "current": i == current}
                           for i, p in enumerate(cdef.phases)],
                "destination": [{"id": x.id, "can": x.can, "accept": x.accept, "units": list(x.units)} for x in cdef.destination],
                "stages": [{"id": x.id, "title": x.title, "weeks": list(x.weeks), "pass": x.pass_, "practice": x.practice}
                           for x in cdef.stages],
                "subjects": subjects,
                "next": [self._detail(n, {u["id"]: u for u in flat}) for n in curriculum.next_steps(flat)]}

    def _detail(self, step: dict, units: dict) -> dict:
        """下一步补上具体是哪一节、多少分钟：接着上次那一节；上次那节走过了，就是它后面第一节没走过的（D-064）。"""
        u = units[step["unit"]]
        out = {**step, "title": u["title"]}
        plan = self.course.plan(u["id"])
        p = self.course.progress(u["id"])
        secs, walked, last = plans.sections_of(plan), set(p["walked"]), p["last_section"]
        order = list(range(len(secs)))
        if last is not None and 0 <= last < len(secs):
            order = order[last:] + order[:last]
        i = next((k for k in order if k not in walked), None)
        if i is not None:
            out["section"] = {"index": i, "title": secs[i].get("title", ""), "minutes": secs[i].get("minutes")}
        return out
