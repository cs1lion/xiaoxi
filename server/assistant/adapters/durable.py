"""durable 适配器：把一单交给本机 pi-durable sidecar 执行。

sidecar 的耐久保证（每步 checkpoint、进程死了重开续跑）只在它持有存储和任务时
成立，所以这一层只做两件事：把派单密件递进去，把标准事件听回来。幂等键是
commission_id——重复投递返回同一单，不会开出第二份工。

内部契约见 docs/PI_DURABLE_MIGRATION.md §4.3。事件的五种类型与协议 §22.2
一一对应，调度核心因此不需要知道这一单跑在子进程、远端还是 sidecar 里。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, AsyncIterator

from .base import (ARTIFACT, ERROR, LOG, PROGRESS, TOKEN, AdapterError,
                   BaseWorkerAdapter, artifact_event, make_event)
from .registry import register_adapter

DEFAULT_TIMEOUT = 600.0

# 与 agent-runtime/src/routes.ts 必须逐项一致；check_protocol.py 会比对两边，
# 改名只在一边发生会直接让门禁报警，而不是运行时静默 404。
SIDECAR_ROUTES = (
    "/healthz",
    "/v1/runs",
    "/v1/runs/{id}",
    "/v1/runs/{id}/cancel",
    "/v1/events",
    "/v1/shutdown",
)


@register_adapter("durable")
class DurableAdapter(BaseWorkerAdapter):
    adapter_type = "durable"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._task_id: int | None = None
        self._cancelled = False

    def _endpoint(self) -> str:
        endpoint = self.config.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise AdapterError("durable requires a nonempty `endpoint`")
        return endpoint.strip().rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        token = self.config.get("token")
        if not (isinstance(token, str) and token):
            env_name = self.config.get("token_env")
            token = os.environ.get(env_name or "") if isinstance(env_name, str) else ""
        if isinstance(token, str) and token:
            headers["authorization"] = f"Bearer {token}"
        return headers

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        return {
            "commission_id": str(commission_id),
            "agent_id": str(self.config.get("id") or ""),
            "endpoint": self._endpoint(),
            "headers": self._headers(),
            "timeout": float(self.config.get("timeout") or DEFAULT_TIMEOUT),
        }

    async def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        import httpx

        context = task_spec.get("context") or {}
        commission_id = str(context.get("commission_id") or task_spec.get("commission_id") or "")
        packet = task_spec.get("packet") or {}
        endpoint = str(context.get("endpoint") or "").rstrip("/")
        headers = dict(context.get("headers") or {})
        timeout = float(context.get("timeout") or DEFAULT_TIMEOUT)
        step_idx = 0
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                created = await client.post(
                    f"{endpoint}/v1/runs",
                    headers=headers,
                    json={"commission_id": commission_id,
                          "agent_id": str(context.get("agent_id") or ""),
                          "packet": packet},
                )
                if created.status_code >= 400:
                    yield make_event(ERROR, step_idx=0,
                                     text=f"执行内核拒绝接单：HTTP {created.status_code}")
                    return
                ref = created.json()
                self._task_id = int(ref.get("task_id") or 0)
                async with client.stream("GET", f"{endpoint}/v1/events",
                                         params={"task_id": self._task_id},
                                         headers=headers) as response:
                    if response.status_code >= 400:
                        yield make_event(ERROR, step_idx=0,
                                         text=f"执行内核事件流 HTTP {response.status_code}")
                        return
                    async for line in response.aiter_lines():
                        if self._cancelled:
                            return
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if not payload:
                            continue
                        try:
                            event = json.loads(payload)
                        except ValueError:
                            continue
                        if os.environ.get("AQI_DURABLE_DEBUG"):
                            print(f"[durable] {json.dumps(event, ensure_ascii=False)[:400]}", file=sys.stderr)
                        mapped = self._map(event, step_idx)
                        if mapped is None:
                            continue
                        step_idx += 1
                        yield mapped
                        # artifact 不是"停止读取"：sidecar 会在交差之后补一条
                        # token 用量，提前 return 会把这条用量丢掉。
                        if mapped["type"] == ERROR:
                            return
        except Exception as exc:
            yield make_event(ERROR, step_idx=step_idx,
                             text=f"{type(exc).__name__}: {exc}"[:240])

    async def cancel(self) -> None:
        """幂等，绝不抛异常。终端任务上的取消由 sidecar 自己变成空操作。"""
        self._cancelled = True
        task_id = self._task_id
        if not task_id:
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{self._endpoint()}/v1/runs/{task_id}/cancel",
                                  headers=self._headers())
        except Exception:
            pass

    @staticmethod
    def _map(event: Any, fallback_step: int) -> dict[str, Any] | None:
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        step = int(event.get("step_idx") or fallback_step + 1)
        if kind == PROGRESS:
            return make_event(PROGRESS, step_idx=step, text=str(event.get("text") or "")[:600])
        if kind == TOKEN:
            return make_event(TOKEN, step_idx=step,
                              model=str(event.get("model") or ""),
                              prompt_tokens=int(event.get("prompt_tokens") or 0),
                              completion_tokens=int(event.get("completion_tokens") or 0),
                              ttft_ms=int(event.get("ttft_ms") or 0),
                              duration_ms=int(event.get("duration_ms") or 0),
                              status_code=int(event.get("status_code") or 0),
                              error=str(event.get("error") or "")[:200])
        if kind == ARTIFACT:
            artifact = event.get("artifact")
            return artifact_event(artifact) if isinstance(artifact, dict) else None
        if kind in (LOG, ERROR):
            return make_event(kind, step_idx=fallback_step,
                              text=str(event.get("text") or "")[:600])
        return None


__all__ = ["DurableAdapter"]
