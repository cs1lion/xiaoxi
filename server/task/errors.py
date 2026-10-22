"""状态机异常（规范 §4.4 / §4.5 的拒绝语义）。

两类拒绝必须分开，调用方才能决定是「换目标」还是「补事实」：

- IllegalTransition：这条迁移根本不在表里；
- GuardRejected：边在表里，但守卫不满足。

两者都继承 TransitionError(ValueError)，message 同时带 state / target
与 allowed / reasons 的可读内容，方便直接进日志或回给语音层。
"""
from __future__ import annotations


class TransitionError(ValueError):
    """状态迁移错误的基类。"""

    kind: str = "transition_error"


class IllegalTransition(TransitionError):
    """目标迁移不在迁移表里。"""

    kind = "illegal_transition"

    def __init__(self, state: str, target: str, allowed: tuple[str, ...]) -> None:
        self.state = state
        self.target = target
        self.allowed = tuple(allowed)
        allowed_text = "、".join(self.allowed) if self.allowed else "（无）"
        super().__init__(
            f"非法迁移：{self.state!r} -> {self.target!r}；允许的目标：{allowed_text}"
        )


class GuardRejected(TransitionError):
    """迁移边存在，但守卫拒绝。"""

    kind = "guard_rejected"

    def __init__(
        self,
        state: str,
        target: str,
        rule_ids: tuple[str, ...],
        reasons: tuple[str, ...],
    ) -> None:
        self.state = state
        self.target = target
        self.rule_ids = tuple(rule_ids)
        self.reasons = tuple(reasons)
        rules_text = "、".join(self.rule_ids) if self.rule_ids else "（无）"
        reasons_text = "；".join(self.reasons) if self.reasons else "（无）"
        super().__init__(
            f"守卫拒绝：{self.state!r} -> {self.target!r}；"
            f"规则：{rules_text}；原因：{reasons_text}"
        )
