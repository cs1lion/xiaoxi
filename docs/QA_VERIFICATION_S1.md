# S1 各卡独立验收记录（QA Verification · S1）

- 验收方：qa-runtime（Agent Teams · QA Engineer），独立于各卡作者
- 验收时间：2026-10-19 13:06–13:10（task-7）；**13:13–13:18（task-9 + task-8）**；**13:23–13:29（task-12 D4-A + task-11 D3-A）**；**13:41–13:49（task-13 S1-5）**；**13:47–13:53（task-14 S1-6）**；**13:56–14:01（task-15 + task-16）**；**14:18–14:29（task-10 + 提交与重构复验）**；**14:37–14:41（task-17 S2-1 多角色）**；**14:54–14:58（task-21 多角色 P3）**；**15:13–15:18（task-18 存档单槽+恢复）**；**15:24–15:28（task-23 台账时间戳透传）**；**15:32–15:36（task-24 v1 字段漂移修复）**；**15:44–15:49（task-19 调试面板 UI）**；**15:52–15:54（task-25 调试注入区 P2 修复）**；**16:06–16:12（task-26 调色板审计）**；**16:23–16:31（task-27 中文迁 DOM overlay）**（Asia/Shanghai）
- 验收对象：task-7 / task-9 / task-8 / task-12（D4-A）/ task-11（D3-A）/ task-13（S1-5）/ task-14（S1-6）/ task-15 / task-16 / task-10（排队）/ task-17（S2-1 多角色）/ task-21（多角色 P3）/ task-18（存档 v1 + 恢复）/ task-23（台账时间戳透传）/ task-24（v1 字段漂移修复）/ task-19（S2-3 调试面板）/ task-25（调试注入区 P2 修复）/ task-26（调色板审计，只报告）/ task-27（D5-A 中文迁 DOM overlay）/ 提交链 340d0ab、b656488、150b2a7、8a95c94、d8a57f9、2bde7cb、7e00d27、7db15ab、d3f757e、2b52d7c
- 仓库：`D:\project\neurosama`，分支 `feat/pixel-world-oracle-world`，HEAD `2b52d7c`（本次验收期间作者改动均在未提交的工作区）
- 方法：不采信作者自述；自己看 diff、跑测试与冒烟、做对抗性探针；临时脚本/日志只放工作区
  `C:\Users\18712\.dsh\bot\workspace\recon\`，只在本文件留结论
- 执行节奏：task-7 于 13:06–13:10 复验；task-9、task-8 于 13:13–13:18 收到 Lead 通知后按本文方案执行并回填结论。
  三张卡均已实测；文中保留完整方案与命令，可原样复跑

## 结论速览

| 卡号 | 任务 | 结论 | 一句话 |
|---|---|---|---|
| **S1-7 / task-7** | WS 重连指数退避 | **通过** | 退避序列/重置/上限/dispose/无定时器泄漏均实测；单测+冒烟+真实断网恢复回绿，未发现 P0–P2 问题 |
| **S1-9 / task-9** | 台账帧契约 fixture 测试 | **通过** | 正常 6/6 绿；`agent_id` 改名 → 5/6 FAIL、exit 1；类型漂移 → FAIL；哈希还原后复绿；fixture 无敏感字段、与 8000 实响应字段集合一致；未新增依赖/未削弱既有断言。**二轮独立复验（13:20–13:27，lead/QA）确认通过，变异矩阵扩展至帧 fixture 与 `step_idx` 字段** |
| **S1-8 / task-8** | `/ws` Origin 校验 | **通过** | `server/app.py` 仅 +24 行；py_compile 通过；离线 11/11、8001 live 5/5、QA 独立握手矩阵 12/12；浏览器同源可用/跨源 403；Vite 代理链路 10/10 不误拒；8000 未重启且 PID 不变 |
| **S1-12 / task-12（D4-A）** | 只读 `/api/executive/roster` + 前端兼容回退 | **通过** | server 仅新增 1 条只读路由（+9/0）；`/adapters` 8000 vs 8001 byte-identical；8001 `/roster` 200/7 条/仅 4 字段、无敏感值；前端 22/22 + 对抗 8/8；77/77、smoke 53/53 + 16/16；8000 旧进程 404 属已知限制 |
| **S1-11 / task-11（D3-A）** | 人工交付只对沙盒单 + 离线 Mock 显式标记 | **通过** | 台账单手动完成被拒（source 判断、状态不变）；离线标记/快照/回执/日报分账 18/18 全过；77/77、check 0、smoke 53/53 + 16/16；未碰 ledger/projection/server；README 计数漂移记次要 |
| **S1-5 / task-13** | 周期对账 / 分页截断 / 请求超时 | **通过** | 30s 对账（WS 在线也跑）、单飞、5s 超时、limit=100 截断语义均实测；81/81、check 0、smoke 53/53 + 16/16；真实 8000 台账 239 条>100，状态行「台账可能未取全」可复现；task-7 退避/task-12 回退交叉通过 |
| **S1-6 / task-14** | 真帧端到端 + 三项未验证（D4/D1/D3） | **通过** | 单次复跑 **23/23**（连跑 2 次绿，另 1 次仅隔离 mtime 断言被外部并发写触发假失败，已定位为环境）；冷启动 2.51s/lifespan complete；无 Key offline=true；真 WS 6 帧同 id `drafting→briefing→running→blocked`；浏览器同 id、`toWork`、pushLedger=0；D7 如实标未验证 |
| **S1-15 / task-15** | pickActive 接线（台账 order 驱动派活） | **通过** | `ordered/orderActive`（blocked→running→auditing→其它，同级 id 稳定）+ LedgerView.order + `nextLedgerTask` 排序；QA 矩阵 16/16（含无 order 插入序、撤单重排、单任务上限）；90/90、check 0、smoke 53/53 + 21/21；task-13/task-11 保护与 task-12 回退交叉通过 |
| **S1-16 / task-16** | blocked_question 真帧→快照/状态行（只展示） | **通过** | 全链路透传、60 **码点**截断（emoji surrogate 不劈开）、转 running/收工清除、空值不显示；QA 纯逻辑 27/27 + 浏览器 17/17；全程仅 GET、无 answer 入口；真帧 e2e 23/23 并手工抓到浏览器 `blockedQuestion` 与真实帧逐字一致 |
| **S1-10 / task-10** | 排队 + 取消未出发行礼 | **通过** | 忙时入队（不拒单）、MAX_PENDING=3、台账优先+FIFO、撤最后/按 id、clearDeparture 四条路径、两 sim 同命令同步长 deepEqual；96/96、check 0、smoke 59/59 + 21/21、check_protocol/test_core 不变；task-15/12 交叉通过 |
| **提交与门禁** | 340d0ab / b656488 / 150b2a7 | **通过（1 条 P3）** | 25 文件范围正确、无主产品夹带；roster_view 已移除、`/roster` 自包含、823 字符与基线一致、`/adapters` 不变；门禁 3/3 PASS（96/96）；workflow 统一走脚本、paths 合理；P3：npm 缺失时仍 traceback（退出码 1，安全失败）→ 已由 8a95c94 修复并复验 |
| **S1-17 / task-17（S2-1）** | 多角色（独立作息/派活/排队/归属） | **通过** | `villagers[]`+`villager` 兼容、每角色独立 state、队列 Map<villagerId>、派活只叫一个/第二单换人/双忙各自排队、workerId 归属、3 角色可扩、双 sim deepEqual；**100/100**、check 0、smoke **66/66 + 21/21**、check_protocol/test_core 64/0；渲染层按 id 多实例（截图双人可见） |
| **S1-21 / task-21（多角色 P3）** | 日报归属 + 手动完成对象 | **通过** | 新增 `deliveriesByVillager` 按人分账（台账/Mock 不混）；日报每人都写清、全闲/全忙/混合措辞；`complete` 带 `target`（id/名字）、未知目标拒绝、台账完成权不回退；**106/106**、check 0、smoke **74/74 + 21/21**、check_protocol/test_core 64/0；P3：DOM `#finish` 无 target 入口（仅 Oracle API） |
| **S1-18 / task-18（存档）** | 单槽存档 v1 + 读档对账/失败降级 | **通过（P2 已由 task-23 关闭）** | `WorldSaveV1` 与样例一致、指纹 `f53a0736d782d657`（206 路径去重 99）、运行时快照无 settled；变异样例测试必红、指纹不符按坏档；settled/去重/对账三分支/坏档备份 3/5/高版本只读/配额降级/30s+pagehide/刷新续跑均实测；**118/118**、check 0、smoke **85/85 + 21/21**、check_protocol/test_core 64/0。原 P2（ledger completedAt 恒 null）已由 task-23 接线并实测关闭 |
| **S1-23 / task-23（台账时间戳）** | `closed_at ?? updated_at` 透传 → settled | **通过（P2 已由 task-24 关闭）** | 解析三分支/秒×1000/毫秒原样/无本地兜底全部实测；真实 8000 行交付后 `settled.completedAt=1792377786778`（×1000）、clock=ledger；finishedAt 缺失退回已见 updated_at；都缺→null；样例指纹仍合法、`assertSaveV1(sim.toSave())` 通过、旧档可读；**126/126**、check 0、smoke **85/85 + 21/21**、check_protocol/test_core 64/0。原 P2（`toSave` 多出 `tasks[].ledgerCompletedAt`）已由 task-24 剥离+嵌套防线关闭 |
| **S1-24 / task-24（v1 字段漂移修复）** | 剥离运行时字段 + 嵌套键集合防线 | **通过** | `toSave()` 落盘 tasks 键集合与样例 10 键完全一致、无 `ledgerCompletedAt`；样例 SHA `F24FBAEF…298402`、schemaVersion 1 不变；`SAVE_TASK_KEYS`/`SAVE_SETTLED_KEYS` 与样例逐键一致，`assertSaveV1` 校验 tasks/settled 键集合 + `done⊆settled`；变异（tasks 加字段/伪 done 无 settled/settled 加字段/少字段）全部抛；去掉剥离的临时副本测试 **9/1 FAIL**；**129/129**、check 0、smoke **85/85 + 21/21**、check_protocol/test_core 64/0；task-18 刷新/对账/坏档/配额/高版本与 task-23 真实台账时间戳回归全过 |
| **S1-19 / task-19（S2-3 调试面板）** | 只读实体/事件流/连接存档面板 | **通过（P2 已由 task-25 关闭）** | 开关（按钮/快捷键 D/输入框内不触发）、实体行数与快照一致、任务 source/worker/blocked、按角色队列、最近 50 事件 + tone 过滤、暂停冻结而世界继续、台账四字段（离线/在线）、存档 v1/savedAt/paused、只读（无 fetch/WS、canvas 布局不变）、刷新 3.96Hz、无 JS 报错；**133/133**、check 0、smoke **96/96 + 21/21**、check_protocol/test_core 64/0。原 P2（注入区 250ms 后消失）已由 task-25 独立槽修复并实测关闭；P3（390px 页面级溢出/单测覆盖）不阻塞、记入后续卡 |
| **S1-25 / task-25（调试注入区 P2 修复）** | 注入区移出刷新区 | **通过** | `#debug-inject` 移至 `#debug-inject-slot`（独立于 `#debug-content`），随面板创建一次；400ms/1s/2s 后仍在、2 按钮可用、文案含「仅调试、不写台账」；暂停/切 tone 后仍在且展开状态保留；点 feed 一次只触发一次、两次各 +1（无重复绑定）；刷新 3.98Hz、无 JS 报错；**133/133**、check 0、smoke **104/104 + 21/21**；check_protocol/test_core 沿用 task-19（本轮未改主产品/server） |
| **S1-26 / task-26（调色板审计，只报告）** | 源色审计 + 16+8 提案 + 重画清单 | **通过（2 条 P3）** | 世界 77 唯一/86 引用、UI 17/41、共享 3、并集 91 **独立重算一致**；默认/--json/--md 两跑字节一致、3 个 cwd 结果一致、默认 exit 0、--strict exit 1；手算 villager.ts 13/13、prefabs 16/15、index.html 20/17 核对通过；越界表 61+9 行全部带 file:line；16+8 提案全部取自现有源色、无重复；11 组 WCAG 对比度独立复算一致；D4 未裁决/三栋材料趋同/prefab 十进制需同步测试均已写入报告。P3：--strict 提示仍写"本轮不启用"；77→71 为估算，需 task-27 后复跑 |
| **S1-27 / task-27（D5-A 中文迁 DOM）** | 画布中文清零 + DOM overlay 跟随 | **有条件通过（1 条 P2：审计报告附录未完整更新）** | src 内 `.add.text(`/`.text(`/`setText(` **0 处**、no-cjk 测试 2/2、静态文字改图标；浏览器 100/200/400% 名字/气泡误差 **0.00–0.40px ≤1**（zoom200/resize 后仍 ≤0.4）、走动/气泡/进屋/多角色不串、390px overlay 与 canvas 同框不溢出；**139/139**、check 0、smoke **114/114 + 21/21**、check_protocol/test_core 64/0；交叉存档 32/24/多角色 26/归属 26 全过；审计复跑世界 **71/85**、UI **17/45**、共享 2、并集 86、世界越界 55，仅删 6 个预期文字/气泡色、无新增色、提案未动。**P2**：docs 附录世界 Top16 与实跑不一致、越界长表仍 61 行含已删 6 色（正文计数/每文件表/Top10/§5.1 均已更新） | 

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

### 二轮独立复验（2026-10-19 13:20–13:27，lead · QA Engineer）

背景：本文档 13:18:16 已有一份并行会话（验收方署名 qa-runtime）的记录。按 Chief 指示对 task-9
做**独立复验**：不采信既有结论与既有日志，全部命令由本会话重新执行；临时日志以 `qa9-` 前缀
落工作区 `recon\`，与上轮 `s1-` 前缀证据区分。task-8 按 Chief 指示已打回、暂停验收，本轮不含 task-8。

| # | 检查 | 实测 | 判定 |
|---|---|---|---|
| 1 | 基线：fixture 测试单跑 | `node test\ledger-fixtures.test.ts` → **6/6 pass**，exit 0（10.0 ms，13:21） | ✅ |
| 2 | 变异 A：`commissions.json` 字段改名 `step_idx`→`stepIndex`（3 处；上轮未覆盖的字段） | **3 fail / 3 pass**，exit 1；红的是：字段集合锁（test 1）、逐条解析（test 2）、帧/REST 同形（test 5） | ✅ 契约真会红 |
| 3 | 变异 A 还原 | 字节级还原后 SHA256 与 `s1-fixture-orig-hash.json` **一致**；复跑 **6/6 pass**，exit 0 | ✅ |
| 4 | 变异 B：`commission_update.json` **帧内** `step_idx`→`stepIndex`（上轮未覆盖的文件） | **test 5 fail**，exit 1（`deepStrictEqual` 帧与 REST 键集合） | ✅ 帧侧契约也会红 |
| 5 | 变异 B 还原 | 两个 fixture 哈希均与原始记录一致；复跑 **6/6 pass**，exit 0 | ✅ |
| 6 | fixture 敏感字段扫描 | `api_key/token/secret/password/authorization/bearer/sk-/phone/身份证/邮箱` 等 12 类模式 **0 命中**；`brief` 非空 0 条、`delivery/audit/packet` 均为 `{}`；`agent_id` 仅公开花名册 `scribe`/`claude`；唯一非空用户向文本是通用问句「先按短句还是完整句式？」 | ✅ 无敏感数据 |
| 7 | 契约来源核对（只读） | `PROTOCOL.md` §19（L334–339）15 字段与测试 `CONTRACT_FIELDS` **逐一一致**；§19 L348–350「commission 就是 items 元素本身，不另立一套字段」由 test 5 锁定 | ✅ |
| 8 | 实响应形状比对（只读，未写 8000） | 8000（PID **22996**，未重启）`GET /api/executive/commissions?limit=100` → 100 条、**16 键与 fixture 完全一致**（KEY SETS IDENTICAL）；线上 100 条全为 `done`，与 fixture `note` 自述的录制时状态相符 | ✅ fixture 与真实形状一致 |
| 9 | 未削弱既有断言 | `git diff --numstat -- test/`：`ledger.test.ts +284/−0`、`surface.test.ts +45/−0`、`world.test.ts +55/−0`、`grid.test.ts` 0 变更——**全部纯新增**；`projection.ts` 仅 `parseAgentRow` 向后兼容扩展（`id` 或 `agent_id`），`parseCommissionRow`/`project` 零改动 | ✅ |
| 10 | 依赖/离线性 | `package.json` 无 diff（0 新依赖）；fixture 测试 0 处网络调用（`readFileSync` + 纯函数 + 已注入的假时钟/假 location，`applyPush` 不建连） | ✅ |
| 11 | 回归 | `npm run check` exit 0；`npm test` **77/77 pass**，exit 0（13:26，`qa9-npm-test-final.log`） | ✅ |

**变异矩阵（本轮独立执行，字段/文件均与上轮不重复）**：

| 编号 | 对象 | 变异 | 结果 |
|---|---|---|---|
| M-A | `commissions.json` | `step_idx`→`stepIndex` ×3 | 3 fail / 3 pass，exit 1 → 还原后 6/6 绿 |
| M-B | `commission_update.json` | 帧内 `step_idx`→`stepIndex` ×1 | test 5 fail，exit 1 → 还原后 6/6 绿 |

**测试强度评估（诚实结论）**：字段集合用 `deepEqual` 精确锁定，改名（M-A/M-B 实测）与删字段
都会红；逐字段值相等（test 2）能兜住 REST items 的类型漂移与缺字段。发现的真实边界：
帧侧缺字段时 `parseCommissionRow` 不抛（缺失 `step_idx` 默认 0），因此变异 B 中 test 6 仍绿——
帧侧防线实际只有 test 5 的键集合锁（已证明会红），见下方 P3。

**发现问题分级（二轮）**

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3 | `parseCommissionRow` 注释称「字段缺失一律拒绝，不用默认值把坏数据蒙混进来」，实际缺失字符串字段默认 `''`、缺失 `step_idx` 默认 `0`，仅 `id` 缺失抛错 | 既有代码（HEAD 即有），非 task-9 引入；要么补严解析（缺字段即抛），要么改注释与行为一致。当前 fixture 测试对 REST items 有逐字段值相等兜底，帧侧仅靠键集合锁 |
| P3（信息） | 复验期间测试总数持续增长（69→73→77，并行 D4-A 等工作，均为纯新增） | 本结论基于 13:26 快照；fixture 测试自身（13:06:07 落盘后）未再变动 |

**总评（二轮）：通过。** 独立复验确认：正常 fixture 全绿；两类变异（REST 字段改名、帧字段改名）
均如预期变红且字节级还原后复绿；fixture 无敏感字段且与 8000 实响应字段集合一致；既有断言零削弱；
无新增依赖、测试纯离线。对上轮两条 P3 建议（补录真实 running/blocked、补逐字段类型断言）无异议，
可作为后续顺手项。

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

# 卡 4：S1-12（task-12，D4-A）只读花名册端点 + 前端兼容回退

## 结论：**通过**（2026-10-19 13:23–13:27 执行）

### 1) 范围/结构复核

| 复核项 | 实测 | 判定 |
|---|---|---|
| `server/assistant/executive_api.py` | `git diff -U0` 仅 1 个 hunk `@@ -123,0 +124,9 @@`，numstat **+9/−0**；只在 `create_executive_router` 里新增 `GET /roster`，原有路由零改动 | ✅ 0 删除、只有新增路由 |
| `server/assistant/executive.py` | `roster_view()` 所在 hunk 为纯新增（无 `-` 行）；`adapter_bindings` 函数体与 `git show HEAD` **逐字相同**（641 chars，equal=True）。该文件工作区总 numstat +155/−5 含 2026-10-18 遗留改动（bind_durable/recover 等，非本卡），本卡新增仅 roster_view | ✅ 纯新增、不影响 adapter_bindings |
| `/adapters` 返回结构（用户硬条件：不改主产品 server 返回结构） | 8000 旧进程与 8001 新代码各取一次 `GET /api/executive/adapters`：**len 均 1478 B、rawEqual=True、canonical equal=True**（7 条 agent、字段一致） | ✅ byte-identical |
| 前端 `projection.ts` | `parseAgentRow` 兼容 `id`（新 `/roster`）与 `agent_id`（旧 `/adapters`）；`strengths` 缺失/非数组时留空，不抛 | ✅ |
| 前端 `ledger.ts` | `refreshAgents()` 先 `/roster`，非 OK/异常再 `/adapters`；两源都失败只降级分类，不把 commissions 成功判离线 | ✅ |

> 归因说明：`executive.py` 的其余 working-tree 改动（durable 对账/审批等）mtime 为 2026-10-18，
> 早于本卡开工；本卡改动集中在 mtime 13:20–13:21 的 roster 相关行。`server/app.py` 的 +24 行属
> task-8，非本卡。

### 2) 端点实测（8001 临时实例，隔离，不碰 8000；用后关闭）

启动：`.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8001 --lifespan off`，
`DB_PATH` 指向工作区临时文件，PID 18436；`GET /api/health` → 200（`memories:0`）。

- `GET /api/executive/roster` → **200**，823 B，**7 条**；每条键集合恰为 `adapter,id,name,strengths`：
  `scribe/笔录官(manual)`、`builder/营造匠(manual)`、`scout/探马(manual)`、`claude/真机 Claude(cli_subproc)`、
  `workbuddy/真机 WorkBuddy(cli_subproc)`、`dsh/真机 DSH(cli_subproc)`、`codex/真机 Codex(cli_subproc)`
- 敏感字段扫描（响应 JSON）：key 名 `endpoint/executable/env/token/api_key/secret/password/credential/workspace_root/
  base_url/headers/authorization` **0 命中**；值扫描 `http:// https:// Bearer sk- /usr/ C:\ \\` **0 命中**；
  cli_subproc 的 claude/workbuddy/dsh/codex 均只有 id/name/strengths/adapter
- 只读性：`POST /api/executive/roster` → **405**；代码审阅 `roster_view()` 只读 `self.roster`，不实例化适配器、不写库
- 旧进程已知限制：`GET http://127.0.0.1:8000/api/executive/roster` → **404**（8000 是 2026-10-18 启动的旧代码，
  新路由要下次重启生效）。**按 Lead 要求记入结论，不算失败**
- 8000 未重启：全程 PID **22996**、CreationTime 2026-10-18 16:40:37、`/api/health` 200 不变

### 3) 前端测试/对抗

