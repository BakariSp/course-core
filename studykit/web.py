"""答题网页的本地服务器（HTTP 适配器）。只监听 127.0.0.1；只做路由和 JSON 编解码，用例都在 app/。

GET  /                      答题页面
GET  /api/lessons           所有课时
GET  /api/lesson?ref=...    一套题（每道题的 view + 最近一次作答结果）
GET  /api/runs?ref=&qid=    一道代码题的运行记录（每次的代码快照和测试结果）
GET  /api/units             已发布的课程页（D-010）
GET  /api/unit?id=...       一个单元的课程计划 + 学习进度
GET  /api/kg?topic=...      知识树：节点（带状态）+ 先修边（D-020）
GET  /api/kg/node?id=...    一个节点的详情和证据
POST /api/unit/progress     课程页事件：打开小节、心跳、跳过、新词反馈、费劲程度……
POST /api/unit/check        检查点判分（D-013）
POST /api/unit/hint         检查点的下一级提示
POST /api/unit/lab          练习场：执行命令、还原到本节开始、重置、用参考做法补齐（D-014）
POST /api/act               作答过程中的交互（跑测试、执行终端命令）
POST /api/submit            提交一道题：检验器判分 → 记一条证据
"""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from studykit.bootstrap import App, build

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 2 * 1024 * 1024    # 代码题提交的代码也不会超过这个大小


def make_handler(app: App, token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def get(path: str, qs: dict):
        one = lambda k, d="": qs.get(k, [d])[0]  # noqa: E731
        if path == "/api/lessons":
            return app.assessment.list()
        if path == "/api/lesson":
            return app.assessment.view(one("ref"), one("retake", "0") == "1")
        if path == "/api/runs":
            return app.assessment.runs(one("ref"), one("qid"))
        if path == "/api/units":
            return app.course.list()
        if path == "/api/unit":
            return app.course.page(one("id"))
        if path == "/api/kg":
            return app.learner.tree(one("topic", None) or None)
        if path == "/api/kg/node":
            return app.learner.show(one("id"))
        return None

    def post(path: str, body: dict):
        if path == "/api/act":
            return app.assessment.act(body["lesson"], body["qid"], body.get("action") or {})
        if path == "/api/submit":
            return app.assessment.submit(body["lesson"], body["qid"], body.get("response"))
        if path == "/api/unit/progress":
            extra = {k: body.get(k) for k in ("kind", "node", "action", "text", "rating")}
            return app.course.record(body["unit"], body.get("section"), body["event"], body.get("minutes"), **extra)
        if path == "/api/unit/check":
            return app.course.check(body["unit"], body["section"], body["idx"], body.get("response"))
        if path == "/api/unit/hint":
            return app.course.hint(body["unit"], body["section"], body["idx"])
        if path == "/api/unit/lab":
            return app.course.lab(body["unit"], body["op"], body.get("section"), body.get("cmd", ""))
        return None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            # WHY: 校验 Host 防 DNS rebinding：别的网站把自己的域名解析到 127.0.0.1 后就能读这个服务。
            return self.headers.get("Host") in allowed_hosts

        def _dispatch(self, fn, *args) -> None:
            try:
                out = fn(*args)
            except (ValueError, KeyError, TypeError) as e:     # DomainError 是 ValueError；缺字段是 KeyError
                return self._json({"error": str(e)}, 400)
            if out is None:
                return self._json({"error": "not found"}, 404)
            self._json(out)

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            url = urlparse(self.path)
            if url.path == "/":
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            self._dispatch(get, url.path, parse_qs(url.query))

        def do_POST(self):
            # WHY: 先把请求体读完再决定怎么回应。拒绝请求时不读请求体，Windows 会直接重置连接，
            # 客户端收到的是"连接被中止"而不是 403。
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = -1
            if not 0 <= length <= MAX_BODY:
                self.close_connection = True
                return self._json({"error": "请求体太大或长度不对"}, 413)
            raw = self.rfile.read(length)
            # WHY: 每次启动生成随机 token 写进页面。别的网站在浏览器里偷偷 POST 到 localhost 时拿不到它，
            # 否则任何网页都能借你的浏览器在练习终端里执行命令（CSRF）。
            if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Study-Token", ""), token):
                return self._json({"error": "forbidden"}, 403)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "请求体不是 JSON"}, 400)
            self._dispatch(post, self.path, body)

    return Handler


def serve(port: int = 8770, app: App | None = None) -> None:
    app = app or build()
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, token, port))
    print(f"答题页面：http://127.0.0.1:{port}/   （Ctrl+C 停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
