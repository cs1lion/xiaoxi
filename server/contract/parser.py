# -*- coding: utf-8 -*-
"""契约 v2 · 第 1 步：自由文本 → `Draft`（意图解析层）。

`gate.evaluate()` 吃的是结构化 `Draft`，用户说的是自由文本，中间缺一层。
本模块就是那一层：**只负责把一句话变成待检的草稿，不做任何判定**。
判定全在 `gate.evaluate()`（G1–G6 + §3.7 lint），本模块一行判定逻辑都不写。

设计上守四条线
--------------
1. **不判「这单做不做得成」**。只判「这是不是一单」——是就产 Draft，
   不是就返回 None。闸门不负责判断「这是不是一单」（实测：把「今天天气不错啊」
   当 goal 喂进去，闸门回的是 needs_clarification/G3 而不是拒绝），
   所以这件事必须在本层做完。
2. **不编造槽位**。模型给的每个槽位都要过「依据检查」：原话里没有线索就丢弃。
   实测教训：模型对「去把便笺整理成三版」编出 `deliver_to=self`，而 `deliver_to`
   一有值，G5（优先级最高）就会挡住 G3——拿编造的字段把更该问的问题挡掉了。
3. **不把推断标成 user_utterance**。模型抽的槽位一律 `default` + `reason`，
   并 `highlight=True`，让它在契约卡上可见。标成 `user_utterance` 等于伪造
   「这是用户说的」，会绕过 §3.4 的确认规则。
4. **零判定、零 IO**。本模块只 import `server.llm` 与本包内的 `model`，
   不碰 `server.assistant`/`dialogue`/`memory`/`persona`/`app`（由
   `scripts/test_contract.py` 的边界断言守着）。

实测准确率（`scripts/test_intent_parser_e2e.py`，真模型 step-3.7-flash）
  in-sample 31 句  30/31
  held-out  15 句  15/15   ← 未参与调参，以此为准
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .model import ArtifactSpec, Budget, CheckSpec, Criterion, InputRef, Slot, SlotSource

# 模型抽出来的槽位一律用这个 source.kind：它是"默认值/推断"，不是"用户说的"。
# §3.1 的四个取值里，default 是唯一既不冒充用户、又不需要记忆 id 的那个。
INFERRED = "default"

# 原话里出现这些词，才算用户交代了「交付给谁」。
# 判据是子串，故意保守：宁可漏掉线索让它变成缺口被追问，也不要凭空补一个交付对象。
_DELIVERY_CUES = ("发给", "交给", "送到", "传给他", "告诉他", "发我", "发到",
                  "给小汐", "上报", "通知")

# 用户明说格式的线索词。
_FORMAT_CUES = ("md", "markdown", "csv", "json", "txt", "pdf", "表格", "文档",
                "excel", "xlsx")

# 判定「这句话是不是一单」的四分类。delegate 才建单，其余都不建。
INTENT_DELEGATE = "delegate"
INTENT_QUERY = "query"
INTENT_CHAT = "chat"
INTENT_EDIT = "edit"
INTENTS = (INTENT_DELEGATE, INTENT_QUERY, INTENT_CHAT, INTENT_EDIT)

# 实测教训（写在这里，避免后来的人重蹈）：prompt 里如果写「做完有可交付的产物」，
# 模型会按「有没有明确交付物」判，12 句含糊派单漏 6 句。delegate 的判据必须是
# 「有没有事情要被做掉」，**不要求说清对象或交付物**——含糊不等于不是委托。
SYSTEM_PROMPT = """判断用户这句话属于哪类意图。严格输出 JSON：{"intent": "delegate|query|chat|edit", "goal": "动词+对象，不超过80字" 或 null, "artifact": {"kind": "...", "format": "..."} 或 null, "deliver_to": "self"/"third_party"/null, "acceptance": ["一条可核对的条件"], "confidence": 0.0-1.0}

意图分类：
- delegate：用户希望**某件事被做掉**，包括说得很含糊的委托
  （「这活得有人干一下」「麻烦你那边安排一下」「这份活儿交出去吧」都是 delegate）
