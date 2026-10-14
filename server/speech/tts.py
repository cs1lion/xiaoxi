from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ..config import persona_cfg, settings

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=\n)")


@dataclass
class SpeechChunk:
    text: str
    audio: bytes
    emotion: str
    index: int


def voice_profile(emotion: str) -> dict[str, Any]:
    voices = persona_cfg()["tts_emotions"]
    return voices.get(emotion) or voices.get(settings.tts_default_emotion) or voices["base"]


def pick_emotion(text: str, hint: str | None = None) -> str:
    if hint and hint in persona_cfg()["tts_emotions"]:
        return hint
    from ..dialogue.orchestrator import detect_emotion

    return detect_emotion(text)


def split_for_speech(text: str, max_chars: int = 60) -> list[str]:
    pieces: list[str] = []
    buffer = ""
    for sentence in _SENT_SPLIT.split(text or ""):
        sentence = (sentence or "").strip()
        if not sentence:
            continue
        while len(sentence) > max_chars:
            cut = sentence.rfind("，", 0, max_chars)
            cut = cut if cut > 8 else max_chars
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip("，")
        if not sentence:
            continue
        if len(buffer) + len(sentence) > max_chars and buffer:
            pieces.append(buffer)
            buffer = ""
        buffer += sentence
    if buffer:
        pieces.append(buffer)
    return [p for p in pieces if p]


class TTSEngine:
    """Pluggable TTS. Every engine answers the same question:
    (text, emotion) -> audio bytes. Personality lives in two places -- the
    persona.yaml emotion table, and whatever knobs the engine exposes.
    """

    def __init__(self) -> None:
        self.engine = settings.tts_engine
        self._cache: dict[str, bytes] = {}
        self._word_timings: dict[str, list[dict[str, Any]]] = {}

    def profile(self, emotion: str) -> dict[str, Any]:
        return voice_profile(emotion)

    async def synthesize(self, text: str, emotion: str = "base") -> bytes:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return b""
        return await self.synthesize_profile(text, self.profile(emotion))

    async def synthesize_profile(self, text: str, profile: dict[str, Any]) -> bytes:
        """Explicit per-call profile; never mutates persona or engine defaults."""
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return b""
        key = self._cache_key(text, profile)
        if key in self._cache:
            return self._cache[key]
        if len(self._cache) > 256:
            self._cache.clear()
            self._word_timings.clear()
        timings: list[dict[str, Any]] = []
        if self.engine == "edge_tts":
            audio = await self._edge(text, profile, word_timings=timings)
        elif self.engine == "stepaudio":
            audio = await self._stepaudio(text, profile)
        elif self.engine == "silent":
            audio = b""
        else:
            audio = await self._edge(text, profile, word_timings=timings)
        self._cache[key] = audio
        if timings:
            self._word_timings[key] = timings
        return audio

    def _cache_key(self, text: str, profile: dict[str, Any]) -> str:
        return hashlib.sha1(f"{self.engine}|{profile}|{text}".encode("utf-8")).hexdigest()

    def word_timings(self, text: str, emotion: str = "base", *,
                     profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Actual provider boundaries from the matching cached synthesis, in seconds.

        StepAudio and silent synthesis supply no timestamps. Callers can omit
        timing metadata and use an explicitly approximate alignment instead.
        """
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return []
        key = self._cache_key(text, profile if profile is not None else self.profile(emotion))
        return [dict(item) for item in self._word_timings.get(key, [])]

    async def _stepaudio(self, text: str, profile: dict[str, Any]) -> bytes:
        """StepAudio 2.5 TTS -- instruction is natural-language direction, so the
        emotion table can say *how* to perform rather than nudging pitch by 8Hz."""
        import httpx

        from ..llm import llm

        client = await llm.client()
        payload: dict[str, Any] = {
            "model": settings.tts_model,
            "input": text[:1000],  # service hard limit
            "voice": profile.get("voice") or settings.tts_voice,
            "response_format": settings.tts_format,
            "sample_rate": settings.tts_sample_rate,
            "text_normalization": "standard",  # latency-sensitive path
        }
        instruction = (profile.get("instruction") or "").strip()
        if instruction:
            payload["instruction"] = instruction[:200]  # service hard limit

        resp = await client.post(f"{llm.base_url}/audio/speech", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"tts {resp.status_code}: {resp.text[:200]}")
        return resp.content

    async def _edge(self, text: str, profile: dict[str, Any], *,
                    word_timings: list[dict[str, Any]] | None = None) -> bytes:
        import edge_tts

        voice = profile.get("voice", "")
        if not voice.startswith("zh-CN"):  # profile is written for the other engine
            voice = "zh-CN-XiaoxiaoNeural"
        options = {"voice": voice, "rate": profile.get("rate", "+0%"),
                   "pitch": profile.get("pitch", "+0Hz"), "volume": profile.get("volume", "+0%")}
        # edge-tts 6.x emits word boundaries by default; newer releases need
        # an explicit selection. Keep the declared >=6.1.10 compatibility.
        parameters = inspect.signature(edge_tts.Communicate).parameters
        if "boundary" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            options["boundary"] = "WordBoundary"
        communicate = edge_tts.Communicate(text, **options)
        buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio" and chunk["data"]:
                buffer.extend(chunk["data"])
            elif chunk["type"] == "WordBoundary" and word_timings is not None:
                try:
                    start = float(chunk["offset"]) / 10_000_000
                    end = start + float(chunk["duration"]) / 10_000_000
                    word = chunk["text"]
                except (KeyError, TypeError, ValueError, OverflowError):
                    continue
                if (isinstance(word, str) and word and math.isfinite(start) and math.isfinite(end)
                        and 0 <= start <= end):
                    word_timings.append({"text": word, "start": start, "end": end})
        return bytes(buffer)

    async def speak(self, text: str, emotion: str = "base") -> AsyncIterator[SpeechChunk]:
        pieces = split_for_speech(text)
        for index, piece in enumerate(pieces):
            audio = await self.synthesize(piece, emotion)
            yield SpeechChunk(text=piece, audio=audio, emotion=emotion, index=index)
            await asyncio.sleep(0)


class LocalTTSHook:
    """Drop-in point for a GPU model (CosyVoice2 / IndexTTS2 / GPT-SoVITS).

    Enable with TTS_ENGINE=local and implement synthesize() -- the rest of the
    stack only ever asks for (text, emotion) -> mp3 bytes.
    """

    def __init__(self) -> None:  # pragma: no cover - extension point
        raise NotImplementedError("implement this and wire it in tts.py")


tts = TTSEngine()
