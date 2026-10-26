"""Offline memory persistence contracts; synthetic databases only.

Run: python -B scripts/test_memory_store_contract.py
Load the real store with a stub config, so even import cannot open the user's
database or read environment credentials. Migration tests use temporary copies.
"""
from __future__ import annotations

import hashlib
import importlib.util
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _guard_sqlite_path(path: str) -> None:
    """桩件版的生产库哨兵（#39）。

    server/memory/store.py 从 ..config 同时 import 了 settings 与
    guard_sqlite_path；本文件把 config 整个换成桩件，所以两个都得给——
    少给一个就是 ImportError（CI 上红过一次）。

    这里比真实现**更严**：真的那个看 TEST_SENTINEL 环境变量，本文件不看，
    仓库 data/ 下的路径一律拒绝。理由是这份测试全程只用 :memory: 与
    tempfile，真指到 data/ 就是回归，条件放行不如直接抛。
    """
    if not path or path == ":memory:":
        return
    try:
        target = Path(path).resolve()
    except (OSError, ValueError):
        return
    if (ROOT / "data").resolve() in target.parents:
        raise RuntimeError(
            f"本测试的数据库路径指向真实数据目录：{target}")


CONFIG = ModuleType("memory_store_contract.config")
CONFIG.settings = SimpleNamespace(db_path=":memory:")
CONFIG.guard_sqlite_path = _guard_sqlite_path
sys.modules[CONFIG.__name__] = CONFIG
SPEC = importlib.util.spec_from_file_location(
    "memory_store_contract.memory.store", ROOT / "server/memory/store.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
Memory, MemoryStore = MODULE.Memory, MODULE.MemoryStore

LEGACY_SCHEMA = """
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'user', key TEXT NOT NULL, value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'stated', status TEXT NOT NULL DEFAULT 'active',
    supersedes INTEGER, importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.8, session_id TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    last_used_at REAL, use_count INTEGER NOT NULL DEFAULT 0);
CREATE UNIQUE INDEX idx_mem_key_active ON memories(key) WHERE status='active';
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL,
    turns INTEGER NOT NULL DEFAULT 0, summary TEXT);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'text',
    created_at REAL NOT NULL);
"""


def legacy_database(path, version):
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(LEGACY_SCHEMA)
        db.execute("INSERT INTO memories VALUES (17,'fact','user','work:company',"
                   "'在甲公司工作','stated','active',NULL,0.7,0.95,'old-session',101,102,103,4)")
        db.execute("INSERT INTO memories VALUES (9,'fact','user','work:company',"
                   "'旧公司','stated','superseded',NULL,0.6,0.9,'old-session',91,92,NULL,0)")
        db.execute("INSERT INTO sessions VALUES ('old-session',90,110,2,'旧会话摘要')")
        db.execute("INSERT INTO messages VALUES (43,'old-session','user','在甲公司工作','text',100)")
        db.execute(f"PRAGMA user_version = {version}")


class MemoryStoreContracts(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")
        self.addCleanup(self.store.close)

    def add(self, value="在甲公司工作", **fields):
        return self.store.upsert(kind=fields.pop("kind", "fact"),
                                 subject=fields.pop("subject", "user"),
                                 key=fields.pop("key", "work:company"), value=value, **fields)

    def linked(self, text, *, source="stated", value=None, session_id="s", **fields):
        message_id = self.store.add_message(session_id, "user", text)
        memory_id = self.add(value or text, source=source, session_id=session_id,
                             evidence_state="linked", evidence_message_id=message_id,
                             evidence_quote=text, **fields)
        return memory_id, message_id

    def test_v0_and_v1_copies_migrate_without_changing_originals(self):
        for version in (0, 1):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as folder:
                original = Path(folder) / "original.db"
                copied = Path(folder) / "migration-copy.db"
                legacy_database(original, version)
                digest = hashlib.sha256(original.read_bytes()).hexdigest()
                shutil.copyfile(original, copied)
                migrated = MemoryStore(str(copied))
                try:
                    self.assertEqual(migrated._query("PRAGMA user_version")[0][0], 4)
                    rows = migrated.list_memories(status=None, limit=20)
                    self.assertEqual({memory.id for memory in rows}, {9, 17})
                    current = migrated.get(17)
                    self.assertEqual((current.value, current.created_at, current.updated_at,
                                      current.last_used_at, current.use_count),
                                     ("在甲公司工作", 101, 102, 103, 4))
                    self.assertEqual((current.evidence_message_id, current.evidence_quote,
                                      current.evidence_state, current.decision),
                                     (None, "", "legacy", "legacy"))
                    self.assertEqual(migrated.get(9).status, "superseded")
                    self.assertEqual(migrated.get_message(43),
                                     {"id": 43, "session_id": "old-session", "role": "user",
                                      "text": "在甲公司工作", "mode": "text", "created_at": 100})
                    self.assertEqual(dict(migrated._query("SELECT * FROM sessions")[0]),
                                     {"id": "old-session", "started_at": 90, "ended_at": 110,
                                      "turns": 2, "summary": "旧会话摘要"})
                    self.assertEqual(migrated.write_state(), (0, 0))
                    self.assertGreater(migrated.add_message("new", "user", "new message"), 43)
                    self.assertEqual([row["name"] for row in migrated._query(
                        "PRAGMA index_info(idx_mem_identity_active)")], ["kind", "subject", "key", "identity_slot"])
                    self.assertFalse(any(row["name"] == "idx_mem_key_active" for row in
                                         migrated._query("PRAGMA index_list(memories)")))
                finally:
                    migrated.close()
                self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), digest)
                with closing(sqlite3.connect(original)) as db, db:
                    self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], version)

    def test_existing_v3_reopens_with_provenance_and_watermark(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "memory.db")
            initial = MemoryStore(path)
            message_id = initial.add_message("s", "user", "在甲公司工作")
            memory_id = initial.upsert(kind="fact", key="work:company", value="在甲公司工作",
                                       session_id="s", evidence_state="linked",
                                       evidence_message_id=message_id, evidence_quote="在甲公司工作")
            initial.invalidate_pending()
            before = initial.get(memory_id).to_dict()
            initial.close()
            reopened = MemoryStore(path)
            try:
                self.assertEqual(reopened.get(memory_id).to_dict(), before)
                self.assertEqual(reopened.write_state(), (1, message_id))
            finally:
                reopened.close()

    def test_future_schema_is_rejected_without_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "future.db"
            legacy_database(path, 5)
            with self.assertRaisesRegex(RuntimeError, "newer"):
                MemoryStore(str(path))
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 5)
                self.assertEqual(db.execute("SELECT value FROM memories WHERE id=17").fetchone()[0],
                                 "在甲公司工作")
                self.assertNotIn("evidence_state", {row[1] for row in db.execute("PRAGMA table_info(memories)")})

    def test_v2_copy_migrates_without_guessing_old_preference_labels(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "v2.db"
            legacy_database(path, 2)
            with closing(sqlite3.connect(path)) as db, db:
                for name, definition in {
                    "evidence_message_id": "INTEGER", "evidence_quote": "TEXT NOT NULL DEFAULT ''",
                    "evidence_state": "TEXT NOT NULL DEFAULT 'legacy'", "decision": "TEXT NOT NULL DEFAULT 'legacy'",
                    "last_observed_message_id": "INTEGER",
                }.items():
                    db.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
                db.execute("DROP INDEX idx_mem_key_active")
                db.execute("CREATE UNIQUE INDEX idx_mem_identity_active ON memories(kind,subject,key) WHERE status='active'")
                db.execute("UPDATE memories SET key='饮食:饮品偏好', value='用户长期最喜欢喝绿茶。',"
                           "evidence_message_id=43,evidence_quote='在甲公司工作',evidence_state='linked',"
                           "decision='add',last_observed_message_id=43 WHERE id=17")
                db.execute("CREATE TABLE memory_control(id INTEGER PRIMARY KEY,write_epoch INTEGER,write_floor INTEGER)")
                db.execute("INSERT INTO memory_control VALUES(1,3,43)")
                before = dict(zip([c[1] for c in db.execute('PRAGMA table_info(memories)')],
                                  db.execute('SELECT * FROM memories WHERE id=17').fetchone()))
            migrated = MemoryStore(str(path))
            try:
                after = migrated.get(17).to_dict()
                self.assertEqual({key: after[key] for key in before}, before)
                self.assertEqual((after['identity_slot'], after['preference_target'], after['preference_level'],
                                  after['preference_scope'], after['preference_as_of']), ('','','','',None))
                self.assertEqual(migrated.write_state(), (3,43))
                self.assertEqual(migrated._query('PRAGMA user_version')[0][0], 4)
            finally:
                migrated.close()

    def test_existing_v2_without_last_observation_adds_only_missing_column(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "early-v2.db"
            legacy_database(path, 2)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("ALTER TABLE memories ADD COLUMN evidence_message_id INTEGER")
                db.execute("ALTER TABLE memories ADD COLUMN evidence_quote TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE memories ADD COLUMN evidence_state TEXT NOT NULL DEFAULT 'legacy'")
                db.execute("ALTER TABLE memories ADD COLUMN decision TEXT NOT NULL DEFAULT 'legacy'")
                db.execute("UPDATE memories SET evidence_message_id=43,evidence_quote='在甲公司工作',"
                           "evidence_state='linked',decision='add' WHERE id=17")
            reopened = MemoryStore(str(path))
            try:
                record = reopened.get(17)
                self.assertEqual((record.evidence_message_id, record.evidence_quote,
                                  record.evidence_state, record.decision, record.last_observed_message_id),
                                 (43, "在甲公司工作", "linked", "add", None))
                older = reopened.add_message("old-session", "user", "新记录")
                self.assertGreater(older, 43)
                self.assertEqual(reopened._query("PRAGMA user_version")[0][0], 4)
            finally:
                reopened.close()

    def test_failed_migration_rolls_back_instead_of_repairing_user_facts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.db"
            legacy_database(path, 1)
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("DROP INDEX idx_mem_key_active")
                db.execute("INSERT INTO memories SELECT 18,kind,subject,key,'冲突旧资料',source,status,"
                           "supersedes,importance,confidence,session_id,created_at,updated_at,last_used_at,"
                           "use_count FROM memories WHERE id=17")
            with self.assertRaises(sqlite3.IntegrityError):
                MemoryStore(str(path))
            with closing(sqlite3.connect(path)) as db, db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 3)
                self.assertNotIn("evidence_quote", {row[1] for row in db.execute("PRAGMA table_info(memories)")})

    def test_old_memory_fixture_fields_still_construct(self):
        fixture = Memory(1, "fact", "user", "work:company", "甲公司", "stated", "active",
                         None, .5, .8, "s", 1, 2, None, 0)
        self.assertEqual((fixture.evidence_message_id, fixture.evidence_state, fixture.decision),
                         (None, "legacy", "legacy"))

    def test_new_manual_record_has_manual_provenance(self):
        record = self.store.get(self.add())
        self.assertEqual((record.evidence_message_id, record.evidence_quote,
                          record.evidence_state, record.decision), (None, "", "manual", "add"))

    def test_identity_separates_kind_and_subject(self):
        user_fact = self.add("用户事实")
        self_fact = self.add("角色事实", subject="self")
        user_episode = self.add("用户经历", kind="episode")
        updated = self.add("用户新事实")
        self.assertEqual(self.store.get(user_fact).status, "superseded")
        self.assertEqual(self.store.get(updated).supersedes, user_fact)
        self.assertEqual(self.store.active_for("fact", "user", "WORK:COMPANY").id, updated)
        self.assertEqual(self.store.active_for("fact", "self", "work:company").id, self_fact)
        self.assertEqual(self.store.active_for("episode", "user", "work:company").id, user_episode)
        self.assertEqual(self.store.count(), 3)

    def test_explicit_conflict_does_not_supersede_active_even_with_supersedes(self):
        original = self.add("甲公司")
        before = self.store.get(original).to_dict()
        conflict = self.add("乙公司", status="conflict", supersedes=original)
        self.assertEqual(self.store.get(original).to_dict(), before)
        self.assertEqual((self.store.get(conflict).status, self.store.get(conflict).decision,
                          self.store.get(conflict).supersedes), ("conflict", "conflict", None))
        self.assertEqual(self.store.count(), 1)

    def test_weak_sources_cannot_replace_stated_active(self):
        original = self.add("明确事实")
        before = self.store.get(original).to_dict()
        for source in ("inferred", "uncertain"):
            with self.subTest(source=source):
                conflict = self.add("不同的推测" + source, source=source, supersedes=original)
                self.assertEqual(self.store.get(conflict).status, "conflict")
                self.assertEqual(self.store.get(original).to_dict(), before)
        self.assertEqual(self.store.active_for("fact", "user", "work:company").id, original)

    def test_same_value_noop_keeps_strong_evidence_and_age(self):
        original, message_id = self.linked("明确事实")
        before = self.store.get(original)
        repeated = self.add("明确事实", source="uncertain", confidence=.2, importance=.1)
        after = self.store.get(repeated)
        self.assertEqual(repeated, original)
        self.assertEqual((after.source, after.evidence_message_id, after.evidence_quote,
                          after.evidence_state, after.updated_at, after.confidence, after.importance),
                         ("stated", message_id, "明确事实", "linked", before.updated_at,
                          before.confidence, before.importance))
        self.assertEqual(after.decision, "noop")

    def test_repeated_inference_does_not_extend_lifetime(self):
        original = self.add("推测事实", source="inferred")
        self.store._write("UPDATE memories SET updated_at=1 WHERE id=?", (original,))
        self.assertEqual(self.add("推测事实", source="inferred"), original)
        self.assertEqual(self.store.get(original).updated_at, 1)

    def test_source_promotion_replaces_inference_evidence_with_new_evidence(self):
        original, old_message = self.linked("也许在甲公司工作", value="在甲公司工作", source="inferred")
        new_message = self.store.add_message("s", "user", "我明确说：在甲公司工作")
        promoted = self.add("在甲公司工作", source="stated", session_id="s", evidence_state="linked",
                            evidence_message_id=new_message, evidence_quote="在甲公司工作")
        record = self.store.get(promoted)
        self.assertEqual(promoted, original)
        self.assertNotEqual(new_message, old_message)
        self.assertEqual((record.source, record.evidence_message_id, record.evidence_quote,
                          record.evidence_state), ("stated", new_message, "在甲公司工作", "linked"))

    def test_unproved_source_promotion_is_noop(self):
        original, message_id = self.linked("也许如此", source="inferred", value="事实")
        self.assertEqual(self.add("事实", source="stated", evidence_state="legacy"), original)
        record = self.store.get(original)
        self.assertEqual((record.source, record.evidence_message_id), ("inferred", message_id))

    def test_cross_session_promotion_updates_evidence_owner_and_source_score_only(self):
        original, old_message = self.linked("也许在甲公司工作", value="在甲公司工作", source="inferred",
                                            session_id="session-A", confidence=.6, importance=.7)
        before = self.store.get(original)
        new_message = self.store.add_message("session-B", "user", "我明确说：在甲公司工作")
        promoted = self.add("在甲公司工作", source="stated", session_id="session-B", evidence_state="linked",
                            evidence_message_id=new_message, evidence_quote="在甲公司工作",
                            confidence=.95, importance=.9)
        after = self.store.get(promoted)
        self.assertEqual(promoted, original)
        self.assertNotEqual(new_message, old_message)
        self.assertEqual((after.source, after.session_id, after.confidence, after.evidence_message_id,
                          after.evidence_quote, after.evidence_state, after.last_observed_message_id),
                         ("stated", "session-B", .95, new_message, "在甲公司工作", "linked", new_message))
        self.assertEqual((after.importance, after.updated_at, after.created_at),
                         (before.importance, before.updated_at, before.created_at))
        self.assertEqual(self.store.get_message(after.evidence_message_id)["session_id"], after.session_id)

    def test_automatic_noop_does_not_overwrite_manual_evidence(self):
        original = self.add("手动确认的事实")
        repeated, _ = self.linked("手动确认的事实")
        record = self.store.get(repeated)
        self.assertEqual(repeated, original)
        self.assertEqual((record.evidence_state, record.evidence_message_id, record.evidence_quote),
                         ("manual", None, ""))

    def test_same_source_can_link_previously_legacy_fact(self):
        original = self.add("已有事实", evidence_state="legacy", decision="legacy")
        linked, message_id = self.linked("已有事实")
        self.assertEqual(linked, original)
        self.assertEqual((self.store.get(linked).evidence_state,
                          self.store.get(linked).evidence_message_id), ("linked", message_id))

    def test_manual_correction_can_supersede_explicit_old_record(self):
        old, _ = self.linked("原事实")
        corrected = self.add("纠正后的事实", supersedes=old, decision="update")
        self.assertEqual((self.store.get(old).status, self.store.get(corrected).supersedes),
                         ("superseded", old))
        self.assertEqual((self.store.get(corrected).source, self.store.get(corrected).evidence_state),
                         ("stated", "manual"))

    def test_failed_insert_rolls_back_supersession(self):
        original = self.add("旧事实")
        before = self.store.get(original).to_dict()
        self.store._write("""CREATE TRIGGER reject_memory BEFORE INSERT ON memories
            WHEN NEW.value='拒绝写入' BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""", ())
        with self.assertRaises(sqlite3.IntegrityError):
            self.add("拒绝写入")
        self.assertEqual(self.store.get(original).to_dict(), before)
        self.assertEqual(len(self.store.list_memories(status=None)), 1)

    def test_messages_have_stable_ids_and_legacy_shape_is_preserved(self):
        first = self.store.add_message("s", "user", "原句", "voice")
        elsewhere = self.store.add_message("other", "user", "另一会话")
        second = self.store.add_message("s", "assistant", "回复", "text")
        third = self.store.add_message("s", "user", "后续")
        self.assertLess(first, elsewhere)
        self.assertLess(elsewhere, second)
        self.assertLess(second, third)
        self.assertEqual(set(self.store.get_message(first)),
                         {"id", "session_id", "role", "text", "mode", "created_at"})
        self.assertIsNone(self.store.get_message(999))
        self.assertEqual([row["id"] for row in self.store.recent_message_records("s", limit=2)],
                         [second, third])
        self.assertEqual([row["id"] for row in self.store.recent_message_records("s", before_id=second)],
                         [first, second])
        self.assertEqual(self.store.recent_messages("s", limit=2),
                         [{"role": "assistant", "text": "回复", "mode": "text"},
                          {"role": "user", "text": "后续", "mode": "text"}])

    def test_linked_evidence_rejects_assistant_wrong_session_and_invented_quotes(self):
        user = self.store.add_message("s", "user", "真实原句")
        assistant = self.store.add_message("s", "assistant", "助手生成内容")
        cases = [(user, "其他会话", "真实原句"), (assistant, "s", "助手生成内容"),
                 (user, "s", "没有出现的引文"), (999, "s", "真实原句")]
        for message_id, session_id, quote in cases:
            with self.subTest(message_id=message_id, quote=quote):
                with self.assertRaises(ValueError):
                    self.add(evidence_state="linked", evidence_message_id=message_id,
                             session_id=session_id, evidence_quote=quote)
        self.assertEqual(self.store.count(), 0)

    def test_delete_key_removes_entire_history_without_scan_limit(self):
        for index in range(1105):
            self.add(f"公司记录 {index}")
        other_subject = self.add("角色同key记录", subject="self")
        other_kind = self.add("同key经历", kind="episode")
        unrelated = self.add("另一属性", key="work:role")
        self.assertEqual(self.store.delete_key("WORK:COMPANY", kind="fact", subject="user"), 1105)
        self.assertIsNotNone(self.store.get(other_subject))
        self.assertIsNotNone(self.store.get(other_kind))
        self.assertIsNotNone(self.store.get(unrelated))
        self.assertEqual(self.store.delete_key("work:company"), 2)
        self.assertEqual(self.store.count(), 1)

    def test_every_deletion_advances_epoch_even_without_matching_rows(self):
        self.assertEqual(self.store.write_state(), (0, 0))
        latest = self.store.add_message("s", "user", "旧消息")
        self.assertFalse(self.store.delete(999))
        self.assertEqual(self.store.write_state(), (1, latest))
        self.assertEqual(self.store.delete_all(kind="episode"), 0)
        self.assertEqual(self.store.write_state(), (2, latest))
        self.assertEqual(self.store.delete_key("absent"), 0)
        self.assertEqual(self.store.write_state(), (3, latest))
        self.assertEqual(self.store.invalidate_pending(), 4)
        self.assertEqual(self.store.write_state(), (4, latest))

    def test_filtered_deletion_preserves_other_subjects_and_messages(self):
        self.add("用户事实")
        retained = self.add("角色事实", subject="self")
        message_id = self.store.add_message("s", "user", "用户原话")
        self.assertEqual(self.store.delete_all(subject="user"), 1)
        self.assertIsNotNone(self.store.get(retained))
        self.assertIsNotNone(self.store.get_message(message_id))
        self.assertEqual(self.store.write_state(), (1, message_id))

    def test_failed_delete_rolls_back_row_and_control_watermark(self):
        original = self.add()
        self.store.add_message("s", "user", "原话")
        self.store._write("""CREATE TRIGGER reject_delete BEFORE DELETE ON memories
            BEGIN SELECT RAISE(ABORT,'synthetic failure'); END""", ())
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.delete(original)
        self.assertIsNotNone(self.store.get(original))
        self.assertEqual(self.store.write_state(), (0, 0))

    def test_stale_epoch_and_old_evidence_are_rejected_across_connections_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "memory.db")
            first, second = MemoryStore(path), MemoryStore(path)
            try:
                old_message = first.add_message("s", "user", "删除前原话")
                captured_epoch = first.write_epoch
                second.delete_key("not-yet-written")
                self.assertEqual(first.write_state(), (1, old_message))
                new_message = first.add_message("s", "user", "删除后新原话")
                fields = dict(kind="fact", key="fact:test", value="新事实", source="stated",
                              session_id="s", evidence_state="linked", evidence_message_id=new_message,
                              evidence_quote="删除后新原话")
                version = first.version
                self.assertIsNone(first.upsert(**fields, expected_write_epoch=captured_epoch))
                self.assertEqual(first.version, version)
                self.assertEqual(first.count(), 0)
                self.assertIsNotNone(first.upsert(**fields, expected_write_epoch=1))
            finally:
                first.close()
                second.close()
            reopened = MemoryStore(path)
            try:
                self.assertEqual(reopened.write_state(), (1, old_message))
                self.assertIsNone(reopened.upsert(kind="fact", key="fact:old", value="旧事实",
                    session_id="s", evidence_state="linked", evidence_message_id=old_message,
                    evidence_quote="删除前原话", expected_write_epoch=1))
                self.assertIsNone(reopened.active_for("fact", "user", "fact:old"))
            finally:
                reopened.close()

    def test_inference_budget_count_includes_conflicts_history_and_only_requested_session(self):
        self.add("旧推测", source="inferred", session_id="s")
        self.add("新推测", source="inferred", session_id="s")
        self.add("不确定冲突", source="uncertain", status="conflict", session_id="s")
        self.add("明确事实", key="fact:stated", session_id="s")
        self.add("另一会话推测", key="fact:elsewhere", source="inferred", session_id="other")
        self.assertEqual(self.store.count_inferences("s"), 3)
        self.assertEqual(self.store.count_inferences("other"), 1)
        self.assertEqual(self.store.count_inferences("absent"), 0)

    def test_older_linked_write_is_rejected_even_when_epoch_has_not_changed(self):
        older = self.store.add_message("s", "user", "旧说法")
        newer = self.store.add_message("s", "user", "新说法")
        current = self.add("新事实", session_id="s", evidence_state="linked",
                           evidence_message_id=newer, evidence_quote="新说法")
        before = self.store.get(current).to_dict()
        version = self.store.version
        self.assertIsNone(self.add("旧事实", session_id="s", evidence_state="linked",
            evidence_message_id=older, evidence_quote="旧说法", expected_write_epoch=self.store.write_epoch))
        self.assertEqual(self.store.get(current).to_dict(), before)
        self.assertEqual(self.store.version, version)
        self.assertEqual(self.store.count(), 1)

    def test_same_message_disagreeing_values_form_conflict_instead_of_replacement(self):
        message_id = self.store.add_message("s", "user", "这段原话有两种解读")
        fields = dict(session_id="s", evidence_state="linked", evidence_message_id=message_id,
                      evidence_quote="这段原话有两种解读")
        original = self.add("解读一", **fields)
        before = self.store.get(original).to_dict()
        conflict = self.add("解读二", supersedes=original, **fields)
        self.assertEqual(self.store.get(original).to_dict(), before)
        self.assertEqual(self.store.get(conflict).status, "conflict")
        self.assertEqual(self.store.count(), 1)

    def test_message_order_guard_observes_other_store_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "memory.db")
            first, second = MemoryStore(path), MemoryStore(path)
            try:
                older = first.add_message("s", "user", "旧资料")
                newer = first.add_message("s", "user", "新资料")
                self.assertIsNone(first.active_for("fact", "user", "fact:order"))
                current = second.upsert(kind="fact", key="fact:order", value="新事实", session_id="s",
                    evidence_state="linked", evidence_message_id=newer, evidence_quote="新资料")
                self.assertIsNone(first.upsert(kind="fact", key="fact:order", value="旧事实", session_id="s",
                    evidence_state="linked", evidence_message_id=older, evidence_quote="旧资料",
                    expected_write_epoch=0))
                self.assertEqual(first.active_for("fact", "user", "fact:order").id, current)
            finally:
                first.close()
                second.close()

    def test_latest_noop_orders_writes_without_replacing_first_evidence(self):
        initial = self.store.add_message("s", "user", "旧值")
        late = self.store.add_message("s", "user", "纠正为中间值")
        reaffirmed = self.store.add_message("s", "user", "我再次确认旧值")
        original = self.add("旧值", session_id="s", evidence_state="linked",
                            evidence_message_id=initial, evidence_quote="旧值")
        original_age = self.store.get(original).updated_at
        self.assertEqual(self.add("旧值", session_id="s", evidence_state="linked",
                                 evidence_message_id=reaffirmed, evidence_quote="我再次确认旧值"), original)
        record = self.store.get(original)
        self.assertEqual((record.evidence_message_id, record.evidence_quote,
                          record.last_observed_message_id, record.updated_at),
                         (initial, "旧值", reaffirmed, original_age))
        self.assertIsNone(self.add("中间值", session_id="s", evidence_state="linked",
                                  evidence_message_id=late, evidence_quote="纠正为中间值",
                                  expected_write_epoch=0, decision="update"))
        self.assertEqual(self.store.active_for("fact", "user", "work:company").value, "旧值")

    def test_weak_noop_does_not_advance_latest_strong_observation(self):
        original, initial = self.linked("明确旧值")
        later = self.store.add_message("s", "user", "我推测也许是明确旧值")
        self.assertEqual(self.add("明确旧值", source="inferred", session_id="s", evidence_state="linked",
                                 evidence_message_id=later, evidence_quote="我推测也许是明确旧值"), original)
        record = self.store.get(original)
        self.assertEqual((record.evidence_message_id, record.last_observed_message_id, record.source),
                         (initial, initial, "stated"))

    def test_invalid_provenance_arguments_do_not_modify_active_record(self):
        original = self.add()
        before = self.store.get(original).to_dict()
        for fields in ({"evidence_message_id": True}, {"evidence_message_id": 0},
                       {"evidence_quote": None}, {"evidence_state": "verified"},
                       {"expected_write_epoch": True}, {"status": "invented"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.add("另一事实", **fields)
        self.assertEqual(self.store.get(original).to_dict(), before)

    def test_close_is_idempotent(self):
        self.store.close()
        self.store.close()
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.store.invalidate_pending()


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        MODULE.store.close()
