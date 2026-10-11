from __future__ import annotations

import time
from typing import Any

from ..config import memory_cfg
from .extractor import extract_memories
from .retriever import MemoryRetriever
from .store import Memory, MemoryStore, store as default_store


class MemoryManager:
    def __init__(self, store: MemoryStore | None = None, retriever: MemoryRetriever | None = None) -> None:
        self.store = store or default_store
        self.cfg = memory_cfg()
        self.retriever = retriever or MemoryRetriever(self.store)
        self.enabled = True

    # ---------- write path ----------
    async def observe_turn(
        self,
        *,
        session_id: str,
        user_text: str,
        assistant_text: str,
        recent_context: str = "",
    ) -> list[Memory]:
        """Extract candidates from one exchange and persist them."""
        if not self.enabled:
            return []
        cfg = self.cfg
        items = await extract_memories(user_text, assistant_text, recent_context=recent_context)
        saved: list[Memory] = []
        infer_budget = self._infer_budget(session_id)
        written_infer = 0
        for item in items:
            if not cfg["write"].get("allow_self_write", True) and item["subject"] == "self":
                continue
            if item["source"] != "stated" and infer_budget <= written_infer:
                continue
            memory = self._persist(item, session_id=session_id)
            if memory is None:
                continue
            if memory.source != "stated":
                written_infer += 1
            saved.append(memory)
        return saved

    def _infer_budget(self, session_id: str) -> int:
        return int(self.cfg["write"].get("max_infer_per_session", 8))

    def _persist(self, item: dict[str, Any], *, session_id: str | None) -> Memory | None:
        existing = self.store.list_memories(status="active", limit=5000)
        by_key = {m.key: m for m in existing}
        prior = by_key.get(item["key"])
        new_id = self.store.upsert(session_id=session_id, **item)
        if prior is not None and prior.id != new_id and prior.value != item["value"]:
            self.store.link_supersedes(new_id, prior.id)
        memory = self.store.get(new_id)
        return memory

    def remember(self, **item: Any) -> Memory | None:
        """Direct write used by API/tests."""
        return self._persist(item, session_id=item.pop("session_id", None))

    # ---------- correction / deletion ----------
    def correct(self, memory_id: int, value: str, *, source: str = "stated") -> Memory | None:
        memory = self.store.get(memory_id)
        if memory is None:
            return None
        value = " ".join(value.strip().split())
        new_id = self.store.upsert(
            kind=memory.kind,
            subject=memory.subject,
            key=memory.key,
            value=value,
            source=source,
            status="active",
            importance=memory.importance,
            confidence=0.95,
            session_id=memory.session_id,
            supersedes=memory.id if value != memory.value else None,
        )
        return self.store.get(new_id)

    def forget(self, memory_id: int, *, purge_history: bool = True) -> int:
        """Hard delete. By default the whole chain of the same key goes with it,
        because "忘掉这件事" should not leave an old version behind."""
        memory = self.store.get(memory_id)
        if memory is None:
            return 0
        if purge_history:
            return self.forget_key(memory.key)
        return 1 if self.store.delete(memory_id) else 0

    def forget_all(self, **filters: Any) -> int:
        return self.store.delete_all(**filters)

    def forget_key(self, key: str) -> int:
        memories = self.store.list_memories(status=None, include_history=True, limit=1000)
        targets = [m for m in memories if m.key == key.lower()]
        for memory in targets:
            self.store.delete(memory.id)
        return len(targets)

    # ---------- read path ----------
    def context_for(self, query: str, *, subject: str | None = None) -> str:
        if not self.enabled:
            return ""
        block, _ids = self.retriever.build_context(query, subject=subject)
        return block

    def snapshot(self, *, include_history: bool = False, limit: int = 300) -> list[dict[str, Any]]:
        rows = self.store.list_memories(include_history=include_history, limit=limit)
        return [m.to_dict() for m in rows]

    def history_of(self, key: str) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.store.history_for_key(key.lower())]

    def history(self, key: str) -> list[Any]:
        return self.store.history_for_key(key.lower())


manager = MemoryManager()