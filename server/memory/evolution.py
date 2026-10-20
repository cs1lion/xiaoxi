"""后台自进化状态与主动开口（协议第十二节）。

self_state 是角色自己的品味、世界观和好奇心种子，与用户事实永远隔离：
它持久化在记忆库的独立表里，注入只经 persona.bias_block 同一条路，
任何写入都不碰 subject="user"。
"""
from __future__ import annotations

import copy
import time
from typing import TYPE_CHECKING, Any

from ..config import memory_cfg, persona_cfg
from ..llm import _loose_json

if TYPE_CHECKING:  # Annotation-only: importing this module must not open the database.
    from .store import MemoryStore

TASTE_CAP = 12              # 协议第十二节
WORLDVIEW_CAP = 6           # 协议第十二节
STANCE_MAX_CHARS = 60       # 协议第十二节
CURIOSITY_SEED_CAP = 8      # 内部约束
MAX_OPENING_CHARS = 120     # 协议第十二节：主动开口字数上限
MAX_SEED_TEXT_CHARS = 120
CHANGE_KINDS = ("taste", "worldview", "spark")

SELF_EVOLUTION_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "idle_minutes": 240,
    "max_per_day": 3,
    "allow_sources": ["taste", "worldview", "spark"],
    # 下面几个键必须登记在这里：self_evolution_cfg() 只放行 defaults 里有的键，
    # 漏登记 = memory.yaml 里写了也静默失效。max_model_calls_per_cycle / max_retries
    # 正是靠 _reflect 从 cfg 读的，之前就因为没登记而变成死配置（默认值恰好相等，
    # 所以一直看不出症状）。默认值与既有代码里的硬编码回落保持一致，行为不变。
    "max_reflection_inputs": 12,
    "max_model_calls_per_cycle": 2,
    "max_retries": 2,
    "similarity_dedupe": 0.86,
    # 子种子新颖度下限：子问题的内容里必须有多大比例是父问题没有的。
    # 相似度阈值分不开"换措辞的近义复述"和"只差一个字的敷衍"，所以另立一道闸门。
    "child_novelty_min": 0.45,
    # 子种子派生数量（§14 强制裂变 1~2 个）。此前只在 validate_reflection_payload
    # 里硬编码成 REFLECT_MIN/MAX_CHILDREN，memory.yaml 的 seed_fork_min/max 写了
    # 没人读——审计脚本第一次跑就把它们报成死键。
    "seed_fork_min": 1,
    "seed_fork_max": 2,
    # 下面两个是**嵌套**段，§15.1 / §15.2 的配置就挂在这里。同样必须登记：
    # self_evolution_cfg() 只放行 defaults 里出现过的键，而 2026-10-18 之前这两个
    # 键没登记，于是 `circadian.timezone` 与 `mood.*` 在 memory.yaml 里怎么写都读不
    # 到，回落值又恰好等于配置值（Asia/Shanghai / 30 分钟），所以零症状。
    "circadian": {"timezone": "Asia/Shanghai"},
    "mood": {"enabled": True, "default_ttl_minutes": 30},
}

OPENING_SYSTEM = """你是角色本人，现在用户没说话，你想主动开口说一句。

规则：
1. 只能用下面给出的自己的想法（品味/世界观/灵感）作为内容，不补充任何材料里没有的事实。
2. 第一人称，不超过 120 字，像随口一说，不要总结腔，不要以"作为AI"开头。
3. 不要复述用户说过的话；最多带一个轻轻的问句，不要连续追问。
4. 只输出这一句话本身，不要任何解释、引号或前缀。"""


def self_evolution_cfg() -> dict[str, Any]:
    """Config with protocol defaults; a missing section behaves as disabled.

    Nested sections (`circadian` / `mood`) merge one level deep: a yaml that only
    sets `mood.enabled: false` must keep `default_ttl_minutes` from the defaults
    instead of dropping the whole dict.
    """
    cfg = copy.deepcopy(SELF_EVOLUTION_DEFAULTS)
    configured = memory_cfg().get("self_evolution")
    if isinstance(configured, dict):
        for key, value in configured.items():
            if key not in cfg:
                continue
            if isinstance(cfg[key], dict) and isinstance(value, dict):
                cfg[key] = {**cfg[key], **value}
            else:
                cfg[key] = value
    sources = cfg.get("allow_sources")
    cfg["allow_sources"] = [kind for kind in (sources or []) if kind in CHANGE_KINDS]
    return cfg


