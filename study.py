"""学习环境命令行。用法见 README.md，题目格式见 docs/question-format.md。

    python study.py serve                    启动答题网页（http://127.0.0.1:8770/）
    python study.py status                   重新生成 progress.md
    python study.py weak                     输出给导师看的学习状态（JSON）
    python study.py grade <lesson> <qid> --score 0.5 --note "..."   导师批改简答题
    python study.py runs <lesson> [qid]      看代码题的作答过程（每次运行的结果和代码改动）
    python study.py log --concept dsa.hash --level 3 --score 1 --note "LeetCode 1"   记录课外练习
    python study.py time [--days 7]          每天学了多久、学了什么（D-016）
    python study.py time add --start 2026-09-25T15:30 --minutes 48 --note "看第 1 讲视频"   补录一段学习
    python study.py kg summary               学习者模型第 0 级：每个学科的掌握情况和薄弱点标题（D-020）
    python study.py kg related <单元>         第 1 级：和这个单元相关、还没掌握的节点
    python study.py kg show <节点>            第 2 级：一个节点的描述、先修、全部证据
    python study.py kg observe <节点> weak|ok "原话"   导师观察：把"哪里没跟上"记到具体节点上
    python study.py kg check                 检查知识图（id、先修是否存在、有没有环）
    python study.py course-eval <单元> [--plan <版本>] [--write]   课程评测：预测 vs 实际（D-019）
    python study.py lab verify <agent 运行目录名>   把课程计划的练习场从头走一遍（会执行 agent 写的命令，先审阅）
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys

from studykit import store


def cmd_serve(args) -> None:
    from studykit import server
    server.serve(args.port)


def cmd_status(args) -> None:
    store.write_progress_md()
    print(f"已更新 {store.PROGRESS_MD}")


def cmd_weak(args) -> None:
    syllabus = store.load_yaml(store.SYLLABUS)
    attempts = store.load_attempts()
    stats = store.concept_stats(attempts)
    _, pending = store.split_attempts(attempts)
    studied, untested = [], []
    for tid, topic in (syllabus.get("topics") or {}).items():
        for unit in topic.get("units") or []:
            if unit.get("status") in ("done", "watching"):
                studied.append({"topic": tid, **unit})
                untested += [c for c in unit.get("concepts") or [] if c not in stats]
    out = {
        "today": store.now().date().isoformat(),
        "studied_units": studied,
        "concepts": sorted(stats.values(), key=lambda s: (not s["weak"], s["mastery"])),
        "untested_concepts": untested,
        "pending_grading": [{k: a.get(k) for k in ("quiz", "qid", "concept", "level", "response", "ts")}
                            for a in pending],
        # 代码题的练习过程：跑了很多次才通过的题，即使最终满分也说明还不熟。详情用 study.py runs 看。
        "code_practice": store.practice_summary(store.load_runs())[:20],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


def cmd_runs(args) -> None:
    runs = store.load_runs(args.lesson, args.qid)
    if not runs:
        sys.exit(f"{args.lesson} 没有运行记录")
    prev_code: dict[str, str] = {}
    for i, r in enumerate(runs, 1):
        label = "提交" if r.get("kind") == "submit" else "运行"
        print(f"\n#{i} {r['ts'].replace('T', ' ')}  {r['qid']}  {label}  通过 {r.get('passed', 0)}/{r.get('total', 0)}")
        for f in r.get("failures") or []:
            where = f"（第 {'、'.join(map(str, f['lines']))} 行）" if f.get("lines") else ""
            msg = (f.get("message") or "").splitlines()[0][:100] if f.get("message") else ""
            print(f"   ✗ {f['name']}{where} {msg}")
        before = prev_code.get(r["qid"])
        if before is not None and before != r.get("code") and not args.no_diff:
            diff = difflib.unified_diff(before.splitlines(), (r.get("code") or "").splitlines(),
                                        "上一次", "这一次", lineterm="", n=1)
            print("   " + "\n   ".join(list(diff)[2:]))
        prev_code[r["qid"]] = r.get("code") or ""


def cmd_grade(args) -> None:
    _, pending = store.split_attempts(store.load_attempts())
    target = [a for a in pending if a["quiz"] == args.lesson and a["qid"] == args.qid]
    if not target:
        sys.exit(f"{args.lesson} {args.qid} 没有待批改的记录")
    p = target[-1]
    store.record(quiz=p["quiz"], qid=p["qid"], concept=p["concept"], level=p["level"],
                 checker=p.get("checker", "short"), response=p["response"], score=args.score,
                 result=store.result_of(args.score), grader="agent", note=args.note or "",
                 feedback=[], supersedes=p["id"])
    store.write_progress_md()
    print(f"已批改 {args.lesson} {args.qid}：{args.score}")


def cmd_log(args) -> None:
    store.record(quiz="external", qid="-", concept=args.concept, level=args.level, checker="external",
                 response="", score=args.score, result=store.result_of(args.score), grader="self",
                 note=args.note or "")
    store.write_progress_md()
    print("已记录。")


def cmd_time(args) -> None:
    from studykit import course, timeline
    if args.action == "add":
        if not args.start or not args.minutes:
            sys.exit("补录要写 --start 2026-09-25T15:30 和 --minutes 48")
        store.record_study(event="manual", start=args.start + (":00" if len(args.start) == 16 else ""),
                           minutes=args.minutes, note=args.note or "手动补录")
        print("已补录。")
        return
    print(timeline.report(args.days, plan_of=course.plan_of_event))


def cmd_kg(args) -> None:
    from studykit import knowledge
    if args.action == "summary":
        print(knowledge.summary())
    elif args.action == "related":
        print(json.dumps(knowledge.related(args.target), ensure_ascii=False, indent=2))
    elif args.action == "show":
        print(json.dumps(knowledge.show(args.target), ensure_ascii=False, indent=2))
    elif args.action == "observe":
        if args.polarity not in ("weak", "ok") or not args.note:
            sys.exit('用法：kg observe <节点> weak|ok "原话"')
        knowledge.observe(args.target, args.polarity, args.note)
        store.write_progress_md()
        print(f"已记录：{args.target} {args.polarity}")
    elif args.action == "check":
        errors = knowledge.validate(knowledge.load_graph())
        print("\n".join(errors) or "知识图没有问题。")
        if errors:
            sys.exit(1)


def cmd_course_eval(args) -> None:
    from studykit import course_eval
    result = course_eval.evaluate(args.unit, args.plan)
    text = course_eval.format_report(result)
    print(text)
    if args.write:
        d = store.ROOT / "runs" / "agents" / "tutor-prep" / result["plan"]
        if d.exists():
            (d / "learner_eval.md").write_text(text, encoding="utf-8")
            (d / "learner_eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写入 {d / 'learner_eval.md'}")
        else:
            print(f"找不到运行目录 {d}，没有写入")


def cmd_lab(args) -> None:
    from studykit import lab
    from studykit.agent_env import evaluate
    run_dir = evaluate.resolve_run("tutor-prep", args.run)
    plan = json.loads((run_dir / "output.json").read_text(encoding="utf-8"))
    result = lab.verify(plan, store.SANDBOX / "lab-verify" / run_dir.name)
    (run_dir / "lab_verify.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for sec in result["sections"]:
        tries = " ".join("✓" if t["rc"] == 0 else f"✗{t['rc']}" for t in sec["try"])
        cps = " ".join(f"检查点{c['idx']}：做之前 {sum(c['passed_before'])}/{len(c['passed_before'])} → 做之后 "
                       f"{sum(c['passed_after'])}/{len(c['passed_after'])}" for c in sec["checkpoints"])
        print(f"第 {sec['section']} 节 {sec['title']}  动手 {tries or '—'}  {cps}")
    for w in result["warnings"]:
        print("注意：" + w)
    for p_ in result["problems"]:
        print("问题：" + p_)
    print("通过" if result["ok"] else "没通过")
    if not result["ok"]:
        sys.exit(1)


def cmd_agent(args) -> None:
    from studykit.agent_env import evaluate, runner
    if args.action == "run":
        run_dir = runner.run(args.agent, args.unit, model=args.model)
        meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        print(f"运行目录：{run_dir}")
        print(f"提交：{'是' if meta['submitted'] else '否'} · 用时 {meta['seconds']}s · token {meta['tokens']} · "
              f"工具调用 {meta['tool_calls']} · prompt 版本 {meta['prompt_version']}")
        if meta["errors"]:
            print("错误：" + "；".join(map(str, meta["errors"])))
        if not args.no_eval and meta["submitted"]:
            print(evaluate.format_result(evaluate.evaluate(run_dir, use_judge=not args.no_judge)))
    elif args.action == "eval":
        print(evaluate.format_result(evaluate.evaluate(evaluate.resolve_run(args.agent, args.run), use_judge=not args.no_judge)))
    elif args.action == "report":
        print(evaluate.report(args.agent))
    elif args.action == "publish":
        print(f"已发布到 {evaluate.publish(evaluate.resolve_run(args.agent, args.run))}")


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="cs-study 学习环境")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="启动答题网页")
    s.add_argument("--port", type=int, default=8770)
    s.set_defaults(func=cmd_serve)
    sub.add_parser("status", help="重新生成 progress.md").set_defaults(func=cmd_status)
    sub.add_parser("weak", help="输出学习状态 JSON（给导师用）").set_defaults(func=cmd_weak)
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
    ag.add_argument("action", choices=["run", "eval", "report", "publish"])
    ag.add_argument("agent", help="agents/ 下的 agent 名，如 tutor-prep")
    ag.add_argument("--unit", help="run：单元 id，如 tools-01-shell")
    ag.add_argument("--run", help="eval / publish：运行目录名，默认最近一次")
    ag.add_argument("--model", help="run：临时换模型（覆盖 agent.yaml）")
    ag.add_argument("--no-eval", action="store_true", help="run：跑完不评测")
    ag.add_argument("--no-judge", action="store_true", help="只跑自动检查，不调用评分模型")
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
    ce.add_argument("--plan", help="课程版本（agent 运行目录名），默认当前发布的")
    ce.add_argument("--write", action="store_true", help="把报告写进 agent 的运行目录")
    ce.set_defaults(func=cmd_course_eval)
    lb = sub.add_parser("lab", help="练习场")
    lb.add_argument("action", choices=["verify"])
    lb.add_argument("run", nargs="?", help="tutor-prep 的运行目录名，默认最近一次")
    lb.set_defaults(func=cmd_lab)
    lg = sub.add_parser("log", help="记录课外练习（LeetCode、PortSwigger 靶场等）")
    lg.add_argument("--concept", required=True)
    lg.add_argument("--level", type=int, default=3, choices=[1, 2, 3, 4])
    lg.add_argument("--score", type=float, required=True)
    lg.add_argument("--note")
    lg.set_defaults(func=cmd_log)
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
