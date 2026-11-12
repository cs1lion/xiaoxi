# -*- coding: utf-8 -*-
"""Fish Audio 逐字对齐 → `audio.word_timings`（P4，2026-11-12）。

**契约**：`docs/PROTOCOL.md:132` 的 `word_timings` 是
`[{text,start,end}]`，相对当前片段秒数；前端 `providerMap`
（`web/avatar/drivers/speech-plan.js:44`）按**去标点后的字符序列**逐字匹配，
序列不完整/乱序就整份拒绝并退回 `alignment:"estimated"`。

**Fish 的实测形状**（`POST /v1/tts/stream/with-timestamp`，SSE）：
- 每个 `data:` 事件带 `audio_base64` 与 `alignment.segments`；
- `segments` 是**累计**的（第 1 个事件 1 段，最后 1 个事件 N 段），
  时间是**相对整段音频**的绝对秒数，不是分块局部；
- 段粒度是**逐字**（中文一字一段），且**不含标点**；
- 方括号 instruction 不进 segments（已实测：`[语气自然随意] 正文`
  的 segments 只覆盖「正文去标点」）；
- `chunk_audio_offset_sec` 实测恒为 0（短/长文本均如此），不能当分块偏移用。

所以实现只取**最长的一份** segments（不是拼接每个事件），再逐条校验。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", ":memory:")


def _ev(segments):
    return {"alignment": {"segments": segments}}


class FishAlignmentTests(unittest.TestCase):
    def test_cumulative_picks_longest(self):
        """累计列表：取最长的一份，绝不能把每个事件拼起来（会重复）。"""
        from server.speech.tts import _fish_word_timings

        events = [
            _ev([{"text": "你", "start": 0.0, "end": 0.16}]),
            _ev([{"text": "你", "start": 0.0, "end": 0.16},
                 {"text": "好", "start": 0.16, "end": 0.4}]),
        ]
        got = _fish_word_timings(events)
        self.assertEqual([w["text"] for w in got], ["你", "好"])
        self.assertEqual(got[1], {"text": "好", "start": 0.16, "end": 0.4})

    def test_bad_segments_are_dropped(self):
        """空文本、非有限数、倒序区间、负起点都不进协议。"""
        from server.speech.tts import _fish_word_timings

        events = [_ev([
            {"text": "好", "start": 0.0, "end": 0.2},
            {"text": "", "start": 0.2, "end": 0.3},
            {"text": "坏", "start": 0.3, "end": 0.3},
            {"text": "坏", "start": -1.0, "end": 0.4},
            {"text": "坏", "start": float("nan"), "end": 0.5},
            {"text": "坏", "start": 0.6},
            {"text": "嗯", "start": 0.4, "end": 0.6},
        ])]
        self.assertEqual([w["text"] for w in _fish_word_timings(events)], ["好", "嗯"])

    def test_out_of_order_list_is_rejected_whole(self):
        """乱序会让前端整份退回估算；与其送坏数据不如明确返回空。"""
        from server.speech.tts import _fish_word_timings

        events = [_ev([
            {"text": "你", "start": 0.5, "end": 0.7},
            {"text": "好", "start": 0.1, "end": 0.3},
        ])]
        self.assertEqual(_fish_word_timings(events), [])

    def test_no_alignment_is_empty_not_error(self):
        from server.speech.tts import _fish_word_timings

        self.assertEqual(_fish_word_timings([]), [])
        self.assertEqual(_fish_word_timings([{"alignment": {}}, {}]), [])
        self.assertEqual(_fish_word_timings([_ev(None)]), [])


class FishSynthesisWiringTests(unittest.TestCase):
    """synthesize_profile 的 fish 分支必须把 timings 交给引擎并随缓存保存。"""

    def _engine(self):
        from server.speech.tts import TTSEngine

        engine = TTSEngine()
        engine.engine = "fish"
        return engine

    def test_fish_timings_reach_word_timings(self):
        engine = self._engine()
        profile = {"voice": "test-voice"}

        async def fake_fish(text, profile, *, word_timings=None):
            self.assertIsNotNone(word_timings, "fish 分支必须传 word_timings")
            word_timings.append({"text": "你", "start": 0.0, "end": 0.12})
            word_timings.append({"text": "好", "start": 0.12, "end": 0.3})
            return b"ID3fake-bytes"

        engine._fish = fake_fish
        audio = asyncio.run(engine.synthesize_profile("你好", profile))
        self.assertEqual(audio, b"ID3fake-bytes")
        self.assertEqual(engine.word_timings("你好", profile=profile),
                         [{"text": "你", "start": 0.0, "end": 0.12},
                          {"text": "好", "start": 0.12, "end": 0.3}])

    def test_cache_hit_keeps_timings(self):
        engine = self._engine()
        profile = {"voice": "test-voice"}
        calls = []

        async def fake_fish(text, profile, *, word_timings=None):
            calls.append(text)
            word_timings.append({"text": "嗨", "start": 0.0, "end": 0.2})
            return b"ID3fake-bytes"

        engine._fish = fake_fish
        asyncio.run(engine.synthesize_profile("嗨", profile))
        asyncio.run(engine.synthesize_profile("嗨", profile))
        self.assertEqual(calls, ["嗨"], "第二次必须是缓存命中，不再调引擎")
        self.assertEqual(engine.word_timings("嗨", profile=profile),
                         [{"text": "嗨", "start": 0.0, "end": 0.2}])
        self.assertEqual(engine.voice_metrics()["hit"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
