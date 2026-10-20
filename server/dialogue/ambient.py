"""三梯度非侵入式主动在场（协议 §16）：分级选择与事件构造，纯逻辑无 IO。

后台自进化与前台演出解耦：本模块只决定“此刻该用哪一梯度、事件长什么样”，
内容来源由调用方注入。没有真实内容时绝不编造——后台没形成有价值的新成果，
就不声称已完成反思或准备了礼物。阈值来自 config/pacing.yaml 的 ambient 段。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from ..config import pacing_cfg

TIERS = ("micro_act", "mumble", "proactive")
SUPERVISOR_ACTIONS = ("ledger_glance", "monitor_glance", "pen_tap")
AMBIENT_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "micro_act_seconds": 20,
    "mumble_seconds": 28,
    "proactive_seconds": 90,
    "dedupe_window_seconds": 300,
    "respect_typing": True,
    "quiet_mode_default": False,
    "require_authorization": True,
    "mumble_voice": True,
    "voice_backoff": 2.0,
    "voice_backoff_max_seconds": 240,
    "cycle_reset_seconds": 5,
}


def ambient_cfg() -> dict[str, Any]:
    configured = pacing_cfg().get("ambient") or {}
    merged = dict(AMBIENT_DEFAULTS)
    if isinstance(configured, dict):
        for key in merged:
            if configured.get(key) is not None:
                merged[key] = configured[key]
    return merged


def select_tier(idle_seconds: Any, cfg: dict[str, Any] | None = None, *,
                quiet_mode: bool = False) -> str | None:
    """Map continuous idle time to a tier (protocol §16.2): 20/45/90 by default.

    Returns None when disabled or below the first threshold. Quiet mode keeps only
    the silent micro_act tier.
    """
    cfg = cfg or ambient_cfg()
    if not cfg.get("enabled") or idle_seconds is None:
        return None
    try:
        idle = float(idle_seconds)
    except (TypeError, ValueError):
        return None
    if idle >= float(cfg["proactive_seconds"]):
        tier = "proactive"
    elif idle >= float(cfg["mumble_seconds"]):
        tier = "mumble"
    elif idle >= float(cfg["micro_act_seconds"]):
        tier = "micro_act"
    else:
        return None
    if quiet_mode and tier in ("mumble", "proactive"):
        return "micro_act"
    return tier


def new_event_id(prefix: str = "amb") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def backoff_threshold(base: Any, cycle: Any, *, factor: Any = 2.0,
                      max_seconds: Any = 240.0) -> float:
    """Idle threshold for the `cycle`-th voiced opener in one idle period.

    First contact keeps `base` (25~30s by config); every later opener in the
    same idle period multiplies it by `factor` and clamps at `max_seconds`, so
    a long silence never turns into nagging. The caller resets the cycle when
    the user becomes active again ("quiet for half a minute and she will
    always reach out first").
    """
    try:
        value = float(base)
    except (TypeError, ValueError):
        value = 28.0
    try:
        count = max(0, int(cycle))
    except (TypeError, ValueError):
        count = 0
    try:
        rate = float(factor)
    except (TypeError, ValueError):
        rate = 2.0
    try:
        cap = float(max_seconds)
    except (TypeError, ValueError):
        cap = 240.0
    if rate < 1.0:
        rate = 1.0
    if cap < value:
        cap = value
    return min(cap, value * (rate ** count))


def build_micro_act(*, action: str, reason: str, source_refs: list[str] | None = None,
                    now: float | None = None, event_id: str | None = None,
                    expires_in: float = 30.0) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    return {"type": "micro_act", "event_id": event_id or new_event_id("micro"), "action": action,
            "trigger_reason": reason, "triggered_at": now, "source_refs": list(source_refs or []),
            "expires_at": now + expires_in}


def build_supervisor_check(*, reason: str, now: float | None = None, event_id: str | None = None,
                           expires_in: float = 30.0) -> dict[str, Any]:
    """V3 监工微动作：手上有在跑的督办时，小溪低头翻手账/瞥监控副屏。

    与 build_micro_act 同一事件形状，前端未知 action 安全降级；
    reason 用 running_<idle>s 前缀，便于和 idle 微动作区分。
    """
    now = time.time() if now is None else float(now)
    seq = int(now) % len(SUPERVISOR_ACTIONS)
    return build_micro_act(action=SUPERVISOR_ACTIONS[seq], reason=f"supervising_{reason}",
                           now=now, event_id=event_id, expires_in=expires_in)


def build_mumble(*, text: str, reason: str, voice: bool = False, bubble: Any = None,
                 source_refs: list[str] | None = None, now: float | None = None,
                 event_id: str | None = None, dedupe_key: str | None = None,
                 expires_in: float = 60.0) -> dict[str, Any]:
    now = time.time() if now is None else float(now)
    event: dict[str, Any] = {
        "type": "mumble", "event_id": event_id or new_event_id("mumble"), "text": text,
        "voice": bool(voice), "trigger_reason": reason, "triggered_at": now,
        "source_refs": list(source_refs or []), "interactive": True,
        "dedupe_key": dedupe_key or (text[:24] if text else new_event_id("mumble")),
        "expires_at": now + expires_in}
    if bubble:
        event["bubble"] = bubble if isinstance(bubble, dict) else {"text": str(bubble), "ttl_ms": int(expires_in * 1000)}
    return event


def choose_content(*, discovery: Any = None, mood: Any = None, spark: Any = None,
                   child_seed: Any = None) -> tuple[str | None, list[str]]:
    """Real content only (§16.2): (text, source_refs) or (None, []).

    Prefers a recent reflection discovery, then the mood afterglow, then a surfaced
    spark, then a fresh child seed. Returns (None, []) when nothing real exists so
    the caller stays silent instead of fabricating a completed reflection.
    """
    if isinstance(discovery, dict) and isinstance(discovery.get("text"), str) and discovery["text"].strip():
        return discovery["text"].strip(), list(discovery.get("source_refs") or [])
    if isinstance(mood, dict) and isinstance(mood.get("narrative"), str) and mood["narrative"].strip():
        return mood["narrative"].strip(), list(mood.get("source_refs") or [])
    if isinstance(spark, dict) and isinstance(spark.get("text"), str) and spark["text"].strip():
        return spark["text"].strip(), []
    if isinstance(child_seed, dict) and isinstance(child_seed.get("question"), str) and child_seed["question"].strip():
        return child_seed["question"].strip(), list(child_seed.get("source_refs") or [])
    return None, []


__all__ = ["AMBIENT_DEFAULTS", "SUPERVISOR_ACTIONS", "TIERS", "ambient_cfg", "backoff_threshold",
           "build_micro_act", "build_mumble", "choose_content", "new_event_id", "select_tier"]
