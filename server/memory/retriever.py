from __future__ import annotations

import math
import time

from sklearn.feature_extraction.text import TfidfVectorizer

from ..config import memory_cfg
from .store import Memory, MemoryStore, store as default_store


class MemoryRetriever:
    """Hybrid retrieval: char-ngram TF-IDF + importance + recency.

    The vectoriser is rebuilt from the live table, so a hard delete really is a
    hard delete -- there is no side index left holding the removed text.
    """

    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or default_store
        self.cfg = memory_cfg()["retrieval"]
        self._version = -1
        self._ids: list[int] = []
        self._matrix = None
        self._vectorizer: TfidfVectorizer | None = None

    def _ensure_index(self) -> None:
        if self._version == self.store.version and self._matrix is not None:
            return
        rows = self.store.list_memories(status="active", limit=5000)
        self._ids = [m.id for m in rows]
        if not rows:
            self._matrix = None
            self._vectorizer = None
            self._version = self.store.version
            return
        corpus = [f"{m.key} {m.value}" for m in rows]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), min_df=1, sublinear_tf=True)
        self._matrix = vec.fit_transform(corpus)
        self._vectorizer = vec
        self._version = self.store.version

    def _similarity(self, query: str) -> dict[int, float]:
        self._ensure_index()
        if self._matrix is None or not self._ids:
            return {}
        qv = self._vectorizer.transform([query])
        sims = (self._matrix @ qv.T).toarray().ravel()
        return {mid: float(score) for mid, score in zip(self._ids, sims)}

    def _recency(self, memory: Memory, now: float) -> float:
        half_life_days = float(self.cfg.get("recency_half_life_days", 14))
        half_life = max(half_life_days, 0.5) * 86400
        age = max(0.0, now - memory.updated_at)
        return math.pow(0.5, age / half_life)

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        kinds: tuple[str, ...] | None = None,
        subject: str | None = None,
        include_done: bool = False,
    ) -> list[tuple[Memory, float]]:
        cfg = self.cfg
        top_k = top_k or int(cfg.get("top_k", 6))
        now = time.time()
        sims = self._similarity(query or "")
        w_imp = float(cfg.get("importance_weight", 0.35))
        w_rec = float(cfg.get("recency_weight", 0.25))
        w_sim = float(cfg.get("similarity_weight", 0.4))
        min_score = float(cfg.get("min_score", 0.0))
        infer_decay_hours = float(memory_cfg()["write"].get("infer_decay_hours", 72))

        out: list[tuple[Memory, float]] = []
        for memory in self.store.list_memories(status=None, limit=5000):
            if not include_done and memory.status != "active":
                continue
            if kinds and memory.kind not in kinds:
                continue
            if subject and memory.subject != subject:
                continue
            sim = sims.get(memory.id, 0.0)
            if memory.source in ("inferred", "uncertain"):
                age_h = (now - memory.updated_at) / 3600
                if age_h > infer_decay_hours and sim < 0.12:
                    continue
            score = w_sim * sim + w_imp * memory.importance + w_rec * self._recency(memory, now)
            score *= 0.5 + 0.5 * memory.confidence
            if score < min_score:
                continue
            out.append((memory, score))

        out.sort(key=lambda pair: pair[1], reverse=True)
        return out[:top_k]

    def build_context(self, query: str, *, subject: str | None = None) -> tuple[str, list[int]]:
        """Return (prompt block, used ids). Grouped by certainty tier on purpose."""
        cfg = memory_cfg()
        hits = self.search(query, subject=subject)
        if not hits:
            return "", []
        max_chars = int(cfg["retrieval"].get("max_inject_chars", 600))
        lines: list[str] = []
        used: list[int] = []
        tiers: dict[str, list[str]] = {"stated": [], "inferred": [], "uncertain": []}
        for memory, _score in hits:
            label = {"fact": "关于你", "episode": "我们一起", "todo": "待办", "self_state": "我自己的"}.get(
                memory.kind, memory.kind
            )
            text = f"{label}：{memory.value}"
            if memory.source == "uncertain":
                text += "（不太确定）"
            tiers[memory.source].append(text)
            used.append(memory.id)

        for tier, title in (
            ("stated", "【你明确说过的】"),
            ("inferred", "【我的推测，不一定对】"),
            ("uncertain", "【我不确定的】"),
        ):
            if not tiers[tier]:
                continue
            block = title + "\n" + "\n".join("- " + t for t in tiers[tier])
            if sum(len(line) for line in lines) + len(block) > max_chars and lines:
                continue
            lines.append(block)

        self.store.mark_used(used)
        return "\n".join(lines), used


retriever = MemoryRetriever()