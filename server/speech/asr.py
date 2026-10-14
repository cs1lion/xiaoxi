from __future__ import annotations

import io
import json
import math
import os
import struct
import wave
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import settings


@dataclass
class Transcript:
    text: str
    language: str
    duration_ms: int
    confidence: float


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate = wav.getframerate()
        n_channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sample_width]
    audio = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    if n_channels > 1:
        audio = audio.reshape(-1, n_channels).mean(axis=1)
    if dtype == np.uint8:
        audio = (audio - 128) / 128.0
    else:
        audio = audio / float(np.iinfo(dtype).max)
    return audio, rate


def energy_db(chunk: np.ndarray) -> float:
    if chunk.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(chunk))))
    return 20 * math.log10(rms + 1e-8)


def trim_silence(audio: np.ndarray, *, threshold_db: float = -42.0, keep_ms: int = 120) -> np.ndarray:
    if audio.size == 0:
        return audio
    frame = 320
    pad = frame // 2
    padded = np.pad(audio, (pad, pad), mode="constant")
    frames = np.lib.stride_tricks.sliding_window_view(padded, frame)[:: frame // 2]
    energies = 20 * np.log10(np.sqrt(np.mean(np.square(frames), axis=1)) + 1e-8)
    voiced = np.where(energies > threshold_db)[0]
    if voiced.size == 0:
        return audio
    keep = max(1, (keep_ms / 1000) * (audio.size / (len(energies) * (frame / 2))))
    start = max(0, voiced[0] - int(keep))
    end = min(len(energies), voiced[-1] + int(keep))
    return padded[start * (frame // 2) : end * (frame // 2)]


class Recognizer:
    """Transcription backend. faster-whisper locally, StepAudio over HTTP."""

    def __init__(self) -> None:
        self._model: Any = None

    def _ensure(self) -> Any:
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = self._load()
        return self._model

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        kwargs = dict(
            device=settings.asr_device,
            compute_type=settings.asr_compute_type,
        )
        try:
            return WhisperModel(settings.asr_model, **kwargs)
        except Exception as exc:  # network blocked -> try the mirror once
            mirror = os.getenv("HF_ENDPOINT", "https://hf-mirror.com")
            if not mirror:
                raise
            try:
                import huggingface_hub.constants as hf_constants

                hf_constants.ENDPOINT = mirror
                os.environ["HF_ENDPOINT"] = mirror
                return WhisperModel(settings.asr_model, **kwargs)
            except Exception:
                os.environ.pop("HF_ENDPOINT", None)
                raise exc

    async def transcribe(self, audio: np.ndarray, sample_rate: int, *, language: str | None = None) -> Transcript:
        language = settings.asr_language if language is None else language
        if sample_rate != settings.audio_sample_rate:
            duration = audio.size / sample_rate
            target_n = int(duration * settings.audio_sample_rate)
            audio = np.interp(
                np.linspace(0, audio.size - 1, target_n),
                np.arange(audio.size),
                audio,
            ).astype(np.float32)
        audio = trim_silence(audio)
        if audio.size < int(0.2 * settings.audio_sample_rate):
            return Transcript("", "zh", int(audio.size / settings.audio_sample_rate * 1000), 0.0)

        duration_ms = int(audio.size / settings.audio_sample_rate * 1000)
        if settings.asr_engine == "stepaudio":
            return await self._stepaudio(audio, duration_ms, language)
        return await self._whisper(audio, duration_ms, language)

    async def _whisper(self, audio: np.ndarray, duration_ms: int, language: str | None = None) -> Transcript:
        language = settings.asr_language if language is None else language
        model = self._ensure()
        import anyio

        def _run() -> tuple[str, float]:
            segments, info = model.transcribe(
                audio,
                language=language or None,
                beam_size=5,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt="以下是普通话的句子，请使用简体中文输出。" if language == "zh" else None,
            )
            text = ""
            probs: list[float] = []
            for segment in segments:
                text += segment.text
                probs.append(getattr(segment, "avg_logprob", -1.0))
            confidence = 0.0
            if probs:
                confidence = max(0.0, min(1.0, math.exp(sum(probs) / len(probs))))
            return text.strip(), confidence

        text, confidence = await anyio.to_thread.run_sync(_run)
        return Transcript(
            text=text,
            language=language or "auto",
            duration_ms=duration_ms,
            confidence=confidence,
        )

    async def _stepaudio(self, audio: np.ndarray, duration_ms: int, language: str | None = None) -> Transcript:
        """StepAudio 2.5 ASR over HTTP+SSE. One shot in, transcript events out."""
        language = settings.asr_language if language is None else language
        import base64

        import httpx

        from ..llm import llm

        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        payload = {
            "audio": {
                "data": base64.b64encode(pcm).decode("ascii"),
                "input": {
                    "transcription": {
                        "model": settings.asr_model_id,
                        "language": language,
                        "enable_itn": True,
                    },
                    "format": {
                        "type": "pcm",
                        "codec": "pcm_s16le",
                        "rate": settings.audio_sample_rate,
                        "bits": 16,
                        "channel": 1,
                    },
                },
            }
        }
        client = await llm.client()
        chunks: list[str] = []
        final = ""
        error: str | None = None
        async with client.stream(
            "POST",
            f"{llm.base_url}/audio/asr/sse",
            json=payload,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", "replace")
                raise RuntimeError(f"asr {resp.status_code}: {body[:200]}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if not body or body == "[DONE]":
                    continue
                try:
                    event = json.loads(body)
                except json.JSONDecodeError:
                    continue
                kind = event.get("type")
                if kind == "transcript.text.delta":
                    chunks.append(event.get("delta") or "")
                elif kind == "transcript.text.done":
                    final = event.get("text") or ""
                elif kind == "error":
                    error = (event.get("message") or "asr error")[:200]
        if error:
            raise RuntimeError(error)
        text = final or "".join(chunks)
        return Transcript(
            text=text.strip(),
            language=language or "auto",
            duration_ms=duration_ms,
            confidence=0.0 if text.strip() else 0.0,
        )

    async def transcribe_wav(self, data: bytes, *, language: str | None = None) -> Transcript:
        audio, rate = decode_wav(data)
        return await self.transcribe(audio, rate, language=language)


def to_wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def rms_level(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


def pcm16(audio: np.ndarray) -> bytes:
    samples = (np.clip(audio, -1, 1) * 32767).astype(int).tolist()
    return struct.pack("<%dh" % len(samples), *samples)


recognizer = Recognizer()