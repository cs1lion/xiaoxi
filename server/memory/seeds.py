"""双螺旋好奇心树：Seed 生命周期、裂变、去重与播种（协议 §14）。

Seed 从一次性待办升级为可反复审视、能裂变、有完整思想历史的节点。本模块是策略层：
生命周期转移、从 config/seeds.yaml 播种、旧 curiosity_seeds 迁移、子种子裂变与相似去重。
数据层在 store.py；本模块不在 import 时开库。
"""
from __future__ import annotations

import difflib
import hashlib
import re
from typing import TYPE_CHECKING, Any

from .store import SEED_STATES

if TYPE_CHECKING:  # Annotation-only: importing must not open the database.
    from .store import MemoryStore

# 终态可逆：archived/dormant 都能回到 active；digested 也可重新审视。
SEED_TRANSITIONS: dict[str, set[str]] = {
    "active": {"active", "incubating", "digested", "dormant", "archived"},
    "incubating": {"active", "digested", "dormant", "archived"},
    "digested": {"active", "incubating", "dormant", "archived"},
    "dormant": {"active", "incubating", "digested", "archived"},
    "archived": {"active", "dormant"},
}

MAX_QUESTION_CHARS = 200
MAX_TITLE_CHARS = 40


def seed_transition_allowed(current: str, target: str) -> bool:
    return target in SEED_TRANSITIONS.get(current, set())


def stable_seed_id(text: str) -> str:
    """Deterministic slug for a question; used for legacy migration + forks."""
    digest = hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()[:10]
    return f"seed_{digest}"


def _normalize(text: str) -> str:
    return re.sub(r"[\s，。？！、；：""''（）()?.!,;:~～—-]+", "", (text or "").lower())


