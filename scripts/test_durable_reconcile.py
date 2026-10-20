"""Periodic durable reconciliation: adopt completed, block missing/failed,
leave running alone. No auto-done.

    python scripts/test_durable_reconcile.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


ROSTER = {
    "selection": {"max_concurrent_default": 2},
    "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2},
    "agents": [
        {"id": "scribe", "name": "笔录官", "adapter": "durable", "summary": "文档",
         "strengths": ["写作"], "cost_tier": "cheap", "max_concurrent": 10},
    ],
}


def running(executive, title: str) -> dict:
    commission = executive.open(title, "对账测试")
    executive.dispatcher.dispatch(commission["id"], agent_id="scribe")
    executive.dispatcher.start(commission["id"])
    return commission


def main() -> int:
    directory = tempfile.mkdtemp(prefix="durable-reconcile-")
    executive = ExecutiveService(directory, roster=ROSTER)
    missing = running(executive, "内核丢失")
    completed = running(executive, "内核完成")
    still_running = running(executive, "还在跑")
    failed_run = running(executive, "内核失败")

    runs = {
        completed["id"]: {"status": "terminal", "outcome": {"status": "completed",
            "result": {"artifact": {"summary": "已完成", "intercepted": "没有",
                                    "artifacts": [{"kind": "note", "ref": "note:n1"}],
                                    "wired": True}}}},
        still_running["id"]: {"status": "running"},
        failed_run["id"]: {"status": "terminal", "outcome": {"status": "failed"}},
    }

    def lookup(commission_id, attempt=0):
        return runs.get(commission_id)

    result = executive.reconcile_durable(lookup)
    print("[durable reconcile]")
    check("内核丢失转 blocked", executive.ledger.get(missing["id"])["status"] == "blocked")
    check("内核完成被采纳", executive.ledger.get(completed["id"])["status"] == "ready_to_report")
    check("内核完成不自动 done", executive.ledger.get(completed["id"])["status"] != "done")
    check("内核仍在跑不误杀", executive.ledger.get(still_running["id"])["status"] == "running")
    check("内核失败转 blocked", executive.ledger.get(failed_run["id"])["status"] == "blocked")
    check("对账结果分类", missing["id"] in result["blocked"] and
          completed["id"] in result["adopted"] and
          failed_run["id"] in result["blocked"] and
          still_running["id"] in result["pending"], str(result))
    check("丢失/失败都带请示",
          bool(executive.ledger.get(missing["id"])["blocked_question"]) and
          bool(executive.ledger.get(failed_run["id"])["blocked_question"]))

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
