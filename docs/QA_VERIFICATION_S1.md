# S1 三张卡独立验收记录（QA Verification · S1）

- 验收方：qa-runtime（Agent Teams · QA Engineer），独立于三张卡的作者
- 验收时间：2026-10-19 13:06–13:10（task-7）；**2026-10-19 13:13–13:18（task-9 + task-8 正式复验）**（Asia/Shanghai）
- 验收对象：task-7（S1-7 WS 指数退避）/ task-9（S1-9 台账帧契约 fixture）/ task-8（S1-8 `/ws` 同源 Origin 校验）
- 仓库：`D:\project\neurosama`，分支 `feat/pixel-world-oracle-world`，HEAD `9cd0c5a4`（本次验收期间作者改动均在未提交的工作区）
- 方法：不采信作者自述；自己看 diff、跑测试与冒烟、做对抗性探针；临时脚本/日志只放工作区
  `C:\Users\18712\.dsh\bot\workspace\recon\`，只在本文件留结论
- 执行节奏：task-7 于 13:06–13:10 复验；task-9、task-8 于 13:13–13:18 收到 Lead 通知后按本文方案执行并回填结论。
  三张卡均已实测；文中保留完整方案与命令，可原样复跑

## 结论速览

| 卡号 | 任务 | 结论 | 一句话 |
|---|---|---|---|
| **S1-7 / task-7** | WS 重连指数退避 | **通过** | 退避序列/重置/上限/dispose/无定时器泄漏均实测；单测+冒烟+真实断网恢复回绿，未发现 P0–P2 问题 |
| **S1-9 / task-9** | 台账帧契约 fixture 测试 | **通过** | 正常 6/6 绿；`agent_id` 改名 → 5/6 FAIL、exit 1；类型漂移 → FAIL；哈希还原后复绿；fixture 无敏感字段、与 8000 实响应字段集合一致；未新增依赖/未削弱既有断言 |
| **S1-8 / task-8** | `/ws` Origin 校验 | **通过** | `server/app.py` 仅 +24 行；py_compile 通过；离线 11/11、8001 live 5/5、QA 独立握手矩阵 12/12；浏览器同源可用/跨源 403；Vite 代理链路 10/10 不误拒；8000 未重启且 PID 不变 |

---

# 卡 1：S1-7（task-7）WS 重连指数退避

## 结论：**通过**

依据是 6 层独立证据，全部为本次实际执行：

### 证据 1：diff 审阅（逻辑正确性）

命令：

```powershell
cd D:\project\neurosama
git diff --stat -- pixel-world/src/world/ledger.ts pixel-world/test/ledger.test.ts
git diff -- pixel-world/src/world/ledger.ts
```

输出摘要：`ledger.ts +99/−15`、`test/ledger.test.ts +181`（两文件合计 +280/−15，见 `git diff --stat`）。
关键实现（`pixel-world/src/world/ledger.ts`，未提交工作区）：

- `reconnectDelay(failures) = min(500 × 2^failures, 5000)` → 500/1000/2000/4000/5000/5000…，
  负数/NaN 归到 500（`Math.max(0, Math.floor(...))`）
- `scheduleReconnect()`：`reconnectTimer !== null` 时直接返回（不叠加定时器）；`failures` 在排程时自增
- `open` 事件：`if (this.ws !== socket) return`；`reconnectFailures = 0`（成功即重置）；
  若 `!connected` 补一次 REST 全量（断线增量不补发，协议 §19）
- `close` 事件：先校验 `this.ws === socket`（旧连接迟到的 close 不干扰新连接），置 `ws=null`、
  `connected=false`、`publish()`，`disposed` 后不排重连
- `stop()`：清 `pollTimer`（4s REST 兜底）+ `reconnectTimer`，关连接置 `disposed`
- REST 4s 对账保留：`setInterval` 在 `wsAlive()` 为 false 时 `refresh()` —— 退避重连与 REST 兜底互不替代
- 可选注入（`location`/`timers`/`socketFactory`）默认值保持生产行为；`ws.readyState` 用常量 `1` 比较，
  假连接也能正确判定

未发现逻辑错误；范围仅限 task-7 声明的 2 个文件（见证据 6）。

### 证据 2：类型检查与全量测试

```powershell
cd D:\project\neurosama\pixel-world
npm run check   # exit 0，2061 ms
npm test        # exit 0，69/69 pass，0 fail，node duration 349.5 ms，墙钟 905 ms
```

- `npm run check`：退出码 **0**，`tsc --noEmit` 无输出（证据 `recon\s1-check.log`）
- `npm test`：退出码 **0**，`# tests 69 / # pass 69 / # fail 0`（证据 `recon\s1-npm-test.log`）
- **计数归因**：69 = 既有 59 + task-7 新增 4（`ledger.test.ts`）+ task-9 的 6 项。
  为剥离并行作业，单独跑 `node test\ledger.test.ts`：

