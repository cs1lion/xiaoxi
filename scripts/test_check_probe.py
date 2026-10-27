# -*- coding: utf-8 -*-
"""check_probe 自身回归门禁：四条判据不许退化。

**为什么需要它**：`check_probe.py` 的四条判据是三次真实事故逼出来的——
- 等价改动/只抹一处 → `break_exit == 0` 判「探针无效」（plan-108/110 连踩）
- 锚点撞多处 → `patch_file` 的 `assert count==1` 当场炸（plan-108）
- 注入没生效 → `break_it` 后文件必须与注入前不同（plan-113）
- 还原不干净 → `restore` 后必须与注入前逐字节相同（plan-111/112）

前三次的验证都是一次性脚本、跑完即删。下次有人改 `probe_self_check` 或
`patch_file`，判据退化了不会有人知道——而它们恰恰是被事故逼出来的。

本门禁把五种形态固化成常驻断言。目标文件用临时目录里的 CRLF 文件
（`patch_file` 的二进制读写与 `watch` 的 sha256 比对正是为 CRLF 设计的），
不碰仓库任何真实文件。

Run: .venv/Scripts/python.exe -B scripts/test_check_probe.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_probe import patch_file, probe_self_check  # noqa: E402

# 目标文件的形态：CRLF 行尾 + 一个唯一锚点。CRLF 是关键——plan-111 的缺陷
# （text 模式读写把 CRLF 改写成 LF）只有拿 CRLF 文件才测得到。
ORIG = b"x = 1\r\nMARKER = 1\r\ny = 2\r\n"
ANCHOR = "MARKER = 1"


def _make_target() -> tuple[Path, Path]:
    """造一个临时 CRLF 目标文件，返回 (sandbox_dir, target_path)。"""
    sandbox = Path(tempfile.mkdtemp(prefix="check-probe-test-"))
    target = sandbox / "gate.py"
    target.write_bytes(ORIG)
    return sandbox, target


class ProbeSelfCheckTests(unittest.TestCase):
    """probe_self_check 的五种形态：四个必须判无效，一个必须判有效。"""

    def _run(self, target: Path) -> int:
        """被测目标：含 BROKEN 则红，否则绿。"""
        return 1 if b"BROKEN" in target.read_bytes() else 0

    def test_valid_injection_is_accepted(self):
        """正常注入 → 判有效，且文件逐字节复原。"""
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        inject, restore = patch_file(target, ANCHOR, "MARKER = BROKEN")
        result = probe_self_check("正常注入", lambda: self._run(target),
                                  inject, restore, watch=target)
        self.assertTrue(result.ok, result.report())
        self.assertEqual(target.read_bytes(), ORIG)

    def test_equivalent_change_is_rejected(self):
        """等价改动（文件变了但门禁仍绿）→ 判无效。

        plan-108/110 的坑：把 `is_absolute()` 去掉、把 `all(...)` 换成 True、
        把 `== 1` 放宽成 `>= 0`——注入确实发生了，破坏版却照样绿。
        """
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        # 注入一个不影响门禁结论的改动：加一行注释。
        inject, restore = patch_file(target, ANCHOR, "MARKER = 1  # touched")
        result = probe_self_check("等价改动", lambda: self._run(target),
                                  inject, restore, watch=target)
        self.assertFalse(result.ok, result.report())
        self.assertEqual(result.break_exit, 0, "等价改动的破坏版应该也是绿的")
        self.assertEqual(target.read_bytes(), ORIG, "还原必须逐字节复原")

    def test_noop_break_it_is_rejected(self):
        """break_it 空转 → 判无效且点名「注入没生效」。

        plan-113 的坑：API 从「patch_file 返回前就注入」改成「返回
        (inject, restore)」后，调用点仍写 break_it=lambda: None，inject
        从未被调用，文件纹丝未动，门禁全绿被误判成「恒绿（判据无效）」。
        那不是等价改动，是什么都没测。
        """
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        # 造好 inject 但不调用——正是那个坑的形态。
        _inject, restore = patch_file(target, ANCHOR, "MARKER = BROKEN")
        restore()
        result = probe_self_check("break_it 空转", lambda: self._run(target),
                                  lambda: None, restore, watch=target)
        self.assertFalse(result.ok, result.report())
        self.assertTrue(any("注入没生效" in n for n in result.notes),
                        f"应点名注入没生效，实际 notes={result.notes}")

    def test_dirty_restore_is_rejected(self):
        """还原不干净 → 判无效且点名还原差异。

        plan-111 的坑：text 模式读写在退出码为 0 的情况下把 CRLF 改写成 LF，
        还原跑了、码也绿，仓库却留下格式差异。
        """
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)

        def break_it():
            target.write_bytes(ORIG.replace(b"MARKER = 1", b"MARKER = BROKEN"))

        def restore():
            target.write_bytes(b"x = 1\nMARKER = 1\ny = 2\n")   # CRLF → LF

        result = probe_self_check("还原不干净", lambda: self._run(target),
                                  break_it, restore, watch=target)
        self.assertFalse(result.ok, result.report())
        self.assertTrue(any("还原不干净" in n for n in result.notes),
                        f"应点名还原不干净，实际 notes={result.notes}")

    def test_ambiguous_anchor_fails_fast(self):
        """锚点撞多处 → patch_file 当场炸，且文件未被改动。

        plan-108 第三次失败的形态：锚点出现两次，replace 改错地方或漏改。
        """
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        target.write_bytes(b"a = 1\r\nDUP = 1\r\nb = 2\r\nDUP = 1\r\n")
        before = target.read_bytes()
        with self.assertRaises(AssertionError):
            patch_file(target, "DUP = 1", "DUP = 2")
        self.assertEqual(target.read_bytes(), before, "炸之前不许改动文件")


class PatchFileTests(unittest.TestCase):
    """patch_file 的两个契约：二进制读写保行尾、不立即注入。"""

    def test_binary_roundtrip_preserves_crlf(self):
        """注入再还原，CRLF 行尾逐字节不变。"""
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        inject, restore = patch_file(target, ANCHOR, "MARKER = BROKEN")
        inject()
        self.assertEqual(target.read_bytes().count(b"\r\n"), 3,
                         "注入后 CRLF 数不该变")
        restore()
        self.assertEqual(target.read_bytes(), ORIG, "还原后必须逐字节相同")

    def test_patch_file_does_not_inject_immediately(self):
        """patch_file 不许在返回前就注入。

        plan-112 的坑：返回前就注入会让 probe_self_check 在 break_it 之前
        拍的「原文」快照拍到破坏版，还原后必然对不上，误报「还原不干净」。
        谁负责注入，快照就必须拍在它之前。
        """
        sandbox, target = _make_target()
        self.addCleanup(shutil.rmtree, sandbox, True)
        _inject, _restore = patch_file(target, ANCHOR, "MARKER = BROKEN")
        self.assertEqual(target.read_bytes(), ORIG,
                         "patch_file 返回时文件必须还是原文")


if __name__ == "__main__":
    unittest.main()
