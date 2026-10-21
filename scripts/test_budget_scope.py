"""Budget contract: per-call context is a quality gate; commission SUM is a cost alert."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.evaluator import Evaluator, _check_token_budget  # noqa: E402
from server.assistant.telemetry import TelemetryStore  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def packet(**budget) -> dict:
    return {"budget": budget}


def main() -> int:
    thresholds = {}
    per_call_120k = packet(max_prompt_tokens_per_call=120_000,
                           max_total_prompt_tokens=400_000)

    retries_are_not_bloat = {
        "calls": 3, "prompt_tokens": 300_000, "max_prompt_tokens_per_call": 100_000}
    check("retries do not fail the per-call quality gate",
          _check_token_budget({}, per_call_120k, retries_are_not_bloat, thresholds) == [])
    note = Evaluator._usage_note(["token_budget"], retries_are_not_bloat, per_call_120k)
    check("per-call limit is reported separately",
          note["max_prompt_tokens_per_call"] == 100_000
          and note["max_prompt_tokens_per_call_limit"] == 120_000, str(note))
    check("healthy total is not flagged", note["total_budget_exceeded"] is False, str(note))

    over_total = {"calls": 4, "prompt_tokens": 450_000, "max_prompt_tokens_per_call": 110_000}
    check("commission SUM over the cost line still passes the quality gate",
          _check_token_budget({}, per_call_120k, over_total, thresholds) == [])
    note = Evaluator._usage_note(["token_budget"], over_total, per_call_120k)
    check("commission SUM is exposed as a cost alert",
          note["total_budget_exceeded"] is True
          and note["max_total_prompt_tokens"] == 400_000, str(note))

    too_wide = {"calls": 1, "prompt_tokens": 130_000, "max_prompt_tokens_per_call": 130_000}
    findings = _check_token_budget({}, per_call_120k, too_wide, thresholds)
    check("one oversized call is rejected",
          len(findings) == 1 and findings[0]["rule_id"] == "prompt_token_bloat"
          and findings[0]["severity"] == "major", str(findings))
    check("finding names the single-call limit",
          "单次调用" in findings[0]["detail"] and "120000" in findings[0]["detail"],
          findings[0]["detail"])

    legacy = packet(max_prompt_tokens=80_000)
    legacy_usage = {"calls": 1, "prompt_tokens": 70_000}
    check("legacy packet and usage fall back to per-call semantics",
          _check_token_budget({}, legacy, legacy_usage, thresholds) == [])
    legacy_over = {"calls": 1, "prompt_tokens": 90_000}
    check("legacy key still rejects a genuinely oversized call",
          _check_token_budget({}, legacy, legacy_over, thresholds)[0]["rule_id"]
          == "prompt_token_bloat")

    empty = Evaluator._usage_note(["token_budget"], {"calls": 0}, per_call_120k)
    check("no spans means budget_checked=false",
          empty["budget_checked"] is False and empty["total_budget_exceeded"] is False,
          str(empty))

    with tempfile.TemporaryDirectory() as tmp:
        store = TelemetryStore(Path(tmp) / "telemetry.db")
        for prompt in (60_000, 100_000, 80_000):
            store.record_span(commission_id="c_budget", agent_id="w", prompt_tokens=prompt,
                              completion_tokens=1_000)
        usage = store.commission_usage("c_budget")
        store.close()
    check("telemetry keeps SUM and MAX distinct",
          usage["prompt_tokens"] == 240_000
          and usage["max_prompt_tokens_per_call"] == 100_000, str(usage))

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