def load_self_state(store: MemoryStore | None = None) -> dict[str, Any]:
    if store is None:
        from .store import store as default_store
        store = default_store
    return store.get_self_state()


def _capped(entries: Any, cap: int) -> list[dict[str, Any]]:
    """Protocol caps are re-enforced on save: keep the heaviest entries."""
    valid = [entry for entry in (entries or []) if isinstance(entry, dict)]
    valid.sort(key=lambda entry: _weight(entry), reverse=True)
    return valid[:cap]


def _weight(entry: dict[str, Any]) -> float:
    value = entry.get("weight", entry.get("heat", 0))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return min(1.0, max(0.0, float(value)))


def save_self_state(store: MemoryStore, state: dict[str, Any]) -> None:
    store.save_self_state(
        taste=_capped(state.get("taste"), TASTE_CAP),
        worldview=_capped(state.get("worldview"), WORLDVIEW_CAP),
        curiosity_seeds=[seed for seed in (state.get("curiosity_seeds") or [])
                         if isinstance(seed, dict)][:CURIOSITY_SEED_CAP],
        last_cycle_at=state.get("last_cycle_at"),
        cycles_today=state.get("cycles_today"),
        cycles_on=state.get("cycles_on"),
    )


def _stance_of(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    topic, stance, weight = raw.get("topic"), raw.get("stance"), raw.get("weight")
    if (not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > 20
            or not isinstance(stance, str) or not stance.strip()
            or len(stance.strip()) > STANCE_MAX_CHARS
            or isinstance(weight, bool) or not isinstance(weight, (int, float))
            or not 0 <= float(weight) <= 1):
        return None
    return {"topic": topic.strip(), "stance": stance.strip(), "weight": float(weight)}


def plan_state_changes(
    state: dict[str, Any], proposals: Any, *, now: float, max_changes: int,
    allow_sources: tuple[str, ...] = ("taste", "worldview", "spark"),
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Fold validated proposals into the state under every protocol cap.

    Pure: no I/O, no exceptions. Returns (new_state, applied, skipped); skipped
    entries carry the reason so the caller can put it in the trace.
    """
    payload = proposals if isinstance(proposals, dict) else {}
    new_state = {
        "taste": [entry for entry in (state.get("taste") or []) if isinstance(entry, dict)],
        "worldview": [entry for entry in (state.get("worldview") or []) if isinstance(entry, dict)],
        "curiosity_seeds": [seed for seed in (state.get("curiosity_seeds") or []) if isinstance(seed, dict)],
        "last_cycle_at": state.get("last_cycle_at"),
        "cycles_today": state.get("cycles_today", 0),
        "cycles_on": state.get("cycles_on"),
    }
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    def budget_left() -> bool:
        return len(applied) < max_changes

    for kind, cap in (("taste", TASTE_CAP), ("worldview", WORLDVIEW_CAP)):
        if kind not in allow_sources:
            continue
        entries = new_state[kind]
        for raw in payload.get(kind) or []:
            proposal = _stance_of(raw)
            if proposal is None:
                skipped.append({"kind": kind, "reason": "invalid_proposal"})
                continue
            if not budget_left():
                skipped.append({"kind": kind, "reason": "change_budget", "topic": proposal["topic"]})
                continue
            existing = next((entry for entry in entries if entry.get("topic") == proposal["topic"]), None)
            if existing is None and len(entries) >= cap:
                skipped.append({"kind": kind, "reason": "cap_reached", "topic": proposal["topic"]})
                continue
            record = {"topic": proposal["topic"], "stance": proposal["stance"],
                      "weight": proposal["weight"], "updated_at": now}
            if existing is None:
                entries.append(record)
            else:
                existing.update(record)
            applied.append({"kind": kind, "topic": proposal["topic"], "summary": proposal["stance"],
                            "weight": proposal["weight"]})

    seeds = new_state["curiosity_seeds"]
    for raw in payload.get("seeds") or []:
        if not isinstance(raw, dict):
            skipped.append({"kind": "seed", "reason": "invalid_proposal"})
            continue
        text, heat = raw.get("text"), raw.get("heat")
        if (not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_SEED_TEXT_CHARS
                or isinstance(heat, bool) or not isinstance(heat, (int, float))
                or not 0 <= float(heat) <= 1):
            skipped.append({"kind": "seed", "reason": "invalid_proposal"})
            continue
        if not budget_left():
            skipped.append({"kind": "seed", "reason": "change_budget"})
            continue
        if any(seed.get("text") == text.strip() for seed in seeds):
            skipped.append({"kind": "seed", "reason": "duplicate"})
            continue
        if len(seeds) >= CURIOSITY_SEED_CAP:
            skipped.append({"kind": "seed", "reason": "cap_reached"})
            continue
        seeds.append({"text": text.strip(), "heat": min(1.0, max(0.0, float(heat)))})
        applied.append({"kind": "seed", "topic": "好奇心", "summary": text.strip()[:120]})
    return new_state, applied, skipped


def self_evolution_payload(change: Any) -> dict[str, Any] | None:
    """Build the quiet self_evolution event; the send point belongs to the orchestrator."""
    if not isinstance(change, dict) or change.get("kind") not in CHANGE_KINDS:
        return None
    topic, summary = change.get("topic"), change.get("summary")
    if (not isinstance(topic, str) or not topic.strip()
            or not isinstance(summary, str) or not summary.strip()):
        return None
    return {"type": "self_evolution",
            "change": {"kind": change["kind"], "topic": topic.strip()[:40],
                       "summary": summary.strip()[:120]},
            "quiet": True}


def _opening_material(state: dict[str, Any], sparks: list[dict[str, Any]]) -> tuple[str, str] | None:
    """Pick one thing to say: a hot surfaced spark beats a heavy stance."""
    usable = [spark for spark in sparks or []
              if isinstance(spark, dict) and spark.get("state") == "surfaced"
              and isinstance(spark.get("text"), str) and spark["text"].strip()]
    if usable:
        spark = max(usable, key=lambda item: _weight(item))
        return "spark", f"我脑子里突然闪过一个念头：{spark['text'].strip()}"
    for kind in ("worldview", "taste"):
        entries = [entry for entry in (state.get(kind) or []) if isinstance(entry, dict)]
        if entries:
            entry = max(entries, key=_weight)
            return kind, f"我刚刚还在想{entry.get('topic', '一件事')}——{entry.get('stance', '')}"
    return None


def _valid_opening(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    text = " ".join(text.strip().split())
    if not text or len(text) > MAX_OPENING_CHARS:
        return ""
    reference = (persona_cfg().get("voice_style") or {}).get("self_reference") or "我"
    if reference not in text:
        return ""
    return text


async def self_topic_opening(state: dict[str, Any], sparks: list[dict[str, Any]] | None = None,
                             *, provider: Any = None) -> str:
    """One sentence to open with, grounded only in taste / worldview / sparks.

    Falls back to a deterministic template whenever there is no provider, the
    call fails, or the output fails validation; returns "" when there is nothing
    to say. Consuming the spark (latent -> surfaced -> spent) is the caller's job.
    """
    material = _opening_material(state or {}, sparks or [])
    if material is None:
        return ""
    kind, template = material
    if provider is None:
        return _valid_opening(template)
    try:
        result = await provider.complete_raw(
            [{"role": "system", "content": OPENING_SYSTEM},
             {"role": "user", "content": f"材料（{kind}）：{template}"}],
            temperature=0.8, max_tokens=200)
        text = _valid_opening(unwrap_json_text(result.get("text", "")))
    except Exception:
        text = ""
    return text or _valid_opening(template)


def unwrap_json_text(text: Any) -> str:
    """Openings are plain sentences; only unwrap an accidental JSON envelope."""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            payload = _loose_json(stripped)
        except Exception:
            return stripped
        if isinstance(payload, dict):
            for key in ("text", "opening", "line"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
    return stripped


__all__ = [
    "CHANGE_KINDS", "TASTE_CAP", "WORLDVIEW_CAP", "load_self_state", "plan_state_changes",
    "save_self_state", "self_evolution_cfg", "self_evolution_payload", "self_topic_opening",
    "unwrap_json_text",
]
