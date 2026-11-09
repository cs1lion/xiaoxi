# 工作区根绝对化：三条门禁不再写真实 `data/commissions/`

日期 2026-11-09 ｜ 分支 `fix/db-sentinel-no-real-write` ｜ 基线 `39198ea`
（上一轮 `docs/evidence/20261109-db-sentinel-no-real-write/` 的遗留项）

## 1. 问题

上一轮全量审计确认，跑全量门禁会在**真实** `data/commissions/<id>/out/verify_report.json`
里留下单子目录，来源三条：

| 门禁 | 泄漏路径（修复前实测） |
| --- | --- |
| `gate_tmp_leak`（内嵌抽跑 drill） | `data/commissions/c_720f…/out/verify_report.json`、`c_e929…` |
| `step8_e2e_browser` | `data/commissions/c_b359…/out/verify_report.json` |
| `drill_ws_reconnect` | `data/commissions/c_643a…/out/verify_report.json` |

根因不是门禁没做隔离，而是**相对路径的解析基准是进程 cwd**：

1. `config/agents_roster.yaml` 里 5 个 Worker 的 `AQI_WORKSPACE_ROOT: "data/commissions"`
   （以及 durable 段的 `workspace_root: data/commissions`）是**相对值**；
2. `cli_subproc.prepare_context` 把花名册 env 原样塞进子进程；
3. shim（`scripts/worker_shims/*_worker.py`）`Path(root).resolve()` 按**子进程 cwd** 解析；
4. `durable._workspace()` 在服务进程里同样按 cwd 解析。

门禁进程的 cwd 恰好是仓库根 → 布局落在仓库里。更隐蔽的后果：两侧 cwd 一旦不同，
**产物写在一处、验收按另一处回读**（`artifact_json` 判文件不存在），表现为"执行体没产出"。

## 2. 修法（生产语义改动，非测试补丁）

| 文件 | 改动 |
| --- | --- |
| `server/config.py` | 新增 `repository_root()`（`__file__` 锚）与 `service_data_dir()`（从 `app.py` 上提）。基准的唯一出处，避免 config ← app 成环 |
| `server/app.py` | `_service_data_dir()` 改成薄包装（既有调用与测试不变）；`switch_agents` 的 `workspace_root` 默认值 `data/commissions` → `commissions` |
| `server/assistant/adapters/workspace.py`（新） | `resolve_workspace_root(configured, *, default, allow_env)`：显式绝对路径原样采纳；相对值一律 `service_data_dir()/value`；**不保留 cwd 语义** |
| `server/assistant/adapters/cli_subproc.py` | 下发前把 `AQI_WORKSPACE_ROOT` 换成解析后的**绝对路径** |
| `server/assistant/adapters/durable.py` | `_workspace()` 用同一解析器（未配置仍返回 `""`）；`_command()` 把 worker env 里的根也绝对化，保证 sidecar 里的 shim 与沙箱目录同根 |
| `config/agents_roster.yaml` | 5 处 `AQI_WORKSPACE_ROOT` + durable `workspace_root` → `commissions`（若保留 `data/commissions` 会拼成 `data/data/commissions`） |
| `scripts/doctor_integration.py` | `resolve_workspace()` 改用同一解析器，体检结论与运行时同口径 |
| `scripts/probe_live_workers.py` | 探针里写盘用的字面值同步为 `commissions` |
| `docs/AGENT_INTEGRATION_GUIDE.md` | 目录示例与配置片段同步；写明"基准是服务数据目录，不是 cwd" |
| `docs/TESTING.md` | `_service_data_dir` 上提让 app.py 前移 19 行，按 `ast` 实测重锚 5 个函数行号（`doc_drift` 门禁逼出来的） |
| `scripts/run_gates.py` | 注册新门禁 `workspace_root` |

**解析基准固定为 `config.service_data_dir()`**：生产 = 仓库 `data/`；`TEST_SENTINEL=1`
的进程依次跟随已 patch 的 `DATA_DIR` → `APP_DATA_DIR` → `DB_PATH` 同根 → 进程临时目录。
所以同一份花名册相对值 `commissions` 在生产落到 `data/commissions`、在门禁里落到临时根，
**都不再依赖 cwd**。优先级：花名册配置 > 进程 `AQI_WORKSPACE_ROOT` > 默认 `commissions`
（配置优先是为了保住逐 Worker 隔离，durable 的 `workspace_root` 单条覆盖全局）。
durable 的"花名册留空 = 不许写文件"不被宿主 shell 里偶然 export 的
`AQI_WORKSPACE_ROOT` 顶开（`allow_env=False`）：明确关闭不该变成"看环境"。

