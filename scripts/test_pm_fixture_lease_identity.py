"""PM 评测夹具的 lease 必须以「路径」为键，不能以调用方自报的 label 为键。

回归 2026-11-17 CI 红（PR #65 run 44003970649）：
`pm_induction_fixture.install()` 用 label='cases-induction' 往
`eval/research/procedural_memory_eval_cases.json` 落盘，而
`pm_eval_fixture.ensure_cases()` 用的是 label='cases'。清理时「还有没有别人在用」
只看自己那个 label 前缀的 lease 文件，于是**创建者**退出时看不到另一种 label 的
lease，把仍在被读取的夹具删掉。并发分片（ci_test_shard --workers 4）下分片重排
改变进程退出顺序，`test_procedural_memory_diagnostics.py` 随后就撞上
`SnapshotError`（`source_file_unavailable:eval/research/procedural_memory_eval_cases.json`）
与 plan 模式的 `FileNotFoundError`。

判据：两个进程对同一路径持 lease，任何一个先退出都不能让文件消失。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pm_eval_fixture

# 子进程：用真实的 install() 创建夹具（label='cases-induction'），等父进程就位后退出。
CHILD = r'''
import sys, time, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pm_eval_fixture
import pm_induction_fixture

pm_eval_fixture.CASE_PATH = Path(sys.argv[2])
module = types.ModuleType('lease_identity_target')
module.load_sources = lambda: {'source_bundles': []}
pm_induction_fixture.install(module)
Path(sys.argv[3]).write_text('ready', encoding='utf-8')
deadline = time.monotonic() + 60
while not Path(sys.argv[4]).exists():
    if time.monotonic() > deadline:
        raise SystemExit('parent never signalled')
    time.sleep(0.02)
'''


class FixtureLeaseIdentityTests(unittest.TestCase):
    def test_creator_cleanup_keeps_file_while_another_caller_holds_a_lease(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            fixture = base / 'procedural_memory_eval_cases.json'
            ready, go = base / 'ready', base / 'go'
            child = subprocess.Popen(
                [sys.executable, '-X', 'utf8', '-B', '-c', CHILD,
                 str(Path(pm_eval_fixture.__file__).resolve().parent), str(fixture),
                 str(ready), str(go)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8')
            try:
                self.assertTrue(self._wait_for(ready), '子进程没能落盘夹具')
                self.assertTrue(fixture.exists())
                # 父进程以另一个 label 持同一路径的 lease（真实调用方：
                # pm_eval_fixture.ensure_cases → label='cases'）。
                self.assertTrue(pm_eval_fixture.ensure_file(
                    fixture, pm_eval_fixture.build_sources(), pm_eval_fixture.cases_valid))
                go.write_text('go', encoding='utf-8')
                stdout, stderr = child.communicate(timeout=120)
                self.assertEqual(child.returncode, 0, (stdout or '') + (stderr or ''))
                # 创建者已退出并跑过 atexit 清理：另一个进程的 lease 必须被看见。
                self.assertTrue(fixture.exists(),
                                '创建者清理时删掉了另一个进程仍在读的夹具')
            finally:
                if child.poll() is None:
                    child.kill()
                    child.communicate()

    @staticmethod
    def _wait_for(path, timeout=60.0):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.exists():
                return True
            time.sleep(0.02)
        return False


if __name__ == '__main__':
    unittest.main(verbosity=2)
