"""Outcome feedback for memory retrieval (MemRL-style runtime signal).

A retrieved memory is a *bet*: it looked relevant, but only the next user turn
can say whether it actually helped. This module turns that next turn into a
conservative reward and updates the memory's Q value:

    Q <- Q + alpha * (r - Q)

Only explicit confirmation/correction is treated as reward. Silence, topic
continuation and "嗯" are not evidence of usefulness, so they leave Q untouched
and the retrieval prior keeps shrinking them out. A single bad turn cannot bury
a memory either: retrieval shrinks Q toward 0.5 by ``utility_n`` before ranking.

Design note (2026-10): this is the part of MemRL that survives contact with a
companion product. MemRL's reward is task success; a companion has no task
grader, so we use the one honest signal it does have -- the user telling us we
remembered correctly, or that we got it wrong.
"""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from ..config import memory_cfg

if TYPE_CHECKING:  # Annotation-only: importing must not open the database.
    from .store import Memory, MemoryStore

FEEDBACK_DEFAULTS: dict[str, Any] = {
    # Shipped config enables it; a missing config section keeps old behaviour.
    "enabled": False,
    "utility_alpha": 0.3,
    "utility_prior_strength": 3.0,
    "max_settle_age_hours": 12.0,
    "min_overlap": 0.12,
}

# Negative first: "不对" also contains "对".
_NEGATIVE = re.compile(
    r"(?<!是)不是(?:这样|这个|的|啦)|不对|错了|记错|没说过|没提过|没这回事|"
    r"别乱说|瞎说|搞错|哪有|才不是|早就不是|已经不(?:是|在)|不再(?:是|在)|"
    r"改(?:了|成)|换(?:了|成)|别(?:再)?提|不要提",
    re.I,
)
_POSITIVE = re.compile(
    r"^(?:对|嗯|是|是的|对呀|对的|没错|确实|正是|就是(?:这个|这样))(?=[，,。！!？?~\s]|$)|"
    r"记得没错|你记得|你还记得|记性真好|说对了|没错呀",
    re.I,
)
_MEMORY_EXPLICIT = re.compile(
    r"记得|记性|你说过|你之前说|这个信息|这条信息|对的[，,]?就是|没错[，,]?就是", re.I)
# Lessons are behavioural, not factual. A fact correction must not punish a
# style lesson; only an explicit complaint/praise about the way we spoke does.
_LESSON_NEGATIVE = re.compile(
    r"太啰嗦|别这么(?:说|讲)|不要这么(?:说|讲)|不用安慰|别说教|别追问|太正式|太官方|"
    r"别绕|别卖关子|别总是问", re.I)
_LESSON_POSITIVE = re.compile(
    r"这样(?:说|就好)|就这么说|这个语气|舒服多了|这样刚好", re.I)

_PUNCT = re.compile(r"[\s，。？！、；：\"'“”‘’（）()\[\]【】?.!,;:~～—\-]+")


def feedback_cfg() -> dict[str, Any]:
    cfg = dict(FEEDBACK_DEFAULTS)
    configured = memory_cfg().get("feedback")
    if isinstance(configured, dict):
        cfg.update({key: value for key, value in configured.items() if key in cfg})
    return cfg


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        if isinstance(value, bool):
            return default
        return min(high, max(low, float(value)))
    except (TypeError, ValueError):
        return default


