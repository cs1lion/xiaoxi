"""契约 v2 · §3.2 字段约束 + §3.3 意图完备度拦截器（IntentGate）。

规范原文（§3.3 流程）：

1. 解析话语，生成 ContractDraft（goal、inputs、artifact、deliver_to 候选）。
2. 记忆槽位补全（3.5）。
3. 运行检查 G1 到 G6。
4. 全部通过返回 ready；否则选出"影响最大的一个缺口"发起追问。

**本模块只做第 3、4 步。** 第 1 步（从话语解析出 draft）属于 NLU，需要模型，
不在 §3 的可判定范围内；第 2 步（记忆补全）由 §3.5 的 `SilentFillPolicy` 决定策略，
但真正读记忆库是调用方的事。所以 `evaluate()` 接的是**已经补全好的 draft**。

边界纪律（见 docs/CONTRACT_V2_SCOPE.md）：
- G2 的「输入可解析」需要 stat/探测 —— 本模块只读 `InputRef.exists` 字段，
  由调用方先探测再填。没探测（`exists is None`）时按**未证实**处理并追问，
  不假设存在。
- G6 的权限等级校验：v2 规范 §6.2 给出了 L0–L6 七级表（原 §7.1 的占位由它取代），
  并明写「G6 从『非空字符串』升级为『必须在本表内』」。所以 G6 现在既查非空，
  也查每个等级是否在这七级闭集里。等级表只此一份，见 PERMISSION_LEVELS。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .lint import lint_acceptance
from .model import (
    ContractShapeError, Criterion, InputRef, SideEffect, Slot, TaskContract, parse_contract,
    slot_to_mapping,
)
from .risk import compute_risk

# §3.3 追问策略：总轮数上限 3
MAX_CLARIFICATION_ROUNDS = 3
# §3.2 goal：「一句话，形如"动词 + 对象"，不超过 80 字」
MAX_GOAL_CHARS = 80

# §6.2 的七级权限表（规范 :956-964）。这是 permissions_required 的**取值域**，
# 也是 G6 的判定依据（规范 :954 原话：「G6 从『非空字符串』升级为『必须在本表内』」）。
#
# 七级逐个列，不用 range 或推导式生成：这是一张**闭集**，少写一级就是多放行一类
# 越权声明，而循环生成时删掉一项在源码里看不出来。
#
# L6 的边界（刻意写在这里，否则下一个人会「顺手」把它也拦掉）：
#   L6 **可声明、不可默认授予**。「默认不可授予 / 需人工逐次批准」是**授予策略**，
#   在运行时判（谁在什么条件下能拿到 L6），不在 G6 判。G6 判的是「这个等级名合不合法」——
#   声明 L6 是合法的，所以这里必须放行。两件事不能合并，合并就等于用契约校验
#   代替了运行时授权，而后者才是真正防越权的那一道。
PERMISSION_LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4", "L5", "L6")
PERMISSION_LEVEL_SET = frozenset(PERMISSION_LEVELS)

GATE_READY = "ready"
GATE_NEEDS_CLARIFICATION = "needs_clarification"
GATE_REJECTED = "rejected"

# §2.2 的 rejected 三个理由。
REJECT_UNVERIFIABLE = "unverifiable_criteria"
REJECT_UNSAFE = "unsafe"
REJECT_OUT_OF_SCOPE = "out_of_scope"
REJECT_REASONS = (REJECT_UNVERIFIABLE, REJECT_UNSAFE, REJECT_OUT_OF_SCOPE)

# §3.3 追问排序：「按影响排序：副作用槽位 > 输入源 > 交付物 > 验收标准 > 其他」
GAP_PRIORITY = (
    "side_effect",      # 副作用槽位
    "input",            # 输入源
    "artifact",         # 交付物
    "acceptance",       # 验收标准
    "goal",             # 其他（目标/预算/权限）
    "budget",
    "permission",
)


@dataclass(frozen=True)
class Gap:
    """一个缺口。`key` 决定追问优先级，`question` 是可以直接念出来的一句话。"""

    check: str                      # G1..G6
    key: str                        # GAP_PRIORITY 里的键
    question: str
    missing: tuple[str, ...] = ()
    options: tuple[dict[str, str], ...] = ()

    def priority(self) -> int:
        try:
            return GAP_PRIORITY.index(self.key)
        except ValueError:
            return len(GAP_PRIORITY)


@dataclass(frozen=True)
class GateResult:
    status: str
    contract: TaskContract | None = None
    gap: Gap | None = None
    missing: tuple[str, ...] = ()
    reason: str = ""
    high_lighted_slots: tuple[str, ...] = ()


@dataclass
class Draft:
    """待检的契约草案。

    刻意做成可变容器而不是完整 TaskContract：gate 检查的就是「还没冻结的东西」，
    而且 §3.3 允许追问超限后用最保守默认值填充再进 PROPOSED。
    """

    contract_id: str = ""
    version: int = 1
    created_at: str = ""
    utterance: str = ""
    goal: Slot | None = None
    inputs: tuple[Slot, ...] = ()
    artifact: Slot | None = None
    deliver_to: Slot | None = None
    acceptance: tuple[Criterion, ...] = ()
    side_effects: tuple[Any, ...] = ()
    permissions_required: tuple[str, ...] = ()
    budget: Any = None
    extra: dict = field(default_factory=dict)


# ---------- G1 目标清晰 ----------


def _check_goal(draft: Draft) -> Gap | None:
    """G1：goal 能写成「动词 + 对象」，且只有一个主目标；不超过 80 字。"""
    if draft.goal is None:
        return Gap(check="G1", key="goal", missing=("goal",),
                   question="这单要做的到底是什么？给我一句话就够。")
    value = draft.goal.value
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return Gap(check="G1", key="goal", missing=("goal",),
                   question="这单要做的到底是什么？给我一句话就够。")
    if len(text) > MAX_GOAL_CHARS:
        return Gap(
            check="G1", key="goal", missing=("goal",),
            question=f"目标太长（{len(text)} 字，上限 {MAX_GOAL_CHARS}）。用一句话说清主要要做的事：")
    if len(text) < 2:
        return Gap(check="G1", key="goal", missing=("goal",),
                   question="目标太短了，具体要做什么？")
    return None


# ---------- G2 输入可解析 ----------


def _check_inputs(draft: Draft) -> Gap | None:
    """G2：每个 input 的 exists 为 true，或类型为 text。

    「无输入的任务才允许为空」（§3.2）。所以空 inputs 是合法的，
    只要 artifact 不是需要输入的产物——这个判断留给 G3/调用方，不在这里猜。
    """
    unresolved: list[str] = []
    for index, slot in enumerate(draft.inputs):
        value = slot.value
        if not isinstance(value, InputRef):
            unresolved.append(f"inputs[{index}] 形状不对")
            continue
        if value.type == "text":
            continue
        if value.exists is True:
            continue
        if value.exists is None:
            unresolved.append(f"{value.uri}（未探测）")
        else:
            unresolved.append(f"{value.uri}（不存在）")
    if not unresolved:
        return None
    return Gap(
        check="G2", key="input", missing=tuple(unresolved),
        question="这几个输入我找不到，确认一下：" + "、".join(unresolved[:3]) +
                 "。给我准确路径或换一个？",
        options=({"label": "我给你路径", "value": "provide_path"},
                 {"label": "用我最近改过的那个", "value": "use_recent"}))


# ---------- G3 交付物明确 ----------


def _check_artifact(draft: Draft) -> Gap | None:
    """G3：artifact.kind 已确定，file 类必须有 format。"""
    from .model import ARTIFACT_KINDS

    if draft.artifact is None:
        return Gap(check="G3", key="artifact", missing=("artifact",),
                   question="做出来的东西要什么样？文件、报告，还是消息？")
    value = draft.artifact.value
    kind = getattr(value, "kind", "")
    fmt = getattr(value, "format", "")
    if kind not in ARTIFACT_KINDS:
        return Gap(check="G3", key="artifact", missing=("artifact",),
                   question="交付物的类型没定，是文件、报告、表格还是消息？")
    if kind == "file" and not fmt:
        return Gap(check="G3", key="artifact", missing=("artifact",),
                   question=f"文件要什么格式？" + "、".join(("md", "csv", "json", "txt", "pdf")) +
                            " 里挑一个，或者说别的。",
                   options=({"label": "markdown", "value": "format:md"},
                            {"label": "csv", "value": "format:csv"},
                            {"label": "json", "value": "format:json"}))
    return None


# ---------- G4 验收标准可验 ----------


def _check_acceptance(draft: Draft) -> Gap | None:
    """G4：至少 1 条 required 标准，且全部通过 §3.7 的 lint。"""
    if not draft.acceptance:
        return Gap(check="G4", key="acceptance", missing=("acceptance",),
                   question="怎么算做完了？给一条能核对的条件，我来写检查。")
    result = lint_acceptance(draft.acceptance)
    if result.ok:
        return None
    messages = tuple(str(problem) for problem in result.problems)
    return Gap(check="G4", key="acceptance", missing=messages,
               question="验收标准有问题，我改不了你的意思，得你定：" + messages[0])


# ---------- G5 风险与副作用已评估 ----------


def _check_risk(draft: Draft) -> Gap | None:
    """G5：side_effects 穷举完毕，risk 由 §3.4 规则算出。

    副作用为空是合法的（只读任务 → low），所以这里只拦「声明了副作用但形状不对」。
    """
    effects = tuple(draft.side_effects)
    for index, effect in enumerate(effects):
        # Draft 只收 SideEffect 实例。裸 dict 在 risk_of_side_effect 里会 AttributeError
        # 直接崩掉，而且更糟的是：若哪天用 getattr 兜底，一条 third_party 发消息的
        # 副作用会被洗成全空字段 → compute_risk 算出 low。两道防护都上。
        if not isinstance(effect, SideEffect):
            return Gap(check="G5", key="side_effect", missing=(f"side_effects[{index}]",),
                       question="副作用没写清楚（必须是 SideEffect），这条任务会动到别人的东西吗？")
        kind = getattr(effect, "type", "")
        if not kind:
            return Gap(check="G5", key="side_effect", missing=(f"side_effects[{index}]",),
                       question="这条任务会动到别人的东西吗？会发消息、改外部文件或花钱吗？")
        if not getattr(effect, "reversible", None) in (True, False):
            return Gap(check="G5", key="side_effect", missing=(f"side_effects[{index}]",),
                       question="这个动作能不能撤回？撤不回的我得先跟你确认。")
        if not getattr(effect, "idempotency_key", ""):
            return Gap(check="G5", key="side_effect", missing=(f"side_effects[{index}]",),
                       question="这条动作缺防重复的标识，我先补上，你确认一下要不要真的执行。")
    # §3.2「side_effects 必须穷举」。`deliver_to` 有值就意味着"要交付给谁"，
    # 必然产生一条 send_message 副作用。没声明就是没穷举——否则一条
    # 「发给 webhook」的契约会被算成 risk=low（实测过）。
    if draft.deliver_to is not None and not any(
            getattr(effect, "type", "") in ("send_message", "http_write")
            for effect in effects):
        return Gap(check="G5", key="side_effect", missing=("side_effects",),
                   question="这单要发出去，但没声明发送动作。发给谁、能不能撤回？",
                   options=({"label": "发给我自己", "value": "target_class:self"},
                            {"label": "发给别人", "value": "target_class:third_party"},
                            {"label": "不发了，只落文件", "value": "no_delivery"}))
    return None


# ---------- G6 预算与权限已声明 ----------


def _check_budget_permission(draft: Draft) -> Gap | None:
    """G6：budget 必填，permissions_required 必须是 §6.2 的 L0–L6。

    三个检查按「先形状、后取值」排：budget 缺失要问标准问题，空项/非字符串是
    结构错误（问法不同），最后才是等级是否在这七级闭集里。
    """
    if draft.budget is None:
        return Gap(check="G6", key="budget", missing=("budget",),
                   question="这单最多允许跑多久、重试几次？不给我就用默认值。")
    if not draft.permissions_required:
        return Gap(check="G6", key="permission", missing=("permissions_required",),
                   question="这单需要哪些权限？不给的话我按只读处理。")
    for index, item in enumerate(draft.permissions_required):
        if not isinstance(item, str) or not item.strip():
            return Gap(check="G6", key="permission",
                       missing=(f"permissions_required[{index}]",),
                       question="权限列表里有空项，确认一下需要哪些权限。")
    for index, item in enumerate(draft.permissions_required):
        if item not in PERMISSION_LEVEL_SET:
            return Gap(
                check="G6", key="permission",
                missing=(f"permissions_required[{index}]",),
                question=(f"权限等级 {item!r} 不在 L0–L6 里。"
                          f"可选：{'、'.join(PERMISSION_LEVELS)}；"
                          f"不知道选哪个就说要做什么，我按最小权限算。"),
            )
    return None


CHECKS = (_check_goal, _check_inputs, _check_artifact, _check_acceptance,
          _check_risk, _check_budget_permission)


def collect_gaps(draft: Draft) -> list[Gap]:
    """跑 G1–G6，返回**全部**缺口（按影响排序）。

    §3.3 说「每轮只问 1 个问题」「选出影响最大的一个缺口发起追问」——
    所以对外只暴露第一个，但内部要全收：追问轮数用完时要拿全量缺口做保守填充。
    """
    gaps = [gap for gap in (check(draft) for check in CHECKS) if gap is not None]
    gaps.sort(key=lambda gap: gap.priority())
    return gaps


def highlighted_slots(draft: Draft, policy: Any = None) -> tuple[str, ...]:
    """哪些槽位要在契约卡上高亮。

    §3.5 两条会造出高亮：
    - 置信度落在确认档（`SilentFillPolicy.needs_highlight`）
    - 副作用槽位（`deliver_to` 是典型，§3.6 的示例即使 c=0.88 也高亮）
    """
    from .confidence import SilentFillPolicy

    active = policy or SilentFillPolicy()
    marked: list[str] = []
    for name, slot, is_side_effect in (
        ("goal", draft.goal, False),
        ("artifact", draft.artifact, False),
        ("deliver_to", draft.deliver_to, True),
    ):
        if slot is None:
            continue
        confidence = slot.source.confidence
        if slot.highlight or active.needs_highlight(
                confidence, is_side_effect_slot=is_side_effect):
            marked.append(name)
    for index, slot in enumerate(draft.inputs):
        if slot.highlight or active.needs_highlight(
                slot.source.confidence, is_side_effect_slot=False):
            marked.append(f"inputs[{index}]")
    # §3.5 点名了「副作用槽位（目的地、收件人、被覆盖对象）」三类。
    # 目的地 = deliver_to（上面已收），**收件人 / 被覆盖对象 = side_effects[].target**，
    # 这类没有任何置信度概念，但永远要在契约卡上让人看见。
    for index, effect in enumerate(draft.side_effects):
        target = getattr(effect, "target", "")
        if target:
            marked.append(f"side_effects[{index}].target")
    return tuple(marked)


@dataclass
class GateOutcome:
    """`evaluate` 的完整返回值，含追问轮数状态。"""

    status: str
    contract: TaskContract | None = None
    gap: Gap | None = None
    gaps: tuple[Gap, ...] = ()
    rounds_used: int = 0
    # §2.2：rejected 要带 reason，取值限定三个。
    #   unverifiable_criteria —— 没法生成可验的标准（目标/交付物/验收标准这类
    #                            内容不能替用户猜，是本包里唯一的拒绝理由）
    #   unsafe                —— 风险过高（§3.4 只给等级，没有"拒绝阈值"，所以暂无来源）
    #   out_of_scope          —— 超出范围（§7 权限缺失，判不了，所以暂无来源）
    reason: str = ""
    # 专指 §3.3「追问超限 → 不得走倒计时自动开工」这一条。
    # §3.4 按风险的确认策略是另一回事，由 risk.confirmation_policy() 给出，
    # 两者不要混用同一个字段名。
    degraded_requires_explicit_confirmation: bool = False
    high_lighted_slots: tuple[str, ...] = ()


def evaluate(draft: Draft, *, round_index: int = 0, policy: Any = None) -> GateOutcome:
    """§3.3 第 3、4 步：跑检查，挑一个缺口追问；全通过则产出冻结契约。

    `round_index` 是**已经问过几轮**。达到上限（3）后不再追问，改用保守默认值
    填充并标 highlight，且**必须显式确认**（§3.3「此类任务不得走倒计时自动开工」）。
    """
    from .confidence import SilentFillPolicy

    active = policy or SilentFillPolicy()
    gaps = collect_gaps(draft)
    marked = highlighted_slots(draft, active)

    if not gaps:
        contract = freeze(draft)
        return GateOutcome(
            status=GATE_READY, contract=contract, gaps=(),
            rounds_used=round_index, degraded_requires_explicit_confirmation=False,
            high_lighted_slots=marked,
        )

    if round_index < MAX_CLARIFICATION_ROUNDS:
        return GateOutcome(
            status=GATE_NEEDS_CLARIFICATION, gap=gaps[0], gaps=tuple(gaps),
            rounds_used=round_index, degraded_requires_explicit_confirmation=False,
            high_lighted_slots=marked,
        )

    # 追问超限：§3.3 要求「用最保守的默认值填充，并将这些槽位标 highlight，
    # 进入 PROPOSED；此类任务不得走倒计时自动开工，必须显式确认」。
    filled = fill_conservative_defaults(draft, gaps)
    # **关键**：填充只改形式，不改事实。填完必须重跑全量 G1–G6 与 §3.7 lint——
    # 否则「exists=False 的输入」「9 条 required 超 L6」「81 字目标」这些
    # 第一轮就被拦下的东西，会在第 3 轮原样放行成 ready。
    # 规范 §3.3 只允许「用保守默认值填充缺省」，不允许把不合格洗成合格。
    remaining = collect_gaps(filled)
    if remaining:
        # 填充没能补齐 → 拒绝。规范 §2.2 的 rejected 三个理由里，最贴近的是
        # 'unverifiable_criteria'（目标/交付物/验收标准这类内容不能替用户猜）。
        # **必须在 freeze 之前判**：freeze 假定输入形状已对，形状坏时它会抛
        # AttributeError 而不是 ContractShapeError，绕过这里。
        return GateOutcome(
            status=GATE_REJECTED, gaps=tuple(gaps) + tuple(remaining),
            rounds_used=round_index, reason=REJECT_UNVERIFIABLE,
            high_lighted_slots=highlighted_slots(filled, active),
        )
    try:
        contract = freeze(filled)
    except ContractShapeError:
        return GateOutcome(
            status=GATE_REJECTED, gaps=tuple(gaps),
            rounds_used=round_index, reason=REJECT_UNVERIFIABLE,
            high_lighted_slots=marked,
        )
    return GateOutcome(
        status=GATE_READY, contract=contract, gaps=tuple(gaps),
        rounds_used=round_index, degraded_requires_explicit_confirmation=True,
        high_lighted_slots=highlighted_slots(filled, active),
    )


def fill_conservative_defaults(draft: Draft, gaps: list[Gap]) -> Draft:
    """追问超限后的保守填充。**只填不猜业务内容**。

    能填的只有"形式"（预算默认值、空权限即只读、空输入即无输入）；
    目标、交付物、验收标准**填不了也不该填**——那等于替用户决定他要什么，
    比追问失败更糟。填不了的会让 `freeze` 抛 shape 错误，走 rejected。
    """
    from .confidence import DEFAULT_CONFIRM_FILL_MIN, DEFAULT_SILENT_FILL_MIN
    from .model import Budget, SlotSource

    filled = Draft(**{**draft.__dict__})
    filled.extra = dict(draft.extra)
    keys = {gap.key for gap in gaps}

    def mark(slot: Slot | None) -> Slot | None:
        if slot is None:
            return None
        return Slot(value=slot.value, source=slot.source, confirmed=slot.confirmed,
                    highlight=True)

    if "budget" in keys and filled.budget is None:
        filled.budget = Budget()
    # 权限**不填**。规范 §6.2 已经把等级定死为 L0–L6（见 PERMISSION_LEVELS），
    # 但「这单到底需要哪几级」必须从任务语义推出来，不是从等级表抄一个默认值。
    # 编一个 "L0" 出来看着合法、实则替用户做了权限判断，且它会被冻进契约。
    # 宁可让 G6 保持缺口走 rejected，让用户自己说是哪几级。
    # 有缺口的槽位一律高亮，让用户在契约卡上看得到哪些是猜的。
    filled.goal = mark(filled.goal)
    filled.artifact = mark(filled.artifact)
    filled.deliver_to = mark(filled.deliver_to)
    filled.inputs = tuple(
        Slot(value=slot.value, source=slot.source, confirmed=slot.confirmed,
             highlight=True) if slot.highlight or "input" in keys else slot
        for slot in filled.inputs)
    return filled


def freeze(draft: Draft) -> TaskContract:
    """把 draft 冻结成 TaskContract。

    `risk` 由 §3.4 规则算出——规范禁止手填降级，所以这里不接受外部传入的风险。
    形状不全时抛 ContractShapeError，由调用方决定是继续追问还是拒绝。
    """
    payload = _draft_payload(draft)
    return parse_contract(payload)


def _draft_payload(draft: Draft) -> dict[str, Any]:
    # 槽位序列化用 model.slot_to_mapping，不要在这里再写一份内联副本。
    # 曾经就是「两处各写一遍」导致 model.to_mapping 漂移：它漏了 InputRef/
    # ArtifactSpec 的嵌套类型转换，json.dumps 直接抛 TypeError。
    # 抽成共用函数后如果这里又抄一份，同一个 bug 会以同样方式再出现一次。
    slot = slot_to_mapping

    def criterion(item: Criterion) -> dict[str, Any]:
        check = item.check
        check_payload: dict[str, Any] = {"type": check.type}
        for name in ("path", "format", "schema_ref", "lang", "cmd", "cwd"):
            value = getattr(check, name)
            if value:
                check_payload[name] = value
        if check.timeout_s is not None:
            check_payload["timeout_s"] = check.timeout_s
        if check.min_bytes is not None:
            check_payload["min_bytes"] = check.min_bytes
        if check.rules:
            check_payload["rules"] = [
                {"name": rule.name, **({"args": rule.args} if rule.args else {})}
                for rule in check.rules]
        if check.rubric:
            check_payload["rubric"] = list(check.rubric)
            check_payload["evidence_required"] = check.evidence_required
        if check.question:
            check_payload["question"] = check.question
        if check.options:
            check_payload["options"] = list(check.options)
        return {"id": item.id, "text": item.text, "kind": item.kind,
                "check": check_payload, "required": item.required,
                "promotion_weight": item.promotion_weight}

    effects = []
    for effect in draft.side_effects:
        effects.append({
            "id": getattr(effect, "id", ""),
            "type": getattr(effect, "type", ""),
            "target": getattr(effect, "target", ""),
            "target_class": getattr(effect, "target_class", ""),
            "reversible": getattr(effect, "reversible", False),
            "has_backup": getattr(effect, "has_backup", False),
            "idempotency_key": getattr(effect, "idempotency_key", ""),
        })

    risk = compute_risk(tuple(draft.side_effects))
    budget = draft.budget
    budget_payload = None
    if budget is not None:
        budget_payload = {
            "max_retries": getattr(budget, "max_retries", 3),
            "max_step_retries": getattr(budget, "max_step_retries", 2),
            "max_wall_s": getattr(budget, "max_wall_s", 900),
            "max_tokens": getattr(budget, "max_tokens", None),
            "max_cost_usd": getattr(budget, "max_cost_usd", None),
            "max_tool_calls": getattr(budget, "max_tool_calls", None),
        }

    return {
        "contract_id": draft.contract_id,
        "version": draft.version,
        "created_at": draft.created_at,
        "utterance": draft.utterance,
        "goal": slot(draft.goal),
        "inputs": [slot(item) for item in draft.inputs],
        "artifact": slot(draft.artifact),
        "deliver_to": slot(draft.deliver_to),
        "acceptance": [criterion(item) for item in draft.acceptance],
        "risk": {"level": risk.level, "reasons": list(risk.reasons)},
        "side_effects": effects,
        "permissions_required": list(draft.permissions_required),
        "budget": budget_payload,
    }


def effective_start_policy(outcome: GateOutcome, *, voice_mode: bool = True,
                          countdown_s: float | None = None) -> dict:
    """把「§3.3 追问降级」和「§3.4 风险策略」两个来源合成一个开工策略。

    两个来源是独立的：
    - §3.3「追问超限 → 此类任务不得走倒计时自动开工，必须显式确认」
    - §3.4「low 可自动开工 / medium 必须显式确认 / high 还要逐项点头」

    各自都对，但拼起来会出问题：追问超限的 **low 风险**任务，
    `confirmation_policy("low")` 仍返回 `countdown_allowed=True`，
    于是照样走 3 秒倒计时——直接违反 §3.3。

    所以调用方**不要自己拼**，用这个函数。规则是 OR：任一来源要求显式确认就显式确认。
    """
    from .risk import AUTO_START, EXPLICIT_CONFIRM, confirmation_policy

    if outcome.contract is None:
        # 还没冻结成契约（needs_clarification），谈不上开工策略。
        return {
            "risk": None,
            "start": EXPLICIT_CONFIRM,
            "countdown_allowed": False,
            "countdown_default_s": None,
            "delivery_confirmation": "not_applicable",
            "acknowledge_each_highlighted_slot": False,
            "degraded": bool(outcome.degraded_requires_explicit_confirmation),
        }

    has_highlight = bool(outcome.high_lighted_slots)
    policy = confirmation_policy(
        outcome.contract.risk.level,
        has_highlighted_slot=has_highlight,
        countdown_s=countdown_s,
        voice_mode=voice_mode,
    )
    policy["degraded"] = bool(outcome.degraded_requires_explicit_confirmation)
    if outcome.degraded_requires_explicit_confirmation:
        # §3.3 兜底：追问超限的契约一律显式确认，倒计时作废。
        if policy["start"] == AUTO_START:
            policy["start"] = EXPLICIT_CONFIRM
        policy["countdown_allowed"] = False
        policy["countdown_default_s"] = None
    return policy

def gate(draft: Draft, *, round_index: int = 0, policy: Any = None) -> GateOutcome:
    """`evaluate` 的别名，跟 §2.2 的 `IntentGate.evaluate` 对齐读起来更顺。"""
    return evaluate(draft, round_index=round_index, policy=policy)
