"""流程看板（D-024）：数据库里每一条新记录，从哪来、记到哪、被谁用。

看板上的节点分四类（从左到右）：谁 → 从哪进来 → 记到哪 → 被哪个计算用。
每条记录变成一个"流动"：path 是它经过的节点，feeds 是它被哪些计算读到（读哪些字段）。

"被谁用"不是在这里手写的：证据的来自 Verb.feeds（tests/test_lineage.py 检查它和代码一致），
学习时长来自 timeline.counts_as_study。这里只负责把它们翻译成看板上的路径。
"""
from __future__ import annotations

from studykit.app.ports import ActivityFeed
from studykit.domain import timeline
from studykit.domain.evidence import Evidence, VerbRegistry, split_question
from studykit.domain.harness import Grade, Run

NODES = {
    # 谁
    "learner": {"label": "学习者", "sub": "浏览器", "kind": "actor", "desc": "你：在课程页和练习题里的每一次点击、作答。"},
    "tutor": {"label": "导师", "sub": "Claude Code", "kind": "actor", "desc": "在命令行里批改、观察、跑 agent、审阅、发布。"},
    "agent": {"label": "备课 agent", "sub": "pi + DeepSeek", "kind": "actor", "desc": "按简报查资料、编课程计划。只能用环境给的两个工具。",
              "code": "adapters/pi.py"},
    "judge": {"label": "评分模型", "sub": "更强的模型", "kind": "actor", "desc": "按 rubric 给课程计划打 1-5 分，列出怀疑没出处的说法。",
              "code": "app/harness.py · _judge"},
    "system": {"label": "系统规则", "sub": "确定性代码", "kind": "actor", "desc": "不做判断，只执行规则（如：整节做对 → 这节的新词算会用了）。"},
    # 从哪进来
    "course_page": {"label": "课程页", "sub": "/?unit=", "kind": "surface", "desc": "一节一节学：讲解、新词、检查点、提示、练习场。",
                    "code": "app/learning.py · Course"},
    "quiz": {"label": "练习题", "sub": "/?lesson=", "kind": "surface", "desc": "一套题：选择、填空、代码、终端、简答。",
             "code": "app/learning.py · Assessment"},
    "cli": {"label": "命令行", "sub": "study.py", "kind": "surface", "desc": "批改、观察、补录时间、跑 agent、发布。", "code": "cli.py"},
    "tools": {"label": "agent 工具", "sub": "fetch_url · submit_plan", "kind": "surface",
              "desc": "agent 能做的全部事情：读白名单网页、提交课程计划（不合格就退回）。", "code": "app/harness.py · call_tool"},
    # 记到哪
    "evidence": {"label": "证据", "sub": "evidence 表 · 只追加", "kind": "store",
                 "desc": "学习者身上发生过的每一件事，同一个形状：谁 · 做了什么 · 对什么 · 结果 · 影响哪些知识点。", "code": "adapters/sqlite.py"},
    "runs": {"label": "运行记录", "sub": "run · run_step", "kind": "store",
             "desc": "agent 每次运行：冻结的输入、每一步、产出、用量、版本（Variant）。", "code": "adapters/sqlite.py"},
    "grades": {"label": "评分", "sub": "grade 表 · 只追加", "kind": "store",
               "desc": "对一次运行的评分：自动检查、评分模型、核对出处、练习场验证、导师审阅、学习结果。", "code": "domain/harness.py"},
    "plans": {"label": "课程版本", "sub": "plan_version · 不可变", "kind": "store",
              "desc": "发布过的每一版课程计划。证据记着它属于哪一版。", "code": "adapters/sqlite.py"},
    # 被哪个计算用
    "mastery": {"label": "掌握度", "sub": "0-4 级", "kind": "projection",
                "desc": "每个概念每一级最近 3 次平均分 ≥ 70% 算通过；14 天没练算需复习。", "code": "domain/mastery.py"},
    "node_state": {"label": "节点状态", "sub": "薄弱点 · 已知词", "kind": "projection",
                   "desc": "每个知识点：没学 / 在学 / 薄弱 / 掌握 / 需复习，以及理由。", "code": "domain/knowledge.py"},
    "timeline": {"label": "学习时长", "sub": "会话", "kind": "projection",
                 "desc": "相邻两条学习者证据间隔 ≤ 15 分钟算同一次学习；课程页暂停后回来，学习者说离开的那段算不算（D-027）。", "code": "domain/timeline.py"},
    "progress": {"label": "单元进度", "sub": "每节通过没有", "kind": "projection",
                 "desc": "这一版课程里每节的检查点、提示、跳过、自评。", "code": "domain/progress.py"},
    "course_eval": {"label": "课程评测", "sub": "预测 vs 实际", "kind": "projection",
                    "desc": "计划分钟 vs 实际、预测负荷 vs 自评、新词预测准不准、检查点难度。", "code": "domain/course_eval.py"},
    "profile": {"label": "老师的观察", "sub": "还有效的观察", "kind": "projection",
                "desc": "老师对学习者的观察（讲法、容易过载的地方），没被学习者推翻的那些；每条带出处（D-047）。",
                "code": "domain/profile.py · observations"},
    "context": {"label": "备课上下文", "sub": "下一次的简报", "kind": "projection",
                "desc": "给 agent 的：已掌握的词、相关薄弱点、没掌握的先修、已有知识节点、时间预算。",
                "code": "app/harness.py · build_input"},
    "gate": {"label": "发布闸门", "sub": "检查 + 练习场验证", "kind": "projection",
             "desc": "自动检查全过、有练习场的已经验证通过，才能发布。", "code": "app/harness.py · publish"},
    "report": {"label": "版本对比", "sub": "agent report", "kind": "projection",
               "desc": "按 Variant 汇总评分，列出相邻两版差在哪一层（prompt / 上下文 / 工具 / 模型）。", "code": "app/harness.py · report"},
}

