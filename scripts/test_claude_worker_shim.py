"""Deterministic proof that the Claude shim speaks PROTOCOL §22's five events.

    .\\.venv\\Scripts\\python.exe scripts\\test_claude_worker_shim.py

No network, no model, no Claude install: a fake `claude` emits Claude Code's
stream-json and writes a real file. The shim is driven through the real
`cli_subproc` adapter, so this is the mapping the dispatcher consumes.
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
from server.assistant.telemetry import normalize_signature  # noqa: E402

SHIM = ROOT / "scripts" / "worker_shims" / "claude_worker.py"
PY = sys.executable

GOOD_CLAUDE = """
import json, os
from pathlib import Path
out = Path(os.environ["AQI_OUTDIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "note.txt").write_text("OK\\n", encoding="utf-8")
for event in (
    {"type": "system", "subtype": "init", "model": "claude-opus-5-5"},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "I'll create it."},
        {"type": "tool_use", "id": "t1", "name": "Write",
         "input": {"file_path": "note.txt", "content": "OK"}},
        {"type": "tool_use", "id": "t2", "name": "Write",
         "input": {"file_path": "other.txt", "content": "OK"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "created"},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "result": "Created note.txt.", "duration_ms": 1200, "ttft_ms": 300,
     "usage": {"input_tokens": 123, "output_tokens": 45}},
):
    print(json.dumps(event), flush=True)
"""

BAD_CLAUDE = """
import json
print(json.dumps({"type": "result", "subtype": "error_max_turns", "is_error": True,
                  "result": "permission denied", "duration_ms": 100,
                  "usage": {"input_tokens": 10, "output_tokens": 2}}), flush=True)
"""

PACKET = {
    "objective": "在 out 目录创建 note.txt，内容 OK",
    "deliverable": "out/note.txt",
    "constraints": ["不要修改工作目录以外的文件"],
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


def build_adapter(workdir: Path, launcher: Path) -> CliSubprocessAdapter:
    return CliSubprocessAdapter({
        "id": "fake_claude",
        "executable": PY,
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 30,
        "env": {
            "AQI_CLAUDE_EXE": str(launcher),
            "AQI_WORKDIR": str(workdir),
            "AQI_OUTDIR": str(workdir / "out"),
            "AQI_CLAUDE_CONFIG_DIR": str(workdir / ".claude-config"),
            "AQI_GATEWAY_URL": "http://127.0.0.1:15722",
            "AQI_CLAUDE_TIMEOUT": "30",
            "AQI_CLAUDE_SKIP_GATEWAY_CHECK": "1",
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
    temp = Path(tempfile.mkdtemp(prefix="claude-shim-test-", dir=workroot))
    try:
        good = make_fake(temp, "good_claude", GOOD_CLAUDE)
        events = await collect(build_adapter(temp, good), "c_good")
        types = [str(event.get("type")) for event in events]
        print("\n[happy path]")
        check("all emitted types are protocol events",
              all(kind in EVENT_TYPES for kind in types), str(types))
        check("progress is emitted", "progress" in types)
        check("tool progress is visible",
              any("tool Write" in str(event.get("text")) for event in events if event.get("type") == "progress"))
        # 熔断器签名的就是 progress 的 text。两次同名不同参的 Write 必须签出
        # 两个名：只报工具名（或把参数截断在同一目录前缀里）会让三次完全正常
        # 的调用看起来像原地打转，把好单拦下来。
        writes = [event for event in events
                  if event.get("type") == "progress" and "tool Write" in str(event.get("text"))]
        check("两次同名工具都上报了", len(writes) == 2, str(len(writes)))
        check("不同参数的签名不同",
              len({normalize_signature(event) for event in writes}) == 2,
              str([event.get("text") for event in writes]))
        check("log is emitted for a tool result", "log" in types)
        check("token is emitted once", types.count("token") == 1, str(types))
        check("artifact terminates the happy run", types[-1] == "artifact", str(types))
        check("no error on the happy path", "error" not in types)

        token = next(event for event in events if event.get("type") == "token")
        check("token carries real usage",
              token.get("prompt_tokens") == 123 and token.get("completion_tokens") == 45, str(token))
        check("token carries duration and ttft",
              token.get("duration_ms") == 1200 and token.get("ttft_ms") == 300, str(token))

        artifact = next(event for event in events if event.get("type") == "artifact")
        card = artifact.get("artifact") or {}
        refs = [str(item.get("ref")) for item in card.get("artifacts") or []]
        check("artifact card has summary and intercepted",
              bool(card.get("summary")) and bool(card.get("intercepted")), str(card))
        check("artifact ref is one real absolute file",
              len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).is_file(), str(refs))
        check("artifact file content matches",
              Path(refs[0]).read_text(encoding="utf-8") == "OK\n")

        bad = make_fake(temp, "bad_claude", BAD_CLAUDE)
        bad_events = await collect(build_adapter(temp, bad), "c_bad")
        bad_types = [str(event.get("type")) for event in bad_events]
        print("\n[failure path]")
        check("failure emits the error terminal", bad_types[-1] == "error", str(bad_types))
        check("failure never emits an artifact", "artifact" not in bad_types)
        error = next(event for event in bad_events if event.get("type") == "error")
        check("error text explains the failure", "permission denied" in str(error.get("text")), str(error))

        covered = set(types) | set(bad_types)
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
