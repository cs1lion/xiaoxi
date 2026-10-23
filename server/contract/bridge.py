# -*- coding: utf-8 -*-
"""第 9 步接线封装：把解析器 + 闸门接进对话路径的**可测试形态**。

    .\.venv\Scripts\python.exe scripts\test_intent_gate_bridge.py

本模块**不碰 `app.py`**。它提供三样东西，让接线那一步只剩「调用」：

1. `classify_intent()` —— 二分预判：这是不是一单。**要并行跑**，不能串行。
2. `GateSession`      —— 会话级追问状态：`round_index`、待补的 `Draft`、补槽。
3. `decide()`         —— 把上面两者合成一个决定：建单 / 追问 / 补槽 / 落普通对话。

为什么必须并行（实测，不是设计偏好）
------------------------------------
普通对话一轮基线 3.19s（最慢 4.20s）。二分预判平均 2.95s（最慢 5.99s）。
串行相加 = 6.14s，普通对话会被拖慢一倍。所以预判必须与回话**同时**起，
回话说完后如果预判也回来了再补一句。

为什么要有 GateSession（实测）
------------------------------
`app.py` 全仓搜 `round_index` / `clarif` **零命中**，`Connection` 上也没有
追问字段。而 `gate.evaluate` 靠 `round_index` 区分第几轮——不传它，
追问永远到不了上限、也永远不降级。

最容易被忽略的一条：第 2 轮用户答的「markdown」是**补槽**，不是新句子。
当新句子再解析会被判 chat，pending 被丢掉、追问白问。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# 二分预判的 prompt：只要一个词。判据与完整解析一致——
# delegate 看「有没有事情要被做掉」，不要求说清对象或交付物。
# 实测：step-3.5-flash 44/46、平均 2.95s；step-3.7-flash 44/46 但更慢（3.59s）
# 且会因思维链吃光 max_tokens 返回空串。
BINARY_PROMPT = """用户的话是不是在让别人去做一件事（委托）？只回答 1 或 0，不要解释。
1 = 有事情要被做掉（包括「这活得有人干一下」这类含糊说法）
0 = 闲聊、问进展、评价、让助手就地改措辞"""

# 推理模型必须给足 max_tokens：给 2 时思维链吃光额度、content 变空串（实测）
BINARY_MAX_TOKENS = 300

# 决策类型
DECIDE_DELEGATE = "delegate"      # 信息够，直接建单
DECIDE_CLARIFY = "clarify"        # 缺信息，要追问
DECIDE_REJECTED = "rejected"      # 追不上，拒绝
DECIDE_FILL = "fill_slot"         # 这一句是补上一轮的槽，不是新任务
DECIDE_CHAT = "chat"              # 不是一单，落普通对话


def parse_binary_answer(text: str) -> bool | None:
    """把二分预判的回答解析成 True/False/None。

    None 表示**没拿到可用答案**（空串、超时、乱答）——调用方应退回正则，
    不能当成「不是一单」（那会让该建单的句子静默掉）。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    head = cleaned[0]
    if head == "1":
        return True
    if head == "0":
        return False
    return None


@dataclass
class GateSession:
    """一个会话的追问状态。**按 session_id 分开存**，别挂 Connection。

    WebSocket 一断就丢，重连后追问上下文没了；放这里至少能由上层决定
    是持久化还是丢弃。
    """

    draft: Any = None                 # 待补的 Draft
    rounds: int = 0                   # 已问过几轮
    gap_key: str = ""                 # 上一轮问的是哪个缺口
    gap_check: str = ""               # G1..G6

    def clear(self) -> None:
        self.draft = None
        self.rounds = 0
        self.gap_key = ""
        self.gap_check = ""

    @property
    def active(self) -> bool:
        return self.draft is not None


class SessionStore:
    """按 session_id 存 `GateSession`。故意不做持久化——由调用方决定。"""

    def __init__(self) -> None:
        self._items: dict[str, GateSession] = {}

    def get(self, session_id: str) -> GateSession:
        return self._items.setdefault(session_id, GateSession())

    def drop(self, session_id: str) -> None:
        self._items.pop(session_id, None)

    def __len__(self) -> int:
        return len(self._items)


# ---------- 补槽：判断「这句话是不是在回答上一轮的追问」 ----------

