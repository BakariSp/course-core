"""备课状态的文件实现（D-040）：<data>/prep/<单元>.json。实现 app.ports.PrepStatus。

WHY: 备课可能由网页的后台线程发起，也可能由命令行发起（另一个进程）；课程页要能看到任何一个进程里正在跑的备课，
服务器重启也不能丢。状态写进文件，并记下是哪个进程在跑——进程不在了，就不算"正在备课"（不会永远卡在"备课中"）。
INVARIANT: 同一个单元同时只有一个活着的进程在备课（begin 就是锁）。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # WHY: Windows 上 os.kill(pid, 0) 会调用 TerminateProcess，不能拿来探测
        import ctypes
        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class FilePrepStatus:
    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def _path(self, unit: str) -> Path:
        return self.root / f"{unit}.json"

    def _read(self, unit: str) -> dict | None:
        p = self._path(unit)
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (json.JSONDecodeError, OSError):
            return None

    def _write(self, unit: str, rec: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._path(unit).with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self._path(unit))

    @staticmethod
    def _now() -> str:
        return dt.datetime.now().isoformat(timespec="seconds")

    def begin(self, unit: str) -> bool:
        with self._lock:
            cur = self.get(unit)
            if cur and cur["alive"]:
                return False
            self._write(unit, {"state": "running", "pid": os.getpid(), "started": self._now(), "updated": self._now(),
                               "progress": {}, "error": ""})
            return True

    def update(self, unit: str, progress: dict) -> None:
        with self._lock:
            rec = self._read(unit) or {"state": "running", "pid": os.getpid(), "started": self._now(), "error": ""}
            self._write(unit, {**rec, "progress": progress, "updated": self._now()})

    def end(self, unit: str, error: str = "") -> None:
        with self._lock:
            rec = self._read(unit) or {}
            self._write(unit, {**rec, "state": "error" if error else "done", "error": error, "updated": self._now()})

    def get(self, unit: str) -> dict | None:
        rec = self._read(unit)
        if rec is None:
            return None
        return {**rec, "alive": rec.get("state") == "running" and pid_alive(int(rec.get("pid") or 0))}
