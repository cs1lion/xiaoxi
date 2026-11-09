# -*- coding: utf-8 -*-
"""#39 生产库哨兵：测试进程打开仓库 data/ 下的库必须被拦。

2026-10-24 清理真实台账时发现 379 条测试样式委托打进了 data/commissions.db；
约定（各套件自己用 tempdir）拦不住，所以把隔离做成机械保证：
`TEST_SENTINEL=1` 的进程在**打开数据库**时，若目标是仓库 data/ 下的文件就报错。

检查放在打开点而不是 import 点——静态检查器（check_protocol 等）只 import
配置、从不建连接，import 即抛会误伤它们。

本套件自己**不许写仓库 data/**：2026-11-09 发现旧版第一条检查用无哨兵进程
真的打开 `data/memory.db`，SCHEMA_VERSION 4→5 之后每跑一次全量就把真实库写成
v5（旧 8000 进程随即从「500」变成「schema v5 newer than v4」硬拒绝），而门禁
照样报 PASS。现在改成两条互不重叠的检查：
  ① 纯函数调用 `guard_sqlite_path(真实路径)`，环境与路径都和线上一致，但
     不建立连接、不产生任何 I/O——证明「生产模式不被误伤」；
  ② 在临时目录里放一份真实库的副本，让生产模式走完整的 MemoryStore 构造与
     迁移，证明「哨兵不误伤」的同时迁移确实跑到了当前 SCHEMA_VERSION。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
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


# import 这个模块本身就会创建模块级单例 `store = MemoryStore()`
# （store.py 末尾），也就是「import 即打开默认库」。所以本文件自己**不能**
# import server.*——那会在父进程里直接对真实库开连接（实测：加了这行 import，
# 本门禁自己把 data/memory.db 从 v4 写成 v5，还报 PASS）。版本号改由子进程
# 打印出来。
MEMORY_OPEN = (
    "from server.memory.store import MemoryStore, SCHEMA_VERSION\n"
    "MemoryStore({path!r}).close()\n"
    "print('OPENED')\n"
    "print('VERSION', SCHEMA_VERSION)\n"
)
# 只问哨兵放不放行，不碰文件：这是「生产进程打开真实库」的可观测替身。
GUARD_ALLOW = (
    "from server.config import guard_sqlite_path\n"
    "guard_sqlite_path({path!r})\n"
    "print('ALLOWED')\n"
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

    real_memory = ROOT / "data" / "memory.db"

    # ① 生产模式 + 真实路径 + 不建连接：路径与环境都跟线上一致，但零 I/O。
    prod_guard = run({"TEST_SENTINEL": ""}, GUARD_ALLOW.format(path=str(real_memory)))
    check("生产进程（无 TEST_SENTINEL）放行真实库路径",
          prod_guard.returncode == 0 and "ALLOWED" in prod_guard.stdout,
          prod_guard.stderr[-200:])

    # ② 生产模式走完整构造与迁移，但目标换成临时副本——真实库一个字节都不碰。
    # 有真实库就复制它，让迁移跑在真实形状的数据上；干净机器上就地新建。
    # DB_PATH 必须一起钉到副本：`import server.memory.store` 会创建模块级单例，
    # 它会打开 settings.db_path（默认就是 data/memory.db）。
    prod_dir = Path(tempfile.mkdtemp(prefix="sentinel-prod-"))
    try:
        prod_db = prod_dir / "memory.db"
        if real_memory.is_file():
            shutil.copy2(real_memory, prod_db)
        prod = run({"TEST_SENTINEL": "", "DB_PATH": str(prod_db)},
                   MEMORY_OPEN.format(path=str(prod_db)))
        check("生产进程（无 TEST_SENTINEL）打开库副本不报错",
              prod.returncode == 0 and "OPENED" in prod.stdout, prod.stderr[-200:])
        # 断言强度不降反升：旧版只验「没报错」，这里再验迁移真的落到了当前版本。
        code_version = next((int(line.split()[1]) for line in prod.stdout.splitlines()
                             if line.startswith("VERSION ")), None)
        got_version = sqlite3.connect(str(prod_db)).execute("PRAGMA user_version").fetchone()[0]
        check("副本经生产模式打开后 schema 为当前版本",
              code_version is not None and got_version == code_version,
              f"user_version={got_version} != code={code_version}")
    finally:
        shutil.rmtree(prod_dir, ignore_errors=True)

    real = run({"TEST_SENTINEL": "1"}, MEMORY_OPEN.format(path=str(real_memory)))
    check("测试进程打开真实 memory.db 被拦",
          real.returncode != 0 and "真实数据目录" in real.stderr, real.stderr[-200:])

    # 「拦得住」要能落成可观测事实：被拦的那次连文件都不该被创建。
    # DB_PATH 也钉到这个不存在的路径，否则模块级单例先撞上 memory.db 就抛了，
    # 这条断言会「因为别的原因」变绿。
    probe = ROOT / "data" / f"sentinel-probe-{os.getpid()}.db"
    try:
        probe_run = run({"TEST_SENTINEL": "1", "DB_PATH": str(probe)},
                        MEMORY_OPEN.format(path=str(probe)))
        check("被拦时不会在建库位置留下文件",
              probe_run.returncode != 0 and not probe.exists(), probe_run.stderr[-200:])
    finally:
        if probe.exists():
            probe.unlink()

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
