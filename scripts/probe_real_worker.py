"""Live probe: drive one real commission through a real coding agent.

This is not a regression test. It costs tokens and needs the local Codex CLI plus
the local gateway. Run it by hand:

    .\\.venv\\Scripts\\python.exe scripts\\probe_real_worker.py

It proves four things the mock tests cannot:
  1. the dispatcher can hand a briefing packet to a real CLI agent;
  2. the agent writes a real file in the commission workspace;
  3. its JSONL stream maps onto the five adapter events;
  4. the delivery card passes L1 and the commission reaches ready_to_report.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SHIM = ROOT / "scripts" / "worker_shims" / "codex_worker.py"
CLAUDE_SHIM = ROOT / "scripts" / "worker_shims" / "claude_worker.py"


def build_roster(workdir: Path) -> dict:
    roster = load_roster()
    # A full CLI agent is not a bare chat call: Codex injects ~29k tokens of its own
    # instructions before it reads the packet, and reading input files pushes a real
    # run past 40k. Use a budget that leaves headroom and *check it for real*.
    roster.setdefault("dispatch", {})["max_prompt_tokens"] = 80000
    roster.setdefault("agents", []).append({
        "id": "real_codex",
        "name": "真机 Codex",
        "adapter": "cli_subproc",
        "executable": str(PYTHON),
        "args": [str(SHIM)],
        "cwd": str(workdir),
        "timeout": 300,
        "env": {"AQI_WORKDIR": str(workdir),
                "AQI_CODEX_SANDBOX": "danger-full-access",
                "AQI_CODEX_EFFORT": "low"},
        "summary": "在指定工作目录完成测试任务，交付真实文件",
        "strengths": ["测试", "写入", "文件", "note"],
        "weaknesses": [],
        "cost_tier": "medium",
        "max_concurrent": 1,
        # `token_budget` is the point: without it the audit legitimately reports
        # budget_checked=false and a 29k-token run sails through unchecked.
        "eval_commands": ["required_fields", "artifacts_exist", "token_budget"],
    })
    # Text-only agent: the gateway translates Anthropic Messages, but it does not
    # translate tool calls yet. The shim writes the answer itself.
    roster["agents"].append({
        "id": "claude_text",
        "name": "真机 Claude",
        "adapter": "cli_subproc",
        "executable": str(PYTHON),
        "args": [str(CLAUDE_SHIM)],
        "cwd": str(workdir),
        "timeout": 240,
        "env": {"AQI_WORKDIR": str(workdir),
                "AQI_ANTHROPIC_URL": os.environ.get("AQI_ANTHROPIC_URL",
                                                    "http://127.0.0.1:15723"),
                "AQI_GATEWAY_URL": os.environ.get("AQI_ANTHROPIC_URL",
                                                  "http://127.0.0.1:15723")},
        "summary": "用一句中文总结给定内容，交付文本结论",
        "strengths": ["总结", "概括", "回答", "文本"],
        "weaknesses": [],
        "cost_tier": "cheap",
        "max_concurrent": 1,
        "eval_commands": ["required_fields", "artifacts_exist"],
    })
    return roster


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    workdir = (ROOT / "tmp" / f"real-worker-{stamp}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AQI_GATEWAY_URL", "http://127.0.0.1:15721/v1")
    os.environ.setdefault("AQI_GATEWAY_MODEL", "step-5-preview")
    service = ExecutiveService(workdir / "state", roster=build_roster(workdir))
    report = {"workdir": str(workdir), "started": time.time()}
    try:
        result = await service.delegate("测试：请在 note.txt 里写入 OK")
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
            "out_files": [str(p.relative_to(outdir)) for p in outdir.rglob("*") if p.is_file()] if outdir.exists() else [],
            "note_exists": note.is_file(),
            "note_text": note.read_text(encoding="utf-8", errors="replace") if note.is_file() else "",
            "elapsed_ms": int((time.time() - report["started"]) * 1000),
        })
    finally:
        service.close()
    (workdir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (report.get("status") == "ready_to_report"
          and report.get("budget_checked") is True
          and report.get("note_exists")
          and "OK" in report.get("note_text", ""))
    print("VERIFIED" if ok else "NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