def looks_like_slot_answer(text: str, gap_key: str) -> bool:
    """这句话像不像在补上一轮问的那个槽？

    **判据要保守**：宁可漏判（当新任务重解析，最多多问一次），
    也不要错判（把新任务或闲聊当补槽，把内容填进了错误的字段）。

    上一版用「长度 ≤12 就算补槽」，那是错的——实测「今天天气不错啊」
    （7 字）、「我有点累」（4 字）、「你好」（2 字）全都会中招。
    有 pending 时用户照样能说闲话，不能把闲话填进契约。

    现在只认**与所问槽位相关的内容**：

      artifact    要出现格式词（md/csv/json/txt/pdf/表格/文档…）
      permission  要出现权限等级（L0-L6）或「只读」「不用」
      acceptance  要出现验收措辞（就行/即可/为准/只要/能跑…）
      goal        太短的不像目标；要求带动词或明确对象

    都不匹配就返回 False —— 让它当新句子重新走一遍，最多多问一次。
    """
    cleaned = (text or "").strip()
    if not cleaned or not gap_key:
        return False
    # 太长的话不像补槽，更像新的一句话
    if len(cleaned) > 20:
        return False

    lowered = cleaned.lower()

    if gap_key == "artifact":
        formats = ("md", "markdown", "csv", "json", "txt", "pdf", "表格",
                   "文档", "excel", "xlsx", "报告", "图片", "png")
        return any(word in lowered for word in formats)

    if gap_key == "permission":
        if any(f"l{n}" in lowered for n in range(7)):
            return True
        return any(word in cleaned for word in ("只读", "不用", "不许", "可以写"))

    if gap_key == "acceptance":
        return any(word in cleaned
                   for word in ("就行", "即可", "为准", "只要", "能跑", "通过",
                                "不少于", "不超过", "至少", "完成"))

    if gap_key == "goal":
        # 目标要是个动宾短语；单个词/语气词不算
        verbs = ("整理", "生成", "导出", "写", "做", "改", "查", "跑", "清理",
                 "安排", "汇总", "翻译", "统计")
        return any(word in cleaned for word in verbs)

    if gap_key == "budget":
        return any(word in cleaned
                   for word in ("分钟", "小时", "默认", "随便", "不限", "快点"))

    # 未知的 gap_key：不猜，交给重新解析
    return False


def apply_slot_answer(draft: Any, gap_key: str, text: str) -> Any:
    """把补槽回答写进 Draft。**原地改，返回同一个 draft**。

    只处理能机械填的槽；填不了的（如 goal）保持原样，让闸门再问一次。
    """
    from .model import ArtifactSpec, Slot, SlotSource

    cleaned = (text or "").strip()
    if draft is None or not cleaned:
        return draft

    def as_user_slot(value: Any) -> Slot:
        # 补槽是用户刚说的，标 user_confirmed（§3.1 允许的四种来源之一），
        # 且必须带 turn_id，否则 freeze 会抛 ContractShapeError。
        return Slot(value=value,
                    source=SlotSource(kind="user_confirmed", turn_id="slot-answer"),
                    confirmed=True)

    if gap_key == "artifact":
        formats = {"md": "md", "markdown": "md", "csv": "csv", "json": "json",
                   "txt": "txt", "pdf": "pdf", "表格": "csv", "excel": "xlsx",
                   "xlsx": "xlsx", "文档": "md", "报告": "md"}
        fmt = ""
        lowered = cleaned.lower()
        for word, value in formats.items():
            if word in lowered:
                fmt = value
                break
        if fmt:
            current = draft.artifact.value if draft.artifact is not None else None
            kind = getattr(current, "kind", "") or "file"
            draft.artifact = as_user_slot(ArtifactSpec(kind=kind, format=fmt))
    elif gap_key == "permission":
        lowered = cleaned.lower()
        level = ""
        for candidate in ("l0", "l1", "l2", "l3", "l4", "l5", "l6"):
            if candidate in lowered:
                level = candidate.upper()
                break
        if not level and ("只读" in cleaned or "不用" in cleaned):
            level = "L0"
        if level:
            draft.permissions_required = (level,)
    return draft


# ---------- 合成决定 ----------

@dataclass(frozen=True)
class Decision:
    """一次对话该做什么。

    `kind` 是**这一轮实际该做的动作**（DECIDE_* 之一）。
    `from_slot_fill` 单独标出「这一轮是补上一轮的槽」——两者不是一回事：
    补槽之后可能还要再追问（kind=clarify），但调用方仍需知道这是接着上次说，
    而不是用户开了个新任务。
    """

    kind: str
    question: str = ""          # clarify 时要念出来的话
    draft: Any = None
    outcome: Any = None
    reason: str = ""            # 为什么这么判（可审计）
    fallback_to_regex: bool = False   # 模型不可用时为 True，调用方应走老路
    from_slot_fill: bool = False      # 这一轮是补槽（接着上次说）


