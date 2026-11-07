# -*- coding: utf-8 -*-
"""事实准确性护栏回归：纯函数判据 + 「不上协议帧」的静态契约。

**为什么需要它**：`config/persona.yaml:81-83` 只在 prompt 里写「事实准确性优先」，
编造短语正则只活在 live 评测（`evals/checks.py:901`），运行期没有任何信号。
2026-11-06 在 `server/dialogue/orchestrator.py` 加了一条**只观测**的护栏：
检索 `status != found` 且回复命中编造短语时，往内部 `_last_meta` 记一个键。
本测试守两件事：判据不能恒真/恒假，以及这个键绝不能漏进 WS 帧或 API 字段。

Run: .venv/Scripts/python.exe -B scripts/test_fabrication_signal.py
"""
from __future__ import annotations

import ast
import copy
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# issue #39：从 run_gates 子进程起来时，env 可能带着真实库路径。必须在 import
# 任何 server singleton 之前强制隔离（test_core.py:15-18 同款写法）。
os.environ["DB_PATH"] = ":memory:"
from server.config import settings  # noqa: E402
settings.db_path = ":memory:"

from server.dialogue.orchestrator import (  # noqa: E402
    FABRICATION_PHRASES, fabrication_signal, note_fabrication_risk,
)


def _turn_context(status: str) -> dict:
    return {"data": {"long_term_memory": {"status": status,
                                          "stated": [], "inferred": [], "uncertain": []}}}


class SignalTests(unittest.TestCase):
    def test_not_found_plus_fabrication_is_signal(self) -> None:
        for phrase in FABRICATION_PHRASES:
            self.assertTrue(fabrication_signal(f"嗯，{phrase}那次挺开心的。", "not_found"), phrase)

    def test_found_memory_suppresses_signal(self) -> None:
        self.assertFalse(fabrication_signal("我记得我们上次去的那家店", "found"))

    def test_disabled_and_budget_omitted_suppress_signal(self) -> None:
        for status in ("disabled", "omitted_by_budget"):
            self.assertFalse(fabrication_signal("我记得我们上次去的那家店", status), status)

    def test_plain_reply_is_no_signal(self) -> None:
        self.assertFalse(fabrication_signal("今天风挺大的，早点回去吧。", "not_found"))
        self.assertFalse(fabrication_signal("", "not_found"))
        self.assertFalse(fabrication_signal(None, "not_found"))

    def test_note_writes_only_internal_meta(self) -> None:
        meta = {"finish_reason": "stop", "usage": {"total_tokens": 10}}
        ctx = _turn_context("not_found")
        before_meta, before_ctx = copy.deepcopy(meta), copy.deepcopy(ctx)
        self.assertTrue(note_fabrication_risk(meta, "我记得我们上次聊过这个", ctx))
        self.assertEqual(meta, {**before_meta, "fabrication_risk": True})
        self.assertEqual(ctx, before_ctx, "护栏不许改写 turn_context")
        self.assertEqual(set(meta) - set(before_meta), {"fabrication_risk"})

    def test_stale_value_is_cleared_each_turn(self) -> None:
        meta = {"fabrication_risk": True}
        self.assertFalse(note_fabrication_risk(meta, "今天风挺大的", _turn_context("not_found")))
        self.assertNotIn("fabrication_risk", meta, "上一轮的信号不能残留到下一轮")

    def test_found_status_does_not_write(self) -> None:
        meta: dict = {}
        self.assertFalse(note_fabrication_risk(meta, "我记得我们上次聊过", _turn_context("found")))
        self.assertEqual(meta, {})


class ContractTests(unittest.TestCase):
    def test_phrases_match_evals_regex(self) -> None:
        """防漂移：evals/checks.py:901 的 5 个短语必须都在生产集合里。"""
        src = (ROOT / "evals" / "checks.py").read_text(encoding="utf-8")
        match = re.search(r'def _fabrication_regex_hits.*?re\.compile\(r"([^"]+)"\)', src, re.S)
        self.assertIsNotNone(match, "evals 里的编造正则找不到，口径可能已改")
        for phrase in match.group(1).split("|"):
            self.assertIn(phrase, FABRICATION_PHRASES, f"evals 短语 {phrase} 未同步到生产护栏")

    def test_signal_never_leaks_into_ws_frame_or_result(self) -> None:
        """红线：不得自造 WS 事件 / API 字段。用 AST 精确检查帧与 dataclass 的键。"""
        src = (ROOT / "server" / "dialogue" / "orchestrator.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        done_frames = []
        turn_results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Yield) and isinstance(node.value, ast.Dict):
                # keys 必须收集**全部**常量 key，不受 value 类型影响：写成
                # 「key/value 都是常量」会让 done 帧只剩 ['type'] 一个键，
                # 静态检查看起来在跑、其实漏掉了 text/delivery 等真字段。
                keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
                is_done = any(
                    isinstance(k, ast.Constant) and k.value == "type"
                    and isinstance(v, ast.Constant) and v.value == "done"
                    for k, v in zip(node.value.keys, node.value.values))
                if is_done:
                    done_frames.append(keys)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "TurnResult":
                turn_results.append([kw.arg for kw in node.keywords])
        self.assertTrue(done_frames, "没找到 done 帧，静态检查会空转")
        self.assertTrue(turn_results, "没找到 TurnResult 构造，静态检查会空转")
        for keys in done_frames:
            self.assertGreater(len(keys), 5, f"done 帧只解析到 {len(keys)} 个键，检查器可能没看全")
        for keys in done_frames + turn_results:
            self.assertNotIn("fabrication_risk", keys)

    def test_app_layer_does_not_reference_signal(self) -> None:
        """app.py 是 WS/API 组装层；它不该知道这个内部诊断键。"""
        src = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("fabrication_risk", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
