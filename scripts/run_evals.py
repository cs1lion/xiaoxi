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


def subject_label() -> dict:
    """被测对象标识，随快照落盘。让跨时间的对比可复现、可解释。

    为什么需要：不记模型标识时，报告层的「最近退化」无法区分「模型退步」和
    「换过模型/供应商」——两者处置相反（前者查回归，后者只是换了被测对象、
    曲线本就不连续）。docs/EVAL_MECHANISM_RESEARCH.md:296 建议过，未落地。

    记什么：provider / base_url / model 三件套 + 产出这条曲线的 git HEAD。
    judge 单列（无 judge 时为 null）——判据方换了人，结论的可比性同样变。
    **不记 prompt、不记凭据**（api_key 一律不进快照，落盘红线不变）。

    离线跑同样记：offline 层不调模型，但「这套配置下测的是什么」仍要可追溯。
    """
    import subprocess

    def _head() -> str:
        try:
            out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                 capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            return ""  # 非 git 环境或 git 不可用：留空，不让它崩掉落盘

    judge = None
    if (settings.llm_judge_base_url and settings.llm_judge_api_key
            and settings.llm_judge_model):
        judge = {"provider": settings.llm_judge_provider,
                 "base_url": settings.llm_judge_base_url.rstrip("/"),
                 "model": settings.llm_judge_model}
    return {"provider": settings.llm_provider,
            "base_url": settings.llm_base_url.rstrip("/"),
            "model": settings.llm_model,
            "judge": judge,
            "git_head": _head()}


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
        if not target_text:
            # 空文本上的负向断言恒真：`value not in ""` 永远是 True。
            # 那是空转，不是通过——R16 只在加载期禁了 offline + target:reply，
            # 但底层函数对这个洞没防（live 用例模型返回空回复时同样中招）。
            return False, "目标文本为空，负向断言在空文本上恒真，按失败计"
        ok = check["value"] not in target_text
        return ok, "" if ok else f"不应出现 {check['value']!r}"
    if kind == "not_regex":
        if not target_text:
            # 同上：`re.findall(pattern, "")` 返回空表，`not hits` 恒为 True。
            return False, "目标文本为空，负向断言在空文本上恒真，按失败计"
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
        elif r.get("passed"):
            # passed 必须排在 failed_runs **之前**：run_case 已按 pass_rule
            # （all / min_rate）算出它，min_rate 的容错就在这一行兑现。
            # 曾经的顺序反了——先看 failed_runs，于是 min_rate 用例 4/5
            # （≥0.8、passed=True）仍被判 FAIL、exit 1，YAML 里写明的
            # 「容忍 5 次里 1 次失败」被分桶层作废；报告层还会同时打
            # 「4/5 轮通过」和「FAIL」两个自相矛盾的信号。
            mark = "PASS"
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


# live 成本估算用的实测均值（R27）。出处：2026-10-27 从 evals/results/ 的
# 381 个 turn_meta 记录统计——每轮 completion_tokens 中位数 323、均值 378
# （min 58 / max 1200，含截断救援重试）。取中位数而非均值：它对「思维链被打满」
# 那几次长尾不敏感，估算更稳。**这是估算，不替代真实记账**——真实数字看
# evals_report 的 completion_tokens 列。
EST_COMPLETION_TOKENS_PER_TURN = 323

# judge 调用估算：_judge_with_aux 把整份 transcript 一次发给裁判，所以是
# 「每次运行 1 次」而不是每轮 1 次。裁判同样花钱，必须计入总调用量。
EST_JUDGE_CALLS_PER_RUN = 1

# live 成本警告阈值（R27）：预计 completion_tokens 超过此值即打印显著警告，
# 但**不拦**——拦截是 --max-calls 的职责，警告只是让人在花钱前愣一下。
# 取 45000：2026-10-27 实测全量 live 约 15771 tokens / 54 次调用，此阈值约为
# 其 3 倍。定 3 倍是为了让警告稀有：正常全量跑不触发，只有异常放大的跑法
# （临时加大 runs、--case 漏写导致全量、用例数暴涨）才触发。警告太勤等于没有。
EST_TOKEN_WARN_THRESHOLD = 45000


def estimate_live_cost(cases: list[dict]) -> dict:
    """估算 live 层的调用量与 token。不调模型、不写任何文件、不读快照。

    返回 {"per_case": [...], "model_calls", "judge_calls", "total_calls",
          "turns", "tokens"}。只统计 layer == "live" 的用例——offline 不花钱。
    """
    rows = []
    model_calls = judge_calls = turns = 0
    for case in cases:
        if case.get("layer") != "live":
            continue
        runs_n = int(case.get("runs", 3))
        turn_n = len(case.get("turns") or [])
        # judge 计数按 check 的 value 里含 "judge" 算：三个 judge 检查
        # （judge_hallucination / judge_fact_correction / judge_temporal_history）
        # 都走 _judge_with_aux，一次运行各发一份 transcript。
        judges = sum(1 for ch in (case.get("checks") or [])
                     if "judge" in str(ch.get("value") or ""))
        c_model = runs_n * turn_n
        c_judge = runs_n * judges * EST_JUDGE_CALLS_PER_RUN
        rows.append({"id": case["id"], "layer": case.get("layer"),
                     "runs": runs_n, "turns": turn_n,
                     "judge_checks": judges, "model_calls": c_model,
                     "judge_calls": c_judge})
        model_calls += c_model
        judge_calls += c_judge
        turns += runs_n * turn_n
    return {"per_case": rows, "model_calls": model_calls, "judge_calls": judge_calls,
            "total_calls": model_calls + judge_calls, "turns": turns,
            "tokens": turns * EST_COMPLETION_TOKENS_PER_TURN}


