# -*- coding: utf-8 -*-
"""契约层验证台：第 1 步解析器必须满足的「Draft 形状契约」。

    .\.venv\Scripts\python.exe scripts\test_intent_parser_contract.py

它验什么、不验什么
-------------------
**不验第 1 步本身**——自由文本 → Draft 的解析器还不存在，模型额度也 402。
本文件验的是**第 1 步的输出必须长什么样**：把「一句话已被解析成 Draft」这个前提
写死成可执行断言，喂进真 `gate.evaluate()`，钉死它该落在哪个缺口。

所以它同时是**第 1 步的验收清单**：解析器写完后，同一批断言换个来源（模型输出
而不是手写 Draft）再跑一遍，对不上就红。这样「模型输出看着挺像」不能代替通过。

三组断言
--------
  A 字段阶梯     信息每补一格，闸门露出的缺口按 GAP_PRIORITY 走
  B 形状硬约束   第 1 步吐错类型/漏 source 字段时会怎样（freeze 才是唯一真关口）
  C 闲聊不出 Draft 闸门不拒闲聊，只追问 G3——所以「不建单」必须由第 1 步自己判

C 组是最容易被忽略的一条：把「今天天气不错啊」当 goal 喂进去，闸门回的是
`needs_clarification / G3`，而不是拒绝。**闸门不负责判断「这是不是一单」。**
把这件事交给闸门，结果是每句闲聊都被追问「做出来的东西要什么样？」。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract.gate import (  # noqa: E402
    GAP_PRIORITY, GATE_NEEDS_CLARIFICATION, GATE_READY, GATE_REJECTED,
    MAX_CLARIFICATION_ROUNDS, Draft, ContractShapeError, evaluate,
)
from server.contract.model import (  # noqa: E402
    ArtifactSpec, Budget, CheckSpec, Criterion, InputRef, SideEffect, Slot, SlotSource,
)

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# ---------- 构造工具：Draft 的每个槽位都必须带合法 source ----------

TURN = "turn-0001"
NOW = "2026-10-23T16:00:00+08:00"


def user_slot(value: Slot) -> Slot:
    """用户原话槽位。**必须带 turn_id**，否则 freeze 抛 ContractShapeError。"""
    return Slot(value=value, source=SlotSource(kind="user_utterance", turn_id=TURN))


def model_slot(value, reason: str) -> Slot:
    """模型推断的槽位。标 default + reason，**绝不能标 user_utterance**。"""
    return Slot(value=value,
                source=SlotSource(kind="default", reason=reason),
                highlight=True)


def goal(text: str) -> Slot:
    return user_slot(text)


def artifact(kind: str, fmt: str = "") -> Slot:
    return user_slot(ArtifactSpec(kind=kind, format=fmt))


def acceptance_ok() -> tuple:
    return (Criterion(id="a1", text="输出文件存在且非空", kind="deterministic",
                      check=CheckSpec(type="file_exists_nonempty", path="out/report.md")),)


def base(**over) -> Draft:
    """信息完备的 Draft。任何一项被覆盖成「缺失」就能量出对应缺口。"""
    fields = dict(
        contract_id="c-0001", version=1, created_at=NOW,
        utterance="整理一下便笺",
        goal=goal("整理便笺"),
        inputs=(),
        artifact=artifact("report_md"),
        deliver_to=None,
        acceptance=acceptance_ok(),
        side_effects=(),
        permissions_required=("L0",),
        budget=Budget(),
    )
    fields.update(over)
    return Draft(**fields)


def gap_of(draft: Draft, round_index: int = 0):
    out = evaluate(draft, round_index=round_index)
    return out, (out.gap.check, out.gap.key) if out.gap else None


# ---------- A 字段阶梯 ----------

def test_ladder() -> None:
    print("[A 字段阶梯：每补一格，缺口按 GAP_PRIORITY 后退]")

    out, gap = gap_of(base(artifact=None, acceptance=(), budget=None,
                           permissions_required=()))
    check("只缺 artifact 时先问 G3", gap == ("G3", "artifact"), str(gap))

    out, gap = gap_of(base(acceptance=(), budget=None, permissions_required=()))
    check("有 artifact 后问 G4 验收", gap == ("G4", "acceptance"), str(gap))

    out, gap = gap_of(base(budget=None, permissions_required=()))
    check("有验收后问 G6 预算", gap == ("G6", "budget"), str(gap))

    out, gap = gap_of(base(permissions_required=()))
    check("有预算后问 G6 权限", gap == ("G6", "permission"), str(gap))

    # file 类必须带 format —— 这是「交付物没定死」的第二形态
    out, gap = gap_of(base(artifact=artifact("file")))
    check("file 无 format 仍问 G3", gap == ("G3", "artifact"), str(gap))
    out, gap = gap_of(base(artifact=artifact("file", "md")))
    check("file 带 format 就过 G3", gap is None or gap[0] != "G3", str(gap))

    # deliver_to 有值就必须声明 send_message 副作用（§3.2 穷举）
    out, gap = gap_of(base(deliver_to=user_slot("self")))
    check("deliver_to 无副作用 -> G5", gap == ("G5", "side_effect"), str(gap))

    effect = SideEffect(id="e1", type="send_message", target="self",
                        target_class="self", reversible=True,
                        idempotency_key="k-1")
    out, gap = gap_of(base(deliver_to=user_slot("self"), side_effects=(effect,)))
    check("deliver_to + 声明 send_message -> 无 G5", gap is None or gap[0] != "G5", str(gap))

    # 信息全给 -> ready
    out = evaluate(base())
    check("信息完备 -> ready", out.status == GATE_READY, out.status)
    check("ready 必须带 contract", out.contract is not None, "contract 为空")
    if out.contract is not None:
        check("ready 的契约已算 risk", bool(out.contract.risk.level), "risk 为空")
        check("只读任务 risk=low", out.contract.risk.level == "low",
              out.contract.risk.level)

    # 优先级：同时缺 artifact 与 acceptance，先问 artifact
    out, gap = gap_of(base(artifact=None, acceptance=(), budget=None,
                           permissions_required=()))
    check("多缺口时按 GAP_PRIORITY 只问第一个",
          gap == ("G3", "artifact"), str(gap))
    check("但 gaps 里收了全部缺口", len(out.gaps) >= 3, f"只有 {len(out.gaps)} 个")

    # 优先级表本身要能回答「什么改动会让它红」：改 GAP_PRIORITY 顺序 -> 上面会红
    check("GAP_PRIORITY 顺序是规范 §3.3 的",
          GAP_PRIORITY[:4] == ("side_effect", "input", "artifact", "acceptance"),
          str(GAP_PRIORITY))


# ---------- B 形状硬约束 ----------

def test_shape() -> None:
    print("[B 形状硬约束：第 1 步吐错类型时的真实后果]")

    # artifact 必须是 ArtifactSpec 实例，不能是裸 dict
    bad = base()
    bad.artifact = user_slot({"kind": "report_md"})
    out, gap = gap_of(bad)
    check("裸 dict 当 artifact 被 G3 拦", gap == ("G3", "artifact"), str(gap))

    # 正确形状必须能冻结
    out = evaluate(base())
    check("ArtifactSpec 实例能冻结", out.status == GATE_READY, out.status)

    # source 漏 turn_id：G1-G6 全过，freeze 才抛 —— 这是最危险的一种
    leaky = base()
    leaky.goal = Slot(value="整理便笺",
                      source=SlotSource(kind="user_utterance"))  # 故意漏 turn_id
    try:
        evaluate(leaky)
        raised = ""
    except ContractShapeError as exc:
        raised = str(exc)
    check("source 漏 turn_id 会抛 ContractShapeError", bool(raised), "居然没抛")
    check("抛错信息点名 turn_id", "turn_id" in raised, raised[:80])

    # 所以：第 1 步不能只标 kind，必须把 source 的必填项填齐
    problems: list[str] = []
    SlotSource.from_mapping({"kind": "user_utterance"}, where="goal.source",
                            problems=problems)
    check("user_utterance 缺 turn_id 会被记问题",
          any("turn_id" in p for p in problems), str(problems))

    problems = []
    SlotSource.from_mapping({"kind": "default"}, where="goal.source", problems=problems)
    check("default 缺 reason 会被记问题",
          any("reason" in p for p in problems), str(problems))

    problems = []
    SlotSource.from_mapping({"kind": "memory"}, where="goal.source", problems=problems)
    check("memory 缺 memory_id/confidence 会被记问题",
          any("memory_id" in p for p in problems) and
          any("confidence" in p for p in problems), str(problems))

    # 模型猜的槽位绝不能标 user_utterance —— 这条要能机械判定
    guessed = base()
    guessed.artifact = Slot(
        value=ArtifactSpec(kind="report_md"),
        source=SlotSource(kind="user_utterance", turn_id=TURN))
    # 用户没说过「报告」时若标成 user_utterance，就等于伪造「你说的」。
    # 闸门查不出这件事（它只查形状），所以必须由第 1 步自己守，且此处锁住判据：
    check("伪造 user_utterance 形状合法、闸门查不出（故须自守）",
          evaluate(guessed).status == GATE_READY, "闸门居然拦了")

    # 降级路径：追问超限后 budget 会被填，但 goal/artifact 填不了 -> rejected
    out = evaluate(base(artifact=None), round_index=MAX_CLARIFICATION_ROUNDS)
    check("超限后仍缺 artifact -> rejected",
          out.status == GATE_REJECTED, out.status)
    check("rejected 带 reason", bool(out.reason), "reason 为空")

    # 超限后只缺 budget：能填，ready 但必须显式确认
    out = evaluate(base(budget=None), round_index=MAX_CLARIFICATION_ROUNDS)
    check("超限后只缺 budget -> 降级 ready", out.status == GATE_READY, out.status)
    check("降级 ready 必须标显式确认",
          out.degraded_requires_explicit_confirmation is True,
          str(out.degraded_requires_explicit_confirmation))

    # 未到上限时必须追问，不能提前降级
    out = evaluate(base(budget=None), round_index=MAX_CLARIFICATION_ROUNDS - 1)
    check("未到上限 -> needs_clarification",
          out.status == GATE_NEEDS_CLARIFICATION, out.status)


# ---------- C 闲聊不得产出 Draft ----------

C_CHITCHAT = [
    "今天天气不错啊", "帮我理一理思路", "我有点累", "你说我该不该换工作",
    "就是第二个", "你好", "确认", "下午三点",
    "把第二段改自然一点", "语气轻松一点", "结尾改具体一点",
]


def test_chitchat() -> None:
    print("[C 闲聊：闸门不拒闲聊，所以第 1 步必须自己判「这不是一单」]")

    # 事实断言：把闲聊原句当 goal 喂进去，闸门**不会**拒绝
    for text in C_CHITCHAT:
        d = Draft(utterance=text, goal=goal(text))
        out = evaluate(d)
        check(f"闸门不拒闲聊 {text}",
              out.status == GATE_NEEDS_CLARIFICATION,
              f"实际 {out.status}")

    # 由此得出的硬约束：第 1 步必须能说「这句不产出 Draft」。
    # 这里不需要再加一条单独断言——上面 11 条已经锁住了判据本身：
    # 若哪天有人把「闲聊过滤」挪进闸门（让它回 rejected），那 11 条会先红。
    # 下面用一个真实反例把边界钉死：闲聊能一路走到 ready，只要硬塞齐字段。
    d = base(utterance="今天天气不错啊", goal=goal("今天天气不错啊"))
    out = evaluate(d)
    check("硬塞齐字段的闲聊会被冻成契约（更证明过滤必须在前置层）",
          out.status == GATE_READY, out.status)


# ---------- D 来源标记：模型补的必须可见 ----------

def test_source_marking() -> None:
    print("[D 来源标记：模型补的槽位必须 highlight，不能静默改写]")

    d = base()
    d.artifact = model_slot(ArtifactSpec(kind="report_md"), "用户说了整理，格式未定")
    out = evaluate(d)
    check("模型补的槽位进 high_lighted_slots",
          "artifact" in out.high_lighted_slots, str(out.high_lighted_slots))

    d = base()
    out = evaluate(d)
    check("用户明说的槽位不高亮",
          "artifact" not in out.high_lighted_slots, str(out.high_lighted_slots))

    # 置信度落在确认档也要高亮（§3.5）
    d = base()
    d.goal = Slot(value="整理便笺",
                  source=SlotSource(kind="memory", memory_id="m-1", confidence=0.55))
    out = evaluate(d)
    check("低置信度记忆补的槽位要高亮",
          "goal" in out.high_lighted_slots, str(out.high_lighted_slots))


def main() -> int:
    test_ladder()
    test_shape()
    test_chitchat()
    test_source_marking()

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