- `node test/ledger.test.ts` → **22/22 pass**（14 旧 + task-7 4 + task-12 4），新增 4 项：
  `parseAgentRow 兼容`、`/roster 优先且不再请求 /adapters`、`/roster 404 回退`、`两源失败只降级不判离线`
- `npm test` 安静期快照 → **77/77 pass、0 fail**（77 = 73 + task-11 D3 新增 4；task-12 自身的 4 项见上）；
  `npm run check` → exit 0
- QA 独立对抗探针 `recon\qa-roster-probe.mjs` → **8/8 ALL PASS**，exit 0：
  缺 strengths / strengths 为字符串 / `items: []` / 条目非法 / 信封形状坏 / 两源失败 / 旧 404 回退 / 两次刷新保留花名册
  （缺字段、字符串 strengths、空 items 均不崩并退回标题关键词分类；坏信封回退 `/adapters`）
- P3 观察：`/roster` 返回 200 且 `items` 为数组但条目全非法时，前端**不会**再回退 `/adapters`（200+数组被当作
  权威清单）；对当前实现无实际影响（新端点已过滤无 id 条目），建议后续补一条显式测试/注释

### 4) 端到端冒烟

- 沙盒 smoke：**53/53 通过**，exit 0（32.9 s）
- 台账 smoke（走 8000 旧服务 = 回退路径）：**16/16 通过**，exit 0（6.2 s）
- 真实页面额外取证（`recon\qa-d3-probe.mjs`）：`fetch('/api/executive/roster')=404`、
  `fetch('/api/executive/adapters')=200`（7 条 agent_id）→ 台账连接建立、注入台账单能驱动世界，
  证明旧服务回退链路真实可用

### 5) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | 8000 旧进程无 `/roster`，新路径需下次重启生效 | 已知限制，非失败；重启前旧客户端继续走 `/adapters` 回退 |
| P3（信息） | 200+数组但条目全非法时不回退 `/adapters` | 设计选择；建议补测试/注释固定语义 |
| P3（信息） | `/roster` 每次 4s 轮询都会请求（无缓存） | 7 条只读内存数据、开销极低；暂不构成问题 |

**总评：通过。** 用户硬条件（不改主产品 server 返回结构）以 byte-identical `/adapters` 实证；
新端点只读、无敏感字段；前端兼容/回退/降级均有测试与独立探针覆盖。

---

# 卡 5：S1-11（task-11，D3-A）人工交付只对沙盒单 + 离线 Mock 显式标记

## 结论：**通过**（2026-10-19 13:23–13:29 执行）

### 1) 范围复核（用户三条硬约束）

| 复核项 | 实测 | 判定 |
|---|---|---|
| 改动文件（numstat） | `simulation.ts +35/−5`、`types.ts +14/−1`、`SandboxScene.ts +4/−6`、`report.ts +16/−3`、`scripts/smoke.mjs +44/−2`、`test/world.test.ts +55/−0`、`test/surface.test.ts +45/−0` | ✅ 均在声明范围（report.ts/surface.test.ts 属离线统计区分所需） |
| 声明范围中的 `oracle-panel.ts` | `git diff` **0 行**：回执文案由 simulation 返回值直接进 `#reply`，UI 无需改；非越界、非缺失 | ✅ 说明即可 |
| 是否碰 task-12 文件 | `ledger.ts` / `projection.ts` 的 working-tree diff 全部是 task-12 的 roster/backoff 内容，无 D3 字段/文案改动；mtime 13:21:00（task-12）早于 task-11 文件 13:21:30+ | ✅ 未碰 |
| 是否碰主产品 server（约束③） | `server/**/*.py` 在 13:21 之后**零修改**（app.py 13:12=task-8；executive*.py 13:20:51=task-12；其余为 10-18 旧账）；task-11 未新增 server 改动 | ✅ 未改主产品 server |
| 是否为了标记 Mock 写台账/加网络（约束②） | task-11 新增代码行扫描 `fetch(/WebSocket/XMLHttpRequest/http(s)://axios/kind:'sync'` **0 命中**；只新增内存计数器 `ledgerDeliveries`/`mockDeliveries` | ✅ 无写台账、无网络 |
| 完成权唯一来源（约束①） | `finish()` 先判 `task.source === 'ledger'` → 拒绝；台账单只能由 `sync` 收尾；沙盒单才走人工收尾 | ✅ 代码 + 实测 |

### 2) 关键行为（真实浏览器，`recon\qa-d3-probe.mjs`，**18/18 PASS**，exit 0）

- **离线 `?ledger=off`**：快照 `ledger.mock === true`；状态行
  `…台账离线 · 离线 Mock　　台账交付 0 单 · Mock 交付 0 单…`；沙盒短活点「现实完成」→ 成功，
  回执 `这单办了（离线 Mock，不计入台账）。`；`deliveries={ledger:0,mock:1}`；
  日报行 `离线 Mock 交付 1 单，最近一单：…`（不含「台账交付」）
- **在线（8000）**：快照 `ledger.mock === false`、状态行无「离线 Mock」；
  注入台账单（running）→ 她进铺子；点 `#finish` → 被拒，回执 `这单的完成权在台账，人工不能收尾。`，
  仍 `working`、单未完成、`deliveries` 不变（拒绝靠 `source`，不是靠文案）
- **同一在线模式下**：推送台账单 `ready_to_report` → 正常交付，`deliveries.ledger=1`；
  再点沙盒 `quick` 单 → 人工完成**成功**，回执 `这单办了（沙盒 Mock，不计入台账）。`，`deliveries.mock=1`
  —— 同一入口两种来源结果不同，证明按 `source` 区分而非按模式一刀切
- **日报/统计分账**：在线时 `台账交付 1 单，Mock 交付 1 单，最近一单：…`，报纸 DOM 同步渲染；
  两数分开、不合并
- 全程页面 JS 报错 **0**

### 3) 单测/回归

- `npm test` → **77/77 pass、0 fail**（node 320.6 ms / 墙钟 825 ms），`npm run check` → exit 0
- task-11 新增 4 项全部通过：`日报把台账交付与 Mock 交付分开写`（ok 38）、
  `台账单不能人工完成：完成权在台账，状态不变`（ok 68）、`沙盒单可以人工收尾，回执标明离线 Mock`（ok 69）、
  `快照的离线 Mock 标记随台账连接变化`（ok 70）
- **既有断言未被削弱**：`test/world.test.ts` 与 `test/surface.test.ts` diff 均为 **0 删除行**（+55/+0、+45/+0）；
  既有日报断言（空交付 / 单笔交付 / 里程碑 / 新楼 / 日记）全部仍然通过
- 沙盒 smoke：**53/53 通过**（49 + 新增 4）；台账 smoke：**16/16 通过**（10 + 新增 6），
  8000 只读、未重启（PID 22996 不变）

### 4) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（文档） | README 计数过期：`README.md:25/67` 仍写「59 项」，实际已 **77** | 文档漂移，次要问题，不算 D3 验收失败；建议 S0 文档对账再跑一轮 |
| P3（信息） | 在线时沙盒交付回执写「沙盒 Mock」而非「离线 Mock」 | 语义正确（不是离线）；若产品文案要求统一，可再讨论 |
| P3（信息） | 台账交付计数只在 `finishActiveTask` 落地；台账单必须先被驱动到铺子 | 当前流程成立；后续多角色改造时需复核计数点 |

**总评：通过。** 三条用户硬约束逐条有代码 + 运行时证据；离线 Mock 在状态行/快照/回执/日报四处显式
且不合并计数；台账完成权未被人工入口绕过。

> **并发期环境备注（不计失败）**：13:23 复验时曾观察到 `test/fixtures/commission_update.json` 被并行作业
> 瞬时改成 `stepIndex`，导致全量测试出现 1 个 fixture 测试失败；13:24 该文件已恢复（SHA256 `39F57390…` 与
> 原始一致），安静期（13:26）复跑 **77/77 全绿**。该现象来自其他卡的并发写，不是 task-11/12 引入。

---

# 卡 6：S1-5（task-13）周期对账 / 分页截断 / 请求超时

## 结论：**通过**（2026-10-19 13:41–13:49 执行）

### 1) 实现复核（代码事实）

- `ledger.ts`：新增 `RECONCILE_MS = 30_000` 独立 interval（**WS 在线也调用 `refresh()`**）；
  `refresh()` 用 `inFlight` 做**单飞**（在途时后续调用直接 `return this.inFlight`）；
  `getJson()` 用 **AbortController + `REQUEST_TIMEOUT_MS = 5000`**，超时走失败分支；
  `COMMISSIONS_LIMIT = 100`、`COMMISSIONS_URL = '/api/executive/commissions?limit=100'`；
  `absorbCommissions()` 里 **`complete = items.length < 100`**，`publish()` 把 `complete` 透给世界
- `simulation.ts`：`sync(..., complete)`（命令缺省 `complete ?? true` 保持旧调用兼容）；
  `complete=false` 时**只按 `finished` 收尾，绝不把"不在快照里"当撤单**；`complete=true` 才撤单；
  `ledger.complete` 透进 `WorldSnapshot`；由完整→不完整时写 warn 日志
- `types.ts`：`WorldSnapshot.ledger.complete` 字段新增
- `SandboxScene.ts`：把 `view.complete` 传给 `sync`；状态行在 `complete=false` 时追加
  「台账可能未取全」（与「台账在线/离线 · 离线 Mock」并列，不覆盖离线标记）
- 单测新增 4 项（ok 36–39）：30s 对账、取满 limit、截断不撤单、5s 超时

### 2) 自跑结果

| 检查 | 实测 | 判定 |
|---|---|---|
| `npm run check` | exit 0（tsc 静默） | ✅ |
| `npm test` | **81/81 pass、0 fail**（node 327.7 ms / 墙钟 830 ms）；ok 36–39 为新增 4 项 | ✅ 与作者声称 81 一致 |
| 沙盒 smoke | **53/53 通过**，exit 0，32.6 s | ✅ |
| 台账 smoke（8000 只读） | **16/16 通过**，exit 0，6.2 s | ✅ |

### 3) 对抗探针（不依赖作者单测；`recon\qa-task13-probe.mjs`，16/16 ALL PASS，exit 0）

| 场景 | 实测 |
|---|---|
| fetch 返回恰好 100 条 | `complete=false`、`finished` 仍完整映射 100 条 |
| fetch 返回 99 条 | `complete=true` |
| 截断快照（complete=false）里旧单不在 items | 不撤单：旧单仍在，`snapshot.ledger.complete=false` |
| 99 条完整快照里旧单确实消失 | 允许撤单：旧单被移除，`ledger.complete=true` |
| fetch 挂起 6s（真实计时） | **5006 ms 后超时**、降级 `connected=false`、**无未捕获 Promise 异常** |
| 并发 3 次 `refresh()` | 只发 **1 次** commissions 请求；在途结束后下一次正常发起（单飞有效） |
| WS 在线 | 4s 兜底**不**刷新；30s 对账**刷新 1 次** |
| 定时器收口 | 正常返回后 5s 超时定时器被清；stop 清两个 interval 与退避定时器（task-7 探针项） |

### 4) 真实截断取证（8000，只读；复现作者证据）

- 8000 API：`limit=100` → **恰好 100 条**；`limit=101/200/1000` 仍 100 条（服务端 `MAX_LIST=100` 硬顶）。
  项目 `data/commissions.db` **只读副本**统计：`commissions` 表 **239 行、全部 `done`** → 台账真实总数 >100，
  **不是恰好 100**：每次 REST 都被截断，前端 `complete=false` 判定正确
- 真实页面 `?ledger=on`：`window.Oracle.view().ledger.complete === false`，状态行
  `…台账在线 · 台账可能未取全…`（截图 `14-truncation-status.png`）；`?ledger=off` 不显示该标记；全程零 JS 报错
  （`recon\qa-task13-real.mjs`，5/5 PASS）
- 8000 未重启：PID **22996**、CreationTime 2026-10-18 16:40:37、health 200 全程不变

### 5) 交叉回归（任务要求：不破坏 task-7 / task-12）

- **task-7 退避**：用真实断网复跑（杀 5188 → 重启）：状态行 **121 ms** 转离线、**1118 ms** 回在线、
  恢复后 `ledger.connected=true`、页面零报错（`recon\s1-task13-cross-task7-disconnect.log`，6/6 通过）。
  备注：旧的 fake-clock 退避探针因 task-13 新增了「每请求一个 5s 超时定时器 + 30s 对账 interval」，
  其计数口径失效（3 项 FAIL 是探针口径问题，不是产品回归），故改用真实断网复跑为准
- **task-12 花名册**：`qa-roster-probe.mjs` 适配 `?limit=100` URL 后 **8/8 PASS**；
  真实旧服务仍走 `/roster 404 → /adapters 200` 回退，台账 smoke 16/16 不回退
- 说明：13:42 起 8001 被另一成员临时实例占用（PID 28664，anaconda uvicorn），**未触碰**；
  本卡自己的临时进程均未占用 8000，5188 已释放

### 6) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | 断网时状态行会保留上次 `complete=false` → `台账离线 · 离线 Mock · 台账可能未取全` | 旧快照残留；建议离线时把 `complete` 视为未知/不显示截断提示（不阻断） |
| P3（信息） | `absorbCommissions()` 对非数组 `items` 直接 return，`complete` 保留旧值 | 坏响应时截断标记可能滞后一拍；低风险 |
| P3（信息） | 服务端 `MAX_LIST=100` 硬顶，前端无法真正翻页取全 | 本卡目标只是"不把截断当撤单"；真正取全需服务端分页/游标，超出本卡范围 |
| P3（信息） | 30s 对账 + 4s 兜底两个 interval 并存 | 已由单飞去重，无请求叠加；本次实测无并发叠加 |

**总评：通过。** 30s 对账/单飞/5s 超时/100 截断语义逐项有代码 + 独立探针证据；真实 8000 的截断标记
可复现且判定正确（239>100）；task-7 退避与 task-12 回退交叉回归均未破坏。

---

# 卡 7：S1-6（task-14）真帧端到端 + 三项未验证

## 结论：**通过**（2026-10-19 13:47–13:53 执行；功能项 23/23，隔离断言有一次被外部并发写触发假失败，见 §4）

### 1) 交付与复跑方式

- 新增：`pixel-world/scripts/e2e-real-frame.mjs`（437 行）、`docs/E2E_REAL_FRAME.md`（138 行）；
  `server/` 无本卡改动（git status 中 server 改动均属 task-8/12，且 mtime 早于本卡）
- 复跑命令（3 次，证据均落工作区）：
  `cd pixel-world; node scripts/e2e-real-frame.mjs`
  （`E2E_EVIDENCE` 指到 `recon\e2e-real-frame-evidence*.json`；第二次另加 `--keep` 查日志后手工清理）
- 结果：**run1 23/23（exit 0，15.2s）**；run2（--keep）**22/23**（唯一失败为「仓库 data/memory.db 未被触碰」，
  功能 22 项全过）；**run3 23/23（exit 0，15.1s）**。证据：`s1-task14-e2e.log`、
  `e2e-real-frame-evidence.json`、`s1-task14-e2e-keep.log`、`e2e-real-frame-evidence-keep.json`、
  `s1-task14-e2e-run3.log`、`e2e-real-frame-evidence-run3.json`

### 2) 逐项核对（run3 数字；run1 同量级）

| 声称项 | 实测 | 判定 |
|---|---|---|
| 冷启动 lifespan ON、startup complete、bootMs≈2.5s | run3 `bootMs=2507ms / wall=2536ms`（run1 2517/2548，--keep 2979）；`--keep` 的 `server.log` 确有 `Application startup complete.` 与 `Uvicorn running on http://127.0.0.1:8001`；无 `--lifespan off` | ✅ |
| 无 Key Mock、health offline=true、链路真跑通 | 子进程 `LLM_API_KEY=''`；health `ok=true, llm.offline=true, provider=openai, model=deepseek-chat, memories=0, proactivity=false`；WS 文本派单后真实帧到 blocked | ✅ |
| 真实 WS 帧 drafting→briefing→running→blocked（含 blocked_question） | 独立采集连接（Node undici WebSocket 直连 8001 `/ws`，与浏览器分开）收到 **6 帧**，**同一新 id `c_d89a20255410`**：`atMs=3414 drafting` → `3415 briefing` → `3415 running` → `3415 blocked`（blocked_question=`笔录官 没有配置执行体…`）→ `4421 running` → `4421 blocked`（D7 的 answer 后续跑） | ✅ |
| 真帧端到端：浏览器世界出现真实 id、toWork、pushLedgerCalls=0 | 临时 Vite 5189→8001、`?ledger=on`；世界快照 `tasks[0].id=c_d89a20255410`（与帧 id 相同）、`kind=writing`、`villagerMode=toWork`、`villagerTaskId` 相同、`ledger.connected=true/complete=true`、**`pushLedgerCalls=0`**、`pageerror=0`；状态行「赶往 抄书斋…台账在线」 | ✅ |
| D7 未验证（manual+无 Key 到不了 ready_to_report） | `POST …/answer` → 200；随后 running→blocked，最终 `finalStatus=blocked`；证据 JSON `d7.verified=false` + reason；脚本只 `check(true, 'D7 标注未验证…')` 记录，没有伪造通过 | ✅ 如实未验证 |

### 3) 断言可信度（是否"脚本自己说绿"）

- **真 WS vs 注入**：采集连接是脚本内独立 `new WebSocket('ws://127.0.0.1:8001/ws')`，与浏览器无关；
  `--keep` 的 `server.log` 显示 **2 条 `WebSocket /ws [accepted]`**（采集 + 浏览器）、
  `GET /api/executive/commissions?limit=100`、`GET /api/executive/roster`、`POST …/answer` 200，链路对得上
- **pushLedger 计数可信**：页面侧 `window.Oracle.pushLedger` 真实存在（`SandboxScene.ts:225/241`），
  脚本在发文本前把它包一层计数；全脚本对 pushLedger 的引用只有这层包装（grep 5 处均为注释/包装/断言）。
  世界的真实帧走 `LedgerClient.applyPush`（`ledger.ts:198/245`），不经 pushLedger → **0 可信**
- **不是旧缓存/初始态**：临时根 `data/` 是全新库（`tempRoot/data/` 下自建 `memory.db/commissions.db` 等），
  帧 id 由本轮文本产生；浏览器任务 id 与帧 id 完全一致；帧时间全部在发送文本（约 3.4s）之后
- **23/23 的构成**：22 项功能性断言 + 1 项「D7 标注未验证」的记录项（无条件 `check(true)`）。
  记录项不算功能验证，但 D7 的 `verified=false` 是如实记录，不是伪造通过

### 4) 隔离性（含一次并发假失败）

- **run1/run3**：仓库 `data/commissions.db`、`data/memory.db` mtime 前后**完全相同**
  （run3：`1792377786785.2737 / 1792388950918.0684` 前后一致）；8000 PID **22996**、启动时间
  2026-10-18 16:40:34、health `200→200` 全程不变（脚本只对 8000 做 GET /health）
- **run2 唯一失败**（`仓库 data/memory.db 未被触碰`）已定位为**外部并发写**，不是临时实例：
  - `--keep` 证据显示临时实例全部 DB 写在 `tempRoot/data/`（memory.db 143,360 B、commissions.db 28,672 B
    等，mtime 13:48:57–13:49:07）；
  - 同时段仓库 `pixel-world/eval/screens` 被另一进程改写成 1 B 文件（13:48:45–13:49:03），提示有并发作业；
  - run2 后静默监视 30s，仓库 memory.db 无写入；随后 run3 前后完全一致并 23/23
  - 归因：8000 的会话/记忆写入或其他 Agent 的并发 smoke，非 e2e 临时实例
- **收口**：run1/run3 自动清理临时目录；`--keep` 目录在查完日志后手工删除（`tempRoot exists=False`）；
  `Get-ChildItem %TEMP%\aqi-e2e-real-frame-*` = 0；8001/5189 无监听（run3 evidence
  `portsReleased={8001:true,5189:true}` + 实际 netstat 复核）；仓库无临时 Vite 配置/无 `.tmp*` 残留；
  新增仓库文件只有 `pixel-world/scripts/e2e-real-frame.mjs` 与 `docs/E2E_REAL_FRAME.md`

### 5) 与文档声称的差异（小）

- `docs/E2E_REAL_FRAME.md` 写「启动 2512ms」「帧 3316/3317/4247ms」，那是作者那一轮的数值；
  本次复跑得 2507/2517/2979ms 与 3414/4421ms —— **量级与顺序可复现，毫秒数因机器负载浮动**，不是矛盾
- 文档称「复跑 23 passed」：本次 2/3 次是 23/23，另 1 次仅隔离断言受外部并发影响；功能项均通过

### 6) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（测试稳健性） | 隔离断言只看仓库 `data/*.db` 的 mtime，**会被 8000/其他 Agent 的并发写触发假失败**（本次 run2 遇到） | 建议：失败时区分"临时实例写"与"外部写"（例如同时记录进程/文件大小、或在安静窗口复跑）；或在证据里标注可重试 |
| P3（断言强度） | 帧没断言「全部同一 id / title 与派单文本一致 / atMs 递增」，理论上一条旧广播也可能满足现有断言 | 本次 QA 手工核对证据（单 id、标题逐字一致、时间递增）；建议脚本补这三条断言 |
| P3（计数口径） | 23/23 中 1 项是「D7 标注未验证」的记录项，不是功能断言 | 报告时说明 22 功能 + 1 记录，避免误读为 23 项功能全验 |
| P3（运维） | `--keep` 不会自动删临时目录 | 文档已写明；QA 查完日志后手工清理 |

**总评：通过。** 三项未验证（D4/D1/D3）都有独立可复现证据；真 WS 帧与浏览器世界同 id、pushLedger=0、
无注入；D7 如实标注未验证；隔离与端口收口完整（个别外部并发写会干扰 mtime 断言，已定位为环境问题）。

### 7) 复跑命令（QA 使用）

```powershell
cd D:\project\neurosama\pixel-world
$env:E2E_EVIDENCE = 'C:\Users\18712\.dsh\bot\workspace\recon\e2e-real-frame-evidence.json'
node scripts/e2e-real-frame.mjs            # 期望 23 passed, 0 failed
Remove-Item Env:E2E_EVIDENCE
# 查日志时：node scripts/e2e-real-frame.mjs --keep，然后读证据 JSON 的 coldStart.tempRoot，查完手动删目录
```

---

# 卡 8：S1-15（task-15）pickActive 接线

## 结论：**通过**（2026-10-19 13:56–14:00 执行）

