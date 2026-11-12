# -*- coding: utf-8 -*-
"""LLM 客户端的思考模式开关（2026-11-12）。

**为什么要有这个字段**：项目 `llm.py` 只发 `reasoning_effort`，没有
`enable_thinking`。对**默认开思考**的模型（讯飞星火 spark-x2.6 就是），
`reasoning_effort=low` 不是「少想一点」，反而是**打开思考的另一个入口** ——
实测中位耗时反而比不带更差。

2026-11-12 实测（讯飞 spark-x2.6，项目真实 persona prompt，每种 2 次取中位数）：

| 请求形状 | 中位耗时 | content 长度 | reasoning 长度 |
| --- | ---: | --- | --- |
| 项目现状（`reasoning_effort=low`） | 14878ms | 61 / 63 | 152 / 268 |
| `enable_thinking=false` | 6755ms | 64 / 62 | 0 / 0 |
| `enable_thinking=false` + `reasoning_effort=low` | **5353ms** | 61 / 50 | 0 / 0 |
| `enable_thinking=true` | 13600ms | 64 / 65 | 128 / 213 |
| 什么都不带（默认开思考） | 21285ms | 52 / 63 | 233 / 169 |

即：**关思考比现状快 2.8 倍，正文长度不变**（思考内容从 reasoning 挪到正文，
总量相当）。这对快环的首字延迟是实打实的收益 —— 之前真实验收里
「首个 text_delta」实测 14833ms。

**为什么不改默认行为**：`enable_thinking` 是**供应商特有字段**，别的 OpenAI 兼容
网关不认（可能直接 400）。所以默认**不下发**（`None` = 不带这个键），
只在 `.env` 显式配置时才发。这与 `llm_frequency_penalty` 的既有做法一致
（`config.py:211-213` 记着「个别网关会因未知字段直接 400」）。

**语义**：`LLM_ENABLE_THINKING` 只在显式设为 `false` / `true` 时下发；
未设置 = 不下发 = 供应商自己的默认行为。

**生产路径读数（2026-11-12 真实验收，交错 A/B，各 2 次）**：首个 text_delta
从 17.2 / 30.3s 降到 6.46 / 6.92s。注意这组数是在 `stream()` 也补上字段之后
才成立的 —— 只改 `complete_raw()` 时线上首字没有变化（详见本文件
`EnableThinkingStreamPayloadTests` 与验收证据 §8）。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", ":memory:")


class EnableThinkingConfigTests(unittest.TestCase):
    def test_setting_is_exposed_and_defaults_to_unset(self):
        """默认不下发 —— 供应商特有字段，不能污染别的 OpenAI 兼容网关。"""
        from server import config as sc

        self.assertTrue(hasattr(sc.settings, "llm_enable_thinking"))
        self.assertIsNone(sc.settings.llm_enable_thinking,
                          "默认必须是 None（= 不下发），而不是 False")

    def test_env_value_is_parsed(self):
        """LLM_ENABLE_THINKING=false → False；true → True；空/未设 → None。"""
        from server import config as sc

        original = sc.settings.llm_enable_thinking
        try:
            for raw, expected in [("false", False), ("true", True),
                                  ("False", False), ("TRUE", True),
                                  ("", None), ("  ", None)]:
                with patch.dict(os.environ, {"LLM_ENABLE_THINKING": raw}):
                    got = sc._bool_env("LLM_ENABLE_THINKING", None)
                self.assertEqual(got, expected, f"LLM_ENABLE_THINKING={raw!r}")
        finally:
            sc.settings.llm_enable_thinking = original


class EnableThinkingPayloadTests(unittest.TestCase):
    def _payload(self, *, enable_thinking, reasoning_effort="low"):
        """造一个能观察到 payload 的 LLM 实例（不发真请求）。

        **不能拿 `sc.settings` 当 config**：CI 没有 `LLM_API_KEY`，
        `offline=True` 会让 `complete_raw()` 直接返回 Mock、根本走不到 payload
        组装（2026-11-12 CI 实测 4 条红）。这里给一个假 key，强制走网络分支，
        由假 client 接住请求。
        """
        from types import SimpleNamespace

        from server.llm import LLM

        cfg = SimpleNamespace(
            llm_provider="openai", llm_base_url="https://example.invalid/v1",
            llm_api_key="test-key", llm_model="test-model",
            llm_temperature=0.9, llm_max_tokens=400, llm_reply_tokens=1200,
            llm_reasoning_effort=reasoning_effort, llm_frequency_penalty=0,
            llm_presence_penalty=0, llm_enable_thinking=enable_thinking,
        )
        captured = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "好"}, "finish_reason": "stop"}],
                        "usage": {}}

        class _Client:
            async def post(self, url, *, json=None, **kwargs):
                captured.update(json or {})
                return _Resp()

        import asyncio

        llm = LLM(config=cfg)
        llm._client = _Client()
        asyncio.run(llm.complete_raw([{"role": "user", "content": "hi"}]))
        return captured

    def test_not_set_means_the_key_is_absent(self):
        """未配置时 payload 里**没有** enable_thinking 这个键。"""
        payload = self._payload(enable_thinking=None)
        self.assertNotIn("enable_thinking", payload)

    def test_false_is_sent_as_false(self):
        payload = self._payload(enable_thinking=False)
        self.assertIs(payload.get("enable_thinking"), False)

    def test_true_is_sent_as_true(self):
        payload = self._payload(enable_thinking=True)
        self.assertIs(payload.get("enable_thinking"), True)

    def test_existing_reasoning_effort_still_sent(self):
        """新字段不能挤掉既有字段 —— reasoning_effort 照发。"""
        payload = self._payload(enable_thinking=False, reasoning_effort="low")
        self.assertEqual(payload.get("reasoning_effort"), "low")
        self.assertIs(payload.get("enable_thinking"), False)


class EnableThinkingStreamPayloadTests(unittest.TestCase):
    """生产对话走的是 `stream()`，不是 `complete_raw()`。

    2026-11-12 真实验收 A/B 暴露的缺口：`enable_thinking` 起初只加进了
    `complete_raw()` 的 payload，`stream()` 自己另拼一份 payload，于是
    A/B 四轮（首字 18.1/14.5/24.8/19.8s）证明**开关对线上路径无效**。
    这组用例把「stream 也必须下发」钉住。
    """

    def _stream_payload(self, *, enable_thinking):
        from types import SimpleNamespace

        from server.llm import LLM

        cfg = SimpleNamespace(
            llm_provider="openai", llm_base_url="https://example.invalid/v1",
            llm_api_key="test-key", llm_model="test-model",
            llm_temperature=0.9, llm_max_tokens=400, llm_reply_tokens=1200,
            llm_reasoning_effort="low", llm_frequency_penalty=0,
            llm_presence_penalty=0, llm_enable_thinking=enable_thinking,
        )
        captured = {}

        class _Resp:
            status_code = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                for line in ('data: {"choices":[{"delta":{"content":"好"}}]}',
                             "data: [DONE]"):
                    yield line

        class _Client:
            def stream(self, method, url, json=None, **kwargs):
                captured.update(json or {})
                return _Resp()

            async def aclose(self):
                return None

        import asyncio

        llm = LLM(config=cfg)
        llm._client = _Client()

        async def drain():
            async for _ in llm.stream([{"role": "user", "content": "hi"}]):
                pass

        asyncio.run(drain())
        return captured

    def test_stream_not_set_means_the_key_is_absent(self):
        payload = self._stream_payload(enable_thinking=None)
        self.assertNotIn("enable_thinking", payload)

    def test_stream_false_is_sent_as_false(self):
        payload = self._stream_payload(enable_thinking=False)
        self.assertIs(payload.get("enable_thinking"), False)

    def test_stream_true_is_sent_as_true(self):
        payload = self._stream_payload(enable_thinking=True)
        self.assertIs(payload.get("enable_thinking"), True)

    def test_stream_keeps_existing_fields(self):
        payload = self._stream_payload(enable_thinking=False)
        self.assertIs(payload.get("stream"), True)
        self.assertEqual(payload.get("reasoning_effort"), "low")
        self.assertEqual(payload.get("max_tokens"), 1200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
