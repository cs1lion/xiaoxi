# -*- coding: utf-8 -*-
"""打印一个 SQLite 文件的三个读数：user_version / mtime_ns / size。

为什么是这三个：
- user_version 是本次事故的实体（SCHEMA_VERSION 4→5 被门禁写进真实库）；
- mtime_ns 能抓到「内容没变但文件被重写」；
- size 能抓到「只加了一张空表」这类 user_version 不变的写入。
只读打开（mode=ro），不会自己改文件。

用法：
    python three_values.py <db 路径> [<db 路径> ...]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys


def read(path: str) -> dict:
    st = os.stat(path)
    conn = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    return {"path": os.path.abspath(path), "user_version": version,
            "mtime_ns": st.st_mtime_ns, "size": st.st_size}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    print(json.dumps([read(p) for p in argv], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
