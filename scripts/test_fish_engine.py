# -*- coding: utf-8 -*-
"""Fish Audio 作为 TTS / ASR 引擎的契约测试（先红后绿）。

要钉的四件事：

1. ``TTS_ENGINE=fish`` 时真的走 Fish，不落进 edge_tts 的兜底分支
   （``server/speech/tts.py`` 的 else 分支会静默退回 edge_tts —— 那会让
   「engine 配了 fish」变成假绿）。
2. persona.yaml 的七档 ``instruction``（中文自然语言）**原样**放进 Fish 的方括号，
   不需要另造映射表 —— 这是接入 Fish 最大的省事之处。
3. ``prosody.speed`` 由 persona 的 ``rate``（"+10%" 形式）换算，方向不反。
4. ASR 走 Fish 时上传的是 WAV 容器（Fish 明确不支持裸 PCM），且转写文本能取回来。

所有 HTTP 都被桩住：不联网、不花钱、不碰真实凭据。
"""
from __future__ import annotations

import base64
import io
import json as jsonlib
import os
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# import server.* 之前先钉死数据目录与凭据（issue #39 的隔离姿势）。
os.environ.setdefault("DB_PATH", ":memory:")


def _wav(pcm: bytes, rate: int = 16000) -> bytes:
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
    header += struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200, headers=None):
        self.content = payload
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return jsonlib.loads(self.content.decode("utf-8"))

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    async def aiter_lines(self):
        for line in self.text.splitlines():
            yield line

    async def aread(self):
        return self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Ctx:
    def __init__(self, payload: bytes):
        self.payload = payload

    async def __aenter__(self):
        return _FakeResponse(self.payload)

    async def __aexit__(self, *a):
        return False


class FakeHttpx:
    """只实现 tts.py / asr.py 用到的那几个方法。

    实现里用 `async with httpx.AsyncClient(...)`，所以桩要支持异步上下文管理器。
    """

    def __init__(self, *, tts_bytes: bytes = b"ID3fake-mp3-bytes" * 40,
                 asr_payload: dict | None = None):
        self.tts_bytes = tts_bytes
        self.asr_payload = asr_payload or {"text": "我今天有点累", "segments": []}
        self.tts_calls: list[dict] = []
        self.asr_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, *, json=None, headers=None, files=None, data=None, **kwargs):
        # 参数名 json 遮住了模块 json —— 这里用 _json_body 别名取。
        if url.endswith("/v1/tts"):
            self.tts_calls.append({"url": url, "json": json, "headers": headers or {}})
            return _FakeResponse(self.tts_bytes)
        if url.endswith("/v1/asr"):
            self.asr_calls.append({"url": url, "json": data, "headers": headers or {},
                                   "files": files})
            return _FakeResponse(jsonlib.dumps(self.asr_payload).encode("utf-8"))
        raise AssertionError(f"unexpected POST {url}")

    def stream(self, method, url, *, json=None, headers=None, **kwargs):
        if url.endswith("/v1/tts/stream/with-timestamp"):
            # P4：Fish 逐字对齐走 SSE；假 transport 回一条带 audio + segments 的事件。
            self.tts_calls.append({"url": url, "json": json, "headers": headers or {}})
            event = {
                "audio_base64": base64.b64encode(self.tts_bytes).decode("ascii"),
                "alignment": {"segments": [
                    {"text": "你", "start": 0.0, "end": 0.2},
                    {"text": "好", "start": 0.2, "end": 0.4},
                ]},
            }
            body = ("data: " + jsonlib.dumps(event, ensure_ascii=False) + "\n\n").encode("utf-8")
            return _Ctx(body)
        self.asr_calls.append({"url": url, "json": json, "headers": headers or {}})
        return _Ctx(jsonlib.dumps(self.asr_payload).encode("utf-8"))


