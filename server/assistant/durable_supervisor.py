"""durable sidecar 的进程监督：起、探活、收尸。

设计依据 docs/PI_DURABLE_MIGRATION.md §4.2。三条边界：

- 只起一个 sidecar，它持有唯一的 SQLite（库不强制单写者，所以锁在 Node 侧）。
- 启动失败是**显式失败**：抛 AdapterError，派单转 BLOCKED 并说"执行内核不可用"，
  不静默改派、不假装执行。
- 关闭先走 `/v1/shutdown`，超时才杀进程树（复用适配器层的 terminate_process_group）。

默认不启用。由调用方显式创建并 start()，所以现有链路零影响。
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from .adapters.base import AdapterError, terminate_process_group

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENTRY = ROOT / "agent-runtime" / "src" / "main.ts"


class DurableSupervisor:
    def __init__(self, *, directory: str | Path, node: str = "node",
                 entry: str | Path | None = None, token: str | None = None,
                 startup_timeout: float = 15.0, max_restarts: int = 3,
                 window_seconds: float = 600.0, auto_restart: bool = False,
                 extra_env: dict[str, str] | None = None):
        self.directory = Path(directory)
        self.node = node
        self.entry = Path(entry) if entry is not None else DEFAULT_ENTRY
        self.token = token or secrets.token_urlsafe(24)
        self.startup_timeout = float(startup_timeout)
        self.max_restarts = int(max_restarts)
        self.window_seconds = float(window_seconds)
        self.auto_restart = auto_restart
        # 随 sidecar 进程一起给的环境变量（审批白名单、遥测代理等）。子进程在
        # 启动时读它们，所以只能在 _spawn 里注入，不能事后改。
        self.extra_env = {str(k): str(v) for k, v in (extra_env or {}).items() if str(v)}

        self.endpoint: str | None = None
        self.available = False
        self.reason = "not_started"
        self._process: asyncio.subprocess.Process | None = None
        self._monitor: asyncio.Task | None = None
        self._drain: asyncio.Task | None = None
        self._patrol: asyncio.Task | None = None
        self._stopping = False
        self._restarts: list[float] = []

    async def start(self) -> str:
        if self.endpoint is not None:
            return self.endpoint
        self._stopping = False
        self.endpoint = await self._spawn()
        if self.auto_restart and self._monitor is None:
            self._monitor = asyncio.create_task(self._watch())
        return self.endpoint

    async def _spawn(self) -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["XIAOXI_RUNTIME_DIR"] = str(self.directory)
        env["XIAOXI_RUNTIME_TOKEN"] = self.token
        env.update(self.extra_env)
        # `XIAOXI_APPROVED_TOOLS` 是测试用的"全局预批准"开关：它绕过所有审批，
        # 且按进程生效。生产的放行必须按单走 /v1/approvals，不能继承宿主 shell
        # 里残留的这个变量，否则一整个 sidecar 的所有单都不再需要确认。
        env.pop("XIAOXI_APPROVED_TOOLS", None)
        try:
            process = await asyncio.create_subprocess_exec(
                self.node, "--experimental-strip-types", str(self.entry),
                cwd=str(self.entry.parent.parent),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            self.available = False
            self.reason = "node_not_found"
            raise AdapterError("执行内核不可用：找不到 node") from exc
        self._process = process
        assert process.stdout is not None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), self.startup_timeout)
        except asyncio.TimeoutError as exc:
            await self.stop()
            self.reason = "startup_timeout"
            raise AdapterError("执行内核不可用：sidecar 未在时限内就绪") from exc
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith("PORT "):
            await self.stop()
            self.reason = "bad_startup_output"
            detail = await self._stderr_tail(process)
            raise AdapterError(f"执行内核不可用：sidecar 启动输出异常（{text[:80] or detail[:120]}）")
        endpoint = f"http://127.0.0.1:{int(text.split()[1])}"
        self._drain = asyncio.create_task(self._drain_logs(process))
        await self._wait_healthy(endpoint)
        self.endpoint = endpoint
        self.available = True
        self.reason = "ready"
        return endpoint

    async def _wait_healthy(self, endpoint: str) -> None:
        deadline = time.monotonic() + self.startup_timeout
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                try:
                    response = await client.get(f"{endpoint}/healthz")
                    if response.status_code == 200:
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        await self.stop()
        self.reason = "unhealthy"
        raise AdapterError("执行内核不可用：健康检查未通过")

    async def _drain_logs(self, process: asyncio.subprocess.Process) -> None:
        # 日志必须落盘：sidecar 的 stderr 读掉就丢，排查时什么都看不到。
        await asyncio.gather(
            self._pipe_to(process.stdout, "sidecar.out.log"),
            self._pipe_to(process.stderr, "sidecar.err.log"),
        )

    async def _pipe_to(self, stream, name: str) -> None:
        if stream is None:
            return
        with contextlib.suppress(Exception):
            with (self.directory / name).open("ab") as handle:
                while True:
                    chunk = await stream.readline()
                    if not chunk:
                        return
                    handle.write(chunk)
                    handle.flush()

    @staticmethod
    async def _stderr_tail(process: asyncio.subprocess.Process, limit: int = 300) -> str:
        if process.stderr is None:
            return ""
        with contextlib.suppress(Exception):
            chunk = await asyncio.wait_for(process.stderr.read(limit), 1.0)
            return chunk.decode("utf-8", "replace")
        return ""

    async def _watch(self) -> None:
        while not self._stopping:
            process = self._process
            if process is None:
                return
            await process.wait()
            if self._stopping:
                return
            now = time.monotonic()
            self._restarts = [item for item in self._restarts if now - item < self.window_seconds]
            if len(self._restarts) >= self.max_restarts:
                self.available = False
                self.reason = "restart_limit"
                self.endpoint = None
                return
            self._restarts.append(now)
            try:
                self.endpoint = await self._spawn()
            except AdapterError:
                self.available = False
                self.reason = "restart_failed"
                return

    async def healthy(self) -> bool:
        endpoint = self.endpoint
        if endpoint is None:
            return False
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{endpoint}/healthz")
                return response.status_code == 200
        except Exception:
            return False

    async def start_patrol(self, interval: float = 60.0) -> None:
        """进程还活着但不应答时，按 60s 巡检重起。计入重启预算。"""
        if self._patrol is not None:
            return
        self._patrol = asyncio.create_task(self._patrol_loop(max(1.0, float(interval))))

    async def _patrol_loop(self, interval: float) -> None:
        while not self._stopping:
            await asyncio.sleep(interval)
            if self._stopping:
                return
            if await self.healthy():
                continue
            now = time.monotonic()
            self._restarts = [item for item in self._restarts if now - item < self.window_seconds]
            if len(self._restarts) >= self.max_restarts:
                self.available = False
                self.reason = "restart_limit"
                return
            self._restarts.append(now)
            # Teardown rather than stop(): stop() would cancel this very task.
            await self._teardown()
            try:
                await self._spawn()
            except AdapterError as exc:
                self.available = False
                self.reason = f"restart_failed:{exc}"
                return

    async def stop(self) -> None:
        self._stopping = True
        await self._cancel_tasks()
        await self._teardown()

    async def _cancel_tasks(self) -> None:
        for name in ("_monitor", "_drain", "_patrol"):
            task = getattr(self, name)
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            setattr(self, name, None)

    async def _teardown(self) -> None:
        process, self._process = self._process, None
        endpoint, self.endpoint = self.endpoint, None
        self.available = False
        if process is None:
            return
        if endpoint is not None:
            with contextlib.suppress(Exception):
                async with httpx.AsyncClient(timeout=3.0) as client:
                    await client.post(f"{endpoint}/v1/shutdown",
                                      headers={"authorization": f"Bearer {self.token}"})
        with contextlib.suppress(Exception):
            await asyncio.wait_for(process.wait(), 10.0)
        await terminate_process_group(process)

    async def __aenter__(self) -> "DurableSupervisor":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    def config(self) -> dict[str, Any]:
        """直接塞进花名册条目用的配置；没起来就抛，绝不给一个假端点。"""
        if not self.available or self.endpoint is None:
            raise AdapterError(f"执行内核不可用（{self.reason}）")
        return {"endpoint": self.endpoint, "token": self.token}

    def current_endpoint(self) -> str | None:
        """内核现在的地址。硬杀重起后端口会变，所以不要缓存调用方的结果。

        返回 None 表示内核此刻不可用；调用方据此决定抛错，而不是拿旧地址去连。
        进程已退出但 `_watch` 还没跑完 `_spawn` 时，`endpoint` 仍是上一次的端口
        ——那一小段窗口里返回旧地址，适配器只会得到 `ConnectError`，看上去像
        "内核拉起来了但连不上"。先看进程是否活着，比看缓存更可信。
        """
        if not self.available or self.endpoint is None:
            return None
        process = self._process
        if process is None or process.returncode is not None:
            return None
        return self.endpoint
