"""派发后不得重放（规范 §4.8）。

传输层超时 ≠ 工具没执行。唯一的判据是「有没有 dispatch_receipt」：
没回执才允许重新规划；有回执且确认幂等去重才允许同键重试；
其余一律先对账（UNKNOWN），不许直接重跑。
"""
from __future__ import annotations

from dataclasses import dataclass

REPLAY_REPLAN = "replan"
REPLAY_SAME_KEY = "replay_same_key"
REPLAY_UNKNOWN = "unknown_reconcile"


@dataclass(frozen=True)
class DispatchEvidence:
    """一次派发留下的证据；三个字段都只描述「确定的事实」。"""

    has_receipt: bool = False
    idempotent: bool = False
    remote_dedup_confirmed: bool = False


def replay_decision(evidence: DispatchEvidence) -> str:
    """按 §4.8 的分支返回三种处置之一。"""
    if not evidence.has_receipt:
        return REPLAY_REPLAN
    if evidence.idempotent and evidence.remote_dedup_confirmed:
        return REPLAY_SAME_KEY
    return REPLAY_UNKNOWN


def unknown_exits() -> tuple[str, ...]:
    """UNKNOWN 只有两条安全出口（§4.5 末）。"""
    return ("OBSERVED", "READY")


def requires_reconciliation(evidence: DispatchEvidence) -> bool:
    """结果不明时一律先对账。"""
    return replay_decision(evidence) == REPLAY_UNKNOWN