# 计算之间的依赖（静态）：谁的结果被谁用
LINKS = [
    ("mastery", "node_state", "掌握等级"), ("node_state", "context", "已掌握的词 · 相关薄弱点"),
    ("profile", "context", "老师的观察"),
    ("timeline", "progress", "每节用时"), ("timeline", "course_eval", "实际分钟"), ("progress", "course_eval", "通过没有"),
    ("course_eval", "grades", "学习结果评分"), ("context", "agent", "简报"), ("grades", "gate", "检查通过了吗"),
    ("grades", "report", "按版本汇总"), ("gate", "plans", "发布"), ("plans", "course_page", "当前版本"),
]

LANES = {"prep": "① 生成课程", "learn": "② 学习与答题", "model": "③ 评估与反馈"}


def _short(v, n: int = 160):
    if isinstance(v, str):
        return v if len(v) <= n else v[:n] + "…"
    if isinstance(v, (list, tuple)):
        return [_short(x, 60) for x in v[:8]] + (["…"] if len(v) > 8 else [])
    if isinstance(v, dict):
        return {k: _short(x, 80) for k, x in list(v.items())[:12]}
    return v


class Observer:
    def __init__(self, feed: ActivityFeed, verbs: VerbRegistry, learner: str):
        self.feed, self.verbs, self.learner = feed, verbs, learner

    def graph(self) -> dict:
        return {"nodes": NODES, "links": [{"from": a, "to": b, "label": l} for a, b, l in LINKS], "lanes": LANES,
                "verbs": [{"name": v.name, "title": v.title, "spec": v.spec,
                           "feeds": [{"node": f.projection, "fields": f.fields, "object": f.object_type} for f in v.feeds]}
                          for v in self.verbs.all()]}

    def since(self, cursor: dict | None = None, limit: int = 60) -> dict:
        rows, cursor = self.feed.activity(cursor, limit)
        events = [self.describe(kind, obj) for kind, obj in rows]
        return {"events": [e for e in events if e], "cursor": cursor}

    def describe(self, kind: str, obj) -> dict | None:
        if kind == "evidence":
            return self._evidence(obj)
        if kind == "run":
            return self._run(obj)
        if kind == "grade":
            return self._grade(obj)
        if kind == "publication":
            return {"id": f"pub-{obj['seq']}", "kind": kind, "ts": obj["ts"], "lane": "prep",
                    "title": f"发布课程 · {obj['unit']}", "subtitle": f"版本 {obj['plan_id']}",
                    "actor": f"{obj['actor_type']}:{obj['actor_id']}", "path": ["tutor", "cli", "gate", "plans", "course_page"],
                    "feeds": [], "recorded": {"unit": obj["unit"], "plan": obj["plan_id"]}}
        return None

    def _evidence(self, e: Evidence) -> dict:
        v = self.verbs.get(e.verb)
        if e.actor.type == "system":
            path = ["system", "evidence"]
        else:
            who = "learner" if e.actor.type == "learner" else ("tutor" if e.actor.role == "tutor" else "agent")
            surface = ("quiz" if e.object_type == "question" else
                       "cli" if e.object_type in ("external", "time") or e.actor.type == "agent" else "course_page")
            path = [who, surface, "evidence"]
        feeds = [{"node": f.projection, "fields": f.fields} for f in (v.feeds if v else ())
                 if not f.object_type or f.object_type == e.object_type]
        if timeline.counts_as_study(e) or e.verb == "logged_time":
            feeds.append({"node": "timeline", "fields": "时间点" if e.verb != "logged_time" else "补录的时间段"})
        target = split_question(e.object_id)[0] + " " + split_question(e.object_id)[1] if e.object_type == "question" else e.object_id
        bits = [f"得分 {e.score:g}" if e.score is not None else ("等批改" if e.pending else ""),
                "✓" if e.ok else ("✗" if e.ok is False else ""), " · ".join(e.nodes)]
        recorded = {"verb": e.verb, "actor": f"{e.actor.type}:{e.actor.id}", "object": f"{e.object_type}:{e.object_id}",
                    "unit": e.unit, "plan": e.plan, "section": e.section, "score": e.score, "ok": e.ok,
                    "pending": e.pending or None, "nodes": list(e.nodes) or None, "caused_by": e.caused_by,
                    **{f"payload.{k}": _short(x) for k, x in e.payload.items()}}
        return {"id": e.id, "kind": "evidence", "ts": e.ts, "lane": "learn" if e.actor.type == "learner" else "model",
                "verb": e.verb, "title": f"{(v.title if v else e.verb)} · {target}",
                "subtitle": " · ".join(b for b in bits if b), "actor": f"{e.actor.type}:{e.actor.id}",
                "path": path, "feeds": feeds, "recorded": {k: x for k, x in recorded.items() if x not in (None, "", [])}}

    def _run(self, r: Run) -> dict:
        calls = " · ".join(f"{k}×{c['calls']}" for k, c in r.tool_calls.items())
        return {"id": r.id, "kind": "run", "ts": r.started, "lane": "prep", "title": f"agent 运行 · {r.agent} · {r.unit}",
                "subtitle": f"{'已提交' if r.submitted else '没提交'} · {r.tokens} token · {calls}", "actor": f"agent:{r.agent}",
                "path": ["tutor", "context", "agent", "tools", "runs"], "feeds": [{"node": "report", "fields": "版本 · 用量"}],
                "recorded": {"variant": r.variant, "unit": r.unit, "seconds": r.seconds, "tokens": r.tokens,
                             "cost_usd": r.cost_usd, "tool_calls": r.tool_calls, "submitted": r.submitted,
                             "input（冻结的上下文）": sorted(r.input)}}

    def _grade(self, g: Grade) -> dict:
        path = {"judge": ["runs", "judge", "grades"], "review": ["tutor", "cli", "grades"],
                "outcome": ["course_eval", "grades"]}.get(g.grader, ["runs", "grades"])     # 其余是系统按规则打的分
        feeds = [{"node": "report", "fields": "按版本汇总"}]
        if g.grader in ("check", "practice_verify"):
            feeds.append({"node": "gate", "fields": "通过才能发布"})
        score = "" if g.score is None else f"{g.score:.0%}"
        return {"id": g.id, "kind": "grade", "ts": g.ts, "lane": "model" if g.grader == "outcome" else "prep",
                "title": f"评分 · {g.grader} · {g.run}", "subtitle": " · ".join(x for x in (score, g.verdict) if x),
                "actor": g.actor, "path": path, "feeds": feeds,
                "recorded": {k: x for k, x in {"grader": g.grader, "grader_version": g.grader_version, "score": g.score,
                                               "verdict": g.verdict, "dims": sorted(g.dims) or None,
                                               "issues": _short(g.issues) or None}.items() if x not in (None, "")}}
