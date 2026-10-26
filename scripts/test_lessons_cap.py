# -*- coding: utf-8 -*-
"""#59 回归：lessons 扫描边界来自真实行数，不是写死的 1000。

改动前 prune 与 retrieve_lessons 都写着 limit=1000。active 一旦超过 1000，
最老那批既不会被 cap 退役、也不会被检索命中，而且没有任何断言会响——这是
静默失效，不是报错。本测试用 1005 条真实数据钉住两件事：

- prune 看得见全部 active，cap 之外按分数退役，且收敛后 active 数等于 cap；
- retrieve_lessons 能命中最新 1000 条之外的 lesson（cue 命中路径）。

判据是行为：用超出旧边界的真实行数跑生产函数，不是断言「代码里写了什么」。

Run: .venv/Scripts/python.exe -B scripts/test_lessons_cap.py
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""

from server.memory import lessons  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402

CAP = 24
TOTAL = 1005          # 超过改动前写死的 1000：最老 5 条必须仍被看得见
TOP_SCORED = 5        # 最老的 5 条拿最高分，按分数退役就必须留住它们
CUE_QUERY = "今天关键词甲让我想起一件事"


def lesson_config(**overrides):
    cfg = dict(lessons.LESSON_DEFAULTS)
    cfg.update({"enabled": True, "max_active": CAP, "max_inject": 3,
                "min_similarity": 0.12, "dedupe_similarity": 0.72, "retire_harmful": 2})
    cfg.update(overrides)
    return cfg


class LessonsCapCase(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore(":memory:")
        self.addCleanup(self.store.close)
        patcher = patch("server.memory.lessons.lessons_cfg",
                        side_effect=lambda: lesson_config())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _seed(self, total: int, *, cue_on_oldest: bool = False) -> list[int]:
        """Oldest first: created_at/updated_at = index, so ids 1..total 由老到新。"""
        ids = []
        for index in range(1, total + 1):
            cues = ["关键词甲"] if (cue_on_oldest and index == 1) else None
            lesson_id = self.store.add_lesson(
                trigger=f"触发词{index}", action=f"做法{index}", cues=cues,
                now=float(index))
            self.assertIsNotNone(lesson_id)
            ids.append(int(lesson_id))
        return ids

    def test_prune_sees_every_active_lesson_and_converges_to_cap(self):
        ids = self._seed(TOTAL)
        for lesson_id in ids[:TOP_SCORED]:
            for _ in range(10):
                self.store.feedback_lesson(lesson_id, helpful=True, now=1.0)
        self.assertEqual(self.store.count_active_lessons(), TOTAL)

        retired = lessons.prune(self.store, cfg=lesson_config())

        # 旧边界下 prune 只看得见最新 1000 条：退役后 active = 24 + 5 条
        # 没被看见的老 lesson = 29，这个断言就是那条会响的警报。
        self.assertEqual(self.store.count_active_lessons(), CAP)
        self.assertGreater(retired, 0)
        # 按分数退役：最高分的最老 5 条必须活着，而不是被 id 新旧误伤。
        for lesson_id in ids[:TOP_SCORED]:
            self.assertEqual(self.store.get_lesson(lesson_id)["status"], "active")

    def test_retrieve_hits_a_lesson_beyond_the_oldest_1000(self):
        self._seed(TOTAL, cue_on_oldest=True)
        hits = lessons.retrieve_lessons(self.store, CUE_QUERY, cfg=lesson_config())
        # cue 命中加 0.65 权重，最老那条必须排第一；旧边界下它根本不在扫描集里，
        # 实测先红在 assertTrue（其余相似度都低于 min_similarity，hits 为空）。
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], 1)

    def test_prune_is_a_noop_below_the_cap(self):
        self._seed(CAP)
        self.assertEqual(lessons.prune(self.store, cfg=lesson_config()), 0)
        self.assertEqual(self.store.count_active_lessons(), CAP)


if __name__ == "__main__":
    unittest.main(verbosity=2)