```text
# tests 18 / # pass 18 / # fail 0 / exit 0
ok 15 - 重连退避序列：500/1000/2000/4000/5000/5000
ok 16 - 断线按退避序列重连，连上后重置回 500ms
ok 17 - stop 之后不再重连，也不留定时器
ok 18 - 注入连接上的 commission_update 会进世界视图
```

（证据 `recon\s1-ledger-single.log`；14 旧 + 4 新 = 18，全绿）

### 证据 3：浏览器冒烟（沙盒 49/49 + 台账 10/10）

```powershell
# 1) 后台起 dev（5188）
cd D:\project\neurosama\pixel-world
npm run dev -- --host 127.0.0.1 --port 5188 --strictPort
# 2) cwd 工作区，截图不落仓库
cd C:\Users\18712\.dsh\bot\workspace
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
$env:LEDGER='1'; node D:\project\neurosama\pixel-world\scripts\smoke.mjs   # 复用现有 8000，只读，不重启
```

- dev：`VITE v8.3.4 ready in 228 ms`；`GET http://127.0.0.1:5188/` → **200**（4953 B，13:07:25）
- 沙盒 smoke：**49/49 通过**，退出码 0，32.673 s（13:08:01；`recon\s1-smoke-sandbox.log`）
- 台账 smoke：**10/10 通过**，退出码 0，2.319 s（13:08:07；`recon\s1-smoke-ledger.log`），
  8000 始终为既有 PID 22996（未重启、未写入）
- 无回退：与 12:35 基线（49/49、10/10）一致

### 证据 4：对抗性探针（不依赖作者单测，独立验证边界）

临时脚本 `recon\qa-reconnect-probe.mjs`（不进仓库），16 项断言 **ALL PASS，退出码 0**（166 ms，
`recon\s1-reconnect-probe.log`）：

| # | adversarial 场景 | 结果 |
|---|---|---|
| 1 | 连续失败 30 次的重连延迟序列 | 500/1000/2000/4000/5000×26，PASS |
| 2 | 30 连失败期间始终只有 1 个 pending timer | PASS（断言语义为"从未 >1"，循环内逐次校验） |
| 3 | 30 连失败后 socket 数 = 失败数 + 1 | 31，PASS |
| 4 | REST 4s 对账 interval 全程未丢 | 1，PASS |
| 5 | 同一连接重复 `close` | 只排 1 个定时器，PASS |
| 6 | 旧连接迟到的 `close` | 不排新定时器、不关掉新连接，PASS |
| 7 | `error` + `close` 双事件 | 只排 1 次重连、视图 connected=false，PASS |
| 8 | `socketFactory` 连续抛错 3 次 | 按 500/1000/2000 退避，第 4 次建连成功，PASS |
| 9 | `stop()` 在退避等待中 | timeout/interval 全清 0，之后旧 socket 事件不再建连，PASS |
| 10 | 连上后再次断线 | 重新从 500 ms 起跳（重置生效），PASS |

**单测强度评估（诚实结论）**：作者测试第 1 条（`reconnectDelay` 纯函数）本身只断言常量，属于弱证据；
但第 2 条用假时钟 + 假 socket 走真实 `LedgerClient`，断言的是**客户端实际排出的延迟序列**
（500/1000/2000/4000/5000/5000）与"连上重置回 500"，若实现改成固定 4s 重连会直接 FAIL —— 因此
"没有退避"能被现有测试捕获，不是仅靠常量断言。未覆盖的边界（旧连接迟到 close、构造抛错、error+close、
30 连漏定时器）由本次探针补齐，全部通过。

