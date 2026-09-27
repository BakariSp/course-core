"""课程计划的硬性检查（agent 提交时就跑，不通过退回去让它改）+ 给导师审阅的 Markdown 版本。

每个问题都是一条带地址的发现（D-035），修复时只改那一处。

core 只检查学什么都成立的东西：预算、每节新词数、必填项、知识节点、检查点的通用字段、出处。
学科相关的规则（cs-practice：练习场、"用到的命令必须先声明"）由学科 spec 通过 rules 传进来。
INVARIANT: 时间预算、每节新词数是硬约束，超了不收（F-014、F-019）。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable

from studykit.domain.artifact import Finding, checkpoint_address, section_address
from studykit.domain.ids import NODE_ID_RE
from studykit.domain.plan import plan_minutes, plan_parts, plan_sessions, sections_of, stubs_of

# WHY: 中文里链接常被全角括号、引号、句号包着（如「（https://…）」），这些字符不能算进 URL，
# 否则核对出处和检查能否打开都会误报。整个环境只用这一个提取函数。
URL_RE = re.compile(r"https?://[^\s)>\]\"'`（）「」『』，。；：、！？《》【】]+")


def extract_urls(text: str) -> set[str]:
    return {u.rstrip(".,;:") for u in URL_RE.findall(text or "")}


def url_key(url: str) -> str:
    """比较链接时用的形式：去掉 #锚点、结尾的斜杠和标点。"""
    return url.split("#")[0].rstrip(".,;").rstrip("/")


def plan_urls(plan: dict) -> set[str]:
    """计划里引用的链接。练习场文件里的链接是故事道具（比如日志里的请求地址），不算引用。"""
    return extract_urls(json.dumps({k: v for k, v in plan.items() if k != "lab"}, ensure_ascii=False))


@dataclass
class PlanLimits:
    """检查一份计划需要知道的外部事实（生成时的快照）。"""
    unit: str
    unit_budget_minutes: int = 180
    session_minutes: int = 45
    max_new_terms: int = 5
    known_terms: set[str] = field(default_factory=set)         # 已掌握的术语（小写标题 / 节点 id，D-017）
    existing_nodes: dict[str, str] = field(default_factory=dict)   # 知识图里已有的节点 id → 标题（D-020）
    grounded: set[str] = field(default_factory=set)            # 本次运行真正打开过的页面（url_key 形式）
    checkpoint_types: tuple[str, ...] = ("choice", "fill")

    @property
    def topic(self) -> str:
        return self.unit.split("-")[0]


EVALUATOR = "plan_check"

# 学科规则：(计划, 限制) → 发现（自己给地址）；检查点规则：(位置描述, 检查点) → 文字（地址由调用方给）
PlanRule = Callable[[dict, PlanLimits], list[Finding]]
CheckpointRule = Callable[[str, dict], list[str]]


def _check_checkpoint(where: str, c: dict, node_ids: set[str], limits: PlanLimits,
                      rules: dict[str, CheckpointRule]) -> list[str]:
    kind = c.get("type")
    if kind not in limits.checkpoint_types:
        return [f"{where}：type 只能是 {' / '.join(limits.checkpoint_types)}"]
    errors = []
    if not str(c.get("prompt") or "").strip():
        errors.append(f"{where}：缺少 prompt")
    hints = [h for h in c.get("hints") or [] if str(h).strip()]
    if len(hints) != 3:
        errors.append(f"{where}：hints 要正好 3 级（方向 → 关键概念 → 接近答案），现在 {len(hints)} 条")
    if not str(c.get("explain") or "").strip():
        errors.append(f"{where}：缺少 explain（做对之后的解析）")
    if not c.get("concept"):
        errors.append(f"{where}：缺少 concept（这道题检验哪个知识节点）")
    elif c["concept"] not in node_ids:
        errors.append(f"{where}：concept {c['concept']} 不在知识图里，也没有在 nodes 里提议")
    if kind == "choice":
        opts = c.get("options") or []
        letters = {chr(65 + i) for i in range(len(opts))}
        ans = c.get("answer")
        ans = ans if isinstance(ans, list) else [ans]
        if len(opts) < 2:
            errors.append(f"{where}：选择题至少 2 个选项")
        if not ans or not all(str(a).upper() in letters for a in ans):
            errors.append(f"{where}：answer 要是选项字母（{'、'.join(sorted(letters)) or '无'}）")
    elif kind == "fill":
        blanks = str(c.get("prompt") or "").count("____")
        accept = c.get("accept") or []
        if not blanks or blanks != len(accept) or not all(accept):
            errors.append(f"{where}：题干里 ____ 的个数（{blanks}）要和 accept 的组数（{len(accept)}）一样，每组至少一个答案")
    elif kind in rules:
        errors += rules[kind](where, c)
    for t in c.get("traps") or []:
        if not all(str(t.get(k) or "").strip() for k in ("when", "symptom", "cause", "fix")):
            errors.append(f"{where}：每个 trap 都要写清 when / symptom / cause / fix")
    return errors


