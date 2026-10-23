# -*- coding: utf-8 -*-
"""第 1 步解析器（server/contract/parser.py）的单元测试。

    .\.venv\Scripts\python.exe scripts\test_intent_parser.py

**不调模型**：解析器本身是纯函数（模型 JSON 进、Draft 出），所以它的全部
逻辑都能用手写 JSON 覆盖。真模型那一半在 `test_intent_parser_e2e.py`。

为什么这两半必须分开
--------------------
如果只测端到端，一个失败分不清是「模型抽错了」还是「映射写错了」。
把映射单独测死，端到端失败就只剩一个可能：模型。

六组断言
--------
  A 意图分流   delegate 建单，query/chat/edit 一律不建单
  B 依据检查   模型编造的槽位必须被丢弃并记录（这是实测踩出来的缺陷）
  C 来源标记   模型抽的一律 default+highlight，绝不冒充 user_utterance
  D 形状校验   artifact 必须是 ArtifactSpec；形状非法不让建 Draft
  E 解析容错   ```json 围栏能剥、坏 JSON 不崩、空内容不崩
  F 边界       不 import 业务模块、不写判定逻辑
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.contract.gate import Draft, evaluate  # noqa: E402
from server.contract.model import ArtifactSpec  # noqa: E402
from server.contract.parser import (  # noqa: E402
    INTENT_CHAT, INTENT_DELEGATE, INTENT_EDIT, INTENT_QUERY,
    ParseResult, build_draft, parse_payload,
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


TURN = "turn-001"
NOW = "2026-10-23T18:00:00+08:00"


def mk(utterance: str, **over) -> ParseResult:
    payload = {"intent": INTENT_DELEGATE, "goal": "整理便笺",
               "artifact": {"kind": "report_md", "format": ""},
               "deliver_to": None, "acceptance": [], "confidence": 0.8}
    payload.update(over)
    return build_draft(utterance, payload, turn_id=TURN, created_at=NOW)


# ---------- A 意图分流 ----------

def test_intent_split() -> None:
    print("[A 意图分流：只有 delegate 建单]")

    result = mk("去把便笺整理成三版")
    check("delegate 建单", result.draft is not None, result.error)
    check("delegate 的 intent 正确", result.intent == INTENT_DELEGATE, result.intent)

    for intent, text in ((INTENT_QUERY, "那单到哪了"),
                         (INTENT_CHAT, "今天天气不错啊"),
                         (INTENT_EDIT, "把第二段改自然一点")):
        result = mk(text, intent=intent, goal=None)
        check(f"{intent} 不建单", result.draft is None,
              "居然建了单")
        check(f"{intent} 保留 intent 供上层记录", result.intent == intent, result.intent)

    # 非法 intent 也不能建单
    result = mk("随便说点", intent="nonsense")
    check("非法 intent 不建单", result.draft is None)
    check("非法 intent 带 error", bool(result.error), "没有说明原因")

    # delegate 但 goal 为空 -> 不能建单（否则 G1 会追问一个空目标）
    result = mk("去弄一下", goal=None)
    check("delegate 但 goal 空 -> 不建单", result.draft is None)
    check("goal 空带 error", "goal" in result.error, result.error)

    result = mk("去弄一下", goal="   ")
    check("goal 全空白 -> 不建单", result.draft is None)


# ---------- B 依据检查（实测踩出来的缺陷） ----------

def test_grounding() -> None:
    print("[B 依据检查：模型编造的槽位必须丢弃并记录]")

    # 这是原型第一版真实踩到的坑：模型对「去把便笺整理成三版」编 deliver_to=self
    result = mk("去把便笺整理成三版", deliver_to="self")
    check("无交付线索时 deliver_to 被丢弃",
          result.draft is not None and result.draft.deliver_to is None,
          str(result.draft.deliver_to if result.draft else None))
    check("丢弃被记录（可审计）",
          any("deliver_to" in item for item in result.dropped), str(result.dropped))

    # 原话真说了才保留
    result = mk("整理完发给我", deliver_to="self")
    check("有交付线索时 deliver_to 保留",
          result.draft is not None and result.draft.deliver_to is not None,
          str(result.dropped))
    if result.draft is not None and result.draft.deliver_to is not None:
        check("有线索的 deliver_to 是 user_utterance",
              result.draft.deliver_to.source.kind == "user_utterance",
              result.draft.deliver_to.source.kind)
        check("有线索的 deliver_to 带 turn_id",
              bool(result.draft.deliver_to.source.turn_id), "缺 turn_id")

    # format：原话没说就不许填
    result = mk("把周报弄一下", artifact={"kind": "file", "format": "csv"})
    check("原话未提格式时 format 被清空",
          result.draft is not None
          and result.draft.artifact is not None
          and result.draft.artifact.value.format == "",
          str(result.draft.artifact.value if result.draft and result.draft.artifact else None))
    check("format 丢弃被记录",
          any("format" in item for item in result.dropped), str(result.dropped))

    # 原话提了格式就保留
    result = mk("把周报导出成 CSV", artifact={"kind": "csv", "format": "csv"})
    check("原话提到 csv 时 format 保留",
          result.draft is not None
          and result.draft.artifact is not None
          and result.draft.artifact.value.format == "csv",
          str(result.draft.artifact.value if result.draft and result.draft.artifact else None))

    # acceptance：编的验收标准必须丢
    result = mk("把便笺整理一下",
                acceptance=["每个版本结构清晰易读", "便笺整理成三个版本"])
    texts = [c.text for c in (result.draft.acceptance if result.draft else ())]
    check("原话无依据的验收被丢弃",
          "每个版本结构清晰易读" not in texts, str(texts))
    check("原话有依据的验收被保留",
          "便笺整理成三个版本" in texts, str(texts))
    check("验收丢弃被记录",
          any("每个版本" in item for item in result.dropped), str(result.dropped))

    # 关键：丢弃 deliver_to 之后不能触发 G5，否则就挡住更该问的 G3
    result = mk("把便笺整理一下", deliver_to="self",
                artifact={"kind": "file", "format": ""})
    if result.draft is not None:
        out = evaluate(result.draft)
        check("丢弃编造 deliver_to 后不触发 G5",
              out.gap is None or out.gap.check != "G5",
              out.gap.check if out.gap else "无缺口")
        check("此时问的是 G3（交付物格式）",
              out.gap is not None and out.gap.check == "G3",
              out.gap.check if out.gap else "无缺口")


# ---------- C 来源标记 ----------

def test_source_marking() -> None:
    print("[C 来源标记：模型抽的一律 default，绝不冒充 user_utterance]")

    result = mk("去把便笺整理成三版")
    draft = result.draft
    check("有 draft", draft is not None)
    if draft is None:
        return

    check("goal 来源是 default", draft.goal.source.kind == "default",
          draft.goal.source.kind)
    check("goal 带 reason", bool(draft.goal.source.reason), "缺 reason")
    check("goal 被 highlight", draft.goal.highlight is True, "未高亮")

    check("artifact 来源是 default",
          draft.artifact.source.kind == "default", draft.artifact.source.kind)
    check("artifact 被 highlight", draft.artifact.highlight is True, "未高亮")

    # 最危险的一条：伪造 user_utterance 形状合法、闸门查不出
    check("goal 不是 user_utterance（否则等于伪造用户原话）",
          draft.goal.source.kind != "user_utterance", draft.goal.source.kind)

    # 高亮要能真的传到 GateOutcome
    out = evaluate(draft)
    check("模型抽的槽位进 high_lighted_slots",
          "goal" in out.high_lighted_slots, str(out.high_lighted_slots))

    # confidence 透传
    check("confidence 透传到 source",
          draft.goal.source.confidence == 0.8, str(draft.goal.source.confidence))


# ---------- D 形状校验 ----------

def test_shape() -> None:
    print("[D 形状校验：artifact 必须是 ArtifactSpec]")

    result = mk("整理便笺", artifact={"kind": "report_md", "format": ""})
    check("artifact 是 ArtifactSpec 实例",
          result.draft is not None
          and isinstance(result.draft.artifact.value, ArtifactSpec),
          type(result.draft.artifact.value).__name__ if result.draft else "无")

    # 裸 dict 会过 G3 但 freeze 抛——所以绝不能吐 dict
    check("绝不是裸 dict",
          result.draft is not None
          and not isinstance(result.draft.artifact.value, dict), "吐了 dict")

    # 没有 artifact 时也不能崩，要留给 G3
    result = mk("整理便笺", artifact=None)
    check("artifact 缺失时不崩且留空",
          result.draft is not None and result.draft.artifact is None,
          str(result.draft.artifact if result.draft else None))
    if result.draft is not None:
        out = evaluate(result.draft)
        check("缺 artifact 时闸门问 G3",
              out.gap is not None and out.gap.check == "G3",
              out.gap.check if out.gap else "无缺口")

    # 非法 artifact 形状（不是 dict）也不能崩
    for bad in ("report_md", 123, ["kind"]):
        result = mk("整理便笺", artifact=bad)
        check(f"artifact={type(bad).__name__} 不崩",
              result.draft is not None and result.draft.artifact is None,
              str(result.draft.artifact if result.draft else "无 draft"))

    # 完整形状必须能冻结（否则接线时会在 freeze 崩）
    # 注意：contract_id / created_at 是**调用方**该填的（解析层不生成 id），
    # 所以这里显式传，别让测试替解析器补职责。
    result = mk("把季度报告生成一下，要 md 格式",
                artifact={"kind": "file", "format": "md"},
                acceptance=["要 md 格式"])
    if result.draft is not None:
        result.draft.contract_id = "c-0001"
        result.draft.created_at = NOW
        result.draft.permissions_required = ("L0",)
        out = evaluate(result.draft)
        check("形状完整时能走到 ready 或只剩权限缺口",
              out.status == "ready" or (out.gap is not None),
              out.status)

    # permissions 必须留空（不替用户猜权限）
    result = mk("整理便笺")
    check("permissions_required 留空",
          result.draft is not None
          and result.draft.permissions_required == (),
          str(result.draft.permissions_required if result.draft else None))


# ---------- E 解析容错 ----------

def test_payload() -> None:
    print("[E 解析容错：围栏、坏 JSON、空内容]")

    good = json.dumps({"intent": "delegate", "goal": "整理便笺",
                       "artifact": None, "deliver_to": None,
                       "acceptance": [], "confidence": 0.7},
                      ensure_ascii=False)

    result = parse_payload(good, "整理便笺", turn_id=TURN, created_at=NOW)
    check("纯 JSON 能解析", result.draft is not None, result.error)

    fenced = "```json\n" + good + "\n```"
    result = parse_payload(fenced, "整理便笺", turn_id=TURN, created_at=NOW)
    check("```json 围栏能剥掉", result.draft is not None, result.error)

    result = parse_payload("这不是 JSON", "整理便笺")
    check("坏 JSON 不崩", result.draft is None)
    check("坏 JSON 带 error", "JSON" in result.error, result.error)

    result = parse_payload("", "整理便笺")
    check("空内容不崩", result.draft is None)
    check("空内容带 error", bool(result.error), "没有说明")

    result = parse_payload("   ", "整理便笺")
    check("全空白不崩", result.draft is None)

    # 模型返回数组而不是对象
    result = parse_payload("[1,2,3]", "整理便笺")
    check("数组不崩", result.draft is None)

    result = parse_payload("null", "整理便笺")
    check("null 不崩", result.draft is None)

    # raw 保留原文，便于归因
    result = parse_payload("这不是 JSON", "整理便笺")
    check("失败路径保留 raw（可审计）", result.raw == "这不是 JSON", result.raw)

    # 成功路径也必须留 raw——只测失败路径会漏掉「成功时把原文丢了」这种改法
    result = parse_payload(good, "整理便笺", turn_id=TURN, created_at=NOW)
    check("成功路径保留 raw（可审计）", result.raw == good, result.raw[:40])


# ---------- F 边界 ----------

def test_boundary() -> None:
    print("[F 边界：不 import 业务模块、不写判定逻辑]")

    parser_path = ROOT / "server" / "contract" / "parser.py"
    tree = ast.parse(parser_path.read_text(encoding="utf-8"),
                     filename=str(parser_path))

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

    source = parser_path.read_text(encoding="utf-8")
    # 解析层不做判定：出现这些就说明判定逻辑被搬进来了。
    # **必须查 AST 而不是查字符串**：注释里提到 gate.evaluate() 是说明文字，
    # 用 in 判断会把注释也算成调用，那种断言答不上「什么改动会让它红」。
    called: list[str] = []
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
    check("不调用 evaluate（判定不在这里）",
          "evaluate" not in called, str([c for c in called if c == "evaluate"]))
    check("不调用 collect_gaps", "collect_gaps" not in called, "调用了")
    check("不调用 freeze（冻结是闸门的事）", "freeze" not in called, "调用了")
    check("不含网络调用（模型调用由调用方注入）",
          "httpx" not in source and "requests" not in source)

    # 本模块不该被 contract/__init__ 自动拉起（避免 gate 依赖链变化）
    init_source = (ROOT / "server" / "contract" / "__init__.py").read_text(
        encoding="utf-8")
    check("__init__ 不导出 parser（保持原有导出面）",
          "parser" not in init_source, "被导出了")


def main() -> int:
    test_intent_split()
    test_grounding()
    test_source_marking()
    test_shape()
    test_payload()
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
