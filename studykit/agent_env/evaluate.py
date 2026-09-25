"""评测一次 agent 运行：自动检查（确定性）+ 评分模型按 rubric 打分，结果按 prompt 版本汇总。

每次评测写两处：
    <run_dir>/eval.json                      这次运行的完整评测
    agents/<agent>/evals/results.jsonl       一行摘要（进 git），用来比较不同 prompt 版本
导师的人工意见写在 <run_dir>/review.md。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path

from studykit import store
from studykit.agent_env import runner, tools



def resolve_run(agent_name: str, run_id: str | None) -> Path:
    base = runner.RUNS / agent_name
    if run_id:
        d = base / run_id
        if not d.is_dir() or not d.resolve().is_relative_to(base.resolve()):
            raise runner.AgentError(f"没有这次运行：{run_id}")
        return d
    runs = sorted(p for p in base.glob("*") if p.is_dir()) if base.exists() else []
    if not runs:
        raise runner.AgentError(f"{agent_name} 还没有运行记录")
    return runs[-1]


def _case_for(agent: runner.Agent, unit: str) -> dict:
    cases = store.load_yaml(agent.dir / agent.spec["eval"]["cases"]).get("cases") or []
    return next((c for c in cases if c.get("unit") == unit), {})


def check(run_dir: Path, _get=tools._http_get) -> list[dict]:
    """确定性检查。每项：id / desc / ok / detail。"""
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    agent = runner.load_agent(meta["agent"])
    out_path = run_dir / "output.md"
    results = []

    def add(id_, desc, ok, detail=""):
        results.append({"id": id_, "desc": desc, "ok": bool(ok), "detail": detail})

    add("submitted", "按时提交了结果", out_path.exists(), "" if out_path.exists() else f"exit={meta.get('exit_code')}")
    if not out_path.exists():
        return results
    md = out_path.read_text(encoding="utf-8")
    spec = json.loads((run_dir / "context.json").read_text(encoding="utf-8"))
    ctx = tools.RunContext.from_spec(run_dir, spec)

    plan_path = run_dir / "output.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        problems = tools.check_plan(ctx, plan)
        add("plan_valid", "课程计划通过环境检查（预算、必填项、出处）", not problems, "；".join(problems))
        total, n = tools.plan_minutes(plan), len(tools.sections_of(plan))
        add("budget", f"总时长不超过预算 {ctx.unit_budget_minutes} 分钟", total <= ctx.unit_budget_minutes, f"{total} 分钟")
        add("shape", "4–8 个小节", 4 <= n <= 8, f"{n} 节")
        secs = tools.sections_of(plan)
        items = [c for sec in secs for c in sec.get("checkpoint") or []]
        lab_items = [c for c in items if c.get("type") == "lab"]
        add("checkpoints", "每节都有检查点，其中至少一半的小节有练习场任务（D-013、D-014）",
            all(sec.get("checkpoint") for sec in secs) and 2 * sum(any(c.get("type") == "lab" for c in sec.get("checkpoint") or []) for sec in secs) >= n,
            f"{len(items)} 道，练习场任务 {len(lab_items)} 道")
        traps = [t for c in items for t in c.get("traps") or []] + [k["trap"] for c in lab_items for k in c.get("checks") or [] if k.get("trap")]
        add("traps", "至少写了 3 个挂在检查点上的坑（答错时才出现，D-015）", len(traps) >= 3, f"{len(traps)} 个")
        concepts = [c for c in items if c.get("concept")]
        add("concepts", "检查点都标了检验的知识节点（D-020）", len(concepts) == len(items), f"{len(concepts)}/{len(items)}")
    else:
        required = ctx.required_sections
        headings = [" ".join(h.split()) for h in re.findall(r"^##\s+(.+?)\s*$", md, re.MULTILINE)]
        order = [h for h in headings if h in required]
        add("sections", "必需章节齐全且顺序正确", order == required, f"实际：{headings}")
        add("length", "长度适中（600–7000 字）", 600 <= len(md) <= 7000, f"{len(md)} 字")

    seen = tools.grounded_urls(ctx)
    urls = sorted(tools.extract_urls(md))
    ungrounded = [u for u in urls if tools.url_key(u) not in seen]
    add("grounded", "每个链接都是打开过的页面", not ungrounded, "；".join(ungrounded))

    fetched_ok = {tools.url_key(f["url"]) for f in ctx.fetched() if f.get("ok")}
    dead = []
    for u in urls[:20]:
        if tools.url_key(u) in fetched_ok:
            continue
        try:
            status, _, _ = _get(u, timeout=15)
            if status >= 400:
                dead.append(f"{u}（{status}）")
        except Exception as e:  # noqa: BLE001  任何网络错误都算打不开
            dead.append(f"{u}（{type(e).__name__}）")
    add("reachable", "链接都能打开", not dead, "；".join(dead))

    pages = len(fetched_ok)
    add("researched", "至少读了 2 个页面再写", pages >= 2, f"打开成功 {pages} 个页面")

    case = _case_for(agent, meta["unit"])
    for s in case.get("must_mention") or []:
        add(f"mention:{s}", f"提到「{s}」", s.lower() in md.lower())
    for s in case.get("must_not_mention") or []:
        add(f"avoid:{s}", f"没有出现「{s}」", s.lower() not in md.lower())
    return results


# ---------- 评分模型 ----------

JUDGE_SYSTEM = """你是一名严格的评审，评估一份由 AI 助教写的课前学习指南。
按给定的评分标准逐项打 1-5 分（5 最好），每项用一句话说明理由，指出最该改进的一处。
只输出一个 JSON 对象，不要任何其他文字，格式：
{"scores": {"<标准 id>": {"score": <1-5>, "reason": "<一句话>"}}, "top_issue": "<最大的问题>", "suggestion": "<对助教的 prompt 最值得做的一处修改>",
 "suspect_claims": [{"claim": "<你怀疑没有出处的说法>", "quote": "<从指南里原样复制的一小段文字，10-40 字>"}]}
