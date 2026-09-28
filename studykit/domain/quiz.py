"""单元题这种产出物（D-041，PRD_V2 阶段 C）：出题 agent 交上来的一份 JSON，每道题连同它的答案。

    {"title", "questions": [{"id", "checker", "concept", "level", "prompt", ...题面字段,
                             "starter"?, "tests"?（code 题的初始代码和判题测试）,
                             "key": {...答案、检查项、rubric、参考做法 / 参考代码, "explain",
                                     "parts": [{concept, level?, misconception?}]（得分点，D-056）}}],
     "misconceptions"?: {<知识点 id>: {<误解 id>: 说明}}（这套题新提出的误解，发布时写进知识图）}

地址：/questions/<i>（第 i 道题，修复的最小单位）、/title。
发布时拆成 lessons/<学科>/<NN-slug>/ 下的 quiz.yaml（题面，会发给网页）、key.yaml（答案，只在服务器读）、code/ 里的文件。
INVARIANT: 答案只进 key.yaml；题面、初始代码里不能有答案。
"""
from __future__ import annotations

import copy
import re

from studykit.domain.artifact import Finding
from studykit.domain.errors import CourseError
from studykit.domain.ids import MISCONCEPTION_ID_RE

EVALUATOR = "quiz_check"
LEVELS = (1, 2, 3, 4)
QUESTION_KEYS = ("id", "checker", "concept", "level", "kind", "prompt", "options", "multi", "file", "intro", "setup",
                 "show_setup", "max_runs")
RUBRIC_POINTS = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*[)）]\s*$")


def question_address(i: int) -> str:
    return f"/questions/{i}"


def quiz_parts(quiz: dict) -> dict[str, object]:
    """切成可比较的部分：每道题一块，其余每个顶层字段一块（修复冻结检查用）。"""
    out: dict[str, object] = {question_address(i): q for i, q in enumerate(quiz.get("questions") or [])}
    out.update({f"/{k}": v for k, v in quiz.items() if k != "questions"})
    return out


def quiz_addresses(quiz: dict) -> set[str]:
    return {"", *quiz_parts(quiz)}


def replace_question(quiz: dict, address: str, value) -> dict:
    if address == "":
        if not isinstance(value, dict):
            raise CourseError("整份替换要给一个完整的单元题对象")
        return copy.deepcopy(value)
    out = copy.deepcopy(quiz)
    segs = address.strip("/").split("/")
    if segs[0] == "questions" and len(segs) == 2 and segs[1].isdigit() and int(segs[1]) < len(out.get("questions") or []):
        if not isinstance(value, dict):
            raise CourseError(f"{address} 要是一道题的对象")
        out["questions"][int(segs[1])] = copy.deepcopy(value)
        return out
    if len(segs) == 1 and segs[0] and segs[0] != "questions":
        out[segs[0]] = copy.deepcopy(value)
        return out
    raise CourseError(f"不能按这个地址替换：{address}")


def split(quiz: dict, unit: str) -> tuple[dict, dict, dict[str, str]]:
    """一份单元题 → （quiz.yaml，key.yaml，code/ 下的文件：相对路径 → 内容）。"""
    questions, answers, files = [], {}, {}
    for q in quiz.get("questions") or []:
        questions.append({k: q[k] for k in QUESTION_KEYS if k in q})
        answers[q["id"]] = {k: v for k, v in (q.get("key") or {}).items() if k != "solution" or q.get("checker") != "code"}
        if q.get("checker") == "code":
            files[q["file"]] = q.get("starter", "")
            files[test_file(q["file"])] = q.get("tests", "")
            answers[q["id"]]["solution"] = (q.get("key") or {}).get("solution", "")    # 参考代码只在 key 里
    head = {"schema_version": 1, "unit": unit, "title": quiz.get("title", ""), "source": f"{unit} 课程页（出题 agent，D-041）"}
    return {**head, "questions": questions}, {"schema_version": 1, "answers": answers}, files


def test_file(file: str) -> str:
    """code/ex1.py → code/test_ex1.py（和 cs_practice 的判题约定一致）。"""
    head, _, name = file.rpartition("/")
    stem = name.rsplit(".", 1)[0]
    return f"{head}/test_{stem}.py" if head else f"test_{stem}.py"


