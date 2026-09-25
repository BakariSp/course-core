"""路径守卫：全仓唯一一份（原来抄了 6 处，审查 §4.3 ⑨）。"""
from __future__ import annotations

from pathlib import Path

from studykit.domain.errors import DomainError


def safe_path(base: Path, rel: str) -> Path:
    """base 下的相对路径；解析后逃出 base（../、绝对路径、符号链接）就报错。"""
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()):
        raise DomainError(f"路径越界：{rel!r}")
    return p
