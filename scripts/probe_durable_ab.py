"""同题 A/B：同一提示分别走 cli_subproc（真机 Claude）与 durable（pi-durable）。

    python scripts/probe_durable_ab.py [--repeat N]

不是回归：会调用真实模型与本地网关（15722），预算固定。用来给 Phase 2 的
验收提供一小组真实读数。

两侧共用**同一份密件**：durable 的 Worker 条目由 claude 的花名册条目派生，
只把 adapter 换掉。提示词里不出现任何引擎私有的工具名（"调用 submit_artifact"
是 durable 侧的话，真机 Claude 没有这个工具，照着做只会空转到超时）。要比的是
执行引擎，不是提示词措辞。初审命令、预算、约束两边逐项相同。

    python scripts/probe_durable_ab.py --repeat 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
from server.assistant.executive import ExecutiveService  # noqa: E402

# 引擎中立：只说做什么，不说用什么交。
PROMPT = ("素材：1) 深夜写代码的专注；2) 通勤路上的播客；3) 周末收拾房间。"
          "把它们理成三版不超过二十字的标题。")

BASELINE_ID = "claude"


def durable_worker_from(baseline: dict[str, any]) -> dict[str, any]:
    """同一份密件：只换 adapter，其余（职责/短板/预算/初审命令）原样保留。"""
    worker = {key: value for key, value in baseline.items()
              if key not in ("adapter", "executable", "args", "env", "id", "name")}
    worker.update({"id": "durable-ab", "name": "durable 对照", "adapter": "durable"})
    return worker


def point_sidecar_at(baseline: dict[str, any]) -> str:
    """让 durable 侧用上与基线相同的模型。

    Phase 2 的验收口径是"同一模型"。sidecar 的模型来自进程环境的
    LLM_BASE_URL / LLM_MODEL（modelsFromEnv），默认落在 .env 的
    step-3.7-flash 上——那比花名册里 claude 用的模型小一辈，比出来的差异
    分不清是引擎还是模型。监督器用 dict(os.environ) 起子进程，所以在起
    sidecar 之前改这里即可。返回实际用的模型名，供汇报里写明。
    """
    env = baseline.get("env") or {}
    model = str(env.get("AQI_CLAUDE_MODEL") or "").strip()
    gateway = str(env.get("AQI_GATEWAY_URL") or "").strip().rstrip("/")
    if not model or not gateway:
        return str(os.environ.get("LLM_MODEL") or "")
    # 网关的 /v1/chat/completions 是 OpenAI Chat 出口，sidecar 说的正是这个方言。
    os.environ["LLM_BASE_URL"] = f"{gateway}/v1"
    os.environ["LLM_MODEL"] = model
    return model


async def run_one(service: ExecutiveService, label: str, index: int) -> dict:
    # 走生产入口 delegate()：它自己驱动到终态，初审打回后按 MAX_RETRY 自动重做。
    # 探针若只调一次 run_commission，就会把"打回重做中"误读成失败。
    started = time.monotonic()
    outcome = await service.delegate(PROMPT)
    elapsed = time.monotonic() - started
    item = service.commission(outcome["commission_id"])
    audit = item.get("audit") or {}
    findings = [f if isinstance(f, dict) else {"raw": f} for f in (audit.get("findings") or [])]
    if item["status"] != "ready_to_report" or findings:
        print(f"  [诊断] {label} #{index} status={item['status']} "
              f"retry={item.get('retry_count')}")
        for event in reversed(service.commission_events(outcome["commission_id"], 40)):
            payload = event.get("payload") or {}
            detail = payload.get("text") or payload.get("signature") or payload
            print(f"         {str(event.get('kind')):10s} {str(detail)[:170]}")
        for finding in findings:
            print(f"         finding {json.dumps(finding, ensure_ascii=False)[:300]}")
    rules = [str(f.get("rule") or f.get("rule_id") or f.get("raw") or "") for f in findings]
    # token_budget 是花名册里的预算闸，不是交付质量：真机 Claude 带工具跑一轮
    # 就要约 10 万 prompt token，而 max_prompt_tokens 是 8 万，且 commission_usage
    # 把一单所有重试的用量求和——越被打回越必然超。把它和真正的质量判据分开报，
    # 否则 A/B 回答的是"谁更省 token"，不是"谁能交差"。
    quality = [rule for rule in rules if rule != "prompt_token_bloat"]
    return {
        "label": label,
        "index": index,
        "status": item["status"],
        "summary": (item.get("delivery") or {}).get("summary"),
        "question": item.get("blocked_question"),
        "findings": rules,
        "budget_blocked": bool(rules) and not quality,
        "retry": int(item.get("retry_count") or 0),
        "elapsed_s": round(elapsed, 1),
        "usage": service.commission_usage(outcome["commission_id"]),
    }


async def durable_side(repeat: int) -> list[dict] | None:
    base = load_roster()
    baseline = [a for a in base.get("agents", []) if a.get("id") == BASELINE_ID]
    if not baseline:
        return None
    base["agents"] = [durable_worker_from(baseline[0])]
    model = point_sidecar_at(baseline[0])  # 必须在起 sidecar 之前：子进程继承这份环境
    directory = Path(tempfile.mkdtemp(prefix="ab-durable-"))
    supervisor = DurableSupervisor(directory=directory / "sidecar", startup_timeout=40.0)
    await supervisor.start()
    try:
        service = ExecutiveService(directory / "state", roster=base)
        service.bind_durable(supervisor.config())
        try:
            rows = [await run_one(service, "durable", i + 1) for i in range(repeat)]
            for row in rows:
                row["model"] = model
            return rows
        finally:
            service.close()
    finally:
        await supervisor.stop()


async def claude_side(repeat: int) -> list[dict] | None:
    base = load_roster()
    claude = [agent for agent in base.get("agents", []) if agent.get("id") == BASELINE_ID]
    if not claude:
        return None
    base["agents"] = claude
    service = ExecutiveService(Path(tempfile.mkdtemp(prefix="ab-claude-")), roster=base)
    try:
        rows = [await run_one(service, "cli_subproc", i + 1) for i in range(repeat)]
        for row in rows:
            row["model"] = str((claude[0].get("env") or {}).get("AQI_CLAUDE_MODEL") or "")
        return rows
    finally:
        service.close()


def report(rows: list[dict]) -> None:
    for row in rows:
        tokens = row["usage"].get("total_tokens")
        print(f"{row['label']:12s} #{row['index']} status={row['status']:16s} "
              f"{row['elapsed_s']:>6}s tokens={tokens} calls={row['usage'].get('calls')} "
              f"retry={row.get('retry')}")
        print(f"             summary={row['summary']}")
        if row["question"]:
            print(f"             question={row['question'][:120]}")
        if row["findings"]:
            print(f"             findings={row['findings']}")
    print()
    for label in sorted({row["label"] for row in rows}):
        side = [row for row in rows if row["label"] == label]
        passed = [row for row in side if row["status"] == "ready_to_report"]
        # 排除预算闸后的通过：交差了就算，哪怕预算判据把它推回去。
        quality_passed = [row for row in side
                          if row["status"] == "ready_to_report" or row.get("budget_blocked")]
        budget_only = [row for row in side if row.get("budget_blocked")]
        times = [row["elapsed_s"] for row in passed]
        tokens = [row["usage"].get("total_tokens") or 0 for row in passed]
        avg_time = round(sum(times) / len(times), 1) if times else None
        avg_tokens = round(sum(tokens) / len(tokens)) if tokens else 0
        print(f"{label:12s} L1 {len(passed)}/{len(side)}  "
              f"排除预算闸 {len(quality_passed)}/{len(side)}"
              f"（其中 {len(budget_only)} 单只死于预算）  "
              f"均时 {avg_time}s  均 token {avg_tokens}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()
    if not os.environ.get("LLM_API_KEY"):
        print("SKIP: 没有 LLM_API_KEY")
        return 0
    rows: list[dict] = []
    durable = await durable_side(args.repeat)
    if durable is not None:
        rows.extend(durable)
    claude = await claude_side(args.repeat)
    if claude is not None:
        rows.extend(claude)
    report(rows)
    # 同模型是这次 A/B 有效的前提，把它和两侧的模型一起说出来。
    models = sorted({str(row.get("model") or "") for row in rows if row.get("model")})
    print(f"两侧模型：{models or ['（未记录）']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