### 1) diff 复核

- `projection.ts`：新增 `ordered: readonly WorldTask[]` 与 `orderActive(tasks)`；排序键
  `blocked(0) → running(1) → auditing(2) → 其它(3)`，同级 `commissionId.localeCompare` 稳定；
  `active` 改为 `ordered[0]`，`pickActive()` 委托给 `orderActive()[0]`，旧调用方不变
- `ledger.ts`：`LedgerView.order = projected.ordered.map(commissionId)`；`publish()` 同时透出
  `status` / `blockedQuestion`（task-16 用）与 `order`
- `simulation.ts`：`sync` 命令新增可选 `order?: readonly string[]`；`ledgerOrder = order`；
  `nextLedgerTask()` 只取 `source==='ledger' && status==='assigned'`，按 `ledgerOrder.indexOf(id)` 排序、
  不在 order 里的 `MAX_SAFE_INTEGER`、稳定排序保持插入序；`assignFromLedger()` 仅 `isFree()` 时派一条
  → **单任务上限语义不变**
- `SandboxScene.ts`：把 `view.order` 透传进 `sync` 命令
- 无 order（旧服务/旧快照/旧测试调用）时 `order ?? []` → 所有候选同 rank → 稳定排序=插入序，行为与改造前一致

### 2) 单测与回归

- `npm run check` → **exit 0**
- `npm test` → **90/90 pass、0 fail**（node 351.6 ms / 墙钟 842 ms）；task-15 新增 5 项 `ok 40–44`
- 沙盒 smoke **53/53**（32.8 s）；台账 smoke **21/21**（7.3 s，8000 只读、未重启）

### 3) QA 独立对抗矩阵（`recon\qa-task15-order-probe.mjs`，16/16 ALL PASS）

| 场景 | 实测 |
|---|---|
| 多状态混排（2 blocked + 2 running + auditing + briefing + done） | `blocked_a, blocked_z, run_a, run_b, audit, brief`；done 不进 ordered |
| 同级 id 稳定 | 两种输入顺序都得 `c_a, c_z` |
| `active`/`pickActive`/`orderActive` 一致性 | 三者一致，active=ordered[0] |
| 带 order 派活 | blocked 优先被派；另一单仍 `assigned`（单任务上限不变） |
| 无 order 兼容 | 插入序 `c_b` 先；旧调用行为不变 |
| order 里有未知 id | 未知排最后，同级保持插入序（`c_y` 先） |
| 撤单后重排 | 撤 `c_a` 后下一拍自动接 `c_b`；被撤单出清单 |
| **task-13 保护** | 截断快照（complete=false）不撤单 |
| **task-11 完成权** | 台账单人工完成被拒「完成权在台账」，任务仍在 |
| **task-12 回退** | 台账 smoke 21/21 走 8000 旧服务：`/roster 404 → /adapters` 回退链路未回退 |

### 4) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | order 里若混入已收工/不存在 id，只影响该 id 的 rank，不会让收工单重新进清单 | 语义安全；可在 `nextLedgerTask` 里过滤 order 到候选集合，纯优化 |
| P3（信息） | 无 order 时 rank 全为 MAX，依赖 `Array.sort` 稳定（V8 已保证） | 显式用插入序兜底更稳，但当前行为符合验收 |

**总评：通过。** 台账顺序真正驱动世界派活，同级稳定、无 order 兼容、撤单重排、单任务上限与既有保护均实测。

---

# 卡 9：S1-16（task-16）blocked_question 透传只展示

## 结论：**通过**（2026-10-19 13:56–14:01 执行）

### 1) diff 复核（含 projection 未改说明）

- `types.ts`：`TaskRecord.blockedQuestion: string | null`；`BLOCKED_QUESTION_LIMIT = 60`；
  `excerptBlockedQuestion()` 用 `Array.from(text)` 按 **Unicode 码点**取 60 再拼 `…`（surrogate pair 不会被劈开），
  先 `trim()`，null/空白返回空串；快照保留完整原文
- `ledger.ts`：`LedgerView.tasks` 增加 `status` 与 `blockedQuestion`，`publish()` 从 `project()` 映射透传
- `simulation.ts`：`sync` 命令增加可选 `status`/`blockedQuestion`；只有 `status==='blocked'` 且问题非空才进
  `blockedQuestions` map；所有既有 ledger 任务按 map 对齐（**转 running/空问题/收工/撤单一律清 null**）；
  新任务落 `blockedQuestion`；`abandonActiveTask()` 显式清 null；sync 回执追加
  `在等你的答复：<60码点>（仅展示，世界不代为回答）`
- `SandboxScene.ts`：状态行找 `tasks.find(blockedQuestion !== null)`，追加
  `在等你的答复：excerptBlockedQuestion(...)`；未新增任何输入/按钮
- **`projection.ts` 未为本卡改动（说明原因）**：`WorldTask.blockedQuestion` 与
  `parseCommissionRow` 的 `blocked_question` 解析在改造前就存在；本卡只是把它从 LedgerView 继续透传到
  TaskRecord/快照。当前 `projection.ts` 的 diff 全部属 task-15 的 ordering 与 task-12 的 agent 兼容，**无本卡改动**
- 无回答入口：`WorldCommand` 仍只有 dispatch/complete/setWeather/build/sync；index.html 只有 `#utterance`
  一个输入与既有按钮；源码无 `/answer` 调用（`e2e-real-frame.mjs` 的 POST answer 属 task-14 D7 探测，不是世界 UI）

### 2) 单测与回归

- `npm run check` exit 0；`npm test` **90/90**（task-16 新增 `ok 45–47` 与 `ok 73`）
- 沙盒 smoke **53/53**；台账 smoke **21/21**（新增 5 项：`blocked_question 进了世界快照`、`状态行写出在等你的答复`、
  `同步回执写出在等你的答复`、`转 running 后答复等待被清除`、`收工后带问题的单退出活动清单`）

### 3) QA 独立探针

**纯逻辑 27/27**（`recon\qa-task16-blocked-probe.mjs`）：
- 截断：61 个 `😀` → 60 emoji + `…`（`Array.from` 计数 61 码点）；**无孤立 surrogate**；恰好 60 码点不加省略号；
  混合 `a...😀` 在 emoji 边界不劈开；null/空白→空串；首尾空白 trim
- 快照三路径：store（完整原文保留）→ clear（转 running / 空白问题 → null）→ remove（收工后任务移除、无残留）；
  截断快照仍不撤单（task-13 保护）
- LedgerClient fetch stub：**全程只有 GET（2 次）**，URL 仅 commissions/roster，**无 answer/reply/respond**；
  `applyPush` 透传完整 blockedQuestion 且不产生新请求
- `parseOracle('答复：…')`/`回答：…`/`reply: …` → **unknown**（不触发回答）

**浏览器 17/17**（`recon\qa-task16-ui-probe.mjs`，dev 5188 + 8000）：
- blocked 帧 → 快照 `blockedQuestion` 完整；状态行出现「在等你的答复：先按短句还是完整句式？」
- 61 emoji 问题 → 快照保留 61 码点原文，状态行显示 60 emoji + `…`，**无孤立 surrogate**（截图 15）
- 转 running → 快照 null、状态行等待提示消失（截图 16）；空白问题不显示
- 「答复：短句」仅得 unknown 回执，世界未进入交付；页面只有 `#utterance` 一个输入、无回答按钮/
  `[data-answer]`、表单无 answer action；所有 `/api/executive/*` 请求均 GET、无 answer/reply/respond；无 JS 报错

### 4) 加分：真帧复核（task-14 e2e，无注入）

- `node pixel-world/scripts/e2e-real-frame.mjs` 复跑：**23/23 pass**（证据 `s1-task16-e2e-real.log` /
  `e2e-task16-evidence.json`）
- 独立 WS 采集到的真实 blocked 帧：id `c_fe5ca701b80e`，
  question=`笔录官 没有配置执行体，本单不会自动执行。。要我再催一次，还是这单算了？`
- 同一次运行的浏览器快照：`taskId=c_fe5ca701b80e`、`pushLedgerCalls=0`，状态行已出现
  「在等你的答复：笔录官 没有配置执行体…」
- 为拿到完整快照字段，用 e2e 的临时副本（仅加一行输出、其余逻辑不变）再跑一次：浏览器
  `fullTasks=[{id:'c_5893b24d22a8', status:'assigned', blockedQuestion:'笔录官 没有配置执行体…'}]`，
  与同 run 的 WS 帧 id/question 逐字一致，`pushLedgerCalls=0`（证据 `e2e-task16-snapshot-evidence.json`）
- 结论：blocked_question 确实来自**真实帧**，经 LedgerView → simulation → 快照 → 状态行，无注入

### 5) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（防御性） | 状态行用 `tasks.find(blockedQuestion !== null)`，没有同时判 `status==='blocked'` | 当前由 simulation 的清除逻辑保证不变量；建议加 `status==='blocked'` 双保险 |
| P3（信息） | 状态行在 `toWork`/`saluting` 等非 blocked 的 villager mode 下也会显示等待提示 | 这是"有单在等答复"的全局提示，语义可接受；若产品要求只在 blocked 时显示，需再定义 |
| P3（信息） | 按**码点**截断，组合 emoji（ZWJ 序列/带变音符号）仍可能被切开 | 需求就是 60 码点；如需按 grapheme 截断要另引库，超出范围 |
| P3（信息） | 快照保留完整原文，只有展示层截断 | 符合"完整值留快照、展示截断"设计；日志/回执也只展示截断 |

**总评：通过。** 全链路透传、只展示不回答、60 码点截断（含 surrogate）与三条清除路径均有独立证据；
真帧复核确认 blocked_question 来自真实 WS 帧而非注入。

---

# 卡 10：S1-10（task-10）排队 + 取消未出发行礼

## 结论：**通过**（2026-10-19 14:24–14:28 执行；工作区写入已停止后正式复验）

### 1) diff 复核

- `types.ts`：新增 `PendingDispatch`（`id` 自增 p1…、`kind`、`summary`、`requestedAt=this.now`、
  `source:'oracle'`、预留 `villagerId`）；`WorldSnapshot.pending`；`OracleIntent` 增 `cancelPending`
- `simulation.ts`：
  - 私有 `pending: PendingDispatch[]` + `nextPendingNumber`；`MAX_PENDING_DISPATCHES = 3`
  - `dispatch()`：忙时不拒单 → `enqueuePendingDispatch()`；未满回执「神谕已排队（第 N 位）」，
    满 3 回执「队列也满了（3 条）；这条先不接」且 ok=false
  - `pumpPending()` 在 `update()` 固定步长末尾调用：先 `assignFromLedger()`（台账真实活优先），
    空闲后才取 `pending[0]` FIFO 开工；派不下去就留在队首下一拍再试
  - `cancelPending(id?)`：省略 id 撤最后一条、按 id 撤、空队列/未知 id 拒绝；只动等待队列，不碰在干的活
  - `clearDeparture()`：`saluteUntil=0 / pendingRoute=[] / pendingBuilding=null`，在
    `finishInPlace / finishActiveTask / abandonActiveTask / arriveAtAltar` 四条路径调用
    （修掉旧 `:366/:729` 不清 pendingRoute 的隐患）
- `oracle.ts`：增 `CANCEL_WORDS`（撤销/取消/cancel/undo），排在 weather 之后、quick/dispatch 之前
- `SandboxScene.ts`：`cancelPending` 命令接线；状态行追加「排队 N 条：<首条 summary>」
- `smoke.mjs`：把旧的「忙时拒单」断言改为「进队列 + 状态行可见 + 交付后自动开工」；
  里程碑断言从 `===1` 放宽为 `>=1`（队列让 Mock 交付多一单，属合理调整，记为 P3 观察）

**未回退 task-13/task-15/task-16**：diff 只有新增/局部替换；`complete` 完成权、ledger `order`、
`blockedQuestion` 透传代码路径均保留（交叉探针与 smoke 21 项验证）。

### 2) 自跑与对抗

| 检查 | 实测 |
|---|---|
| `npm run check` | **exit 0** |
| `npm test` | **96/96 pass、0 fail**（node 365.4 ms / 墙钟 903 ms）；新增 7 项 ok 79–85 |
| 沙盒 smoke（本工作区 dev，5188） | **59/59 通过**，exit 0，37.0 s |
| 台账 smoke（8000 只读） | **21/21 通过**，exit 0，7.4 s |
| `check_protocol.py` | exit 0，PASS |
| `test_core.py` | **64 passed / 0 failed**，exit 0 |
| dev 归属核对 | 5188 监听进程命令行 = `D:\project\neurosama\pixel-world\node_modules\.bin\..\vite\bin\vite.js`（**本工作区**，非旧快照） |

**QA 独立对抗探针 24/24 ALL PASS**（`recon\qa-task10-queue-probe.mjs`）：
- 忙时新神谕进队列（不拒单）；`pending` 2 条、`tasks` 仍 1 条（不提前进台账）；
  pending 字段 `p1/p2`、`source=oracle`、`villagerId=v0`、`requestedAt` 齐全；回执第 1/2 位
- 上限内 3 条都成功、第 4 条被拒「队列也满了」；撤最后一条、按 id 撤、撤不存在 id、空队列撤被拒；
  撤销不影响正在干的活
- 交付后排队第一单自动开工（FIFO），队列剩第二单
- **确定性**：两个 sim 同命令同步长，逐拍 `JSON.stringify(snapshot)` 完全相等；
  快照出现过 `pending/requestedAt`、id 为自增 `p\d+`（无墙钟/随机）
- **台账优先**：sandbox 忙碌时 oracle 排队 + sync 台账单 → 空闲后先派 `c_ledger`，oracle 继续等；
  台账收工后 oracle 才自动开工
- **行礼中 sync**：收工 → `delivering`（不残留 saluting/旧路线）；撤单 → 回 `idle`、任务移除，
  再推进 20 拍仍无旧目标（旧行礼计时器已取消）

**交叉回归**：task-15 保护探针 **16/16**（含截断不撤单、完成权在台账）、
task-12 roster 探针 **8/8**（旧服务 `/roster 404 → /adapters` 回退未破坏）。

### 3) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（断言口径） | smoke 里程碑断言由 `===1` 放宽为 `>=1` | 队列让 Mock 交付多一单、里程碑提前到第 2 次；可接受，但建议改成"里程碑与交付数对应"的更精确断言 |
| P3（信息） | 状态行只显示排队第一条 summary | 有 N 条时只给首条；如产品要求可加"等 N 条" |
| P3（信息） | `cancelPending` 只作用于等待队列（仅 oracle） | 符合设计（台账单不可被神谕撤）；台账撤单仍走 sync |

**总评：通过。** 忙时排队/上限/撤销/FIFO/台账优先/确定性/行礼取消全部有独立证据；
96 项单测、59+21 冒烟与主产品门禁均回绿。

---

# 提交与重构复验（340d0ab / b656488 / 150b2a7）

## 结论：**通过**（1 条 P3：npm 缺失仍 traceback）

### 1) 提交范围（静态）

| 提交 | 文件数 | 结论 |
|---|---|---|
| `340d0ab`（S1 代码+文档） | **25**，+2927/−82 | 不含 `pixel-world/src/main.ts`、不含 `server/assistant/executive.py`；server 仅 `server/app.py` + `server/assistant/executive_api.py`；scripts 仅 `scripts/test_ws_origin.py`；其余为 pixel-world/文档 |
| `b656488`（D7 门禁） | **2** | `.github/workflows/pixel-world.yml` + `scripts/test_pixel_world.py` |
| `150b2a7`（门禁统一） | **2** | workflow + 门禁脚本（见 §3） |

- 提交链：`21c1b12 → 340d0ab → b656488 → 150b2a7`
- 当前主产品 36 个 dirty 文件（.env/config/web/assistant/root scripts 等）与 `340d0ab` **交集 0**，
  没有夹带；340d0ab 中出现的 pixel-world/S1 文件后来被 task-10 再次修改属正常未提交 WIP，不是夹带

### 2) 重构正确性（静态 + 8001 运行时）

- `git grep roster_view` 在 HEAD 与工作区均 **0 命中**；`git diff -- server/assistant/executive.py`
  = **136+/5−**，只剩既有 durable 改动（`bind_durable`/`recover_interrupted`/`_adopt_durable` 等）
- `executive_api.py` 的 `/roster` **自包含**：`from .dispatcher import roster_agents` + 读
  `executive.roster`，不依赖 `executive.roster_view`；`executive_api.py`、`server/app.py` 工作区 diff 为空
- **8001 临时实例（当前代码，`--lifespan off` + 临时 DB_PATH）实测**：
  - `GET /api/executive/roster` → 200、**823 字符 / 7 条**，与重构前基线
    `recon\refactor\roster.before.json` **`-ceq` 逐字一致**（基线 before/after SHA256 相同
    `486DDAE7…`；文件 1141 B vs 内容 823 字符，Lead 所述“823 字节”按字符口径成立）
  - `GET /api/executive/adapters` → 1478 字符，与 **8000 旧进程**及**重构前基线**
    `s1-task12-adapters-8001.json` 均 `-ceq` 一致（结构未变）
  - `/roster` 敏感词扫描（endpoint/executable/env/token/secret/http/Bearer）0 命中
  - 8000 全程 PID **22996**、start 2026-10-18 16:40:34、health 200，未重启/未写台账

### 3) D7 门禁（本地 + CI）

- `python scripts/test_pixel_world.py` 连跑 **3 次：PASS/PASS/PASS**（3.1/3.1/2.9 s），
  每次 `# tests 96 / # pass 96 / # fail 0`，exit 0
- 门禁只跑 `npm run check` + `npm test`（cwd=pixel-world），**无浏览器/端口依赖**；继承 stdio 规避
  Windows pipe EPERM；聚合失败返回 1
- `150b2a7` 后 CI 统一为 `python scripts/test_pixel_world.py`（不再两处重复命令）：
  `setup-node 22` + `setup-python 3.12` + `npm ci (working-directory: pixel-world)` + 调脚本；
  push/PR paths 仍只 `pixel-world/**`、`scripts/test_pixel_world.py`、workflow 自身（合理）
- 提交级干净快照（`git archive 340d0ab` 到工作区隔离目录）另跑：门禁 **3/3 PASS、90/90**，
  smoke **53/53 + 21/21**，`/roster` 与基线一致——用于排除 task-10 写入期间的中间态污染
- **P3 发现（与 150b2a7 提交说明不符）**：把 `PATH` 清空后跑门禁，仍出现
  `OSError: [WinError 87]` **Traceback**（退出码 1，安全失败）。原因：`npm()` 返回 `""` 后
  `subprocess.run(["", "run", "check"], ...)` 直接抛异常，没有先判空。
  **该 P3 已由 8a95c94 修复，见 §5**。

### 4) 并发期说明（为什么不采信当时的工作区单测）

14:19 左右 task-10 正在写入 oracle/simulation/types/SandboxScene/smoke/world.test，
期间原地 `npm run check/test` 出现一次 exit 2 与 89/90（正常中间态）；提交级结论改用
`git archive 340d0ab` 隔离快照验证，未受写入影响。task-10 落完并静默后，本工作区复跑
**96/96、59/59、21/21、门禁 3/3** 全绿。

### 5) 后续提交 `8a95c94`（P3 修复）

- 范围：**仅 `scripts/test_pixel_world.py` +4 行**（`npm()` 为空时直接 `FAIL npm not found` 并返回 1），
  无其它文件
- 复核：`PATH` 清空后运行门禁 → 输出 `FAIL npm not found …`、**exit 1、无 Traceback**
  （`recon\task17-gate-npm-missing.log`）→ §3 的 P3 已关闭
- 正常门禁复跑仍 PASS（task-17 期间 96/96；见卡 11）

---

# 卡 11：S2-1（task-17）多角色（独立作息 / 派活 / 排队 / 归属）

## 结论：**通过**（2026-10-19 14:37–14:41 执行；task-17 改动仍为未提交工作区）

### 1) diff 复核

- `types.ts`：`TaskRecord.workerId`（归属）；`VillagerSnapshot.id`；`WorldSnapshot.villagers`
  且 `villager` 保留 = `villagers[0]`（旧断言兼容）；`PendingDispatch.villagerId` 真正启用
- `simulation.ts`（649 行变更）：
  - `villagers: Villager[]`，构造函数 `villagerNames ?? [villagerName, 助手「阿雾」]`，id 为 `v0/v1/...`；
    每角色独立 `mode/pos/path/taskId/routine/saluteUntil/deliverUntil` 等状态
  - 错开作息：`nextRoutineAt = 1200 + index*1400`、`routineIndex = index*2` → 两人目的地/节奏不同
  - 队列：`pending: Map<villagerId, PendingDispatch[]>`；`enqueuePendingDispatch()` 选排队最少的角色，
    每角色上限 3（两人都满才拒）；`pumpPending()` 对每个空闲角色先 `assignFromLedgerTo()`（台账优先），
    再取自己 FIFO 队首
  - 台账派活：`assignFromLedgerTo` 用 `nextLedgerTasks()`（`workerId === null`）+ `buildingBusy()` 防同铺重复，
    派前检查施工/故障/路径；`workerId` 在接单时写入
  - 完成/归属：`finishActiveTask/finishInPlace` 按 `task.source` 记全局 ledger/mock 交付；
    `completeManually()` 找第一个 `working` 的角色（台账单仍拒绝）
  - `cancelPending(id?)`：按角色队列操作；省略 id 从后往前撤第一个非空队列的队尾
- `SandboxScene.ts`：`Map<string, VillagerView>` 按快照 id 对齐多实例；气泡去重改 per-id；
  状态行改多角色摘要（`莞尔：…　/　阿雾：…`），其余（天气/台账/交付/神恩/等待答复/排队）保留
- `smoke.mjs`：新增「两个小人 / 派活不串任务 / 双忙排队 / 自动开工 / 所有单收尾」等断言
- **`villager.ts` 未改，结论成立**：`createVillager/updateVillager` 本来就是"一个实例一个 view + 一份快照"，
  module 内无共享可变状态；多实例管理放在 `SandboxScene` 的 Map（按 id 对齐）即可，无需改渲染层。
  除 `report.ts` 仍读 `snapshot.villager`（主角色，见 P3）外，`src/` 无其它单角色假设

### 2) 自跑与对抗

