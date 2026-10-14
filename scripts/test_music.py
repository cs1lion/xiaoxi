"""Offline deterministic adapter tests: python scripts/test_music.py.

Loads only the pure module: no app bootstrap, .env, credentials, or provider calls.
"""
from __future__ import annotations

import asyncio
import base64
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "server" / "speech" / "music.py"
spec = importlib.util.spec_from_file_location("isolated_music", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
MusicTransport, MusicError = module.MusicTransport, module.MusicError


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


class FakeHTTP:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []
        self.entered = asyncio.Event()
        self.block = False
        self.cancelled = False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.entered.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response if isinstance(response, Response) else Response(response)


async def no_sleep(_):
    await asyncio.sleep(0)


def state(status="SUCCESS", **overrides):
    data = {"status": status, "task": "text_to_music", "caption": "warm city pop",
            "lyrics": "[Verse 1]\nOriginal lyrics", "response_format": "mp3",
            "sample_rate": 48000}
    if status == "SUCCESS":
        data["audio"] = base64.b64encode(b"fake-mp3").decode()
    if status == "FAILED":
        data["error"] = {"stage": "censor", "message": "untrusted provider secret"}
    data.update(overrides)
    return data


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def adapter(self, fake, **kwargs):
        return MusicTransport(music_url="https://api.stepfun.com/v1/audio/music",
                              music_key="dedicated-test-key", enabled=True,
                              http_client=fake, sleep=no_sleep, **kwargs)

    async def test_offline_default_and_missing_configuration(self):
        fake = FakeHTTP()
        for transport, code in [(MusicTransport(http_client=fake), "music_disabled"),
                                (MusicTransport(enabled=True, http_client=fake), "music_not_configured")]:
            with self.assertRaises(MusicError) as ctx:
                await transport.generate("original song")
            self.assertEqual(ctx.exception.code, code)
        self.assertEqual(fake.calls, [])

    async def test_exact_schema_and_original_lyrics(self):
        fake = FakeHTTP({"task_id": "task_123"}, state("PENDING"), state("RUNNING"), state())
        result = await self.adapter(fake).generate("warm city pop", "[Verse 1]\nOriginal lyrics")
        self.assertEqual(result["audio"], b"fake-mp3")
        url, call = fake.calls[0]
        self.assertEqual(url, "https://api.stepfun.com/v1/audio/music/submit")
        self.assertEqual(call["json"], {"task": "text_to_music", "model_id": "stepaudio-3-music-preview",
                                       "caption": "warm city pop", "lyrics": "[Verse 1]\nOriginal lyrics",
                                       "response_format": "mp3"})
        self.assertEqual(call["headers"]["Authorization"], "Bearer dedicated-test-key")
        self.assertFalse(call["follow_redirects"])
        for url, call in fake.calls[1:]:
            self.assertEqual(url, "https://api.stepfun.com/v1/audio/music/query")
            self.assertEqual(call["json"], {"task_id": "task_123"})

    async def test_optional_lyrics_omitted(self):
        fake = FakeHTTP({"task_id": "t"})
        await self.adapter(fake).submit("new original song")
        self.assertNotIn("lyrics", fake.calls[0][1]["json"])

    async def test_input_validation_before_network(self):
        fake = FakeHTTP()
        transport = self.adapter(fake)
        for prompt, lyrics, fmt in [("", None, "mp3"), ("x" * 4001, None, "mp3"),
                                    ("ok", "", "mp3"), ("ok", "x" * 16001, "mp3"),
                                    ("ok\x00", None, "mp3"), (12, None, "mp3"),
                                    ("ok", None, "html"), ("ok", None, [])]:
            with self.assertRaises(ValueError):
                await transport.submit(prompt, lyrics, response_format=fmt)
        self.assertEqual(fake.calls, [])

    async def test_url_and_bound_validation(self):
        for url in ["http://api.stepfun.com/v1/audio/music", "https://evil.example/v1/audio/music",
                    "https://api.stepfun.com@evil.example/v1/audio/music",
                    "https://api.stepfun.com/v1/audio/music?token=x", "https://127.0.0.1/v1/audio/music",
                    "https://api.stepfun.com/v1/audio/music#fragment"]:
            with self.assertRaises(ValueError):
                MusicTransport(music_url=url)
        for kwargs in [{"max_polls": 0}, {"max_polls": True}, {"total_timeout": float("nan")},
                       {"poll_interval": 1}, {"request_timeout": 0}, {"music_key": "key\nheader"}]:
            with self.assertRaises(ValueError):
                MusicTransport(**kwargs)

    async def test_task_ids_cannot_be_urls_or_paths(self):
        for task_id in ["https://127.0.0.1/admin", "../private", "", {}, "x" * 129]:
            fake = FakeHTTP({"task_id": task_id})
            transport = self.adapter(fake)
            with self.assertRaises(MusicError):
                await transport.submit("ok")
            with self.assertRaises(MusicError):
                await transport.query(task_id)
            self.assertEqual(len(fake.calls), 1)

    async def test_response_validation_and_no_audio_url_fetch(self):
        cases = [state(status="UNKNOWN"), state(status=[]), state(task="music_cover"),
                 state(audio="https://127.0.0.1/private"), state(audio="data:audio/mp3;base64,QQ=="),
                 state(audio=""), state(audio="!!!!"), state(sample_rate=True),
                 state(sample_rate=0), state(response_format="html"), state(response_format=[]),
                 state(lyrics="x" * 16001), [], state(audio=None, audio_url="https://example.com/audio.mp3")]
        for data in cases:
            fake = FakeHTTP(data)
            with self.assertRaises(MusicError):
                await self.adapter(fake).query("t")
            self.assertEqual(len(fake.calls), 1)
            self.assertTrue(fake.calls[0][0].endswith("/query"))

    async def test_audio_size_limit(self):
        fake = FakeHTTP(state(audio=base64.b64encode(b"1234").decode()))
        transport = self.adapter(fake)
        transport.MAX_AUDIO_BYTES = 3
        with self.assertRaises(MusicError):
            await transport.query("t")

    async def test_failed_poll_bound_and_safe_errors(self):
        fake = FakeHTTP({"task_id": "t"}, state("FAILED"))
        with self.assertRaises(MusicError) as ctx:
            await self.adapter(fake).generate("ok")
        self.assertEqual((ctx.exception.code, ctx.exception.task_id), ("generation_failed", "t"))
        self.assertNotIn("secret", str(ctx.exception))
        fake = FakeHTTP({"task_id": "t"}, state("PENDING"), state("RUNNING"))
        with self.assertRaises(MusicError) as ctx:
            await self.adapter(fake, max_polls=2).generate("ok")
        self.assertEqual(ctx.exception.code, "poll_limit")
        self.assertEqual(len(fake.calls), 3)

    async def test_http_errors_no_redirects_no_retries_no_secrets(self):
        for response in [Response({}, 302), Response({}, 401), Response({}, 429), Response({}, 503),
                         RuntimeError("dedicated-test-key: private"), Response(None)]:
            fake = FakeHTTP(response)
            with self.assertRaises(MusicError) as ctx:
                await self.adapter(fake).submit("ok")
            self.assertNotIn("dedicated-test-key", str(ctx.exception))
            self.assertEqual(len(fake.calls), 1)

    async def test_timeout_cancels_http(self):
        fake = FakeHTTP()
        fake.block = True
        with self.assertRaises(MusicError) as ctx:
            await self.adapter(fake, request_timeout=0.01).submit("ok")
        self.assertEqual(ctx.exception.code, "request_timeout")
        self.assertTrue(fake.cancelled)
        fake = FakeHTTP()
        fake.block = True
        with self.assertRaises(MusicError) as ctx:
            await self.adapter(fake, total_timeout=0.01).generate("ok")
        self.assertEqual(ctx.exception.code, "generation_timeout")
        self.assertTrue(fake.cancelled)

    async def test_local_event_and_task_cancellation(self):
        event = asyncio.Event()
        event.set()
        fake = FakeHTTP()
        with self.assertRaises(asyncio.CancelledError):
            await self.adapter(fake).generate("ok", cancel_event=event)
        self.assertEqual(fake.calls, [])
        for use_event in (True, False):
            fake = FakeHTTP()
            fake.block = True
            event = asyncio.Event()
            work = asyncio.create_task(self.adapter(fake).generate("ok", cancel_event=event))
            await fake.entered.wait()
            event.set() if use_event else work.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await work
            self.assertTrue(fake.cancelled)
            self.assertEqual(len(fake.calls), 1)
        self.assertFalse(MusicTransport.supports_remote_cancel)

    async def test_resistant_http_deadline_and_outstanding_cap(self):
        class ResistantHTTP(FakeHTTP):
            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                self.entered.set()
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    await self.release.wait()
                return Response({"task_id": "t"})
        fake = ResistantHTTP()
        fake.release = asyncio.Event()
        transport = self.adapter(fake, request_timeout=0.01)
        try:
            with self.assertRaises(MusicError) as ctx:
                await asyncio.wait_for(transport.submit("ok"), 0.2)
            self.assertEqual(ctx.exception.code, "request_timeout")
            for _ in range(3):
                with self.assertRaises(MusicError) as ctx:
                    await transport.submit("ok")
                self.assertEqual(ctx.exception.code, "music_busy")
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(len(transport._pending_http), 1)
        finally:
            fake.release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        self.assertFalse(transport._pending_http)

    async def test_cancellation_during_poll_wait(self):
        entered = asyncio.Event()
        async def blocked_sleep(_):
            entered.set()
            await asyncio.Event().wait()
        fake = FakeHTTP({"task_id": "t"})
        transport = self.adapter(fake)
        transport._sleep = blocked_sleep
        event = asyncio.Event()
        work = asyncio.create_task(transport.generate("ok", cancel_event=event))
        await entered.wait()
        event.set()
        with self.assertRaises(asyncio.CancelledError):
            await work
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
