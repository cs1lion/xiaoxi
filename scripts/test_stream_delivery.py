"""Offline delivery contracts: python scripts/test_stream_delivery.py.

The database is in-memory before importing any singleton; LLM/TTS, history,
persistence and context are mocked. Memory extraction is explicitly disabled.
"""
from __future__ import annotations

import asyncio
import copy
import importlib
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import dotenv

os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with patch.object(dotenv, 'load_dotenv', return_value=False):
    from server.config import settings
    settings.db_path = ":memory:"
    mod = importlib.import_module("server.dialogue.orchestrator")


class StreamDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.orch = mod.Orchestrator()
        self.orch.pacing = copy.deepcopy(self.orch.pacing)
        self.orch.pacing["length"].update(
            default="short", short_max_chars=24, medium_max_chars=40, hard_limit_chars=96)
        self.saved = []
        self.tts_calls = []
        self.policy = "short"
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

    async def collect(self, deltas, *, fail_at=None, source=None, voice_emotion=None):
        async def stream(*args, **kwargs):
            for delta in deltas:
                yield delta
                await asyncio.sleep(0)

        async def synthesize(text, emotion):
            self.tts_calls.append((text, emotion))
            if len(self.tts_calls) == fail_at:
                raise RuntimeError("offline TTS failure")
            return b"audio"

        with patch.object(mod.llm, "stream", source or stream), patch.object(mod.tts, "synthesize", synthesize):
            async def run():
                return [event async for event in self.orch.stream_respond(
                    session_id="test", user_text="你好", voice_emotion=voice_emotion)]
            events = await asyncio.wait_for(run(), 2)
        done = events[-1]
        self.assertEqual(done["type"], "done")
        text = "".join(e["delta"] for e in events if e["type"] == "text_delta")
        self.assertEqual(done["text"], text)
        self.assertFalse(done['delivery']['playback_confirmed'])
        self.assertEqual(done['delivery']['audio_failed_chunks'], 1 if fail_at is not None else 0)
        if fail_at is not None:
            self.assertIn(done['delivery']['audio_status'], ('partial', 'failed'))
            self.assertTrue(all(e.get('recoverable') for e in events if e['type'] == 'error'))
        self.assertEqual(self.saved[-1][2], text)
        self.assertLessEqual(len(text), 96 if self.policy == "detailed" else 24)
        self.assertEqual("".join(t for t, _ in self.tts_calls), text)
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual([e["index"] for e in audio], list(range(len(audio))))
        if fail_at is None:
            self.assertEqual("".join(e["text"] for e in audio), text)
        return events

    async def test_fixed_natural_voice_preserves_reply_emotion(self):
        events = await self.collect(["[认真]第一句。", "第二句。"], voice_emotion="base")
        self.assertEqual(self.tts_calls, [("第一句。", "base"), ("第二句。", "base")])
        self.assertEqual(events[-1]["emotion"], "认真")
        self.assertTrue(all(e["emotion"] == "认真" for e in events if e["type"] in ("emotion", "audio")))

    async def test_explicit_voice_profile_does_not_change_question_policy(self):
        events = await self.collect(["[收敛]你还好吗？"], voice_emotion="认真")
        self.assertEqual(self.tts_calls, [("你还好吗", "认真")])
        self.assertEqual(events[-1]["text"], "你还好吗")
        self.assertEqual(events[-1]["emotion"], "收敛")
        self.assertTrue(all(e["emotion"] == "收敛" for e in events if e["type"] in ("emotion", "audio")))

    async def test_automatic_and_unknown_voice_follow_reply_emotion(self):
        for voice_emotion in (None, "", "unknown"):
            with self.subTest(voice_emotion=voice_emotion):
                self.tts_calls.clear()
                events = await self.collect(["[调侃]你好。"], voice_emotion=voice_emotion)
                self.assertEqual(self.tts_calls, [("你好。", "调侃")])
                self.assertEqual(events[-1]["emotion"], "调侃")

    async def test_long_reply_sentence_budget(self):
        events = await self.collect(["[base]", "这是完整的一句话。" * 30])
        self.assertEqual(events[-1]["text"], "这是完整的一句话。" * 2)
        self.assertEqual(events[-1]['delivery']['text_status'], 'limit_reached')

    async def test_long_unpunctuated_reply_is_bounded(self):
        closed = asyncio.Event()
        count = 0
        async def source(*args, **kwargs):
            nonlocal count
            try:
                for _ in range(1000):
                    count += 1
                    yield "字"
            finally:
                closed.set()
        events = await self.collect([], source=source)
        self.assertEqual(events[-1]["text"], "字" * 23 + "。")
        self.assertLessEqual(count, 25)
        self.assertTrue(closed.is_set())

    async def test_generation_failure_with_zero_text_is_honest(self):
        """S3 演练②（坏 Key 401）：一个字都没收到时，recoverable 文案不能
        谎称"已保留收到的部分文本"。"""
        async def broken(*args, **kwargs):
            raise RuntimeError("HTTP 401")
            yield ""  # pragma: no cover - 仅为构成 async generator

        events = await self.collect([], source=broken)
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(errors)
        self.assertTrue(all(e.get("recoverable") for e in errors))
        self.assertIn("一个字都没收到", errors[-1]["message"])
        self.assertNotIn("已保留", errors[-1]["message"])
        self.assertEqual(events[-1]["delivery"]["text_status"], "partial")

    async def test_generation_failure_after_partial_text_keeps_old_message(self):
        """真保住了半句时，旧文案仍然正确——失败发生在句间。"""
        async def half(*args, **kwargs):
            yield "说了一半的话。"
            raise RuntimeError("HTTP 401")

        events = await self.collect([], source=half)
        errors = [e for e in events if e["type"] == "error"]
        self.assertTrue(errors)
        self.assertIn("已保留收到的部分文本", errors[-1]["message"])
        self.assertEqual(events[-1]["text"], "说了一半的话。")

    async def test_detailed_uses_hard_limit(self):
        self.policy = "detailed"
        events = await self.collect(["[认真]" + "这是具体解释。" * 40])
        self.assertGreater(len(events[-1]["text"]), 40)

    async def test_split_emotion_all_boundaries(self):
        for tag in ("[轻微不服]", "【认真】", "[收敛]"):
            for cut in range(1, len(tag)):
                with self.subTest(tag=tag, cut=cut):
                    self.saved.clear()
                    self.tts_calls.clear()
                    events = await self.collect([" \n", tag[:cut], tag[cut:], "你好。"])
                    self.assertEqual(events[-1]["text"], "你好。")
                    self.assertEqual(events[-1]["emotion"], tag[1:-1])

    async def test_unknown_tags_never_leak_or_split_sentences(self):
        events = await self.collect(["【陌生。", "情绪！？】", "**你好**", "（小声", "笑）", "呀。"])
        self.assertEqual(events[-1]["text"], "【陌生。情绪！？】你好呀。")
        self.assertEqual(len(self.tts_calls), 1)

    async def test_split_ellipsis_normalized_once(self):
        events = await self.collect(["嗯", "…", "…", "再说吧。"])
        self.assertEqual(events[-1]["text"], "嗯……再说吧。")

    async def test_factual_brackets_and_placeholders_survive(self):
        self.policy = "detailed"
        events = await self.collect(list("[base]模板：【实际状态】；坐标(1, 2)。"))
        self.assertEqual(events[-1]["text"], "模板：【实际状态】；坐标(1, 2)。")

    async def test_incomplete_known_emotion_does_not_leak(self):
        events = await self.collect(["[", "轻微"])
        self.assertEqual(events[-1]["text"], "")

    async def test_multisentence_tail_and_lossless_audio_indexes(self):
        self.policy = "detailed"
        events = await self.collect(["[base]第一句。第二句！", "无句号的尾巴"])
        self.assertEqual([e["index"] for e in events if e["type"] == "audio"], [0, 1, 2])
        self.assertEqual(events[-1]["text"], "第一句。第二句！无句号的尾巴")

    async def test_long_audio_chunk_keeps_commas(self):
        self.policy = "detailed"
        await self.collect(["字" * 35 + "，" + "字" * 35 + "。尾巴"])

    async def test_tts_failure_finishes_promptly_and_tail_continues(self):
        events = await self.collect(["第一句。第二句。尾巴"], fail_at=1)
        self.assertEqual(len([e for e in events if e["type"] == "error"]), 1)
        self.assertEqual([e["text"] for e in events if e["type"] == "audio"], ["第二句。", "尾巴"])

    async def test_tts_tail_failure_still_finishes(self):
        events = await self.collect(["第一句。尾巴"], fail_at=2)
        self.assertEqual(events[-1]["text"], "第一句。尾巴")

    async def test_llm_failure_preserves_delivered_text(self):
        async def source(*args, **kwargs):
            yield "第一句。尾巴"
            raise RuntimeError("offline LLM failure")
        events = await self.collect([], source=source)
        self.assertEqual(events[-1]["text"], "第一句。尾巴")
        self.assertEqual(len([e for e in events if e["type"] == "error"]), 1)

    async def test_question_suppression_before_delivery(self):
        events = await self.collect(["[收敛]你还好吗？"])
        self.assertEqual(events[-1]["text"], "你还好吗")

    async def test_first_sentence_before_llm_finishes(self):
        release = asyncio.Event()
        async def source(*args, **kwargs):
            yield "[base]第一句。"
            await release.wait()
            yield "尾巴"
        async def synthesize(*args):
            release.set()
            return b"audio"
        with patch.object(mod.llm, "stream", source), patch.object(mod.tts, "synthesize", synthesize):
            async def run():
                return [e async for e in self.orch.stream_respond(session_id="test", user_text="你好")]
            events = await asyncio.wait_for(run(), 2)
        self.assertEqual(events[-1]["text"], "第一句。尾巴")

    async def test_cancellation_reclaims_llm_and_tts(self):
        llm_closed, tts_closed, tts_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def source(*args, **kwargs):
            try:
                yield "[base]第一句。"
                await asyncio.Event().wait()
            finally:
                llm_closed.set()
        async def synthesize(*args):
            tts_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                tts_closed.set()
        with patch.object(mod.llm, "stream", source), patch.object(mod.tts, "synthesize", synthesize):
            async def run():
                return [e async for e in self.orch.stream_respond(session_id="test", user_text="你好")]
            task = asyncio.create_task(run())
            await asyncio.wait_for(tts_started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(llm_closed.is_set())
        self.assertTrue(tts_closed.is_set())
        self.assertEqual(self.saved, [("test", "user", "你好", "voice")])
        self.orch._spawn_extraction.assert_not_called()

    async def test_aclose_reclaims_producer(self):
        closed = asyncio.Event()
        async def source(*args, **kwargs):
            try:
                yield "第一句。"
                await asyncio.Event().wait()
            finally:
                closed.set()
        async def synthesize(*args):
            await asyncio.Event().wait()
        with patch.object(mod.llm, "stream", source), patch.object(mod.tts, "synthesize", synthesize):
            stream = self.orch.stream_respond(session_id="test", user_text="你好")
            async for event in stream:
                if event["type"] == "text_delta":
                    break
            await asyncio.wait_for(stream.aclose(), 2)
        self.assertTrue(closed.is_set())
        self.assertEqual(self.saved, [("test", "user", "你好", "voice")])
        self.orch._spawn_extraction.assert_not_called()


class NoticeCallbackErrorTests(unittest.IsolatedAsyncioTestCase):
    """后台通知回调出错必须被记录，不能被静默吞掉。

    `_spawn_extraction` 把抽取跑在后台，`drain()` 是它自带的确定性等待入口。
    notice 阶段曾是裸 `except Exception: pass`——那正是 memory_notice 漏
    `status` 能潜伏的原因：回调抛错被吞，通知从不发出，前端只看到"通知没来"
    而不是一个任何人能看见的错误。
    """

    def _orch_with_memories(self):
        orch = mod.Orchestrator()
        old_enabled = mod.manager.enabled
        mod.manager.enabled = True
        self.addCleanup(setattr, mod.manager, "enabled", old_enabled)

        async def fake_observe(**kwargs):
            return [SimpleNamespace(id=1, kind="fact", value="用户在乙公司工作",
                                    source="stated", status="active")]

        patcher = patch.object(mod.manager, "observe_turn", new=fake_observe)
        patcher.start()
        self.addCleanup(patcher.stop)
        return orch

    def _spawn_with_boom(self, orch):
        async def boom(memories):
            raise RuntimeError("notice contract broken")

        orch._spawn_extraction(session_id="s", user_text="u", assistant_text="a",
                               recent_context="", on_memories=boom)

    async def test_notice_callback_error_is_recorded(self):
        orch = self._orch_with_memories()
        self._spawn_with_boom(orch)
        await orch.drain()
        self.assertIn("notice_error", orch._last_meta)
        self.assertIn("notice contract broken", orch._last_meta["notice_error"])

    async def test_notice_error_does_not_kill_the_background_task(self):
        # 循环必须活下来：drain() 正常返回，异常不逃逸到调用方。
        orch = self._orch_with_memories()
        self._spawn_with_boom(orch)
        await orch.drain()  # 不抛异常即通过
        self.assertIn("notice_error", orch._last_meta)

    async def test_healthy_notice_records_no_error(self):
        # 对照：回调正常时不留 notice_error，也别误记 extract_error。
        orch = self._orch_with_memories()
        seen = []

        async def ok(memories):
            seen.extend(memories)

        orch._spawn_extraction(session_id="s", user_text="u", assistant_text="a",
                               recent_context="", on_memories=ok)
        await orch.drain()
        self.assertEqual(len(seen), 1)
        self.assertNotIn("notice_error", orch._last_meta)
        self.assertNotIn("extract_error", orch._last_meta)


class PacingConfigGuardTest(unittest.TestCase):
    """A typo in pacing.yaml must degrade to the default, not crash.

    ``ambient.config_number`` now backs the sibling sections the orchestrator
    reads -- cooldown, proactivity and questions. The last runs on every reply,
    so a malformed ``max_ratio`` there would silence her completely.
    """

    def _orch(self):
        """A fresh Orchestrator with an isolated pacing dict.

        pacing_cfg() returns a shared object, so mutating orch.pacing in place
        would leak into every other Orchestrator in the process (the class
        comment at orchestrator.py:380 says tests should replace self.pacing).
        """
        orch = mod.Orchestrator()
        orch.pacing = copy.deepcopy(orch.pacing)
        return orch

    def test_pacing_number_guards_every_bad_shape(self):
        self.assertEqual(mod._pacing_number(45, 90.0), 45.0)
        self.assertEqual(mod._pacing_number("45", 90.0), 45.0)
        self.assertEqual(mod._pacing_number(None, 90.0), 90.0)
        self.assertEqual(mod._pacing_number("abc", 90.0), 90.0)
        self.assertEqual(mod._pacing_number([90], 90.0), 90.0)
        # 0 is a real "no cooldown" limit, not a typo: it must survive as 0.0.
        # test_micro_delivery depends on min_interval_seconds=0 being honoured.
        self.assertEqual(mod._pacing_number(0, 90.0), 0.0)
        # PyYAML turns no/yes into False/True; float(True) is 1.0, not the default.
        self.assertEqual(mod._pacing_number(True, 90.0), 90.0)
        self.assertEqual(mod._pacing_number(False, 28.0), 28.0)

    def test_question_ratio_survives_a_malformed_limit(self):
        orch = self._orch()
        orch.state["_recent_assistant"] = ["你好吗？", "在忙吗？", "累不累？"]
        self.assertTrue(orch._question_ratio_exceeded())  # 3/3 >= 0.35
        for bad in ("not-a-number", True, [0.5]):
            with self.subTest(bad=bad):
                orch.pacing["questions"]["max_ratio"] = bad
                self.assertTrue(orch._question_ratio_exceeded())  # falls back to 0.35

    def test_question_ratio_valid_limit_still_governs(self):
        orch = self._orch()
        orch.state["_recent_assistant"] = ["你好吗？", "在忙吗？", "累不累？"]
        orch.pacing["questions"]["max_ratio"] = 1.1  # 3/3 = 1.0 < 1.1
        self.assertFalse(orch._question_ratio_exceeded())

    def test_length_target_survives_malformed_config(self):
        orch = self._orch()
        base = orch.pacing["length"]
        self.assertEqual(orch._length_target("你好"), 100)
        self.assertEqual(orch._length_target("详细说说"), 200)
        for key, ask, fallback in (("short_max_chars", "你好", 100),
                                   ("medium_max_chars", "详细说说", 200)):
            for bad in ("abc", None, [200]):
                with self.subTest(key=key, bad=bad):
                    orch.pacing["length"] = {**base, key: bad}
                    self.assertEqual(orch._length_target(ask), fallback)
        # A typo'd default must not crash the dict lookup.
        orch.pacing["length"] = {**base, "default": "shrt"}
        self.assertEqual(orch._length_target("你好"), 200)

    def test_length_target_zero_is_a_real_limit(self):
        # 0 means "no budget", not "unset" -- it must survive, not fall back.
        orch = self._orch()
        orch.pacing["length"] = {**orch.pacing["length"], "short_max_chars": 0}
        self.assertEqual(orch._length_target("你好"), 0)

    def test_pacing_block_survives_malformed_length_and_questions(self):
        # static_system_prompt is the prefix of every reply's system prompt, so a
        # typo in any value it renders would silence her completely.
        from server import persona
        real_cfg = persona.pacing_cfg  # the function itself, so we can restore it
        real = real_cfg()
        length, questions = real["length"], real["questions"]

        def install(**over):
            persona.pacing_cfg = lambda: {
                "length": {**length, **{k: v for k, v in over.items() if k in length}},
                "questions": {**questions, **{k: v for k, v in over.items() if k in questions}}}

        try:
            for over in ({"default": "shrt"}, {"short_max_chars": "abc"},
                         {"medium_max_chars": None}, {"hard_limit_chars": [400]},
                         {"soft_limit_chars": True}, {"max_ratio": "0.35"},
                         {"max_ratio": "not-a-number"}):
                with self.subTest(over=over):
                    install(**over)
                    self.assertIn("节奏规则", persona._pacing_block())  # must not raise
        finally:
            persona.pacing_cfg = real_cfg

    def test_barge_in_hint_is_unconditional_not_voice_config(self):
        # 2026-11-09：pacing.yaml 的 voice.barge_in 已删除。打断是前端无条件行为，
        # 提示词必须始终出现；既不能要求 pacing["voice"] 存在，也不能被
        # voice.barge_in=False 关掉。
        from server import persona
        real_cfg = persona.pacing_cfg
        real = real_cfg()
        try:
            configs = (
                {"length": real["length"], "questions": real["questions"]},
                {"length": real["length"], "questions": real["questions"],
                 "voice": {"barge_in": False}},
            )
            for cfg in configs:
                with self.subTest(has_voice="voice" in cfg):
                    persona.pacing_cfg = lambda cfg=cfg: cfg
                    block = persona._pacing_block()
                    self.assertIn("你说的话是可打断的", block)
                    self.assertIn("用户开口时立刻停下来听", block)
        finally:
            persona.pacing_cfg = real_cfg


if __name__ == "__main__":
    unittest.main()