## 3. 先红后绿

新门禁 `scripts/test_workspace_root.py`（28 条断言）钉的是契约，不是实现细节：

- 相对值解析成绝对路径、两个 cwd 下结果一致；
- 三层：默认值 `commissions` / 显式覆盖（适配器配置、进程 env、绝对路径原样采纳）/
  测试注入临时根（`TEST_SENTINEL` + `DB_PATH`）；
- `cli_subproc` 下发的值必须是绝对且与解析器一致；`durable._workspace()` 同源；未配置返回空；
- shim 真子进程：拿到绝对根就落在绝对根下、产物不在仓库 `data/`；
- **真实链路回读**：真实 `sandbox` 花名册条目（不改 env）+ `ExecutiveService.delegate` +
  `artifact_json verify_report.json` 验收 → `ready_to_report`，产物在
  `<服务数据目录>/commissions` 下、文件存在且含本单号、仓库 `data/commissions` 前后无变化；
- 无哨兵子进程：基准是 `<repo>/data`（且 cwd 不是仓库根）。

红灯（`red-before.txt`，当前树直接跑）：**12 通过 / 9 失败**，其中

```
FAIL 下发的根是绝对路径  ｜data/commissions
FAIL 产物落在 <服务数据目录>/commissions 下
     ｜D:\...\data\commissions\c_55e5d9f46fe7-...\out\verify_report.json
FAIL 真实 data/commissions 前后无变化  ｜前=1 后=2 新增=['c_55e5d9f46fe7-...']
FAIL 无哨兵时基准是仓库 data/（cwd 不是仓库根）  ｜AttributeError: service_data_dir
```

路灯（`green-after.txt`）：**28 通过 / 0 失败**。