你看不到助教读过的页面正文。怀疑某个具体事实（版本号、章节名、练习内容、数字）没有出处时，把它列进 suspect_claims，
程序会拿它去核对助教实际读到的页面；不要仅凭怀疑就在 grounded 上扣分。"""


def rubric_ids(rubric: str) -> list[str]:
    return re.findall(r"^- `([a-z_]+)`", rubric, re.MULTILINE)


def parse_judge(text: str, ids: list[str]) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        raise ValueError("评分模型没有输出 JSON")
    data = json.loads(m.group(0))
    scores = data.get("scores") or {}
    missing = [i for i in ids if i not in scores]
    if missing:
        raise ValueError(f"评分缺少这些标准：{missing}")
    for i in ids:
        s = scores[i].get("score")
        if not isinstance(s, (int, float)) or not 1 <= s <= 5:
            raise ValueError(f"{i} 的分数不在 1-5：{s}")
    data["avg"] = round(sum(scores[i]["score"] for i in ids) / len(ids), 2)
    return data


def judge(run_dir: Path, timeout: int = 600) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    agent = runner.load_agent(meta["agent"])
    rubric = (agent.dir / agent.spec["eval"]["rubric"]).read_text(encoding="utf-8")
    fetched = [f"- {f.get('title') or '(无标题)'} — {f['url']}" for f in
               tools.RunContext(run_dir, set(), []).fetched() if f.get("ok")]
    (run_dir / "judge_input.md").write_text("\n\n".join([
        "# 评分标准\n\n" + rubric,
        "# 助教拿到的简报\n\n" + (run_dir / "brief.md").read_text(encoding="utf-8"),
        "# 助教实际打开过的页面\n\n" + ("\n".join(fetched) or "（没有）"),
        "# 助教写的指南\n\n" + (run_dir / "output.md").read_text(encoding="utf-8"),
    ]), encoding="utf-8")
    jm = agent.spec["eval"]["judge_model"]
    cmd = runner.pi_command() + [
        "--print", "--no-session", "--no-context-files", "--no-skills", "--no-prompt-templates",
        "--no-extensions", "--no-approve", "--no-tools",
        "--provider", jm["provider"], "--model", jm["id"], "--system-prompt", JUDGE_SYSTEM,
        "@judge_input.md", "按评分标准评估上面的指南，只输出 JSON。",
    ]
    env = {**os.environ, **runner.load_local_env(), "PI_CODING_AGENT_DIR": str(runner.PI_HOME),
           "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0"}
    proc = subprocess.run(cmd, cwd=run_dir, env=env, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    (run_dir / "judge_raw.txt").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
    result = parse_judge(proc.stdout, rubric_ids(rubric))
    result["model"] = f"{jm['provider']}/{jm['id']}"
    result["suspect_checks"] = verify_suspicions(run_dir, result.get("suspect_claims") or [])
    return result


def page_texts(run_dir: Path) -> str:
    """agent 通过 fetch_url 实际读到的全部页面文字（从事件流里取，和 agent 看到的完全一致）。"""
    texts = []
    path = run_dir / "events.jsonl"
    for line in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "tool_execution_end" and ev.get("toolName") == "fetch_url" and not ev.get("isError"):
            for block in (ev.get("result") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text", ""))
    return "\n".join(texts)


def _squash(s: str) -> str:
    return re.sub(r"[\s`*_]+", "", s or "").lower()


def verify_suspicions(run_dir: Path, claims: list[dict]) -> list[dict]:
    """评分模型提出的疑点，逐条到 agent 读过的页面里找原文。WHY: 评分模型看不到页面，只能猜；核对交给程序。"""
    corpus = _squash(page_texts(run_dir))
    out = []
    for c in claims:
        quote = str(c.get("quote") or "")
        # 引文里可能夹着指南自己的中文说明，只要其中的"事实部分"（英文、数字、代码）在页面里出现就算找到
        facts = [f for f in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/:+-]*(?:\s+[A-Za-z0-9][A-Za-z0-9._/:+-]*)*", quote) if len(f) >= 3]
        needles = [_squash(f) for f in facts] or [_squash(quote)]
        found = bool(needles) and all(n and n in corpus for n in needles)
        out.append({**c, "found_in_pages": found})
    return out


# ---------- 汇总 ----------

def evaluate(run_dir: Path, use_judge: bool = True) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    checks = check(run_dir)
    judged = None
    if use_judge and (run_dir / "output.md").exists():
        try:
            judged = judge(run_dir)
        except Exception as e:  # noqa: BLE001  评分失败不影响自动检查的结果
            judged = {"error": str(e)}
    result = {"run": run_dir.name, "meta": meta, "checks": checks, "judge": judged,
              "evaluated": dt.datetime.now().isoformat(timespec="seconds")}
    (run_dir / "eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    agent = runner.load_agent(meta["agent"])
    summary = {
        "schema_version": 1, "run": run_dir.name, "unit": meta["unit"], "prompt_version": meta["prompt_version"],
        "env_version": meta.get("env_version"), "model": meta["model"], "checks_passed": sum(c["ok"] for c in checks), "checks_total": len(checks),
        "failed_checks": [c["id"] for c in checks if not c["ok"]],
        "judge_avg": (judged or {}).get("avg"),
        "judge_scores": {k: v.get("score") for k, v in ((judged or {}).get("scores") or {}).items()},
        "tokens": meta.get("tokens"), "cost_usd": meta.get("cost_usd"), "seconds": meta.get("seconds"),
        "evaluated": result["evaluated"],
    }
    results_path = agent.dir / "evals" / "results.jsonl"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    return result


def format_result(result: dict) -> str:
    lines = [f"\n== 评测 {result['run']} =="]
    for c in result["checks"]:
        lines.append(f"  {'✓' if c['ok'] else '✗'} {c['desc']}" + (f"  —— {c['detail']}" if c["detail"] and not c["ok"] else ""))
    j = result.get("judge")
    if j and "error" in j:
        lines.append(f"  评分模型出错：{j['error']}")
    elif j:
        lines.append(f"  评分（{j['model']}）平均 {j['avg']} / 5")
        for k, v in j["scores"].items():
            lines.append(f"    {k}: {v['score']} — {v.get('reason', '')}")
        for c in j.get("suspect_checks") or []:
            verdict = "页面里有，怀疑不成立" if c["found_in_pages"] else "页面里没找到，可能是编造"
            lines.append(f"    疑点：{c.get('claim', '')}「{c.get('quote', '')}」→ {verdict}")
        lines.append(f"  最大问题：{j.get('top_issue', '')}")
        lines.append(f"  改 prompt 的建议：{j.get('suggestion', '')}")
    return "\n".join(lines)


def report(agent_name: str) -> str:
    agent = runner.load_agent(agent_name)
    path = agent.dir / "evals" / "results.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []
    if not rows:
        return "还没有评测记录。"
    by = defaultdict(list)
    for r in rows:
        by[(r["prompt_version"], r.get("env_version") or "—", r["model"])].append(r)
    out = ["| prompt 版本 | 环境版本 | 模型 | 运行次数 | 自动检查通过率 | 评分均值 | 平均 token | 常见失败 |", "|---|---|---|---|---|---|---|---|"]
    for (pv, ev, model), rs in by.items():
        rate = sum(r["checks_passed"] for r in rs) / max(1, sum(r["checks_total"] for r in rs))
        judged = [r["judge_avg"] for r in rs if r.get("judge_avg") is not None]
        fails = defaultdict(int)
        for r in rs:
            for f in r["failed_checks"]:
                fails[f] += 1
        common = "、".join(f"{k}×{v}" for k, v in sorted(fails.items(), key=lambda kv: -kv[1])[:3])
        judge_cell = f"{sum(judged) / len(judged):.2f}" if judged else "—"
        tokens = int(sum(r.get("tokens") or 0 for r in rs) / len(rs))
        out.append(f"| {pv} | {ev} | {model} | {len(rs)} | {rate:.0%} | {judge_cell} | {tokens} | {common or '无'} |")
    return "\n".join(out)


def publish(run_dir: Path) -> Path:
    """把通过自动检查的产出发布给学习者。没通过的不发布。"""
    ev_path = run_dir / "eval.json"
    if not ev_path.exists():
        raise runner.AgentError("先评测再发布：python study.py agent eval <agent>")
    ev = json.loads(ev_path.read_text(encoding="utf-8"))
    failed = [c["desc"] for c in ev["checks"] if not c["ok"]]
    if failed:
        raise runner.AgentError("自动检查没通过，不能发布：" + "；".join(failed))
    meta = ev["meta"]
    plan_path = run_dir / "output.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
    if plan and plan.get("lab"):
        # INVARIANT: 有练习场的课程，导师审阅命令后跑过 lab verify 并通过，才能发布（D-014）。
        vpath = run_dir / "lab_verify.json"
        if not vpath.exists() or not json.loads(vpath.read_text(encoding="utf-8")).get("ok"):
            raise runner.AgentError(f"练习场还没验证通过：先读 {run_dir / 'output.md'} 里的命令，再运行 python study.py lab verify {run_dir.name}")
    agent = runner.load_agent(meta["agent"])
    dest = store.ROOT / agent.spec["output"]["publish_to"].format(unit=meta["unit"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    judge_avg = (ev.get("judge") or {}).get("avg")
    provenance = {"agent": meta["agent"], "prompt_version": meta["prompt_version"],
                  "env_version": meta.get("env_version"), "model": f"{meta['provider']}/{meta['model']}",
                  "run": meta["run_id"], "judge_avg": judge_avg, "published": dt.datetime.now().isoformat(timespec="seconds")}
    if dest.suffix == ".json":
        from studykit import knowledge
        text = json.dumps({"schema_version": tools.PLAN_SCHEMA_VERSION, "unit": meta["unit"], "provenance": provenance, **plan},
                          ensure_ascii=False, indent=2)
        dest.write_text(text, encoding="utf-8")
        # 每一版都存一份：两版对比、给旧事件找到它属于哪一版（course.plan_of_event）。
        hist = dest.parent / "history" / meta["unit"] / f"{meta['run_id']}.json"
        hist.parent.mkdir(parents=True, exist_ok=True)
        hist.write_text(text, encoding="utf-8")
        # agent 提议的知识节点，导师审阅（发布）后并入知识图；已有的节点不覆盖（D-020）。
        added = knowledge.add_nodes([{**n, "units": n.get("units") or [meta["unit"]]} for n in plan.get("nodes") or []])
        if added:
            print(f"知识图新增 {len(added)} 个节点：{', '.join(added)}")
    else:
        header = (f"<!-- 由助教 agent {meta['agent']} 生成 · prompt 版本 {meta['prompt_version']} · "
                  f"模型 {provenance['model']} · 运行 {meta['run_id']} · 评分 {judge_avg or '—'}/5 -->\n\n")
        dest.write_text(header + (run_dir / "output.md").read_text(encoding="utf-8"), encoding="utf-8")
    return dest
