"""答题网页的本地服务器。只监听 127.0.0.1。

GET  /                      答题页面
GET  /api/lessons           所有课时
GET  /api/lesson?ref=...    一套题（每道题的 view + 最近一次作答结果）
GET  /api/runs?ref=&qid=    一道代码题的运行记录（每次的代码快照和测试结果）
GET  /api/units             已发布的课程页（D-010）
GET  /api/unit?id=...       一个单元的课程计划 + 学习进度
POST /api/unit/progress     记录打开 / 学完某一节
POST /api/act               作答过程中的交互（跑测试、执行终端命令），不记录
POST /api/submit            提交一道题：检验器判分 → 写入 attempts.jsonl → 更新 progress.md
"""
from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from studykit import checkers, course, lessons, store

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 2 * 1024 * 1024    # 代码题提交的代码也不会超过这个大小


def ctx_for(lesson: lessons.Lesson, qid: str) -> checkers.Ctx:
    return checkers.Ctx(lesson.ref, lesson.dir, store.SANDBOX / lesson.ref / qid)


def lesson_payload(ref: str, retake: bool = False) -> dict:
    lesson = lessons.load(ref)
    latest = {} if retake else store.latest_by_question(ref)
    questions = []
    for q in lesson.quiz.get("questions", []):
        checker = checkers.get(q["checker"])
        item = {k: q.get(k) for k in ("id", "checker", "level", "concept", "prompt", "kind")}
        item["level_name"] = store.LEVELS.get(q["level"], "")
        item.update(checker.view(q, ctx_for(lesson, q["id"])))
        prev = latest.get(q["id"])
        if prev:
            item["previous"] = previous_view(prev, lesson.answer_key(q["id"]), checker)
        questions.append(item)
    return {"ref": ref, "title": lesson.quiz.get("title", ref), "source": lesson.quiz.get("source", ""),
            "questions": questions}


def runs_payload(ref: str, qid: str) -> list[dict]:
    lesson = lessons.load(ref)
    lesson.question(qid)          # 确认题目存在
    return [{k: r.get(k) for k in ("id", "ts", "kind", "passed", "total", "failures", "code")}
            for r in store.load_runs(ref, qid)]


def previous_view(rec: dict, key: dict, checker: checkers.Checker) -> dict:
    out = {k: rec.get(k) for k in ("score", "result", "feedback", "response", "ts", "note")}
    tests = (rec.get("detail") or {}).get("tests")
    if tests is not None:
        out["tests"] = tests
    # WHY: 解析只在作答之后给；简答题要等批改完才给，否则等于提前公布评分点。
    if rec.get("result") != "pending":
        out["explain"] = key.get("explain", "")
    return out


def submit(ref: str, qid: str, response) -> dict:
    lesson = lessons.load(ref)
    q = lesson.question(qid)
    key = lesson.answer_key(qid)
    checker = checkers.get(q["checker"])
    verdict = checker.check(q, key, ctx_for(lesson, qid), response)
    rec = store.record(
        quiz=ref, qid=qid, concept=q["concept"], level=q["level"], checker=q["checker"],
        response=response, score=verdict.score,
        result="pending" if verdict.score is None else store.result_of(verdict.score),
        grader="auto" if verdict.score is not None else "pending",
        feedback=verdict.feedback, detail=verdict.detail,
    )
    store.write_progress_md()
    return previous_view(rec, key, checker)


def act(ref: str, qid: str, action: dict) -> dict:
    lesson = lessons.load(ref)
    q = lesson.question(qid)
    return checkers.get(q["checker"]).act(q, lesson.answer_key(qid), ctx_for(lesson, qid), action)


def make_handler(token: str, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

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
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _host_ok(self) -> bool:
            # WHY: 校验 Host 防 DNS rebinding：别的网站把自己的域名解析到 127.0.0.1 后就能读这个服务。
            return self.headers.get("Host") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._json({"error": "bad host"}, 403)
            url = urlparse(self.path)
            try:
                if url.path == "/":
                    html = (WEB / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", token)
                    return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                if url.path == "/api/lessons":
                    return self._json(lessons.list_all())
                if url.path == "/api/lesson":
                    qs = parse_qs(url.query)
                    return self._json(lesson_payload(qs.get("ref", [""])[0], qs.get("retake", ["0"])[0] == "1"))
                if url.path == "/api/units":
                    return self._json(course.list_units())
                if url.path == "/api/unit":
                    return self._json(course.payload(parse_qs(url.query).get("id", [""])[0]))
                if url.path == "/api/runs":
                    qs = parse_qs(url.query)
                    return self._json(runs_payload(qs.get("ref", [""])[0], qs.get("qid", [""])[0]))
            except (lessons.LessonError, course.CourseError, ValueError) as e:
                return self._json({"error": str(e)}, 400)
            self._json({"error": "not found"}, 404)

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
                if self.path == "/api/act":
                    return self._json(act(body["lesson"], body["qid"], body.get("action") or {}))
                if self.path == "/api/unit/progress":
                    course.record(body["unit"], body["section"], body["event"], body.get("minutes"))
                    return self._json(course.progress(body["unit"]))
                if self.path == "/api/submit":
                    return self._json(submit(body["lesson"], body["qid"], body.get("response")))
            except (lessons.LessonError, ValueError, KeyError) as e:
                return self._json({"error": str(e)}, 400)
            self._json({"error": "not found"}, 404)

    return Handler


def serve(port: int = 8770) -> None:
    token = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(token, port))
    print(f"答题页面：http://127.0.0.1:{port}/   （Ctrl+C 停止）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