> 红灯跑出的两个目录 `c_55e5d9f46fe7-20261109-075343`、`c_baaf7efa5412-20261109-075332`
> 留在 worktree 的 `data/commissions/` 里作证据（worktree 的 `data/` 是 gitignore 产物，
> 不是用户真实库；主仓 `d:\project\neurosama\data\` 全程未被本轮改动碰到）。

## 4. 三条门禁的前后读数（同口径）

`gates-after.txt`：每条门禁跑前/跑后各取一次 `data/commissions/` 顶层目录快照：

| 门禁 | 前 | 后 | 差 | 结果 |
| --- | --- | --- | --- | --- |
| `gate_tmp_leak` | 2 | 2 | 无 | PASS（130.3s） |
| `step8_e2e_browser` | 2 | 2 | 无 | PASS（12.5s） |
| `drill_ws_reconnect` | 2 | 2 | 无 | PASS（21.0s） |

（前/后都是红灯阶段留下的同样那 2 个目录，即修复后**一条都没有新增**。）

审计复核（`audit-after.jsonl` 不存在 / 0 行）：用上一轮的 `sitecustomize` 钩子
（包 `sqlite3.connect` / `builtins.open` / `io.open`，只记 `DB_AUDIT_ROOT/data/` 下）
逐门禁重跑三条，**没有任何一行落在 `data/`**。
钩子活性另证：`audit-hook-live.jsonl` 里有一条故意写 `data/audit-probe.tmp` 的记录
（随后删除），证明"空日志 = 真没写"，不是钩子没加载。

## 5. 验收回读仍成功（用户最担心的"改一半就坏"）

- 新门禁里的真实链路用例：`ready_to_report` + 产物文件可读 + 含本单号（见 §3）；
- 既有链路门禁全绿：`acceptance_dispatch`、`executive_v3`、`durable_adapter`、
  `durable_app_wiring`、`workbuddy_worker_shim`、`memory_manage`、`contract_ledger_rules`、
  `agent_loop`、`core`、`db_sentinel`（合计 11 条，`0 失败`）。

## 6. 全量三元组

见 `gates-full-final.txt`（`run_gates.py --allow-skip`，216 条，含新增 `workspace_root`）：

```
跑过并断言 : 214
失败       : 1  durable_supervisor   ← 外部账号：模型 400 no active step plan subscription（见 §9）
跳过       : 1  procedural_memory_eval
```

除上面这一条外部原因外全绿；与本次改动直接相关的门禁都是 PASS：
`workspace_root`、`gate_tmp_leak`、`step8_e2e_browser`、`drill_ws_reconnect`、
`cli_subproc_adapter`、`acceptance_dispatch`、`executive_v3`、`durable_adapter`、
`durable_app_wiring`、`doc_drift`、`memory_manage`、`db_sentinel`。

> 第一次全量（`gates-full-after.txt`，跑到 `doc_drift` 红了即中止重跑）抓到一个不是
> 本次语义引入、但确实由本次改动触发的红：`doc_drift` —— app.py 前移 19 行，
> `docs/TESTING.md` 里 5 个 `函数名` `:行号` 快照锚失配。已按 AST 实测重锚，
> `--only doc_drift` 复跑绿；`gates-full-final.txt` 是改完之后在最终树上重跑的全量。

## 7. 真实记忆库三值未变

`d:\project\neurosama\data\memory.db`（只读 URI 打开，`three_values.py`）：

```json
{"user_version": 4, "mtime_ns": 1794176142941779300, "size": 286720}
```

与上一轮读数逐项一致。主仓 `data/commissions/` 顶层目录数同为 457，无新增（见 §9）。

## 8. 复跑

```powershell
cd D:\project\neurosama-wt-dbsentinel
$env:TEST_SENTINEL = "1"
.\.venv\Scripts\python.exe -B scripts\test_workspace_root.py
.\.venv\Scripts\python.exe -B scripts\run_gates.py --only gate_tmp_leak,step8_e2e_browser,drill_ws_reconnect
.\.venv\Scripts\python.exe -B scripts\run_gates.py --allow-skip
```

审计复核（会写 0 行的 jsonl）：

```powershell
$ev = (Resolve-Path "docs\evidence\20261109-db-sentinel-no-real-write").Path
$env:PYTHONPATH = $ev
$env:DB_AUDIT_ROOT = (Get-Location).Path
$env:DB_AUDIT_LOG  = "$ev\..\20261109-workspace-root-abs\audit-after.jsonl"
.\.venv\Scripts\python.exe "$ev\run_audited_gates.py" --only gate_tmp_leak,step8_e2e_browser,drill_ws_reconnect
```

## 9. 残留与未覆盖

- **`durable_supervisor` 是"假离线"门禁，当前因账号原因红（与本轮改动无关）**。
  它的 docstring 写"零外部网络：只连 127.0.0.1"，但它会经 sidecar 调真实模型
  （worktree `.env` 里 `LLM_BASE_URL=https://api.stepfun.com/step_plan/v1`）。
  本轮跑出：

  ```
  pi.generation → 400: {"message":"you have no active step plan subscription",
                        "type":"request_params_invalid"}
  ```

  证据在 `durable-fail-evidence.txt`（从该次运行的 sidecar 运行时库 `tasks` 表逐字取）。
  本轮改的三个文件（`adapters/*`、`config.py`、`app.py`）都不在该路径上：
  `DurableSupervisor` → Node `agent-runtime` → 模型；该单的 `workspace=""`、
  `worker_env={}` 由测试自己决定，模型在 HTTP 400 就断了，根本没走到写盘或交差。
  同一条门禁昨夜 01:21 的全量是绿的，说明是订阅/额度状态变了。
  **建议**（不在本轮范围）：把这条挪到 live 门禁或加"无订阅即 SKIP"判据，
  归 `fix/gates-honest-skips` 一起做。
- 真实 Worker（claude / workbuddy / dsh / codex）的 shim 各有一份 `resolve_workdir()`，
  默认值仍是 `<repo>/data/commissions`（`__file__` 锚，绝对、不随 cwd）。经 `cli_subproc`
  启动时 env 已被绝对化，所以走的是注入值；只有**手工直接运行 shim** 才用到默认值。
  本轮没有改这 4 个 shim 的默认值（改了会与 `service_data_dir()` 的语义分叉，且无验收用例）。
- `probe_takeover_production.py` 里有一处读旧值的只读统计，未改（live 探针，不在门禁内）。
- 本轮所有改动都在 worktree；未 push、未合 master、未重启 8000/8010、未动主工作树。
