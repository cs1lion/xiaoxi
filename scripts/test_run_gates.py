r"""R51：run_gates.main() 裁决逻辑的 fail-closed 回归测试。

run_gates 曾把 `skipped` 排在非零退出码之前裁决——一个「打印 SKIP 行却又
exit!=0」的门禁被误判成 SKIP；而 `if failed` 不含它、`--allow-skip` 又放过
skipped，三者叠加让一句真实失败被洗成绿，结论还打印「所有门禁都真跑过并断言」
（2026-10-27 合成门禁实证：exit=1+SKIP行+--allow-skip → main() exit 0）。
修复：非零退出码一律 FAIL（fail-closed），SKIP 只在 exit==0 时成立。

本测试用合成门禁（python -c 按需打印 SKIP 行、以指定码退出）驱出各 verdict
分支，零网络零 Key、不跑任何真实门禁。

2026-10-31 补充：unittest 装饰器 skip（@unittest.skipUnless/skipIf）的原生汇总行
是 `OK (skipped=N)`——行首是 OK 不是 SKIP，`^\s*SKIP` 判据命不中，exit 0 时被裁成
PASS（实测 procedural_memory_eval：Ran 24 tests、skipped=20、报 PASS）。run_gate 已
补识别 `OK (skipped=N)`（N>0）；本文件同时钉住：纯 `OK` 与 `OK (skipped=0)` 仍是
PASS（不许把真跑过的绿洗成假跳），以及 R51 红线在新增式下不放宽——exit!=0 且输出
含 `FAILED (failures=1, skipped=2)` 仍必须 FAIL，SKIP 行 + exit!=0 亦照旧 FAIL。
"""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parent))

import run_gates as RG  # noqa: E402


def _synthetic_gate(prints_skip: bool, exit_code: int) -> dict:
    """构造一个合成门禁：按需打印 SKIP 行，以指定退出码结束。"""
    body = "import sys\n"
    body += ("print('SKIP: 环境不满足')\n" if prints_skip
             else "print('gate ran and asserted')\n")
    body += f"sys.exit({exit_code})\n"
    return {"cmd": [str(RG.PY), "-c", body], "skip_regex": r"^\s*SKIP"}


def _script_gate(output_text: str, exit_code: int) -> dict:
    """构造一个合成门禁：按行打印 output_text，以指定退出码结束。

    行内容一律纯 ASCII（unittest 汇总行本就是 ASCII），不依赖控制台编码；
    skip_regex 沿用生产默认 `^\\s*SKIP`，让「装饰器 skip 形态命不中式①」这一
    前提与线上一致。
    """
    lines = "".join(f"print({line!r})\n" for line in output_text.splitlines())
    body = f"import sys\n{lines}sys.exit({exit_code})\n"
    return {"cmd": [str(RG.PY), "-c", body], "skip_regex": r"^\s*SKIP"}


class RunGatesVerdictTests(unittest.TestCase):
    """只测 main() 的 verdict 裁决；用合成门禁，不跑真实门禁。"""

    def _run(self, prints_skip, exit_code, allow_skip=False):
        with patch.dict(RG.GATES, {"g": _synthetic_gate(prints_skip, exit_code)}), \
                patch.object(sys, "argv",
                             ["run_gates", "--only", "g"]
                             + (["--allow-skip"] if allow_skip else [])):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = RG.main()
            out = buffer.getvalue()
        # 精确匹配 verdict 行（[FAIL]/[SKIP]/[PASS] 开头），别误取 [run ] 行
        line = next((l for l in out.splitlines()
                     if l.startswith(("[FAIL]", "[SKIP]", "[PASS]"))), "")
        verdict = line.split("]")[0].lstrip("[") if line else "?"
        return code, verdict, out

    def test_nonzero_exit_with_skip_line_is_fail_not_skip(self):
        """R51 核心：打印 SKIP 行却 exit!=0 → FAIL，永不被洗成 SKIP。"""
        code, verdict, _ = self._run(prints_skip=True, exit_code=1)
        self.assertEqual(verdict, "FAIL", "非零退出码必须 FAIL，即使打印了 SKIP 行")
        self.assertEqual(code, 1)

    def test_allow_skip_does_not_launder_a_real_failure(self):
        """R51 关键：--allow-skip 不得把「exit!=0 的真实失败」洗成绿。

        修前：exit=1+SKIP行 + --allow-skip → main() exit 0（fail-open）。
        """
        code, verdict, out = self._run(prints_skip=True, exit_code=1, allow_skip=True)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(code, 1,
                         "--allow-skip 只接受环境不满足的 exit 0 跳过，不放任真实失败")
        self.assertNotIn("所有门禁都真跑过并断言", out,
                         "有真实失败时不得打印全绿结论")

    def test_nonzero_exit_without_skip_is_fail(self):
        code, verdict, _ = self._run(prints_skip=False, exit_code=1)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(code, 1)

    def test_zero_exit_with_skip_line_is_skip(self):
        """合法跳过：exit==0 且打印 SKIP 行 → SKIP（未被修复误伤）。"""
        code, verdict, _ = self._run(prints_skip=True, exit_code=0)
        self.assertEqual(verdict, "SKIP")
        self.assertEqual(code, 1, "跳过（未 --allow-skip）仍让整体 exit 1")

    def test_zero_exit_skip_with_allow_skip_is_accepted(self):
        """--allow-skip 的既定用途：接受「环境不满足、exit 0」的跳过。"""
        code, verdict, _ = self._run(prints_skip=True, exit_code=0, allow_skip=True)
        self.assertEqual(verdict, "SKIP")
        self.assertEqual(code, 0)

    def test_zero_exit_no_skip_is_pass(self):
        code, verdict, _ = self._run(prints_skip=False, exit_code=0)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(code, 0)


