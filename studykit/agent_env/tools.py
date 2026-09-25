"""agent 能调用的工具。每个工具：一个 JSON Schema（给模型看） + 一个 Python 函数（真正执行）。

INVARIANT: 工具只能读白名单网站、只能写本次运行的目录。agent 没有文件系统和 shell 权限，
它能做的事完全由这里定义。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from studykit import store

CURRICULUM = store.ROOT / "curriculum.md"
MAX_TEXT = 15000
MAX_LINKS = 80
USER_AGENT = "cs-study-agent/0.1 (+local learning environment)"


class ToolError(Exception):
    """工具执行失败，错误信息会原样返回给模型，让它自己调整。"""


# ---------- 白名单 ----------

def _norm_host(host: str) -> str:
    host = (host or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def allowed_hosts(curriculum: Path | None = None) -> set[str]:
    """白名单 = curriculum.md 里出现过的所有网站。改白名单就改 curriculum.md。"""
    text = (curriculum or CURRICULUM).read_text(encoding="utf-8")
    return {_norm_host(urlparse(u).netloc) for u in re.findall(r"https?://[^\s)>\]]+", text)}


def is_allowed(url: str, hosts: set[str]) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and _norm_host(p.netloc) in hosts


# ---------- HTML → 纯文本 ----------

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "li", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "table"}

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.parts, self.links, self.title = base, [], [], ""
        self._skip = 0
        self._in_title = False
        self._href: str | None = None
        self._anchor: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "h1" or tag == "h2" or tag == "h3":
            self.parts.append("#" * int(tag[1]) + " ")
        if tag == "a":
            href = dict(attrs).get("href")
            self._href = urljoin(self.base, href) if href else None
            self._anchor = []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._href:
            self.links.append((self._href.split("#")[0], " ".join("".join(self._anchor).split())[:100]))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        self.parts.append(data)
        if self._href is not None:
            self._anchor.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        lines = [" ".join(l.split()) for l in raw.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------- 运行上下文 ----------

@dataclass
class RunContext:
    run_dir: Path
    hosts: set[str]
    required_sections: list[str]
    unit_budget_minutes: int = 180
    session_minutes: int = 45

    def log(self, name: str, rec: dict) -> None:
        with (self.run_dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def fetched(self) -> list[dict]:
        p = self.run_dir / "fetches.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _http_get(url: str, timeout: float = 20.0) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,zh;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(2_000_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.status, resp.geturl(), body.decode(charset, errors="replace")


# ---------- 工具：fetch_url ----------

FETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "要抓取的网页地址（必须在白名单网站内）"},
        "start": {"type": "integer", "description": "从正文第几个字开始读，默认 0。长页面会分段返回，结果末尾会告诉你下一段的 start。"},
    },
    "required": ["url"],
}


def fetch_url(ctx: RunContext, url: str, start: int = 0, _get=_http_get) -> str:
    # WHY: #锚点指向同一个页面。不去掉的话 agent 会把 x/ 和 x/#exercises 当成两页各读一遍，白白消耗 token。
    url = (url or "").strip().split("#")[0]
    start = max(0, int(start or 0))
    if not is_allowed(url, ctx.hosts):
        raise ToolError(f"不在白名单里：{url}。只能访问这些网站：{', '.join(sorted(ctx.hosts))}")
    if any(f.get("ok") and f["url"] == url and f.get("start", 0) == start for f in ctx.fetched()):
        return (f"你在这次任务里已经读过 {url} 的这一段（start={start}），内容见之前的结果。"
                "要读后面的内容，用上次结果末尾给出的 start。")
    try:
        status, final_url, html = _get(url)
    except urllib.error.HTTPError as e:
        ctx.log("fetches.jsonl", {"url": url, "status": e.code, "ok": False})
        raise ToolError(f"HTTP {e.code}：{url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        ctx.log("fetches.jsonl", {"url": url, "status": None, "ok": False})
        raise ToolError(f"访问失败：{url}（{e}）") from e
    parser = _TextExtractor(final_url)
    parser.feed(html)
    links, seen = [], set()
    for href, anchor in parser.links:
        if href not in seen and is_allowed(href, ctx.hosts):
            seen.add(href)
            links.append((href, anchor))
    links = links[:MAX_LINKS]
    title = " ".join(parser.title.split())
    full = parser.text()
    end = min(len(full), start + MAX_TEXT)
    ctx.log("fetches.jsonl", {"url": url, "final_url": final_url, "status": status, "ok": True, "start": start,
                              "title": title, "links": [h for h, _ in links]})
    if start >= len(full) and full:
        return f"{url} 的正文一共 {len(full)} 字，start={start} 已经超过结尾。"
    # WHY: 长页面分段返回，并在末尾写明下一段怎么读。只截断不说明的话，agent 会去猜别的网址找剩下的内容。
    if end < len(full):
        note = f"\n\n[这是第 {start}–{end} 字，全文共 {len(full)} 字。继续读：fetch_url(url=\"{url}\", start={end})]"
    else:
        note = f"\n\n[全文共 {len(full)} 字，已读到结尾]" if start else ""
    header = f"# {title}\nURL: {final_url}\n\n"
    # 页面链接只在第一段附上，后面几段不重复。
    link_block = ""
    if start == 0:
        link_lines = "\n".join(f"- {a or '(无文字)'} → {h}" for h, a in links)
        link_block = f"\n\n## 页面里的白名单链接\n{link_lines or '(无)'}"
    return header + full[start:end] + note + link_block


# ---------- 工具：submit ----------

SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {"markdown": {"type": "string", "description": "完整的学习指南，Markdown 格式"}},
    "required": ["markdown"],
}


# WHY: 中文里链接常被全角括号、引号、句号包着（如「（https://…）」），这些字符不能算进 URL，
# 否则核对出处和检查能否打开都会误报。整个环境只用这一个提取函数。
URL_RE = re.compile(r"https?://[^\s)>\]\"'`（）「」『』，。；：、！？《》【】]+")


def extract_urls(text: str) -> set[str]:
    return {u.rstrip(".,;:") for u in URL_RE.findall(text or "")}


def url_key(url: str) -> str:
    """比较链接时用的形式：去掉 #锚点、结尾的斜杠和标点。"""
    return url.split("#")[0].rstrip(".,;").rstrip("/")