### 证据 5：真实断网 / 恢复（验收项"断网 30s 内转离线，恢复转在线"）

临时脚本 `recon\qa-disconnect-probe.mjs`：真杀 5188 vite 进程 → 观察页面 → 重启 vite → 观察恢复。
结果 **exit 0**（8.6 s，`recon\s1-disconnect-probe.log`，页面报错 0）：

| 检查 | 实测 | 阈值 |
|---|---|---|
| 初始状态行 | `…台账在线…`（18 ms） | — |
| 杀服务后转「台账离线」 | **111 ms** | ≤ 30 s ✅ |
| 断网后快照 `ledger.connected` | `false` ✅ | — |
| 离线期 3 s 内是否漂回在线 | 保持离线（无幽灵在线）✅ | — |
| 重启服务后回「台账在线」 | **589 ms** | ≤ 30 s ✅ |
| 恢复后 `ledger.connected` | `true` ✅ | — |

截图（工作区，非仓库）：
`09-ledger-online-before-drop.png`（13:08:23）、`10-ledger-offline-dropped.png`（13:08:24）、
`11-ledger-recovered.png`（13:08:27）。

### 证据 6：范围检查（是否偷改范围外文件）

`git status --porcelain -- pixel-world` 当前快照（13:09）：

| 文件 | 归属 | 判定 |
|---|---|---|
| `pixel-world/src/world/ledger.ts`（M） | task-7 | ✅ 在范围内 |
| `pixel-world/test/ledger.test.ts`（M） | task-7 | ✅ 在范围内 |
| `pixel-world/README.md`（M）、`pixel-world/src/main.ts`（M） | task-6（S0 文档对账，12:58:41 落盘） | 非 task-7 越界，内容为 45→59 项/480×396→480×320 |
| `pixel-world/test/fixtures/`（??）、`test/ledger-fixtures.test.ts`（??） | task-9（13:05:53–13:06:07） | 非 task-7 越界 |
| `server/app.py`（M，13:07 起） | task-8 并行开工 | 非 task-7 越界 |

结论：**task-7 未越界**（仅 2 个文件；server/、config/、scripts/、package.json 均无其改动）。

### 发现问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0** | 无 | — |
| **P1** | 无 | — |
| **P2** | 无 | — |
| P3（轻微） | `error` 事件只置 `connected=false`，不 `publish()` | 浏览器正常路径 error→close，close 会 publish；若只收到 error 而 close 迟到，状态行会短暂停留在旧值。建议在 error 里也 publish（一行） |
| P3（轻微） | 连接尝试"挂死"（无 open/close/error）时不会另排重连 | 目前靠 4s REST 兜底拉回在线；极端网络下 WS 可能长期滞留。可选：open 超时后主动 close |
| P3（信息） | `message` 只接受字符串 `event.data` | 若服务端改发二进制帧会被忽略；当前 `/ws` 发 JSON 文本，无实际影响 |
| P3（信息） | `stop()` 后再 `start()` 会留下 interval | `start()` 未检查 `disposed`；当前唯一调用方 `SandboxScene.ts:121-136` 只 start 一次、stop 后不再 start，无生产影响 |

**总评：通过。** 4 条 P3 均为防御性建议，不阻断验收；不要求作者返工，可在后续顺手补。

### 复验命令清单（task-7）

```powershell
cd D:\project\neurosama
git diff --stat -- pixel-world/src/world/ledger.ts pixel-world/test/ledger.test.ts
cd pixel-world
npm run check
npm test
node test\ledger.test.ts
# 起 dev（另开终端/后台），然后：
cd C:\Users\18712\.dsh\bot\workspace
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
$env:LEDGER='1'; node D:\project\neurosama\pixel-world\scripts\smoke.mjs; Remove-Item Env:LEDGER
# 对抗性 + 真实断网（临时脚本，不进仓库）
node recon\qa-reconnect-probe.mjs
# 真断网需先取 5188 的 PID：
$pid5188 = (Get-NetTCPConnection -LocalPort 5188 -State Listen).OwningProcess
$env:DEV_PID="$pid5188"; node recon\qa-disconnect-probe.mjs; Remove-Item Env:DEV_PID
```

