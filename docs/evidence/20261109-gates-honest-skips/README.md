# 门禁在非理想环境下给出假结论 —— 修复证据（2026-11-09）

分支 `fix/gates-honest-skips`，基线 `origin/master` = `0bac4b9`（含 `live_acceptance_guard`，
全量 215 条）。
本目录是一次全量门禁的两组原始读数与盘点依据，不是结论截图。

## 1. 症状

干净检出（缺 gitignore 产物）跑全量必红，且红的位置与代码无关：

- `step8_e2e_browser`：`pixel-world/dist/index.html` 不在 → `server/static.py`
  的 `mount_pixel_world` 不挂载 `/world` → 最后一段断言 FAIL。
- 该门禁按设计「失败留沙盘」→ `tmp/` 净增 1 项 → `gate_tmp_leak` 判「泄漏」FAIL。
  一个「没构建」被报成「代码坏了 + 泄漏」两条红。
- `speech_performance_route`：判据是墙钟「循环最长停顿 < 10ms」。并发跑两份全量时
  实测 13.15ms → 假红。
- `test_memory_fastloop`：同一类墙钟判据（`async_worst < sync_worst / 2`）。
  32 个 CPU 占用进程下实测同步 310/314ms、异步 229/261ms，3 次里 2 次假红。
- `test_intent_probe`：`elapsed < 0.5s`（两个 0.3s 并行，串行是 0.6s），
  只有 0.2s 余量；`elapsed < 1.0s`（timeout 0.2s）。

## 2. 改动

| 文件 | 改动 |
| --- | --- |
| `scripts/run_gates.py` | 新增 `ARTIFACTS` 依赖物登记 + `_requirements_for()` 按命令形状自动登记 + `missing_artifact()`；`run_gate()` 在缺依赖物时**不执行命令**、直接记 SKIP 并打印缺失路径与补救命令。`--json`、`--allow-skip`、fail-closed 裁决（exit!=0 永远 FAIL）均不变。 |
| `scripts/test-step8-e2e-browser.mjs` | 脚本入口先查 `pixel-world/dist/index.html`；缺则打印 `SKIP:` 三行（缺什么 / 为什么 / 怎么补）并 `exit 0`，**在创建沙盘之前**返回，不在 `tmp/` 留目录。 |
| `scripts/test_gate_tmp_leak.py` | 复用 `run_gates.missing_artifact` 提前判掉缺依赖物的抽样门禁；另外「退出码 0 + 行首 SKIP」的门禁也不计差分。缺产物不再算泄漏。裁决拆成 `classify()` 四态：`ok` / `skip` / `leak`（退 0 但 tmp 净增）/ `gate_failed`（退非 0，沙盘按设计保留）——门禁自己红不再被打印成「LEAK」，反过来「失败但清干净」也不再判 ok。 |
| `scripts/test_speech_performance_route.py` | 判据从「循环最长停顿 < 10ms」改为「40ms 同步窗口内事件循环有没有被打点」。心跳协程 `sleep(0)` 记时间戳，桩在同步 `sleep` 前后记窗口，数落进窗口的打点数；对照臂要求同步窗口内打点数为 0。 |
| `scripts/test_memory_fastloop.py` | `_measure()` 多返回「窗口内打点数」；删除墙钟比值判据，改为「同步对照窗口 0 打点 / 异步窗口 ≥1 打点」。心跳同时记 gap 与时间戳，`worst` 语义不变。 |
| `scripts/test_intent_probe.py` | 超时用例：改判「5s 的模型调用有没有走完」（`answered["done"] is False`），外层 `wait_for(3.0)` 只做防挂死安全网。并行用例：改判「两段工作的时间窗口有没有重叠」，串行为 0、并行为 ≈0.3s。 |
| `scripts/test_run_gates.py` | 新增 10 条回归用例（23 tests）：缺依赖物 → SKIP + 理由 + `--allow-skip` 下总码 0；未 `--allow-skip` 仍是 1；依赖物在时非零退出仍 FAIL；关键门禁必须登记 `requires`；Playwright 浏览器按目录探测；泄漏棘轮四态裁决（`gate_failed` 不叫泄漏、也不静默通过）。 |
| `scripts/test-ui-nav-browser.mjs` | SKIP 行原本写死「127.0.0.1:8000 未启动」——设了 `BASE_URL` 却连不上时会把人引到错端口（本轮实测：8022 沙盘服务掉了，日志怪 8000）。改为打印 `PAGE_URL` 的实际 origin。 |
| `scripts/dev_env_check.py` | 与 `ad3458e` 新增的自检脚本对齐措辞：缺项现在是「run_gates 记 SKIP」，不再是「直接失败 / 直接红」；`web/vendor` 的「影响浏览器门禁」按实测收紧。 |
| `docs/TESTING.md` | 本机产物表的「缺了影响」列同步为「记 SKIP + 补救命令」，与 `ARTIFACTS` 口径一致。 |

