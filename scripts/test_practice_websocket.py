"""Offline practice/WebSocket contract tests.

Run: python scripts/test_practice_websocket.py
The existing voice harness AST-compiles real app definitions without importing
app bootstrap. PracticeSession is loaded directly from its pure source file;
providers, assistant routing, persistence and relative imports are local fakes.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


def load_practice():
    path = Path(__file__).resolve().parents[1] / "server/speech/practice.py"
    spec = importlib.util.spec_from_file_location("isolated_practice_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.PracticeSession


PracticeSession = load_practice()


class PracticeWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = load_ws()
        self.env["__package__"] = "practice_ws_test"
        self.respond_calls = []
        self.review_calls = []
        self.connections = []
        self.releases = []
        self.store_message = Mock()
        self.route = Mock(return_value=False)
        self.stream_calls = []
        self.proactive_calls = []
        self.tts = object()
        owner = self

        class TestPractice(PracticeSession):
            async def respond(self, text, llm, tts, is_current):
                owner.respond_calls.append((text, llm, tts, is_current))
                async for event in owner.respond_events(is_current):
                    yield event

            async def review(self, llm, is_current):
                owner.review_calls.append((llm, is_current))
                yield {"type": "text_delta", "delta": "review"}
                yield {"type": "done", "text": "review", "items": []}

        original_connection = self.env["Connection"]

        def connection(ws):
            conn = original_connection(ws)
            self.connections.append(conn)
            return conn

        async def stream(**kwargs):
            self.stream_calls.append(kwargs)
            yield {"type": "text_delta", "delta": "original"}
            yield {"type": "done", "text": "original"}

        async def proactive(**kwargs):
            self.proactive_calls.append(kwargs)
            return SimpleNamespace(text="proactive")

        async def assistant_start(text, session_id, history=None):
            return {"text": "assistant action"}

        self.env.update(Connection=connection,
                        assistant=SimpleNamespace(should_route=self.route, start=assistant_start, waiting_input=lambda _: None),
                        store=SimpleNamespace(start_session=Mock(), end_session=Mock()),
                        orchestrator=SimpleNamespace(stream_respond=stream, proactive_turn=proactive,
                                                     state={"proactivity_enabled": False},
                                                     set_proactivity=Mock(),
                executive_route=lambda text, executive=None, memory_enabled=False: None))
        modules = {}
        for name, attrs in {
            "practice_ws_test.speech.practice": {"PracticeSession": TestPractice},
            "practice_ws_test.speech.tts": {"tts": self.tts},
            "practice_ws_test.dialogue.orchestrator": {"store_message": self.store_message,
                                                        "store_recent": lambda session_id, limit: []},
        }.items():
            module = ModuleType(name)
            module.__dict__.update(attrs)
            modules[name] = module
        self.module_patch = patch.dict("sys.modules", modules)
        self.module_patch.start()
        self.ws = FakeSocket()
        self.endpoint = asyncio.create_task(self.env["websocket_endpoint"](self.ws))
        await self.ws.wait("ready")
        self.conn = self.connections[0]

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        self.ws.inbox.put_nowait({"type": "websocket.disconnect"})
        try:
            await asyncio.wait_for(self.endpoint, 2)
            tasks = list(self.conn.tasks)
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        finally:
            self.module_patch.stop()

    async def respond_events(self, is_current):
        yield {"type": "thinking"}
        yield {"type": "text_delta", "delta": "practice reply"}
        yield {"type": "audio", "audio": b"\x00\xffraw-audio", "index": 2,
               "text": "practice reply", "emotion": "base", "revision": 7}
        yield {"type": "done", "text": "practice reply"}

    async def fresh(self, kind, start):
        async def find():
            while True:
                self.ws.changed.clear()
                for event in self.ws.events[start:]:
                    if event["type"] == kind:
                        return event
                await self.ws.changed.wait()
        return await asyncio.wait_for(find(), 2)

    async def config(self, value):
        start = len(self.ws.events)
        self.ws.send("config", practice=value)
        return await self.fresh("config_ack", start)

    async def barrier(self):
        start = len(self.ws.events)
        self.ws.send("config")
        await self.fresh("config_ack", start)

    def assert_isolated(self):
        self.route.assert_not_called()
        self.store_message.assert_not_called()
        self.assertEqual(self.stream_calls, [])
        self.assertEqual(self.proactive_calls, [])
        # Session bookkeeping remains part of /ws, not practice persistence.
        self.env["store"].start_session.assert_called_once_with(self.conn.session_id)
        self.env["store"].end_session.assert_not_called()

    async def test_practice_bypasses_assistant_store_and_orchestrator(self):
        await self.config({"mode": "free"})
        self.route.side_effect = AssertionError("Practice must not even consult assistant routing")
        self.ws.send("text", turn_id="practice", text="open a file")
        await self.ws.wait("done", "practice")
        self.assertEqual(len(self.respond_calls), 1)
        text, llm, tts, current = self.respond_calls[0]
        self.assertEqual(text, "open a file")
        self.assertIs(llm, self.env["llm"])
        self.assertIs(tts, self.tts)
        self.assertFalse(current())  # terminal completion invalidates callback
        self.assert_isolated()

    async def test_audio_encoded_once_and_all_events_use_request_turn_id(self):
        await self.config({"mode": "cafe"})
        start = len(self.ws.events)
        self.ws.send("text", turn_id="audio", text="coffee")
        await self.ws.wait("done", "audio")
        events = self.ws.events[start:]
        self.assertTrue(all(event["turn_id"] == "audio" for event in events))
        audio = await self.ws.wait("audio", "audio")
        self.assertNotIn("audio", audio)
        self.assertEqual(base64.b64decode(audio["data"]), b"\x00\xffraw-audio")
        self.assertEqual((audio["index"], audio["revision"]), (2, 7))
        self.assertEqual(sum(e["type"] == "done" for e in events), 1)

    async def test_review_receives_current_callback_and_requested_turn_id(self):
        await self.config({"mode": "interview"})
        self.ws.send("practice_review", turn_id="review-42")
        done = await self.ws.wait("done", "review-42")
        self.assertEqual(done["items"], [])
        self.assertEqual(self.respond_calls, [])
        self.assertEqual(len(self.review_calls), 1)
        self.assertIs(self.review_calls[0][0], self.env["llm"])
        self.assertFalse(self.review_calls[0][1]())
        self.assert_isolated()

    async def test_review_without_id_gets_server_turn_id(self):
        await self.config({"mode": "free"})
        self.ws.send("practice_review")
        done = await self.ws.wait("done")
        self.assertTrue(done["turn_id"].startswith("server-"))

    async def test_review_off_is_ignored(self):
        await self.config({"mode": "off"})
        self.ws.send("practice_review", turn_id="ignored")
        await self.barrier()
        self.assertEqual(self.review_calls, [])
        self.assertIsNone(self.conn.turn)

    async def test_off_preserves_original_orchestrator_path(self):
        await self.config({"mode": "free"})
        await self.config({"mode": "off"})
        self.ws.send("text", turn_id="ordinary", text="hello")
        done = await self.ws.wait("done", "ordinary")
        self.assertEqual(done["text"], "original")
        self.route.assert_called_once_with("hello", self.conn.session_id)
        self.assertEqual(self.stream_calls[0]["user_text"], "hello")
        self.assertEqual(self.respond_calls, [])

    async def test_off_preserves_original_assistant_and_storage_path(self):
        await self.config({"mode": "off"})
        self.route.return_value = True
        self.ws.send("text", turn_id="action", text="open a file")
        await self.ws.wait("done", "action")
        await self.ws.wait("assistant_result", "action")
        self.assertEqual(self.store_message.call_count, 2)
        self.assertEqual(self.stream_calls, [])
        self.assertEqual(self.respond_calls, [])

    async def test_config_validation_is_atomic_and_socket_survives(self):
        await self.config({"mode": "cafe", "level": "A2", "correction": "immediate"})
        before = (self.conn.practice.mode, self.conn.practice.level,
                  self.conn.practice.correction, self.conn.practice.revision)
        for invalid in (None, [], "free", 3, {"mode": "invalid"}, {"level": "C9"},
                        {"correction": "always"}, {"unexpected": True},
                        {"mode": "interview", "level": "invalid"}):
            with self.subTest(config=invalid):
                ack = await self.config(invalid)
                self.assertIn("practice_error", ack)
                self.assertEqual((self.conn.practice.mode, self.conn.practice.level,
                                  self.conn.practice.correction, self.conn.practice.revision), before)
        self.ws.send("text", turn_id="survived", text="hello")
        await self.ws.wait("done", "survived")

    async def test_invalid_config_does_not_cancel_current_recording(self):
        await self.config({"mode": "free"})
        self.ws.send("start", turn_id="recording")
        await self.ws.wait("recording", "recording")
        ack = await self.config({"mode": "invalid"})
        self.assertIn("practice_error", ack)
        self.assertTrue(self.conn.current(self.conn.turn))
        self.assertTrue(self.conn.recording)
        self.assertFalse(any(e["type"] == "interrupted" for e in self.ws.events))

    async def install_stubborn(self):
        started, cancelled, release, returned = [asyncio.Event() for _ in range(4)]
        self.releases.append(release)
        callbacks = []

        async def stubborn(current):
            callbacks.append(current)
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            returned.set()
            yield {"type": "text_delta", "delta": "STALE"}
            yield {"type": "audio", "audio": b"STALE"}
            yield {"type": "done", "text": "STALE"}

        self.respond_events = stubborn
        self.ws.send("text", turn_id="old", text="hello")
        await asyncio.wait_for(started.wait(), 2)
        return cancelled, release, returned, callbacks

    async def test_barge_in_fences_cancellation_resistant_practice(self):
        await self.config({"mode": "free"})
        cancelled, release, returned, callbacks = await self.install_stubborn()
        old_task = self.conn.task
        self.ws.send("barge_in", turn_id="old")
        await self.ws.wait("playback_stopped", "old")
        await asyncio.wait_for(cancelled.wait(), 2)
        self.assertFalse(callbacks[0]())
        release.set()
        await asyncio.wait_for(old_task, 2)
        self.assertTrue(returned.is_set())
        self.assertFalse(any(e["type"] in ("text_delta", "audio", "done") for e in self.ws.events))

    async def test_config_cancels_old_turn_and_rejects_late_output(self):
        await self.config({"mode": "free"})
        cancelled, release, returned, callbacks = await self.install_stubborn()
        old_task = self.conn.task
        await self.config({"mode": "cafe"})
        await asyncio.wait_for(cancelled.wait(), 2)
        await self.ws.wait("interrupted", "old")
        await self.ws.wait("playback_stopped", "old")
        self.assertFalse(callbacks[0]())
        self.ws.send("start", turn_id="new")
        await self.ws.wait("recording", "new")
        release.set()
        await asyncio.wait_for(old_task, 2)
        self.assertTrue(returned.is_set())
        self.assertTrue(self.conn.current(self.conn.turn))
        self.assertEqual(self.conn.turn.turn_id, "new")
        self.assertFalse(any(e["type"] in ("text_delta", "audio", "done") for e in self.ws.events))

    async def test_config_cancels_existing_nonpractice_turn(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            yield {"type": "done"}

        self.env["orchestrator"].stream_respond = blocked
        self.ws.send("text", turn_id="ordinary-old", text="hello")
        await asyncio.wait_for(started.wait(), 2)
        await self.config({"mode": "free"})
        await asyncio.wait_for(cancelled.wait(), 2)
        await self.ws.wait("interrupted", "ordinary-old")
        self.assertFalse(self.conn.turn.live)

    async def test_proactive_check_blocked_while_practice_active_then_restored(self):
        await self.config({"mode": "free"})
        self.assertFalse(self.conn.busy)
        self.ws.send("proactive_check")
        await self.barrier()
        self.assertEqual(self.proactive_calls, [])
        self.assertIsNone(self.conn.task)
        await self.config({"mode": "off"})
        self.ws.send("proactive_check")
        started = await self.ws.wait("turn_started")
        await self.ws.wait("done", started["turn_id"])
        self.assertEqual(len(self.proactive_calls), 1)

    async def test_recoverable_error_continues_to_done(self):
        async def events(current):
            yield {"type": "error", "recoverable": True, "message": "audio unavailable"}
            yield {"type": "done", "text": "text still works"}
        self.respond_events = events
        await self.config({"mode": "free"})
        self.ws.send("text", turn_id="recoverable", text="hello")
        done = await self.ws.wait("done", "recoverable")
        error = await self.ws.wait("error", "recoverable")
        self.assertTrue(error["recoverable"])
        self.assertEqual(done["text"], "text still works")

    async def test_terminal_event_stops_provider_stream(self):
        for kind in ("done", "error"):
            with self.subTest(kind=kind):
                async def events(current):
                    yield {"type": kind, "text": "terminal"}
                    yield {"type": "text_delta", "delta": "AFTER TERMINAL"}
                self.respond_events = events
                await self.config({"mode": "free"})
                self.ws.send("text", turn_id=kind, text="hello")
                terminal = await self.ws.wait(kind, kind)
                if kind == "error":
                    self.assertTrue(terminal["terminal"])
                await self.barrier()
                self.assertFalse(self.conn.turn.live)
                self.assertFalse(any(e.get("delta") == "AFTER TERMINAL" for e in self.ws.events))

    async def test_voice_practice_uses_per_call_english_asr_then_restores_default(self):
        calls = []

        async def transcribe(wav, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="Coffee please", duration_ms=12)

        self.env["recognizer"] = SimpleNamespace(transcribe_wav=transcribe)
        await self.config({"mode": "cafe"})
        self.ws.send("start", turn_id="english-voice")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="english-voice")
        await self.ws.wait("done", "english-voice")
        self.assertEqual(calls, [{"language": "en"}])
        self.assertEqual(self.respond_calls[0][0], "Coffee please")
        self.assert_isolated()
        await self.config({"mode": "off"})
        self.ws.send("start", turn_id="ordinary-voice")
        self.ws.audio()
        self.ws.send("audio_end", turn_id="ordinary-voice")
        await self.ws.wait("done", "ordinary-voice")
        self.assertEqual(calls, [{"language": "en"}, {}])
        self.assertEqual(self.stream_calls[0]["user_text"], "Coffee please")

    async def test_real_practice_provider_emits_browser_compatible_text_delta(self):
        """Exercise the pure implementation too, with only in-memory providers."""
        replies = iter(("A coffee, please.", '{"items": [], "summary": "One turn."}'))

        async def complete(*args, **kwargs):
            return next(replies)

        class TTS:
            async def speak(self, text, **kwargs):
                yield SimpleNamespace(audio=b"real-contract", index=0, text=text, emotion="base")

        self.env["llm"] = SimpleNamespace(offline=False, complete=complete)
        import sys
        sys.modules["practice_ws_test.speech.tts"].tts = TTS()
        self.conn.practice = PracticeSession()
        await self.config({"mode": "cafe"})
        self.ws.send("text", turn_id="real-reply", text="I want coffee")
        await self.ws.wait("done", "real-reply")
        self.ws.send("practice_review", turn_id="real-review")
        await self.ws.wait("done", "real-review")
        for turn_id in ("real-reply", "real-review"):
            with self.subTest(turn_id=turn_id):
                delta = await self.ws.wait("text_delta", turn_id)
                self.assertTrue(delta.get("delta"), "Browser text_delta handler requires nonempty delta")
        self.assert_isolated()

    async def test_empty_provider_stream_gets_terminal_done(self):
        async def events(current):
            if False:
                yield {}
        self.respond_events = events
        await self.config({"mode": "free"})
        self.ws.send("text", turn_id="empty", text="hello")
        done = await self.ws.wait("done", "empty")
        self.assertEqual(done["text"], "")
        self.assertFalse(self.conn.turn.live)


if __name__ == "__main__":
    unittest.main(verbosity=2)
