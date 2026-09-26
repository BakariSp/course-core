"""学习域的用例。网页和命令行只调这里；这里只依赖 domain 和端口。

    Assessment     做题、判分、批改、代码题的作答过程
    Course         课程页：计划、事件、检查点、提示、练习场、进度、课程评测
    LearnerModel   学习者模型：掌握度、节点状态、分级查询、观察、学习时长、进度报告
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from studykit.app.ports import CheckCtx, CheckpointGrader, Clock, Content, EvidenceStore, IdGen, PlanStore, PracticeEnv
from studykit.domain import course_eval, knowledge, mastery, plan as plans, progress, timeline
from studykit.domain.assessment import Checker, Lesson, Record, Verdict
from studykit.domain.errors import CourseError, KnowledgeError, LessonError
from studykit.domain.evidence import (SYSTEM, Actor, Evidence, VerbRegistry, checkpoint_id, learner_actor,
                                      question_id, section_id, split_question)
from studykit.domain.ids import NODE_ID_RE, UnitId

TUTOR = Actor("agent", "tutor", "tutor")          # 导师（Claude Code 会话）默认的身份
MAX_RESPONSE = 2000                               # 检查点作答最多存多少字


@dataclass
class Deps:
    """三个服务共用的端口，加上"写一条证据"的公共逻辑。"""
    evidence: EvidenceStore
    plans: PlanStore
    content: Content
    clock: Clock
    ids: IdGen
    verbs: VerbRegistry
    learner: str

    def ts(self) -> str:
        return self.clock.now().isoformat(timespec="seconds")

    def new(self, verb: str, object_type: str, object_id: str, actor: Actor | None = None, **fields) -> Evidence:
        e = Evidence(id=self.ids.new(), ts=self.ts(), learner=self.learner, actor=actor or learner_actor(self.learner),
                     verb=verb, object_type=object_type, object_id=object_id, **fields)
        self.verbs.validate(e)
        return e

    def all(self) -> list[Evidence]:
        return self.evidence.query(self.learner)


def _version(*parts) -> str:
    """内容版本 = 内容的哈希（D-021，G3）：改了题，旧作答对应的是旧版本。"""
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:10]


# ======================================================================
# 做题
# ======================================================================

class Assessment:
    def __init__(self, deps: Deps, checkers: dict[str, Checker], sandbox: Path, root: Path, short_grader=None):
        self.d, self.checkers, self.sandbox, self.root = deps, checkers, sandbox, root
        self.short_grader = short_grader       # 简答题的 LLM 批改（D-031）；None = 等导师批改

    def checker(self, kind: str) -> Checker:
        if kind not in self.checkers:
            raise LessonError(f"未知检验器：{kind}（可用：{', '.join(sorted(self.checkers))}）")
        return self.checkers[kind]

    def _ctx(self, lesson: Lesson, qid: str) -> CheckCtx:
        return CheckCtx(lesson.ref, self.d.content.lesson_dir(lesson.ref), self.sandbox / lesson.ref / qid, self.root)

    def list(self) -> list[dict]:
        return [{"ref": l.ref, "title": l.title, "count": len(l.questions())} for l in self.d.content.lessons()]

    def view(self, ref: str, retake: bool = False) -> dict:
        lesson = self.d.content.lesson(ref)
        latest = {} if retake else self._latest(ref)
        questions = []
        for q in lesson.questions():
            checker = self.checker(q["checker"])
            item = {k: q.get(k) for k in ("id", "checker", "level", "concept", "prompt", "kind")}
            item["level_name"] = mastery.LEVELS.get(q["level"], "")
            item.update(checker.view(q, self._ctx(lesson, q["id"])))
            if q["id"] in latest:
                item["previous"] = _previous_view(latest[q["id"]], lesson.answer_key(q["id"]))
            limit = lesson.max_runs(q) if q["checker"] == "code" else None
            if limit is not None:
                item["max_runs"], item["runs_left"] = limit, max(0, limit - self._runs_used(ref, q["id"]))
            questions.append(item)
        return {"ref": ref, "title": lesson.title, "source": lesson.quiz.get("source", ""), "questions": questions,
                "exam": lesson.exam}

    def _latest(self, ref: str) -> dict[str, dict]:
        """每道题最近一次作答；批改过的用批改的分数。"""
        latest: dict[str, dict] = {}
        for e in self.d.evidence.query(self.d.learner, verbs=("answered", "graded"), object_type="question"):
            lesson, qid = split_question(e.object_id)
            if lesson != ref:
                continue
            if e.verb == "answered":
                latest[qid] = {"id": e.id, "score": e.score, "result": mastery.result_of(e.score),
                               "feedback": e.payload.get("feedback") or [], "response": e.payload.get("response"),
                               "ts": e.ts, "note": "", "detail": e.payload.get("detail") or {}}
            elif qid in latest and latest[qid]["id"] == e.caused_by:
                latest[qid].update(score=e.score, result=mastery.result_of(e.score), note=e.payload.get("note", ""),
                                   ts=e.ts, graded_by=e.actor.id, items=e.payload.get("items"), feedback=[])
        return latest

    def act(self, ref: str, qid: str, action: dict) -> dict:
        lesson = self.d.content.lesson(ref)
        q = lesson.question(qid)
        limit = lesson.max_runs(q) if q["checker"] == "code" and action.get("op") == "run" else None
        if limit is not None and self._runs_used(ref, qid) >= limit:
            raise LessonError(f"这道题的 {limit} 次运行已经用完，交卷时按编辑器里现在的代码判分")
        result = self.checker(q["checker"]).act(q, lesson.answer_key(qid), self._ctx(lesson, qid), action)
        if result.records:
            self.d.evidence.append(*[self._record(lesson, q, r) for r in result.records])
        if limit is not None:
            result.data["runs_left"] = max(0, limit - self._runs_used(ref, qid))
        return result.data

    def _runs_used(self, ref: str, qid: str) -> int:
        """这一轮作答（上次交卷之后）已经运行了几次测试。"""
        return sum(e.verb == "ran_tests" and not e.payload.get("submit") for e in self._since_last_answer(ref, qid))

    def submit(self, ref: str, qid: str, response) -> dict:
        lesson = self.d.content.lesson(ref)
        if lesson.exam:
            raise LessonError("单元题要整卷一次交（D-031）：做完所有题后点页面底部的「交卷」")
        q = lesson.question(qid)
        key = lesson.answer_key(qid)
        verdict = self.checker(q["checker"]).check(q, key, self._ctx(lesson, qid), response)
        records, answer = self._answer(lesson, q, response, verdict)
        self.d.evidence.append(*records, answer)
        return self._answer_view(answer, verdict, key)

    def _answer(self, lesson: Lesson, q: dict, response, verdict: Verdict,
                exam: str | None = None) -> tuple[list[Evidence], Evidence]:
        qid, key = q["id"], lesson.answer_key(q["id"])
        records = [self._record(lesson, q, r) for r in verdict.records]
        # 这次提交之前、上次提交之后的每次运行，都算这次作答的过程（D-021，G1）。
        runs = [e.id for e in self._since_last_answer(lesson.ref, qid) if e.verb == "ran_tests"] + [r.id for r in records]
        answer = self.d.new(
            "answered", "question", question_id(lesson.ref, qid), object_version=_version(q, key), unit=lesson.unit,
            score=verdict.score, ok=None if verdict.score is None else verdict.score >= mastery.PASS_SCORE,
            pending=verdict.score is None, nodes=(q["concept"],),
            payload={"level": q["level"], "checker": q["checker"], "response": response,
                     "feedback": verdict.feedback, "detail": verdict.detail, "runs": runs,
                     **({"exam": exam} if exam else {})})
        return records, answer

    @staticmethod
    def _answer_view(answer: Evidence, verdict: Verdict, key: dict, grade: Evidence | None = None) -> dict:
        view = {"id": answer.id, "score": answer.score, "result": mastery.result_of(answer.score),
                "feedback": verdict.feedback, "response": answer.payload["response"], "ts": answer.ts, "note": "",
                "detail": verdict.detail}
        if grade is not None:
            view.update(score=grade.score, result=mastery.result_of(grade.score), note=grade.payload.get("note", ""),
                        graded_by=grade.actor.id, items=grade.payload.get("items"), feedback=[])
        return _previous_view(view, key)

    def submit_exam(self, ref: str, responses: dict) -> dict:
        """整卷一次交（D-031）：逐题判分，简答题并行交给 LLM 批改，最后记一条"交卷"把这些作答串起来。"""
        lesson = self.d.content.lesson(ref)
        if not lesson.exam:
            raise LessonError(f"{ref} 不是整卷模式，一题一题提交")
        exam = self.d.ids.new()
        evs, answers, verdicts = [], {}, {}
        for q in lesson.questions():
            qid, key, response = q["id"], lesson.answer_key(q["id"]), (responses or {}).get(q["id"])
            # 代码题、终端题判的是文件 / 沙箱的状态，没有作答也照样判；其他题没作答就是 0 分。
            if response is None and q["checker"] not in ("code", "terminal"):
                verdict = Verdict(0.0, ["没有作答。"])
            else:
                verdict = self.checker(q["checker"]).check(q, key, self._ctx(lesson, qid), response)
            records, answer = self._answer(lesson, q, response, verdict, exam)
            evs += [*records, answer]
            answers[qid], verdicts[qid] = answer, verdict
        grades = self._llm_grade(lesson, {qid: a for qid, a in answers.items() if a.pending})
        scores = [grades[qid].score if qid in grades else a.score for qid, a in answers.items()]
        submitted = self.d.new("submitted_exam", "lesson", ref, unit=lesson.unit,
                               score=None if None in scores else round(sum(scores) / len(scores), 3),
                               payload={"answers": {qid: a.id for qid, a in answers.items()}})
        self.d.evidence.append(*evs, *grades.values(), submitted)
        return {"exam": exam, "score": submitted.score,
                "questions": {qid: self._answer_view(a, verdicts[qid], lesson.answer_key(qid), grades.get(qid))
                              for qid, a in answers.items()}}

    def _llm_grade(self, lesson: Lesson, pending: dict[str, Evidence]) -> dict[str, Evidence]:
        """简答题并行调用模型批改。失败的题保持待批改（导师再用 grade 批），不影响其他题。"""
        if not self.short_grader or not pending:
            return {}

        def one(item: tuple[str, Evidence]) -> tuple[str, dict]:
            qid, a = item
            if not str(a.payload.get("response") or "").strip():
                return qid, {"score": 0.0, "items": [], "feedback": "没有作答。", "model": "", "version": "",
                             "workspace": ""}
            try:
                return qid, self.short_grader.grade(a.object_id, lesson.question(qid), lesson.answer_key(qid),
                                                    a.payload["response"])
            except Exception as e:  # noqa: BLE001 —— 模型不可用、回复格式不对：留给导师批
                return qid, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as pool:
            out = dict(pool.map(one, pending.items()))
        grades = {}
        for qid, r in out.items():
            if "error" in r:
                continue
            a = pending[qid]
            grades[qid] = self.d.new(
                "graded", "question", a.object_id, Actor("agent", "short-grader", "grader", r["version"]),
                object_version=a.object_version, unit=a.unit, caused_by=a.id, score=r["score"],
                payload={"note": r["feedback"], "items": r["items"], "model": r["model"], "workspace": r["workspace"]})
        return grades

    def _since_last_answer(self, ref: str, qid: str) -> list[Evidence]:
        evs = self.d.evidence.query(self.d.learner, object_type="question", object_id=question_id(ref, qid))
        last = max((i for i, e in enumerate(evs) if e.verb == "answered"), default=-1)
        return evs[last + 1:]

    def _record(self, lesson: Lesson, q: dict, r: Record) -> Evidence:
        return self.d.new(r.verb, "question", question_id(lesson.ref, q["id"]),
                          object_version=_version(q, lesson.answer_key(q["id"])), unit=lesson.unit, score=r.score,
                          nodes=(q["concept"],), payload={"level": q["level"], **r.payload})

    def pending(self) -> list[Evidence]:
        return mastery.scored(self.d.all())[1]

    def grade(self, ref: str, qid: str, score: float, note: str = "", actor: Actor = TUTOR) -> Evidence:
        """批改简答题。已经被 LLM 批过的，导师再批就覆盖它的分数（D-031：两条都留着，掌握度只算最后一次）。"""
        oid = question_id(ref, qid)
        evs = self.d.evidence.query(self.d.learner, verbs=("answered", "graded"), object_type="question", object_id=oid)
        mine = {e.caused_by for e in evs if e.verb == "graded" and e.actor.id == actor.id}
        target = [a for a in evs if a.verb == "answered" and a.pending and a.id not in mine]
        if not target:
            raise LessonError(f"{ref} {qid} 没有待批改的记录")
        a = target[-1]
        e = self.d.new("graded", "question", a.object_id, actor, object_version=a.object_version, unit=a.unit,
                       caused_by=a.id, score=score, payload={"note": note})
        self.d.evidence.append(e)
        return e

    def log_practice(self, concept: str, level: int, score: float, note: str = "") -> Evidence:
        """课外练习（LeetCode、靶场……）。"""
        e = self.d.new("logged_practice", "external", note or "-", score=score, nodes=(concept,),
                       payload={"level": level, "note": note})
        self.d.evidence.append(e)
        return e

    def runs(self, ref: str, qid: str | None = None) -> list[dict]:
        lesson = self.d.content.lesson(ref)
        if qid:
            lesson.question(qid)
        out = []
        for e in self.d.evidence.query(self.d.learner, verbs=("ran_tests",), object_type="question"):
            lref, q = split_question(e.object_id)
            if lref == ref and (qid is None or q == qid):
                out.append({"id": e.id, "ts": e.ts, "qid": q, "kind": "submit" if e.payload.get("submit") else "run",
                            **{k: e.payload.get(k) for k in ("passed", "total", "failures", "code")}})
        return out


def _previous_view(rec: dict, key: dict) -> dict:
    out = {k: rec.get(k) for k in ("score", "result", "feedback", "response", "ts", "note", "graded_by", "items")}
    tests = (rec.get("detail") or {}).get("tests")
    if tests is not None:
        out["tests"] = tests
    # WHY: 解析只在作答之后给；简答题要等批改完才给，否则等于提前公布评分点。
    if rec.get("result") != "pending":
        out["explain"] = key.get("explain", "")
        if key.get("rubric"):                    # 批改后给评分点原文，对照逐条批改看（F-051）
            out["rubric"] = [str(r) for r in key["rubric"]]
    return out


# ======================================================================
# 学习者模型
# ======================================================================

class LearnerModel:
    def __init__(self, deps: Deps):
        self.d = deps

    def stats(self, evidence: list[Evidence] | None = None) -> dict[str, dict]:
        return mastery.concept_stats(self.d.all() if evidence is None else evidence, self.d.clock.now().date())

    def states(self, nodes: dict[str, knowledge.Node] | None = None) -> dict[str, knowledge.NodeState]:
        evs = self.d.all()
        nodes = self.d.content.graph() if nodes is None else nodes
        return knowledge.derive_states(nodes, self.stats(evs), knowledge.signals(evs))

    def known(self, topic: str | None = None) -> set[str]:
        return knowledge.known_titles(self.states(), topic)

    def observe(self, nid: str, polarity: str, note: str, actor: Actor = TUTOR) -> Evidence:
        """把"讲 glob 那段完全跟不上"这样的话记到具体节点上（D-020）。"""
        if polarity not in ("weak", "ok"):
            raise KnowledgeError("polarity 只能是 weak 或 ok")
        if not NODE_ID_RE.match(nid or ""):
            raise KnowledgeError(f"节点 id 格式不对：{nid}")
        e = self.d.new("observed", "node", nid, actor, nodes=(nid,), payload={"polarity": polarity, "note": note})
        self.d.evidence.append(e)
        return e

    def check_graph(self) -> list[str]:
        nodes = self.d.content.graph()
        errors = knowledge.validate(nodes)
        orphans = knowledge.orphans(nodes, self.states(nodes))
        return errors + [f"证据里有、知识图里没有的节点：{o}（拼错了，还是忘了加进知识图？）" for o in orphans]

    # ---------- 分级查询（给 agent 和导师，D-020） ----------

    def summary(self, max_weak: int = 5) -> str:
        """第 0 级：每个学科的数量和前几个薄弱点标题。长度不随节点数增长。"""
        by: dict[str, list[knowledge.NodeState]] = defaultdict(list)
        for s in self.states().values():
            by[s.node.topic].append(s)
        if not by:
            return "知识图还是空的。"
        lines = ["| 学科 | 掌握 | 在学 | 薄弱 | 需复习 | 没学 | 薄弱点（前几个） |", "|---|---|---|---|---|---|---|"]
        for topic, ss in sorted(by.items()):
            c = defaultdict(int)
            for s in ss:
                c[s.state] += 1
            weak = sorted((s for s in ss if s.state in ("weak", "stale")),
                          key=lambda s: s.signals[-1].ts if s.signals else "", reverse=True)
            names = "、".join(f"{s.node.title}（`{s.node.id}`）" for s in weak[:max_weak]) + ("…" if len(weak) > max_weak else "")
            lines.append(f"| {topic} | {c['mastered']} | {c['learning']} | {c['weak']} | {c['stale']} | {c['new']} | {names or '—'} |")
        return "\n".join(lines)

    def related(self, unit: str, depth: int = 2) -> dict:
        """第 1 级：这个单元教的节点 + 往上 depth 层的先修里，没掌握的节点。"""
        nodes = self.d.content.graph()
        sts = self.states(nodes)
        taught = [n.id for n in nodes.values() if unit in n.units]
        dist = knowledge.ancestors(nodes, taught, depth)
        prereq = [sts[nid].brief() | {"distance": d} for nid, d in sorted(dist.items(), key=lambda kv: kv[1])
                  if d > 0 and sts[nid].state != "mastered"]
        return {"unit": unit,
                "taught": [sts[nid].brief() for nid in taught],
                "prerequisites_not_mastered": prereq,
                "weak": [sts[nid].brief() | {"why": sts[nid].reasons} for nid in dist if sts[nid].state in ("weak", "stale")]}

    def show(self, nid: str) -> dict:
        """第 2 级：一个节点的全部信息。"""
        nodes = self.d.content.graph()
        sts = self.states(nodes)
        if nid not in sts:
            raise KnowledgeError(f"没有节点 {nid}")
        s = sts[nid]
        return {"id": nid, "title": s.node.title, "desc": s.node.desc, "kind": s.node.kind, "state": s.state,
                "reasons": s.reasons, "mastery": s.mastery, "units": s.node.units, "aliases": s.node.aliases,
                "requires": [sts[r].brief() if r in sts else {"id": r, "state": "?"} for r in s.node.requires],
                "required_by": [sts[m].brief() for m, n in nodes.items() if nid in n.requires],
                "evidence": [{"ts": x.ts[:16], "sign": x.sign, "source": x.source, "note": x.note} for x in s.signals]}

    def tree(self, topic: str | None = None) -> dict:
        """知识树视图：节点（带状态和层数）+ 先修边。"""
        nodes = self.d.content.graph()
        sts = self.states(nodes)
        ds = knowledge.depths(nodes)
        keep = {nid for nid, n in nodes.items() if topic is None or n.topic == topic}
        return {"topics": sorted({n.topic for n in nodes.values()}),
                "nodes": [{"id": nid, "title": nodes[nid].title, "desc": nodes[nid].desc, "kind": nodes[nid].kind,
                           "state": sts[nid].state, "reasons": sts[nid].reasons, "depth": ds[nid], "units": nodes[nid].units}
                          for nid in sorted(keep, key=lambda x: (ds[x], x))],
                "edges": [[r, nid] for nid in keep for r in nodes[nid].requires if r in keep]}

    # ---------- 给导师的学习状态 ----------

    def weak(self) -> dict:
        evs = self.d.all()
        stats = self.stats(evs)
        nodes = self.d.content.graph()
        # YAML 会把不加引号的 done_on: 2026-09-26 解析成 date，这里统一转成字符串
        studied = [{"topic": tid, **{k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in u.items()}}
                   for tid, t in (self.d.content.syllabus().get("topics") or {}).items()
                   for u in t.get("units") or [] if u.get("status") in ("done", "watching")]
        studied_ids = {u["id"] for u in studied}
        _, pending = mastery.scored(evs)
        return {
            "today": self.d.clock.now().date().isoformat(),
            "studied_units": studied,
            "concepts": sorted(stats.values(), key=lambda s: (not s["weak"], s["mastery"])),
            "untested_concepts": sorted(n.id for n in nodes.values() if set(n.units) & studied_ids and n.id not in stats),
            "pending_grading": [{"lesson": split_question(a.object_id)[0], "qid": split_question(a.object_id)[1],
                                 "concept": a.nodes[0], "level": a.payload.get("level"),
                                 "response": a.payload.get("response"), "ts": a.ts} for a in pending],
            # 代码题的练习过程：跑了很多次才通过的题，即使最终满分也说明还不熟。详情用 study.py runs 看。
            "code_practice": mastery.practice_summary(evs)[:20],
        }

    # ---------- 学习时长（D-016） ----------

    def sessions(self) -> list[timeline.Session]:
        return timeline.sessions(*timeline.ticks(self.d.all()))

    def journal(self) -> dict:
        """学习记录页（D-027）：新词 id 换成标题，最近的一天在前。"""
        nodes = self.d.content.graph()
        days = timeline.journal(self.d.all())
        for day in days:
            for s in day["sessions"]:
                s["terms"] = [getattr(nodes.get(n), "title", n) for n in s["terms"]]
        return {"days": days[::-1], "total_minutes": round(sum(d["minutes"] for d in days), 1)}

    def time_report(self, days: int | None = None) -> str:
        since = (self.d.clock.now().date() - dt.timedelta(days=days - 1)).isoformat() if days else None
        return timeline.report(self.sessions(), since)

    def log_time(self, start: str, minutes: float, note: str = "") -> Evidence:
        e = self.d.new("logged_time", "time", start, payload={"start": start, "minutes": minutes, "note": note or "手动补录"})
        self.d.evidence.append(e)
        return e

    # ---------- 导出（数据库不进 git；导出的 JSONL 进 git，可读、可 diff，D-021） ----------

    def export(self) -> list[dict]:
        return [dataclasses.asdict(e) for e in self.d.all()]

    # ---------- progress.md ----------

    def progress_md(self) -> str:
        evs = self.d.all()
        stats = self.stats(evs)
        items, pending = mastery.scored(evs)
        titles = {nid: n.title for nid, n in self.d.content.graph().items()}
        icon = {"done": "✅", "watching": "🟡"}
        lines = ["# 学习进度", "",
                 "> 由 `python study.py status` 生成，不要手改。课程进度改 `progress/syllabus.yaml`；答题记录在数据库里。",
                 f"> 更新于 {self.d.clock.now().isoformat(sep=' ')}", "", "## 课程进度", "",
                 "| 学科 | 完成 | 单元 |", "|---|---|---|"]
        for tid, t in (self.d.content.syllabus().get("topics") or {}).items():
            units = t.get("units") or []
            cells = " · ".join(f"{icon.get(u.get('status'), '⬜')} {u['title']}" for u in units)
            lines.append(f"| `{tid}` {t.get('title', '')} | {sum(u.get('status') == 'done' for u in units)}/{len(units)} | {cells} |")
        lines += ["", "## 概念掌握度", "",
                  "等级：1 记忆 → 2 理解 → 3 应用 → 4 分析。某一级最近 3 次平均分 ≥ 70% 算通过。", ""]
        if stats:
            lines += ["| 概念 | 掌握 | 练习次数 | 最近 | 提示 |", "|---|---|---|---|---|"]
            for s in sorted(stats.values(), key=lambda s: (s["topic"], s["concept"])):
                tips = (["第 " + "、".join(map(str, s["failing_levels"])) + " 级有错"] if s["failing_levels"] else []) \
                    + (["需复习"] if s["stale"] else [])
                bar = "█" * s["mastery"] + "░" * (4 - s["mastery"])
                lines.append(f"| `{s['concept']}` {titles.get(s['concept'], '')} | {bar} {s['mastery']} "
                             f"{mastery.LEVELS.get(s['mastery'], '')} | {s['attempts']} | {s['last']} | {'；'.join(tips)} |")
        else:
            lines.append("还没有练习记录。")
        lines += ["", "## 最近测验", ""]
        by_lesson: dict[str, list[mastery.Scored]] = defaultdict(list)
        for s in items:
            if s.ref != "external":
                by_lesson[split_question(s.ref)[0]].append(s)
        if by_lesson:
            lines += ["| 测验 | 最近一次 | 已判分题数 | 平均分 |", "|---|---|---|---|"]
            for ref, recs in sorted(by_lesson.items(), key=lambda kv: max(r.ts for r in kv[1]), reverse=True)[:10]:
                lines.append(f"| {ref} | {max(r.ts for r in recs)[:10]} | {len(recs)} | {sum(r.score for r in recs) / len(recs):.0%} |")
        else:
            lines.append("还没有。")
        lines += ["", "## 待批改", ""]
        lines += [f"- {a.object_id.replace('#', ' ')}（{a.ts[:10]}）" for a in pending] or ["无。"]
        return "\n".join(lines) + "\n"


# ======================================================================
# 课程页
# ======================================================================

class Course:
    def __init__(self, deps: Deps, learner_model: LearnerModel, checkpoints: dict[str, CheckpointGrader],
                 practice: PracticeEnv | None):
        self.d, self.lm, self.checkpoints, self.practice = deps, learner_model, checkpoints, practice

    def plan(self, unit: str) -> dict:
        UnitId(unit)
        plan = self.d.plans.current(unit)
        if plan is None:
            raise CourseError(f"单元 {unit} 还没有课程页。对导师说「准备学 {unit}」生成一份。")
        return plan

    def settings(self) -> dict:
        s = self.d.content.settings()
        return {"unit_budget_minutes": int(s.get("unit_budget_minutes") or 180),
                "session_minutes": int(s.get("session_minutes") or 45),
                "max_new_terms": int(s.get("max_new_terms") or 5)}

    def publish(self, plan: dict, actor: Actor = TUTOR) -> list[str]:
        """发布一版课程计划；agent 提议的知识节点并入知识图（已有的不覆盖）。返回新增的节点。"""
        unit = str(UnitId(plan["unit"]))          # 校验格式，但往外传普通字符串（YAML 不认 str 的子类）
        self.d.plans.publish(unit, plan, self.d.ts(), actor)
        return self.d.content.add_nodes([{**n, "units": n.get("units") or [unit]} for n in plan.get("nodes") or []])

    def _evidence(self, unit: str, pid: str) -> list[Evidence]:
        return self.d.evidence.query(self.d.learner, unit=unit, plan=pid)

    def _spent(self, unit: str, pid: str) -> dict[int, float]:
        return timeline.section_minutes(timeline.ticks(self.d.all())[0], unit, pid)

    def progress(self, unit: str) -> dict:
        plan = self.plan(unit)
        pid = plans.plan_id(plan)
        return progress.project(plan, pid, self._evidence(unit, pid), self._spent(unit, pid))

    def list(self) -> list[dict]:
        out = []
        for unit in self.d.plans.units():
            plan = self.plan(unit)
            out.append({"unit": unit, "title": plan.get("title", unit), "minutes": plans.plan_minutes(plan),
                        "sections": len(plans.sections_of(plan)), "done": len(self.progress(unit)["passed"])})
        return out

    def page(self, unit: str) -> dict:
        plan = self.plan(unit)
        cfg = self.settings()
        quiz = next((l.ref for l in self.d.content.lessons() if l.unit == unit), None)
        return {
            "unit": unit, "plan": plans.public_view(plan), "settings": cfg,
            "planned_minutes": plans.plan_minutes(plan),
            "sessions": plans.plan_sessions(plan, cfg["session_minutes"]),
            "section_count": len(plans.sections_of(plan)),
            "load": progress.predicted_load(plan, self.lm.known(UnitId(unit).topic)),
            "sources": [{"title": x.get("title", x["url"]), "url": x["url"]} for x in plan.get("sources") or []],
            "progress": self.progress(unit),
            "quiz": quiz,
        }

    def record(self, unit: str, section, event: str, minutes: float | None = None, **extra) -> dict:
        """页面发来的事件。检查点、提示、终端命令由服务器自己记，不走这里。"""
        plan = self.plan(unit)
        pid = plans.plan_id(plan)
        if event == "resume":                   # 页面报"离开了几分钟"，起点用服务器的时钟算（D-027）
            away = extra.pop("away_minutes", None)
            if isinstance(away, bool) or not isinstance(away, (int, float)) or not 0 <= away <= 24 * 60:
                raise CourseError("away_minutes 要是 0–1440 之间的分钟数")
            extra["away_start"] = (self.d.clock.now() - dt.timedelta(minutes=away)).isoformat(timespec="seconds")
        ce = plans.client_event(plan, unit, section, event, minutes, **extra)
        self.d.evidence.append(self.d.new(ce.verb, ce.object_type, ce.object_id, unit=unit, plan=pid,
                                          section=ce.section, nodes=ce.nodes, payload=ce.payload))
        if ce.verb == "opened" and plan.get("lab") and self.practice:
            self.practice.snapshot_once(unit, pid, section, plan["lab"].get("files") or [])
        return self.progress(unit)

    # ---------- 检查点（D-013） ----------

    def check(self, unit: str, section: int, idx: int, response) -> dict:
        plan = self.plan(unit)
        pid = plans.plan_id(plan)
        item = plans.checkpoint_item(plan, section, idx)
        grader = self.checkpoints.get(item.get("type"))
        if grader is None:
            raise CourseError(f"不认识的检查点题型：{item.get('type')}")
        r = grader(unit, item, response)
        ok = r.score >= 0.999
        evs = self._evidence(unit, pid)
        before = progress.project(plan, pid, evs, {})
        o = progress.checkpoint_outcome(plan, before, section, idx, ok)
        clipped = json.dumps(response, ensure_ascii=False)
        answer = self.d.new("answered", "checkpoint", checkpoint_id(unit, section, idx), object_version=pid,
                            unit=unit, plan=pid, section=section, caused_by=progress.last_hint(evs, section, idx),
                            score=round(r.score, 3), ok=ok, nodes=tuple(n for n in [item.get("concept")] if n),
                            payload={"idx": idx, "type": item.get("type"),
                                     "response": response if len(clipped) <= MAX_RESPONSE else clipped[:MAX_RESPONSE]})
        new = [answer]
        if o["first_pass"]:
            new.append(self.d.new("passed_section", "section", section_id(unit, section), SYSTEM, unit=unit, plan=pid,
                                  section=section, caused_by=answer.id, nodes=tuple(o["terms"])))
        self.d.evidence.append(*new)
        out = {"ok": ok, "score": r.score, "feedback": r.feedback, "attempt": o["attempt"],
               "trap": r.trap and {k: r.trap.get(k) for k in ("symptom", "cause", "fix")},
               "section_passed": o["section_passed"], "checks": r.checks}
        if ok:
            out["explain"] = item.get("explain", "")
            out.update(r.reveal)
        out["progress"] = self.progress(unit)
        return out

    def hint(self, unit: str, section: int, idx: int) -> dict:
        """三级提示：方向 → 关键概念 → 接近答案。每要一次记一条证据（D-019 用它算检查点难度）。"""
        plan = self.plan(unit)
        pid = plans.plan_id(plan)
        hints = plans.checkpoint_item(plan, section, idx).get("hints") or []
        if not hints:
            raise CourseError("这道题没有提示")
        used = progress.hints_used(self._evidence(unit, pid), section, idx)
        level = min(used + 1, len(hints))
        if used < len(hints):
            self.d.evidence.append(self.d.new("requested_hint", "checkpoint", checkpoint_id(unit, section, idx),
                                              unit=unit, plan=pid, section=section, payload={"idx": idx, "level": level}))
        return {"level": level, "total": len(hints), "hints": hints[:level]}

    # ---------- 练习环境（D-014，由学科 spec 提供） ----------

    def lab(self, unit: str, op: str, section: int | None = None, cmd: str = "") -> dict:
        plan = self.plan(unit)
        spec = plan.get("lab")
        if not spec or self.practice is None:
            raise CourseError("这个单元没有练习场")
        files, pid = spec.get("files") or [], plans.plan_id(plan)

        def log(op: str, **payload) -> None:
            self.d.evidence.append(self.d.new("practiced", "lab", unit, unit=unit, plan=pid,
                                              section=section if isinstance(section, int) else None,
                                              payload={"op": op, **payload}))

        if op == "open":
            if isinstance(section, int):
                plans.section(plan, section)
                self.practice.snapshot_once(unit, pid, section, files)
            return self.practice.open(unit, files)
        if op == "run":
            cmd = str(cmd or "")
            if not cmd.strip():
                return {"output": "", "rc": 0}
            if len(cmd) > 4000:
                raise CourseError("命令太长")
            res = self.practice.run(unit, files, cmd)
            log("run", cmd=cmd, rc=res["rc"], outside=res["outside"])
            return res
        if op == "restore":
            plans.section(plan, section)
            self.practice.restore(unit, pid, section)
            log("restore")
            return {"output": f"练习场已还原到第 {section + 1} 节开始时的样子。", "cwd": self.practice.home(unit)}
        if op == "reset":
            self.practice.reset(unit, files)
            log("reset")
            if isinstance(section, int):
                self.practice.snapshot_once(unit, pid, section, files)
            return {"output": "练习场已重置为单元开始时的样子（所有改动都清掉了）。", "cwd": self.practice.home(unit)}
        if op == "fill":
            # 跳过一节时，用参考做法把练习场补到"这一节做完"的样子，下一节才能接着做。
            outs = []
            for item in plans.section(plan, section).get("checkpoint") or []:
                for c in item.get("solution") or []:
                    rc, out = self.practice.run_once(unit, c)
                    outs.append(f"$ {c}" + (f"\n{out.rstrip()}" if out.strip() else ""))
            log("fill")
            return {"output": "\n".join(outs) or "这一节没有要补的练习场操作。", "cwd": self.practice.home(unit)}
        raise CourseError(f"不认识的练习场操作：{op}")

    # ---------- 课程评测（D-019） ----------

    def evaluate(self, unit: str, pid: str | None = None) -> dict:
        plan = self.plan(unit) if pid is None else self.d.plans.version(unit, pid)
        if plan is None:
            raise CourseError(f"找不到 {unit} 的版本 {pid}")
        pid = plans.plan_id(plan)
        return course_eval.evaluate(unit, plan, self._evidence(unit, pid), self._spent(unit, pid))

    @staticmethod
    def format_evaluation(result: dict) -> str:
        return course_eval.format_report(result)
