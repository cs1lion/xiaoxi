"""Operation outbox: receipt survives crashes; projection is idempotent."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.loop import AgentLoop, Tool  # noqa: E402
from server.assistant.runtime import RuntimeStore  # noqa: E402
from server.assistant.service import AssistantService  # noqa: E402
from server.assistant.store import AssistantStore  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def model_script(actions):
    remaining = list(actions)

    async def model(_payload):
        if not remaining:
            return {"action": "final", "text": "done"}
        return remaining.pop(0)

    return model


def mutation_tool(handler) -> Tool:
    return Tool("task.create", "create", {}, (), handler, True, idempotency_key="request_id")


def run_with_service(tmp: Path):
    service = AssistantService(tmp / "service", llm=None)
    thread = service.threads.create("回执测试")
    calls: list[str] = []

    def handler(request_id=None):
        calls.append(request_id or "")
        return {"id": "task-1", "request_id": request_id}

    loop = AgentLoop(model_script([
        {"action": "tool_call", "tool": "task.create", "arguments": {}},
    ]), [mutation_tool(handler)], service.runtime)
    waiting = asyncio.run(loop.start("建个任务", {"companion_thread_id": thread["id"]}))
    result = asyncio.run(loop.resume(
        waiting["run_id"], waiting["confirmation"]["id"], True,
        context={"companion_thread_id": thread["id"]}))
    return service, thread, calls, result


def main() -> int:
    print("operation outbox")
    with tempfile.TemporaryDirectory() as tmp:
        service = None
        try:
            service, thread, calls, result = run_with_service(Path(tmp))
            check("mutation ran once", len(calls) == 1, str(calls))
            check("server injected the operation id as request_id",
                  calls[0] and calls[0].startswith(result["run_id"] + ":"), str(calls))
            pending = service.runtime.pending_operations(run_id=result["run_id"])
            check("successful mutation is pending projection", len(pending) == 1, str(pending))
            if pending:
                check("outbox stores the receipt and target thread",
                      json.loads(pending[0]["receipt"])["status"] == "succeeded"
                      and pending[0]["thread_id"] == thread["id"], str(pending[0]))
            warnings = service._flush_outbox(run_id=result["run_id"])
            check("projection has no warnings", warnings == [], str(warnings))
            thread_after = service.threads.get(thread["id"])
            receipts = [event for event in thread_after["events"]
                        if event["kind"] == "tool_receipt"]
            check("projection writes exactly one matter receipt", len(receipts) == 1,
                  str(thread_after["events"]))
            check("projection drains the outbox",
                  service.runtime.pending_operations(run_id=result["run_id"]) == [])
            service._flush_outbox(run_id=result["run_id"])
            again = service.threads.get(thread["id"])
            check("second projection is idempotent",
                  len([event for event in again["events"]
                       if event["kind"] == "tool_receipt"]) == 1)
        finally:
            if service is not None:
                service.close()

        # Startup projection picks up a receipt committed before the previous
        # service was closed, without re-running the tool.
        restarted = AssistantService(Path(tmp) / "service", llm=None)
        try:
            restarted_thread = restarted.threads.get(thread["id"])
            check("startup projection is idempotent",
                  len([event for event in restarted_thread["events"]
                       if event["kind"] == "tool_receipt"]) == 1)
        finally:
            restarted.close()

    with tempfile.TemporaryDirectory() as tmp:
        # Store-level mutations accept the same operation id as the loop.
        store = AssistantStore(Path(tmp) / "assistant.db")
        task = store.create_task("幂等任务")
        first = store.update_task(task["id"], {"status": "done"}, request_id="op-update")
        second = store.update_task(task["id"], {"status": "done"}, request_id="op-update")
        check("task.update replay returns the first result",
              first["version"] == second["version"] and second["status"] == "done",
              f"{first} / {second}")
        reminder = store.create_reminder("幂等提醒", 10**12)
        cancelled = store.cancel_reminder(reminder["id"], request_id="op-cancel")
        replayed = store.cancel_reminder(reminder["id"], request_id="op-cancel")
        check("reminder.cancel replay is idempotent",
              cancelled["version"] == replayed["version"] == 2, f"{cancelled} / {replayed}")
        snoozed = store.create_reminder("延后提醒", 10**12)
        first_snooze = store.snooze_reminder(snoozed["id"], 10**12 + 60,
                                             request_id="op-snooze")
        second_snooze = store.snooze_reminder(snoozed["id"], 10**12 + 60,
                                              request_id="op-snooze")
        check("reminder.snooze replay is idempotent",
              first_snooze["version"] == second_snooze["version"], f"{first_snooze} / {second_snooze}")
        store.close()

    with tempfile.TemporaryDirectory() as tmp:
        runtime = RuntimeStore(Path(tmp) / "agent_runtime.db")
        calls: list[str] = []

        def crashing(request_id=None):
            calls.append(request_id or "")
            raise asyncio.CancelledError()

        loop = AgentLoop(model_script([
            {"action": "tool_call", "tool": "task.create", "arguments": {}},
        ]), [mutation_tool(crashing)], runtime)
        waiting = asyncio.run(loop.start("建个任务"))
        try:
            asyncio.run(loop.resume(waiting["run_id"], waiting["confirmation"]["id"], True))
        except asyncio.CancelledError:
            pass
        else:
            check("crashing mutation cancels the run", False)
        # The cancelled run was saved as interrupted by _guarded; recover marks
        # its started operation unknown and writes an audit event.
        runtime.recover_interrupted()
        rows = runtime.db.execute(
            "SELECT operation_id,status,delivered_at FROM agent_outbox").fetchall()
        check("started operation becomes unknown, not succeeded",
              len(rows) == 1 and rows[0]["status"] == "unknown"
              and rows[0]["delivered_at"] is None, str([dict(row) for row in rows]))
        run_row = runtime.db.execute("SELECT id,status FROM agent_runs").fetchone()
        events = runtime.events(run_row["id"])
        check("unknown operation leaves an audit event",
              any(event["kind"] == "operation_unknown" for event in events), str(events))
        loop2 = AgentLoop(model_script([]), [mutation_tool(crashing)], runtime)
        try:
            asyncio.run(loop2.resume_plan(run_row["id"]))
        except ValueError as exc:
            check("unknown operation blocks blind resume", "unknown" in str(exc), str(exc))
        else:
            check("unknown operation blocks blind resume", False)
        check("handler was not replayed", len(calls) == 1, str(calls))
        runtime.close()

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
