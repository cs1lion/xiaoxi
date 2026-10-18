"""Claude Code worker shim: run one commission through the local Anthropic gateway.

The dispatcher speaks the five-event line protocol (PROTOCOL §22). Claude Code
speaks its own `stream-json`; this shim translates one into the other and builds
the delivery card the evaluator expects.

Claude runs with its own tools (Read/Write/Edit/Bash/...), so the deliverable is
a real file written by Claude — not text the shim saved on its behalf.

Environment:
  AQI_BRIEFING_PACKET    briefing packet JSON (set by cli_subproc)
  AQI_COMMISSION_ID      commission id (set by cli_subproc)
  AQI_WORKDIR            workspace Claude may write to (default: cwd)
  AQI_OUTDIR             where deliverables must land (default: <workdir>/out)
  AQI_GATEWAY_URL        Anthropic gateway (default: http://127.0.0.1:15722)
  AQI_ANTHROPIC_URL      alias for AQI_GATEWAY_URL
  AQI_CLAUDE_EXE         claude executable (default: PATH / user install)
  AQI_CLAUDE_CONFIG_DIR  isolated Claude config dir (default: <workdir>/.claude-config)
  AQI_CLAUDE_MODEL       model name Claude asks for (gateway maps it upstream)
  AQI_CLAUDE_TIMEOUT     seconds (default: 300)
  AQI_CLAUDE_ALLOWED_TOOLS  comma list of built-in tools
"""
from __future__ import annotations

import json
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SKIP = {".claude-config", ".claude-home", "tmp", ".git"}
DEFAULT_TOOLS = "Read,Write,Edit,Bash,PowerShell,Glob,Grep"
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


def gateway_url() -> str:
    return (os.environ.get("AQI_GATEWAY_URL")
            or os.environ.get("AQI_ANTHROPIC_URL")
            or "http://127.0.0.1:15722").rstrip("/")


def gateway_ready() -> bool:
    if os.environ.get("AQI_CLAUDE_SKIP_GATEWAY_CHECK") == "1":
        return True
    try:
        with urllib.request.urlopen(gateway_url() + "/health", timeout=3) as response:
            return response.status == 200
    except Exception:
        return False


def resolve_workdir() -> Path:
    """Use an explicit workspace, or allocate a per-commission one.

    A production roster entry runs from the repo root. Without this, Claude would
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


def find_claude() -> str:
    override = os.environ.get("AQI_CLAUDE_EXE")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    candidate = Path(os.environ.get("USERPROFILE") or "") / ".local" / "bin" / "claude.exe"
    return str(candidate) if candidate.is_file() else ""


def write_config(config_dir: Path) -> Path:
    """Point an isolated Claude config at our gateway, never at the user's config."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "settings.json").write_text(json.dumps({
        "env": {
            "ANTHROPIC_BASE_URL": gateway_url(),
            "ANTHROPIC_AUTH_TOKEN": "local",
            "ANTHROPIC_API_KEY": "local",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        "permissions": {
            "allow": ["Read", "Write", "Edit", "Bash", "PowerShell", "Glob", "Grep"],
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_dir


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
        "Use your tools to inspect and write files; do not only describe the work.",
        "Do not ask questions. Do not modify files outside the working directory.",
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
    exe = find_claude()
    if not exe or not Path(exe).is_file():
        emit({"type": "error", "text": f"claude executable not found: {exe or '(PATH)'}"})
        return 0
    if not gateway_ready():
        emit({"type": "error",
              "text": f"Anthropic gateway is not running at {gateway_url()}; "
                      "start it with scripts/run.py or uvicorn server.gateway.anthropic_proxy:app"})
        return 0

    config_dir = Path(os.environ.get("AQI_CLAUDE_CONFIG_DIR")
                      or (workdir / ".claude-config")).resolve()
    write_config(config_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    (workdir / "tmp").mkdir(exist_ok=True)

    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    env["ANTHROPIC_BASE_URL"] = gateway_url()
    env["ANTHROPIC_AUTH_TOKEN"] = "local"
    env["ANTHROPIC_API_KEY"] = "local"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["TMP"] = str(workdir / "tmp")
    env["TEMP"] = str(workdir / "tmp")
    env["PYTHONIOENCODING"] = "utf-8"
    # The packet is delivered to this shim; it need not sit in Claude's env.
    env.pop("AQI_BRIEFING_PACKET", None)

    model = os.environ.get("AQI_CLAUDE_MODEL", "claude-opus-5-5")
    allowed = os.environ.get("AQI_CLAUDE_ALLOWED_TOOLS", DEFAULT_TOOLS)
    timeout = float(os.environ.get("AQI_CLAUDE_TIMEOUT", "300"))
    cmd = [
        exe, "-p", prompt_for(pack, workdir, outdir),
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--allowedTools", allowed,
    ]
    started = time.time()
    emit({"type": "progress", "step_idx": 1, "text": "claude worker started"})
    try:
        process = subprocess.Popen(
            cmd, cwd=str(workdir), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        emit({"type": "error", "text": f"claude failed to start: {type(exc).__name__}"})
        return 0

    deadline = started + timeout
    final_text = ""
    usage: dict = {}
    result_error = ""
    token_sent = False
    assert process.stdout is not None
    for line in process.stdout:
        if time.time() > deadline:
            process.kill()
            emit({"type": "error", "text": f"claude timed out after {timeout:.0f}s"})
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
        if kind == "system" and event.get("subtype") == "init":
            emit({"type": "progress", "step_idx": 2,
                  "text": f"claude ready: {clean(event.get('model'), 60)}"})
        elif kind == "assistant":
            message = event.get("message") or {}
            for block in (message.get("content") or []):
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    final_text = clean(block.get("text"), 400)
                    emit({"type": "log", "text": final_text[:200]})
                elif block.get("type") == "tool_use":
                    # 只报工具名时，连续三次同名工具（完全正常的活）会撞上鬼打墙
                    # 熔断，把好单拦下来。cli_subproc 的 progress 只留
                    # step_idx/text，而熔断器签名的正是这段 text，所以把完整参数
                    # 的摘要放进去——与 dsh_worker 同一套办法。参数原文不能直接
                    # 塞：同一督办目录下前几十字全是相同的绝对路径前缀，截断恰好
                    # 把区分彼此的文件名砍掉。
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
            usage = event.get("usage") or {}
            final_text = clean(event.get("result"), 400) or final_text
            duration = int(event.get("duration_ms") or (time.time() - started) * 1000)
            ttft = int(event.get("ttft_ms") or 0)
            emit({
                "type": "token",
                "model": model,
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "ttft_ms": ttft or duration,
                "duration_ms": duration,
                "status_code": 0,
            })
            token_sent = True
            if event.get("is_error"):
                result_error = clean(event.get("result") or event.get("subtype"), 200) \
                    or "claude reported an error"
        elif kind == "error":
            result_error = clean(event.get("message") or event.get("error"), 240) or "claude error"

    code = process.wait()
    if code != 0:
        emit({"type": "error", "text": f"claude exited {code}"})
        return 0
    if result_error:
        emit({"type": "error", "text": result_error})
        return 0
    if not token_sent:
        emit({"type": "log", "text": "claude finished without a result usage record"})

    created = files_created(outdir, started)
    if not created and final_text.strip():
        # Tool calls are not translated by the gateway yet. When Claude could not
        # write a file, keep its answer as the deliverable instead of failing.
        answer = outdir / "answer.txt"
        answer.write_text(final_text.strip() + "\n", encoding="utf-8")
        created = [answer]
    if not created:
        emit({"type": "error", "text": "claude produced no file in the output directory"})
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
