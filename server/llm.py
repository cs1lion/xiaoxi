from __future__ import annotations

import asyncio
import json
import random
import re
from typing import Any, AsyncIterator

import httpx

from .config import settings


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

    def __init__(self) -> None:
        self.provider = settings.llm_provider
        self.base_url = settings.llm_base_url.rstrip("/")
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.offline = self.provider == "mock" or not self.api_key
        self._client: httpx.AsyncClient | None = None
        self.last_usage: dict[str, Any] = {}

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), headers=headers)
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
    ) -> dict[str, Any]:
        """Full result: text plus finish_reason / usage / reasoning.

        The text alone is not enough. A reasoning model given a tight token
        budget will spend all of it thinking and return empty content with
        finish_reason='length' -- which looks identical to "the model has
        nothing to say" unless you keep the metadata.

        `model` exists so a slow-but-precise model can be used for bookkeeping
        while the fast role-playing one handles the conversation.
        """
        if self.offline:
            return {"text": self._mock(messages), "finish_reason": "stop", "usage": {}, "reasoning": ""}
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        # 重复抑制。OpenAI 兼容接口普遍支持，但个别自建/代理网关会因未知字段
        # 直接 400，所以默认 0（不下发），要用再在 .env 里开。
        # 建议对话场景 frequency=0.2、presence=0.2；再高会让它不敢复用正常词。
        if settings.llm_frequency_penalty:
            payload["frequency_penalty"] = settings.llm_frequency_penalty
        if settings.llm_presence_penalty:
            payload["presence_penalty"] = settings.llm_presence_penalty
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        effort = reasoning_effort if reasoning_effort is not None else settings.llm_reasoning_effort
        if effort:
            payload["reasoning_effort"] = effort

        client = await self.client()
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = await client.post(f"{self.base_url}/chat/completions", json=payload)
                if resp.status_code >= 400:
                    last_error = LLMError(f"{resp.status_code}: {resp.text[:300]}")
                    if resp.status_code < 500:
                        break
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
                await asyncio.sleep(0.6 * (attempt + 1))
        raise LLMError(str(last_error))

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

        client = await self.client()
        for attempt in range(3):
            started = False
            try:
                async with client.stream(
                    "POST", f"{self.base_url}/chat/completions", json=payload
                ) as resp:
                    resp.raise_for_status()
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
        if "JSON" in messages[0]["content"] or "只输出 JSON" in messages[0]["content"]:
            return json.dumps({"facts": [], "episodes": [], "todos": [], "self_states": []}, ensure_ascii=False)
        pool = [
            "嗯，这个我听着呢。",
            "继续说，我在。",
            "这个我还真没什么头绪，你想到哪一步了？",
            "记下了。……不过我得说，你这计划听着挺美。",
        ]
        if "？" in last_user or "吗" in last_user:
            return "大概吧，我猜的，不一定准。"
        return random.choice(pool)


llm = LLM()