"""模块级记忆单例的惰性化门禁：import 不得建库，显式取单例才建库。

    python scripts/test_store_import_is_inert.py

背景：`server/memory/store.py` 曾有模块级 `store = MemoryStore()`，任何人
`import server.memory.store`（哪怕只拿 `Memory` / `SCHEMA_VERSION`）都会打开并迁移
真实 `data/memory.db`。`manager` / `retriever` 两个派生单例同理。

这条门禁全部在**子进程**里跑，父进程不 import `server.*`：
① 无哨兵，`DB_PATH` 指向不存在的临时库；
② 必须覆盖 `import server.memory.store`、`from ... import Memory, SCHEMA_VERSION`、
   `import server.memory.manager`、`import server.memory.retriever` 四种形态；
③ 显式取 `store` / `manager` / `retriever` **才**允许建库，且 `user_version` 落到
   `SCHEMA_VERSION`（证明惰性没把建库/迁移弄丢）；
④ 反向用例：`TEST_SENTINEL=1` + 仓库 `data/` 路径时，import 仍须无副作用，
   显式取单例仍必须报错（哨兵不因惰性失效）。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISOLATED = Path(tempfile.mkdtemp(prefix="store-inert-"))

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def run(code: str, *, db: Path, sentinel: bool = False) -> subprocess.CompletedProcess:
    """在子进程里执行；默认剥掉宿主可能残留的 TEST_SENTINEL。"""
    env = {**os.environ}
    env.pop("TEST_SENTINEL", None)
    env["PYTHONIOENCODING"] = "utf-8"
    if sentinel:
        env["TEST_SENTINEL"] = "1"
    env["DB_PATH"] = str(db)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180, env=env,
    )


def version_of(db: Path) -> int:
    connection = sqlite3.connect(db)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def must_be_inert(label: str, code: str, db: Path) -> None:
    result = run(code, db=db)
    check(f"{label} 退出 0", result.returncode == 0,
          (result.stderr or result.stdout).strip()[-300:])
    check(f"{label} 没有建库（{db.name} 不存在）", not db.exists())


def main() -> int:
    print("[store import is inert]")

    # ① 常量：拿 Memory / SCHEMA_VERSION 不该碰库，并能读到正确版本号。
    const_db = ISOLATED / "constants.db"
    constants = run("from server.memory.store import Memory, SCHEMA_VERSION\n"
                    "print(SCHEMA_VERSION)", db=const_db)
    check("取常量退出 0", constants.returncode == 0,
          (constants.stderr or constants.stdout).strip()[-300:])
    check("取常量没有建库", not const_db.exists())
    expected = int((constants.stdout or "0").strip() or 0)

    # ② bare import：四种最容易漏的 import 形态都不能建库。
    must_be_inert("bare import store 模块", "import server.memory.store", ISOLATED / "bare.db")
    must_be_inert("import manager 模块", "import server.memory.manager", ISOLATED / "manager.db")
    must_be_inert("import retriever 模块", "import server.memory.retriever", ISOLATED / "retriever.db")

    # ③ 显式取单例才建库，且版本号必须落到 SCHEMA_VERSION。
    store_db = ISOLATED / "store-explicit.db"
    explicit = run(
        "from server.memory.store import store as a\n"
        "from server.memory.store import store as b\n"
        "print(a is b, type(a).__name__)", db=store_db)
    check("from ... import store 退出 0", explicit.returncode == 0,
          (explicit.stderr or explicit.stdout).strip()[-300:])
    check("显式取 store 才建库", store_db.exists())
    check("两次取到同一个单例", (explicit.stdout or "").split(" ")[:1] == ["True"],
          (explicit.stdout or "").strip())
    check("store 是 MemoryStore", (explicit.stdout or "").strip().endswith("MemoryStore"))
    if store_db.exists():
        check("store 库版本 = SCHEMA_VERSION", version_of(store_db) == expected,
              f"{version_of(store_db)} != {expected}")

    manager_db = ISOLATED / "manager-explicit.db"
    manager = run("from server.memory.manager import manager\n"
                  "print(type(manager).__name__)", db=manager_db)
    check("from ... import manager 退出 0", manager.returncode == 0,
          (manager.stderr or manager.stdout).strip()[-300:])
    check("显式取 manager 才建库", manager_db.exists())
    check("manager 库版本 = SCHEMA_VERSION",
          version_of(manager_db) == expected if manager_db.exists() else False)

    retriever_db = ISOLATED / "retriever-explicit.db"
    retriever = run("from server.memory.retriever import retriever\n"
                    "print(type(retriever).__name__)", db=retriever_db)
    check("from ... import retriever 退出 0", retriever.returncode == 0,
          (retriever.stderr or retriever.stdout).strip()[-300:])
    check("显式取 retriever 才建库", retriever_db.exists())

    # ④ 哨兵反向用例：import 无副作用，显式取单例必须报错，且不落文件。
    guarded = ROOT / "data" / "__store_import_probe.db"
    guarded_db = guarded
    result = run("import server.memory.store", db=guarded_db, sentinel=True)
    check("哨兵下 import store 不报错", result.returncode == 0,
          (result.stderr or result.stdout).strip()[-300:])
    check("哨兵下 import store 不落文件", not guarded_db.exists())

    result = run("from server.memory.store import store", db=guarded_db, sentinel=True)
    check("哨兵下显式取 store 必须报错", result.returncode != 0,
          (result.stdout or "").strip()[-200:])
    check("哨兵报错指向真实数据目录", "真实数据目录" in (result.stderr or ""),
          (result.stderr or "").strip()[-200:])
    check("哨兵拦下后没有落文件", not guarded_db.exists())

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
