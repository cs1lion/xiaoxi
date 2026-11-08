# 门禁写真实数据库：审计与修复（2026-11-09）

**分支**：`fix/db-sentinel-no-real-write`
**基线**：`34bc5a2`（`merge/memory-schema-guard`，把 `78b661a`「SCHEMA_VERSION 4→5 +
schema 过新拒绝」合入 `origin/master` `0bac4b9` 后的合并提交）。用 `git log -1 34bc5a2`
可复算；本分支尚未 push、未合 master。

## 问题

`scripts/test_db_sentinel.py` 旧版第一条检查是「生产进程（无 `TEST_SENTINEL`）打开
`ROOT/data/memory.db` 不报错」。它真的对仓库真实库开连接、真的让 store 迁移。

在基线（`SCHEMA_VERSION=5`）上实测：**门禁报 PASS，同时把 `data/memory.db` 的
`user_version` 从 4 写成 5**。而用户那台跑了 15 天的旧 8000 进程从此从「`SELECT *`
报错 → 500」升级成「schema v5 newer than v4」硬拒绝——一次「全绿」的全量跑会直接
弄坏在跑的服务。

## 修法（`scripts/test_db_sentinel.py`）

断言强度不降反升，拆成两条互不重叠的检查，外加一条「拦得住」的可观测断言：

| 检查 | 做法 | 与旧版的关系 |
| --- | --- | --- |
| ① 生产进程放行真实库路径 | 子进程 `TEST_SENTINEL=""` 调纯函数 `guard_sqlite_path("<ROOT>/data/memory.db")`，只问放不放行 | 替换旧版「真的打开」。**路径与环境都跟线上一致，但零 I/O**——不建连接，就不可能有写 |
| ② 生产进程打开库副本不报错 | 真实库若存在则 `shutil.copy2` 到 `tempfile.mkdtemp()`，子进程 `DB_PATH` 与显式路径都钉到副本，走完整 `MemoryStore` 构造与迁移 | 旧版只断言「没报错」；新版追加断言副本 `user_version == 子进程打印的 SCHEMA_VERSION`，迁移没真跑也红 |
| ③ 被拦时不会在建库位置留下文件 | `TEST_SENTINEL=1` + `DB_PATH=<data>/sentinel-probe-<pid>.db`（不存在的路径），断言非零退出且文件没被创建 | 新增。旧版只能证明「抛了异常」，证明不了「异常发生在写入之前」 |

为什么 ① 必须用真实路径而不能用副本：它要验的正是「真实路径 + 生产模式**不会**被
哨兵误伤」，路径一换，被测条件就变了。但它只调用守卫函数，不 `connect`，所以不存在
「用真实路径就会写」的问题——这也是不接受「用副本代替」的原因：副本能证明「别的路径
不被拦」，证不了「这条路径不被拦」。

本文件**不能 import `server.*`**：`server/memory/store.py` 末尾有模块级
`store = MemoryStore()`，import 即打开默认库。R1 阶段把这行 import 写进检查脚本后，
脚本自己在父进程里把真实库写成了 v5（见下「R1 教训」）。版本号改由子进程打印。

## 读数（同一份真实库副本，`data/memory.db`）

三值：`user_version` / `mtime_ns` / `size`，取法见 `three_values.py`。

### R0：改之前（旧脚本，基线树）

| 时点 | user_version | mtime_ns | size |
| --- | --- | --- | --- |
| 跑 `--only db_sentinel` 前 | 4 | 1794176142941779300 | 286720 |
| 跑之后 | **5** | **已变** | 286720 |

门禁结论：`PASS`。这就是事故：破坏性和绿灯同时发生。

### R1：中间态（已改成临时副本，但脚本 import 了 `SCHEMA_VERSION`）

| 时点 | user_version | mtime_ns | size |
| --- | --- | --- | --- |
| 跑之前 | 4 | 1794176142941779300 | 286720 |
| 跑之后 | **5** | **已变** | 286720 |

教训：`import server.memory.store` 本身就会建单例并打开默认库。只要在父进程里 import
任何会带出 store 的东西，「用临时副本」就形同虚设。

### R2：改之后（本分支）

| 时点 | user_version | mtime_ns | size |
| --- | --- | --- | --- |
| 全量跑之前 | 4 | 1794176142941779300 | 286720 |
| 全量跑之后 | 4 | 1794176142941779300 | 286720 |

（全量审计那一轮的 before/after 见 `readings-before.json` 与 `readings-after.json`，三个值逐个相同。）

## 哨兵仍拦得住（复证）

```powershell
$env:TEST_SENTINEL='1'
.\.venv\Scripts\python.exe -c "from server.memory.store import MemoryStore; MemoryStore(r'data\memory.db')"
```

输出（`sentinel-blocked-proof.txt`）：

```
RuntimeError: 测试进程的数据库路径指向真实数据目录：...\data\memory.db。
测试必须用临时库（tempfile / :memory:），见 issue #39。
exit=1
```

前后三值完全一致（4 / 1794176142941779300 / 286720），说明拦截发生在任何写入之前。
门禁内的 ③ 号检查把这套判据固化了。

## 全量审计：还有谁在写 `data/`

### 方法（可复跑）

1. `sitecustomize.py`：解释器启动即被 `site` 加载。它包三层：
   - `sqlite3.connect`：记录 `data/` 下的打开，并用 authorizer 抓语句旁证，
     进程退出时用 `mtime_ns + size` 的 stat 差判定「是否真的写了」；
   - `builtins.open` **和** `io.open`：记录 `data/` 下的写模式打开
     （`Path.write_text` 走 `io.open`，只包 `builtins` 会漏掉整整一类）；
   - 任何异常都吞掉，钩子绝不会把被测门禁搞红。
