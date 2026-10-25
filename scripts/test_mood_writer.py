"""Mood writer: an idle reflection may set a mood, low novelty must not erase it."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 生产库哨兵（#39/#41）：`server.memory.store` 在模块底部建了 `store = MemoryStore()`
# 单例，import 即开库。不先把库钉到内存，测试还没跑就撞在哨兵上。
# 必须写在下面任何 `from server...` 之前——import 顺序就是隔离顺序。
import os  # noqa: E402
os.environ.setdefault('DB_PATH', ':memory:')

from server.assistant.loop import SelfEvolutionCycle  # noqa: E402
from server.memory import seeds as seed_mod  # noqa: E402
from server.memory.diary import mood_overlap  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402

TODAY = "2026-10-17"
GOOD = {
    "reflection": {
        "title": "深夜改稿",
        "narrative": "我翻了他的日记和笔记，想到他深夜改稿可能和白天被打断有关。",
        "discovery": "他深夜改稿可能和白天被打断有关",
        "source_refs": ["diary:2026-10-17", "note:n1"],
    },
    "child_seeds": [{
        "title": "完整时间块",
        "question": "一个人终于获得不被打断的时间时，对原方案的判断会发生什么变化？",
        "reason": "白天被切碎会导致晚上补偿性推翻，需要验证完整时间块是否改变判断",
        "source_refs": ["diary:2026-10-17", "note:n1"],
    }],
    "epiphany_edges": [],
}


def notes():
    return [{"id": "n1", "text": "今天一天被消息打断十几次，根本没进入状态。"}]


class Reflector:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    async def complete_raw(self, messages, **kwargs):
        return {"text": json.dumps(self.payloads.pop(0), ensure_ascii=False)}


def cfg(mood_enabled: bool = True) -> dict:
    return {"enabled": True, "idle_minutes": 1, "max_per_day": 99, "allow_sources": [],
            "max_retries": 0, "max_model_calls_per_cycle": 1, "max_reflection_inputs": 12,
            "similarity_dedupe": 0.86, "child_novelty_min": 0.45,
            "mood": {"enabled": mood_enabled, "default_ttl_minutes": 30,
                     "overwrite_overlap_max": 0.30}}


def new_store(tmp: Path) -> MemoryStore:
    store = MemoryStore(str(tmp / "memory.db"))
    store.add_diary(date=TODAY, title="他又半夜改稿", body="嘴上说算了，凌晨又打开。",
                    mood="调侃", energy=0.4, topic_keys=["作息"], source_message_ids=[1])
    seed_mod.bootstrap_seeds(store, now_iso=TODAY)
    return store


def main() -> int:
    passed = 0
    failed: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        nonlocal passed
        if condition:
            passed += 1
            print(f"  ok   {name}")
        else:
            failed.append(f"{name}{'  ' + detail if detail else ''}")
            print(f"  FAIL {name}  {detail}")

    check("identical text has overlap 1",
          mood_overlap("深夜改稿", "深夜改稿") == 1.0)
    check("unrelated text has low overlap",
          mood_overlap("深夜改稿", "今天阳光很好") < 0.3,
          str(mood_overlap("深夜改稿", "今天阳光很好")))

    with tempfile.TemporaryDirectory() as raw:
        now = 2_000_000_000.0
        store = new_store(Path(raw))
        try:
            result = asyncio.run(SelfEvolutionCycle(
                store=store, provider=Reflector([GOOD]), cfg=cfg(), clock=lambda: now
            ).reflect(idle_seconds=99999, notes_provider=notes))
            check("reflection succeeds", result["status"] == "ok", str(result["trace"]))
            check("idle reflection writes mood", result["trace"].get("mood_written") is True,
                  str(result["trace"]))
            mood = store.get_mood()
            check("mood narrative is the reflection narrative",
                  mood["narrative"].startswith("我翻了他的日记"), str(mood))
            check("mood is active and gets a TTL",
                  mood["state"] == "active" and mood["expires_at"] == now + 1800, str(mood))
            check("mood carries evidence refs", mood["source_refs"] == GOOD["reflection"]["source_refs"],
                  str(mood))
            check("curiosity stays internal trace, not an event",
                  "curiosity_reflection" not in [event.get("type") for event in result["events"]]
                  and result["trace"].get("curiosity_reflection", {}).get("child_seed_ids"),
                  str(result))
        finally:
            store.close()

    with tempfile.TemporaryDirectory() as raw:
        now = 2_000_000_000.0
        store = new_store(Path(raw))
        try:
            store.save_mood(narrative=GOOD["reflection"]["discovery"], reason="test",
                            state="active", expires_at=now + 1800)
            before = store.get_mood()
            result = asyncio.run(SelfEvolutionCycle(
                store=store, provider=Reflector([GOOD]), cfg=cfg(), clock=lambda: now
            ).reflect(idle_seconds=99999, notes_provider=notes))
            after = store.get_mood()
            check("low-novelty discovery does not overwrite mood",
                  result["trace"].get("mood_written") is False
                  and result["trace"].get("mood_skipped") == "low_novelty",
                  str(result["trace"]))
            check("old narrative stays intact", after["narrative"] == before["narrative"],
                  f"{before} / {after}")
        finally:
            store.close()

    with tempfile.TemporaryDirectory() as raw:
        now = 2_000_000_000.0
        store = new_store(Path(raw))
        try:
            store.save_mood(narrative="今天只是有点累", reason="test",
                            state="active", expires_at=now + 1800)
            result = asyncio.run(SelfEvolutionCycle(
                store=store, provider=Reflector([GOOD]), cfg=cfg(), clock=lambda: now
            ).reflect(idle_seconds=99999, notes_provider=notes))
            mood = store.get_mood()
            check("a genuinely new discovery can turn the mood",
                  result["trace"].get("mood_written") is True
                  and mood["narrative"] != "今天只是有点累", str(mood))
        finally:
            store.close()

    with tempfile.TemporaryDirectory() as raw:
        now = 2_000_000_000.0
        store = new_store(Path(raw))
        try:
            result = asyncio.run(SelfEvolutionCycle(
                store=store, provider=Reflector([GOOD]), cfg=cfg(False), clock=lambda: now
            ).reflect(idle_seconds=99999, notes_provider=notes))
            check("mood.enabled=false writes nothing",
                  result["trace"].get("mood_skipped") == "disabled"
                  and store.get_mood()["narrative"] == "", str(result["trace"]))
        finally:
            store.close()

    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
