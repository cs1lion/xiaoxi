"""Offline semantic motion / provider timing contracts.

Run: .venv/Scripts/python.exe -B scripts/test_speech_performance.py
No model, audio service, credentials, or persistent database are used.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.config import settings
settings.db_path = ":memory:"
mod = importlib.import_module("server.dialogue.orchestrator")
from server.dialogue.performance import (MotionMetadataFilter, performance_plan,
                                         slice_performance, strip_motion_metadata)
from server.speech.tts import TTSEngine


def marker(intent="self_reference", anchor="我觉得", strength=0.4):
    return "[motion:" + json.dumps({"intent": intent, "anchor": anchor, "strength": strength},
                                    ensure_ascii=False) + "]"


class MetadataTests(unittest.TestCase):
    def test_only_reserved_namespace_removed_including_json_brackets_and_escapes(self):
        raw = '【状态】坐标(1, 2)[motion] ' + marker("explanation", '括号[值]和"引号"') + '正文。'
        self.assertEqual(strip_motion_metadata(raw), '【状态】坐标(1, 2)[motion] 正文。')
        self.assertEqual(strip_motion_metadata("[Motion:普通事实]"), "[Motion:普通事实]")

    def test_malformed_and_incomplete_recognized_blocks_do_not_leak(self):
        for suffix in ('[motion:bad]', '[motion:{bad}]', '[motion:', '[moti',
                       '[motion:{"intent":"self_reference"', '[motion:{"anchor":"unclosed]尾巴'):
            with self.subTest(suffix=suffix):
                self.assertEqual(strip_motion_metadata("正文。" + suffix), "正文。")
        self.assertEqual(strip_motion_metadata("[备注"), "[备注")

    def test_invalid_intents_unknown_fields_anchors_and_finite_strength(self):
        text = "𠮷我觉得，可以试试。"
        self.assertEqual(performance_plan(text, [{"intent": "script", "anchor": "我觉得"}])["source"], "fallback")
        self.assertEqual(performance_plan(text, [{"intent": "self_reference", "anchor": "我认为"}])["cues"], [])
        self.assertEqual(performance_plan(text, [{"intent": "self_reference", "anchor": "我觉得", "bones": {}}])["cues"], [])
        for strength, expected in ((-3, 0.15), (5, 0.75), (True, 0.4), (float("nan"), 0.4), ("0.2", 0.4)):
            cue = performance_plan(text, [{"intent": "self_reference", "anchor": "我觉得", "strength": strength}])["cues"][0]
            self.assertEqual((cue["start_char"], cue["end_char"], cue["strength"]), (1, 4, expected))
            self.assertEqual(text[cue["start_char"]:cue["end_char"]], cue["anchor"])

    def test_neutral_is_authoritative_and_cues_bounded_to_two(self):
        self.assertEqual(performance_plan("你好我觉得", [{"intent": "neutral"}]),
                         {"version": 1, "source": "llm", "cues": []})
        annotations = [{"intent": intent, "anchor": "正文"} for intent in
                       ("greeting", "question", "disagreement", "explanation")]
        self.assertEqual(len(performance_plan("正文", annotations)["cues"]), 2)

    def test_cross_chunk_anchors_omitted_without_keyword_fallback(self):
        text = "字" * 59 + "我觉得可以试试。"
        plan = performance_plan(text, [{"intent": "self_reference", "anchor": "我觉得"}])
        self.assertEqual(slice_performance(plan, 0, 60), {"version": 1, "source": "llm", "cues": []})
        self.assertEqual(slice_performance(plan, 60, len(text)), {"version": 1, "source": "llm", "cues": []})

    def test_reserved_buffer_is_bounded(self):
        parser = MotionMetadataFilter()
        raw = '[motion:{"anchor":"' + "字" * 10000 + '"}]正文。'
        out = []
        for char in raw:
            out.extend(value for kind, value in parser.feed(char) if kind == "text")
            self.assertLessEqual(len(parser._body), parser.MAX_METADATA_CHARS)
        self.assertEqual("".join(out), "正文。")
        self.assertEqual(strip_motion_metadata('[motion:' + '[' * 1200 + '0' + ']' * 1200 + ']正文。'), '正文。')


class SpeechPerformanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orch = mod.Orchestrator()
        self.orch.pacing = copy.deepcopy(self.orch.pacing)
        self.orch.pacing["length"].update(default="short", short_max_chars=300,
                                         medium_max_chars=300, hard_limit_chars=300)
        self.saved, self.tts_calls = [], []
        old_enabled = mod.manager.enabled
        mod.manager.enabled = False
        self.addCleanup(setattr, mod.manager, "enabled", old_enabled)

        def strategy(user_text, session_id):
            self.orch.session_state(session_id)["response_policy"] = {"length": "detailed"}
            return "test"
        for patcher in (patch.object(mod, "store_recent", return_value=[]),
                        patch.object(mod, "store_message", side_effect=lambda *args: self.saved.append(args)),
                        patch.object(self.orch, "_response_strategy", side_effect=strategy),
                        patch.object(self.orch, "_spawn_extraction")):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def collect(self, deltas, *, failure=False, tts_failure=None, timings=None):
        self.tts_calls.clear()
        self.generated_messages = []
        async def stream(*args, **kwargs):
            self.generated_messages.append(args[0])
            for delta in deltas:
                yield delta
                await asyncio.sleep(0)
            if failure:
                raise RuntimeError("offline generation interrupted")
        async def synthesize(text, emotion):
            self.tts_calls.append((text, emotion))
            if len(self.tts_calls) == tts_failure:
                raise RuntimeError("offline speech unavailable")
            return b"audio"
        with patch.object(mod.llm, "stream", stream), patch.object(mod.tts, "synthesize", synthesize), \
                patch.object(mod.tts, "word_timings", return_value=timings or []):
            async def run():
                return [e async for e in self.orch.stream_respond(session_id="test", user_text="详细解释")]
            events = await asyncio.wait_for(run(), 2)
        done = events[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["text"], "".join(e["delta"] for e in events if e["type"] == "text_delta"))
        self.assertEqual(done["text"], "".join(text for text, _ in self.tts_calls))
        self.assertEqual(done["text"], self.saved[-1][2])
        self.assertNotIn("motion:", str(events))
        for event in events:
            if event["type"] == "audio":
                for cue in event["performance"]["cues"]:
                    self.assertEqual(event["text"][cue["start_char"]:cue["end_char"]], cue["anchor"])
        return events

    async def test_actual_messages_require_declarations_and_allow_neutral_without_forcing_gesture(self):
        await self.collect(['[base][motion:{"intent":"neutral"}]先喘口气。'])
        self.assertEqual(len(self.generated_messages), 1)
        messages = self.generated_messages[0]
        instruction = "\n".join(message["content"] for message in messages if message["role"] == "system")
        self.assertIn("每句口头文本之前必须输出 1 到 2 条", instruction)
        self.assertIn("包括只有一句话的简短回复", instruction)
        self.assertIn("情绪标签仍必须放在整条回复最前面", instruction)
        self.assertIn('必须输出 [motion:{"intent":"neutral"}]', instruction)
        self.assertIn("别为每句话硬加手势", instruction)
        self.assertIn("neutral 表示不做手势，不需要 anchor 或 strength", instruction)
        self.assertEqual(messages[-1], {"role": "user", "content": "详细解释"})

    async def test_every_split_of_metadata_and_emotion_matches_sanitized_unicode_text(self):
        raw = "[认真]" + marker() + "**𠮷**我觉得，这个办法可以试试。"
        for cut in range(1, len(raw)):
            with self.subTest(cut=cut):
                events = await self.collect([raw[:cut], raw[cut:]])
                audio = next(e for e in events if e["type"] == "audio")
                self.assertEqual(audio["text"], "𠮷我觉得，这个办法可以试试。")
                self.assertEqual(audio["emotion"], "认真")
                self.assertEqual(audio["performance"], {"version": 1, "source": "llm", "cues": [
                    {"intent": "self_reference", "gesture": "self", "anchor": "我觉得",
                     "start_char": 1, "end_char": 4, "strength": 0.4}]})

    async def test_per_sentence_semantics_two_intents_and_neutral_not_leaked(self):
        raw = ("[base]" + marker("greeting", "你好") + "你好。" + marker("self_reference", "我觉得") +
               marker("explanation", "试试") + "我觉得可以试试。" + '[motion:{"intent":"neutral"}]再见。')
        events = await self.collect(list(raw))
        plans = [e["performance"] for e in events if e["type"] == "audio"]
        self.assertEqual([len(p["cues"]) for p in plans], [1, 2, 0])
        self.assertEqual([p["source"] for p in plans], ["llm"] * 3)
        self.assertEqual([c["gesture"] for c in plans[1]["cues"]], ["self", "palm"])

    async def test_malformed_fallback_and_factual_brackets_preserved(self):
        raw = '[base][motion:{bad}]模板：【实际状态】；坐标(1, 2)[motion]。'
        events = await self.collect(list(raw))
        self.assertEqual(events[-1]["text"], '模板：【实际状态】；坐标(1, 2)[motion]。')
        self.assertEqual(next(e for e in events if e["type"] == "audio")["performance"]["source"], "fallback")

    async def test_truncation_and_sixty_character_chunks_rebase_valid_cues(self):
        body = "字" * 65 + "我觉得可以试试。"
        events = await self.collect([marker() + body])
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual([len(e["text"]) for e in audio], [60, len(body) - 60])
        self.assertEqual(audio[0]["performance"]["cues"], [])
        self.assertEqual(audio[1]["performance"]["cues"][0]["start_char"], 5)
        self.orch.pacing["length"]["hard_limit_chars"] = 68
        events = await self.collect([marker() + body])
        self.assertEqual(events[-1]["text"], body[:67] + "。")
        self.assertTrue(all(e["performance"]["source"] == "fallback" for e in events if e["type"] == "audio"))

    async def test_failure_and_truncated_metadata_never_reaches_text_or_audio(self):
        for tail in ('[motion:', '[motion:{"intent":"self_reference","anchor":"', '[moti'):
            for failure in (False, True):
                with self.subTest(tail=tail, failure=failure):
                    events = await self.collect([marker("explanation", "正文") + "正文。尾巴" + tail], failure=failure)
                    self.assertEqual(events[-1]["text"], "正文。尾巴")
        events = await self.collect([marker("greeting", "你好") + "你好。" + marker() + "我觉得可行。"], tts_failure=1)
        audio = next(e for e in events if e["type"] == "audio")
        self.assertEqual(audio["performance"]["cues"][0]["intent"], "self_reference")
        self.assertEqual(audio["index"], 0)

    async def test_ellipsis_sentence_metadata_does_not_move_to_previous_sentence(self):
        events = await self.collect(list("[base]等等……" + marker() + "我觉得可以试试。"))
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual(audio[0]["performance"]["source"], "fallback")
        self.assertEqual(audio[1]["performance"]["cues"][0]["anchor"], "我觉得")

    async def test_actual_optional_timings_forwarded_only_when_available(self):
        timing = [{"text": "我觉得", "start": 0.1, "end": 0.7}]
        events = await self.collect([marker() + "我觉得可行。"], timings=timing)
        self.assertEqual(next(e for e in events if e["type"] == "audio")["word_timings"], timing)
        events = await self.collect(["没有提供时间戳。"])
        self.assertNotIn("word_timings", next(e for e in events if e["type"] == "audio"))

    async def test_semantic_first_audio_does_not_wait_for_reply_end(self):
        release = asyncio.Event()
        async def stream(*args, **kwargs):
            yield "[base]" + marker() + "我觉得可行。"
            await release.wait()
            yield '[motion:{"intent":"neutral"}]继续。'
        async def synthesize(text, emotion):
            release.set()
            return b"audio"
        with patch.object(mod.llm, "stream", stream), patch.object(mod.tts, "synthesize", synthesize), \
                patch.object(mod.tts, "word_timings", return_value=[]):
            async def run():
                return [e async for e in self.orch.stream_respond(session_id="test", user_text="详细解释")]
            events = await asyncio.wait_for(run(), 2)
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual([e["text"] for e in audio], ["我觉得可行。", "继续。"])
        self.assertEqual([len(e["performance"]["cues"]) for e in audio], [1, 0])

    async def test_cancellation_with_motion_metadata_reclaims_providers_without_metadata_persistence(self):
        llm_closed, tts_closed, tts_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def stream(*args, **kwargs):
            try:
                yield "[base]" + marker() + "我觉得可行。[motion:{\"intent\":"
                await asyncio.Event().wait()
            finally:
                llm_closed.set()
        async def synthesize(*args):
            tts_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                tts_closed.set()
        with patch.object(mod.llm, "stream", stream), patch.object(mod.tts, "synthesize", synthesize):
            async def run():
                return [e async for e in self.orch.stream_respond(session_id="test", user_text="详细解释")]
            running = asyncio.create_task(run())
            await asyncio.wait_for(tts_started.wait(), 2)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running
        self.assertTrue(llm_closed.is_set())
        self.assertTrue(tts_closed.is_set())
        self.assertFalse(any(row[1] == "assistant" for row in self.saved))
        self.assertNotIn("motion:", str(self.saved))

    async def test_plain_response_removes_complete_and_partial_metadata_without_extra_call(self):
        raw = {"text": "[认真]" + marker() + "我觉得可以试试。[motion:{bad}]尾巴[moti",
               "finish_reason": "stop"}
        complete = AsyncMock(return_value=raw)
        with patch.object(mod.llm, "complete_raw", complete), patch.object(mod.llm, "complete", AsyncMock()) as retry:
            reply = await self.orch.respond(session_id="test", user_text="详细解释")
        self.assertEqual(reply.text, "我觉得可以试试。尾巴")
        self.assertEqual(self.saved[-1][2], reply.text)
        complete.assert_awaited_once()
        retry.assert_not_awaited()


class ProviderTimingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_edge_interface_still_captures_provider_word_boundaries(self):
        class LegacyCommunicate:
            def __init__(self, text, *, voice, rate, pitch, volume):
                pass
            async def stream(self):
                yield {"type": "WordBoundary", "text": "你好", "offset": 0, "duration": 2_000_000}
                yield {"type": "audio", "data": b"legacy"}
        engine = TTSEngine()
        engine.engine = "edge_tts"
        engine.profile = lambda emotion: {"voice": "zh-CN-XiaoxiaoNeural"}
        with patch.dict(sys.modules, {"edge_tts": SimpleNamespace(Communicate=LegacyCommunicate)}):
            self.assertEqual(await engine.synthesize("你好"), b"legacy")
            self.assertEqual(engine.word_timings("你好"), [{"text": "你好", "start": 0.0, "end": 0.2}])

    async def test_edge_ticks_seconds_bytes_cache_and_profile_keys(self):
        calls = []
        class Communicate:
            def __init__(self, text, **kwargs):
                calls.append((text, kwargs))
            async def stream(self):
                yield {"type": "WordBoundary", "text": "我觉得", "offset": 1_000_000, "duration": 6_000_000}
                yield {"type": "WordBoundary", "text": "bad", "offset": -1, "duration": 3}
                yield {"type": "WordBoundary", "text": "bad", "offset": "nan", "duration": 3}
                yield {"type": "audio", "data": b"mp3"}
        engine = TTSEngine()
        engine.engine = "edge_tts"
        engine.profile = lambda emotion: {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%" if emotion == "base" else "+3%"}
        with patch.dict(sys.modules, {"edge_tts": SimpleNamespace(Communicate=Communicate)}):
            self.assertEqual(await engine.synthesize(" 我觉得 ", "base"), b"mp3")
            self.assertEqual(await engine.synthesize("我觉得", "base"), b"mp3")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]["boundary"], "WordBoundary")
            self.assertEqual(engine.word_timings("我觉得", "base"), [{"text": "我觉得", "start": 0.1, "end": 0.7}])
            self.assertEqual(engine.word_timings("我觉得", "认真"), [])
            engine.word_timings("我觉得")[0]["start"] = 9
            self.assertEqual(engine.word_timings("我觉得")[0]["start"], 0.1)
        engine.engine = "stepaudio"
        with patch.object(engine, "_stepaudio", AsyncMock(return_value=b"step")):
            self.assertEqual(await engine.synthesize("我觉得"), b"step")
            self.assertEqual(engine.word_timings("我觉得"), [])


if __name__ == "__main__":
    unittest.main()