### 依赖物按命令形状登记（避免 215 条手写清单漏项）

- `cmd[0]` 是本仓 Python 解释器 → `venv`。`run_gates.py` 在 `.venv` 缺失时**静默**
  回退系统解释器，那正是「报 PASS 但跑的其实是另一个解释器」的来源。
- `cwd == agent-runtime` 的 node 门禁 → `agent_runtime_node_modules`。
- 其余 `node` / `npm` 门禁 → `node_modules`（browser 门的 `@playwright/test`、
  `npm test` 的 `three` / `@pixiv/three-vrm`）；`.mjs` 门禁再加 `playwright_browser`
  （探测 `PLAYWRIGHT_BROWSERS_PATH` 或默认 `ms-playwright/chromium*` 目录，
  不靠 `require()` 是否抛错来猜）。
- 显式附加：`step8_e2e_browser` → `pixel_world_dist`；
  `ui_browser` / `drill_network` / `drill_badkey` / `drill_ws_reconnect` → `venv`
  （这几个 `.mjs` 自己起沙盘服务、Python 路径写死在脚本里）。

## 3. 盘点：215 条门禁的依赖物（脚本生成，非手抄）

| 依赖物 | 门禁数 | 门禁 | 改前行为 | 改后行为 |
| --- | ---: | --- | --- | --- |
| `.venv` | 187 | 全部 Python 门禁（`core`、`protocol`、`venv_lock`、`speech_performance_route`、`live_acceptance_guard`…） | 缺失时 `venv_lock` FAIL；其余静默回退系统解释器（可能 PASS = 假结论，也可能 ModuleNotFoundError = FAIL） | 全部 SKIP：`缺 .venv/Scripts/python.exe；补救：uv sync --frozen` |
| `agent-runtime/node_modules` | 17 | `durable_*`（`durable_takeover`、`durable_block_kind`、`durable_mcp`…） | `ERR_MODULE_NOT_FOUND` → FAIL | SKIP：`cd agent-runtime && npm ci` |
| `node_modules` | 1 | `npm` | 缺 `three` / `@pixiv/three-vrm` 报错 → FAIL | SKIP：`npm ci` |
| `node_modules` + Playwright Chromium | 5 | `ui_nav_browser`、`blocks_browser`、`companion_browser`、`experience_entry_browser`、`voice_browser` | 缺 `@playwright/test` → FAIL；缺浏览器 → 启动抛错 exit 1（实测 `MJS_EXIT=1`） | SKIP：`npm ci` / `npx playwright install chromium` |
| `node_modules` + Playwright Chromium + `.venv` | 4 | `ui_browser`、`drill_network`、`drill_badkey`、`drill_ws_reconnect` | 任缺一个就起不来 → FAIL | 缺哪个报哪个，SKIP |
| `node_modules` + Playwright Chromium + `pixel-world/dist` | 1 | `step8_e2e_browser` | 缺 dist → `/world` FAIL + 留沙盘 | SKIP + 补救命令，不建沙盘 |
| `web/models` | 0 | ——（`test_seed_eye_*` 已在 `3d86bf9` 移出 GATES，自报 `unittest.skip`） | 不适用 | 登记在 `ARTIFACTS` 里备用 |
| `.agents` bundle | 0 | `poteto_loading` 自己判：两个 bundle 目录都不在 → SKIP；目录在但文件缺 → FAIL（半装坏不伪装成没安装） | 已正确 | 不变 |
| 8000 服务 | 0（直接依赖） | `ui_nav_browser` 读 `BASE_URL`（默认 8000），连不上自报 `SKIP:` exit 0 | 已正确 | 不变 |

> 8000 服务有一处**已知限制**：`ui_nav_browser` 连上谁就测谁，若 8000 上跑的是
> 另一个 worktree 的服务，它会测到错的代码还报绿。彻底修需要像 `ui_browser`
> 那样校验被服务页面里的标识串；这轮没动（超出「缺产物误报」范围），记录在此。

## 4. 两组读数（同一命令、同一数据集）

命令（分支 `fix/gates-honest-skips`，跑在 `c0c0df1` 之上、本目录这次提交前的代码状态）：