def similarity(a: str, b: str) -> float:
    """Character-level similarity in [0,1]; dependency-free near-duplicate gate."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _dedupe_threshold() -> float:
    from ..config import memory_cfg
    value = ((memory_cfg().get("self_evolution") or {}).get("similarity_dedupe", 0.86))
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.86


def find_duplicate_seed(store: MemoryStore, question: str, *,
                        exclude_id: str | None = None) -> dict[str, Any] | None:
    """The most similar existing seed above the dedupe threshold, or None."""
    threshold = _dedupe_threshold()
    best: dict[str, Any] | None = None
    best_score = threshold
    for seed in store.list_seeds(limit=1000):
        if exclude_id is not None and seed["id"] == exclude_id:
            continue
        score = similarity(question, seed.get("question", ""))
        if score >= best_score:
            best, best_score = seed, score
    return best


def load_initial_seeds() -> list[dict[str, Any]]:
    """Read config/seeds.yaml; a missing/invalid file degrades to [] (never raises)."""
    from ..config import CONFIG_DIR
    import yaml
    path = CONFIG_DIR / "seeds.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    seeds = data.get("seeds") if isinstance(data, dict) else None
    if not isinstance(seeds, list):
        return []
    return [seed for seed in seeds if isinstance(seed, dict)
            and isinstance(seed.get("id"), str) and seed["id"].strip()
            and isinstance(seed.get("question"), str) and seed["question"].strip()]


def bootstrap_seeds(store: MemoryStore, *, now_iso: str | None = None) -> int:
    """Seed the five initial questions only when the graph is empty.

    Idempotent: never overwrites an existing graph, so real evolution survives a
    restart. Returns the number of seeds created.
    """
    if store.count_seeds() > 0:
        return 0
    from .diary import local_date
    created = now_iso or local_date()
    count = 0
    for raw in load_initial_seeds():
        title = str(raw.get("title") or raw.get("question", "")).strip()[:MAX_TITLE_CHARS]
        question = str(raw["question"]).strip()[:MAX_QUESTION_CHARS]
        status = raw.get("status") if raw.get("status") in SEED_STATES else "active"
        heat = raw.get("heat")
        heat = float(heat) if isinstance(heat, (int, float)) and not isinstance(heat, bool) else 0.5
        source_refs = [str(r) for r in (raw.get("source_refs") or []) if isinstance(r, str)]
        store.upsert_seed(id=str(raw["id"]).strip(), title=title or question[:MAX_TITLE_CHARS],
                          question=question, status=status, heat=min(1.0, max(0.0, heat)),
                          source_refs=source_refs or ["config:seeds.yaml"],
                          created_at=str(raw.get("created_at") or created))
        count += 1
    return count


def migrate_legacy_seeds(store: MemoryStore) -> int:
    """One-time lift of self_state.curiosity_seeds JSON into the seeds table.

    Only runs when the seeds table is empty and legacy entries exist; the old
    JSON is left in place (read-only) for rollback. Returns migrated count.
    """
    if store.count_seeds() > 0:
        return 0
    state = store.get_self_state()
    legacy = [seed for seed in (state.get("curiosity_seeds") or [])
              if isinstance(seed, dict) and isinstance(seed.get("text"), str) and seed["text"].strip()]
    count = 0
    for seed in legacy:
        text = seed["text"].strip()[:MAX_QUESTION_CHARS]
        heat = seed.get("heat")
        heat = float(heat) if isinstance(heat, (int, float)) and not isinstance(heat, bool) else 0.5
        store.upsert_seed(id=stable_seed_id(text), title=text[:MAX_TITLE_CHARS], question=text,
                          status="active", heat=min(1.0, max(0.0, heat)),
                          source_refs=["legacy:self_state"], created_at="legacy")
        count += 1
    return count


def fork_child_seed(store: MemoryStore, parent_id: str, *, title: str, question: str,
                    reason: str, source_refs: list[str] | None = None,
                    now_iso: str | None = None, heat: float = 0.5) -> dict[str, Any] | None:
    """Create a genuinely different child seed and wire the parent link + edge.

    Refuses near-duplicates of existing seeds (returns None) so the graph cannot
    grow a swarm of restatements. Records the parent's child id and a persisted
    parent_child edge carrying the fork reason.
    """
    parent = store.get_seed(parent_id)
    if parent is None:
        return None
    title = (title or "").strip()[:MAX_TITLE_CHARS]
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        return None
    if find_duplicate_seed(store, question) is not None:
        return None
    from .diary import local_date
    child_id = stable_seed_id(f"{parent_id}:{question}")
    if store.get_seed(child_id) is not None:
        return None
    store.upsert_seed(id=child_id, title=title or question[:MAX_TITLE_CHARS], question=question,
                      status="active", heat=min(1.0, max(0.0, heat)),
                      parent_seed_ids=[parent_id],
                      source_refs=list(source_refs or []) or [f"seed:{parent_id}"],
                      created_at=now_iso or local_date())
    parent["child_seed_ids"] = list(parent.get("child_seed_ids") or []) + [child_id]
    store.upsert_seed(id=parent_id, title=parent["title"], question=parent["question"],
                      status=parent["status"], heat=parent.get("heat", 0.5),
                      cooldown=parent.get("cooldown", 0.0),
                      parent_seed_ids=parent.get("parent_seed_ids") or [],
                      child_seed_ids=parent["child_seed_ids"],
                      source_refs=parent.get("source_refs") or [],
                      related_refs=parent.get("related_refs") or [],
                      reflections=parent.get("reflections") or [],
                      created_at=parent["created_at"],
                      last_reflected_at=parent.get("last_reflected_at"),
                      version=int(parent.get("version", 1)))
    store.add_edge(source=f"seed:{parent_id}", target=f"seed:{child_id}", kind="parent_child",
                   weight=parent.get("heat", 0.5), reason=reason.strip()[:200] or "子种子裂变")
    return store.get_seed(child_id)


def transition_seed(store: MemoryStore, seed_id: str, target: str) -> dict[str, Any] | None:
    """Apply a lifecycle transition if allowed; returns the updated seed or None."""
    seed = store.get_seed(seed_id)
    if seed is None or target not in SEED_STATES:
        return None
    if not seed_transition_allowed(seed["status"], target):
        return None
    if seed["status"] == target:
        return seed
    store.upsert_seed(id=seed_id, title=seed["title"], question=seed["question"], status=target,
                      heat=seed.get("heat", 0.5), cooldown=seed.get("cooldown", 0.0),
                      parent_seed_ids=seed.get("parent_seed_ids") or [],
                      child_seed_ids=seed.get("child_seed_ids") or [],
                      source_refs=seed.get("source_refs") or [],
                      related_refs=seed.get("related_refs") or [],
                      reflections=seed.get("reflections") or [],
                      created_at=seed["created_at"],
                      last_reflected_at=seed.get("last_reflected_at"),
                      version=int(seed.get("version", 1)) + 1)
    return store.get_seed(seed_id)


def pick_seed_to_reflect(store: MemoryStore, *, now: float) -> dict[str, Any] | None:
    """Highest-heat active/incubating seed, preferring the least recently reflected.

    heat is the only scheduling valve here; it decides order, never content.
    """
    pool = [seed for seed in store.list_seeds(limit=500)
            if seed.get("status") in ("active", "incubating")]
    if not pool:
        return None
    return max(pool, key=lambda s: (float(s.get("heat") or 0), -(float(s.get("last_reflected_at") or 0))))


def gather_reflection_material(store: MemoryStore, *, seed: dict[str, Any],
                               notes_provider: Any = None, threads_provider: Any = None,
                               max_inputs: int = 12, diary_limit: int = 3,
                               other_seed_limit: int = 3) -> list[dict[str, str]]:
    """Cross-source material for one reflection (§14.4 Workspace Osmosis).

    Relevance-ordered and bounded; never reads the whole history. notes/threads come
    from injectable callables returning ``[{id, text}]`` so this memory layer never
    opens another module's database; the defaults yield no notes/threads.
    """
    items: list[dict[str, str]] = [{"ref": f"seed:{seed['id']}", "kind": "seed", "text": seed["question"]}]
    for other in store.list_seeds(status="active", limit=other_seed_limit + 1):
        if other["id"] != seed["id"]:
            items.append({"ref": f"seed:{other['id']}", "kind": "seed", "text": other["question"]})
    for diary in store.list_diaries(limit=diary_limit):
        items.append({"ref": f"diary:{diary['date']}", "kind": "diary",
                      "text": f"{diary.get('title', '')}。{diary.get('body', '')}"})
    state = store.get_self_state()
    for kind in ("taste", "worldview"):
        for entry in (state.get(kind) or [])[:3]:
            topic, stance = entry.get("topic"), entry.get("stance")
            if isinstance(topic, str) and topic.strip() and isinstance(stance, str) and stance.strip():
                items.append({"ref": f"{kind}:{topic}", "kind": kind, "text": f"{topic}：{stance}"})
    for kind, provider, cap in (("note", notes_provider, 4), ("thread", threads_provider, 4)):
        if provider is None:
            continue
        try:
            fetched = provider() or []
        except Exception:
            fetched = []
        for record in list(fetched)[:cap]:
            if isinstance(record, dict) and record.get("id") is not None and isinstance(record.get("text"), str):
                items.append({"ref": f"{kind}:{record['id']}", "kind": kind, "text": record["text"]})
    seen: set[str] = set()
    bounded: list[dict[str, str]] = []
    for item in items:
        if item["ref"] in seen:
            continue
        seen.add(item["ref"])
        bounded.append(item)
        if len(bounded) >= max(1, int(max_inputs)):
            break
    return bounded


__all__ = [
    "SEED_STATES", "SEED_TRANSITIONS", "bootstrap_seeds", "find_duplicate_seed", "fork_child_seed",
    "gather_reflection_material", "load_initial_seeds", "migrate_legacy_seeds",
    "pick_seed_to_reflect", "seed_transition_allowed", "similarity", "stable_seed_id", "transition_seed",
]
