# -*- coding: utf-8 -*-
"""#39 生产库哨兵：测试进程把 DB_PATH 指向仓库 data/ 时必须启动即失败。

2026-10-24 清理真实台账时发现 379 条测试样式委托打进了 data/commissions.db；
约定（各套件自己用 tempdir）拦不住，所以把隔离做成机械保证。
"""
from __future__ import annotations

import os
import subprocess
import sys
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


def run(env_extra: dict[str, str]) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_extra}
    return subprocess.run([PY, "-c", "from server.config import DEFAULTS; print(DEFAULTS['db_path'])"],
                          cwd=str(ROOT), env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> int:
    print("[生产库哨兵]")
    prod = run({})
    check("生产进程（无 TEST_SENTINEL）读真实库不报错",
          prod.returncode == 0 and "memory.db" in prod.stdout, prod.stderr[-200:])

    real = str(ROOT / "data" / "memory.db")
    guarded = run({"TEST_SENTINEL": "1", "DB_PATH": real})
    check("测试进程指真实 memory.db 启动即失败",
          guarded.returncode != 0 and "指向真实数据目录" in guarded.stderr, guarded.stderr[-200:])

    nested = str(ROOT / "data" / "sub" / "x.db")
    guarded_nested = run({"TEST_SENTINEL": "1", "DB_PATH": nested})
    check("data/ 子目录同样被拦（不只挡 memory.db 一个文件）",
          guarded_nested.returncode != 0, guarded_nested.stderr[-200:])

    temp = run({"TEST_SENTINEL": "1", "DB_PATH": ":memory:"})
    check("测试进程用 :memory: 正常", temp.returncode == 0 and ":memory:" in temp.stdout,
          temp.stderr[-200:])

    import tempfile
    scratch = str(Path(tempfile.mkdtemp(prefix="sentinel-")) / "m.db")
    temp_file = run({"TEST_SENTINEL": "1", "DB_PATH": scratch})
    check("测试进程用临时文件正常", temp_file.returncode == 0, temp_file.stderr[-200:])

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
