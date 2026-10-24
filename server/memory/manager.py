from __future__ import annotations

import math
import json
import re
from typing import Any

from ..config import memory_cfg
from ..broadcast import publish_memory_update
from .extractor import MAX_EVIDENCE_QUOTE_CHARS, MAX_KEY_CHARS, extract_memories, is_direct_user_evidence
from .retriever import MemoryRetriever
from .preference import normalize_preference, PreferenceValidationError
from .store import KINDS, SOURCES, Memory, MemoryStore, store as default_store

# 批量删除前最多枚举多少行来逐条发 MEMORY_UPDATE：个人记忆库远低于这个量级；
# 超限时多出来的行不再发事件（store 仍是真相），不为此做无界查询。
_BULK_EVENT_LIMIT = 1000


# Change words are a conservative gate, not proof of semantic entailment.
# Ambiguous changes remain conflicts; users can correct them in the memory UI.
_EXPLICIT_CHANGE = re.compile(
    r"纠正|更正|说错|记错|改为|改成|改了|换到|换成|不再|已经不|早就不|"
    r"(?:以前|之前|原来).*(?:现在|如今)|"
    r"\b(?:correction|no longer|used to|changed to|switched to)\b", re.I,
)


def _score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and 0 <= number <= 1 else None


def _publish_memory_event(action: str, memory: Memory, *, provenance: str) -> None:
    """§5.6 MEMORY_UPDATE（至少一次）：一次真实的记忆写入/纠正/删除就是一次
    档案室增删。action 语义表与真实操作一一对应（promoted=更强来源 upsert、
    corrected=新值 supersede 旧值、deleted=物理硬删），不猜别的动作名。

    发布失败不冒泡：记忆以 store 为真相，事件是旁路通知（协议 §19 同口径）。
    """
    try:
        publish_memory_update(action=action, memory_ref=f"{memory.kind}:{memory.key}",
                              source=memory.source, confidence=float(memory.confidence),
                              provenance=provenance)
    except Exception:
        return


