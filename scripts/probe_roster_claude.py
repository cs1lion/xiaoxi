"""Live probe: the production roster selects the real Claude worker.

    .\\.venv\\Scripts\\python.exe scripts\\probe_roster_claude.py

Unlike probe_claude_worker.py (which appends a temporary agent), this uses
config/agents_roster.yaml as shipped. It proves the product path, not just the
adapter path:
  - the dispatcher picks `claude` from the real roster;
  - the worker gets its own workspace under data/commissions/;
  - the gateway + Claude Code write a real file;
  - the audit sees real usage and the commission reaches ready_to_report.
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

GATEWAY = os.environ.get("AQI_GATEWAY_URL", "http://127.0.0.1:15722").rstrip("/")


def gateway_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{GATEWAY}/health", timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if not gateway_ready():
        print(f"NOT VERIFIED: gateway is not healthy at {GATEWAY}")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    workdir = (ROOT / "tmp" / f"roster-claude-{stamp}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    roster = load_roster()
    agent = next((item for item in roster.get("agents", [])
                  if item.get("id") == "claude"), None)
    if agent is None:
        print("NOT VERIFIED: config/agents_roster.yaml has no `claude` agent")
        return 2
    agent.setdefault("env", {})["AQI_WORKSPACE_ROOT"] = str(workdir / "commissions")
    agent["env"]["AQI_GATEWAY_URL"] = GATEWAY

    service = ExecutiveService(workdir / "state", roster=roster)
    report = {"workdir": str(workdir), "gateway": GATEWAY, "started": time.time()}
    try:
        result = await service.delegate("实现：在 out 目录创建 note.txt，内容只有一行 OK")
        commission = service.commission(result["commission_id"])
        artifacts = (commission.get("delivery") or {}).get("artifacts") or []
        refs = [Path(str(item.get("ref"))) for item in artifacts if item.get("kind") == "file"]
        usage = (commission.get("audit") or {}).get("usage") or {}
        report.update({
            "agent_id": commission.get("agent_id"),
            "status": commission["status"],
            "audit": commission.get("audit"),
            "budget_checked": usage.get("budget_checked"),
            "delivery": commission.get("delivery"),
            "blocked_question": commission.get("blocked_question"),
            "artifact_refs": [str(path) for path in refs],
            "artifact_exists": bool(refs) and all(path.is_file() for path in refs),
            "artifact_text": refs[0].read_text(encoding="utf-8", errors="replace") if refs else "",
            "elapsed_ms": int((time.time() - report["started"]) * 1000),
        })
    finally:
        service.close()
    (workdir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (report.get("agent_id") == "claude"
          and report.get("status") == "ready_to_report"
          and report.get("budget_checked") is True
          and report.get("artifact_exists") is True
          and "OK" in report.get("artifact_text", ""))
    print("VERIFIED" if ok else "NOT VERIFIED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
