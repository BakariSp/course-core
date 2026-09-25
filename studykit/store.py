"""学习数据：课程进度、答题记录、掌握度计算、progress.md 生成。与检验器种类无关。"""
from __future__ import annotations

import datetime as dt
import json
import sys
import threading
import uuid
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
LESSONS = ROOT / "lessons"
SANDBOX = ROOT / ".sandbox"
PROGRESS_DIR = ROOT / "progress"
SYLLABUS = PROGRESS_DIR / "syllabus.yaml"
ATTEMPTS = PROGRESS_DIR / "attempts.jsonl"
RUNS = PROGRESS_DIR / "runs.jsonl"
PROGRESS_MD = ROOT / "progress.md"

SCHEMA_VERSION = 1
LEVELS = {1: "记忆", 2: "理解", 3: "应用", 4: "分析"}
PASS_SCORE = 0.7    # 某一级最近几次的平均分达到这个值，算这一级通过
WINDOW = 3          # 只看每一级最近 3 次
STALE_DAYS = 14     # 通过后超过这么多天没再练，标记为需复习
IGNORED_TOPICS = {"demo"}

_write_lock = threading.Lock()


def now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_attempts() -> list[dict]:
    return _read_jsonl(ATTEMPTS)


def _append(path: Path, fields: dict) -> dict:
    # WHY: 网页服务器是多线程的，不加锁时两次写入可能交错成半行。
    rec = {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex[:12], "ts": now().isoformat(), **fields}
    with _write_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"警告：{path.name} 有一行无法解析，已跳过：{line[:60]}", file=sys.stderr)
    return out


def record(**fields) -> dict:
    # INVARIANT: attempts.jsonl 只追加不改写。它是答题记录的唯一事实来源，progress.md 由它生成。
    return _append(ATTEMPTS, fields)


def record_run(**fields) -> dict:
    """记录一次代码运行（「运行测试」和「提交」都算）。

    INVARIANT: runs.jsonl 只追加不改写。它记录作答过程（每次的代码快照和测试结果），
    不参与掌握度计算——掌握度只看提交结果（attempts.jsonl）。导师批改时用它看解题过程。
    """
    return _append(RUNS, fields)


def load_runs(quiz: str | None = None, qid: str | None = None) -> list[dict]:
    return [r for r in _read_jsonl(RUNS)
            if (quiz is None or r.get("quiz") == quiz) and (qid is None or r.get("qid") == qid)]


def practice_summary(runs: list[dict]) -> list[dict]:
    """每道代码题的练习过程：跑了几次、第几次第一次全部通过。"""
    by: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        by[(r["quiz"], r["qid"])].append(r)
    out = []
    for (quiz, qid), rs in by.items():
        rs.sort(key=lambda r: r["ts"])
        first_pass = next((i + 1 for i, r in enumerate(rs) if r.get("total") and r["passed"] == r["total"]), None)
        out.append({"quiz": quiz, "qid": qid, "concept": rs[-1].get("concept"), "runs": len(rs),
                    "first_full_pass_at_run": first_pass, "last": rs[-1]["ts"][:16],
                    "last_result": f"{rs[-1].get('passed', 0)}/{rs[-1].get('total', 0)}"})
    return sorted(out, key=lambda s: s["last"], reverse=True)


def result_of(score: float) -> str:
    if score >= PASS_SCORE:
        return "pass"
    return "partial" if score > 0 else "fail"


# ---------- 掌握度 ----------

def split_attempts(attempts: list[dict]) -> tuple[list[dict], list[dict]]:
    """返回（已判分的记录, 仍待批改的记录）。导师批改时写一条新记录，用 supersedes 指向被批改的那条。"""
    superseded = {a["supersedes"] for a in attempts if a.get("supersedes")}
    graded = [a for a in attempts if a.get("result") != "pending"]
    pending = [a for a in attempts if a.get("result") == "pending" and a["id"] not in superseded]
    return graded, pending