class RunGatesUnittestSkipTests(unittest.TestCase):
    """unittest 装饰器 skip 的原生汇总行形态（2026-10-31 盲区修复）。

    旧判据只有 spec["skip_regex"]（`^\\s*SKIP`），而 @unittest.skipUnless/skipIf
    的输出是 `s` + `OK (skipped=N)`——行首是 OK 不是 SKIP，命不中式①，exit 0
    被裁成 PASS。修复：run_gate 增加式② `^\\s*OK \\(skipped=([1-9]\\d*)`。
    """

    def _run(self, output_text, exit_code, allow_skip=False):
        with patch.dict(RG.GATES, {"g": _script_gate(output_text, exit_code)}), \
                patch.object(sys, "argv",
                             ["run_gates", "--only", "g"]
                             + (["--allow-skip"] if allow_skip else [])):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = RG.main()
            out = buffer.getvalue()
        line = next((l for l in out.splitlines()
                     if l.startswith(("[FAIL]", "[SKIP]", "[PASS]"))), "")
        verdict = line.split("]")[0].lstrip("[") if line else "?"
        return code, verdict, out

    def test_unittest_skipped_summary_zero_exit_is_skip(self):
        """缺陷正主：`OK (skipped=20)` + exit 0 → SKIP（修前是 PASS）。"""
        out_text = ("sssssssssssssssssss...s.\n"
                    "----------------------------------------------------------------------\n"
                    "Ran 24 tests in 0.013s\n"
                    "\n"
                    "OK (skipped=20)")
        code, verdict, _ = self._run(out_text, exit_code=0)
        self.assertEqual(verdict, "SKIP", "装饰器 skip 的原生汇总行必须被探测到")
        self.assertEqual(code, 1, "任何门禁跳过即失败：SKIP 让总退出码非零（预期语义）")

    def test_unittest_skipped_summary_multiple_lines_any_hit(self):
        """多结论行时任一 N>0 即命中（例如套件输出里出现两行汇总）。"""
        out_text = ("Ran 3 tests\n"
                    "OK (skipped=0)\n"
                    "Ran 5 tests\n"
                    "OK (skipped=5)")
        _, verdict, _ = self._run(out_text, exit_code=0)
        self.assertEqual(verdict, "SKIP")

    def test_plain_ok_is_pass(self):
        """纯 `OK` 是全跑全过，不许被式② 误判成 SKIP。"""
        out_text = "........\n----------------------------------------------------------------------\nRan 8 tests in 0.1s\n\nOK"
        code, verdict, _ = self._run(out_text, exit_code=0)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(code, 0)

    def test_ok_skipped_zero_is_pass(self):
        """`OK (skipped=0)` 同样是全跑全过：N>0 才算 skip，0 不算。"""
        out_text = "........\nRan 8 tests in 0.1s\n\nOK (skipped=0)"
        code, verdict, _ = self._run(out_text, exit_code=0)
        self.assertEqual(verdict, "PASS")
        self.assertEqual(code, 0)

    def test_failed_summary_not_flipped_by_skip_detection(self):
        """R51 红线在新增式下不放宽：exit!=0 一律 FAIL。

        `FAILED (failures=1, skipped=2)` 含 skipped 字样但行首不是 OK，式② 不命中；
        即使命中，裁决式也先拦 exit!=0——真实失败绝不被洗成 SKIP。
        """
        out_text = ("F..\n"
                    "=====================================================================\n"
                    "FAIL: test_x\n"
                    "----------------------------------------------------------------------\n"
                    "AssertionError\n"
                    "\n"
                    "Ran 3 tests\n"
                    "\n"
                    "FAILED (failures=1, skipped=2)")
        code, verdict, out = self._run(out_text, exit_code=1)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(code, 1)
        self.assertNotIn("所有门禁都真跑过并断言", out)

    def test_skip_line_with_failed_skipped_nonzero_exit_is_fail(self):
        """自定义 SKIP 行 + `FAILED (failures=1, skipped=2)` + exit!=0 → FAIL（R51 照旧）。"""
        out_text = ("SKIP: 夹具缺失\n"
                    "Ran 3 tests\n"
                    "\n"
                    "FAILED (failures=1, skipped=2)")
        code, verdict, _ = self._run(out_text, exit_code=1)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual(code, 1)

    def test_ok_skipped_with_allow_skip_accepted(self):
        """--allow-skip 的既定用途同样覆盖新形态：SKIP 且 exit 0 → 总码 0。"""
        out_text = "Ran 24 tests\n\nOK (skipped=20)"
        code, verdict, _ = self._run(out_text, exit_code=0, allow_skip=True)
        self.assertEqual(verdict, "SKIP")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
