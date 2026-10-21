"""Deterministic proof that the Codex shim speaks PROTOCOL §22's five events.

    .\\.venv\\Scripts\\python.exe scripts\\test_codex_worker_shim.py

No network, no model, no Codex install: a fake `codex` executable emits Codex-shaped
JSONL and writes a real file. The shim is driven through the real `cli_subproc`
adapter, so this checks the mapping the dispatcher actually consumes:

    progress / log / token / artifact / error

It deliberately covers both a successful delivery and a failed run; a happy path
alone cannot prove `error` is reachable, and a failure alone cannot prove `artifact`
is a real file.
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

from server.assistant.adapters.base import EVENT_TYPES  # noqa: E402
from server.assistant.adapters.cli_subproc import CliSubprocessAdapter  # noqa: E402

SHIM = ROOT / "scripts" / "worker_shims" / "codex_worker.py"
PY = sys.executable

GOOD_CODEX = """
import json, os, sys
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "note.txt").write_text("OK\\n", encoding="utf-8")
for event in (
    {"type": "item.started", "item": {"id": "tool-1"}},
    {"type": "item.completed", "item": {"type": "reasoning"}},
    {"type": "item.completed", "item": {"type": "agent_message", "text": "Created note.txt"}},
    {"type": "turn.completed", "usage": {"input_tokens": 123, "output_tokens": 45}},
):
    print(json.dumps(event), flush=True)
"""

BAD_CODEX = """
import json, sys
print(json.dumps({"type": "item.completed",
                  "item": {"type": "error", "message": "sandbox denied"}}), flush=True)
sys.exit(3)
"""

PACKET = {
    "objective": "在 note.txt 里写入 OK",
    "deliverable": "out/note.txt",
    "constraints": ["不要修改工作目录以外的文件"],
    "budget": {"max_prompt_tokens": 40000},
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


def build_adapter(workdir: Path, launcher: Path) -> CliSubprocessAdapter:
    return CliSubprocessAdapter({
        "id": "fake_codex",
        "executable": PY,
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 30,
        "env": {
            "AQI_CODEX_EXE": str(launcher),
            "AQI_WORKDIR": str(workdir),
            "AQI_OUTDIR": str(workdir / "out"),
            "AQI_CODEX_TIMEOUT": "30",
            "AQI_GATEWAY_MODEL": "test-model",
        },
    })


async def collect(adapter: CliSubprocessAdapter, commission_id: str) -> list[dict]:
    context = await adapter.prepare_context(commission_id, PACKET)
    return [event async for event in adapter.execute({
        "context": context, "commission_id": commission_id, "packet": PACKET,
    })]


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    workroot = ROOT / "tmp"
    workroot.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="shim-test-", dir=workroot))
    try:
        good = make_fake(temp, "good_codex", GOOD_CODEX)
        good_events = await collect(build_adapter(temp, good), "c_good")
        good_types = [str(event.get("type")) for event in good_events]
        print("\n[happy path]")
        check("all emitted types are protocol events",
              all(kind in EVENT_TYPES for kind in good_types), str(good_types))
        check("progress is emitted", "progress" in good_types)
        check("log is emitted for reasoning", "log" in good_types)
        check("token is emitted once", good_types.count("token") == 1, str(good_types))
        check("artifact terminates the happy run", good_types[-1] == "artifact", str(good_types))
        check("no error on the happy path", "error" not in good_types)

        progress = [event for event in good_events if event.get("type") == "progress"]
        check("progress carries step_idx and text",
              all(isinstance(event.get("step_idx"), int) and event.get("text")
                  for event in progress), str(progress[:2]))
        token = next(event for event in good_events if event.get("type") == "token")
        check("token carries real usage",
              token.get("prompt_tokens") == 123 and token.get("completion_tokens") == 45, str(token))
        check("unknown ttft is recorded as duration, not zero",
              token.get("ttft_ms") == token.get("duration_ms") and token.get("duration_ms", 0) > 0,
              str(token))

        artifact = next(event for event in good_events if event.get("type") == "artifact")
        card = artifact.get("artifact") or {}
        refs = [str(item.get("ref")) for item in card.get("artifacts") or []]
        check("artifact card has summary and intercepted",
              bool(card.get("summary")) and bool(card.get("intercepted")), str(card))
        check("artifact ref is one real absolute file",
              len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).is_file(), str(refs))
        check("artifact file content matches",
              Path(refs[0]).read_text(encoding="utf-8") == "OK\n")

        bad = make_fake(temp, "bad_codex", BAD_CODEX)
        bad_events = await collect(build_adapter(temp, bad), "c_bad")
        bad_types = [str(event.get("type")) for event in bad_events]
        print("\n[failure path]")
        check("failure emits the error terminal", bad_types[-1] == "error", str(bad_types))
        check("failure never emits an artifact", "artifact" not in bad_types)
        error = next(event for event in bad_events if event.get("type") == "error")
        check("error text explains the exit", "exited 3" in str(error.get("text")), str(error))

        covered = set(good_types) | set(bad_types)
        print("\n[coverage]")
        check("both runs together cover all five adapter events",
              covered == set(EVENT_TYPES), f"covered={sorted(covered)}")
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
