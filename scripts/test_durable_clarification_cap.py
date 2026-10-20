"""Scripted blocked worker: adapter error -> blocked_kind=clarification ->
answer cap. No real model involved.

    python scripts/test_durable_clarification_cap.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.adapters.base import ERROR, BaseWorkerAdapter, make_event  # noqa: E402
from server.assistant.adapters.registry import register_adapter, unregister_adapter  # noqa: E402
from server.assistant.durable_api import (  # noqa: E402
    _question_id, create_durable_router, set_host_token,
)
from server.assistant.durable_leases import LeaseStore  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


class BlockingAdapter(BaseWorkerAdapter):
    adapter_type = "c2_block"

    async def prepare_context(self, commission_id, briefing_packet):
        return {"commission_id": commission_id}

    async def execute(self, task_spec):
        yield make_event(ERROR, step_idx=0, text="请补充素材后再继续",
                         blocked_kind="clarification")

    async def cancel(self):
        return None


ROSTER = {
    "selection": {"max_concurrent_default": 2},
    "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2,
                 "spin_guard": {"max_identical": 3}},
    "agents": [
        {"id": "scribe", "name": "笔录官", "adapter": "c2_block", "summary": "文档",
         "strengths": ["写作"], "cost_tier": "cheap", "max_concurrent": 10},
    ],
}


def main() -> int:
    register_adapter("c2_block")(BlockingAdapter)
    directory = tempfile.mkdtemp(prefix="durable-cap-")
    executive = ExecutiveService(directory, roster=ROSTER)
    assistant = SimpleNamespace(
        executive=executive,
        _search_notes=lambda query: {"status": "available", "items": []},
        _read_note=lambda note_id, version: {"body": ""},
        _read_thread=lambda thread_id, query="": {"id": thread_id},
    )
    leases = LeaseStore(executive.ledger)
    app = FastAPI()
    app.include_router(create_durable_router(assistant, SimpleNamespace(enabled=False), leases))
    set_host_token("test-token")
    headers = {"authorization": "Bearer test-token"}
    try:
        with TestClient(app) as client:
            print("[durable clarification cap: scripted worker]")
            lease = client.post("/internal/durable/lease", headers=headers, json={
                "session_id": "s_cap", "user_intent": "测上限", "mode": "auto",
            }).json()
            commission = executive.open("脚本 blocked", "每次都要澄清")
            executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
            executive.dispatcher.start(commission["id"])
            asyncio.run(executive.run_commission(commission["id"]))
            state = executive.ledger.get(commission["id"])
            check("脚本 Worker 产生 blocked",
                  state["status"] == "blocked" and state["blocked_kind"] == "clarification",
                  str(state)[:300])
            leases.get(lease["lease_id"]).commissions.add(commission["id"])

            statuses = []
            for index in range(3):
                state = executive.ledger.get(commission["id"])
                response = client.post("/internal/durable/answer", headers=headers, json={
                    "lease_id": lease["lease_id"], "session_id": "s_cap",
                    "commission_id": commission["id"],
                    "blocked_question_id": _question_id(state),
                    "answer": f"第 {index + 1} 次补充",
                })
                statuses.append(response.status_code)
                # The answer route also schedules a background drive. Let it or
                # this call block the order again; whichever claims first wins.
                try:
                    asyncio.run(executive.run_commission(commission["id"]))
                except ValueError:
                    pass
                if executive.ledger.get(commission["id"])["status"] != "blocked":
                    asyncio.run(executive.run_commission(commission["id"]))
            check("前三次澄清可答", statuses == [200, 200, 200], str(statuses))
            state = executive.ledger.get(commission["id"])
            fourth = client.post("/internal/durable/answer", headers=headers, json={
                "lease_id": lease["lease_id"], "session_id": "s_cap",
                "commission_id": commission["id"],
                "blocked_question_id": _question_id(state),
                "answer": "第 4 次补充",
            })
            check("第四次澄清 429", fourth.status_code == 429, str(fourth.status_code))
    finally:
        unregister_adapter("c2_block")
        set_host_token(None)

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
