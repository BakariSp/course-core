"""harness 的用例（D-022）：跑 agent、给它的工具、评测、发布、按版本对比。

一次运行的工作目录 data/runs/<agent>/<run>/（大文件，Run 和 Grade 的索引在数据库里）：
    brief.md          渲染好的简报（给模型的上下文）
    input.json        冻结的输入数据：生成时学习者会什么、预算、白名单……（永久：课程评测要用）
    fetches.jsonl     agent 读过的页面（含读到的文字，核对出处用）
    submissions.jsonl 每次提交：接受 / 退回 + 原因（运行中自我修正的记录）
    output.json/.md   产出（课程计划 + 给导师审阅的 Markdown）
    raw/              agent loop 的原始日志，可以过期清理

INVARIANT: 优化者（现在是导师）只改 Variant 的组成；评测标准（rubric、用例、评分器）的修改单独记录（D-022）。
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from studykit.app.learning import Course, LearnerModel
from studykit.app.ports import (AgentDef, AgentRuntime, Clock, Content, Fetcher, FetchError, IdGen, RunStore, Spec)
from studykit.app.paths import safe_path
from studykit.domain import course_eval, harness as h, plan_check
from studykit.domain.errors import DomainError
from studykit.domain.ids import UnitId
from studykit.domain.plan import PLAN_SCHEMA_VERSION, plan_minutes, sections_of

MAX_TEXT = 15000          # fetch_url 一次最多返回多少字
MAX_LINKS = 80
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")


class ToolError(Exception):
    """工具执行失败：错误信息原样返回给模型，让它自己调整。"""


def _src(*objs) -> str:
    return h.content_hash(*[inspect.getsource(o) for o in objs])


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _norm_host(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def allowed_hosts(curriculum: str) -> set[str]:
    """白名单 = curriculum.md 里出现过的所有网站。改白名单就改 curriculum.md。"""
    from urllib.parse import urlparse
    return {_norm_host(urlparse(u).netloc) for u in re.findall(r"https?://[^\s)>\]]+", curriculum)}


def is_allowed(url: str, hosts: set[str]) -> bool:
    from urllib.parse import urlparse
    p = urlparse(url)
    return p.scheme in ("http", "https") and _norm_host(p.netloc) in hosts


class Harness:
    def __init__(self, *, runs: RunStore, runtime: AgentRuntime, fetcher: Fetcher, content: Content, course: Course,
                 learner: LearnerModel, specs: list[Spec], clock: Clock, ids: IdGen, root: Path, workspace: Path,
                 learner_id: str):
        self.runs, self.runtime, self.fetcher, self.content = runs, runtime, fetcher, content
        self.course, self.learner, self.specs, self.clock, self.ids = course, learner, specs, clock, ids
        self.root, self.workspace, self.learner_id = root, workspace, learner_id

    # ---------- agent 定义与版本 ----------

    def agent(self, name: str) -> AgentDef:
        d = self.root / "agents" / name
        if not re.fullmatch(r"[a-z0-9-]+", name or "") or not (d / "agent.yaml").exists():
            raise DomainError(f"没有这个 agent：{name}")
        return AgentDef(name, d, yaml.safe_load((d / "agent.yaml").read_text(encoding="utf-8")))

    def model(self, agent: AgentDef, override: str | None = None) -> dict:
        m = dict(agent.spec["model"])
        if override:
            m["id"] = override
        return m

    def variant(self, agent: AgentDef, model: dict) -> h.Variant:
        """一次运行用的版本组合。上下文配方和工具是代码，按源码算哈希：改了就是新版本（H1）。"""
        spec_rules = [r for s in self.specs for r in (*s.plan_rules, *s.checkpoint_rules.values())]
        return h.Variant(agent.name, {
            "system_prompt": h.content_hash(agent.file("system_prompt").read_bytes()),
            "task": h.content_hash(agent.file("task").read_bytes()),
            "tools": h.content_hash(agent.spec.get("tools"), _src(Harness.call_tool, Harness._fetch_url,
                                                                  Harness._submit_plan, plan_check), _src(*spec_rules)),
            "context": _src(Harness.build_input, Harness.render_brief),
            "model": f"{model['provider']}/{model['id']}" + (f":{model['thinking']}" if model.get("thinking") else ""),
            "runtime": self.runtime.version,
        })

    # ---------- 上下文配方 ----------

    def build_input(self, unit: str) -> dict:
        """给 agent 的全部输入数据。只给和这个单元相关的学习者模型（D-020 第 1 级），不给全部。"""
        unit = UnitId(unit)
        topic_id, topic, u = self._find_unit(unit)
        curriculum = (self.root / "curriculum.md").read_text(encoding="utf-8")
        nodes = self.content.graph()
        states = self.learner.states(nodes)
        profile = self.root / "progress" / "learner.md"
        return {
            "unit": unit, "unit_title": u.get("title", unit), "unit_notes": u.get("notes") or "无",
            "topic_id": topic_id, "topic_title": topic.get("title", topic_id),
            "curriculum_row": _curriculum_row(curriculum, topic_id),
            "hosts": sorted(allowed_hosts(curriculum)),
            **self.course.settings(),
            "known_terms": sorted(self.learner.known(topic_id)),
            "known_titles": sorted(s.node.title for s in states.values() if s.state == "mastered" and s.node.topic == topic_id),
            "existing_nodes": {nid: n.title for nid, n in nodes.items() if n.topic == topic_id},
            "related": self.learner.related(unit),
            "learner": re.sub(r"<!--.*?-->", "", profile.read_text(encoding="utf-8"), flags=re.DOTALL).strip()
            if profile.exists() else "（没有学习者画像）",
        }

    @staticmethod
    def render_brief(task: str, i: dict) -> str:
        lines = ["已经掌握（讲解里可以直接用，不用再解释）：" + ("、".join(i["known_titles"]) or "（还没有）")]
        rel = i["related"]
        if rel["weak"]:
            lines.append("和这个单元相关的薄弱点（讲到时放慢、多给例子）：")
            lines += [f"- {w['title']}（`{w['id']}`）：{'；'.join(w.get('why') or [])}" for w in rel["weak"]]
        if rel["prerequisites_not_mastered"]:
            lines.append("先修里还没掌握的：" + "、".join(f"{n['title']}（`{n['id']}`）" for n in rel["prerequisites_not_mastered"]))
        if i["existing_nodes"]:
            lines.append("知识图里这个学科已有的节点（新词和检查点的 concept 优先用这些 id，不要重复提议）：")
            lines += [f"- `{nid}` {title}" for nid, title in sorted(i["existing_nodes"].items())]
        return task.format(unit_id=i["unit"], unit_title=i["unit_title"], topic_id=i["topic_id"],
                           topic_title=i["topic_title"], unit_notes=i["unit_notes"], curriculum_row=i["curriculum_row"],
                           learner=i["learner"], knowledge="\n".join(lines),
                           unit_budget_minutes=i["unit_budget_minutes"], session_minutes=i["session_minutes"],
                           max_new_terms=i["max_new_terms"])

    def _find_unit(self, unit: str) -> tuple[str, dict, dict]:
        for tid, topic in (self.content.syllabus().get("topics") or {}).items():
            for u in topic.get("units") or []:
                if u.get("id") == unit:
                    return tid, topic, u
        raise DomainError(f"syllabus.yaml 里没有单元 {unit}")

    # ---------- 运行 ----------

    def run(self, name: str, unit: str, model: str | None = None, timeout: int = 900) -> h.Run:
        agent = self.agent(name)
        m = self.model(agent, model)
        variant = self.variant(agent, m)
        data = self.build_input(unit)
        started = self.clock.now()
        run_id = f"{started:%Y%m%d-%H%M%S}-{unit}"
        ws = self.dir(name, run_id)
        ws.mkdir(parents=True)
        (ws / "brief.md").write_text(self.render_brief(agent.file("task").read_text(encoding="utf-8"), data), encoding="utf-8")
        (ws / "input.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        raw = self.runtime.run(agent, ws, m, list(agent.spec["tools"]), timeout)
        s = h.summarize(raw.steps)
        run = h.Run(run_id, name, variant.id, unit, self.learner_id, "batch", started.isoformat(timespec="seconds"),
                    data, raw.seconds, raw.exit_code, (ws / "output.json").exists(), s["tokens"], s["cost_usd"],
                    s["tool_calls"], s["errors"], s["final_text"])
        self.runs.add_run(run, variant, raw.steps)
        return run

    def dir(self, agent: str, run_id: str) -> Path:
        return safe_path(self.workspace, f"{agent}/{run_id}")

    def resolve(self, agent: str, run_id: str | None = None) -> h.Run:
        if run_id:
            run = self.runs.run(run_id)
            if run is None or run.agent != agent:
                raise DomainError(f"{agent} 没有这次运行：{run_id}")
            return run
        runs = self.runs.runs(agent)
        if not runs:
            raise DomainError(f"{agent} 还没有运行记录")
        return runs[-1]

    # ---------- agent 的工具（pi 通过 python -m studykit.agent_tools 调用） ----------

    def tool_schemas(self, names: list[str] | None = None) -> list[dict]:
        types = {**plan_check.CORE_CHECKPOINT_TYPES, **{k: v for s in self.specs for k, v in s.checkpoint_docs.items()}}
        schema = plan_check.plan_schema(types, {k: v for s in self.specs for k, v in s.checkpoint_fields.items()},
                                        {k: v for s in self.specs for k, v in s.plan_fields.items()})
        tools = {
            "fetch_url": {"description": "抓取一个白名单网站的网页，返回正文（纯文本）和页面里的白名单链接。用它查课程官网、讲义、视频列表。",
                          "parameters": {"type": "object", "properties": {
                              "url": {"type": "string", "description": "要抓取的网页地址（必须在白名单网站内）"},
                              "start": {"type": "integer", "description": "从正文第几个字开始读，默认 0。长页面会分段返回，结果末尾会告诉你下一段的 start。"}},
                              "required": ["url"]}},
            "submit_plan": {"description": "提交这个单元的课程计划（结构化）。环境会检查时间预算、每节的新词数、检查点、知识节点、链接是否打开过；不通过会返回错误，改完再提交。",
                            "parameters": {"type": "object", "properties": {"plan": schema}, "required": ["plan"]}},
        }
        return [{"name": n, **t} for n, t in tools.items() if names is None or n in names]

    def call_tool(self, ws: Path, name: str, args: dict) -> str:
        if name == "fetch_url":
            return self._fetch_url(ws, args.get("url", ""), args.get("start", 0))
        if name == "submit_plan":
            return self._submit_plan(ws, args.get("plan") or {})
        raise ToolError(f"没有这个工具：{name}")

    def _log(self, ws: Path, name: str, rec: dict) -> None:
        with (ws / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _input(self, ws: Path) -> dict:
        return json.loads((ws / "input.json").read_text(encoding="utf-8"))

    def _fetch_url(self, ws: Path, url: str, start: int = 0) -> str:
        hosts = set(self._input(ws)["hosts"])
        # WHY: #锚点指向同一个页面。不去掉的话 agent 会把 x/ 和 x/#exercises 当成两页各读一遍，白白消耗 token。
        url = (url or "").strip().split("#")[0]
        start = max(0, int(start or 0))
        if not is_allowed(url, hosts):
            raise ToolError(f"不在白名单里：{url}。只能访问这些网站：{', '.join(sorted(hosts))}")
        if any(f.get("ok") and f["url"] == url and f.get("start", 0) == start for f in _read_jsonl(ws / "fetches.jsonl")):
            return (f"你在这次任务里已经读过 {url} 的这一段（start={start}），内容见之前的结果。"
                    "要读后面的内容，用上次结果末尾给出的 start。")
        try:
            page = self.fetcher.fetch(url)
        except FetchError as e:
            self._log(ws, "fetches.jsonl", {"url": url, "status": e.status, "ok": False})
            raise ToolError(str(e)) from e
        links, seen = [], set()
        for href, anchor in page.links:
            if href not in seen and is_allowed(href, hosts):
                seen.add(href)
                links.append((href, anchor))
        links = links[:MAX_LINKS]
        full = page.text
        end = min(len(full), start + MAX_TEXT)
        chunk = full[start:end]
        self._log(ws, "fetches.jsonl", {"url": url, "final_url": page.final_url, "status": page.status, "ok": True,
                                        "start": start, "title": page.title, "links": [x for x, _ in links], "text": chunk})
        if start >= len(full) and full:
            return f"{url} 的正文一共 {len(full)} 字，start={start} 已经超过结尾。"
        # WHY: 长页面分段返回，并在末尾写明下一段怎么读。只截断不说明的话，agent 会去猜别的网址找剩下的内容。
        if end < len(full):
            note = f"\n\n[这是第 {start}–{end} 字，全文共 {len(full)} 字。继续读：fetch_url(url=\"{url}\", start={end})]"
        else:
            note = f"\n\n[全文共 {len(full)} 字，已读到结尾]" if start else ""
        link_block = ""
        if start == 0:      # 页面链接只在第一段附上，后面几段不重复
            link_lines = "\n".join(f"- {a or '(无文字)'} → {x}" for x, a in links)
            link_block = f"\n\n## 页面里的白名单链接\n{link_lines or '(无)'}"
        return f"# {page.title}\nURL: {page.final_url}\n\n" + chunk + note + link_block

    def limits(self, ws: Path) -> plan_check.PlanLimits:
        i = self._input(ws)
        grounded = set()
        # INVARIANT: 只算打开成功的页面，不算页面上出现过的链接——没打开过的页面，agent 不知道里面是什么。
        for f in _read_jsonl(ws / "fetches.jsonl"):
            if f.get("ok"):
                grounded |= {plan_check.url_key(f["url"]), plan_check.url_key(f.get("final_url", f["url"]))}
        types = (*plan_check.CORE_CHECKPOINT_TYPES, *(k for s in self.specs for k in s.checkpoints))
        return plan_check.PlanLimits(i["unit"], i["unit_budget_minutes"], i["session_minutes"], i["max_new_terms"],
                                     {t.lower() for t in i["known_terms"]}, dict(i["existing_nodes"]), grounded, tuple(types))

    def check_plan(self, ws: Path, plan: dict) -> list[str]:
        return plan_check.check_plan(plan, self.limits(ws), [r for s in self.specs for r in s.plan_rules],
                                     {k: r for s in self.specs for k, r in s.checkpoint_rules.items()})

    def _submit_plan(self, ws: Path, plan: dict) -> str:
        errors = self.check_plan(ws, plan)
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "minutes": plan_minutes(plan or {})})
        if errors:
            raise ToolError("课程计划没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_plan_md(plan, self._input(ws)["session_minutes"]), encoding="utf-8")
        return "已收到，检查通过。任务完成，不需要再做别的。"

    # ---------- 评测 ----------

    def _grade(self, run: h.Run, grader: str, version: str, actor: str, **kw) -> h.Grade:
        g = h.Grade(self.ids.new(), self.clock.now().isoformat(timespec="seconds"), run.id, grader, version, actor, **kw)
        self.runs.add_grade(g)
        return g

    def evaluate(self, run: h.Run, use_judge: bool = True) -> list[h.Grade]:
        agent = self.agent(run.agent)
        ws = self.dir(run.agent, run.id)
        grades = [self._check(run, agent, ws)]
        if use_judge and (ws / "output.json").exists():
            try:
                grades += self._judge(run, agent, ws)
            except Exception as e:  # noqa: BLE001  评分失败不影响自动检查的结果
                grades.append(self._grade(run, "judge", "error", "system", verdict="error", detail={"error": str(e)}))
        return grades

    def _check(self, run: h.Run, agent: AgentDef, ws: Path) -> h.Grade:
        items = []
        add = lambda i, desc, ok, detail="": items.append((i, desc, bool(ok), detail))  # noqa: E731
        out = ws / "output.json"
        add("submitted", "按时提交了结果", out.exists(), "" if out.exists() else f"exit={run.exit_code}")
        if out.exists():
            plan = json.loads(out.read_text(encoding="utf-8"))
            limits = self.limits(ws)
            problems = self.check_plan(ws, plan)
            add("plan_valid", "课程计划通过环境检查", not problems, "；".join(problems))
            secs = sections_of(plan)
            add("shape", "4–8 个小节", 4 <= len(secs) <= 8, f"{len(secs)} 节")
            items_ = [c for s in secs for c in s.get("checkpoint") or []]
            practice = [s for s in secs if any(c.get("type") not in plan_check.CORE_CHECKPOINT_TYPES for c in s.get("checkpoint") or [])]
            if len(limits.checkpoint_types) > len(plan_check.CORE_CHECKPOINT_TYPES):
                add("practice", "至少一半的小节有动手型检查点（D-013、D-014）", 2 * len(practice) >= len(secs),
                    f"{len(practice)}/{len(secs)} 节")
            traps = [t for c in items_ for t in c.get("traps") or []] + [k["trap"] for c in items_ for k in c.get("checks") or [] if k.get("trap")]
            add("traps", "至少写了 3 个挂在检查点上的坑（答错时才出现，D-015）", len(traps) >= 3, f"{len(traps)} 个")
            tagged = [c for c in items_ if c.get("concept")]
            add("concepts", "检查点都标了检验的知识节点（D-020）", len(tagged) == len(items_), f"{len(tagged)}/{len(items_)}")
            urls = sorted(plan_check.plan_urls(plan))
            dead = []
            for u in [u for u in urls if plan_check.url_key(u) not in limits.grounded][:20]:
                try:
                    if self.fetcher.fetch(u, timeout=15).status >= 400:
                        dead.append(u)
                except FetchError as e:
                    dead.append(f"{u}（{e.status or '打不开'}）")
            add("reachable", "链接都能打开", not dead, "；".join(dead))
            pages = {plan_check.url_key(f["url"]) for f in _read_jsonl(ws / "fetches.jsonl") if f.get("ok")}
            add("researched", "至少读了 2 个页面再写", len(pages) >= 2, f"打开成功 {len(pages)} 个页面")
            md = (ws / "output.md").read_text(encoding="utf-8").lower()
            case = self._case(agent, run.unit)
            for s in case.get("must_mention") or []:
                add(f"mention:{s}", f"提到「{s}」", s.lower() in md)
            for s in case.get("must_not_mention") or []:
                add(f"avoid:{s}", f"没有出现「{s}」", s.lower() not in md)
        score, dims = h.check_results(items)
        version = h.content_hash(_src(Harness._check), self._case(agent, run.unit))
        return self._grade(run, "check", version, "system", score=score, verdict="pass" if score == 1 else "fail", dims=dims)

    def _case(self, agent: AgentDef, unit: str) -> dict:
        path = agent.dir / agent.spec["eval"]["cases"]
        cases = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cases") or [] if path.exists() else []
        return next((c for c in cases if c.get("unit") == unit), {})

    def _judge(self, run: h.Run, agent: AgentDef, ws: Path) -> list[h.Grade]:
        rubric = (agent.dir / agent.spec["eval"]["rubric"]).read_text(encoding="utf-8")
        jm = agent.spec["eval"]["judge_model"]
        fetched = [f for f in _read_jsonl(ws / "fetches.jsonl") if f.get("ok")]
        (ws / "judge_input.md").write_text("\n\n".join([
            "# 评分标准\n\n" + rubric,
            "# 助教拿到的简报\n\n" + (ws / "brief.md").read_text(encoding="utf-8"),
            "# 助教实际打开过的页面\n\n" + ("\n".join(f"- {f.get('title') or '(无标题)'} — {f['url']}" for f in fetched) or "（没有）"),
            "# 助教写的课程计划\n\n" + (ws / "output.md").read_text(encoding="utf-8"),
        ]), encoding="utf-8")
        data = h.parse_judge(self.runtime.complete(jm, h.JUDGE_SYSTEM, ws / "judge_input.md", ws, 600), h.rubric_ids(rubric))
        model = f"{jm['provider']}/{jm['id']}"
        judge = self._grade(run, "judge", h.content_hash(rubric, jm, h.JUDGE_SYSTEM), model,
                            score=round((data["avg"] - 1) / 4, 3), dims=data["scores"],
                            issues=[{"layer": "prompt", "what": data.get("suggestion", "")}] if data.get("suggestion") else [],
                            detail={"avg": data["avg"], "top_issue": data.get("top_issue", "")})
        checks = h.verify_claims(data.get("suspect_claims") or [], "\n".join(f.get("text", "") for f in fetched))
        claim = self._grade(run, "claim_check", _src(h.verify_claims), "system",
                            score=sum(c["found_in_pages"] for c in checks) / len(checks) if checks else None,
                            refs=[judge.id], detail={"claims": checks})
        return [judge, claim]

    def review(self, run: h.Run, verdict: str, note: str, issues: list[dict], actor: str = "tutor") -> h.Grade:
        """导师审阅（结构化）：结论 + 问题出在哪一层，喂回下一次迭代（D-022）。"""
        if verdict not in ("publish", "revise", "reject"):
            raise DomainError("verdict 只能是 publish / revise / reject")
        bad = [i for i in issues if i.get("layer") not in ("prompt", "context", "tools", "model", "runtime", "eval")]
        if bad:
            raise DomainError("每个问题要标层：prompt / context / tools / model / runtime / eval")
        return self._grade(run, "review", "tutor", actor, verdict=verdict, issues=issues, detail={"note": note})

    def verify_lab(self, run: h.Run) -> dict:
        practice = next((s.practice for s in self.specs if s.practice), None)
        if practice is None:
            raise DomainError("没有能验证练习场的学科 spec")
        ws = self.dir(run.agent, run.id)
        result = practice.verify(json.loads((ws / "output.json").read_text(encoding="utf-8")), ws / "raw" / "lab-verify")
        self._grade(run, "practice_verify", _src(type(practice).verify), "system", score=1.0 if result["ok"] else 0.0,
                    verdict="pass" if result["ok"] else "fail", detail=result)
        return result

    def grade_outcome(self, result: dict) -> h.Grade:
        """学习结果评分（D-019）：学习者用过这版课程之后，预测和实际对上了多少。最终裁判。"""
        run = self.runs.run(result["plan"])
        if run is None:
            raise DomainError(f"找不到产出这版课程的运行：{result['plan']}")
        s = result["summary"]
        return self._grade(run, "outcome", _src(course_eval.evaluate, course_eval.summarize), "learner",
                           dims={k: v for k, v in s.items() if k != "advice"},
                           issues=[{"layer": "prompt", "what": a} for a in s["advice"]],
                           refs=[f"{result['unit']}@{result['plan']}"], detail=result)

    def _latest(self, run: h.Run, grader: str) -> h.Grade | None:
        gs = [g for g in self.runs.grades(run.id) if g.grader == grader]
        return gs[-1] if gs else None

    def publish(self, run: h.Run) -> list[str]:
        """把通过检查的产出发布给学习者。返回并入知识图的新节点。"""
        check = self._latest(run, "check")
        if check is None:
            raise DomainError(f"先评测再发布：python study.py agent eval {run.agent} --run {run.id}")
        if check.verdict != "pass":
            raise DomainError("自动检查没通过，不能发布：" + "；".join(d["reason"] for d in check.dims.values() if d["score"] < 1))
        ws = self.dir(run.agent, run.id)
        plan = json.loads((ws / "output.json").read_text(encoding="utf-8"))
        if plan.get("lab"):
            # INVARIANT: 有练习场的课程，导师审阅命令后跑过 lab verify 并通过，才能发布（D-014）。
            v = self._latest(run, "practice_verify")
            if v is None or v.verdict != "pass":
                raise DomainError(f"练习场还没验证通过：先读 {ws / 'output.md'} 里的命令，再运行 python study.py lab verify {run.id}")
        judge = self._latest(run, "judge")
        plan = {"schema_version": PLAN_SCHEMA_VERSION, "unit": run.unit, **plan,
                "provenance": {"agent": run.agent, "variant": run.variant, "run": run.id,
                               "judge_avg": judge.detail.get("avg") if judge else None,
                               "published": self.clock.now().isoformat(timespec="seconds"),
                               "known_terms": run.input.get("known_terms", [])}}
        return self.course.publish(plan)

    def export(self, agent: str) -> list[dict]:
        """每次运行一行：版本组成、用量、全部评分。进 git，用来在 diff 里看 agent 是怎么变好的。"""
        grades = defaultdict(list)
        for g in self.runs.grades(agent=agent):
            grades[g.run].append({k: v for k, v in dataclasses.asdict(g).items() if k not in ("run", "detail")})
        return [{**{k: v for k, v in dataclasses.asdict(r).items() if k not in ("input", "final_text")},
                 "variant_parts": (self.runs.variant(r.variant) or h.Variant(agent, {})).parts, "grades": grades[r.id]}
                for r in self.runs.runs(agent)]

    # ---------- 按版本对比 ----------

    def report(self, agent: str) -> str:
        runs = self.runs.runs(agent)
        if not runs:
            return "还没有运行记录。"
        grades = defaultdict(list)
        for g in self.runs.grades(agent=agent):
            grades[g.run].append(g)
        by = defaultdict(list)
        for r in runs:
            by[r.variant].append(r)
        out = ["| 版本 | 和上一版的差别 | 运行 | 自动检查 | 评分模型 | 学习结果 | 平均 token | 常见失败 |",
               "|---|---|---|---|---|---|---|---|"]
        prev = None
        for vid, rs in by.items():
            v = self.runs.variant(vid)
            diff = "、".join(v.diff(prev)) if prev and v else "—"
            latest = lambda r, k: next((g for g in reversed(grades[r.id]) if g.grader == k), None)  # noqa: E731
            checks = [g for g in (latest(r, "check") for r in rs) if g]
            judged = [g.detail["avg"] for g in (latest(r, "judge") for r in rs) if g and "avg" in g.detail]
            outcomes = [g for g in (latest(r, "outcome") for r in rs) if g]
            fails = Counter(k for g in checks for k, d in g.dims.items() if d["score"] < 1)
            cell = lambda xs, f: f(xs) if xs else "—"  # noqa: E731
            out.append(f"| `{vid}` | {diff} | {len(rs)} | {cell(checks, lambda c: f'{sum(g.score for g in c) / len(c):.0%}')} | "
                       f"{cell(judged, lambda j: f'{sum(j) / len(j):.2f}')} | {len(outcomes) or '—'} | "
                       f"{int(sum(r.tokens for r in rs) / len(rs))} | "
                       f"{'、'.join(f'{k}×{n}' for k, n in fails.most_common(3)) or '无'} |")
            prev = v
        return "\n".join(out)

    @staticmethod
    def format_run(run: h.Run) -> str:
        lines = [f"运行 {run.id}（版本 {run.variant}）",
                 f"提交：{'是' if run.submitted else '否'} · 用时 {run.seconds}s · token {run.tokens} · "
                 f"成本 ${run.cost_usd} · 工具调用 {run.tool_calls}"]
        if run.errors:
            lines.append("错误：" + "；".join(map(str, run.errors)))
        return "\n".join(lines)

    @staticmethod
    def format_eval(grades: list[h.Grade]) -> str:
        lines = []
        for g in grades:
            if g.grader == "check":
                lines.append(f"\n== 自动检查（{g.score:.0%}）==")
                lines += [f"  {'✓' if d['score'] == 1 else '✗'} {d['reason']}" for d in g.dims.values()]
            elif g.grader == "judge" and g.verdict == "error":
                lines.append(f"  评分模型出错：{g.detail['error']}")
            elif g.grader == "judge":
                lines.append(f"== 评分（{g.actor}）平均 {g.detail['avg']} / 5 ==")
                lines += [f"    {k}: {v['score']} — {v.get('reason', '')}" for k, v in g.dims.items()]
                lines.append(f"  最大问题：{g.detail.get('top_issue', '')}")
                lines += [f"  改 prompt 的建议：{i['what']}" for i in g.issues]
            elif g.grader == "claim_check":
                for c in g.detail["claims"]:
                    verdict = "页面里有，怀疑不成立" if c["found_in_pages"] else "页面里没找到，可能是编造"
                    lines.append(f"    疑点：{c.get('claim', '')}「{c.get('quote', '')}」→ {verdict}")
        return "\n".join(lines)


def _curriculum_row(text: str, topic_id: str) -> str:
    lines = text.splitlines()
    header = next((l for l in lines if l.startswith("| id ")), "")
    row = next((l for l in lines if l.startswith(f"| `{topic_id}` ")), "")
    if not row:
        raise DomainError(f"curriculum.md 里找不到学科 {topic_id}")
    return "\n".join([header, "|" + "---|" * (header.count("|") - 1), row]) if header else row