- query：在问**已经存在的**任务的进展/状态（「到哪了」「弄完了吗」）
- chat：闲聊、寒暄、情绪、评价、确认（「嗯嗯」「好的」「确认」）
- edit：让助手**就地修改用户自己给的文字**（「把第二段改自然一点」「语气轻松一点」）

关键区分：
1. delegate 看「有没有事情要被做掉」，**不要求说清对象或交付物**。含糊不等于不是委托。
2. edit 与 delegate：改的是**当前这段文字本身**就是 edit；改外部的东西才是 delegate。
3. 单个词的回答（「确认」「好的」「嗯嗯」）是 chat。

artifact.kind 只能是：file, report_md, csv, code_patch, message, chart, other
artifact.kind=file 时必须给 format（md/csv/json/txt/pdf 之一）。

**只填原话里真的有的东西**：
- deliver_to 只有用户明确说「发给/交给/告诉谁」时才填，否则必须是 null。
  不要因为「任务做完总要给用户看」就填 self——那是你的假设，不是用户说的。
- format 只有用户说了格式才填，没说就留空字符串。
- acceptance 只有用户说了验收条件才给，不要自己编「结构清晰易读」这类。"""


@dataclass(frozen=True)
class ParseResult:
    """解析结果。`draft` 为 None 表示「这句话不是一单」。

    `dropped` 记录被依据检查丢掉的编造槽位——**这是可审计性的来源**：
    出了问题时能回答「模型本来想填什么、为什么没采用」，而不是只有一个黑盒结果。
    """

    draft: Any | None
    intent: str = ""
    dropped: tuple[str, ...] = ()
    confidence: float | None = None
    raw: str = ""
    error: str = ""

    @property
    def is_task(self) -> bool:
        return self.draft is not None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if 0.0 <= number <= 1.0:
            return number
    return None


def _has_any(text: str, cues: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in cues)


def _inferred_slot(value: Any, reason: str,
                   confidence: float | None) -> Slot:
    """模型推断的槽位：default + reason + highlight。

    **绝不返回 user_utterance**：那等于声称「这是用户说的」，
    会绕过 §3.4「非用户来源要显式确认」的规则。
    """
    return Slot(value=value,
                source=SlotSource(kind=INFERRED, reason=reason,
                                  confidence=confidence),
                highlight=True)


def _user_slot(value: Any, turn_id: str) -> Slot:
    """用户原话里确有的槽位。user_utterance 必须带 turn_id，否则 freeze 会抛。"""
    return Slot(value=value,
                source=SlotSource(kind="user_utterance", turn_id=turn_id))


def build_draft(utterance: str, parsed: dict, *, turn_id: str = "",
                contract_id: str = "", created_at: str = "",
                budget: Any = None) -> ParseResult:
    """把模型输出的 JSON 映射成 `Draft`。**纯函数，不调模型、不做判定。**

    形状校验全在这里，不在模型里：模型只负责抽字段，能不能变成 Draft 由本函数判。

    返回 `ParseResult`，`draft` 为 None 时 `intent`/`error` 说明原因。
    """
    if not isinstance(parsed, dict):
        return ParseResult(draft=None, error="模型输出不是对象")

    intent = str(parsed.get("intent") or "").strip()
    confidence = _as_float(parsed.get("confidence"))

    # 只有 delegate 才建单。query/chat/edit 都不建——这条是本层的核心判据。
    if intent != INTENT_DELEGATE:
        return ParseResult(draft=None, intent=intent, confidence=confidence,
                           error="" if intent in INTENTS else "intent 取值非法")

    goal_text = (parsed.get("goal") or "").strip() if isinstance(
        parsed.get("goal"), str) else ""
    if not goal_text:
        return ParseResult(draft=None, intent=intent, confidence=confidence,
                           error="delegate 但 goal 为空，无法建 Draft")

    dropped: list[str] = []

    # --- artifact：kind 必须合法；format 必须有原话依据 ---
    artifact_slot = None
    artifact = parsed.get("artifact")
    if isinstance(artifact, dict) and artifact.get("kind"):
        kind = str(artifact["kind"]).strip()
        fmt = str(artifact.get("format") or "").strip()
        if fmt and not _has_any(utterance, _FORMAT_CUES):
            dropped.append(f"artifact.format={fmt!r}（原话未提格式）")
            fmt = ""
        artifact_slot = _inferred_slot(
            ArtifactSpec(kind=kind, format=fmt), "模型抽取的交付物", confidence)

    # --- deliver_to：必须有交付线索，否则是编的 ---
    deliver_slot = None
    deliver = parsed.get("deliver_to")
    if deliver in ("self", "third_party"):
        if _has_any(utterance, _DELIVERY_CUES):
            deliver_slot = _user_slot(deliver, turn_id)
        else:
            dropped.append(f"deliver_to={deliver!r}（原话没有「发给谁」）")

    # --- acceptance：每条都要能在原话里找到影子 ---
    criteria: list[Criterion] = []
    for index, item in enumerate(parsed.get("acceptance") or []):
        if not (isinstance(item, str) and item.strip()):
            continue
        text = item.strip()
        # 至少有一个 2 字以上的片段与原话重合，才算有依据
        grounded = any(text[i:i + 2] in utterance for i in range(len(text) - 1))
        if not grounded:
            dropped.append(f"acceptance={text!r}（原话无依据）")
            continue
        criteria.append(Criterion(
            id=f"a{index + 1}", text=text, kind="deterministic",
            check=CheckSpec(type="file_exists_nonempty",
                            path=f"out/criterion-{index + 1}.md")))

    # --- inputs：只收形状合法的 ---
    inputs: list[Slot] = []
    for item in parsed.get("inputs") or []:
        if not isinstance(item, dict) or not item.get("uri"):
            continue
        inputs.append(_user_slot(
            InputRef(type=str(item.get("type") or "file"),
                     uri=str(item["uri"]), exists=None),
            turn_id))

    draft = _make_draft(
        utterance=utterance, goal_text=goal_text, artifact=artifact_slot,
        deliver_to=deliver_slot, acceptance=tuple(criteria),
        inputs=tuple(inputs), confidence=confidence, turn_id=turn_id,
        contract_id=contract_id, created_at=created_at, budget=budget)

    return ParseResult(draft=draft, intent=intent, dropped=tuple(dropped),
                       confidence=confidence)


def _make_draft(*, utterance: str, goal_text: str, artifact: Slot | None,
                deliver_to: Slot | None, acceptance: tuple[Criterion, ...],
                inputs: tuple[Slot, ...], confidence: float | None,
                turn_id: str, contract_id: str, created_at: str,
                budget: Any) -> Any:
    # 延迟 import：本模块被 import 时不拉起 gate 的整条依赖链
    from .gate import Draft

    return Draft(
        contract_id=contract_id,
        version=1,
        created_at=created_at,
        utterance=utterance,
        goal=_inferred_slot(goal_text, "模型抽取的目标", confidence),
        inputs=inputs,
        artifact=artifact,
        deliver_to=deliver_to,
        acceptance=acceptance,
        side_effects=(),
        # 权限**不猜**。§6.2 的 L0–L6 必须从任务语义推出来，
        # 编一个 "L0" 看着合法、实则替用户做了权限判断，且会被冻进契约。
        # 留空让 G6 去问（与 gate.fill_conservative_defaults 的处理一致）。
        permissions_required=(),
        budget=budget if budget is not None else Budget(),
    )


def parse_payload(payload: str, utterance: str, **kwargs: Any) -> ParseResult:
    """把模型返回的**原始文本**解析成 ParseResult。

    模型偶尔会在 JSON 外套 ```json 围栏，这里一并剥掉——但**不做宽松修补**：
    真解析不了就返回 error，让调用方决定是重试还是当闲聊。
    """
    text = (payload or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    if not text:
        return ParseResult(draft=None, error="模型返回空内容")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return ParseResult(draft=None, error=f"非 JSON: {exc}", raw=payload)
    result = build_draft(utterance, data, **kwargs)
    return ParseResult(draft=result.draft, intent=result.intent,
                       dropped=result.dropped, confidence=result.confidence,
                       raw=payload, error=result.error)
