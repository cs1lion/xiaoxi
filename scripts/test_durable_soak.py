"""Chaos soak: create several scripted runs, hard-kill the sidecar mid-flight,
let the supervisor restart it, and assert exactly-once progress/artifact.

    python scripts/test_durable_soak.py

No model calls. Skips when node or agent-runtime deps are missing.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

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


def hard_kill(pid: int) -> bool:
    if process_is_windows():
        result = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, text=True)
        return result.returncode == 0
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False


def process_is_windows() -> bool:
    return os.name == "nt"


def runnable() -> tuple[bool, str]:
    if shutil.which("node") is None:
        return False, "本机没有 node"
    if not (ROOT / "agent-runtime" / "node_modules" / "@earendil-works" / "pi-durable").exists():
        return False, "agent-runtime 依赖未安装（npm install --ignore-scripts）"
    return True, ""


async def main() -> int:
    ok, reason = runnable()
    if not ok:
        print(f"SKIP durable soak: {reason}")
        return 0
    directory = tempfile.mkdtemp(prefix="durable-soak-")
    supervisor = DurableSupervisor(
        directory=directory, startup_timeout=40.0, auto_restart=True, max_restarts=3,
        extra_env={
            "XIAOXI_WORKER_MODE": "script",
            "XIAOXI_STEP_DELAY_MS": "80",
        },
    )
    try:
        endpoint = await supervisor.start()
        headers = {"authorization": f"Bearer {supervisor.token}"}
        async with httpx.AsyncClient(timeout=20.0) as client:
            created = []
            for index in range(8):
                response = await client.post(f"{endpoint}/v1/runs", headers=headers, json={
                    "commission_id": f"c_soak_{index}",
                    "agent_id": "w1",
                    "packet": {"objective": f"soak {index}"},
                })
                response.raise_for_status()
                created.append(int(response.json()["task_id"]))
            print(f"[durable soak] created {len(created)} runs; killing sidecar")
            pid = supervisor._process.pid if supervisor._process is not None else None
            check("sidecar 有 pid", bool(pid), str(pid))
            await asyncio.sleep(0.12)
            killed = bool(pid) and hard_kill(int(pid))
            check("硬杀 sidecar", killed)
            deadline = time.monotonic() + 60.0
            new_endpoint = None
            while time.monotonic() < deadline:
                current = supervisor.current_endpoint()
                if current is not None and current != endpoint:
                    new_endpoint = current
                    break
                await asyncio.sleep(0.25)
            check("supervisor 在硬杀后换端口重起", new_endpoint is not None,
                  str(supervisor.reason))
            if new_endpoint is None:
                return 1
            endpoint = new_endpoint
            for task_id in created:
                task_deadline = time.monotonic() + 60.0
                view = None
                while time.monotonic() < task_deadline:
                    response = await client.get(f"{endpoint}/v1/runs/{task_id}", headers=headers)
                    if response.status_code == 200:
                        view = response.json()
                        if view.get("status") == "terminal":
                            break
                    await asyncio.sleep(0.2)
                check(f"task {task_id} 完成", bool(view) and view.get("status") == "terminal",
                      str(view)[:200] if view else "no view")
                if not view:
                    continue
                events = view.get("events") or []
                progress = [event for event in events if event.get("type") == "progress"]
                artifacts = [event for event in events if event.get("type") == "artifact"]
                check(f"task {task_id} 三步不重不漏",
                      len(progress) == 3 and len({event.get("step_idx") for event in progress}) == 3,
                      str(progress))
                check(f"task {task_id} artifact 恰好一条", len(artifacts) == 1,
                      str(artifacts))
    finally:
        await supervisor.stop()
        shutil.rmtree(directory, ignore_errors=True)

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
