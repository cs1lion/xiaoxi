from __future__ import annotations

import sqlite3
import json
import math
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

from ..config import guard_sqlite_path, settings

SPARK_STATES = ("latent", "surfaced", "spent")
SPARK_TRANSITIONS = {("latent", "surfaced"), ("surfaced", "spent")}
MAX_ACTIVE_LATENT_SPARKS = 5
SEED_STATES = ("active", "incubating", "digested", "dormant", "archived")
MOOD_STATES = ("active", "easing", "resolved")
PERSISTED_EDGE_KINDS = ("parent_child", "related_memory", "epiphany")

KINDS = ("fact", "episode", "todo", "self_state")
SOURCES = ("stated", "inferred", "uncertain")
STATUSES = ("active", "superseded", "done", "conflict")
LESSON_STATUSES = ("active", "retired")
SCHEMA_VERSION = 4  # Additive tables below keep this: older files gain empty
# tables on open, so the "newer schema" guard still means a real future schema.

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,
    subject       TEXT NOT NULL DEFAULT 'user',
    key           TEXT NOT NULL,
    value         TEXT NOT NULL,
    source        TEXT NOT NULL DEFAULT 'stated',
    status        TEXT NOT NULL DEFAULT 'active',
    supersedes    INTEGER,
    importance    REAL NOT NULL DEFAULT 0.5,
    confidence    REAL NOT NULL DEFAULT 0.8,
    session_id    TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_used_at  REAL,
    use_count     INTEGER NOT NULL DEFAULT 0,
    evidence_message_id INTEGER,
    evidence_quote TEXT NOT NULL DEFAULT '',
    evidence_state TEXT NOT NULL DEFAULT 'legacy',
    decision      TEXT NOT NULL DEFAULT 'legacy',
    last_observed_message_id INTEGER,
    identity_slot TEXT NOT NULL DEFAULT '',
    preference_target TEXT NOT NULL DEFAULT '',
    preference_level TEXT NOT NULL DEFAULT '',
    preference_scope TEXT NOT NULL DEFAULT '',
    preference_as_of REAL,
    retired_by_message_id INTEGER,
    -- MemRL-style utility: Q in [0,1] and how many outcome signals produced it.
    -- Retrieval shrinks Q toward the neutral prior while n is small, so one
    -- lucky/unlucky turn can never dominate relevance.
    utility REAL NOT NULL DEFAULT 0.5,
    utility_n INTEGER NOT NULL DEFAULT 0,
    last_reward_at REAL,
    -- Soft mute for retrieval assembly (user-managed, reversible). Not a
    -- lifecycle status, never a delete: the physical hard-delete path stays
    -- the explicit 忘掉 action only.
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    turns       INTEGER NOT NULL DEFAULT 0,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    mode        TEXT NOT NULL DEFAULT 'text',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS memory_control (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    write_epoch INTEGER NOT NULL DEFAULT 0,
    write_floor INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO memory_control(id, write_epoch, write_floor) VALUES (1, 0, 0);

-- One row per user turn that actually injected memories/lessons. The next user
-- message supplies the outcome signal. Utility is updated only after it arrives.
-- This is an audit trail as much as a reward table: deleting a memory does not
-- rewrite history, and an unsettled turn can always be explained.
CREATE TABLE IF NOT EXISTS memory_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    user_message_id INTEGER NOT NULL,
    memory_ids      TEXT NOT NULL DEFAULT '[]',
    lesson_ids      TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    settled_at      REAL,
    reward          REAL,
    reward_reason   TEXT NOT NULL DEFAULT '',
    reward_detail   TEXT NOT NULL DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_turn ON memory_usage(session_id, user_message_id);
CREATE INDEX IF NOT EXISTS idx_usage_open ON memory_usage(settled_at);

-- Procedural memory (EvolveR / ACE / MemSkill): reusable "when X, do Y" lessons
-- distilled from reflection. They never mix with subject="user" facts and are
-- retired by evidence, not by a fixed lifetime.
CREATE TABLE IF NOT EXISTS lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger       TEXT NOT NULL,
    action        TEXT NOT NULL,
    cues          TEXT NOT NULL DEFAULT '[]',
    scope         TEXT NOT NULL DEFAULT 'dialogue',
    source        TEXT NOT NULL DEFAULT 'reflection',
    evidence_refs TEXT NOT NULL DEFAULT '[]',
    helpful       INTEGER NOT NULL DEFAULT 0,
    harmful       INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    last_used_at  REAL,
    use_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_lesson_status ON lessons(status);

CREATE TABLE IF NOT EXISTS diaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    mood          TEXT NOT NULL,
    energy        REAL NOT NULL,
    topic_keys    TEXT NOT NULL DEFAULT '[]',
    source_message_ids TEXT NOT NULL DEFAULT '[]',
    created_at    REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_diary_date ON diaries(date);

CREATE TABLE IF NOT EXISTS drifts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic         TEXT NOT NULL,
    attitude      TEXT NOT NULL,
    weight        REAL NOT NULL,
    evidence_diary_ids TEXT NOT NULL DEFAULT '[]',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_drift_topic ON drifts(topic);

CREATE TABLE IF NOT EXISTS sparks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    state         TEXT NOT NULL DEFAULT 'latent',
    heat          REAL NOT NULL,
    born_diary_id INTEGER,
    surfaced_at   REAL,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spark_state ON sparks(state);

CREATE TABLE IF NOT EXISTS self_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    taste TEXT NOT NULL DEFAULT '[]',
    worldview TEXT NOT NULL DEFAULT '[]',
    curiosity_seeds TEXT NOT NULL DEFAULT '[]',
    last_cycle_at REAL,
    cycles_today INTEGER NOT NULL DEFAULT 0,
    cycles_on TEXT
);
INSERT OR IGNORE INTO self_state(id) VALUES (1);

