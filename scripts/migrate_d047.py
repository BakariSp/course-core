"""D-047 一次性迁移：知识图的先修改成带类型的边、去掉 units；learner.md 里老师的观察改成证据；删掉旧文件。

用法：python scripts/migrate_d047.py          # 只看会做什么
      python scripts/migrate_d047.py --apply  # 真的做

course.yaml、profile.yaml 是手写的（对照 curriculum.md、syllabus.yaml、learner.md 逐条核对过），这里不生成。
已经迁移过（知识图里已经是新格式、证据里已经有迁移来的观察）就跳过对应的步骤。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from studykit.bootstrap import build  # noqa: E402
from studykit.domain import plan as plans  # noqa: E402

SOURCE = "learner.md（2026-09-25 导师记录，D-047 迁移）"
HEADER = ("# 知识图（D-020、D-047）：节点 + 先修关系。状态不写在这里，由证据算出（study.py kg）。\n"
          "# requires 每条：id、kind（required 必须先会 / helpful 先会更好）、by（谁说的：运行 id 或 learner）。\n")

# learner.md 里导师写的观察（第三人称的段落），逐条改写成一句话；原文在 git 历史里（progress/learner.md）
OBSERVATIONS = [
    "讲新概念先给心智模型（谁在读这个符号、流水线分两步），再用实验让学习者亲眼看到（如 set -x 看到 *.txt 被改写成文件名），最后才给符号对照表；只给符号表读不懂",
    "Shell 的 glob / sed 那段完全听不懂：要从「终端 / shell / 命令」三个角色讲起",
    "把命令的选项当成修饰词，不知道选项常常是在切换输出模式（例：以为 grep -l 会打印匹配的行）；可以考「给一条命令，说出它会打印什么」",
    "「shell 的语法字符（; | >）」和「shell 要展开的字符（* $ ~）」的区分还没建立。有效顺序：先给「; 就等于换行」这个等价物，再让学习者看到 shell 真的切成了两条命令，最后才给三类字符表",
    "中文输入法的全角符号（如全角分号）会让命令静默出错，报错里完全不提全角；要提醒「结果不对就把可疑字符删掉重打」",
    "会自己提出「A 和 B 是不是等价」这类推测：要鼓励，并用实验验证",
    "面对长解释会过载，会问「这个是不是知道怎么用即可」：讲到次要细节时要明说这次哪些可以不管；不要用固定的开场白",
    "一节塞太多东西（例：8 个命令 + glob 原理）就听不懂：讲解和出题一次只给一个主任务，宁可分两次课；听不懂多半是量的问题，不是能力问题",
    "喜欢用「对比两种写法」来问（例：cat f 和 cat < f）。讲重定向时先说「文件是谁打开的」，不急着说「标准输入」这个术语",
    "会问「怎么查文档」这类元问题：教查文档的入口（help / --help / git <cmd> -h 三层），而不是背命令",
    "Git Bash 在 Windows 上不真正执行权限位（chmod 000 后仍能读）：别用权限做实验得出错误结论",
    "test / [ 的心智模型曾是空白（以为 test 会回显答案）：结果在退出码里、一个字都不打印；test 和 $? 要一起讲，并强调 0 才是成功",
]


def convert_knowledge(app, apply: bool) -> None:
    by_node: dict[str, str] = {}                                  # 节点 → 第一个提议它的已发布课程计划（运行 id）
    for unit in app.content.course().unit_ids():
        plan = app.course.d.plans.current(unit)
        if plan:
            run = (plan.get("provenance") or {}).get("run") or ""
            for n in plan.get("nodes") or []:
                by_node.setdefault(n["id"], run)
    for path in sorted((ROOT / "knowledge").glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        nodes = data.get("nodes") or {}
        changed = 0
        for nid, n in nodes.items():
            reqs = n.get("requires") or []
            if reqs and isinstance(reqs[0], str):
                n["requires"] = [{"id": r, "kind": "required", "by": by_node.get(nid, "tutor")} for r in reqs]
                changed += 1
            if "units" in n:
                del n["units"]
                changed += 1
            if "requires" in n and not n["requires"]:
                del n["requires"]
        data.pop("schema_version", None)
        print(f"  {path.name}: {len(nodes)} 个节点，改了 {changed} 处")
        if apply and changed:
            path.write_text(HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")


def import_observations(app, apply: bool) -> None:
    done = [o for o in app.learner.profile()["observations"] if o["source"] == SOURCE]
    if done:
        print(f"  已经导入过 {len(done)} 条，跳过")
        return
    for text in OBSERVATIONS:
        print("  +", text[:60] + ("…" if len(text) > 60 else ""))
        if apply:
            app.learner.note_strategy(text, source=SOURCE)


def retire_old_files(apply: bool) -> None:
    for rel in ("progress/syllabus.yaml", "progress/learner.md"):
        p = ROOT / rel
        print(f"  删除 {rel}" + ("" if p.exists() else "（已经没有了）"))
        if apply and p.exists():
            p.unlink()
    cur = ROOT / "curriculum.md"
    if cur.exists():
        text = cur.read_text(encoding="utf-8")
        start = text.index("## 学科与资源")
        end = text.index("## 用 nanoteacher 当题库")
        stages = text.index("## 阶段")
        practice = text.index("## 工程实践")
        notes = ("# 课程说明\n\n> D-047：学科、主课、阶段挪进了 `progress/course.yaml`（结构化，代码和界面都读它）；"
                 "项目映射也在那里的 `project_links`。这里留下给人读的原则和验证阶梯。\n\n"
                 + text[text.index("原则："):start].strip() + "\n\n" + text[practice:].strip() + "\n")
        print("  curriculum.md → docs/curriculum-notes.md（只留原则和验证阶梯）")
        assert end < stages < practice
        if apply:
            (ROOT / "docs" / "curriculum-notes.md").write_text(notes, encoding="utf-8")
            cur.unlink()
    settings = ROOT / "progress" / "settings.yaml"
    lines = settings.read_text(encoding="utf-8").splitlines()
    keep = [l for l in lines if not l.startswith(("unit_budget_minutes", "session_minutes", "max_new_terms"))]
    keep = [l.replace("# 学习设置（你可以直接改）。助教 agent 写课程计划时必须遵守这些上限。",
                      "# 系统设置（你可以直接改）。时间偏好在 progress/profile.yaml（D-047）。") for l in keep]
    print(f"  settings.yaml：去掉 {len(lines) - len(keep)} 行时间设置")
    if apply:
        settings.write_text("\n".join(keep) + "\n", encoding="utf-8")


def main() -> None:
    apply = "--apply" in sys.argv
    backup = ROOT / "data" / "study.db"
    if apply:
        shutil.copy2(backup, backup.with_suffix(".db.before-d047"))
        print(f"数据库已备份到 {backup.with_suffix('.db.before-d047').name}")
    app = build()
    try:
        print("① 知识图"); convert_knowledge(app, apply)
        print("② 老师的观察 → 证据"); import_observations(app, apply)
    finally:
        app.close()
    print("③ 旧文件"); retire_old_files(apply)
    print("完成。" if apply else "（只是预览；加 --apply 真的做）")


if __name__ == "__main__":
    main()
