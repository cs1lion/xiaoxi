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
        if self.offline:
            return self._mock(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
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
                return data["choices"][0]["message"]["content"] or ""
            except (httpx.HTTPError, KeyError, IndexError) as exc:  # pragma: no cover - network
                last_error = exc
                await asyncio.sleep(0.6 * (attempt + 1))
        raise LLMError(str(last_error))

    async def stream(self, messages: list[dict[str, str]], *, temperature: float | None = None) -> AsyncIterator[str]:
        if self.offline:
            text = self._mock(messages)
            for i in range(0, len(text), 6):
                yield text[i : i + 6]
                await asyncio.sleep(0.02)
            return
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": settings.llm_max_tokens,
            "stream": True,
        }
        client = await self.client()
        async with client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"]
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                piece = delta.get("content")
                if piece:
                    yield piece

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