| 检查 | 实测 |
|---|---|
| `npm run check` | exit 0 |
| `npm test` | **100/100 pass、0 fail**（node 374.0 ms / 墙钟 900 ms）；新增 11 项 ok 79–89 |
| 沙盒 smoke（本工作区 dev，5188） | **66/66 通过**，exit 0，50.3 s；dev 命令行确认为 `D:\project\neurosama\pixel-world` |
| 台账 smoke（8000 只读） | **21/21 通过**，exit 0，7.2 s |
| `check_protocol.py`（venv python） | PASS |
| `test_core.py`（`.venv\Scripts\python.exe`） | **64 passed / 0 failed** |
| 视觉证据 | `eval\screens\01-salute.png` 同屏可见两名小人（莞尔行礼 + 阿雾在街口），状态行 `莞尔：正向神谕行礼　/　阿雾：在街口站着看了一会儿` |

**QA 独立对抗探针 26/26 ALL PASS**（`recon\qa-task17-multivillager-probe.mjs`）：
- 默认 2 角色；开局作息标签不同、位置/路线独立；派活只叫一个，另一人继续日常
- 第二单交给另一个空闲角色；两单 `workerId` 分别为 v0/v1、`buildingId` 不同、不串任务
- 双忙时共 6 条队列（每人 3 条）都成功，第 7 条被拒「队列也满了」；排队不影响在干的两单
- 两人各自交付：`deliveries.mock=2, ledger=0`，`workerId` 保留可追溯，无串单
- 3 角色：传 `villagerNames` 3 个名字 → v0/v1/v2；3 单分别派 3 人；3 人都忙时第 4 单进队列
- 两 sim 同命令同步长快照 **deepEqual**
- 未回退：台账 `order` 先派高优先单、`blockedQuestion` 透传、同铺占位不重复、台账单人工完成仍被拒、queue 仍工作

### 3) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | `report.ts` 的日报/日记仍只写 `snapshot.villager`（主角色） | 助手交付不会进日报/日记；多人世界建议按 `workerId` 汇总或遍历 `villagers` |
| P3（信息） | `completeManually()` 取第一个 `working` 角色 | 两人同时干活时单按钮无法指定收尾对象；UI 入口限制，逻辑本身未串单 |
| P3（信息） | 3 角色只有 `Simulation` API（`villagerNames`）支持 | `SandboxScene`/`main.ts` 没有配置入口，要 3 人仍需改代码；文档已注明"传 3 个名字即可扩" |
| P3（信息） | `cancelPending()` 省略 id 时从最后角色的队尾撤 | 与单角色时"最后一条"语义略有差别（非全局入队时间序）；有 id 时行为明确 |

**总评：通过。** 两个角色拥有独立作息/任务/队列/计时器，派活、归属、统计、确定性均实测正确；
50 项渲染冒烟 + 双人截图证明渲染层多实例成立；既有 complete/order/blocked/queue 行为未回退。

---

# 卡 12：task-21（多角色 P3）日报归属 + 手动完成对象

## 结论：**通过**（2026-10-19 14:54–14:58 执行；task-21 改动仍为未提交工作区）

### 1) diff 复核（范围）

只改：`report.ts`、`simulation.ts`、`types.ts`、`SandboxScene.ts`、`oracle-panel.ts`、`smoke.mjs`、
`test/surface.test.ts`、`test/world.test.ts`；**未改** `ledger.ts` / `projection.ts` / `server/*`
（工作区里 main.ts 与 server 的改动是既有遗留，不是本卡）。

- **日报归属**：`report.ts` 先按 `deliveriesByVillager` 逐人写，再在每个人名下分「台账 N / Mock M」；
  `villagerTail` 改为遍历 `villagers`：全闲写「A、B都在祭坛边坐着」，全忙写「A手上是「x」，B手上是「y」」，
  混合写「…，X在祭坛边坐着」；headline 仍按全局交付数（与正文一致）
- **手动完成对象**：`WorldCommand` 的 `complete` 增 `target?: string`；`completeManually(target?)`
  按 id 或 name 精确匹配，未知目标拒绝、目标空闲拒绝、台账单仍拒绝；成功回执写「（{name}，…，不计入台账）」
- **UI/接口**：`OracleApi.finish(target?: string)`；`SandboxScene.finish(target?)` 传给 sim；
  状态行在有 `working` 角色时追加「默认交付：{name}的单」；working 文案带任务摘要
- `villager.ts` / 渲染层未改（本卡不涉及）

### 2) 新增字段 / 接口清单（供 ADR schema 冻结）

**A. 快照结构（`WorldSnapshot`）新增**
- `deliveriesByVillager: readonly VillagerDeliveries[]`（**新增**；顺序与 `villagers` 一致）
- `VillagerDeliveries { villagerId: string; name: string; ledger: number; mock: number }`（**新增**）
- 既有归属相关字段（引用，不新增）：`deliveries { ledger, mock }`（全局按来源）、`tasks[].workerId: string|null`、
  `villagers[]`（每项含 `id`）、`villager`（兼容 = `villagers[0]`）、`pending[].villagerId`、
  `ledger { connected, lastSyncAt, mock, complete }`。**没有名为 `settled` 的字段**；归属结算由
  `deliveriesByVillager + deliveries + workerId` 表达

**B. 命令 / JS 接口新增**
- `WorldCommand`: `{ kind: 'complete', target?: string }`（**新增可选 `target`**；值为角色 id 或 name）
- `OracleApi.finish(target?: string): void`（**新增可选参数**；`window.Oracle.finish` 同步暴露）
- `Simulation.completeManually(target?: string)`（private）；内部 `deliveryTally: Map<villagerId, {ledger,mock}>`
- 既有（不新增）：`dispatch` / `cancelPending(id?)` / `sync` / `finish()`（省略 target 走旧行为）

**C. 输出字符串契约（供前端/日报消费，非结构体）**
- 日报行：`{name}交付 {total} 单（台账 {n} / {Mock|离线 Mock} {m}）`，多角色用 ` / ` 连接（0 单写 `{name}交付 0 单`）
- 日报收尾：全闲 `A、B都在祭坛边坐着`；全忙 `A手上是「…」，B手上是「…」`；混合追加 `X在祭坛边坐着`
- 状态行：`默认交付：{name}的单`（有 working 者时）；working 文案含 `「{summary}」`
- 回执：成功 `（{name}，{沙盒 Mock|离线 Mock}，不计入台账）`；未知目标 `没有叫「{target}」的角色`；
  目标空闲 `{name}现在没有在干活`；台账单 `这单的完成权在台账…`
- 未新增任何网络/服务端字段、无回答入口

### 3) 自跑与对抗

| 检查 | 实测 |
|---|---|
| `npm run check` | exit 0 |
| `npm test` | **106/106 pass、0 fail**（≥104；新增 7 项 ok 50–53、96–98） |
| 沙盒 smoke（本工作区 dev，5188） | **74/74 通过**（≥70，46.0 s） |
| 台账 smoke（8000 只读） | **21/21 通过**（未回退） |
| `check_protocol.py` / `test_core.py`（venv python） | PASS / **64 passed 0 failed** |
| 浏览器 UI 探针（15/15） | 见下 |

**QA 纯逻辑对抗 26/26**（`qa-task21-attribution-probe.mjs`）：
- 两人的 Mock 交付各记 1，`deliveriesByVillager` 顺序/名字与 `villagers` 一致；日报两人都出现、来源标签正确
- 混合来源：v0 mock=1、v1 ledger=1，全局 ledger/mock 未合并；日报行含两人且 `台账 1`/`Mock 1` 分列
- 日报收尾：全闲「都在祭坛边坐着」、全忙「两人手上是…」、混合「一人忙一人在祭坛」均可读
- 指定 `v0` 收工 → 只 v0 delivering、v1 仍 working、计入 v0；指定名字「阿雾」收 v1 → 计入 v1、总数 +1
- 未知 id → `没有叫「nobody」…` 且世界不动；目标存在但空闲 → 明确拒绝
- **台账单指定角色完成仍被拒**（完成权不回退）
- 两 sim 同命令同步长（含 target 完成）快照 deepEqual，且快照含 `deliveriesByVillager`

**浏览器 UI 15/15**（`qa-task21-ui-probe.mjs`）：
- 单人干活时状态行「默认交付：莞尔的单」；点 `#finish` 回执「成果已交付（莞尔，离线 Mock，…）」，
  只影响默认角色
- `window.Oracle.finish('nobody')` → 明确拒绝且世界不动
- 两人同时干活：指定 `finish('v0')` → v0 delivering、v1 working；再 `finish('阿雾')` → v1 delivering；
  两人分账各计各的（v0:2 / v1:1）
- 报纸/Oracle.report 均写出「莞尔交付 2 单（离线 Mock 2） / 阿雾交付 1 单（离线 Mock 1）」；全闲收尾措辞正确
- 截图 `17-task21-attribution.png`；浏览器零 JS 报错

### 4) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（接口/UI 差异） | DOM 的 `#finish` 按钮没有 target 选择入口（点击走省略 target 的旧行为）；指定对象只能经 `window.Oracle.finish(id/name)` | 当前用「默认交付：{name}的单」提示默认对象；若验收要求按钮层可选对象，需要加 UI（下拉/参数） |
| P3（信息） | 日报 Mock 标签用全局 `snapshot.ledger.mock`，非按角色历史 | 若某角色在离线期交付、之后台账恢复，标签会随当前状态变化；不影响计数 |
| P3（信息） | target 按 id/name 匹配，重名角色会命中第一个 | 当前默认名唯一；多角色命名约束可写进 schema 文档 |

**总评：通过。** 日报按人分账、不合并来源、助手不再漏报；手动完成可指定对象且未知目标/台账单均正确拒绝；
106 项单测、74+21 冒烟、主产品门禁与 UI 探针全部回绿。

---

# 卡 13：task-18（存档单槽 + 恢复，按 ADR_SAVE final 验）

## 结论：**通过**（2026-10-19 15:13–15:18 初验；15:24–15:28 经 task-23 复验后 P2 关闭）

- 初验唯一未达成的硬点是 **ADR §2.2/硬点 3 的「ledger completedAt 取台账 `closed_at ?? updated_at`」**：
  当时运行时恒为 `null`（`simulation.ts` 自认"当前投影未带"），格式层支持非空（冻结样例里 ledger 记录
  `completedAt=1700000000000` 且 `completedAtClock:'ledger'`）。**task-23 已把投影/LedgerView/WorldCommand
  接线并将秒×1000 写入 settled，QA 用真实 8000 台账行复验通过（见卡 14）→ 本条 P2 关闭**。
- 注意：task-23 复验同时发现一个新的 **P2（存档字段冻结漂移：运行时 toSave 多出
  `world.tasks[].ledgerCompletedAt`，未升 v2）**，归属 task-23、见卡 14；不推翻本卡主体结论。

### 1) 基准与交付

- 基准：`docs/ADR_SAVE.md`（final，§1 frozen）、`docs/save-v1.sample.json`（5680 B）、
  `SCHEMA_V1_FINGERPRINT = "f53a0736d782d657"`（`src/world/save.ts:25`）
- task-18 新增/改动（未提交工作区）：`src/world/save.ts`、`src/ui/save-store.ts`、
  `test/save.test.ts`、`test/save-fingerprint.test.ts`、`test/save-quota.test.ts` +
  `simulation.ts/types.ts/setup.ts/SandboxScene.ts/oracle-panel.ts/index.html/smoke.mjs` 接线
- **存档 schema 清单（供 ADR/实现对齐）**：
  `WorldSaveV1 { schemaVersion:1, world: SaveWorld, meta:{ savedAtWallClock, savedReconciled, schemaFingerprint } }`；
  `SaveWorld` 字段 = `now/villagers/buildings/roads/props/tasks/pending/activities/constructions/weather/faith/
  settled/nextTaskNumber/nextPendingNumber`；`SettledRecord { id, source:'ledger'|'mock', workerId,
  completedAt:number|null, completedAtClock:'ledger'|'local' }`；storage key
  `xiaoxi.pixel-world.save.v1`；坏档备份前缀 `…save.v1.corrupt.<epochMs>`（保留 3）

### 2) 字段边界 + 指纹（硬点 1/2）

- 样例：`assertSaveV1` 通过；**206 条原始叶子路径 → 去重 99 条**；`fingerprintOfPaths(unique)=f53a0736d782d657`；
  `saveFingerprint(sample)=` 同值；`sample.meta.schemaFingerprint=` 同值；`leafPaths=206/unique=99` 与 ADR 一致
- 运行时 `WorldSnapshot` 序列化**不含 `"settled"`**（QA 实测 + smoke）；`toSave()` 才生成 `world.settled`
- **QA 真实变异**：给实际 `docs/save-v1.sample.json` 的 `world` 加一个字段 →
  `node test/save-fingerprint.test.ts` **红**（`expected 99, actual 100`，exit 1）；
  字节级还原后 SHA256 `F24FBAEF…298402`（与变异前一致，5680 B）→ 再跑 **3/3 绿**（证据 `task18-fingerprint-*.log`）
- 加载指纹不符：用 fake storage 写入 `meta.schemaFingerprint='deadbeefdeadbeef'` → `read()` 返回
  `corrupt`、生成带时间戳备份、主槽原字节保留（不静默重置）；高版本 `schemaVersion:99` → `version` 拒绝、
  `paused=true`、自动存档关闭、原槽字节不变、`write()` 被暂停（不降级写回）

### 3) settled 去重 / 分账 / 时钟（硬点 3）

- mock：`completedAt = wallClock()`、`completedAtClock:'local'`（QA 实测 6000；浏览器实测 epoch ms）
- ledger：`completedAtClock:'ledger'`、初验时 `completedAt` 恒 null（**task-23 已接线关闭，见卡 14**）；
  未用本地时间顶替（这一点满足）
- 存档不写计数：`fromSave` 由 `settled` 重建 `deliveries`/`deliveriesByVillager`（QA 实测 1 单 mock →
  `{ledger:0,mock:1}` 且按 workerId 归属）；重开后再 sync 不重复计数
- 样例里 ledger/mock 两条 settled 的时钟字段与源码一致（`ledger/1700000000000`、`local/1234567890123`），
  说明**格式层**已支持非空 ledger 时间，缺的只是运行时投影接线

### 4) 对账三分支（硬点 4）

- 读档后 `ledger.reconciled=false / connected=false / mock=true`；**首 sync 前不派台账单**
  （QA 观察 5 拍，`c_demo000001` 未被派；`pumpPending` 内有 `!ledgerReconciled` 保护）
- 首个 sync：`finished` 里已 settled id → **不重复交付/计数**；已 settled id 以活动单回来 → **不复活**；
  `complete=false` 缺席 → **保留、不撤单**；`complete=true` 缺席 → **丢弃、不复活**；
  活动单 kind/title/blockedQuestion 被台账覆盖；对账后 `reconciled=true`
- 在线（8000）浏览器：读档后状态行「读档未对账」，首个 sync 后变「台账在线」且 `reconciled=true`；
  `?ledger=off` 永久 `reconciled=false` 且状态行明示（smoke/浏览器均验）

### 5) 失败路径（硬点 5，QA 独立 32 项探针）

- 坏档 JSON → 备份原档（`corrupt.<epochMs>`）+ 提示一次，主槽原字节保留；连续 4 次坏档 → **只留最近 3 份**
- 配额紧张：假存储首次 `setItem` 抛错 → **先删最旧备份再重试，主存档成功**（`ok:true`，备份被清 1）
- 配额持续失败：`write()` **不抛**、返回 paused、自动存档关闭、提示一次；世界继续（测试断言）
- `setItem` 抛 `QuotaExceededError` 的单元测试 + 高版本只读 + origin/端口提示（`getItem` 抛错）全部 PASS
- `settled > 5000` → 只告警一次、不截断（测试 + QA 探针）
- **P3**：`SaveStore.enableAutoSave()` 存在且测试可用，但运行时**没有接线**（无 UI/重试入口），
  暂停后 `saveNow()` 也直接返回 paused → ADR §4「手动保存可重试 / 恢复后自动存档重开」当前只有 API 能力

### 6) 真机刷新续跑（BRIEF 验收 4，硬点 6）

- 离线 `?ledger=off`：交付 1 单 + 扩建 1 栋 + 雨/晴切换 → `saveNow()` → localStorage 单槽
  `schemaVersion:1`、指纹同常量、`settled.length=1`（mock/local）、`savedReconciled:false`；刷新后
  「建筑/道路/信仰/任务/交付/角色 id」结构一致，角色位置漂移 **0.9 格**（从关页那一刻续跑）
- **pagehide**：派单并交付后 `dispatchEvent(pagehide)` → `__saveWrites` 1→4、存档 settled=2（有写入证据）
- **30s**：真实等待 31s → 自动存档写入 4→5、`meta.savedAtWallClock` 更新（有写入证据）
- 在线 `?ledger=on`：读档 → 首个 sync 对账成功（`reconciled:true`），写档 `savedReconciled:true`；
  刷新后建筑 4 栋、mock 交付 2 单保留；全程零 JS 报错；截图 `18-task18-persist.png`

### 7) 回归

- `npm run check` exit 0；`npm test` **118/118 pass**（save 相关 ok 48–58 等）
- 沙盒 smoke **85/85**（≥80，含「手动存档/刷新后建筑/道路/神恩/按角色统计恢复/读档未对账/清档」）；
  台账 smoke **21/21** 不回退
- `check_protocol.py` PASS；`test_core.py`（venv）**64/0**
- 交叉探针：task-10 队列 24/24（单角色化口径）、task-15 16/16、task-17 26/26、task-21 26/26 →
  complete/order/blockedQuestion/queue/multi-villager/deliveriesByVillager 未回退

### 8) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P2** | ledger `settled.completedAt` 恒 null，未取 `closed_at ?? updated_at`（ADR §2.2 硬点 3） | 投影/WorldCommand 未带这两个字段；格式层已支持非空（样例可加载）。建议：`projection.parseCommissionRow` 增 `closed_at/updated_at` → LedgerView → sync → `recordDelivery` 用 `closed_at ?? updated_at`×1000，缺失才 null |
| P3 | 配额暂停后运行时无恢复接线 | `enableAutoSave()` 只有 API；建议 UI「重试保存」或下次成功写入自动重开 |
| P3 | `assertSaveV1` 形状校验较浅 | 未校验 `tasks.status==='done' ⊆ settled` 等写档不变量；指纹正确但内容异常的档可能部分加载（fromSave 静默跳过坏条目）。建议补写档不变量校验 |
| P3 | `saveStateKey` 只含 deliveries/faith.milestones/constructions/props/tasks 数 | 天气/角色位置等变化靠 30s 或 pagehide 兜底（符合 ADR 写入点定义，记录为信息） |

**总评：有条件通过。** 存档格式/指纹/备份/对账/降级/真机续跑全部实测通过；唯一硬缺口是台账时间未接线（P2），
其余为 P3 增强项。若用户/ADR 明确接受"投影未带→null"，可改判通过。

---

# 卡 14：task-23（台账时间戳透传 `closed_at ?? updated_at` → settled）

## 结论：**通过**（P2 已由 task-24 关闭；task-18 同步保持通过）

- 初验标记的 P2（`toSave()` 多出 `world.tasks[].ledgerCompletedAt`、未升 v2）**已由 task-24 修复**：
  落盘 task 键集合与样例完全一致（10 键、无 `ledgerCompletedAt`），并新增 `SAVE_TASK_KEYS`/`SAVE_SETTLED_KEYS`
  嵌套键集合防线 + `done ⊆ settled` 校验；QA 独立变异/去剥离复现均验证（见卡 15）。

### 1) 范围（硬点 1）

- task-23 改动（mtime 15:20–15:22）：`projection.ts`、`ledger.ts`、`types.ts`、`simulation.ts`、
  `SandboxScene.ts` + `test/ledger.test.ts`、`test/ledger-fixtures.test.ts`、`test/fixtures/commissions.json`；
  `save.ts`/样例未改（mtime 15:10，SHA 仍 `F24FBAEF…298402`）
- `git diff -- server` **非空，但全部是 task-23 之前就存在的既有 dirty**（server 最新 mtime
  `14:14:16`，早于 task-23 的 15:20；无 server 文件 mtime > 15:13）→ **task-23 未改 server**，
  server 返回结构未动；真实 API 响应仍带 `closed_at/updated_at`（下面实测）

### 2) 解析三分支 + 秒/毫秒（硬点 2）

- `parseCommissionRow`：`completedAt = epoch(closed_at) ?? epoch(updated_at)`；`epoch()` 只认有限数字，
  字符串/NaN/缺失→null；`closed_at` 优先并保留原始秒值（1792377786.5）
- QA 实测：closed 优先 ✓、closed=null 退 updated ✓、两者都缺→null ✓、非数字/NaN→null ✓
- `ledgerCompletedAtMs`：秒→`Math.round(×1000)`（1792377786.5→1792377786500）；已是毫秒（>1e12）原样；
  null/undefined/NaN→null；**没有任何本地时间兜底**

### 3) 运行时（硬点 3，真实 8000 只读 + 浏览器）

- 从 `GET /api/executive/commissions?limit=100` 取真实行：`closed_at=1792377786.7782023`；
  `parseCommissionRow` 得同值；`LedgerClient.finishedAt` 携带同值且与 `finished` 同集合
- 纯运行时：真实 id 以 running 进 sync → 开工 → `finishedAt` 收工 → `sim.toSave().world.settled`：
  `{source:'ledger', completedAt:1792377786778, completedAtClock:'ledger'}`（=台账秒×1000）；
  `assertSaveV1(sim.toSave())` 通过
- 浏览器（5188 + 8000，`?ledger=on`）：`pushLedger` 真实行 running→ready_to_report，交付后
  `settled.completedAt=1792377786778`、clock=ledger、source=ledger；saveNow 后 localStorage
  `schemaVersion:1`、`meta.schemaFingerprint` 正确；快照无 settled；无 JS 报错；截图
  `19-task23-ledger-time.png`
- `finishedAt` 缺失：退回上一拍已见到的 `updated_at`×1000；两者都缺：`completedAt=null`、clock 仍 ledger
  （未用本地墙钟，测试里墙钟设为 9999999999999 可区分）

### 4) 存档格式（硬点 4）与 **新发现 P2**

- 样例/指纹测试仍绿（126/126 内「冻结样例 99 路径/常量」「变异探针」均 PASS）；
  `assertSaveV1(sim.toSave())` 在 ledger 结算后通过；旧档 `fromSave` 可读且样例 ledger 时间
  `1700000000000` 原样保留