class FishConfigTests(unittest.TestCase):
    def setUp(self):
        from server import config as sc

        self.settings = sc.settings
        self.saved = {
            "tts_engine": self.settings.tts_engine,
            "tts_model": self.settings.tts_model,
            "tts_voice": self.settings.tts_voice,
            "asr_engine": self.settings.asr_engine,
            "asr_model": self.settings.asr_model,
            "asr_model_id": self.settings.asr_model_id,
        }
        for key, value in {
            "fish_base_url": "https://api.fish.audio",
            "fish_api_key": "sk-fish-TEST",
            "fish_tts_model": "s2.1-pro-free",
            "fish_asr_model": "transcribe-1-pro",
        }.items():
            if not hasattr(self.settings, key):
                setattr(self.settings, key, value)
            self.saved.setdefault(key, getattr(self.settings, key))

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(self.settings, key, value)

    def test_settings_expose_fish_keys(self):
        for key in ("fish_base_url", "fish_api_key", "fish_tts_model", "fish_asr_model"):
            self.assertTrue(hasattr(self.settings, key), f"settings 缺 {key}")

    def test_fish_engine_is_not_silently_edge(self):
        """engine=fish 时不能落进 edge_tts 兜底分支。"""
        from server.speech.tts import TTSEngine

        self.settings.tts_engine = "fish"
        engine = TTSEngine()
        self.assertEqual(engine.engine, "fish")
        # 关键：synthesize_profile 的分支里必须有 fish；否则会静默退回 edge。
        import inspect

        source = inspect.getsource(TTSEngine.synthesize_profile)
        self.assertIn('"fish"', source,
                      "synthesize_profile 没有 fish 分支 —— engine=fish 会静默退回 edge_tts")


class FishTtsTests(unittest.TestCase):
    def setUp(self):
        from server import config as sc

        self.settings = sc.settings
        self.saved = {k: getattr(self.settings, k) for k in
                      ("tts_engine", "fish_base_url", "fish_api_key", "fish_tts_model")}
        self.settings.tts_engine = "fish"
        self.settings.fish_base_url = "https://api.fish.audio"
        self.settings.fish_api_key = "sk-fish-TEST"
        self.settings.fish_tts_model = "s2.1-pro-free"

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(self.settings, key, value)

    async def _synth(self, text, profile):
        import server.speech.tts as tts_mod

        fake = FakeHttpx()
        real_client = tts_mod.httpx_client_for_test if hasattr(tts_mod, "httpx_client_for_test") else None
        # tts.py 用 `import httpx` 后 `httpx.AsyncClient`；直接替换模块属性。
        import httpx

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **k: fake  # type: ignore[assignment]
        try:
            engine = tts_mod.TTSEngine()
            audio = await engine.synthesize_profile(text, profile)
        finally:
            httpx.AsyncClient = original  # type: ignore[assignment]
        return audio, fake

    def test_fish_tts_sends_instruction_in_brackets(self):
        """persona 的中文 instruction 进方括号 —— 不需要另造映射表。

        但**只取第一子句**：2026-11-12 实测（每档 3 次，Fish ASR 转写回来比对）
        整条 instruction 进方括号时，七档里 base 与 得意 3/3 泄漏 ——
        instruction 的后半截被当成正文念了出来（「不要播音腔，不刻意表演」）。
        只取第一子句后七档 0/2 泄漏，且七档字节仍两两不同。
        """
        import asyncio

        instruction = "语气自然随意，像熟人聊天，不要播音腔，不刻意表演"
        audio, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "livelybreezy-female", "instruction": instruction,
            "rate": "+0%", "pitch": "+0Hz", "volume": "+0%",
        }))
        self.assertGreater(len(audio), 0)
        self.assertEqual(len(fake.tts_calls), 1)
        call = fake.tts_calls[0]
        self.assertTrue(call["url"].endswith("/v1/tts/stream/with-timestamp"), call["url"])
        # 模型走请求头，不是 body 字段。
        self.assertEqual(call["headers"].get("model"), "s2.1-pro-free")
        # instruction 的**第一子句**在方括号里。
        self.assertIn("[语气自然随意]", call["json"]["text"])
        # 后半截不能出现 —— 那会被念出来。
        self.assertNotIn("不要播音腔", call["json"]["text"])
        self.assertNotIn("不刻意表演", call["json"]["text"])
        # 凭据走 Authorization。
        self.assertEqual(call["headers"].get("Authorization"), "Bearer sk-fish-TEST")

    def test_fish_tts_uses_only_first_clause_for_every_persona_emotion(self):
        """七档 persona 逐档验：只取第一子句。"""
        import asyncio

        import yaml

        persona = yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "config" / "persona.yaml")
            .read_text(encoding="utf-8"))
        for name, item in persona["tts_emotions"].items():
            instruction = item["instruction"]
            _, fake = asyncio.run(self._synth("你好呀。", {
                "voice": item.get("voice") or "v", "instruction": instruction,
                "rate": item.get("rate") or "+0%", "pitch": item.get("pitch") or "+0Hz",
                "volume": item.get("volume") or "+0%",
            }))
            sent = fake.tts_calls[0]["json"]["text"]
            first = instruction.replace("，", ",").split(",")[0].strip()
            self.assertTrue(sent.startswith(f"[{first}] "),
                            f"{name}: 实际发了 {sent!r}，期望以 [{first}] 开头")
            for clause in instruction.replace("，", ",").split(",")[1:]:
                clause = clause.strip()
                if clause:
                    self.assertNotIn(clause, sent,
                                     f"{name}: 后续子句 {clause!r} 不该进方括号（会被念出来）")

    def test_fish_tts_rate_becomes_speed(self):
        """persona 的 rate（"+10%"）→ prosody.speed，方向不反。"""
        import asyncio

        _, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "v", "instruction": "自然", "rate": "+10%",
            "pitch": "+8Hz", "volume": "+3%",
        }))
        speed = fake.tts_calls[0]["json"]["prosody"]["speed"]
        self.assertGreater(speed, 1.0, f"rate=+10% 应给出 speed>1，实际 {speed}")
        self.assertAlmostEqual(speed, 1.10, places=2)

    def test_fish_tts_negative_rate_slows_down(self):
        import asyncio

        _, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "v", "instruction": "自然", "rate": "-8%",
            "pitch": "+0Hz", "volume": "+0%",
        }))
        speed = fake.tts_calls[0]["json"]["prosody"]["speed"]
        self.assertLess(speed, 1.0, f"rate=-8% 应给出 speed<1，实际 {speed}")

    def test_fish_tts_volume_maps_to_db(self):
        """persona 的 volume 是百分比（"+3%"/"-12%"）；Fish 的 prosody.volume 是 dB。"""
        import asyncio

        _, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "v", "instruction": "自然", "rate": "+0%",
            "pitch": "+0Hz", "volume": "-12%",
        }))
        volume = fake.tts_calls[0]["json"]["prosody"]["volume"]
        self.assertLess(volume, 0.0, f"volume=-12% 应给出负 dB，实际 {volume}")

    def test_fish_tts_language_is_zh_cn(self):
        import asyncio

        _, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "v", "instruction": "自然", "rate": "+0%",
            "pitch": "+0Hz", "volume": "+0%",
        }))
        self.assertEqual(fake.tts_calls[0]["json"].get("language"), "zh-CN")

    def test_fish_tts_never_sends_pitch(self):
        """Fish 没有 pitch 旋钮；把 pitch 塞进请求会被上游拒或被忽略。

        这里只钉「不把它当成 Fish 的旋钮发出去」——语气的表演力由 instruction 承担。
        """
        import asyncio

        _, fake = asyncio.run(self._synth("你好呀。", {
            "voice": "v", "instruction": "自然", "rate": "+0%",
            "pitch": "+14Hz", "volume": "+0%",
        }))
        body = fake.tts_calls[0]["json"]
        self.assertNotIn("pitch", body)
        self.assertNotIn("pitch", body.get("prosody", {}))

    def test_fish_tts_without_key_raises_instead_of_silent_edge(self):
        """没配 key 必须报错，不许静默换个引擎。"""
        import asyncio

        self.settings.fish_api_key = ""
        with self.assertRaises(Exception):
            asyncio.run(self._synth("你好呀。", {
                "voice": "v", "instruction": "自然", "rate": "+0%",
                "pitch": "+0Hz", "volume": "+0%",
            }))


