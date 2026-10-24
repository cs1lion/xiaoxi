"""Issue #28 记忆管理垂直切片（何时说过 / 停用）的契约测试。

Run: .venv/Scripts/python.exe -B scripts/test_memory_manage.py

两个隔离层：
- StoreRetrievalTests：内存 SQLite，只碰 store + retriever。验证软停用位对
  三档置信度装配（stated / inferred / uncertain）的排除与恢复，以及
  「停用不动硬删、不动 updated_at」的边界。
- MemoryManageAPITests：真实 app 装配（同 test_memory_api.py 的隔离骨架），
  验证 PATCH /api/memories/{id} 的 disabled 扩展与 /api/memories/context 的
  装配行为。无监听、无用户库、无模型、无音频、无供应商网络。
"""
from __future__ import annotations

import asyncio
import importlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["TTS_ENGINE"] = "silent"
os.environ["MUSIC_ENABLED"] = "false"
os.environ["MUSIC_API_KEY"] = ""
os.environ["MUSIC_URL"] = ""

with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.store import MemoryStore
    from server.memory.retriever import MemoryRetriever


def linked_candidate(text, message_id, *, value="用户在甲公司工作", operation="add"):
    return {"kind": "fact", "subject": "user", "key": "工作:公司", "value": value,
            "source": "stated", "importance": .8, "confidence": .95,
            "operation": operation, "evidence_message_id": message_id,
            "evidence_quote": text, "evidence_state": "linked"}