def plan_findings(plan: dict, limits: PlanLimits, rules: list[PlanRule] = (),
                  checkpoint_rules: dict[str, CheckpointRule] | None = None) -> list[Finding]:
    """环境检查（D-035）：每个问题都带地址，修复时只改那一处。"""
    if not isinstance(plan, dict):
        return [Finding("", "plan 必须是一个对象", EVALUATOR)]
    out, node_ids = _unit_findings(plan, limits)
    add = lambda address, what: out.append(Finding(address, what, EVALUATOR))  # noqa: E731
    sections = sections_of(plan)

    known = set(limits.known_terms)
    for i, s in enumerate(sections):
        at = section_address(i)
        name = f"第 {i + 1} 节「{s.get('title', '')}」"
        m = s.get("minutes")
        if not isinstance(m, int) or isinstance(m, bool) or m <= 0:
            add(at, f"{name}：minutes 必须是正整数")
        elif m > limits.session_minutes:
            add(at, f"{name}：{m} 分钟超过单次学习上限 {limits.session_minutes} 分钟，拆成几节")
        for key in ("goal", "explain"):
            if not str(s.get(key) or "").strip():
                add(at, f"{name}：缺少 {key}")
        if len(str(s.get("explain") or "")) < 120:
            add(at, f"{name}：explain 太短，要把这一节讲清楚，学习者只读这里就能学会")
        if not s.get("try"):
            add(at, f"{name}：至少要有一条动手（try）")
        for t in s.get("try") or []:
            if not str(t.get("command") or "").strip() or not str(t.get("expect") or "").strip():
                add(at, f"{name}：每条动手都要有 command 和 expect")
        if s.get("pitfalls") or s.get("sources"):
            add(at, f"{name}：不要再写 pitfalls / sources。常见坑写进检查点的 traps（答错时才出现），出处写在单元的 sources 里")
        terms = s.get("terms") or []
        new_terms = [t for t in terms if str(t.get("id")) not in known and str(t.get("term", "")).lower() not in known]
        if len(new_terms) > limits.max_new_terms:
            add(at, f"{name}：新词 {len(new_terms)} 个，超过每节上限 {limits.max_new_terms} 个。"
                    "新东西太多学习者记不住：把这一节拆成两节，或者把次要的词挪到后面的节")
        for t in terms:
            if not all(str(t.get(k) or "").strip() for k in ("id", "term", "explain")):
                add(at, f"{name}：每个新词都要有 id / term / explain")
            elif t["id"] not in node_ids:
                add(at, f"{name}：新词 {t['term']} 的 id {t['id']} 不在知识图里，要在 nodes 里提议")
        known |= {str(t.get("id")) for t in terms} | {str(t.get("term", "")).lower() for t in terms}
        items = s.get("checkpoint") or []
        if not 1 <= len(items) <= 3:
            add(at, f"{name}：检查点要 1–3 道题，现在 {len(items)} 道")
        for j, c in enumerate(items):
            for what in _check_checkpoint(f"{name} 检查点第 {j + 1} 题", c, node_ids, limits, checkpoint_rules or {}):
                add(checkpoint_address(i, j), what)
    for rule in rules:
        out += rule(plan, limits)
    return out


