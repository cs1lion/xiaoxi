"""App wiring self-check: env override starts the sidecar, switches `scribe`,
and binds the live endpoint provider + workspace root.

    python scripts/test_durable_app_wiring.py

Uses a temporary durable directory and never dispatches a commission. Skips when
Node or agent-runtime dependencies are absent, like the supervisor test.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = ":memory:"
os.environ["XIAOXI_DURABLE_ENABLED"] = "1"
os.environ["XIAOXI_DURABLE_SWITCH_AGENTS"] = "scribe"

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


async def main() -> int:
    ok, reason = runnable()
    if not ok:
        print(f"SKIP durable app wiring: {reason}")
        return 0

    directory = tempfile.mkdtemp(prefix="durable-wiring-")
    os.environ["XIAOXI_DURABLE_DIR"] = directory
    try:
        from server import app as app_module

        print("[durable app wiring]")
        await app_module._start_durable_if_enabled()
        supervisor = app_module._durable_supervisor
        check("sidecar 就绪", supervisor is not None and supervisor.available,
              getattr(supervisor, "reason", "missing"))

        roster = app_module.executive.dispatcher.roster
        scribe = next((item for item in (roster.get("agents") or [])
                       if isinstance(item, dict) and item.get("id") == "scribe"), None)
        check("scribe 存在", isinstance(scribe, dict), str(scribe))
        if isinstance(scribe, dict):
            check("scribe 被切到 durable", scribe.get("adapter") == "durable", str(scribe.get("adapter")))
            check("scribe 有写盘根", bool(scribe.get("workspace_root")), str(scribe.get("workspace_root")))
            check("scribe 有动态端点", callable(scribe.get("endpoint_provider")),
                  str(scribe.get("endpoint_provider")))

        if supervisor is not None:
            live = supervisor.current_endpoint()
            check("当前端点可查", isinstance(live, str) and live.startswith("http://127.0.0.1:"),
                  str(live))
            await supervisor.stop()
            check("停止后端点清空", supervisor.current_endpoint() is None, str(supervisor.current_endpoint()))
    finally:
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