---

# 卡 2：S1-9（task-9）台账帧契约 fixture 测试

## 结论：**通过**（2026-10-19 13:15–13:17 执行）

### 现状快照

- 新文件（未跟踪）：`pixel-world/test/ledger-fixtures.test.ts`（6,121 B，13:06:07）、
  `test/fixtures/commissions.json`（1,999 B）、`test/fixtures/commission_update.json`（515 B，均 13:05:53）
- 测试内容（只读审阅）：锁定 PROTOCOL §19 的 15 个契约字段 + `packet`；覆盖
  blocked/running/done 三态解析；`project()` 映射（blocked→active、done→finished）；
  无花名册回退（D4 现状）；REST item 与 WS 帧同形；`applyPush` 进视图
- 复验前只读旁证：`npm test` 69/69 中已包含其 6 项（TAP `ok 8–13`）；本次已补做"改 fixture 必须变红"的变异验收

### 执行结果（本次实测；命令见下方方案）

| 检查 | 实测 | 判定 |
|---|---|---|
| 正常 fixture | `node test\ledger-fixtures.test.ts` → **6/6 pass**，退出码 0（175 ms，13:15:32） | ✅ |
| `agent_id` 改名（3 处 → `agentId`） | fixture sha256 `2C5742A0…`；测试 **5 fail / 1 pass**，退出码 **1**；失败为 `deepStrictEqual` 字段集合（test 文件 :115） | ✅ 契约真的会红 |
| 还原 | SHA256 回到 `A29D9BC4…`（与备份一致，hash match=True），复跑 **6/6 pass**、退出码 0 | ✅ |
| 附加类型漂移（`items[0].step_idx = 2` → `"0"`） | **1 fail / 5 pass**，退出码 **1**；还原后 hash match=True、复跑 6/6 绿 | ✅ 解析层会拒绝类型漂移 |
| fixture 敏感字段扫描 | `api_key/token/secret/password/authorization/bearer/sk-/phone/身份证/@` 全部 0 命中；`brief` 非空 0 条、`delivery/audit/packet` 均为 `{}`；`agent_id` 仅公开花名册 `scribe`/`claude` | ✅ 无敏感数据 |
| 来源可信度（只读） | 8000 `GET /api/executive/commissions?limit=100` 实响应的 item 键集合与 fixture **完全一致**（16 键，含 `packet`） | ✅ fixture 与真实形状一致 |
| 未削弱既有断言 | `git diff --numstat`：`ledger.test.ts +181/−0`（属 task-7 新增）、`world/grid/surface.test.ts` 0 变更；test 总数 59+4+6=**69**（只增不减） | ✅ |
| 依赖/范围 | `package.json` 无 diff；fixture 测试 0 处网络调用（纯离线）；task-9 未改 `server/` | ✅ |
| 全量回归 | `npm run check` 退出码 0；`npm test` 退出码 0，**69/69 pass**，0 fail（node 388 ms / 墙钟 899 ms，13:16:03） | ✅ |

证据：`recon\s1-fixtures-normal.log`、`s1-fixtures-mutated-agent_id.log`、`s1-fixtures-restored.log`、
`s1-fixtures-mutated-type.log`、`s1-fixture-orig-hash.json`、`s1-check-final.log`、`s1-npm-test-final.log`。

### 独立验收方案（已执行，保留供复现）

原始备份与哈希做在 `recon\fixture-bak-*.json` / `recon\s1-fixture-orig-hash.json`；脚本：

```powershell
$R = 'C:\Users\18712\.dsh\bot\workspace\recon'
$PW = 'D:\project\neurosama\pixel-world'
cd $PW; npm run check; npm test
'commissions.json','commission_update.json' | ForEach-Object {
  $p = "$PW\test\fixtures\$_"
  Write-Host "$_ sha256=$((Get-FileHash $p -Algorithm SHA256).Hash)"
  Copy-Item $p "$R\fixture-bak-$_"
}
node test\ledger-fixtures.test.ts        # 期望 6/6 pass
```

**变异矩阵（每条：备份→改一处→期望 FAIL→恢复→哈希核对→复跑应绿）**

