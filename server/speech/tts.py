from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

from ..config import pacing_cfg, persona_cfg, settings

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=\n)")


@dataclass
class SpeechChunk:
    text: str
    audio: bytes
    emotion: str
    index: int
    # 协议第十一节的可选 chunk={seq,of,chars,pause_after_ms,breath}。
    # 旧调用方忽略它即可；缺失时前端退回现有逐句行为。
    chunk: dict[str, Any] | None = None


def voice_profile(emotion: str) -> dict[str, Any]:
    voices = persona_cfg()["tts_emotions"]
    return voices.get(emotion) or voices.get(settings.tts_default_emotion) or voices["base"]


def _bump_offset(value: Any, delta: float, unit: str) -> Any:
    """Add delta to an edge_tts-style signed offset (\"+10%\" / \"+8Hz\")."""
    if not isinstance(value, str):
        return value
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)", value)
    if not match:
        return value
    number = float(match.group(1)) + delta
    return f"{'+' if number >= 0 else ''}{number:g}{unit}"


def _delivery_factor(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def apply_voice_delivery(profile: dict[str, Any]) -> dict[str, Any]:
    """Layer the global voice_delivery rate/pitch (pacing.yaml §7.1) over a persona
    emotion profile. rate/pitch are additive offsets on top of the per-emotion
    values, so a 1.0/1.0 (or absent) config is a byte-for-byte no-op. stepaudio
    ignores rate/pitch (it is instruction-driven), which this leaves untouched.
    """
    delivery = pacing_cfg().get("voice_delivery") or {}
    rate_f = _delivery_factor(delivery.get("rate"))
    pitch_f = _delivery_factor(delivery.get("pitch"))
    if rate_f is None and pitch_f is None:
        return profile
    result = dict(profile)
    if rate_f is not None and rate_f != 1.0:
        result["rate"] = _bump_offset(profile.get("rate", "+0%"), (rate_f - 1.0) * 100, "%")
    if pitch_f is not None and pitch_f != 1.0:
        result["pitch"] = _bump_offset(profile.get("pitch", "+0Hz"), (pitch_f - 1.0) * 100, "Hz")
    return result


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


_MICRO_WEAK = "，,、；;：:"
_MICRO_STRONG = "。！？!?…"
_MICRO_PARTICLES = "吧呀啊哦呢嘛哈哟欸诶呐咯啦哇嘻"


def first_chunk_cut(text: str, min_chars: int = 6, max_chars: int = 12) -> int:
    """首包激进切分的切点长度；不适用时返回 0。

    在 [min_chars, max_chars] 里找最早的标点/语气词边界，切点含其后紧跟的
    闭合引号。窗口内没有边界（或全文短于窗口）时返回 0，调用方退回正常目标
    长度切分——激进模式只允许把首包提前，不允许把句子切碎。
    """
    text = text or ""
    low = max(1, int(min_chars))
    high = max(low, int(max_chars))
    for length in range(low, min(high, len(text)) + 1):
        if (text[length - 1] in _MICRO_WEAK or text[length - 1] in _MICRO_STRONG
                or text[length - 1] in _MICRO_PARTICLES):
            end = length
            while end < len(text) and text[end] in _MICRO_CLOSERS:
                end += 1
            return end
    return 0


_MICRO_CLOSERS = "”’\"』」）)》"


def split_for_speech_micro(text: str, target_chars: int = 20, max_chars: int = 60, *,
                           aggressive_first: bool = False, first_min: int = 6,
                           first_max: int = 12) -> list[str]:
    """Lossless micro-sentence split for long-voice streaming (F2).

    Unlike ``split_for_speech`` (which drops the comma at a cut and strips
    whitespace), this never removes a character: the pieces concatenate back to
    the exact input. That lets the caller keep Unicode-codepoint offsets so
    ``slice_performance`` rebases motion anchors exactly, and so ``"".join(pieces)
    == text`` stays true. It prefers clause/sentence boundaries near
    ``target_chars`` and only hard-cuts at ``max_chars`` when no boundary is in
    range. Old callers keep using ``split_for_speech`` unchanged.

    ``aggressive_first`` implements the fast first-chunk strategy (protocol
    section 11): the first piece ends at the earliest punctuation or modal
    particle once the reply has 6..12 characters, so the user hears audio while
    the model is still writing. When the window holds no boundary the whole
    split degrades to the normal target-length behaviour, and a reply that
    already fits in a single micro-sentence is never split at all: there is no
    second sentence to race ahead of, so an extra breath pause would only make
    one short line choppy. The lossless invariant is what keeps this safe for
    performance anchors: offsets keep accumulating over the same text either
    way.
    """
    text = text or ""
    n = len(text)
    if n == 0:
        return []
    target = max(1, int(target_chars))
    cap = max(target, int(max_chars))
    pieces: list[str] = []
    i = 0
    if aggressive_first:
        cut = first_chunk_cut(text, first_min, first_max) if n > target else 0
        if cut > 0:
            pieces.append(text[:cut])
            i = cut
    while i < n:
        if n - i <= target:
            pieces.append(text[i:])
            break
        window_end = min(i + cap, n)
        target_end = i + target
        best = -1
        last_boundary = -1
        for k in range(i + 1, window_end + 1):
            prev = text[k - 1]
            if prev in _MICRO_STRONG or prev in _MICRO_WEAK:
                end = k
                while end < n and end < i + cap and text[end] in _MICRO_CLOSERS:
                    end += 1
                if k <= target_end:
                    best = end
                last_boundary = end
        if best > i:
            pieces.append(text[i:best])
            i = best
        elif last_boundary > i:
            pieces.append(text[i:last_boundary])
            i = last_boundary
        else:
            cut = min(i + cap, n)
            pieces.append(text[i:cut])
            i = cut
    return [p for p in pieces if p]


def micro_pause(piece: str, *, clause_ms: int = 120, sentence_ms: int = 260,
                max_ms: int = 400) -> tuple[int, bool]:
    """Breath-pause suggestion (pause_after_ms, breath) from a micro-sentence's
    trailing boundary. Sentence-final punctuation is a natural breath point;
    clause punctuation is a shorter micro-pause; a hard-cut continuation has none.
    """
    for ch in reversed(piece or ""):
        if ch in _MICRO_STRONG:
            return min(max_ms, max(0, sentence_ms)), True
        if ch in _MICRO_WEAK:
            return min(max_ms, max(0, clause_ms)), False
        if ch in _MICRO_CLOSERS:
            continue
        break
    return 0, False


def speech_micro_cfg() -> dict[str, Any] | None:
    """Micro-chunk parameters from pacing.yaml, or None when the feature is off.

    ``speech.enabled`` is the single switch (protocol section 11): off restores
    the legacy 60-character sequential synthesis byte for byte.
    """
    speech = pacing_cfg().get("speech") or {}
    if not speech.get("enabled", False):
        return None
    micro = speech.get("micro_chunk") or {}
    first = speech.get("first_chunk") or {}
    pause = speech.get("pause") or {}
    target = max(1, int(micro.get("target_chars", 20)))
    return {
        "target_chars": target,
        "max_chars": max(target, int(micro.get("max_chars", 60))),
        "aggressive_first": bool(first.get("enabled", True)),
        "first_min": max(1, int(first.get("min_chars", 6))),
        "first_max": max(1, int(first.get("max_chars", 12))),
        "clause_ms": int(pause.get("clause_ms", 120)),
        "sentence_ms": int(pause.get("sentence_ms", 260)),
        "max_ms": int(pause.get("max_ms", 400)),
    }


def iter_micro_chunks(text: str, *, total_known: bool = False, **options: Any):
    """Yield protocol-11 chunk envelopes for one already complete reply.

    Shared by the streaming orchestrator (total unknown while the model writes)
    and the fixed-reply deliverer (total known), so both paths split, count and
    pause identically. Each item is ``{"seq", "of", "chars", "text",
    "pause_after_ms", "breath"}``; ``of`` stays None until the total is known.
    """
    pieces = split_for_speech_micro(
        text,
        options.get("target_chars", 20),
        options.get("max_chars", 60),
        aggressive_first=bool(options.get("aggressive_first", False)),
        first_min=options.get("first_min", 6),
        first_max=options.get("first_max", 12))
    total = len(pieces)
    for seq, piece in enumerate(pieces):
        pause_ms, breath = micro_pause(
            piece,
            clause_ms=int(options.get("clause_ms", 120)),
            sentence_ms=int(options.get("sentence_ms", 260)),
            max_ms=int(options.get("max_ms", 400)))
        yield {"seq": seq, "of": total if total_known else None, "chars": len(piece),
               "text": piece, "pause_after_ms": pause_ms, "breath": breath}


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
        return await self.synthesize_profile(text, apply_voice_delivery(self.profile(emotion)))

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
        resolved = profile if profile is not None else apply_voice_delivery(self.profile(emotion))
        key = self._cache_key(text, resolved)
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
        """Yield audio per micro-sentence, carrying the protocol-11 chunk field.

        Fixed replies (proactive chatter, star-map narration, assistant results)
        share the streaming path's split, so they get the same fast first chunk
        and the same breath pauses. The chunk envelope rides on each
        ``SpeechChunk``; providers and callers that ignore it keep the legacy
        per-sentence behaviour. Feature-off keeps the old 60-character windows.
        """
        micro = speech_micro_cfg()
        if micro is None:
            for index, piece in enumerate(split_for_speech(text)):
                audio = await self.synthesize(piece, emotion)
                yield SpeechChunk(text=piece, audio=audio, emotion=emotion, index=index)
                await asyncio.sleep(0)
            return
        for index, item in enumerate(iter_micro_chunks(text, total_known=True, **micro)):
            audio = await self.synthesize(item["text"], emotion)
            yield SpeechChunk(text=item["text"], audio=audio, emotion=emotion, index=index,
                              chunk={"seq": item["seq"], "of": item["of"], "chars": item["chars"],
                                     "pause_after_ms": item["pause_after_ms"], "breath": item["breath"]})
            await asyncio.sleep(0)


class LocalTTSHook:
    """Drop-in point for a GPU model (CosyVoice2 / IndexTTS2 / GPT-SoVITS).

    Enable with TTS_ENGINE=local and implement synthesize() -- the rest of the
    stack only ever asks for (text, emotion) -> mp3 bytes.
    """

    def __init__(self) -> None:  # pragma: no cover - extension point
        raise NotImplementedError("implement this and wire it in tts.py")


tts = TTSEngine()