def _unit_findings(plan: dict, limits: PlanLimits) -> tuple[list[Finding], set[str]]:
    """课程计划和大纲共用的单元级检查：标题、出处、知识节点、outcomes、总时长、链接是否打开过。返回（发现，可用的节点 id）。"""
    out: list[Finding] = []
    add = lambda address, what: out.append(Finding(address, what, EVALUATOR))  # noqa: E731
    for key in ("title", "summary"):
        if not str(plan.get(key) or "").strip():
            add("/" + key, f"缺少 {key}")
    if not plan.get("sources"):
        add("/sources", "至少要有一个出处（sources，整个单元列一次）")
    if not sections_of(plan):
        add("", "至少要有一个 part 和一个 section")

    proposed = {n.get("id"): n for n in plan.get("nodes") or []}
    for nid, n in proposed.items():
        if not NODE_ID_RE.match(str(nid or "")):
            add("/nodes", f"nodes：id 格式不对：{nid}（小写，点分层，如 {limits.topic}.cmd.grep）")
        elif not nid.startswith(limits.topic + "."):
            add("/nodes", f"nodes：{nid} 要以学科 {limits.topic}. 开头")
        if nid in limits.existing_nodes:
            add("/nodes", f"nodes：{nid} 已经在知识图里了，直接用，不要重复提议")
        if not str(n.get("desc") or "").strip():
            add("/nodes", f"nodes：{nid} 缺少 desc")
    node_ids = set(limits.existing_nodes) | set(proposed)
    for nid, n in proposed.items():
        for r in n.get("requires") or []:
            if r not in node_ids:
                add("/nodes", f"nodes：{nid} 的先修 {r} 不存在（知识图里没有，也没有提议）")

    total = plan_minutes(plan)
    if total > limits.unit_budget_minutes:
        add("", f"总时长 {total} 分钟超过单元预算 {limits.unit_budget_minutes} 分钟。"
                "砍掉次要的小节放进 later，不要压缩每节的分钟数来凑数")
    outcomes = plan.get("outcomes") or []
    if not 3 <= len(outcomes) <= 5:
        add("/outcomes", f"outcomes 要 3-5 条，现在是 {len(outcomes)} 条")
    for url in sorted(plan_urls(plan)):
        if url_key(url) not in limits.grounded:
            add("", f"链接没有打开过：{url}。只能引用你用 fetch_url 打开过的页面")
    return out, node_ids


# ---------- 大纲（PRD_V2 阶段 B，D-038） ----------

