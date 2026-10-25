"""§17 覆盖必须真的到达读取方，且 app 生命周期必须装上 watcher。

`scripts/test_config_hot.py` 只验 config_hot 模块本身（临时 config 目录）。
2026-10-22 抓到的事故在它视线外：机制写得完整、那条测试也全绿，但生产代码
没有任何一处调用 `start_watcher()`，于是 `snapshot()` 恒为 None，
`config/*.local.yaml` 从头到尾没生效过，且没有日志。本文件补上这一层。

Run: python -B scripts/test_config_hot_consumers.py
Covers: startup/shutdown 装上 watcher、覆盖到达 retriever/manager/orchestrator、
热切换不用重启、显式覆盖仍能隔离单实例、REFUSE_KEYWORDS 不再冻结在 import 期。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 生产库哨兵（#39/#41）：`server.memory.store` 在模块底部建了 `store = MemoryStore()`
# 单例，import 即开库。必须写在下面任何 `from server...` 之前。
os.environ.setdefault('DB_PATH', ':memory:')

from server import config as C  # noqa: E402
from server import config_hot as H  # noqa: E402

APP_REL = "server/app.py"
ORCH_REL = "server/dialogue/orchestrator.py"


def _module(rel: str) -> ast.Module:
    return ast.parse((ROOT / rel).read_text(encoding="utf-8"), rel)


def _top_level_func(mod: ast.Module, rel: str, name: str) -> ast.FunctionDef:
    for node in mod.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{rel} has no top-level function {name!r}")


def _dotted_calls(node: ast.AST, dotted: str) -> list[ast.Call]:
    """Every `a.b(...)` call under node whose dotted name equals `dotted`."""
    found = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and f"{func.value.id}.{func.attr}" == dotted):
            found.append(child)
    return found


def _is_self_pacing(node: ast.AST) -> bool:
    """True for `self.pacing[...]`, including nested subscripts like
    `self.pacing["cooldown"]["on_refuse_keywords"]`."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return (isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self" and node.attr == "pacing")


class WiringTest(unittest.TestCase):
    """app 生命周期必须装上 watcher。

    走 AST 而不是跑 TestClient：startup() 会拉起 durable 执行内核、调度器和
    续跑，为一个配置断言付那个代价不值得；而 AST 断言同样能抓住"有人把接线删了"
    ——这正是本文件存在的理由。同理不直接调辅助函数：只断言 install() 被调用的
    话，把 startup 里的接线删掉测试照样绿。
    """

    def test_startup_installs_the_watcher(self) -> None:
        startup = _top_level_func(_module(APP_REL), APP_REL, "startup")
        self.assertTrue(_dotted_calls(startup, "config_hot.start_watcher"),
                        "server/app.py 的 startup() 必须调 config_hot.start_watcher()，"
                        "否则 config/*.local.yaml 静默失效")

    def test_shutdown_stops_the_watcher(self) -> None:
        shutdown = _top_level_func(_module(APP_REL), APP_REL, "shutdown")
        self.assertTrue(_dotted_calls(shutdown, "config_hot.stop_watcher"),
                        "server/app.py 的 shutdown() 必须调 config_hot.stop_watcher()，"
                        "否则测试进程里残留一个每 2 秒醒一次的轮询线程")

    def test_refuse_keywords_are_not_frozen_at_import(self) -> None:
        """拒识词表不得再是模块级常量（orchestrator.py:201 原先就是）。"""
        mod = _module(ORCH_REL)
        for node in mod.body:
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, ast.AnnAssign) else [])
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            self.assertNotIn("REFUSE_KEYWORDS", names,
                             "模块级 REFUSE_KEYWORDS 会在 import 期冻结 pacing 配置")
        for child in ast.walk(mod):
            self.assertFalse(isinstance(child, ast.Name) and child.id == "REFUSE_KEYWORDS",
                             "orchestrator.py 仍在读取 REFUSE_KEYWORDS 这个名字")
        loops = [n for n in ast.walk(mod) if isinstance(n, ast.For) and _is_self_pacing(n.iter)]
        self.assertTrue(loops, "拒识循环应改为 self.pacing[...] 现读")


