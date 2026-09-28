"""学习者模型（D-020）：一张知识图 + 证据；节点状态、薄弱点、已知词表都是投影。

INVARIANT: 节点状态只由证据算出，不手写。薄弱点 = 状态为 weak 的节点，不单独存。
INVARIANT: 知识图和具体课程无关（D-047）：节点上不记"哪个单元教它"，那是已发布课程计划的投影（taught_by）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from studykit.domain.evidence import Evidence
from studykit.domain.ids import MISCONCEPTION_ID_RE, NODE_ID_RE

KINDS = {"concept": "概念", "term": "术语/命令", "skill": "技能"}
EDGE_KINDS = {"required": "必须先会", "helpful": "先会更好"}
STATES = {"new": "没学", "learning": "在学", "weak": "薄弱", "mastered": "掌握", "stale": "需复习"}
REASONS_SHOWN = 3         # 薄弱点描述里最多带几条理由


@dataclass(frozen=True)
class Edge:
    """一条先修关系（D-047）：学这个节点之前，id 是必须先会（required）还是先会更好（helpful）；by = 谁说的（运行 id 或 learner）。"""
    id: str
    kind: str = "required"
    by: str = ""


@dataclass
class Node:
    id: str
    title: str
    desc: str = ""
    kind: str = "concept"
    edges: list[Edge] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    misconceptions: dict[str, str] = field(default_factory=dict)   # 常见误解：id → 说明（事实层，和学习者无关，D-056）

    @property
    def topic(self) -> str:
        return self.id.split(".")[0]

    @property
    def requires(self) -> list[str]:
        """必须先会的节点。"""
        return [e.id for e in self.edges if e.kind == "required"]

    @property
    def prerequisites(self) -> list[str]:
        """全部先修（必须的 + 更好的）。"""
        return [e.id for e in self.edges]


# ---------- 图 ----------

def validate(nodes: dict[str, Node]) -> list[str]:
    """知识图的硬性检查：id 格式、类型、先修指向存在的节点、没有环、误解 id 格式。"""
    errors = []
    for n in nodes.values():
        if not NODE_ID_RE.match(n.id):
            errors.append(f"节点 id 格式不对：{n.id}（小写，用点分层，第一段是学科，如 tools.shell.glob）")
        if n.kind not in KINDS:
            errors.append(f"{n.id}：kind 只能是 {'/'.join(KINDS)}")
        for e in n.edges:
            if e.id not in nodes:
                errors.append(f"{n.id}：先修 {e.id} 不存在")
            if e.kind not in EDGE_KINDS:
                errors.append(f"{n.id}：先修 {e.id} 的 kind 只能是 {'/'.join(EDGE_KINDS)}")
        for mid in n.misconceptions:
            if not MISCONCEPTION_ID_RE.match(mid):
                errors.append(f"误解 id 格式不对：{n.id}/{mid}（小写字母开头，只用小写、数字、下划线）")
    color: dict[str, int] = {}

    def visit(nid: str, path: list[str]) -> None:
        color[nid] = 1
        for r in nodes[nid].prerequisites:
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
        reqs = [r for r in nodes[nid].prerequisites if r in nodes and r not in seen]
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


def studied(states: dict[str, NodeState], taught_by: dict[str, list[str]], earlier: list[str]) -> list[str]:
    """学过的（D-051）：路线上前面的单元（earlier，学习者已经学到过的）教过、但还没到"掌握"的知识点。
    备课默认学习者会，直接用、不当新词，第一次用到时放一个回顾。薄弱的不算（它们放慢讲，不是默认会）。"""
    rank = {u: i for i, u in enumerate(earlier)}
    out = [(min(rank[u] for u in units if u in rank), nid) for nid, units in taught_by.items()
           if any(u in rank for u in units) and (states.get(nid) is None or states[nid].state not in ("mastered", "weak"))]
    return [nid for _, nid in sorted(out, key=lambda x: x[0])]     # 先按单元在路线上的先后，同一单元里按教的顺序


def known_titles(states: dict[str, NodeState], topic: str | None = None) -> set[str]:
    """已知词表（D-017）：已掌握的节点的 id、标题和别名（标题、别名转小写）。"""
    out = set()
    for s in states.values():
        if s.state == "mastered" and (topic is None or s.node.topic == topic):
            out |= {s.node.id, *(t.lower() for t in [s.node.title, *s.node.aliases])}
    return out


# ---------- 先修要求（推理，D-047）：知识图 × 学习者状态 ----------

def readiness(nodes: dict[str, Node], states: dict[str, "NodeState"], taught: list[str],
              taught_by: dict[str, list[str]]) -> dict:
    """一个单元能不能学：它教的节点（taught）依赖的、不属于它的节点，按类型和现在的状态分组。

    返回 {"required": {"met": [...], "unmet": [...]}, "helpful": {...}, "learn": [...]}；
    每个先修带 for（本单元里哪些节点需要它）、taught_in（哪个单元教它）、by（谁说它是先修）、why（状态的理由）。
    """
    own = set(taught)
    found: dict[str, dict] = {}
    for nid in taught:
        for e in nodes[nid].edges if nid in nodes else []:
            if e.id in own:
                continue
            item = found.get(e.id)
            if item is None:
                s = states.get(e.id)
                n = nodes.get(e.id)
                item = found[e.id] = {"id": e.id, "title": n.title if n else e.id, "kind": e.kind, "by": e.by,
                                      "state": s.state if s else "new", "why": s.reasons if s else [],
                                      "taught_in": taught_by.get(e.id, []), "for": []}
            elif e.kind == "required":
                item["kind"] = "required"           # 有一个节点必须先会，就算必须
            item["for"].append(nid)
    out = {k: {"met": [], "unmet": []} for k in EDGE_KINDS}
    for item in found.values():
        out[item["kind"]]["met" if item["state"] == "mastered" else "unmet"].append(item)
    out["learn"] = [{"id": nid, "title": nodes[nid].title if nid in nodes else nid,
                     "state": states[nid].state if nid in states else "new"} for nid in taught]
    return out
