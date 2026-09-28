"""出单元题（D-041，PRD_V2 阶段 C）：单元题是产出循环（D-035）里的又一种产出物，和备课共用一台机器。

    生成        quiz-maker 出一整套（submit_quiz，提交时就做静态检查）
    检验器链    便宜的先跑，前面阻断就不跑后面的：
                  1. check    静态：结构、每种题的必填、答案不漏进题面、分布（6–8 道、≥3 种题型、≥1 道 4 级）、命令安全的固定规则
                  2. safety   评审模型读一遍要执行的东西（代码题的测试和参考实现、终端题的 setup 和参考做法）
                  3. run      实跑：代码题"初始代码红、参考实现绿"，终端题"不做不过、在受限终端里按参考做法做完就过"
                  4. quality  评审模型按 rubric 的质量结论（和 2 是同一次调用；3 先阻断时也一起交给修复）
    修复        只重写被指出的题（submit_quiz_repair）
    发布        拆成 lessons/<学科>/<NN-slug>/ 的 quiz.yaml、key.yaml、code/

输入 = 发布的课程计划（outcomes、各节讲了什么、检查点题干）+ 这个学习者在这个单元的表现（course_eval 的逐节数据 + 薄弱节点）。
INVARIANT: 什么时候出——学习者打开这个单元最后一节时在后台开始（证据已经最全），学完时题目已经在了（prefetch）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from studykit.app.harness import Harness, ToolError
from studykit.app.learning import Course, LearnerModel
from studykit.app.loop import Attempt, Budget, Evaluator, Kind, run_loop
from studykit.app.ports import Content, JobRunner, PlanStore, PrepStatus
from studykit.app.prep import Progress
from studykit.domain import harness as h
from studykit.domain.artifact import Finding, blocking, repair_scope, within
from studykit.domain.errors import CourseError, DomainError
from studykit.domain.plan import sections_of
from studykit.domain.quiz import question_address, quiz_addresses, quiz_findings, quiz_parts, replace_question, split, test_file

AGENT = "quiz-maker"
# 代码题会在本机用 pytest 跑。固定规则只拦明确危险的写法，其余交给评审模型（D-030 同理）
_PY_DANGER = [(re.compile(r"\bshutil\.rmtree\b|\bos\.(remove|unlink|rmdir|removedirs)\b"), "删除文件或目录"),
              (re.compile(r"\b(socket|urllib|requests|http\.client|ftplib|smtplib)\b"), "访问网络"),
              (re.compile(r"\bos\.system\b|\bos\.popen\b|\beval\(|\bexec\("), "执行任意命令或代码")]


def lesson_ref(unit: str, taken: set[str]) -> str:
    """单元 id → 题目目录：tools-02-git → tools/02-git（已经有了就加 -2、-3）。"""
    topic, _, rest = unit.partition("-")
    base = f"{topic}/{rest or unit}"
    ref, n = base, 1
    while ref in taken:
        n += 1
        ref = f"{base}-{n}"
    return ref


def _node_line(nid: str, title: str, misconceptions: dict[str, str]) -> str:
    """简报里的一个知识点；已有的误解列在后面，得分点直接引用这些 id（D-056）。"""
    ms = "；".join(f"`{m}` {d}" for m, d in sorted(misconceptions.items()))
    return f"- `{nid}` {title}" + (f"（已有误解：{ms}）" if ms else "")


def _findings(g: h.Grade | None, evaluators: tuple[str, ...] | None = None) -> list[Finding]:
    fs = [Finding.from_dict(d) for d in (g.detail.get("findings") or [] if g else [])]
    return [f for f in fs if evaluators is None or f.evaluator in evaluators]


class QuizMaker:
    def __init__(self, *, harness: Harness, course: Course, learner: LearnerModel, content: Content, plans: PlanStore,
                 checkers: dict, status: PrepStatus, jobs: JobRunner, command_problems: Callable[[str], list[str]] = lambda c: [],
                 budget: Budget = Budget()):
        """command_problems：学科给的命令安全固定规则（cs_practice/safety.py），终端题的命令用它先筛一遍。"""
        self.harness, self.course, self.learner, self.content, self.plans = harness, course, learner, content, plans
        self.command_problems = command_problems
        self.checkers, self.status, self.jobs, self.budget = checkers, status, jobs, budget
        self.runs = harness.runs
        harness.register_tool("submit_quiz", {
            "description": "提交这个单元的单元题（title + questions，每道题连同 key）。环境会检查结构、分布、答案有没有漏进题面；不通过会返回错误，改完再提交。",
            "parameters": {"type": "object", "properties": {"quiz": {"type": "object", "properties": {
                "title": {"type": "string"}, "questions": {"type": "array", "items": {"type": "object"}}},
                "required": ["title", "questions"]}}, "required": ["quiz"]}}, self._submit_quiz)
        harness.register_tool("submit_quiz_repair", {
            "description": "修复时只提交被指出的题：每个地址一项，value 是这道题修改后的完整对象（包括 key）。",
            "parameters": {"type": "object", "properties": {"parts": {"type": "array", "items": {"type": "object", "properties": {
                "address": {"type": "string", "description": "如 /questions/2"}, "value": {"type": "object"}},
                "required": ["address", "value"]}}}, "required": ["parts"]}}, self._submit_repair)

    # ---------- 输入 ----------

    def build_input(self, unit: str) -> dict:
        plan = self.plans.current(unit)
        if plan is None:
            raise DomainError(f"{unit} 还没有发布的课程，出不了单元题")
        topic = unit.split("-")[0]
        graph = {nid: n for nid, n in self.content.graph().items() if n.topic == topic}
        nodes = {nid: n.title for nid, n in graph.items()}
        misconceptions = {nid: dict(n.misconceptions) for nid, n in graph.items() if n.misconceptions}
        return {"unit": unit, "unit_title": plan.get("title", unit), "outcomes": plan.get("outcomes") or [],
                "sections": [{"title": s.get("title", ""), "goal": s.get("goal", ""),
                              "terms": [t.get("term") for t in s.get("terms") or []],
                              "checkpoints": [{"type": c.get("type"), "concept": c.get("concept"), "prompt": c.get("prompt", "")}
                                              for c in s.get("checkpoint") or []]} for s in sections_of(plan)],
                "nodes": nodes, "misconceptions": misconceptions,
                "performance": self.course.evaluate(unit)["rows"], "related": self.learner.related(unit),
                "learner": self.harness.build_input(unit)["learner"], "plan": (plan.get("provenance") or {}).get("run")}

    @staticmethod
    def render_brief(task: str, d: dict) -> str:
        secs = []
        for i, s in enumerate(d["sections"], 1):
            secs.append(f"## 第 {i} 节 {s['title']}\n- 目标：{s['goal']}\n- 新词：{'、'.join(map(str, s['terms'])) or '无'}")
            secs += [f"- 检查点（{c['type']}，{c['concept']}）：{c['prompt'].strip()[:300]}" for c in s["checkpoints"]]
        perf = []
        for r in d["performance"]:
            bits = [f"用时 {r['actual']}/{r['planned']} 分钟"]
            if r.get("rating"):
                bits.append(f"费劲程度 {r['rating']}/5")
            if r["attempts"]:
                bits.append("检查点提交次数 " + "、".join(map(str, r["attempts"])) + "（第一次就对：" +
                            "、".join("是" if x else "否" for x in r["first_try"]) + "）")
            if r.get("max_hint"):
                bits.append(f"最多用到第 {r['max_hint']} 级提示")
            if r.get("unknown"):
                bits.append(f"{r['unknown']} 个新词点了「没看懂」")
            if r.get("misses"):
                bits.append("标出没解释的词：" + "、".join(r["misses"]))
            if r.get("skipped"):
                bits.append("跳过了这一节")
            perf.append(f"- 第 {r['section']} 节 {r['title']}：" + "；".join(bits))
        weak = d["related"].get("weak") or []
        perf.append("- 知识图里和这个单元相关的薄弱点：" + ("；".join(f"{w['title']}（`{w['id']}`）" for w in weak) or "无"))
        return (task.replace("{unit_id}", d["unit"]).replace("{unit_title}", d["unit_title"])
                .replace("{outcomes}", "\n".join(f"{i}. {o}" for i, o in enumerate(d["outcomes"], 1)))
                .replace("{sections}", "\n\n".join(secs))
                .replace("{nodes}", "\n".join(_node_line(nid, t, d.get("misconceptions", {}).get(nid, {}))
                                               for nid, t in sorted(d["nodes"].items())))
                .replace("{performance}", "\n".join(perf)).replace("{learner}", d["learner"]))

    # ---------- 运行 ----------

    def make(self, unit: str, model: str | None = None, timeout: int = 900) -> h.Run:
        hs = self.harness
        agent = hs.agent(AGENT)
        data = self.build_input(unit)
        brief = self.render_brief(agent.file("task").read_text(encoding="utf-8"), data) + hs.stage(agent, "make")[0]
        return hs._go(agent, "make", unit, "-q", data, brief, model, timeout)

    def repair(self, prev: h.Run, findings: list[Finding], model: str | None = None, timeout: int = 900) -> h.Run:
        hs = self.harness
        agent = hs.agent(AGENT)
        quiz = self._output(prev)
        info = prev.input.get("repair") or {}
        rnd = int(info.get("round", 0)) + 1
        data = {**{k: v for k, v in prev.input.items() if k not in ("stage", "repair")},
                "repair": {"of": prev.id, "round": rnd, "allowed": repair_scope(findings), "findings": [f.as_dict() for f in findings]}}
        found = []
        for f in findings:
            found.append(f"- `{f.address or '（整套）'}`（{f.evaluator}）：{f.what}")
            if f.evidence:
                found.append("  证据：\n  ```\n  " + f.evidence[-800:].replace("\n", "\n  ") + "\n  ```")
        blocks = [f"## {a}\n\n```json\n{json.dumps(v, ensure_ascii=False, indent=1)}\n```" for a, v in quiz_parts(quiz).items()]
        brief = (hs.stage(agent, "repair")[0].replace("{unit_id}", prev.unit).replace("{unit_title}", str(data.get("unit_title", "")))
                 .replace("{findings}", "\n".join(found))
                 .replace("{allowed}", "\n".join(f"- `{a}`" if a else "- 整套（地址写 \"\"）" for a in data["repair"]["allowed"]))
                 .replace("{quiz}", "\n\n".join(blocks)))
        return hs._go(agent, "repair", prev.unit, f"-q-r{rnd}", data, brief, model, timeout, files={"current.json": quiz})

    # ---------- 工具 ----------

    @staticmethod
    def _known(inp: dict) -> tuple[set[str], dict[str, set[str]]]:
        """输入里的知识点 id，和它们下面已有的误解 id。"""
        return set(inp["nodes"]), {nid: set(ms) for nid, ms in (inp.get("misconceptions") or {}).items()}

    def static(self, quiz: dict, known: tuple[set[str], dict[str, set[str]]]) -> list[Finding]:
        node_ids, misconceptions = known
        out = quiz_findings(quiz, node_ids, set(self.checkers), misconceptions=misconceptions)
        for i, q in enumerate(quiz.get("questions") or [] if isinstance(quiz, dict) else []):
            k = q.get("key") or {}
            if q.get("checker") == "terminal":
                for line in [*(q.get("setup") or []), *(k.get("solution") or [])]:
                    for p in self.command_problems(str(line)):
                        out.append(Finding(question_address(i), f"第 {i + 1} 题的命令不安全（{p}）：{line}", "safety", evidence=str(line)))
            if q.get("checker") == "code":
                code = "\n".join(str(x) for x in (q.get("tests"), q.get("starter"), k.get("solution")) if x)
                for pat, why in _PY_DANGER:
                    if pat.search(code):
                        out.append(Finding(question_address(i), f"第 {i + 1} 题的代码不安全（{why}）：代码题只该在临时目录里算东西", "safety"))
        return out

    def _submit_quiz(self, ws: Path, args: dict) -> str:
        quiz = args.get("quiz") or {}
        errors = [f.what for f in self.static(quiz, self._known(self.harness._input(ws)))]
        self.harness._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors})
        if errors:
            raise ToolError("单元题没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
        return "已收到，检查通过。任务完成，不需要再做别的。"

    def _submit_repair(self, ws: Path, args: dict) -> str:
        info = self.harness._input(ws).get("repair")
        if not info:
            raise ToolError("这次运行不是修复，用 submit_quiz 提交")
        parts, allowed = args.get("parts"), info["allowed"]
        if not isinstance(parts, list) or not parts or not all(isinstance(p, dict) for p in parts):
            raise ToolError("parts 要是非空数组，每项是 {address, value}")
        quiz = json.loads((ws / "current.json").read_text(encoding="utf-8"))
        seen = set()
        for p in parts:
            addr = str(p.get("address") or "")
            if "" not in allowed and addr not in allowed:
                raise ToolError(f"{addr or '（整套）'} 不在要重写的部分里，只能改：{', '.join(allowed)}")
            try:
                quiz = replace_question(quiz, addr, p.get("value"))
            except CourseError as e:
                raise ToolError(str(e)) from e
            seen.add(addr)
        missing = [] if "" in allowed else [a for a in allowed if a not in seen]
        if missing:
            raise ToolError("这些题还没有给出修改后的内容：" + ", ".join(missing))
        errors = [f.what for f in self.static(quiz, self._known(self.harness._input(ws)))
                  if any(within(f.address, a) or within(a, f.address) for a in allowed)]
        self.harness._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "parts": sorted(seen)})
        if errors:
            raise ToolError("修改后的题没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(quiz, ensure_ascii=False, indent=2), encoding="utf-8")
        return "已收到，检查通过。任务完成，不需要再做别的。"

    # ---------- 检验器 ----------

    def reviewer(self, run: h.Run) -> h.Grade:
        """评审模型：安全闸门 + 质量。同一次运行只评一次。"""
        hs = self.harness
        done = hs._latest(run, "reviewer")
        if done is not None:
            return done
        agent = hs.agent(AGENT)
        ws = hs.dir(AGENT, run.id)
        quiz = self._output(run)
        conf = agent.spec["review"]
        system = (agent.dir / conf["prompt"]).read_text(encoding="utf-8")
        rubric = (agent.dir / agent.spec["eval"]["rubric"]).read_text(encoding="utf-8")
        blocks = [f"## {a}\n\n```json\n{json.dumps(v, ensure_ascii=False, indent=1)}\n```" for a, v in quiz_parts(quiz).items()]
        (ws / "review_input.md").write_text("\n\n".join([
            "# 评分标准\n\n" + rubric, "# 出题人拿到的简报\n\n" + (ws / "brief.md").read_text(encoding="utf-8"),
            "# 单元题（按地址）\n\n" + "\n\n".join(blocks)]), encoding="utf-8")
        model = f"{conf['model']['provider']}/{conf['model']['id']}"
        rv = hs.versions.record(f"{AGENT}#reviewer", {"system": system, "rubric": rubric}, {"model": model})
        cost, tokens = 0.0, 0
        try:
            reply = hs.runtime.complete(conf["model"], system, ws / "review_input.md", ws, 600)
            cost, tokens = reply.cost_usd, reply.tokens
            found = h.parse_review(reply.text, quiz_addresses(quiz))
        except Exception as e:  # noqa: BLE001  评审失败 = 没被放行
            found = [Finding("", f"评审模型没有给出可用的结论：{e}", "reviewer_safety")]
        return hs._grade(run, "reviewer", rv.id, model, verdict="fail" if blocking(found) else "pass",
                         detail={"findings": [f.as_dict() for f in found], "cost_usd": cost, "tokens": tokens})

    def verify(self, run: h.Run) -> list[Finding]:
        """实跑：每道能执行的题（code、terminal）在临时目录里走一遍。"""
        hs = self.harness
        quiz = self._output(run)
        _, _, files = split(quiz, run.unit)
        base = hs.dir(AGENT, run.id) / "raw" / f"verify-{hs.ids.new()}"
        found = []
        for i, q in enumerate(quiz.get("questions") or []):
            checker = self.checkers.get(q.get("checker"))
            if not hasattr(checker, "verify"):
                continue
            mine = {rel: text for rel, text in files.items() if q.get("file") and rel in (q["file"], test_file(q["file"]))}
            for what, evidence in checker.verify(q, q.get("key") or {}, mine, base / str(q.get("id"))):
                found.append(Finding(question_address(i), f"第 {i + 1} 题（{q.get('id')}）：{what}", "quiz_verify", evidence=evidence))
        hs._grade(run, "practice_verify", "quiz-d041", "system", score=0.0 if blocking(found) else 1.0,
                  verdict="fail" if blocking(found) else "pass", detail={"findings": [f.as_dict() for f in found]})
        return found

    def kind(self, model: str | None, progress: Progress) -> Kind:
        def attempt(run: h.Run) -> Attempt:
            out = self.harness.dir(AGENT, run.id) / "output.json"
            return Attempt(json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}, run.id, run.cost_usd)

        def generate(inp: dict) -> Attempt:
            progress.set(step="make", label="按你在这个单元的表现出题", round=1)
            a = attempt(self.make(inp["unit"], model))
            a.costs = {"出题": a.cost}
            return a

        def repair(prev: Attempt, findings: list[Finding], inp: dict) -> Attempt:
            if not prev.artifact:
                progress.set(step="make", label="重新出题", round=progress.state["round"] + 1)
                return attempt(self.make(inp["unit"], model))
            progress.repairing(repair_scope(findings))
            a = attempt(self.repair(self._run(prev), findings, model))
            a.costs = {"修复": a.cost}
            return a

        def check(a: Attempt) -> list[Finding]:
            if not a.artifact:
                return [Finding("", "没有交出单元题（运行出错或超时）", "check")]
            progress.checking("检查结构和分布")
            found = self.static(a.artifact, self._known(self._run(a).input))
            self.harness._grade(self._run(a), "check", "quiz-d041", "system", verdict="fail" if blocking(found) else "pass",
                                detail={"findings": [f.as_dict() for f in found]})
            return found

        def safety(a: Attempt) -> list[Finding]:
            progress.checking("评审模型读题、看要执行的代码")
            return _findings(self.reviewer(self._run(a)), ("reviewer_safety",))

        def run_(a: Attempt) -> list[Finding]:
            progress.checking("试跑代码题和终端题")
            found = self.verify(self._run(a))
            if blocking(found):          # 同一次评审调用给出的质量问题，一起交给这一轮修复
                found += _findings(self.reviewer(self._run(a)), ("reviewer",))
            return found

        def quality(a: Attempt) -> list[Finding]:
            return _findings(self.reviewer(self._run(a)), ("reviewer",))

        return Kind("unit_quiz", generate, repair, [Evaluator("check", check), Evaluator("safety", safety),
                                                    Evaluator("run", run_), Evaluator("quality", quality)], quiz_parts,
                    review_cost=lambda a: self.harness.model_cost(a.run))

    # ---------- 用例 ----------

    def prepare(self, unit: str, model: str | None = None, report: Callable[[dict], None] | None = None) -> dict:
        """出单元题：出题 → 检验 → 只修被指出的题 → 通过就发布。返回这次循环的摘要。"""
        if self.lesson_for(unit):
            raise DomainError(f"{unit} 已经有单元题了：{self.lesson_for(unit)}")
        self.build_input(unit)                                 # 没有发布的课程就在这里报错
        if not self.status.begin(unit):
            raise DomainError(f"{unit} 的单元题已经在出了，等它跑完")

        def both(p: dict) -> None:
            self.status.update(unit, p)
            if report:
                report(p)
        try:
            result = run_loop(self.kind(model, Progress(both)), {"unit": unit}, self.budget)
            final = self.runs.run(result.rounds[-1].run)
            summary = {"spent_usd": result.spent, "blocking": [f.as_dict() for f in result.blocking],
                       "rounds": [{"run": r.run, "cost_usd": round(r.cost, 4), "costs": r.costs, "findings": [f.as_dict() for f in r.findings]}
                                  for r in result.rounds]}
            self.harness._grade(final, "loop", "d-041", "system", verdict=result.status, detail=summary)
            ref = None
            if result.status == "accepted":
                ref = lesson_ref(unit, {l.ref for l in self.content.lessons()})
                quiz_yaml, key_yaml, files = split(result.artifact, unit)
                self.content.write_lesson(ref, quiz_yaml, key_yaml, files)
                self.content.add_misconceptions(result.artifact.get("misconceptions") or {}, by=final.id)
        except BaseException as e:
            self.status.end(unit, f"{type(e).__name__}: {e}")
            raise
        self.status.end(unit)
        return {"unit": unit, "status": result.status, "run": final.id, "lesson": ref, **summary}

    def lesson_for(self, unit: str) -> str | None:
        return next((l.ref for l in self.content.lessons() if l.unit == unit), None)

    def state(self, unit: str) -> dict:
        """课程页"学完了"那里显示什么：ready（有题了）/ preparing（在出，带进度）/ escalated / interrupted / todo。"""
        ref = self.lesson_for(unit)
        if ref:
            return {"stage": "ready", "lesson": ref}
        st = self.status.get(unit) or {}
        if st.get("alive"):
            return {"stage": "preparing", "progress": st.get("progress") or {"label": "开始出题"}}
        runs = [r for r in self.runs.runs(AGENT) if r.unit == unit]
        if not runs:
            return {"stage": "todo"}
        loop = self.harness._latest(runs[-1], "loop")
        if loop is None:
            return {"stage": "interrupted", "error": st.get("error", "")}
        return {"stage": "escalated", "stuck": [f["what"] for f in loop.detail.get("blocking") or []]}

    def start(self, unit: str) -> dict:
        """在后台出题（网页上的按钮、打开最后一节时的预备）。"""
        if (self.status.get(unit) or {}).get("alive"):
            raise DomainError(f"{unit} 的单元题已经在出了，等它跑完")
        if not self.jobs.start(f"{AGENT}:{unit}", lambda: self.prepare(unit)):
            raise DomainError(f"{unit} 的单元题已经在出了，等它跑完")
        return self.state(unit)

    def prefetch(self, unit: str, section) -> bool:
        """学习者打开这个单元最后一节时，在后台开始出题（D-041）。只在从没出过、也没在出的时候开始。"""
        plan = self.plans.current(unit)
        if plan is None or section != len(sections_of(plan)) - 1 or self.content.settings().get("prefetch_next") is False:
            return False
        if self.state(unit)["stage"] != "todo":
            return False
        self.start(unit)
        return True

    # ---------- 内部 ----------

    def _run(self, a: Attempt) -> h.Run:
        run = self.runs.run(a.run)
        if run is None:
            raise DomainError(f"找不到运行 {a.run}")
        return run

    def _output(self, run: h.Run) -> dict:
        return json.loads((self.harness.dir(AGENT, run.id) / "output.json").read_text(encoding="utf-8"))

