from __future__ import annotations

import asyncio
import hashlib
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


def voice_profile(emotion: str) -> dict[str, str]:
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
    """Pluggable TTS. edge-tts is the default because it needs no GPU and
    exposes pitch/rate per utterance, which is what carries 'personality' here."""

    def __init__(self) -> None:
        self.engine = settings.tts_engine
        self._cache: dict[str, bytes] = {}

    def profile(self, emotion: str) -> dict[str, Any]:
        return voice_profile(emotion)

    async def synthesize(self, text: str, emotion: str = "base") -> bytes:
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return b""
        profile = self.profile(emotion)
        key = hashlib.sha1(
            f"{self.engine}|{profile}|{text}".encode("utf-8")
        ).hexdigest()
        if key in self._cache:
            return self._cache[key]
        if len(self._cache) > 256:
            self._cache.clear()
        if self.engine == "edge_tts":
            audio = await self._edge(text, profile)
        elif self.engine == "silent":
            audio = b""
        else:
            audio = await self._edge(text, profile)
        self._cache[key] = audio
        return audio

    async def _edge(self, text: str, profile: dict[str, Any]) -> bytes:
        import edge_tts

        communicate = edge_tts.Communicate(
            text,
            voice=profile["voice"],
            rate=profile.get("rate", "+0%"),
            pitch=profile.get("pitch", "+0Hz"),
            volume=profile.get("volume", "+0%"),
        )
        buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio" and chunk["data"]:
                buffer.extend(chunk["data"])
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