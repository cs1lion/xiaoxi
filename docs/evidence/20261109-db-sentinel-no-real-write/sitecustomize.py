# -*- coding: utf-8 -*-
"""生产数据写入审计钩子：包一层 sqlite3.connect，记录谁打开了仓库 data/ 下的文件。

用法（PowerShell）：
    $env:PYTHONPATH = "<本目录>"
    $env:DB_AUDIT_LOG = "<输出 jsonl>"
    $env:DB_AUDIT_ROOT = "<仓库根>"
    <venv>\\Scripts\\python.exe scripts\\run_gates.py --allow-skip
    <venv>\\Scripts\\python.exe docs\\evidence\\...\\summarize_audit.py <输出 jsonl> <仓库根>

设计要点：
- 这个文件叫 `sitecustomize`，解释器启动时由 `site` 自动 import；放进
  PYTHONPATH 就会被**每个** python 子进程加载（run_gates 用 `env={**os.environ}`
  传环境，所以门禁子进程也带上）。
- 只记录 `DB_AUDIT_ROOT/data/` 下的目标——临时目录、:memory: 一律不记。
- 「是否写」以**文件 stat 差**为准（连接建立时快照 mtime_ns+size，进程退出时
  再快照一次），authorizer 捕获的语句类别只作旁证。为什么不用 authorizer 当
  主判据：SQLite 在 `CREATE TABLE IF NOT EXISTS` / `PRAGMA table_info(x)` 这类
  不落盘的操作上也会回调，单看语句类别会把空转记成写（实测误报）。
- 任何异常都吞掉：审计钩子绝不能把被测门禁搞红。
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import traceback

_LOG = os.getenv("DB_AUDIT_LOG") or ""
_GATE = os.getenv("DB_AUDIT_GATE") or ""  # 由 run_audited_gates.py 逐门禁注入
_DATA_DIR = os.path.abspath(os.path.join(os.getenv("DB_AUDIT_ROOT") or ".", "data"))

# authorizer 动作码：只用来给「确实改了文件」的进程补一条人类可读的旁证。
_WRITE_ACTIONS = {
    1: "CREATE_INDEX", 2: "CREATE_TABLE", 3: "CREATE_TEMP_INDEX",
    4: "CREATE_TEMP_TABLE", 5: "CREATE_TEMP_TRIGGER", 6: "CREATE_TEMP_VIEW",
    7: "CREATE_TRIGGER", 8: "CREATE_VIEW", 9: "DELETE",
    10: "DROP_INDEX", 11: "DROP_TABLE", 12: "DROP_TEMP_INDEX",
    13: "DROP_TEMP_TABLE", 14: "DROP_TEMP_TRIGGER", 15: "DROP_TEMP_VIEW",
    16: "DROP_TRIGGER", 17: "DROP_VIEW", 18: "INSERT",
    23: "UPDATE", 26: "ALTER_TABLE", 27: "REINDEX", 28: "ANALYZE",
}
# 真正会改库文件头的 PRAGMA；`PRAGMA foreign_keys=ON` 之类只影响连接。
_WRITE_PRAGMAS = {"user_version", "application_id", "schema_version", "journal_mode"}


def _resolve(database) -> str:
    if isinstance(database, bytes):
        database = database.decode("utf-8", "replace")
    if not database or not isinstance(database, str):
        return ""
    raw = database
    if raw == ":memory:" or raw.startswith("file::memory:"):
        return ""
    if raw.startswith("file:"):
        raw = raw[5:].split("?", 1)[0]
    return os.path.abspath(raw)


def _under_data(path: str) -> bool:
    return path.startswith(_DATA_DIR + os.sep)


def _snapshot(path: str):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def _stack() -> str:
    frames = [f for f in traceback.extract_stack()[:-1] if "sitecustomize" not in f.filename]
    return " | ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in frames[-6:])


def _log(row: dict) -> None:
    try:
        with open(_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


_TRACKED: list[dict] = []


def _install() -> None:
    import builtins
    import io
    import sqlite3

    original = sqlite3.connect

    def connect(database=":memory:", *args, **kwargs):
        base = None
        try:
            path = _resolve(database)
            if path and _under_data(path):
                base = {"gate": _GATE, "pid": os.getpid(), "ppid": os.getppid(),
                        "argv": sys.argv[1:] or [sys.argv[0]], "cwd": os.getcwd(),
                        "target": path, "existed_before": os.path.exists(path),
                        "before": _snapshot(path), "actions": [], "stack": _stack()}
                _TRACKED.append(base)
                _log({**base, "event": "open"})
        except BaseException:
            base = None
        conn = original(database, *args, **kwargs)
        if base is not None:
            try:
                def authorizer(action, arg1, arg2, _dbname, _source):
                    try:
                        if action == 19:  # PRAGMA
                            if arg1 in _WRITE_PRAGMAS and arg2:
                                base["actions"].append(f"PRAGMA({arg1}={arg2})")
                        else:
                            name = _WRITE_ACTIONS.get(action)
                            if name:
                                base["actions"].append(name)
                    except BaseException:
                        pass
                    return 0  # SQLITE_OK

                conn.set_authorizer(authorizer)
            except BaseException:
                pass
        return conn

    sqlite3.connect = connect
    if hasattr(sqlite3, "dbapi2"):
        sqlite3.dbapi2.connect = connect

    # 非 SQLite 的落盘同样算「写真实数据」（实测：drill_ws_reconnect 会把
    # data/commissions/<id>-<stamp>/out/*.json 写进仓库）。只记 data/ 下的写模式。
    original_open = builtins.open

    def hooked_open(file, mode="r", *args, **kwargs):
        try:
            if isinstance(file, (str, bytes, os.PathLike)):
                path = os.path.abspath(os.fspath(file))
                if _under_data(path) and any(ch in str(mode) for ch in "wax+"):
                    _log({"event": "file_write", "gate": _GATE, "pid": os.getpid(),
                          "argv": sys.argv[1:] or [sys.argv[0]], "cwd": os.getcwd(),
                          "target": path, "mode": str(mode), "stack": _stack()})
        except BaseException:
            pass
        return original_open(file, mode, *args, **kwargs)

    builtins.open = hooked_open
    # pathlib 的 Path.write_text 走的是 io.open，不是 builtins.open——只包
    # builtins 会漏掉整整一类写入（实测 sandbox_worker 就是这条路径）。
    io.open = hooked_open

    def flush() -> None:
        for row in _TRACKED:
            after = _snapshot(row["target"])
            if after != row["before"]:
                actions = sorted(set(row["actions"]))
                _log({"event": "write", "gate": row.get("gate"), "pid": row["pid"],
                      "argv": row["argv"], "cwd": row["cwd"], "target": row["target"],
                      "before": row["before"], "after": after,
                      "actions": actions[:12], "stack": row["stack"]})

    atexit.register(flush)


if _LOG:
    try:
        _install()
    except BaseException:
        pass