def grounded_urls(ctx: RunContext) -> set[str]:
    """本次运行里真正打开过的页面。

    INVARIANT: 只算打开成功的页面，不算页面上出现过的链接。
    WHY: 没打开过的页面，agent 不知道里面是什么，写进指南就等于在猜。多打开一页的成本很低。
    """
    seen = set()
    for f in ctx.fetched():
        if f.get("ok"):
            seen.update({f["url"], f.get("final_url", f["url"])})
    return {url_key(u) for u in seen}


def check_submission(ctx: RunContext, markdown: str) -> list[str]:
    errors = []
    headings = {" ".join(h.split()) for h in re.findall(r"^##\s+(.+?)\s*$", markdown, re.MULTILINE)}
    for sec in ctx.required_sections:
        if sec not in headings:
            errors.append(f"缺少二级标题「## {sec}」")
    seen = grounded_urls(ctx)
    for url in sorted(extract_urls(markdown)):
        if url_key(url) not in seen:
            errors.append(f"链接没有打开过：{url}。只能引用你用 fetch_url 打开过的页面；想推荐它就先打开读一下，不需要就删掉")
    return errors


def submit(ctx: RunContext, markdown: str) -> str:
    errors = check_submission(ctx, markdown or "")
    ctx.log("submissions.jsonl", {"accepted": not errors, "errors": errors, "chars": len(markdown or "")})
    if errors:
        raise ToolError("没有通过格式检查，请修改后重新提交：\n- " + "\n- ".join(errors))
    (ctx.run_dir / "output.md").write_text(markdown, encoding="utf-8")
    return "已收到，格式检查通过。任务完成，不需要再做别的。"


# ---------- 工具：submit_plan（课程计划，D-010） ----------

_SOURCE = {"type": "object", "properties": {
    "title": {"type": "string"}, "url": {"type": "string", "description": "必须是你用 fetch_url 打开过的页面"}},
    "required": ["title", "url"]}
_TRY = {"type": "object", "properties": {
    "command": {"type": "string", "description": "学习者要在终端里敲的一条命令（或一小段）"},
    "expect": {"type": "string", "description": "敲完应该看到什么；看到别的说明什么"}},
    "required": ["command", "expect"]}