def _norm(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


def _bigrams(text: str) -> set[str]:
    normalized = _norm(text)
    if len(normalized) <= 1:
        return {normalized} if normalized else set()
    return {normalized[i:i + 2] for i in range(len(normalized) - 1)}


def memory_overlap(memory: "Memory", text: str) -> float:
    """How much of the stored claim appears in the next user message.

    Containment (shared / smaller set), not Jaccard: a short memory mentioned
    inside a long message is a stronger attribution signal than a long memory
    that merely shares a topic word.
    """
    haystack = f"{memory.key} {memory.value} {memory.evidence_quote or ''}"
    stored, said = _bigrams(haystack), _bigrams(text)
    if not stored or not said:
        return 0.0
    return len(stored & said) / max(1, min(len(stored), len(said)))


def classify(text: str) -> int:
    """+1 explicit confirmation, -1 explicit correction, 0 no usable signal."""
    stripped = " ".join((text or "").split())
    if not stripped:
        return 0
    if _NEGATIVE.search(stripped):
        return -1
    if _POSITIVE.search(stripped):
        return 1
    return 0


def attribute(text: str, memories: list["Memory"], *, min_overlap: float = 0.12
              ) -> list[tuple[int, float]]:
    """Return [(memory_id, reward 1|0)] only for claims the user responded to."""
    polarity = classify(text)
    if polarity == 0 or not memories:
        return []
    attributed = [memory for memory in memories
                  if memory_overlap(memory, text) >= min_overlap]
    if not attributed:
        # A bare "不对" after a reply that cited exactly one memory is still
        # attributable. With several candidates it is not, and guessing would
        # poison unrelated memories; leave them untouched.
        if len(memories) == 1 and (polarity < 0 or _MEMORY_EXPLICIT.search(text)):
            attributed = memories
        else:
            return []
    reward = 1.0 if polarity > 0 else 0.0
    return [(memory.id, reward) for memory in attributed]


def settle_pending(store: "MemoryStore", *, session_id: str, current_message_id: int,
                   now: float | None = None) -> dict[str, Any]:
    """Score every unsettled turn whose next user message is already stored.

    Runs off the dialogue path (inside async extraction). Any failure is the
    caller's to log, never the user's to hear. Older-than-window turns settle as
    ``expired`` with no reward, so a week-later "不对" cannot rewrite history.
    """
    cfg = feedback_cfg()
    result: dict[str, Any] = {"enabled": bool(cfg["enabled"]), "settled": 0, "updates": []}
    if not cfg["enabled"]:
        return result
    alpha = _number(cfg.get("utility_alpha"), 0.3, 0.0, 1.0)
    min_overlap = _number(cfg.get("min_overlap"), 0.12, 0.0, 1.0)
    max_age = _number(cfg.get("max_settle_age_hours"), 12.0, 0.0, 24 * 365) * 3600
    stamp = time.time() if now is None else float(now)

    for usage in store.pending_usage(session_id=session_id, before_message_id=current_message_id):
        following = store.next_user_message(session_id, usage["user_message_id"])
        if following is None:
            continue
        if stamp - float(usage.get("created_at") or 0) > max_age:
            store.settle_usage(usage["id"], reward=None, reason="expired", now=stamp)
            result["settled"] += 1
            result["updates"].append({"usage_id": usage["id"], "reason": "expired", "detail": []})
            continue

        text = following.get("text") or ""
        memories = store.get_many(usage.get("memory_ids") or [])
        attributions = attribute(text, memories, min_overlap=min_overlap)
        detail: list[dict[str, Any]] = []
        for memory_id, reward in attributions:
            value = store.reinforce_memory(memory_id, reward=reward, alpha=alpha, now=stamp)
            detail.append({"id": memory_id, "reward": reward, "utility": value})

        lesson_ids = [i for i in (usage.get("lesson_ids") or []) if type(i) is int and i > 0]
        lesson_helpful: bool | None = None
        if lesson_ids:
            if _LESSON_NEGATIVE.search(text):
                lesson_helpful = False
            elif _LESSON_POSITIVE.search(text):
                lesson_helpful = True
            if lesson_helpful is not None:
                # apply_feedback owns counters *and* evidence-based retirement,
                # so a repeatedly unhelpful lesson leaves the prompt immediately.
                from .lessons import apply_feedback
                for lesson in apply_feedback(store, lesson_ids, helpful=lesson_helpful, now=stamp):
                    detail.append({"lesson_id": lesson["id"], "helpful": lesson_helpful,
                                   "status": lesson.get("status")})

        rewards = [item["reward"] for item in detail if "reward" in item]
        if rewards:
            mean = sum(rewards) / len(rewards)
            reason = "mixed" if len(set(rewards)) > 1 else ("positive" if mean > 0.5 else "negative")
        elif lesson_helpful is True:
            mean, reason = None, "lesson_positive"
        elif lesson_helpful is False:
            mean, reason = None, "lesson_negative"
        else:
            mean, reason = None, "neutral"
        if store.settle_usage(usage["id"], reward=mean, reason=reason, detail=detail, now=stamp):
            result["settled"] += 1
            result["updates"].append({"usage_id": usage["id"], "reason": reason, "detail": detail})
    return result


__all__ = ["FEEDBACK_DEFAULTS", "attribute", "classify", "feedback_cfg", "memory_overlap",
           "settle_pending"]
