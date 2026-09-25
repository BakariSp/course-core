"""抓网页并转成纯文本（agent 的 fetch_url 工具用它；白名单检查在 app 层）。实现 app.ports.Fetcher。"""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

from studykit.app.ports import FetchError, Page

USER_AGENT = "cs-study-agent/0.1 (+local learning environment)"


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
        if tag in ("h1", "h2", "h3"):
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


def html_to_page(status: int, final_url: str, html: str) -> Page:
    parser = _TextExtractor(final_url)
    parser.feed(html)
    return Page(status, final_url, " ".join(parser.title.split()), parser.text(), parser.links)


class UrllibFetcher:
    def fetch(self, url: str, timeout: float = 20.0) -> Page:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,zh;q=0.8"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read(2_000_000)
                charset = resp.headers.get_content_charset() or "utf-8"
                return html_to_page(resp.status, resp.geturl(), body.decode(charset, errors="replace"))
        except urllib.error.HTTPError as e:
            raise FetchError(url, e.code) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError(url, None, str(e)) from e
