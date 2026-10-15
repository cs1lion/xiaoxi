"""V2 双螺旋好奇心树契约（PROTOCOL §14）。只用临时库，不碰真实 data/。

Run: python -B scripts/test_curiosity_graph.py
Covers: 初始种子加载、子种子裂变可追溯、近义去重、生命周期可逆、反思处理、
跨来源 epiphany 边、心境持久化。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.memory.store import MemoryStore
from server.memory import seeds as S

from types import SimpleNamespace


class StarmapV2Test(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="starmapv2-")) / "memory.db"))
        self.manager = SimpleNamespace(enabled=True, store=self.store)

    def tearDown(self) -> None:
        self.store.close()

    def test_seed_and_reflection_nodes_and_persisted_edges(self):
        from server.memory import starmap
        S.bootstrap_seeds(self.store, now_iso="2026-10-14")
        self.store.add_diary(date="2026-10-13", title="改稿", body="凌晨又打开。", mood="调侃",
                             energy=0.4, topic_keys=["作息"], source_message_ids=[1])
        child = S.fork_child_seed(self.store, "seed_night_decisions", title="无干扰时间",
                                  question="不被打断时判断会怎么变？", reason="注意力中断")
        self.store.add_reflection(id="refl_1", seed_id="seed_night_decisions", narrative="想了很久",
                                  discovery="与被打断有关", child_seed_ids=[child["id"]])
        self.store.add_edge(source="seed:seed_night_decisions", target="diary:2026-10-13",
                            kind="epiphany", weight=0.7, reason="深夜决策与改稿日记共振")
        graph = starmap.starmap_graph(self.manager)
        kinds = {n["kind"] for n in graph["nodes"]}
        self.assertIn("seed", kinds)
        self.assertIn("reflection", kinds)
        seed_node = next(n for n in graph["nodes"] if n["id"] == "seed:seed_night_decisions")
        self.assertEqual(set(seed_node), {"id", "kind", "label", "date", "weight", "group", "ref_id"})
        self.assertEqual(seed_node["group"], "self")
        edge_kinds = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        self.assertIn(("seed:seed_night_decisions", f"seed:{child['id']}", "parent_child"), edge_kinds)
        self.assertIn(("seed:seed_night_decisions", "diary:2026-10-13", "epiphany"), edge_kinds)

    def test_node_detail_for_seed_and_reflection(self):
        from server.memory import starmap
        S.bootstrap_seeds(self.store, now_iso="2026-10-14")
        self.store.add_reflection(id="refl_x", seed_id="seed_night_decisions", narrative="n",
                                  discovery="d")
        self.assertEqual(starmap.starmap_node(self.manager, "seed:seed_night_decisions")["question"],
                         "观察人类为什么总喜欢在深夜做推翻白天的决定？")
        self.assertEqual(starmap.starmap_node(self.manager, "reflection:refl_x")["discovery"], "d")
        self.assertIsNone(starmap.starmap_node(self.manager, "seed:nope"))
        self.assertIsNone(starmap.starmap_node(self.manager, "reflection:nope"))

    def test_existing_nodes_unaffected_without_seeds(self):
        from server.memory import starmap
        # no seeds -> no seed/reflection nodes, persisted-edge merge is empty
        graph = starmap.starmap_graph(self.manager)
        self.assertNotIn("seed", {n["kind"] for n in graph["nodes"]})
        self.assertEqual(graph["edges"], [])


class CuriosityGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.mkdtemp(prefix="curiosity-")
        self.store = MemoryStore(str(Path(self._dir) / "memory.db"))

    def tearDown(self) -> None:
        self.store.close()

    def test_bootstrap_loads_five_seeds_and_is_idempotent(self) -> None:
        self.assertEqual(S.bootstrap_seeds(self.store), 5)
        self.assertEqual(self.store.count_seeds(), 5)
        self.assertEqual(S.bootstrap_seeds(self.store), 0)  # never overwrites
        self.assertEqual(self.store.count_seeds(), 5)
        seed = self.store.get_seed("seed_night_decisions")
        self.assertIsNotNone(seed)
        self.assertEqual(seed["status"], "active")
        self.assertIn("config:seeds.yaml", seed["source_refs"])

    def test_fork_is_traceable_to_parent_and_reason(self) -> None:
        S.bootstrap_seeds(self.store)
        child = S.fork_child_seed(
            self.store, "seed_night_decisions", title="无干扰时间",
            question="一个人在不被打断的时间里会如何重新评价原有方案？",
            reason="来自注意力中断的发现", source_refs=["seed:seed_night_decisions", "diary:2026-10-13"])
        self.assertIsNotNone(child)
        self.assertEqual(child["parent_seed_ids"], ["seed_night_decisions"])
        parent = self.store.get_seed("seed_night_decisions")
        self.assertIn(child["id"], parent["child_seed_ids"])
        edges = self.store.list_edges(kind="parent_child")
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["source"], "seed:seed_night_decisions")
        self.assertEqual(edges[0]["target"], f"seed:{child['id']}")
        self.assertEqual(edges[0]["reason"], "来自注意力中断的发现")

    def test_near_duplicate_fork_is_refused(self) -> None:
        S.bootstrap_seeds(self.store)
        q = "一个人在不被打断的时间里会如何重新评价原有方案？"
        first = S.fork_child_seed(self.store, "seed_night_decisions", title="A", question=q, reason="r")
        self.assertIsNotNone(first)
        again = S.fork_child_seed(self.store, "seed_night_decisions", title="B", question=q, reason="r")
        self.assertIsNone(again)  # no swarm of restatements

    def test_lifecycle_is_reversible_and_guarded(self) -> None:
        S.bootstrap_seeds(self.store)
        self.assertEqual(S.transition_seed(self.store, "seed_night_decisions", "digested")["status"], "digested")
        self.assertEqual(S.transition_seed(self.store, "seed_night_decisions", "active")["status"], "active")
        S.transition_seed(self.store, "seed_night_decisions", "archived")
        self.assertIsNone(S.transition_seed(self.store, "seed_night_decisions", "digested"))  # illegal
        self.assertIsNotNone(S.transition_seed(self.store, "seed_night_decisions", "active"))  # reactivate

    def test_reflection_processing_flag(self) -> None:
        S.bootstrap_seeds(self.store)
        self.store.add_reflection(id="refl_1", seed_id="seed_night_decisions", narrative="想很久",
                                  discovery="与被打断有关", child_seed_ids=[])
        self.assertFalse(self.store.get_reflection("refl_1")["processed"])
        self.assertTrue(self.store.set_reflection_processed("refl_1"))
        self.assertTrue(self.store.get_reflection("refl_1")["processed"])

    def test_cross_source_epiphany_edge_persists_with_reason(self) -> None:
        S.bootstrap_seeds(self.store)
        # A seed + a diary + a user note converge on one insight (protocol §14.2).
        added = self.store.add_edge(source="diary:2026-10-13", target="note:n1", kind="epiphany",
                                    weight=0.7, reason="日记的完美主义与笔记的注意力中断共同指向判断变化")
        self.assertTrue(added)
        self.store.add_edge(source="seed:seed_night_decisions", target="diary:2026-10-13",
                            kind="related_memory", weight=0.6, reason="种子引用该日记")
        self.assertFalse(self.store.add_edge(source="diary:2026-10-13", target="note:n1",
                                             kind="epiphany", reason="dup"))  # deduped
        edges = self.store.list_edges(kind="epiphany")
        self.assertEqual(len(edges), 1)
        self.assertIn("注意力中断", edges[0]["reason"])

    def test_legacy_migration_lifts_curiosity_seeds(self) -> None:
        self.store.save_self_state(taste=[], worldview=[],
                                   curiosity_seeds=[{"text": "旧问题甲", "heat": 0.5},
                                                    {"text": "旧问题乙", "heat": 0.7}])
        self.assertEqual(S.migrate_legacy_seeds(self.store), 2)
        self.assertEqual(self.store.count_seeds(), 2)
        self.assertEqual(S.migrate_legacy_seeds(self.store), 0)  # only once


if __name__ == "__main__":
    unittest.main(verbosity=2)
