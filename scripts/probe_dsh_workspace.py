"""Drive the local DSH through durable in a fresh workspace.

This is intentionally isolated from data/commissions: every task gets a
per-commission tree under tmp/dsh-workspace-<stamp>/. It uses the real gateway
and real DSH, so it spends tokens.

    python scripts/probe_dsh_workspace.py --tasks 3 --keep
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402,F401  (loads .env)
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

TASKS = [
    "在 out 目录创建 hello.txt，内容写 hello world。完成后交差。",
    "在 out 目录创建 titles.md，写三版不超过二十字的标题，素材：深夜写代码的专注、通勤路上的播客。",
    "在 out 目录创建 notes.md，用两行话总结：整理房间先分类，再决定丢弃。",
]


def dsh_worker(workspace_root: Path) -> dict:
    roster = load_roster()
    entry = next((item for item in roster.get("agents") or []
                  if isinstance(item, dict) and item.get("id") == "dsh"), None)
    if entry is None:
        raise SystemExit("花名册里没有 dsh")
    worker = {key: value for key, value in entry.items() if key != "id"}
    worker["id"] = "dsh"
    worker["adapter"] = "durable"
    worker["workspace_root"] = str(workspace_root)
    return worker


async def run(args) -> int:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace_root = ROOT / "tmp" / f"dsh-workspace-{stamp}"
    workspace_root.mkdir(parents=True, exist_ok=True)
    state_dir = Path(tempfile.mkdtemp(prefix="dsh-state-"))
    sidecar_dir = Path(tempfile.mkdtemp(prefix="dsh-sidecar-"))
    roster = load_roster()
    roster["agents"] = [dsh_worker(workspace_root)]
    service = ExecutiveService(state_dir, roster=roster)
    supervisor = DurableSupervisor(directory=sidecar_dir, startup_timeout=40.0,
                                   extra_env={"XIAOXI_CONTEXT_WINDOW": "32768",
                                              "XIAOXI_MAX_TOKENS": "8000"})
    results = []
    try:
        await supervisor.start()
        service.bind_durable({"endpoint": supervisor.current_endpoint(),
                              "token": supervisor.token,
                              "workspace_root": str(workspace_root)},
                             endpoint_provider=supervisor.current_endpoint)
        for index, prompt in enumerate(TASKS[:args.tasks], 1):
            print(f"[task {index}] {prompt}", flush=True)
            outcome = await service.delegate(prompt)
            item = service.commission(outcome["commission_id"])
            files = []
            for path in workspace_root.rglob("*"):
                if path.is_file():
                    files.append({"path": str(path), "size": path.stat().st_size})
            result = {
                "task": index,
                "commission_id": outcome["commission_id"],
                "status": item.get("status"),
                "agent_id": item.get("agent_id"),
                "delivery": (item.get("delivery") or {}).get("summary"),
                "blocked_question": item.get("blocked_question"),
                "audit": item.get("audit") or {},
                "files": files,
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            if item.get("status") != "ready_to_report":
                break
    finally:
        await supervisor.stop()
        service.close()
    print(json.dumps({"workspace_root": str(workspace_root), "results": results},
                     ensure_ascii=False, indent=2))
    if not args.keep:
        import shutil
        shutil.rmtree(workspace_root, ignore_errors=True)
    return 0 if results and all(item["status"] == "ready_to_report" for item in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=3)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
