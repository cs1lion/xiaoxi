# -*- coding: utf-8 -*-
"""ci_test_shard 的输出捕获契约（2026-11-12）。

**为什么要有这条**：2026-11-12 实测跑全量时，CI 分片器的 stderr 刷出
`UnicodeDecodeError: 'gbk' codec can't decode byte 0xab in position 1072`，
而且**失败脚本的诊断输出整段丢失**。

根因：`scripts/ci_test_shard.py:48-49` 用 `capture_output=True, text=True` 抓子进程
输出，没给 `encoding=`。Windows 上默认走 locale 编码（本机 cp936/GBK）。子进程写出的
字节里有 GBK 不可解码的（UTF-8 的中文、emoji、二进制片段），Python 的 `_readerthread`
就抛 UnicodeDecodeError，`proc.stdout` 变 `None` —— 于是 `run_one` 的 tail 变成空串，
**失败原因一个字都贴不出来**，CI 日志只剩一个脚本名。

对比：`scripts/run_gates.py:1315-1316` 早就写对了（显式 `encoding="utf-8",
errors="replace"`）。分片器是同一批人后来写的，漏了。

修法：显式 `encoding="utf-8", errors="replace"`。
"""
from __future__ import annotations

import io
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ci_test_shard as cts


class ShardOutputCaptureTests(unittest.TestCase):
    def _write_child(self, tmp: Path, name: str, body: str) -> Path:
        path = tmp / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_non_gbk_bytes_do_not_lose_the_failure_tail(self):
        """子进程写出 GBK 不可解码的字节时，失败原因必须仍然能贴出来。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child = self._write_child(tmp, "boom.py", (
                "import sys\n"
                "sys.stdout.buffer.write('失败断言：期望 3 实得 5'.encode('utf-8'))\n"
                "sys.stdout.buffer.write(bytes([0x80, 0xAF]))\n"
                "sys.stdout.flush()\n"
                "sys.stderr.write('stderr 里的中文'.encode('utf-8'))\n"
                "sys.exit(1)\n"
            ))
            name, code, tail = cts.run_one(child)
            self.assertEqual(code, 1, "退出码必须仍是 1，不能被解码错误吃掉")
            self.assertIn("失败断言", tail,
                          "失败原因必须还在 tail 里 —— 修前这里是空串")
            self.assertIn("期望 3 实得 5", tail)

    def test_chinese_output_is_readable_not_mojibake(self):
        """中文必须是**可读的中文**，不是 \\ufffd 乱码。

        2026-11-12 实测：本机子进程的 stdout 走 locale 编码（cp936/GBK），
        字节是 d6d0cec4...（中文测试的 GBK）。父进程按 utf-8 解就得到乱码。
        所以修法是**两端都钉死 utf-8**：子进程用 PYTHONIOENCODING=utf-8 写，
        父进程用 encoding='utf-8' 解。

        只改父进程不够 —— replace 兜底虽不丢输出，但中文全是乱码，
        「贴失败原因」这件事等于没做。
        """
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child = self._write_child(tmp, "zh.py", (
                "print('失败断言：期望 3 实得 5')\n"
                "sys.exit(1)\n"
            ))
            name, code, tail = cts.run_one(child)
            self.assertEqual(code, 1)
            self.assertIn("失败断言：期望 3 实得 5", tail,
                          "中文必须可读，不能是乱码")
            self.assertNotIn("�", tail, "不该有替换字符")

    def test_stdout_stderr_are_both_captured(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child = self._write_child(tmp, "both.py", (
                "import sys\n"
                "print('OUT 中文')\n"
                "print('ERR 中文', file=sys.stderr)\n"
                "sys.exit(1)\n"
            ))
            name, code, tail = cts.run_one(child)
            self.assertEqual(code, 1)
            self.assertIn("OUT 中文", tail)
            self.assertIn("ERR 中文", tail)

    def test_passing_script_still_returns_empty_tail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child = self._write_child(tmp, "ok.py", "print('fine')\n")
            name, code, tail = cts.run_one(child)
            self.assertEqual(code, 0)
            self.assertEqual(tail, "")

    def test_timeout_is_reported_as_124(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            child = self._write_child(tmp, "slow.py", "import time\ntime.sleep(30)\n")
            with patch.object(cts.subprocess, "run",
                              side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
                name, code, tail = cts.run_one(child)
            self.assertEqual(code, 124)
            self.assertIn("timeout", tail)

    def test_run_one_declares_an_explicit_encoding(self):
        """静态检查：run_one 必须显式给 encoding=，不能靠 locale 兜底。"""
        import inspect

        source = inspect.getsource(cts.run_one)
        self.assertIn("encoding=", source,
                      "run_one 抓子进程输出必须显式 encoding=（Windows 上 locale 是 GBK）")
        self.assertIn("errors=", source,
                      "必须给 errors='replace'，否则一个坏字节就丢掉整段输出")

    def test_child_is_forced_to_write_utf8(self):
        """子进程也必须被钉成写 utf-8，否则父进程解出来是乱码。"""
        import inspect

        source = inspect.getsource(cts.run_one)
        self.assertIn("PYTHONIOENCODING", source,
                      "必须给子进程设 PYTHONIOENCODING=utf-8 —— "
                      "本机子进程默认按 locale（cp936/GBK）写，"
                      "父进程按 utf-8 解就得到乱码")


if __name__ == "__main__":
    unittest.main(verbosity=2)
