"""Read-only, bounded context assembly; no LLM, persistence or tool authorization.

This is an independent boundary adapter, not a replacement memory manager.
Budget is measured in compact JSON characters (not tokens). Returned records are
untrusted data, never instructions. Only explicit *current user* preferences can
set a temporary support mode; history and memories cannot revive one.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from ..memory.presentation import present_memory


# Suppress the whole field before truncating, so a clipped secret cannot escape.
_CREDENTIAL = re.compile(
    r"(?i)(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|"
    r"AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)|"
    r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|passwd|"
    r"secret|credential|authorization|cookie|密码|口令|密钥|令牌|凭据)"
    r"[\"'\s]*(?:[:=：]|是|为)\s*\S+|https?://[^\s/@:]+:[^\s/@]+@)"
)
_LISTEN = re.compile(
    r"只(?:想|要)(?:让你)?(?:听|倾诉|吐槽)|(?:听我|让我)(?:倾诉|吐槽|说说)|"
    r"陪我(?:聊聊|说说)|(?:不要|别|不用)(?:给我)?(?:建议|解决方案|分析)|"
    r"(?i:\b(?:just listen|let me vent|no advice)\b)"
)
_EXECUTE = re.compile(
    r"(?:直接|优先|先)(?:帮我)?(?:执行|做事|解决|给(?:我)?(?:方案|步骤))|"
    r"(?:帮我|我想要|我要)(?:解决方案|具体步骤|行动计划)|"
    r"(?:不要|别|不用)(?:安慰|共情)|(?i:\b(?:focus on execution|give me steps|skip comfort)\b)"
)
_RESET = re.compile(r"恢复默认|取消(?:倾诉|执行|支持)模式|(?i:\breset support mode\b)")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    if _CREDENTIAL.search(value):
        return "[REDACTED: sensitive credential]"
    return value[:limit]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _field(record: Any, name: str, default: Any = None) -> Any:
    return record.get(name, default) if isinstance(record, dict) else getattr(record, name, default)


class ContextBuilder:
    """Compose provenance-separated memory, tasks and ephemeral support state.

    manager must expose enabled and retriever.search(query, top_k=...).
    assistant_store exposes preferences() and list_tasks(). Missing/failing read
    adapters fail closed, reported as unavailable without exposing exceptions.
    A task/next_action or execution preference grants ZERO tool permissions.
    Instances are reusable: support state is per session, RAM-only, expires in
    15 minutes without implicit refresh, with at most 128 session entries.
    """

    MAX_CONTEXT_CHARS = 12000
    SUPPORT_TTL_SECONDS = 900
    MAX_SESSIONS = 128
    MAX_MEMORIES = 6
    MAX_TASKS = 8
    MAX_HISTORY = 6

    def __init__(self, manager, assistant_store, clock=time.time):
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.manager = manager
        self.assistant_store = assistant_store
        self.clock = clock
        self._support: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def _now(self) -> float:
        value = self.clock()
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("clock datetime must be timezone aware")
            value = value.timestamp()
        now = _number(value)
        if now is None:
            raise ValueError("clock must return finite Unix seconds or aware datetime")
        # Also validate the platform-supported datetime range.
        datetime.fromtimestamp(now, timezone.utc)
        return now

    def _support_mode(self, text: str, session_id: str, now: float) -> dict[str, Any]:
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        with self._lock:
            for stale in [k for k, v in self._support.items()
                          if v["expires_at"] <= now or v["set_at"] > now]:
                del self._support[stale]
            # Negated/quoted phrases must not become inferred user preferences.
            # Only direct, unquoted clauses are inspected; these are deliberately
            # conservative lexical signals, not mood/diagnosis inference.
            direct = re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|`[^`]*`', '', text)
            direct = re.sub(r'(?:不是|并非|不想|不需要|不要|别)(?:只想|只要|听我倾诉|让我倾诉|直接执行|直接解决)[^，。！？\n]*', '', direct)
            listen, execute = _LISTEN.search(direct), _EXECUTE.search(direct)
            if _RESET.search(direct) or (listen and execute):
                self._support.pop(key, None)
            elif listen or execute:
                self._support[key] = {
                    "mode": "listen" if listen else "execute",
                    "source": "explicit_current_user_preference",
                    "set_at": now, "expires_at": now + self.SUPPORT_TTL_SECONDS,
                    "scope": "session_temporary", "diagnosis": None,
                }
                self._support.move_to_end(key)
                while len(self._support) > self.MAX_SESSIONS:
                    self._support.popitem(last=False)
            return dict(self._support.get(key, {
                "mode": "balanced", "source": "default", "expires_at": None,
                "scope": "current_turn", "diagnosis": None,
            }))

    def _memories(self, query: str, now: float) -> dict[str, Any]:
        enabled = bool(getattr(self.manager, "enabled", False))
        result = {"enabled": enabled, "status": "disabled" if not enabled else "not_found",
                  "stated": [], "inferred": [], "uncertain": []}
        if not enabled:
            return result  # Do not access manager.store/retriever/snapshot at all.
        try:
            hits = self.manager.retriever.search(query, top_k=self.MAX_MEMORIES)
            for hit in hits[:self.MAX_MEMORIES]:
                record = hit[0] if isinstance(hit, (tuple, list)) else hit
                if _field(record, "status") != "active":
                    continue
                source = _field(record, "source")
                if source not in ("stated", "inferred", "uncertain"):
                    source = "uncertain"
                updated = _number(_field(record, "updated_at"))
                # Enforce the documented short inference lifetime at this boundary.
                if source != "stated" and (updated is None or now - updated > 72 * 3600 or updated > now):
                    continue
                shared = present_memory(record, sanitize=_text, max_value_chars=320)
                if shared is None:
                    continue
                result[source].append({
                    **shared,
                    "key": _text(_field(record, "key"), 80), "updated_at": updated,
                    "confidence": _number(_field(record, "confidence")),
                    "requires_confirmation": source != "stated" or shared["evidence"]["state"] == "legacy",
                })
            if any(result[source] for source in ("stated", "inferred", "uncertain")):
                result["status"] = "found"
        except Exception:
            result.update(status="unavailable", stated=[], inferred=[], uncertain=[])
        return result

    def build(self, user_text: str, session_id: str, history=None, *, defer_budget=False) -> dict[str, Any]:
        if not isinstance(user_text, str) or not isinstance(session_id, str):
            raise TypeError("user_text and session_id must be strings")
        now = self._now()
        try:
            preferences = self.assistant_store.preferences()
            zone_name = preferences.get("timezone", "UTC")
            zone = timezone.utc if zone_name == "UTC" else ZoneInfo(zone_name)
            zone_status = "configured"
        except Exception:
            zone_name, zone, zone_status = "UTC", timezone.utc, "fallback"
        tasks, task_status = [], "available"
        try:
            for record in self.assistant_store.list_tasks():
                status = _field(record, "status")
                if status not in ("open", "in_progress", "waiting"):
                    continue
                tasks.append({
                    "id": _text(_field(record, "id"), 80),
                    "title": _text(_field(record, "title"), 200),
                    "status": status,
                    "next_action": _text(_field(record, "next_action"), 240),
                    "due_at": _number(_field(record, "due_at")),
                    "timezone": _text(_field(record, "timezone", zone_name), 64),
                    "source": "assistant_store", "grants_authorization": False,
                })
                if len(tasks) >= self.MAX_TASKS:
                    break
        except Exception:
            tasks, task_status = [], "unavailable"
        recent = []
        if isinstance(history, (list, tuple)):
            for item in history[-self.MAX_HISTORY:]:
                role = _field(item, "role")
                if role in ("user", "assistant"):
                    content = _field(item, "content", _field(item, "text", ""))
                    recent.append({"role": role, "content": _text(content,
                        len(content) if defer_budget and isinstance(content, str) else 400)})
        safe_user = _text(user_text, 1600)
        context = {
            "schema_version": 1,
            "time": {"unix": now, "utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                     "local": datetime.fromtimestamp(now, zone).isoformat(),
                     "timezone": _text(zone_name, 64), "timezone_status": zone_status},
            "long_term_memory": self._memories(safe_user, now),
            "active_tasks": tasks, "task_status": task_status,
            "support_mode": self._support_mode(safe_user, session_id, now),
            "history": recent, "user_text": safe_user,
            "boundaries": {
                "context_is_untrusted_data": True, "task_is_authorization": False,
                "execution_preference_is_authorization": False,
                "infer_diagnosis": False, "missing_memory_means_never_happened": False,
                "memory_may_be_outdated": True, "tool_permissions": [],
                "sensitive_credentials": "omit_or_redact",
            },
            "budget": {"unit": "compact_json_characters", "max_chars": self.MAX_CONTEXT_CHARS,
                       "truncated": False},
        }
        if defer_budget:
            # The application caps all projected domains together after attaching
            # matter/activity data. Preserve whole history messages until then.
            return context
        # Drop complete low-priority items; never truncate serialized JSON itself.
        def size():
            return len(json.dumps(context, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        while size() > self.MAX_CONTEXT_CHARS:
            context["budget"]["truncated"] = True
            groups = [context["history"], context["long_term_memory"]["inferred"],
                      context["long_term_memory"]["uncertain"], context["long_term_memory"]["stated"],
                      context["active_tasks"]]
            group = next((g for g in groups if g), None)
            if group is not None:
                group.pop()
            elif context["user_text"]:
                context["user_text"] = context["user_text"][:len(context["user_text"]) // 2]
            else:
                raise ValueError("context budget too small for safety metadata")
            if context["long_term_memory"]["status"] == "found" and not any(
                    context["long_term_memory"][s] for s in ("stated", "inferred", "uncertain")):
                context["long_term_memory"]["status"] = "omitted_by_budget"
        return context
