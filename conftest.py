import re
import sys

# WHY: 每个课时的 code/ 里都有同名的 ex1.py。导入某个测试文件之前，把它所在目录放到
# sys.path 最前面，并清掉上一个课时缓存的同名模块，这样 `from ex1 import solve`
# 总是拿到同目录那一份，一次跑完整个 lessons/ 也不会串题。
_EX_MODULE = re.compile(r"^ex\d+$")


def pytest_pycollect_makemodule(module_path, parent):
    code_dir = str(module_path.parent)
    for name in [n for n in sys.modules if _EX_MODULE.match(n)]:
        del sys.modules[name]
    if code_dir in sys.path:
        sys.path.remove(code_dir)
    sys.path.insert(0, code_dir)
