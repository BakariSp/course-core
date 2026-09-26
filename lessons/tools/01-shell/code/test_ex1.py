"""用 Git Bash 在一个临时目录里真的运行 ex1.sh，检查它留下的文件和打印的东西。"""
import subprocess
from pathlib import Path

import pytest

from studykit.specs.cs_practice.practice import find_bash

SCRIPT = Path(__file__).with_name("ex1.sh")

APP = ["2026-09-26 08:09:58 INFO  server started",
       "2026-09-26 08:10:01 ERROR db connection refused",
       "2026-09-26 08:10:02 INFO  retrying",
       "2026-09-26 08:10:05 ERROR db connection refused"]
OLD = ["2026-09-20 22:00:00 ERROR disk almost full",
       "2026-09-20 22:00:01 INFO  cleanup done"]
ARCHIVE = ["2026-08-01 10:00:00 ERROR this one is archived"]
EXPECTED = [APP[1], APP[3], OLD[0]]


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "logs" / "archive").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    (tmp_path / "logs" / "app.log").write_text("\n".join(APP) + "\n", encoding="utf-8", newline="\n")
    (tmp_path / "logs" / "old app.log").write_text("\n".join(OLD) + "\n", encoding="utf-8", newline="\n")
    (tmp_path / "logs" / "archive" / "2026-08.log").write_text("\n".join(ARCHIVE) + "\n", encoding="utf-8", newline="\n")
    # WHY: 网页编辑器在 Windows 上保存可能带 \r\n，bash 会把 \r 当成命令的一部分；复制一份去掉 \r 再跑。
    script = tmp_path / "ex1.sh"
    script.write_text(SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    return tmp_path


def run(lab):
    return subprocess.run([find_bash(), "ex1.sh"], cwd=lab, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=30)


def errors_file(lab):
    f = lab / "work" / "errors.txt"
    assert f.exists(), "work/errors.txt 没有生成"
    return [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_errors_file_has_exactly_the_error_lines(lab):
    run(lab)
    assert sorted(errors_file(lab)) == sorted(EXPECTED)


def test_lines_have_no_filename_prefix(lab):
    run(lab)
    assert all(not l.startswith("logs/") for l in errors_file(lab)), "行首带了文件名"


def test_file_with_space_in_name_is_included(lab):
    run(lab)
    assert OLD[0] in errors_file(lab), "漏了 'old app.log' 里的错误行"


def test_archive_subdirectory_is_skipped(lab):
    run(lab)
    assert ARCHIVE[0] not in errors_file(lab), "archive/ 子目录里的日志不该算进来"


def test_prints_only_the_count(lab):
    out = run(lab).stdout.split()
    assert out == ["3"], f"标准输出应该只有一个数字 3，实际是：{out}"


def test_running_twice_does_not_duplicate(lab):
    run(lab)
    run(lab)
    assert len(errors_file(lab)) == 3, "运行两次后行数变了：是不是用了 >> ？"
