# -*- coding: utf-8 -*-
"""#39 生产库哨兵：测试进程打开仓库 data/ 下的库必须被拦。

2026-10-24 清理真实台账时发现 379 条测试样式委托打进了 data/commissions.db；
约定（各套件自己用 tempdir）拦不住，所以把隔离做成机械保证：
`TEST_SENTINEL=1` 的进程在**打开数据库**时，若目标是仓库 data/ 下的文件就报错。

检查放在打开点而不是 import 点——静态检查器（check_protocol 等）只 import
配置、从不建连接，import 即抛会误伤它们。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def run(env_extra: dict[str, str], code: str) -> subprocess.CompletedProcess:
    # 子进程必须显式 UTF-8：Windows 下 stdout/stderr 是管道时默认走 locale（GBK），
    # 而哨兵的报错文案是中文——子进程按 GBK 写出、父进程按 utf-8 解码，中文全变成
    # `�`，断言「"真实数据目录" in stderr」就永远不成立，于是**一个坏掉的检查器
    # 报出的红看起来像被测行为坏了**（实测踩过）。父进程那侧的 encoding 只解决解码。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **env_extra}
    return subprocess.run([PY, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


MEMORY_OPEN = (
    "from server.memory.store import MemoryStore\n"
    "MemoryStore({path!r}).close()\n"
    "print('OPENED')\n"
)
LEDGER_OPEN = (
    "from server.interaction.threads import CommissionStore\n"
    "CommissionStore({path!r})\n"
    "print('OPENED')\n"
)


def main() -> int:
    # 本套件的输出里有中文，断言失败时还会把子进程的中文 stderr 原样打出来。
    # Windows 控制台默认 GBK，遇到 `�`（父进程解码替换符）就 UnicodeEncodeError
    # 整只脚本崩掉——报出来的是编码错误，不是哨兵的结论。先把自己钉成 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass  # 老解释器或被重定向到不支持 reconfigure 的对象
    print("[生产库哨兵]")

    prod = run({"TEST_SENTINEL": ""}, MEMORY_OPEN.format(path=str(ROOT / "data" / "memory.db")))
    check("生产进程（无 TEST_SENTINEL）打开真实库不报错",
          prod.returncode == 0 and "OPENED" in prod.stdout, prod.stderr[-200:])

    real = run({"TEST_SENTINEL": "1"}, MEMORY_OPEN.format(path=str(ROOT / "data" / "memory.db")))
    check("测试进程打开真实 memory.db 被拦",
          real.returncode != 0 and "真实数据目录" in real.stderr, real.stderr[-200:])

    ledger = run({"TEST_SENTINEL": "1"}, LEDGER_OPEN.format(path=str(ROOT / "data" / "commissions.db")))
    check("测试进程打开真实台账 commissions.db 被拦",
          ledger.returncode != 0 and "真实数据目录" in ledger.stderr, ledger.stderr[-200:])

    nested = run({"TEST_SENTINEL": "1"},
                 MEMORY_OPEN.format(path=str(ROOT / "data" / "sub" / "x.db")))
    check("data/ 子目录同样被拦（不只挡顶层文件）",
          nested.returncode != 0, nested.stderr[-200:])

    memory_db = run({"TEST_SENTINEL": "1", "DB_PATH": ":memory:"}, MEMORY_OPEN.format(path=":memory:"))
    check(":memory: 正常", memory_db.returncode == 0 and "OPENED" in memory_db.stdout,
          memory_db.stderr[-200:])

    scratch = Path(tempfile.mkdtemp(prefix="sentinel-")) / "m.db"
    temp_file = run({"TEST_SENTINEL": "1", "DB_PATH": str(scratch)},
                    MEMORY_OPEN.format(path=str(scratch)))
    check("临时文件正常", temp_file.returncode == 0 and "OPENED" in temp_file.stdout,
          temp_file.stderr[-200:])

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
