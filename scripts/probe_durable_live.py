"""真实端点端到端实跑：真模型 + 真 sidecar + 现有 dispatcher。

    python scripts/probe_durable_live.py

不是回归：会调用真实模型、有固定预算。用来验证 Phase 2 的链路在真机上成立，
并把 token / 耗时 / 初审结果记下来供 A/B 对照。没有 LLM_API_KEY 时直接退出。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402,F401  (import 即加载 .env)
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

PROMPT = ("素材：1) 深夜写代码的专注；2) 通勤路上的播客；3) 周末收拾房间。"
          "把它们理成三版不超过二十字的标题。做完必须调用 submit_artifact 交差。")

WORKER = {
    "id": "durable-live", "name": "durable 实测工", "adapter": "durable",
    "strengths": ["整理", "标题"], "weaknesses": [], "cost_tier": "cheap",
    "max_concurrent": 1, "eval_commands": ["required_fields", "artifacts_exist"],
}


async def main() -> int:
    if not os.environ.get("LLM_API_KEY"):
        print("SKIP: 没有 LLM_API_KEY")
        return 0
    directory = Path(tempfile.mkdtemp(prefix="durable-live-"))
    supervisor = DurableSupervisor(directory=directory / "sidecar", startup_timeout=40.0)
    await supervisor.start()
    try:
        base = load_roster()
        base["agents"] = [dict(WORKER)]
        service = ExecutiveService(directory / "state", roster=base)
        service.bind_durable(supervisor.config())
        commission = service.open("实测三版标题", PROMPT)
        service.dispatch(commission["id"], intent="整理标题")
        service.start(commission["id"])
        started = time.monotonic()
        result = await service.run_commission(commission["id"])
        elapsed = time.monotonic() - started
        item = result["commission"]
        usage = service.commission_usage(commission["id"])
        print(f"status: {item['status']}")
        print(f"delivery: {(item.get('delivery') or {}).get('summary')}")
        print(f"question: {item.get('blocked_question')}")
        print(f"elapsed: {elapsed:.1f}s")
        print(f"usage: {usage}")
        service.close()
        return 0 if item["status"] == "ready_to_report" else 1
    finally:
        await supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
