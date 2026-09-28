"""答题网页的本地服务器（HTTP 适配器）。只监听 127.0.0.1；只做路由和 JSON 编解码，用例都在 app/。

GET  /                      答题页面
GET  /app                   新界面（D-046）：课程 · 我的 · 老师；/app/<文件> 是它的样式和脚本（只读 web/app/ 下）
GET  /api/lessons           所有课时
GET  /api/lesson?ref=...    一套题（每道题的 view + 最近一次作答结果）
GET  /api/runs?ref=&qid=    一道代码题的运行记录（每次的代码快照和测试结果）
GET  /api/exam?lesson=&exam=  一次交卷的结果；还在后台批的简答题列在 grading 里（D-049）
GET  /api/course            学习者看到的课程：阶段 → 学科 → 单元（状态算好）、下一步（D-046、D-047）
GET  /api/profile           我的资料、时间偏好、老师的观察（D-047）
POST /api/profile/refute    学习者推翻一条老师的观察
GET  /api/units             已发布的课程页（D-010）
GET  /api/unit?id=...       一个单元的课程计划 + 学习进度 + 先修要求（readiness）
GET  /api/kg?topic=...      知识树：节点（带状态）+ 先修边（D-020）
GET  /api/kg/node?id=...    一个节点的详情和证据
GET  /flow                  流程看板（D-024）
GET  /api/flow/graph        看板的节点、依赖、每个 verb 被谁用
GET  /api/flow?limit=       最近的记录（每条：路径、被谁用、记了什么）
GET  /api/flow/stream       新记录的实时推送（SSE）；?cursor= 从某个位置接着推
POST /api/unit/progress     课程页事件：打开小节、心跳、跳过、新词反馈、费劲程度……
POST /api/unit/check        检查点判分（D-013）
POST /api/unit/hint         检查点的下一级提示
POST /api/unit/lab          练习场：执行命令、还原到本节开始、重置、用参考做法补齐（D-014）；列出 / 读 / 写文件（D-057）
POST /api/act               作答过程中的交互（跑测试、执行终端命令）
POST /api/submit            提交一道题：检验器判分 → 记一条证据（整卷模式的单元题不能单题提交）
POST /api/exam/submit       交卷：整套题一次判分，简答题由 LLM 在后台批改（D-031、D-049）
"""
from __future__ import annotations

import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from studykit.bootstrap import App, build

