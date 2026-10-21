"""契约 v2 · §3.5 置信度使用规则。

规范表（逐字）：

| 槽位类别 | 置信度 c | 行为 |
|---|---|---|
| 非副作用槽位 | c ≥ 0.85 | 静默补全，来源折叠展示在契约卡里 |
| 非副作用槽位 | 0.70 ≤ c < 0.85 | 补全，并在契约卡上高亮"请确认" |
| 非副作用槽位 | c < 0.70 | 不补全，转为追问 |
| 副作用槽位 | 任意 | 必须在契约卡显式展示并高亮；risk 为 medium 及以上时必须点头 |
| 多条记忆冲突 | 任意 | 取最近一次 user_confirmed 的；没有则追问 |

阈值是配置项（§3.5 末尾的 yaml）。本模块**不写死常量**：
`SilentFillPolicy` 由调用方传入阈值，默认值来自 `config/contract.yaml`。

规范特别说明：`silent_fill_min` 这一档用户看不到，所以目标准确率不低于 95%，
并且阈值必须用评测集校准。评测集缺失（§8 冻结），因此这里只用规范给的初始默认值。
"""
from __future__ import annotations

from dataclasses import dataclass

# 规范 §3.5 yaml 里的初始默认值。只作为配置缺失时的兜底，不当作结论。
DEFAULT_SILENT_FILL_MIN = 0.85
DEFAULT_CONFIRM_FILL_MIN = 0.70

FILL_SILENT = "silent"
FILL_CONFIRM = "confirm"
FILL_ASK = "ask"


@dataclass(frozen=True)
class SilentFillPolicy:
    """补全判定策略。`side_effect_slots_always_show` 控制副作用槽位是否强制高亮。"""

    silent_fill_min: float = DEFAULT_SILENT_FILL_MIN
    confirm_fill_min: float = DEFAULT_CONFIRM_FILL_MIN
    side_effect_slots_always_show: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.confirm_fill_min <= 1.0:
            raise ValueError("confirm_fill_min 必须在 [0,1]")
        if not 0.0 <= self.silent_fill_min <= 1.0:
            raise ValueError("silent_fill_min 必须在 [0,1]")
        if self.confirm_fill_min > self.silent_fill_min:
            # 两档阈值倒挂会让「确认档」区间为空，配置写错了应当立刻炸。
            raise ValueError("confirm_fill_min 不能大于 silent_fill_min")

    @staticmethod
    def from_config(config: dict | None) -> "SilentFillPolicy":
        """从 `config/contract.yaml` 的 `memory` 段构造。缺失字段用规范默认值。"""
        data = config if isinstance(config, dict) else {}
        memory = data.get("memory") if isinstance(data.get("memory"), dict) else data

        def pick(key: str, fallback: float) -> float:
            raw = memory.get(key, fallback)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return float(raw)
            return fallback

        always_show = memory.get("side_effect_slots_always_show",
                                 DEFAULT_SIDE_EFFECT_ALWAYS_SHOW)
        return SilentFillPolicy(
            silent_fill_min=pick("silent_fill_min", DEFAULT_SILENT_FILL_MIN),
            confirm_fill_min=pick("confirm_fill_min", DEFAULT_CONFIRM_FILL_MIN),
            side_effect_slots_always_show=always_show is not False,
        )

    def decide(self, confidence: float | None, *, is_side_effect_slot: bool) -> str:
        """返回 `silent` / `confirm` / `ask`。

        副作用槽位不看置信度：规范说「任意置信度都必须显式展示并高亮」，
        且「risk 为 medium 及以上时必须点头」。这里只回答「要不要补全」——
        副作用槽位即使 c=0.99 也要走 `confirm`（补全但高亮让用户点头），
        避免把推断出的收件人静默用上。
        """
        if is_side_effect_slot and self.side_effect_slots_always_show:
            return FILL_CONFIRM
        if confidence is None:
            # 没有来源置信度（例如纯用户话语）：不需要补全，也不需要追问。
            return FILL_SILENT
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            raise ValueError("confidence 必须是数字")
        value = float(confidence)
        if value >= self.silent_fill_min:
            return FILL_SILENT
        if value >= self.confirm_fill_min:
            return FILL_CONFIRM
        return FILL_ASK

    def needs_highlight(self, confidence: float | None, *,
                        is_side_effect_slot: bool) -> bool:
        """契约卡上是否需要高亮「请确认」。"""
        return self.decide(confidence, is_side_effect_slot=is_side_effect_slot) != FILL_SILENT


DEFAULT_SIDE_EFFECT_ALWAYS_SHOW = True


def resolve_conflicting_memories(candidates: list[dict]) -> dict | None:
    """多条记忆冲突时的取值规则（§3.5 表最后一行）。

    「取最近一次 user_confirmed 的；没有则追问」→ 返回 None 表示必须追问。

    每个候选形如 `{"memory_id": ..., "confirmed_at": float|None, "source_kind": str}`。
    规范说「user_confirmed」，对应 SlotSource.kind == 'user_confirmed'。
    时间取 `confirmed_at` 最大值；缺失视为不可用。
    """
    confirmed = []
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("source_kind") or item.get("kind") or "")
        stamp = item.get("confirmed_at")
        if kind != "user_confirmed":
            continue
        if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
            continue
        confirmed.append((float(stamp), item))
    if not confirmed:
        return None
    confirmed.sort(key=lambda pair: pair[0], reverse=True)
    return confirmed[0][1]
