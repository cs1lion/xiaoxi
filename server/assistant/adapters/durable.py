"""durable 适配器：把一单交给本机 pi-durable sidecar 执行。

sidecar 的耐久保证（每步 checkpoint、进程死了重开续跑）只在它持有存储和任务时
成立，所以这一层只做两件事：把派单密件递进去，把标准事件听回来。幂等键是
`commission_id@attempt`——重投同一次（网络重试、宿主重启）返回同一单；初审打回
或请示获答会让 attempt 递增，重做拿新单并真的再拉起执行体。

内部契约见 docs/PI_DURABLE_MIGRATION.md §4.3。事件的五种类型与协议 §22.2
一一对应，调度核心因此不需要知道这一单跑在子进程、远端还是 sidecar 里。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, AsyncIterator

from .base import (ARTIFACT, ERROR, LOG, PROGRESS, TOKEN, AdapterError,
                   BaseWorkerAdapter, artifact_event, make_event)
from .registry import register_adapter

DEFAULT_TIMEOUT = 600.0

# 仓库根：花名册里的 executable/args 是相对它写的，而 sidecar 的子进程跑在
# 沙箱工作目录里，所以要在这里就改成绝对路径。
REPO_ROOT = Path(__file__).resolve().parents[3]

# 与 agent-runtime/src/routes.ts 必须逐项一致；check_protocol.py 会比对两边，
# 改名只在一边发生会直接让门禁报警，而不是运行时静默 404。
SIDECAR_ROUTES = (
    "/healthz",
    "/v1/stats",
    "/v1/runs",
    "/v1/runs/{id}",
    "/v1/runs/{id}/cancel",
    "/v1/events",
    "/v1/approvals",
    "/v1/lookup",
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
        """sidecar 的当前地址。

        优先用注入的 `endpoint_provider`：内核被硬杀后监督器在**新端口**重起，
        花名册里那份 endpoint 是绑定时写死的，之后每一次派单都会连到死端口
        （实测表现：内核明明拉回来了，重试却报 "All connection attempts
        failed"）。提供者返回 None 时回落，仍然抛 AdapterError——不给假端点。
        """
        provider = self.config.get("endpoint_provider")
        if callable(provider):
            try:
                current = provider()
            except Exception:
                current = None
            if isinstance(current, str) and current.strip():
                return current.strip().rstrip("/")
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

    def _workspace(self, commission_id: str) -> str:
        """这一单可以写入的目录，绝对路径。

        与 `cli_subproc` 的 shim 同一套派生（`workspace_root` + commission_id +
        时间戳）：成果必须落在每单独立的树里，否则两个并发单会互相覆盖，而
        sidecar 的 `write_file` 只认这一个目录。没配 `workspace_root` 就返回空
        字符串——sidecar 那时不允许写文件，也不会去猜一个目录。
        """
        root = self.config.get("workspace_root")
        if not (isinstance(root, str) and root.strip()):
            return ""
        safe = "".join(ch for ch in commission_id if ch.isalnum() or ch in "-_")[:40] or "ad-hoc"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        workspace = (Path(root).resolve() / f"{safe}-{stamp}").resolve()
        # 与 shim 的 main() 一致：目录必须先存在。sidecar 的 write_file 用 realpath
        # 定沙箱基目录，基目录不存在时它只会报错，不会替宿主猜一个位置。
        workspace.mkdir(parents=True, exist_ok=True)
        return str(workspace)

    def _command(self) -> tuple[list[str], dict[str, str]]:
        """这一单要接管的本机执行体命令行，来自花名册的 executable/args/env。

        配了就是"durable 当外壳、真机 CLI 干活"：sidecar 用 env.exec 拉起它，
        解析它 stdout 上的五行事件协议。env 块必须一起给——少了它，shim 拿着
        空配置启动，几秒就退，看起来像"执行体什么都没产出"，其实是没配过。
        没配 executable 就退回 durable 自己的模型路径。

        命令会被改写成绝对路径：sidecar 的子进程 cwd 是本单的沙箱工作目录，
        花名册里相对仓库根的 `scripts/worker_shims/x.py` 在那里并不存在。
        """
        executable = self.config.get("executable")
        if not (isinstance(executable, str) and executable.strip()):
            return [], {}
        args = self.config.get("args") or []
        if not isinstance(args, list):
            args = []
        env = self.config.get("env") or {}
        if not isinstance(env, dict):
            env = {}
        command = [str(executable).strip()] + [str(item) for item in args]
        resolved = [self._absolute(item) for item in command]
        # 显式失败，不靠猜：把 python 解析成 .venv 的解释器，把 node 解析成 PATH 上的
        # 那个。sidecar 的子进程不继承宿主 PATH 以外的约定，而 Windows 上裸
        # `python` 可能落在与项目不兼容的 Anaconda（numpy 2.x 会直接 ImportError）。
        if resolved[0] == "python":
            venv_python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
            if venv_python.is_file():
                resolved[0] = str(venv_python)
        elif resolved[0] == "node":
            found = shutil.which("node")
            if found:
                resolved[0] = found
        return resolved, {str(k): str(v) for k, v in env.items()}

    @staticmethod
    def _absolute(item: str) -> str:
        """把相对仓库根的路径元素变成绝对路径；不是路径的原样返回。"""
        candidate = Path(item)
        if candidate.is_absolute() or not candidate.parts:
            return item
        resolved = (REPO_ROOT / candidate).resolve()
        return str(resolved) if resolved.exists() else item

    async def _endpoint_wait(self, timeout: float = 20.0) -> str:
        """取当前端点；内核正在重起时等它一会儿，而不是立刻判死。

        硬杀后 `_watch` 要重新拉起 sidecar、拿新端口、过健康检查，这中间有
        几秒窗口；被硬杀留下的锁还要等满 `stale`（10s）才会被认过期。请示获答
        会立刻重派，如果这一刻直接抛错，老板会看到"执行体没起来"，而其实内核
        再过一会儿就绪。轮询有界（默认 20s，与锁重试预算同量级）：超时仍抛
        AdapterError，绝不无限等，也绝不返回假端点。
        """
        provider = self.config.get("endpoint_provider")
        if callable(provider):
            deadline = time.monotonic() + max(0.0, float(timeout))
            while True:
                try:
                    current = provider()
                except Exception:
                    current = None
                if isinstance(current, str) and current.strip():
                    return current.strip().rstrip("/")
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(0.25)
        return self._endpoint()

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        command, worker_env = self._command()
        # 第几次做这一单（初审打回后重做会 +1）。sidecar 的幂等键必须带上它：
        # 只按 commission_id 幂等时，重做会拿回上一版已终态的任务，执行体不被
        # 再拉起，旧成果被原样再交一次——实测两个请求拿到同一个 taskId、子进程
        # 只跑过一次。重投同一次（网络重试、崩溃后重启）attempt 不变，仍返回同一单。
        attempt = briefing_packet.get("attempt")
        telemetry = briefing_packet.get("telemetry") or {}
        return {
            "commission_id": str(commission_id),
            "agent_id": str(self.config.get("id") or ""),
            "endpoint": await self._endpoint_wait(),
            "headers": self._headers(),
            "workspace": self._workspace(str(commission_id)),
            "command": command,
            "worker_env": worker_env,
            "cost_tier": str(self.config.get("cost_tier") or ""),
            "attempt": int(attempt) if isinstance(attempt, int) and attempt >= 0 else 0,
            # 与 cli_subproc 同源：Worker 自己的模型调用走观测网关，token 才记得
            # 起来。sidecar 进程环境里没有这个地址，只能由密件带进来。
            "telemetry_proxy": str((telemetry or {}).get("proxy_url") or ""),
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
                          "packet": packet,
                          "workspace": str(context.get("workspace") or ""),
                          "command": [str(item) for item in (context.get("command") or [])],
                          "worker_env": dict(context.get("worker_env") or {}),
                          "cost_tier": str(context.get("cost_tier") or ""),
                          "attempt": int(context.get("attempt") or 0),
                          "telemetry_proxy": str(context.get("telemetry_proxy") or "")},
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

    def lookup(self, commission_id: str, attempt: int = 0) -> dict[str, Any] | None:
        """问内核这一单现在是什么状态；查不到返回 None。

        只读，不建单。重启对账用它判断"内核已经把活干完了，还是丢了这一单"。
        同步实现：调用方 `ExecutiveService.recover_interrupted` 是启动期的同步
        收口，一次 loopback 短查询不值得为它把整条收口改成异步。
        `attempt` 与幂等键同源（见 prepare_context）。
        """
        import httpx

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"{self._endpoint()}/v1/lookup", headers=self._headers(),
                    params={"commission_id": str(commission_id), "attempt": int(attempt)})
                if response.status_code >= 400:
                    return None
                found = response.json()
                if not found.get("found"):
                    return None
                task_id = int(found.get("task_id") or 0)
                if task_id <= 0:
                    return None
                view = client.get(f"{self._endpoint()}/v1/runs/{task_id}",
                                  headers=self._headers())
                if view.status_code >= 400:
                    return None
                return view.json()
        except Exception:
            return None

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
            fields = {"step_idx": fallback_step, "text": str(event.get("text") or "")[:600]}
            if kind == ERROR and event.get("kind") in ("clarification", "approval"):
                fields["blocked_kind"] = event["kind"]
            return make_event(kind, **fields)
        return None


__all__ = ["DurableAdapter"]