CREATE TABLE IF NOT EXISTS seeds (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    heat REAL NOT NULL DEFAULT 0.5,
    cooldown REAL NOT NULL DEFAULT 0,
    parent_seed_ids TEXT NOT NULL DEFAULT '[]',
    child_seed_ids TEXT NOT NULL DEFAULT '[]',
    source_refs TEXT NOT NULL DEFAULT '[]',
    related_refs TEXT NOT NULL DEFAULT '[]',
    reflections TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    last_reflected_at REAL,
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seed_status ON seeds(status);

CREATE TABLE IF NOT EXISTS reflections (
    id TEXT PRIMARY KEY,
    seed_id TEXT NOT NULL,
    narrative TEXT NOT NULL,
    discovery TEXT NOT NULL,
    source_refs TEXT NOT NULL DEFAULT '[]',
    child_seed_ids TEXT NOT NULL DEFAULT '[]',
    status_changes TEXT NOT NULL DEFAULT '[]',
    epiphany_edges TEXT NOT NULL DEFAULT '[]',
    processed INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reflection_seed ON reflections(seed_id);

CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.5,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_unique ON graph_edges(source, target, kind);
CREATE INDEX IF NOT EXISTS idx_edge_kind ON graph_edges(kind);

CREATE TABLE IF NOT EXISTS mood_state (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    narrative TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    source_refs TEXT NOT NULL DEFAULT '[]',
    influence_note TEXT NOT NULL DEFAULT '',
    related_summary TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'active',
    created_at REAL,
    expires_at REAL
);
INSERT OR IGNORE INTO mood_state(id) VALUES (1);
"""


def _json_list(value: Any) -> list[Any]:
    """Decode a stored JSON array; corruption degrades to empty, never raises."""
    try:
        decoded = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _diary_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["topic_keys"] = _json_list(record.get("topic_keys"))
    record["source_message_ids"] = _json_list(record.get("source_message_ids"))
    return record


def _drift_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["evidence_diary_ids"] = _json_list(record.get("evidence_diary_ids"))
    return record


def _seed_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for key in ("parent_seed_ids", "child_seed_ids", "source_refs", "related_refs", "reflections"):
        record[key] = _json_list(record.get(key))
    return record


def _reflection_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    for key in ("source_refs", "child_seed_ids", "status_changes", "epiphany_edges"):
        record[key] = _json_list(record.get(key))
    record["processed"] = bool(record.get("processed"))
    return record


def _edge_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _mood_row(row: sqlite3.Row) -> dict[str, Any]:
    record = dict(row)
    record["source_refs"] = _json_list(record.get("source_refs"))
    return record


@dataclass(slots=True)
class RankingRow:
    """Ranking-time projection of a memory: only what a score reads.

    Deliberately NOT a `Memory` with holes. Retrieval ranks thousands of rows
    per turn and keeps ~6; building the full record (30 fields, `**kwargs`
    unpack) for every one of them was 88 of the 166 ms a 10k-row warm turn cost
    (2026-10-25). This carries the ranking inputs and nothing else, so there is
    no "half-populated Memory" that a caller could mistake for a real record.
    """

    id: int
    key: str
    value: str
    kind: str
    subject: str
    source: str
    status: str
    importance: float
    confidence: float
    updated_at: float
    utility: float
    utility_n: int
    disabled: bool

    def __post_init__(self) -> None:
        # SQLite hands back 0/1; the ranking gate wants a real bool.
        self.disabled = bool(self.disabled)


# Column order must match RankingRow's field order (positional construction).
RANKING_COLUMNS = ("id, key, value, kind, subject, source, status, importance, "
                   "confidence, updated_at, utility, utility_n, disabled")


@dataclass(slots=True)
class Memory:
    id: int
    kind: str
    subject: str
    key: str
    value: str
    source: str
    status: str
    supersedes: int | None
    importance: float
    confidence: float
    session_id: str | None
    created_at: float
    updated_at: float
    last_used_at: float | None
    use_count: int
    evidence_message_id: int | None = None
    evidence_quote: str = ""
    evidence_state: str = "legacy"
    decision: str = "legacy"
    last_observed_message_id: int | None = None
    identity_slot: str = ""
    preference_target: str = ""
    preference_level: str = ""
    preference_scope: str = ""
    preference_as_of: float | None = None
    retired_by_message_id: int | None = None
    utility: float = 0.5
    utility_n: int = 0
    last_reward_at: float | None = None
    disabled: bool = False

    def __post_init__(self) -> None:
        # SQLite hands back 0/1 for the flag; the exported shape stays a bool.
        self.disabled = bool(self.disabled)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryStore:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.db_path
        # 生产库哨兵（#39）：测试进程（TEST_SENTINEL=1）打开仓库 data/ 下的库
        # 就地失败。检查在构造点而不是 import 点——静态检查器只 import 配置，
        # 任何真的读写记忆的路径都绕不开这里。
        guard_sqlite_path(self.db_path)
        self._lock = threading.RLock()
        self._closed = False
        # 与 AssistantStore 一致：父目录不存在就建出来。否则全新检出（data/ 被
        # gitignore）时，模块级 store = MemoryStore() 会在 ensure_dirs() 之前炸，
        # 报一句和真实原因无关的 "unable to open database file"。
        parent = Path(self.db_path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._migrate()
        except BaseException:
            self._conn.close()
            self._closed = True
            raise
        self.version = 0  # bumped on any write so the retriever can invalidate

    # ---------- low level ----------
    @contextmanager
    def _transaction(self, *, track_write: bool = False, invalidate: bool = False):
        with self._lock:
            if self._closed:
                raise RuntimeError("MemoryStore is closed")
            self._conn.execute("BEGIN IMMEDIATE")
            changes_before = self._conn.total_changes
            try:
                yield self._conn
                if invalidate:
                    self._invalidate_pending(self._conn)
                self._conn.commit()
                if track_write and self._conn.total_changes != changes_before:
                    self.version += 1
            except BaseException:
                self._conn.rollback()
                raise

    def _migrate(self) -> None:
        """Add fields without guessing preference semantics for existing rows."""
        with self._transaction() as db:
            existing = int(db.execute("PRAGMA user_version").fetchone()[0])
            if existing > SCHEMA_VERSION:
                raise RuntimeError(f"memory database schema v{existing} is newer than supported v{SCHEMA_VERSION}")
            # executescript commits an open transaction; execute each statement instead.
            # Strip whole-line SQL comments first: a semicolon inside prose (we hit
            # "outcome signal; utility ...") would otherwise become a statement
            # boundary and fail with a misleading "near utility" syntax error.
            schema_sql = "\n".join(
                line for line in SCHEMA.splitlines() if not line.lstrip().startswith("--"))
            for statement in schema_sql.split(";"):
                if statement.strip():
                    db.execute(statement)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(memories)")}
            additions = {
                "evidence_message_id": "INTEGER",
                "evidence_quote": "TEXT NOT NULL DEFAULT ''",
                "evidence_state": "TEXT NOT NULL DEFAULT 'legacy'",
                "decision": "TEXT NOT NULL DEFAULT 'legacy'",
                "last_observed_message_id": "INTEGER",
                "identity_slot": "TEXT NOT NULL DEFAULT ''",
                "preference_target": "TEXT NOT NULL DEFAULT ''",
                "preference_level": "TEXT NOT NULL DEFAULT ''",
                "preference_scope": "TEXT NOT NULL DEFAULT ''",
                "preference_as_of": "REAL",
                "retired_by_message_id": "INTEGER",
                "utility": "REAL NOT NULL DEFAULT 0.5",
                "utility_n": "INTEGER NOT NULL DEFAULT 0",
                "last_reward_at": "REAL",
                "disabled": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
            db.execute("DROP INDEX IF EXISTS idx_mem_key_active")
            db.execute("DROP INDEX IF EXISTS idx_mem_identity_active")
            db.execute("""CREATE UNIQUE INDEX idx_mem_identity_active
                ON memories(kind, subject, key, identity_slot) WHERE status = 'active'""")
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @property
    def write_epoch(self) -> int:
        return self.write_state()[0]

    @property
    def write_floor(self) -> int:
        return self.write_state()[1]

    def write_state(self) -> tuple[int, int]:
        row = self._query("SELECT write_epoch, write_floor FROM memory_control WHERE id = 1")[0]
        return int(row[0]), int(row[1])

    @staticmethod
    def _invalidate_pending(db) -> None:
        # This conservative global floor also blocks fresh re-extraction of old
        # messages after restart; merely cancelling the current await is insufficient.
        db.execute("""UPDATE memory_control SET write_epoch = write_epoch + 1,
                      write_floor = MAX(write_floor, COALESCE((SELECT MAX(id) FROM messages), 0))
                      WHERE id = 1""")

    def invalidate_pending(self) -> int:
        with self._transaction(invalidate=True) as db:
            epoch = int(db.execute("SELECT write_epoch FROM memory_control WHERE id = 1").fetchone()[0])
        return epoch + 1

    def _write(self, sql: str, params: Iterable[Any]) -> sqlite3.Cursor:
        with self._transaction(track_write=True) as db:
            cur = db.execute(sql, tuple(params))
        return cur

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, tuple(params)).fetchall())

    # ---------- memories ----------
    def upsert(
        self,
        *,
        kind: str,
        value: str,
        key: str | None = None,
        subject: str = "user",
        source: str = "stated",
        status: str = "active",
        importance: float = 0.5,
        confidence: float = 0.8,
        session_id: str | None = None,
        supersedes: int | None = None,
        evidence_message_id: int | None = None,
        evidence_quote: str = "",
        evidence_state: str = "manual",
        decision: str | None = None,
        expected_write_epoch: int | None = None,
        identity_slot: str = "",
        preference_target: str = "",
        preference_level: str = "",
        preference_scope: str = "",
        preference_as_of: float | None = None,
        force_revision: bool = False,
    ) -> int | None:
        if status not in STATUSES or source not in SOURCES:
            raise ValueError("invalid memory status or source")
        if evidence_state not in ("legacy", "manual", "linked"):
            raise ValueError("invalid evidence state")
        if not isinstance(evidence_quote, str):
            raise ValueError("evidence_quote must be text")
        if evidence_message_id is not None and (type(evidence_message_id) is not int or evidence_message_id < 1):
            raise ValueError("evidence_message_id must be a positive integer")
        if expected_write_epoch is not None and (type(expected_write_epoch) is not int or expected_write_epoch < 0):
            raise ValueError("expected_write_epoch must be a nonnegative integer")
        if not all(isinstance(field, str) for field in
                   (identity_slot, preference_target, preference_level, preference_scope)):
            raise ValueError("invalid preference fields")
        structured = bool(preference_target or preference_level or preference_scope)
        if structured and (not preference_target or preference_level not in ("like", "favorite", "dislike")
                           or preference_scope not in ("long_term", "recent", "current", "unspecified")):
            raise ValueError("incomplete preference fields")
        if structured and evidence_state != "linked":
            raise ValueError("structured preferences require verified user evidence")
        if preference_as_of is not None and not structured:
            raise ValueError("preference time requires a structured preference")
        if type(force_revision) is not bool:
            raise ValueError("force_revision must be a boolean")
        now = time.time()
        value = " ".join(value.strip().split())
        key = (key or value).strip().lower()
        with self._transaction(track_write=True) as db:
            control = db.execute("SELECT write_epoch, write_floor FROM memory_control WHERE id = 1").fetchone()
            if expected_write_epoch is not None and control["write_epoch"] != expected_write_epoch:
                return None
            if evidence_state == "linked" and evidence_message_id is not None and evidence_message_id <= control["write_floor"]:
                return None
            if evidence_state == "linked":
                evidence = db.execute("SELECT session_id, role, text, created_at FROM messages WHERE id = ?",
                                      (evidence_message_id,)).fetchone()
                if (evidence is None or evidence["role"] != "user" or not evidence_quote.strip()
                        or evidence_quote not in evidence["text"]
                        or (session_id is not None and evidence["session_id"] != session_id)):
                    raise ValueError("linked evidence requires an exact quote from a user message in this session")
                session_id = evidence["session_id"]
            if structured:
                # The final write transaction repeats the semantic gate. Model
                # metadata and a caller-supplied slot cannot grant set membership.
                from .preference import normalize_preference, preference_change_allowed
                verified = normalize_preference(
                    {"target": preference_target, "level": preference_level, "scope": preference_scope},
                    evidence_quote, value, key, kind, subject, source,
                    message_text=evidence["text"],
                )
                if verified is None:
                    raise ValueError("unsupported preference evidence")
                slot = json.dumps([preference_target, preference_scope], ensure_ascii=False, separators=(",", ":"))
                if identity_slot and identity_slot != slot:
                    raise ValueError("preference slot does not match verified evidence")
                identity_slot = slot
                # Never accept a model/caller timestamp in place of the message.
                preference_as_of = evidence["created_at"]
                # A superseded slot stays retired after further changes or a
                # restart. Only looking at active rivals allows an old favorite
                # to revive once its replacement is itself superseded.
                retired = db.execute(
                    """SELECT MAX(retired_by_message_id) FROM memories
                       WHERE kind = ? AND subject = ? AND key = ? AND
                         (identity_slot = ? OR (? = 'favorite' AND preference_level = 'favorite')
                          OR (preference_target = ? AND preference_level != '' AND
                              (preference_level = 'dislike') != (? = 'dislike')))""",
                    (kind, subject, key, identity_slot, preference_level, preference_target, preference_level),
                ).fetchone()[0]
                if retired is not None and evidence_message_id <= retired:
                    return None
            elif evidence_state == "linked" and identity_slot:
                raise ValueError("automatic slots require verified preference fields")
            row = db.execute(
                "SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND identity_slot = ? AND status = 'active'",
                (kind, subject, key, identity_slot),
            ).fetchone()
            replacements: list[sqlite3.Row] = []
            if evidence_state == "linked":
                family = list(db.execute(
                    "SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND status = 'active'",
                    (kind, subject, key)))
                if structured:
                    # Unknown legacy/manual semantics block an automatic widening
                    # of the property's cardinality. We never label old text by guess.
                    blockers = [m for m in family if not m["preference_target"]]
                    rivals = [m for m in family if m["id"] != (row["id"] if row else None) and (
                        (preference_level == "favorite" and m["preference_level"] == "favorite"
                         and m["preference_target"] != preference_target)
                        or (m["preference_target"] == preference_target and
                            (m["preference_level"] == "dislike") != (preference_level == "dislike")))]
                    if row is not None and row["preference_level"] != preference_level:
                        rivals.append(row)
                    latest = max((max(m["last_observed_message_id"] or 0,
                                      m["evidence_message_id"] or 0) for m in rivals), default=0)
                    if rivals and evidence_message_id < latest:
                        return None
                    can_change = (source == "stated" and evidence_message_id > latest
                                  and preference_change_allowed(evidence_quote, level=preference_level,
                                                                message_text=evidence["text"]))
                    if blockers or (rivals and not can_change):
                        status, decision, supersedes = "conflict", "conflict", None
                    elif rivals:
                        replacements = rivals
                        # A single deterministic predecessor is sufficient for
                        # navigation; all retired rows remain in the property history.
                        supersedes = max(rivals, key=lambda m: m["id"])["id"]
                        decision = "update"
                elif any(m["identity_slot"] for m in family):
                    # An unstructured output cannot replace or silently join a set.
                    status, decision, supersedes = "conflict", "conflict", None
            if (status == "active" and evidence_state == "linked" and row is not None
                    and (row["last_observed_message_id"] is not None or row["evidence_message_id"] is not None)):
                latest_message = max(row["last_observed_message_id"] or 0, row["evidence_message_id"] or 0)
                if latest_message > evidence_message_id:
                    return None
                if latest_message == evidence_message_id and row["value"] != value:
                    status, decision, supersedes = "conflict", "conflict", None
            same_claim = (row is not None and structured and
                          row["preference_target"] == preference_target and
                          row["preference_level"] == preference_level and
                          row["preference_scope"] == preference_scope)
            if status == "active" and row is not None and not force_revision and (row["value"] == value or same_claim):
                # Repetition is not fresh evidence and cannot refresh an inference's lifetime.
                # A current explicit assertion may strengthen its source, never weaken it.
                strength = {"uncertain": 0, "inferred": 1, "stated": 2}
                stronger = strength[source] > strength.get(row["source"], -1)
                valid_new_evidence = evidence_state in ("manual", "linked")
                replace_evidence = valid_new_evidence and (
                    stronger or (source == row["source"] and row["evidence_state"] == "legacy"))
                retained_source = source if stronger and valid_new_evidence else row["source"]
                latest_observed = row["last_observed_message_id"]
                if evidence_state == "linked" and strength[source] >= strength.get(row["source"], -1):
                    latest_observed = max(latest_observed or 0, row["evidence_message_id"] or 0, evidence_message_id)
                db.execute(
                    """UPDATE memories SET source = ?, evidence_message_id = ?, evidence_quote = ?,
                       evidence_state = ?, session_id = ?, confidence = ?, decision = 'noop',
                       last_observed_message_id = ?, preference_as_of = ? WHERE id = ?""",
                    (retained_source,
                     evidence_message_id if replace_evidence else row["evidence_message_id"],
                     evidence_quote if replace_evidence else row["evidence_quote"],
                     evidence_state if replace_evidence else row["evidence_state"],
                     session_id if replace_evidence else row["session_id"],
                     confidence if stronger and valid_new_evidence else row["confidence"],
                     latest_observed,
                     preference_as_of if replace_evidence and structured else row["preference_as_of"], row["id"]),
                )
                memory_id = int(row["id"])
            else:
                prior = db.execute("SELECT * FROM memories WHERE id = ?", (supersedes,)).fetchone() if supersedes is not None else None
                protected = source != "stated" and any(
                    candidate is not None and candidate["source"] == "stated" and candidate["status"] == "active"
                    for candidate in (row, prior)
                )
                if status == "conflict" or protected:
                    status, decision, supersedes = "conflict", "conflict", None
                elif status == "active":
                    if prior is not None and ((prior["kind"], prior["subject"], prior["key"]) != (kind, subject, key)
                            or (prior["identity_slot"] != identity_slot and prior not in replacements)):
                        raise ValueError("supersedes must refer to the same memory identity")
                    if row is not None:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id if structured else None, row["id"]))
                        supersedes = supersedes if supersedes is not None else int(row["id"])
                    if supersedes is not None:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id if structured else None, supersedes))
                    for replaced in replacements:
                        db.execute("""UPDATE memories SET status = 'superseded', updated_at = ?,
                                   retired_by_message_id = COALESCE(?, retired_by_message_id) WHERE id = ?""",
                                   (now, evidence_message_id, replaced["id"]))
                    decision = decision or ("update" if row is not None or supersedes is not None else "add")
                else:
                    supersedes = None
                    decision = decision or "add"
                cur = db.execute(
                    """INSERT INTO memories
                       (kind, subject, key, value, source, status, supersedes, importance,
                        confidence, session_id, created_at, updated_at, evidence_message_id,
                        evidence_quote, evidence_state, decision, last_observed_message_id,
                        identity_slot, preference_target, preference_level, preference_scope, preference_as_of)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (kind, subject, key, value, source, status, supersedes, importance, confidence,
                     session_id, now, now, evidence_message_id, evidence_quote, evidence_state, decision,
                     evidence_message_id if evidence_state == "linked" else None,
                     identity_slot, preference_target, preference_level, preference_scope, preference_as_of),
                )
                memory_id = int(cur.lastrowid)
        return memory_id

    def add(self, mem: dict[str, Any]) -> int | None:
        return self.upsert(**mem)

    def get(self, memory_id: int) -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return Memory(**dict(rows[0])) if rows else None

    def active_for(self, kind: str, subject: str, key: str, identity_slot: str = "") -> Memory | None:
        rows = self._query("SELECT * FROM memories WHERE kind = ? AND subject = ? AND key = ? AND identity_slot = ? AND status = 'active'",
                           (kind, subject, key.strip().lower(), identity_slot))
        return Memory(**dict(rows[0])) if rows else None

    def count_inferences(self, session_id: str) -> int:
        return int(self._query("SELECT COUNT(*) FROM memories WHERE session_id = ? AND source IN ('inferred', 'uncertain')",
                               (session_id,))[0][0])

    def list_memories(
        self,
        *,
        status: str | None = "active",
        kind: str | None = None,
        subject: str | None = None,
        limit: int = 200,
        include_history: bool = False,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if status and not include_history:
            sql += " AND status = ?"
            params.append(status)
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [Memory(**dict(r)) for r in self._query(sql, params)]

    def history_for_key(self, key: str, limit: int = 10) -> list[Memory]:
        rows = self._query(
            "SELECT * FROM memories WHERE key = ? ORDER BY created_at DESC LIMIT ?", (key, limit)
        )
        return [Memory(**dict(r)) for r in rows]

    def retrieval_candidates(self, *, include_done: bool = False) -> list[Memory]:
        """Read one live eligible set; historical rows cannot consume its limit.

        Unlike the paginated management list, retrieval has no implicit 5000-row
        cut-off. The retriever indexes and ranks this same query-time snapshot.
        """
        statuses = ("active", "done") if include_done else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        rows = self._query(
            f"SELECT * FROM memories WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, id DESC", statuses)
        return [Memory(**dict(row)) for row in rows]

    def retrieval_ranking_rows(self, *, include_done: bool = False) -> list[RankingRow]:
        """The same candidate set, in the cheap shape ranking actually needs.

        Retrieval used to start from `retrieval_candidates()`, which builds one
        `Memory` per row -- 30 fields, each arriving as a `**kwargs` unpack --
        for the whole table, every turn, and then throws all but `top_k` of them
        away. Measured at 10k rows (2026-10-25): 51 ms to read + **88 ms** to
        marshal = 139 of the 166 ms a warm turn cost, while the vector math it
        was assumed to be dominated by (`matrix @ qv`) took 1.2 ms.

        This returns only the columns the score actually reads (plus `key` /
        `value` for the TF-IDF index). Callers rank on these, then fetch full
        records for the handful that survive -- see `memories_by_ids`.

        The ORDER BY is not needed for the final ordering (callers sort by
        score) but it keeps iteration order identical to the previous
        implementation, so stable sorting breaks ties the same way.
        """
        statuses = ("active", "done") if include_done else ("active",)
        placeholders = ",".join("?" for _ in statuses)
        rows = self._query(
            f"SELECT {RANKING_COLUMNS} FROM memories WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, id DESC", statuses)
        return [RankingRow(*row) for row in rows]

    def memories_by_ids(self, ids: Iterable[int]) -> dict[int, Memory]:
        """Full records for a deliberately small, caller-chosen id set."""
        wanted = [int(i) for i in ids]
        if not wanted:
            return {}
        marks = ",".join("?" for _ in wanted)
        rows = self._query(f"SELECT * FROM memories WHERE id IN ({marks})", wanted)
        return {row["id"]: Memory(**dict(row)) for row in rows}

    def delete(self, memory_id: int) -> bool:
        """Hard delete: the row itself, plus the uniqueness slot it occupied."""
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes = ?", (memory_id,))
            cur = db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._scrub_deleted_indexes()
        return cur.rowcount > 0

    def delete_all(self, *, kind: str | None = None, subject: str | None = None) -> int:
        sql = "DELETE FROM memories WHERE 1 = 1"
        params: list[Any] = []
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if subject:
            sql += " AND subject = ?"
            params.append(subject)
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes IN (" +
                       sql.replace("DELETE FROM", "SELECT id FROM", 1) + ")", params)
            cur = db.execute(sql, params)
        self._scrub_deleted_indexes()
        return cur.rowcount

    def delete_key(self, key: str, *, kind: str | None = None, subject: str | None = None,
                   identity_slot: str | None = None) -> int:
        sql = "DELETE FROM memories WHERE key = ?"
        params: list[Any] = [key.strip().lower()]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if subject is not None:
            sql += " AND subject = ?"
            params.append(subject)
        if identity_slot is not None:
            sql += " AND identity_slot = ?"
            params.append(identity_slot)
        with self._transaction(track_write=True, invalidate=True) as db:
            db.execute("UPDATE memories SET supersedes = NULL WHERE supersedes IN (" +
                       sql.replace("DELETE FROM", "SELECT id FROM", 1) + ")", params)
            cur = db.execute(sql, params)
        self._scrub_deleted_indexes()
        return cur.rowcount

    def _scrub_deleted_indexes(self) -> None:
        """Hard deletion leaves no id behind in audit/lesson provenance.

        Usage rows and lesson evidence contain ids, never memory text, but an
        id that can still be joined back is still an index. After any delete we
        drop refs to rows that no longer exist; the surrounding turn audit stays.
        """
        with self._transaction(track_write=True) as db:
            known = {int(row[0]) for row in db.execute("SELECT id FROM memories")}
            for row in db.execute("SELECT id, memory_ids, reward_detail FROM memory_usage"):
                old_ids = _json_list(row["memory_ids"])
                memory_ids = [i for i in old_ids if type(i) is int and i in known]
                deleted = {i for i in old_ids if i not in known}
                old_detail = _json_list(row["reward_detail"])
                detail = [item for item in old_detail
                          if not (isinstance(item, dict) and item.get("id") in deleted)]
                if memory_ids != old_ids:
                    db.execute("UPDATE memory_usage SET memory_ids = ? WHERE id = ?",
                               (json.dumps(memory_ids, separators=(",", ":")), row["id"]))
                if len(detail) != len(old_detail):
                    db.execute("UPDATE memory_usage SET reward_detail = ? WHERE id = ?",
                               (json.dumps(detail, ensure_ascii=False, separators=(",", ":")),
                                row["id"]))
            known_refs = {f"memory:{i}" for i in known}
            for row in db.execute("SELECT id, evidence_refs FROM lessons"):
                refs = _json_list(row["evidence_refs"])
                kept = [ref for ref in refs
                        if not (isinstance(ref, str) and ref.startswith("memory:")
                                and ref not in known_refs)]
                if kept != refs:
                    db.execute("UPDATE lessons SET evidence_refs = ? WHERE id = ?",
                               (json.dumps(kept, ensure_ascii=False, separators=(",", ":")),
                                row["id"]))

    def mark_used(self, ids: list[int], *, session_id: str | None = None,
                  lesson_ids: list[int] | None = None) -> None:
        """Record injection for outcome learning, not just a counter.

        ``session_id`` binds this turn's memories to the saved user message so
        the *next* user message can supply a reward (MemRL's runtime signal).
        Without it the call keeps the old behaviour: counters only.
        """
        memory_ids = [i for i in (ids or []) if type(i) is int and i > 0]
        lesson_ids = [i for i in (lesson_ids or []) if type(i) is int and i > 0]
        if not memory_ids and not lesson_ids:
            return
        now = time.time()
        with self._transaction(track_write=True) as db:
            if memory_ids:
                db.executemany(
                    "UPDATE memories SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                    [(now, i) for i in memory_ids],
                )
            if lesson_ids:
                db.executemany(
                    "UPDATE lessons SET last_used_at = ?, use_count = use_count + 1 WHERE id = ?",
                    [(now, i) for i in lesson_ids],
                )
        if session_id and (memory_ids or lesson_ids):
            self.record_usage(session_id=session_id, memory_ids=memory_ids,
                              lesson_ids=lesson_ids, now=now)

    # ---------- memory utility / outcome feedback ----------
    @staticmethod
    def _merge_ids(existing: Any, extra: Any) -> list[int]:
        merged: list[int] = []
        for value in list(existing or []) + list(extra or []):
            if type(value) is int and value > 0 and value not in merged:
                merged.append(value)
        return merged

    def latest_user_message_id(self, session_id: str) -> int | None:
        rows = self._query(
            "SELECT MAX(id) FROM messages WHERE session_id = ? AND role = 'user'", (session_id,))
        return int(rows[0][0]) if rows and rows[0][0] is not None else None

    def next_user_message(self, session_id: str, after_message_id: int) -> dict[str, Any] | None:
        rows = self._query(
            """SELECT id, session_id, role, text, mode, created_at FROM messages
               WHERE session_id = ? AND role = 'user' AND id > ?
               ORDER BY id ASC LIMIT 1""", (session_id, int(after_message_id)))
        return dict(rows[0]) if rows else None

    def record_usage(self, *, session_id: str, memory_ids: list[int],
                     lesson_ids: list[int] | None = None, now: float | None = None) -> int | None:
        """Bind injected ids to the session's current user message (idempotent)."""
        message_id = self.latest_user_message_id(session_id)
        if message_id is None:
            return None
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            row = db.execute(
                "SELECT id, memory_ids, lesson_ids FROM memory_usage WHERE session_id = ? AND user_message_id = ?",
                (session_id, message_id)).fetchone()
            if row is None:
                cursor = db.execute(
                    """INSERT INTO memory_usage(session_id, user_message_id, memory_ids,
                                                lesson_ids, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (session_id, message_id,
                     json.dumps(self._merge_ids([], memory_ids), separators=(",", ":")),
                     json.dumps(self._merge_ids([], lesson_ids), separators=(",", ":")),
                     stamp))
                return int(cursor.lastrowid)
            merged_memory = self._merge_ids(_json_list(row["memory_ids"]), memory_ids)
            merged_lesson = self._merge_ids(_json_list(row["lesson_ids"]), lesson_ids)
            db.execute(
                """UPDATE memory_usage SET memory_ids = ?, lesson_ids = ? WHERE id = ?""",
                (json.dumps(merged_memory, separators=(",", ":")),
                 json.dumps(merged_lesson, separators=(",", ":")), row["id"]))
            return int(row["id"])

    def pending_usage(self, *, session_id: str, before_message_id: int) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT * FROM memory_usage WHERE session_id = ? AND user_message_id < ?
               AND settled_at IS NULL ORDER BY user_message_id ASC""",
            (session_id, int(before_message_id)))
        records = []
        for row in rows:
            record = dict(row)
            record["memory_ids"] = _json_list(record.get("memory_ids"))
            record["lesson_ids"] = _json_list(record.get("lesson_ids"))
            records.append(record)
        return records

    def settle_usage(self, usage_id: int, *, reward: float | None, reason: str,
                     detail: list[dict[str, Any]] | None = None,
                     now: float | None = None) -> bool:
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """UPDATE memory_usage SET settled_at = ?, reward = ?, reward_reason = ?,
                   reward_detail = ? WHERE id = ? AND settled_at IS NULL""",
                (stamp, reward, str(reason or "")[:80],
                 json.dumps(detail or [], ensure_ascii=False, separators=(",", ":")),
                 int(usage_id)))
        return cursor.rowcount > 0

    def reinforce_memory(self, memory_id: int, *, reward: float, alpha: float,
                         now: float | None = None) -> float | None:
        """EMA/Monte-Carlo update of Q: Q <- Q + alpha * (r - Q)."""
        try:
            r = min(1.0, max(0.0, float(reward)))
            a = min(1.0, max(0.0, float(alpha)))
        except (TypeError, ValueError):
            return None
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """UPDATE memories SET utility = MIN(1.0, MAX(0.0, utility + ? * (? - utility))),
                   utility_n = utility_n + 1, last_reward_at = ? WHERE id = ?""",
                (a, r, stamp, int(memory_id)))
            if cursor.rowcount <= 0:
                return None
            row = db.execute("SELECT utility FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
        return float(row[0]) if row is not None else None

    def get_many(self, ids: list[int]) -> list[Memory]:
        wanted = [i for i in (ids or []) if type(i) is int and i > 0]
        if not wanted:
            return []
        placeholders = ",".join("?" for _ in wanted)
        rows = self._query(f"SELECT * FROM memories WHERE id IN ({placeholders})", wanted)
        return [Memory(**dict(row)) for row in rows]

    # ---------- procedural lessons ----------
    def add_lesson(self, *, trigger: str, action: str, cues: list[str] | None = None,
                   scope: str = "dialogue", source: str = "reflection",
                   evidence_refs: list[str] | None = None,
                   status: str = "active", now: float | None = None) -> int | None:
        if status not in LESSON_STATUSES:
            raise ValueError("invalid lesson status")
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """INSERT INTO lessons(trigger, action, cues, scope, source, evidence_refs,
                                       status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (trigger, action,
                 json.dumps([cue for cue in (cues or [])], ensure_ascii=False, separators=(",", ":")),
                 scope, source,
                 json.dumps([ref for ref in (evidence_refs or [])], ensure_ascii=False,
                            separators=(",", ":")),
                 status, stamp, stamp))
            return int(cursor.lastrowid)

    def get_lesson(self, lesson_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM lessons WHERE id = ?", (int(lesson_id),))
        return self._lesson_row(rows[0]) if rows else None

    def list_lessons(self, *, status: str | None = "active", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM lessons"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [self._lesson_row(row) for row in self._query(sql, params)]

    @staticmethod
    def _lesson_row(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        record["cues"] = [cue for cue in _json_list(record.get("cues")) if isinstance(cue, str)]
        record["evidence_refs"] = [ref for ref in _json_list(record.get("evidence_refs"))
                                   if isinstance(ref, str)]
        return record

    def merge_lesson_evidence(self, lesson_id: int, evidence_refs: list[str],
                              *, now: float | None = None) -> bool:
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            row = db.execute("SELECT evidence_refs FROM lessons WHERE id = ?", (int(lesson_id),)).fetchone()
            if row is None:
                return False
            merged = list(dict.fromkeys(_json_list(row[0]) + list(evidence_refs or [])))
            db.execute("UPDATE lessons SET evidence_refs = ?, updated_at = ? WHERE id = ?",
                       (json.dumps(merged, ensure_ascii=False, separators=(",", ":")), stamp,
                        int(lesson_id)))
        return True

    def feedback_lesson(self, lesson_id: int, *, helpful: bool,
                        now: float | None = None) -> dict[str, Any] | None:
        stamp = time.time() if now is None else float(now)
        column = "helpful" if helpful else "harmful"
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                f"UPDATE lessons SET {column} = {column} + 1, updated_at = ? WHERE id = ?",
                (stamp, int(lesson_id)))
            if cursor.rowcount <= 0:
                return None
        return self.get_lesson(lesson_id)

    def set_lesson_status(self, lesson_id: int, status: str,
                          *, now: float | None = None) -> bool:
        if status not in LESSON_STATUSES:
            raise ValueError("invalid lesson status")
        stamp = time.time() if now is None else float(now)
        with self._transaction(track_write=True) as db:
            cursor = db.execute("UPDATE lessons SET status = ?, updated_at = ? WHERE id = ?",
                                (status, stamp, int(lesson_id)))
        return cursor.rowcount > 0

    def count_active_lessons(self) -> int:
        return int(self._query("SELECT COUNT(*) FROM lessons WHERE status = 'active'")[0][0])

    def set_status(self, memory_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"bad status: {status}")
        self._write("UPDATE memories SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), memory_id))

    def set_disabled(self, memory_id: int, disabled: bool) -> bool:
        """Soft mute for retrieval assembly. Reversible, never a delete.

        ``updated_at`` is deliberately untouched: it marks the content
        lifecycle and feeds recency decay, and a retrieval switch is neither.
        Toggling it must not make an old memory look fresh again.
        """
        if not isinstance(disabled, bool):
            raise ValueError("disabled must be a boolean")
        with self._transaction(track_write=True) as db:
            cursor = db.execute("UPDATE memories SET disabled = ? WHERE id = ?",
                                (1 if disabled else 0, int(memory_id)))
        return cursor.rowcount > 0

    def link_supersedes(self, new_id: int, old_id: int) -> None:
        """New value wins, old row stays queryable via history_for_key()."""
        self._write("UPDATE memories SET supersedes = ? WHERE id = ?", (old_id, new_id))

    # ---------- diary / drift / spark / self_state ----------
    # Protocol sections 10 and 12. Policy (mood vocabulary, quote rules, caps on
    # what one diary may derive) lives in memory/diary.py; this layer only
    # guards argument shape and keeps writes atomic.
    def add_diary(self, *, date: str, title: str, body: str, mood: str, energy: float,
                  topic_keys: list[str], source_message_ids: list[int]) -> int | None:
        """One diary per local date; a second synthesis for the same day is a no-op."""
        if not isinstance(date, str) or not date.strip():
            raise ValueError("diary date required")
        for field in (title, body, mood):
            if not isinstance(field, str) or not field.strip():
                raise ValueError("diary text fields required")
        if (not isinstance(energy, (int, float)) or isinstance(energy, bool)
                or not math.isfinite(float(energy)) or not 0 <= float(energy) <= 1):
            raise ValueError("diary energy must be within [0, 1]")
        if (not isinstance(topic_keys, list)
                or not all(isinstance(key, str) and key.strip() for key in topic_keys)
                or not isinstance(source_message_ids, list) or not source_message_ids
                or not all(type(i) is int and i > 0 for i in source_message_ids)):
            raise ValueError("diary topic keys and source message ids required")
        now = time.time()
        with self._transaction(track_write=True) as db:
            if db.execute("SELECT 1 FROM diaries WHERE date = ?", (date,)).fetchone() is not None:
                return None
            cursor = db.execute(
                """INSERT INTO diaries (date, title, body, mood, energy, topic_keys,
                                        source_message_ids, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (date, title, body, mood, float(energy),
                 json.dumps(topic_keys, ensure_ascii=False, separators=(",", ":")),
                 json.dumps(source_message_ids, ensure_ascii=False, separators=(",", ":")), now))
            return int(cursor.lastrowid)

    def get_diary(self, diary_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM diaries WHERE id = ?", (diary_id,))
        return _diary_row(rows[0]) if rows else None

    def diary_for_date(self, date: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM diaries WHERE date = ?", (date,))
        return _diary_row(rows[0]) if rows else None

    def list_diaries(self, *, since_date: str | None = None, until_date: str | None = None,
                     limit: int = 90) -> list[dict[str, Any]]:
        sql = "SELECT * FROM diaries WHERE 1 = 1"
        params: list[Any] = []
        if since_date is not None:
            sql += " AND date >= ?"
            params.append(since_date)
        if until_date is not None:
            sql += " AND date <= ?"
            params.append(until_date)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_diary_row(row) for row in self._query(sql, params)]

    def messages_between(self, start_ts: float, end_ts: float) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT id, session_id, role, text, mode, created_at FROM messages
               WHERE created_at >= ? AND created_at < ? ORDER BY id ASC""", (start_ts, end_ts))
        return [dict(row) for row in rows]

    def upsert_drift(self, *, topic: str, attitude: str, weight: float,
                     evidence_diary_id: int) -> int:
        """One row per topic; repeated evidence appends instead of duplicating."""
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("drift topic required")
        if not isinstance(attitude, str) or not attitude.strip():
            raise ValueError("drift attitude required")
        if (not isinstance(weight, (int, float)) or isinstance(weight, bool)
                or not math.isfinite(float(weight)) or not 0 <= float(weight) <= 1):
            raise ValueError("drift weight must be within [0, 1]")
        if type(evidence_diary_id) is not int or evidence_diary_id <= 0:
            raise ValueError("drift evidence diary id required")
        now = time.time()
        with self._transaction(track_write=True) as db:
            row = db.execute("SELECT * FROM drifts WHERE topic = ?", (topic.strip(),)).fetchone()
            if row is None:
                cursor = db.execute(
                    """INSERT INTO drifts (topic, attitude, weight, evidence_diary_ids,
                                          created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (topic.strip(), attitude.strip(), float(weight),
                     json.dumps([evidence_diary_id], ensure_ascii=False), now, now))
                return int(cursor.lastrowid)
            evidence = _json_list(row["evidence_diary_ids"])
            if evidence_diary_id not in evidence:
                evidence.append(evidence_diary_id)
            db.execute(
                """UPDATE drifts SET attitude = ?, weight = ?, evidence_diary_ids = ?,
                   updated_at = ? WHERE id = ?""",
                (attitude.strip(), float(weight),
                 json.dumps(evidence, ensure_ascii=False, separators=(",", ":")), now, row["id"]))
            return int(row["id"])

    def list_drifts(self) -> list[dict[str, Any]]:
        return [_drift_row(row) for row in
                self._query("SELECT * FROM drifts ORDER BY weight DESC, id ASC")]

    def add_spark(self, *, text: str, heat: float, born_diary_id: int,
                  state: str = "latent") -> int | None:
        """New sparks are born latent; the latent cap is enforced here atomically."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("spark text required")
        if (not isinstance(heat, (int, float)) or isinstance(heat, bool)
                or not math.isfinite(float(heat))):
            raise ValueError("spark heat must be finite")
        if type(born_diary_id) is not int or born_diary_id <= 0:
            raise ValueError("spark must name the diary it was born from")
        if state not in SPARK_STATES:
            raise ValueError("invalid spark state")
        now = time.time()
        with self._transaction(track_write=True) as db:
            if state == "latent" and int(db.execute(
                    "SELECT COUNT(*) FROM sparks WHERE state = 'latent'").fetchone()[0]) >= MAX_ACTIVE_LATENT_SPARKS:
                return None
            cursor = db.execute(
                """INSERT INTO sparks (text, state, heat, born_diary_id, surfaced_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (text.strip(), state, min(1.0, max(0.0, float(heat))), born_diary_id,
                 now if state == "surfaced" else None, now))
            return int(cursor.lastrowid)

    def get_spark(self, spark_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM sparks WHERE id = ?", (spark_id,))
        return dict(rows[0]) if rows else None

    def list_sparks(self, *, states: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sparks"
        params: list[Any] = []
        if states:
            for state in states:
                if state not in SPARK_STATES:
                    raise ValueError("invalid spark state")
            sql += " WHERE state IN (" + ",".join("?" for _ in states) + ")"
            params.extend(states)
        sql += " ORDER BY created_at ASC, id ASC"
        return [dict(row) for row in self._query(sql, params)]

    def count_sparks(self, state: str) -> int:
        if state not in SPARK_STATES:
            raise ValueError("invalid spark state")
        return int(self._query("SELECT COUNT(*) FROM sparks WHERE state = ?", (state,))[0][0])

    def update_spark_state(self, spark_id: int, state: str,
                           *, surfaced_at: float | None = None) -> bool:
        """Forward-only machine: latent -> surfaced -> spent. Anything else is a no-op."""
        if state not in SPARK_STATES:
            raise ValueError("invalid spark state")
        now = time.time()
        with self._transaction(track_write=True) as db:
            row = db.execute("SELECT state FROM sparks WHERE id = ?", (spark_id,)).fetchone()
            if row is None or (row["state"], state) not in SPARK_TRANSITIONS:
                return False
            if state == "surfaced":
                db.execute(
                    "UPDATE sparks SET state = 'surfaced', surfaced_at = COALESCE(?, ?) WHERE id = ?",
                    (surfaced_at, now, spark_id))
            else:
                db.execute("UPDATE sparks SET state = ? WHERE id = ?", (state, spark_id))
            return True

    def get_self_state(self) -> dict[str, Any]:
        rows = self._query("SELECT * FROM self_state WHERE id = 1")
        if not rows:
            return {"taste": [], "worldview": [], "curiosity_seeds": [],
                    "last_cycle_at": None, "cycles_today": 0, "cycles_on": None}
        record = dict(rows[0])
        return {
            "taste": _json_list(record.get("taste")),
            "worldview": _json_list(record.get("worldview")),
            "curiosity_seeds": _json_list(record.get("curiosity_seeds")),
            "last_cycle_at": record.get("last_cycle_at"),
            "cycles_today": int(record.get("cycles_today") or 0),
            "cycles_on": record.get("cycles_on"),
        }

    def save_self_state(self, *, taste: list[dict[str, Any]], worldview: list[dict[str, Any]],
                        curiosity_seeds: list[dict[str, Any]], last_cycle_at: float | None = None,
                        cycles_today: int | None = None, cycles_on: str | None = None) -> None:
        for entries in (taste, worldview):
            if not isinstance(entries, list) or not all(
                    isinstance(entry, dict) and isinstance(entry.get("topic"), str) and entry["topic"].strip()
                    and isinstance(entry.get("stance"), str) and entry["stance"].strip()
                    and isinstance(entry.get("weight"), (int, float)) and not isinstance(entry.get("weight"), bool)
                    for entry in entries):
                raise ValueError("invalid taste or worldview entries")
        if not isinstance(curiosity_seeds, list) or not all(
                isinstance(seed, dict) and isinstance(seed.get("text"), str) and seed["text"].strip()
                and isinstance(seed.get("heat"), (int, float)) and not isinstance(seed.get("heat"), bool)
                for seed in curiosity_seeds):
            raise ValueError("invalid curiosity seeds")
        payload = (
            json.dumps(taste, ensure_ascii=False, separators=(",", ":")),
            json.dumps(worldview, ensure_ascii=False, separators=(",", ":")),
            json.dumps(curiosity_seeds, ensure_ascii=False, separators=(",", ":")),
            last_cycle_at, 0 if cycles_today is None else int(cycles_today), cycles_on,
        )
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """UPDATE self_state SET taste = ?, worldview = ?, curiosity_seeds = ?,
                   last_cycle_at = ?, cycles_today = ?, cycles_on = ? WHERE id = 1""", payload)
            if cursor.rowcount == 0:
                db.execute(
                    """INSERT INTO self_state (id, taste, worldview, curiosity_seeds,
                       last_cycle_at, cycles_today, cycles_on) VALUES (1, ?, ?, ?, ?, ?, ?)""", payload)

    # ---------- seeds / reflections / graph_edges / mood_state (V2 §14-16) ----------
    # The curiosity graph. This layer only guards argument shape and keeps writes
    # atomic; lifecycle, forking and dedup policy live in memory/seeds.py.
    def upsert_seed(self, *, id: str, title: str, question: str, status: str = "active",
                    heat: float = 0.5, cooldown: float = 0.0, parent_seed_ids: list[str] | None = None,
                    child_seed_ids: list[str] | None = None, source_refs: list[str] | None = None,
                    related_refs: list[str] | None = None, reflections: list[str] | None = None,
                    created_at: str, last_reflected_at: float | None = None,
                    version: int = 1) -> None:
        if not isinstance(id, str) or not id.strip():
            raise ValueError("seed id required")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 40:
            raise ValueError("seed title required and <= 40 chars")
        if not isinstance(question, str) or not question.strip() or len(question.strip()) > 200:
            raise ValueError("seed question required and <= 200 chars")
        if status not in SEED_STATES:
            raise ValueError("invalid seed status")
        for name, value in (("heat", heat), ("cooldown", cooldown)):
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(float(value)) or not 0 <= float(value) <= 1):
                raise ValueError(f"seed {name} must be within [0, 1]")
        for name, value in (("parent_seed_ids", parent_seed_ids), ("child_seed_ids", child_seed_ids),
                            ("source_refs", source_refs), ("related_refs", related_refs),
                            ("reflections", reflections)):
            if value is not None and (not isinstance(value, list)
                    or not all(isinstance(item, str) for item in value)):
                raise ValueError(f"seed {name} must be a list of strings")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ValueError("seed created_at required")
        now = time.time()
        payload = (title.strip(), question.strip(), status, float(heat), float(cooldown),
                   json.dumps(parent_seed_ids or [], ensure_ascii=False, separators=(",", ":")),
                   json.dumps(child_seed_ids or [], ensure_ascii=False, separators=(",", ":")),
                   json.dumps(source_refs or [], ensure_ascii=False, separators=(",", ":")),
                   json.dumps(related_refs or [], ensure_ascii=False, separators=(",", ":")),
                   json.dumps(reflections or [], ensure_ascii=False, separators=(",", ":")),
                   created_at.strip(), last_reflected_at, int(version), now, id.strip())
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """UPDATE seeds SET title = ?, question = ?, status = ?, heat = ?, cooldown = ?,
                   parent_seed_ids = ?, child_seed_ids = ?, source_refs = ?, related_refs = ?,
                   reflections = ?, created_at = ?, last_reflected_at = ?, version = ?, updated_at = ?
                   WHERE id = ?""", payload)
            if cursor.rowcount == 0:
                db.execute(
                    """INSERT INTO seeds (title, question, status, heat, cooldown, parent_seed_ids,
                       child_seed_ids, source_refs, related_refs, reflections, created_at,
                       last_reflected_at, version, updated_at, id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    payload)

    def get_seed(self, seed_id: str) -> dict[str, Any] | None:
        if not isinstance(seed_id, str) or not seed_id.strip():
            return None
        rows = self._query("SELECT * FROM seeds WHERE id = ?", (seed_id.strip(),))
        return _seed_row(rows[0]) if rows else None

    def list_seeds(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM seeds"
        params: list[Any] = []
        if status is not None:
            if status not in SEED_STATES:
                raise ValueError("invalid seed status filter")
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY heat DESC, updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_seed_row(row) for row in self._query(sql, params)]

    def count_seeds(self) -> int:
        return int(self._query("SELECT COUNT(*) FROM seeds")[0][0])

    def add_reflection(self, *, id: str, seed_id: str, narrative: str, discovery: str,
                       source_refs: list[str] | None = None, child_seed_ids: list[str] | None = None,
                       status_changes: list[dict[str, Any]] | None = None,
                       epiphany_edges: list[dict[str, Any]] | None = None,
                       processed: bool = False) -> None:
        if not isinstance(id, str) or not id.strip():
            raise ValueError("reflection id required")
        if not isinstance(seed_id, str) or not seed_id.strip():
            raise ValueError("reflection seed_id required")
        if not isinstance(narrative, str) or not narrative.strip():
            raise ValueError("reflection narrative required")
        if not isinstance(discovery, str) or not discovery.strip():
            raise ValueError("reflection discovery required")
        for name, value in (("source_refs", source_refs), ("child_seed_ids", child_seed_ids)):
            if value is not None and (not isinstance(value, list)
                    or not all(isinstance(item, str) for item in value)):
                raise ValueError(f"reflection {name} must be a list of strings")
        for name, value in (("status_changes", status_changes), ("epiphany_edges", epiphany_edges)):
            if value is not None and not isinstance(value, list):
                raise ValueError(f"reflection {name} must be a list")
        now = time.time()
        with self._transaction(track_write=True) as db:
            db.execute(
                """INSERT OR REPLACE INTO reflections (id, seed_id, narrative, discovery, source_refs,
                   child_seed_ids, status_changes, epiphany_edges, processed, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id.strip(), seed_id.strip(), narrative, discovery,
                 json.dumps(source_refs or [], ensure_ascii=False, separators=(",", ":")),
                 json.dumps(child_seed_ids or [], ensure_ascii=False, separators=(",", ":")),
                 json.dumps(status_changes or [], ensure_ascii=False, separators=(",", ":")),
                 json.dumps(epiphany_edges or [], ensure_ascii=False, separators=(",", ":")),
                 1 if processed else 0, now))

    def get_reflection(self, reflection_id: str) -> dict[str, Any] | None:
        if not isinstance(reflection_id, str) or not reflection_id.strip():
            return None
        rows = self._query("SELECT * FROM reflections WHERE id = ?", (reflection_id.strip(),))
        return _reflection_row(rows[0]) if rows else None

    def list_reflections(self, *, seed_id: str | None = None,
                         processed: bool | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reflections WHERE 1 = 1"
        params: list[Any] = []
        if seed_id is not None:
            sql += " AND seed_id = ?"
            params.append(seed_id)
        if processed is not None:
            sql += " AND processed = ?"
            params.append(1 if processed else 0)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_reflection_row(row) for row in self._query(sql, params)]

    def set_reflection_processed(self, reflection_id: str, *, processed: bool = True) -> bool:
        if not isinstance(reflection_id, str) or not reflection_id.strip():
            return False
        with self._transaction(track_write=True) as db:
            cursor = db.execute("UPDATE reflections SET processed = ? WHERE id = ?",
                                (1 if processed else 0, reflection_id.strip()))
            return cursor.rowcount > 0

    def add_edge(self, *, source: str, target: str, kind: str, weight: float = 0.5,
                 reason: str = "") -> bool:
        """Persist a semantic edge; a duplicate (source, target, kind) is a no-op."""
        if not isinstance(source, str) or not source.strip() or not isinstance(target, str) or not target.strip():
            raise ValueError("edge source and target required")
        if source == target:
            return False
        if kind not in PERSISTED_EDGE_KINDS:
            raise ValueError("invalid persisted edge kind")
        if (not isinstance(weight, (int, float)) or isinstance(weight, bool)
                or not math.isfinite(float(weight)) or not 0 <= float(weight) <= 1):
            raise ValueError("edge weight must be within [0, 1]")
        if not isinstance(reason, str):
            raise ValueError("edge reason must be a string")
        now = time.time()
        with self._transaction(track_write=True) as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO graph_edges (source, target, kind, weight, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (source.strip(), target.strip(), kind, float(weight), reason.strip()[:200], now))
            return cursor.rowcount > 0

    def list_edges(self, *, kind: str | None = None, limit: int = 2000) -> list[dict[str, Any]]:
        sql = "SELECT * FROM graph_edges"
        params: list[Any] = []
        if kind is not None:
            if kind not in PERSISTED_EDGE_KINDS:
                raise ValueError("invalid edge kind filter")
            sql += " WHERE kind = ?"
            params.append(kind)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        return [_edge_row(row) for row in self._query(sql, params)]

    def get_mood(self) -> dict[str, Any]:
        rows = self._query("SELECT * FROM mood_state WHERE id = 1")
        return _mood_row(rows[0]) if rows else {"narrative": "", "state": "active", "source_refs": []}

    def save_mood(self, *, narrative: str, reason: str = "", source_refs: list[str] | None = None,
                  influence_note: str = "", related_summary: str = "", state: str = "active",
                  expires_at: float | None = None) -> None:
        if not isinstance(narrative, str):
            raise ValueError("mood narrative must be a string")
        if state not in MOOD_STATES:
            raise ValueError("invalid mood state")
        if source_refs is not None and (not isinstance(source_refs, list)
                or not all(isinstance(item, str) for item in source_refs)):
            raise ValueError("mood source_refs must be a list of strings")
        now = time.time()
        with self._transaction(track_write=True) as db:
            db.execute(
                """UPDATE mood_state SET narrative = ?, reason = ?, source_refs = ?, influence_note = ?,
                   related_summary = ?, state = ?, created_at = ?, expires_at = ? WHERE id = 1""",
                (narrative, reason,
                 json.dumps(source_refs or [], ensure_ascii=False, separators=(",", ":")),
                 influence_note, related_summary, state, now, expires_at))

    # ---------- sessions / messages ----------
    def start_session(self, session_id: str | None = None) -> str:
        sid = session_id or uuid.uuid4().hex[:12]
        self._write("INSERT OR REPLACE INTO sessions (id, started_at) VALUES (?, ?)", (sid, time.time()))
        return sid

    def end_session(self, session_id: str, summary: str | None = None) -> None:
        self._write(
            "UPDATE sessions SET ended_at = ?, summary = COALESCE(?, summary) WHERE id = ?",
            (time.time(), summary, session_id),
        )

    def has_session(self, session_id: str) -> bool:
        """断线重连续接（#34）的前置检查：这个会话 id 是否真实存在过。"""
        return bool(self._query("SELECT id FROM sessions WHERE id = ?", (session_id,)))

    def resume_session(self, session_id: str) -> None:
        """续接已结束的会话（#34）：只清 ended 标记，不动 started_at 与 summary——
        会话的开始时间与首段小结属于它的历史，重连不是重启。"""
        self._write("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))

    def add_message(self, session_id: str, role: str, text: str, mode: str = "text") -> int:
        cursor = self._write(
            "INSERT INTO messages (session_id, role, text, mode, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, text, mode, time.time()),
        )
        return int(cursor.lastrowid)

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT id, session_id, role, text, mode, created_at FROM messages WHERE id = ?", (message_id,))
        return dict(rows[0]) if rows else None

    def recent_message_records(self, session_id: str, limit: int = 12,
                               before_id: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, session_id, role, text, mode, created_at FROM messages WHERE session_id = ?"
        params: list[Any] = [session_id]
        if before_id is not None:
            sql += " AND id <= ?"
            params.append(before_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in reversed(self._query(sql, params))]

    def recent_messages(self, session_id: str, limit: int = 12) -> list[dict[str, str]]:
        rows = self._query(
            "SELECT role, text, mode FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        )
        return [dict(r) for r in reversed(rows)]

    def count(self, status: str = "active") -> int:
        rows = self._query("SELECT COUNT(*) AS n FROM memories WHERE status = ?", (status,))
        return int(rows[0]["n"]) if rows else 0

    def recent_turns_with_questions(self, session_id: str, limit: int = 6) -> list[dict[str, str]]:
        return self.recent_messages(session_id, limit=limit)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True


# 生产库哨兵（#39）落在 MemoryStore/__init__ 的 guard_sqlite_path 与
# CommissionStore 的同类检查上：测试态下打开仓库 data/ 下的库即报错。
# 模块级单例照常创建（生产路径零变化）——测试只要钉了临时库就不受影响，
# 没钉的会在真正打开库时被拦。
store = MemoryStore()
