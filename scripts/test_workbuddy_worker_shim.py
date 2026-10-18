"""Deterministic proof that the WorkBuddy shim speaks PROTOCOL §22's five events.

    .\\.venv\\Scripts\\python.exe scripts\\test_workbuddy_worker_shim.py

No network, no model, no WorkBuddy install: a fake launcher emits CodeBuddy's
Claude-shaped stream-json and writes real files. The shim is driven through the
real `cli_subproc` adapter, so this is the mapping the dispatcher consumes.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.adapters.base import EVENT_TYPES  # noqa: E402
from server.assistant.adapters.cli_subproc import CliSubprocessAdapter  # noqa: E402

SHIM = ROOT / "scripts" / "worker_shims" / "workbuddy_worker.py"
PY = sys.executable

GOOD_WORKBUDDY = """
import json, os, sys
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "note.txt").write_text("OK\\n", encoding="utf-8")
Path(os.environ["FAKE_ARGV_FILE"]).write_text(
    json.dumps(sys.argv[1:], ensure_ascii=False), encoding="utf-8")
for event in (
    {"type": "system", "subtype": "init", "model": "step-5-preview"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I'll create it."},
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": "out/note.txt", "content": "OK"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "created"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "Created note.txt.", "duration_ms": 1200,
     "usage": {"input_tokens": 123, "output_tokens": 45}},
):
    print(json.dumps(event), flush=True)
"""

BOGUS_WORKBUDDY = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "bogus.txt").write_text("kept\\n", encoding="utf-8")
print("this line is not json", flush=True)
print(json.dumps({"type": "mystery", "text": "unmapped"}), flush=True)
for event in (
    {"type": "system", "subtype": "init", "model": "step-5-preview"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "created"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "Created bogus.txt.", "duration_ms": 800,
     "usage": {"input_tokens": 7, "output_tokens": 3}},
):
    print(json.dumps(event), flush=True)
"""

NORESULT_WORKBUDDY = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "plain.txt").write_text("plain\\n", encoding="utf-8")
print(json.dumps({"type": "system", "subtype": "init", "model": "step-5-preview"}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Wrote plain.txt."},
]}}), flush=True)
"""

FAIL_WORKBUDDY = """
import json, os, sys
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "draft.txt").write_text("partial\\n", encoding="utf-8")
print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                  "result": "FAILED: permission denied by gateway", "duration_ms": 100,
                  "usage": {"input_tokens": 10, "output_tokens": 2}}), flush=True)
sys.exit(1)
"""

FAILED_TEXT_WORKBUDDY = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "draft.txt").write_text("partial\\n", encoding="utf-8")
for event in (
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "FAILED: the source file is missing"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "FAILED: the source file is missing", "duration_ms": 50,
     "usage": {"input_tokens": 5, "output_tokens": 1}},
):
    print(json.dumps(event), flush=True)
"""

ALLOC_WORKBUDDY = """
import json
from pathlib import Path
out = Path("out")
out.mkdir(parents=True, exist_ok=True)
(out / "alloc.txt").write_text("alloc\\n", encoding="utf-8")
for event in (
    {"type": "system", "subtype": "init", "model": "step-5-preview"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Write", "input": {}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "created"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "Created alloc.txt.", "duration_ms": 10,
     "usage": {"input_tokens": 1, "output_tokens": 1}},
):
    print(json.dumps(event), flush=True)
"""

