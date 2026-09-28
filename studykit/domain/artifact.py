"""产出物与发现（D-035）：检验器和生成者之间唯一的接口。

产出物 = 模型产出、要检验才能给学习者用的东西（课程计划、单元题、简答题的批改）。
发现 = 某个检验器对产出物某一处的判断。地址用 JSON Pointer 的写法（RFC 6901），空字符串表示整份产出物；
有了地址，修复才能只重写被指出的部分，其余部分冻结。

INVARIANT: 地址是"逻辑地址"，由产出物种类定义。课程计划的小节按全书顺序编号（/sections/i，从 0 开始），
不管它在哪个 part 里——和学习者看到的"第 i+1 节"一一对应。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

SEVERITIES = ("block", "warn")          # block：不修不能发布；warn：记下，不阻断


@dataclass(frozen=True)
class Finding:
    address: str                 # JSON Pointer，如 /sections/4/checkpoint/1；"" = 整份产出物
    what: str                    # 问题是什么：给生成者看的，要能照着改
    evaluator: str               # 哪个检验器，如 plan_check / lab_verify / reviewer
    severity: str = "block"
    evidence: str = ""           # 证据：命令输出、页面原文、测试报错
    layer: str = ""              # 猜测根因在哪一层（prompt / context / tools / model）：给改进循环用

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity 只能是 {' / '.join(SEVERITIES)}：{self.severity}")

    def as_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Finding":
        return Finding(**{k: d.get(k, "") for k in ("address", "what", "evaluator", "evidence", "layer")},
                       severity=d.get("severity") or "block")


def section_address(i: int) -> str:
    return f"/sections/{i}"


def checkpoint_address(i: int, j: int) -> str:
    return f"/sections/{i}/checkpoint/{j}"


def within(address: str, prefix: str) -> bool:
    """address 是不是 prefix 这一部分（或它的下级）。"""
    return prefix == "" or address == prefix or address.startswith(prefix + "/")


def repair_unit(address: str) -> str:
    """一个发现允许重写的最小单位：列表里一项之内的任何地方 → 整项（一节、一道题）；其余 → 它所在的顶层字段；"" → 整份。"""
    segs = address.strip("/").split("/") if address else []
    if not segs:
        return ""
    if len(segs) >= 2 and segs[1].isdigit():            # 列表里的一项（/sections/3、/questions/2）是修复的最小单位
        return f"/{segs[0]}/{segs[1]}"
    return f"/{segs[0]}"


def repair_scope(findings: list[Finding]) -> list[str]:
    """一组发现合起来允许重写哪些部分（去重、按地址排序；有整份的发现就只有 ""）。"""
    units = sorted({repair_unit(f.address) for f in findings})
    return [""] if "" in units else units


def blocking(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "block"]


def unexpected_changes(before: dict[str, object], after: dict[str, object], allowed: list[str]) -> list[str]:
    """定点修复的冻结规则：修复前后哪些部分变了，却不在被指出的范围里。

    before / after = 产出物种类切出来的"部分"（地址 → 内容）。一个发现允许改它指向的部分、它的上级和下级：
    检查点 /sections/4/checkpoint/1 的发现，允许重写第 5 节整节（小节是修复的最小单位）。
    """
    changed = sorted(a for a in set(before) | set(after) if before.get(a) != after.get(a))
    return [c for c in changed if not any(within(c, a) or within(a, c) for a in allowed)]


def keep_going(rounds: list[list[Finding]], spent: float, cap: float) -> str:
    """产出循环用完一次预算以后，要不要自动再修一轮（D-063）。rounds = 每一轮的阻断发现，按时间顺序。

    返回空串 = 接着修；"stuck" = 上一轮指出的问题（同一处、同一个检验器），修了一轮还在：再修也是花钱，
    问题多半在检验器、prompt 或工具，交给开发者；"cap" = 这次备课花到上限了。
    WHY: 不看阻断数有没有变少——每一轮修好旧的、又查出新的真问题（2026-09-28 test-02 1→2→1），也是在往前走。"""
    if len(rounds) >= 2:
        before = {(f.address, f.evaluator) for f in blocking(rounds[-2])}
        if any((f.address, f.evaluator) in before for f in blocking(rounds[-1])):
            return "stuck"
    if spent >= cap:
        return "cap"
    return ""