_PITFALL = {"type": "object", "properties": {
    "symptom": {"type": "string", "description": "学习者会看到的现象，最好是原样的报错或输出"},
    "cause": {"type": "string", "description": "为什么会这样，一两句话"},
    "fix": {"type": "string", "description": "具体怎么做，给命令"}},
    "required": ["symptom", "cause", "fix"]}
_SECTION = {"type": "object", "properties": {
    "title": {"type": "string"},
    "minutes": {"type": "integer", "description": "学完这一节要多少分钟（含动手）"},
    "goal": {"type": "string", "description": "学完这一节能做到什么，写成可检验的行为"},
    "explain": {"type": "string", "description": "用你自己的话把这一节讲清楚（Markdown），学习者只读这里就能学会；不要只说'去看讲义'"},
    "try": {"type": "array", "items": _TRY, "description": "动手：按顺序敲的命令和预期结果"},
    "pitfalls": {"type": "array", "items": _PITFALL},
    "watch": {"type": "string", "description": "可选：想看原讲解时，对应视频/讲义的哪一部分"},
    "sources": {"type": "array", "items": _SOURCE, "description": "这一节内容的出处"}},
    "required": ["title", "minutes", "goal", "explain", "try", "sources"]}
PLAN_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "summary": {"type": "string", "description": "两三句话：这一课学什么，和学习者目标的关系"},
    "parts": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "sections": {"type": "array", "items": _SECTION}},
        "required": ["title", "sections"]}},
    "later": {"type": "array", "description": "因为时间预算没放进来、以后可以再学的内容", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "why": {"type": "string"}, "url": {"type": "string"}}, "required": ["title", "why"]}},
    "outcomes": {"type": "array", "items": {"type": "string"}, "description": "学完整个单元能做到的 3-5 件事，之后的练习题按这些出"}},
    "required": ["title", "summary", "parts", "outcomes"]}


def sections_of(plan: dict) -> list[dict]:
    return [s for p in plan.get("parts") or [] for s in p.get("sections") or []]


def plan_minutes(plan: dict) -> int:
    return sum(int(s.get("minutes") or 0) for s in sections_of(plan))


def plan_sessions(plan: dict, session_minutes: int) -> list[list[int]]:
    """按顺序把小节装进一次次学习（每次不超过 session_minutes），返回每次包含的小节序号。"""
    sessions, cur, used = [], [], 0
    for i, s in enumerate(sections_of(plan)):
        m = int(s.get("minutes") or 0)
        if cur and used + m > session_minutes:
            sessions.append(cur)
            cur, used = [], 0
        cur.append(i)
        used += m
    if cur:
        sessions.append(cur)
    return sessions


def plan_urls(plan: dict) -> set[str]:
    return extract_urls(json.dumps(plan, ensure_ascii=False))


def check_plan(ctx: RunContext, plan: dict) -> list[str]:
    """课程计划的硬性检查。INVARIANT: 时间预算是硬约束，超了不收（F-014：没有预算一个单元会无限膨胀）。"""
    errors = []
    if not isinstance(plan, dict):
        return ["plan 必须是一个对象"]
    for key in ("title", "summary"):
        if not str(plan.get(key) or "").strip():
            errors.append(f"缺少 {key}")
    sections = sections_of(plan)
    if not sections:
        errors.append("至少要有一个 part 和一个 section")
    for i, s in enumerate(sections, 1):
        name = f"第 {i} 节「{s.get('title', '')}」"
        m = s.get("minutes")
        if not isinstance(m, int) or m <= 0:
            errors.append(f"{name}：minutes 必须是正整数")
        elif m > ctx.session_minutes:
            errors.append(f"{name}：{m} 分钟超过单次学习上限 {ctx.session_minutes} 分钟，拆成几节")
        for key in ("goal", "explain"):
            if not str(s.get(key) or "").strip():
                errors.append(f"{name}：缺少 {key}")
        if len(str(s.get("explain") or "")) < 120:
            errors.append(f"{name}：explain 太短，要把这一节讲清楚，学习者只读这里就能学会")
        if not s.get("try"):
            errors.append(f"{name}：至少要有一条动手（try）")
        for t in s.get("try") or []:
            if not str(t.get("command") or "").strip() or not str(t.get("expect") or "").strip():
                errors.append(f"{name}：每条动手都要有 command 和 expect")
        for p in s.get("pitfalls") or []:
            if not all(str(p.get(k) or "").strip() for k in ("symptom", "cause", "fix")):
                errors.append(f"{name}：每个坑都要写清 symptom / cause / fix")
        if not s.get("sources"):
            errors.append(f"{name}：至少要有一个出处（sources）")
    total = plan_minutes(plan)
    if total > ctx.unit_budget_minutes:
        errors.append(f"总时长 {total} 分钟超过单元预算 {ctx.unit_budget_minutes} 分钟。"
                      "砍掉次要的小节放进 later，不要压缩每节的分钟数来凑数")
    outcomes = plan.get("outcomes") or []
    if not 3 <= len(outcomes) <= 5:
        errors.append(f"outcomes 要 3-5 条，现在是 {len(outcomes)} 条")
    seen = grounded_urls(ctx)
    for url in sorted(plan_urls(plan)):
        if url_key(url) not in seen:
            errors.append(f"链接没有打开过：{url}。只能引用你用 fetch_url 打开过的页面")
    return errors