2. `run_audited_gates.py`：逐条调用 `run_gates.run_gate()`，调用前注入
   `DB_AUDIT_GATE=<门禁名>`。这样连门禁的孙子进程（drill → uvicorn →
   sandbox_worker）也能归属到具体门禁，不用靠 argv 猜。
3. `summarize_audit.py`：把 jsonl 汇总成「门禁 → 路径 → 是否写」。

```powershell
$ev = "<repo>/docs/evidence/20261109-db-sentinel-no-real-write"
$env:PYTHONPATH = $ev
$env:DB_AUDIT_ROOT = (Get-Location).Path
$env:DB_AUDIT_LOG = "$ev/audit-full.jsonl"
.\.venv\Scripts\python.exe "$ev/run_audited_gates.py"
.\.venv\Scripts\python.exe "$ev/summarize_audit.py" "$ev/audit-full.jsonl" (Get-Location).Path
```

### 结果

全量：**214 跑过并断言 / 0 失败 / 1 跳过**（`procedural_memory_eval`，环境不满足），
耗时 792.2s，见 `gates-audited.log`。本轮 `gate_tmp_leak` 通过——本 worktree 里手动
建了 `tmp/`；干净检出缺 `tmp/` 时它仍会 FAIL，那是另一个任务
（`fix/gates-honest-skips`：依赖非 git 产物的门禁应 SKIP）的范围，不在本次改动内。

| 门禁 | 打开 / 写入的真实路径 | 是否写 | 依据 |
| --- | --- | --- | --- |
| gate_tmp_leak（内嵌抽跑 drill） | `data/commissions/c_720f…/out/verify_report.json`<br>`data/commissions/c_e929…/out/verify_report.json` | 写 | `w` 模式写文件 |
| step8_e2e_browser | `data/commissions/c_b359…/out/verify_report.json` | 写 | `w` 模式写文件 |
| drill_ws_reconnect | `data/commissions/c_643a…/out/verify_report.json` | 写 | `w` 模式写文件 |

四条写入的调用栈完全相同，都落在测试替身 worker 上：

```
sandbox_worker.py:142:<module> | sandbox_worker.py:138:main
  | sandbox_worker.py:91:run | pathlib.py:1047:write_text
```

**没有**任何门禁再打开 `data/` 下的 SQLite 文件（`audit-full.jsonl` 里 sqlite 事件为 0）。

### 同时修掉的两条（审计发现的真实库写入）

| 门禁 | 改前 | 改后 |
| --- | --- | --- |
| `core` | `Dispatcher(commissions=...)` 不传 evaluator → 自建 `Evaluator` → `EvalRubricStore("data/executive.db")`，**创建/改写真实执行库** | 传 `Evaluator(path=":memory:")`，审计零命中 |
| `contract_ledger_rules` | 同上（干净环境中同样会建 `data/executive.db`） | 同上 |
| `agent_loop` | `tempfile.TemporaryDirectory(dir=<repo>/data)`，在真实数据目录里落 `tmp*/runtime.db`（用例结束删掉，但依然是往 data/ 写） | 改到系统临时目录，`prefix="agent-loop-"` |

`Evaluator` 的默认路径 `"data/executive.db"` 是**相对路径**，按进程 cwd（门禁里就是仓库
根）解析——这和 `test_db_sentinel` 是同一个病根：真实路径写死在一个「看起来像临时」的
默认值里。

### 残留发现（本次不改，需要拍板）

`data/commissions/**/out/verify_report.json` 的三条写入来自
`scripts/worker_shims/sandbox_worker.py:58-64`：

```python
root = Path(os.environ.get("AQI_WORKSPACE_ROOT") or (ROOT / "data" / "commissions"))
```

roster 里给的是相对值 `AQI_WORKSPACE_ROOT: "data/commissions"`（`config/agents_roster.yaml`
四处），相对路径按**进程 cwd** 解析，门禁里 cwd 就是仓库根 → 落进真实 `data/`。

不能只改 shim：server 侧 `durable._workspace()` 用同一份 `workspace_root` 独立计算
回读目录，两处不一致会让 `artifact_json` 验收找不到文件。要改就得同时改
`server/assistant/adapters/cli_subproc.py`（下发给 shim 的 env）与
`durable.py::_workspace`（server 侧回读），把相对根在 `TEST_SENTINEL=1` 时改挂到
`_service_data_dir()`。这动的是生产适配器的路径解析语义（即使只在哨兵下生效），
所以留作独立决策，不在本次「审计 + 哨兵修复」里顺手改。

## 复跑（一条命令一条门禁的完整流程）

见上「方法」小节。产物：

- `audit-full.jsonl`：原始事件（每行一个 open / write / file_write）
- `gates-audited.log`：逐门禁 PASS/FAIL/SKIP 与耗时
- `readings-before.json` / `readings-after.json`：全量前后三值
- `red-r0.txt`：旧脚本在副本上的破坏性证据（4→5 且 6 passed）
- `green-r2.txt`：新脚本 9 条检查全过、副本三值不变
- `sentinel-blocked-proof.txt`：哨兵拦截复证

## 已知边界

- Node 侧不经过 Python 的 `sitecustomize`，但 `scripts/*.mjs` 里没有任何 sqlite 调用
  （`grep -n "sqlite" scripts/*.mjs` 为空），DB 写入只可能来自 Python 侧。
- 进程被强杀时 `atexit` 不执行，stat 差判据会漏一条；open/file_write 记录仍在。

