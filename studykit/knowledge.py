"""学习者模型（D-020）：一张知识图 + 一份证据；薄弱点、进度、已知词表、知识树都是投影。

    knowledge/<学科>.yaml         知识图：节点（标题、一句话描述、类型、先修、由哪些单元教）。人和 agent 都能改
    progress/attempts.jsonl       证据：答题（按 concept = 节点 id）
    progress/study_log.jsonl      证据：检查点、新词👍👎、导师观察（observation）……

INVARIANT: 节点状态只由证据算出，不手写。薄弱点 = 状态为 weak 的节点，不单独存。
WHY: 手写的薄弱点（原来在 learner.md 里）会和答题证据打架，也没法按课、按相关性查；
课程一多，agent 只能分级按需读：summary → related → show（F-031）。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from studykit import store

KNOWLEDGE = store.ROOT / "knowledge"
ID_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9][a-z0-9_-]*)+$")
KINDS = {"concept": "概念", "term": "术语/命令", "skill": "技能"}
STATES = {"new": "没学", "learning": "在学", "weak": "薄弱", "mastered": "掌握", "stale": "需复习"}
EVIDENCE_SHOWN = 3        # 薄弱点描述里最多带几条证据


class KnowledgeError(ValueError):
    pass


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


@dataclass
class Evidence:
    ts: str
    node: str
    sign: int                  # +1 支持"会了"，-1 支持"不会"，0 中性（比如学之前不认识）
    source: str                # attempt / checkpoint / term / observation
    note: str = ""


# ---------- 知识图 ----------

def topic_file(topic: str) -> Path:
    return KNOWLEDGE / f"{topic}.yaml"


def load_graph() -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for path in sorted(KNOWLEDGE.glob("*.yaml")) if KNOWLEDGE.exists() else []:
        data = store.load_yaml(path)
        for nid, n in (data.get("nodes") or {}).items():
            nodes[nid] = Node(nid, n.get("title") or nid, n.get("desc") or "", n.get("kind") or "concept",
                              list(n.get("requires") or []), list(n.get("units") or []), list(n.get("aliases") or []))
    # 兼容：syllabus.yaml 里的概念也算节点（没有描述和先修）。
    syllabus = store.load_yaml(store.SYLLABUS)
    for tid, t in (syllabus.get("topics") or {}).items():
        for unit in t.get("units") or []:
            for cid in unit.get("concepts") or []:
                nodes.setdefault(cid, Node(cid, (t.get("concepts") or {}).get(cid, cid), units=[unit["id"]]))
        for cid, name in (t.get("concepts") or {}).items():
            nodes.setdefault(cid, Node(cid, name))
    return nodes


def validate(nodes: dict[str, Node]) -> list[str]:
    """知识图的硬性检查：id 格式、类型、先修指向存在的节点、没有环。"""
    errors = []
    for n in nodes.values():
        if not ID_RE.match(n.id):
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


def add_nodes(proposed: list[dict]) -> list[str]:
    """把新节点写进 knowledge/<学科>.yaml。已有的节点不覆盖（导师审阅过的内容优先）。返回新增的 id。"""
    import yaml
    added, by_topic = [], defaultdict(list)
    existing = load_graph()
    for n in proposed:
        if n["id"] in existing:
            continue
        by_topic[n["id"].split(".")[0]].append(n)
    for topic, items in by_topic.items():
        path = topic_file(topic)
        data = store.load_yaml(path) or {"schema_version": 1, "topic": topic, "nodes": {}}
        data.setdefault("nodes", {})
        for n in items:
            data["nodes"][n["id"]] = {k: n[k] for k in ("title", "desc", "kind", "requires", "units", "aliases") if n.get(k)}
            added.append(n["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        header = "# 知识图（D-020）：节点 + 先修关系。状态不写在这里，由证据算出（study.py kg）。\n"
        path.write_text(header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    return added


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


# ---------- 证据 ----------

def evidence() -> dict[str, list[Evidence]]:
    by: dict[str, list[Evidence]] = defaultdict(list)
    for e in store.load_study_log():
        kind = e.get("event")
        if kind == "observation":
            by[e["node"]].append(Evidence(e["ts"], e["node"], {"weak": -1, "ok": 1}.get(e.get("polarity"), 0),
                                          "observation", e.get("note", "")))
        elif kind == "term" and e.get("node") and e.get("action") in ("known", "unknown"):
            # 👎「我早就知道」= 会了；👍「有用，我确实不懂」= 学之前不认识，不算薄弱，只是在学。
            by[e["node"]].append(Evidence(e["ts"], e["node"], 1 if e["action"] == "known" else 0, "term",
                                          "点了「我早就知道」" if e["action"] == "known" else "学之前不认识这个词"))
        elif kind == "checkpoint":
            where = f"{e.get('unit')} 第 {int(e.get('section', 0)) + 1} 节检查点"
            for nid in e.get("nodes") or []:
                by[nid].append(Evidence(e["ts"], nid, 1 if e.get("ok") else -1, "checkpoint",
                                        f"{where}{'做对了' if e.get('ok') else '做错了'}"))
    return by


# ---------- 投影：节点状态 ----------

@dataclass
class NodeState:
    node: Node
    state: str
    reasons: list[str]
    mastery: int = 0
    evidence: list[Evidence] = field(default_factory=list)

    def brief(self) -> dict:
        return {"id": self.node.id, "title": self.node.title, "state": self.state}


def states(nodes: dict[str, Node] | None = None) -> dict[str, NodeState]:
    nodes = nodes if nodes is not None else load_graph()
    stats = store.concept_stats(store.load_attempts())
    ev = evidence()
    out = {}
    for nid in set(nodes) | set(stats) | set(ev):
        node = nodes.get(nid) or Node(nid, nid)
        items = sorted(ev.get(nid, []), key=lambda x: x.ts)
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
                reasons += [m.get("note") or f"{m['quiz']} {m['qid']} 得分 {m['score']}" for m in s["recent_mistakes"]]
            elif s["stale"]:
                st = "stale"
                reasons.append("超过 14 天没练")
            elif mastery >= 1 and st != "weak":
                st = "mastered" if mastery >= 2 else "learning"
        out[nid] = NodeState(node, st, [r for r in reasons if r][:EVIDENCE_SHOWN], mastery, items)
    return out


def known_titles(topic: str | None = None, sts: dict[str, NodeState] | None = None) -> set[str]:
    """已知词表（D-017）：已掌握的节点的 id、标题和别名（标题、别名转小写）。"""
    sts = sts if sts is not None else states()
    out = set()
    for s in sts.values():
        if s.state == "mastered" and (topic is None or s.node.topic == topic):
            out |= {s.node.id, *(t.lower() for t in [s.node.title, *s.node.aliases])}
    return out


# ---------- 分级查询（给 agent 和导师） ----------

def summary(max_weak: int = 5) -> str:
    """第 0 级：每个学科的数量和前几个薄弱点标题。长度不随节点数增长。"""
    sts = states()
    by: dict[str, list[NodeState]] = defaultdict(list)
    for s in sts.values():
        by[s.node.topic].append(s)
    if not by:
        return "知识图还是空的。"
    lines = ["| 学科 | 掌握 | 在学 | 薄弱 | 需复习 | 没学 | 薄弱点（前几个） |", "|---|---|---|---|---|---|---|"]
    for topic, ss in sorted(by.items()):
        c = defaultdict(int)
        for s in ss:
            c[s.state] += 1
        weak = sorted((s for s in ss if s.state in ("weak", "stale")), key=lambda s: s.evidence[-1].ts if s.evidence else "", reverse=True)
        names = "、".join(f"{s.node.title}（`{s.node.id}`）" for s in weak[:max_weak]) + ("…" if len(weak) > max_weak else "")
        lines.append(f"| {topic} | {c['mastered']} | {c['learning']} | {c['weak']} | {c['stale']} | {c['new']} | {names or '—'} |")
    return "\n".join(lines)


def related(unit: str, depth: int = 2) -> dict:
    """第 1 级：这个单元教的节点 + 往上 depth 层的先修里，没掌握的节点。只给 id / 标题 / 状态。"""
    nodes = load_graph()
    sts = states(nodes)
    taught = [n.id for n in nodes.values() if unit in n.units]
    dist = ancestors(nodes, taught, depth)
    prereq = [sts[nid].brief() | {"distance": d} for nid, d in sorted(dist.items(), key=lambda kv: kv[1])
              if d > 0 and sts[nid].state != "mastered"]
    return {"unit": unit,
            "taught": [sts[nid].brief() for nid in taught],
            "prerequisites_not_mastered": prereq,
            "weak": [sts[nid].brief() | {"why": sts[nid].reasons} for nid in list(dist) if sts[nid].state in ("weak", "stale")]}


def show(nid: str) -> dict:
    """第 2 级：一个节点的全部信息。"""
    nodes = load_graph()
    sts = states(nodes)
    if nid not in sts:
        raise KnowledgeError(f"没有节点 {nid}")
    s = sts[nid]
    return {"id": nid, "title": s.node.title, "desc": s.node.desc, "kind": s.node.kind, "state": s.state,
            "reasons": s.reasons, "mastery": s.mastery, "units": s.node.units, "aliases": s.node.aliases,
            "requires": [sts[r].brief() if r in sts else {"id": r, "state": "?"} for r in s.node.requires],
            "required_by": [sts[m].brief() for m, n in nodes.items() if nid in n.requires],
            "evidence": [{"ts": e.ts[:16], "sign": e.sign, "source": e.source, "note": e.note} for e in s.evidence]}


def tree(topic: str | None = None) -> dict:
    """知识树视图的数据：节点（带状态和层数）+ 先修边。"""
    nodes = load_graph()
    sts = states(nodes)
    ds = depths(nodes)
    keep = {nid for nid, n in nodes.items() if topic is None or n.topic == topic}
    return {"topics": sorted({n.topic for n in nodes.values()}),
            "nodes": [{"id": nid, "title": nodes[nid].title, "desc": nodes[nid].desc, "kind": nodes[nid].kind,
                       "state": sts[nid].state, "reasons": sts[nid].reasons, "depth": ds[nid], "units": nodes[nid].units}
                      for nid in sorted(keep, key=lambda x: (ds[x], x))],
            "edges": [[r, nid] for nid in keep for r in nodes[nid].requires if r in keep]}


def observe(nid: str, polarity: str, note: str) -> dict:
    """导师观察：把"讲 glob 那段完全跟不上"这样的话记到具体节点上。"""
    if polarity not in ("weak", "ok"):
        raise KnowledgeError("polarity 只能是 weak 或 ok")
    if not ID_RE.match(nid):
        raise KnowledgeError(f"节点 id 格式不对：{nid}")
    return store.record_study(event="observation", node=nid, polarity=polarity, note=note)
