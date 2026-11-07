# -*- coding: utf-8 -*-
"""文档 vs 代码漂移门禁（2026-11-07）。

**为什么需要它**：2026-11-05~11-07 连续多轮改造后，`docs/ROADMAP.md:11` 还写着
「`/` 归世界」（语义完全反了）、`ci.yaml` 头部还写「逐脚本运行」且注释里留着
过时的 72 条、`TESTING.md` 仍说「`ci.yaml` 的 glob 会真跑它」。这类漂移靠人眼
回查才被发现，本门禁把它变成 CI 断言。

判据分三类：
1. **旧口径不得复活**：几个明确作废的字符串出现即红。
2. **新口径必须在**：入口、并发分片、快照锚、复核数字。
3. **可机读的锚必须与代码一致**：从 TESTING.md 解析 `` `函数名` `:行号` ``，
   用 AST 取 `server/app.py` 的真实函数定义行逐条比对；用例数也与
   `evals/cases/*.yaml` 实际数量比对。两边一起改会自动跟随，不写死数字。

Run: .venv/Scripts/python.exe -B scripts/test_doc_drift.py
"""
from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ROADMAP = ROOT / "docs" / "ROADMAP.md"
TESTING = ROOT / "docs" / "TESTING.md"
CI = ROOT / ".github" / "workflows" / "ci.yaml"


class OldWordingTests(unittest.TestCase):
    def test_obsolete_entry_wording_is_gone(self) -> None:
        text = ROADMAP.read_text(encoding="utf-8-sig")
        for stale in ("`/chat` 留旧聊天页", "全量保留、不再演进",
                      "server/app.py:1498-1517", "索引重建目前在快环路径内"):
            self.assertNotIn(stale, text, f"ROADMAP 旧口径复活：{stale}")
        # 只拦「作为现状陈述」的入口口径，不拦「旧口径已作废」这类复述——
        # 纯字符串包含会把注明作废的句子也判红（2026-11-07 首跑实测）。
        self.assertIsNone(re.search(r"^-\s*入口形态：.*归世界", text, re.M),
                          "ROADMAP 仍把 / 写成世界页")

    def test_obsolete_ci_wording_is_gone(self) -> None:
        text = CI.read_text(encoding="utf-8-sig")
        for stale in ("逐脚本运行", "那一档只有 72 条", "会砍掉 100 个回归"):
            self.assertNotIn(stale, text, f"ci.yaml 旧口径复活：{stale}")

    def test_obsolete_testing_wording_is_gone(self) -> None:
        text = TESTING.read_text(encoding="utf-8-sig")
        for stale in ("`ci.yaml` 的 `scripts/test_*.py` 循环会真跑它",
                      "只在 `ci.yaml` 的 `scripts/test_*.py` glob 中",
                      "该文件是 16 个用例中唯一缺"):
            self.assertNotIn(stale, text, f"TESTING 旧口径复活：{stale}")


class CurrentWordingTests(unittest.TestCase):
    def test_current_entry_and_split_ci_are_documented(self) -> None:
        roadmap = ROADMAP.read_text(encoding="utf-8-sig")
        self.assertIn("`/` 是语音陪伴页", roadmap)
        self.assertIn("server/app.py:1676", roadmap)
        ci = CI.read_text(encoding="utf-8-sig")
        self.assertIn("ci_test_shard.py --workers 4", ci)
        self.assertIn("--self-test", ci)

    def test_snapshot_anchor_and_recheck_numbers_present(self) -> None:
        text = TESTING.read_text(encoding="utf-8-sig")
        self.assertIn("2026-11-07 口径", text)
        self.assertIn("快照原文保留", text)
        self.assertIn("2026-11-07 复核当前", text)

    def test_case_count_matches_disk(self) -> None:
        """文档里的复核数字必须与 evals/cases/ 实际数量一致。"""
        cases = sorted((ROOT / "evals" / "cases").glob("*.yaml"))
        layers = [re.search(r"^layer:\s*(\w+)", p.read_text(encoding="utf-8-sig"), re.M)
                  for p in cases]
        offline = sum(1 for m in layers if m and m.group(1) == "offline")
        live = sum(1 for m in layers if m and m.group(1) == "live")
        self.assertGreater(len(cases), 0, "用例目录空了，统计会真空绿")
        expected = f"当前 {len(cases)} 个：{live} live + {offline} offline"
        self.assertIn(expected, TESTING.read_text(encoding="utf-8-sig"),
                      f"TESTING.md 用例数漂移，应为「{expected}」")


class AnchorTests(unittest.TestCase):
    def test_function_line_anchors_match_ast(self) -> None:
        """文档写的 `函数名` `:行号` 必须真的指向 app.py 里同名函数。"""
        text = TESTING.read_text(encoding="utf-8-sig")
        pairs = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)` `:(\d+)`", text)
        self.assertGreaterEqual(len(pairs), 4, "快照锚太少，检查器可能没解析到")
        tree = ast.parse((ROOT / "server" / "app.py").read_text(encoding="utf-8-sig"))
        positions = {node.name: node.lineno for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name, line in pairs:
            self.assertIn(name, positions, f"app.py 里已无函数 {name}")
            self.assertEqual(positions[name], int(line),
                             f"TESTING.md 锚 {name}:{line} 与 app.py 实际 {positions[name]} 不符")


if __name__ == "__main__":
    unittest.main(verbosity=2)
