from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Any, AsyncIterator

import httpx

from .config import settings


# 离线 Mock 回复池（2026-11-06）：每条都必须带合法 [情绪] 标签。
# 标签是 TTS 七档声学参数与 Avatar 表情的唯一输入；此前 4 句全无标签，
# parse_emotion 只能回退 detect_emotion → 恒 base，离线体验里七档一档都
# 触发不了（包括前一天刚补的 volume）。合法集合不在这里硬编码，由
# evals.checks:offline_emotion_tags 用 config/persona.yaml 的 tts_emotions
# 逐条校验，并带条数下限棘轮防真空绿。
MOCK_REPLIES: tuple[str, ...] = (
    "[base]嗯，这个我听着呢。",
    "[base]继续说，我在。",
    "[认真]这个我还真没什么头绪，你想到哪一步了？",
    "[轻微不服]记下了。……不过我得说，你这计划听着挺美。",
)
MOCK_QUESTION_REPLY = "[认真]大概吧，我猜的，不一定准。"


class LLMError(RuntimeError):
    pass


def _strip_fence(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        return fence.group(1)
    return text


def _loose_json(text: str) -> Any:
    text = _strip_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"[\{\[].*[\}\]]", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


class LLM:
    """Thin async client. Provider is chosen by env, shape is OpenAI-compatible."""

    def __init__(self, *, config: Any = None, request_timeout: float = 60.0, trust_env: bool = True) -> None:
        self.settings = config or settings
        self.provider = self.settings.llm_provider
        self.base_url = self.settings.llm_base_url.rstrip("/")
        self.api_key = self.settings.llm_api_key
        self.model = self.settings.llm_model
        self.request_timeout = request_timeout
        self.trust_env = trust_env
        self.offline = self.provider == "mock" or not self.api_key
        self._client: httpx.AsyncClient | None = None
        self.last_usage: dict[str, Any] = {}

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self.request_timeout, connect=min(10.0, self.request_timeout)), headers=headers, trust_env=self.trust_env)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        result = await self.complete_raw(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )
        return result["text"]

    async def complete_raw(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        """Full result: text plus finish_reason / usage / reasoning.

        The text alone is not enough. A reasoning model given a tight token
        budget will spend all of it thinking and return empty content with
        finish_reason='length' -- which looks identical to "the model has
        nothing to say" unless you keep the metadata.

        `model` exists so a slow-but-precise model can be used for bookkeeping
        while the fast role-playing one handles the conversation.
        """
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts_must_be_1_to_3")
        if self.offline:
            return {"text": self._mock(messages), "finish_reason": "stop", "usage": {}, "reasoning": ""}
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
        }
        # 重复抑制。OpenAI 兼容接口普遍支持，但个别自建/代理网关会因未知字段
        # 直接 400，所以默认 0（不下发），要用再在 .env 里开。
        # 建议对话场景 frequency=0.2、presence=0.2；再高会让它不敢复用正常词。
        if self.settings.llm_frequency_penalty:
            payload["frequency_penalty"] = self.settings.llm_frequency_penalty
        if self.settings.llm_presence_penalty:
            payload["presence_penalty"] = self.settings.llm_presence_penalty
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        effort = reasoning_effort if reasoning_effort is not None else self.settings.llm_reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort
        # 思考模式开关。**只在显式配置过时下发** —— enable_thinking 是供应商特有
        # 字段，别家 OpenAI 兼容网关不认，可能直接 400。未配置 = 不带这个键，
        # 走供应商自己的默认行为。
        # 用 getattr 而不是直接取属性：调用方可能传一个不含该键的 settings
        # 桩（scripts/test_llm_client.py:111 那种 SimpleNamespace）。
        enable_thinking = getattr(self.settings, "llm_enable_thinking", None)
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking

        client = await self.client()
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
                if resp.status_code >= 400:
                    # Provider error bodies may echo credentials or request data.
                    last_error = LLMError(f"HTTP {resp.status_code}")
                    if resp.status_code < 500:
                        break
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(0.6 * (attempt + 1))
                    continue
                data = resp.json()
                choice = data["choices"][0]
                message = choice["message"]
                self.last_usage = data.get("usage", {}) or {}
                return {
                    "text": message.get("content") or "",
                    "reasoning": message.get("reasoning") or message.get("reasoning_content") or "",
                    "finish_reason": choice.get("finish_reason", ""),
                    "usage": self.last_usage,
                }
            except (httpx.HTTPError, KeyError, IndexError) as exc:  # pragma: no cover - network
                last_error = exc
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(0.6 * (attempt + 1))
        raise LLMError(str(last_error) if isinstance(last_error, LLMError) else f"request_failed:{type(last_error).__name__}")

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield content deltas as they arrive.

        Reasoning models stream their thinking in `reasoning` first; we skip it
        and yield only `content`, because the thinking text must never reach
        the voice or the subtitle.
        """
        if self.offline:
            text = self._mock(messages)
            for i in range(0, len(text), 6):
                yield text[i : i + 6]
                await asyncio.sleep(0.02)
            return
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_reply_tokens,
            "stream": True,
        }
        effort = reasoning_effort if reasoning_effort is not None else settings.llm_reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort
        # 与 complete_raw 同一套三态语义：未配置不下发。生产对话走 stream()，
        # 这条分支漏了字段等于开关对线上无效（2026-11-12 真实验收 A/B 发现）。
        enable_thinking = getattr(self.settings, "llm_enable_thinking", None)
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking

        client = await self.client()
        for attempt in range(3):
            started = False
            try:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        if resp.status_code < 500:
                            # 与 complete_raw 同语义：4xx 重试不会自愈（S3 演练②实测
                            # 坏 Key 401 重试三次把失败拖到 ~4s），直接失败；错误体
                            # 可能回显凭据，信息里只留状态码。
                            raise LLMError(f"HTTP {resp.status_code}")
                        resp.raise_for_status()  # 5xx -> HTTPStatusError -> 下方退避重试
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            event = json.loads(data)
                            choice = event["choices"][0]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        started = True
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            yield piece
                    return
            except httpx.HTTPError as exc:
                if started:
                    raise
                last: Exception | None = exc
                await asyncio.sleep(0.6 * (attempt + 1))
                if attempt == 2:
                    raise LLMError(str(exc))
        raise LLMError("stream failed")

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 900,
        retries: int = 2,
    ) -> Any:
        instruction = messages[0]["content"] + "\n\n只输出 JSON，不要任何解释或代码块标记。"
        msgs = [{"role": "system", "content": instruction}] + messages[1:]
        last_error: Exception | None = None
        for _ in range(retries + 1):
            raw = await self.complete(msgs, temperature=temperature, max_tokens=max_tokens, json_mode=not self.offline)
            try:
                return _loose_json(raw)
            except json.JSONDecodeError as exc:
                last_error = exc
        raise LLMError(f"invalid json from model: {last_error}")

    # ---- offline fallback so the stack runs with zero credentials ----
    def _mock(self, messages: list[dict[str, str]]) -> str:
        last_user = ""
        for msg in reversed(messages):
            if msg["role"] == "user":
                last_user = msg["content"]
                break
        system = messages[0]["content"] if messages else ""
        if '"memories"' in system:
            # EXTRACT_SYSTEM 要求的输出结构是 {"memories": [...]}，而旧 mock
            # 返回的是 facts/episodes/todos/self_states —— 键名从未对上，
            # parse_extraction 一律读成空，trace 里连 error 都没有。
            # 2026-11-06 对齐契约；离线仍不造假记忆（返回空），但留一条日志
            # 让开发者知道「写入链路此刻是关闭的」而不是静默空转。
            logging.getLogger("server.llm").debug(
                "offline mock: memory extraction disabled, returning empty memories")
            return json.dumps({"memories": []}, ensure_ascii=False)
        if "JSON" in system or "只输出 JSON" in system:
            # complete_json 的规划请求（server/assistant/service.py:806）期望 dict。
            return json.dumps({"steps": []}, ensure_ascii=False)
        if "？" in last_user or "吗" in last_user:
            return MOCK_QUESTION_REPLY
        return random.choice(MOCK_REPLIES)


llm = LLM()