| 编号 | 变异 | 操作 | 本次结果 |
|---|---|---|---|
| M1 | `commissions.json` 字段改名 `step_idx`→`stepIndex` | 文本替换 | 未单跑（同 M2 的字段集合路径已被证明会红） |
| M2 | `commissions.json` 字段改名 `agent_id`→`agentId`（3 处） | 文本替换 | **已执行**：5 fail/1 pass，exit 1 ✅ |
| M3 | `commission_update.json` 帧内字段改名 `step_idx`→`stepIndex` | 文本替换 | 未单跑（帧/REST 同形用例同样走 deepEqual） |
| M4 | `commissions.json` 状态值 `running`→`drafting` | 改 `items[2].status` | 未单跑（三态用例会红） |
| M6 | 类型漂移 `items[0].step_idx` number→string | JSON 改写 | **已执行**：1 fail/5 pass，exit 1 ✅ |

恢复与核对（每条变异后执行，**文件字节级还原**）：

```powershell
Copy-Item "$R\fixture-bak-commissions.json" "$PW\test\fixtures\commissions.json" -Force
Copy-Item "$R\fixture-bak-commission_update.json" "$PW\test\fixtures\commission_update.json" -Force
(Get-FileHash "$PW\test\fixtures\commissions.json").Hash -eq $orig['commissions.json']   # True
cd $PW; node test\ledger-fixtures.test.ts   # 恢复后必须重新 6/6 绿
```

**来源抽查（read-only，不写 8000）**：

```powershell
$live = (Invoke-WebRequest 'http://127.0.0.1:8000/api/executive/commissions?limit=100' -UseBasicParsing).Content | ConvertFrom-Json
$live.items[0].PSObject.Properties.Name | Sort-Object
# 本次实测：与 fixture items[0] 键集合完全一致（16 键）
```

**通过标准**：正常 fixture 6/6 绿；改字段名必须 FAIL（本次 M2 实测）；恢复后哈希一致且复绿；
`npm run check`、`npm test` 全绿；无新增依赖、无网络依赖；范围外零改动。—— 本次均满足。

### 发现问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | 3 条 item 中只有 `done` 一条是完全真实录制；`blocked`/`running` 两条按 §19 状态机派生（fixture `note` 已自述） | 契约测试目的已达成；若将来要锁真实状态字段，建议再用真实 running/blocked 单补录 |
| P3（信息） | 字段集合锁定强；值/类型层面只覆盖了部分断言（类型漂移仅 1/6 用例捕获） | 建议补一条"逐字段类型断言"用例，把类型契约钉死 |

**总评：通过。** 契约真会红、来源可对账、无敏感数据、未削弱既有断言。

---

# 卡 3：S1-8（task-8）`/ws` 同源 Origin 校验

## 结论：**通过**（2026-10-19 13:13–13:15 执行）

### 现状快照（最终版）

`server/app.py` 工作区 diff **+24/−0，单 hunk** `@@ -1009,0 +1010,24 @@`，只改 `/ws` 接入头部：
accept 之前校验 Origin，只比较 `netloc`（双方 lower）、不比较 scheme；`urlsplit` 的 `ValueError`
被捕获并按拒绝处理；无 Origin / 空串放行；拒绝写一条 warning 日志后 `close(1008)` 并 return（accept 前，
uvicorn 落成 HTTP 403）。注释明确写了"浏览器看不到 1008/reason"。

### 执行结果

**1) diff 复核（逐项）**

| 复核项 | 实测 | 判定 |
|---|---|---|
| 只比 netloc、不比 scheme | 代码 `origin_netloc != host`；A4（`https://127.0.0.1:8001`）实测 101 | ✅ |
| 双方 lower | 代码 `str(host).lower()`、`urlsplit(...).netloc.lower()`；A8（`LocalHost:8001` vs `localhost:8001`）实测 101 | ✅ |
| `urlsplit` 捕获 `ValueError` | `try/except ValueError` → `origin_netloc=""` → 拒绝；A10（`http://[`）实测 403 | ✅ |
| 畸形 Origin 按拒绝且不崩 | 8001 日志无 500，返回 403；live 用例同 | ✅ |
| 无 Origin / 空串放行 | A1（无头）101、A11（空串）101 | ✅ |
| 注释与"uvicorn 下 HTTP 403、1008 不达浏览器"一致 | 注释原文如此；8001 日志：`WebSocket /ws 403` + `connection rejected (403 Forbidden)`；浏览器 console 同 | ✅ |
| `/ws/interpret*`、`/ws/proactive` 未被改动 | `git diff -U0 -- server/app.py` 仅 1 个 hunk（/ws）；其余 ws 端点零 diff | ✅ |
| 范围 | `server/` 仅 `server/app.py` 本次改动（mtime 13:12:16；其余 8 个 server 文件为 2026-10-18 旧改动，与本卡无关） | ✅ |

