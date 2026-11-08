# 记忆库 schema 新于代码：可读诊断（2026-11-09）

**分支**：`fix/memory-schema-newer-guard`（基于 `origin/master` @ `0bac4b9`，未 push）
**问题**：旧进程（`249a071`，2026-10-25）对已被 `0f87efa`（2026-10-29）加列
`fact_valid_from` / `fact_valid_until` 的 `data/memory.db` 执行 `SELECT *` +
`Memory(**dict(row))`，`GET /api/memories` 与 `GET /api/memories/context` 一律 500。

## 新规矩（取代旧惯例）

> **`memories` 行形状变化必须升 `SCHEMA_VERSION`，包括纯加列；只有纯加法表（旧代码
> 从不 SELECT）才免升版。**
>
> 旧惯例「加列不升 SCHEMA_VERSION」正是本次事故根因：旧代码的版本守卫看到 v4 == v4
> 直接放行，随后 `SELECT *` + `Memory(**row)` 撞上未知列变成 500。下次要复用旧惯例时，
> 先看本条与 `server/memory/store.py` 的 `SCHEMA_VERSION` 注释。

## 归因（实测）

- 真实库只读探测：`PRAGMA user_version = 4`，`memories` 32 列，含 `fact_valid_*`，3 行。
- `249a071` 的 `server/memory/store.py` 同样是 `SCHEMA_VERSION = 4`，但 `Memory`
  dataclass 没有 `fact_valid_*`；`list_memories` 用 `Memory(**dict(row))`。
- 因此现有 `existing > SCHEMA_VERSION` 守卫不会触发：版本号相同，行形状已经变了。
  **根因是「加列不升版」的惯例，不是守卫写错。**

## 改前 / 改后（同一临时副本，源库只读复制，未改真实 `data/memory.db`）

| 场景 | 命令 | 改前（master `0bac4b9`） | 改后（本分支） |
|---|---|---|---|
| 旧代码 + 真实库副本（v4，含新列） | 见下 | `TypeError: Memory.__init__() got an unexpected keyword argument 'fact_valid_from'` | 旧代码仍 TypeError（**进程已运行时无法被新代码修复**）；但该副本被新代码打开一次后 `user_version=5` |
| 新代码 + 真实库副本（v4，含新列） | 见下 | `list ok 3`（新版 Memory 认识该列） | `opened, user_version 5`；`list ok 3` |
| 旧代码（v4）+ 新代码升级后的库（v5） | 见下 | — | `RuntimeError: memory database schema v5 is newer than supported v4` |
| 新代码 + 未来库（v5 + 未知列 `later_code_added`） | 见下 | `TypeError: ... unexpected keyword argument`（列检查不存在） | `RuntimeError: memory database schema (user_version v5) has columns this code does not know: later_code_added; it is newer than this code (v5). Upgrade the code or restore an older database copy.` |
| 运行中的 store，库被外部加列 | `test_running_store_reports_unknown_columns_instead_of_typeerror` | `TypeError` | `RuntimeError`，消息含列名与升级/回滚指引 |

复现命令（PowerShell，`<WT>` 为 worktree 路径）：

```powershell
Copy-Item data\memory.db tmp\schema-repro\before.db   # 源库只读复制
# 旧代码（249a071 worktree）：
python -c "from server.memory.store import MemoryStore; MemoryStore(r'...\before.db').list_memories(status=None)"
# 新代码（本 worktree）：
python -c "from server.memory.store import MemoryStore, SCHEMA_VERSION; s=MemoryStore(r'...\copy.db'); print(s._query('PRAGMA user_version')[0][0], len(s.list_memories(status=None)))"
```

## 先红后绿

- 改前红：`python -B scripts/test_memory_store_contract.py` → `FAILED (failures=6)`：
  `AssertionError: 4 != 5`（4 处版本断言）与 `AssertionError: RuntimeError not raised`（2 处新用例）。
- 改后绿：同上 → `Ran 37 tests ... OK`，exit 0。

## 黄金命令（本 worktree，`TEST_SENTINEL=1`）

| 命令 | 改前 | 改后 |
|---|---|---|
| `python scripts/check_protocol.py` | PASS，exit 0 | PASS，exit 0 |
| `python scripts/test_core.py` | 134 passed, 0 failed | 134 passed, 0 failed |
| `npm test` | exit 0 | exit 0 |
| `python scripts/run_evals.py` | 18 passed, 0 failed | 18 passed, 0 failed |

附加：`test_episodic_diary.py` 61、`test_memory_manage.py` 12、`test_memory_api.py` 7、
`test_memory_snapshot.py` 10、`test_durable_read_api.py` 14，全部 OK。

## 仍未验证 / 边界

- 已在运行的旧进程（用户当前 8000）**不会被本改动修复**：它加载的是旧字节码。
  需要重启（用户红线，未执行）才能加载新代码并给出启动期诊断。
- 只覆盖 `memories` 行形状。其他表以 dict 读取，多列不会炸；`assistant` 库有独立
  `SCHEMA_VERSION`，不在本次范围。
- 未知列检查在**启动时**覆盖全表（不依赖有行）；运行期 `_memory_from_row` 只在
  真正返回行时触发，空表不会报（启动守卫已兜住）。
- live 评测未跑（`LLM_API_KEY` 未设置）；本改动不涉及模型 / Prompt。
