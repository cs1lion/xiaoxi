"""Deterministic proof that provider padding is trimmed and nothing else moves.

    .\\.venv\\Scripts\\python.exe scripts\\test_tts_trim_silence.py

No network and no model: synthetic 16-bit PCM WAVs stand in for provider output.
The suite pins the measurable defect this fixes -- roughly 0.36s before and 0.38s
after every chunk -- and proves the trim removes it without clipping speech,
corrupting the container, or touching non-WAV bytes.
"""
from __future__ import annotations

import math
import struct
import sys
import unittest
import wave
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.speech.tts import trim_wav_silence  # noqa: E402

RATE = 24000


def wav_bytes(values: list[float], *, rate: int = RATE, channels: int = 1) -> bytes:
    """One value per frame, replicated across channels so stereo stays interleaved."""
    pcm = struct.pack(f"<{len(values) * channels}h",
                      *[int(max(-1.0, min(1.0, value)) * 32767)
                        for value in values for _ in range(channels)])
    out = BytesIO()
    with wave.open(out, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return out.getvalue()


def read_wav(data: bytes) -> tuple[list[float], int, int]:
    with wave.open(BytesIO(data), "rb") as handle:
        channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    samples = [value / 32768.0 for value in struct.unpack(f"<{len(frames) // 2}h", frames)]
    return samples, channels, rate


def tone(count: int, freq: float = 220.0) -> list[float]:
    return [0.5 * math.sin(2 * math.pi * freq * index / RATE) for index in range(count)]


def padded(lead_ms: int, trail_ms: int, speech_ms: int = 400) -> bytes:
    return wav_bytes([0.0] * int(RATE * lead_ms / 1000)
                     + tone(int(RATE * speech_ms / 1000))
                     + [0.0] * int(RATE * trail_ms / 1000))


class TrimWavSilenceTests(unittest.TestCase):
    def test_provider_padding_is_removed(self):
        audio = padded(360, 380)
        trimmed = trim_wav_silence(audio, threshold=0.012, pad_ms=0)
        samples, channels, rate = read_wav(trimmed)
        self.assertEqual((channels, rate), (1, RATE), "container header must survive")
        expected = int(RATE * 400 / 1000)
        self.assertLess(len(samples), expected * 1.05, "padding must be gone")
        self.assertGreater(len(samples), expected * 0.9, "speech must survive")

    def test_leading_and_trailing_silence_shrink(self):
        trimmed = trim_wav_silence(padded(360, 380), threshold=0.012, pad_ms=0)
        samples, _, _ = read_wav(trimmed)
        leading = next((i for i, value in enumerate(samples) if abs(value) > 0.012), None)
        trailing = len(samples) - 1 - next(
            (i for i, value in enumerate(reversed(samples)) if abs(value) > 0.012), None)
        self.assertLessEqual(leading, 5, "no leading silence should remain")
        self.assertLessEqual(len(samples) - 1 - trailing, 5, "no trailing silence should remain")

    def test_keeps_the_requested_pad(self):
        trimmed = trim_wav_silence(padded(360, 380), threshold=0.012, pad_ms=60)
        samples, _, _ = read_wav(trimmed)
        leading = next(i for i, value in enumerate(samples) if abs(value) > 0.012)
        self.assertAlmostEqual(leading / RATE, 0.06, delta=0.01,
                               msg="a small edge pad should survive so onsets are not clipped")

    def test_all_silent_input_is_returned_unchanged(self):
        audio = wav_bytes([0.0] * RATE)
        self.assertEqual(trim_wav_silence(audio), audio, "nothing to trim, nothing to break")

    def test_speech_already_at_both_edges_is_untouched(self):
        audio = wav_bytes(tone(RATE // 2))
        self.assertEqual(trim_wav_silence(audio), audio, "no padding means no rewrite")

    def test_non_wav_bytes_pass_through(self):
        for payload in (b"", b"ID3\x04\x00\x00\x00\x00\x00\x00", b"not audio at all"):
            self.assertEqual(trim_wav_silence(payload), payload)

    def test_stereo_is_handled_per_channel(self):
        lead, trail = int(RATE * 0.3), int(RATE * 0.3)
        frames = [0.0] * lead + [0.4] * (RATE // 5) + [0.0] * trail
        trimmed = trim_wav_silence(wav_bytes(frames, channels=2), threshold=0.012, pad_ms=0)
        samples, channels, _ = read_wav(trimmed)
        self.assertEqual(channels, 2, "channel count must survive")
        self.assertLess(len(samples), (lead + RATE // 5 + trail) * 2 * 0.8)

    def test_result_decodes_as_valid_pcm(self):
        trimmed = trim_wav_silence(padded(360, 380))
        with wave.open(BytesIO(trimmed), "rb") as handle:
            self.assertEqual(handle.getcomptype(), "NONE")
            self.assertGreater(handle.getnframes(), 0)


class CacheKeyTests(unittest.TestCase):
    """The synthesis cache must not hand back the other wire format's bytes.

    Trimming re-encodes to WAV while the untrimmed path keeps the configured
    format, so the format is part of the cache identity. Before this was in the
    key, flipping tts_trim_silence on a warm cache returned MP3 bytes for a WAV
    request (or the reverse), which the frontend still decoded but which made
    the toggle a no-op.
    """

    def setUp(self):
        from server.config import settings
        self.settings = settings
        self.saved = self.settings.tts_trim_silence
        self.saved_engine = self.settings.tts_engine
        # Trimming re-encodes PCM to WAV only on StepAudio; the cache-key contract
        # is about that wire-format switch, not the Edge path (which never trims).
        self.settings.tts_engine = "stepaudio"

    def tearDown(self):
        self.settings.tts_trim_silence = self.saved
        self.settings.tts_engine = self.saved_engine

    def _key(self):
        from server.speech.tts import TTSEngine
        return TTSEngine()._cache_key("同一句话", {"voice": "v", "instruction": "i"})

    def test_trim_toggle_changes_the_key(self):
        self.settings.tts_trim_silence = True
        trimmed = self._key()
        self.settings.tts_trim_silence = False
        untrimmed = self._key()
        self.assertNotEqual(trimmed, untrimmed,
                            "trim on/off must not share a cache entry")

    def test_key_is_stable_for_the_same_settings(self):
        self.settings.tts_trim_silence = True
        self.assertEqual(self._key(), self._key(), "same settings must hit the same entry")

    def test_different_text_or_profile_changes_the_key(self):
        from server.speech.tts import TTSEngine
        self.settings.tts_trim_silence = True
        engine = TTSEngine()
        base = engine._cache_key("一句话", {"voice": "v"})
        self.assertNotEqual(base, engine._cache_key("另一句话", {"voice": "v"}))
        self.assertNotEqual(base, engine._cache_key("一句话", {"voice": "other"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
