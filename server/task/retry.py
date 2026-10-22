"""重试与确认策略（规范 §4.7）。

两个决策函数都把「为什么不能重试」写进 reason 里，并且必须带数字：
调用方（尤其语音层）要能直接把这句念给用户听，不能只说「失败了」。

判定顺序是契约写死的，不能重排——「人必须判」要先于「还能自动重试」，
否则一个 human 标准会把任务无限重试下去。
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import SideEffectDecl

# §4.7 三种确认门策略；默认取 (a) 一律显式确认。
START_POLICIES: tuple[str, ...] = (
    "always_explicit",
    "low_risk_countdown",
    "countdown_hint_only",
)


@dataclass(frozen=True)
class RetryDecision:
    """一次重试判定：动作、给人看的中文原因、判定后的计数。"""

    action: str
    reason: str
    attempt_after: int


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def task_retry_decision(
    *,
    attempt: int,
    max_retries: int,
    failure_signature: str,
    last_failure_signature: str,
    has_failed_required: bool,
    human_required_count: int,
) -> RetryDecision:
    """Task 级（验收打回）重试判定，顺序见 §4.7。"""
    attempt = _number(attempt)
    max_retries = _number(max_retries)
    humans = _number(human_required_count)
    current = _text(failure_signature)
    previous = _text(last_failure_signature)

    if humans > 0:
        return RetryDecision(
            action="block",
            reason=(
                f"有 {humans} 条 human 标准必须人工判定（§4.10 SM-17）；"
                f"attempt {attempt} / max_retries {max_retries}"
            ),
            attempt_after=attempt,
        )
    if not has_failed_required:
        return RetryDecision(
            action="block",
            reason=(
                "没有 required 标准未通过，不构成验收打回；"
                f"attempt {attempt} / max_retries {max_retries}"
            ),
            attempt_after=attempt,
        )
    if current and current == previous:
        return RetryDecision(
            action="block",
            reason=(
                f"失败签名与上次相同（{current}），同一种错误不重试；"
                f"attempt {attempt} / max_retries {max_retries}"
            ),
            attempt_after=attempt,
        )
    if attempt >= max_retries:
        return RetryDecision(
            action="block",
            reason=f"重试次数已用完（attempt {attempt} >= max_retries {max_retries}）",
            attempt_after=attempt,
        )
    return RetryDecision(
        action="retry",
        reason=f"还能自动重试（attempt {attempt} < max_retries {max_retries}）",
        attempt_after=attempt + 1,
    )


def step_retry_decision(
    *,
    retries: int,
    max_step_retries: int,
    failure_signature: str,
    last_failure_signature: str,
    retryable: bool,
) -> RetryDecision:
    """Step 级（工具重做）重试判定，顺序见 §4.7。"""
    retries = _number(retries)
    maximum = _number(max_step_retries)
    current = _text(failure_signature)
    previous = _text(last_failure_signature)

    if not retryable:
        return RetryDecision(
            action="block",
            reason=f"错误不可重试（retries {retries} / max_step_retries {maximum}）",
            attempt_after=retries,
        )
    if current and current == previous:
        return RetryDecision(
            action="block",
            reason=(
                f"失败签名与上次相同（{current}），不再重做；"
                f"retries {retries} / max_step_retries {maximum}"
            ),
            attempt_after=retries,
        )
    if retries >= maximum:
        return RetryDecision(
            action="block",
            reason=f"重试次数已用完（retries {retries} >= max_step_retries {maximum}）",
            attempt_after=retries,
        )
    return RetryDecision(
        action="retry",
        reason=f"还能重做（retries {retries} < max_step_retries {maximum}，签名未重复）",
        attempt_after=retries + 1,
    )


def can_auto_start(
    *,
    risk: str,
    side_effects: tuple[SideEffectDecl, ...] = (),
    highlighted_slots: tuple[str, ...] = (),
    policy: str = "always_explicit",
) -> bool:
    """§4.7 的确认门：只有「低风险、无副作用、无高亮」才允许倒计时自动开工。"""
    if policy not in START_POLICIES:
        raise ValueError(f"未知的确认门策略：{policy!r}")
    if policy in ("always_explicit", "countdown_hint_only"):
        return False
    return (
        risk == "low"
        and not tuple(side_effects or ())
        and not tuple(highlighted_slots or ())
    )
