"""Pure, bounded memory records shared by chat and action context.

Stored evidence was checked by the writer. This projection checks its shape,
keeps source/time qualifications, and never upgrades missing evidence. It does
not read original messages, open a database, import configuration, or authorize
an action. Callers inject their existing text sanitizer and budget whole records.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import math
import re
from typing import Any


_LEVELS = frozenset(("like", "favorite", "dislike"))
_SCOPES = frozenset(("long_term", "recent", "current", "unspecified"))


def _field(record: Any, name: str, default: Any = None) -> Any:
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


def _clean(value: Any, sanitize: Callable[[str, int], str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    result = sanitize(value, len(value))
    return (result if isinstance(result, str) and result.strip()
            and not result.startswith("[REDACTED:") else None)


def present_memory(
    record: Any, *, sanitize: Callable[[str, int], str],
    max_value_chars: int | None = None,
    allow_historical: bool = False,
    allow_future: bool = False,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Return a complete shared record, or omit an unsafe/oversized record.

    ``max_value_chars`` limits the complete stored value, not an excerpt. Evidence
    quotes, session IDs and internal identity/watermark fields are not exported.
    A linked reference is provenance, not proof of the claim's meaning or current
    validity. Preference scope describes the original temporal self-report only.

    ``allow_historical`` admits a superseded row that carries an explicit
    ``fact_valid_until`` -- a historical record the user themselves bounded
    ("到去年为止…"). It defaults to off so /api/memories, the starmap and the
    durable export keep their exact shape; only the model-facing builder opts in,
    and the record then carries a ``time_note`` stating it was replaced and what
    bound the user set, so it is never read as the current state.

    ``allow_future`` is the mirror opt-in: an active row whose ``fact_valid_from``
    is still ahead of ``now`` is a plan, not a current fact ("从下个月起…"). The
    ranking layer already demotes it, but a demoted row can still be delivered,
    and without a note the model would read next month's plan as today's state.
    Same default-off so the exported shape never changes; ``now`` is injected by
    the caller so the annotation is deterministic and this module reads no clock.
    """
    if not callable(sanitize):
        raise ValueError("sanitize must be callable")
    if max_value_chars is not None and (type(max_value_chars) is not int or max_value_chars < 0):
        raise ValueError("max_value_chars must be a nonnegative integer or None")
    if type(allow_historical) is not bool:
        raise ValueError("allow_historical must be a boolean")
    if type(allow_future) is not bool:
        raise ValueError("allow_future must be a boolean")
    status = _field(record, "status")
    # Either bound marks a superseded row as one the user themselves bounded, so
    # both directions are admitted -- the mirror of the five retrieval admission
    # points. A lower bound only ("从下个月起…") is a plan the one-active-value-
    # per-topic key strategy superseded without the user withdrawing it.
    until = _field(record, "fact_valid_until")
    started = _field(record, "fact_valid_from")
    historical = (status == "superseded" and (until is not None or started is not None))
    if status != "active" and not (allow_historical and historical):
        return None
    identifier = _field(record, "id")
    if type(identifier) is not int or identifier < 1:
        return None
    raw_value = _field(record, "value")
    if (not isinstance(raw_value, str)
            or (max_value_chars is not None and len(raw_value) > max_value_chars)):
        return None
    value = _clean(raw_value, sanitize)
    if value is None:
        return None
    kind, subject = _field(record, "kind"), _field(record, "subject")
    if kind not in ("fact", "episode", "todo", "self_state") or subject not in ("user", "self", "assistant"):
        return None
    raw_source = _field(record, "source")
    source = raw_source if raw_source in ("stated", "inferred", "uncertain") else "uncertain"
    state = _field(record, "evidence_state", "legacy")
    state = state if state in ("manual", "linked", "legacy") else "legacy"
    message_id, quote = _field(record, "evidence_message_id"), _field(record, "evidence_quote")
    linked = (state == "linked" and subject == "user" and kind != "self_state"
              and type(message_id) is int and message_id > 0
              and isinstance(quote, str) and bool(quote.strip()) and len(quote) <= 1200)
    if state == "linked" and not linked:
        state = "legacy"
    evidence: dict[str, Any] = {"state": state}
    if linked:
        evidence["message_id"] = message_id
    if kind == "self_state" or subject != "user":
        provenance = "角色自身记录，不是用户明确陈述"
        if source != "stated":
            provenance += "；推测或不确定，需确认"
    elif source != "stated":
        provenance = ("【我的推测】不一定对，需确认" if raw_source == "inferred" else
                      "【我不确定的】需确认" if raw_source == "uncertain" else "未知来源，需确认")
    elif linked:
        provenance = "【你明确说过的】"
    else:
        provenance = ("【手动保存的】未关联原话" if state == "manual"
                      else "【旧记忆】未关联原话，需核对")
    result = {"id": identifier, "kind": kind, "subject": subject, "source": source,
              "provenance": provenance, "value": value, "evidence": evidence}
    if historical:
        # The value may already carry the bound in prose; this states it plainly
        # and forbids using the row as the current state, so the model cannot
        # answer a "now" question with a fact the user retired. The wording is
        # graded by source: an assistant-relayed claim (FMR downgrades it to
        # uncertain) must not be described as something "you explicitly set",
        # even though the bound itself came from the recorded text.
        who = "你明确划定" if source == "stated" else "记录中标注"
        if until is not None:
            try:
                day = datetime.fromtimestamp(until, tz=timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError, TypeError):
                day = "未明"
            result["time_note"] = (
                f"该条已被后来的陈述取代，只在{who}的时间范围内有效（至 {day} UTC）；"
                "回答现在/目前类问题不要采用，仅回答过去相关问题时可用")
        else:
            # A superseded row carrying only a LOWER bound. Its supersede is an
            # artifact of the one-active-value-per-topic key strategy, not a
            # retraction: "我现在在乙公司" does not withdraw "下个月起去丙公司",
            # and both hold at different times. So this note must claim neither
            # that the plan still stands nor that it was withdrawn -- asserting
            # either side would turn unconfirmed information into fact. State only
            # what is certain and let the model hedge.
            try:
                day = datetime.fromtimestamp(started, tz=timezone.utc).date().isoformat()
            except (ValueError, OverflowError, OSError, TypeError):
                day = "未明"
            result["time_note"] = (
                f"该条是早前声明的未来安排（{who}的时间范围：自 {day} UTC 起才生效），"
                "同主题后来另有陈述，是否仍有效未经确认；"
                "回答现在/目前类问题不要采用，仅回答将来计划类问题时可提及并说明未经确认")
    elif allow_future:
        # The mirror case: an active row whose lower bound is still ahead of the
        # injected `now` is a plan, not a current fact ("从下个月起…"). The ranking
        # layer already demotes it, but a demoted row can still be delivered, and
        # without this note the model reads next month's plan as today's state.
        # `historical` required superseded, so the two branches cannot both fire.
        started = _field(record, "fact_valid_from")
        # An absurdly large int overflows float conversion, so isfinite itself can
        # raise; the sibling preference-date path guards the same way.
        try:
            bounded = (isinstance(started, (int, float)) and not isinstance(started, bool)
                       and math.isfinite(started))
        except (OverflowError, ValueError):
            bounded = False
        if bounded:
            if now is None or not isinstance(now, (int, float)) or isinstance(now, bool) or not math.isfinite(now):
                raise ValueError("allow_future requires a finite numeric now")
            if started > now:
                try:
                    day = datetime.fromtimestamp(started, tz=timezone.utc).date().isoformat()
                except (ValueError, OverflowError, OSError):
                    day = "未明"
                who = "你明确划定" if source == "stated" else "记录中标注"
                result["time_note"] = (
                    f"该条有{who}的时间范围：自 {day} UTC 起才生效；"
                    "回答现在/目前类问题不要把它当作已生效的事实，"
                    "仅回答将来计划类问题时可用")
    if not (source == "stated" and kind == "fact" and subject == "user" and linked):
        return result
    target, level, scope = (_field(record, "preference_" + name) for name in ("target", "level", "scope"))
    if (not isinstance(target, str) or not target.strip() or len(target) > 40
            or not re.fullmatch(r"[\w\-\u3400-\u9fff]+", target)
            or not isinstance(level, str) or not isinstance(scope, str)
            or level not in _LEVELS or scope not in _SCOPES):
        return result
    clean_target = _clean(target, sanitize)
    if clean_target is None:
        return result
    preference: dict[str, Any] = {"target": clean_target, "level": level, "scope": scope}
    as_of = _field(record, "preference_as_of")
    if type(as_of) in (int, float):
        try:
            if math.isfinite(as_of):
                preference["as_of_date_utc"] = datetime.fromtimestamp(as_of, tz=timezone.utc).date().isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    preference["time_note"] = ("仅原话当时自述，未确认延续至今；未设定过期日"
        if scope in ("recent", "current") else "原话的长期自述，仍可能变化"
        if scope == "long_term" else "原话未明确时间范围，不能推定始终有效")
    if historical:
        # The row is retired: its preference wording must not claim it may still
        # hold. Reuse the authoritative top-level note so the two never disagree.
        preference["time_note"] = result["time_note"]
    result["preference"] = preference
    return result
