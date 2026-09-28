"""路径守卫：全仓唯一一份（原来抄了 6 处，审查 §4.3 ⑨）。"""
from __future__ import annotations

from pathlib import Path

from studykit.domain.errors import DomainError


def _resolve(p: Path) -> Path:
    """Path.resolve()，去掉 Windows 偶发留下的 \\\\?\\ 前缀。

    ntpath.realpath 的竞态：路径不存在时先记下错误码，解析完再用同一错误码判断能不能去前缀；
    中间别的线程建出了上级目录，错误码从 3 变成 2，前缀就留下来了（并行备课时偶发"路径越界"）。
    \\\\?\\C:\\x 和 C:\\x 是同一个路径，输入没带前缀就去掉。"""
    r = p.resolve()
    s = str(r)
    if s.startswith("\\\\?\\") and not str(p).startswith("\\\\?\\"):
        r = Path("\\\\" + s[8:] if s.startswith("\\\\?\\UNC\\") else s[4:])
    return r


def safe_path(base: Path, rel: str) -> Path:
    """base 下的相对路径；解析后逃出 base（../、绝对路径、符号链接）就报错。"""
    p = _resolve(base / rel)
    if not p.is_relative_to(_resolve(base)):
        raise DomainError(f"路径越界：{rel!r}")
    return p
