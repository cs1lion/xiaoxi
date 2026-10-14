"""Offline ASGI music routes and bounded HTTP tests; never import app/config."""
from __future__ import annotations
import asyncio
import base64
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI
from server.speech import music_api as api
from server.speech.music import MusicError


class FakeTransport:
    calls = []
    gate = None
    entered = None
    fail = False
    result = {"audio": b"offline-mp3", "response_format": "mp3"}

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def generate(self, caption, lyrics=None, **kwargs):
        self.calls.append((caption, lyrics, kwargs, self.kwargs))
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("secret-key <script>provider body</script>")
        return self.result


class MusicAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        api._generation_lock = None
        FakeTransport.calls = []
        FakeTransport.gate = FakeTransport.entered = None
        FakeTransport.fail = False
        FakeTransport.result = {"audio": b"offline-mp3", "response_format": "mp3"}
        self.settings = SimpleNamespace(music_enabled=True,
            music_url="https://api.stepfun.com/v1/audio/music", music_api_key="dedicated-test-key")

    def client(self, settings=None, factory=FakeTransport):
        app = FastAPI()
        app.include_router(api.create_music_router(settings or self.settings, factory))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline")

    async def test_disabled_never_constructs_transport_or_networks(self):
        def forbidden(**kwargs):
            self.fail("Disabled path constructed a transport")
        for settings in [SimpleNamespace(), SimpleNamespace(music_enabled=False),
                         SimpleNamespace(music_enabled=True, music_url="https://evil.test", music_api_key="x")]:
            async with self.client(settings, forbidden) as client:
                caps = await client.get("/api/music/capabilities")
                self.assertFalse(caps.json()["configured"])
                response = await client.post("/api/music/create", json={"caption": "original", "user_confirmed": True})
                self.assertEqual(response.status_code, 503)

    async def test_strict_validation_confirmation_no_browser_credentials(self):
        async with self.client() as client:
            for body in [{"caption": "a"}, {"caption": "a", "user_confirmed": "true"},
                         {"caption": "a" * 2001, "user_confirmed": True},
                         {"caption": "a", "lyrics": "x" * 8001, "user_confirmed": True},
                         {"caption": "a", "user_confirmed": True, "music_key": "injected"},
                         {"caption": " ", "user_confirmed": True},
                         {"caption": "a\u0000", "user_confirmed": True}]:
                self.assertEqual((await client.post("/api/music/create", json=body)).status_code, 422)
            self.assertEqual(FakeTransport.calls, [])

    async def test_audio_no_secrets_urls_files_or_untrusted_metadata(self):
        FakeTransport.result["rewritten_lyrics"] = "<script>unsafe</script>"
        async with self.client() as client:
            caps = (await client.get("/api/music/capabilities")).json()
            self.assertTrue(caps["configured"])
            self.assertNotIn("dedicated-test-key", str(caps))
            result = (await client.post("/api/music/create", json={"caption": " original ",
                "lyrics": " my original words ", "user_confirmed": True})).json()
            self.assertEqual(base64.b64decode(result["audio_base64"]), b"offline-mp3")
            self.assertEqual(result["mime"], "audio/mpeg")
            self.assertIn("does not cancel", result["cancellation_notice"])
            self.assertEqual(set(result), {"audio_base64", "mime", "cancellation_notice"})
            self.assertEqual(FakeTransport.calls[0][:2], ("original", "my original words"))

    async def test_global_busy_and_release(self):
        FakeTransport.gate, FakeTransport.entered = asyncio.Event(), asyncio.Event()
        async with self.client() as client, self.client() as other:
            body = {"caption": "original", "user_confirmed": True}
            first = asyncio.create_task(client.post("/api/music/create", json=body))
            await FakeTransport.entered.wait()
            self.assertEqual((await other.post("/api/music/create", json=body)).status_code, 409)
            FakeTransport.gate.set()
            self.assertEqual((await first).status_code, 200)
            self.assertEqual((await other.post("/api/music/create", json=body)).status_code, 200)

    async def test_timeout_cancels_local_wait_and_releases_lock(self):
        FakeTransport.gate = asyncio.Event()
        original_timeout = api.TOTAL_TIMEOUT
        api.TOTAL_TIMEOUT = 0.01
        try:
            async with self.client() as client:
                response = await client.post("/api/music/create", json={"caption": "original", "user_confirmed": True})
                self.assertEqual(response.status_code, 504)
                self.assertIn("does not cancel", response.text)
                self.assertFalse(api._generation_lock.locked())
        finally:
            api.TOTAL_TIMEOUT = original_timeout

    async def test_safe_failures_and_output_cap(self):
        async with self.client() as client:
            FakeTransport.fail = True
            response = await client.post("/api/music/create", json={"caption": "a", "user_confirmed": True})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn("secret-key", response.text)
            FakeTransport.fail = False
            FakeTransport.result = {"audio": b"x" * (api.MAX_AUDIO_BYTES + 1), "response_format": "mp3"}
            self.assertEqual((await client.post("/api/music/create", json={"caption": "a", "user_confirmed": True})).status_code, 502)

    async def test_bounded_json_stream_no_redirects(self):
        calls = []
        async def handler(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://evil.test"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await api.BoundedMusicHTTPClient(client).post("https://offline.test/submit", headers={}, json={}, timeout=1)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(len(calls), 1)
        old = api.MAX_BODY_BYTES
        api.MAX_BODY_BYTES = 8
        try:
            for response in [httpx.Response(200, content=b'{"too":"large"}'),
                             httpx.Response(200, content=b'invalid'),
                             httpx.Response(200, content=b'[]')]:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: response)) as client:
                    with self.assertRaises(MusicError):
                        await api.BoundedMusicHTTPClient(client).post("https://offline.test", headers={}, json={}, timeout=1)
        finally:
            api.MAX_BODY_BYTES = old


if __name__ == "__main__":
    unittest.main()
