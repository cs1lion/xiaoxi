"""Live C3 probe in isolated temp databases.

This starts a local host, a durable sidecar, a temp AssistantService, and drives
one or more real scribe commissions through the C3 chain. It never opens the
project's real data/ directory.

    python scripts/probe_c3_live.py --runs 1
    python scripts/probe_c3_live.py --runs 5 --keep

Each run:
  orchestrator prompt -> dispatch_task -> scribe run -> blocked/answer or
  ready_to_report -> review -> propose/authorize -> accept (or report blockers)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import tempfile
import threading
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from server.assistant.durable_api import (  # noqa: E402
    _question_id, create_durable_router, set_host_token,
)
from server.assistant.durable_leases import LeaseStore  # noqa: E402
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402
from server.assistant.service import AssistantService  # noqa: E402
from server.llm import llm  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def wait_task(base: str, token: str, task_id: int, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    headers = {"authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        while time.monotonic() < deadline:
            response = await client.get(f"{base}/v1/runs/{task_id}", headers=headers)
            if response.status_code == 200:
                view = response.json()
                if view.get("status") == "terminal":
                    return view
            await asyncio.sleep(1.0)
    raise TimeoutError(f"orchestrator task {task_id} did not settle")


async def wait_commission(executive, commission_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        commission = executive.ledger.get(commission_id)
        if commission["status"] in ("ready_to_report", "blocked", "done"):
            return commission
        await asyncio.sleep(1.0)
    return executive.ledger.get(commission_id)


async def post(host: str, token: str, path: str, body: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{host}{path}",
                                     headers={"authorization": f"Bearer {token}"}, json=body)
        return {"status": response.status_code, "body": response.json()}


async def auto_confirm(host: str, token: str, lease_id: str, session_id: str,
                       stop: asyncio.Event) -> None:
    """Act as the boss for this probe: approve pending per-task confirmations."""
    headers = {"authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=10.0) as client:
        while not stop.is_set():
            try:
                response = await client.get(
                    f"{host}/internal/durable/confirmations",
                    headers=headers,
                    params={"lease_id": lease_id, "session_id": session_id},
                )
                if response.status_code == 200:
                    for item in response.json().get("items") or []:
                        await client.post(f"{host}/internal/durable/confirm",
                                          headers=headers,
                                          json={"confirmation_id": item["confirmation_id"],
                                                "approved": True})
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass


async def run_one(args, ordinal: int, host: str, host_token: str,
                  supervisor: DurableSupervisor, assistant: AssistantService,
                  leases: LeaseStore) -> dict:
    session = f"c3-live-{os.getpid()}-{ordinal}"
    default_prompt = (
        "请把这件事派给 dsh：在 out 目录创建 hello.txt，内容写 hello dsh。"
        "派单后跟踪它：如果它请示，就用 answer_blocked 回答；"
        "如果它 ready_to_report，就用 review_delivery 审阅，再 propose_accept。"
        "不要自己写文件，不要接受交付。"
    ) if args.worker == "dsh" else (
        "请把这件事派给 scribe：写一份三行短诗到 out/poem.md，然后交差。"
        "派单后跟踪它：如果它请示，就用 answer_blocked 回答；"
        "如果它 ready_to_report，就用 review_delivery 审阅，再 propose_accept。"
        "不要自己写文件，不要接受交付。"
    )
    prompt = args.prompt or default_prompt
    lease = leases.issue(session, prompt, "confirm" if args.worker == "dsh" else "auto",
                         allowed_agents=(args.worker,))
    payload = {
        "commission_id": f"orchestrator:{session}",
        "agent_id": "orchestrator",
        "profile": "orchestrator",
        "lease_id": lease["lease_id"],
        "session_id": session,
        "packet": {"objective": prompt, "prompt": prompt},
    }
    endpoint = supervisor.current_endpoint()
    token = supervisor.token
    async with httpx.AsyncClient(timeout=30.0) as client:
        created = await client.post(f"{endpoint}/v1/runs",
                                    headers={"authorization": f"Bearer {token}"}, json=payload)
        created.raise_for_status()
        task_id = int(created.json()["task_id"])
    stop = asyncio.Event()
    confirmer = asyncio.create_task(
        auto_confirm(host, host_token, lease["lease_id"], session, stop))
    try:
        view = await wait_task(endpoint, token, task_id, args.timeout)
    finally:
        stop.set()
        await confirmer
    lease_obj = leases.get(lease["lease_id"])
    if not lease_obj.commissions:
        return {"ordinal": ordinal, "ok": False, "reason": "no_commission_dispatched",
                "orchestrator": view}
    commission_id = sorted(lease_obj.commissions)[0]
    commission = await wait_commission(assistant.executive, commission_id, args.timeout)
    result = {"ordinal": ordinal, "commission_id": commission_id,
              "status": commission.get("status"), "orchestrator": view}
    if commission.get("status") == "blocked":
        result["blocked_question"] = commission.get("blocked_question")
        result["blocked_kind"] = commission.get("blocked_kind")
        # A clarification can be answered by the host for this probe. An
        # approval is deliberately left blocked; the boss path is not exercised.
        if commission.get("blocked_kind") == "clarification":
            queued = await post(host, host_token, "/internal/durable/answer", {
                "lease_id": lease["lease_id"], "session_id": session,
                "commission_id": commission_id,
                "blocked_question_id": _question_id(commission),
                "answer": "放在 out 目录，按最短三行交付。",
            })
            result["answer"] = queued
            commission = await wait_commission(assistant.executive, commission_id, args.timeout)
            result["status"] = commission.get("status")
    if commission.get("status") == "ready_to_report":
        reviewed = await post(host, host_token, "/internal/durable/review", {
            "lease_id": lease["lease_id"], "session_id": session,
            "commission_id": commission_id,
        })
        result["review"] = reviewed
        if reviewed["status"] == 200 and reviewed["body"].get("can_accept"):
            action = args.action
            proposed = await post(host, host_token, "/internal/durable/propose", {
                "lease_id": lease["lease_id"], "session_id": session,
                "commission_id": commission_id, "action": action,
                "suggested_feedback": "请按更短的三行重做" if action == "reject" else "",
            })
            result["proposal"] = proposed
            if proposed["status"] == 200:
                authorized = await post(host, host_token, "/internal/durable/authorize", {
                    "proposal_id": proposed["body"]["proposal_id"], "approved": True,
                })
                result["authorization"] = authorized
                if authorized["status"] == 200:
                    if action == "accept":
                        accepted = await post(host, host_token, "/internal/durable/accept", {
                            "proposal_id": proposed["body"]["proposal_id"],
                            "authorization_id": authorized["body"]["authorization_id"],
                        })
                        result["accept"] = accepted
                        result["ok"] = accepted["status"] == 200
                    else:
                        rejected = await post(host, host_token, "/internal/durable/reject", {
                            "proposal_id": proposed["body"]["proposal_id"],
                            "authorization_id": authorized["body"]["authorization_id"],
                            "feedback": "请按更短的三行重做",
                        })
                        result["reject"] = rejected
                        result["ok"] = rejected["status"] == 200
                        if rejected["status"] == 200:
                            await assistant.executive.drive(commission_id)
                            redone = await wait_commission(assistant.executive, commission_id, args.timeout)
                            result["redrive_status"] = redone.get("status")
                            result["ok"] = redone.get("status") in ("ready_to_report", "done")
    result.setdefault("ok", commission.get("status") == "done")
    return result


async def main_async(args) -> int:
    root = Path(tempfile.mkdtemp(prefix="xiaoxi-c3-live-"))
    assistant_dir = root / "assistant"
    durable_dir = root / "durable"
    workspace = root / "commissions"
    assistant_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    assistant = AssistantService(assistant_dir, llm)
    leases = LeaseStore(assistant.executive.ledger)
    host_token = secrets.token_urlsafe(24)
    set_host_token(host_token)
    port = free_port()
    host = f"http://127.0.0.1:{port}"
    app = FastAPI()
    supervisor_box: dict[str, DurableSupervisor] = {}

    def runtime_provider():
        supervisor = supervisor_box.get("supervisor")
        return supervisor.config() if supervisor is not None else None

    app.include_router(create_durable_router(assistant, None, leases, runtime_provider))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    if not server.started:
        raise RuntimeError("host did not start")

    extra_env = {
        "XIAOXI_HOST_URL": host,
        "XIAOXI_HOST_TOKEN": host_token,
        "XIAOXI_SKILLS_DIR": str(ROOT / "config" / "skills"),
        "XIAOXI_MCP_CONFIG": str(ROOT / "config" / "mcp.json"),
        "XIAOXI_CONTEXT_WINDOW": "32768",
        "XIAOXI_MAX_TOKENS": "8000",
        "XIAOXI_APPROVAL_TOOLS": "run_worker",
    }
    supervisor = DurableSupervisor(directory=durable_dir, startup_timeout=30.0,
                                   auto_restart=False, extra_env=extra_env)
    supervisor_box["supervisor"] = supervisor
    results = []
    try:
        endpoint = await supervisor.start()
        assistant.executive.bind_durable(
            {"endpoint": endpoint, "token": supervisor.token,
             "workspace_root": str(workspace)},
            apply_to=[args.worker], endpoint_provider=supervisor.current_endpoint)
        for index in range(1, args.runs + 1):
            result = await run_one(args, index, host, host_token, supervisor, assistant, leases)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    finally:
        await supervisor.stop()
        server.should_exit = True
        thread.join(timeout=10)
        set_host_token(None)
        assistant.close()
    print(json.dumps({"root": str(root), "results": results}, ensure_ascii=False, indent=2))
    if not args.keep:
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    return 0 if all(item.get("ok") for item in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--prompt", default="", help="override the orchestrator prompt")
    parser.add_argument("--action", choices=("accept", "reject"), default="accept")
    parser.add_argument("--worker", choices=("scribe", "dsh"), default="scribe")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