**2) 静态检查与离线用例**

- `python -m py_compile server/app.py` → **exit 0**
- `python scripts/test_ws_origin.py`（作者离线用例）→ **Ran 11 tests, OK**，exit 0（0.325 s，13:14:13；
  `recon\s1-ws-origin-offline.log`）

**3) 8001 临时实例 live（隔离，不碰 8000）**

- 启动方式：`.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8001 --lifespan off`，
  `DB_PATH` 指向 `recon\ws-origin-8001.db`（工作区，143,360 B）；PID 41892；`/api/health` 200
  （`memories:0, proactivity:false`，确认临时实例）
- `python scripts/test_ws_origin.py --live ws://127.0.0.1:8001/ws` → **5/5 通过**，exit 0：
  同源 ready / 跨源 403 / 无 Origin ready / `Origin:null` 403 / 畸形 Origin 403
  （`recon\s1-ws-origin-live.log`；UTF-8 重跑日志清晰）
- 8001 服务日志出现 `websocket origin rejected: origin='http://evil.example' host='127.0.0.1:8001'` 等可解释记录，
  跨源均为 `403 Forbidden`（`recon\s1-ws-8001.log`）

**4) QA 独立握手矩阵（不依赖作者脚本）**

临时脚本 `recon\qa-ws-origin.mjs` 直接发 HTTP Upgrade，**12/12 ALL PASS**，exit 0（13:14:44；
`recon\s1-ws-origin-matrix.log`）：

| 用例 | 期望 | 实测 |
|---|---|---|
| A1 无 Origin | 101 | 101 ✅ |
| A2 同源 `http://127.0.0.1:8001` | 101 | 101 ✅ |
| A3 跨源 `http://127.0.0.1:5188` | 403 | 403 ✅ |
| A4 同 netloc、scheme=https（设计只比 netloc） | 101 | 101 ✅ |
| A5 `http://evil.example` | 403 | 403 ✅ |
| A6 Vite 代理仿真 `Host=Origin=127.0.0.1:5188` | 101 | 101 ✅ |
| A7 代理 Host=5188 / Origin=5187 | 403 | 403 ✅ |
| A8 Host 大小写不同 | 101 | 101 ✅ |
| A9 `Origin: null` | 403 | 403 ✅ |
| A10 畸形 `http://[` | 403 | 403 ✅ |
| A11 空串 Origin | 101 | 101 ✅ |
| A12 Origin 带路径但 netloc 相同 | 101 | 101 ✅ |

**5) 浏览器级复核（Playwright headless）**

- 同源（页面在 5190，经 Vite 代理到 8001）：`window.Oracle.view().ledger.connected === true`、状态行
  `…台账在线…`，页面零报错；
- 跨源（同一页面内直连 `ws://127.0.0.1:8001/ws`）：`onerror`，console
  `WebSocket connection to 'ws://127.0.0.1:8001/ws' failed: ... Unexpected response code: 403`；
  8001 日志记 `origin='http://127.0.0.1:5190' host='127.0.0.1:8001'` 后 403
  （`recon\s1-ws-browser.log`）

**6) 代理场景（task-8 重点：不能被误拒）**

- 临时 Vite 配置（工作区 `recon\vite-proxy-8001.config.mjs`，root=pixel-world，`/api`/`/ws` → 8001，
  `changeOrigin:false`）起在 **5190**；
- `PIXEL_WORLD_URL=http://127.0.0.1:5190/ LEDGER=1 node scripts/smoke.mjs` → **10/10 通过**，exit 0
  （2.249 s，13:15:00；`recon\s1-smoke-ledger-proxy-8001.log`）
