"""SQLite 存储（D-021）：证据 + 发布过的课程计划。

WAL + busy_timeout：网页服务器和命令行是两个进程，会同时写；WAL 让读写不互相阻塞，
写冲突时等锁而不是报错。每次操作开一个连接：服务器是多线程的，sqlite3 的连接不能跨线程共用。
INVARIANT: 证据、计划版本、发布记录只追加——由触发器强制，不靠自觉。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

from studykit.domain.evidence import Actor, Evidence
from studykit.domain.harness import Grade, Run, RunStep, Variant

MIGRATIONS = [
    """
    CREATE TABLE evidence (
      seq            INTEGER PRIMARY KEY,
      id             TEXT NOT NULL UNIQUE,
      ts             TEXT NOT NULL,
      learner        TEXT NOT NULL,
      actor_type     TEXT NOT NULL CHECK (actor_type IN ('learner', 'agent', 'system')),
      actor_id       TEXT NOT NULL,
      actor_role     TEXT NOT NULL DEFAULT '',
      actor_variant  TEXT NOT NULL DEFAULT '',
      verb           TEXT NOT NULL,
      object_type    TEXT NOT NULL,
      object_id      TEXT NOT NULL,
      object_version TEXT NOT NULL DEFAULT '',
      unit           TEXT,
      plan           TEXT,
      section        INTEGER,
      caused_by      TEXT REFERENCES evidence(id),
      run            TEXT,
      score          REAL CHECK (score IS NULL OR score BETWEEN 0 AND 1),
      ok             INTEGER,
      pending        INTEGER NOT NULL DEFAULT 0,
      payload        TEXT NOT NULL DEFAULT '{}',
      CHECK (pending = 0 OR score IS NULL)
    );
    CREATE INDEX evidence_by_time   ON evidence(learner, ts);
    CREATE INDEX evidence_by_unit   ON evidence(learner, unit, plan, ts);
    CREATE INDEX evidence_by_object ON evidence(learner, object_type, object_id, ts);
    CREATE INDEX evidence_by_verb   ON evidence(learner, verb, ts);
    CREATE UNIQUE INDEX one_grade_per_answer ON evidence(caused_by) WHERE verb = 'graded';

    CREATE TABLE evidence_node (
      evidence_id TEXT NOT NULL REFERENCES evidence(id),
      node        TEXT NOT NULL,
      PRIMARY KEY (evidence_id, node)
    );
    CREATE INDEX evidence_node_by_node ON evidence_node(node);

    CREATE TABLE plan_version (
      unit       TEXT NOT NULL,
      plan_id    TEXT NOT NULL,
      body       TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY (unit, plan_id)
    );
    CREATE TABLE publication (
      seq        INTEGER PRIMARY KEY,
      ts         TEXT NOT NULL,
      unit       TEXT NOT NULL,
      plan_id    TEXT NOT NULL,
      actor_type TEXT NOT NULL,
      actor_id   TEXT NOT NULL,
      FOREIGN KEY (unit, plan_id) REFERENCES plan_version(unit, plan_id)
    );

    CREATE TRIGGER evidence_no_update BEFORE UPDATE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER evidence_no_delete BEFORE DELETE ON evidence BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER evidence_node_no_update BEFORE UPDATE ON evidence_node BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER evidence_node_no_delete BEFORE DELETE ON evidence_node BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
    CREATE TRIGGER plan_version_no_update BEFORE UPDATE ON plan_version BEGIN SELECT RAISE(ABORT, 'plan versions are immutable'); END;
    CREATE TRIGGER plan_version_no_delete BEFORE DELETE ON plan_version BEGIN SELECT RAISE(ABORT, 'plan versions are immutable'); END;
    CREATE TRIGGER publication_no_update BEFORE UPDATE ON publication BEGIN SELECT RAISE(ABORT, 'publications are append-only'); END;
    CREATE TRIGGER publication_no_delete BEFORE DELETE ON publication BEGIN SELECT RAISE(ABORT, 'publications are append-only'); END;
    """,
    # 2：harness（D-022）
    """
    CREATE TABLE variant (
      id         TEXT PRIMARY KEY,
      agent      TEXT NOT NULL,
      parts      TEXT NOT NULL,
      first_seen TEXT NOT NULL
    );
    CREATE TABLE run (
      id         TEXT PRIMARY KEY,
      agent      TEXT NOT NULL,
      variant    TEXT NOT NULL REFERENCES variant(id),
      unit       TEXT NOT NULL,
      learner    TEXT NOT NULL,
      mode       TEXT NOT NULL CHECK (mode IN ('batch', 'interactive')),
      started    TEXT NOT NULL,
      seconds    REAL NOT NULL,
      exit_code  INTEGER,
      submitted  INTEGER NOT NULL,
      tokens     INTEGER NOT NULL,
      cost_usd   REAL NOT NULL,
      tool_calls TEXT NOT NULL,
      errors     TEXT NOT NULL,
      final_text TEXT NOT NULL,
      input      TEXT NOT NULL
    );
    CREATE INDEX run_by_agent ON run(agent, started);
    CREATE TABLE run_step (
      run     TEXT NOT NULL REFERENCES run(id),
      idx     INTEGER NOT NULL,
      kind    TEXT NOT NULL,
      tool    TEXT NOT NULL,
      ok      INTEGER NOT NULL,
      tokens  INTEGER NOT NULL,
      cost    REAL NOT NULL,
      summary TEXT NOT NULL,
      detail  TEXT NOT NULL,
      PRIMARY KEY (run, idx)
    );
    CREATE TABLE grade (
      id             TEXT PRIMARY KEY,
      ts             TEXT NOT NULL,
      run            TEXT NOT NULL REFERENCES run(id),
      grader         TEXT NOT NULL CHECK (grader IN ('check', 'judge', 'claim_check', 'practice_verify', 'review', 'outcome')),
      grader_version TEXT NOT NULL,
      actor          TEXT NOT NULL,
      score          REAL CHECK (score IS NULL OR score BETWEEN 0 AND 1),
      verdict        TEXT NOT NULL,
      dims           TEXT NOT NULL,
      issues         TEXT NOT NULL,
      refs           TEXT NOT NULL,
      detail         TEXT NOT NULL
    );
    CREATE INDEX grade_by_run ON grade(run, grader, ts);
    CREATE TRIGGER run_no_update BEFORE UPDATE ON run BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
    CREATE TRIGGER run_no_delete BEFORE DELETE ON run BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
    CREATE TRIGGER run_step_no_update BEFORE UPDATE ON run_step BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
    CREATE TRIGGER run_step_no_delete BEFORE DELETE ON run_step BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
    CREATE TRIGGER grade_no_update BEFORE UPDATE ON grade BEGIN SELECT RAISE(ABORT, 'grades are append-only'); END;
    CREATE TRIGGER grade_no_delete BEFORE DELETE ON grade BEGIN SELECT RAISE(ABORT, 'grades are append-only'); END;
    CREATE TRIGGER variant_no_update BEFORE UPDATE ON variant BEGIN SELECT RAISE(ABORT, 'variants are immutable'); END;
    """,
]


class SqliteStore:
    """实现 EvidenceStore、PlanStore 和 RunStore。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > len(MIGRATIONS):
                raise RuntimeError(f"{path} 的结构版本 {version} 比代码新（{len(MIGRATIONS)}），先更新代码")
            for i in range(version, len(MIGRATIONS)):
                c.executescript("BEGIN;" + MIGRATIONS[i] + f"PRAGMA user_version = {i + 1}; COMMIT;")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self):
        with self._conn() as c:
            # WHY: IMMEDIATE 在事务开始时就拿写锁；默认的 DEFERRED 先读后写，两个进程同时升级写锁会直接报 busy。
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise

    # ---------- 证据 ----------

    def append(self, *evidence: Evidence) -> None:
        with self._tx() as c:
            for e in evidence:
                c.execute(
                    "INSERT INTO evidence (id, ts, learner, actor_type, actor_id, actor_role, actor_variant, verb,"
                    " object_type, object_id, object_version, unit, plan, section, caused_by, run, score, ok, pending,"
                    " payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e.id, e.ts, e.learner, e.actor.type, e.actor.id, e.actor.role, e.actor.variant, e.verb,
                     e.object_type, e.object_id, e.object_version, e.unit, e.plan, e.section, e.caused_by, e.run,
                     e.score, None if e.ok is None else int(e.ok), int(e.pending),
                     json.dumps(e.payload, ensure_ascii=False)))
                c.executemany("INSERT INTO evidence_node (evidence_id, node) VALUES (?, ?)",
                              [(e.id, n) for n in dict.fromkeys(e.nodes)])

    def query(self, learner: str, *, unit: str | None = None, plan: str | None = None,
              verbs: Iterable[str] | None = None, object_type: str | None = None,
              object_id: str | None = None) -> list[Evidence]:
        where, args = ["learner = ?"], [learner]
        for col, val in (("unit", unit), ("plan", plan), ("object_type", object_type), ("object_id", object_id)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        if verbs is not None:
            verbs = list(verbs)
            where.append(f"verb IN ({','.join('?' * len(verbs))})")
            args += verbs
        return self._select(" AND ".join(where), args)

    def get(self, evidence_id: str) -> Evidence | None:
        rows = self._select("id = ?", [evidence_id])
        return rows[0] if rows else None

    def _select(self, where: str, args: list) -> list[Evidence]:
        # WHY: nodes 的顺序有意义（作答的第一个节点是它考的概念），按写入顺序拼回来。
        sql = (f"SELECT e.*, (SELECT group_concat(node, char(31)) FROM (SELECT node FROM evidence_node"
               f" WHERE evidence_id = e.id ORDER BY rowid)) AS nodes FROM evidence e WHERE {where} ORDER BY ts, seq")
        with self._conn() as c:
            return [_row(r) for r in c.execute(sql, args)]

    # ---------- 课程计划 ----------

    def publish(self, unit: str, plan: dict, ts: str, actor: Actor) -> None:
        pid = (plan.get("provenance") or {}).get("run")
        if not pid:
            raise ValueError("课程计划缺少 provenance.run（版本 id）")
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO plan_version (unit, plan_id, body, created_at) VALUES (?,?,?,?)",
                      (unit, pid, json.dumps(plan, ensure_ascii=False), ts))
            c.execute("INSERT INTO publication (ts, unit, plan_id, actor_type, actor_id) VALUES (?,?,?,?,?)",
                      (ts, unit, pid, actor.type, actor.id))

    def current(self, unit: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT v.body FROM publication p JOIN plan_version v USING (unit, plan_id)"
                          " WHERE p.unit = ? ORDER BY p.seq DESC LIMIT 1", (unit,)).fetchone()
        return json.loads(r["body"]) if r else None

    def version(self, unit: str, plan_id: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT body FROM plan_version WHERE unit = ? AND plan_id = ?", (unit, plan_id)).fetchone()
        return json.loads(r["body"]) if r else None

    def units(self) -> list[str]:
        with self._conn() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT unit FROM publication ORDER BY unit")]

    # ---------- harness ----------

    def add_run(self, run: Run, variant: Variant, steps: list[RunStep]) -> None:
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO variant (id, agent, parts, first_seen) VALUES (?,?,?,?)",
                      (variant.id, variant.agent, _j(variant.parts), run.started))
            c.execute("INSERT INTO run (id, agent, variant, unit, learner, mode, started, seconds, exit_code, submitted,"
                      " tokens, cost_usd, tool_calls, errors, final_text, input) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (run.id, run.agent, run.variant, run.unit, run.learner, run.mode, run.started, run.seconds,
                       run.exit_code, int(run.submitted), run.tokens, run.cost_usd, _j(run.tool_calls), _j(run.errors),
                       run.final_text, _j(run.input)))
            c.executemany("INSERT INTO run_step (run, idx, kind, tool, ok, tokens, cost, summary, detail)"
                          " VALUES (?,?,?,?,?,?,?,?,?)",
                          [(run.id, s.idx, s.kind, s.tool, int(s.ok), s.tokens, s.cost, s.summary, _j(s.detail))
                           for s in steps])

    def run(self, run_id: str) -> Run | None:
        rows = self._runs("id = ?", [run_id])
        return rows[0] if rows else None

    def runs(self, agent: str) -> list[Run]:
        return self._runs("agent = ?", [agent])

    def _runs(self, where: str, args: list) -> list[Run]:
        with self._conn() as c:
            return [Run(r["id"], r["agent"], r["variant"], r["unit"], r["learner"], r["mode"], r["started"],
                        json.loads(r["input"]), r["seconds"], r["exit_code"], bool(r["submitted"]), r["tokens"],
                        r["cost_usd"], json.loads(r["tool_calls"]), json.loads(r["errors"]), r["final_text"])
                    for r in c.execute(f"SELECT * FROM run WHERE {where} ORDER BY started, id", args)]

    def steps(self, run_id: str) -> list[RunStep]:
        with self._conn() as c:
            return [RunStep(r["idx"], r["kind"], r["tool"], bool(r["ok"]), r["tokens"], r["cost"], r["summary"],
                            json.loads(r["detail"]))
                    for r in c.execute("SELECT * FROM run_step WHERE run = ? ORDER BY idx", (run_id,))]

    def variant(self, variant_id: str) -> Variant | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM variant WHERE id = ?", (variant_id,)).fetchone()
        return Variant(r["agent"], json.loads(r["parts"])) if r else None

    def add_grade(self, g: Grade) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO grade (id, ts, run, grader, grader_version, actor, score, verdict, dims, issues, refs,"
                      " detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (g.id, g.ts, g.run, g.grader, g.grader_version, g.actor, g.score, g.verdict, _j(g.dims),
                       _j(g.issues), _j(g.refs), _j(g.detail)))

    def grades(self, run_id: str | None = None, agent: str | None = None) -> list[Grade]:
        where, args = ["1 = 1"], []
        if run_id:
            where.append("g.run = ?")
            args.append(run_id)
        if agent:
            where.append("r.agent = ?")
            args.append(agent)
        with self._conn() as c:
            return [Grade(r["id"], r["ts"], r["run"], r["grader"], r["grader_version"], r["actor"], r["score"],
                          r["verdict"], json.loads(r["dims"]), json.loads(r["issues"]), json.loads(r["refs"]),
                          json.loads(r["detail"]))
                    for r in c.execute(f"SELECT g.* FROM grade g JOIN run r ON r.id = g.run WHERE {' AND '.join(where)}"
                                       " ORDER BY g.ts, g.rowid", args)]


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _row(r: sqlite3.Row) -> Evidence:
    return Evidence(
        id=r["id"], ts=r["ts"], learner=r["learner"],
        actor=Actor(r["actor_type"], r["actor_id"], r["actor_role"], r["actor_variant"]),
        verb=r["verb"], object_type=r["object_type"], object_id=r["object_id"], object_version=r["object_version"],
        unit=r["unit"], plan=r["plan"], section=r["section"], caused_by=r["caused_by"], run=r["run"],
        score=r["score"], ok=None if r["ok"] is None else bool(r["ok"]), pending=bool(r["pending"]),
        nodes=tuple(r["nodes"].split("\x1f")) if r["nodes"] else (), payload=json.loads(r["payload"]))