class OverrideReachesConsumersTest(unittest.TestCase):
    """覆盖必须改变读取方看到的值。用临时 config 目录，绝不碰真实 config/。"""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="cfghotc-"))
        (self.dir / "memory.yaml").write_text(
            "retrieval:\n  top_k: 6\n  max_inject_chars: 600\n"
            "write:\n  max_infer_per_session: 8\n", encoding="utf-8")
        (self.dir / "pacing.yaml").write_text(
            "length:\n  hard_limit_chars: 120\n"
            "cooldown:\n  on_refuse_keywords: [base_word]\n"
            "  suppress_proactivity_seconds: 300\n", encoding="utf-8")
        (self.dir / "persona.yaml").write_text("name: base\n", encoding="utf-8")
        (self.dir / "avatar.yaml").write_text("neutral: {}\n", encoding="utf-8")
        (self.dir / "agents_roster.yaml").write_text(
            "version: 2\nagents: []\nseed_rules: []\ndispatch: {}\n", encoding="utf-8")
        (self.dir / "seeds.yaml").write_text("seeds: []\n", encoding="utf-8")
        # 覆盖文件：install() 会把它们合并进快照。少了这两份，测的就只是基础配置。
        (self.dir / "memory.local.yaml").write_text("retrieval:\n  top_k: 99\n", encoding="utf-8")
        (self.dir / "pacing.local.yaml").write_text(
            "length:\n  hard_limit_chars: 999\n", encoding="utf-8")
        self._old_c, self._old_h = C.CONFIG_DIR, H.CONFIG_DIR
        C.CONFIG_DIR, H.CONFIG_DIR = self.dir, self.dir
        self._reset()

    def tearDown(self) -> None:
        H.stop_watcher()
        C.CONFIG_DIR, H.CONFIG_DIR = self._old_c, self._old_h
        self._reset()

    def _reset(self) -> None:
        for loader in (C._persona_base, C._memory_base, C._pacing_base, C._avatar_base,
                       C._agents_roster_base, C._seeds_base):
            loader.cache_clear()
        H._snapshots.clear()
        H._mtimes.clear()
        H._installed = False

    def _rewrite_overlay(self, name: str, text: str) -> None:
        path = H._local_path(name)
        path.write_text(text, encoding="utf-8")
        future = time.time() + 5
        os.utime(path, (future, future))

    def test_consumers_track_the_overlay_after_install(self) -> None:
        """构造发生在 install 之前，读取仍必须看到覆盖。"""
        from server.dialogue.orchestrator import Orchestrator
        from server.memory.manager import MemoryManager
        from server.memory.retriever import MemoryRetriever

        retriever = MemoryRetriever()
        manager = MemoryManager()
        orch = Orchestrator()
        self.assertEqual(retriever.cfg["top_k"], 6, "install 前应看到基础值")

        H.install()

        self.assertEqual(retriever.cfg["top_k"], 99, "retriever 必须现读 memory_cfg()")
        self.assertEqual(manager.cfg["retrieval"]["top_k"], 99, "manager 必须现读 memory_cfg()")
        self.assertEqual(orch.pacing["length"]["hard_limit_chars"], 999,
                         "orchestrator 必须现读 pacing_cfg()")

    def test_hot_swap_reaches_consumers_without_restart(self) -> None:
        """进程运行中改覆盖，下一次读取就看到新值。"""
        from server.dialogue.orchestrator import Orchestrator

        H.install()
        orch = Orchestrator()
        self.assertEqual(orch.pacing["length"]["hard_limit_chars"], 999)

        self._rewrite_overlay("pacing.yaml",
                              "length:\n  hard_limit_chars: 555\n"
                              "cooldown:\n  on_refuse_keywords: [hot_word]\n"
                              "  suppress_proactivity_seconds: 300\n")
        self.assertIn("pacing.yaml", H.refresh())
        self.assertEqual(orch.pacing["length"]["hard_limit_chars"], 555,
                         "热切换后 orchestrator 必须立刻看到新值")
        self.assertEqual(orch.pacing["cooldown"]["on_refuse_keywords"], ["hot_word"],
                         "拒识词表必须随热切换更新")

    def test_explicit_override_still_isolates_one_instance(self) -> None:
        """16 个测试文件用 `self.cfg = dict(...)` 隔离配置，setter 必须继续有效。"""
        from server.memory.retriever import MemoryRetriever

        H.install()
        retriever = MemoryRetriever()
        isolated = dict(retriever.cfg)
        isolated["top_k"] = 1
        retriever.cfg = isolated
        self.assertEqual(retriever.cfg["top_k"], 1)

        other = MemoryRetriever()
        self.assertEqual(other.cfg["top_k"], 99, "覆盖一个实例不能影响另一个实例")

        retriever.cfg = None
        self.assertEqual(retriever.cfg["top_k"], 99, "清掉显式覆盖要回到现读")


if __name__ == "__main__":
    unittest.main(verbosity=2)
