"""Isolated fixed-reply delivery: python -B scripts/test_reply_delivery.py.

Only stdlib and the pure delivery module are imported; all audio providers are
local fakes, and no application, model, credentials or database is initialized.
"""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.dialogue.delivery import deliver_reply


class TTS:
    def __init__(self, audio=(b"first", b"second"), *, fail_after=None):
        self.audio, self.fail_after = audio, fail_after
        self.calls = []
        self.entered, self.closed, self.cancelled = (asyncio.Event() for _ in range(3))
        self.release = None
        self.resist = False

    async def speak(self, text, emotion="base"):
        self.calls.append((text, emotion))
        self.entered.set()
        try:
            if self.release is not None:
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancelled.set()
                        if not self.resist:
                            raise
            for index, audio in enumerate(self.audio):
                if self.fail_after == index:
                    raise RuntimeError("SECRET provider payload")
                yield SimpleNamespace(audio=audio, text=text, index=index + 17, emotion=emotion)
            if self.fail_after == len(self.audio):
                raise RuntimeError("SECRET provider payload")
        finally:
            self.closed.set()


async def collect(events):
    return [event async for event in events]


class ReplyDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_text_order_and_voice_override_preserve_character_emotion(self):
        tts = TTS()
        text = "  原文【待填】\n第二行。  "
        events = await collect(deliver_reply(text, tts, emotion="认真", voice_emotion="base"))
        self.assertEqual([e["type"] for e in events], ["emotion", "text_delta", "audio", "audio", "done"])
        self.assertEqual(tts.calls, [(text, "base")])
        self.assertEqual(events[1]["delta"], text)
        self.assertEqual(events[-1]["text"], text)
        self.assertTrue(all(e["emotion"] == "认真" for e in events if "emotion" in e))
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual([e["index"] for e in audio], [0, 1])
        self.assertEqual([e["audio"] for e in audio], [b"first", b"second"])
        self.assertEqual(events[-1]["delivery"], {"text_status": "complete", "audio_status": "synthesized",
            "audio_failed_chunks": 0, "audio_succeeded_chunks": 2, "playback_confirmed": False})

    async def test_no_provider_silent_engine_and_empty_audio_have_no_playable_events(self):
        silent_engine = TTS()
        silent_engine.engine = "silent"
        for tts in (None, silent_engine, TTS(audio=(b"", b""))):
            events = await collect(deliver_reply("Text", tts))
            self.assertEqual([e["type"] for e in events], ["emotion", "text_delta", "done"])
            self.assertEqual(events[-1]["delivery"]["audio_status"], "none")
            self.assertEqual(events[-1]["delivery"]["audio_failed_chunks"], 0)
        self.assertEqual(silent_engine.calls, [])

    async def test_empty_reply_never_calls_provider(self):
        tts = TTS()
        events = await collect(deliver_reply("", tts))
        self.assertEqual(tts.calls, [])
        self.assertEqual(events[-1]["text"], "")
        self.assertEqual(events[-1]["delivery"]["audio_status"], "none")

    async def test_provider_failure_retains_text_and_partial_status(self):
        for fail_after, status in ((0, "failed"), (1, "partial")):
            events = await collect(deliver_reply("Useful reply", TTS(fail_after=fail_after)))
            self.assertEqual(events[-2]["type"], "error")
            self.assertTrue(events[-2]["recoverable"])
            self.assertNotIn("SECRET", str(events))
            self.assertEqual(events[-2]["message"], "音频暂不可用，文字回复已保留。")
            self.assertEqual(events[-1]["text"], "Useful reply")
            self.assertEqual(events[-1]["delivery"]["audio_status"], status)
            self.assertEqual(events[-1]["delivery"]["audio_succeeded_chunks"], fail_after)

    async def test_audio_bounds_and_invalid_payloads_are_recoverable(self):
        for tts, limits, sent in ((TTS(), {"max_audio_chunks": 1}, 1),
                                 (TTS(audio=(b"12", b"345")), {"max_audio_bytes": 4}, 1),
                                 (TTS(audio=("bad",)), {}, 0),
                                 (TTS(), {"max_audio_chunks": 0}, 0)):
            events = await collect(deliver_reply("Text", tts, **limits))
            self.assertEqual(len([e for e in events if e["type"] == "audio"]), sent)
            self.assertEqual(events[-1]["delivery"]["audio_failed_chunks"], 1)
            self.assertEqual(events[-1]["text"], "Text")

    async def test_single_chunk_matches_browser_base64_boundary(self):
        limit = 9 * 1024 * 1024
        events = await collect(deliver_reply("Text", TTS(audio=(b"a" * limit,))))
        audio = [e for e in events if e["type"] == "audio"]
        self.assertEqual(len(audio), 1)
        self.assertEqual(len(base64.b64encode(audio[0]["audio"])), 12 * 1024 * 1024)
        self.assertEqual(events[-1]["delivery"]["audio_status"], "synthesized")
        events = await collect(deliver_reply("Text", TTS(audio=(b"a" * (limit + 1),))))
        self.assertFalse(any(e["type"] == "audio" for e in events))
        self.assertEqual(events[-1]["delivery"]["audio_status"], "failed")
        self.assertEqual(events[-1]["text"], "Text")

    async def test_infinite_empty_chunks_stop_at_observed_chunk_limit(self):
        class EmptyTTS:
            def __init__(self):
                self.count = 0
                self.closed = asyncio.Event()

            async def speak(self, text, **kwargs):
                try:
                    while True:
                        self.count += 1
                        yield SimpleNamespace(audio=b"", text=text)
                finally:
                    self.closed.set()

        tts = EmptyTTS()
        events = await asyncio.wait_for(collect(deliver_reply("Text", tts, max_audio_chunks=3)), 1)
        self.assertEqual(tts.count, 4)
        self.assertFalse(any(e["type"] == "audio" for e in events))
        self.assertEqual(events[-1]["delivery"]["audio_status"], "failed")
        await asyncio.wait_for(tts.closed.wait(), 1)

    async def test_offline_provider_is_unavailable_without_calling_it(self):
        tts = TTS()
        tts.offline = True
        events = await collect(deliver_reply("Text", tts))
        self.assertEqual(tts.calls, [])
        self.assertEqual(events[-1]["delivery"]["audio_status"], "failed")

    async def test_stale_at_every_event_boundary_has_no_late_done(self):
        for stop in ("emotion", "text_delta", "audio", "error"):
            state = {"current": True}
            tts = TTS(fail_after=0 if stop == "error" else None)
            events = []
            async for event in deliver_reply("Text", tts, is_current=lambda: state["current"]):
                events.append(event)
                if event["type"] == stop:
                    state["current"] = False
            self.assertEqual(events[-1]["type"], stop)
            self.assertFalse(any(e["type"] == "done" for e in events))
        tts = TTS()
        self.assertEqual(await collect(deliver_reply("Text", tts, is_current=lambda: False)), [])
        self.assertEqual(tts.calls, [])

    async def test_stale_while_provider_waits_discards_completed_audio(self):
        state, tts = {"current": True}, TTS()
        tts.release = asyncio.Event()
        running = asyncio.create_task(collect(deliver_reply("Text", tts, is_current=lambda: state["current"])))
        await tts.entered.wait()
        state["current"] = False
        tts.release.set()
        events = await running
        self.assertEqual([e["type"] for e in events], ["emotion", "text_delta"])
        await asyncio.wait_for(tts.closed.wait(), 1)

    async def test_cancellation_resistant_provider_cannot_emit_late_events(self):
        tts = TTS()
        tts.release, tts.resist = asyncio.Event(), True
        events = []
        async def run():
            async for event in deliver_reply("Text", tts):
                events.append(event)
        task = asyncio.create_task(run())
        await tts.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        await tts.cancelled.wait()
        tts.release.set()
        await asyncio.wait_for(tts.closed.wait(), 1)
        self.assertEqual([e["type"] for e in events], ["emotion", "text_delta"])

    async def test_timeout_does_not_wait_for_cancellation_resistant_provider(self):
        tts = TTS()
        tts.release, tts.resist = asyncio.Event(), True
        try:
            events = await asyncio.wait_for(collect(deliver_reply("Text", tts, audio_timeout=.01)), 1)
            self.assertEqual([e["type"] for e in events], ["emotion", "text_delta", "error", "done"])
            self.assertEqual(events[-1]["delivery"]["audio_status"], "failed")
            await tts.cancelled.wait()
        finally:
            tts.release.set()
        await asyncio.wait_for(tts.closed.wait(), 1)

    async def test_aclose_releases_audio_iterator_without_done(self):
        tts = TTS()
        stream = deliver_reply("Text", tts)
        async for event in stream:
            if event["type"] == "audio":
                break
        await stream.aclose()
        await asyncio.wait_for(tts.closed.wait(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
