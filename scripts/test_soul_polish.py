"""V2 灵魂收尾打磨契约：人格 Ledger/Pushback 注入、心境自然衰减、对话式偏好学习。

Run: python -B scripts/test_soul_polish.py
Covers: pushback_note 注入与空输入兼容、mood active->easing->resolved 衰减、
consider_viewpoint 防抖/增信/上限。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.persona import pushback_note, dynamic_prompt
from server.memory.diary import decay_mood
from server.memory.preference import consider_viewpoint
from server.memory.store import MemoryStore


class PushbackBlockTest(unittest.TestCase):
    def test_empty_returns_blank(self):
        self.assertEqual(pushback_note(None), "")
        self.assertEqual(pushback_note([]), "")
        self.assertEqual(pushback_note([{"topic": "", "stance": "x"}]), "")

    def test_block_surfaces_viewpoints_and_reasoned_disagreement(self):
        note = pushback_note([{"topic": "界面", "stance": "留白一点更好看"}])
        self.assertIn("留白", note)
        self.assertIn("理由", note)
        self.assertIn("不强行拉回执念", note)

    def test_dynamic_prompt_additive_and_byte_identical_when_empty(self):
        base = dynamic_prompt(memory_block="记忆X", bias_block="偏置Y")
        self.assertEqual(base, dynamic_prompt(memory_block="记忆X", bias_block="偏置Y", pushback=""))
        note = pushback_note([{"topic": "界面", "stance": "留白"}])
        self.assertIn("留白", dynamic_prompt(memory_block="记忆X", pushback=note))


class MoodDecayTest(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(str(Path(tempfile.mkdtemp(prefix="mooddecay-")) / "memory.db"))
        self.addCleanup(self.store.close)

    def test_active_to_easing_to_resolved(self):
        now = 1000.0
        self.store.save_mood(narrative="总觉得缺个环节", state="active", expires_at=now)
        self.assertEqual(decay_mood(self.store, now=now - 1)["state"], "active")  # not yet
        eased = decay_mood(self.store, now=now, easing_seconds=100.0)
        self.assertEqual(eased["state"], "easing")
        self.assertEqual(eased["narrative"], "总觉得缺个环节")  # narrative kept while easing
        self.assertEqual(decay_mood(self.store, now=now + 50)["state"], "easing")  # still easing
        resolved = decay_mood(self.store, now=now + 101)
        self.assertEqual(resolved["state"], "resolved")
        self.assertEqual(resolved["narrative"], "")  # faded out, no opposite flip

    def test_no_expiry_is_stable(self):
        self.store.save_mood(narrative="余温", state="active", expires_at=None)
        self.assertEqual(decay_mood(self.store, now=9e9)["state"], "active")


class ViewpointLearningTest(unittest.TestCase):
    def test_single_signal_is_debounced(self):
        ledger, action = consider_viewpoint([], topic="表达", stance="直接清楚更好", now=1, prior_signals=0)
        self.assertEqual(action, "ignored")
        self.assertEqual(ledger, [])

    def test_corroborated_signal_adds_candidate(self):
        ledger, action = consider_viewpoint([], topic="表达", stance="直接清楚更好", now=2, prior_signals=1)
        self.assertEqual(action, "added")
        self.assertEqual(ledger[0]["status"], "candidate")
        self.assertEqual(ledger[0]["support"], 2)

    def test_repeat_bumps_confidence_bounded(self):
        ledger, _ = consider_viewpoint([], topic="表达", stance="s", now=1, prior_signals=1)
        first = ledger[0]["confidence"]
        ledger2, action = consider_viewpoint(ledger, topic="表达", stance="s", now=2)
        self.assertEqual(action, "bumped")
        self.assertGreater(ledger2[0]["confidence"], first)
        capped = [{"topic": "表达", "stance": "s", "confidence": 0.89, "support": 9}]
        out, _ = consider_viewpoint(capped, topic="表达", stance="s", now=3)
        self.assertLessEqual(out[0]["confidence"], 0.9)

    def test_other_topics_are_separate(self):
        ledger, _ = consider_viewpoint([], topic="表达", stance="s", now=1, prior_signals=1)
        out, action = consider_viewpoint(ledger, topic="配色", stance="少即是多", now=2, prior_signals=1)
        self.assertEqual(action, "added")
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
