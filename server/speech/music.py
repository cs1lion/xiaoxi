"""Opt-in, isolated StepFun original-song adapter. No application/config imports.

Official protocol: https://platform.stepfun.com/docs/zh/api-reference/audio/music.md
Only injected HTTP is used; importing or constructing this module cannot network.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import math
import re
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlsplit


class MusicError(Exception):
    """Safe adapter error; provider bodies and credentials are never exposed."""

    def __init__(self, code: str, *, task_id: str | None = None) -> None:
        self.code = code
        self.task_id = task_id
        super().__init__(code)


class MusicHTTPClient(Protocol):
    async def post(self, url: str, *, headers: dict[str, str],
                   json: dict[str, Any], timeout: float,
                   follow_redirects: bool) -> Any:
        """Return an object with integer status_code and synchronous json().

        Enforce a response-body cap (48 MiB recommended) before decoding JSON.
        Own connection lifecycle; never log authorization or response bodies.
        """
        ...


def _text(value: Any, name: str, limit: int, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > limit
            or (not empty and not value.strip())
            or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value)):
        raise ValueError(f"Invalid {name}")
    return value.strip()


def _task_id(value: Any) -> str:
    # Opaque IDs, not paths or URLs. Official examples are UUIDs; do not require UUID version.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise MusicError("invalid_task_id")
    return value


def _seconds(value: Any, name: str, maximum: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 < value <= maximum):
        raise ValueError(f"Invalid {name}")
    return float(value)


class MusicTransport:
    """Explicit dedicated credentials and opt-in; no shared subscription fallback.

    This adapter supports only text_to_music with optional original lyrics.
    Cancellation stops local waiting, NOT the already submitted provider job.
    """

    MODEL = "stepaudio-3-music-preview"
    MAX_AUDIO_BYTES = 32 * 1024 * 1024
    FORMATS = frozenset(("wav", "flac", "opus", "mp3", "pcm"))
    supports_remote_cancel = False

    def __init__(self, *, music_url: str = "", music_key: str = "",
                 http_client: MusicHTTPClient | None = None,
                 enabled: bool = False, request_timeout: float = 30,
                 total_timeout: float = 600, poll_interval: float = 5,
                 max_polls: int = 120,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        self.enabled = enabled
        # Only the documented service is supported. No arbitrary host, URL joining,
        # userinfo, query, redirect, returned task URL, or provider audio URL is used.
        if not isinstance(music_url, str) or not isinstance(music_key, str):
            raise ValueError("Dedicated music URL/key must be strings")
        if music_url:
            parsed = urlsplit(music_url)
            if (parsed.scheme != "https" or parsed.netloc != "api.stepfun.com"
                    or parsed.path.rstrip("/") != "/v1/audio/music"
                    or parsed.query or parsed.fragment):
                raise ValueError("Unsupported music URL; use official HTTPS music base")
        if music_key and (len(music_key) > 4096 or any(ch.isspace() or ord(ch) < 32 for ch in music_key)):
            raise ValueError("Invalid dedicated music key")
        self._url = music_url.rstrip("/")
        self._key = music_key
        self._http = http_client
        self.request_timeout = _seconds(request_timeout, "request timeout", 120)
        self.total_timeout = _seconds(total_timeout, "total timeout", 1800)
        self.poll_interval = _seconds(poll_interval, "poll interval", 60)
        if self.poll_interval < 5:
            raise ValueError("Poll interval must be at least five seconds")
        if isinstance(max_polls, bool) or not isinstance(max_polls, int) or not 1 <= max_polls <= 360:
            raise ValueError("Invalid poll bound")
        self.max_polls = max_polls
        self._sleep = sleep
        self._pending_http: set[asyncio.Task[Any]] = set()
        self._active_generation: asyncio.Task[Any] | None = None

    @staticmethod
    def _consume(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            task.exception()

    def _http_finished(self, task: asyncio.Task[Any]) -> None:
        self._pending_http.discard(task)
        self._consume(task)

    def _ready(self) -> None:
        if not self.enabled:
            raise MusicError("music_disabled")
        if not self._url or not self._key or self._http is None:
            raise MusicError("music_not_configured")

    async def _post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ready()
        if self._pending_http:
            raise MusicError("music_busy")
        request = asyncio.create_task(self._http.post(
            f"{self._url}/{operation}",
            headers={"Authorization": f"Bearer {self._key}",
                     "Content-Type": "application/json; charset=utf-8"},
            json=payload, timeout=self.request_timeout, follow_redirects=False,
        ))
        self._pending_http.add(request)
        request.add_done_callback(self._http_finished)
        try:
            done, _ = await asyncio.wait({request}, timeout=self.request_timeout)
            if not done:
                raise MusicError("request_timeout")
            response = request.result()
            status = response.status_code
            if isinstance(status, bool) or not isinstance(status, int):
                raise MusicError("invalid_response")
            if not 200 <= status < 300:
                # Do not retry submissions: a lost response may still have created a paid task.
                raise MusicError("provider_http_error")
            data = response.json()
            if not isinstance(data, dict):
                raise MusicError("invalid_response")
            return data
        except MusicError:
            raise
        except asyncio.TimeoutError:
            raise MusicError("request_timeout") from None
        except Exception:
            raise MusicError("provider_transport_error") from None
        finally:
            if not request.done():
                request.cancel()
                await asyncio.sleep(0)

    async def submit(self, prompt: str, lyrics: str | None = None,
                     *, response_format: str = "mp3") -> str:
        self._ready()
        caption = _text(prompt, "prompt", 4000)
        if not isinstance(response_format, str) or response_format not in self.FORMATS:
            raise ValueError("Invalid output format")
        body: dict[str, Any] = {"task": "text_to_music", "model_id": self.MODEL,
                                "caption": caption, "response_format": response_format}
        if lyrics is not None:
            body["lyrics"] = _text(lyrics, "lyrics", 16000)
        return _task_id((await self._post("submit", body)).get("task_id"))

    async def query(self, task_id: str) -> dict[str, Any]:
        task_id = _task_id(task_id)
        data = await self._post("query", {"task_id": task_id})
        status = data.get("status")
        if not isinstance(status, str) or status not in {"PENDING", "RUNNING", "SUCCESS", "FAILED"}:
            raise MusicError("invalid_task_status", task_id=task_id)
        if data.get("task") != "text_to_music":
            raise MusicError("invalid_task_type", task_id=task_id)
        result: dict[str, Any] = {"task_id": task_id, "status": status}
        for field, limit in (("caption", 4000), ("lyrics", 16000),
                             ("rewritten_caption", 4000), ("rewritten_lyrics", 16000)):
            if field in data:
                try:
                    result[field] = _text(data[field], field, limit, empty=True)
                except ValueError:
                    raise MusicError("invalid_response_text", task_id=task_id) from None
        if status == "FAILED":
            error = data.get("error")
            if not isinstance(error, dict) or not isinstance(error.get("stage"), str):
                raise MusicError("invalid_failure_response", task_id=task_id)
            result["error_stage"] = error["stage"] if error["stage"] in {
                "audio_transcode", "no_speech_detected", "censor", "internal"} else "unknown"
        if status == "SUCCESS":
            fmt, rate, audio = data.get("response_format"), data.get("sample_rate"), data.get("audio")
            if (not isinstance(fmt, str) or fmt not in self.FORMATS
                    or isinstance(rate, bool) or not isinstance(rate, int) or not 1 <= rate <= 384000
                    or not isinstance(audio, str) or not audio
                    or len(audio) > 4 * ((self.MAX_AUDIO_BYTES + 2) // 3)):
                raise MusicError("invalid_audio_response", task_id=task_id)
            try:
                decoded = base64.b64decode(audio, validate=True)
            except (ValueError, binascii.Error):
                raise MusicError("invalid_audio_response", task_id=task_id) from None
            if not decoded or len(decoded) > self.MAX_AUDIO_BYTES:
                raise MusicError("invalid_audio_response", task_id=task_id)
            result.update(audio=decoded, response_format=fmt, sample_rate=rate)
        return result

    async def generate(self, prompt: str, lyrics: str | None = None, *,
                       response_format: str = "mp3",
                       cancel_event: asyncio.Event | None = None) -> dict[str, Any]:
        """Submit once and boundedly poll. Task cancellation also stops local IO.

        A terminal FAILED raises MusicError('generation_failed', task_id=...).
        Returned audio is bytes, not a URL; caller owns playback and any persistence.
        """
        self._ready()
        if self._active_generation is not None and not self._active_generation.done():
            raise MusicError("music_busy")
        if cancel_event is not None and cancel_event.is_set():
            raise asyncio.CancelledError
        task_id: str | None = None

        async def run() -> dict[str, Any]:
            nonlocal task_id
            task_id = await self.submit(prompt, lyrics, response_format=response_format)
            for _ in range(self.max_polls):
                await self._sleep(self.poll_interval)
                result = await self.query(task_id)
                if result["status"] == "SUCCESS":
                    return result
                if result["status"] == "FAILED":
                    raise MusicError("generation_failed", task_id=task_id)
            raise MusicError("poll_limit", task_id=task_id)

        work = asyncio.create_task(run())
        self._active_generation = work
        work.add_done_callback(self._consume)
        cancellation = asyncio.create_task(cancel_event.wait()) if cancel_event is not None else None
        tasks = {work} if cancellation is None else {work, cancellation}
        try:
            done, _ = await asyncio.wait(tasks, timeout=self.total_timeout,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancellation is not None and cancellation in done:
                raise asyncio.CancelledError
            if work not in done:
                raise MusicError("generation_timeout", task_id=task_id)
            return work.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # Never wait indefinitely for a cancellation-resistant injected client.
            # Tracked outstanding work keeps the adapter busy until it truly settles.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
