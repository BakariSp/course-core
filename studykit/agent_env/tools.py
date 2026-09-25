"""agent 能调用的工具。每个工具：一个 JSON Schema（给模型看） + 一个 Python 函数（真正执行）。

INVARIANT: 工具只能读白名单网站、只能写本次运行的目录。agent 没有文件系统和 shell 权限，
它能做的事完全由这里定义。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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
    unit: str = ""
    max_new_terms: int = 5
    known_terms: set[str] = field(default_factory=set)        # 学习者已掌握的术语（小写标题 / 节点 id，D-017）
    existing_nodes: dict[str, str] = field(default_factory=dict)   # 知识图里已有的节点 id → 标题（D-020）

    @classmethod
    def from_spec(cls, run_dir: Path, spec: dict) -> "RunContext":
        return cls(run_dir, set(spec["hosts"]), spec.get("required_sections") or [],
                   spec.get("unit_budget_minutes", 180), spec.get("session_minutes", 45),
                   spec.get("unit", ""), spec.get("max_new_terms", 5),
                   {t.lower() for t in spec.get("known_terms") or []}, dict(spec.get("existing_nodes") or {}))

    @property
    def topic(self) -> str:
        return self.unit.split("-")[0] if self.unit else ""

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


# ---------- 工具：submit_plan（课程计划 v2：D-010、D-013 ~ D-017、D-020） ----------

PLAN_SCHEMA_VERSION = 2
NODE_ID = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9_-]*)+$")

_SOURCE = {"type": "object", "properties": {
    "title": {"type": "string"}, "url": {"type": "string", "description": "必须是你用 fetch_url 打开过的页面"}},
    "required": ["title", "url"]}
_TRY = {"type": "object", "properties": {
    "command": {"type": "string", "description": "学习者要在右边的终端里敲的一条命令（或一小段），要能在练习场里直接跑通"},
    "expect": {"type": "string", "description": "敲完应该看到什么；看到别的说明什么"}},
    "required": ["command", "expect"]}
_TRAP = {"type": "object", "properties": {
    "when": {"type": "string", "description": "什么样的错误答案说明踩了这个坑：选择题写选项字母；填空题写匹配错误答案的正则"},
    "symptom": {"type": "string", "description": "学习者会看到的现象"},
    "cause": {"type": "string", "description": "为什么会这样，一两句话"},
    "fix": {"type": "string", "description": "具体怎么做"}},
    "required": ["when", "symptom", "cause", "fix"]}
_CHECK = {"type": "object", "properties": {
    "run": {"type": "string", "description": "在练习场根目录执行的 bash 命令，检查它的输出"},
    "file": {"type": "string", "description": "或者：检查练习场里这个文件的内容（相对路径）"},
    "equals": {"type": "string"}, "contains": {"type": "string"}, "not_contains": {"type": "string"},
    "matches": {"type": "string", "description": "正则"}, "absent": {"type": "boolean", "description": "file 不应该存在"},
    "desc": {"type": "string", "description": "给学习者看的检查项，如「report.txt 里有 ERROR 的次数」"},
    "trap": {"type": "object", "description": "这一项没通过时最可能的原因（symptom / cause / fix）", "properties": {
        "symptom": {"type": "string"}, "cause": {"type": "string"}, "fix": {"type": "string"}}}},
    "required": ["desc"]}
_CHECKPOINT = {"type": "object", "properties": {
    "type": {"type": "string", "enum": ["choice", "fill", "lab"],
             "description": "choice 选择；fill 填空（题干里每个 ____ 是一个空）；lab 在练习场里完成一个任务，环境检查练习场的状态"},
    "prompt": {"type": "string"},
    "concept": {"type": "string", "description": "这道题检验的知识节点 id（知识图里已有的，或你在 nodes 里提议的）"},
    "options": {"type": "array", "items": {"type": "string"}, "description": "choice：选项文字，不带字母"},
    "multi": {"type": "boolean"},
    "answer": {"description": "choice：正确选项字母，如 \"B\"；多选用列表"},
    "accept": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "fill：每个空可接受的答案"},
    "checks": {"type": "array", "items": _CHECK, "description": "lab：做完后练习场应该是什么状态"},
    "solution": {"type": "array", "items": {"type": "string"}, "description": "lab：参考做法（按顺序的命令）。学习者跳过这一节时，环境用它把练习场补齐"},
    "hints": {"type": "array", "items": {"type": "string"}, "description": "正好 3 级：方向 → 关键概念 → 接近答案（不直接给答案）"},
    "traps": {"type": "array", "items": _TRAP, "description": "choice / fill：常见坑，答错时才显示"},
    "explain": {"type": "string", "description": "做对之后显示的解析"}},
    "required": ["type", "prompt", "hints", "explain"]}
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
_SECTION = {"type": "object", "properties": {
    "title": {"type": "string"},
    "minutes": {"type": "integer", "description": "学完这一节要多少分钟（含动手和检查点）"},
    "goal": {"type": "string", "description": "学完这一节能做到什么，写成可检验的行为"},
    "mission": {"type": "string", "description": "这一节在练习场故事里的任务（一两句话），它的结果是下一节的材料"},
    "explain": {"type": "string", "description": "用你自己的话把这一节讲清楚（Markdown），学习者只读这里就能学会"},
    "terms": {"type": "array", "items": _TERM, "description": "这一节第一次出现、对这个学习者来说是新的术语和命令"},
    "try": {"type": "array", "items": _TRY, "description": "动手：按顺序敲的命令和预期结果"},
    "checkpoint": {"type": "array", "items": _CHECKPOINT, "description": "1–3 道检查点题，做对才算学完这一节"},
    "watch": {"type": "string", "description": "可选：想看原讲解时，视频/讲义里对应的章节名"}},
    "required": ["title", "minutes", "goal", "explain", "terms", "try", "checkpoint"]}
_LAB = {"type": "object", "properties": {
    "story": {"type": "string", "description": "练习场的故事：学习者接手了什么、要一步步查清楚什么"},
    "files": {"type": "array", "items": {"type": "object", "properties": {
        "path": {"type": "string", "description": "相对练习场根目录的路径；以 / 结尾表示空目录"},
        "content": {"type": "string"}}, "required": ["path"]}}},
    "required": ["story", "files"]}
PLAN_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"},
    "summary": {"type": "string", "description": "两三句话：这一课学什么，和学习者目标的关系"},
    "lab": _LAB,
    "parts": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "sections": {"type": "array", "items": _SECTION}},
        "required": ["title", "sections"]}},
    "nodes": {"type": "array", "items": _NODE, "description": "提议加进知识图的新节点（terms 和 concept 用到的、知识图里还没有的）"},
    "sources": {"type": "array", "items": _SOURCE, "description": "整个单元的出处（只列一次，不要每节重复）"},
    "later": {"type": "array", "description": "因为时间预算没放进来、以后可以再学的内容", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "why": {"type": "string"}, "url": {"type": "string"}}, "required": ["title", "why"]}},
    "outcomes": {"type": "array", "items": {"type": "string"}, "description": "学完整个单元能做到的 3-5 件事，之后的练习题按这些出"}},
    "required": ["title", "summary", "parts", "sources", "outcomes"]}


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


# WHY: 这些是 shell 语法里跟在别的词后面的关键字，出现在行首不代表是一个新命令。
_NOT_COMMANDS = {"then", "do", "done", "fi", "else", "elif", "esac", "in", "{", "}", "(", ")", "!"}
_CMD = re.compile(r"^[a-z][a-z0-9_+-]*$")


def command_words(text: str) -> set[str]:
    """一段 shell 命令里用到的命令名：按 | && || ; 和换行切开，取每段的第一个词。"""
    words = set()
    for seg in re.split(r"\|\||&&|[|;\n]|\$\(|`", text or ""):
        toks = seg.strip().split()
        while toks and (re.match(r"^\w+=", toks[0]) or toks[0] in _NOT_COMMANDS or toks[0] in ("sudo", "time")):
            toks = toks[1:]
        if toks and _CMD.match(toks[0]):
            words.add(toks[0])
    return words


def inline_commands(markdown: str) -> set[str]:
    """讲解里行内代码中的命令。只看至少两个词的片段：单个词（如 `g`、`-i`）多半是参数或文件名，不是命令。"""
    words = set()
    for span in re.findall(r"(?<!`)`([^`\n]+)`(?!`)", markdown or ""):
        if len(span.split()) >= 2:
            words |= command_words(span)
    return words


def _check_checkpoint(name: str, j: int, c: dict, node_ids: set[str]) -> list[str]:
    errors = []
    where = f"{name} 检查点第 {j} 题"
    kind = c.get("type")
    if kind not in ("choice", "fill", "lab"):
        return [f"{where}：type 只能是 choice / fill / lab"]
    if not str(c.get("prompt") or "").strip():
        errors.append(f"{where}：缺少 prompt")
    hints = [h for h in c.get("hints") or [] if str(h).strip()]
    if len(hints) != 3:
        errors.append(f"{where}：hints 要正好 3 级（方向 → 关键概念 → 接近答案），现在 {len(hints)} 条")
    if not str(c.get("explain") or "").strip():
        errors.append(f"{where}：缺少 explain（做对之后的解析）")
    if c.get("concept") and c["concept"] not in node_ids:
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
    elif kind == "lab":
        checks = c.get("checks") or []
        if not checks:
            errors.append(f"{where}：lab 题至少要有一个 check")
        for k in checks:
            if not (k.get("run") or k.get("file")):
                errors.append(f"{where}：每个 check 要有 run 或 file")
            if not str(k.get("desc") or "").strip():
                errors.append(f"{where}：每个 check 要有 desc")
        if not c.get("solution"):
            errors.append(f"{where}：lab 题要有 solution（参考做法），学习者跳过时用它补齐练习场")
    for t in c.get("traps") or []:
        if not all(str(t.get(k) or "").strip() for k in ("when", "symptom", "cause", "fix")):
            errors.append(f"{where}：每个 trap 都要写清 when / symptom / cause / fix")
    return errors


def _check_lab(lab: dict) -> list[str]:
    errors = []
    if not str(lab.get("story") or "").strip():
        errors.append("lab 缺少 story")
    files = lab.get("files") or []
    if not files:
        errors.append("lab 至少要有一个文件或目录")
    if len(files) > 80:
        errors.append(f"lab 文件太多（{len(files)} 个，上限 80）")
    size = 0
    for f in files:
        path = str(f.get("path") or "")
        parts = Path(path).parts
        if not path or path.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", path) or ".." in parts:
            errors.append(f"lab 文件路径要是练习场里的相对路径：{path!r}")
        size += len(str(f.get("content") or ""))
    if size > 200_000:
        errors.append(f"lab 文件总大小 {size} 字，上限 200000")
    return errors


def check_plan(ctx: RunContext, plan: dict) -> list[str]:
    """课程计划的硬性检查。INVARIANT: 时间预算、每节新词数是硬约束，超了不收（F-014、F-019）。"""
    errors = []
    if not isinstance(plan, dict):
        return ["plan 必须是一个对象"]
    for key in ("title", "summary"):
        if not str(plan.get(key) or "").strip():
            errors.append(f"缺少 {key}")
    if not plan.get("sources"):
        errors.append("至少要有一个出处（sources，整个单元列一次）")
    sections = sections_of(plan)
    if not sections:
        errors.append("至少要有一个 part 和一个 section")

    proposed = {n.get("id"): n for n in plan.get("nodes") or []}
    for nid, n in proposed.items():
        if not NODE_ID.match(str(nid or "")):
            errors.append(f"nodes：id 格式不对：{nid}（小写，点分层，如 {ctx.topic or 'tools'}.cmd.grep）")
        elif ctx.topic and not nid.startswith(ctx.topic + "."):
            errors.append(f"nodes：{nid} 要以学科 {ctx.topic}. 开头")
        if nid in ctx.existing_nodes:
            errors.append(f"nodes：{nid} 已经在知识图里了，直接用，不要重复提议")
        if not str(n.get("desc") or "").strip():
            errors.append(f"nodes：{nid} 缺少 desc")
    node_ids = set(ctx.existing_nodes) | set(proposed)
    for nid, n in proposed.items():
        for r in n.get("requires") or []:
            if r not in node_ids:
                errors.append(f"nodes：{nid} 的先修 {r} 不存在（知识图里没有，也没有提议）")

    has_lab_task = False
    known = set(ctx.known_terms)
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
        if s.get("pitfalls") or s.get("sources"):
            errors.append(f"{name}：不要再写 pitfalls / sources。常见坑写进检查点的 traps（答错时才出现），出处写在单元的 sources 里")

        terms = s.get("terms") or []
        new_terms = [t for t in terms if str(t.get("id")) not in known and str(t.get("term", "")).lower() not in known]
        if len(new_terms) > ctx.max_new_terms:
            errors.append(f"{name}：新词 {len(new_terms)} 个，超过每节上限 {ctx.max_new_terms} 个。"
                          "新东西太多学习者记不住：把这一节拆成两节，或者把次要的词挪到后面的节")
        for t in terms:
            if not all(str(t.get(k) or "").strip() for k in ("id", "term", "explain")):
                errors.append(f"{name}：每个新词都要有 id / term / explain")
            elif t["id"] not in node_ids:
                errors.append(f"{name}：新词 {t['term']} 的 id {t['id']} 不在知识图里，要在 nodes 里提议")
        known |= {str(t.get("id")) for t in terms} | {str(t.get("term", "")).lower() for t in terms}
        used = inline_commands(s.get("explain", "")) | inline_commands(s.get("mission", ""))
        for t in s.get("try") or []:
            used |= command_words(t.get("command", ""))
        undeclared = sorted(w for w in used if w not in known)
        if undeclared:
            errors.append(f"{name}：用到了学习者还不认识的命令 {', '.join(undeclared)}。"
                          "要么列进这一节的 terms（给一句话解释），要么换成已经学过的命令")

        items = s.get("checkpoint") or []
        if not 1 <= len(items) <= 3:
            errors.append(f"{name}：检查点要 1–3 道题，现在 {len(items)} 道")
        for j, c in enumerate(items, 1):
            errors += _check_checkpoint(name, j, c, node_ids)
            has_lab_task |= c.get("type") == "lab"
    if has_lab_task and not plan.get("lab"):
        errors.append("有 lab 类型的检查点，但没有定义练习场（lab）")
    if plan.get("lab"):
        errors += _check_lab(plan["lab"])

    total = plan_minutes(plan)
    if total > ctx.unit_budget_minutes:
        errors.append(f"总时长 {total} 分钟超过单元预算 {ctx.unit_budget_minutes} 分钟。"
                      "砍掉次要的小节放进 later，不要压缩每节的分钟数来凑数")
    outcomes = plan.get("outcomes") or []
    if not 3 <= len(outcomes) <= 5:
        errors.append(f"outcomes 要 3-5 条，现在是 {len(outcomes)} 条")
    seen = grounded_urls(ctx)
    # 练习场文件里的链接是故事道具（比如日志里的请求地址），不算引用，不检查。
    for url in sorted(plan_urls({k: v for k, v in plan.items() if k != "lab"})):
        if url_key(url) not in seen:
            errors.append(f"链接没有打开过：{url}。只能引用你用 fetch_url 打开过的页面")
    return errors


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
    "submit_plan": {"description": "提交这个单元的课程计划（结构化）。环境会检查时间预算、每节的新词数和没声明的命令、检查点、练习场、知识节点、链接是否打开过；不通过会返回错误，改完再提交。",
                    "schema": {"type": "object", "properties": {"plan": PLAN_SCHEMA}, "required": ["plan"]},
                    "fn": lambda ctx, a: submit_plan(ctx, a.get("plan") or {})},
}
