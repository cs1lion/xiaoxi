# -*- coding: utf-8 -*-
"""check_staged_scope 回归门禁：判据不许退化成永真。

**为什么需要它**：本会话「提交前忘了核对暂存范围」这个形态重复了至少 8 次，
plan-101 才把它变成一个退出码。但那个工具自身没有回归保护——判据
（暂存区 ⊆ 声明路径）只在 plan-101/102 用一次性脚本验过。若有人把 `foreign`
判断改成永假（比如误以为「声明制太严」而放宽），不会有任何测试红，而那时
它恰好回到「什么都没检查」的状态。

**测试怎么造场景而不碰真实 index**：`check_staged_scope` 的 `git()` 与
`staged_paths()` 在 plan-116 加了可选的 `root` 参数（默认仍是 ROOT，生产调用
不受影响）。测试在 `tempfile.mkdtemp()` 里 `git init` 一个临时仓库，往里
`add` 文件造出「暂存区」，然后把 `root` 指过去跑 `main()`。

**覆盖的四种形态**：
- 暂存区全部在声明范围内 → exit 0；
- 有声明外文件 → exit 1，且输出含该文件名与撤出命令；
- 暂存区为空 → exit 0；
- 无参数 → exit 2（用法 + 拒绝猜）。

Run: .venv/Scripts/python.exe -B scripts/test_check_staged_scope.py
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_staged_scope as css  # noqa: E402


def _init_repo() -> Path:
    """造一个临时 git 仓库，返回其路径。

    **断言沙盘不落在仓库内**：`tempfile.mkdtemp()` 默认用 `TMP`/`TEMP`
    环境变量，本机是 `C:\\Users\\...\\AppData\\Local\\Temp`。若将来有人把
    这两个变量改成仓库内路径，本测试的沙盘就会落进仓库 `tmp/`，每次跑门禁都
    给 `tmp_sweep` 的计数棘轮添项——那正是 plan-45 修过的泄漏形态。这条断言
    让那种改法当场红，而不是静默泄漏。
    """
    sandbox = Path(tempfile.mkdtemp(prefix="staged-scope-test-"))
    repo_tmp = (ROOT / "tmp").resolve()
    resolved = sandbox.resolve()
    if repo_tmp == resolved or repo_tmp in resolved.parents:
        raise AssertionError(
            f"测试沙盘落在仓库 tmp/ 内：{resolved}。TMP/TEMP 环境变量被改成"
            f"仓库内路径了——这会让每次跑门禁都污染 tmp_sweep 的计数棘轮。")
    subprocess.run(["git", "init", "-q"], cwd=str(sandbox), check=True,
                   capture_output=True)
    # git init 默认分支名各版本不同，但 add/commit 不依赖分支名。
    return sandbox


def _write_and_stage(repo: Path, name: str) -> None:
    (repo / name).write_text(f"content of {name}\n", encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=str(repo), check=True,
                   capture_output=True)


class StagedScopeTests(unittest.TestCase):
    """四种形态：三种 exit 0/1、一种 exit 2。"""

    def setUp(self) -> None:
        self.repo = _init_repo()
        self.addCleanup(shutil.rmtree, self.repo, True)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = css.main(argv, root=self.repo)
        return code, buf.getvalue()

    def test_empty_index_exits_zero(self):
        """暂存区为空 → exit 0。"""
        code, out = self._run(["anything.py"])
        self.assertEqual(code, 0, out)
        self.assertIn("暂存区为空", out)

    def test_all_declared_exits_zero(self):
        """暂存区全部落在声明范围内 → exit 0。"""
        _write_and_stage(self.repo, "mine.py")
        _write_and_stage(self.repo, "mine2.py")
        code, out = self._run(["mine.py", "mine2.py"])
        self.assertEqual(code, 0, out)
        self.assertIn("干净", out)

    def test_foreign_file_exits_one_and_names_it(self):
        """有声明外文件 → exit 1，且输出含该文件名与撤出命令。

        这是判据的本体：把别人的文件拦下来，并告诉人怎么撤。
        """
        _write_and_stage(self.repo, "mine.py")
        _write_and_stage(self.repo, "theirs.py")
        code, out = self._run(["mine.py"])
        self.assertEqual(code, 1, out)
        self.assertIn("theirs.py", out, "必须点名那个声明外文件")
        self.assertIn("git reset HEAD --", out, "必须给撤出命令")

    def test_no_args_exits_two(self):
        """无参数 → exit 2（不猜意图）。"""
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()):
                css.main([], root=self.repo)
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
