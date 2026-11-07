# -*- coding: utf-8 -*-
"""红灯机器门：证明「决策 trail 校验器」真的能红，再相信它说绿。

**为什么需要它**：三阶段流程的 TDD 一环要求「先定死对错的测试、红灯出现才准
写实现」，本质是防 AI 生一个配合错误逻辑的假测试。仓库已有
`scripts/test_check_probe.py` 守 `probe_self_check` 四判据本身，但**没有任何门
检查本项目的决策记录是否真的先红过**。这里把那四判据接到 2026-11-06 新建的
`docs/process/trail.jsonl` 上，并登记进 `run_gates.py` 的 ALWAYS_REQUIRED。

**不重复登记 `check_probe`**：`run_gates.py:88-94` 已有同名 key 且在
`ALWAYS_REQUIRED`（cmd 指向 `test_check_probe.py`）；`check_probe.py` 本体是库
模块、没有 CLI，不能当门禁命令。所以本脚本是「四判据的**应用**」，不是第四条
重复门禁。

**不碰仓库文件**：所有注入发生在 `tempfile` 沙盘里的 trail 副本，通过
`check_process_trail.py --trail <副本>` 校验；`probe_self_check(watch=副本)` 保证
注入真发生、还原逐字节相同。并发全量里也不会和别的脚本抢同一个文件。

Run: .venv/Scripts/python.exe -B scripts/test_check_process_trail.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_probe import patch_file, probe_self_check  # noqa: E402

TRAIL = ROOT / "docs" / "process" / "trail.jsonl"
CHECKER = ROOT / "scripts" / "check_process_trail.py"


def _rows() -> list[dict]:
    return [json.loads(line) for line in TRAIL.read_text(encoding="utf-8").splitlines()
            if line.strip()]


class ProcessTrailGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = Path(tempfile.mkdtemp(prefix="process-trail-test-"))
        repo_tmp = (ROOT / "tmp").resolve()
        resolved = self.sandbox.resolve()
        if repo_tmp == resolved or repo_tmp in resolved.parents:
            raise AssertionError(
                f"沙盘落在仓库 tmp/ 内：{resolved}。TMP/TEMP 被改成仓库内路径会"
                f"污染 tmp_sweep 计数棘轮（test_check_probe.py:29-38 同款事故）。")
        self.copy = self.sandbox / "trail.jsonl"
        self.original = TRAIL.read_bytes()
        self.copy.write_bytes(self.original)

    def tearDown(self) -> None:
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def _check(self, *extra: str) -> int:
        proc = subprocess.run(
            [sys.executable, "-B", str(CHECKER), "--trail", str(self.copy), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode

    def _write(self, rows: list[dict]) -> None:
        self.copy.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    # ---- 基准：真文件必须绿 ----

    def test_real_trail_is_green(self) -> None:
        self.assertEqual(self._check(), 0, "真实 trail 应当是绿的，否则下面全是空谈")

    # ---- 四判据应用：有效注入 / 等价改动 ----

    def test_probe_detects_missing_verification(self) -> None:
        """删一条 verification → 破坏版必须红、还原后必须绿、逐字节复原。"""
        first = self.original.decode("utf-8").splitlines()[0]
        broken = json.loads(first)
        broken.pop("verification")
        inject, restore = patch_file(
            self.copy, first, json.dumps(broken, ensure_ascii=False))
        result = probe_self_check(
            name="check_process_trail 能抓到缺 verification",
            run=lambda: self._check(),
            break_it=inject,
            restore=restore,
            watch=self.copy)
        self.assertTrue(result.ok, result.report())
        self.assertEqual(result.break_exit, 1, "破坏版必须是真红（exit 1）")
        self.assertEqual(result.restore_exit, 0)
        self.assertEqual(self.copy.read_bytes(), self.original, "副本必须逐字节复原")

    def test_probe_rejects_equivalent_rewrite(self) -> None:
        """等价改写（同内容不同键序）→ 破坏版仍绿 → 四判据必须判「探针无效」。

        这正是 plan-108 连踩两次的形态：`is_absolute()` 从断言里去掉、
        `all(...)` 换成 `True`，以为在造破坏版，实际造的是等价改写。
        """
        first = self.original.decode("utf-8").splitlines()[0]
        equivalent = json.dumps(
            dict(reversed(list(json.loads(first).items()))), ensure_ascii=False)
        self.assertNotEqual(first, equivalent, "键序改写必须真的改变字节，否则测的是空转")
        inject, restore = patch_file(self.copy, first, equivalent)
        result = probe_self_check(
            name="等价改写不得被当成有效破坏",
            run=lambda: self._check(),
            break_it=inject,
            restore=restore,
            watch=self.copy)
        self.assertFalse(result.ok, "等价改写的破坏版仍绿，必须判探针无效")
        self.assertEqual(result.break_exit, 0)
        self.assertTrue(any("破坏版也绿了" in n for n in result.notes), result.report())
        self.assertEqual(self.copy.read_bytes(), self.original)

    # ---- 校验器判别力：五种假记录必须红 ----

    def test_missing_evidence_is_red(self) -> None:
        rows = _rows()
        rows[0].pop("evidence")
        self._write(rows)
        self.assertNotEqual(self._check(), 0)

    def test_unanchored_evidence_is_red(self) -> None:
        rows = _rows()
        rows[0]["evidence"] = ["AGENTS.md", "大概是这么回事"]
        self._write(rows)
        self.assertNotEqual(self._check(), 0)

    def test_nonexistent_script_reference_is_red(self) -> None:
        rows = _rows()
        rows[0]["verification"] = ["python scripts/this_gate_does_not_exist.py"]
        self._write(rows)
        self.assertNotEqual(self._check(), 0)

    def test_done_without_result_is_red(self) -> None:
        rows = _rows()
        rows[0].pop("result", None)
        self._write(rows)
        self.assertNotEqual(self._check(), 0)

    def test_too_few_rows_is_red(self) -> None:
        """防真空绿：删到只剩 2 条，下限必须拦住。"""
        self._write(_rows()[:2])
        self.assertNotEqual(self._check(), 0)

    def test_missing_template_is_red(self) -> None:
        """开工模板被删也要红——流程零件不许悄悄消失。"""
        self.assertNotEqual(
            self._check("--template", str(self.sandbox / "no-such-template.md")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
