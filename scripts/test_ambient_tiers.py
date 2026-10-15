"""V2 三梯度主动在场契约（PROTOCOL §16）。纯逻辑，无 IO。

Run: python -B scripts/test_ambient_tiers.py
Covers: 20/45/90 分级、安静模式、禁用、事件结构、真实内容优先与空内容不编造。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.dialogue import ambient as A


class SelectTierTest(unittest.TestCase):
    CFG = {"enabled": True, "micro_act_seconds": 20, "mumble_seconds": 45, "proactive_seconds": 90}

    def test_threshold_boundaries(self):
        self.assertIsNone(A.select_tier(19.9, self.CFG))
        self.assertEqual(A.select_tier(20, self.CFG), "micro_act")
        self.assertEqual(A.select_tier(44.9, self.CFG), "micro_act")
        self.assertEqual(A.select_tier(45, self.CFG), "mumble")
        self.assertEqual(A.select_tier(89.9, self.CFG), "mumble")
        self.assertEqual(A.select_tier(90, self.CFG), "proactive")
        self.assertEqual(A.select_tier(300, self.CFG), "proactive")

    def test_quiet_mode_keeps_only_micro_act(self):
        self.assertEqual(A.select_tier(50, self.CFG, quiet_mode=True), "micro_act")
        self.assertEqual(A.select_tier(200, self.CFG, quiet_mode=True), "micro_act")
        self.assertEqual(A.select_tier(25, self.CFG, quiet_mode=True), "micro_act")
        self.assertIsNone(A.select_tier(10, self.CFG, quiet_mode=True))

    def test_disabled_and_bad_input(self):
        self.assertIsNone(A.select_tier(999, {"enabled": False}))
        self.assertIsNone(A.select_tier(None, self.CFG))
        self.assertIsNone(A.select_tier("x", self.CFG))


class EventShapeTest(unittest.TestCase):
    def test_micro_act_shape(self):
        e = A.build_micro_act(action="glance_away", reason="idle_20s", now=1000.0, event_id="m1")
        self.assertEqual(e["type"], "micro_act")
        self.assertEqual(set(e), {"type", "event_id", "action", "trigger_reason", "triggered_at",
                                  "source_refs", "expires_at"})
        self.assertEqual(e["event_id"], "m1")
        self.assertGreater(e["expires_at"], e["triggered_at"])

    def test_mumble_shape_and_bubble(self):
        e = A.build_mumble(text="这行是不是能更干净点……", reason="idle_45s", voice=True,
                           bubble="这行是不是能更干净点……", source_refs=["seed:s1"], now=1.0)
        self.assertEqual(e["type"], "mumble")
        self.assertTrue(e["voice"])
        self.assertTrue(e["interactive"])
        self.assertIn("bubble", e)
        self.assertEqual(e["bubble"]["text"], "这行是不是能更干净点……")
        self.assertEqual(e["source_refs"], ["seed:s1"])

    def test_proactive_carries_source_refs(self):
        e = A.build_proactive(text="我昨晚把那个问题重顺了一遍", reason="idle_90s",
                              source_refs=["reflection:r1", "seed:s1"], now=1.0)
        self.assertEqual(e["type"], "proactive")
        self.assertEqual(e["source_refs"], ["reflection:r1", "seed:s1"])


class ChooseContentTest(unittest.TestCase):
    def test_prefers_discovery_then_mood_then_spark(self):
        text, refs = A.choose_content(discovery={"text": "发现X", "source_refs": ["diary:d"]},
                                      mood={"narrative": "余温Y"}, spark={"text": "火花Z"})
        self.assertEqual((text, refs), ("发现X", ["diary:d"]))
        text, refs = A.choose_content(mood={"narrative": "余温Y", "source_refs": ["seed:s"]},
                                      spark={"text": "火花Z"})
        self.assertEqual((text, refs), ("余温Y", ["seed:s"]))
        text, refs = A.choose_content(spark={"text": "火花Z"})
        self.assertEqual(text, "火花Z")

    def test_empty_content_returns_none(self):
        self.assertEqual(A.choose_content(), (None, []))
        self.assertEqual(A.choose_content(mood={"narrative": "  "}), (None, []))
        self.assertEqual(A.choose_content(discovery={"text": ""}, mood=None, spark=None, child_seed=None), (None, []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
