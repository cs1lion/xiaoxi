# -*- coding: utf-8 -*-
"""爆炸半径视图回归：分档优先级 + 「只读、不跑门禁」两条硬约束。

**为什么需要它**：`run_gates.py --risk` 是给人核准 PR 用的只读视图。两个最容易
悄悄退化的地方：① `docs/PROTOCOL.md` 因为落在 `docs/` 下被当成「小角落」；
② 有人把它接进 `wanted` 计算，于是 `--risk` 顺手把 200 多条门禁全跑一遍。
这两条都要有常驻断言，而不是靠注释提醒。

**零环境依赖**：分类用纯函数断言；只读性用 mock 拦 `run_gate`，不真的跑 git
之外任何子进程、不写文件、不需要 8000/8010 服务。

Run: .venv/Scripts/python.exe -B scripts/test_risk_view.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import run_gates  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_role0_locked_files_are_stop(self) -> None:
        for path in ("docs/PROTOCOL.md", "AGENTS.md", "docs/AGENT_PLAYBOOK.md",
                     "docs/TASKS.md", "REVIEW.md"):
            self.assertEqual(run_gates.classify(path), "stop", path)

    def test_protocol_seams_and_executive_are_stop(self) -> None:
        for path in ("server/contract/events_v2.py", "server/broadcast/v2.py",
                     "server/assistant/executive.py"):
            self.assertEqual(run_gates.classify(path), "stop", path)

    def test_shared_layers_are_review(self) -> None:
        for path in ("server/app.py", "server/llm.py", "config/persona.yaml",
                     "web/app.js", ".github/workflows/ci.yaml",
                     "evals/checks.py", "scripts/check_protocol.py",
                     "scripts/run_gates.py", "package.json"):
            self.assertEqual(run_gates.classify(path), "review", path)

    def test_independent_corners_are_glance(self) -> None:
        for path in ("docs/HANDOFF_20261105.md", "docs/process/trail.jsonl",
                     "scripts/test_risk_view.py", "scripts/check_process_trail.py",
                     "pixel-world/dist/index.html", "README.md"):
            self.assertEqual(run_gates.classify(path), "glance", path)

    def test_locked_file_wins_over_docs_prefix(self) -> None:
        """优先级回归：不能因为 docs/ 是 glance 就把 PROTOCOL.md 一起放过去。"""
        self.assertEqual(run_gates.classify("docs/PROTOCOL.md"), "stop")
        self.assertEqual(run_gates.classify("docs/TESTING.md"), "glance")

    def test_windows_separator_is_normalised(self) -> None:
        sep = chr(92)
        self.assertEqual(run_gates.classify(f"server{sep}app.py"), "review")
        self.assertEqual(run_gates.classify(f"docs{sep}PROTOCOL.md"), "stop")

    def test_levels_are_exhaustive(self) -> None:
        for path in ("AGENTS.md", "server/app.py", "docs/x.md"):
            self.assertIn(run_gates.classify(path), run_gates.LEVELS)


class ReadOnlyTests(unittest.TestCase):
    def test_risk_view_never_runs_a_gate(self) -> None:
        with patch.object(run_gates, "run_gate") as gate, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_gates.risk_view(), 0)
        gate.assert_not_called()

    def test_main_risk_dispatches_before_wanted(self) -> None:
        """--risk 必须在门禁循环之前 return，否则只读视图会变成全量跑。"""
        with patch.object(sys, "argv", ["run_gates.py", "--risk"]), \
                patch.object(run_gates, "run_gate") as gate, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_gates.main(), 0)
        gate.assert_not_called()

    def test_normal_invocation_still_runs_gates(self) -> None:
        """别把分发改过头：--only 仍必须真的跑门禁。"""
        with patch.object(sys, "argv", ["run_gates.py", "--only", "process_trail"]), \
                patch.object(run_gates, "run_gate",
                             return_value={"name": "process_trail", "exit": 0,
                                           "skipped": False, "seconds": 0,
                                           "output_tail": ""}) as gate, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_gates.main(), 0)
        gate.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
