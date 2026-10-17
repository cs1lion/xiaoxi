"""回归评测集 runner：evals/cases/*.yaml 一个文件一个用例。

    .\\.venv\\Scripts\\python.exe scripts\\run_evals.py           # 离线层（黄金命令，CI 跑这层）
    .\\.venv\\Scripts\\python.exe scripts\\run_evals.py --live    # 离线 + live 层（需 LLM_API_KEY）

分层语义：
- offline：无网络、无 Key、确定性。记忆库强制 :memory:，失败即非零退出码。
- live：真实模型。无 Key 或 Mock 模式时整层跳过并明示，不算失败；原始回复与
  判定落盘 evals/results/（gitignore，仅存回复与判定，不存 prompt 与凭据）。

xfail 用例照常运行并汇报，但不计入失败。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 与 scripts/test_core.py 同一套隔离：导入任何 server 单例之前钉死内存库。
os.environ["DB_PATH"] = ":memory:"

import yaml  # noqa: E402  (PyYAML 是项目既有依赖，server/config.py 同款)

CASES_DIR = ROOT / "evals" / "cases"
RESULTS_DIR = ROOT / "evals" / "results"

from server.config import settings  # noqa: E402
settings.db_path = ":memory:"

from server.memory.manager import manager  # noqa: E402
from server.memory.store import store  # noqa: E402
from server.persona import build_messages  # noqa: E402


def load_cases(only: str | None = None) -> list[dict]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.yaml")):
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        case["_path"] = path.name
        if only and case["id"] != only:
            continue
        cases.append(case)
    return cases


def load_custom(ref: str):
    module_name, _, fn_name = ref.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, fn_name)


def seed_memories(case: dict) -> None:
    store.delete_all()
    for mem in (case.get("setup") or {}).get("memories") or []:
        store.upsert(kind=mem.get("kind", "fact"), subject=mem.get("subject", "user"),
                     key=mem["key"], value=mem["value"],
                     source=mem.get("source", "stated"),
                     importance=float(mem.get("importance", 0.8)))


def build_offline_prompt(case: dict) -> tuple[str, str]:
    """用最后一轮 user 文本走真实检索 + 真实装配，拼出最终 Prompt 文本。"""
    query = (case.get("turns") or [""])[-1]
    block, found = manager.context_with_status(query)
    messages = build_messages(memory_block=block, memory_found=found, user_text=query)
    return "\n".join(m["content"] for m in messages), block


async def run_check(check: dict, state: dict) -> tuple[bool, str]:
    kind = check["type"]
    if kind == "custom":
        import inspect
        outcome = load_custom(check["value"])(state.get("case"), state)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        return outcome
    target_text = {
        "prompt": state.get("prompt", ""),
        "reply": "\n".join(state.get("replies", [])),  # 多轮时每条回复都必须通过
    }[check.get("target", "reply")]
    if kind == "contains":
        ok = check["value"] in target_text
        return ok, "" if ok else f"未找到 {check['value']!r}"
    if kind == "not_contains":
        ok = check["value"] not in target_text
        return ok, "" if ok else f"不应出现 {check['value']!r}"
    if kind == "not_regex":
        hits = re.findall(check["value"], target_text)
        return (not hits), f"命中禁用模式 {hits}" if hits else ""
    if kind == "regex_any":
        ok = re.search(check["value"], target_text) is not None
        return ok, "" if ok else f"未命中任何期望模式 {check['value']!r}"
    return False, f"未知 check 类型 {kind!r}"


async def run_live_turns(case: dict, llm) -> list[str]:
    """真实装配 + 真实模型，逐轮对话，返回每轮原始回复（情绪标签未剥离）。"""
    from server.dialogue.orchestrator import EMOTION_INSTRUCTION

    history: list[dict[str, str]] = []
    replies: list[str] = []
    for user_text in case.get("turns") or []:
        block, found = manager.context_with_status(user_text)
        messages = build_messages(memory_block=block, memory_found=found,
                                  emotion_instruction=EMOTION_INSTRUCTION,
                                  history=history, user_text=user_text)
        reply = await llm.complete(messages, max_tokens=int(settings.llm_reply_tokens))
        replies.append(reply)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply})
    return replies


async def run_case(case: dict, *, live: bool, llm) -> dict:
    """返回 {id, layer, runs: [{run, passed, turns, checks}], passed, xfail}"""
    is_live = case.get("layer") == "live"
    runs_n = int(case.get("runs", 3 if is_live else 1))
    run_records = []
    for run_idx in range(runs_n):
        seed_memories(case)
        prompt, memory_block = ("", "")
        replies: list[str] = []
        if is_live:
            replies = await run_live_turns(case, llm)
        else:
            prompt, memory_block = build_offline_prompt(case)
        state = {"case": case, "replies": replies, "prompt": prompt, "memory_block": memory_block}
        check_results = []
        for check in case.get("checks") or []:
            try:
                ok, detail = await run_check(check, state)
            except Exception as exc:  # check 自身炸了算失败，不静默
                ok, detail = False, f"check 抛异常 {type(exc).__name__}: {exc}"
            check_results.append({
                "type": check["type"], "target": check.get("target", "reply"),
                "value": check.get("value", ""), "pass": bool(ok), "detail": detail,
            })
        run_records.append({
            "run": run_idx + 1,
            "passed": all(c["pass"] for c in check_results),
            # live 落盘只存回复与判定；prompt 与凭据不落盘。
            "turns": [{"user": u, "reply": r} for u, r in
                      zip(case.get("turns") or [], replies)] if is_live else [],
            "checks": check_results,
        })
    passed_runs = sum(1 for r in run_records if r["passed"])
    rule = str(case.get("pass_rule", "all"))
    if rule.startswith("min_rate:"):
        threshold = float(rule.split(":", 1)[1])
        case_passed = passed_runs / max(1, runs_n) >= threshold
    else:
        case_passed = passed_runs == runs_n
    return {"id": case["id"], "layer": case.get("layer", "offline"),
            "xfail": case.get("status") == "xfail",
            "xfail_reason": case.get("reason", ""),
            "runs": run_records, "passed_runs": passed_runs,
            "total_runs": runs_n, "passed": case_passed}


async def main_async(args) -> int:
    cases = load_cases(args.case)
    if not cases:
        print("evals/cases/ 下没有匹配的用例")
        return 1

    offline_cases = [c for c in cases if c.get("layer") != "live"]
    live_cases = [c for c in cases if c.get("layer") == "live"]

    llm = None
    live_skipped = False
    if args.live and live_cases:
        from server.llm import LLM
        llm = LLM()
        if llm.offline:
            live_skipped = True
    elif live_cases:
        live_skipped = True

    results = []
    for case in offline_cases:
        print(f"[offline] {case['id']}")
        results.append(await run_case(case, live=False, llm=None))
    if live_cases:
        if not args.live:
            print(f"[live] 跳过 {len(live_cases)} 条（需显式 --live）")
            live_skipped = True
        elif llm is not None and llm.offline:
            print(f"[live] 跳过 {len(live_cases)} 条（无 LLM_API_KEY / Mock 模式，不算失败）")
            live_skipped = True
        else:
            for case in live_cases:
                print(f"[live] {case['id']} ×{case.get('runs', 3)}")
                results.append(await run_case(case, live=True, llm=llm))

    # 落盘 + 通过率表
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (RESULTS_DIR / f"{stamp}.json").write_text(
        json.dumps({"at": stamp, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("\n=== 通过率表 ===")
    failures = []
    xfails = []
    for r in results:
        mark = "PASS" if r["passed"] else ("XFAIL" if r["xfail"] else "FAIL")
        print(f"  {mark:5} {r['id']:38} {r['passed_runs']}/{r['total_runs']} 轮通过"
              + (f"  (xfail: {r['xfail_reason']})" if r["xfail"] and not r["passed"] else ""))
        if not r["passed"]:
            (xfails if r["xfail"] else failures).append(r["id"])
        for run in r["runs"]:
            for c in run["checks"]:
                if not c["pass"]:
                    print(f"        轮{run['run']} [{c['type']}:{c['target']}] {c['detail']}")
    if live_skipped and args.live:
        print("（live 层本次跳过，未计入上表）")
    if xfails:
        print(f"xfail 用例（已知失败，不计入）：{', '.join(xfails)}")
    print(f"=== {len(results) - len(failures) - len(xfails)} passed, "
          f"{len(failures)} failed, {len(xfails)} xfail ===")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="回归评测集 runner")
    parser.add_argument("--live", action="store_true", help="跑 live 层（需 LLM_API_KEY）")
    parser.add_argument("--case", default=None, help="只跑指定 id 的用例")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