def decide(text: str, *, session: GateSession,
           binary_answer: str | None,
           parser_result: Any | None = None,
           round_limit: int = 3) -> Decision:
    """把二分预判 + 解析结果 + 会话状态合成一个决定。

    参数
    ----
    binary_answer  二分预判的原始回答（"1"/"0"/空串/None）。None 或空串表示
                   **模型没给出可用答案**，此时 `fallback_to_regex=True`，
                   调用方应退回现有正则，绝不能当成「不是一单」。
    parser_result  `parse_payload()` 的结果。只在需要完整解析时传。

    判据顺序（重要）
    ----------------
    1. 有 pending 且这句像补槽 → 补槽，不重新解析（否则「markdown」会被判 chat）
    2. 二分预判说不是一单 → 落普通对话
    3. 二分预判没答案 → 退回正则
    4. 是一单 → 看解析结果：ready 建单 / needs_clarification 追问 / rejected 拒绝
    """
    # 1. 补槽优先：有 pending 时，「markdown」这种短回答必须当补槽
    if session.active and looks_like_slot_answer(text, session.gap_key):
        from .gate import evaluate

        apply_slot_answer(session.draft, session.gap_key, text)
        outcome = evaluate(session.draft, round_index=session.rounds + 1)
        session.rounds += 1
        return _from_outcome(outcome, session, DECIDE_FILL,
                             round_limit=round_limit)

    # 2. 明确说不是一单
    is_task = parse_binary_answer(binary_answer or "")
    if is_task is False:
        # 用户可能是在换话题：清掉 pending，否则旧的追问会一直挂着
        if session.active:
            session.clear()
        return Decision(kind=DECIDE_CHAT, reason="二分预判：不是一单")

    # 3. 没拿到答案 → 退回正则（不能当成不是一单）
    if is_task is None:
        return Decision(kind=DECIDE_CHAT, fallback_to_regex=True,
                        reason="二分预判无可用答案，退回正则")

    # 4. 是一单
    if parser_result is None or parser_result.draft is None:
        return Decision(kind=DECIDE_CHAT, fallback_to_regex=True,
                        reason="预判是一单但解析未产出 Draft，退回正则")

    from .gate import evaluate

    session.draft = parser_result.draft
    session.rounds = 0
    outcome = evaluate(session.draft, round_index=0)
    return _from_outcome(outcome, session, DECIDE_DELEGATE,
                         round_limit=round_limit)


def _from_outcome(outcome: Any, session: GateSession, base_kind: str,
                  *, round_limit: int) -> Decision:
    """把 `GateOutcome` 翻译成决定，并更新会话状态。

    `base_kind` 决定 `from_slot_fill`：补槽路径传 `DECIDE_FILL`，
    它补完可能还要再追问（kind=clarify），但调用方仍需知道这一轮是补槽。
    """
    from_fill = base_kind == DECIDE_FILL
    status = getattr(outcome, "status", "")
    if status == "ready":
        frozen = getattr(outcome, "contract", None)
        session.clear()          # 建单了，追问状态可以清
        return Decision(kind=DECIDE_DELEGATE, outcome=outcome, draft=frozen,
                        from_slot_fill=from_fill,
                        reason="信息完备，冻结成契约")
    if status == "rejected":
        session.clear()
        return Decision(kind=DECIDE_REJECTED, outcome=outcome,
                        from_slot_fill=from_fill,
                        reason=getattr(outcome, "reason", "") or "rejected")
    # needs_clarification
    gap = getattr(outcome, "gap", None)
    if gap is not None:
        session.gap_key = getattr(gap, "key", "")
        session.gap_check = getattr(gap, "check", "")
    rounds = getattr(outcome, "rounds_used", session.rounds)
    session.rounds = max(session.rounds, rounds)
    return Decision(kind=DECIDE_CLARIFY, outcome=outcome,
                    question=getattr(gap, "question", "") if gap else "",
                    from_slot_fill=from_fill,
                    reason=f"缺 {session.gap_check}/{session.gap_key}，第 "
                           f"{session.rounds} 轮追问")