def concept_stats(attempts: list[dict], today: dt.date | None = None) -> dict[str, dict]:
    """每个概念的掌握度 = 最近几次平均分达标的最高一级（0-4）。有一级没达标或太久没练，就算薄弱。"""
    today = today or now().date()
    graded, _ = split_attempts(attempts)
    by: dict[str, dict[int, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for a in graded:
        by[a["concept"]][int(a.get("level", 1))].append(a)

    stats = {}
    for concept, levels in by.items():
        passed, failing, last_pass = [], [], None
        for level, recs in levels.items():
            recs.sort(key=lambda r: r["ts"])
            recent = recs[-WINDOW:]
            if sum(r["score"] for r in recent) / len(recent) >= PASS_SCORE:
                passed.append(level)
                ts = max(r["ts"] for r in recs if r["score"] >= PASS_SCORE)
                last_pass = max(last_pass or ts, ts)
            else:
                failing.append(level)
        all_recs = sorted((r for recs in levels.values() for r in recs), key=lambda r: r["ts"])
        mastery = max(passed, default=0)
        stale = bool(last_pass) and (today - dt.date.fromisoformat(last_pass[:10])).days > STALE_DAYS
        stats[concept] = {
            "concept": concept,
            "topic": concept.split(".")[0],
            "mastery": mastery,
            "target_level": min(4, min(failing) if failing else mastery + 1),
            "failing_levels": sorted(failing),
            "attempts": len(all_recs),
            "last": all_recs[-1]["ts"][:10],
            "stale": stale,
            "weak": bool(failing) or stale,
            "recent_mistakes": [
                {k: r.get(k) for k in ("quiz", "qid", "level", "score", "note", "feedback")}
                for r in all_recs if r["score"] < PASS_SCORE
            ][-3:],
        }
    return stats


def latest_by_question(quiz: str) -> dict[str, dict]:
    """某套题里每道题最近一次作答（批改记录会替代它指向的待批改记录）。"""
    latest: dict[str, dict] = {}
    for a in load_attempts():
        if a.get("quiz") == quiz:
            latest[a["qid"]] = a
    return latest


# ---------- progress.md ----------

def _bar(level: int) -> str:
    return "█" * level + "░" * (4 - level)


def write_progress_md() -> None:
    syllabus = load_yaml(SYLLABUS)
    topics = syllabus.get("topics") or {}
    attempts = load_attempts()
    stats = concept_stats(attempts)
    graded, pending = split_attempts(attempts)
    names = {c: n for t in topics.values() for c, n in (t.get("concepts") or {}).items()}
    icon = {"done": "✅", "watching": "🟡"}

    lines = ["# 学习进度", "",
             "> 自动生成，不要手改。课程进度改 `progress/syllabus.yaml`，答题记录在 `progress/attempts.jsonl`。",
             f"> 更新于 {now().isoformat(sep=' ')}", "", "## 课程进度", "",
             "| 学科 | 完成 | 单元 |", "|---|---|---|"]
    for tid, t in topics.items():
        units = t.get("units") or []
        n_done = sum(u.get("status") == "done" for u in units)
        cells = " · ".join(f"{icon.get(u.get('status'), '⬜')} {u['title']}" for u in units)
        lines.append(f"| `{tid}` {t.get('title', '')} | {n_done}/{len(units)} | {cells} |")

    lines += ["", "## 概念掌握度", "",
              "等级：1 记忆 → 2 理解 → 3 应用 → 4 分析。某一级最近 3 次平均分 ≥ 70% 算通过。", ""]
    shown = [s for s in stats.values() if s["topic"] not in IGNORED_TOPICS]
    if shown:
        lines += ["| 概念 | 掌握 | 练习次数 | 最近 | 提示 |", "|---|---|---|---|---|"]
        for s in sorted(shown, key=lambda s: (s["topic"], s["concept"])):
            tips = []
            if s["failing_levels"]:
                tips.append("第 " + "、".join(map(str, s["failing_levels"])) + " 级有错")
            if s["stale"]:
                tips.append("需复习")
            label = f"`{s['concept']}` {names.get(s['concept'], '')}".strip()
            lines.append(f"| {label} | {_bar(s['mastery'])} {s['mastery']} {LEVELS.get(s['mastery'], '')} "
                         f"| {s['attempts']} | {s['last']} | {'；'.join(tips)} |")
    else:
        lines.append("还没有练习记录（`demo/` 下的演示题不计入）。")

    lines += ["", "## 最近测验", ""]
    quizzes: dict[str, list[dict]] = defaultdict(list)
    for a in graded:
        quizzes[a["quiz"]].append(a)
    if quizzes:
        lines += ["| 测验 | 最近一次 | 已判分题数 | 平均分 |", "|---|---|---|---|"]
        recent = sorted(quizzes.items(), key=lambda kv: max(a["ts"] for a in kv[1]), reverse=True)[:10]
        for quiz, recs in recent:
            avg = sum(a["score"] for a in recs) / len(recs)
            lines.append(f"| {quiz} | {max(a['ts'] for a in recs)[:10]} | {len(recs)} | {avg:.0%} |")
    else:
        lines.append("还没有。")

    lines += ["", "## 待批改", ""]
    lines += [f"- {a['quiz']} {a['qid']}（{a['ts'][:10]}）" for a in pending] or ["无。"]
    PROGRESS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
