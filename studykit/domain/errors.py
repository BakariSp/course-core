"""领域错误。都继承 ValueError：接口层把它们统一变成 400 / 命令行报错。"""
from __future__ import annotations


class DomainError(ValueError):
    pass


class InvalidId(DomainError):
    pass


class InvalidEvidence(DomainError):
    pass


class LessonError(DomainError):
    """课时、题目不存在或格式不对。"""


class CourseError(DomainError):
    """课程页：单元、小节、检查点、事件不合法。"""


class KnowledgeError(DomainError):
    """知识图：节点不存在、格式不对。"""
