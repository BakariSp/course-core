"""连接键的值对象：构造时校验，全仓只有这一份格式规则。

    NodeId     知识节点 = 概念 id，如 tools.shell.glob（第一段是学科）
    UnitId     单元 id，如 tools-01-shell
    LessonRef  一套题，<学科>/<课时>，如 tools/01-shell
    LearnerId  学习者，现在只有 me（D-021：留好多学习者的扩展位）

它们是 str 的子类：能直接当字典键、写进 JSON。
WHY: 这些 id 来自网页请求、agent 提议和人手写的 YAML；不校验就可能用 ../ 读到别的文件，
或者一个拼错的概念 id 静默变成"永远没学"的节点。
"""
from __future__ import annotations

import re

from studykit.domain.errors import InvalidId

NODE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9_-]*)+$")
UNIT_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LESSON_REF_RE = re.compile(r"^[a-z0-9_-]+/[a-z0-9_.-]+$")
LEARNER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Id(str):
    pattern: re.Pattern = re.compile(r".+")
    kind = "id"

    def __new__(cls, value):
        if not cls.valid(value):
            raise InvalidId(f"{cls.kind} 格式不对：{value!r}")
        return super().__new__(cls, value)

    @classmethod
    def valid(cls, value) -> bool:
        return isinstance(value, str) and bool(cls.pattern.match(value))


class NodeId(_Id):
    pattern, kind = NODE_ID_RE, "知识节点 id"

    @property
    def topic(self) -> str:
        return self.split(".")[0]


class UnitId(_Id):
    pattern, kind = UNIT_ID_RE, "单元 id"

    @property
    def topic(self) -> str:
        return self.split("-")[0]


class LessonRef(_Id):
    pattern, kind = LESSON_REF_RE, "课时引用"


class LearnerId(_Id):
    pattern, kind = LEARNER_ID_RE, "学习者 id"


ME = LearnerId("me")
