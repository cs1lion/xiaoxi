"""Offline original-message -> extraction -> later-session integration.

Import the production Orchestrator and use its real prompt assembly, composer,
context broker, memory manager, store and retriever. LLM/TTS are fake; all memory
and preference stores are explicitly in-memory. This checks wiring and lifecycle
ordering, not model or audio quality.
Run: .venv/Scripts/python.exe -B scripts/test_memory_orchestrator.py
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

if __package__:
    from .test_memory_evidence import MemoryManager, MemoryStore, candidate
else:
    from test_memory_evidence import MemoryManager, MemoryStore, candidate
from server.interaction.broker import ContextBroker
from server.interaction.preferences import PreferenceStore


production = importlib.import_module("server.dialogue.orchestrator")


class OrchestratorEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore(":memory:")
        self.manager = MemoryManager(self.store)
        self.manager.retriever.cfg = dict(self.manager.retriever.cfg)
        self.manager.retriever.cfg.update(min_score=0, min_similarity=.01, max_inject_chars=4000)
        self.session = self.store.start_session("synthetic-orch")
        self.prompts = []
        self.extractions = []
        self.tasks = []
        self.preferences = PreferenceStore(":memory:")
        self.addCleanup(self.preferences.close)
        async def complete_raw(messages, **kwargs):
            self.prompts.append(copy.deepcopy(messages))
            return {"text": "[base]合成测试回复。", "finish_reason": "stop", "usage": {}}

        llm = SimpleNamespace(complete_raw=complete_raw)

        async def stream(messages, **kwargs):
            result = await llm.complete_raw(messages, **kwargs)
            yield result["text"]

        llm.stream = stream
        self.module = production
        self.orch = production.Orchestrator()
        self.orch.pacing = copy.deepcopy(self.orch.pacing)
        self.orch.context_broker = ContextBroker(self.preferences, composer=self.orch.composer)

        def recent(session, limit):
            rows = self.store.recent_messages(session, limit=limit * 2)
            return [{"role": row["role"], "content": row["text"]} for row in rows]

        for patcher in (
            patch.object(production, "manager", self.manager),
            patch.object(production, "store_message", side_effect=self.store.add_message),
            patch.object(production, "store_recent", side_effect=recent),
            patch.object(production, "llm", llm),
            patch.object(production, "tts", SimpleNamespace(synthesize=AsyncMock(return_value=b"fake-audio"))),
            patch("server.memory.manager.extract_memories", side_effect=self.extract),
            patch("server.llm.llm.complete_raw", side_effect=AssertionError("No model/network calls")),
            patch("httpx.AsyncClient.send", side_effect=AssertionError("No HTTP/network calls")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for task in self.tasks + list(self.orch._extract_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, *list(self.orch._extract_tasks), return_exceptions=True)
        self.store._conn.close()

    async def extract(self, user_text, assistant_text, **kwargs):
        records = kwargs.get("evidence_messages")
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["role"], "user")
        self.assertEqual(record["text"], user_text)
        self.assertEqual(self.store.get_message(record["id"])["text"], user_text)
        self.extractions.append(record)
        if "甲公司" in user_text:
            value, operation = "用户在甲公司工作", "add"
        elif "乙公司" in user_text:
            value, operation = "用户在乙公司工作", "update"
        else:
            return []
        return [candidate(value=value, quote=user_text, operation=operation,
                          evidence_message_id=record["id"], evidence_state="linked")]

    async def run_turn(self, text, *, stream=False, session=None):
        sid = session or self.session
        if stream:
            return [event async for event in self.orch.stream_respond(session_id=sid, user_text=text)]
        return await self.orch.respond(session_id=sid, user_text=text)

    async def test_text_original_is_linked_and_recalled_in_new_session(self):
        await self.run_turn("我在甲公司工作")
        await self.orch.drain()
        memory = self.store.list_memories()[0]
        original_id = memory.evidence_message_id
        self.assertEqual(self.store.get_message(original_id)["role"], "user")
        self.assertEqual(memory.evidence_quote, "我在甲公司工作")
        self.store.end_session(self.session)
        second = self.store.start_session("synthetic-next-day")
        await self.run_turn("我的公司是什么？", session=second)
        await self.orch.drain()
        supplied = [message for message in self.prompts[-1]
                    if message['role'] == 'user'
                    and message['content'].startswith('【不可信记忆数据')]
        self.assertEqual(len(supplied), 1)
        data = json.loads(supplied[0]['content'].split('\n', 1)[1].rsplit('\n', 1)[0])
        self.assertEqual(data['status'], 'found')
        self.assertEqual(len(data['stated']), 1)
        self.assertEqual(data['stated'][0]['value'], '用户在甲公司工作')
        self.assertEqual(data['stated'][0]['id'], memory.id)
        self.assertEqual(data['stated'][0]['evidence'], {'state': 'linked', 'message_id': original_id})
        self.assertEqual(self.prompts[-1][-1], {'role': 'user', 'content': '我的公司是什么？'})
        self.assertEqual(self.store.get(memory.id).evidence_message_id, original_id)

    async def test_streaming_original_is_linked_once(self):
        events = await self.run_turn("我在甲公司工作", stream=True)
        await self.orch.drain()
        self.assertEqual(events[-1]["type"], "done")
        messages = self.store._query("SELECT * FROM messages WHERE role='user'")
        self.assertEqual(len(messages), 1)
        self.assertEqual(self.store.list_memories()[0].evidence_message_id, messages[0]["id"])

    async def test_input_ids_precede_model_await_and_ignore_reply_completion_order(self):
        old = "我在甲公司工作"
        new = "纠正一下，我现在改为在乙公司工作。"
        started = {text: asyncio.Event() for text in (old, new)}
        release = {text: asyncio.Event() for text in (old, new)}
        original_ids = {}

        async def complete_raw(messages, **kwargs):
            text = messages[-1]["content"]
            rows = self.store._query("SELECT id FROM messages WHERE role='user' AND text=?", (text,))
            self.assertEqual(len(rows), 1, "User source must exist before the first model await")
            original_ids[text] = rows[0]["id"]
            started[text].set()
            await release[text].wait()
            return {"text": "[base]合成测试回复。", "finish_reason": "stop", "usage": {}}

        self.module.llm.complete_raw = complete_raw
        old_task = asyncio.create_task(self.run_turn(old, stream=True))
        self.tasks.append(old_task)
        await asyncio.wait_for(started[old].wait(), 3)
        new_task = asyncio.create_task(self.run_turn(new, stream=True))
        self.tasks.append(new_task)
        await asyncio.wait_for(started[new].wait(), 3)
        self.assertLess(original_ids[old], original_ids[new])
        release[new].set()
        await asyncio.wait_for(new_task, 3)
        await self.orch.drain()
        release[old].set()
        await asyncio.wait_for(old_task, 3)
        await self.orch.drain()
        current = self.store.list_memories()[0]
        self.assertEqual(current.value, "用户在乙公司工作")
        self.assertEqual(current.evidence_message_id, original_ids[new])

    async def test_delete_during_model_generation_prevents_later_extraction(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                started, release = asyncio.Event(), asyncio.Event()

                async def complete_raw(messages, **kwargs):
                    self.assertTrue(self.store._query("SELECT id FROM messages WHERE role='user'"))
                    started.set()
                    await release.wait()
                    return {"text": "[base]合成测试回复。", "finish_reason": "stop", "usage": {}}

                self.module.llm.complete_raw = complete_raw
                task = asyncio.create_task(self.run_turn("我在甲公司工作", stream=streaming))
                self.tasks.append(task)
                await asyncio.wait_for(started.wait(), 3)
                self.store.delete_all()
                release.set()
                await asyncio.wait_for(task, 3)
                await self.orch.drain()
                self.assertEqual(self.store.list_memories(status=None, include_history=True), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