- 结论：浏览器 Origin 与经代理转发的 Host 同为 `127.0.0.1:5190`，新代码 **不误拒** Vite 代理链路；
  说明 `vite.config.ts` 的 `changeOrigin:false` 与该校验配套（若改成 true 会全拒，见 P3）。

**7) 回归与冒烟（走现有 8000，只读）**

- `npm run check` exit 0；`npm test` **69/69**，exit 0（13:16:03）
- 沙盒 smoke：**49/49**，exit 0，32.721 s（13:16:46；`recon\s1-smoke-sandbox-final.log`）
- 台账 smoke（5188→8000）：**10/10**，exit 0，2.266 s（13:16:52；`recon\s1-smoke-ledger-final.log`）
- `python scripts/check_protocol.py` → **exit 0，PASS**（1.203 s；`recon\s1-check-protocol.log`）
- 按 Lead 说明：`scripts/test_voice_turns.py` / `scripts/test_experience_websocket.py` 在 HEAD 即红
  （缺 `_connections`），**非本卡引入**；本卡未改动这两个文件（`git status` 无其变更），只记录、不当回归基线

**8) 隔离与收口**

- 全程未重启/未终止/未写入 8000：验收前后均为 PID **22996**、LISTEN CreationTime 2026-10-18 16:40:37、
  `/api/health` 200（`recon\s1-ws-cleanup.log`）
- 8001、5190 已 kill；探测均 connection refused；`pixel-world` 无 `.tmp*` 临时配置残留；
  临时 DB/临时 Vite 配置只存在于工作区
- 无调试残留进仓库：`git diff -- server/` 仅 `server/app.py` 一个文件、+24 行

### 独立验收方案（已执行，保留供复现）

原始命令即上方执行过程；核心片段：

```powershell
$R = 'C:\Users\18712\.dsh\bot\workspace\recon'
$PY = 'D:\project\neurosama\.venv\Scripts\python.exe'
# 0) 锚点
(Get-NetTCPConnection -LocalPort 8000 -State Listen).OwningProcess   # 22996
# 1) 离线
cd D:\project\neurosama; & $PY -m py_compile server\app.py
& $PY scripts\test_ws_origin.py                                       # 11/11
# 2) 8001 临时实例（--lifespan off + 临时 DB_PATH）
$env:DB_PATH = "$R\ws-origin-8001.db"
& $PY -m uvicorn server.app:app --host 127.0.0.1 --port 8001 --lifespan off
# 3) live + 独立矩阵 + 代理 smoke
& $PY scripts\test_ws_origin.py --live ws://127.0.0.1:8001/ws         # 5/5
node recon\qa-ws-origin.mjs                                           # 12/12
#    临时 vite 5190（root=pixel-world，proxy→8001，changeOrigin:false）后：
$env:PIXEL_WORLD_URL='http://127.0.0.1:5190/'; $env:LEDGER='1'
node D:\project\neurosama\pixel-world\scripts\smoke.mjs               # 10/10
# 4) 收口：杀 8001/5190；确认 8000 PID 不变
```

### 发现问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | 无 Origin 即放行 = 白名单，不是认证 | 安全边界只挡"浏览器跨源"，本地进程/脚本可直连；设计如此，建议在安全说明里写明（不能当 CSRF/认证层） |
| P3（信息） | 只比 netloc、不比 scheme | TLS 终止反代下是正确取舍；意味着 https 页面连 ws 在服务端也被放行（浏览器自身会拦混合内容），非缺陷但需知晓 |
| P3（操作） | 依赖 Vite `changeOrigin:false` | 若部署改成 true，Host 变成后端地址，所有浏览器 WS 会被拒；建议在 `vite.config.ts`/部署文档旁注明该依赖（当前注释已有 proxy 说明，可补一句 Origin 校验联动） |
| P3（信息） | 拒绝仅写 WARNING 日志，无速率限制 | 低风险；若暴露公网可加简单限流/告警 |

**总评：通过。** 逐项复核与 4 类独立证据（离线/live/原始矩阵/浏览器+代理）均符合验收标准；
8000 全程只读且 PID 不变。

---

# 附录 A：证据文件清单（工作区 `C:\Users\18712\.dsh\bot\workspace\recon\`）

