"""F2 micro-sentence streaming + bounded preload contracts (offline, fake TTS).

Covers, without ever touching a real TTS/LLM provider:
  * lossless micro-split boundaries (punctuation / quotes / overlong single sentence)
  * audio.index contiguous and chunk.seq monotonic (with a reorder buffer)
  * bounded preload concurrency
  * a failed segment is skipped without stalling or breaking monotonicity
  * barge-in cancels in-flight synthesis and emits zero late audio
  * feature-off (speech.enabled=false) keeps the legacy contract: no chunk,
    no speech_plan, contiguous index, lossless text.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import dotenv

os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(dotenv, 'load_dotenv', return_value=False):
    from server.config import settings
    settings.db_path = ":memory:"
    mod = importlib.import_module("server.dialogue.orchestrator")
    from server.speech.tts import micro_pause, split_for_speech_micro
    from server import persona as persona_mod
    from server.memory import diary as diary_mod
    from server.memory import evolution as evolution_mod


DEFAULT_SPEECH = {
    "enabled": True,
    "micro_chunk": {"target_chars": 20, "max_chars": 60},
    "preload": {"concurrency": 2},
    "pause": {"clause_ms": 120, "sentence_ms": 260, "max_ms": 400},
}


class MicroDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orch = mod.Orchestrator()
        self.orch.pacing = copy.deepcopy(self.orch.pacing)
        self.orch.pacing["length"].update(
            default="short", short_max_chars=400, medium_max_chars=400, hard_limit_chars=400)
        self.saved = []
        self.policy = "detailed"
        old_enabled = mod.manager.enabled
        mod.manager.enabled = False
        self.addCleanup(setattr, mod.manager, "enabled", old_enabled)
        real_build = self.orch.context_broker.build

        def bundle(*args, **kwargs):
            result = real_build(*args, **kwargs)
            result['response_policy']['length'] = self.policy
            return result

        for patcher in (
            patch.object(mod, "store_recent", return_value=[]),
            patch.object(mod, "store_message", side_effect=lambda *args: self.saved.append(args)),
            patch.object(self.orch.context_broker, "build", side_effect=bundle),
            patch.object(self.orch, "_spawn_extraction"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _speech(self, **over):
        cfg = copy.deepcopy(DEFAULT_SPEECH)
        for key, value in over.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
        self.orch.pacing["speech"] = cfg
        return cfg

    async def _run(self, deltas, synthesize, *, source=None, voice_emotion=None):
        async def default_source(*a, **k):
            for d in deltas:
                yield d
                await asyncio.sleep(0)

        with patch.object(mod.llm, "stream", source or default_source), \
                patch.object(mod.tts, "synthesize", synthesize):
            return [ev async for ev in self.orch.stream_respond(
                session_id="t", user_text="你好", voice_emotion=voice_emotion)]

    @staticmethod
    async def _ok_synth(text, emotion):
        return b"a:" + text.encode("utf-8")

    # ---- pure splitter / pause units ----
    def test_split_micro_lossless_and_bounded(self):
        cases = [
            "这是第一句话，这是第二句话，还有第三句话在这里收尾。",
            "他说“你好，世界。”然后转身走了，再也没有回头。",
            "字" * 80,                                   # overlong single sentence, no boundary
            "短句。",
            "A，B，C，D，E，F，G，H，I，J，K，L，M，N，O，P。",
            "",
        ]
        for text in cases:
            pieces = split_for_speech_micro(text, 20, 60)
            self.assertEqual("".join(pieces), text, repr(text))   # lossless
            for p in pieces:
                self.assertLessEqual(len(p), 60, (repr(text), p))
                self.assertTrue(p)

    def test_split_micro_prefers_boundaries(self):
        self.assertEqual(
            split_for_speech_micro("第一段，第二段，第三段，第四段。", 4, 60),
            ["第一段，", "第二段，", "第三段，", "第四段。"])
        # A closing quote stays attached to the sentence it closes.
        pieces = split_for_speech_micro("他说“你好。”走了。", 7, 60)
        self.assertEqual("".join(pieces), "他说“你好。”走了。")
        self.assertTrue(any(p.endswith("”") for p in pieces))

    def test_micro_pause_boundary_mapping(self):
        self.assertEqual(micro_pause("结束了。", clause_ms=120, sentence_ms=260, max_ms=400), (260, True))
        self.assertEqual(micro_pause("停顿，", clause_ms=120, sentence_ms=260, max_ms=400), (120, False))
        self.assertEqual(micro_pause("半句", clause_ms=120, sentence_ms=260, max_ms=400), (0, False))
        self.assertEqual(micro_pause("引号里的。”", clause_ms=120, sentence_ms=260, max_ms=400), (260, True))
        self.assertEqual(micro_pause("。", clause_ms=999, sentence_ms=999, max_ms=400), (400, True))

    # ---- integration: micro on ----
    async def test_micro_audio_chunks_monotonic_and_plan(self):
        self._speech()
        reply = "这是第一句比较长的内容，接着是第二句，然后第三句收尾。"
        events = await self._run(["[base]" + reply], self._ok_synth)
        audio = [e for e in events if e["type"] == "audio"]
        self.assertGreaterEqual(len(audio), 2)
        self.assertEqual([e["index"] for e in audio], list(range(len(audio))))
        seqs = [e["chunk"]["seq"] for e in audio]
        self.assertEqual(seqs, list(range(len(audio))))          # contiguous seq (no failures)
        self.assertEqual("".join(e["text"] for e in audio), reply)  # lossless
        for e in audio:
            ch = e["chunk"]
            self.assertEqual(set(ch), {"seq", "of", "chars", "pause_after_ms", "breath"})
            self.assertIsNone(ch["of"])
            self.assertEqual(ch["chars"], len(e["text"]))
            self.assertTrue(0 <= ch["pause_after_ms"] <= 400)
            self.assertIsInstance(ch["breath"], bool)
        self.assertTrue(any(e["chunk"]["breath"] for e in audio))
        self.assertTrue(any(e["chunk"]["pause_after_ms"] == 260 for e in audio))
        types = [e["type"] for e in events]
        self.assertIn("speech_plan", types)
        self.assertLess(types.index("emotion"), types.index("speech_plan"))
        self.assertLess(types.index("speech_plan"), types.index("audio"))
        plan = next(e for e in events if e["type"] == "speech_plan")
        self.assertEqual(plan["mode"], "micro")
        self.assertIsNone(plan["expected_chunks"])
        self.assertEqual(plan["first_chunk_chars"], len(audio[0]["text"]))

    async def test_preload_concurrency_is_bounded(self):
        self._speech(micro_chunk={"target_chars": 4, "max_chars": 60})
        state = {"cur": 0, "max": 0}

        async def synthesize(text, emotion):
            state["cur"] += 1
            state["max"] = max(state["max"], state["cur"])
            try:
                await asyncio.sleep(0.02)
                return b"a"
            finally:
                state["cur"] -= 1

        reply = "一，二，三，四，五，六，七，八，九，十。"
        events = await self._run(["[base]" + reply], synthesize)
        audio = [e for e in events if e["type"] == "audio"]
        self.assertGreaterEqual(len(audio), 3)
        self.assertEqual(state["max"], 2)   # preloaded, but never above concurrency

    async def test_failure_skips_segment_keeps_monotonic(self):
        self._speech(micro_chunk={"target_chars": 4, "max_chars": 60})
        calls = {"n": 0}

        async def synthesize(text, emotion):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("offline TTS failure")
            return b"a:" + text.encode("utf-8")

        reply = "一，二，三，四，五。"
        events = await self._run(["[base]" + reply], synthesize)
        audio = [e for e in events if e["type"] == "audio"]
        texts = [e["text"] for e in audio]
        self.assertNotIn("二，", texts)                       # failed segment skipped
        self.assertEqual([e["index"] for e in audio], list(range(len(audio))))  # index contiguous
        seqs = [e["chunk"]["seq"] for e in audio]
        self.assertEqual(seqs, sorted(seqs))                 # seq monotonic
        self.assertEqual(len(set(seqs)), len(seqs))
        self.assertIn(1, [s for s in range(5) if s not in seqs])  # the failed seq left a gap
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertGreaterEqual(done["delivery"]["audio_failed_chunks"], 1)
        self.assertEqual(done["delivery"]["audio_succeeded_chunks"], len(audio))
        self.assertTrue(all(e.get("recoverable") for e in events if e["type"] == "error"))

    async def test_barge_in_cancels_synthesis_zero_late_audio(self):
        self._speech(micro_chunk={"target_chars": 4, "max_chars": 60})
        call = {"n": 0}
        later_reclaimed = asyncio.Event()

        async def synthesize(text, emotion):
            call["n"] += 1
            if call["n"] == 1:
                return b"a0"
            try:
                await asyncio.Event().wait()
            finally:
                later_reclaimed.set()
            return b"ax"

        reply = "一，二，三，四，五，六。"

        async def source(*a, **k):
            yield "[base]" + reply
            await asyncio.Event().wait()

        collected = []
        expected = split_for_speech_micro(reply, 4, 60)
        self.assertGreater(len(expected), 1)
        with patch.object(mod.llm, "stream", source), \
                patch.object(mod.tts, "synthesize", synthesize):
            stream = self.orch.stream_respond(session_id="t", user_text="你好")
            async for ev in stream:
                collected.append(ev)
                if ev["type"] == "audio":
                    break
            await asyncio.wait_for(stream.aclose(), 2)
        self.assertTrue(later_reclaimed.is_set())            # in-flight synthesis cancelled
        # Only the first segment played; every later in-flight synthesis was
        # cancelled, so no late audio escapes after the interrupt.
        self.assertEqual([e["text"] for e in collected if e["type"] == "audio"], expected[:1])

    async def test_feature_off_is_legacy_identical(self):
        self.orch.pacing.pop("speech", None)                 # default: feature off
        reply = "字" * 130                                    # -> three legacy 60-char windows
        events = await self._run(["[base]" + reply], self._ok_synth)
        self.assertNotIn("speech_plan", [e["type"] for e in events])
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual(len(audio), 3)
        for e in audio:
            self.assertNotIn("chunk", e)                     # no chunk field when feature off
            self.assertLessEqual(len(e["text"]), 60)
        self.assertEqual([e["index"] for e in audio], [0, 1, 2])
        self.assertEqual("".join(e["text"] for e in audio), reply)

    # ---- F1 bias_block integration + F3 self_topic_turn ----
    def _enable_proactivity(self):
        self.orch.pacing["proactivity"].update(
            default_enabled=True, min_interval_seconds=0,
            only_when_user_recently_idle_seconds=0, max_per_day=99)
        self.orch.state.update(
            proactivity_enabled=True, proactive_suppressed_until=0.0,
            last_proactive_at=0.0, proactive_count_today=0)

    @staticmethod
    def _minimal_turn_data():
        return {
            'response_policy': {},
            'long_term_memory': {'enabled': False, 'status': 'disabled',
                                 'stated': [], 'inferred': [], 'uncertain': []},
            'state': {'current_state': '', 'user_state_note': '', 'proactivity': ''},
            'history': [],
        }

    def test_bias_block_empty_when_memory_disabled(self):
        mod.manager.enabled = False
        with patch.object(mod, "dynamic_prompt", wraps=mod.dynamic_prompt) as spy, \
                patch.object(persona_mod, "bias_block") as bias_spy:
            self.orch._render_turn_context(self._minimal_turn_data())
        self.assertEqual(spy.call_args.kwargs.get("bias_block"), "")  # memory off -> no injection
        bias_spy.assert_not_called()

    def test_bias_block_empty_when_no_drifts_or_sparks(self):
        mod.manager.enabled = True
        with patch.object(mod, "dynamic_prompt", wraps=mod.dynamic_prompt) as spy, \
                patch.object(diary_mod, "active_drifts", lambda *a, **k: []), \
                patch.object(diary_mod, "surfaced_sparks", lambda *a, **k: []):
            self.orch._render_turn_context(self._minimal_turn_data())
        # Empty bias == the default slot, so the dynamic prompt is byte-identical.
        self.assertEqual(spy.call_args.kwargs.get("bias_block"), "")

    def test_bias_block_injected_when_memory_on(self):
        mod.manager.enabled = True
        with patch.object(mod, "dynamic_prompt", wraps=mod.dynamic_prompt) as spy, \
                patch.object(diary_mod, "active_drifts",
                             lambda *a, **k: [{"topic": "下雨天", "attitude": "喜欢安静", "weight": 0.8}]), \
                patch.object(diary_mod, "surfaced_sparks",
                             lambda *a, **k: [{"id": 1, "state": "surfaced", "text": "想写一首关于雨的歌"}]):
            self.orch._render_turn_context(self._minimal_turn_data())
        block = spy.call_args.kwargs.get("bias_block", "")
        self.assertIn("下雨天", block)
        self.assertIn("想写一首关于雨的歌", block)

    async def test_self_topic_none_when_unauthorized(self):
        mod.manager.enabled = True
        self.orch.state["proactivity_enabled"] = False
        self.orch.pacing["proactivity"]["default_enabled"] = False
        self.assertIsNone(await self.orch.self_topic_turn(session_id="t", idle_seconds=10_000))

    async def test_self_topic_none_when_not_idle(self):
        mod.manager.enabled = True
        self._enable_proactivity()
        self.orch.pacing["proactivity"]["only_when_user_recently_idle_seconds"] = 3600
        self.assertIsNone(await self.orch.self_topic_turn(session_id="t", idle_seconds=10))

    async def test_self_topic_none_when_self_evolution_disabled(self):
        mod.manager.enabled = True
        self._enable_proactivity()
        with patch.object(mod, "memory_cfg", lambda: {}):   # missing self_evolution -> off
            self.assertIsNone(await self.orch.self_topic_turn(session_id="t", idle_seconds=10_000))
        with patch.object(mod, "memory_cfg", lambda: {"self_evolution": {"enabled": False}}):
            self.assertIsNone(await self.orch.self_topic_turn(session_id="t", idle_seconds=10_000))

    async def test_self_topic_returns_spark_id(self):
        mod.manager.enabled = True
        self._enable_proactivity()

        async def fake_opening(state, sparks=None, *, provider=None):
            return "今天忽然想聊聊那片星空。"

        with patch.object(mod, "memory_cfg", lambda: {"self_evolution": {"enabled": True}}), \
                patch.object(diary_mod, "surfaced_sparks",
                             lambda *a, **k: [{"id": 7, "state": "surfaced", "text": "星夜"}]), \
                patch.object(evolution_mod, "load_self_state",
                             lambda *a, **k: {"taste": [], "worldview": [], "curiosity_seeds": []}), \
                patch.object(evolution_mod, "self_topic_opening", fake_opening):
            result = await self.orch.self_topic_turn(session_id="t", idle_seconds=10_000)
        self.assertIsNotNone(result)
        self.assertEqual(result.text, "今天忽然想聊聊那片星空。")
        self.assertEqual(result.spark_id, 7)
        self.assertTrue(result.proactive)


if __name__ == "__main__":
    unittest.main()
