# -*- coding: utf-8 -*-
"""接线封装（server/contract/bridge.py）的单元测试。

    .\.venv\Scripts\python.exe scripts\test_intent_gate_bridge.py

**不调模型**：bridge 是纯逻辑（预判答案 + 解析结果 + 会话状态 → 决定），
所以全部用手写输入覆盖。真模型那一半在 `test_intent_parser_e2e.py`。

六组断言
--------
  A 二分预判解析    "1"/"0"/空串/乱答 的区分——空串必须退回正则，不能当"不是一单"
  B 补槽优先        有 pending 时「markdown」是补槽，不是新任务
  C 会话状态        round_index 累加、ready 后清空、换话题清 pending
  D 三态分派        ready 建单 / needs_clarification 追问 / rejected 拒绝
  E 兜底            模型没答案、解析失败都必须 fallback_to_regex，不能静默
  F 边界            不 import 业务模块、不碰网络
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract.bridge import (  # noqa: E402
    DECIDE_CHAT, DECIDE_CLARIFY, DECIDE_DELEGATE, DECIDE_FILL, DECIDE_REJECTED,
    BINARY_MAX_TOKENS, GateSession, SessionStore, apply_slot_answer, decide,
    looks_like_slot_answer, parse_binary_answer,
)
from server.contract.gate import Draft, evaluate  # noqa: E402
from server.contract.model import (  # noqa: E402
    ArtifactSpec, Budget, CheckSpec, Criterion, Slot, SlotSource,
)
from server.contract.parser import ParseResult, build_draft  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


TURN = "turn-001"
NOW = "2026-10-23T19:00:00+08:00"


def mk_draft(**over) -> Draft:
    """一个信息基本完备的 Draft，按需挖掉字段。"""
    fields = dict(
        contract_id="c-1", version=1, created_at=NOW, utterance="整理便笺",
        goal=Slot(value="整理便笺",
                  source=SlotSource(kind="default", reason="模型抽取",
                                    confidence=0.8),
                  highlight=True),
        inputs=(),
        artifact=Slot(value=ArtifactSpec(kind="report_md"),
                      source=SlotSource(kind="default", reason="模型抽取"),
                      highlight=True),
        deliver_to=None,
        acceptance=(Criterion(id="a1", text="输出存在", kind="deterministic",
                              check=CheckSpec(type="file_exists_nonempty",
                                              path="out/a.md")),),
        side_effects=(),
        permissions_required=("L0",),
        budget=Budget(),
    )
    fields.update(over)
    return Draft(**fields)


def mk_result(draft: Draft | None) -> ParseResult:
    return ParseResult(draft=draft, intent="delegate" if draft else "chat")


# ---------- A 二分预判解析 ----------

def test_binary_parse() -> None:
    print("[A 二分预判解析：空串必须退回正则，不能当「不是一单」]")

    check("'1' -> True", parse_binary_answer("1") is True)
    check("'0' -> False", parse_binary_answer("0") is False)
    check("'1\\n解释' 取首字符", parse_binary_answer("1\n因为…") is True)
    check("'0 不是' 取首字符", parse_binary_answer("0 不是") is False)

    # 这三种都必须返回 None（= 没答案 -> 退回正则）
    check("空串 -> None", parse_binary_answer("") is None)
    check("全空白 -> None", parse_binary_answer("   ") is None)
    check("None -> None", parse_binary_answer(None) is None)
    check("乱答 -> None", parse_binary_answer("我不确定") is None)

    # 这是本组最关键的一条：空串若被当成 False，该建单的句子会静默掉
    session = GateSession()
    result = decide("帮我把报告生成一下", session=session, binary_answer="",
                    parser_result=mk_result(mk_draft()))
    check("空串时 fallback_to_regex=True", result.fallback_to_regex is True,
          str(result.kind))
    check("空串时不落 chat 判定", result.kind != DECIDE_CHAT or result.fallback_to_regex,
          result.kind)

    # max_tokens 必须够大（推理模型给 2 会返回空串，实测过）
    check("BINARY_MAX_TOKENS 足够大（>=200）", BINARY_MAX_TOKENS >= 200,
          str(BINARY_MAX_TOKENS))


# ---------- B 补槽优先 ----------

def test_fill_slot() -> None:
    print("[B 补槽优先：有 pending 时短回答是补槽，不是新任务]")

    # 「markdown」单独看会被判 chat，有 pending 时必须当补槽
    session = GateSession()
    session.draft = mk_draft(artifact=Slot(
        value=ArtifactSpec(kind="file", format=""),
        source=SlotSource(kind="default", reason="模型抽取"), highlight=True))
    session.gap_key = "artifact"

    check("「markdown」被认成补槽",
          looks_like_slot_answer("markdown", "artifact") is True)
    check("「md」被认成补槽", looks_like_slot_answer("md", "artifact") is True)
    check("「存成 csv 吧」被认成补槽",
          looks_like_slot_answer("存成 csv 吧", "artifact") is True)

    # 补槽执行。**故意留出别的缺口**（acceptance 空、权限空、预算空），
    # 否则补完 format 就 ready、session 被清，测不到「追问继续」。
    session = GateSession()
    session.draft = mk_draft(
        artifact=Slot(value=ArtifactSpec(kind="file", format=""),
                      source=SlotSource(kind="default", reason="模型抽取"),
                      highlight=True),
        acceptance=(), permissions_required=(), budget=None)
    session.gap_key = "artifact"

    result = decide("markdown", session=session, binary_answer="0")
    check("补槽后标 from_slot_fill", result.from_slot_fill is True, "没标")
    check("补槽后还有缺口 -> kind=clarify", result.kind == DECIDE_CLARIFY,
          result.kind)
    check("补槽后 artifact.format=md",
          session.draft is not None
          and session.draft.artifact.value.format == "md",
          str(session.draft.artifact.value if session.draft and session.draft.artifact else None))
    check("补槽后 round_index 累加到 1", session.rounds == 1, str(session.rounds))

    # 补槽的 source 必须是 user_confirmed（用户刚说的），且带 turn_id
    if session.draft is not None:
        src = session.draft.artifact.source
        check("补槽 source 是 user_confirmed", src.kind == "user_confirmed", src.kind)
        check("补槽 source 带 turn_id（否则 freeze 抛）", bool(src.turn_id), "缺 turn_id")

    # 关键：补完一个槽若还有别的缺口，session 必须留着（后面还要继续问）
    check("补槽后 pending 仍在（还有别的缺口）", session.active is True,
          "pending 被清了")
    check("补槽后问的是下一个缺口",
          session.gap_key != "artifact", session.gap_key)

    # 长句子不该被当补槽
    check("长句子不认成补槽",
          looks_like_slot_answer("另外帮我把周报也整理一下发给老板", "artifact") is False)
    check("无 pending 时不认补槽",
          looks_like_slot_answer("markdown", "") is False)


# ---------- C 会话状态 ----------

def test_session() -> None:
    print("[C 会话状态：round_index 累加、ready 清空、换话题清 pending]")

    store = SessionStore()
    s1 = store.get("s1")
    s2 = store.get("s2")
    check("不同 session 拿到不同对象", s1 is not s2)
    check("同一 session 拿到同一对象", store.get("s1") is s1)
    check("初始不 active", s1.active is False)

    s1.draft = mk_draft()
    check("有 draft 后 active", s1.active is True)

    s1.clear()
    check("clear 后 draft 为空", s1.draft is None)
    check("clear 后 rounds 归零", s1.rounds == 0)
    check("clear 后 gap_key 清空", s1.gap_key == "")

    # 换话题要清 pending，否则旧追问一直挂着。
    # 用一句**短句闲聊**（上一版会被「长度<=12」误判成补槽，是真缺陷）。
    for chat_text in ("今天天气不错啊", "我有点累", "你好", "嗯嗯"):
        session = GateSession()
        session.draft = mk_draft()
        session.gap_key = "artifact"
        result = decide(chat_text, session=session, binary_answer="0")
        check(f"「{chat_text}」不被当补槽", result.kind == DECIDE_CHAT, result.kind)
        check(f"「{chat_text}」判 chat 时清掉 pending", session.active is False,
              "pending 还在")

    # 追问轮次累加：连续补槽三次应到 3
    session = GateSession()
    session.draft = mk_draft(artifact=Slot(
        value=ArtifactSpec(kind="file", format=""),
        source=SlotSource(kind="default", reason="x"), highlight=True),
        acceptance=(), permissions_required=(), budget=None)
    session.gap_key = "artifact"
    first = decide("md", session=session, binary_answer="1")
    check("第 1 次追问 rounds=1", session.rounds == 1, str(session.rounds))
    check("第 1 次是追问", first.kind == DECIDE_CLARIFY, first.kind)


# ---------- D 三态分派 ----------

def test_dispatch() -> None:
    print("[D 三态分派：ready 建单 / 缺信息追问 / rejected 拒绝]")

    # ready
    session = GateSession()
    result = decide("整理便笺", session=session, binary_answer="1",
                    parser_result=mk_result(mk_draft()))
    check("信息完备 -> delegate", result.kind == DECIDE_DELEGATE, result.kind)
    check("ready 后清空 pending", session.active is False, "pending 还在")

    # needs_clarification
    session = GateSession()
    result = decide("整理便笺", session=session, binary_answer="1",
                    parser_result=mk_result(mk_draft(artifact=None)))
    check("缺 artifact -> clarify", result.kind == DECIDE_CLARIFY, result.kind)
    check("clarify 带要念的问题", bool(result.question), "问题为空")
    check("clarify 记下 gap_key", session.gap_key == "artifact", session.gap_key)
    check("clarify 后 pending 还在", session.active is True)

    # rejected（追问超限仍缺）
    session = GateSession()
    session.draft = mk_draft(artifact=None)
    session.rounds = 3
    session.gap_key = "artifact"
    # 用超限的 round_index 直接跑闸门
    outcome = evaluate(session.draft, round_index=3)
    check("超限仍缺 -> 闸门 rejected", outcome.status == "rejected", outcome.status)

    # 判 rejected 时清 pending
    from server.contract.bridge import _from_outcome
    session.draft = mk_draft(artifact=None)
    decision = _from_outcome(outcome, session, DECIDE_DELEGATE, round_limit=3)
    check("rejected 的 kind", decision.kind == DECIDE_REJECTED, decision.kind)
    check("rejected 后清空 pending", session.active is False, "pending 还在")


# ---------- E 兜底 ----------

def test_fallback() -> None:
    print("[E 兜底：模型没答案 / 解析失败都必须退回正则，不能静默]")

    session = GateSession()
    result = decide("帮我把报告生成一下", session=session, binary_answer=None,
                    parser_result=mk_result(mk_draft()))
    check("无答案 -> fallback_to_regex", result.fallback_to_regex is True, result.kind)

    session = GateSession()
    result = decide("帮我把报告生成一下", session=session, binary_answer="1",
                    parser_result=mk_result(None))
    check("预判是一单但没 Draft -> fallback", result.fallback_to_regex is True,
          result.kind)
    check("fallback 的理由写明", bool(result.reason), "没写理由")

    session = GateSession()
    result = decide("随便", session=session, binary_answer="1", parser_result=None)
    check("parser_result=None -> fallback", result.fallback_to_regex is True)

    # 关键：fallback 时不能把 pending 清掉（用户还在追问中间）
    session = GateSession()
    session.draft = mk_draft()
    session.gap_key = "artifact"
    decide("很长的一句话不是补槽而且模型挂了这里要超过二十个字才行",
           session=session, binary_answer=None)
    check("fallback 不清 pending", session.active is True, "pending 被清了")


# ---------- F 边界 ----------

def test_boundary() -> None:
    print("[F 边界：不 import 业务模块、不碰网络、不调模型]")

    path = ROOT / "server" / "contract" / "bridge.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    forbidden = ("server.assistant", "server.memory", "server.dialogue",
                 "server.interaction", "server.persona", "server.app")
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == m or alias.name.startswith(m + ".")
                       for m in forbidden):
                    bad.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and any(
                    module == m or module.startswith(m + ".") for m in forbidden):
                bad.append(module)
    check("不 import 任何业务模块", not bad, str(bad))

    called = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name:
                called.append(name)
    # bridge 是纯逻辑层：模型调用由调用方做，它只解析答案
    check("不发起模型调用（不调 complete/complete_raw）",
          "complete" not in called and "complete_raw" not in called, str(called))
    check("不碰网络（无 httpx/requests）",
          "httpx" not in source and "requests" not in source)
    check("不调 freeze（冻结是闸门的事）", "freeze" not in called, "调了")

    # 允许调 evaluate —— 这是它必须做的（合成决定要跑闸门）
    check("确实调了 evaluate（这是它的职责）", "evaluate" in called, "没调")


def main() -> int:
    test_binary_parse()
    test_fill_slot()
    test_session()
    test_dispatch()
    test_fallback()
    test_boundary()

    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}")
        for item in failed[:20]:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
