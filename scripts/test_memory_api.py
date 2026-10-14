"""Actual application assembly and memory REST contracts, fully isolated.

Run standalone: .venv/Scripts/python.exe -B scripts/test_memory_api.py
This imports server.app normally, with dotenv disabled, memory DB in RAM and
ROOT/DATA_DIR/AUDIO_DIR redirected before app import. AssistantService's five
databases are temporary; a SQLite guard rejects every other database location.
ASGITransport performs real route dispatch without startup/lifespan/scheduler.
No listener, user database, model, audio device or provider is used.
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


def linked_candidate(text, message_id, *, value="用户在甲公司工作", operation="add"):
    return {"kind": "fact", "subject": "user", "key": "工作:公司", "value": value,
            "source": "stated", "importance": .8, "confidence": .95,
            "operation": operation, "evidence_message_id": message_id,
            "evidence_quote": text, "evidence_state": "linked"}


class MemoryAPITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        if "server.app" in sys.modules:
            raise RuntimeError("Run this script standalone: server.app was already imported")
        cls.temporary = tempfile.TemporaryDirectory(prefix="memory-app-api-")
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
        cls.opened_databases = []
        real_connect = sqlite3.connect

        def guarded_connect(database, *args, **kwargs):
            target = os.fspath(database)
            if target != ":memory:":
                path = Path(target).resolve()
                if not path.is_relative_to(cls.data):
                    raise AssertionError("SQLite attempted a database outside the isolated data root")
                target = str(path)
            cls.opened_databases.append(target)
            return real_connect(database, *args, **kwargs)

        start(patch("sqlite3.connect", side_effect=guarded_connect))
        start(patch("httpx.AsyncHTTPTransport.handle_async_request",
                    side_effect=AssertionError("Provider HTTP is forbidden in this suite")))
        start(patch("httpx.HTTPTransport.handle_request",
                    side_effect=AssertionError("Provider HTTP is forbidden in this suite")))

        # Every singleton is created only now, under the path/database guards.
        cls.module = importlib.import_module("server.app")
        cls.store = cls.module.store
        cls.manager = cls.module.manager
        cls.addClassCleanup(cls.store._conn.close)
        for resource in (cls.module.assistant.store, cls.module.assistant.runtime,
                         cls.module.assistant.preference_store, cls.module.assistant.threads):
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
        self.session = self.store.start_session("synthetic-memory-api")
        self.tasks = []
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.module.app), base_url="http://isolated.local")

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()

    async def request(self, method, path, **kwargs):
        response = await self.client.request(method, path, **kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def observe(self, text, *, message_id=None, value="用户在甲公司工作", operation="add"):
        message_id = message_id or self.store.add_message(self.session, "user", text)
        item = linked_candidate(text, message_id, value=value, operation=operation)
        with patch("server.memory.manager.extract_memories", AsyncMock(return_value=[item])):
            saved = await self.manager.observe_turn(session_id=self.session, user_text=text,
                assistant_text="合成测试回复", user_message_id=message_id)
        return saved, message_id

    async def test_actual_bootstrap_and_all_databases_stay_isolated(self):
        self.assertEqual(self.module.ROOT.resolve(), self.root)
        self.assertEqual(self.store.db_path, ":memory:")
        self.assertIs(self.module.assistant.memory_manager, self.manager)
        self.assertIs(self.module.orchestrator.context_broker, self.module.assistant.context_broker)
        expected = {str(self.data / name) for name in
                    ("assistant.db", "notes.db", "agent_runtime.db", "preferences.db", "threads.db")}
        self.assertTrue(expected.issubset(set(self.opened_databases)))
        self.assertTrue((self.data / "audio").is_dir())
        self.assertIsNone(self.module._scheduler_task)
        body = await self.request("GET", "/api/memories")
        self.assertEqual(body, {"items": [], "count": 0, "enabled": True})
        self.assertIsNone(self.module._scheduler_task)

    async def test_memory_list_get_route_is_registered_once(self):
        routes = [route for route in self.module.app.routes
                  if getattr(route, "path", None) == "/api/memories"
                  and "GET" in getattr(route, "methods", set())]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].endpoint.__name__, "list_memories")

    async def test_manual_api_preserves_manual_provenance_in_response_and_list(self):
        memory = await self.request("POST", "/api/memory/manual", json={
            "kind": "fact", "key": "工作:公司", "value": "用户在甲公司工作",
            "evidence_state": "linked", "evidence_message_id": 9_999_999,
            "evidence_quote": "伪造的原话"})
        self.assertEqual(memory["evidence_state"], "manual")
        self.assertIsNone(memory["evidence_message_id"])
        self.assertEqual(memory["evidence_quote"], "")
        self.assertEqual(memory["decision"], "add")
        self.assertEqual(memory["status"], "active")
        listed = await self.request("GET", "/api/memories")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["items"], [memory])
        context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
        self.assertIn("手动保存的", context["context"])
        self.assertNotIn("你明确说过的", context["context"])

    async def test_linked_fields_and_conflict_history_survive_real_route_serialization(self):
        saved, message_id = await self.observe("我在甲公司工作")
        self.assertEqual(len(saved), 1)
        await self.observe("我在乙公司工作", value="用户在乙公司工作")
        listed = await self.request("GET", "/api/memories")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(len(listed["items"]), 1)
        memory = listed["items"][0]
        self.assertEqual(memory["evidence_message_id"], message_id)
        self.assertEqual(memory["evidence_quote"], "我在甲公司工作")
        self.assertEqual(memory["evidence_state"], "linked")
        history = await self.request("GET", "/api/memories", params={"include_history": "true"})
        self.assertEqual({item["status"] for item in history["items"]}, {"active", "conflict"})
        conflicts = [item for item in history["items"] if item["status"] == "conflict"]
        self.assertEqual(conflicts[0]["decision"], "conflict")
        context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
        self.assertNotIn("用户在乙公司工作", context["context"])

    async def test_correction_api_returns_manual_evidence_and_keeps_linked_history(self):
        saved, message_id = await self.observe("我在甲公司工作")
        corrected = await self.request("PATCH", f"/api/memories/{saved[0].id}",
                                       json={"value": "用户现在在乙公司工作"})
        self.assertEqual(corrected["evidence_state"], "manual")
        self.assertIsNone(corrected["evidence_message_id"])
        self.assertEqual(corrected["evidence_quote"], "")
        self.assertEqual(corrected["decision"], "update")
        self.assertEqual(corrected["supersedes"], saved[0].id)
        listed = await self.request("GET", "/api/memories")
        self.assertEqual([item["id"] for item in listed["items"]], [corrected["id"]])
        history = await self.request("GET", "/api/memories/history", params={"key": "工作:公司"})
        self.assertEqual(len(history["items"]), 2)
        original = next(item for item in history["items"] if item["id"] == saved[0].id)
        self.assertEqual(original["status"], "superseded")
        self.assertEqual(original["evidence_message_id"], message_id)
        self.assertEqual(original["evidence_quote"], "我在甲公司工作")

    async def test_api_toggle_off_on_invalidates_inflight_write_then_accepts_new_message(self):
        text = "我在甲公司工作"
        message_id = self.store.add_message(self.session, "user", text)
        started, release = asyncio.Event(), asyncio.Event()

        async def extract(*args, **kwargs):
            started.set()
            await release.wait()
            return [linked_candidate(text, message_id)]

        with patch("server.memory.manager.extract_memories", new=extract):
            task = asyncio.create_task(self.manager.observe_turn(session_id=self.session,
                user_text=text, assistant_text="合成测试回复", user_message_id=message_id))
            self.tasks.append(task)
            await asyncio.wait_for(started.wait(), 3)
            self.assertEqual(await self.request("POST", "/api/memory/toggle", json={"enabled": False}),
                             {"enabled": False})
            off_context = await self.request("GET", "/api/memories/context", params={"query": "公司"})
            self.assertEqual(off_context, {"items": [], "found": False})
            await self.request("POST", "/api/memory/toggle", json={"enabled": True})
            release.set()
            self.assertEqual(await asyncio.wait_for(task, 3), [])
        self.assertEqual((await self.request("GET", "/api/memories"))["items"], [])
        saved, new_id = await self.observe(text)
        self.assertEqual(saved[0].evidence_message_id, new_id)
        self.assertGreater(new_id, message_id)

    async def test_delete_api_removes_identity_history_and_blocks_original_message_replay(self):
        saved, message_id = await self.observe("我在甲公司工作")
        corrected = await self.request("PATCH", f"/api/memories/{saved[0].id}",
                                       json={"value": "用户现在在乙公司工作"})
        deleted = await self.request("DELETE", f"/api/memories/{corrected['id']}")
        self.assertEqual(deleted["removed_rows"], 2)
        self.assertEqual((await self.request("GET", "/api/memories"))["count"], 0)
        history = await self.request("GET", "/api/memories/history", params={"key": "工作:公司"})
        self.assertEqual(history["items"], [])
        saved, _ = await self.observe("我在甲公司工作", message_id=message_id)
        self.assertEqual(saved, [])
        self.assertIsNotNone(self.store.get_message(message_id))
        transcript = await self.request("GET", f"/api/session/{self.session}/transcript")
        self.assertIn("我在甲公司工作", [item["text"] for item in transcript["items"]])


if __name__ == "__main__":
    unittest.main(verbosity=2)
