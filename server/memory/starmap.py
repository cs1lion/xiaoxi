"""星图记忆图谱（协议第十三节）。

列表接口永远不给正文：节点只有标签和权重，全文只经 node/{id}。latent 火花
不出现在任何接口里，所以图上的 spark 节点只有 surfaced / spent 两种。记忆关闭
时返回空图而不是报错。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import persona_cfg
from .diary import local_date
from .evolution import MAX_OPENING_CHARS, load_self_state, unwrap_json_text

if TYPE_CHECKING:  # Annotation-only: importing this module must not open the database.
    from .store import MemoryStore

STARMAP_KINDS = ("memory", "diary", "spark", "taste", "worldview", "seed", "reflection")
STARMAP_LIMIT = 400       # 协议第十三节硬上限
LABEL_MAX_CHARS = 80      # 协议第十三节：label 上限
MAX_DAYS = 3650
VISIBLE_SPARK_STATES = ("surfaced", "spent")


def _empty_graph() -> dict[str, Any]:
    return {"nodes": [], "edges": [],
            "stats": {"memories": 0, "diaries": 0, "sparks": 0, "taste": 0, "worldview": 0},
            "truncated": False}


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    return min(1.0, max(0.0, number))


def _iso_date(epoch: Any) -> str:
    try:
        return datetime.fromtimestamp(float(epoch)).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _label(text: Any) -> str:
    return text.strip()[:LABEL_MAX_CHARS] if isinstance(text, str) else ""


def _wanted_kinds(kinds: Any) -> set[str]:
    if isinstance(kinds, str):
        candidates = kinds.split(",")
    elif isinstance(kinds, (list, tuple, set)):
        candidates = list(kinds)
    else:
        candidates = []
    wanted = {kind.strip() for kind in candidates if isinstance(kind, str) and kind.strip() in STARMAP_KINDS}
    return wanted or set(STARMAP_KINDS)


def _topic_vocabulary() -> set[str]:
    from ..config import memory_cfg
    conflict = memory_cfg().get("conflict") or {}
    return {topic for topic in (conflict.get("topic_vocabulary") or [])
            if isinstance(topic, str) and topic.strip()}


def _memory_node(memory: Any) -> dict[str, Any]:
    return {"id": f"mem:{memory.id}", "kind": "memory", "label": _label(memory.value),
            "date": _iso_date(memory.created_at), "weight": _clamp01(memory.importance),
            "group": "user" if memory.subject == "user" else "self", "ref_id": memory.id}


def _diary_node(diary: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"diary:{diary['date']}", "kind": "diary", "label": _label(diary["title"]),
            "date": diary["date"], "weight": _clamp01(diary["energy"]), "group": "self",
            "ref_id": diary["id"]}


def _spark_node(spark: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"spark:{spark['id']}", "kind": "spark", "label": _label(spark["text"]),
            "date": _iso_date(spark["created_at"]), "weight": _clamp01(spark["heat"]),
            "group": "self", "ref_id": spark["id"]}


def _stance_node(kind: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"{kind}:{entry.get('topic', '')}", "kind": kind, "label": _label(entry.get("topic")),
            "date": _iso_date(entry.get("updated_at")), "weight": _clamp01(entry.get("weight")),
            "group": "self", "ref_id": entry.get("topic", "")}


def _seed_node(seed: dict[str, Any]) -> dict[str, Any]:
    created = seed.get("created_at")
    return {"id": f"seed:{seed['id']}", "kind": "seed",
            "label": _label(seed.get("title") or seed.get("question")),
            "date": created if isinstance(created, str) else _iso_date(created),
            "weight": _clamp01(seed.get("heat")), "group": "self", "ref_id": seed["id"]}


def _reflection_node(reflection: dict[str, Any]) -> dict[str, Any]:
    return {"id": f"reflection:{reflection['id']}", "kind": "reflection",
            "label": _label(reflection.get("discovery")),
            "date": _iso_date(reflection.get("created_at")), "weight": 0.6,
            "group": "self", "ref_id": reflection["id"]}


def _persisted_edges(nodes: list[dict[str, Any]], store: MemoryStore) -> list[dict[str, Any]]:
    """Persisted parent_child / related_memory / epiphany edges (§18.2), kept only
    when both endpoints survive as nodes so the graph never dangles."""
    present = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    for edge in store.list_edges(limit=2000):
        if edge["source"] in present and edge["target"] in present:
            edges.append({"source": edge["source"], "target": edge["target"], "kind": edge["kind"],
                          "weight": _clamp01(edge.get("weight")), "reason": edge.get("reason", ""),
                          "created_at": edge.get("created_at")})
    return edges


def _node_topics(node: dict[str, Any], store: MemoryStore,
                 diary_records: dict[int, dict[str, Any]], vocabulary: set[str]) -> list[str]:
    if node["kind"] == "memory":
        memory = store.get(node["ref_id"])
        topic = (memory.key or "").split(":", 1)[0].strip() if memory is not None else ""
        return [topic] if topic in vocabulary else []
    if node["kind"] == "diary":
        record = diary_records.get(node["ref_id"]) or {}
        return [key for key in record.get("topic_keys") or [] if key in vocabulary]
    if node["kind"] in ("taste", "worldview"):
        topic = node["ref_id"]
        return [topic] if topic in vocabulary else []
    return []


def _edges(nodes: list[dict[str, Any]], store: MemoryStore,
           diary_records: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    # evidences: a memory whose message a diary cited points at that diary.
    message_to_diaries: dict[int, list[str]] = {}
    for node in nodes:
        if node["kind"] != "diary":
            continue
        record = diary_records.get(node["ref_id"]) or {}
        for message_id in record.get("source_message_ids") or []:
            message_to_diaries.setdefault(message_id, []).append(node["id"])
    for node in nodes:
        if node["kind"] != "memory":
            continue
        memory = store.get(node["ref_id"])
        evidence_id = getattr(memory, "evidence_message_id", None)
        if evidence_id is None:
            continue
        for diary_id in message_to_diaries.get(evidence_id, []):
            edges.append({"source": node["id"], "target": diary_id, "kind": "evidences",
                          "weight": node["weight"]})
    # born: spark -> the diary it was born from.
    diary_by_row = {node["ref_id"]: node["id"] for node in nodes if node["kind"] == "diary"}
    for node in nodes:
        if node["kind"] != "spark":
            continue
        spark = store.get_spark(node["ref_id"]) or {}
        target = diary_by_row.get(spark.get("born_diary_id"))
        if target:
            edges.append({"source": node["id"], "target": target, "kind": "born",
                          "weight": node["weight"]})
    # shaped: taste / worldview -> diaries that carry the same topic. Drift has no
    # node kind in the protocol, so only the exposed stance nodes link here.
    for node in nodes:
        if node["kind"] not in ("taste", "worldview"):
            continue
        for diary_node in nodes:
            if diary_node["kind"] != "diary":
                continue
            record = diary_records.get(diary_node["ref_id"]) or {}
            if node["ref_id"] in (record.get("topic_keys") or []):
                edges.append({"source": node["id"], "target": diary_node["id"], "kind": "shaped",
                              "weight": node["weight"]})
    # mentions: same topic, chained by date. A chain stays bounded where a full
    # clique would not, and still reads as one topical thread.
    vocabulary = _topic_vocabulary()
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        for topic in _node_topics(node, store, diary_records, vocabulary):
            groups.setdefault(topic, []).append(node)
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda item: (item["date"], item["id"]))
        for left, right in zip(members, members[1:]):
            edges.append({"source": left["id"], "target": right["id"], "kind": "mentions",
                          "weight": min(left["weight"], right["weight"])})
    return edges


def starmap_graph(manager: Any, *, days: int = 90, kinds: Any = None, q: str = "",
                  limit: int = STARMAP_LIMIT, now: float | None = None) -> dict[str, Any]:
    """Nodes, edges, stats and the truncation flag. Empty graph when memory is off."""
    if not getattr(manager, "enabled", True):
        return _empty_graph()
    store: MemoryStore = manager.store
    wanted = _wanted_kinds(kinds)
    try:
        days = max(1, min(int(days), MAX_DAYS))
    except (TypeError, ValueError):
        days = 90
    try:
        limit = max(1, min(int(limit), STARMAP_LIMIT))
    except (TypeError, ValueError):
        limit = STARMAP_LIMIT
    reference = time.time() if now is None else now
    cutoff = reference - days * 86400
    cutoff_date = local_date(cutoff)

    nodes: list[dict[str, Any]] = []
    if "memory" in wanted:
        nodes.extend(_memory_node(memory) for memory in store.retrieval_candidates(include_done=False)
                     if memory.created_at >= cutoff)
    if "diary" in wanted:
        nodes.extend(_diary_node(diary) for diary in
                     store.list_diaries(since_date=cutoff_date, limit=1000))
    if "spark" in wanted:
        nodes.extend(_spark_node(spark) for spark in store.list_sparks(states=VISIBLE_SPARK_STATES)
                     if spark["created_at"] >= cutoff)
    state = load_self_state(store)
    for kind in ("taste", "worldview"):
        if kind in wanted:
            nodes.extend(_stance_node(kind, entry) for entry in (state.get(kind) or [])
                         if isinstance(entry, dict) and entry.get("topic"))
    if "seed" in wanted:
        nodes.extend(_seed_node(seed) for seed in store.list_seeds(limit=limit))
    if "reflection" in wanted:
        nodes.extend(_reflection_node(reflection) for reflection in store.list_reflections(limit=limit))

    keyword = (q or "").strip().lower() if isinstance(q, str) else ""
    if keyword:
        nodes = [node for node in nodes if keyword in node["label"].lower()]
    nodes.sort(key=lambda node: (node["weight"], node["date"], node["id"]), reverse=True)
    truncated = len(nodes) > limit
    nodes = nodes[:limit]

    diary_records = {node["ref_id"]: store.get_diary(node["ref_id"])
                     for node in nodes if node["kind"] == "diary"}
    edges = _edges(nodes, store, diary_records)
    edges.extend(_persisted_edges(nodes, store))
    stats = {
        "memories": sum(1 for node in nodes if node["kind"] == "memory"),
        "diaries": sum(1 for node in nodes if node["kind"] == "diary"),
        "sparks": sum(1 for node in nodes if node["kind"] == "spark"),
        "taste": sum(1 for node in nodes if node["kind"] == "taste"),
        "worldview": sum(1 for node in nodes if node["kind"] == "worldview"),
    }
    return {"nodes": nodes, "edges": edges, "stats": stats, "truncated": truncated}


def starmap_node(manager: Any, node_id: Any) -> dict[str, Any] | None:
    """Full text for one node, or None (the route turns that into a 404)."""
    if not getattr(manager, "enabled", True) or not isinstance(node_id, str) or ":" not in node_id:
        return None
    store: MemoryStore = manager.store
    prefix, _, identifier = node_id.partition(":")
    if prefix == "mem" and identifier.isdigit():
        memory = store.get(int(identifier))
        if memory is None or memory.status not in ("active", "done"):
            return None
        return memory.to_dict()
    if prefix == "diary":
        return store.diary_for_date(identifier)
    if prefix == "spark" and identifier.isdigit():
        spark = store.get_spark(int(identifier))
        if spark is None or spark["state"] not in VISIBLE_SPARK_STATES:
            return None
        return spark
    if prefix in ("taste", "worldview"):
        state = load_self_state(store)
        for entry in state.get(prefix) or []:
            if isinstance(entry, dict) and entry.get("topic") == identifier:
                return {"kind": prefix, **entry}
        return None
    if prefix == "seed":
        return store.get_seed(identifier)
    if prefix == "reflection":
        return store.get_reflection(identifier)
    return None


NARRATE_SYSTEM = """你是角色本人，正在给用户讲述自己记忆星图里的一个节点。