WEB = Path(__file__).resolve().parent.parent / "web"
APP = WEB / "app"
APP_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml"}
MAX_BODY = 2 * 1024 * 1024    # 代码题提交的代码也不会超过这个大小
STREAM_POLL = 1.0             # 秒：多久查一次数据库有没有新行
STREAM_PING = 15.0            # 秒：多久发一次心跳，让代理和浏览器知道连接还活着


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
        if path == "/api/exam":
            return app.assessment.exam_result(one("lesson"), one("exam"))
        if path == "/api/units":
            return app.course.list()
        if path == "/api/unit":
            uid = one("id")
            page = app.course.page(uid, one("plan") or None)
            page = {**page, "readiness": app.learner.readiness(uid)}      # 先修要求（D-047）
            try:
                return {**page, "quiz_state": app.quizzes.state(uid)}     # 单元题出到哪了（D-041）
            except Exception:  # noqa: BLE001  出题状态读不到不能影响学习
                return page
        if path == "/api/course":
            return app.journey.overview()
        if path == "/api/profile":
            return app.learner.profile()
        if path == "/api/panel":
            return app.panel.overview()
        if path == "/api/panel/unit":
            return app.panel.unit(one("id"))
        if path == "/api/panel/context":
            return app.panel.context(one("id"))
        if path == "/api/panel/versions":
            return app.panel.versions()
        if path == "/api/panel/version_text":
            return {"text": app.panel.version_text(one("variant"), one("part"))}
        if path == "/api/log":
            return app.learner.journal()
        if path == "/api/kg":
            return app.learner.tree(one("topic", None) or None)
        if path == "/api/kg/node":
            return app.learner.show(one("id"))
        if path == "/api/flow/graph":
            return app.observer.graph()
        if path == "/api/flow":
            return app.observer.since(None, max(1, min(int(one("limit", "60")), 500)))
        return None

    def post(path: str, body: dict):
        if path == "/api/act":
            return app.assessment.act(body["lesson"], body["qid"], body.get("action") or {})
        if path == "/api/submit":
            return app.assessment.submit(body["lesson"], body["qid"], body.get("response"))
        if path == "/api/exam/submit":
            return app.assessment.submit_exam(body["lesson"], body.get("responses") or {})
        if path == "/api/unit/progress":
            extra = {k: body.get(k) for k in ("kind", "node", "action", "text", "rating", "away_minutes", "counted", "remembered")}
            extra = {k: v for k, v in extra.items() if v is not None}
            out = app.course.record(body["unit"], body.get("section"), body["event"], body.get("minutes"), **extra)
            if body["event"] == "open":                     # 学第 N 单元时，第 N+1 单元在后台备好（D-040 ②）
                try:
                    nxt = app.panel.prefetch_after(body["unit"])
                except Exception:  # noqa: BLE001  预备失败不能影响学习；原因会记在备课状态里
                    nxt = None
                if nxt:
                    out = {**out, "prefetch": nxt}
                try:                                         # 打开最后一节：在后台出单元题（D-041）
                    if app.quizzes.prefetch(body["unit"], body.get("section")):
                        out = {**out, "quiz": "preparing"}
                except Exception:  # noqa: BLE001
                    pass
            return out
        if path == "/api/profile/refute":
            e = app.learner.refute_strategy(body["id"], body.get("note") or "")
            return {"refuted": body["id"], "evidence": e.id}
        if path == "/api/panel/prepare":
            return app.panel.prepare(body["unit"])
        if path == "/api/quiz/start":
            return app.quizzes.start(body["unit"])
        if path == "/api/panel/knowledge":
            return app.panel.build_knowledge()
        if path == "/api/panel/publish":
            return app.panel.publish(body["unit"])
        if path == "/api/unit/switch":                 # 改用某一版课程（新的或旧的），进度按版本保留（D-034、D-044）
            return app.course.switch(body["unit"], body["plan"])
        if path == "/api/unit/check":
            return app.course.check(body["unit"], body["section"], body["idx"], body.get("response"))
        if path == "/api/unit/hint":
            return app.course.hint(body["unit"], body["section"], body["idx"])
        if path == "/api/unit/lab":
            return app.course.lab(body["unit"], body["op"], body.get("section"), body.get("cmd", ""),
                                  path=str(body.get("path") or ""), content=str(body.get("content") or ""))
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
            # 只有一个首页（F-096）：`/` 去新界面；老首页在 /old。学习页、答题页、开发者视图还是老页面，
            # 靠查询参数（?unit= / ?lesson= / ?panel=）区分，所以带参数的 `/` 照旧给老页面，已有的链接不断。
            if url.path == "/" and not url.query:
                self.send_response(302)
                self.send_header("Location", "/app/")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if url.path in ("/", "/old"):
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path in ("/app", "/app/"):
                html = (APP / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/app/"):
                return self._app_file(url.path[len("/app/"):])
            if url.path == "/flow":
                return self._send(200, (WEB / "flow.html").read_bytes(), "text/html; charset=utf-8")
            if url.path == "/api/flow/stream":
                return self._stream(parse_qs(url.query).get("cursor", [""])[0])
            self._dispatch(get, url.path, parse_qs(url.query))

        def _app_file(self, rel: str) -> None:
            # WHY: 路径来自浏览器，先解码再解析成绝对路径，必须还在 web/app/ 里面；只给样式和脚本，别的类型一律 404。
            path = (APP / unquote(rel)).resolve()
            ctype = APP_TYPES.get(path.suffix)
            if not ctype or not path.is_relative_to(APP.resolve()) or not path.is_file():
                return self._json({"error": "not found"}, 404)
            self._send(200, path.read_bytes(), ctype)

        def _stream(self, cursor: str) -> None:
            """SSE：一条长连接，服务器有新记录就推一条 `data: <JSON>`。

            WHY: 每秒查一次数据库的新行，而不是在写入的地方发通知——命令行、agent 在别的进程里写的也能看到。
            浏览器的 EventSource 断线会自己重连，重连时带上最后的游标（Last-Event-ID）接着推，不漏不重。
            """
            try:
                cur = json.loads(self.headers.get("Last-Event-ID") or cursor or "null")
            except json.JSONDecodeError:
                cur = None
            if cur is None:
                cur = app.observer.since(None, 1)["cursor"]      # 从现在开始推；历史用 /api/flow 取
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            last_ping = time.monotonic()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    batch = app.observer.since(cur)
                    cur = batch["cursor"]
                    for e in batch["events"]:
                        data = json.dumps(e, ensure_ascii=False)
                        self.wfile.write(f"id: {json.dumps(cur)}\nevent: flow\ndata: {data}\n\n".encode("utf-8"))
                    if time.monotonic() - last_ping > STREAM_PING:
                        self.wfile.write(b": ping\n\n")
                        last_ping = time.monotonic()
                    self.wfile.flush()
                    time.sleep(STREAM_POLL)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return                                          # 浏览器关了页面

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
    resumed = app.panel.resume_interrupted()                  # 上次被中断的备课接着备（D-063）
    if resumed:
        print("接着备上次被中断的：" + "、".join(resumed), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        server.server_close()
