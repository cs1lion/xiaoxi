"""Deterministic proof that the DSH shim speaks PROTOCOL §22's five events.

    .\\.venv\\Scripts\\python.exe scripts\\test_dsh_worker_shim.py

No network, no model, no DSH install: fake launchers emit DSH's `--json`
projection and write real files. The shim is driven through the real
`cli_subproc` adapter, so this is the mapping the dispatcher consumes.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.adapters.base import EVENT_TYPES  # noqa: E402
from server.assistant.adapters.cli_subproc import CliSubprocessAdapter  # noqa: E402

SHIM = ROOT / "scripts" / "worker_shims" / "dsh_worker.py"
PY = sys.executable

GOOD_DSH = """
import json, os, sys, time
from pathlib import Path

out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
Path("prompt.txt").write_text(sys.argv[-1], encoding="utf-8")
(out / "note.txt").write_text("OK\\n", encoding="utf-8")

def send(event):
    print(json.dumps(event), flush=True)

send({"type": "session", "sessionId": "session-test-good", "cwd": os.getcwd()})
send({"type": "status", "phase": "turn_start", "turn": 1})
send({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
print("this is not json", flush=True)
send({"type": "tool_call", "callId": "c1", "tool": "write",
      "input": {"file_path": str(out / "note.txt"), "content": "OK"}})
send({"type": "tool_result", "callId": "c1", "status": "completed",
      "result": "<content>Created file</content>"})
time.sleep(0.15)
send({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
      "usage": {"inputTokens": 123, "outputTokens": 45, "totalTokens": 168}})
send({"type": "status", "phase": "step_start", "turn": 1, "step": 2})
send({"type": "thinking", "text": "private reasoning"})
send({"type": "text", "text": "Wrote note.txt."})
send({"type": "status", "phase": "step_end", "turn": 1, "step": 2,
      "usage": {"inputTokens": 456, "outputTokens": 67}})
send({"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}})
send({"type": "final", "text": "Wrote note.txt."})
"""

MISSING_CREDENTIAL_DSH = """
import json, sys
def send(event):
    print(json.dumps(event), flush=True)
send({"type": "session", "sessionId": "session-test-nokey", "cwd": "."})
send({"type": "status", "phase": "turn_start", "turn": 1})
send({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
send({"type": "status", "phase": "step_end", "turn": 1, "step": 1})
send({"type": "status", "phase": "turn_end", "turn": 1, "reason": {
    "kind": "error", "error": {"code": "MISSING_CREDENTIAL",
                               "message": "no API key for provider route"}}})
send({"type": "final", "text": ""})
sys.exit(1)
"""

NONZERO_EXIT_DSH = """
import json, sys
print(json.dumps({"type": "session", "sessionId": "session-test-exit", "cwd": "."}), flush=True)
print(json.dumps({"type": "status", "phase": "turn_end", "turn": 1,
                  "reason": {"kind": "completed"}}), flush=True)
print(json.dumps({"type": "final", "text": "done"}), flush=True)
sys.exit(3)
"""

NO_FINAL_DSH = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "note.txt").write_text("OK\\n", encoding="utf-8")
def send(event):
    print(json.dumps(event), flush=True)
send({"type": "session", "sessionId": "session-test-nofinal", "cwd": "."})
send({"type": "status", "phase": "turn_start", "turn": 1})
send({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
send({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
      "usage": {"inputTokens": 10, "outputTokens": 2}})
send({"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}})
"""

FAILED_DSH = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "note.txt").write_text("half done\\n", encoding="utf-8")
def send(event):
    print(json.dumps(event), flush=True)
send({"type": "session", "sessionId": "session-test-failed", "cwd": "."})
send({"type": "status", "phase": "turn_start", "turn": 1})
send({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
send({"type": "status", "phase": "step_end", "turn": 1, "step": 1,
      "usage": {"inputTokens": 50, "outputTokens": 5}})
send({"type": "status", "phase": "turn_end", "turn": 1, "reason": {"kind": "completed"}})
send({"type": "final", "text": "FAILED: the source sheet is not readable"})
"""

HANGING_DSH = """
import json, time
print(json.dumps({"type": "session", "sessionId": "session-test-hang", "cwd": "."}), flush=True)
print(json.dumps({"type": "status", "phase": "step_start", "turn": 1, "step": 1}), flush=True)
time.sleep(30)
"""

PACKET = {
    "objective": "在 out 目录创建 note.txt，内容 OK",
    "deliverable": "out/note.txt",
    "constraints": ["不要修改工作目录以外的文件"],
    "acceptance": [{"kind": "artifact_contains", "name": "note.txt", "text": "OK"}],
    "feedback": "上一轮文件内容为空，这次写入 OK。",
    "budget": {"max_prompt_tokens": 80000},
    "telemetry": {},
}

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def make_fake(root: Path, name: str, source: str) -> Path:
    script = root / f"{name}.py"
    script.write_text(source, encoding="utf-8")
    if os.name == "nt":
        launcher = root / f"{name}.cmd"
        launcher.write_text(
            "@echo off" + chr(13) + chr(10)
            + f'"{PY}" "{script}" %*' + chr(13) + chr(10),
            encoding="utf-8", newline="",
        )
    else:
        # CI runs on Linux; a Windows .cmd cannot be exec'd there. The shims
        # invoke the launcher as a program, so an executable shell wrapper is
        # the POSIX equivalent and keeps the adapter mapping covered on both OSes.
        launcher = root / f"{name}.sh"
        launcher.write_text(
            "#!/bin/sh" + chr(10)
            + f'exec "{PY}" "{script}" "$@"' + chr(10),
            encoding="utf-8", newline="",
        )
        launcher.chmod(0o755)
    return launcher


def build_adapter(workdir: Path, launcher: Path, *, gateway: str = "http://127.0.0.1:15722",
                  skip_gateway: bool = True, timeout: str = "30") -> CliSubprocessAdapter:
    env = {
        "AQI_DSH_LAUNCHER": str(launcher),
        "AQI_WORKDIR": str(workdir),
        "AQI_OUTDIR": str(workdir / "out"),
        "DEEPSEEK_BASE_URL": gateway,
        "AQI_DSH_TIMEOUT": timeout,
        "AQI_DSH_SKIP_GATEWAY_CHECK": "1" if skip_gateway else "0",
    }
    return CliSubprocessAdapter({
        "id": "fake_dsh",
        "executable": PY,
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 60,
        "env": env,
    })


async def collect(adapter: CliSubprocessAdapter, commission_id: str) -> list[dict]:
    context = await adapter.prepare_context(commission_id, PACKET)
    return [event async for event in adapter.execute({
        "context": context, "commission_id": commission_id, "packet": PACKET,
    })]


def types_of(events: list[dict]) -> list[str]:
    return [str(event.get("type")) for event in events]


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    workroot = ROOT / "tmp"
    workroot.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="dsh-shim-test-", dir=workroot))
    try:
        good_work = temp / "good"
        good_work.mkdir()
        good = make_fake(temp, "good_dsh", GOOD_DSH)
        events = await collect(build_adapter(good_work, good), "c_good")
        types = types_of(events)
        print("\n[happy path]")
        check("all emitted types are protocol events",
              all(kind in EVENT_TYPES for kind in types), str(types))
        check("progress is emitted", "progress" in types)
        check("session maps to step 1 progress",
              any(event.get("step_idx") == 1 and "session" in str(event.get("text"))
                  for event in events if event.get("type") == "progress"), str(types))
        check("step_start and tool_call both emit progress",
              types.count("progress") >= 4, str(types))
        check("tool progress names the tool",
              any("tool write" in str(event.get("text"))
                  for event in events if event.get("type") == "progress"))
        check("tool_result becomes a log with its status and result",
              any(str(event.get("text")).startswith("tool result completed:")
                  and "Created file" in str(event.get("text"))
                  for event in events if event.get("type") == "log"))
        check("thinking stays a log, never a deliverable",
              any(str(event.get("text")) == "thinking"
                  for event in events if event.get("type") == "log"))
        check("malformed launcher line degrades to log",
              any("this is not json" in str(event.get("text"))
                  for event in events if event.get("type") == "log"))

        tokens = [event for event in events if event.get("type") == "token"]
        check("one token event per step with usage", len(tokens) == 2, str(tokens))
        check("token carries the first step's real usage",
              tokens[0].get("prompt_tokens") == 123
              and tokens[0].get("completion_tokens") == 45, str(tokens[0]))
        check("token carries the second step's real usage",
              tokens[1].get("prompt_tokens") == 456
              and tokens[1].get("completion_tokens") == 67, str(tokens[1]))
        check("token duration comes from step timestamps",
              tokens[0].get("duration_ms", 0) >= 100, str(tokens[0]))
        check("token never fabricates ttft",
              all(event.get("ttft_ms") == 0 for event in tokens), str(tokens))
        check("token names the model",
              all(event.get("model") == "deepseek-flash" for event in tokens))

        check("artifact terminates the happy run", types[-1] == "artifact", str(types))
        check("no error on the happy path", "error" not in types)
        artifact = next(event for event in events if event.get("type") == "artifact")
        card = artifact.get("artifact") or {}
        refs = [str(item.get("ref")) for item in card.get("artifacts") or []]
        check("artifact card uses the final text as summary and has an intercepted note",
              card.get("summary") == "Wrote note.txt." and bool(card.get("intercepted")),
              str(card))
        check("artifact ref is one real absolute file",
              len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).is_file(),
              str(refs))
        check("artifact file content matches",
              Path(refs[0]).read_text(encoding="utf-8") == "OK\n")

        prompt = (good_work / "prompt.txt").read_text(encoding="utf-8")
        check("the constructed prompt reaches the launcher",
              prompt.startswith("You are a worker agent executing one assigned job."),
              prompt[:200])
        # cmd.exe collapses embedded newlines in %*, so the full multi-line prompt
        # is checked against the function that builds it, not the fake's argv.
        spec = importlib.util.spec_from_file_location("dsh_worker", SHIM)
        shim = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shim)
        full = shim.prompt_for(PACKET, good_work, good_work / "out")
        check("prompt carries objective, deliverable, output dir",
              "在 out 目录创建 note.txt" in full
              and "out/note.txt" in full
              and str(good_work / "out") in full, full[:400])
        check("prompt carries acceptance rules and prior feedback",
              "artifact_contains" in full and "上一轮文件内容为空" in full, full[:400])
        check("prompt tells the worker to reply FAILED instead of faking files",
              "FAILED:" in full and "placeholder" in full, full[:400])

        print("\n[failure paths]")
        cases = [
            ("missing_credential", MISSING_CREDENTIAL_DSH, "MISSING_CREDENTIAL"),
            ("nonzero_exit", NONZERO_EXIT_DSH, "exited 3"),
            ("no_final", NO_FINAL_DSH, "without a final event"),
            ("failed_marker", FAILED_DSH, "FAILED: the source sheet is not readable"),
        ]
        failure_types: list[str] = []
        for name, source, expected in cases:
            work = temp / name
            work.mkdir()
            launcher = make_fake(temp, name, source)
            run = await collect(build_adapter(work, launcher), f"c_{name}")
            run_types = types_of(run)
            failure_types.extend(run_types)
            error = next((event for event in run if event.get("type") == "error"), {})
            check(f"{name}: error terminates the run", run_types[-1] == "error", str(run_types))
            check(f"{name}: no artifact", "artifact" not in run_types, str(run_types))
            check(f"{name}: error text explains why", expected in str(error.get("text")),
                  str(error))

        print("\n[timeout]")
        hang_work = temp / "hang"
        hang_work.mkdir()
        hang = make_fake(temp, "hanging", HANGING_DSH)
        t0 = time.monotonic()
        hang_events = await collect(build_adapter(hang_work, hang, timeout="1"), "c_hang")
        hang_elapsed = time.monotonic() - t0
        hang_types = types_of(hang_events)
        failure_types.extend(hang_types)
        check("timeout emits the error terminal", hang_types[-1] == "error", str(hang_types))
        check("timeout does not wait out the hung child",
              hang_elapsed < 15, f"{hang_elapsed:.1f}s")
        check("timeout error names the deadline",
              "timed out after 1s" in str(hang_events[-1].get("text")), str(hang_events[-1]))

        print("\n[gateway boundary]")
        offline_work = temp / "offline"
        offline_work.mkdir()
        offline = await collect(
            build_adapter(offline_work, good, gateway="http://127.0.0.1:9",
                          skip_gateway=False),
            "c_offline")
        offline_types = types_of(offline)
        check("unreachable gateway emits error terminal",
              offline_types[-1] == "error", str(offline_types))
        check("unreachable gateway never launches dsh",
              not (offline_work / "prompt.txt").exists())
        check("gateway error names the base url",
              "127.0.0.1:9" in str(offline[-1].get("text")), str(offline[-1]))

        print("\n[coverage]")
        every = set(types) | set(failure_types) | set(offline_types)
        check("all runs together cover all five adapter events",
              every == set(EVENT_TYPES), f"covered={sorted(every)}")
        check("no run ever emits an unknown type", every <= set(EVENT_TYPES), str(sorted(every)))
    finally:
        shutil.rmtree(temp, ignore_errors=True)

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
