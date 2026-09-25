"""时钟和 id：做成端口，测试里可以换成固定值。"""
from __future__ import annotations

import datetime as dt
import uuid


class SystemClock:
    def now(self) -> dt.datetime:
        return dt.datetime.now().replace(microsecond=0)


class UuidIds:
    def new(self) -> str:
        return uuid.uuid4().hex[:12]