## task-7

| 文件 | 内容 |
|---|---|
| `s1-check.log` | `npm run check` 输出 |
| `s1-npm-test.log` | `npm test` 69 项 TAP |
| `s1-ledger-single.log` | `node test/ledger.test.ts` 18 项 |
| `s1-smoke-sandbox.log` | 沙盒 smoke 49 项 |
| `s1-smoke-ledger.log` | 台账 smoke 10 项 |
| `s1-reconnect-probe.log` | 对抗性探针 16 项 ALL PASS |
| `s1-disconnect-probe.log` | 真实断网恢复：离线 111 ms / 恢复 589 ms |
| `qa-vite-s1.log` | dev（5188）启动日志 |
| `eval\screens\09–11-*.png` | 断网前/离线/恢复截图 |

## task-9

| 文件 | 内容 |
|---|---|
| `s1-fixtures-normal.log` | 正常 fixture 6/6 |
| `s1-fixtures-mutated-agent_id.log` | `agent_id` 改名后 5 fail / 1 pass、exit 1 |
| `s1-fixtures-restored.log` | 还原后 6/6 |
| `s1-fixtures-mutated-type.log` | 类型漂移 1 fail / 5 pass、exit 1 |
| `s1-fixture-orig-hash.json` / `fixture-bak-*.json` | 原始 SHA256 与字节级备份 |
| `s1-check-final.log` / `s1-npm-test-final.log` | 最终 check（0）与 69/69 |

## task-8

| 文件 | 内容 |
|---|---|
| `s1-ws-origin-offline.log` | 作者离线用例 11/11 |
| `s1-ws-origin-live.log` | 8001 live 5/5（UTF-8） |
| `s1-ws-origin-matrix.log` | QA 独立握手矩阵 12/12 |
| `s1-ws-browser.log` | 浏览器同源在线 / 跨源 403 |
| `s1-smoke-ledger-proxy-8001.log` | Vite 5190 代理→8001 台账 smoke 10/10 |
| `s1-ws-8001.log` | 8001 服务日志（accept / 403 / rejected 记录） |
| `s1-check-protocol.log` | `check_protocol.py` PASS |
| `s1-vite-5190.log` / `vite-proxy-8001.config.mjs` | 临时代理 Vite 日志与配置 |
| `s1-smoke-sandbox-final.log` / `s1-smoke-ledger-final.log` | 最终 49/49、10/10 |
| `s1-ws-cleanup.log` | 8001/5190 关闭、8000 PID 锚点 |

# 附录 B：三张卡的一键复验顺序

```powershell
# 1) task-9（先做，纯逻辑，不占端口）
cd D:\project\neurosama\pixel-world; npm run check; npm test
node test\ledger-fixtures.test.ts
# 按"卡 2"变异矩阵逐条执行并恢复哈希
# 2) task-8（临时 8001 + 5188/5190 回归；全程不碰 8000）
# 按"卡 3"执行：py_compile → 离线 11/11 → 8001 live 5/5 → 独立矩阵 12/12
#   → 浏览器同源/跨源 → 代理 smoke 10/10 → check_protocol.py → 杀 8001
# 3) task-7（回归用）
# 按"卡 1"复验命令清单
# 4) 收口
Get-NetTCPConnection -LocalPort 5188,5190,8001,8000 -State Listen -ErrorAction SilentlyContinue
```

# 附录 C：本次验收后状态（2026-10-19 13:18）

- task-7：**通过**（P3 建议非阻断）
- task-9：**通过**（契约变异实测会红、哈希还原、无敏感字段、未削弱断言）
- task-8：**通过**（8001 隔离验收；8000 PID 22996 全程未变）
- 端口：5188 / 5190 / 8001 均已释放（复核无监听）；8000 为既有服务，未重启/未终止
- 仓库：本次除本文件外未修改任何源码/配置/测试/既有文档；临时脚本、日志、临时 DB/临时 Vite 配置
  全部留在工作区 `recon\`，未进仓库
- 已知红项（非本卡引入、不计基线）：`scripts/test_voice_turns.py` / `scripts/test_experience_websocket.py`
  在 HEAD 即红（缺 `_connections`，按 Lead 说明记录）
