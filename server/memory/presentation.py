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
) -> dict[str, Any] | None:
    """Return a complete shared record, or omit an unsafe/oversized record.

    ``max_value_chars`` limits the complete stored value, not an excerpt. Evidence
    quotes, session IDs and internal identity/watermark fields are not exported.
    A linked reference is provenance, not proof of the claim's meaning or current
    validity. Preference scope describes the original temporal self-report only.
    """
    if not callable(sanitize):
        raise ValueError("sanitize must be callable")
    if max_value_chars is not None and (type(max_value_chars) is not int or max_value_chars < 0):
        raise ValueError("max_value_chars must be a nonnegative integer or None")
    if _field(record, "status") != "active":
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
    result["preference"] = preference
    return result