# 得分点和什么一一对应（D-056）。代码题的测试还没法按知识点分组，暂时整题算。
PART_UNITS = {"choice": ("options", "选项"), "fill": ("blanks", "空"), "short": ("rubric", "评分点"), "terminal": ("checks", "检查项")}


def _unit_count(q: dict, k: dict) -> int:
    c = q.get("checker")
    if c == "choice":
        return len(q.get("options") or [])
    if c == "fill":
        return len(k.get("blanks") or [])
    return len(k.get(PART_UNITS[c][0]) or [])


def _part_findings(q: dict, k: dict, name: str, node_ids: set[str], known: dict[str, set[str]]) -> list[str]:
    c = q.get("checker")
    if c not in PART_UNITS:
        return [f"{name}：代码题按整题算，不要写 key.parts"] if k.get("parts") else []
    parts, (_, unit) = k.get("parts"), PART_UNITS[c]
    n = _unit_count(q, k)
    if not isinstance(parts, list) or len(parts) != n:
        return [f"{name}：key.parts 要和{unit}一一对应（{n} 个），每个写 concept（考哪个知识点），"
                f"没拿到时说明某种误解的写 misconception"]
    out = []
    for j, p in enumerate(parts):
        concept = (p or {}).get("concept") or q.get("concept")
        if concept not in node_ids:
            out.append(f"{name}：第 {j + 1} 个{unit}的 concept {concept} 不在知识图里")
        if (p or {}).get("level") is not None and p["level"] not in LEVELS:
            out.append(f"{name}：第 {j + 1} 个{unit}的 level 要是 1–4")
        m = (p or {}).get("misconception")
        if m and m not in known.get(concept, set()):
            out.append(f"{name}：第 {j + 1} 个{unit}的误解 {m} 在 {concept} 下既没有、这套题也没提出（写进 misconceptions）")
    return out


