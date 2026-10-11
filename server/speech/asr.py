from __future__ import annotations

import io
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
    """faster-whisper wrapper. Model loads lazily on first use."""

    def __init__(self) -> None:
        self._model: Any = None
        self._lock_name = "asr"

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

    async def transcribe(self, audio: np.ndarray, sample_rate: int) -> Transcript:
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

        model = self._ensure()
        import anyio

        def _run() -> tuple[str, float]:
            segments, info = model.transcribe(
                audio,
                language=settings.asr_language or None,
                beam_size=5,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt="以下是普通话的句子，请使用简体中文输出。" if settings.asr_language == "zh" else None,
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
            language=settings.asr_language or "zh",
            duration_ms=int(audio.size / settings.audio_sample_rate * 1000),
            confidence=confidence,
        )

    async def transcribe_wav(self, data: bytes) -> Transcript:
        audio, rate = decode_wav(data)
        return await self.transcribe(audio, rate)


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