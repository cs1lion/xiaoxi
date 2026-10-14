"""Dedicated opt-in music routes; importing this module never bootstraps config.

Factories receive MusicTransport constructor keyword arguments. Tests can inject
both a generator factory and an HTTP client without any provider connection.
"""
from __future__ import annotations

import asyncio
import base64
import json as jsonlib
from types import SimpleNamespace
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from .music import MusicError, MusicTransport

MAX_AUDIO_BYTES = 32 * 1024 * 1024
MAX_BODY_BYTES = 48 * 1024 * 1024
TOTAL_TIMEOUT = 600.0
CANCELLATION_NOTICE = (
    "Stopping browser waiting does not cancel a submitted provider task; "
    "it may still complete and incur charges."
)
_generation_lock: asyncio.Lock | None = None


class CreateMusicRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caption: StrictStr = Field(min_length=1, max_length=2000)
    lyrics: StrictStr = Field(default="", max_length=8000)
    user_confirmed: StrictBool = False


class BoundedMusicHTTPClient:
    """Stream and cap JSON before parsing; never follow provider redirects."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def post(self, url: str, *, headers: dict[str, str],
                   json: dict[str, Any], timeout: float,
                   follow_redirects: bool = False) -> Any:
        # Reject redirect opt-in even if a caller tries to enable it.
        if follow_redirects:
            raise MusicError("provider_transport_error")
        async with self.client.stream("POST", url, headers=headers, json=json,
                                      timeout=timeout, follow_redirects=False) as response:
            if not 200 <= response.status_code < 300:
                return SimpleNamespace(status_code=response.status_code, json=lambda: {})
            length = response.headers.get("content-length")
            if length is not None:
                try:
                    if int(length) < 0 or int(length) > MAX_BODY_BYTES:
                        raise MusicError("provider_response_too_large")
                except ValueError:
                    raise MusicError("invalid_response") from None
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    raise MusicError("provider_response_too_large")
                body.extend(chunk)
            try:
                payload = jsonlib.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                raise MusicError("invalid_response") from None
            if not isinstance(payload, dict):
                raise MusicError("invalid_response")
            return SimpleNamespace(status_code=response.status_code, json=lambda: payload)


def _configured(settings: Any) -> bool:
    url, key = getattr(settings, "music_url", ""), getattr(settings, "music_api_key", "")
    if getattr(settings, "music_enabled", False) is not True:
        return False
    if not isinstance(url, str) or not isinstance(key, str) or not key:
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.netloc == "api.stepfun.com"
                and parsed.path.rstrip("/") == "/v1/audio/music"
                and not parsed.query and not parsed.fragment
                and len(key) <= 4096 and not any(ch.isspace() or ord(ch) < 32 for ch in key))
    except ValueError:
        return False


def create_music_router(settings: Any, transport_factory: Callable[..., Any] | None = None) -> APIRouter:
    """Parent passes explicit settings and includes the returned router.

    No shared LLM/TTS credential fallback, persistence, or remote audio URL fetch.
    The process-wide lock bounds active requests to one (not a multi-worker quota).
    """
    router = APIRouter(prefix="/api/music", tags=["music"])
    factory = transport_factory or MusicTransport

    @router.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        return {"configured": _configured(settings), "mode": "original_music_async",
                "max_caption_chars": 2000, "max_lyrics_chars": 8000,
                "max_audio_bytes": MAX_AUDIO_BYTES, "supports_remote_cancel": False,
                "cancellation_notice": CANCELLATION_NOTICE}

    @router.post("/create")
    async def create(request: CreateMusicRequest) -> dict[str, Any]:
        global _generation_lock
        if not _configured(settings):
            raise HTTPException(503, "Original music is not enabled and configured.")
        if not request.user_confirmed:
            raise HTTPException(422, "Confirm original lyrics ownership and paid generation permission.")
        caption, lyrics = request.caption.strip(), request.lyrics.strip()
        if not caption or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in caption + lyrics):
            raise HTTPException(422, "Provide a valid original music description and lyrics.")
        if _generation_lock is None:
            _generation_lock = asyncio.Lock()
        if _generation_lock.locked():
            raise HTTPException(409, "Another music request is in progress.")
        async with _generation_lock:
            try:
                async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
                    transport = factory(music_url=settings.music_url, music_key=settings.music_api_key,
                                        enabled=True, http_client=BoundedMusicHTTPClient(client),
                                        total_timeout=TOTAL_TIMEOUT)
                    result = await asyncio.wait_for(
                        transport.generate(caption, lyrics or None, response_format="mp3"),
                        timeout=TOTAL_TIMEOUT)
                audio, fmt = result.get("audio"), result.get("response_format")
                mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac",
                        "opus": "audio/ogg"}.get(fmt)
                if not isinstance(audio, bytes) or not audio or len(audio) > MAX_AUDIO_BYTES or mime is None:
                    raise MusicError("invalid_audio_response")
                return {"audio_base64": base64.b64encode(audio).decode("ascii"), "mime": mime,
                        "cancellation_notice": CANCELLATION_NOTICE}
            except asyncio.TimeoutError:
                raise HTTPException(504, "Music waiting timed out. " + CANCELLATION_NOTICE) from None
            except MusicError as exc:
                status = 504 if exc.code in {"generation_timeout", "request_timeout", "poll_limit"} else 502
                raise HTTPException(status, "Music generation did not complete. " + CANCELLATION_NOTICE) from None
            except (ValueError, TypeError):
                raise HTTPException(503, "Music provider configuration or response is invalid.") from None
            except Exception:
                raise HTTPException(502, "Music generation failed. " + CANCELLATION_NOTICE) from None

    return router
