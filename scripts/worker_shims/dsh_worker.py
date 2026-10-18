"""DeepSeek Harness worker shim: run one commission through `dsh --profile headless`.

The dispatcher speaks the five-event line protocol (PROTOCOL §22). DSH's `--json`
projection has its own vocabulary; this shim translates one into the other and
builds the delivery card the evaluator expects.

DSH runs its own tool loop (read/write/bash), so the deliverable is a real file
the Agent wrote into `out/`, not text this shim saved on its behalf.

Environment:
  AQI_BRIEFING_PACKET    briefing packet JSON (set by cli_subproc)
  AQI_COMMISSION_ID      commission id (set by cli_subproc)
  AQI_WORKDIR            workspace DSH may write to (default: per-commission tree)
  AQI_OUTDIR             where deliverables must land (default: <workdir>/out)
  DEEPSEEK_BASE_URL      Anthropic gateway (default: http://127.0.0.1:15722)
  DEEPSEEK_API_KEY       gateway token (default: local)
  DSH_HOME               DSH home directory, passed through when set
  DSH_PERMISSION_MODE    optional DSH tool permission mode, passed through when set
  AQI_DSH_EXE            DSH executable (default: the installed desktop app)
  AQI_DSH_CLI            dsh-desktop-host cli.js (default: under the install dir)
  AQI_DSH_LAUNCHER       test override: run this program directly with the app args
  AQI_DSH_MODEL          model label on token events (default: deepseek-flash)
  AQI_DSH_TIMEOUT        seconds (default: 300)
  AQI_DSH_SKIP_GATEWAY_CHECK  1 skips the GET /health probe
"""
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

SKIP = {".dsh-home", "tmp", ".git"}
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = Path(r"D:\Programs\DeepSeek Harness\DeepSeek Harness.exe")
CLI_REL = Path("resources/app.asar/dsh/node_modules/@deepseek-ai/"
               "dsh-desktop-host/lib/cli.js")


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


def gateway_url() -> str:
    return (os.environ.get("DEEPSEEK_BASE_URL")
            or "http://127.0.0.1:15722").rstrip("/")


