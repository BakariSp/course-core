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
    for url in sorted(set(re.findall(r"https?://[^\s)>\]\"'`]+", markdown))):
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


TOOLS = {
    "fetch_url": {"description": "抓取一个白名单网站的网页，返回正文（纯文本）和页面里的白名单链接。用它查课程官网、讲义、视频列表。",
                  "schema": FETCH_SCHEMA, "fn": lambda ctx, a: fetch_url(ctx, a.get("url", ""), a.get("start", 0))},
    "submit": {"description": "提交最终的学习指南（Markdown）。环境会检查必需的章节和链接出处，不通过会返回错误，改完再提交。",
               "schema": SUBMIT_SCHEMA, "fn": lambda ctx, a: submit(ctx, a.get("markdown", ""))},
}
