"""Read-only markdown projection of the memory store (auditable derived layer).

The exploration report's verdict: markdown is NOT the source of truth for 小溪
(SQLite is -- it carries the three-tier confidence signal, temporal
supersession and usage learning that a flat file cannot express), but a
*derived, human-auditable* markdown snapshot is genuinely useful: grep-able,
git-diff-able, openable in any editor, reviewable without touching the live
store. This module renders that projection. It never writes to the store and
never becomes a second source of truth -- delete the file and regenerate it
from SQLite at any time (scripts/export_memory_snapshot.py).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Iterable

from .store import Memory

# The store's ``source`` column IS the three-tier confidence signal that drives
# retrieval decay (retriever.py). Surface it plainly so a reviewer can judge how
# much to trust each line at a glance.
_TIER_NOTE = {
    "stated": "stated · 陈述，保守不过期",
    "inferred": "inferred · 推断，72h 衰减",
    "uncertain": "uncertain · 不确定，72h 衰减",
}
_TIER_ORDER = ("stated", "inferred", "uncertain")


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _day(ts: float | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")


def _bound(memory: Memory) -> str:
    """The user-set fact valid-time bounds, or "" when the fact is unbounded.

    Surfaced here because this projection is the human-auditable surface: a
    reviewer must be able to see that a fact was explicitly bounded, not guess
    it from the prose. ``Memory.to_dict`` excludes these fields (the /api/memories
    shape is fixed), so the snapshot is where they remain visible.
    """
    start, end = memory.fact_valid_from, memory.fact_valid_until
    if start is None and end is None:
        return ""
    parts = []
    if start is not None:
        parts.append(f"自 {_day(start)}")
    if end is not None:
        parts.append(f"至 {_day(end)}")
    return " · 有效时间 " + " ".join(parts)


def render_memory_markdown(memories: Iterable[Memory], *,
                           generated_at: float | None = None) -> str:
    """Render memories as a deterministic, human-auditable markdown document.

    Pure function of its input. ``generated_at`` adds a header timestamp; callers
    omit it for a stable, diff-friendly body -- a projection regenerated from an
    unchanged store should produce an unchanged file. Grouped by subject then
    kind; each record shows its confidence tier, importance, use count and update
    date -- the fields a reviewer needs to judge whether the agent "remembers"
    correctly. Disabled (soft-muted) rows are flagged, never hidden.
    """
    rows = sorted(memories, key=lambda m: (m.subject, m.kind, m.key.lower(), m.id))
    out: list[str] = ["# 小溪 · 记忆快照（只读投影）", ""]
    if generated_at is not None:
        stamp = datetime.fromtimestamp(float(generated_at), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out.append(f"> 生成于 {stamp} · 来源 SQLite（唯一真相源）；本文件可删可重建，不是第二写入方。")
    else:
        out.append("> 只读投影 · SQLite 为唯一真相源；本文件可删可重建，不是第二写入方。")
    out.append("")

    if not rows:
        out.append("（无记忆）")
        out.append("")
        return "\n".join(out)

    tiers = Counter(m.source or "—" for m in rows)
    breakdown = " · ".join(f"{tier} {tiers[tier]}" for tier in _TIER_ORDER if tiers.get(tier))
    extra = " · ".join(f"{tier} {tiers[tier]}" for tier in sorted(tiers)
                       if tier not in _TIER_ORDER)
    statuses = Counter(m.status or "—" for m in rows)
    status_part = " · ".join(f"{status} {statuses[status]}" for status in sorted(statuses))
    summary = f"共 {len(rows)} 条记忆（{status_part}）"
    if breakdown:
        summary += f"（{breakdown}）"
    if extra:
        summary += f"；其它 {extra}"
    out.append(summary + "。")
    out.append("")

    subject = kind = None
    for m in rows:
        if m.subject != subject:
            subject, kind = m.subject, None
            out.append(f"## {m.subject or '（无 subject）'}")
            out.append("")
        if m.kind != kind:
            kind = m.kind
            out.append(f"### {m.kind or '（无 kind）'}")
            out.append("")
        tier = _TIER_NOTE.get(m.source, m.source or "—")
        suffix = " · 已停用" if m.disabled else ""
        if m.status and m.status != "active":
            suffix += f" · {m.status}"
        bound = _bound(m)
        out.append(f"- **{_one_line(m.key)}**：{_one_line(m.value)}")
        out.append(
            f"  - {tier} · 重要度 {m.importance:.2f} · 使用 {m.use_count} 次 · "
            f"更新 {_day(m.updated_at)}{bound}{suffix}")
    out.append("")
    return "\n".join(out)