class MemoryManager:
    def __init__(self, store: MemoryStore | None = None, retriever: MemoryRetriever | None = None) -> None:
        self.store = store or default_store
        # 记忆配置按使用点读，不在构造时冻结（协议 §17）。测试仍可整体替换 self.cfg。
        self._cfg_override: dict[str, Any] | None = None
        self.retriever = retriever or MemoryRetriever(self.store)
        self._enabled = True

    @property
    def cfg(self) -> dict[str, Any]:
        """记忆配置。默认每次读 `memory_cfg()`。

        `config_hot` 换上快照后，下一次写入就看到新值，不需要重启进程。
        """
        if self._cfg_override is not None:
            return self._cfg_override
        return memory_cfg()

    @cfg.setter
    def cfg(self, value: dict[str, Any]) -> None:
        self._cfg_override = value

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        value = bool(value)
        if value != self._enabled:
            # Inputs received while disabled cannot become eligible on re-enable.
            self.store.invalidate_pending()
            self._enabled = value

    def capture_write_epoch(self) -> int:
        """Capture synchronously when a turn/job is accepted, before scheduling."""
        return self.store.write_epoch

    async def observe_turn(
        self, *, session_id: str, user_text: str, assistant_text: str,
        recent_context: str = "", user_message_id: int | None = None,
        write_epoch: int | None = None,
        extraction_provider: Any = None, trace: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> list[Memory]:
        """Extract only from the saved user message belonging to this turn.

        Free text/history cannot substitute for a saved message. The parser and
        manager independently validate evidence; storage checks the epoch with
        its write transaction, including across database connections.
        """
        epoch, floor = self.store.write_state()
        expected_epoch = epoch if write_epoch is None else write_epoch
        if self.enabled and type(user_message_id) is int and user_message_id > 0:
            # The previous turn's memories get their reward from this user
            # message. Off the fast path: extraction already runs async, and a
            # feedback failure must never suppress memory extraction.
            try:
                from .feedback import settle_pending
                outcome = settle_pending(self.store, session_id=session_id,
                                         current_message_id=user_message_id)
                if trace is not None:
                    trace['feedback_settled'] = outcome.get('settled', 0)
            except Exception as exc:
                if trace is not None:
                    trace['feedback_error'] = f'{type(exc).__name__}: {exc}'
        if (not self.enabled or type(expected_epoch) is not int or expected_epoch != epoch
                or type(user_message_id) is not int or user_message_id <= floor):
            if trace is not None:
                trace['manager_skip'] = 'write_state_ineligible'
            return []
        message = self.store.get_message(user_message_id)
        if (message is None or message["role"] != "user" or message["session_id"] != session_id
                or message["text"] != user_text or not is_direct_user_evidence(user_text)):
            if trace is not None:
                trace['manager_skip'] = 'unavailable_or_non_direct_user_message'
            return []
        extraction_options: dict[str, Any] = {}
        if extraction_provider is not None:
            extraction_options['provider'] = extraction_provider
        if trace is not None:
            extraction_options['trace'] = trace
        if max_attempts is not None:
            extraction_options['max_attempts'] = max_attempts
        items = await extract_memories(
            user_text, assistant_text, recent_context=recent_context, evidence_messages=[message],
            **extraction_options,
        )
        if not self.enabled or self.store.write_epoch != expected_epoch or not isinstance(items, list):
            return []
        saved: list[Memory] = []
        infer_budget = self._infer_budget(session_id)
        for raw in items:
            item = self._linked_candidate(raw, message)
            if item is None or (item["source"] != "stated" and infer_budget <= 0):
                continue
            memory = self._persist(item, session_id=session_id, expected_write_epoch=expected_epoch)
            if memory is not None:
                if item["source"] != "stated":
                    infer_budget -= 1
                saved.append(memory)
        return saved

    def _infer_budget(self, session_id: str) -> int:
        maximum = max(0, int(self.cfg["write"].get("max_infer_per_session", 8)))
        return max(0, maximum - self.store.count_inferences(session_id))

    def _linked_candidate(self, raw: Any, message: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        if (raw.get("kind") not in ("fact", "episode", "todo") or raw.get("subject") != "user"
                or raw.get("source") not in SOURCES or raw.get("operation") not in ("add", "update")):
            return None
        key, value, quote = raw.get("key"), raw.get("value"), raw.get("evidence_quote")
        if (not isinstance(key, str) or not key.strip() or len(key) > MAX_KEY_CHARS
                or ":" not in key or not all(part.strip() for part in key.split(":", 1))
                or not isinstance(value, str) or not value.strip() or len(value) > 120
                or not isinstance(quote, str) or not quote.strip()
                or len(quote) > MAX_EVIDENCE_QUOTE_CHARS or quote not in message["text"]):
            return None
        identifier = raw.get("evidence_message_id", message["id"])
        if type(identifier) is not int or identifier != message["id"]:
            return None
        importance = _score(raw.get("importance", .5))
        confidence = _score(raw.get("confidence", {"stated": .95, "inferred": .6, "uncertain": .35}[raw["source"]]))
        if importance is None or confidence is None:
            return None
        # Never forward model-controlled status, supersedes, session or decision.
        # Confidence is a ranking convention, not a measured truth probability.
        preference_fields = ("preference_target", "preference_level", "preference_scope")
        if "preference" in raw and raw["preference"] is None:
            return None
        raw_preference = (raw["preference"] if "preference" in raw else
                          {"target": raw.get("preference_target"), "level": raw.get("preference_level"),
                           "scope": raw.get("preference_scope")} if any(field in raw for field in preference_fields)
                          else None)
        try:
            preference = normalize_preference(
                raw_preference,
                quote, value, key.strip().lower(), raw["kind"], "user", raw["source"],
                message_text=message["text"],
            )
        except PreferenceValidationError:
            return None
        return {
            "kind": raw["kind"], "subject": "user", "source": raw["source"],
            "key": key.strip().lower(), "value": " ".join(value.strip().split()),
            "importance": importance,
            "confidence": {"stated": .95, "inferred": .6, "uncertain": .35}[raw["source"]],
            "operation": raw["operation"], "evidence_message_id": identifier,
            "evidence_quote": quote, "evidence_state": "linked",
            **(preference or {}),
        }

    def _persist(self, item: dict[str, Any], *, session_id: str | None,
                 expected_write_epoch: int | None = None) -> Memory | None:
        item = dict(item)
        operation = item.pop("operation", "add")
        if item.get("preference_target"):
            item["identity_slot"] = json.dumps(
                [item["preference_target"], item["preference_scope"]],
                ensure_ascii=False, separators=(",", ":"))
        prior = self.store.active_for(kind=item["kind"], subject=item["subject"], key=item["key"],
                                      identity_slot=item.get("identity_slot", ""))
        if item.get("evidence_state") == "linked" and prior is not None and not item.get("preference_target"):
            identifier = item["evidence_message_id"]
            observed = max(prior.last_observed_message_id or 0, prior.evidence_message_id or 0)
            if identifier < observed:
                return None
            if prior.value != item["value"]:
                explicit_update = (
                    operation == "update" and item["source"] == "stated"
                    and _EXPLICIT_CHANGE.search(item["evidence_quote"]) is not None
                    and identifier > observed
                )
                if not explicit_update:
                    item["status"] = "conflict"
        new_id = self.store.upsert(session_id=session_id, expected_write_epoch=expected_write_epoch, **item)
        return self.store.get(new_id) if new_id is not None else None

    def remember(self, **item: Any) -> Memory | None:
        """Explicit manual write. Never pretend it was extracted from a chat."""
        kind, subject, source = item.get("kind"), item.get("subject", "user"), item.get("source", "stated")
        value = item.get("value")
        key = item.get("key") or value
        if (kind not in KINDS or subject not in ("user", "self", "assistant") or source not in SOURCES
                or not isinstance(value, str) or not value.strip() or not isinstance(key, str) or not key.strip()):
            return None
        importance, confidence = _score(item.get("importance", .5)), _score(item.get("confidence", .8))
        if importance is None or confidence is None:
            return None
        self.store.invalidate_pending()
        memory = self._persist({
            "kind": kind, "subject": subject, "source": source,
            "key": key.strip().lower(), "value": " ".join(value.strip().split()),
            "importance": importance, "confidence": confidence,
            "evidence_state": "manual", "evidence_message_id": None, "evidence_quote": "",
        }, session_id=item.get("session_id"))
        if memory is not None:
            # §5.6：显式 stated 写入 = 来源升级（promoted）
            _publish_memory_event("promoted", memory, provenance="manual")
        return memory

    def correct(self, memory_id: int, value: str, *, source: str = "stated") -> Memory | None:
        memory = self.store.get(memory_id)
        if memory is None or not isinstance(value, str) or not value.strip() or source not in SOURCES:
            return None
        self.store.invalidate_pending()
        new_id = self.store.upsert(
            kind=memory.kind, subject=memory.subject, key=memory.key,
            value=" ".join(value.strip().split()), source=source,
            importance=memory.importance,
            confidence={"stated": .95, "inferred": .6, "uncertain": .35}[source],
            session_id=memory.session_id, evidence_state="manual",
            identity_slot=memory.identity_slot,
            force_revision=True,
        )
        updated = self.store.get(new_id) if new_id is not None else None
        if updated is not None:
            # §5.6：用户当场纠正 = 新值 supersede 旧值（corrected）
            _publish_memory_event("corrected", updated, provenance="corrected")
        return updated

    def forget(self, memory_id: int, *, purge_history: bool = True) -> int:
        """Delete this fact's complete identity chain unless explicitly limited."""
        memory = self.store.get(memory_id)
        if memory is None:
            return 0
        if purge_history:
            return self.store.delete_key(memory.key, kind=memory.kind, subject=memory.subject,
                                         identity_slot=memory.identity_slot)
        return 1 if self.store.delete(memory_id) else 0

    def forget_all(self, **filters: Any) -> int:
        return self.store.delete_all(**filters)

    def forget_key(self, key: str) -> int:
        return self.store.delete_key(key)

    def set_disabled(self, memory_id: int, disabled: bool) -> Memory | None:
        """Soft-mute one memory out of retrieval assembly; reversible.

        This is not a delete and not a status change: the row keeps its
        evidence chain, its slot in the store and its hard-delete semantics.
        """
        if type(memory_id) is not int or not isinstance(disabled, bool):
            return None
        if not self.store.set_disabled(memory_id, disabled):
            return None
        return self.store.get(memory_id)

    def context_for(self, query: str, *, subject: str | None = None) -> str:
        if not self.enabled:
            return ""
        block, _ids, _found = self.retriever.build_context(query, subject=subject)
        return block

    def lessons_for(self, query: str) -> list[dict[str, Any]]:
        """Procedural lessons matching this turn; [] when memory/lessons are off.

        Only lessons that fit the injection budget are returned, so usage
        telemetry (`mark_used`) records exactly what the model actually saw.
        """
        if not self.enabled:
            return []
        from .lessons import renderable_lessons, retrieve_lessons
        return renderable_lessons(retrieve_lessons(self.store, query))

    def context_with_status(self, query: str, *, subject: str | None = None) -> tuple[str, bool]:
        if not self.enabled:
            return "", False
        block, _ids, found = self.retriever.build_context(query, subject=subject)
        return block, found

    def snapshot(self, *, include_history: bool = False, limit: int = 300) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.list_memories(include_history=include_history, limit=limit)]

    def history_of(self, key: str) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.history_for_key(key.lower())]

    def history(self, key: str) -> list[Any]:
        return self.store.history_for_key(key.lower())


manager = MemoryManager()
