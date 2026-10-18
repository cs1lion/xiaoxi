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
        # V3 花名册也进了 MANAGED；临时目录里必须有一份，否则 install() 读不到文件。
        (self.dir / "agents_roster.yaml").write_text(
            "version: 2\nagents: []\nseed_rules: []\ndispatch: {}\n", encoding="utf-8")
        self._old_c, self._old_h = C.CONFIG_DIR, H.CONFIG_DIR
        C.CONFIG_DIR = self.dir
        H.CONFIG_DIR = self.dir
        for loader in (C._persona_base, C._memory_base, C._pacing_base, C._avatar_base,
                       C._agents_roster_base):
            loader.cache_clear()
        H.stop_watcher()
        H._snapshots.clear()
        H._mtimes.clear()
        H._installed = False

    def tearDown(self) -> None:
        H.stop_watcher()
        C.CONFIG_DIR, H.CONFIG_DIR = self._old_c, self._old_h
        for loader in (C._persona_base, C._memory_base, C._pacing_base, C._avatar_base,
                       C._agents_roster_base):
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

    def test_roster_hot_reloads_without_restart(self) -> None:
        """V3：改花名册不用重启服务，下次派单就生效（协议 §17）。"""
        H.install()
        self.assertEqual(C.agents_roster_cfg()["version"], 2)
        self.assertEqual(C.agents_roster_cfg()["agents"], [])
        H._local_path("agents_roster.yaml").write_text(
            "agents:\n  - id: hot_agent\n    adapter: cli_subproc\n    executable: echo\n",
            encoding="utf-8")
        self._bump("agents_roster.yaml")
        self.assertIn("agents_roster.yaml", H.refresh())
        roster = C.agents_roster_cfg()
        self.assertEqual([agent["id"] for agent in roster["agents"]], ["hot_agent"])  # live
        self.assertEqual(roster["agents"][0]["adapter"], "cli_subproc")
        self.assertEqual(roster["version"], 2)   # base key preserved by the merge

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
        self.assertEqual(C.agents_roster_cfg()["version"], 2)
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

    def test_orphan_override_is_named_not_silently_ignored(self) -> None:
        """MANAGED 之外的 `.local.yaml` 必须被点名，不能静默失效。

        协议 §17 的覆盖范围列了 `seeds.yaml` 与 `response_examples.json`，
        但两者既不在 MANAGED 里、读取方也绕开了 *_cfg()。用户认真写一份覆盖，
        线上毫无变化且无日志——这正是要告警的那类静默失败。

        断言走 `install()` 这条路，不是直接调辅助函数：直接调的话，把 install()
        里的接线删掉测试照样绿（第一版就这么写的，等于白钉）。
        """
        H._warned_orphans.clear()
        H.install()
        self.assertEqual(H.warn_orphan_overrides(), [],
                         "临时目录里只有 MANAGED 内的覆盖，不该有孤儿")

        orphan = self.dir / "seeds.local.yaml"
        orphan.write_text("seeds: []\n", encoding="utf-8")
        try:
            H._warned_orphans.clear()
            H.install()
            self.assertIn("seeds.local.yaml", H._warned_orphans,
                          "install() 必须点名孤儿覆盖，否则它静默失效")
            # 已报过的别再报：否则每个 tick 刷一遍日志
            self.assertEqual(H.warn_orphan_overrides(), [])
        finally:
            orphan.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
