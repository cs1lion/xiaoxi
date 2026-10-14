"""Offline actual-/ws experience contract: python scripts/test_experience_websocket.py.

AST-load real app definitions through the voice harness, never application bootstrap.
Pure sessions are imported by file; LLM, TTS, routing and persistence are local fakes.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from .test_voice_turns import FakeSocket, load_ws
except ImportError:
    from test_voice_turns import FakeSocket, load_ws


def load_session(filename, classname):
    path = Path(__file__).resolve().parents[1] / "server/speech" / filename
    spec = importlib.util.spec_from_file_location("isolated_" + classname, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, classname)


ExperienceSession = load_session("experience.py", "ExperienceSession")
PracticeSession = load_session("practice.py", "PracticeSession")
SOURCE = {"text": "Could you give me a hand?", "translation": "请帮我一下。",
          "label": "Explicit excerpt", "source_id": "excerpt-17"}
RAW_AUDIO = b"\x00\xffexperience-audio"


class ExperienceWebSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = load_ws()
        self.env["__package__"] = "experience_ws_test"
        self.respond_calls, self.practice_calls, self.stream_calls = [], [], []
        self.llm_calls, self.tts_calls, self.proactive_calls = [], [], []
        self.connections, self.releases, self.candidates = [], [], []
        self.store_message = Mock()
        self.route = Mock(return_value=False)
        owner = self

        class TestExperience(ExperienceSession):
            def __init__(self):
                super().__init__()
                owner.candidates.append(self)

            async def respond(self, text, llm, tts, is_current, *, voice_emotion=None):
                owner.respond_calls.append((text, llm, tts, is_current, self.snapshot()))
                async for event in owner.respond_events(is_current):
                    yield event

        class TestPractice(PracticeSession):
            async def respond(self, text, llm, tts, is_current):
                owner.practice_calls.append(text)
                yield {"type": "done", "text": "practice"}

        class TTS:
            offline = False

            async def speak(self, text, **kwargs):
                owner.tts_calls.append((text, kwargs))
                yield SimpleNamespace(audio=RAW_AUDIO, index=2, text=text, emotion="base")

        async def complete(messages, **kwargs):
            self.llm_calls.append((messages, kwargs))
            return "A source-grounded reply."

        async def stream(**kwargs):
            self.stream_calls.append(kwargs)
            yield {"type": "text_delta", "delta": "ordinary"}
            yield {"type": "done", "text": "ordinary"}

        async def proactive(**kwargs):
            self.proactive_calls.append(kwargs)
            return SimpleNamespace(text="proactive")

        async def assistant_start(text, session_id, history=None):
            return {"text": "assistant action"}

        original_connection = self.env["Connection"]

        def connection(ws):
            conn = original_connection(ws)
            self.connections.append(conn)
            return conn

        self.tts = TTS()
        self.env.update(Connection=connection, llm=SimpleNamespace(offline=False, complete=complete),
                        assistant=SimpleNamespace(should_route=self.route, start=assistant_start, waiting_input=lambda _: None),
                        store=SimpleNamespace(start_session=Mock(), end_session=Mock()),
                        orchestrator=SimpleNamespace(stream_respond=stream, proactive_turn=proactive,
                                                     state={"proactivity_enabled": False},
                                                     set_proactivity=Mock()))
        modules = {}
        for name, attrs in {
            "experience_ws_test.speech.experience": {"ExperienceSession": TestExperience},
            "experience_ws_test.speech.practice": {"PracticeSession": TestPractice},
            "experience_ws_test.speech.tts": {"tts": self.tts},
            "experience_ws_test.dialogue.orchestrator": {"store_message": self.store_message,
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

    async def respond_events(self, current):
        yield {"type": "thinking"}
        yield {"type": "text_delta", "delta": "experience reply"}
        yield {"type": "audio", "audio": RAW_AUDIO, "index": 2,
               "text": "experience reply", "emotion": "base", "revision": 7}
        yield {"type": "done", "text": "experience reply"}

    async def fresh(self, kind, start):
        async def find():
            while True:
                self.ws.changed.clear()
                for event in self.ws.events[start:]:
                    if event["type"] == kind:
                        return event
                await self.ws.changed.wait()
        return await asyncio.wait_for(find(), 2)

    async def config(self, **fields):
        start = len(self.ws.events)
        self.ws.send("config", **fields)
        return await self.fresh("config_ack", start)

    async def enable(self, mode="listen", **extra):
        return await self.config(experience={"mode": mode, **extra}, experience_source=SOURCE)

    def assert_isolated(self):
        self.route.assert_not_called()
        self.store_message.assert_not_called()
        self.assertEqual(self.stream_calls, [])
        self.assertEqual(self.proactive_calls, [])
        self.assertEqual(self.practice_calls, [])
        self.env["store"].start_session.assert_called_once_with(self.conn.session_id)

    async def test_ack_uses_session_snapshot_and_explicit_source_dict(self):
        ack = await self.enable("theatre", scene="A fictional bakery")
        self.assertEqual(ack["experience"], self.conn.experience.snapshot())
        self.assertEqual(ack["experience"]["source"], SOURCE)
        self.assertEqual(ack["experience"]["scene"], "A fictional bakery")
        self.assertEqual(ack["experience"]["mode"], "theatre")
        ack = await self.config(experience_source={"text": "A new explicit excerpt"})
        self.assertEqual(ack["experience"], self.conn.experience.snapshot())
        self.assertEqual(ack["experience"]["mode"], "theatre")
        self.assertEqual(ack["experience"]["source"]["translation"], "")

    async def test_experience_request_id_echoed_on_success_and_error(self):
        ack = await self.config(experience={"mode": "listen"}, experience_source=SOURCE,
                                experience_request_id="apply-source-1")
        self.assertEqual(ack["experience_request_id"], "apply-source-1")
        self.assertEqual(ack["experience"], self.conn.experience.snapshot())
        source = {**SOURCE, "text": "A new explicitly selected sentence."}
        boundary_id = "r" * 100
        ack = await self.config(experience_source=source, experience_request_id=boundary_id)
        self.assertEqual(ack["experience_request_id"], boundary_id)
        self.assertEqual(ack["experience"]["source"], source)
        before = self.conn.experience.snapshot()
        for fields in ({"experience": {"mode": "invalid"}},
                       {"experience_source": {"text": 7}},
                       {"experience": {"mode": "walk"}, "practice": {"mode": "free"}}):
            with self.subTest(fields=fields):
                ack = await self.config(**fields, experience_request_id="rejected-apply")
                self.assertIn("experience_error", ack)
                self.assertEqual(ack["experience_request_id"], "rejected-apply")
                self.assertEqual(self.conn.experience.snapshot(), before)

    async def test_nonexperience_settings_never_echo_experience_request_id(self):
        await self.enable()
        before = self.conn.experience.snapshot()
        for request_id in ("ordinary-settings", "", "r" * 101, None, 7, []):
            with self.subTest(request_id=request_id):
                ack = await self.config(memory=True, emotion="base",
                                        experience_request_id=request_id)
                self.assertNotIn("experience_request_id", ack)
                self.assertNotIn("experience_error", ack)
                self.assertEqual(ack["experience"], before)
                self.assertEqual(self.conn.experience.snapshot(), before)
        ack = await self.config(practice={"mode": "free"},
                                experience_request_id="practice-switch")
        self.assertNotIn("experience_request_id", ack)
        self.assertEqual(ack["practice"]["mode"], "free")
        self.assertEqual(ack["experience"]["mode"], "off")

    async def test_legacy_experience_configuration_remains_compatible_without_id(self):
        ack = await self.enable("listen")
        self.assertNotIn("experience_request_id", ack)
        self.assertEqual(ack["experience"]["source"], SOURCE)
        ack = await self.config(experience_source={**SOURCE, "text": "Legacy source update."})
        self.assertNotIn("experience_request_id", ack)
        self.assertEqual(ack["experience"]["source"]["text"], "Legacy source update.")
        before = self.conn.experience.snapshot()
        ack = await self.config(experience={"mode": "invalid"})
        self.assertIn("experience_error", ack)
        self.assertNotIn("experience_request_id", ack)
        self.assertEqual(self.conn.experience.snapshot(), before)

    async def test_invalid_experience_request_ids_are_atomic(self):
        await self.enable("theatre", scene="original scene")
        original = self.conn.experience
        before = original.snapshot()
        self.ws.send("start", turn_id="recording-with-request-id")
        await self.ws.wait("recording", "recording-with-request-id")
        self.env["manager"].enabled = True
        emotion_before = self.conn.emotion_override
        for request_id in ("", "r" * 101, None, False, 7, [], {}):
            for fields in ({"experience": {"mode": "walk", "scene": "changed"}},
                           {"experience_source": {**SOURCE, "text": "Rejected replacement."}}):
                with self.subTest(request_id=request_id, fields=fields):
                    ack = await self.config(**fields, experience_request_id=request_id,
                                            memory=False, proactivity=True, emotion="认真")
                    self.assertIn("experience_error", ack)
                    self.assertNotIn("experience_request_id", ack)
                    self.assertIs(self.conn.experience, original)
                    self.assertEqual(original.snapshot(), before)
                    self.assertTrue(self.conn.current(self.conn.turn))
                    self.assertTrue(self.conn.recording)
                    self.assertTrue(self.env["manager"].enabled)
                    self.assertEqual(self.conn.emotion_override, emotion_before)
                    self.env["orchestrator"].set_proactivity.assert_not_called()
        self.assertFalse(any(e["type"] == "interrupted" for e in self.ws.events))

    async def test_invalid_candidate_is_atomic_and_preserves_current_recording(self):
        await self.enable("theatre", scene="original scene")
        original = self.conn.experience
        before = original.snapshot()
        self.ws.send("start", turn_id="recording")
        await self.ws.wait("recording", "recording")
        invalid = [
            {"experience": None}, {"experience": []}, {"experience": "listen"},
            {"experience": {"mode": "invalid"}}, {"experience": {"scene": 7}},
            {"experience": {"scene": "x" * 1001}}, {"experience": {"unknown": True}},
            {"experience": {"mode": "walk", "scene": "changed"}, "experience_source": None},
            {"experience": {"mode": "walk"}, "experience_source": {"text": 7}},
            {"experience_source": {"text": "x" * 3001}},
            {"experience_source": {"translation": "no text"}},
            {"experience_source": {"text": "valid", "url": "not allowed"}},
            {"experience_source": {"text": "valid", "label": "x" * 201}},
            {"experience_source": {"text": "valid", "source_id": []}},
            {"experience_source": {"text": "valid", "translation": "x" * 3001}},
        ]
        for value in invalid:
            with self.subTest(config=value):
                count = len(self.candidates)
                ack = await self.config(**value)
                self.assertIn("experience_error", ack)
                self.assertEqual(len(self.candidates), count + 1, "Validate a temporary candidate")
                self.assertIs(self.conn.experience, original)
                self.assertEqual(original.snapshot(), before)
                self.assertTrue(self.conn.current(self.conn.turn))
                self.assertTrue(self.conn.recording)
        self.assertFalse(any(e["type"] == "interrupted" for e in self.ws.events))

    async def test_listen_and_expression_require_nonblank_source(self):
        for mode in ("listen", "expression"):
            for source in (None, {"text": ""}, {"text": " \t\n"}):
                with self.subTest(mode=mode, source=source):
                    fields = {"experience": {"mode": mode}}
                    if source is not None:
                        fields["experience_source"] = source
                    ack = await self.config(**fields)
                    self.assertIn("experience_error", ack)
                    self.assertTrue(getattr(self.conn, "experience", None) is None
                                    or self.conn.experience.mode == "off")
        ack = await self.enable("expression")
        self.assertEqual(ack["experience"]["mode"], "expression")

    async def test_joint_practice_and_experience_rejected_without_mutation(self):
        await self.config(practice={"mode": "cafe"})
        await self.config(experience={"mode": "off"})
        before = self.conn.experience.snapshot()
        for fields in ({"experience": {"mode": "walk"}, "practice": {"mode": "off"}},
                       {"experience_source": SOURCE, "practice": {"mode": "free"}}):
            ack = await self.config(**fields)
            self.assertIn("experience_error", ack)
            self.assertEqual(self.conn.practice.mode, "cafe")
            self.assertEqual(self.conn.experience.snapshot(), before)

    async def test_mode_switches_are_exclusive_both_directions(self):
        await self.config(practice={"mode": "cafe"})
        ack = await self.enable("expression")
        self.assertEqual(ack["practice"]["mode"], "off")
        self.assertEqual(self.conn.practice.mode, "off")
        ack = await self.config(practice={"mode": "free"})
        self.assertEqual(ack["experience"]["mode"], "off")
        self.assertEqual(self.conn.experience.mode, "off")
        self.ws.send("text", turn_id="practice", text="hello")
        await self.ws.wait("done", "practice")
        self.assertEqual(self.practice_calls, ["hello"])
        self.assertEqual(self.respond_calls, [])

    async def test_experience_precedes_assistant_and_isolated_from_normal_paths(self):
        await self.enable()
        self.route.side_effect = AssertionError("Experience must not consult tools/assistant routing")
        self.ws.send("text", turn_id="experience", text="open a file")
        await self.ws.wait("done", "experience")
        text, llm, tts, current, snapshot = self.respond_calls[0]
        self.assertEqual(text, "open a file")
        self.assertIs(llm, self.env["llm"])
        self.assertIs(tts, self.tts)
        self.assertEqual(snapshot["source"], SOURCE)
        self.assertFalse(current())
        self.assert_isolated()

    async def test_text_audio_encoded_once_and_done_is_terminal(self):
        await self.enable()
        start = len(self.ws.events)
        self.ws.send("text", turn_id="encoded", text="question")
        await self.ws.wait("done", "encoded")
        events = self.ws.events[start:]
        self.assertTrue(all(e["turn_id"] == "encoded" for e in events))
        self.assertEqual(sum(e["type"] == "done" for e in events), 1)
        self.assertEqual((await self.ws.wait("text_delta", "encoded"))["delta"], "experience reply")
        audio = await self.ws.wait("audio", "encoded")
        self.assertNotIn("audio", audio)
        self.assertEqual(base64.b64decode(audio["data"]), RAW_AUDIO)
        self.assertEqual((audio["index"], audio["revision"]), (2, 7))
        self.assertFalse(self.conn.turn.live)

    async def test_real_provider_receives_source_not_normal_store_or_tools(self):
        await self.enable("expression")
        snapshot = self.conn.experience.snapshot()
        self.conn.experience = ExperienceSession()
        self.conn.experience.configure(mode=snapshot["mode"], scene=snapshot["scene"])
        self.conn.experience.set_source(snapshot["source"])
        self.ws.send("text", turn_id="real", text="Can you help me with this box?")
        done = await self.ws.wait("done", "real")
        messages, kwargs = self.llm_calls[0]
        self.assertEqual(json.loads(messages[1]["content"].split("\n", 1)[1]), SOURCE)
        self.assertEqual(messages[-1]["content"], "Can you help me with this box?")
        self.assertNotIn("tools", kwargs)
        self.assertEqual(done["text"], "A source-grounded reply.")
        self.assertEqual((await self.ws.wait("text_delta", "real"))["delta"], done["text"])
        self.assertEqual(base64.b64decode((await self.ws.wait("audio", "real"))["data"]), RAW_AUDIO)
        self.assertEqual(self.tts_calls[0][0], done["text"])
        self.assert_isolated()

    async def test_neighbor_context_through_actual_configuration_and_provider(self):
        neighbor = {'text': 'The box belongs to Jane.', 'translation': '箱子是简的。', 'label': 'previous confirmed excerpt', 'source_id': 'neighbor-1'}
        selected = {**SOURCE, 'context': [neighbor]}
        ack = await self.config(experience={'mode': 'listen'}, experience_source=selected)
        self.assertEqual(ack['experience']['source'], selected)
        real = ExperienceSession()
        real.configure(mode='listen')
        real.set_source(self.conn.experience.source)
        self.conn.experience = real
        self.ws.send('text', turn_id='neighbors', text='Who owns it?')
        await self.ws.wait('done', 'neighbors')
        messages, _ = self.llm_calls[0]
        self.assertEqual(json.loads(messages[1]['content'].split('\n', 1)[1]), selected)
        self.assert_isolated()
        before = real.snapshot()
        rejected = await self.config(experience_source={**SOURCE, 'context': [neighbor] * 5})
        self.assertIn('experience_error', rejected)
        self.assertEqual(real.snapshot(), before)

    async def test_proactive_blocked_active_and_restored_off(self):
        await self.enable("walk")
        self.ws.send("proactive_check")
        await self.config()
        self.assertEqual(self.proactive_calls, [])
        self.assertIsNone(self.conn.task)
        await self.config(experience={"mode": "off"})
        self.ws.send("proactive_check")
        started = await self.ws.wait("turn_started")
        await self.ws.wait("done", started["turn_id"])
        self.assertEqual(len(self.proactive_calls), 1)

    async def test_mode_change_fences_cancellation_resistant_output(self):
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
        await self.enable("walk")
        self.ws.send("text", turn_id="old", text="hello")
        await asyncio.wait_for(started.wait(), 2)
        old_task = self.conn.task
        await self.enable("theatre", scene="New scene")
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

    async def test_terminal_done_error_and_empty_stream_do_not_leak_late_output(self):
        for kind in ("done", "error", "empty"):
            with self.subTest(kind=kind):
                async def events(current):
                    if kind != "empty":
                        yield {"type": kind, "text": "terminal"}
                        yield {"type": "text_delta", "delta": "AFTER TERMINAL"}
                self.respond_events = events
                await self.enable("walk")
                self.ws.send("text", turn_id=kind, text="hello")
                terminal = await self.ws.wait("done" if kind == "empty" else kind, kind)
                if kind == "error":
                    self.assertTrue(terminal["terminal"])
                await self.config()
                self.assertFalse(self.conn.turn.live)
                self.assertFalse(any(e.get("delta") == "AFTER TERMINAL" for e in self.ws.events))

    async def test_recoverable_error_continues_to_done(self):
        async def events(current):
            yield {"type": "error", "recoverable": True, "message": "audio unavailable"}
            yield {"type": "done", "text": "text available"}
        self.respond_events = events
        await self.enable("walk")
        self.ws.send("text", turn_id="recoverable", text="hello")
        self.assertEqual((await self.ws.wait("done", "recoverable"))["text"], "text available")
        self.assertTrue((await self.ws.wait("error", "recoverable"))["recoverable"])

    async def test_initial_and_explicit_off_preserve_normal_orchestrator(self):
        for turn_id in ("initial", "off"):
            if turn_id == "off":
                await self.enable("walk")
                await self.config(experience={"mode": "off"})
            self.ws.send("text", turn_id=turn_id, text="ordinary hello")
            self.assertEqual((await self.ws.wait("done", turn_id))["text"], "ordinary")
        self.assertEqual(len(self.stream_calls), 2)
        self.assertEqual(self.respond_calls, [])
        self.assertEqual(self.route.call_count, 2)

    async def test_initial_and_explicit_off_preserve_assistant_storage(self):
        self.route.return_value = True
        for turn_id in ("initial-action", "off-action"):
            if turn_id == "off-action":
                await self.config(experience={"mode": "off"})
            self.ws.send("text", turn_id=turn_id, text="open a file")
            await self.ws.wait("done", turn_id)
            await self.ws.wait("assistant_result", turn_id)
        self.assertEqual(self.store_message.call_count, 4)
        self.assertEqual(self.respond_calls, [])
        self.assertEqual(self.stream_calls, [])

    async def test_expression_asr_english_override_only_then_normal_restored(self):
        calls = []

        async def transcribe(wav, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text="Please give me a hand", duration_ms=12)

        self.env["recognizer"] = SimpleNamespace(transcribe_wav=transcribe)
        for index, mode in enumerate((None, "expression", "listen", "walk", "off")):
            if mode is not None:
                await self.enable(mode)
            turn_id = "voice-" + str(index)
            self.ws.send("start", turn_id=turn_id)
            self.ws.audio()
            self.ws.send("audio_end", turn_id=turn_id)
            await self.ws.wait("done", turn_id)
        self.assertEqual(calls, [{}, {"language": "en"}, {}, {}, {}])
        self.assertEqual(len(self.respond_calls), 3)
        self.assertEqual(len(self.stream_calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
