"""Server-side voice activity detection on PCM16 frame sequences.

The fast loop must never block on a model, so this stays a pure frame-level
energy classifier: 20ms frames, adaptive noise floor, no microphone, no
provider. It is only used by the ASR degradation branch in `_transcribe_turn`,
where it answers one question the transcript cannot: did the captured audio
contain speech at all?

It is a coarse detector by design. Loud non-speech (fan noise, typing) can
clear the threshold, so the caller must word its output as "detected speech
activity", never as a transcript.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FRAME_MS = 20
# 9 dB over the measured noise floor keeps steady hiss out while a normal
# speaking voice (typically 15-30 dB above it) clears easily.
MARGIN_DB = 9.0
# Absolute gate: even in a digitally silent buffer the floor percentile is
# about -180 dBFS, so floor + margin would sit far below anything audible.
ABSOLUTE_FLOOR_DBFS = -50.0
MIN_SPEECH_MS = 120


@dataclass(frozen=True)
class VadResult:
    total_ms: int
    speech_ms: int
    frames: int
    speech_frames: int
    noise_floor_dbfs: float
    peak_dbfs: float
    has_speech: bool


def _frame_dbfs(frames: np.ndarray) -> np.ndarray:
    rms = np.sqrt(np.mean(np.square(frames), axis=1))
    return 20.0 * np.log10(rms + 1e-9)


def analyze_pcm(pcm: bytes, sample_rate: int, *, frame_ms: int = FRAME_MS,
                min_speech_ms: int = MIN_SPEECH_MS, margin_db: float = MARGIN_DB,
                absolute_floor_dbfs: float = ABSOLUTE_FLOOR_DBFS) -> VadResult:
    """Classify a PCM16 mono buffer without touching a microphone.

    Empty, odd-length or too-short buffers degrade to "no speech" instead of
    raising: this runs on the failure path and must not become a second failure.
    """
    size = len(pcm) // 2 * 2
    samples = np.frombuffer(pcm[:size], dtype="<i2").astype(np.float32) / 32768.0
    per_frame = max(1, int(sample_rate * frame_ms / 1000))
    count = samples.size // per_frame
    if count == 0:
        return VadResult(0, 0, 0, 0, -120.0, -120.0, False)
    levels = _frame_dbfs(samples[:count * per_frame].reshape(count, per_frame))
    # 20th percentile instead of mean/min: one loud burst must not raise the
    # floor, and one dropout must not lower the threshold to noise.
    noise_floor = float(np.percentile(levels, 20))
    threshold = max(noise_floor + margin_db, absolute_floor_dbfs)
    voiced = levels > threshold
    speech_frames = int(np.count_nonzero(voiced))
    speech_ms = speech_frames * frame_ms
    return VadResult(
        total_ms=count * frame_ms,
        speech_ms=speech_ms,
        frames=count,
        speech_frames=speech_frames,
        noise_floor_dbfs=round(noise_floor, 1),
        peak_dbfs=round(float(np.max(levels)), 1),
        has_speech=speech_ms >= min_speech_ms,
    )