def outline_findings(outline: dict, limits: PlanLimits, rules: list[PlanRule] = ()) -> list[Finding]:
    """大纲的检查：单元级检查 + 每节的桩（时长、目标、任务、新词上限、检查点检验什么、依据哪几页）+ 学科规则（练习场）。"""
    if not isinstance(outline, dict):
        return [Finding("", "outline 必须是一个对象", EVALUATOR)]
    out, node_ids = _unit_findings(outline, limits)
    add = lambda address, what: out.append(Finding(address, what, EVALUATOR))  # noqa: E731
    stubs = stubs_of(outline)
    if stubs and not 4 <= len(stubs) <= 8:
        add("", f"一共 4–8 节，现在 {len(stubs)} 节")
    known = set(limits.known_terms)
    practice = [t for t in limits.checkpoint_types if t not in CORE_CHECKPOINT_TYPES]
    for i, s in enumerate(stubs):
        at, name = section_address(i), f"第 {i + 1} 节「{s.get('title', '')}」"
        m = s.get("minutes")
        if not isinstance(m, int) or isinstance(m, bool) or m <= 0:
            add(at, f"{name}：minutes 必须是正整数")
        elif m > limits.session_minutes:
            add(at, f"{name}：{m} 分钟超过单次学习上限 {limits.session_minutes} 分钟，拆成几节")
        for key in ("title", "goal", "mission"):
            if not str(s.get(key) or "").strip():
                add(at, f"{name}：缺少 {key}")
        terms = s.get("terms") or []
        new = [t for t in terms if str(t.get("id")) not in known and str(t.get("term", "")).lower() not in known]
        if len(new) > limits.max_new_terms:
            add(at, f"{name}：新词 {len(new)} 个，超过每节上限 {limits.max_new_terms} 个：拆节，或者挪到后面的节")
        for t in terms:
            if not all(str(t.get(k) or "").strip() for k in ("id", "term", "explain")):
                add(at, f"{name}：每个新词都要有 id、term 和 explain（一句话定义；各节都用这个定义，写节的人不能改）")
            elif t["id"] not in node_ids:
                add(at, f"{name}：新词 {t['term']} 的 id {t['id']} 不在知识图里，要在 nodes 里提议")
        known |= {str(t.get("id")) for t in terms} | {str(t.get("term", "")).lower() for t in terms}
        check = s.get("check") or {}
        if check.get("type") not in limits.checkpoint_types:
            add(at, f"{name}：check.type 只能是 {' / '.join(limits.checkpoint_types)}")
        if not str(check.get("what") or "").strip():
            add(at, f"{name}：check.what 要写清检查点让学习者做到 / 答出什么")
        if not s.get("reading"):
            add(at, f"{name}：reading 至少列一个写这一节要依据的页面（打开过的）")
        teaches = [x for x in s.get("teaches") or [] if str(x).strip()]
        if not 1 <= len(teaches) <= 4:
            add(at, f"{name}：teaches 写 1–4 个这一节讲清的要点（后面的节会直接引用，不再重讲），现在 {len(teaches)} 个")
        if check.get("type") in practice and not s.get("state_after"):
            add(at, f"{name}：练习场任务的节要写 state_after——做完这一节后练习场满足的断言。后面的节只能依赖这些断言")
        for k in s.get("state_after") or []:
            if not str(k.get("desc") or "").strip():
                add(at, f"{name}：state_after 的每条断言都要有 desc（给学习者和写后面几节的人看）")
    if practice and stubs:
        hands_on = [s for s in stubs if (s.get("check") or {}).get("type") in practice]
        if 2 * len(hands_on) < len(stubs):
            add("", f"至少一半的小节用动手型检查点（{' / '.join(practice)}），现在 {len(hands_on)}/{len(stubs)} 节")
    for rule in rules:
        out += rule(outline, limits)
    return out


def section_findings(section: dict, stub: dict, index: int) -> list[Finding]:
    """写好的一节和大纲里它的桩对不对得上：检查点要有大纲说的那种题型（约定的断言要并进这道题）。
    新词、标题、时长、目标、任务由 fill_stub 按大纲覆盖，不需要在这里查。"""
    want = (stub.get("check") or {}).get("type")
    if want and not any(c.get("type") == want for c in section.get("checkpoint") or []):
        return [Finding(section_address(index), f"大纲说这一节的检查点是 {want} 题：{(stub.get('check') or {}).get('what', '')}"
                        + ("。做完后练习场要满足大纲里的 state_after，环境会把这些断言并进这道题的检查" if stub.get("state_after") else ""),
                        EVALUATOR)]
    return []


def render_by_address(plan: dict) -> str:
    """课程计划按地址逐块列出（给评审模型和修复用：它们的发现和修改都要落在这些地址上）。"""
    titles = {f"/sections/{i}": s.get("title", "") for i, s in enumerate(sections_of(plan))}
    blocks = []
    for addr, value in plan_parts(plan).items():
        head = f"## {addr}" + (f" · 第 {int(addr.split('/')[2]) + 1} 节 {titles[addr]}" if addr in titles else "")
        blocks.append(f"{head}\n\n```json\n{json.dumps(value, ensure_ascii=False, indent=1)}\n```")
    return "\n\n".join(blocks)