- **P2（新发现）**：运行时 `toSave()` 的 task 对象多出 `ledgerCompletedAt` 字段
  （样例 task 键集合无它；`schemaVersion` 仍 1、未升 v2、无迁移）。`save-fingerprint.test.ts` 的
  compile-time `Equal` 只断言 top-level/world/meta/settled 的**顶层键**，不覆盖 `tasks[]` 嵌套字段；
  `assertSaveV1` 只比对 `meta.schemaFingerprint` 字符串 → **该字段漂移不会被门禁/加载拦截**。
  （注：任意运行时档的 `saveFingerprint` 本就不等于样例常量——空数组/可选字段，样例指纹只冻结样例；
  但"运行时多序列化了一个冻结 schema 没有的字段"这一点是确定的）
- 建议（二选一）：① `toSave()` 不序列化 `ledgerCompletedAt`（运行时字段，读档后需要时由 settled/台账再补）；
  ② 正式升 `schemaVersion=2` + 迁移 + 更新样例与指纹，并把 `tasks[]` 字段纳入指纹断言

### 5) 回归（硬点 5）

- `npm run check` 0；`npm test` **126/126**（新增：fixture 时间戳、投影三分支、ledgerCompletedAtMs、
  LedgerView finishedAt、settled×1000、finishedAt 缺失回退）
- 沙盒 smoke **85/85**、台账 smoke **21/21**；`check_protocol` PASS；`test_core`（venv）**64/0**
- 交叉：task-18 存档探针 32/32、task-10 24/24、task-15 16/16、task-17 26/26、task-21 26/26

### 6) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P2** | 运行时存档多出 `world.tasks[].ledgerCompletedAt`，未升 v2（样例/指纹未覆盖嵌套字段） | 破坏 §1.2 frozen 字段边界；建议剥离运行时字段或升版+迁移+补指纹断言 |
| P3 | 无其它 | — |

**总评：有条件通过。** 时间戳透传的解析/归一/运行时/真实台账/存档可读全部通过，task-18 的 P2 关闭；
但新序列化的 `tasks[].ledgerCompletedAt` 突破了 v1 冻结边界且指纹守卫存在嵌套盲区，需 Lead/用户裁决
（剥离字段或升 v2）后才宜作为存档 schema 的最终形态。

---

# 卡 15：task-24（v1 存档字段漂移修复）

## 结论：**通过**（2026-10-19 15:32–15:36 执行）

task-23 的 **P2 关闭**；存档批次（task-18 → task-23 → task-24）最终形态可判 **通过**。

### 1) 修复内容 / 范围

- `simulation.ts` `toSave()`：tasks 序列化时 `delete saved.ledgerCompletedAt`（运行时字段只服务 settled，
  v1 schema 已冻结、样例无此键）
- `save.ts`：新增 `SAVE_TASK_KEYS`（10 键）、`SAVE_SETTLED_KEYS`（5 键）；`assertKeys()` 逐键比较
  （排序 + 长度 + 每个 key）；`assertSaveV1` 新增：`world.tasks[]` 键集合校验、`world.settled[]` 元素键集合校验、
  `status==='done'` 的任务必须能在 settled 找到
- 测试（`save.test.ts` / `save-fingerprint.test.ts`）新增：样例键集合与常量逐键一致、
  `toSave` 剥离后键集合=样例 + `done⊆settled`、嵌套漂移防线（加字段/伪 done 必红，还原恢复）
- 改动 mtime 15:30–15:31；`save-quota.test.ts`、`save-v1.sample.json` 未改；无 server 改动
  （server 最新 mtime 14:14:16）

### 2) 样例与 schema（硬点 1/2）

- 样例 SHA256 仍为 `F24FBAEF2B3B1543AEB0FEC9C242A96AA5D9C9F334B9C0CDD485540529298402`（5680 B）
- `schemaVersion` 仍 1；`CURRENT_SCHEMA_VERSION=1`；指纹常量仍 `f53a0736d782d657`；样例指纹不变
- 样例 `tasks[]` 键集合（排序）= `SAVE_TASK_KEYS`（10 键）；样例 `settled[]` 键集合 = `SAVE_SETTLED_KEYS`（5 键）
- **无 task-24 新增迁移**：`MIGRATIONS` 仅保留 task-18 既有 v1→v2 样例迁移（key `1`），无 2→3

### 3) 运行时落盘（硬点 1）

- 造一个带 `ledgerCompletedAt=1792377786.7` 的活动台账单：**内存 task 对象确实带该字段**（证明剥离前存在）
- `sim.toSave()` 后该 task 的键集合 = `[blockedQuestion,buildingId,completedAt,createdAt,id,kind,source,status,summary,workerId]`，
  **无 `ledgerCompletedAt`**，与样例逐键一致；`assertSaveV1(sim.toSave())` 通过；
  `save.schemaVersion===1`、`meta.schemaFingerprint` 不变
- 结算后台账 settled 仍取内存台账时间 ×1000（剥离不影响结算）→ P2 关闭且 task-23 行为不回归

### 4) 变异 / 嵌套防线（硬点 3，QA 独立）

- `assertSaveV1` 必抛：tasks[] 加字段、tasks[] 少字段、伪造成 `done` 但无 settled、
  settled[] 加字段、settled[] 少 `completedAtClock`
- `done` 且 settled 命中 → 通过
- **去剥离复现**：把 pixel-world 复制到工作区临时目录（不动仓库），删掉 `toSave` 的
  `delete saved.ledgerCompletedAt` → `node --test test/save.test.ts test/save-fingerprint.test.ts`
  由 **10/10 → 9 pass/1 fail**（`not ok 9 - toSave 剥离运行时字段：tasks[] 键集合=样例、done⊆settled、settled 键集合固定`），
  与作者报的"去掉剥离 1 红"方向一致；临时副本已删除

### 5) 回归（硬点 4）

- `npm run check` 0；`npm test` **129/129 pass**
- 沙盒 smoke **85/85**、台账 smoke **21/21**；`check_protocol` PASS；`test_core`（venv）**64/0**
- task-18 存档行为：QA 存档探针 **32/32**；浏览器 **21/21**（离线写档/刷新续跑/pagehide/30s/在线对账）
- task-23 时间戳：纯运行时 **20/20**（真实 8000 行 → settled.completedAt=1792377786778、clock ledger）；
  浏览器 **6/6**
- 交叉：task-10 24/24、task-15 16/16、task-17 26/26、task-21 26/26

### 6) 问题分级

| 级别 | 问题 | 说明 |
|---|---|---|
| **P0/P1/P2** | 无 | — |
| P3（信息） | 运行时 `WorldSnapshot.tasks[]` 内存仍可带 `ledgerCompletedAt` | 该字段不落盘（已剥离），快照 schema 未冻结该字段；记录为信息 |

**总评：通过。** 落盘字段集合与冻结样例逐键一致、样例 SHA/schemaVersion/指纹不变、嵌套防线可拦变异、
去剥离测试必红；task-18/23 行为与全部回归绿。**task-23 P2 关闭，存档批次最终通过。**

---

# 卡 16：task-19（S2-3 调试面板 UI）

## 结论：**通过**（P2 已由 task-25 关闭）

- 初验标记的 P2（`#debug-inject` 首帧后 250ms 被 `#debug-content` 刷新清空且不再重建）**已由 task-25 修复**：
  注入区移动到 `#debug-inject-slot`（在 `#debug-content` 之外），随面板创建一次，repaint 不再触碰；
  QA 复验 400ms/1s/2s 均存在、按钮可用、暂停/过滤后仍在，无重复绑定（见卡 17）。

### 1) 功能实测（硬点 1）

- **开关**：默认 `hidden=true`/`aria-expanded=false`；按钮开/关；快捷键 `D` 开/关；焦点在 `#utterance`
  输入框内按 `D` 不切换（键盘处理过滤 INPUT/TEXTAREA/SELECT）✓
- **实体列表**：角色行数与 `snapshot.villagers` 一致、含 `id name mode task=`；铺子行数与 `workstations`
  一致；任务行数与 `snapshot.tasks` 一致、含 `source·worker=·kind·summary`（blocked 时含请示）；
  等待队列按角色分组（每角色一行 + 明细），空队列也展示 ✓
- **事件流**：行数 = `min(50, Oracle.events(50).length)`；`tone` 过滤（info）后只剩 info 行 ✓
- **暂停**：暂停后派单并等 900ms，任务行/状态行不变、状态行含「已暂停」；世界继续跑（`now` 2700→3600，
  新单生效）✓
- **台账状态**：`connected/complete/reconciled/mock` 四字段展示，与快照逐项一致；在线 `?ledger=on`
  实测 `connected=true · complete=false · reconciled=true · mock=false`（截图 21）✓
- **存档状态**：`存档：v1 · 保存于 HH:MM:SS UTC · 自动存档开启`；`saveStatus()` 与面板一致 ✓

### 2) 只读性（硬点 2）

- 面板打开/刷新期间 **fetch 次数 0、WebSocket.send 次数 0**（页面注入计数器，离线与在线各测）→ 无隐藏写路径
- 打开面板前后 `#app canvas` 的 boundingRect 完全一致（不改变主舞台布局）；`buildDebugModel` 单测证明快照
  JSON 不变
- `feed`/`pushLedger` 调试路径（Oracle 同路径）：无服务器写请求；`feed` 只让世界多一条事件/交付，不写台账
- **P2**：`#debug-inject` 首帧存在（`exists=true, open=false`，文案含「仅调试、不写台账」，
  两个按钮 `feed`/`pushLedger` 标注「注入…」）；**400ms 后再查 `exists=false`**——
  `injectBuilt` 标记导致后续 `replaceChildren()` 后不再重建。面板开着就永远看不到注入按钮，
  smoke 的首帧断言掩盖了这一点。

### 3) 性能 / 布局（硬点 3）

- 刷新节流：MutationObserver 实测 6 次/1508ms ≈ **3.96 Hz**（250ms 定时器，4Hz）✓
- 世界逻辑未被拖慢：1500ms 内 `snapshot.now` 前进 1500ms ✓
- 390px：面板自身 `width=360, right=380 ≤ 390` ✓；**页面级** `document.scrollWidth=677 > 390`
  （面板开/合都 677）→ 既有页面布局（固定 960px canvas，缩放 NONE）造成，非 task-19 引入；
  若本轮验收要求页面级不横向溢出则未达成（需单独做 canvas 响应式）
- 打开时覆盖世界右侧（fixed 浮层），默认收起；"不遮挡"在"默认/不改变布局"意义上成立
- 全程无 JS 报错 ✓

### 4) 测试与回归（硬点 4/5）

- `test/debug-panel.test.ts`：4 项（模型数量/任务归属来源请示、事件最近50+tone过滤、连接与存档摘要、
  只读不改快照）全过；**P3**：未包含开关/暂停/DOM 行为测试（由 smoke/浏览器覆盖）
- `npm run check` 0；`npm test` **133/133**（129 + 4 debug 测试）
- 沙盒 smoke **96/96**（85 + 11 面板断言；其中「调试注入默认折叠」为**首帧**断言，未覆盖消失）
- 台账 smoke **21/21**；`check_protocol` PASS；`test_core`（venv）**64/0**
- 交叉：存档 32/32、台账时间戳 20/20、排队 24/24、order 16/16、多角色 26/26、归属 26/26 → 无回退

### 5) 问题分级

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| **P2** | 调试注入区首帧后消失（`injectBuilt` 只建一次，`replaceChildren()` 清掉后不再补） | 每帧重建 details 并保留 open 状态（或把注入区移出 `#debug-content` 随面板创建），smoke 增加「400ms 后仍存在」断言 |
| P3 | 390px 页面级横向溢出（canvas 固定 960，非本卡引入） | 面板自身合规；如需页面级无溢出，另开响应式任务（canvas CSS 缩放/横向滚动容器） |
| P3 | 单测未覆盖开关/暂停 | `test/debug-panel.test.ts` 补 DOM 绑定测试或明确由 smoke 覆盖 |
| 信息 | 打开时覆盖世界右侧约 346×602px | 默认收起、开关明确；若"不遮挡"要求像素不覆盖需改为抽屉/内嵌 |

**总评：有条件通过。** 面板核心功能、只读性、节流、回归全绿；P2 的调试注入区消失是一个明确可复现的
功能缺陷（但只影响调试辅助入口，不影响主世界/台账/存档），修复并补 smoke 断言后可复验通过。

---

# 卡 17：task-25（调试注入区 P2 修复）

## 结论：**通过**（2026-10-19 15:52–15:54）；**task-19 的 P2 关闭 → task-19 通过，S2 全部完成**

### 1) 修复内容

- `debug-panel.ts`：注入区 `<details id="debug-inject">` 改为在 `bindDebugPanel()` 时创建**一次**，
  放进 `index.html` 新增的 `#debug-inject-slot`（位于 `#debug-content` 之外）；
  4Hz `paint()` 只 `replaceChildren()` 刷新区，不再清掉注入区；按钮事件只绑一次（无重复绑定）
- 注入区默认折叠，summary 仍为「调试注入（仅调试、不写台账；默认收起）」

### 2) 快速复验（QA 独立浏览器 14/14）

- 首帧 / **400ms** / **1s** / **2s**：`#debug-inject` 均存在、2 个按钮（feed/pushLedger）、
  文案含「仅调试、不写台账」、`open=false`、在 `#debug-inject-slot` 内且不在 `#debug-content` 内
- **暂停**后仍在；**切 tone 过滤**后仍在且保持展开状态（`open=true` 不丢）
- **无重复绑定**：点 feed 一次活动量 +1，点两次共 +2；pushLedger 点击无 JS 报错
- 刷新 **3.98Hz**、1500ms 内 sim 前进 1500ms；无 JS 报错；截图 `22-task25-inject-fixed.png`

### 3) 回归（抽查）

- `npm run check` 0；`npm test` **133/133**
- 沙盒 smoke **104/104**（96 + 8 项新注入断言：400ms 后仍在、1s 后仍在、暂停后仍在、
  切过滤后仍在且保持展开、文案标注、默认折叠且在刷新区之外等）；台账 smoke **21/21**
- 本轮未改主产品/server，`check_protocol`/`test_core` 沿用 task-19 的 PASS/64·0（无新增相关改动）

### 4) 遗留 P3（不阻塞，记入后续卡）

- 390px **页面级**横向溢出（既有固定 canvas 布局，非 task-25/19 引入；面板自身 360px 合规）
- `test/debug-panel.test.ts` 未覆盖开关/暂停/DOM（行为已由 smoke/浏览器覆盖）

**总评：通过。** 注入区 P2 已修复且可稳定复现；task-19 由此改判通过，S2 全部完成。

---

# 卡 18：task-26（调色板审计，只报告）

## 结论：**通过（2 条 P3，只报告卡不阻断）**

### 0) 范围与基线

- 新增：`pixel-world/scripts/audit-palette.mjs`（33,343 B）、`docs/ART_PALETTE_AUDIT.md`（37,651 B）
- **源码零改动**：`git status -- pixel-world` 只有新脚本；pixel-world `src/` 最新 mtime 15:50:32，
  早于 audit 脚本 16:03:20 / 报告 16:05:57；HEAD `2b52d7c`
- 当前数字作为 task-27 前基线：世界 **77 唯一色 / 86 引用**、UI **17 / 41**、共享 3、并集 **91**

### 1) 可重复性（硬点 1）

