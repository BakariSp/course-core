"""学习环境命令行（命令行适配器）。用法见 README.md，题目格式见 docs/question-format.md。

    python study.py serve                    启动答题网页（http://127.0.0.1:8770/）
    python study.py status                   生成 progress.md
    python study.py weak                     输出给导师看的学习状态（JSON）
    python study.py export                   导出证据和 agent 运行记录（JSONL，进 git；数据库本身不进 git）
    python study.py grade <lesson> <qid> --score 0.5 --note "..."   导师批改简答题
    python study.py runs <lesson> [qid]      看代码题的作答过程（每次运行的结果和代码改动）
    python study.py log --concept dsa.hash --level 3 --score 1 --note "LeetCode 1"   记录课外练习
    python study.py time [--days 7]          每天学了多久、学了什么（D-016）
    python study.py time add --start 2026-09-25T15:30 --minutes 48 --note "看第 1 讲视频"   补录一段学习
    python study.py kg summary|related <单元>|show <节点>|check   学习者模型的分级查询（D-020）
    python study.py kg observe <节点> weak|ok "原话"   导师观察：把"哪里没跟上"记到具体节点上
    python study.py course-eval <单元> [--plan <版本>] [--write]   课程评测：预测 vs 实际（D-019）
    python study.py prepare <单元> [--from <运行>]   备课：生成 → 检验 → 定点修复 → 通过就发布（D-035，PRD_V2 阶段 A）
    python study.py agent run|eval|review|report|publish ...   助教 agent（D-022）
    python study.py agent versions tutor-prep            每一版改了哪些组成（D-033；tutor-prep#judge、short-grader 同理）
    python study.py agent diff tutor-prep --a 1584 --b ecc2   两个版本逐部件的 diff（版本号写前几位就行）
    python study.py agent review tutor-prep --verdict revise --issue "prompt:字数上限" --note "..."   导师审阅（结构化）
    python study.py lab verify [<运行>]      把课程计划的练习场从头走一遍（会执行 agent 写的命令，先审阅）
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys

from studykit.bootstrap import build


def cmd_serve(app, args) -> None:
    from studykit import web
    web.serve(args.port, app)


def cmd_status(app, args) -> None:
    path = app.config.root / "progress.md"
    path.write_text(app.learner.progress_md(), encoding="utf-8")
    print(f"已更新 {path}")


def cmd_export(app, args) -> None:
    """数据库不进 git；导出成 JSONL 进 git（D-021）。"""
    def dump(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
        print(f"{path}（{len(rows)} 行）")
    dump(app.config.root / "progress" / "evidence.jsonl", app.learner.export())
    for agent in sorted(p.name for p in (app.config.root / "agents").iterdir() if (p / "agent.yaml").exists()):
        dump(app.config.root / "agents" / agent / "evals" / "runs.jsonl", app.harness.export(agent))


def cmd_weak(app, args) -> None:
    print(json.dumps(app.learner.weak(), ensure_ascii=False, indent=2))


def cmd_runs(app, args) -> None:
    runs = app.assessment.runs(args.lesson, args.qid)
    if not runs:
        sys.exit(f"{args.lesson} 没有运行记录")
    prev: dict[str, str] = {}
    for i, r in enumerate(runs, 1):
        label = "提交" if r["kind"] == "submit" else "运行"
        print(f"\n#{i} {r['ts'].replace('T', ' ')}  {r['qid']}  {label}  通过 {r.get('passed') or 0}/{r.get('total') or 0}")
        for f in r.get("failures") or []:
            where = f"（第 {'、'.join(map(str, f['lines']))} 行）" if f.get("lines") else ""
            msg = (f.get("message") or "").splitlines()[0][:100] if f.get("message") else ""
            print(f"   ✗ {f['name']}{where} {msg}")
        before = prev.get(r["qid"])
        if before is not None and before != r.get("code") and not args.no_diff:
            diff = difflib.unified_diff(before.splitlines(), (r.get("code") or "").splitlines(),
                                        "上一次", "这一次", lineterm="", n=1)
            print("   " + "\n   ".join(list(diff)[2:]))
        prev[r["qid"]] = r.get("code") or ""


def cmd_grade(app, args) -> None:
    app.assessment.grade(args.lesson, args.qid, args.score, args.note or "")
    print(f"已批改 {args.lesson} {args.qid}：{args.score}")


def cmd_log(app, args) -> None:
    app.assessment.log_practice(args.concept, args.level, args.score, args.note or "")
    print("已记录。")


def cmd_time(app, args) -> None:
    if args.action == "add":
        if not args.start or not args.minutes:
            sys.exit("补录要写 --start 2026-09-25T15:30 和 --minutes 48")
        app.learner.log_time(args.start + (":00" if len(args.start) == 16 else ""), args.minutes, args.note or "")
        print("已补录。")
        return
    print(app.learner.time_report(args.days))


def cmd_kg(app, args) -> None:
    if args.action == "summary":
        print(app.learner.summary())
    elif args.action == "related":
        print(json.dumps(app.learner.related(args.target), ensure_ascii=False, indent=2))
    elif args.action == "show":
        print(json.dumps(app.learner.show(args.target), ensure_ascii=False, indent=2))
    elif args.action == "observe":
        if args.polarity not in ("weak", "ok") or not args.note:
            sys.exit('用法：kg observe <节点> weak|ok "原话"')
        app.learner.observe(args.target, args.polarity, args.note)
        print(f"已记录：{args.target} {args.polarity}")
    elif args.action == "check":
        errors = app.learner.check_graph()
        print("\n".join(errors) or "知识图没有问题。")
        if errors:
            sys.exit(1)


def cmd_course_eval(app, args) -> None:
    result = app.course.evaluate(args.unit, args.plan)
    print(app.course.format_evaluation(result))
    if args.write:
        grade = app.harness.grade_outcome(result)
        print(f"已记为运行 {grade.run} 的学习结果评分（{grade.id}）")


def cmd_agent(app, args) -> None:
    h = app.harness
    if args.action == "run":
        if not args.unit:
            sys.exit("run 要写 --unit")
        run = h.run(args.agent, args.unit, model=args.model)
        print(h.format_run(run))
        if not args.no_eval and run.submitted:
            print(h.format_eval(h.evaluate(run, use_judge=not args.no_judge)))
    elif args.action == "eval":
        print(h.format_eval(h.evaluate(h.resolve(args.agent, args.run), use_judge=not args.no_judge)))
    elif args.action == "report":
        print(h.report(args.agent))
    elif args.action == "review":
        issues = [dict(zip(("layer", "what"), i.split(":", 1))) for i in args.issue or []]
        h.review(h.resolve(args.agent, args.run), args.verdict, args.note or "", [{k: v.strip() for k, v in i.items()} for i in issues])
        print("已记录审阅。")
    elif args.action == "versions":
        for v in h.versions.history(args.agent):
            changed = "、".join(c["part"] for c in v["changed"]) or "—"
            print(f"{v['variant']}  {v['first_seen']}  跑了 {v['runs']} 次  改了：{changed}")
    elif args.action == "diff":
        if not (args.a and args.b):
            sys.exit("diff 要写 --a 和 --b（版本号前几位）")
        d = h.versions.diff(args.a, args.b, agent=args.agent)
        print(f"{d['a']} → {d['b']}")
        for part in d["parts"]:
            print(f"\n== {part['part']}（{part['change']}）==\n{part['diff']}")
    elif args.action == "publish":
        added = h.publish(h.resolve(args.agent, args.run))
        print("已发布。" + (f"知识图新增 {len(added)} 个节点：{', '.join(added)}" if added else ""))


def cmd_prepare(app, args) -> None:
    r = app.prep.prepare(args.unit, start_from=args.start_from, model=args.model)
    for i, rd in enumerate(r["rounds"], 1):
        blocks = [f for f in rd["findings"] if f["severity"] == "block"]
        print(f"第 {i} 轮  {rd['run']}  ${rd['cost_usd']}  " + (f"{len(blocks)} 个阻断" if blocks else "通过"))
        for f in blocks:
            print(f"    [{f['evaluator']}] {f['address'] or '整份'}：{f['what'][:160]}")
    state = {"accepted": "通过", "escalated": "需要你决定"}[r["status"]]
    print(f"{state}，共 ${r['spent_usd']}。" + ("已发布：/?unit=" + r["unit"] if r["published"]
                                               else "没有发布（已经开始学的单元要你确认）" if r["status"] == "accepted" else ""))


def cmd_lab(app, args) -> None:
    result = app.harness.verify_lab(app.harness.resolve("tutor-prep", args.run))
    for sec in result["sections"]:
        tries = " ".join("✓" if t["rc"] == 0 else f"✗{t['rc']}" for t in sec["try"])
        cps = " ".join(f"检查点{c['idx']}：做之前 {sum(c['passed_before'])}/{len(c['passed_before'])} → 做之后 "
                       f"{sum(c['passed_after'])}/{len(c['passed_after'])}" for c in sec["checkpoints"])
        print(f"第 {sec['section']} 节 {sec['title']}  动手 {tries or '—'}  {cps}")
    for f in result["findings"]:
        print(("问题" if f["severity"] == "block" else "注意") + f"（{f['address'] or '整份'}）：" + f["what"])
    print("通过" if result["ok"] else "没通过")
    if not result["ok"]:
        sys.exit(1)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="cs-study 学习环境")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="启动答题网页")
    s.add_argument("--port", type=int, default=8770)
    s.set_defaults(func=cmd_serve)
    sub.add_parser("status", help="生成 progress.md").set_defaults(func=cmd_status)
    sub.add_parser("weak", help="输出学习状态 JSON（给导师用）").set_defaults(func=cmd_weak)
    sub.add_parser("export", help="把证据和 agent 运行记录导出成 JSONL（进 git）").set_defaults(func=cmd_export)
    g = sub.add_parser("grade", help="批改一道待批改的题")
    g.add_argument("lesson")
    g.add_argument("qid")
    g.add_argument("--score", type=float, required=True, help="0 到 1")
    g.add_argument("--note", help="错在哪个概念")
    g.set_defaults(func=cmd_grade)
    r = sub.add_parser("runs", help="看代码题的作答过程：每次运行的结果和代码改动")
    r.add_argument("lesson")
    r.add_argument("qid", nargs="?")
    r.add_argument("--no-diff", action="store_true", help="不显示代码改动")
    r.set_defaults(func=cmd_runs)
    ag = sub.add_parser("agent", help="运行 / 评测 / 发布助教 agent 的产出")
    ag.add_argument("action", choices=["run", "eval", "review", "report", "publish", "versions", "diff"])
    ag.add_argument("agent", help="agents/ 下的 agent 名，如 tutor-prep")
    ag.add_argument("--unit", help="run：单元 id，如 tools-01-shell")
    ag.add_argument("--run", help="eval / publish：运行 id，默认最近一次")
    ag.add_argument("--model", help="run：临时换模型（覆盖 agent.yaml）")
    ag.add_argument("--no-eval", action="store_true", help="run：跑完不评测")
    ag.add_argument("--no-judge", action="store_true", help="只跑自动检查，不调用评分模型")
    ag.add_argument("--verdict", choices=["publish", "revise", "reject"], help="review：结论")
    ag.add_argument("--issue", action="append", help='review：问题，写成 "层:内容"，层是 prompt/context/tools/model/runtime/eval，可写多次')
    ag.add_argument("--note", help="review：补充说明")
    ag.add_argument("--a", help="diff：旧版本号（前几位即可）")
    ag.add_argument("--b", help="diff：新版本号（前几位即可）")
    ag.set_defaults(func=cmd_agent)
    t = sub.add_parser("time", help="学习时间：每天学了多久、学了什么")
    t.add_argument("action", nargs="?", choices=["report", "add"], default="report")
    t.add_argument("--days", type=int, help="只看最近几天")
    t.add_argument("--start", help="add：开始时间，如 2026-09-25T15:30")
    t.add_argument("--minutes", type=float, help="add：多少分钟")
    t.add_argument("--note", help="add：学了什么")
    t.set_defaults(func=cmd_time)
    k = sub.add_parser("kg", help="学习者模型：知识图、薄弱点（分级查询）")
    k.add_argument("action", choices=["summary", "related", "show", "observe", "check"])
    k.add_argument("target", nargs="?", help="related：单元 id；show / observe：节点 id")
    k.add_argument("polarity", nargs="?", help="observe：weak 或 ok")
    k.add_argument("note", nargs="?", help="observe：学习者的原话或导师看到的现象")
    k.set_defaults(func=cmd_kg)
    ce = sub.add_parser("course-eval", help="课程评测：预测 vs 实际")
    ce.add_argument("unit")
    ce.add_argument("--plan", help="课程版本（运行 id），默认当前发布的")
    ce.add_argument("--write", action="store_true", help="记为产出这版课程的那次运行的学习结果评分")
    ce.set_defaults(func=cmd_course_eval)
    pr = sub.add_parser("prepare", help="备课：生成 → 检验 → 定点修复 → 通过就发布")
    pr.add_argument("unit")
    pr.add_argument("--from", dest="start_from", help="从已有的一次 tutor-prep 运行接着检验和修复（不重新生成）")
    pr.add_argument("--model", help="临时换生成模型")
    pr.set_defaults(func=cmd_prepare)
    lb = sub.add_parser("lab", help="练习场")
    lb.add_argument("action", choices=["verify"])
    lb.add_argument("run", nargs="?", help="tutor-prep 的运行 id，默认最近一次")
    lb.set_defaults(func=cmd_lab)
    lg = sub.add_parser("log", help="记录课外练习（LeetCode、PortSwigger 靶场等）")
    lg.add_argument("--concept", required=True)
    lg.add_argument("--level", type=int, default=3, choices=[1, 2, 3, 4])
    lg.add_argument("--score", type=float, required=True)
    lg.add_argument("--note")
    lg.set_defaults(func=cmd_log)
    return p


def main(argv: list[str] | None = None, app=None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    app = app or build()
    try:
        args.func(app, args)
    except ValueError as e:                  # 领域错误：给人看的一句话，不要堆栈
        sys.exit(str(e))
