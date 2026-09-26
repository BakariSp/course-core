"""JobRunner 的线程实现（D-032）：网页上点「生成课程」后，agent 在服务器进程的后台线程里跑。"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Callable


class ThreadJobs:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

    def start(self, key: str, fn: Callable[[], object]) -> bool:
        with self._lock:
            if (self._jobs.get(key) or {}).get("state") == "running":
                return False
            self._jobs[key] = {"state": "running", "started": dt.datetime.now().isoformat(timespec="seconds"), "error": ""}

        def work():
            try:
                fn()
                state, err = "done", ""
            except Exception as e:  # noqa: BLE001  出错要记下来给页面看，不能让线程悄悄死掉
                state, err = "error", f"{type(e).__name__}: {e}"
            with self._lock:
                self._jobs[key].update(state=state, error=err)

        threading.Thread(target=work, name=f"job-{key}", daemon=True).start()
        return True

    def status(self, key: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(key)
            return dict(j) if j else None
