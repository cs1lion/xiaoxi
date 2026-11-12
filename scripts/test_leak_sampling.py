# -*- coding: utf-8 -*-
"""泄漏棘轮的抽样完整性（2026-11-12）。

**问题**：`_pick_suspects` 命中所有形态像沙盘的门禁后，**静默截断**到
`MAX_GATES = 10`。命中数实际是 12，被丢掉的两条是按字典序排在后面的
`ui_nav_browser` 与 `voice_browser` —— 而这两条恰恰是最可能漏沙盘的浏览器门禁
（浏览器夹具经常在失败路径留 profile 目录）。

**为什么危险**：截断是静默的。棘轮打印「抽样 10 个门禁全部无 tmp/ 净增」，
读起来像「泄漏检测通过」，实际有两条门禁**从没被检过**。而且每加一条字典序靠后的
沙盘门禁，被挤掉的就多一条 —— 这个洞会自己变大。

**修法**：不静默截断。要么全跑，要么明确报告「本次跳过了哪几条、为什么」。
这里选后者 + 提高上限：真正跑不完时必须**说出来**，且跳过的门禁名要打出来。

本文件只测抽样与报告逻辑，不真跑门禁。
"""
from __future__ import annotations

import importlib.util
import io
import re
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location(
    "glt", ROOT / "scripts" / "test_gate_tmp_leak.py")
glt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(glt)


def _gate(name: str) -> dict:
    return {"cmd": ["node", f"scripts/{name}.mjs"]}


class LeakSamplingTests(unittest.TestCase):
    def test_all_matching_gates_are_picked_not_silently_truncated(self):
        """命中的沙盘形态门禁必须**全部**进抽样，不得静默截断。

        2026-11-12 实测：命中 12 条，MAX_GATES=10 静默丢掉
        `ui_nav_browser` 与 `voice_browser`。
        """
        gates = {
            "a_browser": _gate("a_browser"),
            "b_drill": _gate("b_drill"),
            "c_e2e": _gate("c_e2e"),
            "d_shake": _gate("d_shake"),
            "e_step8": _gate("e_step8"),
            "f_browser": _gate("f_browser"),
            "g_drill": _gate("g_drill"),
            "h_e2e": _gate("h_e2e"),
            "i_shake": _gate("i_shake"),
            "j_step8": _gate("j_step8"),
            "k_browser": _gate("k_browser"),
            "voice_browser": _gate("voice_browser"),
        }
        picked = glt._pick_suspects(gates)
        names = [name for name, _ in picked]
        self.assertEqual(len(names), len(gates),
                         f"命中 {len(gates)} 条却只抽了 {len(names)} 条 —— 静默截断")
        for name in gates:
            self.assertIn(name, names)

    def test_skipped_gates_are_reported_by_name(self):
        """被跳过的**沙盘形态**门禁必须按名报出来，不能只报一个数字。"""
        gates = {f"g{i}": _gate(f"g{i}_browser") for i in range(12)}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            glt._report_sampling(gates, [("g0", _gate("g0"))])
        out = buffer.getvalue()
        self.assertIn("跳过", out)
        # 至少要能看到被跳过的门禁名
        self.assertTrue(any(f"g{i}" in out for i in range(1, 12)),
                        "被跳过的门禁名必须出现在报告里")

    def test_non_sandbox_gates_are_not_listed_as_skipped(self):
        """不产生沙盘的门禁**不该**出现在「跳过」名单里。

        门禁有两百多条，绝大多数不产沙盘。把它们全列出来是噪声，
        反而让人以为漏检了一大片。
        """
        gates = {
            "g0_browser": _gate("g0_browser"),
            "plain_a": {"cmd": ["python", "scripts/test_core.py"]},
            "plain_b": {"cmd": ["python", "scripts/test_core2.py"]},
        }
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            glt._report_sampling(gates, [("g0_browser", _gate("g0_browser"))])
        out = buffer.getvalue()
        self.assertNotIn("plain_a", out)
        self.assertNotIn("plain_b", out)

    def test_no_truncation_needed_when_few_gates(self):
        """命中数不超过上限时，不该出现「跳过」字样。"""
        gates = {"a_browser": _gate("a_browser"), "b_drill": _gate("b_drill")}
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            glt._report_sampling(gates, [(n, s) for n, s in gates.items()])
        out = buffer.getvalue()
        self.assertNotIn("跳过", out)

    def test_max_gates_covers_the_real_suspect_count(self):
        """上限必须够装下真实命中数（本机是 12），否则永远在截断。"""
        real = {
            "blocks_browser", "broadcast_e2e", "companion_browser", "drill_badkey",
            "drill_network", "drill_ws_reconnect", "experience_entry_browser",
            "intent_parser_e2e", "step8_e2e_browser", "ui_browser",
            "ui_nav_browser", "voice_browser",
        }
        gates = {name: _gate(name) for name in real}
        picked = glt._pick_suspects(gates)
        self.assertEqual(len(picked), len(real),
                         f"上限 {glt.MAX_GATES} 装不下真实命中数 {len(real)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