- 默认模式连续两次：exit 0，SHA256 均 `4BE6FE069F37F29477EBCD0B6A57336F0AB55716757A4295F7770ED9A41E33B6`（字节一致）
- `--json` 两次字节一致；`--md` 两次字节一致
- 换 cwd：`pixel-world/`、仓库根、`C:\Users\18712\.dsh\bot\workspace`、`D:\` 四处的 `--json` 输出 SHA 完全相同
  （脚本以自身位置解析根目录，输出 root 为绝对路径但一致）
- 默认 exit 0；`--strict` exit **1**（世界超 61 / UI 超 9）；`node --check` 通过

### 2) 口径（硬点 2）

- 世界层：`src/main.ts` + `src/render/*.ts`（含 palette.ts token 定义）+ `src/scenes/*.ts` + `prefabs.json` palette 槽位；
  UI 层：`index.html` 的 `<style>` + `src/ui/*.ts`（当前无颜色字面量）
- 只统计源码源色；**alpha 被忽略**（`rgba()` 只取 RGB 归一为 `#rrggbb`；`fillStyle` 的 alpha 不另算色），
  不模拟运行时压暗/tint/叠加；CSS 变量定义不计为使用（但计入唯一色），`var(--x)` 使用计入引用；
  `palette.ts` 的 token 定义单独计 defs、不算使用；prefab 的十进制裸值按 palette 槽位计入
- 世界/UI 分开：独立重算得到 **77/86、17/41、共享 3（#000000/#e3a13c/#e8e2d2）、并集 91** 全部一致
- 共享色判定合理：`#000000`（世界 UI.shadow + UI box-shadow）、`#e3a13c`（世界 amber token + UI accent）、
  `#e8e2d2`（世界 ink/气泡 + UI 文字）

### 3) 数字抽查（硬点 3，独立重算 12/12）

| 文件 | 审计输出 | QA 独立重算 | 备注 |
|---|---|---|---|
| `src/render/villager.ts` | 13 / 13 | 13 / 13 | 行:色 = 35#f3dca8、42#14110d、43#e8e2d2、84#8c4a3a、86#a85c46、88#3a2d26、99#30506b、101#3f6584、104#223a4d、107#e0b48c、109#2b2320、114#c06a3e、127#1d1a17 |
| `src/world/prefabs.json` | 16 / 15 | 16 / 15 | 4 套 × 4 槽；`#f0c070` 在 code_forge/workshop_plot 重复 1 次 → 15 唯一；十进制裸值未漏 |
| `index.html` `<style>` | 20 / 17 | 20 / 17 | 5 变量 + 12 字面量 + 2 rgba + `#000` = 20；`#000000` 归一后合并 → 17 |
| 世界层合计 | refs 86 | 61 字面量 + token 引用 + 16 prefab 槽位 | 独立实现（不 import 审计脚本）复算一致 |

- 每文件字面量表也独立复算一致（main 0、buildings 5、decor 12/10、palette 22/22、smoke 0、tilemap 2、
  villager 13、weather 4/3、SandboxScene 9、prefabs 16/15）
- 未发现重复计数（跨文件重复色只在层内去重一次、refs 各站点分别计）或漏统计（十进制 prefab、`#rrggbb` 文本色、
  CSS 变量/`#000`/rgba 均已覆盖）

### 4) 报告质量（硬点 4）

- 越界表：世界 **61** 行、UI **9** 行，**每行都带 `file:line`**（JSON `sites` 全部含 file/line；例如
  `#000000 → buildings.ts:36,68 / villager.ts:26`、prefabs `#f0c070 → prefabs.json:14,56`）
- 提案：世界 16 色 / UI 8 色，**全部取自现有源色集合**（独立 membership 检查 16/16、8/8；无重复、无新色相）
- 重画清单：越界 61 色按最近提案色分类 **近似 4 / 微调 29 / 重画 28**；UI **1 / 3 / 5**；与 JSON `outSummary` 一致；
  另给出按模块（暗部/草地/蓝图材质/文字色/speck/UI）的映射与顺序，可执行
- 雨天/夜景：明确写「**初判（不是结论）**」，给出 11 组 WCAG 对比度；QA 独立复算
  outline/grass 1.59、cloak/grass 1.40、skin/grass 6.23、paper/ink 12.41、amber/panel 7.82、smoke/night 13.34
  等全部 ±0.02 内一致；夜景结论有地形亮度差 1.04–1.10:1 依据与 E-16 实验建议，没有冒充已定论
- 风险：§7 明确写入 **D4 色数上限未裁决（16 vs 24/32）**、**三栋建筑材料趋同（靠剪影区分或批 18 色）**、
  **prefab 十进制改色需同步 JSON/解析器/测试**，另有 alpha 滥用/夜景范围/最近色启发式等风险

### 5) P3（不阻塞）

1. `--strict` 模式下退出码正确为 1，但 stdout/`--md` 的提示文案仍写「（本轮不启用）」→ 建议按 strict 分支改文案。
2. 报告「77 → 71」是**估算**：`#e8e2d2` 目前在世界层还有 1 个 def（未被引用的 `UI.paper` token）；
   若 task-27 只删气泡引用、不删该 token 定义，世界层会是 **72** 而非 71。以 task-27 后复跑为准。

### 6) task-27 开工前是否需要复跑审计？

- **不需要**：当前源码自审计后未变（mtime 证据），数字已独立复算且可重复，现基线可直接交给 task-27。
- **task-27 完成后必须复跑**：`node scripts/audit-palette.mjs` + `--md`，更新报告计数、Top、越界表、提案映射
  （5 个画布文字色 + 气泡迁移会改变世界层数字；不要直接把 71 当验收数字）。
- 若 task-27 期间还有任何源码改动，开工前也应重新出基线。

**总评：通过。** 数字真实可复算、口径符合用户要求、报告可执行且风险写明；2 条 P3 属文案/估算口径，不影响
「只报告」卡结论。

---

# 卡 19：task-27（D5-A 中文迁 DOM overlay）

## 结论：**有条件通过（1 条 P2：审计报告附录未完整更新；1 条 P3 信息）**

### 0) 范围 / 基线

- 新增 `src/render/labels.ts`（投影/锚点纯函数）、`src/render/world-overlay.ts`（DOM 名字/气泡）；
  改 `render/villager.ts`、`buildings.ts`、`decor.ts`、`palette.ts`（删 `UI.paper`）、`SandboxScene.ts`、
  `index.html`（`#world-overlay`/`#stage`）、`smoke.mjs`、测试 `labels.test.ts`/`no-cjk-canvas.test.ts`
- HEAD `2b52d7c`；相关文件 mtime 16:10–16:15，仓库安静；8000 未重启

### 1) 红线（硬点 1）

- `src` 内 `.add.text(` / `.text(` / `setText(` 扫描结果 **0 处**（连旧 `scene.add.text` 也不存在）；
  `node --test test/no-cjk-canvas.test.ts` 2/2 通过
- 6 处 canvas 文本删除（diff 可见）：角色名、建筑招牌、摆件标签、祭坛「神谕祭坛」、施工「施工中 X%」、气泡底；
  替换为图标/图形：`drawSignIcon`（建筑招牌图形）、祭坛 rune、施工/摆件图标；元素截图 23-task27-dpr100.png
  可见 canvas 内只剩图标（无中文）
- 动态名字/气泡走 `#world-overlay`（DOM），`world-overlay.ts` 每帧按 canvas 实际 rect + 相机换算位置

### 2) 对齐（硬点 2，QA 浏览器 15/15）

| 场景 | 实测 |
|---|---|
| 100% / 200% / 400%（DPR 1/2/4，元素截图 `#stage`） | 名字与气泡锚点误差 **0.00px**（≤1 逻辑像素） |
| 页面 zoom 200% | 名字误差 **0.40px** |
| 窗口 resize（1010→800） | 名字/气泡误差 **0.00–0.40px** |
| 走动跟随 | 名字 **0.00px**、气泡 **0.40px**（气泡「！吾神降旨！」） |
| 进屋（`visible=false`） | 名字/气泡 `display:none`（不残留） |
| 多角色 | v0/v1 名字分别对应「莞尔」「阿雾」，不串 |
| 390px | overlay 与 canvas 同宽同边界（left/right 完全一致，不超出 canvas）；元素截图 `23-task27-390.png` |

- 实现为**每帧同步**（`renderSnapshot` 末尾 `overlay.sync`）+ window resize 监听；无 ResizeObserver，但
  resize/zoom 后下一帧即恢复对齐（上表为实测）。元素截图不是 `canvas.toDataURL`。
- 说明：390px **页面级**横向溢出（doc=677>390）仍存在，属既有固定 960px canvas 布局，非 overlay 引入
  （overlay 自身与 canvas 同框、不溢出）；记 P3 信息。

### 3) 不回归（硬点 3）

- 多角色名字/气泡不串（浏览器 + 多角色探针 26/26）；调试面板正常（含注入区，任务行可见）；
  浏览器全程 **0 JS 报错**
- 存档探针 32/32、排队探针 24/24、多角色 26/26、归属 26/26 全过；台账 smoke 21/21

### 4) 审计复跑（硬点 4）

- `node scripts/audit-palette.mjs --md/--json` 实测：世界 **71 唯一/85 引用**、UI **17/45**、
  共享 **2**（#000000/#e3a13c）、并集 **86**、世界越界 **55**、UI 越界 9；`--strict` exit 1，
  提示文案已修为「已启用；默认仍返回 0」（task-26 P3 关闭）
- **没有偷改无关色值**：与 task-26 基线 JSON 对比，世界层仅删除 6 个预期色
  （`#f3dca8/#d9d4c5/#a9b49f/#cfc7ae/#e0c98b/#e8e2d2`）、**无新增色**；UI 色集合完全不变
  （引用 41→45，+4 来自 overlay 样式复用现有 CSS 色）；16+8 提案集合未动（task-28 才动）
- docs 更新核对：§2.1 计数表（71/85、17/45、共享 2、并集 86）✓、§2.2 每文件表 ✓（buildings 4/4、
  decor 11/9、palette 21/21、villager 10/10、SandboxScene 9/7、prefabs 16/15、index 24/17）、
  正文 §3.1 Top10 ✓、§5.1 映射 55 ✓
- **P2**：docs 附录**世界 Top16 与实跑 JSON 不一致**（6 个位次/颜色不同），且附录**越界长表仍有 61 行**
  （含已删除的 6 个文字/气泡色，行号指向已删代码），与正文「世界越界 55」矛盾；附录注说"下表已按复跑更新
  Top"与实际不符。建议：用 `--md` 直接重生成附录 Top/越界表（55 行），或把已删 6 色单独列为“已移除”且不混入表

### 5) 回归（硬点 5）

- `npm run check` 0；`npm test` **139/139**（新增 labels 4 项 + no-cjk 2 项）
- 沙盒 smoke **114/114**（104 + 10 overlay 断言：节点数/文案/对齐/气泡让位/100–400% 贴合/390 不溢出 canvas）；
  台账 smoke **21/21**
- `check_protocol` PASS；`test_core`（venv）**64 passed / 0 failed**

### 6) 问题分级

| 级别 | 问题 | 建议 |
|---|---|---|
| **P2** | 审计报告附录 Top16/越界表未按 task-27 复跑更新（61 行含已删 6 色；Top16 与实跑不一致） | 从 `--md` 重生成附录两张表；或显式分“已移除色”区，保持 55 行口径 |
| P3（信息） | 390px 页面级横向溢出（overlay 与 canvas 同框不溢出；既有固定 canvas 布局） | 已在 task-19/26 记录；留后续响应式卡 |
| P3（信息） | overlay 用每帧同步 + window resize，无 ResizeObserver | 行为已实测正确；若后续要做窗口尺寸动画可加 observer 降开销 |

**总评：有条件通过。** D5-A 的红线/对齐/跟随/多角色/回归全部实测通过；唯一缺口是审计报告附录表未同步
（数据正文/每文件/正文 Top 已更新），修复文档后即可复验通过。task-28 可用「55 越界色、提案未动」口径开工。

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
| `qa9-fixtures-normal.log` | 二轮复验：基线 6/6（13:21） |
| `qa9-fixtures-mutated-stepidx.log` | 二轮复验：变异 A（`step_idx` 改名）3 fail / 3 pass、exit 1 |
| `qa9-fixtures-mutated-frame.log` | 二轮复验：变异 B（帧内 `step_idx` 改名）test 5 fail、exit 1 |
| `qa9-fixtures-restored.log` / `qa9-fixtures-final-green.log` | 二轮复验：两次字节级还原后 6/6 绿 |
| `qa9-check.log` / `qa9-check-final.log` | 二轮复验：`npm run check` exit 0 |
| `qa9-npm-test.log` / `qa9-npm-test-final.log` | 二轮复验：全量 73/73（13:21）与 77/77（13:26） |

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

## task-12（D4-A）

| 文件 | 内容 |
|---|---|
| `s1-task12-roster.json` | 8001 `/roster` 原始响应（7 条、823 B） |
| `s1-task12-adapters-8000.json` / `s1-task12-adapters-8001.json` | `/adapters` 新旧进程对照（len 1478、rawEqual=True） |
| `s1-task12-8001.log` | 8001 临时实例日志（route 就绪 / 请求记录） |
| `s1-task12-ledger-single.log` | `node test/ledger.test.ts` 22/22 |
| `s1-task12-roster-probe.log` | QA 花名册对抗 8/8 ALL PASS |
| `s1-task12-npm-test.log` / `s1-task11-task12-npm-test.log` | 全量 77/77（含 task-11 并发新增） |
| `s1-task12-check.log` | `npm run check` exit 0 |
| `s1-task11-smoke-sandbox.log` / `s1-task11-smoke-ledger.log` | 沙盒 53/53、台账 16/16（兼作 task-12 回归） |
| `s1-task11-d3-probe.log` | 真实浏览器：`/roster` 404 → `/adapters` 200 回退取证 |

## task-11（D3-A）

| 文件 | 内容 |
|---|---|
| `s1-task11-d3-probe.log` | 18/18：离线 Mock/在线不误标/台账单拒绝/沙盒单放行/日报分账 |
| `s1-task11-smoke-sandbox.log` | 沙盒 smoke 53/53（新增离线 Mock 断言） |
| `s1-task11-smoke-ledger.log` | 台账 smoke 16/16（新增完成权/分账断言） |
| `s1-task11-task12-npm-test.log` | 安静期全量 77/77 |
| `eval\screens\12-d3-offline-mock.png` / `13-d3-online-separated.png` | 离线 Mock 状态行、在线分账截图 |
| `s1-task11-task12-cleanup.log` | 5188/8001 关闭、8000 PID/健康锚点、无 server 新写入 |

## task-13（S1-5）

| 文件 | 内容 |
|---|---|
| `s1-task13-npm-test.log` | 全量 81/81（含 ok 36–39 新增 4 项） |
| `s1-task13-probe.log` | QA 对抗探针 16/16（截断/撤单/超时/单飞/30s 对账） |
| `s1-task13-real.log` | 真实 8000 截断：complete=false、状态行标记、离线不误标 |
| `s1-task13-smoke-sandbox.log` / `s1-task13-smoke-ledger.log` | 沙盒 53/53、台账 16/16 |
| `s1-task13-cross-task7-disconnect.log` | 真实断网复跑：离线 121 ms / 恢复 1118 ms |
| `s1-task13-cross-task12.log` | roster 探针适配后 8/8 |
| `commissions-ro-copy.db` / `qa-db-count.py` / `qa-db-status.py` | 8000 台账 DB 只读副本：239 行、全 done（真实截断证据） |
| `eval\screens\14-truncation-status.png` | 真实页面「台账可能未取全」状态行截图 |

## task-14（S1-6）

| 文件 | 内容 |
|---|---|
| `s1-task14-e2e.log` | run1 控制台：23/23 |
| `e2e-real-frame-evidence.json` | run1 证据（health、帧时间线、浏览器快照、D7、隔离/端口） |
| `s1-task14-e2e-run3.log` / `e2e-real-frame-evidence-run3.json` | run3 安静复跑 23/23（bootMs 2507、6 帧同 id、push=0） |
| `s1-task14-e2e-keep.log` / `e2e-real-frame-evidence-keep.json` | run2（--keep）22/23：唯一失败为隔离 mtime；用于查 `server.log` 的 startup complete/WS/answer |
| `s1-task14-pre.json` / `s1-task14-post.json` / `s1-task14-run3-before.json` / `s1-task14-run3-after.json` | 仓库 data mtime 与 8000 PID 前后证据（run3 前后一致） |
| `s1-task14-keep-tempdir.txt` | run2 临时目录路径（查完日志后已手动删除） |

## task-15 / task-16

| 文件 | 内容 |
|---|---|
| `s1-task15-order-probe.log` | task-15 矩阵 16/16（排序/稳定/无 order/撤单重排/单任务上限/task-13/11 保护） |
| `s1-task16-blocked-probe.log` | task-16 纯逻辑 27/27（emoji 截断/三路径/仅 GET/unknown） |
| `s1-task16-ui-probe.log` | task-16 浏览器 17/17（状态行/截断/无回答入口/GET-only） |
| `s1-task15-16-npm-test.log` / `s1-task15-16-check.log` | 90/90、check exit 0 |
| `s1-task15-16-smoke-sandbox.log` / `s1-task15-16-smoke-ledger.log` | 53/53、21/21 |
| `s1-task16-e2e-real.log` / `e2e-task16-evidence.json` | 真帧 e2e 23/23：真实 blocked 帧 + 浏览器状态行含真实问题、push=0 |
| `e2e-task16-snapshot-evidence.json` + `recon\e2e-task16-copy.mjs` | e2e 临时副本手工抓完整浏览器快照 `blockedQuestion`（与真实帧 id/文本一致） |
| `eval\screens\15-blocked-question-status.png` / `16-blocked-question-cleared.png` | 状态行展示 / 转 running 后清除 |

## task-10 / 提交与重构复验

| 文件 | 内容 |
|---|---|
| `task10-check.log` / `task10-npm-test.log` | check 0、**96/96**（新增 7 项 ok 79–85） |
| `s1-task10-queue-probe.log` | QA 排队对抗 24/24（入队/上限/撤销/自动开工/确定性/台账优先/行礼 sync） |
| `task10-smoke-sandbox.log` / `task10-smoke-ledger.log` | 沙盒 **59/59**、台账 **21/21** |
| `task10-check-protocol.log` / `task10-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task10-cross-task15.log` / `task10-cross-task12.log` | 交叉 16/16、8/8 |
| `task10-gate-run1..3.log` | 门禁 3 连跑 PASS，每次 96/96 |
| `task10-gate-npm-missing.log` | P3 证据：PATH 清空后仍 Traceback（WinError 87），exit 1 |
| `refactor\roster.before.json` / `refactor\roster.after.json` | /roster 重构前后基线（SHA256 相同，823 字符） |
| `s1-task12-adapters-8001.json` / 运行时对比 | /adapters 结构未变（1478 字符，与 8000 一致） |
| `commit-340d0ab\`（git archive 隔离快照） | 提交级门禁 3/3、90/90、smoke 53/21 的干净环境 |
| `task10-cleanup.log` | 端口释放、8000 PID/启动时间锚点、data 未被写（commissions.db） |

## task-17（S2-1 多角色）

| 文件 | 内容 |
|---|---|
| `task17-check.log` / `task17-npm-test.log` | check 0、**100/100**（新增 11 项 ok 79–89） |
| `s1-task17-multivillager-probe.log` | QA 多角色对抗 26/26（独立作息/换人/双忙队列/归属/3 人/确定性/回归） |
| `task17-smoke-sandbox.log` / `task17-smoke-ledger.log` | 沙盒 **66/66**、台账 **21/21** |
| `task17-check-protocol.log` / `task17-test-core.log` | 主产品 PASS / **64 passed 0 failed**（venv python） |
| `eval\screens\01-salute.png` | 双人同屏 + 多角色状态行（莞尔行礼 / 阿雾在街口） |
| `task17-gate-npm-missing.log` | 8a95c94 复验：npm 缺失时 FAIL 且无 Traceback |
| `task17-cleanup.log` | 端口释放、8000 PID/启动时间锚点 |

## task-21（多角色 P3）

| 文件 | 内容 |
|---|---|
| `task21-check.log` / `task21-npm-test.log` | check 0、**106/106**（新增 7 项 ok 50–53、96–98） |
| `s1-task21-attribution-probe.log` | QA 归属/指定完成对抗 26/26（分账、来源、收尾措辞、指定 id/名字、未知拒绝、台账完成权、确定性） |
| `task21-ui-probe.log` | 浏览器 15/15（默认交付提示、#finish、Oracle.finish、未知目标、两人分账、报纸） |
| `task21-smoke-sandbox.log` / `task21-smoke-ledger.log` | 沙盒 **74/74**、台账 **21/21** |
| `task21-check-protocol.log` / `task21-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `eval\screens\17-task21-attribution.png` | 两人分账后的状态行/报纸截图 |
| `task21-cleanup.log` | 端口释放、8000 PID/启动时间锚点、`commissions.db` 未被写 |

## task-18（存档 v1 + 恢复）

| 文件 | 内容 |
|---|---|
| `task18-npm-test.log` | 全量 **118/118**（save 相关 ok 48–58 等） |
| `task18-check.log` | `npm run check` exit 0 |
| `s1-task18-save-probe.log` | QA 存档探针 **32/32**（round-trip、settled 时钟、对账分支、坏档/配额/高版本/指纹不符/origin/阈值） |
| `task18-fingerprint-mutated.log` / `task18-fingerprint-restored.log` | 样例真实变异 → 测试红；字节级还原 SHA 一致 → 3/3 绿 |
| `save-v1.sample.orig.json` | 样例字节级备份（SHA256 `F24FBAEF…298402`，5680 B） |
| `task18-sample-inspect.mjs` | 样例叶子路径 206/unique 99、指纹、settled 证据 |
| `task18-browser-probe.log` | 浏览器 **21/21**：离线写档/刷新续跑/pagehide/30s/在线对账 |
| `task18-smoke-sandbox.log` / `task18-smoke-ledger.log` | 沙盒 **85/85**、台账 **21/21** |
| `task18-check-protocol.log` / `task18-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task18-cross-qa-task*.log` | 交叉 task-10/15/17/21：24/16/26/26 全过 |
| `eval\screens\18-task18-persist.png` | 离线写档/刷新后续跑截图 |
| `task18-cleanup.log` | 端口释放、8000 PID/启动时间锚点、样例 SHA 复核 |

## task-23（台账时间戳透传）

| 文件 | 内容 |
|---|---|
| `task23-npm-test.log` | 全量 **126/126**（新增时间戳 6 项） |
| `task23-check.log` | `npm run check` exit 0 |
| `s1-task23-timestamp-probe.log` | QA 纯运行时 **20 项**：三分支/秒毫秒/真实 8000 行/finishedAt 缺失/都缺/save 校验 |
| `task23-browser-probe.log` | 浏览器 **6/6**：真实台账行 push → settled=×1000、clock ledger、存档/快照 |
| `task23-fingerprint-drift.mjs` / `task23-task-field-drift.mjs` | 运行时档 vs 样例的路径/字段漂移证据（`world.tasks[].ledgerCompletedAt`） |
| `task23-smoke-sandbox.log` / `task23-smoke-ledger.log` | 沙盒 **85/85**、台账 **21/21** |
| `task23-check-protocol.log` / `task23-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task23-cross-*.log` | 交叉 task-18/10/15/17/21：32/24/16/26/26 全过 |
| `eval\screens\19-task23-ledger-time.png` | 真实台账时间交付后的状态/存档证据 |
| `task23-cleanup.log` | 端口释放、8000 PID/启动时间、样例 SHA、server mtime 复核 |

## task-24（v1 字段漂移修复）

| 文件 | 内容 |
|---|---|
| `task24-npm-test.log` | 全量 **129/129**（新增嵌套键集合/剥离/防线 3 项） |
| `task24-check.log` | `npm run check` exit 0 |
| `task24-nested-keys-probe.log` | QA 独立 **21/21**：样例 SHA/键集合、toSave 剥离、`assertSaveV1` 6 类变异、结算不回归 |
| `task24-mut-baseline.log` / `task24-mut-nostrip.log` | 临时副本去剥离：10/10 → **9 pass / 1 fail**（测试必红） |
| `task24-browser-task18.log` / `task24-browser-task23.log` | task-18 浏览器 **21/21**、task-23 浏览器 **6/6** |
| `task24-smoke-sandbox.log` / `task24-smoke-ledger.log` | 沙盒 **85/85**、台账 **21/21** |
| `task24-check-protocol.log` / `task24-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task24-cross-qa-task*.log` | 交叉 task-18/23/10/15/17/21：32/20/24/16/26/26 全过 |
| `task24-cleanup.log` | 端口释放、8000 PID/启动时间、样例 SHA、server mtime、临时副本已清理 |

## task-19（调试面板 UI）

| 文件 | 内容 |
|---|---|
| `task19-npm-test.log` / `task19-check.log` | **133/133**、check 0 |
| `task19-debug-panel-probe.log` | QA 浏览器 **30 项**：开关/实体/事件/过滤/暂停/只读/节流/390px；含注入区消失 FAIL（P2）与页面溢出 FAIL（P3） |
| `qa-task19-inject-repro.mjs` | 注入区消失的最初复现脚本（首帧 2 按钮 → 400ms 后 0） |
| `eval\screens\20-task19-debug.png`、`20-task19-debug-missing-inject.png`、`21-task19-debug-online.png`、`07-debug-panel.png` | 面板截图（离线/注入缺失/在线台账） |
| `task19-smoke-sandbox.log` / `task19-smoke-ledger.log` | 沙盒 **96/96**、台账 **21/21** |
| `task19-check-protocol.log` / `task19-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task19-cross-qa-task*.log` | 交叉存档/时间戳/排队/order/多角色/归属：32/20/24/16/26/26 全过 |
| `task19-cleanup.log` | 端口释放、8000 PID/启动时间、样例 SHA、server mtime |

## task-25（调试注入区 P2 修复）

| 文件 | 内容 |
|---|---|
| `task25-inject-probe.log` | QA 浏览器复验 **14/14**：400ms/1s/2s 持续存在、2 按钮、暂停/过滤后仍在、无重复绑定、3.98Hz |
| `task25-smoke-sandbox.log` / `task25-smoke-ledger.log` | 沙盒 **104/104**（含 400ms/1s/暂停/过滤注入断言）、台账 **21/21** |
| `task25-npm-test.log` | `npm test` 133/133、`npm run check` 0 |
| `eval\screens\22-task25-inject-fixed.png` | 修复后注入区（稳定存在、默认折叠） |
| `task25-cleanup.log` | 端口释放、8000 PID/启动时间、样例 SHA |

## task-26（调色板审计，只报告）

| 文件 | 内容 |
|---|---|
| `task26-audit-default.log` / `task26-audit-json.log` / `task26-audit-md.md` | 默认 / `--json` / `--md` 三份输出（77/86、17/41、并集 91） |
| `task26-rep-default-1/2.log`、`task26-rep-json-1/2.log`、`task26-rep-md-1/2.md` | 连续两次输出字节一致（SHA `4BE6FE…33B6`） |
| `task26-cwd-root.json` / `task26-cwd-workspace.json` / `task26-cwd-d.json` | 三个不同 cwd 的 `--json`，SHA 与 pixel-world 运行一致 |
| `task26-strict.log` | `--strict` exit 1（世界超 61 / UI 超 9），附「本轮不启用」文案问题 |
| `task26-recount.log` / `qa-task26-recount.mjs` | QA 独立重算 **12/12**：77/86、17/41、共享 3、并集 91、三文件手算、提案 membership、11 组对比度 |
| `qa-task26-json-checks.mjs`（输出见会话记录） | 越界表 61+9 行 file:line 完整、提案 16/8 无重复、分类合计与 outSummary 一致 |
| `D:\project\neurosama\docs\ART_PALETTE_AUDIT.md`、`pixel-world/scripts/audit-palette.mjs` | 被审报告与脚本本身 |

## task-27（D5-A 中文迁 DOM overlay）

| 文件 | 内容 |
|---|---|
| `task27b-npm-test.log` / `task27b-check.log` | **139/139**、check 0；no-cjk 2/2、labels 4/4 |
| `task27b-overlay-probe.log` | QA 浏览器 **15/15**：100/200/400% 对齐、zoom/resize、走动/气泡/进屋/多角色、390 overlay 不超 canvas、调试面板、0 JS 报错 |
| `eval\screens\23-task27-dpr100.png`、`23-task27-dpr200.png`、`23-task27-dpr400.png`、`23-task27-390.png` | 元素截图（非 canvas.toDataURL） |
| `task27b-audit-md.md` / `task27b-audit-json.log` / `task27b-strict.log` | 审计复跑 71/85、17/45、共享2、并集86、越界55；strict exit 1 且文案已修 |
| `task27b-audit-consistency.mjs`（输出见日志） | 与 task-26 基线对比：仅删 6 色/无新增色/UI 集合不变/提案未动；docs 计数/每文件/Top10 一致；附录 Top16/越界表不一致（P2 证据） |
| `task27b-smoke-sandbox.log` / `task27b-smoke-ledger.log` | 沙盒 **114/114**、台账 **21/21** |
| `task27b-check-protocol.log` / `task27b-test-core.log` | 主产品 PASS / 64 passed 0 failed |
| `task27b-cross-qa-task18/10/17/21*.log` | 存档 32、排队 24、多角色 26、归属 26 全过 |
| `task27b-cleanup.log` | 端口释放、8000 PID/启动时间、截图/数据锚点 |

# 附录 B：复验顺序（各卡）

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
- task-9：**通过**（契约变异实测会红、哈希还原、无敏感字段、未削弱断言）；**二轮独立复验（13:20–13:27，lead/QA）再次确认通过**，变异矩阵扩展至帧 fixture 与 `step_idx` 字段，详见「卡 2 · 二轮独立复验」
- task-8：**通过**（8001 隔离验收；8000 PID 22996 全程未变）——注意：Chief 于 13:12 通知 task-8 经首席架构审查**已打回修订**（3 处必修：scheme 误拒 TLS 反代、`urlsplit` 畸形 Origin 未捕获 `ValueError`、1008/403 注释不准确），打回后 `server/app.py` 工作区已含对应修订（只比 netloc、捕获 `ValueError`、注释修正），卡 3 记录即为修订后代码的隔离验收；**最终放评以 Chief 与首席架构的确认为准**，二轮复验按指示未重跑 task-8
- 端口：5188 / 5190 / 8001 均已释放（复核无监听）；8000 为既有服务，未重启/未终止
- 仓库：本次除本文件外未修改任何源码/配置/测试/既有文档；临时脚本、日志、临时 DB/临时 Vite 配置
  全部留在工作区 `recon\`，未进仓库
- 已知红项（非本卡引入、不计基线）：`scripts/test_voice_turns.py` / `scripts/test_experience_websocket.py`
  在 HEAD 即红（缺 `_connections`，按 Lead 说明记录）
- 二轮复验时快照（13:26–13:27）：pixel-world 全量 **77/77** 绿（并行 D4-A 等工作使总数自 69 持续增长，
  各 diff 均为纯新增）；两个 fixture 字节级还原（SHA256 与原始记录一致）；8000 仍为 PID 22996
- **task-12（D4-A）：通过**（13:23–13:27）——`/roster` 只读端点 200/7 条/仅 4 字段、无敏感值；
  `/adapters` 8000 vs 8001 byte-identical；前端兼容/404 回退/两源失败不判离线；77/77、check 0、
  smoke 53/53 + 16/16；8000 旧进程无 `/roster` 属已知限制（下次重启生效），未重启
- **task-11（D3-A）：通过**（13:23–13:29）——台账单人工完成被拒（source 判断、状态不变、回执含「完成权在台账」）、
  沙盒单人工完成正常；`?ledger=off` 状态行/快照/回执/日报含「离线 Mock」且与台账分账；
  77/77、check 0、smoke 53/53 + 16/16；未碰 ledger/projection/server。README 计数 59→实际 77 记为 P3 文档漂移
- **收口（13:28:27）**：5188/8001 已关闭（探测 connection refused）；8000 PID 22996、health 200、未重启；
  `server/**/*.py` 在 13:21 后零修改；pixel-world 无 `.tmp*` 残留；临时 DB 只在工作区 `recon\`
- **并发期环境备注**：13:23 全量测试一度因 `commission_update.json` 被并行作业瞬时改坏而 1 项失败；
  13:24 已恢复原 SHA256（`39F57390…`），安静期 13:26 复跑 77/77 全绿——非 task-11/12 引入
- **task-13（S1-5）：通过**（13:41–13:49）——30s 对账在 WS 在线也运行、refresh 单飞、5s AbortController 超时、
  limit=100 截断语义均实测；81/81、check 0、smoke 53/53 + 16/16；真实 8000 台账 DB **239 行（>100）**，
  页面「台账可能未取全」可复现且判定正确；task-7 退避（真实断网 121ms/1118ms）与 task-12 回退（8/8）交叉通过。
  收口：5188 已释放；8000 PID 22996 未重启；8001 由另一成员临时占用（PID 28664），未触碰
- **task-14（S1-6）：通过**（13:47–13:53）——真帧 e2e 复跑 run1/run3 **23/23**；冷启动 bootMs 2517/2507、
  lifespan `Application startup complete`；无 Key `offline=true`；独立 WS 采集到同 id 6 帧
  `drafting→briefing→running→blocked→(answer)→running→blocked`；浏览器同 id、`toWork`、**pushLedgerCalls=0**；
  D7 如实 `verified=false`。run2（--keep）22/23 仅隔离 mtime 断言被外部并发写触发假失败（临时实例 DB 均在
  `tempRoot/data`，同段他人 smoke 改 eval 截图；静默 30s 无写入、run3 前后一致）→ 环境问题已定位。
  收口：8001/5189 释放、临时目录清理、8000 PID 22996/start 2026-10-18 16:40:34 未变
- **task-15（S1-15）：通过**（13:56–14:00）——`ordered/orderActive`（blocked→running→auditing→其它，同级 id 稳定）、
  LedgerView.order、`nextLedgerTask` 排序接线；QA 矩阵 16/16（无 order 插入序、撤单重排、单任务上限、
  task-13/task-11 保护）；90/90、check 0、smoke 53/53 + 21/21；task-12 旧服务回退未破坏
- **task-16（S1-16）：通过**（13:56–14:01）——blocked_question 真帧→LedgerView→TaskRecord→快照+状态行；
  60 **码点**截断（emoji surrogate 不劈开）、转 running/收工清除、空值不显示；QA 纯逻辑 27/27 + 浏览器 17/17；
  全程仅 GET、无 answer 入口；真帧 e2e 23/23 并手工抓到浏览器 `blockedQuestion` 与真实帧逐字一致（push=0）。
  `projection.ts` 本卡未改（原因见卡 9）
- **task-10（S1-10）：通过**（14:24–14:28）——忙时入队不拒单、MAX_PENDING=3、台账优先+FIFO、
  撤最后/按 id/空队列撤、clearDeparture 四路径、两 sim 同命令同步长 deepEqual、行礼中 sync 收工/撤单
  不残留；96/96、check 0、smoke **59/59 + 21/21**、check_protocol PASS、test_core 64/0；
  task-15/12 交叉 16/16、8/8；P3：smoke 里程碑断言放宽为 `>=1`
- **提交与重构复验（340d0ab / b656488 / 150b2a7）：通过（1 条 P3）**——提交范围 25/2/2 文件、
  无主产品夹带；roster_view 已移除、`/roster` 自包含、823 字符与基线逐字一致、`/adapters` 1478 字符不变；
  门禁 3/3 PASS（96/96）；150b2a7 的 CI 统一走脚本、paths 合理；**P3**：`PATH` 清空（npm 缺失）时
  仍 `Traceback (WinError 87)`（exit 1，安全失败），与提交说明“不再 traceback”不符
- **最终收口（14:28:40）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34 未重启；
  `data/commissions.db` 未被写（mtime 仍 10:43:06）；`data/memory.db` 14:27:33 的 mtime 变化来自 8000
  自身会话写入（ledger smoke WS 触发，非 QA 写台账）；临时 e2e 目录 0、无 `.tmp` 残留
- **task-15/16 收口（14:00）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34 未重启；
  `data/commissions.db` 未被写（mtime 仍 10:43:06）；`data/memory.db` 因 8000 自身会话写入在 13:58:57 有 mtime 变化
  （非 QA 写台账；8000 进程未动）；临时 e2e 目录 0；仓库无 `.tmp*`；新增仅为 QA 文档与 task-14 的脚本/文档
- **task-17（S2-1 多角色）：通过**（14:37–14:41）——`villagers[]` + `villager` 兼容、每角色独立 state/计时器、
  队列 `Map<villagerId>`、派活只叫一个/第二单换人/双忙各自排队、`workerId` 归属不串、3 角色可扩、
  两 sim deepEqual；**100/100**、check 0、smoke **66/66 + 21/21**、check_protocol PASS、test_core **64/0**
  （venv python）；渲染层多实例由 `SandboxScene` Map 按 id 对齐（`villager.ts` 无需改，截图双人可见）；
  P3：`report.ts` 仍只写主角色、手动完成不指定哪个角色、3 人无 UI 配置入口
- **8a95c94（P3 修复）：通过**——仅 `scripts/test_pixel_world.py` +4 行；PATH 清空复测 → `FAIL npm not found`、
  exit 1、**无 Traceback**，150b2a7 节的 P3 关闭
- **task-17 收口（14:40:48）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 14:40:25 的变化来自 8000 自身会话写入
  （ledger smoke WS，非 QA 写台账）；临时目录 0、无 `.tmp` 残留
- **task-21（多角色 P3）：通过**（14:54–14:58）——新增 snapshot `deliveriesByVillager`
  （`VillagerDeliveries{villagerId,name,ledger,mock}`）、`complete` 命令可选 `target`（id/名字）、
  `OracleApi.finish(target?)`；日报按人分账且来源不合并、全闲/全忙/混合措辞可读、助手不漏报；
  指定 v0/名字各自收工不串、未知目标拒绝、台账完成权不回退；**106/106**、check 0、
  smoke **74/74 + 21/21**、check_protocol PASS、test_core **64/0**；浏览器 15/15（含 `#finish` 默认提示、
  `window.Oracle.finish` 指定对象、报纸分账）。P3：DOM `#finish` 无 target 选择入口、Mock 标签随全局台账状态、
  target 按名字匹配重名取第一个。新增字段/接口清单见卡 12 §2（供 ADR schema 冻结）
