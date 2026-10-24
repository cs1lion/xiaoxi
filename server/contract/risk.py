"""契约 v2 · §3.4 风险规则与确认策略。

规范原文（§3.4）：**风险由规则算出，取所有副作用中的最高值**，
并且 §3.2 写明「risk 由规则算出，不允许手填降级」。

所以本模块是 `risk` 字段的唯一合法来源。`parse_contract` 只校验传入的形状，
真正算风险必须调 `compute_risk`。

规则表（逐字对应规范）：

| 副作用 | 条件 | 风险 |
|---|---|---|
| 只读，或只写工作区内 | 无 | low |
| overwrite_file | 有自动备份 | medium |
| overwrite_file | 无备份 | high |
| write_outside_workspace | 仅新建文件 | medium |
| send_message、http_write | target_class 为 self | medium |
| send_message、http_write | target_class 为 third_party | high |
| delete、spend_money、install | 无 | high |

兜底：任何 reversible 为 false 的副作用，风险至少为 medium。

§6.2 跨字段硬断言（3b，2026-10-24 接入）：「permissions_required 里的最高等级，
本身就是 risk 的下界——L5/L6 出现在契约里，risk 不得低于 high。这条是硬断言，
不是建议：风险规则算漏了，权限表兜住。」所以本函数同时读权限等级；
规范只把 L5/L6 这一档操作化成了「至少 high」，L0–L4 没给映射，不擅自发明。
等级名是否合法（L0–L6 闭集）仍是 G6 的职责，这里不重复判——
出现别的字符串时不抬也不炸，交给 G6 去问。
"""
from __future__ import annotations

from .model import RISK_RANK, RiskAssessment, SideEffect


def _bump(current: str, candidate: str) -> str:
    """取更高的那个风险等级。"""
    return candidate if RISK_RANK[candidate] > RISK_RANK[current] else current


def risk_of_side_effect(effect: SideEffect) -> tuple[str, str]:
    """单个副作用的风险与其理由。

    返回 `(level, reason)`。reason 会进 RiskAssessment.reasons，
    让契约卡能说清「为什么这单是 medium」——只给等级不给理由是没法让用户做决定的。
    """
    kind = effect.type
    target_class = effect.target_class

    if kind == "overwrite_file":
        if effect.has_backup:
            level, reason = "medium", "覆盖文件但有自动备份"
        else:
            level, reason = "high", "覆盖文件且无备份"
    elif kind == "write_outside_workspace":
        # 规范只给了「仅新建文件」这一类。带 has_backup 的覆盖属于 overwrite_file，
        # 所以这里按新建处理；若同一副作用既写外部又覆盖，应声明成 overwrite_file 两条。
        level, reason = "medium", "向工作区外写入"
    elif kind in ("send_message", "http_write"):
        if target_class == "third_party":
            level, reason = "high", "向第三方发送内容"
        elif target_class == "self":
            level, reason = "medium", "向自己发送内容"
        else:
            # 规范没有给 target_class=local 的 send_message/http_write 规则。
            # 本地的 http_write 是一种写入，按 reversible 兜底处理：不擅自降级也不擅自升级。
            level, reason = "low", "本地写入"
    elif kind in ("delete", "spend_money", "install"):
        level, reason = "high", f"{kind} 不可逆"
    elif kind == "other":
        # 未归类副作用：规范没给规则。按 reversible 兜底，宁可保守。
        level, reason = "low", "未归类副作用"
    else:  # pragma: no cover - 枚举已在 model 里校验
        level, reason = "low", f"未知副作用类型 {kind}"

    # 兜底规则（§3.4 最后一句）：不可逆的副作用至少 medium。
    if not effect.reversible and RISK_RANK[level] < RISK_RANK["medium"]:
        level = "medium"
        reason = f"{reason}；且不可逆（兜底下限 medium）"
    return level, reason