class StoreRetrievalTests(unittest.TestCase):
    """软停用位 × 三档装配（stated / inferred / uncertain）。"""

    def setUp(self):
        self.store = MemoryStore(':memory:')
        self.retriever = MemoryRetriever(self.store)
        self.retriever.cfg = dict(self.retriever.cfg)
        self.retriever.cfg.update(min_score=0, min_similarity=.08)

    def tearDown(self):
        self.store._conn.close()

    def add(self, value, **kwargs):
        return self.store.upsert(kind=kwargs.pop('kind', 'fact'), value=value,
                                 key=kwargs.pop('key', value), **kwargs)

    def test_all_three_tiers_excluded_when_disabled_and_restored_on_enable(self):
        stated = self.add("用户养了一只猫叫旺财", key="宠物:猫", source="stated")
        inferred = self.add("用户可能在学吉他", key="爱好:乐器", source="inferred")
        uncertain = self.add("用户似乎常熬夜", key="作息:熬夜", source="uncertain")
        query = "用户 养猫 学吉他 熬夜"
        self.assertEqual({m.source for m, _ in self.retriever.search(query)},
                         {"stated", "inferred", "uncertain"})
        for memory_id in (stated, inferred, uncertain):
            self.assertTrue(self.store.set_disabled(memory_id, True))
        self.assertEqual(self.retriever.search(query), [])
        # 恢复逐条可用：只恢复 stated，另外两档仍然排除。
        self.assertTrue(self.store.set_disabled(stated, False))
        hits = [m.source for m, _ in self.retriever.search(query)]
        self.assertEqual(hits, ["stated"])

    def test_build_context_reports_not_found_when_only_hit_is_disabled(self):
        memory_id = self.add("用户养了一只猫叫旺财", key="宠物:猫")
        self.assertTrue(self.store.set_disabled(memory_id, True))
        # 查询用带真实二元桥的整句短语（F1 起 ngram 从 2 起，电报式两字查询
        # 「养猫」在存文「养了一只猫」里没有连续二元，会按设计落空）。
        block, used, found = self.retriever.build_context("用户养了什么宠物")
        # 检索为空必须是显式的「没找到」，不是空串静默：found=False 由上游
        # 注入负向约束，防止脑补共同经历。
        self.assertEqual((block, used, found), ("", [], False))
        self.assertTrue(self.store.set_disabled(memory_id, False))
        block, used, found = self.retriever.build_context("用户养了什么宠物")
        self.assertTrue(found)
        self.assertEqual(used, [memory_id])
        self.assertIn("旺财", block)

    def test_toggle_is_soft_and_never_touches_row_lifecycle(self):
        memory_id = self.add("用户养了一只猫叫旺财", key="宠物:猫")
        before = self.store.get(memory_id)
        self.assertTrue(self.store.set_disabled(memory_id, True))
        after = self.store.get(memory_id)
        # 软位不动内容生命周期：updated_at 是排序/新鲜度输入，拨开关不得
        # 让旧记忆变新；行数不变，物理硬删语义完全留给 delete。
        self.assertEqual(after.updated_at, before.updated_at)
        self.assertEqual(after.status, before.status)
        self.assertEqual(self.store.count(), 1)
        self.assertTrue(after.disabled)
        self.assertTrue(self.store.set_disabled(memory_id, False))
        self.assertFalse(self.store.get(memory_id).disabled)
        self.assertEqual(self.store.count(), 1)

    def test_bool_shape_in_export_and_validation(self):
        memory_id = self.add("用户养了一只猫叫旺财", key="宠物:猫")
        self.store.set_disabled(memory_id, True)
        exported = self.store.get(memory_id).to_dict()
        self.assertIs(exported["disabled"], True)
        with self.assertRaises(ValueError):
            self.store.set_disabled(memory_id, "yes")
        self.assertFalse(self.store.set_disabled(999_999, True))

    def test_legacy_database_gains_disabled_column(self):
        """老库（v4、无 disabled 列）打开即补列，旧行默认未停用。"""
        with tempfile.TemporaryDirectory(prefix="memory-legacy-") as folder:
            path = str(Path(folder) / "legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL, subject TEXT NOT NULL DEFAULT 'user',
                    key TEXT NOT NULL, value TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'stated',
                    status TEXT NOT NULL DEFAULT 'active',
                    supersedes INTEGER, importance REAL NOT NULL DEFAULT 0.5,
                    confidence REAL NOT NULL DEFAULT 0.8, session_id TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    last_used_at REAL, use_count INTEGER NOT NULL DEFAULT 0
                );
                PRAGMA user_version = 4;
            """)
            conn.execute("INSERT INTO memories(kind, key, value, created_at, updated_at)"
                         " VALUES ('fact', '旧:记忆', '旧库里的记忆', 1, 1)")
            conn.commit()
            conn.close()
            store = MemoryStore(path)
            try:
                row = store.list_memories(include_history=True)[0]
                self.assertIs(row.to_dict()["disabled"], False)
                self.assertTrue(store.set_disabled(row.id, True))
                self.assertTrue(store.get(row.id).disabled)
            finally:
                store._conn.close()


class MemoryManageAPITests(unittest.IsolatedAsyncioTestCase):
    """PATCH /api/memories/{id} 的 disabled 扩展（真实装配，全隔离）。"""

    @classmethod
    def setUpClass(cls):
        if "server.app" in sys.modules:
            raise RuntimeError("Run this script standalone: server.app was already imported")
        cls.temporary = tempfile.TemporaryDirectory(prefix="memory-manage-api-")
        cls.root = Path(cls.temporary.name).resolve()
        cls.data = cls.root / "data"
        cls.data.mkdir()
        (cls.root / "web").mkdir()
        (cls.root / "web" / "index.html").write_text("isolated API fixture", encoding="utf-8")
        cls.addClassCleanup(cls.temporary.cleanup)

        def start(patcher):
            patcher.start()
            cls.addClassCleanup(patcher.stop)

        start(patch("dotenv.load_dotenv", return_value=False))
        config = importlib.import_module("server.config")
        start(patch.object(config, "ROOT", cls.root))
        start(patch.object(config, "DATA_DIR", cls.data))
        start(patch.object(config, "AUDIO_DIR", cls.data / "audio"))
        start(patch.object(config.settings, "db_path", ":memory:"))
        real_connect = sqlite3.connect

        def guarded_connect(database, *args, **kwargs):
            target = os.fspath(database)
            if target != ":memory:":
                path = Path(target).resolve()
                if not path.is_relative_to(cls.data):
                    raise AssertionError("SQLite attempted a database outside the isolated data root")
                target = str(path)
            return real_connect(database, *args, **kwargs)

        start(patch("sqlite3.connect", side_effect=guarded_connect))
        start(patch("httpx.AsyncHTTPTransport.handle_async_request",
                    side_effect=AssertionError("Provider HTTP is forbidden in this suite")))
        start(patch("httpx.HTTPTransport.handle_request",
                    side_effect=AssertionError("Provider HTTP is forbidden in this suite")))

        cls.module = importlib.import_module("server.app")
        cls.store = cls.module.store
        cls.manager = cls.module.manager
        cls.addClassCleanup(cls.store._conn.close)
        for resource in (cls.module.assistant.store, cls.module.assistant.runtime,
                         cls.module.assistant.preference_store, cls.module.assistant.threads,
                         cls.module.assistant.executive):
            cls.addClassCleanup(resource.close)
        start(patch.object(cls.module.llm, "complete_raw",
                           side_effect=AssertionError("LLM calls are forbidden")))
        start(patch.object(cls.module.tts, "synthesize",
                           side_effect=AssertionError("TTS calls are forbidden")))
        start(patch.object(cls.module.recognizer, "transcribe",
                           side_effect=AssertionError("ASR calls are forbidden")))

    async def asyncSetUp(self):
        self.store.delete_all()
        self.manager.enabled = True
        self.session = self.store.start_session("synthetic-memory-manage")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.module.app), base_url="http://isolated.local")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def request(self, method, path, *, expected=200, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, expected, response.text)
        return response.json()

    async def observe(self, text, *, value="用户在甲公司工作"):
        message_id = self.store.add_message(self.session, "user", text)
        item = linked_candidate(text, message_id, value=value)
        with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
            saved = await self.manager.observe_turn(session_id=self.session, user_text=text,
                assistant_text="合成测试回复", user_message_id=message_id)
        return saved[0], message_id

    async def test_disable_drops_memory_from_assembly_and_reenable_restores(self):
        memory, _ = await self.observe("我在甲公司工作")
        context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
        self.assertTrue(context["found"])
        self.assertIn("用户在甲公司工作", context["context"])

        before = self.store.get(memory.id)
        patched = await self.request("PATCH", f"/api/memories/{memory.id}",
                                     json={"disabled": True})
        self.assertIs(patched["disabled"], True)
        self.assertEqual(patched["updated_at"], before.updated_at)

        context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
        self.assertFalse(context["found"])
        self.assertEqual(context["context"], "")
        # 列表仍可见（软停用不是删除），带停用位供面板渲染。
        listed = await self.request("GET", "/api/memories")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["items"][0]["disabled"], True)

        restored = await self.request("PATCH", f"/api/memories/{memory.id}",
                                      json={"disabled": False})
        self.assertIs(restored["disabled"], False)
        context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
        self.assertTrue(context["found"])
        self.assertIn("用户在甲公司工作", context["context"])

    async def test_disable_keeps_hard_delete_semantics(self):
        memory, _ = await self.observe("我在甲公司工作")
        await self.request("PATCH", f"/api/memories/{memory.id}", json={"disabled": True})
        deleted = await self.request("DELETE", f"/api/memories/{memory.id}")
        self.assertEqual(deleted["removed_rows"], 1)
        self.assertEqual((await self.request("GET", "/api/memories"))["count"], 0)

    async def test_disable_validation_and_not_found(self):
        memory, _ = await self.observe("我在甲公司工作")
        await self.request("PATCH", f"/api/memories/{memory.id}",
                           json={"disabled": "yes"}, expected=400)
        await self.request("PATCH", f"/api/memories/{memory.id}", json={}, expected=400)
        await self.request("PATCH", f"/api/memories/{memory.id}",
                           json={"value": "改成乙公司", "disabled": True}, expected=400)
        await self.request("PATCH", "/api/memories/999999", json={"disabled": True}, expected=404)
        # 被拒的请求不得改变状态。
        self.assertIs(self.store.get(memory.id).disabled, False)
        # 纯 value 语义原样保留（回归）。
        corrected = await self.request("PATCH", f"/api/memories/{memory.id}",
                                       json={"value": "用户在乙公司工作"})
        self.assertEqual(corrected["decision"], "update")


if __name__ == "__main__":
    unittest.main(verbosity=2)
