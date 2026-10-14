"""Offline tests: python -m unittest scripts.test_stream_interpretation -v."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.speech import stream_interpretation as api


class WS:
    def __init__(self):
        self.input = asyncio.Queue()
        self.out = []
        self.changed = asyncio.Event()

    async def accept(self):
        pass

    async def receive(self):
        return await self.input.get()

    async def send_text(self, text):
        self.out.append(json.loads(text))
        self.changed.set()

    def control(self, kind):
        self.input.put_nowait({"type": "websocket.receive", "text": json.dumps(
            {"type": kind, "session_id": "test"})})

    def binary(self, data):
        self.input.put_nowait({"type": "websocket.receive", "bytes": data})

    async def wait(self, kind, count=1):
        async def loop():
            while True:
                matches = [x for x in self.out if x["type"] == kind]
                if len(matches) >= count:
                    return matches[-1]
                self.changed.clear()
                await self.changed.wait()
        return await asyncio.wait_for(loop(), 2)


class Adapter:
    def __init__(self):
        self.events = asyncio.Queue()
        self.frames = []
        self.closed = 0
        self.opened = asyncio.Event()
        self.open_gate = None
        self.send_gate = None
        self.receive_gate = None

    async def open(self):
        self.opened.set()
        if self.open_gate:
            try:
                await self.open_gate.wait()
            except asyncio.CancelledError:
                await self.open_gate.wait()

    async def send_audio(self, frame):
        self.frames.append(frame)
        if self.send_gate:
            await self.send_gate.wait()

    async def receive(self):
        try:
            return await self.events.get()
        except asyncio.CancelledError:
            if self.receive_gate:
                await self.receive_gate.wait()
                return {"type": "final", "item_id": "late", "text": "late"}
            raise

    async def close(self):
        self.closed += 1

    def emit(self, kind, ident="a", text="hello", stash=""):
        self.events.put_nowait(dict(type=kind, item_id=ident, text=text, stash=stash, words=[]))


class LLM:
    def __init__(self):
        self.calls = []
        self.called = asyncio.Event()
        self.gate = None
        self.resistant = False

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        self.called.set()
        if self.gate:
            try:
                await self.gate.wait()
            except asyncio.CancelledError:
                if not self.resistant:
                    raise
                await self.gate.wait()
        return "译文"


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws, self.adapter, self.llm = WS(), Adapter(), LLM()
        self.task = None

    async def start(self, ready=True):
        self.task = asyncio.create_task(api.serve_stream_interpretation(
            self.ws, self.llm, lambda: self.adapter))
        self.ws.control("start")
        if ready:
            contract = await self.ws.wait("ready")
            self.assertEqual(contract, dict(type="ready", session_id="test",
                                           mode="streaming", sample_rate=16000))

    async def asyncTearDown(self):
        if self.task and not self.task.done():
            self.ws.control("stop")
            await asyncio.wait_for(self.task, 2)
        for gate in (self.adapter.open_gate, self.adapter.receive_gate, self.llm.gate):
            if gate:
                gate.set()
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_partial_replacement_final_only_and_dedupe(self):
        await self.start()
        self.adapter.emit("partial", text="hel", stash="lo")
        self.adapter.emit("partial", text="help", stash="!")
        await self.ws.wait("source_partial", 2)
        self.assertEqual([x["text"] for x in self.ws.out if x["type"] == "source_partial"], ["hel", "help"])
        self.assertEqual(self.llm.calls, [])
        self.adapter.emit("final", text="help!")
        await self.ws.wait("translation")
        self.adapter.emit("partial", text="late")
        self.adapter.emit("final", text="duplicate")
        self.adapter.emit("final", "b", "next")
        await self.ws.wait("translation", 2)
        self.assertEqual(len(self.llm.calls), 2)
        data = json.loads(self.llm.calls[1][1]["content"])
        self.assertEqual(data["context"], [{"source": "help!", "translation": "译文"}])
        self.assertEqual(len([x for x in self.ws.out if x["type"] == "source_partial"]), 2)

    async def test_busy_gap_two_queued_one_active(self):
        self.llm.gate = asyncio.Event()
        await self.start()
        self.adapter.emit("final", "0")
        await self.llm.called.wait()
        for i in range(1, 4):
            self.adapter.emit("final", str(i))
        gap = await self.ws.wait("gap")
        self.assertEqual(gap["item_id"], "3")
        self.llm.gate.set()
        await self.ws.wait("translation", 3)
        self.assertEqual(len(self.llm.calls), 3)

    async def test_stop_during_resistant_open(self):
        self.adapter.open_gate = asyncio.Event()
        await self.start(False)
        await self.adapter.opened.wait()
        self.ws.control("stop")
        await asyncio.wait_for(self.task, 1)
        self.assertGreater(self.adapter.closed, 0)
        self.adapter.open_gate.set()
        for _ in range(8):
            await asyncio.sleep(0)
        self.assertFalse(any(x["type"] == "ready" for x in self.ws.out))

    async def test_stop_fences_resistant_translation_and_receive(self):
        self.llm.gate = asyncio.Event()
        self.llm.resistant = True
        self.adapter.receive_gate = asyncio.Event()
        await self.start()
        self.adapter.emit("final")
        await self.llm.called.wait()
        self.ws.control("stop")
        await asyncio.wait_for(self.task, 1)
        before = list(self.ws.out)
        self.llm.gate.set()
        self.adapter.receive_gate.set()
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(before, self.ws.out)

    async def test_audio_overflow_is_fatal(self):
        self.adapter.send_gate = asyncio.Event()
        await self.start()
        for _ in range(15):
            self.ws.binary(bytes(3200))
        error = await self.ws.wait("error")
        self.assertEqual(error["code"], "audio_overflow")
        await asyncio.wait_for(self.task, 1)
        self.assertGreater(self.adapter.closed, 0)

    async def test_audio_pending_byte_bound(self):
        self.adapter.send_gate = asyncio.Event()
        await self.start()
        self.ws.binary(bytes(32000))
        for _ in range(10):
            await asyncio.sleep(0)
        self.ws.binary(bytes(32000))
        self.ws.binary(bytes(2))
        self.assertEqual((await self.ws.wait("error"))["code"], "audio_overflow")
        await asyncio.wait_for(self.task, 1)

    async def test_audio_frame_bound(self):
        await self.start()
        self.ws.binary(bytes(32002))
        self.assertEqual((await self.ws.wait("error"))["code"], "invalid_audio")
        await self.task

    async def test_item_cap_includes_partial_ids(self):
        with patch.object(api, "MAX_ITEMS", 2):
            await self.start()
            for ident in ("a", "b", "c"):
                self.adapter.emit("partial", ident)
            self.assertEqual((await self.ws.wait("error"))["code"], "session_limit")
            await self.task

    async def test_setup_error_sanitized(self):
        async def bad_open():
            raise RuntimeError("SECRET internal credentials")
        self.adapter.open = bad_open
        await self.start(False)
        error = await self.ws.wait("error")
        self.assertEqual(error["code"], "setup_failed")
        self.assertNotIn("SECRET", json.dumps(self.ws.out))
        await self.task

    async def test_translation_timeout_is_bounded(self):
        self.llm.gate = asyncio.Event()
        self.llm.resistant = True
        with patch.object(api, "LLM_TIMEOUT", 0.02):
            await self.start()
            self.adapter.emit("final")
            await self.llm.called.wait()
            self.adapter.emit("final", "queued1")
            self.adapter.emit("final", "queued2")
            self.assertEqual((await self.ws.wait("error"))["code"], "llm_timeout")
            await asyncio.wait_for(self.task, 1)
            self.assertEqual(len(self.llm.calls), 1)
            self.assertGreater(self.adapter.closed, 0)

    async def test_start_only_once_and_disconnect(self):
        await self.start()
        self.ws.control("start")
        self.assertEqual((await self.ws.wait("error"))["code"], "invalid_control")
        await self.task
        self.assertEqual(self.adapter.closed, 1)

    async def test_provider_text_bound(self):
        await self.start()
        self.adapter.emit("final", text="x" * 12001)
        self.assertEqual((await self.ws.wait("error"))["code"], "provider_failed")
        await self.task
        self.assertEqual(self.llm.calls, [])

    async def test_continuous_audio_and_disconnect(self):
        await self.start()
        self.ws.binary(bytes(3200))
        self.ws.binary(bytes(6400))
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual([len(x) for x in self.adapter.frames], [3200, 6400])
        self.ws.input.put_nowait({"type": "websocket.disconnect"})
        await asyncio.wait_for(self.task, 1)
        self.assertEqual(self.adapter.closed, 1)


if __name__ == "__main__":
    unittest.main()
