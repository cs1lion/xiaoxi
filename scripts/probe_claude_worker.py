"""Live probe: drive one real commission through Claude Code + the local gateway.

    .\\.venv\\Scripts\\python.exe scripts\\probe_claude_worker.py

Prerequisites:
  1. the Anthropic gateway is listening on 127.0.0.1:15722
     (`.venv\\Scripts\\python.exe -m uvicorn server.gateway.anthropic_proxy:app --port 15722`)
  2. `claude` is installed and can run headless.

This is not a regression test. It costs tokens. It proves:
  1. the dispatcher hands a packet to a real Claude Code subprocess;
  2. the gateway translates Anthropic Messages, including tool calls;
  3. Claude writes a real file under out/ using its own Write/Bash tool;
  4. the JSONL maps onto the five adapter events and the card passes L1.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SHIM = ROOT / "scripts" / "worker_shims" / "claude_worker.py"
GATEWAY = os.environ.get("AQI_GATEWAY_URL", "http://127.0.0.1:15722").rstrip("/")


def gateway_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{GATEWAY}/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def build_roster(workdir: Path) -> dict:
    roster = load_roster()
    # Claude Code carries ~35k tokens of its own system prompt/tool catalog.
    roster.setdefault("dispatch", {})["max_prompt_tokens"] = 80000
    roster.setdefault("agents", []).append({
        "id": "real_claude",
        "name": "真机 Claude Code",
        "adapter": "cli_subproc",
        "executable": str(PYTHON),
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 420,
        "env": {
            "AQI_WORKDIR": str(workdir),
            "AQI_OUTDIR": str(workdir / "out"),
            "AQI_CLAUDE_CONFIG_DIR": str(workdir / ".claude-config"),
            "AQI_GATEWAY_URL": GATEWAY,
            "AQI_CLAUDE_MODEL": "claude-opus-5-5",
            "AQI_CLAUDE_TIMEOUT": "360",
        },
        "summary": "在临时目录里用真实 Claude Code 写文件、跑命令",
        "strengths": ["测试", "写入", "文件", "note"],
        "weaknesses": [],
        "cost_tier": "high",
        "max_concurrent": 1,
        "eval_commands": ["required_fields", "artifacts_exist", "token_budget"],
    })
    return roster


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not gateway_ready():
        print(f"NOT VERIFIED: gateway is not healthy at {GATEWAY}")
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    workdir = (ROOT / "tmp" / f"real-claude-{stamp}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    service = ExecutiveService(workdir / "state", roster=build_roster(workdir))
    report = {"workdir": str(workdir), "gateway": GATEWAY, "started": time.time()}
    try:
        result = await service.delegate("测试：请在 out 目录创建 note.txt，内容只有一行 OK")
        commission = service.commission(result["commission_id"])
        outdir = workdir / "out"
        note = outdir / "note.txt"
        usage = (commission.get("audit") or {}).get("usage") or {}
        report.update({
            "status": commission["status"],
            "audit": commission.get("audit"),
            "budget_checked": usage.get("budget_checked"),
            "delivery": commission.get("delivery"),
            "blocked_question": commission.get("blocked_question"),
            "out_files": [str(p.relative_to(outdir)) for p in outdir.rglob("*")
                          if p.is_file()] if outdir.exists() else [],
            "note_exists": note.is_file(),
            "note_text": note.read_text(encoding="utf-8", errors="replace") if note.is_file() else "",
            "elapsed_ms": int((time.time() - report["started"]) * 1000),
        })
    finally:
        service.close()
    (workdir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (report.get("status") == "ready_to_report"
          and report.get("budget_checked") is True
          and report.get("note_exists")
          and "OK" in report.get("note_text", ""))
    print("VERIFIED" if ok else "NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