def gateway_ready() -> bool:
    if os.environ.get("AQI_DSH_SKIP_GATEWAY_CHECK") == "1":
        return True
    try:
        with urllib.request.urlopen(gateway_url() + "/health", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def resolve_workdir() -> Path:
    """Use an explicit workspace, or allocate a per-commission one.

    A production roster entry runs from the repo root. Without this, DSH would
    write into the repo; with it, every commission gets its own disposable tree
    under data/commissions/<id>-<stamp>/.
    """
    explicit = os.environ.get("AQI_WORKDIR")
    if explicit:
        return Path(explicit).resolve()
    root = Path(os.environ.get("AQI_WORKSPACE_ROOT") or (ROOT / "data" / "commissions"))
    raw = os.environ.get("AQI_COMMISSION_ID") or "ad-hoc"
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")[:40] or "ad-hoc"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return (root / f"{safe}-{stamp}").resolve()


def find_exe() -> Path:
    override = os.environ.get("AQI_DSH_EXE")
    return Path(override) if override else DEFAULT_EXE


def find_cli(exe: Path) -> Path:
    override = os.environ.get("AQI_DSH_CLI")
    return Path(override) if override else exe.parent / CLI_REL


def build_command(exe: Path, cli: Path, prompt: str) -> list[str]:
    launcher = os.environ.get("AQI_DSH_LAUNCHER")
    if launcher:
        return [launcher, "headless", "--json", prompt]
    # The desktop app has no `dsh` on PATH; its bundled Electron runs the same
    # CLI when told to act as Node.
    return [str(exe), "--expose-internals", str(cli), "headless", "--json", prompt]


def prompt_for(pack: dict, workdir: Path, outdir: Path) -> str:
    objective = clean(pack.get("objective"), 400) or "完成这一单"
    deliverable = clean(pack.get("deliverable"), 300) or "把成果写成 out 目录里的真实文件"
    constraints = [clean(item, 120) for item in (pack.get("constraints") or [])][:6]
    acceptance = [clean(json.dumps(item, ensure_ascii=False), 160)
                  for item in (pack.get("acceptance") or []) if isinstance(item, dict)][:6]
    feedback = clean(pack.get("feedback"), 400)
    lines = [
        "You are a worker agent executing one assigned job.",
        f"Working directory: {workdir}",
        f"Write every deliverable inside this directory and nowhere else: {outdir}",
        f"Objective: {objective}",
        f"Deliverable: {deliverable}",
    ]
    if constraints:
        lines.append("Constraints:")
        lines.extend(f"- {item}" for item in constraints)
    if acceptance:
        lines.append("Acceptance checks:")
        lines.extend(f"- {item}" for item in acceptance)
    if feedback:
        lines.append(f"Feedback on the previous attempt: {feedback}")
    lines.extend([
        "Do the job now. Create the deliverable as a real file under the output directory.",
        "Use your tools to inspect and write files; do not only describe the work.",
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
        # A retry reuses the same commission workspace. An existing deliverable
        # from the first attempt is still the deliverable; filtering by mtime
        # would turn a retry into a false "produced no file".
        found.append(path)
    return sorted(found)


def preview(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:200].replace("\ufffd", "")
    except OSError:
        text = ""
    return {"kind": "file", "ref": str(path.resolve()), "preview": text}


def _terminate_tree(process: subprocess.Popen) -> None:
    """Kill the whole tree: the launcher is cmd.exe / Electron and its child
    keeps the stdout pipe open, so killing only the direct child hangs the read."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    process.kill()


class Translation:
    """One pass of DSH's `--json` stream to PROTOCOL §22 events.

    `step_end.usage` is real per-step usage. DSH reports no first-token time, so
    `ttft_ms` stays 0 rather than reusing the step duration as an invented ttft.
    """

    def __init__(self, model: str):
        self.model = model
        self.final_text = ""
        self.saw_final = False
        self.turn_error = ""
        self.stream_error = ""
        self._progress_seq = 0
        self._step_started: dict[tuple, float] = {}

    def on_event(self, event: dict) -> list[dict]:
        kind = event.get("type")
        if kind == "session":
            return [self._progress(1, f"dsh session {clean(event.get('sessionId'), 80)}")]
        if kind == "status":
            return self._status(event)
        if kind == "tool_call":
            tool = clean(event.get("tool"), 40)
            payload = event.get("input")
            try:
                digest = hashlib.sha256(json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, default=str
                ).encode("utf-8")).hexdigest()[:8]
            except (TypeError, ValueError):
                digest = ""
            # Include the call's arguments in the text. `cli_subproc` keeps only
            # step_idx/text for progress events, and the spinning guard hashes
            # that text: three `pwsh` calls with different commands must not look
            # like three identical steps.
            out = [self._progress(2, f"tool {tool} {digest}".strip())]
            if os.environ.get("AQI_DSH_DEBUG") == "1":
                out.insert(0, {"type": "log",
                               "text": "RAW " + json.dumps(event, ensure_ascii=False)[:600]})
            return out
        if kind == "tool_result":
            state = clean(event.get("status"), 20) or "completed"
            text = clean(event.get("result"), 160)
            return [{"type": "log", "text": clean(f"tool result {state}: {text}", 400)}]
        if kind == "text":
            return [{"type": "log", "text": clean(event.get("text"), 200)}]
        if kind == "thinking":
            return [{"type": "log", "text": "thinking"}]
        if kind == "final":
            self.saw_final = True
            self.final_text = str(event.get("text") or "")
            return []
        if kind == "error":
            self.stream_error = clean(event.get("message"), 240) or "dsh reported an error"
            return []
        return [{"type": "log", "text": clean(json.dumps(event, ensure_ascii=False), 400)}]

    def _status(self, event: dict) -> list[dict]:
        phase = event.get("phase")
        key = (event.get("turn"), event.get("step"))
        if phase == "step_start":
            self._progress_seq += 1
            self._step_started[key] = time.time()
            # DSH can report the same `step` number for several tool calls in one
            # turn. A bare "step 2" repeated three times looks like a spinning
            # loop to the dispatcher; the sequence keeps progress distinct while
            # genuine repeated tool calls still share a signature.
            return [self._progress(2, f"step {self._progress_seq}: turn {key[0]} step {key[1]}")]
        if phase == "step_end":
            started = self._step_started.pop(key, None)
            duration = int((time.time() - started) * 1000) if started else 0
            usage = event.get("usage")
            if not isinstance(usage, dict):
                return []
            return [{
                "type": "token",
                "model": self.model,
                "prompt_tokens": max(0, int(usage.get("inputTokens") or 0)),
                "completion_tokens": max(0, int(usage.get("outputTokens") or 0)),
                "ttft_ms": 0,
                "duration_ms": duration,
                "status_code": 0,
            }]
        if phase == "turn_end":
            reason = event.get("reason")
            if isinstance(reason, dict) and reason.get("kind") != "completed":
                error = reason.get("error") if isinstance(reason.get("error"), dict) else {}
                code = clean(error.get("code"), 80)
                message = clean(error.get("message"), 240)
                self.turn_error = (f"{code}: {message}" if code and message
                                   else message or code
                                   or f"dsh turn ended: {clean(reason.get('kind'), 40)}")
        return []

    @staticmethod
    def _progress(step_idx: int, text: str) -> dict:
        return {"type": "progress", "step_idx": step_idx, "text": text}


def run(pack: dict, workdir: Path, outdir: Path) -> int:
    launcher = os.environ.get("AQI_DSH_LAUNCHER")
    exe = find_exe()
    cli = find_cli(exe)
    if not launcher and not exe.is_file():
        # The CLI lives inside app.asar, which Electron resolves and the OS
        # cannot; only the executable is checkable from here.
        emit({"type": "error", "text": f"dsh executable not found: {exe}"})
        return 0
    if not gateway_ready():
        emit({"type": "error",
              "text": f"model gateway is not running at {gateway_url()}; "
                      "start it with scripts/run.py or uvicorn server.gateway.anthropic_proxy:app"})
        return 0

    outdir.mkdir(parents=True, exist_ok=True)
    # TMP/TEMP stay wherever the platform put them: DSH refuses a Windows ACL
    # temp root inside the workspace and then every shell tool call fails.
    env = dict(os.environ)
    env["ELECTRON_RUN_AS_NODE"] = "1"
    env["DEEPSEEK_BASE_URL"] = gateway_url()
    if not env.get("DEEPSEEK_API_KEY"):
        env["DEEPSEEK_API_KEY"] = "local"
    env["PYTHONIOENCODING"] = "utf-8"
    # The packet is delivered to this shim; it need not sit in DSH's env.
    env.pop("AQI_BRIEFING_PACKET", None)

    timeout = float(os.environ.get("AQI_DSH_TIMEOUT", "300"))
    cmd = build_command(exe, cli, prompt_for(pack, workdir, outdir))
    started = time.time()
    try:
        process = subprocess.Popen(
            cmd, cwd=str(workdir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        emit({"type": "error", "text": f"dsh failed to start: {type(exc).__name__}"})
        return 0

    timed_out = threading.Event()

    def on_timeout() -> None:
        timed_out.set()
        _terminate_tree(process)

    # The reader blocks on a quiet child, so the deadline needs its own thread;
    # checking the clock between lines would never fire on a hung run.
    watchdog = threading.Timer(timeout, on_timeout)
    watchdog.daemon = True
    watchdog.start()
    translation = Translation(os.environ.get("AQI_DSH_MODEL", "deepseek-flash"))
    try:
        assert process.stdout is not None
        for line in process.stdout:
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
            for translated in translation.on_event(event):
                emit(translated)
        code = process.wait()
    finally:
        watchdog.cancel()

    if timed_out.is_set() and code != 0:
        emit({"type": "error", "text": f"dsh timed out after {timeout:.0f}s"})
        return 0
    if translation.stream_error:
        emit({"type": "error", "text": translation.stream_error})
        return 0
    if translation.turn_error:
        emit({"type": "error", "text": translation.turn_error})
        return 0
    if code != 0:
        emit({"type": "error", "text": f"dsh exited {code}"})
        return 0
    if not translation.saw_final:
        emit({"type": "error", "text": "dsh finished without a final event"})
        return 0
    final_text = clean(translation.final_text, 400)
    if final_text.upper().startswith("FAILED:"):
        emit({"type": "error", "text": final_text[:240]})
        return 0
    created = files_created(outdir, started)
    if not created:
        emit({"type": "error", "text": "dsh produced no file in the output directory"})
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
