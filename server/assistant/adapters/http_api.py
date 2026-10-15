"""http_api 适配器：把一个远端 HTTP Agent 当成 Worker 来驱动作业。

响应体是 JSON 时按一次性产出处理；`Content-Type: text/event-stream` 时按 SSE
逐行解析，data 行沿用与 cli_subproc 同一套事件协议。两种响应共用一套事件出口，
因此上层的遥测落库与初审逻辑完全不需要区分 Agent 是怎么实现的。
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .base import (ARTIFACT, ERROR, LOG, PROGRESS, TOKEN, AdapterError,
                   BaseWorkerAdapter, artifact_event, make_event)
from .registry import register_adapter

DEFAULT_TIMEOUT = 600.0


@register_adapter("http_api")
class HttpApiAdapter(BaseWorkerAdapter):
    adapter_type = "http_api"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._client = None
        self._cancelled = False

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        endpoint = self.config.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise AdapterError("http_api requires a nonempty `endpoint`")
        headers = {"content-type": "application/json"}
        for key, value in (self.config.get("headers") or {}).items():
            headers[str(key)] = str(value)
        return {
            "commission_id": str(commission_id),
            "agent_id": str(self.config.get("id") or ""),
            "endpoint": endpoint.strip(),
            "method": str(self.config.get("method") or "POST").upper(),
            "headers": headers,
            "timeout": float(self.config.get("timeout") or DEFAULT_TIMEOUT),
        }

    async def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        import httpx

        context = task_spec.get("context") or {}
        endpoint = context.get("endpoint") or self.config.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise AdapterError("http_api needs `endpoint` from prepare_context")
        timeout = float(context.get("timeout") or DEFAULT_TIMEOUT)
        self._client = httpx.AsyncClient(timeout=timeout)
        step_idx = 0
        try:
            async with self._client.stream(
                str(context.get("method") or "POST"), endpoint.strip(),
                json=task_spec.get("payload") or task_spec,
                headers=context.get("headers") or {"content-type": "application/json"},
            ) as response:
                if response.status_code >= 400:
                    yield make_event(ERROR, step_idx=step_idx,
                                     text=f"上游返回 {response.status_code}")
                    return
                streaming = "text/event-stream" in (response.headers.get("content-type") or "")
                if not streaming:
                    body = (await response.aread()).decode("utf-8", "replace")
                    event = self._parse_body(body, step_idx)
                    if event is not None:
                        yield event
                    return
                async for line in response.aiter_lines():
                    if self._cancelled:
                        return
                    if not line.strip() or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    event = self._parse_body(line[5:].strip(), step_idx)
                    if event is not None:
                        step_idx += 1
                        yield event
                        if event["type"] in (ARTIFACT, ERROR):
                            return
        except Exception as exc:
            yield make_event(ERROR, step_idx=step_idx,
                             text=f"{type(exc).__name__}: {exc}"[:240])
        finally:
            if self._client is not None:
                await self._client.aclose()
                self._client = None

    async def cancel(self) -> None:
        self._cancelled = True
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    @staticmethod
    def _parse_body(body: str, step_idx: int) -> dict[str, Any] | None:
        body = body.strip()
        if not body:
            return None
        try:
            payload = json.loads(body)
        except (ValueError, TypeError):
            return make_event(LOG, step_idx=step_idx, text=body[:400])
        if not isinstance(payload, dict):
            return make_event(LOG, step_idx=step_idx, text=body[:400])
        kind = payload.get("type")
        if kind == ARTIFACT:
            artifact = payload.get("artifact")
            if not isinstance(artifact, dict):
                return make_event(ERROR, step_idx=step_idx, text="artifact 事件缺少 artifact 对象")
            return artifact_event(artifact)
        if kind == TOKEN:
            return make_event(TOKEN, step_idx=int(payload.get("step_idx") or step_idx),
                              model=str(payload.get("model") or ""),
                              prompt_tokens=int(payload.get("prompt_tokens") or 0),
                              completion_tokens=int(payload.get("completion_tokens") or 0),
                              ttft_ms=int(payload.get("ttft_ms") or 0),
                              duration_ms=int(payload.get("duration_ms") or 0),
                              status_code=int(payload.get("status_code") or 0),
                              error=str(payload.get("error") or "")[:200])
        if kind in (PROGRESS, LOG, ERROR):
            return make_event(kind, step_idx=int(payload.get("step_idx") or step_idx),
                              text=str(payload.get("text") or "")[:600])
        # 一次性 JSON 响应且没带 type：整个对象就当产出，省得 Worker 方多包一层。
        if payload.get("summary") or payload.get("artifacts"):
            return artifact_event(payload)
        return make_event(LOG, step_idx=step_idx, text=body[:400])


__all__ = ["HttpApiAdapter"]
