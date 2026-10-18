"""Codex worker shim: run one commission through the local Codex CLI.

The dispatcher speaks a five-event line protocol (PROTOCOL §22). Codex speaks its
own JSONL. This shim translates one into the other and synthesizes the delivery
card the evaluator expects.

Environment:
  AQI_BRIEFING_PACKET   the briefing packet JSON (set by cli_subproc)
  AQI_COMMISSION_ID     commission id (set by cli_subproc)
  AQI_WORKDIR           directory the worker may write to (default: cwd)
  AQI_OUTDIR            where deliverables must land (default: <workdir>/out)
  AQI_GATEWAY_URL       OpenAI-compatible gateway (default: http://127.0.0.1:15721/v1)
  AQI_GATEWAY_MODEL     model id (default: step-5-preview)
  AQI_CODEX_EXE         codex executable (default: the installed Codex binary)
  AQI_CODEX_TIMEOUT     seconds (default: 240)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKIP = {".codex-home", "tmp", ".codex"}


def find_codex() -> str:
    """Resolve the Codex binary without pinning a version-specific install hash.

    Codex updates replace `bin/<hash>/codex.exe`, so a hard-coded path turns a
    working worker into "executable not found" after one update. Order:
    explicit env override, newest installed binary, then `codex` on PATH.
    """
    override = os.environ.get("AQI_CODEX_EXE")
    if override:
        return override
    base = Path(os.environ.get("LOCALAPPDATA") or "") / "OpenAI" / "Codex" / "bin"
    try:
        candidates = [path for path in base.glob("*/codex.exe") if path.is_file()]
    except OSError:
        candidates = []
    if candidates:
        try:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
        except OSError:
            return str(candidates[0])
    found = shutil.which("codex")
    return found or ""


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def packet() -> dict:
    raw = os.environ.get("AQI_BRIEFING_PACKET") or "{}"
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def clean(text: object, limit: int) -> str:
    return str(text or "").replace("\ufffd", "").strip()[:limit]


def write_codex_home(workdir: Path) -> Path:
    home = workdir / ".codex-home"
    home.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("AQI_GATEWAY_MODEL", "step-5-preview")
    base_url = os.environ.get("AQI_GATEWAY_URL", "http://127.0.0.1:15721/v1")
    (home / "config.toml").write_text(
        "\n".join([
            'model_provider = "custom"',
            f'model = "{model}"',
            f'model_reasoning_effort = "{os.environ.get("AQI_CODEX_EFFORT", "low")}"',
            "",
            "[model_providers.custom]",
            'name = "custom"',
            f'base_url = "{base_url}"',
            'wire_api = "responses"',
            'experimental_bearer_token = "PROXY_MANAGED"',
            "requires_openai_auth = false",
        ]) + "\n",
        encoding="utf-8",
    )
    return home


def prompt_for(pack: dict, workdir: Path, outdir: Path) -> str:
    objective = clean(pack.get("objective"), 400) or "完成这一单"
    deliverable = clean(pack.get("deliverable"), 300) or "把成果写成 out 目录里的真实文件"
    constraints = [clean(item, 120) for item in (pack.get("constraints") or [])][:6]
    lines = [
        "You are a worker agent executing one assigned job.",
        f"Working directory: {workdir}",
        f"Write every deliverable inside this directory: {outdir}",
        f"Objective: {objective}",
        f"Deliverable: {deliverable}",
    ]
    if constraints:
        lines.append("Constraints:")
        lines.extend(f"- {item}" for item in constraints)
    lines.extend([
        "Do the job now. Create the deliverable as a real file under the output directory.",
        "Do not ask questions. Do not modify files outside the working directory.",
        "If the objective cannot be completed, do not create placeholder or error files.",
        "Reply exactly `FAILED: <short reason>` and stop instead.",
        "When finished, reply with one short sentence describing what you created.",
    ])
    return "\n".join(lines)


def files_created(outdir: Path, since: float) -> list[Path]:
    found = []
    for path in outdir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP for part in path.relative_to(outdir).parts):
            continue
        try:
            if path.stat().st_mtime >= since - 1:
                found.append(path)
        except OSError:
            continue
    return sorted(found)


def preview(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:200].replace("\ufffd", "")
    except OSError:
        text = ""
    return {"kind": "file", "ref": str(path.resolve()), "preview": text}


def run(pack: dict, workdir: Path, outdir: Path) -> int:
    exe = find_codex()
    if not exe or not Path(exe).is_file():
        emit({"type": "error", "text": f"codex executable not found: {exe}"})
        return 0
    timeout = float(os.environ.get("AQI_CODEX_TIMEOUT", "240"))
    home = write_codex_home(workdir)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "tmp").mkdir(exist_ok=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(home)
    env["TMP"] = str(workdir / "tmp")
    env["TEMP"] = str(workdir / "tmp")
    env["PYTHONIOENCODING"] = "utf-8"
    started = time.time()
    emit({"type": "progress", "step_idx": 1, "text": "codex worker started"})
    cmd = [exe, "exec", "--json", "-C", str(workdir), "-s",
           os.environ.get("AQI_CODEX_SANDBOX", "workspace-write"),
           prompt_for(pack, workdir, outdir)]
    final_text = ""
    model = os.environ.get("AQI_GATEWAY_MODEL", "step-5-preview")
    try:
        process = subprocess.Popen(
            cmd, cwd=str(workdir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        emit({"type": "error", "text": f"codex failed to start: {type(exc).__name__}"})
        return 0
    deadline = started + timeout
    assert process.stdout is not None
    for line in process.stdout:
        if time.time() > deadline:
            process.kill()
            emit({"type": "error", "text": f"codex timed out after {timeout:.0f}s"})
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            emit({"type": "log", "text": line[:400]})
            continue
        kind = event.get("type")
        if kind == "item.started":
            item = event.get("item") or {}
            emit({"type": "progress", "step_idx": 2,
                  "text": f"tool {clean(item.get('id'), 20)}"})
        elif kind == "item.completed":
            item = event.get("item") or {}
            item_type = item.get("type")
            if item_type == "agent_message":
                final_text = clean(item.get("text"), 300)
                emit({"type": "progress", "step_idx": 2, "text": final_text[:200]})
            elif item_type == "reasoning":
                emit({"type": "log", "text": "thinking"})
            elif item_type == "command_execution":
                emit({"type": "log", "text": f"exit {item.get('exit_code')}"})
            elif item_type == "error":
                emit({"type": "log", "text": clean(item.get("message"), 400)})
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            duration_ms = int((time.time() - started) * 1000)
            emit({
                "type": "token",
                "model": model,
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
                # Codex's JSONL does not expose first-token latency. PROTOCOL §20
                # says non-streaming calls record duration in both fields rather
                # than inventing a 0 that reads like "instant".
                "ttft_ms": duration_ms,
                "duration_ms": duration_ms,
                "status_code": 0,
            })
        elif kind == "error":
            emit({"type": "error", "text": clean(event.get("message"), 240)})
            return 0
    code = process.wait()
    if code != 0:
        emit({"type": "error", "text": f"codex exited {code}"})
        return 0
    if final_text.strip().upper().startswith("FAILED"):
        emit({"type": "error", "text": final_text[:240]})
        return 0
    created = files_created(outdir, started)
    if not created:
        emit({"type": "error", "text": "codex produced no file in the output directory"})
        return 0
    emit({
        "type": "artifact",
        "artifact": {
            "summary": (final_text or f"created {created[0].name}")[:400],
            "intercepted": "没有需要总管拦下的东西。",
            "artifacts": [preview(path) for path in created[:3]],
        },
    })
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    workdir = Path(os.environ.get("AQI_WORKDIR") or os.getcwd()).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = Path(os.environ.get("AQI_OUTDIR") or (workdir / "out")).resolve()
    return run(packet(), workdir, outdir)


if __name__ == "__main__":
    raise SystemExit(main())