```powershell
$env:BASE_URL="http://127.0.0.1:8022/chat"   # 独立沙盘服务，不碰 8000/8010
.\.venv\Scripts\python.exe scripts\run_gates.py --allow-skip --json "$env:TEMP\gates-<env>.json"
```

| 环境 | 跑过并断言 | 失败 | 跳过 |
| --- | ---: | ---: | --- |
| 干净环境：无 `pixel-world/dist`、无 `.agents` bundle | **212** | **0** | **3**：`poteto_loading`（bundle 不在）、`procedural_memory_eval`（1 例跳过）、`step8_e2e_browser`（缺 dist，SKIP 行含补救命令） |
| 完整环境：`.agents` 全 bundle + `pixel-world` junction（dist 已构建）都就位 | **214** | **0** | **1**：`procedural_memory_eval` |

原始 JSON / 日志：本目录 `gates-clean.json`、`gates-clean.log`、`gates-full.json`、`gates-full.log`。

口径说明（别把这两行当成「整机从零装」）：

- `.venv` / `node_modules` / `agent-runtime/node_modules` / `web/models` 两轮都在（junction
  复用主检出）。它们的「缺了会怎样」由 `test_run_gates.py` 的合成用例覆盖，不是这两行
  读数覆盖的；见 §7。
- `web/models` 是 junction，本轮没移走。0 条门禁依赖它（§3），对读数无影响。
- 完整环境的 `pixel-world` 是 junction 到主检出的已构建目录；干净环境里它整个不在
  （等价于没 `npm ci && npm run build:world`）。

改前同口径（干净环境）会多两条红：`step8_e2e_browser`（`/world` FAIL）与
`gate_tmp_leak`（把 step8 留下的沙盘当泄漏）。改后两条都不再出现。

> 两组读数之间的一次失败尝试（基线 `ad3458e`，当时 214 条）：干净环境第一次跑出
> 210/0/4，多出的跳过是 `ui_nav_browser`——我用 `Start-Process` 起的 8022 沙盘服务
> 被回收了，门禁按设计 SKIP（连不上就当环境不满足，不判红）。重开服务后同一命令得
> 211/0/3。这次还顺带暴露了它的 SKIP 行写死「8000 未启动」，已改为打印 `PAGE_URL`
> 的实际 origin。rebase 到 `0bac4b9` 后多了 `live_acceptance_guard`，同口径读数为
> 212/0/3 与 214/0/1（见上表）。

> 另一次中间读数（基线 `ad3458e`，完整环境、但 `.agents` 只挂了半个 bundle）是
> 212/1/1，唯一红为 `poteto_loading: FAIL principle 叶子非空`。那不是回归：该门禁的
> 设计就是「目录在、文件缺 → 判红，不把装坏了说成没安装」。补全 bundle 后 213/0/1。

## 5. 计时型断言盘点（现状）

| 位置 | 判据 | 高负载会不会假红 | 依据 |
| --- | --- | --- | --- |
| `test_speech_performance_route.py` | ~~循环最长停顿 < 10ms~~ → 窗口内打点数 ≥1（对照 0） | 改前会，改后不会 | 改前并发实测 13.15ms 红；改后 32 进程占用下连跑 5 次全绿 |
| `test_memory_fastloop.py` | ~~`async_worst < sync_worst/2`~~ → 同步窗口 0 打点、异步窗口 ≥1 打点 | 改前会，改后不会 | 改前 32 进程占用下 3 次红 2 次（同步 310/314ms，异步 229/261ms，比值 0.74）；改后 3 次全绿 |
| `test_intent_probe.py` 超时 | ~~`elapsed < 1.0s`（timeout 0.2s）~~ → 5s 调用是否走完 + `wait_for(3.0)` 安全网 | 改前余量 5x，中低风险；改后只受 3s 挂死网约束 | 0.2s 对 5s 对 3s，两侧余量都大 |
| `test_intent_probe.py` 并行 | ~~`elapsed < 0.5s`~~ → 两窗口是否重叠 | 改前余量仅 0.2s，会；改后与负载无关 | 串行重叠为 0、并行为 0.3s，实测两种实现分别 FAIL/PASS |
| `run_gates.py` 子进程 `timeout=900` | 单条门禁 15 分钟天花板 | 不会 | 完整环境最慢 `ui_browser` ≈30s，余量 ~30x；超时是 exit 124 → FAIL，属真挂死 |
| `test_gate_tmp_leak.py` `PER_GATE_TIMEOUT=120` | 单条抽样门禁 2 分钟 | 不会假红（超时记 skip，不判泄漏） | 代价：超时会漏掉对该门禁的泄漏检查；最慢抽样 `step8` ≈11–30s，余量 ~4x |
| `test_cli_subproc_adapter.py:146` | `elapsed < WATCHDOG/2 = 5.0s`（适配器 timeout 0.5s） | 可能性低 | 10x 余量；测的是「截止时间管用」，不是性能 |
| `test_dsh_worker_shim.py:313` | `hang_elapsed < 15s`（子进程 timeout 1s） | 可能性低 | 15x 余量 |
| `test_scheduler_health.py` | 窗口内打点数（已是正确修法） | 不会 | 本轮改法即照它 |
| `test_experience.py:426/450` | `wait_for(..., 0.5)` 等进程内假实现 | 极低 | 进程内假实现毫秒级完成，0.5s 是安全网不是性能断言 |
| `test_music.py:224` | `wait_for(transport.submit("ok"), 0.2)` | 极低 | 同上，且失败含义是「提交没完成」而非「慢」 |
| `test_asr_language.py` `speech_ms` | 由合成 PCM 帧数换算 | 不会 | 不是墙钟，是数据长度换算 |
| `test_memory_fastloop.py` 控制臂 | `sync_worst > 20ms` | 不会假红（只会更容易满足） | 控制臂要求「看得见阻塞」，负载只会帮它 |

