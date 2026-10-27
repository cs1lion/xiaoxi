"""R50：intent_corpus 门禁的空转场景 fail-closed 回归测试。

`measure_intent_corpus.main()` 的门禁段曾有一个空转缺陷：真实语料是既定断言的
权威输入，但它缺失时地板线与差距断言会双双跳过、exit 0，还打印误导的
「地板线仍生效」——门禁空转还撒谎（R50 修）。本测试钉住修复后的各分支：
真实语料缺失必须 exit 1；地板线/差距/仅真实存在等分支行为不变。

零网络、零 Key、不跑真实分类——patch 掉 measure 喂罐头准确率，patch 掉
AssistantService/Orchestrator 使其不真正构造。分类逻辑 route_of 是产品判据，
不在本测试范围。
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent))

# 强制置位（不用 setdefault）：即使后续 server.config 加载 .env，load_dotenv
# 默认 override=False 也不会盖掉它们，保证测试进程连的是 :memory:、mock provider。
os.environ["DB_PATH"] = ":memory:"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["LLM_API_KEY"] = ""

import measure_intent_corpus as mic  # noqa: E402


class IntentCorpusGateTests(unittest.TestCase):
    """只测 main() 的门禁分支；用罐头准确率驱走真实分类。"""

    def _run_main(self, *, self_acc=None, real_acc=None):
        """隔离依赖跑 main()，返回 (exit_code, stdout)。

        self_acc/real_acc 为 None 表示对应语料「缺失」（指向不存在的路径，
        main() 的 path.exists() 会跳过它）。用文件内容标记 SELF/REAL 区分两份
        语料，fake_measure 据此返回对应罐头准确率——比依赖调用顺序更稳。
        """
        tmp = Path(tempfile.mkdtemp(prefix="intent-gate-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self_path = tmp / "self.jsonl"
        real_path = tmp / "user.jsonl"
        self_path.write_text('{"label":"chat","text":"SELF"}\n', encoding="utf-8")
        real_path.write_text('{"label":"chat","text":"REAL"}\n', encoding="utf-8")

        canned = {}
        if self_acc is not None:
            canned["SELF"] = {"total": 1, "correct": 1, "accuracy": self_acc,
                              "matrix": {}, "errors": []}
        if real_acc is not None:
            canned["REAL"] = {"total": 1, "correct": 1, "accuracy": real_acc,
                              "matrix": {}, "errors": []}

        def fake_measure(rows, service, orchestrator, *, verbose):
            return canned[rows[0]["text"]]

        self_cfg = self_path if self_acc is not None else tmp / "missing_self.jsonl"
        real_cfg = real_path if real_acc is not None else tmp / "missing.jsonl"

        with patch.object(mic, "CORPUS", self_cfg), \
                patch.object(mic, "CORPUS_USER", real_cfg), \
                patch.object(mic, "ROOT", tmp), \
                patch.object(mic, "measure", fake_measure), \
                patch("server.assistant.service.AssistantService"), \
                patch("server.dialogue.orchestrator.Orchestrator"), \
                patch.object(sys, "argv", ["measure_intent_corpus"]):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = mic.main()
            return code, buffer.getvalue()

    def test_missing_real_corpus_fails_closed(self):
        """R50 核心：真实语料（权威输入）缺失 → exit 1，不再空转绿。"""
        code, out = self._run_main(self_acc=1.0, real_acc=None)
        self.assertEqual(code, 1, "真实语料缺失必须 fail-closed，不能静默 exit 0")
        self.assertIn("真实语料缺失", out)
        self.assertNotIn("地板线仍生效", out,
                         "旧的误导信息（地板线其实没生效）必须删除")

    def test_both_corpora_present_passes(self):
        """两语料齐全、准确率达标、差距在限内 → exit 0（不被误伤）。"""
        code, out = self._run_main(self_acc=0.85, real_acc=0.85)
        self.assertEqual(code, 0, out)

    def test_real_below_floor_fails(self):
        code, out = self._run_main(self_acc=0.85, real_acc=0.70)
        self.assertEqual(code, 1)
        self.assertIn("地板线", out)

    def test_gap_over_limit_fails(self):
        code, out = self._run_main(self_acc=0.95, real_acc=0.85)
        self.assertEqual(code, 1)
        self.assertIn("差距", out)

    def test_only_real_present_passes_with_floor(self):
        """仅真实语料存在 → 地板线仍对其生效、exit 0，且提示是实话。"""
        code, out = self._run_main(self_acc=None, real_acc=0.85)
        self.assertEqual(code, 0, out)
        self.assertIn("自造语料缺失", out)
        self.assertIn("地板线仍对真实语料生效", out)


if __name__ == "__main__":
    unittest.main()
