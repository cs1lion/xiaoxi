"""mcp 适配器：经 Model Context Protocol 桥接一个 MCP Server 当作 Worker。

MCP over stdio 的三个必要动作：initialize 握手、tools/list 发现自己能干什么、
tools/call 真正下单。这里只实现"驱动一次作业"所需的最小面——我们不复刻 MCP
客户端，只做够用的 JSON-RPC 收发，因此没有引入额外依赖。

作业结果从 tools/call 的 content 里取：约定 Worker 把最终产出放在一个
`{"type":"artifact","artifact":{...}}` 的 text content 块里。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from typing import Any, AsyncIterator

from .base import (ARTIFACT, ERROR, LOG, TOKEN, AdapterError,
                   BaseWorkerAdapter, artifact_event, make_event)
from .registry import register_adapter

DEFAULT_TIMEOUT = 600.0
PROTOCOL_VERSION = "2024-11-05"


@register_adapter("mcp")
class McpBridgeAdapter(BaseWorkerAdapter):
    adapter_type = "mcp"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._cancelled = False

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        executable = self.config.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            raise AdapterError("mcp requires a nonempty `executable` (the MCP server command)")
        packet = dict(briefing_packet or {})
        return {
            "commission_id": str(commission_id),
            "agent_id": str(self.config.get("id") or ""),
            "executable": executable.strip(),
            "args": [str(item) for item in (self.config.get("args") or [])],
            "tool": str(self.config.get("tool") or "run_commission"),
            "timeout": float(self.config.get("timeout") or DEFAULT_TIMEOUT),
            "objective": str(packet.get("objective") or ""),
            "deliverable": str(packet.get("deliverable") or ""),
            "constraints": list(packet.get("constraints") or []),
        }

    async def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        context = task_spec.get("context") or {}
        executable = context.get("executable") or self.config.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            raise AdapterError("mcp needs `executable` from prepare_context")
        timeout = float(context.get("timeout") or DEFAULT_TIMEOUT)
        try:
            self._process = await asyncio.create_subprocess_exec(
                executable.strip(),
                *[str(item) for item in (context.get("args") or self.config.get("args") or [])],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise AdapterError(f"mcp server not found: {executable}") from exc
        except Exception as exc:
            raise AdapterError(f"failed to start mcp server: {type(exc).__name__}") from exc

        step_idx = 0
        try:
            await self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "aqi-executive", "version": "3.0"},
            }, timeout)
            yield make_event(LOG, step_idx=0, text="MCP 握手完成")
            result = await self._request("tools/call", {
                "name": str(context.get("tool") or "run_commission"),
                "arguments": {key: context.get(key) for key in
                              ("commission_id", "objective", "deliverable", "constraints")},
            }, timeout)
            artifact = self._extract_artifact(result)
            if artifact is None:
                yield make_event(ERROR, step_idx=step_idx,
                                 text="MCP 返回里没有 artifact content 块")
                return
            yield artifact_event(artifact)
        except asyncio.TimeoutError:
            await self.cancel()
            yield make_event(ERROR, step_idx=step_idx, text=f"MCP 调用超时（{timeout:.0f}s）")
        except Exception as exc:
            yield make_event(ERROR, step_idx=step_idx,
                             text=f"{type(exc).__name__}: {exc}"[:240])
        finally:
            await self._reap()

    async def cancel(self) -> None:
        self._cancelled = True
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 5.0)
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)

    # ---------- 内部 ----------
    def _allocate_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def _request(self, method: str, params: dict[str, Any],
                       timeout: float) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise AdapterError("mcp server is not running")
        request_id = self._allocate_id()
        process.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
        }, ensure_ascii=False) + "\n").encode("utf-8"))
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), timeout)
            if not line:
                raise AdapterError("mcp server closed stdout")
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except (ValueError, TypeError):
                continue  # 通知/日志行，与本请求无关
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if message.get("error"):
                raise AdapterError(f"mcp error: {message['error']}"[:240])
            result = message.get("result")
            return result if isinstance(result, dict) else {}

    @staticmethod
    def _extract_artifact(result: dict[str, Any]) -> dict[str, Any] | None:
        content = result.get("content")
        if not isinstance(content, list):
            return None
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            try:
                payload = json.loads(str(block.get("text") or ""))
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and payload.get("type") == ARTIFACT:
                artifact = payload.get("artifact")
                if isinstance(artifact, dict):
                    return artifact
        return None

    async def _reap(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 2.0)
        if process.returncode is None:
            await self.cancel()


__all__ = ["McpBridgeAdapter"]
