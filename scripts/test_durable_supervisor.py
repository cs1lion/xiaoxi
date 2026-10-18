"""durable sidecar 监督器自检：起得来、探得活、收得干净。

    python scripts/test_durable_supervisor.py

需要用本机 Node 和 agent-runtime 的依赖；缺任何一样就 SKIP，不算失败
（与 test-ui-nav-browser.mjs 同一约定）。零外部网络：只连 127.0.0.1。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

import httpx  # noqa: E402

from server.assistant.adapters.base import AdapterError  # noqa: E402
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def runnable() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "本机没有 node"
    if not (ROOT / "agent-runtime" / "node_modules" / "@earendil-works" / "pi-durable").exists():
        return False, "agent-runtime 依赖未安装（npm install --ignore-scripts）"
    return True, ""


async def test_lifecycle() -> None:
    directory = tempfile.mkdtemp(prefix="durable-supervisor-")
    supervisor = DurableSupervisor(directory=directory, startup_timeout=30.0)
    try:
        endpoint = await supervisor.start()
        check("起得来并回报端口", endpoint.startswith("http://127.0.0.1:"), endpoint)
        async with httpx.AsyncClient(timeout=10.0) as client:
            health = await client.get(f"{endpoint}/healthz")
            check("健康检查通过", health.status_code == 200, str(health.status_code))

            headers = {"authorization": f"Bearer {supervisor.token}"}
            created = await client.post(f"{endpoint}/v1/runs", headers=headers, json={
                "commission_id": "c_supervisor", "agent_id": "w1", "packet": {"objective": "自检"},
            })
            check("接单", created.status_code == 200, str(created.status_code))
            task_id = created.json()["task_id"]

            events: list[dict] = []
            async with client.stream("GET", f"{endpoint}/v1/events",
                                     params={"task_id": task_id}, headers=headers) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
        check("事件流以 artifact 收尾",
              bool(events) and events[-1]["type"] == "artifact", str(events[-1:])[:200])
        check("进度事件都在", sum(1 for e in events if e["type"] == "progress") == 3)
    finally:
        await supervisor.stop()
    check("收尸后端点清空", supervisor.endpoint is None and not supervisor.available)


async def test_patrol() -> None:
    directory = tempfile.mkdtemp(prefix="durable-supervisor-patrol-")
    supervisor = DurableSupervisor(directory=directory, startup_timeout=30.0)
    try:
        first = await supervisor.start()
        # 模拟"进程还在但不应答"：直接把当前内核收掉，让巡检重起一个。
        await supervisor._teardown()
        check("收掉后健康检查为假", not await supervisor.healthy())
        await supervisor.start_patrol(1.0)
        deadline = asyncio.get_running_loop().time() + 40.0
        while asyncio.get_running_loop().time() < deadline:
            if await supervisor.healthy():
                break
            await asyncio.sleep(0.5)
        check("巡检把内核拉回来了", await supervisor.healthy(), supervisor.reason)
        check("端口换了新内核", supervisor.endpoint is not None and supervisor.endpoint != first)
    finally:
        await supervisor.stop()
    check("巡检任务已清掉", supervisor._patrol is None)


async def test_unavailable() -> None:
    directory = tempfile.mkdtemp(prefix="durable-supervisor-bad-")
    supervisor = DurableSupervisor(directory=directory, entry=ROOT / "agent-runtime" / "nope.ts",
                                   startup_timeout=5.0)
    try:
        await supervisor.start()
        check("入口不存在时不假装起来", False, "start() 没有抛错")
    except AdapterError as exc:
        check("入口不存在时明说不可用", "执行内核不可用" in str(exc), str(exc)[:120])
    finally:
        await supervisor.stop()
    check("失败后不留进程", supervisor._process is None)


def main() -> int:
    ok, reason = runnable()
    if not ok:
        print(f"SKIP durable supervisor: {reason}")
        return 0
    print("[durable supervisor]")
    asyncio.run(test_lifecycle())
    asyncio.run(test_patrol())
    asyncio.run(test_unavailable())
    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
