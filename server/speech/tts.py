from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import logging
import math
import re
import struct
import wave
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from typing import Any, AsyncIterator

from ..config import pacing_cfg, persona_cfg, settings

_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=\n)")


def trim_wav_silence(audio: bytes, *, threshold: float | None = None,
                     pad_ms: int | None = None) -> bytes:
    """Strip leading and trailing near-silence from a 16-bit PCM WAV.

    The provider pads every chunk with roughly 0.36s before and 0.38s after the
    speech. Stacked on the designed breath pauses in pacing.yaml, a comma gap
    that should be 140ms becomes about 880ms, so a three-chunk reply carries
    seconds of dead air and reads as halting.

    Returns the input unchanged when it is not a WAV this can parse, so an
    unexpected container never becomes a synthesis failure.
    """
    if not audio or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return audio
    limit = settings.tts_trim_threshold if threshold is None else threshold
    keep = settings.tts_trim_pad_ms if pad_ms is None else pad_ms
    try:
        with wave.open(BytesIO(audio), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError, struct.error):
        return audio
    if width != 2 or channels < 1 or rate <= 0 or not frames:
        return audio
    samples = struct.unpack(f"<{len(frames) // 2}h", frames[:len(frames) // 2 * 2])
    stride = channels
    count = len(samples) // stride
    if count == 0:
        return audio

    def loud(index: int) -> bool:
        base = index * stride
        return any(abs(samples[base + ch]) / 32768.0 > limit for ch in range(stride))

    first = next((i for i in range(count) if loud(i)), None)
    if first is None:
        return audio
    last = count - 1
    while last > first and not loud(last):
        last -= 1
    keep_samples = min(count, int(rate * max(0, keep) / 1000.0))
    start = max(0, first - keep_samples)
    end = min(count, last + 1 + keep_samples)
    if start == 0 and end == count:
        return audio
    body = struct.pack(f"<{(end - start) * stride}h", *samples[start * stride:end * stride])
    out = BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(body)
    return out.getvalue()


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
    """情绪 → 声学参数。未知情绪显式告警后回落，不再静默变平。

    2026-11-05：此前 `voices.get(...) or ...` 的回落链没有任何日志，一个拼错
    的情绪标签会让整句话以 base 播出而毫无痕迹——陪伴场景里表现为「她突然
    没情绪了」，只能靠听去排查。回落行为本身不变（不抛错、不阻断语音），
    只补一条可查的告警。回归见 scripts/test_core.py::test_tts_split。
    """
    voices = persona_cfg()["tts_emotions"]
    if emotion not in voices:
        import logging

        logging.getLogger("server.speech.tts").warning(
            "unknown emotion %r, falling back to %r", emotion,
            getattr(settings, "tts_default_emotion", "base"))
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


def _signed_percent(value: Any) -> float | None:
    """把 edge_tts 风格的 "+10%" / "-8%" 解析成小数（0.10 / -0.08）。"""
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    match = re.match(r"\s*([+-]?\d+(?:\.\d+)?)\s*%", value)
    if not match:
        return None
    return float(match.group(1)) / 100.0


def _fish_speed(profile: dict[str, Any]) -> float:
    """persona 的 rate（"+10%"）→ Fish 的 prosody.speed（1.10）。

    Fish 的 speed 是倍率（0.5~2.0，1.0 = 正常），与 edge_tts 的 rate 是百分比偏移，
    两者语义不同但方向一致：越大越快。所以 1 + rate/100。
    """
    rate = _signed_percent(profile.get("rate"))
    if rate is None:
        return 1.0
    return max(0.5, min(2.0, 1.0 + rate))


def _fish_volume_db(profile: dict[str, Any]) -> float:
    """persona 的 volume（"+3%" / "-12%"）→ Fish 的 prosody.volume（dB）。

    百分比转 dB 用 20*log10(1 + pct/100)：-12% ≈ -1.1 dB，+3% ≈ +0.26 dB。
    人耳对 dB 的敏感度远高于线性百分比，所以这个换算会显得比原值"轻"——
    那是物理事实，不是换算写错。
    """
    pct = _signed_percent(profile.get("volume"))
    if pct is None:
        return 0.0
    ratio = max(0.01, 1.0 + pct)
    return 20.0 * math.log10(ratio)


def _fish_word_timings(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fish `alignment.segments` → `word_timings`（2026-11-12 实测形状）。

    Fish 的 segments 是**累计**的：每个 SSE 事件都带一份从 0 开始的完整列表，
    取最长的一份即可；把每个事件拼起来会重复整段。时间是相对整段音频的绝对
    秒数（`chunk_audio_offset_sec` 实测恒为 0，不能当分块偏移加）。

    校验按前端 `providerMap` 的同一套口径（`web/avatar/drivers/speech-plan.js:44`）：
    文本非空、start/end 有限、`0 <= start < end`、列表整体单调。
    任何一条不合格就整份返回空 —— 前端本来也会整份退回估算，与其送坏数据
    让下游猜，不如在这里明确降级。
    """
    best: list[Any] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segments = (event.get("alignment") or {}).get("segments")
        if isinstance(segments, list) and len(segments) > len(best):
            best = segments
    out: list[dict[str, Any]] = []
    last_end = 0.0
    for segment in best:
        if not isinstance(segment, dict):
            continue
        word = segment.get("text")
        try:
            start = float(segment["start"])
            end = float(segment["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not (isinstance(word, str) and word.strip()):
            continue
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            continue
        if start < last_end - 1e-3:
            return []  # 乱序列表整体不可信
        out.append({"text": word, "start": start, "end": end})
        last_end = end
    return out


def _fish_text(text: str, profile: dict[str, Any]) -> str:
    """把 persona 的 instruction 变成 Fish 的方括号提示。

    Fish S2 的方括号标签接受**自然语言描述**，不限于固定 tag 表
    （官方 emotions 文档：「Bracket cues can use natural language descriptions
    and are not limited to a fixed set of tags」）。所以 persona.yaml 的中文
    instruction 不需要另造映射表 —— 但**不能整条照搬**。

    2026-11-12 实测（每档 3 次，Fish ASR 转写回来比对）：
      整条 instruction 进方括号 → 七档里 **base 与 得意 3/3 泄漏**，
      泄漏的内容是 instruction 的**后半截**被当成正文念了出来
      （base 念出「不要播音腔，不刻意表演」）。
      只取**第一子句**（逗号前）→ 七档全部 0/2 泄漏，
      且七档音频字节仍两两不同（情绪分化保留）。

    这与 Fish 官方文档的说法一致：方括号描述要**简洁**，
    「Don't make bracket descriptions so long that they interrupt readability」。
    逗号分隔的多子句长描述会被模型当成要念的内容。

    所以这里只取第一子句 —— 那才是 persona 情绪的核心
    （「语气自然随意」「带点调侃和促狭」「有点小得意」…），
    后半截是补充说明，丢掉它们不改变情绪归属。
    """
    instruction = (profile.get("instruction") or "").strip()
    if not instruction:
        return text
    # 方括号内的文本不能含 [ ] < > —— persona 侧已由 test_fish_engine 钉住，
    # 这里再挡一次，避免将来有人改了 yaml 而忘了改这里。
    if any(ch in instruction for ch in "[]<>"):
        logging.getLogger("server.speech.tts").warning(
            "fish instruction 含 Fish 会当标签解析的字符，原样丢弃：%r", instruction)
        return text
    cue = instruction.replace("，", ",").split(",")[0].strip()
    if not cue:
        return text
    return f"[{cue}] {text}"


class TTSEngine:
    """Pluggable TTS. Every engine answers the same question:
    (text, emotion) -> audio bytes. Personality lives in two places -- the
    persona.yaml emotion table, and whatever knobs the engine exposes.
    """

    def __init__(self) -> None:
        self.engine = settings.tts_engine
        # 2026-11-06：原先普通 dict + 「超过 256 条一次性 clear()」，第 257 条
        # 起命中率断崖归零，连 _word_timings 一起被清空，已缓存音频拿不到
        # 字级时间戳（唇形/字幕静默退化）。改成 LRU，只淘汰最旧条目，且只
        # 淘汰它自己的 timings，存活 key 的时间戳一律保留。
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._word_timings: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._metrics: dict[str, int] = {"hit": 0, "miss": 0, "evict": 0}

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
            self._cache.move_to_end(key)
            self._metrics["hit"] += 1
            return self._cache[key]
        self._metrics["miss"] += 1
        while len(self._cache) >= 256:
            oldest, _ = self._cache.popitem(last=False)
            self._word_timings.pop(oldest, None)
            self._metrics["evict"] += 1
        timings: list[dict[str, Any]] = []
        if self.engine == "edge_tts":
            audio = await self._edge(text, profile, word_timings=timings)
        elif self.engine == "stepaudio":
            audio = await self._stepaudio(text, profile)
        elif self.engine == "fish":
            audio = await self._fish(text, profile, word_timings=timings)
        elif self.engine == "silent":
            audio = b""
        else:
            audio = await self._edge(text, profile, word_timings=timings)
        self._cache[key] = audio
        if timings:
            self._word_timings[key] = timings
        total = self._metrics["hit"] + self._metrics["miss"]
        if total % 100 == 0:
            logging.getLogger("server.speech.tts").info(
                "tts_cache hit=%d miss=%d evict=%d entries=%d hit_rate=%.3f",
                self._metrics["hit"], self._metrics["miss"], self._metrics["evict"],
                len(self._cache), self._metrics["hit"] / total)
        return audio

    def voice_metrics(self) -> dict[str, Any]:
        """只读指标快照：不落盘、不改 WS 帧，供诊断与报告层取用。"""
        total = self._metrics["hit"] + self._metrics["miss"]
        return {**self._metrics, "entries": len(self._cache),
                "hit_rate": (self._metrics["hit"] / total) if total else 0.0}

    def _cache_key(self, text: str, profile: dict[str, Any]) -> str:
        # The wire format decides the bytes (trimming re-encodes to WAV), so it
        # belongs in the key. Without it, toggling tts_trim_silence on a warm
        # cache hands back the other format's bytes.
        wire = "wav" if settings.tts_trim_silence and self.engine == "stepaudio" else settings.tts_format
        return hashlib.sha1(f"{self.engine}|{wire}|{profile}|{text}".encode("utf-8")).hexdigest()

    def word_timings(self, text: str, emotion: str = "base", *,
                     profile: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Actual provider boundaries from the matching cached synthesis, in seconds.

        Edge TTS supplies WordBoundary events; Fish's streaming endpoint supplies
        per-character `alignment.segments` (see `_fish_word_timings`). StepAudio
        and silent synthesis supply no timestamps. Callers can omit timing
        metadata and use an explicitly approximate alignment instead.
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
        # 修剪只能在 PCM 上做，所以开关打开时按 WAV 取流；浏览器 decodeAudioData
        # 按容器嗅探，交付格式不必与 settings.tts_format 一致。
        wire_format = "wav" if settings.tts_trim_silence else settings.tts_format
        payload: dict[str, Any] = {
            "model": settings.tts_model,
            "input": text[:1000],  # service hard limit
            "voice": profile.get("voice") or settings.tts_voice,
            "response_format": wire_format,
            "sample_rate": settings.tts_sample_rate,
            "text_normalization": "standard",  # latency-sensitive path
        }
        instruction = (profile.get("instruction") or "").strip()
        if instruction:
            payload["instruction"] = instruction[:200]  # service hard limit

        resp = await client.post(f"{llm.base_url}/audio/speech", json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"tts {resp.status_code}: {resp.text[:200]}")
        audio = resp.content
        if settings.tts_trim_silence:
            audio = trim_wav_silence(audio)
        return audio

    async def _fish(self, text: str, profile: dict[str, Any], *,
                    word_timings: list[dict[str, Any]] | None = None) -> bytes:
        """Fish Audio TTS（2026-11-12）。

        与 stepaudio 的关键差别：**instruction 是自然语言括号标签**，不是引擎旋钮。
        persona.yaml 的七档 instruction 直接放进 `text` 的方括号，Fish S2 认这个。
        语速走 prosody.speed（倍率），音量走 prosody.volume（dB）。
        **没有 pitch 旋钮** —— 语气表演力全交给 instruction。

        逐字对齐走 `POST /v1/tts/stream/with-timestamp`（SSE）：每个事件带
        `audio_base64` 与累计的 `alignment.segments`。实测段粒度是中文**逐字**、
        不含标点、方括号 instruction 不进 segments（正好与前端
        `providerMap` 的「去标点后逐字匹配」口径一致）。非流式 `/v1/tts`
        不返回任何时间戳，所以只要走 Fish 就必须用这个端点。

        凭据与出口独立于 LLM（api.fish.audio），不与 chat/completions 共用。
        """
        import httpx

        key = str(settings.fish_api_key or "").strip()
        if not key:
            raise RuntimeError(
                "TTS_ENGINE=fish 需要 FISH_API_KEY（.env）；不配就报错，"
                "不许静默退回别的引擎")
        base = str(settings.fish_base_url or "https://api.fish.audio").rstrip("/")
        model = str(settings.fish_tts_model or "s2.1-pro-free")
        payload: dict[str, Any] = {
            "text": _fish_text(text, profile),
            "format": settings.tts_format if settings.tts_format in ("mp3", "wav", "opus", "pcm")
                      else "mp3",
            "language": "zh-CN",
            "prosody": {
                "speed": _fish_speed(profile),
                "volume": _fish_volume_db(profile),
                "normalize_loudness": True,
            },
            "normalize": True,
            # 流式端点参数：一次请求远小于 200 字，分块只为边生成边下发。
            "chunk_length": 200,
            "latency": str(getattr(settings, "fish_tts_latency", "balanced") or "balanced"),
        }
        reference_id = str(profile.get("fish_reference_id") or settings.fish_tts_reference_id or "").strip()
        if reference_id:
            payload["reference_id"] = reference_id
        headers = {
            "Authorization": f"Bearer {key}",
            # Fish 用 model **请求头**选模型，不是 body 字段。
            "model": model,
        }
        events: list[dict[str, Any]] = []
        audio = bytearray()
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", f"{base}/v1/tts/stream/with-timestamp",
                                     json=payload, headers=headers) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise RuntimeError(f"fish tts {resp.status_code}: {resp.text[:200]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    encoded = event.get("audio_base64")
                    if isinstance(encoded, str) and encoded:
                        try:
                            audio.extend(base64.b64decode(encoded))
                        except (ValueError, TypeError):
                            continue
                    events.append(event)
        if word_timings is not None:
            word_timings.extend(_fish_word_timings(events))
        return bytes(audio)

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
    """预留的本地 GPU 音色扩展点（CosyVoice2 / IndexTTS2 / GPT-SoVITS）。

    2026-11-05 更正：这个扩展点**尚未接线**。TTSEngine.synthesize_profile 的
    engine 分支只有 edge_tts / stepaudio / silent，`TTS_ENGINE=local` 会落到
    else 静默退回 edge_tts，永远不会实例化本类。要启用需同时实现 synthesize()
    并在 tts.py 的分支里加 `local` case；在那之前不要按注释里的旧说法去配
    TTS_ENGINE=local（不会报错，只会以为换了引擎其实没换）。
    """

    def __init__(self) -> None:  # pragma: no cover - extension point
        raise NotImplementedError("implement this and wire it in tts.py")


tts = TTSEngine()
