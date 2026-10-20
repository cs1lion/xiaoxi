"""真机接管探针：durable sidecar 拉起本地 DeepSeek Harness 干一单。

    python scripts/probe_takeover_live.py

不是回归：会调真实模型与本地网关（15722），花 token。用来回答一个问题——
**durable 能不能真的接管本机执行体**，而不只是跑自己的模型。

    python scripts/probe_takeover_live.py --dry-run     # 只看计划，不跑
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402,F401  (import 即加载 .env)
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402

PROMPT = ("在 out 目录建一个 titles.md，写三版不超过二十字的标题，素材："
          "1) 深夜写代码的专注；2) 通勤路上的播客；3) 周末收拾房间。")

WORKER_ID = "dsh"


def plan() -> dict:
    roster = load_roster()
    entry = next((a for a in roster.get("agents", []) if a.get("id") == WORKER_ID), None)
    if entry is None:
        raise SystemExit(f"花名册里没有 {WORKER_ID}")
    executable = str(entry.get("executable") or "python")
    # 花名册里的 args 是相对仓库根写的（scripts/worker_shims/x.py），而 sidecar
    # 的子进程 cwd 是本单的沙箱工作目录。原样传过去，Python 打不开脚本、把
    # 错误写到 stderr、退出 0，整单只剩一句"没有交差"——排查时看不出是路径问题。
    # 生产路径（DurableAdapter._command）已经做这件事，探针必须同源。
    args = [str(ROOT / item) if (ROOT / item).exists() else str(item)
            for item in (entry.get("args") or [])]
    env = entry.get("env") or {}
    if not isinstance(env, dict):
        env = {}
    return {"executable": executable, "args": args,
            "worker_env": {str(k): str(v) for k, v in env.items()}, "entry": entry}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--gateway", default="",
                        help="覆盖花名册里的 DEEPSEEK_BASE_URL（换一个网关/上游做对照）")
    args = parser.parse_args()

    spec = plan()
    if args.gateway:
        spec["worker_env"]["DEEPSEEK_BASE_URL"] = args.gateway
    command = [spec["executable"], *spec["args"]]
    print(f"接管命令：{' '.join(command)}")
    if args.dry_run:
        print("DRY-RUN：不真跑。")
        return 0
    if not os.environ.get("LLM_API_KEY"):
        print("SKIP: 没有 LLM_API_KEY")
        return 0

    directory = Path(tempfile.mkdtemp(prefix="takeover-live-"))
    workspace = directory / "work"
    workspace.mkdir(parents=True, exist_ok=True)
    supervisor = DurableSupervisor(directory=directory / "sidecar", startup_timeout=40.0)
    await supervisor.start()
    try:
        import httpx
        headers = {"content-type": "application/json",
                   "authorization": f"Bearer {supervisor.token}"}
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=args.timeout) as client:
            created = await client.post(
                f"{supervisor.endpoint}/v1/runs", headers=headers,
                json={"commission_id": "c_takeover_live", "agent_id": WORKER_ID,
                      "packet": {"objective": PROMPT, "deliverable": "out/titles.md"},
                      "workspace": str(workspace), "command": command,
                      "worker_env": spec["worker_env"]})
            if created.status_code >= 400:
                print(f"内核拒绝接单：HTTP {created.status_code} {created.text[:200]}")
                return 1
            ref = created.json()
            task_id = int(ref["task_id"])
            while True:
                view = (await client.get(f"{supervisor.endpoint}/v1/runs/{task_id}",
                                         headers=headers)).json()
                # readRun 报的是任务状态；终态统一是 terminal，成败看 outcome。
                if view.get("status") == "terminal":
                    break
                if time.monotonic() - started > args.timeout:
                    print("超时")
                    return 1
                await asyncio.sleep(2)
        elapsed = time.monotonic() - started
        events = view.get("events") or []
        print(f"status={view.get('status')}  {elapsed:.1f}s  事件 {len(events)} 条")
        for event in events:
            kind = str(event.get("type"))
            detail = event.get("text") or (event.get("artifact") or {}).get("summary") or ""
            print(f"  {kind:9s} {str(detail)[:150]}")
        outcome = view.get("outcome") or {}
        artifact = (outcome.get("result") or {}).get("artifact") or {}
        verdict = str(outcome.get("status") or "")
        print(f"outcome={verdict}")
        print(f"summary={artifact.get('summary')}")
        produced = sorted(str(p) for p in (workspace / "out").rglob("*") if p.is_file())
        print(f"工作目录里的真实文件：{produced}")
        ok = verdict == "completed" and bool(produced)
        print("VERIFIED" if ok else "NOT VERIFIED")
        return 0 if ok else 1
    finally:
        await supervisor.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
