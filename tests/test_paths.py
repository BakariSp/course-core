"""路径守卫 safe_path。"""
import sys

import pytest

from studykit.app.paths import safe_path
from studykit.domain.errors import DomainError


def test_rejects_paths_that_escape_base(tmp_path):
    with pytest.raises(DomainError):
        safe_path(tmp_path, "../outside")
    assert safe_path(tmp_path, "a/b") == (tmp_path / "a" / "b").resolve()


@pytest.mark.skipif(sys.platform != "win32", reason="竞态在 Windows 的 ntpath.realpath 里")
def test_parent_created_by_another_thread_mid_resolve_is_still_inside(tmp_path, monkeypatch):
    """并行备课时，另一个线程恰好在 resolve 途中建出 agent 目录：
    ntpath.realpath 第一次报"路径不存在"(3)，复查时变成"文件不存在"(2)，于是留下 \\\\?\\ 前缀，
    和 base 比较就误判越界。这里用桩把"另一个线程建目录"固定在第一次失败之后。"""
    import ntpath
    real = ntpath._getfinalpathname
    target = tmp_path / "tutor-prep" / "run-1"
    fired = []

    def racing(p):
        try:
            return real(p)
        except OSError:
            if not fired and str(p).lower() == str(target).lower():
                fired.append(1)
                target.parent.mkdir()          # 另一个线程的 ws.mkdir(parents=True)
            raise

    monkeypatch.setattr(ntpath, "_getfinalpathname", racing)
    p = safe_path(tmp_path, "tutor-prep/run-1")
    assert fired
    assert p == target.resolve()