def render_plan_md(plan: dict, session_minutes: int = 45) -> str:
    """课程计划的 Markdown 版本：给导师审阅用（包括答案和参考做法），网页用的是 JSON。"""
    lines = [f"# {plan.get('title', '')}", "", plan.get("summary", ""), "",
             f"总时长 {plan_minutes(plan)} 分钟，分 {len(plan_sessions(plan, session_minutes))} 次学完。", ""]
    if plan.get("lab"):
        lines += ["## 练习场", "", plan["lab"].get("story", ""), "",
                  "文件：" + "、".join(f"`{f['path']}`" for f in plan["lab"].get("files") or []), ""]
    n = 0
    for p in plan.get("parts") or []:
        lines += [f"## {p.get('title', '')}", ""]
        for s in p.get("sections") or []:
            n += 1
            lines += [f"### {n}. {s.get('title', '')}（{s.get('minutes')} 分钟）", "", f"**目标**：{s.get('goal', '')}", ""]
            if s.get("mission"):
                lines += [f"**任务**：{s['mission']}", ""]
            if s.get("terms"):
                lines += ["**新词**：" + "；".join(f"{t['term']}（`{t['id']}`）：{t['explain']}" for t in s["terms"]), ""]
            lines += [s.get("explain", ""), ""]
            if s.get("try"):
                lines.append("**动手**")
                lines += [f"- `{t['command']}` → {t['expect']}" for t in s["try"]]
                lines.append("")
            for j, c in enumerate(s.get("checkpoint") or [], 1):
                lines.append(f"**检查点 {j}（{c.get('type')}，{c.get('concept') or '—'}）**：{c.get('prompt', '')}")
                if c.get("options"):
                    lines += [f"  {chr(65 + k)}. {o}" for k, o in enumerate(c["options"])]
                if c.get("answer") is not None:
                    lines.append(f"  答案：{c['answer']}")
                if c.get("accept"):
                    lines.append(f"  可接受：{c['accept']}")
                for k in c.get("checks") or []:
                    how = next((f"{m} {k[m]!r}" for m in ("equals", "contains", "not_contains", "matches") if m in k),
                               "不存在" if k.get("absent") else "有输出")
                    target = f"执行 `{k['run']}`" if k.get("run") else f"文件 `{k.get('file')}`"
                    lines.append(f"  检查：{k.get('desc')} ← {target}，{how}")
                if c.get("solution"):
                    lines.append("  参考做法：" + " ; ".join(f"`{x}`" for x in c["solution"]))
                lines += [f"  提示 {h + 1}：{t}" for h, t in enumerate(c.get("hints") or [])]
                lines += [f"  坑（{t.get('when')}）：{t.get('symptom')} / {t.get('cause')} / {t.get('fix')}" for t in c.get("traps") or []]
                lines.append("")
            if s.get("watch"):
                lines += [f"**对应原讲解**：{s['watch']}", ""]
    if plan.get("nodes"):
        lines += ["## 提议的知识节点", ""] + [
            f"- `{x['id']}` {x['title']}（{x.get('kind')}）：{x.get('desc', '')}" +
            (f" · 先修 {', '.join(x['requires'])}" if x.get("requires") else "") for x in plan["nodes"]] + [""]
    if plan.get("sources"):
        lines += ["## 出处", ""] + [f"- [{x['title']}]({x['url']})" for x in plan["sources"]] + [""]
    if plan.get("later"):
        lines += ["## 以后再学", ""] + [f"- {x['title']}：{x['why']}" + (f"（{x['url']}）" if x.get("url") else "")
                                        for x in plan["later"]] + [""]
    lines += ["## 学完能做到", ""] + [f"{i}. {o}" for i, o in enumerate(plan.get("outcomes") or [], 1)]
    return "\n".join(lines) + "\n"


# ---------- 给 agent 看的 JSON Schema（core 部分；学科 spec 往里加字段） ----------

_SOURCE = {"type": "object", "properties": {
    "title": {"type": "string"}, "url": {"type": "string", "description": "必须是你用 fetch_url 打开过的页面"}},
    "required": ["title", "url"]}