def render_plan_md(plan: dict, session_minutes: int = 45) -> str:
    """课程计划的 Markdown 版本：给导师审阅和命令行阅读用，网页用的是 JSON。"""
    lines = [f"# {plan.get('title', '')}", "", plan.get("summary", ""), "",
             f"总时长 {plan_minutes(plan)} 分钟，分 {len(plan_sessions(plan, session_minutes))} 次学完。", ""]
    n = 0
    for p in plan.get("parts") or []:
        lines += [f"## {p.get('title', '')}", ""]
        for s in p.get("sections") or []:
            n += 1
            lines += [f"### {n}. {s.get('title', '')}（{s.get('minutes')} 分钟）", "", f"**目标**：{s.get('goal', '')}", "",
                      s.get("explain", ""), ""]
            if s.get("try"):
                lines.append("**动手**")
                lines += [f"- `{t['command']}` → {t['expect']}" for t in s["try"]]
                lines.append("")
            if s.get("pitfalls"):
                lines.append("**常见坑**")
                lines += [f"- 现象：{p_['symptom']}；原因：{p_['cause']}；怎么办：{p_['fix']}" for p_ in s["pitfalls"]]
                lines.append("")
            if s.get("watch"):
                lines += [f"**对应原讲解**：{s['watch']}", ""]
            lines += ["出处：" + "；".join(f"[{x['title']}]({x['url']})" for x in s.get("sources") or []), ""]
    if plan.get("later"):
        lines += ["## 以后再学", ""] + [f"- {x['title']}：{x['why']}" + (f"（{x['url']}）" if x.get("url") else "")
                                        for x in plan["later"]] + [""]
    lines += ["## 学完能做到", ""] + [f"{i}. {o}" for i, o in enumerate(plan.get("outcomes") or [], 1)]
    return "\n".join(lines) + "\n"


def submit_plan(ctx: RunContext, plan: dict) -> str:
    errors = check_plan(ctx, plan)
    ctx.log("submissions.jsonl", {"accepted": not errors, "errors": errors, "minutes": plan_minutes(plan or {})})
    if errors:
        raise ToolError("课程计划没有通过检查，请修改后重新提交：\n- " + "\n- ".join(errors))
    (ctx.run_dir / "output.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    (ctx.run_dir / "output.md").write_text(render_plan_md(plan, ctx.session_minutes), encoding="utf-8")
    return "已收到，检查通过。任务完成，不需要再做别的。"


TOOLS = {
    "fetch_url": {"description": "抓取一个白名单网站的网页，返回正文（纯文本）和页面里的白名单链接。用它查课程官网、讲义、视频列表。",
                  "schema": FETCH_SCHEMA, "fn": lambda ctx, a: fetch_url(ctx, a.get("url", ""), a.get("start", 0))},
    "submit": {"description": "提交最终的学习指南（Markdown）。环境会检查必需的章节和链接出处，不通过会返回错误，改完再提交。",
               "schema": SUBMIT_SCHEMA, "fn": lambda ctx, a: submit(ctx, a.get("markdown", ""))},
    "submit_plan": {"description": "提交这个单元的课程计划（结构化）。环境会检查时间预算、每节的目标/讲解/动手/出处、链接是否打开过；不通过会返回错误，改完再提交。",
                    "schema": {"type": "object", "properties": {"plan": PLAN_SCHEMA}, "required": ["plan"]},
                    "fn": lambda ctx, a: submit_plan(ctx, a.get("plan") or {})},
}
