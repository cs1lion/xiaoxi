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
import os
import sys
import tempfile
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
        self.assertEqual(code, 1,                         "--allow-skip 只接受环境不满足的 exit 0 跳过，不放任真实失败")
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

    def test_allow_skip_does_not_print_a_false_all_green_conclusion(self):
        """--allow-skip 接受跳过时，**不得**再打印「所有门禁都真跑过并断言」。

        2026-11-12 实测踩到：加了 --allow-skip 后，
        `run_gates.py` 跳过「有门禁这轮没有真的跑」的分支，直接落到无条件
        `print("结论：所有门禁都真跑过并断言。")` —— 明明这一轮有 3 条门禁
        没跑，却打印「所有门禁都真跑过并断言」。两份已入库的证据日志带着这句
        自相矛盾的话（gates-clean.log:441 / gates-full.log:438）。

        修法：结论行按实际跳过数分两种说法。
        """
        code, verdict, out = self._run(prints_skip=True, exit_code=0, allow_skip=True)
        self.assertEqual(code, 0, "--allow-skip 接受跳过时整体仍应 exit 0")
        self.assertNotIn("所有门禁都真跑过并断言。", out,
                         "有门禁被跳过时不得打印「所有门禁都真跑过并断言。」")
        self.assertIn("条门禁没有真的跑", out,
                      "应明确说明这一轮有门禁没跑，只是不判失败")

    def test_no_skip_still_prints_the_all_green_conclusion(self):
        """对照：真跑过并断言时，那句话照旧要出现（别把结论改没了）。"""
        code, verdict, out = self._run(prints_skip=False, exit_code=0)
        self.assertEqual(code, 0)
        self.assertIn("所有门禁都真跑过并断言。", out)

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


class RunGatesEncodingTests(unittest.TestCase):
    """门禁子进程的输出编码必须两端钉死 utf-8（2026-11-12）。

    父进程这一半 run_gates.py 早就写对了（encoding='utf-8', errors='replace'），
    但**子进程那一半漏了**：本机子进程 stdout 走 locale（cp936/GBK），
    字节是「中文」的 GBK 编码，父进程按 utf-8 解出来是乱码 ——
    不丢输出，但「贴失败原因」这件事等于没做。

    scripts/ci_test_shard.py 漏得更狠：父进程也没给 encoding=，
    坏字节直接把 stdout 打成 None，tail 整段变空 —— CI 日志只剩一个脚本名。
    """

    def test_child_env_forces_utf8_output(self):
        import inspect

        source = inspect.getsource(RG.run_gate)
        self.assertIn("PYTHONIOENCODING", source,
                      "必须给子进程设 PYTHONIOENCODING=utf-8 —— "
                      "本机子进程默认按 locale（cp936/GBK）写，"
                      "父进程按 utf-8 解就得到乱码")

    def test_parent_decodes_utf8_with_replace(self):
        import inspect

        source = inspect.getsource(RG.run_gate)
        self.assertIn('encoding="utf-8"', source)
        self.assertIn('errors="replace"', source,
                      "必须 errors='replace'，否则一个坏字节就丢掉整段输出")


class RunGatesShardEncodingTests(unittest.TestCase):
    """分片器的编码契约（2026-11-12）。详见 scripts/test_ci_test_shard.py。"""

    def test_shard_runner_declares_explicit_encoding(self):
        import inspect
        import sys as _sys

        _sys.path.insert(0, str(RG.ROOT / "scripts"))
        import ci_test_shard as cts

        source = inspect.getsource(cts.run_one)
        self.assertIn("encoding=", source)
        self.assertIn("errors=", source)
        self.assertIn("PYTHONIOENCODING", source)


