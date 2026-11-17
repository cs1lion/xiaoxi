from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import struct
import threading
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
        # 双检锁：_ensure 现在跑在工作线程里（模型加载与转写同一次卸载），
        # 事件循环不再是天然互斥量——barge-in 重叠或语音/口译双 WS 并发
        # 首次加载会双重构造 WhisperModel（内存翻倍、HF 回退跑两遍）。
        # 锁只圈住首次加载，命中后无锁快路径。
        self._model_lock = threading.Lock()

    def _ensure(self) -> Any:
        if self._model is None:
            with self._model_lock:
                if self._model is None:
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
        if settings.asr_engine == "fish":
            return await self._fish(audio, duration_ms, language)
        return await self._whisper(audio, duration_ms, language)

    async def _whisper(self, audio: np.ndarray, duration_ms: int, language: str | None = None) -> Transcript:
        language = settings.asr_language if language is None else language

        def _run() -> tuple[str, float]:
            # 模型加载与转写同一次线程外执行：_ensure 会 import faster_whisper
            # 并构造 WhisperModel（首次还有 HF 下载回退），秒级。放在循环上
            # 等于首句语音冻住所有连接；搬进闭包后最坏只是这一轮等几秒。
            model = self._ensure()
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

        # asyncio.to_thread 而非 anyio.to_thread：实测 anyio 首次调用有约
        # 29ms 的循环侧设置成本（线程池与 backend 探测），首句语音会吃到；
        # asyncio.to_thread 首调约 3.5ms，且与 orchestrator/app 的卸载点同款。
        text, confidence = await asyncio.to_thread(_run)
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

    async def _fish(self, audio: np.ndarray, duration_ms: int, language: str | None = None) -> Transcript:
        """Fish Audio ASR（2026-11-12）。

        与 stepaudio 的关键差别：**上传的是文件**，不是 JSON 里的 base64。
        Fish 的 `/v1/asr` 只接受 multipart/form-data（文件上传）或 msgpack；
        裸 PCM 不支持，所以这里包成 WAV 容器再传（官方文档：支持 WAV/MP3/AAC/FLAC/
        Ogg/WebM，不支持 AIFF/CAF/WMA/AMR/AC-3 与无头 PCM）。

        模型走 **请求头**（`model: transcribe-1-pro`），body 里的 model 字段会被忽略。
        ASR **按音频时长计费**，所以这里不请求时间戳（`ignore_timestamps` 默认 true）。
        """
        import httpx

        key = str(settings.fish_api_key or "").strip()
        if not key:
            raise RuntimeError(
                "ASR_ENGINE=fish 需要 FISH_API_KEY（.env）；不配就报错，"
                "不许静默退回别的引擎")
        base = str(settings.fish_base_url or "https://api.fish.audio").rstrip("/")
        model = str(settings.fish_asr_model or "transcribe-1-pro")
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        wav = to_wav_bytes(audio, settings.audio_sample_rate)
        payload: dict[str, Any] = {}
        if language:
            payload["language"] = str(language)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base}/v1/asr",
                files={"audio": ("turn.wav", wav, "audio/wav")},
                data=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    # 模型走请求头；body 里的 model 会被忽略。
                    "model": model,
                },
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"fish asr {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        text = str(body.get("text") or "")
        # Fish 会把说话人标记写成 <|speaker:0|>；单人场景去掉它，别混进转写文本。
        text = re.sub(r"<\|speaker:\d+\|>", " ", text).strip()
        # Fish 还会把语气/音频事件写成短方括号标签（实测 [疑问]、[笑声]），
        # 那不是用户说过的话，混进气泡和 LLM 输入会直接改变语义。
        # 只清「1–6 个汉字/字母」的短标签：带数字的 [2]、长句方括号不动。
        # 直接删掉而不是换成空格：中文句子中间多一个空格很显眼。
        text = re.sub(r"\[[A-Za-z\u4e00-\u9fff]{1,6}\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return Transcript(
            text=text,
            language=str(body.get("language_code") or language or "auto"),
            duration_ms=duration_ms,
            confidence=0.0,
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