def compute_risk(
    side_effects: tuple[SideEffect, ...] | list[SideEffect],
    *,
    permissions_required: tuple[str, ...] | list[str] | None = None,
) -> RiskAssessment:
    """把一组副作用（与权限等级，§6.2）汇总成一个风险等级。

    没有任何副作用 = 只读 = low（规范表第一行）——但 L5/L6 在权限表里时
    仍要抬到 high：花钱/对外写/安装的权限本身就已经不是「低风险只读」了。
    """
    effects = tuple(side_effects)
    reasons: list[str] = []
    if not effects:
        level = "low"
        reasons.append("没有副作用（只读）")
    else:
        level = "low"
        for effect in effects:
            effect_level, reason = risk_of_side_effect(effect)
            level = _bump(level, effect_level)
            reasons.append(f"{effect.id}（{effect.type}）: {reason} → {effect_level}")

    # §6.2 硬断言：权限等级是 risk 的下界，L5/L6 → 至少 high。
    # 只兜下界，不做降级：副作用算出的 high 不会因为权限低而被拉下来。
    elevated = sorted({item for item in (permissions_required or ()) if item in ("L5", "L6")})
    if elevated:
        if RISK_RANK[level] < RISK_RANK["high"]:
            level = "high"
        reasons.append(
            f"permissions_required 含 {'/'.join(elevated)}：权限等级是 risk 的下界"
            "（§6.2 硬断言），risk 不得低于 high"
        )
    return RiskAssessment(level=level, reasons=tuple(reasons))


# ---------- 按风险的确认策略（§3.4 表二） ----------

AUTO_START = "auto_start"
EXPLICIT_CONFIRM = "explicit_confirm"
EXPLICIT_CONFIRM_WITH_SLOT_ACK = "explicit_confirm_with_slot_ack"


def confirmation_policy(risk_level: str, *, has_highlighted_slot: bool = False,
                        countdown_s: float | None = None,
                        voice_mode: bool = True) -> dict:
    """按风险给出开工/交付前的确认要求。

    规范表二：

    | 风险 | 开工确认 | 契约卡倒计时 | 交付前确认 |
    | low | 可自动开工 | 允许，默认 3 秒（语音场景建议 5 秒）；仅当契约卡上没有高亮槽位时 | 不需要 |
    | medium | 必须显式确认 | 不允许 | 需要，展示将执行的动作与预览 |
    | high | 必须显式确认，并对高亮的关键槽位逐项点头 | 不允许 | 必须，交付时再次展示目的地与内容摘要 |

    `voice_mode` 决定 low 风险的倒计时默认值：语音场景 5 秒，其他 3 秒。
    规范说这些数值是配置项，所以调用方可以覆盖 `countdown_s`。
    """
    if risk_level not in RISK_RANK:
        raise ValueError(f"unknown risk level: {risk_level!r}")

    if risk_level == "low":
        # 有高亮槽位时不给倒计时——规范写的是「仅当契约卡上没有高亮槽位时」。
        allow_countdown = not has_highlighted_slot
        default_countdown = 5.0 if voice_mode else 3.0
        return {
            "risk": "low",
            "start": AUTO_START,
            "countdown_allowed": allow_countdown,
            "countdown_default_s": (countdown_s if countdown_s is not None
                                    else default_countdown) if allow_countdown else None,
            "delivery_confirmation": "not_required",
            "acknowledge_each_highlighted_slot": False,
        }
    if risk_level == "medium":
        return {
            "risk": "medium",
            "start": EXPLICIT_CONFIRM,
            "countdown_allowed": False,
            "countdown_default_s": None,
            "delivery_confirmation": "required_show_actions_and_preview",
            "acknowledge_each_highlighted_slot": False,
        }
    return {
        "risk": "high",
        "start": EXPLICIT_CONFIRM_WITH_SLOT_ACK,
        "countdown_allowed": False,
        "countdown_default_s": None,
        "delivery_confirmation": "required_show_destination_and_summary",
        # high 风险要「对高亮的关键槽位逐项点头」；没有高亮槽位时无槽可点头，
        # 但风险确认本身仍然必须（start 已表达这一点）。
        "acknowledge_each_highlighted_slot": has_highlighted_slot,
    }