def quiz_findings(quiz: dict, node_ids: set[str], checkers: set[str], count: tuple[int, int] = (6, 8),
                  misconceptions: dict[str, set[str]] | None = None) -> list[Finding]:
    """静态检查（免费，出题 agent 提交时就跑）：结构、每种题的必填、答案不漏进题面、整套的分布（CLAUDE.md「出题」）、
    得分点的标注（D-056）。misconceptions = 知识图里已有的误解（知识点 → 误解 id）。"""
    out: list[Finding] = []
    add = lambda address, what: out.append(Finding(address, what, EVALUATOR))  # noqa: E731
    if not isinstance(quiz, dict):
        return [Finding("", "quiz 必须是一个对象", EVALUATOR)]
    if not str(quiz.get("title") or "").strip():
        add("/title", "缺少 title")
    qs = quiz.get("questions") or []
    lo, hi = count
    if not lo <= len(qs) <= hi:
        add("", f"一套 {lo}–{hi} 道题，现在 {len(qs)} 道")
    ids = [str(q.get("id")) for q in qs]
    proposed = quiz.get("misconceptions") or {}
    if not isinstance(proposed, dict):
        add("/misconceptions", "misconceptions 要是 {知识点 id: {误解 id: 说明}}")
        proposed = {}
    for nid, ms in proposed.items():
        if nid not in node_ids:
            add("/misconceptions", f"误解提在了不在知识图里的知识点上：{nid}")
        for mid, desc in (ms or {}).items() if isinstance(ms, dict) else []:
            if not MISCONCEPTION_ID_RE.match(str(mid)) or not str(desc or "").strip():
                add("/misconceptions", f"{nid} 的误解 {mid}：id 用小写字母、数字、下划线，并写一句说明")
    known = {nid: set(ms) for nid, ms in (misconceptions or {}).items()}
    for nid, ms in proposed.items():
        known.setdefault(nid, set()).update(ms if isinstance(ms, dict) else {})
    files = [q.get("file") for q in qs if q.get("checker") == "code"]
    for i, q in enumerate(qs):
        at, name = question_address(i), f"第 {i + 1} 题（{q.get('id')}）"
        k = q.get("key") or {}
        if not re.fullmatch(r"q\d+", str(q.get("id") or "")) or ids.count(str(q.get("id"))) > 1:
            add(at, f"{name}：id 要是 q1、q2……而且不能重复")
        if q.get("checker") not in checkers:
            add(at, f"{name}：checker 只能是 {' / '.join(sorted(checkers))}")
        if q.get("concept") not in node_ids:
            add(at, f"{name}：concept {q.get('concept')} 不在知识图里（用课程里出现过的知识点 id）")
        if q.get("level") not in LEVELS:
            add(at, f"{name}：level 要是 1–4")
        if not str(q.get("prompt") or "").strip():
            add(at, f"{name}：缺少 prompt")
        if not str(k.get("explain") or "").strip():
            add(at, f"{name}：key 里缺少 explain（批改后显示的解析）")
        c = q.get("checker")
        if c == "choice":
            opts, ans = q.get("options") or [], k.get("answer")
            letters = {chr(65 + j) for j in range(len(opts))}
            ans = ans if isinstance(ans, list) else [ans]
            if len(opts) < 2 or not ans or not all(str(a).upper() in letters for a in ans):
                add(at, f"{name}：选择题至少 2 个选项，key.answer 是选项字母（{'、'.join(sorted(letters)) or '无'}）")
            if len(ans) > 1 and not q.get("multi"):
                add(at, f"{name}：答案有几个时要写 multi: true")
        elif c == "fill":
            blanks, accept = str(q.get("prompt") or "").count("____"), k.get("blanks") or []
            if not blanks or blanks != len(accept) or not all(accept):
                add(at, f"{name}：题干里 ____ 的个数（{blanks}）要和 key.blanks 的组数（{len(accept)}）一样，每组至少一个答案")
        elif c == "code":
            f = str(q.get("file") or "")
            if not re.fullmatch(r"code/ex\d+\.(py|sh)", f) or files.count(f) > 1:
                add(at, f"{name}：file 要是 code/exN.py（或 .sh），每道题一个")
            if not str(q.get("tests") or "").strip() or "def test_" not in str(q.get("tests")):
                add(at, f"{name}：tests 要是 pytest 测试（至少一个 def test_…）")
            if not str(k.get("solution") or "").strip():
                add(at, f"{name}：key.solution 要有参考实现（环境用它验证测试能通过）")
            if str(k.get("solution") or "").strip() and str(k.get("solution")).strip() == str(q.get("starter") or "").strip():
                add(at, f"{name}：starter 就是参考实现——初始代码只放签名、docstring 和未实现")
        elif c == "terminal":
            if not q.get("setup"):
                add(at, f"{name}：终端题要有 setup（搭场景的命令）")
            if not k.get("checks"):
                add(at, f"{name}：key.checks 至少一条（做完后仓库应该是什么状态）")
            if not k.get("solution"):
                add(at, f"{name}：key.solution 要有参考做法（在练习终端里能敲的命令，环境用它验证）")
            for chk in k.get("checks") or []:
                if not (chk.get("run") or chk.get("file")) or not str(chk.get("desc") or "").strip():
                    add(at, f"{name}：每条 check 要有 run 或 file，以及 desc")
        elif c == "short":
            rubric = [str(r) for r in k.get("rubric") or []]
            points = [float(m.group(1)) for r in rubric if (m := RUBRIC_POINTS.search(r))]
            if not rubric:
                add(at, f"{name}：简答题要有 key.rubric（批改要点）")
            elif abs(sum(points) - 1) > 0.01:
                add(at, f"{name}：rubric 各条末尾写分值，如「……（0.5）」，加起来要是 1（现在 {sum(points):g}）")
        for what in _part_findings(q, k, name, node_ids, known) if c in PART_UNITS or c == "code" else []:
            add(at, what)
        # 答案不能出现在题面里
        leaks = []
        if c == "fill":
            leaks = [a for group in k.get("blanks") or [] for a in group if len(str(a)) >= 3 and str(a) in str(q.get("prompt"))]
        if c == "code" and str(k.get("solution") or "").strip() and str(k.get("solution")).strip() in str(q.get("prompt") or ""):
            leaks = ["参考实现"]
        if leaks:
            add(at, f"{name}：题干里直接出现了答案：{'、'.join(map(str, leaks))}")
    kinds = {q.get("checker") for q in qs}
    if qs and len(kinds) < 3:
        add("", f"至少用 3 种题型（现在 {len(kinds)} 种：{'、'.join(sorted(map(str, kinds)))}）")
    if qs and not any(q.get("level") == 4 for q in qs):
        add("", "至少 1 道 4 级题（找 bug、判断取舍、nanoteacher 映射）")
    return out
