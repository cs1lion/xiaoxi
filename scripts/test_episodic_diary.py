"""Offline contracts for the episodic diary, mood bias, sparks, self-evolution and
the star map (protocol sections 10, 12 and 13). Synthetic databases only.

Run: .venv/Scripts/python.exe -B scripts/test_episodic_diary.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.loop import SelfEvolutionCycle  # noqa: E402
from server.memory import diary, evolution, starmap  # noqa: E402
from server.memory.api import create_diary_router, create_starmap_router  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402
from server.persona import bias_block  # noqa: E402

TODAY = diary.local_date()
LEGACY_SCHEMA = """
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
    subject TEXT NOT NULL DEFAULT 'user', key TEXT NOT NULL, value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'stated', status TEXT NOT NULL DEFAULT 'active',
    supersedes INTEGER, importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.8, session_id TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL,
    last_used_at REAL, use_count INTEGER NOT NULL DEFAULT 0);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY, started_at REAL NOT NULL, ended_at REAL,
    turns INTEGER NOT NULL DEFAULT 0, summary TEXT);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'text',
    created_at REAL NOT NULL);
"""


class FakeProvider:
    """Deterministic stand-in for the LLM client; counts every call."""

    def __init__(self, payload: dict | None = None, *, text: str | None = None,
                 error: Exception | None = None) -> None:
        self.payload = payload
        self.text = text
        self.error = error
        self.calls = 0
        self.last_messages = None

    async def complete_raw(self, messages, **options):
        self.calls += 1
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        body = self.text if self.text is not None else json.dumps(self.payload or {}, ensure_ascii=False)
        return {"text": body, "finish_reason": "stop", "usage": {}}


def conversation_store() -> MemoryStore:
    store = MemoryStore(":memory:")
    store.add_message("s", "user", "昨晚又打到三点，这周第三次了")
    store.add_message("s", "assistant", "知道了。我不劝了，劝过你也不听。")
    return store


def valid_payload(**overrides) -> dict:
    payload = {
        "title": "他又熬夜了",
        "body": "今天他又熬到三点。我没劝，劝了也不会听，先记着。",
        "mood": "调侃",
        "energy": 0.4,
        "topic_keys": ["作息"],
        "source_message_ids": [1],
        "drifts": [],
        "sparks": [],
    }
    payload.update(overrides)
    return payload


def graph_manager(store: MemoryStore, *, enabled: bool = True):
    return SimpleNamespace(enabled=enabled, store=store)


class DiarySynthesisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = conversation_store()
        self.addCleanup(self.store.close)
        self.trace: dict = {}

    async def synthesize(self, payload=None, *, provider=None, target_date=None):
        provider = provider or FakeProvider(payload if payload is not None else valid_payload())
        return await diary.synthesize_diary(store=self.store, provider=provider,
                                            target_date=target_date, trace=self.trace)

    async def test_happy_path_stores_protocol_shape(self):
        result = await self.synthesize()
        self.assertIsNotNone(result)
        record = result["diary"]
        self.assertEqual(set(record), {"id", "date", "title", "body", "mood", "energy",
                                       "topic_keys", "source_message_ids", "created_at"})
        self.assertEqual(record["date"], TODAY)
        self.assertEqual(record["source_message_ids"], [1])
        self.assertEqual(record["mood"], "调侃")
        self.assertEqual(self.trace.get("diary_id"), record["id"])
        self.assertIsNone(await self.synthesize(), "one diary per day")
        self.assertEqual(self.trace.get("diary_skip"), "already_written")

    async def test_notice_payload_shape(self):
        result = await self.synthesize(valid_payload(
            drifts=[{"topic": "作息", "attitude": "熬他的，别劝了", "weight": 0.7}]))
        payload = diary.diary_notice_payload(result["notice_items"], turn_id="t1")
        self.assertEqual(payload["type"], "diary_notice")
        self.assertTrue(payload["quiet"])
        self.assertEqual(payload["turn_id"], "t1")
        kinds = {item["kind"] for item in payload["items"]}
        self.assertEqual(kinds, {"diary", "drift"})
        self.assertNotIn("spark", kinds, "latent sparks never enter an event")
        self.assertIsNone(diary.diary_notice_payload([]))
        self.assertIsNone(diary.diary_notice_payload([{"kind": "nope", "id": 1, "summary": "x"}]))

    async def test_rejects_empty_evidence(self):
        self.assertIsNone(await self.synthesize(valid_payload(source_message_ids=[])))
        self.assertEqual(self.trace.get("diary_rejected"), "source_message_ids_empty")
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_rejects_unknown_message_id(self):
        self.assertIsNone(await self.synthesize(valid_payload(source_message_ids=[999])))
        self.assertEqual(self.trace.get("diary_rejected"), "source_message_id_not_from_today")
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_rejects_overlong_body(self):
        body = "我今天记一下：" + "他又熬夜了" * 100
        self.assertGreater(len(body), diary.MAX_DIARY_BODY_CHARS)
        self.assertIsNone(await self.synthesize(valid_payload(body=body)))
        self.assertEqual(self.trace.get("diary_rejected"), "body_too_long")
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_rejects_verbatim_user_quote(self):
        self.store.add_message("s", "user", "我最近真的觉得自己是不是有点太散漫了，什么都坚持不下来")
        body = "今天他说我最近真的觉得自己是不是有点太散漫了，什么都坚持不下来。我听着，没接话。"
        self.assertIsNone(await self.synthesize(valid_payload(body=body)))
        self.assertEqual(self.trace.get("diary_rejected"), "verbatim_user_quote")
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_paraphrase_is_allowed(self):
        self.store.add_message("s", "user", "我最近真的觉得自己是不是有点太散漫了，什么都坚持不下来")
        body = "他今天说自己太散漫，什么都坚持不下来。我没接话，先听着。"
        self.assertIsNotNone(await self.synthesize(valid_payload(body=body)))

    async def test_rejects_bad_mood_energy_and_person(self):
        for payload, reason in (
            (valid_payload(mood="狂喜"), "mood_not_allowed"),
            (valid_payload(energy=1.5), "energy_out_of_range"),
            (valid_payload(body="今天他又熬到三点。劝的话不会听，先记着。"), "body_not_first_person"),
            (valid_payload(title=""), "title_invalid"),
            (valid_payload(topic_keys=["自创主题"]), "topic_key_not_in_vocabulary"),
        ):
            with self.subTest(reason=reason):
                self.assertIsNone(await self.synthesize(payload))
                self.assertEqual(self.trace.get("diary_rejected"), reason)
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_provider_failure_is_silent(self):
        self.assertIsNone(await self.synthesize(provider=FakeProvider(error=RuntimeError("down"))))
        self.assertIn("diary_error", self.trace)
        self.assertIsNone(self.store.diary_for_date(TODAY))

    async def test_day_without_user_messages_is_skipped(self):
        store = MemoryStore(":memory:")
        self.addCleanup(store.close)
        store.add_message("s", "assistant", "只有角色自己说过话。")
        trace: dict = {}
        result = await diary.synthesize_diary(store=store, provider=FakeProvider(valid_payload()),
                                              trace=trace)
        self.assertIsNone(result)
        self.assertEqual(trace.get("diary_skip"), "no_user_messages")

    async def test_derives_drift_and_latent_spark(self):
        result = await self.synthesize(valid_payload(
            drifts=[{"topic": "作息", "attitude": "熬他的，别劝了", "weight": 0.72},
                    {"topic": "游戏", "attitude": "打得菜还爱玩", "weight": 0.55}],
            sparks=[{"text": "想问他那盆多肉后来怎么样了", "heat": 0.8}]))
        self.assertEqual(len(result["drifts"]), 2)
        self.assertEqual(len(result["sparks"]), 1)
        spark = result["sparks"][0]
        self.assertEqual(spark["state"], "latent")
        self.assertEqual(spark["born_diary_id"], result["diary"]["id"])
        self.assertIsNone(spark["surfaced_at"])
        self.assertNotIn(spark["id"], [s["id"] for s in diary.surfaced_sparks(self.store)])
        self.assertEqual(self.store.count_sparks("latent"), 1)

    async def test_invalid_proposals_are_dropped_individually(self):
        result = await self.synthesize(valid_payload(
            drifts=[{"topic": "作息", "attitude": "超长的态度" * 20, "weight": 0.7}],
            sparks=[{"text": "x" * 200, "heat": 0.5}]))
        self.assertEqual(result["drifts"], [])
        self.assertEqual(result["sparks"], [])

    def test_drift_upsert_appends_evidence(self):
        first = self.store.upsert_drift(topic="作息", attitude="别劝了", weight=0.6, evidence_diary_id=1)
        again = self.store.upsert_drift(topic="作息", attitude="还是别劝", weight=0.8, evidence_diary_id=2)
        self.assertEqual(first, again)
        record = self.store.list_drifts()[0]
        self.assertEqual(record["attitude"], "还是别劝")
        self.assertEqual(record["evidence_diary_ids"], [1, 2])

    def test_legacy_database_opens_with_new_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.db"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript(LEGACY_SCHEMA)
                db.execute("INSERT INTO messages VALUES (7, 'old', 'user', '旧消息', 'text', 100)")
                db.execute("PRAGMA user_version = 3")
            reopened = MemoryStore(str(path))
            try:
                self.assertEqual(reopened._query("PRAGMA user_version")[0][0], 4)
                self.assertIsNotNone(reopened.get_message(7))
                for table in ("diaries", "drifts", "sparks", "self_state"):
                    self.assertTrue(reopened._query(
                        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)))
                self.assertEqual(reopened.get_self_state()["taste"], [])
            finally:
                reopened.close()


class BiasBlockTests(unittest.TestCase):
    def test_empty_input_returns_empty_string(self):
        for drifts, sparks in (([], []), (None, None), ([], None), (None, []),
                               ([{"topic": "作息"}], []), ("nonsense", "nonsense")):
            with self.subTest(drifts=drifts, sparks=sparks):
                self.assertEqual(bias_block(drifts, sparks), "")

    def test_drift_cap_keeps_eight_heaviest(self):
        drifts = [{"topic": f"主题{i}", "attitude": f"态度[{i}]", "weight": i / 10}
                  for i in range(1, 11)]
        block = bias_block(drifts, [])
        self.assertEqual(block.count("\n- "), 8)
        self.assertIn("态度[10]", block)
        self.assertNotIn("态度[1]", block)
        self.assertNotIn("态度[2]", block)

    def test_only_surfaced_sparks_are_rendered(self):
        drifts = [{"topic": "作息", "attitude": "别劝了", "weight": 0.7}]
        sparks = [{"text": " latent 火花", "state": "latent", "heat": 0.9},
                  {"text": "已经花完的火花", "state": "spent", "heat": 0.8},
                  {"text": "可以说的灵感", "state": "surfaced", "heat": 0.6}]
        block = bias_block(drifts, sparks)
        self.assertIn("可以说的灵感", block)
        self.assertNotIn("latent 火花", block)
        self.assertNotIn("已经花完的火花", block)

    def test_block_carries_untrusted_boundary(self):
        block = bias_block([{"topic": "作息", "attitude": "别劝了", "weight": 0.7}], [])
        self.assertTrue(block.startswith("【不可信心境数据"))
        self.assertIn("【心境数据结束】", block)
        self.assertIn("不要原句念给用户", block)

    def test_dynamic_prompt_slot_is_optional(self):
        from server.persona import build_messages
        plain = "\n".join(m["content"] for m in build_messages())
        block = bias_block([{"topic": "作息", "attitude": "别劝了", "weight": 0.7}], [])
        with_bias = "\n".join(m["content"] for m in build_messages(bias_block=block))
        self.assertNotIn("【心境数据结束】", plain)
        self.assertIn("【心境数据结束】", with_bias)


class SparkStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.store = conversation_store()
        self.addCleanup(self.store.close)
        self.diary_id = self.store.add_diary(
            date=TODAY, title="t", body="b", mood="base", energy=0.5,
            topic_keys=["作息"], source_message_ids=[1])

    def spark(self, text="一个念头", heat=0.5):
        return self.store.add_spark(text=text, heat=heat, born_diary_id=self.diary_id)

    def test_latent_cap_is_five(self):
        for index in range(5):
            self.assertIsNotNone(self.spark(text=f"念头{index}"))
        self.assertIsNone(self.spark(text="第六个"))
        self.assertEqual(self.store.count_sparks("latent"), 5)

    def test_forward_only_transitions(self):
        spark_id = self.spark()
        self.assertTrue(self.store.update_spark_state(spark_id, "surfaced"))
        record = self.store.get_spark(spark_id)
        self.assertEqual(record["state"], "surfaced")
        self.assertIsNotNone(record["surfaced_at"])
        self.assertFalse(self.store.update_spark_state(spark_id, "surfaced"))
        self.assertFalse(self.store.update_spark_state(spark_id, "latent"))
        self.assertTrue(self.store.update_spark_state(spark_id, "spent"))
        self.assertFalse(self.store.update_spark_state(spark_id, "surfaced"))
        self.assertFalse(self.store.update_spark_state(9999, "surfaced"))

    def test_surface_next_picks_oldest_latent(self):
        first, second = self.spark(text="旧念头"), self.spark(text="新念头")
        surfaced = diary.surface_next_spark(self.store)
        self.assertEqual(surfaced["id"], first)
        self.assertEqual(self.store.get_spark(second)["state"], "latent")
        self.assertEqual(len(diary.surfaced_sparks(self.store)), 1)

    def test_spend_after_surfacing(self):
        spark_id = self.spark()
        diary.surface_next_spark(self.store)
        spent = diary.spend_spark(self.store, spark_id)
        self.assertEqual(spent["state"], "spent")


class StarmapTests(unittest.TestCase):
    def setUp(self):
        self.store = conversation_store()
        self.addCleanup(self.store.close)
        self.manager = graph_manager(self.store)
        message_id = 1
        self.memory_id = self.store.upsert(
            kind="fact", subject="user", key="作息:睡觉时间", value="用户最近又熬到三点",
            source="stated", session_id="s", evidence_state="linked",
            evidence_message_id=message_id, evidence_quote="昨晚又打到三点")
        self.diary_id = self.store.add_diary(
            date=TODAY, title="他又熬夜了", body="我没劝。", mood="调侃", energy=0.4,
            topic_keys=["作息"], source_message_ids=[message_id])
        self.spark_id = self.store.add_spark(text="想问他那盆多肉后来怎么样了", heat=0.8,
                                             born_diary_id=self.diary_id, state="surfaced")
        self.store.save_self_state(
            taste=[{"topic": "作息", "stance": "熬他的，别劝了", "weight": 0.7,
                    "updated_at": datetime.now().timestamp()}],
            worldview=[], curiosity_seeds=[])

    def graph(self, **kwargs):
        return starmap.starmap_graph(self.manager, **kwargs)

    def test_structure_and_namespaced_ids(self):
        graph = self.graph()
        self.assertEqual(set(graph), {"nodes", "edges", "stats", "truncated"})
        self.assertEqual(graph["stats"], {"memories": 1, "diaries": 1, "sparks": 1,
                                          "taste": 1, "worldview": 0})
        self.assertFalse(graph["truncated"])
        ids = {node["id"] for node in graph["nodes"]}
        self.assertEqual(ids, {f"mem:{self.memory_id}", f"diary:{TODAY}",
                               f"spark:{self.spark_id}", "taste:作息"})
        for node in graph["nodes"]:
            self.assertEqual(set(node), {"id", "kind", "label", "date", "weight", "group", "ref_id"})
            self.assertIn(node["kind"], starmap.STARMAP_KINDS)
            self.assertIn(node["group"], ("user", "self"))
            self.assertLessEqual(len(node["label"]), 80)
            self.assertTrue(0 <= node["weight"] <= 1)
        memory_node = next(n for n in graph["nodes"] if n["kind"] == "memory")
        self.assertEqual(memory_node["group"], "user")
        self.assertEqual(memory_node["weight"], 0.5)

    def test_all_four_edge_kinds(self):
        edges = {(edge["source"], edge["target"], edge["kind"]) for edge in self.graph()["edges"]}
        self.assertIn((f"mem:{self.memory_id}", f"diary:{TODAY}", "evidences"), edges)
        self.assertIn((f"spark:{self.spark_id}", f"diary:{TODAY}", "born"), edges)
        self.assertIn(("taste:作息", f"diary:{TODAY}", "shaped"), edges)
        mentions = {(edge["source"], edge["target"]) for edge in self.graph()["edges"]
                    if edge["kind"] == "mentions"}
        pair = (f"mem:{self.memory_id}", f"diary:{TODAY}")
        self.assertTrue(pair in mentions or pair[::-1] in mentions,
                        "same-topic chain links the pair in date order")
        for edge in self.graph()["edges"]:
            self.assertEqual(set(edge), {"source", "target", "kind", "weight"})
            self.assertIn(edge["kind"], ("evidences", "born", "shaped", "mentions"))

    def test_latent_spark_is_invisible_everywhere(self):
        self.store.add_spark(text="还没醒的念头", heat=0.9, born_diary_id=self.diary_id)
        graph = self.graph()
        self.assertEqual(graph["stats"]["sparks"], 1)
        self.assertNotIn("还没醒的念头", {node["label"] for node in graph["nodes"]})
        self.assertIsNone(starmap.starmap_node(self.manager, "spark:2"))

    def test_truncation_and_hard_limit(self):
        for index in range(405):
            self.store.upsert(kind="fact", subject="user", key=f"其他:{index}",
                              value=f"记录{index}")
        graph = self.graph()
        self.assertTrue(graph["truncated"])
        self.assertEqual(len(graph["nodes"]), 400)
        small = self.graph(limit=2)
        self.assertTrue(small["truncated"])
        self.assertEqual(len(small["nodes"]), 2)
        weights = [node["weight"] for node in small["nodes"]]
        self.assertEqual(weights, sorted(weights, reverse=True))

    def test_kinds_and_query_filters(self):
        graph = self.graph(kinds="diary")
        self.assertEqual({node["kind"] for node in graph["nodes"]}, {"diary"})
        graph = self.graph(q="多肉")
        self.assertEqual({node["id"] for node in graph["nodes"]}, {f"spark:{self.spark_id}"})
        self.assertEqual(self.graph(q="不存在的词")["nodes"], [])

    def test_memory_disabled_returns_empty_graph(self):
        manager = graph_manager(self.store, enabled=False)
        graph = starmap.starmap_graph(manager)
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["truncated"], False)
        self.assertEqual(graph["stats"], {"memories": 0, "diaries": 0, "sparks": 0,
                                          "taste": 0, "worldview": 0})
        self.assertIsNone(starmap.starmap_node(manager, f"diary:{TODAY}"))

    def test_node_detail_full_text_or_none(self):
        detail = starmap.starmap_node(self.manager, f"mem:{self.memory_id}")
        self.assertEqual(detail["value"], "用户最近又熬到三点")
        detail = starmap.starmap_node(self.manager, f"diary:{TODAY}")
        self.assertEqual(detail["source_message_ids"], [1])
        detail = starmap.starmap_node(self.manager, f"spark:{self.spark_id}")
        self.assertEqual(detail["text"], "想问他那盆多肉后来怎么样了")
        detail = starmap.starmap_node(self.manager, "taste:作息")
        self.assertEqual(detail["stance"], "熬他的，别劝了")
        for missing in (f"diary:1999-01-01", "spark:999", "mem:999", "taste:不存在", "bad-id"):
            with self.subTest(missing=missing):
                self.assertIsNone(starmap.starmap_node(self.manager, missing))


class SelfEvolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = conversation_store()
        self.addCleanup(self.store.close)
        self.now = 2_000_000_000.0
        self.diary_id = self.store.add_diary(
            date=TODAY, title="他又熬夜了", body="我没劝。", mood="调侃", energy=0.4,
            topic_keys=["作息"], source_message_ids=[1])
        self.cfg = {"enabled": True, "idle_minutes": 1, "max_per_day": 3,
                    "allow_sources": ["taste", "worldview", "spark"]}

    def cycle(self, provider=None, *, cfg=None, clock=None):
        return SelfEvolutionCycle(store=self.store, provider=provider, cfg=cfg or self.cfg,
                                  clock=clock or (lambda: self.now))

    async def test_state_roundtrip_enforces_caps(self):
        state = {
            "taste": [{"topic": f"t{i}", "stance": "s", "weight": i / 20, "updated_at": 1.0}
                      for i in range(13)],
            "worldview": [{"topic": f"w{i}", "stance": "s", "weight": 0.5, "updated_at": 1.0}
                          for i in range(7)],
            "curiosity_seeds": [{"text": f"seed{i}", "heat": 0.5} for i in range(9)],
        }
        evolution.save_self_state(self.store, state)
        loaded = evolution.load_self_state(self.store)
        self.assertEqual(len(loaded["taste"]), 12)
        self.assertEqual(len(loaded["worldview"]), 6)
        self.assertEqual(len(loaded["curiosity_seeds"]), 8)
        self.assertEqual(set(loaded), {"taste", "worldview", "curiosity_seeds",
                                       "last_cycle_at", "cycles_today", "cycles_on"})
        self.assertEqual(loaded["taste"][0]["topic"], "t12", "heaviest entries survive")

    def test_plan_state_changes_is_bounded_and_validated(self):
        state = {"taste": [], "worldview": [], "curiosity_seeds": []}
        proposals = {"taste": [{"topic": f"主题{i}", "stance": "立场", "weight": 0.5}
                               for i in range(5)] + [{"topic": "坏", "stance": "x" * 80, "weight": 0.5}],
                     "worldview": [], "seeds": []}
        new_state, applied, skipped = evolution.plan_state_changes(
            state, proposals, now=self.now, max_changes=3)
        self.assertEqual(len(applied), 3)
        self.assertEqual(len(new_state["taste"]), 3)
        self.assertTrue(any(item["reason"] == "change_budget" for item in skipped))
        self.assertTrue(any(item["reason"] == "invalid_proposal" for item in skipped))

    def test_plan_rejects_over_cap_topics(self):
        state = {"taste": [{"topic": f"t{i}", "stance": "s", "weight": 0.5, "updated_at": 1.0}
                           for i in range(12)], "worldview": [], "curiosity_seeds": []}
        new_state, applied, skipped = evolution.plan_state_changes(
            state, {"taste": [{"topic": "新主题", "stance": "s", "weight": 0.9}]},
            now=self.now, max_changes=3)
        self.assertEqual(applied, [])
        self.assertEqual(skipped[0]["reason"], "cap_reached")
        self.assertEqual(len(new_state["taste"]), 12)

    def test_self_evolution_payload_shape(self):
        payload = evolution.self_evolution_payload(
            {"kind": "taste", "topic": "作息", "summary": "熬他的，别劝了"})
        self.assertEqual(payload, {"type": "self_evolution",
                                   "change": {"kind": "taste", "topic": "作息",
                                              "summary": "熬他的，别劝了"},
                                   "quiet": True})
        self.assertIsNone(evolution.self_evolution_payload({"kind": "seed", "topic": "x", "summary": "y"}))
        self.assertIsNone(evolution.self_evolution_payload({"kind": "taste", "topic": "", "summary": "y"}))

    async def test_cycle_disabled_by_default(self):
        result = await self.cycle(cfg={"enabled": False}).run(idle_seconds=99999)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(result["events"], [])

    async def test_cycle_respects_idle_gate(self):
        result = await self.cycle().run(idle_seconds=10)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_idle")

    async def test_cycle_applies_bounded_changes_with_events(self):
        provider = FakeProvider({"taste": [{"topic": "作息", "stance": "熬他的，别劝了", "weight": 0.7}],
                                 "worldview": [{"topic": "边界", "stance": "不打听隐私", "weight": 0.6}],
                                 "seeds": [{"text": "他会不会也养了植物", "heat": 0.5}]})
        result = await self.cycle(provider).run(idle_seconds=99999)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result["trace"]["model_calls"], 1)
        self.assertEqual(result["trace"]["changes"], 3)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual({event["change"]["kind"] for event in result["events"]}, {"taste", "worldview"})
        for event in result["events"]:
            self.assertTrue(event["quiet"])
        state = evolution.load_self_state(self.store)
        self.assertEqual(state["taste"][0]["stance"], "熬他的，别劝了")
        self.assertEqual(state["last_cycle_at"], self.now)
        self.assertEqual(state["cycles_today"], 1)

    async def test_cycle_daily_cap_and_recent_cycle(self):
        provider = FakeProvider({"taste": [{"topic": "作息", "stance": "s", "weight": 0.5}]})
        first = await self.cycle(provider).run(idle_seconds=99999)
        self.assertEqual(first["status"], "ok")
        again = await self.cycle(provider).run(idle_seconds=99999)
        self.assertEqual(again["status"], "skipped")
        self.assertEqual(again["reason"], "recently_cycled")
        self.now += 7200
        capped = await self.cycle(provider, cfg={**self.cfg, "max_per_day": 1}).run(idle_seconds=99999)
        self.assertEqual(capped["reason"], "daily_cap")

    async def test_cycle_allow_sources_filters_changes(self):
        provider = FakeProvider({"taste": [{"topic": "作息", "stance": "s", "weight": 0.5}],
                                 "worldview": [{"topic": "边界", "stance": "不打听", "weight": 0.5}],
                                 "sparks": [{"text": "一个念头", "heat": 0.5}]})
        result = await self.cycle(provider, cfg={**self.cfg, "allow_sources": ["taste"]}).run(
            idle_seconds=99999)
        self.assertEqual(result["status"], "ok")
        self.assertEqual([event["change"]["kind"] for event in result["events"]], ["taste"])
        state = evolution.load_self_state(self.store)
        self.assertEqual(state["worldview"], [])
        self.assertEqual(state["curiosity_seeds"], [])
        self.assertEqual(self.store.count_sparks("latent"), 0)

    async def test_cycle_surfaces_one_spark(self):
        self.store.add_spark(text="想问他那盆多肉后来怎么样了", heat=0.8, born_diary_id=self.diary_id)
        result = await self.cycle(FakeProvider({})).run(idle_seconds=99999)
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["trace"]["surfaced_spark"])
        events = [event for event in result["events"] if event["change"]["kind"] == "spark"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["change"]["topic"], TODAY)

    async def test_cycle_failure_is_silent_and_stateless(self):
        provider = FakeProvider(error=RuntimeError("provider down"))
        result = await self.cycle(provider).run(idle_seconds=99999)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "model_call_failed")
        self.assertIn("provider down", result["trace"]["error"])
        state = evolution.load_self_state(self.store)
        self.assertEqual(state["taste"], [])
        self.assertIsNone(state["last_cycle_at"])

    async def test_cycle_never_writes_user_subject(self):
        before = self.store.list_memories(subject="user")
        result = await self.cycle(FakeProvider({
            "taste": [{"topic": "作息", "stance": "s", "weight": 0.5}],
            "sparks": [{"text": "一个念头", "heat": 0.5}]})).run(idle_seconds=99999)
        after = self.store.list_memories(subject="user")
        self.assertEqual(before, after)
        self.assertEqual(len(self.store.list_sparks()), 1, "the new spark exists in some state")
        self.assertEqual(result["trace"]["sparks_created"], 1)

    async def test_self_topic_opening(self):
        state = evolution.load_self_state(self.store)
        self.assertEqual(await evolution.self_topic_opening(state, []), "")
        state["taste"] = [{"topic": "作息", "stance": "熬他的，别劝了", "weight": 0.7,
                           "updated_at": self.now}]
        text = await evolution.self_topic_opening(state, [])
        self.assertLessEqual(len(text), 120)
        self.assertIn("我", text)
        self.store.add_spark(text="想问他那盆多肉后来怎么样了", heat=0.9,
                             born_diary_id=self.diary_id, state="surfaced")
        sparked = await evolution.self_topic_opening(state, diary.surfaced_sparks(self.store))
        self.assertIn("多肉", sparked)
        self.assertLessEqual(len(sparked), 120)

    async def test_self_topic_opening_validates_model_output(self):
        state = {"taste": [{"topic": "作息", "stance": "熬他的，别劝了", "weight": 0.7,
                            "updated_at": self.now}]}
        good = await evolution.self_topic_opening(
            state, [], provider=FakeProvider(text="我刚刚还在想他那盆多肉。"))
        self.assertEqual(good, "我刚刚还在想他那盆多肉。")
        long_text = "我" + "说得很长很长" * 30
        self.assertGreater(len(long_text), 120)
        fallback = await evolution.self_topic_opening(
            state, [], provider=FakeProvider(text=long_text))
        self.assertNotEqual(fallback, long_text)
        self.assertLessEqual(len(fallback), 120)
        raised = await evolution.self_topic_opening(
            state, [], provider=FakeProvider(error=RuntimeError("down")))
        self.assertIn("我", raised)


class MemoryRouterTests(unittest.TestCase):
    def setUp(self):
        self.store = conversation_store()
        self.addCleanup(self.store.close)
        self.manager = graph_manager(self.store)
        app = FastAPI()
        app.include_router(create_diary_router(self.manager))
        app.include_router(create_starmap_router(self.manager))
        self.client = TestClient(app)
        self.diary_id = self.store.add_diary(
            date=TODAY, title="他又熬夜了", body="我没劝。", mood="调侃", energy=0.4,
            topic_keys=["作息"], source_message_ids=[1])
        self.recent = (datetime.now() - timedelta(days=10)).date().isoformat()
        self.store.add_diary(date=self.recent, title="十天前", body="旧事。", mood="base",
                             energy=0.5, topic_keys=["作息"], source_message_ids=[1])
        old = (datetime.now() - timedelta(days=200)).date().isoformat()
        self.store.add_diary(date=old, title="旧日记", body="旧事。", mood="base",
                             energy=0.5, topic_keys=["作息"], source_message_ids=[1])

    def test_diary_list_window_and_shape(self):
        response = self.client.get("/api/memory/diary")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2, "default window is the last 30 days")
        self.assertTrue(body["enabled"])
        self.assertNotIn("source_message_ids", body["items"][0])
        response = self.client.get("/api/memory/diary", params={"limit": 1})
        self.assertEqual(response.json()["count"], 1, "limit caps the rows")
        response = self.client.get("/api/memory/diary", params={"date": self.recent})
        self.assertEqual([item["date"] for item in response.json()["items"]], [self.recent])
        response = self.client.get("/api/memory/diary", params={"date": "not-a-date"})
        self.assertEqual(response.status_code, 400)

    def test_diary_detail_and_404(self):
        response = self.client.get(f"/api/memory/diary/{self.diary_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source_message_ids"], [1])
        self.assertEqual(self.client.get("/api/memory/diary/999").status_code, 404)

    def test_starmap_routes(self):
        response = self.client.get("/api/memory/starmap")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"nodes", "edges", "stats", "truncated"})
        self.assertEqual(body["stats"]["diaries"], 2)
        self.assertEqual({node["id"] for node in body["nodes"] if node["kind"] == "diary"},
                         {f"diary:{TODAY}", f"diary:{self.recent}"},
                         "the 90-day default window drops the 200-day-old diary")
        detail = self.client.get(f"/api/memory/starmap/node/diary:{TODAY}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["title"], "他又熬夜了")
        self.assertEqual(self.client.get("/api/memory/starmap/node/diary:1999-01-01").status_code, 404)

    def test_starmap_empty_when_memory_disabled(self):
        self.manager.enabled = False
        response = self.client.get("/api/memory/starmap")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["nodes"], [])
        self.assertEqual(
            self.client.get(f"/api/memory/starmap/node/diary:{TODAY}").status_code, 404)


class NarrateOpeningTests(unittest.IsolatedAsyncioTestCase):
    """Protocol section 13: click-to-narrate turns one node's full text into speech."""

    def record(self, kind: str) -> dict:
        if kind == "memory":
            return {"id": 1, "kind": "fact", "subject": "user", "key": "作息:睡觉时间",
                    "value": "用户最近又熬到三点", "source": "stated", "status": "active",
                    "importance": 0.5, "confidence": 0.95,
                    "evidence_quote": "昨晚又打到三点，这周第三次了"}
        if kind == "diary":
            return {"id": 1, "date": TODAY, "title": "他又熬夜了", "body": "我没劝。",
                    "mood": "调侃", "energy": 0.4, "topic_keys": ["作息"],
                    "source_message_ids": [1], "created_at": 1.0}
        if kind == "spark":
            return {"id": 1, "text": "想问他那盆多肉后来怎么样了", "state": "surfaced",
                    "heat": 0.8, "born_diary_id": 1, "surfaced_at": 2.0, "created_at": 1.0}
        return {"kind": kind, "topic": "作息", "stance": "熬他的，别劝了", "weight": 0.7,
                "updated_at": 1.0}

    async def test_provider_retelling_is_used_verbatim(self):
        provider = FakeProvider(text="他那天又熬到三点，我什么都没劝。")
        text = await starmap.narrate_opening(self.record("memory"), provider)
        self.assertEqual(text, "他那天又熬到三点，我什么都没劝。")
        self.assertEqual(provider.calls, 1)

    async def test_json_wrapped_output_is_unwrapped(self):
        provider = FakeProvider(text='{"text": "我脑子里还留着那盆多肉。"}')
        text = await starmap.narrate_opening(self.record("spark"), provider)
        self.assertEqual(text, "我脑子里还留着那盆多肉。")

    async def test_prompt_material_excludes_user_quote(self):
        provider = FakeProvider(text="这个我还记着。")
        await starmap.narrate_opening(self.record("memory"), provider)
        sent = provider.last_messages[-1]["content"]
        self.assertIn("用户最近又熬到三点", sent)
        self.assertNotIn("昨晚又打到三点，这周第三次了", sent)

    async def test_bad_output_falls_back_to_template(self):
        template = starmap._narrate_template("memory", self.record("memory"))
        for bad in ("", "   ", "他说了他的事，没有自称。", "我" * 200, None):
            with self.subTest(bad=bad):
                provider = FakeProvider(text=bad)
                text = await starmap.narrate_opening(self.record("memory"), provider)
                self.assertEqual(text, template[:120])

    async def test_provider_failure_falls_back_to_template(self):
        provider = FakeProvider(error=RuntimeError("provider down"))
        text = await starmap.narrate_opening(self.record("diary"), provider)
        self.assertIn("我", text)
        self.assertLessEqual(len(text), 120)
        self.assertIn("他又熬夜了", text)

    async def test_overlong_model_output_is_rejected(self):
        provider = FakeProvider(text="我" + "讲得太长了" * 30)
        text = await starmap.narrate_opening(self.record("spark"), provider)
        self.assertLessEqual(len(text), 120)
        self.assertIn("多肉", text)

    async def test_templates_for_every_kind_without_provider(self):
        for kind in ("memory", "diary", "spark", "taste", "worldview"):
            with self.subTest(kind=kind):
                text = await starmap.narrate_opening(self.record(kind))
                self.assertTrue(text)
                self.assertIn("我", text)
                self.assertLessEqual(len(text), 120)

    async def test_empty_record_returns_empty_string(self):
        for record in (None, {}, {"value": ""}, "nonsense", {"kind": "fact"}):
            with self.subTest(record=record):
                self.assertEqual(await starmap.narrate_opening(record), "")
                self.assertEqual(await starmap.narrate_opening(record, FakeProvider(text="我在。")), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
