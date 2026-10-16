"""V2 三梯度编排契约（PROTOCOL §16）：orchestrator.ambient_event 集成。只用临时库。

Run: python -B scripts/test_ambient_orchestration.py
Covers: micro_act/mumble 分级产出、真实内容来源、冷启动破冰兜底、去重、
指数退避与周期重置、梯度3 陪伴通道接管（无静音断层）、授权门、真声字段。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.dialogue import orchestrator as O
from server.memory.store import MemoryStore


class AmbientOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="amborch-")) / "memory.db"))
        self._orig_manager = O.manager
        O.manager = SimpleNamespace(enabled=True, store=self.store)
        orch = O.orchestrator
        orch.state["proactivity_enabled"] = True
        orch.state["proactive_suppressed_until"] = 0.0
        orch.state["last_ambient_tier"] = None
        orch.state["last_ambient_at"] = 0.0
        orch.state["ambient_seq"] = 0
        orch.state["voice_cycle"] = 0

    def tearDown(self):
        O.manager = self._orig_manager
        self.store.close()

    def _reset_dedup(self):
        orch = O.orchestrator
        orch.state["last_ambient_tier"] = None
        orch.state["last_ambient_at"] = 0.0
        orch.state["voice_cycle"] = 0

    def test_micro_act_tier(self):
        event = O.orchestrator.ambient_event(idle_seconds=25, now=1000.0)
        self.assertEqual(event["type"], "micro_act")
        self.assertIn(event["action"], O._MICRO_ACTIONS)

    def test_mumble_uses_real_reflection_content(self):
        self.store.add_reflection(id="refl_1", seed_id="seed_a", narrative="n",
                                  discovery="他深夜改稿可能和白天被打断有关", source_refs=["diary:d"])
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=50, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertIn("被打断", event["text"])
        self.assertIn("reflection:refl_1", event["source_refs"])

    def test_mumble_falls_back_to_mood(self):
        self.store.save_mood(narrative="总觉得还缺一个关键环节", reason="逻辑卡点", state="active")
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=50, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertIn("关键环节", event["text"])

    def test_voiced_mumble_carries_voice_and_bubble(self):
        self.store.save_mood(narrative="余温", state="active")
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=50, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertTrue(event["voice"], "打磨轮主动搭话默认走真声链路（mumble_voice: true）")
        self.assertEqual(event["bubble"]["text"], event["text"])

    def test_first_voiced_threshold_is_25_to_30s(self):
        self._reset_dedup()
        # 28s 之前只有无声微动作，不会有声搭话。
        early = O.orchestrator.ambient_event(idle_seconds=27.9, now=1000.0)
        self.assertEqual(early["type"], "micro_act")
        event = O.orchestrator.ambient_event(idle_seconds=28, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertTrue(event["voice"])

    def test_cold_start_icebreaker_uses_real_samples(self):
        # 空库：没有 reflection / mood / spark，也必须开口，而不是装哑巴。
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=28, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertTrue(event["voice"])
        self.assertTrue(event["text"].strip())
        self.assertIn("config:response_examples.json", event["source_refs"],
                      "破冰语料必须真实可溯，不能声称完成过反思")
        self.assertTrue(event["trigger_reason"].startswith("icebreaker_"))

    def test_cold_start_icebreaker_prefers_real_todo(self):
        todo_id = self.store.upsert(kind="todo", value="把周报改成三句话", key="todo:weekly")
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=28, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertIn("周报", event["text"])
        self.assertIn(f"memory:{todo_id}", event["source_refs"])

    def test_voice_backoff_then_cycle_reset(self):
        self.store.save_mood(narrative="余温", state="active")
        self._reset_dedup()
        first = O.orchestrator.ambient_event(idle_seconds=28, now=1000.0)
        self.assertEqual(first["type"], "mumble")
        # 同一空闲周期：退避后阈值 56s，idle 40s 只够 micro_act，且被去重挡住。
        self.assertIsNone(O.orchestrator.ambient_event(idle_seconds=40, now=1010.0))
        # 越过退避阈值但仍处于去重窗口内，不刷屏。
        self.assertIsNone(O.orchestrator.ambient_event(idle_seconds=60, now=1015.0))
        # 用户一有动作（空闲回落到 5s 以下）：退避计数清零，去重窗口也已过去。
        self.assertIsNone(O.orchestrator.ambient_event(idle_seconds=2, now=1500.0))
        again = O.orchestrator.ambient_event(idle_seconds=28, now=1501.0)
        self.assertEqual(again["type"], "mumble", "新空闲周期里安静半分钟 again 被搭理")

    def test_proactive_tier_stays_voiced_companion(self):
        # 梯度3（90s+）不再留给 900s 门的重通道独占：陪伴通道接管，消除静音断层。
        self._reset_dedup()
        event = O.orchestrator.ambient_event(idle_seconds=200, now=1000.0)
        self.assertEqual(event["type"], "mumble")
        self.assertTrue(event["voice"])

    def test_dedup_blocks_repeat_within_window(self):
        self.store.save_mood(narrative="余温", state="active")
        self._reset_dedup()
        first = O.orchestrator.ambient_event(idle_seconds=50, now=1000.0)
        self.assertEqual(first["type"], "mumble")
        again = O.orchestrator.ambient_event(idle_seconds=55, now=1010.0)  # same tier, in window
        self.assertIsNone(again)

    def test_escalation_micro_to_mumble_allowed(self):
        self.store.save_mood(narrative="余温", state="active")
        self._reset_dedup()
        micro = O.orchestrator.ambient_event(idle_seconds=25, now=1000.0)
        self.assertEqual(micro["type"], "micro_act")
        mumble = O.orchestrator.ambient_event(idle_seconds=50, now=1010.0)  # escalate
        self.assertEqual(mumble["type"], "mumble")

    def test_authorization_gate(self):
        O.orchestrator.state["proactivity_enabled"] = False
        self._reset_dedup()
        self.assertIsNone(O.orchestrator.ambient_event(idle_seconds=50, now=1000.0))

    def test_disabled_stays_silent(self):
        from unittest.mock import patch
        from server.dialogue import ambient as A
        with patch.object(A, "ambient_cfg", return_value={**A.AMBIENT_DEFAULTS, "enabled": False}):
            self._reset_dedup()
            self.assertIsNone(O.orchestrator.ambient_event(idle_seconds=50, now=1000.0))


class TouchSampleTest(unittest.TestCase):
    def setUp(self):
        O._TOUCH_RECENT.clear()
        self.addCleanup(O._TOUCH_RECENT.clear)

    def test_picks_real_samples_and_rotates(self):
        head1 = O.touch_sample_line("head")
        head2 = O.touch_sample_line("head")
        self.assertTrue(head1 and head2, "returns a real touch_head sample")
        self.assertNotEqual(head1, head2, "rotates to avoid immediate repeats")
        self.assertTrue(O.touch_sample_line("face"))

    def test_invalid_region_and_missing_are_silent(self):
        self.assertEqual(O.touch_sample_line("hand"), "")
        self.assertEqual(O.touch_sample_line("elbow"), "")
        self.assertEqual(O.touch_sample_line(""), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
