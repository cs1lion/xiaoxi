"""V2 无痕热修改契约（PROTOCOL §17）。用临时 config 目录，绝不碰真实 config/。

Run: python -B scripts/test_config_hot.py
Covers: 无覆盖等价基础、本地覆盖合并与热切换、无效配置不回退、删除覆盖回退、
mtime 防抖。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import config as C
from server import config_hot as H


class ConfigHotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cfghot-"))
        (self.dir / "memory.yaml").write_text(
            "self_evolution:\n  enabled: true\n  idle_minutes: 60\n", encoding="utf-8")
        (self.dir / "persona.yaml").write_text("name: base\n", encoding="utf-8")
        (self.dir / "pacing.yaml").write_text("ambient:\n  mumble_seconds: 45\n", encoding="utf-8")
        (self.dir / "avatar.yaml").write_text("neutral: {}\n", encoding="utf-8")
        self._old_c, self._old_h = C.CONFIG_DIR, H.CONFIG_DIR
        C.CONFIG_DIR = self.dir
        H.CONFIG_DIR = self.dir
        for loader in (C._persona_base, C._memory_base, C._pacing_base, C._avatar_base):
            loader.cache_clear()
        H.stop_watcher()
        H._snapshots.clear()
        H._mtimes.clear()
        H._installed = False

    def tearDown(self) -> None:
        H.stop_watcher()
        C.CONFIG_DIR, H.CONFIG_DIR = self._old_c, self._old_h
        for loader in (C._persona_base, C._memory_base, C._pacing_base, C._avatar_base):
            loader.cache_clear()
        H._snapshots.clear()
        H._mtimes.clear()
        H._installed = False

    def _bump(self, name: str) -> None:
        path = H._local_path(name)
        if not path.exists():
            return
        future = time.time() + 5
        os.utime(path, (future, future))

    def test_no_overlay_returns_base(self) -> None:
        self.assertIsNone(H.snapshot("memory.yaml"))
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 60)

    def test_local_override_merges_and_hot_swaps_without_restart(self) -> None:
        H.install()
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 60)
        H._local_path("memory.yaml").write_text("self_evolution:\n  idle_minutes: 5\n", encoding="utf-8")
        self._bump("memory.yaml")
        self.assertIn("memory.yaml", H.refresh())
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 5)   # live, no restart
        self.assertTrue(C.memory_cfg()["self_evolution"]["enabled"])            # base key preserved

    def test_invalid_override_keeps_last_valid(self) -> None:
        H.install()
        lp = H._local_path("memory.yaml")
        lp.write_text("self_evolution:\n  idle_minutes: 7\n", encoding="utf-8")
        self._bump("memory.yaml")
        H.refresh()
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 7)
        lp.write_text("broken: : : ][ not yaml", encoding="utf-8")
        self._bump("memory.yaml")
        H.refresh()
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 7)   # previous kept

    def test_remove_override_reverts_to_base(self) -> None:
        H.install()
        lp = H._local_path("memory.yaml")
        lp.write_text("self_evolution:\n  idle_minutes: 9\n", encoding="utf-8")
        self._bump("memory.yaml")
        H.refresh()
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 9)
        lp.unlink()
        self._bump("memory.yaml")
        H.refresh()
        self.assertEqual(C.memory_cfg()["self_evolution"]["idle_minutes"], 60)  # back to base

    def test_debounce_skips_unchanged_file(self) -> None:
        H.install()
        H._local_path("memory.yaml").write_text("self_evolution:\n  idle_minutes: 3\n", encoding="utf-8")
        self._bump("memory.yaml")
        self.assertTrue(H.refresh())
        self.assertEqual(H.refresh(), [])  # unchanged mtime -> no recompute


if __name__ == "__main__":
    unittest.main(verbosity=2)