class RunGatesArtifactRequirementTests(unittest.TestCase):
    """不在 git 里的依赖物缺失时必须 SKIP + 说清缺什么，而不是 FAIL（2026-11-09）。

    干净检出里 .venv / node_modules / agent-runtime/node_modules / pixel-world/dist
    都可能不在。此前缺它们时门禁要么直接 FAIL（ModuleNotFoundError、/world 404），
    要么静默跑在回退解释器上还报 PASS——「环境没装」被说成「代码坏了」，
    干净机器跑全量必红。判据：缺依赖物 → exit 0 + 行首 SKIP + 打印缺失路径与
    补救命令；依赖物在 → 照常跑门禁命令。
    """

    def _run_spec(self, spec, allow_skip=False):
        with patch.dict(RG.GATES, {"g": spec}), \
                patch.object(sys, "argv", ["run_gates", "--only", "g"]
                             + (["--allow-skip"] if allow_skip else [])):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = RG.main()
        return code, buffer.getvalue()

    def test_missing_artifact_skips_with_reason_instead_of_failing(self):
        """依赖物缺失：命令一次都不许执行，verdict=SKIP，理由含路径与补救。"""
        spec = {"cmd": [str(RG.PY), "-c", "raise SystemExit(9)"],
                "skip_regex": r"^\s*SKIP", "requires": ["__test_absent__"]}
        with patch.dict(RG.ARTIFACTS, {"__test_absent__":
                                       ("tmp/__absent_artifact__/x", "make it")}):
            code, out = self._run_spec(spec, allow_skip=True)
        self.assertIn("[SKIP]", out)
        self.assertIn("__absent_artifact__", out, "必须打印缺的是哪个路径")
        self.assertIn("make it", out, "必须打印补救命令")
        self.assertEqual(code, 0, "缺依赖物是环境事实，--allow-skip 下总码应为 0")
        self.assertNotIn("[FAIL]", out)

    def test_missing_artifact_still_fails_without_allow_skip(self):
        """跳过即失败的旧口径不变：缺依赖物仍是 SKIP，未 --allow-skip 时总码 1。"""
        spec = {"cmd": [str(RG.PY), "-c", "raise SystemExit(9)"],
                "skip_regex": r"^\s*SKIP", "requires": ["__test_absent__"]}
        with patch.dict(RG.ARTIFACTS, {"__test_absent__":
                                       ("tmp/__absent_artifact__/x", "make it")}):
            code, out = self._run_spec(spec)
        self.assertIn("[SKIP]", out)
        self.assertEqual(code, 1)

    def test_present_artifact_does_not_change_verdict(self):
        """依赖物在时机制不许误伤：命令照跑，非零退出仍 FAIL。"""
        spec = {"cmd": [str(RG.PY), "-c", "raise SystemExit(3)"],
                "skip_regex": r"^\s*SKIP", "requires": ["__test_present__"]}
        with patch.dict(RG.ARTIFACTS, {"__test_present__": ("uv.lock", "n/a")}):
            code, out = self._run_spec(spec, allow_skip=True)
        self.assertIn("[FAIL]", out)
        self.assertEqual(code, 1)

    def test_artifact_dependent_gates_declare_their_requirement(self):
        """关键依赖必须被登记，不许靠人记得手动加。"""
        self.assertIn("pixel_world_dist", RG.GATES["step8_e2e_browser"]["requires"])
        self.assertIn("venv", RG.GATES["core"]["requires"])
        self.assertIn("agent_runtime_node_modules",
                      RG.GATES["durable_takeover"]["requires"])
        self.assertIn("node_modules", RG.GATES["ui_browser"]["requires"])
        # 跑 .mjs 的门禁都要 chromium.launch()，浏览器二进制同样不在 git 里。
        self.assertIn("playwright_browser", RG.GATES["blocks_browser"]["requires"])
        self.assertNotIn("playwright_browser", RG.GATES["npm"]["requires"],
                         "npm test 不启动浏览器，不该被浏览器二进制拖成 SKIP")

    def test_playwright_browser_artifact_is_detected(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": home}):
                self.assertIsNotNone(
                    RG.missing_artifact({"requires": ["playwright_browser"]}),
                    "空目录里没有 chromium*，应判缺失")
                (Path(home) / "chromium-1234" / "chrome-win").mkdir(parents=True)
                self.assertIsNone(
                    RG.missing_artifact({"requires": ["playwright_browser"]}),
                    "有 chromium* 目录就不该再判缺失")


def _load_leak_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gate_tmp_leak_for_test", SCRIPTS / "test_gate_tmp_leak.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateTmpLeakVerdictTests(unittest.TestCase):
    """泄漏棘轮的裁决：门禁自己红 ≠ 泄漏，失败也不许静默通过。

    2026-11-09 前只看 delta>0：抽样门禁失败时按设计留沙盘 → 被打印成 LEAK，
    排查方向被带偏；反过来失败但清干净 → 判 ok，是静默通过。
    """

    def test_gate_failure_with_leftover_sandbox_is_not_called_a_leak(self):
        verdict, detail = _load_leak_module().classify(1, 1, "FAIL - 某条断言红了")
        self.assertEqual(verdict, "gate_failed")
        self.assertIn("1", detail)

    def test_gate_failure_that_cleaned_up_is_not_a_silent_pass(self):
        verdict, _ = _load_leak_module().classify(2, 0, "")
        self.assertEqual(verdict, "gate_failed")

    def test_clean_exit_with_growth_is_a_leak(self):
        self.assertEqual(_load_leak_module().classify(0, 3, "")[0], "leak")

    def test_clean_exit_without_growth_is_ok(self):
        self.assertEqual(_load_leak_module().classify(0, 0, "ok - 都过了")[0], "ok")

    def test_self_reported_skip_and_missing_command_skip(self):
        leak = _load_leak_module()
        self.assertEqual(leak.classify(0, 0, "SKIP: 8000 未起")[0], "skip")
        self.assertEqual(leak.classify(127, 0, "")[0], "skip")


if __name__ == "__main__":
    unittest.main()