- **task-21 收口（14:58:03）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 14:57:43 的变化来自 8000 自身会话写入
  （ledger smoke WS，非 QA 写台账）；临时目录 0、无 `.tmp` 残留
- **task-18（存档 v1 + 恢复）：有条件通过（1 条 P2）**（15:13–15:18）——样例 206 路径去重 99、指纹
  `f53a0736d782d657`；运行时快照无 settled；真实变异样例 → 指纹测试红、字节还原后绿；
  settled 去重/分账/时钟、读档未对账→首 sync 三分支、坏档备份 3 份/配额先删旧再写主档/高版本只读/
  指纹不符按坏档/origin 提示/30s+pagehide/刷新续跑（结构一致、漂移 0.9 格）均实测；**118/118**、check 0、
  smoke **85/85 + 21/21**、check_protocol PASS、test_core 64/0；交叉 task-10/15/17/21 全过。
  **P2**：ledger settled.completedAt 恒 null（未取 `closed_at ?? updated_at`，投影未接线）；P3：配额恢复无运行时接线、
  `assertSaveV1` 形状校验较浅
- **task-18 收口（15:18:17）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 15:17:44 的变化来自 8000 自身会话写入
  （ledger smoke WS，非 QA 写台账）；样例 SHA256 `F24FBAEF…298402`（5680 B）已复核；临时目录 0、无 `.tmp` 残留
- **task-23（台账时间戳）：有条件通过（1 条 P2 新发现）；task-18 P2 关闭 → 通过**（15:24–15:28）——
  三分支解析（closed_at 优先/退 updated_at/都缺 null）、秒×1000、毫秒原样、无本地兜底；
  真实 8000 行 `closed_at=1792377786.7782023` → settled `completedAt=1792377786778`、clock=ledger
  （纯运行时 + 浏览器各验一次）；finishedAt 缺失退回已见 updated_at；都缺→null；
  `assertSaveV1(sim.toSave())` 通过、样例指纹/旧档读取不回归；**126/126**、check 0、smoke **85/85 + 21/21**、
  check_protocol/test_core 64/0；交叉存档/排队/order/多角色/归属全过。
  **P2（新）**：运行时 toSave 的 task 多出 `ledgerCompletedAt`（样例无、未升 v2；指纹断言只覆盖顶层键），
  建议剥离运行时字段或升 v2+迁移+补嵌套断言
- **task-23 收口（15:28:23）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 15:27:44 变化来自 8000 自身会话写入；
  样例 SHA `F24FBAEF…298402` 未变；server 最新 mtime 14:14:16（task-23 未碰 server）；临时目录 0、无 `.tmp` 残留
- **task-24（v1 字段漂移修复）：通过**（15:32–15:36）——`toSave()` 剥离 `tasks[].ledgerCompletedAt`，
  落盘 tasks 键集合与样例 10 键逐键一致；样例 SHA `F24FBAEF…298402`、schemaVersion 1、无新增迁移；
  `SAVE_TASK_KEYS`/`SAVE_SETTLED_KEYS` 与样例逐键一致，`assertSaveV1` 校验 tasks/settled 键集合 + `done⊆settled`；
  变异（加字段/伪 done/settled 加字段/少字段）全抛；临时副本去剥离 → **9 pass/1 fail**（测试必红）；
  **129/129**、check 0、smoke **85/85 + 21/21**、check_protocol/test_core 64/0；task-18 刷新/对账/坏档/配额/高版本
  与 task-23 真实台账时间戳回归全过。**task-23 P2 关闭，task-18/23/24 最终通过**
- **task-24 收口（15:35:53）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 15:35:46 变化来自 8000 自身会话写入；
  样例 SHA `F24FBAEF…298402` 未变；server 最新 mtime 14:14:16；临时变异副本已删除、无 `.tmp` 残留
- **task-19（S2-3 调试面板）：有条件通过（1 条 P2 + 2 条 P3）**（15:44–15:49）——开关（按钮/D/输入框保护）、
  实体行数与快照一致、任务 source/worker/blocked、按角色队列、事件最近 50 + tone 过滤、暂停冻结且世界继续、
  台账四字段（离线/在线）、存档 v1/savedAt/paused、只读（fetch/WS 0、canvas 布局不变）、刷新 3.96Hz、无 JS 报错；
  **133/133**、check 0、smoke **96/96 + 21/21**、check_protocol/test_core 64/0；交叉存档/时间戳/排队/order/
  多角色/归属全过。**P2**：调试注入区首帧后 250ms 被刷新清空且不再重建（按钮实际不可用；smoke 只测首帧）；
  P3：390px 页面级横向溢出（677>390，既有 canvas 布局、非本卡；面板自身 360px 合规）、单测未覆盖开关/暂停。
  修复注入区重建并补「400ms 后仍存在」smoke 断言后可复验
- **task-19 收口（15:49:11）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 15:48:52 变化来自 8000 自身会话写入；
  样例 SHA 未变；server 最新 mtime 14:14:16；无临时目录/.tmp 残留
- **task-25（调试注入区 P2 修复）：通过**（15:52–15:54）——注入区移至 `#debug-inject-slot`（`#debug-content` 外）
  随面板创建一次，400ms/1s/2s 后仍在、2 按钮可用、文案含「仅调试、不写台账」；暂停/切过滤后仍在且展开状态保留；
  点 feed 一次只触发一次、两次各 +1（无重复绑定）；刷新 3.98Hz、无 JS 报错；**133/133**、check 0、
  smoke **104/104 + 21/21**。**task-19 P2 关闭 → task-19 通过，S2 全部完成**；P3（390px 页面级溢出/单测覆盖）
  不阻塞、记入后续卡
- **task-25 收口（15:54:18）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 15:54:07 变化来自 8000 自身会话写入；
  样例 SHA 未变；无临时目录/.tmp 残留
- **task-26（调色板审计，只报告）：通过（2 条 P3）**（16:06–16:12）——世界 **77 唯一/86 引用**、UI **17/41**、
  共享 3、并集 **91** 与 QA 独立重算一致；默认/`--json`/`--md` 两跑字节一致、3 个 cwd 结果一致、
  默认 exit 0、`--strict` exit 1；手算 villager.ts 13/13、prefabs 16/15、index.html 20/17 核对；
  越界表 61+9 行全带 file:line；16+8 提案全部取自现有源色；11 组对比度独立复算一致；D4 未裁决/三栋材料趋同/
  prefab 十进制需同步测试已写入报告。P3：`--strict` 提示仍写「本轮不启用」；77→71 为估算（`#e8e2d2` 可能因
  未引用 token 留在世界层 → 72），**task-27 后必须复跑审计**。**task-27 开工前无需复跑**（源码未变、数字已验）
- **task-26 收口（16:12）**：本卡只报告，未起 dev/临时端口；8000 PID 22996、start 2026-10-18 16:40:34、
  health 200 未重启；`data/commissions.db` 未被写；pixel-world `src/` 无改动（最新 mtime 15:50:32 < 审计 16:03）
- **task-27（D5-A 中文迁 DOM overlay）：有条件通过（1 条 P2 文档 + 1 条 P3 信息）**（16:23–16:31）——src 内
  `.add.text(` 等 **0 处**、no-cjk 2/2、6 处静态文字改图标；浏览器 100/200/400% 名字/气泡误差 **0.00–0.40px**、
  zoom/resize/走动/进屋/多角色均通过、390px overlay 与 canvas 同框不溢出；**139/139**、check 0、
  smoke **114/114 + 21/21**、check_protocol/test_core 64/0；交叉存档 32/排队 24/多角色 26/归属 26 全过；
  审计复跑世界 **71/85**、UI **17/45**、共享 2、并集 86、世界越界 55，且仅删 6 个预期色、无新增色、提案未动。
  **P2**：docs 附录世界 Top16 与实跑不一致、越界长表仍 61 行含已删 6 色（正文计数/每文件表/Top10/§5.1 已更新）；
  P3：390px 页面级溢出（既有 canvas 布局，overlay 自身不溢出）
- **task-27 收口（16:25:37）**：5188/8001/5189 无监听；8000 PID 22996、start 2026-10-18 16:40:34、health 200
  未重启；`data/commissions.db` 未被写（mtime 10:43:06）；`data/memory.db` 16:25:03 变化来自 8000 自身会话写入；
  无临时目录/.tmp 残留；截图 100/200/400/390 均存在

---

## task-27 最终签收复验（2026-10-19 16:33–16:40，lead · QA Engineer）

- **基线声明**：HEAD `5214eb5 feat(pixel-world): 中文迁 DOM overlay、调色板审计与图标化（S3-1/S3-2a)`；
  `docs/ART_PALETTE_AUDIT.md` 已包含在该提交内、`git status -- docs/` 干净。16:36 起主工作区 `pixel-world/`
  已被 task-28 施工改为脏树（13 个 M + `test/palette-budget.test.ts` 未跟踪，均未提交），故本轮用
  `git archive 5214eb5 pixel-world`（外加 `docs/save-v1.sample.json`）抽取到 TEMP 独立副本验证：
  **不触碰主工作区、不回退任何作者改动**。副本内 `palette.ts` 35 行与 HEAD 一致（主工作区已 64 行，差异即 task-28）。
- **task-27 最终结论：通过（P2 关闭）**。该 P2 是文档一致性缺陷（附录 A 与脚本实跑不一致），本条按
  「附录 A ↔ `--md` 实跑逐行全等」闭环，未改动历史记录。
- **附录 A 逐行核对（核心证据）**：`node scripts/audit-palette.mjs --md` 取「## 世界层 Top（按使用次数）」
  至「## 提案 8 色 UI」表尾，与 `docs/ART_PALETTE_AUDIT.md` 附录 A 对应段（L340–484）做大小写敏感的
  元素级比对：**非空行 132 / 132 全等，0 处不一致**。
- **表格行数**：世界层 Top **16 行**、UI 层 Top 10 行、越界色世界层 **55 行**、越界色 UI 层 **9 行**、
  提案 16 色世界 **16 行**、提案 8 色 UI **8 行**——与文档正文及 Chief 复核口径一致。
- **计数口径**：世界 **71 唯一 / 85 引用**、越界 **55**；UI **17 / 45**、越界 **9**；共享 **2**
  （`#000000`、`#e3a13c`）；并集 **86**——与 §0 摘要、§2.1、§8.3「预期（task-27 后）」、§8.4 完全一致。
- **每文件表 §2.2 独立手算复核**（不采信脚本自述）：`villager.ts` `0x` 字面量 **10 个 / 10 唯一**；
  `decor.ts` **11 个 / 9 唯一**（`#2b2b28`×2、`#f0cf7a`×2，其余各 1）；`index.html` hex 字面量 22 个
  + `rgba(...)` 2 个 = **24 次 / 17 唯一**（两条 rgba 均归一为 `#000000`，不增加唯一色）——与脚本及文档一致。
- **退出码**（pinned 副本）：默认 `node scripts/audit-palette.mjs` → **exit 0**；`--strict` → **exit 1**，
  提示文案「已启用；默认仍返回 0」，与 §8.1 / §8.3 一致。
- **类型检查与测试**（pinned 副本）：`npx tsc --noEmit` → **exit 0**；测试 **139/139 全绿**
  （debug-panel 4 / grid 7 / labels 4 / ledger-fixtures 7 / ledger 41 / no-cjk-canvas 2 /
  save-fingerprint 4 / save-quota 5 / save 6 / surface 17 / world 42）。
  注：`npm test`（`node --test test/*.test.ts`）在 QA 沙箱下 **spawn EPERM**（沙箱禁止管道捕获子进程 stdio，
  属环境限制而非项目缺陷），故改为逐文件 in-process 运行同一批测试文件，计数与 `isolation=none` 口径一致。
- **遗留不变**：P3（390px 页面级溢出，既有 canvas 布局，overlay 自身不溢出）不阻塞；剩余 55 个越界色
  收敛清单见 `docs/ART_PALETTE_AUDIT.md` §5.3，交 task-28。
- **快照提醒**：16:36 后 `pixel-world/` 为 task-28 施工中的脏树（未提交）。task-28 验收时必须重新声明
  基线（HEAD 或工作区快照 + `git status` 复核），不可沿用本条 5214eb5 的数字。

---

## task-28 验收方案（待施工完成后执行；口径经 Chief 2026-10-19 16:39 修订）

- **基线**：开工前声明 HEAD / 工作区快照，复跑前再核一次 `git status`；确认 `git diff` 不含 task-28 之外的
  无关改动（16:41 观察：`palette.ts` 已被删除、新增 `src/world/palette-data.ts`、`test/palette-budget.test.ts`、
  `docs/ADR_AGENT_BRIDGE.md`，`docs/ART_PALETTE_AUDIT.md` 亦在工作区被改——均需在验收时重新核对）。
- **P1 必验（旧存档 palette 兼容）**：构造一份 `PlacedBuilding.palette` 为旧 hex 的存档 → 读档 → 断言渲染取到
  新 WORLD token 色（而非存档里的旧值）；同时确认存档 v1 指纹/样例未被动（99 路径、`f53a0736d782d657`）。
- **色数按源码色数验**（`audit-palette --json` 的 `counts`）：世界 **≤16**、UI **≤8**、共享 **≤1**
  （只剩 `#e3a13c`）、并集 **= 23**（16 + 8 − 1；**不是 ≤17**）。不按截图唯一色数判（运行时 alpha/叠加
  必然 >16，属假失败）；截图只验可读性。