## 6. 逐条验收命令（都能跑出 pass/fail）

```powershell
# 1) 缺依赖物 → SKIP 机制（含反向用例：依赖物在时不许误伤）
.\.venv\Scripts\python.exe scripts\test_run_gates.py

# 2) step8 直接跑：缺 dist 时必须是 exit 0 + 行首 SKIP，且不建沙盘
node scripts\test-step8-e2e-browser.mjs ; echo "EXIT=$LASTEXITCODE"
Get-ChildItem tmp -Directory -Filter 'step8-e2e-*'      # 期望：0 项

# 3) 泄漏棘轮：缺 dist 时 step8 记为 skip，不判泄漏
.\.venv\Scripts\python.exe scripts\test_gate_tmp_leak.py ; echo "EXIT=$LASTEXITCODE"

# 4) 缺 Playwright 浏览器 → SKIP（拿空目录冒充，不动本机已装的浏览器）
$env:PLAYWRIGHT_BROWSERS_PATH="$env:TEMP\pw-empty"; New-Item -ItemType Directory -Force $env:PLAYWRIGHT_BROWSERS_PATH | Out-Null
.\.venv\Scripts\python.exe scripts\run_gates.py --only blocks_browser --allow-skip   # 期望：1 跳过
node scripts\test-aqi-blocks-browser.mjs ; echo "EXIT=$LASTEXITCODE"                 # 期望：1（绕过 run_gates 时的原行为）
Remove-Item Env:\PLAYWRIGHT_BROWSERS_PATH

# 5) 计时判据（空载 + 高负载各一次，两次都必须绿）
.\.venv\Scripts\python.exe -m unittest scripts.test_speech_performance_route
.\.venv\Scripts\python.exe scripts\test_memory_fastloop.py
.\.venv\Scripts\python.exe scripts\test_intent_probe.py

# 6) ui_nav_browser 的 SKIP 行必须报 BASE_URL 的 origin，不是写死的 8000
$env:BASE_URL="http://127.0.0.1:8099/chat"
node scripts\test-ui-nav-browser.mjs | Select-String 'SKIP'   # 期望：含 127.0.0.1:8099
Remove-Item Env:\BASE_URL
```

## 7. 未验证 / 不做

- 没有在「完全没装 `.venv` / `node_modules`」的机器上跑全量——本 worktree 用
  junction 复用了主检出的依赖物。该场景由 `test_run_gates.py` 的合成用例覆盖
  （缺依赖物 → SKIP），但没有真实整机读数，标「未验证」。
- 8000 服务的身份校验（测错 worktree 仍会绿）未改，见 §3 注。
- **`gate_tmp_leak` 的一次历史间歇红没查出根因**：`drill_ws_reconnect` 曾两次报
  「tmp/ 净增 1 项」。之后复跑全绿——单项连跑 2 次、整轮 2 次、32 个 CPU 占用进程下
  单项 2 次 + 整轮 1 次。没有足够证据说它修好了，所以只把**结论的措辞**改对：若下次
  是门禁自己失败，会报 `FAIL 门禁本身失败（退出码 N）` 并附输出末几行，不再说成
  「泄漏」；若真是退 0 却留了目录，仍报 `LEAK`。根因仍未定位，标「未验证」。
- 未 push、未合 master、未重启 8000/8010。