规则：
1. 只用记录里有的信息，第一人称复述，不超过 120 字。
2. 不要补充记录里没有的事实，不要复述用户说过的话，不要提问。
3. 节点是日记时可以带出当时的心情；是灵感火花时可以保留"闪过一个念头"的语感。
4. 只输出这一句话本身，不要解释、引号或前缀。"""


def narrate_record_kind(record: Any) -> str | None:
    """Which of the five node kinds a full record is; None when nothing is narratable."""
    if not isinstance(record, dict):
        return None
    if "state" in record and "born_diary_id" in record:
        return "spark"
    if "mood" in record and "topic_keys" in record:
        return "diary"
    if "stance" in record and "topic" in record:
        return record["kind"] if record.get("kind") in ("taste", "worldview") else "taste"
    if isinstance(record.get("value"), str) and record["value"].strip():
        return "memory"
    return None


def _self_reference() -> str:
    style = persona_cfg().get("voice_style") or {}
    reference = style.get("self_reference") or "我"
    return reference if isinstance(reference, str) and reference.strip() else "我"


def _valid_narration(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    text = " ".join(text.strip().split())
    if not text or len(text) > MAX_OPENING_CHARS:
        return ""
    if _self_reference() not in text:
        return ""
    return text


def _narrate_template(kind: str, record: dict[str, Any]) -> str:
    if kind == "memory":
        return f"这个我还记着：{record.get('value', '')}"
    if kind == "diary":
        return f"那天我在日记里写：{record.get('title', '')}。当时大概是{record.get('mood', '')}的心情。"
    if kind == "spark":
        return f"我脑子里闪过一个念头：{record.get('text', '')}"
    return f"对{record.get('topic', '这件事')}，我的看法是：{record.get('stance', '')}"


async def narrate_opening(record: Any, provider: Any = None) -> str:
    """First-person retelling of one node's full text (protocol section 13).

    Takes the record exactly as starmap_node returns it. One bounded model call;
    a missing provider, a failed call or failed validation falls back to a
    deterministic template. Returns "" when the record has nothing to say.
    """
    kind = narrate_record_kind(record)
    if kind is None:
        return ""
    template = _narrate_template(kind, record)[:MAX_OPENING_CHARS]
    if provider is None:
        return _valid_narration(template)
    # The user's verbatim quote is evidence, not narration material.
    material = {key: value for key, value in record.items() if key != "evidence_quote"}
    try:
        result = await provider.complete_raw(
            [{"role": "system", "content": NARRATE_SYSTEM},
             {"role": "user", "content": "节点类型：" + kind + "\n记录："
              + json.dumps(material, ensure_ascii=False, separators=(",", ":"))}],
            temperature=0.7, max_tokens=200)
        text = _valid_narration(unwrap_json_text(result.get("text", "")))
    except Exception:
        text = ""
    return text or _valid_narration(template)


__all__ = ["STARMAP_KINDS", "STARMAP_LIMIT", "narrate_opening", "narrate_record_kind",
           "starmap_graph", "starmap_node"]