PACKET = {
    "objective": "在 out 目录创建 note.txt，内容 OK",
    "deliverable": "out/note.txt",
    "constraints": ["不要修改工作目录以外的文件"],
    "acceptance": ["out/note.txt 存在且内容为 OK"],
    "feedback": "上次少了文件",
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
    launcher = root / f"{name}.cmd"
    launcher.write_text(f'@echo off\r\n"{PY}" "{script}" %*\r\n', encoding="utf-8")
    return launcher


def build_adapter(workdir: Path, launcher: Path, argv_file: Path,
                  allocate: bool = False) -> CliSubprocessAdapter:
    workdir.mkdir(parents=True, exist_ok=True)
    env = {
        "AQI_WORKBUDDY_LAUNCHER": str(launcher),
        "AQI_WORKBUDDY_TIMEOUT": "30",
        "AQI_WORKBUDDY_SKIP_GATEWAY_CHECK": "1",
        "FAKE_ARGV_FILE": str(argv_file),
    }
    if allocate:
        env["AQI_WORKSPACE_ROOT"] = str(workdir / "workspaces")
    else:
        env["AQI_WORKDIR"] = str(workdir)
        env["AQI_OUTDIR"] = str(workdir / "out")
    return CliSubprocessAdapter({
        "id": "fake_workbuddy",
        "executable": PY,
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 30,
        "env": env,
    })


async def collect(adapter: CliSubprocessAdapter, commission_id: str) -> list[dict]:
    context = await adapter.prepare_context(commission_id, PACKET)
    return [event async for event in adapter.execute({
        "context": context, "commission_id": commission_id, "packet": PACKET,
    })]


def logs(events: list[dict]) -> list[str]:
    return [str(event.get("text") or "") for event in events if event.get("type") == "log"]


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    workroot = ROOT / "tmp"
    workroot.mkdir(exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix="workbuddy-shim-test-", dir=workroot))
    try:
        good = make_fake(temp, "good_workbuddy", GOOD_WORKBUDDY)
        good_dir = temp / "good"
        argv_file = temp / "good-argv.json"
        events = await collect(build_adapter(good_dir, good, argv_file), "c_good")
        types = [str(event.get("type")) for event in events]
        print("\n[happy path]")
        check("all emitted types are protocol events",
              all(kind in EVENT_TYPES for kind in types), str(types))
        check("progress is emitted", "progress" in types)
        check("init progress names the model",
              any("step-5-preview" in str(event.get("text"))
                  for event in events if event.get("type") == "progress"))
        check("tool progress is visible",
              any("tool Write" in str(event.get("text"))
                  for event in events if event.get("type") == "progress"))
        check("log is emitted for a tool result", "tool result" in logs(events), str(logs(events)))
        check("token is emitted once", types.count("token") == 1, str(types))
        check("artifact terminates the happy run", types[-1] == "artifact", str(types))
        check("no error on the happy path", "error" not in types)

        token = next(event for event in events if event.get("type") == "token")
        check("token carries real usage",
              token.get("prompt_tokens") == 123 and token.get("completion_tokens") == 45,
              str(token))
        check("token carries the real duration", token.get("duration_ms") == 1200, str(token))
        check("token never invents ttft",
              token.get("ttft_ms") == token.get("duration_ms"), str(token))

        artifact = next(event for event in events if event.get("type") == "artifact")
        card = artifact.get("artifact") or {}
        refs = [str(item.get("ref")) for item in card.get("artifacts") or []]
        check("artifact card has summary and intercepted",
              bool(card.get("summary")) and bool(card.get("intercepted")), str(card))
        check("artifact ref is one real absolute file",
              len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).is_file(),
              str(refs))
        check("artifact file content matches",
              Path(refs[0]).read_text(encoding="utf-8") == "OK\n")
        check("artifact ref lives under the out directory",
              Path(refs[0]).parent == (good_dir / "out").resolve(), refs[0])

        artifact_at = types.index("artifact")
        check("token precedes the artifact",
              types.index("token") < artifact_at, str(types))
        check("tool result log precedes the artifact",
              any(i < artifact_at and event.get("type") == "log"
                  and event.get("text") == "tool result"
                  for i, event in enumerate(events)), str(types))

        argv = json.loads(argv_file.read_text(encoding="utf-8"))
        # A .cmd launcher truncates its command line at the first newline in the
        # prompt, so only the leading CLI shape is observable from the fake.
        check("launcher override receives the CLI args", argv[:1] == ["-p"], str(argv))

        bogus = make_fake(temp, "bogus_workbuddy", BOGUS_WORKBUDDY)
        bogus_events = await collect(
            build_adapter(temp / "bogus", bogus, temp / "bogus-argv.json"), "c_bogus")
        bogus_types = [str(event.get("type")) for event in bogus_events]
        print("\n[bogus stream lines]")
        check("a non-JSON line degrades to a log",
              any("this line is not json" in text for text in logs(bogus_events)),
              str(logs(bogus_events)))
        check("an unknown stream event degrades to a log",
              any("mystery" in text for text in logs(bogus_events)), str(logs(bogus_events)))
        check("bogus run emits only protocol events",
              all(kind in EVENT_TYPES for kind in bogus_types), str(bogus_types))
        bogus_artifact = next(event for event in bogus_events if event.get("type") == "artifact")
        bogus_ref = bogus_artifact["artifact"]["artifacts"][0]["ref"]
        check("bogus run still delivers the real file",
              Path(bogus_ref).read_text(encoding="utf-8") == "kept\n", bogus_ref)

        noresult = make_fake(temp, "noresult_workbuddy", NORESULT_WORKBUDDY)
        plain_events = await collect(
            build_adapter(temp / "noresult", noresult, temp / "noresult-argv.json"), "c_plain")
        plain_types = [str(event.get("type")) for event in plain_events]
        print("\n[missing usage record]")
        check("no token is fabricated without a result record", "token" not in plain_types,
              str(plain_types))
        check("the file is still delivered without a result record",
              plain_types[-1] == "artifact", str(plain_types))

        bad = make_fake(temp, "fail_workbuddy", FAIL_WORKBUDDY)
        bad_events = await collect(
            build_adapter(temp / "bad", bad, temp / "bad-argv.json"), "c_bad")
        bad_types = [str(event.get("type")) for event in bad_events]
        print("\n[failure path]")
        check("failure emits the error terminal", bad_types[-1] == "error", str(bad_types))
        check("failure never emits an artifact", "artifact" not in bad_types, str(bad_types))
        error = next(event for event in bad_events if event.get("type") == "error")
        check("error text reports the reason, not just the exit code",
              "permission denied" in str(error.get("text")), str(error))

        failed_text = make_fake(temp, "failed_text_workbuddy", FAILED_TEXT_WORKBUDDY)
        failed_events = await collect(
            build_adapter(temp / "failed", failed_text, temp / "failed-argv.json"), "c_failed")
        failed_types = [str(event.get("type")) for event in failed_events]
        print("\n[FAILED answer]")
        check("a FAILED reply emits the error terminal", failed_types[-1] == "error",
              str(failed_types))
        check("a FAILED reply never emits an artifact", "artifact" not in failed_types,
              str(failed_types))
        failed_error = next(event for event in failed_events if event.get("type") == "error")
        check("the FAILED reason reaches the error text",
              "source file is missing" in str(failed_error.get("text")), str(failed_error))

        alloc = make_fake(temp, "alloc_workbuddy", ALLOC_WORKBUDDY)
        alloc_events = await collect(
            build_adapter(temp / "alloc", alloc, temp / "alloc-argv.json", allocate=True),
            "c_alloc")
        alloc_ref = Path(alloc_events[-1]["artifact"]["artifacts"][0]["ref"])
        print("\n[workspace isolation]")
        check("missing AQI_WORKDIR allocates under the workspace root",
              (temp / "alloc" / "workspaces").resolve() in alloc_ref.parents, str(alloc_ref))
        check("the allocated workspace is named after the commission",
              alloc_ref.parents[1].name.startswith("c_alloc-"), str(alloc_ref))
        check("nothing lands in the process cwd",
              not (temp / "alloc" / "out").exists(), str(temp / "alloc"))

        covered = set(types) | set(bogus_types) | set(plain_types) | set(bad_types) \
            | set(failed_types) | {str(event.get("type")) for event in alloc_events}
        print("\n[coverage]")
        check("all runs together cover all five adapter events",
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