_TRY = {"type": "object", "properties": {
    "command": {"type": "string", "description": "学习者要动手做的一步（或一小段），要能直接做通"},
    "expect": {"type": "string", "description": "做完应该看到什么；看到别的说明什么"}},
    "required": ["command", "expect"]}
_TRAP = {"type": "object", "properties": {
    "when": {"type": "string", "description": "什么样的错误答案说明踩了这个坑：选择题写选项字母；填空题写匹配错误答案的正则"},
    "symptom": {"type": "string", "description": "学习者会看到的现象"},
    "cause": {"type": "string", "description": "为什么会这样，一两句话"},
    "fix": {"type": "string", "description": "具体怎么做"}},
    "required": ["when", "symptom", "cause", "fix"]}
_TERM = {"type": "object", "properties": {
    "id": {"type": "string", "description": "知识节点 id，如 tools.cmd.grep、tools.shell.glob；知识图里已有的就用已有的 id"},
    "term": {"type": "string", "description": "页面上出现的原词，如 grep、通配符"},
    "explain": {"type": "string", "description": "一句话解释，学习者点这个词时看到"}},
    "required": ["id", "term", "explain"]}
_NODE = {"type": "object", "properties": {
    "id": {"type": "string"}, "title": {"type": "string"}, "desc": {"type": "string", "description": "一句话描述"},
    "kind": {"type": "string", "enum": ["concept", "term", "skill"]},
    "requires": {"type": "array", "items": {"type": "string"}, "description": "先修节点 id：学这个之前必须先会的"},
    "units": {"type": "array", "items": {"type": "string"}, "description": "哪些单元教它（通常就是这个单元）"}},
    "required": ["id", "title", "desc", "kind"]}


def plan_schema(checkpoint_types: dict[str, str], checkpoint_fields: dict, plan_fields: dict) -> dict:
    """checkpoint_types = 题型 → 说明；checkpoint_fields / plan_fields = 学科 spec 加的字段。"""
    checkpoint = {"type": "object", "properties": {
        "type": {"type": "string", "enum": list(checkpoint_types),
                 "description": "；".join(f"{k} {v}" for k, v in checkpoint_types.items())},
        "prompt": {"type": "string"},
        "concept": {"type": "string", "description": "这道题检验的知识节点 id（知识图里已有的，或你在 nodes 里提议的）"},
        "options": {"type": "array", "items": {"type": "string"}, "description": "choice：选项文字，不带字母"},
        "multi": {"type": "boolean"},
        "answer": {"description": "choice：正确选项字母，如 \"B\"；多选用列表"},
        "accept": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "fill：每个空可接受的答案"},
        **checkpoint_fields,
        "hints": {"type": "array", "items": {"type": "string"}, "description": "正好 3 级：方向 → 关键概念 → 接近答案（不直接给答案）"},
        "traps": {"type": "array", "items": _TRAP, "description": "常见坑，答错时才显示"},
        "explain": {"type": "string", "description": "做对之后显示的解析"}},
        "required": ["type", "prompt", "concept", "hints", "explain"]}
    section = {"type": "object", "properties": {
        "title": {"type": "string"},
        "minutes": {"type": "integer", "description": "学完这一节要多少分钟（含动手和检查点）"},
        "goal": {"type": "string", "description": "学完这一节能做到什么，写成可检验的行为"},
        "mission": {"type": "string", "description": "这一节在练习故事里的任务（一两句话），它的结果是下一节的材料"},
        "explain": {"type": "string", "description": "用你自己的话把这一节讲清楚（Markdown），学习者只读这里就能学会"},
        "terms": {"type": "array", "items": _TERM, "description": "这一节第一次出现、对这个学习者来说是新的术语"},
        "try": {"type": "array", "items": _TRY, "description": "动手：按顺序做的步骤和预期结果"},
        "checkpoint": {"type": "array", "items": checkpoint, "description": "1–3 道检查点题，做对才算学完这一节"},
        "watch": {"type": "string", "description": "可选：想看原讲解时，视频/讲义里对应的章节名"}},
        "required": ["title", "minutes", "goal", "explain", "terms", "try", "checkpoint"]}
    return {"type": "object", "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string", "description": "两三句话：这一课学什么，和学习者目标的关系"},
        **plan_fields,
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "sections": {"type": "array", "items": section}},
            "required": ["title", "sections"]}},
        "nodes": {"type": "array", "items": _NODE, "description": "提议加进知识图的新节点（terms 和 concept 用到的、知识图里还没有的）"},
        "sources": {"type": "array", "items": _SOURCE, "description": "整个单元的出处（只列一次，不要每节重复）"},
        "later": {"type": "array", "description": "因为时间预算没放进来、以后可以再学的内容", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "why": {"type": "string"}, "url": {"type": "string"}}, "required": ["title", "why"]}},
        "outcomes": {"type": "array", "items": {"type": "string"}, "description": "学完整个单元能做到的 3-5 件事，之后的练习题按这些出"}},
        "required": ["title", "summary", "parts", "sources", "outcomes"]}


