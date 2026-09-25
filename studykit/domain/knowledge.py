"""学习者模型（D-020）：一张知识图 + 证据；节点状态、薄弱点、已知词表都是投影。

INVARIANT: 节点状态只由证据算出，不手写。薄弱点 = 状态为 weak 的节点，不单独存。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from studykit.domain.evidence import Evidence
from studykit.domain.ids import NODE_ID_RE

KINDS = {"concept": "概念", "term": "术语/命令", "skill": "技能"}
STATES = {"new": "没学", "learning": "在学", "weak": "薄弱", "mastered": "掌握", "stale": "需复习"}
REASONS_SHOWN = 3         # 薄弱点描述里最多带几条理由


@dataclass
class Node:
    id: str
    title: str
    desc: str = ""
    kind: str = "concept"
    requires: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)

    @property
    def topic(self) -> str:
        return self.id.split(".")[0]


# ---------- 图 ----------

def validate(nodes: dict[str, Node]) -> list[str]:
    """知识图的硬性检查：id 格式、类型、先修指向存在的节点、没有环。"""
    errors = []
    for n in nodes.values():
        if not NODE_ID_RE.match(n.id):
            errors.append(f"节点 id 格式不对：{n.id}（小写，用点分层，第一段是学科，如 tools.shell.glob）")
        if n.kind not in KINDS:
            errors.append(f"{n.id}：kind 只能是 {'/'.join(KINDS)}")
        for r in n.requires:
            if r not in nodes:
                errors.append(f"{n.id}：先修 {r} 不存在")
    color: dict[str, int] = {}

    def visit(nid: str, path: list[str]) -> None:
        color[nid] = 1
        for r in nodes[nid].requires:
            if r not in nodes:
                continue
            if color.get(r) == 1:
                errors.append("先修关系有环：" + " → ".join(path + [nid, r]))
            elif not color.get(r):
                visit(r, path + [nid])
        color[nid] = 2

    for nid in nodes:
        if not color.get(nid):
            visit(nid, [])
    return errors


def ancestors(nodes: dict[str, Node], start: list[str], depth: int) -> dict[str, int]:
    """从 start 往上找先修，返回 {节点: 距离}。"""
    dist = {s: 0 for s in start if s in nodes}
    frontier = list(dist)
    for d in range(1, depth + 1):
        nxt = []
        for nid in frontier:
            for r in nodes[nid].requires:
                if r in nodes and r not in dist:
                    dist[r] = d
                    nxt.append(r)
        frontier = nxt
    return dist


def depths(nodes: dict[str, Node]) -> dict[str, int]:
    """每个节点在先修树里的层数（没有先修的是第 0 层），画知识树用。"""
    memo: dict[str, int] = {}

    def d(nid: str, seen: frozenset) -> int:
        if nid in memo:
            return memo[nid]
        reqs = [r for r in nodes[nid].requires if r in nodes and r not in seen]
        memo[nid] = 0 if not reqs else 1 + max(d(r, seen | {nid}) for r in reqs)
        return memo[nid]

    return {nid: d(nid, frozenset()) for nid in nodes}


# ---------- 证据 → 节点状态 ----------

@dataclass
class Signal:
    """一条证据对某个节点的意义。"""
    ts: str
    node: str
    sign: int                  # +1 支持"会了"，-1 支持"不会"，0 中性（比如学之前不认识）
    source: str                # verb
    note: str = ""


@dataclass
class NodeState:
    node: Node
    state: str
    reasons: list[str]
    mastery: int = 0
    signals: list[Signal] = field(default_factory=list)

    def brief(self) -> dict:
        return {"id": self.node.id, "title": self.node.title, "state": self.state}


def signals(evidence: list[Evidence]) -> dict[str, list[Signal]]:
    """课程页和导师的证据对节点意味着什么。答题的分数走掌握度规则（mastery），不在这里。"""
    by: dict[str, list[Signal]] = defaultdict(list)
    for e in evidence:
        where = f"{e.unit} 第 {(e.section or 0) + 1} 节"
        if e.verb == "observed":
            sign = {"weak": -1, "ok": 1}.get(e.payload.get("polarity"), 0)
            who = "" if e.actor.type == "learner" else f"（{e.actor.id}）"
            for n in e.nodes:
                by[n].append(Signal(e.ts, n, sign, e.verb, e.payload.get("note", "") + who))
        elif e.verb == "voted_term":
            # 👎「我早就知道」= 会了；👍「有用，我确实不懂」= 学之前不认识，不算薄弱，只是在学。
            known = e.payload.get("vote") == "known"
            for n in e.nodes:
                by[n].append(Signal(e.ts, n, 1 if known else 0, e.verb,
                                    "点了「我早就知道」" if known else "学之前不认识这个词"))
        elif e.verb == "answered" and e.object_type == "checkpoint":
            for n in e.nodes:
                by[n].append(Signal(e.ts, n, 1 if e.ok else -1, e.verb, f"{where}检查点{'做对了' if e.ok else '做错了'}"))
        elif e.verb == "passed_section":
            for n in e.nodes:
                by[n].append(Signal(e.ts, n, 1, e.verb, f"{where}整节做对了，用到了这个词"))
    return by


def derive_states(nodes: dict[str, Node], stats: dict[str, dict],
                  sig: dict[str, list[Signal]]) -> dict[str, NodeState]:
    """nodes = 知识图；stats = 掌握度统计（mastery.concept_stats）；sig = signals()。"""
    out = {}
    for nid in set(nodes) | set(stats) | set(sig):
        node = nodes.get(nid) or Node(nid, nid)
        items = sorted(sig.get(nid, []), key=lambda x: x.ts)
        st, reasons, mastery = "new", [], 0
        signed = [x for x in items if x.sign]
        if items:
            st = "learning"
        if signed:
            # 最近一条有方向的证据说了算：错过、后来做对了，就不再算薄弱。
            last = signed[-1]
            st = "weak" if last.sign < 0 else "mastered"
            if last.sign < 0:
                reasons.append(last.note)
        s = stats.get(nid)
        if s:
            # 有答题记录时，按 D-003 的掌握度规则裁决（答题是最强的证据）。
            mastery = s["mastery"]
            if s["failing_levels"]:
                st = "weak"
                reasons.append("第 " + "、".join(map(str, s["failing_levels"])) + " 级有错")
                reasons += [m["note"] or f"{m['ref']} 得分 {m['score']}" for m in s["recent_mistakes"]]
            elif s["stale"]:
                st = "stale"
                reasons.append("超过 14 天没练")
            elif mastery >= 1 and st != "weak":
                st = "mastered" if mastery >= 2 else "learning"
        out[nid] = NodeState(node, st, [r for r in reasons if r][:REASONS_SHOWN], mastery, items)
    return out


def orphans(nodes: dict[str, Node], states: dict[str, NodeState]) -> list[str]:
    """证据里出现、知识图里没有的节点（多半是拼错的概念 id，审查 §4.3 ⑤）。"""
    return sorted(set(states) - set(nodes))


def known_titles(states: dict[str, NodeState], topic: str | None = None) -> set[str]:
    """已知词表（D-017）：已掌握的节点的 id、标题和别名（标题、别名转小写）。"""
    out = set()
    for s in states.values():
        if s.state == "mastered" and (topic is None or s.node.topic == topic):
            out |= {s.node.id, *(t.lower() for t in [s.node.title, *s.node.aliases])}
    return out
