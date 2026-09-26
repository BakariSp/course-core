"""依赖规则（backend-core-design §4.2）：箭头只从外向内指。扫描 studykit/ 下每个 .py 的 import，违反就红。

    domain      纯规则，零 IO；学习域模块和 harness.py 互不 import
    app         用例 + 端口，只依赖 domain
    specs       学科 spec，依赖 domain 和 app 的端口
    adapters    端口的实现，依赖 domain、app
    web / cli / agent_tools   接口，只调 app 和 bootstrap
    bootstrap   组合根，可以依赖一切
"""
import ast
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent / "studykit"

ALLOWED = {
    "studykit.domain": {"studykit.domain"},
    "studykit.app": {"studykit.domain", "studykit.app"},
    "studykit.specs": {"studykit.domain", "studykit.app.ports", "studykit.app.paths", "studykit.specs"},
    "studykit.adapters": {"studykit.domain", "studykit.app", "studykit.adapters"},
    "studykit.web": {"studykit.app", "studykit.bootstrap"},
    "studykit.cli": {"studykit.app", "studykit.bootstrap", "studykit.web"},
    "studykit.agent_tools": {"studykit.app", "studykit.bootstrap"},
    "studykit.bootstrap": {"studykit"},
}
# 领域层只做纯计算：这些模块意味着文件、进程、网络、数据库。
DOMAIN_FORBIDDEN = {"sqlite3", "yaml", "subprocess", "http", "socket", "urllib", "shutil", "os", "pathlib", "io",
                    "threading", "tempfile"}
HARNESS = "studykit.domain.harness"


def module_name(path: Path) -> str:
    parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports_of(path: Path) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            out.append(node.module or "")
            out += [f"{node.module}.{a.name}" for a in node.names]
    return out


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


# 两个领域共用的内核。artifact（产出物与发现，D-035）是检验器和生成者之间唯一的接口：
# 学习域的课程计划检查产出它，harness 域的评审模型解析也产出它
SHARED = ("studykit.domain.errors", "studykit.domain.ids", "studykit.domain.artifact")


def _crosses_domains(mod: str, name: str) -> bool:
    if not _within(name, "studykit.domain") or any(_within(name, s) for s in SHARED) or name == "studykit.domain":
        return False
    return _within(mod, HARNESS) != _within(name, HARNESS)


def violations(mod: str, imported: list[str]) -> list[str]:
    owner = max((p for p in ALLOWED if _within(mod, p)), key=len, default=None)
    if owner is None:
        return [f"{mod}：不属于任何一层（先想清楚它在哪一层，再加进 ALLOWED）"] if mod != "studykit" else []
    bad = []
    for name in imported:
        top = name.split(".")[0]
        if owner == "studykit.domain" and top in DOMAIN_FORBIDDEN:
            bad.append(f"{mod} → {name}（领域层不能做 IO）")
        if top != "studykit" or name == "studykit":
            continue
        if not any(_within(name, ok) for ok in ALLOWED[owner]):
            bad.append(f"{mod} → {name}（{owner} 不能依赖它）")
        # 两个领域在 domain 里互不 import：harness 引用学习证据只用 id
        if owner == "studykit.domain" and _crosses_domains(mod, name):
            bad.append(f"{mod} → {name}（学习域和 harness 域不能互相依赖）")
    return bad


def test_dependency_rules_hold():
    problems = []
    for path in sorted(PKG.rglob("*.py")):
        problems += violations(module_name(path), imports_of(path))
    assert not problems, "违反依赖规则：\n" + "\n".join(problems)


@pytest.mark.parametrize("mod, imported", [
    ("studykit.domain.mastery", ["sqlite3"]),                          # 领域层做 IO
    ("studykit.domain.mastery", ["studykit.app.learning"]),            # 向外依赖
    ("studykit.domain.harness", ["studykit.domain.evidence"]),         # 两个领域互相依赖
    ("studykit.domain.evidence", ["studykit.domain.harness"]),
    ("studykit.app.learning", ["studykit.adapters.sqlite"]),           # 用例依赖实现
    ("studykit.web", ["studykit.adapters.sqlite"]),                    # 接口层绕过应用层
    ("studykit.web", ["studykit.domain.plan"]),
    ("studykit.specs.cs_practice", ["studykit.adapters.files"]),       # spec 依赖基础设施
    ("studykit.specs.cs_practice", ["studykit.app.learning"]),         # spec 依赖用例
    ("studykit.newthing", ["json"]),                                   # 没归层的新模块
])
def test_rule_catches_violations(mod, imported):
    assert violations(mod, imported)


def test_rule_allows_the_right_direction():
    assert not violations("studykit.app.learning", ["studykit.domain.mastery", "json", "pathlib"])
    assert not violations("studykit.adapters.sqlite", ["studykit.app.ports", "sqlite3"])
    assert not violations("studykit.domain.harness", ["studykit.domain.errors", "json"])
    assert not violations("studykit.specs.cs_practice.code", ["studykit.app.paths", "subprocess"])