def outline_schema(checkpoint_types: dict[str, str], plan_fields: dict, state_check: dict | None = None) -> dict:
    """大纲 = 课程计划去掉每节的正文，换成"桩"：这一节学什么、做什么、检查什么、依据哪几页，
    以及各节之间的接口约定——新词的定义、讲清的要点、做完后练习场满足的断言（state_check：学科给的断言格式）。"""
    stub = {"type": "object", "properties": {
        "title": {"type": "string"},
        "minutes": {"type": "integer", "description": "学完这一节要多少分钟（含动手和检查点）"},
        "goal": {"type": "string", "description": "学完这一节能做到什么，写成可检验的行为"},
        "mission": {"type": "string", "description": "这一节在练习故事里的任务（一两句话），它的结果是下一节的材料"},
        "terms": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "description": "知识节点 id；知识图里已有的就用已有的"}, "term": {"type": "string"},
            "explain": {"type": "string", "description": "一句话定义（先说它是什么、属于哪一类）。所有节都用这个定义"}},
            "required": ["id", "term", "explain"]},
            "description": "这一节第一次出现、对这个学习者来说是新的术语。写这一节的人只能用这些，定义以这里为准"},
        "teaches": {"type": "array", "items": {"type": "string"},
                    "description": "这一节讲清的 1–4 个要点。写后面几节的人会看到，直接引用、不再重讲；写前面几节的人会看到，不会抢先讲"},
        **({"state_after": {"type": "array", "items": state_check,
                            "description": "做完这一节（含检查点）后练习场必须满足的断言，格式和检查点的 checks 一样。"
                                           "这是后面几节唯一能依赖的练习场状态；环境会把它们并进这一节检查点的检查"}} if state_check else {}),
        "check": {"type": "object", "properties": {
            "type": {"type": "string", "enum": list(checkpoint_types)},
            "what": {"type": "string", "description": "检查点让学习者做到 / 答出什么（练习场任务写清结果是什么状态）"}},
            "required": ["type", "what"]},
        "reading": {"type": "array", "items": {"type": "string"}, "description": "写这一节要依据的页面网址（必须打开过）"}},
        "required": ["title", "minutes", "goal", "mission", "terms", "teaches", "check", "reading"]}
    full = plan_schema(checkpoint_types, {}, plan_fields)
    props = {**full["properties"], "parts": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "sections": {"type": "array", "items": stub}}, "required": ["title", "sections"]}}}
    return {"type": "object", "properties": props, "required": full["required"]}


def section_schema(checkpoint_types: dict[str, str], checkpoint_fields: dict, plan_fields: dict) -> dict:
    """写一节时提交的小节：没有 terms——新词和定义归大纲（fill_stub 按大纲补上）。"""
    s = plan_schema(checkpoint_types, checkpoint_fields, plan_fields)["properties"]["parts"]["items"]["properties"]["sections"]["items"]
    return {**s, "properties": {k: v for k, v in s["properties"].items() if k != "terms"},
            "required": [k for k in s["required"] if k != "terms"]}


