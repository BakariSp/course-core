"""harness 的用例（D-022）：跑 agent、给它的工具、评测、发布、按版本对比。

一次运行的工作目录 data/runs/<agent>/<run>/（大文件，Run 和 Grade 的索引在数据库里）：
    brief.md          渲染好的简报（给模型的上下文）
    input.json        冻结的输入数据：生成时学习者会什么、预算、白名单……（永久：课程评测要用）
    fetches.jsonl     agent 读过的页面（含读到的文字，核对出处用）
    grounded.jsonl    修复运行：从上一轮继承的"打开过的页面"（出处可以继续用）
    current.json      修复运行：修复前的课程计划
    context.json      写一节的运行：大纲

备课分步（D-038），每一步是一次运行，input.json 里的 stage 说明是哪一步：
    research（单元知识库，和学习者无关，按单元缓存，D-040）→ outline → section × N（并行）
    → assemble（不调模型，把各节拼成课程计划）→ 产出循环（检验、repair）
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
from studykit.app.ports import (AgentDef, AgentRuntime, Clock, Content, Fetcher, FetchError, IdGen, RawRun, RunStore,
                                 Spec)
from studykit.app.paths import safe_path
from studykit.app.versions import Versions, load_prompt, prompt_texts
from studykit.domain import course_eval, harness as h, plan_check
from studykit.domain.artifact import Finding, blocking, repair_scope, within
from studykit.domain.errors import CourseError, DomainError
from studykit.domain.ids import UnitId
from studykit.domain.plan import (PLAN_SCHEMA_VERSION, assemble, fill_stub, plan_addresses, plan_minutes, replace_part,
                                  sections_of, stubs_of, with_section)

MAX_TEXT = 15000          # fetch_url 一次最多返回多少字
MAX_LINKS = 80
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$")
STAGE_KEYS = ("stage", "repair", "index", "outline_run", "sections", "kb_run")     # 某一步自己的输入；其余是单元的冻结输入
MAX_READING = 12000       # 写一节时，每个依据页面最多给多少字的原文
MAX_READING_TOTAL = 36000


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
        self.versions = Versions(runs, clock)

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
        """一次运行用的版本组合（D-022），每个文本组成的内容都存下来（D-033）。
        上下文配方和工具是代码，存的是源码：改了代码就是新版本（H1）。"""
        spec_rules = [r for s in self.specs for r in (*s.plan_rules, *s.checkpoint_rules.values())]
        src = lambda *objs: "\n\n".join(inspect.getsource(o) for o in objs)  # noqa: E731
        texts = {
            **prompt_texts(load_prompt(agent.dir, agent.spec)),
            "task": agent.file("task").read_text(encoding="utf-8"),
            **{f"stage:{name}": (agent.dir / st["task"]).read_text(encoding="utf-8") for name, st in agent.spec["stages"].items()},
            "tools": "\n\n".join([json.dumps({n: st["tools"] for n, st in agent.spec["stages"].items()}, ensure_ascii=False),
                                   src(Harness.call_tool, Harness._fetch_url, Harness._submit_plan, Harness._submit_research,
                                       Harness._submit_outline,
                                       Harness._submit_section, Harness._submit_repair, plan_check),
                                   src(*spec_rules) if spec_rules else ""]),
            "context": src(Harness.build_input, Harness.render_brief, Harness.render_section, Harness._reading),
        }
        labels = {"model": f"{model['provider']}/{model['id']}" + (f":{model['thinking']}" if model.get("thinking") else ""),
                  "runtime": self.runtime.version}
        return self.versions.record(agent.name, texts, labels)

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
            # WHY: 命令和术语不分学科——Shell 单元学会的 echo、python，备 test 学科的课时也算已经会的
            "known_terms": sorted(self.learner.known()),
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

    def stage(self, agent: AgentDef, name: str) -> tuple[str, list[str]]:
        """一步的说明（接在单元简报后面）和这一步开放的工具。"""
        st = agent.spec["stages"][name]
        return (agent.dir / st["task"]).read_text(encoding="utf-8"), list(st["tools"])

    def _start(self, agent: AgentDef, unit: str, suffix: str, data: dict, brief: str,
               files: dict[str, object] | None = None, grounded: list[dict] = ()) -> tuple[str, Path]:
        """开一次运行的工作目录：冻结的输入、简报、system prompt、继承的出处、这一步要的其他文件。"""
        run_id = self._new_id(agent.name, f"{self.clock.now():%Y%m%d-%H%M%S}-{unit}{suffix}")
        ws = self.dir(agent.name, run_id)
        ws.mkdir(parents=True)
        (ws / "input.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "brief.md").write_text(brief, encoding="utf-8")
        (ws / "system.md").write_text(h.assemble_prompt(load_prompt(agent.dir, agent.spec)), encoding="utf-8")
        for name, value in (files or {}).items():
            (ws / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        if grounded:
            (ws / "grounded.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in grounded), encoding="utf-8")
        return run_id, ws

    def _go(self, agent: AgentDef, stage: str, unit: str, suffix: str, data: dict, brief: str, model: str | None,
            timeout: int, **kw) -> h.Run:
        m = self.model(agent, model)
        variant = self.variant(agent, m)
        started = self.clock.now()
        run_id, ws = self._start(agent, unit, suffix, {**data, "stage": stage} if stage != "plan" else data, brief, **kw)
        raw = self.runtime.run(agent, ws, m, self.stage(agent, stage)[1], timeout, ws / "system.md")
        return self._record(agent.name, run_id, variant, unit, self._input(ws), started, raw, ws)

    def run(self, name: str, unit: str, model: str | None = None, timeout: int = 900) -> h.Run:
        """一次写完整份（调试、对照用；备课走 outline → section）。"""
        agent = self.agent(name)
        data = self.build_input(unit)
        brief = self.render_brief(agent.file("task").read_text(encoding="utf-8"), data) + self.stage(agent, "plan")[0]
        return self._go(agent, "plan", unit, "", data, brief, model, timeout)

    def research(self, name: str, unit: str, model: str | None = None, timeout: int = 900) -> h.Run:
        """调研（D-040 ③）：读讲义，整理单元知识库。简报里没有学习者信息——同一个单元的知识库可以反复用。"""
        agent = self.agent(name)
        full = self.build_input(unit)
        # INVARIANT: 只放和学习者无关的输入；known_terms 为空（检查新词时不按任何学习者算）
        data = {k: full[k] for k in ("unit", "unit_title", "topic_id", "topic_title", "curriculum_row", "hosts",
                                     "existing_nodes", "unit_budget_minutes", "session_minutes", "max_new_terms")}
        data["known_terms"] = []
        nodes = "\n".join(f"- `{nid}` {title}" for nid, title in sorted(data["existing_nodes"].items())) or "（还没有）"
        brief = (self.stage(agent, "research")[0].replace("{unit_id}", data["unit"]).replace("{unit_title}", data["unit_title"])
                 .replace("{topic_id}", data["topic_id"]).replace("{topic_title}", data["topic_title"])
                 .replace("{curriculum_row}", data["curriculum_row"]).replace("{existing_nodes}", nodes))
        return self._go(agent, "research", unit, "-k", data, brief, model, timeout)

    def knowledge(self, name: str, unit: str) -> h.Run | None:
        """这个单元最近一次交出来的知识库（调研运行）。没有就是 None。"""
        runs = [r for r in self.runs.runs(name) if r.unit == unit and r.input.get("stage") == "research" and r.submitted]
        return runs[-1] if runs else None

    def outline(self, name: str, unit: str, model: str | None = None, timeout: int = 900, kb_run: h.Run | None = None) -> h.Run:
        """写大纲（D-038）：练习场 + 每节的桩和各节之间的约定。有知识库时从知识库组装（D-040），读过的页面也继承过来。"""
        agent = self.agent(name)
        data = self.build_input(unit)
        brief = self.render_brief(agent.file("task").read_text(encoding="utf-8"), data) + self.stage(agent, "outline")[0]
        pages: list[dict] = []
        if kb_run is not None:
            kws = self.dir(name, kb_run.id)
            brief += "\n\n# 这个单元的知识库（调研整理，和学习者无关）\n\n" + plan_check.render_by_address(
                json.loads((kws / "output.json").read_text(encoding="utf-8")))
            pages = self._fetched(kws)
            data = {**data, "kb_run": kb_run.id}
        return self._go(agent, "outline", unit, "-o", data, brief, model, timeout, grounded=pages)

    def section(self, outline_run: h.Run, index: int, model: str | None = None, timeout: int = 900) -> h.Run:
        """写第 index 节：只拿大纲（练习场、每一节的桩）和这一节要依据的页面原文。
        INVARIANT: 不依赖别的节写成什么样——各节可以同时写。前后衔接靠大纲里每节的任务，衔接不上由拼起来之后的练习场实跑发现。"""
        agent = self.agent(outline_run.agent)
        outline = json.loads((self.dir(agent.name, outline_run.id) / "output.json").read_text(encoding="utf-8"))
        base = self._base(outline_run.input)
        pages = self._fetched(self.dir(agent.name, outline_run.id))
        brief = (self.render_brief(agent.file("task").read_text(encoding="utf-8"), base)
                 + self.render_section(self.stage(agent, "section")[0], outline, index, pages))
        data = {**base, "index": index, "outline_run": outline_run.id}
        return self._go(agent, "section", outline_run.unit, f"-s{index + 1}", data, brief, model, timeout,
                        files={"context.json": {"outline": outline}}, grounded=pages)

    def assemble(self, outline_run: h.Run, section_runs: list[h.Run]) -> h.Run:
        """把大纲和写好的各节拼成课程计划，记成一次运行（不调模型）。产出循环从这一次开始检验。"""
        agent = self.agent(outline_run.agent)
        load = lambda r: json.loads((self.dir(agent.name, r.id) / "output.json").read_text(encoding="utf-8"))  # noqa: E731
        plan = assemble(load(outline_run), [load(r) for r in section_runs])
        base = self._base(outline_run.input)
        pages = [f for r in (outline_run, *section_runs) for f in self._fetched(self.dir(agent.name, r.id))]
        started = self.clock.now()
        data = {**base, "stage": "assemble", "outline_run": outline_run.id, "sections": [r.id for r in section_runs]}
        brief = (self.dir(agent.name, outline_run.id) / "brief.md").read_text(encoding="utf-8")
        run_id, ws = self._start(agent, outline_run.unit, "-a", data, brief, grounded=pages)
        (ws / "output.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_plan_md(plan, base["session_minutes"]), encoding="utf-8")
        return self._record(agent.name, run_id, self.variant(agent, self.model(agent)), outline_run.unit, data, started,
                            RawRun([], 0, 0.0), ws)

    @staticmethod
    def _base(inp: dict) -> dict:
        return {k: v for k, v in inp.items() if k not in STAGE_KEYS}

    @staticmethod
    def render_section(task: str, outline: dict, index: int, pages: list[dict]) -> str:
        stubs = stubs_of(outline)
        stub = stubs[index]
        dump = lambda v: "```json\n" + json.dumps(v, ensure_ascii=False, indent=1) + "\n```"  # noqa: E731

        def block(i: int, st: dict) -> str:
            where = "👉 这一节" if i == index else "学习者已经学完" if i < index else "还没学到"
            lines = [f"### 第 {i + 1} 节「{st.get('title', '')}」（{where}）",
                     f"- 目标：{st.get('goal', '')}；任务：{st.get('mission', '')}",
                     "- 讲清的要点：" + ("；".join(st.get("teaches") or []) or "（无）")]
            if i <= index:           # 前面几节的新词定义和练习场状态：这一节可以直接用、可以依赖
                lines += [f"- 新词 {t.get('term', '')}：{t.get('explain', '')}" for t in st.get("terms") or []]
                lines += [f"- 做完后练习场满足：{k.get('desc', '')} `{json.dumps({x: y for x, y in k.items() if x != 'desc'}, ensure_ascii=False)}`"
                          for k in st.get("state_after") or []]
            return "\n".join(lines)

        head = {k: outline.get(k) for k in ("title", "summary", "outcomes") if outline.get(k)}
        return (task.replace("{number}", str(index + 1)).replace("{title}", str(stub.get("title", "")))
                .replace("{check_type}", str((stub.get("check") or {}).get("type", "")))
                .replace("{stub}", dump(stub))
                .replace("{outline}", dump(head) + "\n\n" + "\n\n".join(block(i, st) for i, st in enumerate(stubs)))
                .replace("{lab}", dump(outline.get("lab") or {}))
                .replace("{reading}", Harness._reading(pages, stub.get("reading") or [])))

    @staticmethod
    def _reading(pages: list[dict], urls: list[str]) -> str:
        """大纲给这一节列的依据页面 → 读到的原文（按段拼回去，截断）。WHY: 写一节的运行不用重新抓一遍页面。"""
        out, total = [], 0
        for url in urls:
            key = plan_check.url_key(url)
            chunks = sorted({f.get("start", 0): f.get("text", "") for f in pages
                             if key in (plan_check.url_key(f["url"]), plan_check.url_key(f.get("final_url", f["url"])))}.items())
            text = "".join(t for _, t in chunks)[:MAX_READING]
            if not text or total >= MAX_READING_TOTAL:
                out.append(f"### {url}\n\n（没有读到原文，需要的话用 fetch_url 打开）")
                continue
            text = text[:MAX_READING_TOTAL - total]
            total += len(text)
            out.append(f"### {url}\n\n{text}")
        return "\n\n".join(out) or "（大纲没有列依据页面）"

    def repair(self, prev: h.Run, findings: list[Finding], model: str | None = None, timeout: int = 900) -> h.Run:
        """定点修复（D-035）：同一个 agent、同一份冻结的输入，只重写发现指向的部分。修复也是一次运行，能回放。

        INVARIANT: 修复只能提交"要重写的部分"里的地址（submit_repair 在结构上保证），其余部分逐字不变。
        """
        agent = self.agent(prev.agent)
        prev_ws = self.dir(prev.agent, prev.id)
        if not (prev_ws / "output.json").exists():
            raise DomainError(f"{prev.id} 没有交出课程计划，没有可修的东西")
        plan = json.loads((prev_ws / "output.json").read_text(encoding="utf-8"))
        info = prev.input.get("repair") or {}
        rnd = int(info.get("round", 0)) + 1
        data = {**self._base(prev.input),
                "repair": {"of": prev.id, "root": info.get("root", prev.id), "round": rnd, "allowed": repair_scope(findings),
                           "findings": [f.as_dict() for f in findings]}}
        brief = self.render_repair(self.stage(agent, "repair")[0], data, plan)
        return self._go(agent, "repair", prev.unit, f"-r{rnd}", data, brief, model, timeout,
                        files={"current.json": plan}, grounded=self._fetched(prev_ws))

    @staticmethod
    def render_repair(task: str, data: dict, plan: dict) -> str:
        titles = {f"/sections/{i}": f"第 {i + 1} 节 {s.get('title', '')}" for i, s in enumerate(sections_of(plan))}
        found = []
        for f in data["repair"]["findings"]:
            sev = "阻断" if f["severity"] == "block" else "警告"
            found.append(f"- `{f['address'] or '（整份）'}`（{f['evaluator']}，{sev}）：{f['what']}")
            if f.get("evidence"):
                found.append("  证据：\n  ```\n  " + f["evidence"][-800:].replace("\n", "\n  ") + "\n  ```")
        allowed = [(f"- `{a}`" + (f"（{titles[a]}）" if a in titles else "")) if a
                   else '- 整份计划（地址写 ""，给出完整的课程计划）' for a in data["repair"]["allowed"]]
        # WHY: 用 replace 而不是 format：课程计划的 JSON 里全是花括号
        return (task.replace("{unit_id}", data["unit"]).replace("{unit_title}", data["unit_title"])
                .replace("{findings}", "\n".join(found)).replace("{allowed}", "\n".join(allowed))
                .replace("{plan}", plan_check.render_by_address(plan)))

    def _record(self, name: str, run_id: str, variant: h.Variant, unit: str, data: dict, started, raw, ws: Path) -> h.Run:
        s = h.summarize(raw.steps)
        run = h.Run(run_id, name, variant.id, unit, self.learner_id, "batch", started.isoformat(timespec="seconds"),
                    data, raw.seconds, raw.exit_code, (ws / "output.json").exists(), s["tokens"], s["cost_usd"],
                    s["tool_calls"], s["errors"], s["final_text"])
        self.runs.add_run(run, variant, raw.steps)
        return run

    def _new_id(self, agent: str, base: str) -> str:
        """同一秒里开了两次运行（修复失败后马上重新生成）时加序号，不覆盖已有的运行目录。"""
        run_id, n = base, 1
        while self.dir(agent, run_id).exists():
            n += 1
            run_id = f"{base}-{n}"
        return run_id

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
        cp_fields = {k: v for s in self.specs for k, v in s.checkpoint_fields.items()}
        plan_fields = {k: v for s in self.specs for k, v in s.plan_fields.items()}
        schema = plan_check.plan_schema(types, cp_fields, plan_fields)
        tools = {
            "fetch_url": {"description": "抓取一个白名单网站的网页，返回正文（纯文本）和页面里的白名单链接。用它查课程官网、讲义、视频列表。",
                          "parameters": {"type": "object", "properties": {
                              "url": {"type": "string", "description": "要抓取的网页地址（必须在白名单网站内）"},
                              "start": {"type": "integer", "description": "从正文第几个字开始读，默认 0。长页面会分段返回，结果末尾会告诉你下一段的 start。"}},
                              "required": ["url"]}},
            "submit_plan": {"description": "提交这个单元的课程计划（结构化）。环境会检查时间预算、每节的新词数、检查点、知识节点、链接是否打开过；不通过会返回错误，改完再提交。",
                            "parameters": {"type": "object", "properties": {"plan": schema}, "required": ["plan"]}},
            "submit_research": {"description": "提交这个单元的知识库（和学习者无关）。环境会检查知识点的 id、定义、要点、依据页面是否打开过；不通过会返回错误，改完再提交。",
                                "parameters": {"type": "object", "properties": {"knowledge": plan_check.kb_schema()},
                                               "required": ["knowledge"]}},
            "submit_outline": {"description": "提交这个单元的大纲：练习场和每一节的桩。环境会检查时间预算、每节新词数、知识节点、练习场、链接是否打开过；不通过会返回错误，改完再提交。",
                               "parameters": {"type": "object", "properties": {"outline": plan_check.outline_schema(
                                   types, plan_fields, (cp_fields.get("checks") or {}).get("items"))},
                                              "required": ["outline"]}},
            "submit_section": {"description": "提交简报里要写的那一节（一个小节对象）。环境把它放进大纲、接在前面几节后面检查；不通过会返回错误，改完再提交。",
                               "parameters": {"type": "object", "properties": {"section": plan_check.section_schema(types, cp_fields, plan_fields)},
                                              "required": ["section"]}},
            "submit_repair": {"description": "定点修复时提交：只给出简报里「要重写的部分」，每个地址一项，value 是这一部分修改后的完整内容。"
                                             "环境把它们换进原计划（其余部分不变）再检查；不通过会返回错误，改完再提交。",
                              "parameters": {"type": "object", "properties": {"parts": {"type": "array", "items": {
                                  "type": "object", "properties": {
                                      "address": {"type": "string", "description": "要重写的地址，如 /sections/3、/lab；整份重写写空字符串"},
                                      "value": {"type": "object", "description": "小节地址给整个小节对象；/lab 给 lab 对象；"
                                                "其他顶层字段（/sources、/outcomes、/nodes、/title……）给 {\"<字段名>\": 新值}；整份重写给完整的课程计划"}},
                                  "required": ["address", "value"]}}}, "required": ["parts"]}},
        }
        return [{"name": n, **t} for n, t in tools.items() if names is None or n in names]

    def call_tool(self, ws: Path, name: str, args: dict) -> str:
        if name == "fetch_url":
            return self._fetch_url(ws, args.get("url", ""), args.get("start", 0))
        if name == "submit_plan":
            return self._submit_plan(ws, args.get("plan") or {})
        if name == "submit_repair":
            return self._submit_repair(ws, args.get("parts"))
        if name == "submit_research":
            return self._submit_research(ws, args.get("knowledge") or {})
        if name == "submit_outline":
            return self._submit_outline(ws, args.get("outline") or {})
        if name == "submit_section":
            return self._submit_section(ws, args.get("section") or {})
        raise ToolError(f"没有这个工具：{name}")

    def _log(self, ws: Path, name: str, rec: dict) -> None:
        with (ws / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    @staticmethod
    def _fetched(ws: Path) -> list[dict]:
        """这次运行"打开过"的页面：从上一轮继承的 + 这一轮读的（只算打开成功的）。"""
        return [f for f in _read_jsonl(ws / "grounded.jsonl") + _read_jsonl(ws / "fetches.jsonl") if f.get("ok")]

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
        for f in self._fetched(ws):
            grounded |= {plan_check.url_key(f["url"]), plan_check.url_key(f.get("final_url", f["url"]))}
        types = (*plan_check.CORE_CHECKPOINT_TYPES, *(k for s in self.specs for k in s.checkpoints))
        return plan_check.PlanLimits(i["unit"], i["unit_budget_minutes"], i["session_minutes"], i["max_new_terms"],
                                     {t.lower() for t in i["known_terms"]}, dict(i["existing_nodes"]), grounded, tuple(types))

    def plan_findings(self, ws: Path, plan: dict) -> list[Finding]:
        """环境检查（D-035）：每个问题带地址。agent 提交时看到的是它们的文字。"""
        return plan_check.plan_findings(plan, self.limits(ws), [r for s in self.specs for r in s.plan_rules],
                                        {k: r for s in self.specs for k, r in s.checkpoint_rules.items()})

    def _submit_plan(self, ws: Path, plan: dict) -> str:
        errors = [f.what for f in self.plan_findings(ws, plan)]
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "minutes": plan_minutes(plan or {})})
        if errors:
            raise ToolError("课程计划没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_plan_md(plan, self._input(ws)["session_minutes"]), encoding="utf-8")
        return "已收到，检查通过。任务完成，不需要再做别的。"

    def _submit_research(self, ws: Path, kb: dict) -> str:
        errors = [f.what for f in plan_check.kb_findings(kb, self.limits(ws))]
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "points": len((kb or {}).get("points") or [])})
        if errors:
            raise ToolError("知识库没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_by_address(kb), encoding="utf-8")
        return "已收到，知识库检查通过。任务完成，不需要再做别的。"

    def _submit_outline(self, ws: Path, outline: dict) -> str:
        errors = [f.what for f in plan_check.outline_findings(outline, self.limits(ws), [r for s in self.specs for r in s.plan_rules])]
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "minutes": plan_minutes(outline or {})})
        if errors:
            raise ToolError("大纲没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_by_address(outline), encoding="utf-8")
        return "已收到，大纲检查通过。任务完成，不需要再做别的。"

    def _submit_section(self, ws: Path, section: dict) -> str:
        i = self._input(ws)["index"]
        ctx = json.loads((ws / "context.json").read_text(encoding="utf-8"))
        stub = stubs_of(ctx["outline"])[i]
        if not isinstance(section, dict):
            raise ToolError("section 要是一个小节对象")
        merged = fill_stub(stub, section)
        partial = with_section(ctx["outline"], i, section)
        found = plan_check.section_findings(merged, stub, i) + [
            f for f in self.plan_findings(ws, partial) if f.address == "" or within(f.address, f"/sections/{i}")]
        errors = [f.what for f in found]
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "index": i})
        if errors:
            raise ToolError("这一节没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
        (ws / "output.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        (ws / "output.md").write_text(plan_check.render_by_address(partial), encoding="utf-8")
        return "已收到，这一节检查通过。任务完成，不需要再做别的。"

    def _submit_repair(self, ws: Path, parts) -> str:
        info = self._input(ws).get("repair")
        if not info:
            raise ToolError("这次运行不是修复，用 submit_plan 提交")
        allowed = info["allowed"]
        if not isinstance(parts, list) or not parts or not all(isinstance(p, dict) for p in parts):
            raise ToolError("parts 要是非空数组，每项是 {address, value}")
        plan = json.loads((ws / "current.json").read_text(encoding="utf-8"))
        seen = set()
        for p in parts:
            addr, value = str(p.get("address") or ""), p.get("value")
            if "" not in allowed and addr not in allowed:
                raise ToolError(f"{addr or '（整份）'} 不在要重写的部分里，只能改：{', '.join(allowed)}")
            name = addr.strip("/")
            if addr and not addr.startswith("/sections/") and addr != "/lab" and isinstance(value, dict) and set(value) == {name}:
                value = value[name]                           # 顶层字段包在 {"<字段名>": 新值} 里
            try:
                plan = replace_part(plan, addr, value)
            except CourseError as e:
                raise ToolError(str(e)) from e
            seen.add(addr)
        missing = [] if "" in allowed else [a for a in allowed if a not in seen]
        if missing:
            raise ToolError("这些部分还没有给出修改后的内容：" + ", ".join(missing))
        # 只算落在要重写的部分里（或整份）的问题：别处原有的问题这一轮改不了，由下一轮检验再指出
        errors = [f.what for f in self.plan_findings(ws, plan)
                  if any(within(f.address, a) or within(a, f.address) for a in allowed)]
        self._log(ws, "submissions.jsonl", {"accepted": not errors, "errors": errors, "parts": sorted(seen)})
        if errors:
            raise ToolError("修改后的计划没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
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
        found: list[Finding] = []
        add("submitted", "按时提交了结果", out.exists(), "" if out.exists() else f"exit={run.exit_code}")
        if out.exists():
            plan = json.loads(out.read_text(encoding="utf-8"))
            limits = self.limits(ws)
            found = self.plan_findings(ws, plan)
            add("plan_valid", "课程计划通过环境检查", not found, "；".join(f.what for f in found))
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
            pages = {plan_check.url_key(f["url"]) for f in self._fetched(ws)}
            add("researched", "至少读了 2 个页面再写", len(pages) >= 2, f"打开成功 {len(pages)} 个页面")
            md = (ws / "output.md").read_text(encoding="utf-8").lower()
            case = self._case(agent, run.unit)
            for s in case.get("must_mention") or []:
                add(f"mention:{s}", f"提到「{s}」", s.lower() in md)
                if s.lower() not in md and secs:
                    # WHY: "全文要提到 X"没有天然的位置。指到第 1 节（认识练习场、确认环境），不指整份——整份的发现会放开整份重写
                    found.append(Finding("/sections/0", f"全文没有提到「{s}」（评测用例要求）：在第 1 节讲环境的地方补上", "check"))
            for s in case.get("must_not_mention") or []:
                add(f"avoid:{s}", f"没有出现「{s}」", s.lower() not in md)
        score, dims = h.check_results(items)
        version = h.content_hash(_src(Harness._check), self._case(agent, run.unit))
        # 发现 = 环境检查的（带地址）+ 用例要求的（指到第 1 节）+ 其余没过的检查项（没有位置，指整份）
        found += [Finding("", d["reason"], "check") for k, d in dims.items()
                  if d["score"] < 1 and k != "plan_valid" and not k.startswith("mention:")]
        return self._grade(run, "check", version, "system", score=score, verdict="pass" if score == 1 else "fail", dims=dims,
                           detail={"findings": [f.as_dict() for f in found]})

    def _case(self, agent: AgentDef, unit: str) -> dict:
        path = agent.dir / agent.spec["eval"]["cases"]
        cases = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cases") or [] if path.exists() else []
        return next((c for c in cases if c.get("unit") == unit), {})

    def _judge(self, run: h.Run, agent: AgentDef, ws: Path) -> list[h.Grade]:
        rubric = (agent.dir / agent.spec["eval"]["rubric"]).read_text(encoding="utf-8")
        jm = agent.spec["eval"]["judge_model"]
        fetched = self._fetched(ws)
        (ws / "judge_input.md").write_text("\n\n".join([
            "# 评分标准\n\n" + rubric,
            "# 助教拿到的简报\n\n" + (ws / "brief.md").read_text(encoding="utf-8"),
            "# 助教实际打开过的页面\n\n" + ("\n".join(f"- {f.get('title') or '(无标题)'} — {f['url']}" for f in fetched) or "（没有）"),
            "# 助教写的课程计划\n\n" + (ws / "output.md").read_text(encoding="utf-8"),
        ]), encoding="utf-8")
        data = h.parse_judge(self.runtime.complete(jm, h.JUDGE_SYSTEM, ws / "judge_input.md", ws, 600), h.rubric_ids(rubric))
        model = f"{jm['provider']}/{jm['id']}"
        jv = self.versions.record(f"{agent.name}#judge", {"system": h.JUDGE_SYSTEM, "rubric": rubric}, {"model": model})
        judge = self._grade(run, "judge", jv.id, model,
                            score=round((data["avg"] - 1) / 4, 3), dims=data["scores"],
                            issues=[{"layer": "prompt", "what": data.get("suggestion", "")}] if data.get("suggestion") else [],
                            detail={"avg": data["avg"], "top_issue": data.get("top_issue", "")})
        checks = h.verify_claims(data.get("suspect_claims") or [], "\n".join(f.get("text", "") for f in fetched))
        claim = self._grade(run, "claim_check", _src(h.verify_claims), "system",
                            score=sum(c["found_in_pages"] for c in checks) / len(checks) if checks else None,
                            refs=[judge.id], detail={"claims": checks})
        return [judge, claim]

    def reviewer(self, run: h.Run) -> h.Grade:
        """评审模型（D-035）：兼任命令的安全闸门（D-030）和质量评审，给出带地址的发现。同一次运行只评一次（结果存在 Grade 里）。"""
        done = self._latest(run, "reviewer")
        if done is not None:
            return done
        agent = self.agent(run.agent)
        ws = self.dir(run.agent, run.id)
        plan = json.loads((ws / "output.json").read_text(encoding="utf-8"))
        conf = agent.spec["review"]
        system = (agent.dir / conf["prompt"]).read_text(encoding="utf-8")
        rubric = (agent.dir / agent.spec["eval"]["rubric"]).read_text(encoding="utf-8")
        fetched = self._fetched(ws)
        (ws / "review_input.md").write_text("\n\n".join([
            "# 评分标准\n\n" + rubric,
            "# 助教拿到的简报\n\n" + (ws / "brief.md").read_text(encoding="utf-8"),
            "# 助教实际打开过的页面\n\n" + ("\n".join(f"- {f.get('title') or '(无标题)'} — {f['url']}" for f in fetched) or "（没有）"),
            "# 课程计划（按地址）\n\n" + plan_check.render_by_address(plan),
        ]), encoding="utf-8")
        model = f"{conf['model']['provider']}/{conf['model']['id']}"
        rv = self.versions.record(f"{agent.name}#reviewer", {"system": system, "rubric": rubric}, {"model": model})
        try:
            found = h.parse_review(self.runtime.complete(conf["model"], system, ws / "review_input.md", ws, 600),
                                   plan_addresses(plan))
        except Exception as e:  # noqa: BLE001  评审失败 = 没被放行：安全闸门不能默认通过
            found = [Finding("", f"评审模型没有给出可用的结论：{e}", "reviewer_safety")]
        return self._grade(run, "reviewer", rv.id, model, verdict="fail" if blocking(found) else "pass",
                           detail={"findings": [f.as_dict() for f in found]})

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
        # WHY: 每次检验一个新目录。同一次运行被检验两次（面板和命令行各开了一次）时，共用目录会互相删掉对方跑到一半的练习场，
        # 把好的课程判成"按参考做法做完仍然没通过"（2026-09-26 tools-03-debug 第 2 轮就是这样被误判的）
        workdir = ws / "raw" / f"lab-verify-{self.ids.new()}"
        result = practice.verify(json.loads((ws / "output.json").read_text(encoding="utf-8")), workdir)
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
        # INVARIANT: 只有产出循环放行的运行能发布（D-035）：自动检查、评审模型（含命令安全）、练习场实跑都没有阻断。
        loop = self._latest(run, "loop")
        if loop is None or loop.verdict != "accepted":
            raise DomainError(f"这次运行没有通过产出循环，不能发布：{run.id}")
        ws = self.dir(run.agent, run.id)
        plan = json.loads((ws / "output.json").read_text(encoding="utf-8"))
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
