"""回归评测集 runner：evals/cases/*.yaml 一个文件一个用例。

    .\\.venv\\Scripts\\python.exe scripts\\run_evals.py           # 离线层（黄金命令，CI 跑这层）
    .\\.venv\\Scripts\\python.exe scripts\\run_evals.py --live    # 离线 + live 层（需 LLM_API_KEY）

分层语义：
- offline：无网络、无 Key、确定性。记忆库强制 :memory:，失败即非零退出码；
  用例缺 `origin`（来源实验 / 事故 / commit）同样非零退出，见 main_async 起手处。
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
    for lesson in (case.get("setup") or {}).get("lessons") or []:
        store.add_lesson(trigger=lesson["trigger"], action=lesson["action"],
                         cues=lesson.get("cues"), scope=lesson.get("scope", "dialogue"),
                         source=lesson.get("source", "reflection"),
                         evidence_refs=lesson.get("evidence_refs"))


def build_offline_prompt(case: dict) -> tuple[str, str]:
    """用最后一轮 user 文本走真实检索 + 真实装配，拼出最终 Prompt 文本。"""
    from server.memory.lessons import render_lessons
    query = (case.get("turns") or [""])[-1]
    block, found = manager.context_with_status(query)
    procedural = manager.lessons_for(query)
    lesson_block = render_lessons(procedural) if procedural else ""
    messages = build_messages(memory_block=block, memory_found=found, user_text=query)
    # Inject lessons as an additional system message, matching the Orchestrator path.
    if lesson_block:
        messages.append({"role": "system", "content": lesson_block})
    return "\n".join(m["content"] for m in messages), block


async def run_check(check: dict, state: dict) -> tuple[bool, str, str]:
    """返回 (ok, detail, verdict)；verdict ∈ pass / fail / review。

    review 是第三态：判定器说不清（judge 没配、调用失败、与正则不一致）时用，
    既不算通过也不算失败，交人工复核。只有 fail 计入退出码。
    既有 check 仍返回两元组，在这里归一化成 pass / fail，行为逐位不变。
    """
    if check["type"] == "custom":
        import inspect
        outcome = load_custom(check["value"])(state.get("case"), state)
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if isinstance(outcome, tuple) and len(outcome) == 3:
            ok, detail, verdict = outcome
            return bool(ok), str(detail), str(verdict or "fail")
        ok, detail = outcome
        return bool(ok), str(detail), "pass" if ok else "fail"
    ok, detail = await _run_builtin_check(check, state)
    return bool(ok), str(detail), "pass" if ok else "fail"


async def _run_builtin_check(check: dict, state: dict) -> tuple[bool, str]:
    """内置 check 类型：contains / not_contains / not_regex / regex_any。"""
    kind = check["type"]
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


async def run_live_turns(case: dict, llm) -> tuple[list[str], list[dict]]:
    """真实装配 + 真实模型，逐轮对话。

    返回 (每轮原始回复, 每轮元信息)。元信息必须留下 finish_reason：推理模型
    把 token 预算吃光时正文为空，如果只看文本，会把"被截断"误报成"缺情绪标签"
    （2026-10-17 实测：18 轮里 1 轮 finish_reason=length，正文 0 字）。

    截断时按生产路径同款救援再要一次短回复；救援是否成功记在 rescued 里，
    仍然为空就如实判失败，不靠重试把失败掩盖掉。

    与生产的差异是**故意的**：生产在 server/dialogue/orchestrator.py:1091 还有
    一层无条件空正文兜底（`if not text.strip(): text = '嗯，我在呢。'`），那是 UX
    保证——用户不能听到静音。评测层刻意**不**复刻那层：这里测的是「模型能不能
    稳定产出合规回复」，兜掉就等于掩盖退化；且兜底文案本身不带 [情绪] 标签，
    复刻它反而会制造新的标签失败。要改先想清楚你在测哪个问题。
    """
    from server.dialogue.orchestrator import EMOTION_INSTRUCTION, parse_emotion

    history: list[dict[str, str]] = []
    replies: list[str] = []
    meta: list[dict] = []
    for user_text in case.get("turns") or []:
        block, found = manager.context_with_status(user_text)
        messages = build_messages(memory_block=block, memory_found=found,
                                  emotion_instruction=EMOTION_INSTRUCTION,
                                  history=history, user_text=user_text)
        raw = await llm.complete_raw(messages, max_tokens=int(settings.llm_reply_tokens))
        text = raw.get("text") or ""
        finish = raw.get("finish_reason") or ""
        rescued = False
        retry_meta: dict = {}
        if finish == "length" and len(parse_emotion(text)[1]) < 6:
            rescue = list(messages) + [{
                "role": "user",
                "content": "（上一条被截断了，现在只回一句话，保留开头的 [情绪] 标签，20 字以内。）",
            }]
            try:
                retry = await llm.complete_raw(rescue, max_tokens=600,
                                               reasoning_effort="minimal")
            except Exception:  # provider rejects reasoning_effort
                retry = await llm.complete_raw(rescue, max_tokens=600)
            text = retry.get("text") or ""
            finish = retry.get("finish_reason") or finish
            rescued = True
            retry_meta = {
                "retry_finish_reason": finish,
                "retry_completion_tokens": int(
                    (retry.get("usage") or {}).get("completion_tokens") or 0),
            }
        replies.append(text)
        meta.append({
            "finish_reason": finish,
            "rescued": rescued,
            "text_chars": len(text),
            "completion_tokens": int((raw.get("usage") or {}).get("completion_tokens") or 0),
            **retry_meta,
        })
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": text})
    return replies, meta


async def run_case(case: dict, *, live: bool, llm) -> dict:
    """返回 {id, layer, runs: [{run, passed, turns, checks}], passed, xfail}"""
    is_live = case.get("layer") == "live"
    runs_n = int(case.get("runs", 3 if is_live else 1))
    run_records = []
    for run_idx in range(runs_n):
        seed_memories(case)
        prompt, memory_block = ("", "")
        replies: list[str] = []
        turn_meta: list[dict] = []
        if is_live:
            replies, turn_meta = await run_live_turns(case, llm)
        else:
            prompt, memory_block = build_offline_prompt(case)
        state = {"case": case, "replies": replies, "prompt": prompt,
                 "memory_block": memory_block, "turn_meta": turn_meta}
        check_results = []
        for check in case.get("checks") or []:
            try:
                ok, detail, verdict = await run_check(check, state)
            except Exception as exc:  # check 自身炸了算失败，不静默
                ok, detail, verdict = False, f"check 抛异常 {type(exc).__name__}: {exc}", "fail"
            check_results.append({
                "type": check["type"], "target": check.get("target", "reply"),
                "value": check.get("value", ""), "pass": bool(ok),
                "verdict": verdict, "detail": detail,
            })
        # 一轮的结论：全部 pass 才算通过；有 fail 算失败；只剩 review 算"说不清"
        # （不计入通过也不计入失败，退出码只看 fail）。
        verdicts = [c["verdict"] for c in check_results]
        run_records.append({
            "run": run_idx + 1,
            "verdict": ("pass" if all(v == "pass" for v in verdicts)
                        else "fail" if any(v == "fail" for v in verdicts) else "review"),
            "passed": all(v == "pass" for v in verdicts),
            # live 落盘只存回复与判定；prompt 与凭据不落盘。
            "turns": [{"user": u, "reply": r} for u, r in
                      zip(case.get("turns") or [], replies)] if is_live else [],
            "turn_meta": turn_meta if is_live else [],
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
            "total_runs": runs_n, "passed": case_passed,
            # 三态计数：退出码只看 failed_runs，review 既不红也不绿（方案 §四）。
            "failed_runs": sum(1 for r in run_records if r.get("verdict") == "fail"),
            "reviewed_runs": sum(1 for r in run_records if r.get("verdict") == "review")}


def classify_results(results: list[dict]) -> dict:
    """按三态给用例定 mark 并分桶。退出码只由 fail 轮次决定。

    设计（docs/EVAL_MECHANISM_PLAN.md §四）：review 是"判定器说不清"，
    既不算通过也不算失败，交人工复核；只有 fail 计入退出码。

    曾按 `passed=False` 把 review 扫进 failures：judge 一没配或一调用失败，
    整条 live 门禁就退出码 1——红的不是被测系统，是评测配置，而这种红在
    paid live run 里最难分辨。回归测试见 scripts/test_judge_three_state.py
    的 RunEvalsClassificationTests。
    """
    marks: list[str] = []
    failures: list[str] = []
    xfails: list[str] = []
    reviewed: list[str] = []
    for r in results:
        failed_runs = int(r.get("failed_runs") or 0)
        reviewed_runs = int(r.get("reviewed_runs") or 0)
        if r.get("xfail") and not r.get("passed"):
            mark = "XFAIL"
            xfails.append(r["id"])
        elif failed_runs:
            mark = "FAIL"
            failures.append(r["id"])
        elif reviewed_runs:
            mark = "REVIEW"
            reviewed.append(r["id"])
        elif r.get("passed"):
            mark = "PASS"
        else:
            # 不是通过、又没有 fail/review 轮次（含缺 verdict 的旧形状）：
            # 未知状态按失败算，别让漏判静默变成放行。
            mark = "FAIL"
            failures.append(r["id"])
        marks.append(mark)
    return {"marks": marks, "failures": failures, "xfails": xfails, "reviewed": reviewed}


async def main_async(args) -> int:
    cases = load_cases(args.case)
    if not cases:
        print("evals/cases/ 下没有匹配的用例")
        return 1

    # origin 必填（docs/TESTING.md:59）：缺失或空串都算没溯源，直接失败。
    # 只校验过滤后的用例：--case 指定单条时不为无关坏档陪失败；不带 --case 的
    # 整跑（CI / run_gates 的 evals 门禁）仍覆盖 evals/cases/ 下全部 yaml。
    missing_origin = [c for c in cases if not str(c.get("origin") or "").strip()]
    if missing_origin:
        print("以下用例缺少 origin（必填，来源实验 / 事故 / commit）："
              + "、".join(f"{c['_path']}（id: {c.get('id', '?')}）" for c in missing_origin))
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
    buckets = classify_results(results)
    failures = buckets["failures"]
    xfails = buckets["xfails"]
    reviewed = buckets["reviewed"]
    for r, mark in zip(results, buckets["marks"]):
        print(f"  {mark:5} {r['id']:38} {r['passed_runs']}/{r['total_runs']} 轮通过"
              + (f"  (xfail: {r['xfail_reason']})" if r["xfail"] and not r["passed"] else ""))
        for run in r["runs"]:
            for c in run["checks"]:
                if not c["pass"]:
                    print(f"        轮{run['run']} [{c['type']}:{c['target']}] {c['detail']}")
    if live_skipped and args.live:
        print("（live 层本次跳过，未计入上表）")
    if xfails:
        print(f"xfail 用例（已知失败，不计入）：{', '.join(xfails)}")
    if reviewed:
        print("review 用例（判定器说不清，未计入通过也未计入失败，需人工复核）："
              + "、".join(reviewed))
    passed_count = len(results) - len(failures) - len(xfails) - len(reviewed)
    print(f"=== {passed_count} passed, {len(failures)} failed, "
          f"{len(xfails)} xfail, {len(reviewed)} review ===")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="回归评测集 runner")
    parser.add_argument("--live", action="store_true", help="跑 live 层（需 LLM_API_KEY）")
    parser.add_argument("--case", default=None, help="只跑指定 id 的用例")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
