# 惰性记忆单例（方案 A 落地）

日期：2026-11-09 ｜ 基线：`fix/db-sentinel-no-real-write` @ `3895ef0`（工作区根修复之上）
方案：`docs/evidence/20261109-memory-store-singleton/store-plan.md` §3-A

## 1. 改了什么

| 文件 | 变化 |
| --- | --- |
| `server/memory/store.py:1720` | `store = MemoryStore()` → `get_store()` + 模块 `__getattr__("store")`（PEP 562，双检锁） |
| `server/memory/manager.py:17/57/388` | 顶层 `store as default_store` → `from . import store as store_module`，构造时取 `store_module.store`；`manager = MemoryManager()` → `get_manager()` + `__getattr__("manager")` |
| `server/memory/retriever.py:17/76/426` | 同上 → `get_retriever()` + `__getattr__("retriever")` |
| `scripts/test_store_import_is_inert.py` | 新门禁（`store_import_inert`），全部在子进程跑 |
| `scripts/run_gates.py:938` | 注册 `store_import_inert` |

三个单例共享一条惰性链：`manager` 构造 → `.store` 的 `__getattr__` → `get_store()`；
`retriever` 同理。

## 2. 为什么必须用 `store_module.store` 而不是 `get_store()`

`scripts/test_memory_fastloop.py:96-99` 的既有手法是

```python
import server.memory.store as store_module
store_module.store = MemoryStore(":memory:")   # 模块字典赋值
```

模块字典赋值优先于 `__getattr__`，所以 `store_module.store` 一定拿到补丁值；
而 `get_store()` 看的是 `_default_store` 缓存，会绕过补丁。manager/retriever 的构造
因此写成 `store or store_module.store`，补丁语义与改前完全一致。`from .store import store`
（`diary.py:59`、`evolution.py:89`、`loop.py:805`）也在函数内取值，同样落在模块字典。

## 3. 读数（先红后绿）

- 改前（当前树直接跑）：`=== 18 passed, 5 failed ===`，5 条失败全是同一语义：
  「import 就建库」（常量 / bare import store / import manager / import retriever），
  外加「哨兵下 import store 不报错」——eager 单例在哨兵下直接抛。
  原始输出 `red-before.txt`。
- 改后：`=== 23 passed, 0 failed ===`，`green-after.txt`。
- 覆盖的 import 形态：`import server.memory.store`、
  `from server.memory.store import Memory, SCHEMA_VERSION`、**`from server.memory.store
  import store`**（用户点名的最易漏形态）、`import server.memory.manager`、
  `import server.memory.retriever`；显式取三者都建库、`user_version == SCHEMA_VERSION`，
  `from ... import store` 两次拿到同一实例。
- 哨兵反向：`TEST_SENTINEL=1` + 仓库 `data/__store_import_probe.db` 时 import 无副作用，
  显式取单例仍抛「真实数据目录」，且不落文件。

## 4. 全量与真实库

两次全量（`--allow-skip`，与上一轮同口径）：

| 文件 | 三元组 | 说明 |
| --- | --- | --- |
| `gates-full-after.txt` | 215 过 / 1 失败 / 1 跳过 | 唯一失败是 `ui_browser`：`page.reload()` 撞 `ERR_CONNECTION_REFUSED`（它自起的 8022 沙盘中途没了，14.9s）。同命令单独复跑 `[PASS] ui_browser exit=0 30.9s`，属环境抖动，非本次改动 |
| `gates-full-final.txt` | **216 过 / 0 失败 / 1 跳过** | 跳过的是 `procedural_memory_eval`（环境不满足），exit=0；本轮新增 `store_import_inert` PASS 8.2s、`durable_supervisor` PASS 11.1s |

- `data/memory.db` 三值：全量前后均为
  `{"user_version": 4, "mtime_ns": 1794176142941779300, "size": 286720}`
  （只读 URI 打开，不写）。
- `data/commissions`：主仓 457 项、worktree 2 项（先前红灯样本），全量前后不变。

## 5. 已知边界（未改）

- `server/app.py:34` 仍是顶层 `from .memory.store import store`：服务进程本来就该建库，
  生产路径零变化（方案 §3-A 已写明）。要连它也惰性，把那行挪进使用点即可，1 处。
- `orchestrator.py:292` 顶层 `from ..memory.manager import manager` 同样按需 eager：
  导入编排器就建库，属调用方显式使用，不是本门禁的判据。
- 未加 `reset_default_store()`：现有测试只用模块字典赋值，未发现需要重置的路径。
  若将来有测试先 `get_store()` 再打补丁，需要补一个重置钩子。