def print_live_estimate(cases: list[dict]) -> dict:
    """打印 live 成本估算（--dry-run 用）。返回估算结果供调用方判预算。"""
    est = estimate_live_cost(cases)
    print("=== live 成本预估（不调模型、不落快照；估算口径见 run_evals.py 常量） ===")
    if not est["per_case"]:
        print("  没有 live 用例——offline 层不花模型钱。")
        return est
    print(f"{'用例':38} {'runs':>5} {'turns':>6} {'judge':>6} "
          f"{'模型调用':>8} {'裁判调用':>9}")
    for row in est["per_case"]:
        print(f"{row['id'][:38]:38} {row['runs']:>5} {row['turns']:>6} "
              f"{row['judge_checks']:>6} {row['model_calls']:>8} {row['judge_calls']:>9}")
    print(f"  合计 completion 调用: {est['total_calls']} 次"
          f"（被测模型 {est['model_calls']} + 裁判 {est['judge_calls']}）")
    print(f"  估算 completion_tokens: 约 {est['tokens']}"
          f"（按每轮中位数 {EST_COMPLETION_TOKENS_PER_TURN} × {est['turns']} 轮）")
    print("  **这是估算，不是报价**：真实 token 数以 evals_report 的 "
          "completion_tokens 列为准（含截断救援重试）。")
    return est


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

    # 真空绿防线（R16，实测过两条都能让用例"跑过、绿了、一条断言都没执行"）：
    #
    # ① runs 必须为正整数。runs_n == 0 时 run_case 的
    #    `passed_runs == runs_n` 变成 0 == 0 → True，于是一条 check 都不跑
    #    就判 PASS（实测：check 写 contains 一段永不可能出现的文字，
    #    仍输出 PASS 0/0 轮通过、exit 0）。
    # ② offline 用例不许 target: reply。offline 路径 replies 恒为 []
    #    （run_evals.py:203-208），_run_builtin_check 由此拼出
    #    target_text = ""，于是 not_regex / not_contains 永远不命中——
    #    把幻觉护栏抄成 offline 用例会得到一条永远绿、什么都不防的用例。
    #    反方向同样坏：contains / regex_any 配空文本会永远 FAIL。
    #    现有 offline 用例的 check 全部用 prompt、live 全部用 reply，无误红。
    #    数字会随新增用例漂移，要核就跑
    #    `grep -l "^layer: offline" evals/cases/*.yaml | wc -l`，别信这里写的数。
    bad_runs = [c for c in cases
                if not str(c.get("runs", 1)).lstrip("-").isdigit()
                or int(c.get("runs", 1)) <= 0]
    if bad_runs:
        print("以下用例的 runs 不是正整数（0 或负值会让 0==0 判 PASS，"
              "一条 check 都不跑就绿）："
              + "、".join(f"{c['_path']}（id: {c.get('id', '?')}，"
                         f"runs={c.get('runs')!r}）" for c in bad_runs))
        return 1
    # R47：checks 形状校验必须排在 bad_target 之前——bad_target 要遍历
    # checks 调 ch.get，若 checks 是字符串/None、或条目不是带 type 的 dict，
    # 它自己先崩成裸 traceback。放前面就能用一句话说清，而不是崩。
    # 空列表这半边同时堵 R47 主缺陷：run_case 第 263 行
    # `for check in case.get("checks") or []` 对空列表跳过，随后
    # all(v == "pass" for v in []) 恒真 → passed=True、检查数 0，
    # 即「一条断言都不跑就判 PASS」。R16 堵了 runs:0 却没堵这条。
    def _checks_shape_bad(c: dict) -> bool:
        chks = c.get("checks")
        if not isinstance(chks, list) or not chks:
            return True
        return any(not isinstance(ch, dict) or not ch.get("type") for ch in chks)
    bad_checks = [c for c in cases if _checks_shape_bad(c)]
    if bad_checks:
        print("以下用例的 checks 为空或形状损坏（空列表让 all([]) 恒真、"
              "一条断言都不跑就判 PASS；非 list / 条目缺 type 会在 run_case 崩）："
              + "、".join(f"{c['_path']}（id: {c.get('id', '?')}）" for c in bad_checks))
        return 1
    bad_target = [(c, ch) for c in cases if c.get("layer") != "live"
                  for ch in (c.get("checks") or [])
                  if ch.get("target", "reply") == "reply"]
    if bad_target:
        print("offline 用例不能用 target: reply（offline 路径 replies 恒为 []，"
              "target_text 拼成空串，负向 check 永远不命中、正向 check 永远失败）："
              + "、".join(f"{c['_path']}（id: {c.get('id', '?')}，"
                         f"check {ch.get('type')}）" for c, ch in bad_target))
        return 1
    # R47 补：pass_rule 的 min_rate 阈值必须是 (0,1] 的有效浮点数。
    # 阈值 0 让 run_case:291 的 passed_runs/max(1,runs_n) >= 0 恒真——
    # 实测：min_rate:0 + 一条必败 check → passed=True、failed_runs=1，
    # classify_results 仍 marks=['PASS']、failures=[]，退出码 0。
    # 即「断言全红却整条判过」，比 checks:[] 更隐蔽（用例看着有断言）。
    # 非数值（min_rate:abc）会在 float() 崩；>1 则永远无法满足、永远红。
    bad_rule = []
    for c in cases:
        rule = str(c.get("pass_rule", "all"))
        if not rule.startswith("min_rate:"):
            continue
        raw = rule.split(":", 1)[1].strip()
        try:
            threshold = float(raw)
        except ValueError:
            bad_rule.append(f"{c['_path']}（id: {c.get('id', '?')}，"
                            f"pass_rule={rule!r} 不是有效数值）")
            continue
        if not 0 < threshold <= 1:
            bad_rule.append(f"{c['_path']}（id: {c.get('id', '?')}，"
                            f"min_rate 阈值 {threshold} 不在 (0,1]）")
    if bad_rule:
        print("以下用例的 pass_rule 阈值非法（min_rate:0 会让 0/1>=0 恒真、"
              "断言全红仍判 PASS；非数值会在 float() 崩；>1 永远无法满足）："
              + "、".join(bad_rule))
        return 1
    # R47 补：turns 为空/非列表 = 一次对话轮次都不发生。live 用例于是 0 次模型
    # 调用（实测 estimate_live_cost：turns:[] → model_calls=0），offline 的
    # prompt 也失去内容——与 bad_checks 同族：一条 check 都不跑形同虚设，
    # 一轮对话都不发生同样是。15 条真实用例 turns 全非空，不会误伤。
    bad_turns = [c for c in cases
                 if not isinstance(c.get("turns"), list) or not c.get("turns")]
    if bad_turns:
        print("以下用例的 turns 为空或不是列表（没有对话轮次：live 会 0 次模型"
              "调用、offline 的 prompt 失去内容，用例形同虚设）："
              + "、".join(f"{c['_path']}（id: {c.get('id', '?')}）" for c in bad_turns))
        return 1

    offline_cases = [c for c in cases if c.get("layer") != "live"]
    live_cases = [c for c in cases if c.get("layer") == "live"]

    # live 成本预估与预算上限（R27）。放在任何模型调用之前：dry-run 就此折返，
    # max-calls 在此 fail-fast——别跑了一半才停在半路上，那既浪费钱又留半份快照。
    if getattr(args, "dry_run", False):
        print_live_estimate(cases)
        return 0
    est = estimate_live_cost(cases)
    max_calls = getattr(args, "max_calls", None)
    if args.live and max_calls is not None and est["total_calls"] > max_calls:
        print(f"拒绝开跑：live 预计 {est['total_calls']} 次 completion 调用，"
              f"超过 --max-calls 上限 {max_calls}。")
        print("  超支来源（按用例）：")
        for row in est["per_case"]:
            print(f"    {row['id'][:38]:38} 模型 {row['model_calls']} + "
                  f"裁判 {row['judge_calls']} = {row['model_calls'] + row['judge_calls']}")
        print("  处理：调大 --max-calls、用 --case 缩小范围，"
              "或先 --dry-run 看清预估再决定。")
        return 1
    # 高成本显著警告：即使不拦也要让人在花钱前愣一下。实测本项目 .env 常驻
    # 真实 Key，所以「忘了加 --max-calls 直接 --live」是条真实会花钱的路径。
    if args.live and est["tokens"] > EST_TOKEN_WARN_THRESHOLD:
        print(f"**警告**：本次 live 预计约 {est['tokens']} completion_tokens"
              f"（{est['total_calls']} 次调用），超过警告线 "
              f"{EST_TOKEN_WARN_THRESHOLD}。确知成本用 --dry-run，"
              "要拦截用 --max-calls N。")

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
        json.dumps({"at": stamp, "subject": subject_label(), "results": results},
                   ensure_ascii=False, indent=2),
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
    parser.add_argument("--dry-run", action="store_true",
                        help="只估算 live 调用量与花费，不调模型、不落快照")
    parser.add_argument("--max-calls", type=int, default=None,
                        help="live 预计 completion 次数上限，超过则拒绝开跑（fail-fast）")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