class FishAsrTests(unittest.TestCase):
    def setUp(self):
        from server import config as sc

        self.settings = sc.settings
        self.saved = {k: getattr(self.settings, k) for k in
                      ("asr_engine", "fish_base_url", "fish_api_key", "fish_asr_model")}
        self.settings.asr_engine = "fish"
        self.settings.fish_base_url = "https://api.fish.audio"
        self.settings.fish_api_key = "sk-fish-TEST"
        self.settings.fish_asr_model = "transcribe-1-pro"

    def tearDown(self):
        for key, value in self.saved.items():
            setattr(self.settings, key, value)

    def test_fish_asr_uploads_wav_container(self):
        """Fish 不支持裸 PCM（文档明说），必须包成 WAV 上传。"""
        import asyncio
        import inspect

        import server.speech.asr as asr_mod

        source = inspect.getsource(asr_mod.Recognizer._fish)
        self.assertIn("wav", source.lower())

    def test_fish_asr_returns_transcript(self):
        import asyncio

        import server.speech.asr as asr_mod

        fake = FakeHttpx(asr_payload={"text": "我今天有点累", "segments": []})
        real_stream = fake.stream

        async def fake_stream(method, url, **kwargs):
            fake.asr_calls.append({"url": url, "json": kwargs.get("json"),
                                   "headers": kwargs.get("headers") or {}})
            return await real_stream(method, url, **kwargs)

        fake.stream = fake_stream
        import httpx

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **k: fake  # type: ignore[assignment]
        try:
            import numpy as np

            recognizer = asr_mod.Recognizer()
            pcm = (np.sin(np.linspace(0, 200, 8000)) * 8000).astype(np.int16)
            result = asyncio.run(recognizer.transcribe(pcm, 16000, language="zh"))
        finally:
            httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertIn("累", result.text)
        self.assertEqual(result.language, "zh")

    def test_fish_asr_without_key_raises(self):
        import asyncio

        import server.speech.asr as asr_mod

        self.settings.fish_api_key = ""
        import numpy as np

        recognizer = asr_mod.Recognizer()
        pcm = (np.sin(np.linspace(0, 200, 8000)) * 8000).astype(np.int16)
        with self.assertRaises(Exception):
            asyncio.run(recognizer.transcribe(pcm, 16000, language="zh"))

    def test_fish_asr_strips_audio_event_tags(self):
        """Fish ASR 会把语气/事件写成方括号标签（实测 [疑问]），不能混进用户原话。

        2026-11-13 场景录屏：4 条合成语音里 1 条转写成
        「晚饭还没想好吃什么，[疑问]你有什么建议吗？」，标签直接进了
        用户气泡与 LLM 输入。
        """
        import asyncio

        import server.speech.asr as asr_mod

        payload = {"text": "[疑问]晚饭还没想好吃什么，[笑声]你有什么建议吗？",
                   "segments": [], "language_code": "zh"}
        fake = FakeHttpx(asr_payload=payload)

        async def fake_stream(method, url, **kwargs):
            fake.asr_calls.append({"url": url, "json": kwargs.get("json"),
                                   "headers": kwargs.get("headers") or {}})
            return _Ctx(jsonlib.dumps(payload).encode("utf-8"))

        fake.stream = fake_stream
        import httpx

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **k: fake  # type: ignore[assignment]
        try:
            import numpy as np

            recognizer = asr_mod.Recognizer()
            pcm = (np.sin(np.linspace(0, 200, 8000)) * 8000).astype(np.int16)
            result = asyncio.run(recognizer.transcribe(pcm, 16000, language="zh"))
        finally:
            httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertEqual(result.text, "晚饭还没想好吃什么，你有什么建议吗？")

    def test_fish_asr_keeps_normal_square_brackets_in_words(self):
        """只清标签，不动正常文本：含数字的方括号与长句子不误伤。"""
        import asyncio

        import server.speech.asr as asr_mod

        payload = {"text": "第[2]题不会，[这个说法挺长的不是标签]", "segments": [],
                   "language_code": "zh"}
        fake = FakeHttpx(asr_payload=payload)

        async def fake_stream(method, url, **kwargs):
            return _Ctx(jsonlib.dumps(payload).encode("utf-8"))

        fake.stream = fake_stream
        import httpx

        original = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **k: fake  # type: ignore[assignment]
        try:
            import numpy as np

            recognizer = asr_mod.Recognizer()
            pcm = (np.sin(np.linspace(0, 200, 8000)) * 8000).astype(np.int16)
            result = asyncio.run(recognizer.transcribe(pcm, 16000, language="zh"))
        finally:
            httpx.AsyncClient = original  # type: ignore[assignment]
        self.assertIn("[2]", result.text)
        self.assertIn("[这个说法挺长的不是标签]", result.text)


class FishPersonaMappingTests(unittest.TestCase):
    """七档 persona 的 instruction 必须能原样进 Fish 方括号 —— 不含 Fish 会误解析的字符。"""

    def test_all_persona_instructions_are_bracket_safe(self):
        import yaml

        persona = yaml.safe_load((ROOT / "config" / "persona.yaml").read_text(encoding="utf-8"))
        emotions = persona["tts_emotions"]
        self.assertGreaterEqual(len(emotions), 7)
        for name, item in emotions.items():
            instruction = item.get("instruction") or ""
            self.assertTrue(instruction, f"{name} 没有 instruction")
            for ch in "[]<>\n\r":
                self.assertNotIn(ch, instruction,
                                 f"{name} 的 instruction 含 {ch!r}，Fish 会当标签解析")


if __name__ == "__main__":
    unittest.main(verbosity=2)
