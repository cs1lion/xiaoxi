"""WorkBuddy worker shim: run one commission through WorkBuddy's bundled CodeBuddy CLI.

The dispatcher speaks the five-event line protocol (PROTOCOL §22). WorkBuddy
speaks Claude-Code-shaped `stream-json`; this shim translates one into the other
and builds the delivery card the evaluator expects.

WorkBuddy ships the CLI inside its Electron bundle, so "the executable" is
WorkBuddy.exe itself running as Node. `bin/codebuddy` is missing `dist/codebuddy`;
the packaged `codebuddy-lite-wb.mjs` is the entry that runs.

Environment:
  AQI_BRIEFING_PACKET      briefing packet JSON (set by cli_subproc)
  AQI_COMMISSION_ID        commission id (set by cli_subproc)
  AQI_WORKDIR              workspace WorkBuddy may write to (default: per-commission dir)
  AQI_OUTDIR               where deliverables must land (default: <workdir>/out)
  AQI_WORKSPACE_ROOT       parent for per-commission workspaces (default: <repo>/data/commissions)
  AQI_WORKBUDDY_EXE        WorkBuddy.exe (default: %LOCALAPPDATA%\\Programs\\WorkBuddy)
  AQI_WORKBUDDY_CLI        packaged CLI entry (default: beside the exe)
  AQI_WORKBUDDY_LAUNCHER   test hook: run this with the CLI args instead of Electron
  AQI_WORKBUDDY_MODEL      model id (default: step-5-preview)
  AQI_WORKBUDDY_TOOLS      built-in tool allowlist (default: Read,Write,Edit,Glob,Grep)
  AQI_WORKBUDDY_TIMEOUT    seconds (default: 300)
  AQI_WORKBUDDY_SKIP_GATEWAY_CHECK  "1" skips the /models probe (tests)
  CODEBUDDY_BASE_URL       OpenAI-compatible gateway (default: http://127.0.0.1:15721/v1)
  CODEBUDDY_API_KEY        gateway key (default: PROXY_MANAGED)
  CODEBUDDY_CONFIG_DIR     isolated config dir (default: <workdir>/.codebuddy-config)
  CODEBUDDY_DATA_ROOT      isolated data root (default: <workdir>/.codebuddy-data)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://127.0.0.1:15721/v1"
DEFAULT_MODEL = "step-5-preview"
DEFAULT_TOOLS = "Read,Write,Edit,Glob,Grep"
SKIP = {".codebuddy-config", ".codebuddy-data", "tmp", ".git"}
ROOT = Path(__file__).resolve().parents[2]


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def clean(text: object, limit: int) -> str:
    return str(text or "").replace("\ufffd", "").strip()[:limit]


def packet() -> dict:
    raw = os.environ.get("AQI_BRIEFING_PACKET") or "{}"
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def base_url() -> str:
    return (os.environ.get("CODEBUDDY_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def gateway_ready() -> bool:
    if os.environ.get("AQI_WORKBUDDY_SKIP_GATEWAY_CHECK") == "1":
        return True
    try:
        with urllib.request.urlopen(base_url() + "/models", timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def find_exe() -> str:
    override = os.environ.get("AQI_WORKBUDDY_EXE")
    if override:
        return override
    candidate = (Path(os.environ.get("LOCALAPPDATA") or "")
                 / "Programs" / "WorkBuddy" / "WorkBuddy.exe")
    return str(candidate) if candidate.is_file() else ""


def find_cli(exe: str) -> str:
    override = os.environ.get("AQI_WORKBUDDY_CLI")
    if override:
        return override
    base = (Path(exe).parent if exe
            else Path(os.environ.get("LOCALAPPDATA") or "") / "Programs" / "WorkBuddy")
    candidate = (base / "resources" / "app.asar.unpacked" / "cli" / "dist"
                 / "codebuddy-lite-wb.mjs")
    return str(candidate) if candidate.is_file() else ""


def resolve_workdir() -> Path:
    """Use an explicit workspace, or allocate a per-commission one.

    A production roster entry runs from the repo root. Without this, WorkBuddy
    would write into the repo; with it, every commission gets its own disposable
    tree under data/commissions/<id>-<stamp>/.
    """
    explicit = os.environ.get("AQI_WORKDIR")
    if explicit:
        return Path(explicit).resolve()
    root = Path(os.environ.get("AQI_WORKSPACE_ROOT") or (ROOT / "data" / "commissions"))
    raw = os.environ.get("AQI_COMMISSION_ID") or "ad-hoc"
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")[:40] or "ad-hoc"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return (root / f"{safe}-{stamp}").resolve()


def bullets(value: object, limit: int) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    result = [clean(item, 200) for item in items]
    return [item for item in result if item][:limit]


def prompt_for(pack: dict, workdir: Path, outdir: Path) -> str:
    objective = clean(pack.get("objective"), 400) or "完成这一单"
    deliverable = clean(pack.get("deliverable"), 300) or "把成果写成 out 目录里的真实文件"
    constraints = bullets(pack.get("constraints"), 6)
    acceptance = bullets(pack.get("acceptance"), 8)
    feedback = bullets(pack.get("feedback"), 6)
    lines = [
        "You are a worker agent executing one assigned job.",
        f"Working directory: {workdir}",
        f"Write every deliverable inside this directory: {outdir}",
        "Do not write files anywhere else.",
        f"Objective: {objective}",
        f"Deliverable: {deliverable}",
    ]
    if constraints:
        lines.append("Constraints:")
        lines.extend(f"- {item}" for item in constraints)
    if acceptance:
        lines.append("Acceptance criteria:")
        lines.extend(f"- {item}" for item in acceptance)
    if feedback:
        lines.append("Reviewer feedback from the previous attempt (address it):")
        lines.extend(f"- {item}" for item in feedback)
    lines.extend([
        "Do the job now. Create the deliverable as a real file under the output directory.",
        "Use your tools to inspect and write files; do not only describe the work.",
        "Do not ask questions.",
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
    launcher = os.environ.get("AQI_WORKBUDDY_LAUNCHER") or ""
    exe = "" if launcher else find_exe()
    if not launcher and (not exe or not Path(exe).is_file()):
        emit({"type": "error", "text": f"WorkBuddy executable not found: {exe or '(default path)'}"})
        return 0
    cli = "" if launcher else find_cli(exe)
    if not launcher and (not cli or not Path(cli).is_file()):
        emit({"type": "error", "text": f"WorkBuddy CLI entry not found: {cli or '(default path)'}"})
        return 0
    if not gateway_ready():
        emit({"type": "error",
              "text": f"WorkBuddy gateway is not running at {base_url()}; "
                      "start it with scripts/run.py or the CC Switch proxy"})
        return 0

    config_dir = Path(os.environ.get("CODEBUDDY_CONFIG_DIR")
                      or (workdir / ".codebuddy-config")).resolve()
    data_root = Path(os.environ.get("CODEBUDDY_DATA_ROOT")
                     or (workdir / ".codebuddy-data")).resolve()
    config_dir.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "tmp").mkdir(exist_ok=True)

    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    env["CODEBUDDY_BASE_URL"] = base_url()
    env["CODEBUDDY_API_KEY"] = os.environ.get("CODEBUDDY_API_KEY") or "PROXY_MANAGED"
    env["CODEBUDDY_CONFIG_DIR"] = str(config_dir)
    env["CODEBUDDY_DATA_ROOT"] = str(data_root)
    env["TMP"] = str(workdir / "tmp")
    env["TEMP"] = str(workdir / "tmp")
    env["PYTHONIOENCODING"] = "utf-8"
    # The packet is delivered to this shim; it need not sit in WorkBuddy's env.
    env.pop("AQI_BRIEFING_PACKET", None)

    model = os.environ.get("AQI_WORKBUDDY_MODEL") or DEFAULT_MODEL
    tools = os.environ.get("AQI_WORKBUDDY_TOOLS") or DEFAULT_TOOLS
    timeout = float(os.environ.get("AQI_WORKBUDDY_TIMEOUT", "300"))
    cli_args = [
        "-p", prompt_for(pack, workdir, outdir),
        "--model", model,
        "--output-format", "stream-json",
        "-y",
        "--no-session-persistence",
        "--tools", tools,
    ]
    cmd = [launcher, *cli_args] if launcher else [exe, cli, *cli_args]

    started = time.time()
    emit({"type": "progress", "step_idx": 1, "text": "workbuddy worker started"})
    try:
        process = subprocess.Popen(
            cmd, cwd=str(workdir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        emit({"type": "error", "text": f"workbuddy failed to start: {type(exc).__name__}"})
        return 0

    deadline = started + timeout
    final_text = ""
    result_error = ""
    token_sent = False
    assert process.stdout is not None
    for line in process.stdout:
        if time.time() > deadline:
            process.kill()
            emit({"type": "error", "text": f"workbuddy timed out after {timeout:.0f}s"})
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            emit({"type": "log", "text": line[:400]})
            continue
        if not isinstance(event, dict):
            emit({"type": "log", "text": line[:400]})
            continue
        kind = event.get("type")
        if kind == "system":
            if event.get("subtype") == "init":
                emit({"type": "progress", "step_idx": 1,
                      "text": f"workbuddy ready: {clean(event.get('model'), 60)}"})
        elif kind == "assistant":
            message = event.get("message") or {}
            for block in (message.get("content") or []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    final_text = clean(block.get("text"), 400)
                    emit({"type": "log", "text": final_text[:200]})
                elif block.get("type") == "tool_use":
                    # 与 claude_worker / dsh_worker 同一套办法：cli_subproc 的
                    # progress 只留 step_idx/text，熔断器签名的正是这段 text，
                    # 只报工具名会让三次正常的同名调用看起来像原地打转。
                    try:
                        digest = hashlib.sha256(json.dumps(
                            block.get("input") or {}, ensure_ascii=False,
                            sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]
                    except (TypeError, ValueError):
                        digest = ""
                    emit({"type": "progress", "step_idx": 2,
                          "text": f"tool {clean(block.get('name'), 40)} {digest}".strip()})
        elif kind == "user":
            message = event.get("message") or {}
            has_result = any(isinstance(block, dict) and block.get("type") == "tool_result"
                             for block in (message.get("content") or []))
            if has_result:
                emit({"type": "log", "text": "tool result"})
        elif kind == "result":
            if not token_sent:
                usage = event.get("usage") or {}
                duration = int(event.get("duration_ms") or (time.time() - started) * 1000)
                # The result records no first-token timestamp. PROTOCOL §20 puts the
                # duration in both fields rather than a 0 that reads like "instant".
                emit({
                    "type": "token",
                    "model": model,
                    "prompt_tokens": int(usage.get("input_tokens") or 0),
                    "completion_tokens": int(usage.get("output_tokens") or 0),
                    "ttft_ms": int(event.get("ttft_ms") or 0) or duration,
                    "duration_ms": duration,
                    "status_code": 0,
                })
                token_sent = True
            final_text = clean(event.get("result"), 400) or final_text
            if event.get("is_error"):
                result_error = (clean(event.get("result") or event.get("subtype"), 200)
                                or "workbuddy reported an error")
        elif kind == "error":
            result_error = clean(event.get("message") or event.get("error"), 240) or "workbuddy error"
        else:
            emit({"type": "log", "text": f"workbuddy event {clean(kind, 40) or '?'}"})

    code = process.wait()
    if result_error:
        emit({"type": "error", "text": result_error})
        return 0
    if final_text.strip().upper().startswith("FAILED"):
        emit({"type": "error", "text": final_text[:240]})
        return 0
    if code != 0:
        emit({"type": "error", "text": f"workbuddy exited {code}"})
        return 0
    if not token_sent:
        emit({"type": "log", "text": "workbuddy finished without a result usage record"})

    created = files_created(outdir, started)
    if not created:
        emit({"type": "error", "text": "workbuddy produced no file in the output directory"})
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
    workdir = resolve_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    outdir = Path(os.environ.get("AQI_OUTDIR") or (workdir / "out")).resolve()
    return run(packet(), workdir, outdir)


if __name__ == "__main__":
    raise SystemExit(main())