CORE_CHECKPOINT_TYPES = {"choice": "选择", "fill": "填空（题干里每个 ____ 是一个空）"}


# ---------- 单元知识库（D-040 ③）：和学习者无关的通用层 ----------

def kb_findings(kb: dict, limits: PlanLimits) -> list[Finding]:
    """单元知识库的检查：知识点要有合法 id、一句话定义、要点、依据页面（打开过的）；先修要能找到。
    INVARIANT: 知识库不含学习者信息（生成它的简报里就没有），所以同一个单元可以反复用、以后给别的学习者用。"""
    out: list[Finding] = []
    add = lambda address, what: out.append(Finding(address, what, EVALUATOR))  # noqa: E731
    if not isinstance(kb, dict):
        return [Finding("", "knowledge 必须是一个对象", EVALUATOR)]
    if not str(kb.get("summary") or "").strip():
        add("/summary", "缺少 summary（讲义讲了什么，两三句话）")
    if not kb.get("sources"):
        add("/sources", "至少要有一个出处")
    points = kb.get("points") or []
    if len(points) < 3:
        add("/points", f"知识点至少 3 个，现在 {len(points)} 个")
    ids = {str(p.get("id")) for p in points}
    for i, p in enumerate(points):
        at, name = f"/points/{i}", f"知识点 {p.get('id')}"
        nid = str(p.get("id") or "")
        if not NODE_ID_RE.match(nid) or not nid.startswith(limits.topic + "."):
            add(at, f"{name}：id 要像 {limits.topic}.cmd.grep（小写、点分层、以学科 {limits.topic}. 开头）")
        for key in ("term", "explain"):
            if not str(p.get(key) or "").strip():
                add(at, f"{name}：缺少 {key}")
        teaches = [x for x in p.get("teaches") or [] if str(x).strip()]
        if not 1 <= len(teaches) <= 4:
            add(at, f"{name}：teaches 写 1–4 个要点，现在 {len(teaches)} 个")
        if not p.get("reading"):
            add(at, f"{name}：reading 至少列一个讲它的页面")
        for r in p.get("requires") or []:
            if r not in ids and r not in limits.existing_nodes:
                add(at, f"{name}：先修 {r} 既不在这份知识库里，也不在知识图里")
    for url in sorted(extract_urls(json.dumps(kb, ensure_ascii=False))):
        if url_key(url) not in limits.grounded:
            add("", f"链接没有打开过：{url}。只能引用你用 fetch_url 打开过的页面")
    return out


def kb_schema() -> dict:
    point = {"type": "object", "properties": {
        "id": {"type": "string", "description": "知识节点 id，如 test.pytest.fixture；知识图里已有的就用已有的"},
        "term": {"type": "string", "description": "讲义里的原词"},
        "kind": {"type": "string", "enum": ["concept", "term", "skill"]},
        "explain": {"type": "string", "description": "一句话定义：先说它是什么、属于哪一类（不用比喻）"},
        "requires": {"type": "array", "items": {"type": "string"}, "description": "先修：学它之前必须先会的知识点 id"},
        "teaches": {"type": "array", "items": {"type": "string"}, "description": "讲清它要讲的 1–4 个要点（讲义里说的，不是你的发挥）"},
        "reading": {"type": "array", "items": {"type": "string"}, "description": "讲它的页面网址（必须打开过）"}},
        "required": ["id", "term", "kind", "explain", "teaches", "reading"]}
    return {"type": "object", "properties": {
        "summary": {"type": "string", "description": "这个单元的讲义讲了什么（两三句话）"},
        "sources": {"type": "array", "items": _SOURCE},
        "points": {"type": "array", "items": point, "description": "讲义里的知识点，按讲义的顺序"},
        "exercises": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "what": {"type": "string", "description": "练习要做什么"},
            "reading": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "what"]},
            "description": "讲义里的练习、可以改编成练习场任务的素材"}},
        "required": ["summary", "sources", "points"]}
