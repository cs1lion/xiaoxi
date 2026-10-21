"""Real-process crash drill for the AgentLoop outbox (isolated temp data only).

    python scripts/probe_outbox_crash.py

Two child processes hard-exit at the two dangerous windows:

1. after the tool side effect, before the receipt/outbox result is committed;
2. after the receipt/outbox result is committed, before the matter projection.

The parent reopens AssistantService (the same startup path a real restart
takes) and checks that case 1 becomes `unknown` and blocks blind resume, while
case 2 is projected exactly once with no duplicate side effect.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def _model():
    calls = 0

    async def model(_payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"action": "tool_call", "tool": "task.create", "arguments": {}}
        return {"action": "final", "text": "done"}

    return model


def _tool(marker: Path, mode: str):
    from server.assistant.loop import Tool

    def handler(request_id=None):
        marker.write_text(f"{request_id}\n", encoding="utf-8")
        if mode == "before_receipt":
            os._exit(17)
        return {"id": "task-1", "request_id": request_id}

    return Tool("task.create", "create", {}, (), handler, True, idempotency_key="request_id")


def child(mode: str, root: Path, thread_id: str) -> int:
    from server.assistant.loop import AgentLoop
    from server.assistant.service import AssistantService

    service = AssistantService(root, llm=None)
    marker = root / "side-effect.txt"
    loop = AgentLoop(_model(), [_tool(marker, mode)], service.runtime)
    waiting = asyncio.run(loop.start("建个任务", {"companion_thread_id": thread_id}))
    try:
        result = asyncio.run(loop.resume(
            waiting["run_id"], waiting["confirmation"]["id"], True,
            context={"companion_thread_id": thread_id}))
    except SystemExit:
        raise
    # For before_projection the result is durable but no _with_receipts ran.
    (root / "child-result.json").write_text(
        json.dumps({"run_id": result["run_id"], "status": result["status"]}),
        encoding="utf-8")
    os._exit(18)


def run_child(mode: str, root: Path, thread_id: str) -> int:
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--child", mode,
         "--root", str(root), "--thread", thread_id],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if proc.returncode not in (17, 18):
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def parent() -> int:
    from server.assistant.loop import AgentLoop
    from server.assistant.service import AssistantService

    print("outbox real crash drill")
    with tempfile.TemporaryDirectory(prefix="outbox-crash-") as raw:
        root = Path(raw)
        bootstrap = AssistantService(root, llm=None)
        thread = bootstrap.threads.create("崩溃演练")
        bootstrap.close()

        # Case 1: side effect happened, receipt never committed.
        case1_root = root
        code = run_child("before_receipt", case1_root, thread["id"])
        check("child exits at the before-receipt window", code == 17, str(code))
        check("side effect actually happened", (case1_root / "side-effect.txt").is_file())

        restarted = AssistantService(case1_root, llm=None)
        try:
            row = restarted.runtime.db.execute(
                "SELECT operation_id,status,delivered_at FROM agent_outbox").fetchone()
            check("started operation becomes unknown",
                  row is not None and row["status"] == "unknown"
                  and row["delivered_at"] is None, str(dict(row) if row else None))
            run_id = restarted.runtime.db.execute("SELECT id,status FROM agent_runs").fetchone()["id"]
            check("run is interrupted, not replayed",
                  restarted.runtime.get(run_id)["status"] == "interrupted")
            check("unknown operation leaves an audit event",
                  any(event["kind"] == "operation_unknown"
                      for event in restarted.runtime.events(run_id)))
            try:
                asyncio.run(AgentLoop(_model(), [], restarted.runtime).resume_plan(run_id))
            except ValueError as exc:
                check("blind resume is blocked", "unknown" in str(exc), str(exc))
            else:
                check("blind resume is blocked", False)
            check("thread has no forged receipt",
                  not any(event["kind"] == "tool_receipt"
                          for event in restarted.threads.get(thread["id"])["events"]))
        finally:
            restarted.close()

        # Case 2: receipt committed, projection never ran.
        case2_root = root / "case2"
        case2_root.mkdir()
        bootstrap = AssistantService(case2_root, llm=None)
        thread2 = bootstrap.threads.create("投影窗口")
        bootstrap.close()
        code = run_child("before_projection", case2_root, thread2["id"])
        check("child exits at the before-projection window", code == 18, str(code))
        child_result = json.loads((case2_root / "child-result.json").read_text(encoding="utf-8"))
        check("child completed the run before exiting", child_result["status"] == "completed",
              str(child_result))
        restarted = AssistantService(case2_root, llm=None)
        try:
            receipts = [event for event in restarted.threads.get(thread2["id"])["events"]
                        if event["kind"] == "tool_receipt"]
            check("startup projects exactly one matter receipt", len(receipts) == 1,
                  str(receipts))
            rows = restarted.runtime.db.execute(
                "SELECT delivered_at FROM agent_outbox").fetchall()
            check("outbox is drained after startup projection",
                  rows and all(row["delivered_at"] is not None for row in rows), str(rows))
            check("side effect still happened once", (case2_root / "side-effect.txt").read_text().count("\n") == 1)
            restarted._flush_outbox()
            again = [event for event in restarted.threads.get(thread2["id"])["events"]
                     if event["kind"] == "tool_receipt"]
            check("second startup flush is idempotent", len(again) == 1, str(again))
        finally:
            restarted.close()

    print(f"\n=== {PASSED} passed, {len(FAILED)} failed ===")
    for item in FAILED:
        print(f"  - {item}")
    return 1 if FAILED else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=["before_receipt", "before_projection"])
    parser.add_argument("--root")
    parser.add_argument("--thread")
    args = parser.parse_args()
    if args.child:
        return child(args.child, Path(args.root), args.thread)
    return parent()


if __name__ == "__main__":
    raise SystemExit(main())
