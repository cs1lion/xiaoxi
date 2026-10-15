"""cli_subproc 适配器：把一个 CLI Agent 拉成子进程来干活。

为什么值得单独一种适配器：`claude` / `codex` 这类命令行 Agent 自带工具循环，
我们不需要复刻它的逻辑，只要把派单密件喂给它、把它的输出听回来。

行协议（Worker 侧约定，见 docs/PROTOCOL.md §22）：

    {"type":"progress","step_idx":1,"text":"..."}   进展
    {"type":"token", ...}                          一次模型调用用量
    {"type":"artifact","artifact":{...}}           最终产出，唯一终止事件

非 JSON 行一律降级成 LOG 事件，绝不因为 Worker 多打印了一行废话就中断任务。
`cancel` 杀整个进程组（start_new_session=True），避免子进程再 fork 出孙子进程
变成孤儿——那是"中断了但没中断干净"的典型来源。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from typing import Any, AsyncIterator

from .base import (ARTIFACT, ERROR, LOG, PROGRESS, TOKEN, AdapterError,
                   BaseWorkerAdapter, artifact_event, make_event)
from .registry import register_adapter

DEFAULT_TIMEOUT = 600.0
MAX_LINE = 64 * 1024


@register_adapter("cli_subproc")
class CliSubprocessAdapter(BaseWorkerAdapter):
    adapter_type = "cli_subproc"

    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        self._process: asyncio.subprocess.Process | None = None
        self._cancelled = False

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        executable = self.config.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            raise AdapterError("cli_subproc requires a nonempty `executable`")
        packet = dict(briefing_packet or {})
        env = dict(os.environ)
        # 派单密件经环境变量递给 Worker；不落盘、不进命令行参数——命令行参数会
        # 出现在 ps 里，等于把任务详情广播给同机所有进程。
        env["AQI_COMMISSION_ID"] = str(commission_id)
        env["AQI_BRIEFING_PACKET"] = json.dumps(packet, ensure_ascii=False)
        telemetry = packet.get("telemetry") or {}
        if telemetry.get("proxy_url"):
            # 让 Worker 自己的模型调用也走观测网关，Token 才记得起来。
            env["AQI_TELEMETRY_PROXY"] = str(telemetry["proxy_url"])
            env.setdefault("OPENAI_BASE_URL", str(telemetry["proxy_url"]))
        for key, value in (self.config.get("env") or {}).items():
            env[str(key)] = str(value)
        return {
            "commission_id": str(commission_id),
            "agent_id": str(self.config.get("id") or ""),
            "executable": executable.strip(),
            "args": [str(item) for item in (self.config.get("args") or [])],
            "cwd": str(self.config.get("cwd") or "") or None,
            "env": env,
            "timeout": float(self.config.get("timeout") or DEFAULT_TIMEOUT),
        }

    async def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        context = task_spec.get("context") or {}
        executable = context.get("executable") or self.config.get("executable")
        if not isinstance(executable, str) or not executable.strip():
            raise AdapterError("cli_subproc needs `executable` from prepare_context")
        args = [str(item) for item in (context.get("args") or self.config.get("args") or [])]
        timeout = float(context.get("timeout") or DEFAULT_TIMEOUT)
        step_idx = 0
        try:
            self._process = await asyncio.create_subprocess_exec(
                executable.strip(), *args,
                cwd=context.get("cwd") or None,
                env=context.get("env") or None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            raise AdapterError(f"executable not found: {executable}") from exc
        except Exception as exc:
            raise AdapterError(f"failed to start subprocess: {type(exc).__name__}") from exc

        assert self._process.stdout is not None
        try:
            while True:
                raw = await self._process.stdout.readline()
                if not raw:
                    break
                if len(raw) > MAX_LINE:
                    raw = raw[:MAX_LINE]
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                event = self._parse_line(line, step_idx)
                if event is not None:
                    step_idx += 1
                    yield event
                    if event["type"] in (ARTIFACT, ERROR):
                        return
            return_code = await asyncio.wait_for(self._process.wait(), timeout=max(1.0, timeout))
            if self._cancelled:
                return
            if return_code != 0:
                yield make_event(ERROR, step_idx=step_idx, text=f"子进程退出码 {return_code}")
        except asyncio.TimeoutError:
            await self.cancel()
            yield make_event(ERROR, step_idx=step_idx, text=f"执行超时（{timeout:.0f}s）")
        finally:
            await self._reap()

    async def cancel(self) -> None:
        """杀整个进程组并回收。幂等：重复调用只发一次信号。"""
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
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), 5.0)

    @staticmethod
    def _parse_line(line: str, step_idx: int) -> dict[str, Any] | None:
        try:
            payload = json.loads(line)
        except (ValueError, TypeError):
            return make_event(LOG, step_idx=step_idx, text=line[:400])
        if not isinstance(payload, dict):
            return make_event(LOG, step_idx=step_idx, text=line[:400])
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
        return make_event(LOG, step_idx=step_idx, text=line[:400])

    async def _reap(self) -> None:
        process = self._process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 2.0)
        if process.returncode is None:
            await self.cancel()


__all__ = ["CliSubprocessAdapter"]