- **退出码**：`--strict` 收敛后必须 **exit 0**，默认也 exit 0；strict 仅在超预算时 exit 1。
- **门禁范围**：本地 gate 接 strict、CI 只报告属后续卡，**不在本轮验收范围**。
- **对抗验证**：① 临时把某个已收敛的世界/UI token 改回旧 hex → `--strict` 必须 exit 1 → 字节级还原并核
  SHA256 一致；② 核 prefab 旧 hex 兼容分支（旧存档仍能解析）。
- **截图三项前置**：清 localStorage；zoom 2 的降采样方法记录（临时 zoom=1 或 960×640 最近邻）；暂停固定帧 +
  雨滴固定种子——否则每张雨帧不同、不可比。雨帧要 **100% 全图 + 480×320 原生 + 400% 最近邻** 三张，
  主角/雨幕/路径可区分。
- **夜景**：只读试验要给「并团 / 可辨」的明确结论。
- **回归**：`npm run check`（tsc）exit 0、tests 139/139（isolation=none）、smoke 不降、台账 21。
- **人类判定项**：「3 秒指认」由用户最终判定，报告只能写「待用户评审」，不得写成通过。

---

## task-28（D6-A 调色板收敛）独立复验（2026-10-19 16:51–17:12，lead · QA Engineer）

- **基线声明**：HEAD `5214eb5 feat(pixel-world): 中文迁 DOM overlay、调色板审计与图标化（S3-1/S3-2a)`；
  工作区脏树。`pixel-world/` 实测 **13 M + 1 D + 2 ??**（Chief 预期 14 M + 1 D + 2 新，M 数差 1，已核对无遗漏文件）：
  M = `index.html`、`scripts/audit-palette.mjs`、`src/main.ts`、`src/render/{buildings,decor,smoke,tilemap,villager,weather}.ts`、
  `src/scenes/SandboxScene.ts`、`src/world/{blueprints.ts,prefabs.json,setup.ts}`；D = `src/render/palette.ts`；
  ?? = `src/world/palette-data.ts`、`test/palette-budget.test.ts`。docs：`ART_PALETTE_AUDIT.md`、`QA_VERIFICATION_S1.md`、
  `PI_DURABLE_MIGRATION.md`、`TESTING.md`、`pixel-agent-chief-starter/docs/PROJECT_PROGRESS.md`；
  新增 `docs/ADR_AGENT_BRIDGE.md`、`docs/ART_SLICE_REVIEW_CHECKLIST.md`。
  **主产品既有 35 个改动（.env.example / .gitignore / config×2 / scripts×11 / server×8 / web×7）与 16:41 快照逐条一致，未被动**。
  本轮我对仓库的唯一改动是本文件。
- **最终结论：有条件通过**（2 条 P2 + 3 条 P3；1 项未验证、1 项待用户评审）。代码收敛本身全部实测达标。

### 通过项（全部本会话自跑）

| # | 验收项 | 实测结果 |
|---|---|---|
| 1 | `npm run check` | `npx tsc --noEmit` → **exit 0** |
| 2 | tests 148/148 | `node --test --experimental-test-isolation=none test/*.test.ts` → **148/148，exit 0**（139 原有 + 9 新增 palette-budget） |
| 3 | 色数 | `audit-palette` → 世界 **16**（引用 90）/ UI **8**（引用 45）/ 共享 **1**（仅 `#e3a13c`）/ 并集 **23** / 越界 **0 / 0**；`unusedTokens` 空；`--strict` **exit 0**、默认 **exit 0** |
| 4 | 对抗 M1（世界加色） | `WORLD` 注入 `ghost: 0x8f7a4c` → 世界 17 / 并集 24 / `--strict` **exit 1**；字节级还原后 SHA256 `C95D1CAB…C1E2` 与改前一致、strict 回 0 |
| 5 | 对抗 M3（UI 加第 9 色） | `index.html` 注入 `.qa-ghost{color:#8f7a4c}` → UI 9 / `--strict` **exit 1**；还原后 SHA256 `BC58F4BB…A994` 一致 |
| 6 | 对抗 M4（prefab 槽写回旧 hex） | `star_archive.body` → `"#6b7a6a"` → 世界 17 / `--strict` **exit 1**；解析器兼容分支实测解析为 `0x6b7a6a` 并给出可用 token 清单告警；预算测试 **3 条变红**；还原后 SHA256 `03B9FD94…E572` 一致 |
| 7 | gap A 旧存档 palette | 自写独立探针（不采信施工方测试）：旧数字色 / 旧 `#rrggbb` 字符串 / 单槽位旧色，读档后**全部重解为当前 prefab 色**；不传蓝图表时保留存档色（向后兼容）；`ghost_forge` 保留存档色不崩，且**同一缺失 id 只告警 1 次**（2 栋、3 栋同 id 均实测 1 次；两个不同缺失 id → 2 次）；全命中 → 0 告警 |
| 8 | 存档 v1 指纹/样例 | `src/world/save.ts`、`docs/save-v1.sample.json`、`test/save-fingerprint.test.ts` 三者**相对 HEAD 零改动**；独立重算（自带 collectSavePaths + FNV-1a 实现，不 import 项目代码）→ **99 条叶子路径、指纹 `f53a0736d782d657`**，与常量及样例 `meta.schemaFingerprint` 一致；加字段变异 → 指纹变 `fe6f2990f94b5f34`（门有效） |
| 9 | 截图技术规格 | native 实测 **480×320**；400pct 实测 **1920×1280**，且是 native 的**严格 4× 最近邻**放大（3 张图各 2,457,600 次像素比较，**0 处不匹配**）；canvas 960×640 → native 为 **2× 最近邻降采样取左上像素**（3 张图共 460,800 个 2×2 块，**0 处块外不匹配**） |
| 10 | 晴/雨/夜同构图 | 48×32 网格亮度结构相关性：sunny↔night **0.9906**、sunny↔rain **0.9446**、rain↔night **0.9318**；对照组 sunny vs 水平镜像 sunny = **0.662**（证明该指标能分辨构图差异，0.93+ 即同构图不同叠加） |
| 11 | 夜景结论独立核验 | **成立**。地形类间对比度：grass/stone **1.10:1**、grass/farm **1.04:1**、grassLit/stone **1.01:1**、grass/plaza(路径) **1.40:1**；均匀压暗后进一步压缩：×0.6 → 1.05 / 1.02 / 1.18，×0.45 → 1.02 / 1.02 / 1.12。实测夜帧为晴帧的 **0.5554 倍**（153,136 像素平均 R 比），落在 ×0.6 区间 → **路径与石面确实并团，16 色不足以支撑有层次的夜景**，可作为 18 色申请素材 |
| 12 | `node_modules` 补丁不进 git | `pixel-world/.gitignore:1` 忽略 `node_modules/`；`git ls-files pixel-world/node_modules` = **0 个跟踪文件**；`git status` 对 node_modules 为空 → vite 的 try/catch 补丁**不可能进 git** |
| 13 | 应用可构建 | `npx vite build` → **exit 0**（产出 1,280.42 kB，仅 chunk 大小告警）；`node --check scripts/smoke.mjs` → exit 0；vite dev server 起得来（`http://127.0.0.1:5188/` HTTP 200） |

### 未验证 / 待用户评审

- **未验证：smoke 114/114**。官方 `npm run test:browser` 在本沙箱 **`browserType.launch: spawn EPERM`**
  （Playwright 用 `--remote-debugging-pipe` + 管道 stdio，被沙箱禁）。我做了一轮绕开尝试：用
  `Start-Process` 起系统 Chrome headless + `--remote-debugging-port`（9222/9223/9224 三种参数组合），
  Chrome **均以退出码 1 立即退出**、无任何输出、不生成 `DevToolsActivePort`——Chrome 自身的多进程 IPC
  同样依赖命名管道，也被沙箱挡。故 114/114 **无法复跑，按未验证处理，不计入通过**。
  静态侧只能确认：smoke.mjs 有 **106 个 `check(` 调用点**（运行期 114 条说明有循环内调用），无法静态证实 114。
- **不通过（可复现性）：截图前置不可复现**。`pixel-world/eval/` 下**只有 PNG、没有任何拍摄脚本**，
  仓库里也查不到记录前置步骤的文档；且 `src/render/weather.ts:31,32,55,56` 的 140 个雨滴用
  **`Math.random()`、没有种子注入点**（splash 是确定性的、地形抖动 `tilemap.ts:53-57` 是哈希种子，
  但雨滴不是）→「雨滴固定种子」无法通过应用自身 API 达成，只能是外部注入，而注入方式没有记录。
  因此这组图**不能作为可复现的验收证据**（技术规格本身合格，见通过项 9/10）。
- **待用户评审：3 秒指认 / 主角·雨幕·路径可读性的人类判定**。本会话模型无图像输入能力，无法看图。
  已做的客观替代测量：角色亮部 vs 草地 = 皮肤 **6.23:1**、围巾 **3.02:1**、琥珀 **5.31:1**、饰边 **5.23:1**
  → 主角可靠亮部读出；但斗篷 **1.40:1**、斗篷暗部 **1.00:1**（与草地完全同亮度）→ 斗篷本体并团。
  晴帧中角色精确命中 token 像素仅 **174 px**（皮肤 38 / 斗篷 60 / 斗篷暗 20 / 围巾 24 / 轮廓 32，全部聚在 x≈247.5），
  占 153,600 px 帧的 0.11%；路径 vs 草地 1.40:1 偏弱。**以上是数据，不是人类判定，不得据此写通过。**

### 缺陷列表

- **P2-1 `docs/ART_PALETTE_AUDIT.md` 全文停留在 task-28 之前，与实跑全面不符**。task-28 对该文档只 +1 行
  （task-27 的 6 色溯源说明），没有随收敛重生成。实跑已是 16/8/1/23/0/0，文档仍写世界 71/85、UI 17/45、
  共享 2、并集 86、世界越界 55、UI 越界 9（§0 摘要、§2.1、§3.1/§3.2 Top 表、§5.1/§5.2 收敛清单、§8.3
  「预期（task-27 后）」、附录 A 全部过期）；§1.2/§1.3 仍称 token 定义在**已删除的 `src/render/palette.ts`**；
  §2.2 每文件表仍是 palette.ts 21/21、buildings 4/4、decor 11/9、tilemap 2/2、villager 10/10、weather 4/3，
  而实跑这些文件 **全部 0/0**（token 已移到 `src/world/palette-data.ts`，且该文件不再进每文件表）；
  §1.4/§8.1 的 `--strict` 文案「本轮未接入 CI」也与脚本实际的「已启用」不一致。
  复现：`node pixel-world/scripts/audit-palette.mjs` 与文档任一处计数对照。预期：文档计数与实跑一致。
  这与 task-27 已关闭的 P2 是同一类失败，建议按同一标准重生成附录 A 与正文。
- **P2-2 调色板截图证据不可复现**（见上「不通过（可复现性）」）。预期：`eval/` 附拍摄脚本 + 前置步骤
  （清 localStorage / zoom 与降采样方法 / 冻结帧 / 种子注入方式）可复跑。
- **P3-1 `--strict` 门禁比验收口径窄**。`audit-palette.mjs:803-805` 只判唯一色**数量**（≤16/≤8），
  不判色值是否在提案内、不判共享数与并集。实测变异 **M2**：把 `WORLD.ink` 从 `#14110d` 改成 `#000000`
  → 世界仍 16、共享仍 1、并集仍 23 → `--strict` **exit 0**、`palette-budget.test.ts` **9/9 仍绿**，
  只有人读报告的越界表多出 1 行 `#000000`。即「把某个已收敛 token 换回旧 hex」**不会**被任何自动门禁挡住
  （Chief 该条对抗预期不成立；只有"加新色"和"prefab 槽写旧 hex"会红，见 M1/M3/M4）。
  建议给 `palette-budget.test.ts` 补一条「`Object.values(WORLD)` 的 hex 集合 == 提案 16 色集合」断言。
- **P3-2 施工方夜景结论的数对不准**。原文「路径与广场石面并团（W05/W06 1.10:1）」：实测 W05 stone vs
  W06 plaza = **1.27:1**，而 **1.10:1 实际是 W03 grass vs W05 stone**。结论方向正确（已独立复验），
  但引用的色对与数值错配，建议改正，避免后续按错误数对做决策。
- **P3-3 `npm test` 在本沙箱不可用**。`package.json` 的 `test` 是 `node --test test/*.test.ts`（默认子进程
  隔离 → 每文件 spawn → EPERM）；加 `--experimental-test-isolation=none` 单进程跑则 148/148。
  建议把该 flag 写进 `test` 脚本并在 `docs/TESTING.md` 记录，否则任何人照 README/AGENTS.md 跑都会撞墙。

### 观察（非缺陷，交像素美术裁决）

- 三栋常驻铺子当前 palette：`code_forge` 与 `ink_house` **四个槽完全相同**（body `#8a5a44` / roof `#4a3f39` /
  trim `#c9a86a` / window `#e3a13c`），只有 `star_archive` 不同（roof `#2d3b2e` / trim `#d8dad3`）。
  即 §4.1/§7 预言的「退化为剪影 + 饰边区分」已成事实，且其中两栋**连饰色都没差别**，只能靠剪影与分区。
- 晴帧中 `W11 amber #e3a13c` 精确命中 **0 px**（当前无点亮的窗光/进度），焦点色在静帧里不出现。

---

## task-28 修复后最终签收复验（2026-10-19 17:15–17:40，lead · QA Engineer）

- **基线声明**：HEAD `5214eb5`；工作区脏树。`pixel-world/` 实测 **15 M + 1 D + 4 ??**（Chief 预期 16 M + 1 D + 4 新；
  M 数差 1，已逐条核对：README/package.json/audit-palette.mjs/index.html/main.ts/render×6/scenes/SandboxScene.ts/
  world/{blueprints.ts,prefabs.json,setup.ts}；D = `src/render/palette.ts`；4 个新文件与预期完全一致）。
  docs：`ART_PALETTE_AUDIT.md`、`TESTING.md`、`PROJECT_PROGRESS.md`、`QA_VERIFICATION_S1.md` +
  新增 `ADR_AGENT_BRIDGE.md`、`ART_SLICE_REVIEW_CHECKLIST.md`、`pixel-world/docs/E2E_PALETTE.md`、
  `pixel-world/scripts/capture-palette.mjs`。**主产品原有 35 个已跟踪改动逐条未动**（本次 37 个 M = 35 + 上述两个 task-28 文档）。
- **最终结论：通过**（附 2 条 P3；1 项因沙箱限制记未验证，不计入通过）。上一轮 2 条 P2 的修复机制均已独立复核生效。

### 现场数字（本会话重跑）

| 项 | 结果 |
|---|---|
| `npm run check` | `npx tsc --noEmit` → **exit 0** |
| `npm run test:inprocess` | **150/150，exit 0**（148 + 2 新增：提案值断言 ×2；`palette-budget.test.ts` 由 9 → 11 条） |
| `node scripts/audit-palette.mjs` | 世界 **16**（引用 90）/ UI **8**（45）/ 共享 **1**（`#e3a13c`）/ 并集 **23** / 越界 **0 / 0** |
| 退出码 | `--strict` **exit 0**、默认 **exit 0** |
| `test` 入口 | 仍为 `node --test test/*.test.ts`（**未改**）；新增 `test:inprocess` 用 isolation=none，可用 |

### P2-1 复验：审计文档已按实跑重生成

- 头部计数 **16 / 8 / 1 / 23**、越界 **0 / 0**、`--strict` exit **0**，与实跑一致；token 位置已改为
  `src/world/palette-data.ts`；两张越界表为空表（保留表头）；§「收敛记录」把 task-26/27 时点快照（71/17/2/86、55/9）
  明确移交给提交 `5214eb5` 的历史版本，本文只写当前事实——这个处理方式比上一轮更好。
- **逐行比对（按 task-27 的同一标准）**：`--md` 输出「## 计数」至文末 vs 文档 L11–144，
  非空行 **script 111 / doc 112**，其中 **109 行全等、2 行不一致**。Chief 报的「111/111 全等」不准确。
  两处差异**都是 `weather.ts` 的文件:行号**：文档写 `weather.ts:51,63`（pale）与 `weather.ts:19,43`（cloakDark），
  实跑为 `weather.ts:78,90` 与 `weather.ts:45,69`。原因是 `src/render/weather.ts` 的 mtime **17:20:51** 晚于
  文档重生成的 **17:16:10**——种子支持是后加进去的，行号因此整体下移。
  计数、色值、token 名、引用数、站点前三后位全部一致，**仅 2 处行号引用过期**。
- 判定：P2-1 **实质性关闭**，残留记 **P3-2**（见缺陷列表）；提交前用同一条命令再生成一次即可全等。

### P2-2 复验：截图可复现机制已建立

- **脚本只连不启**：`scripts/capture-palette.mjs:167` 只有 `chromium.connectOverCDP(CDP_URL)`，
  全文无 `chromium.launch`；CDP 连不上时（实测把 `CDP_URL` 指向 9399）打印
  「无法连接 CDP 端点 … 这个脚本不会自己 launch Chrome（沙箱会 EPERM）」+ 可直接复制的启动命令，**exit 1**，
  不会自行拉起浏览器 ✓
- **三把锁都在代码里**：① `window.__RAIN_SEED__` → `weather.ts` 的 LCG 种子；② `Math.random` 被同一 LCG 覆盖；
  ③ rAF 改为帧计数 + 固定 `1000/60`ms 时间戳，冻结在第 240 帧、雨在第 30 帧按下；另有热身页丢首帧差异。
- **默认行为未变（重点核过）**：`weather.ts:26-31` `rainSeed === null` 时**直接走 `Math.random()`**；
  全仓库 grep `__RAIN_SEED__` 只有捕获脚本写入、`weather.ts` 只读，应用自身不设 → **线上行为不变** ✓
- **LCG 一致性实测**：捕获脚本注入的 LCG 与 `weather.ts` 的 `rainRandom()` 在种子 20261019 下
  **前 5 项完全同序列**（0.443158 0.987910 0.134867 0.960879 0.895569），且各自动复跑结果一致 → 确定性成立 ✓
- **文档**：`pixel-world/docs/E2E_PALETTE.md`（前置/命令/环境变量/产物/方法/自检）与 README 新增段一致，
  都写清了「脚本不负责起浏览器」和沙箱 EPERM 的原因 ✓
- **未验证：双跑字节一致**。Chromium 在本沙箱**起不来**：完整 Chrome 与 `chrome-headless-shell` 共 5 种参数组合
  （9222/9223/9224/9334/9335，含 `--single-process`、`--disable-gpu`、cmd 重定向捕获输出）**全部以退出码 1 立即退出、
  无任何输出、不生成 `DevToolsActivePort`**——Chromium 自身的多进程 IPC 也依赖命名管道。
  因此「跑两次、比 12 个产物 sha 全等」**我无法复现，按未验证处理，不计入通过**。
  旁证（非独立证据）：`eval/screens/palette-*` 12 个文件 mtime 为 **17:21:22–17:21:30**，按 sunny→rain→night
  顺序每态 4 个连续生成，与一次 `capture-palette.mjs` 运行的产物时序吻合；且这些图**晚于**脚本与 `weather.ts` 的
  落盘时间，说明确由新流程产出。但这是施工方的运行，不是我的复跑。

### P3-1 复验：换值门禁已补上

- `palette-budget.test.ts` 新增两条：`WORLD 的每个 token 值都等于提案 16 色（换值必须变红）`、
  `UI 的源色集合等于提案 8 色（换值必须变红）`，提案表逐字抄在测试里。
- **四方交叉核验（我自己抠数据，不采信任何一方的自述）**：审计脚本内 `PROPOSED_WORLD/PROPOSED_UI` ==
  测试里抄的提案表 == `WORLD` 实际值（**token 顺序也一致**）== `index.html` 实跑 UI 源色集合 —— **6/6 PASS**。
- **对抗 M2b**：`WORLD.ink` `0x14110d` → `0x000000` → `--strict` 仍 **exit 0**（证实上一轮的判断：strict 只判色数），
  但新断言 **`not ok 3 - WORLD 的每个 token 值都等于提案 16 色`** → 11 条里 1 红 ✓ 门禁缺口已闭合。
  字节级还原后 SHA256 `C95D1CAB…C1E2` 与改前一致，post-restore 11/11 全绿。

### P3-3 复验

- `package.json` 新增 `test:inprocess`（`node --test --experimental-test-isolation=none test/*.test.ts`），实测 150/150；
  `test` 入口保持原样未改 ✓。（`test` 入口本身在本沙箱仍会 spawn EPERM，这是沙箱限制，`test:inprocess` 就是为此给的口子。）

### 缺陷列表（本轮）

- **P3-2**：`docs/ART_PALETTE_AUDIT.md` 有 **2 处 `weather.ts` 行号引用过期**（pale 51/63→78/90、cloakDark 19/43→45/69），
  因 `weather.ts`（17:20:51）晚于文档重生成（17:16:10）。计数/色值/token 全对，仅行号。提交前重跑一次 `--md` 即全等。
- **P3-3**：`pixel-world/docs/E2E_PALETTE.md` §5 自检写「**八个产物**应逐字节一致」，但 §3 与脚本实际产出是
  **12 个**（3 态 × 4 变体）——文档自相矛盾，改数字即可。
- **P3-4**：`docs/_palette_band_check.py`、`_palette_band_crop.py`、`_palette_screen_check.py` 三个未跟踪临时脚本
  17:2x 仍在仓库里（Chief 已让美术清理、提交前会确认）。提醒：`git add -A` 会把它们一起卷进去。

### 上一轮遗留项的最终状态

- P2-1（审计文档过期）→ **已修复**，残留 P3-2（2 行行号）。
- P2-2（截图不可复现）→ **机制已修复并文档化**；双跑字节一致因沙箱禁 Chromium 记**未验证**。
- P3-1（strict 只判色数）→ **已修复**（M2b 实测变红）。
- P3-3（npm test 沙箱不可用）→ **已修复**（`test:inprocess`）。
- 上一轮 P3-2（夜景数对错配：W05/W06 1.10:1）→ 施工方已在 `ART_SLICE_REVIEW_CHECKLIST.md` 侧修正映射，
  本轮审计文档的对比度表由脚本生成，不再有手写数对；该条关闭。

### 收口

- 我对仓库的唯一改动仍是 `docs/QA_VERIFICATION_S1.md`（纯追加）；变异过的 `palette-data.ts` 已字节级还原
  （SHA256 与改前一致）；我起的 vite 5188 已停、无残留监听、无 dist 残留。
- **待用户评审**：3 秒指认 / 晴雨夜可读性的人类判定（本会话模型无图像输入，只做了对比度与构图的数据测量）。
