# ADR_SAVE — 世界存档（D-SAVE-1）

> 状态：**proposed**（用户/Chief 批准后才允许 task-18 开工；S2 顺序见 `NEXT_PHASE_PLAN.md:100`）
> 日期：2026-10-19 · 作者：Arch · 依据：用户原话「只存世界自有事实；读档先与台账对账；带版本号」；`PROJECT_BRIEF.md:30,43`；D2 已 accepted（`DECISION_TRAIL_20261019.md:35-39`）
> 一句话：**单槽 JSON 存档只序列化世界自有事实；台账是真相源、存档不复制；读档先重建世界、再与台账对账；高版本/坏档拒绝加载而非静默重置。**

## 决策 D-SAVE-1

采用**单存档槽 + `schemaVersion` + 迁移链 + 读档对账**：存档是世界自己的事实（谁在哪、手上什么活、世界长什么样、攒了多少），不是台账副本，也不是渲染/日报缓存。读档分两步：先 `restore` 出世界，再由台账首个成功快照裁决台账相关事实；对账前世界不驱动台账单。

### 1. 存什么（`WorldSaveV1`，只存世界自有事实）

| 字段 | 源（当前代码） | 说明 |
|---|---|---|
| `now` | `simulation.ts:167,204-206` | 逻辑时钟（ms，世界内）。读档从存档值继续，**不做离线快进** |
| `villagers[]` | 内部 `Villager`（`simulation.ts:90-105,177-192`） | mode/pos/facing/visible/taskId/bubble/bubbleUntil/saluteUntil/deliverUntil/nextRoutineAt/routineIndex/routineLabel/inPlaceUntil/**path/pendingRoute/pendingBuildingId**；S2-1 后为数组 |
| `buildings[]`（顺序保留） | `simulation.ts:129,175,700-722` | `PlacedBuilding` 只读事实；顺序 = 落位顺序，重建网格用 |
| `roads[]` / `props[]` | `:137,136,268,267` | 铺好的路、神恩摆件 |
| `tasks[]` | `:131,265`；`types.ts:88-104` | 含 `source`；**台账单是"未对账投影"，见 §2** |
| `pending[]` + `nextPendingNumber` | `:158-160,266,575-583` | 神谕等待队列（FIFO） |
| `activities`（原始 `WorkstationActivity`） | `:132`；`types.ts:159-178` | 存档里只存原始态（`startedAt/estimateMs/determinate/...`），不存派生 elapsed/progress |
| `constructions`（原始 remaining/total） | `:138`；`types.ts:193-198` | 同上，displayName 派生 |
| `weather`（完整 `WeatherState`） | `:141,213`；`weather.ts` | kind/intensity/remainingMs 全存，保证雨幕 TTL 连续 |
| `faith`（points/milestones） | `:142-143,271` | 世界记忆 |
| `deliveries`（ledger/mock 计数） | `:145-146,273,948-951` | 台账已完成单会从 `tasks` 移除，**计数不可由 tasks 反推**，必须存 |
| `nextTaskNumber` | `:168` | 防读档后 id 复用（`t1` 撞车） |

**不存（派生/瞬时/他人真相）**：`WorldGrid` 单元格（由 `buildings` 顺序 + `grid.place` 重建，`grid.ts:105-121`）；`events` 环形缓冲（诊断缓存；日报由 `report.ts:17-71` 从快照派生）；`queue/lastResults`（命令通道，非世界事实）；快照里的 `workstations.elapsedMs/progress`、`umbrella`、`ledger.*` 链接态；Phaser 视图/粒子/相机；日报/编年史文案。`ledgerConnected/ledgerSyncedAt/ledgerComplete/ledgerOrder`（`simulation.ts:147-156`）**不入档**，读档后置 `connected=false, lastSyncAt=null, complete=true, order=[]`。

### 2. 读档与台账对账（台账优先；未对账不驱动）

- R1 **台账是唯一真相源**：存档里的台账单只是世界上一轮的**投影缓存**（id/kind/title/status/buildingId/blockedQuestion），带 `unreconciled` 标记；读档后**不派活、不继续 work、不交付、不撤单**，只在状态行/纸面标「读档未对账，等台账」。沙盒单（`source==='sandbox'`）照常续跑。
- R2 **首个成功 sync 裁决**（复用 `ledger.ts:15-32` 的 tasks/finished/complete/order；`simulation.ts:316-408` 的在线语义基础上加 `reconciling` 分支）：
  - id 在 `finished`：若读档时它正在活动 → 走一次正常交付并计 1 次；若存档已是 done → 只删除，**不重复计数**；
  - id 在活动清单：**以台账为准**覆盖 status/kind/title（本地 done/running 不敌台账）；
  - id 不在且 `complete===true`：删除本地投影 → **撤单不得因存档复活**（沿用 `simulation.ts:368-370` 的"不完整快照不撤单"）；
  - id 不在且 `complete===false`：保留未对账，不撤单（`simulation.ts:340-344`）。
- R3 对账成功后 `snapshot.ledger.reconciled=true`（新增字段），随后与现有在线 sync 完全一致；世界仍**只读台账**，不写回。
- R4 **断线/`?ledger=off`**：保留本地展示，`reconciled=false`，状态行写「未对账 · 离线 Mock」；恢复连接后按 R2 收敛。不得因离线而把台账单当沙盒单自动收尾（`simulation.ts:673-698` 已完成权限分离）。

### 3. 版本与迁移

- `schemaVersion`（整数，当前 `CURRENT_SCHEMA_VERSION=1`）。加载顺序：`JSON.parse` → 形状校验 → 版本分流 → 迁移链 → `assertSaveV1` → 重建世界。
- 缺字段/非整数版本 → 视为坏档（见 §4）；`schemaVersion > CURRENT` → **拒绝加载**并提示「存档来自更新版本，未加载、未覆盖」；`< CURRENT` → `MIGRATIONS[v]` 逐级迁移，任一步抛错 → 拒绝加载，原槽保留。
- 迁移只做形状转换（例：1→2 把 `villager` 单数转 `villagers[]`；S2-1 先落地则 v1 直接含 `villagers[]`），不补业务事实、不伪造台账。
- `assertSaveV1` 校验：枚举合法、数组元素形状、引用完整性（`task.buildingId`/`pending.villagerId` 指向已恢复实体）、`nextTaskNumber > max(现存 tN)`。

### 4. 单槽与写入时机

- **单槽**：`localStorage['xiaoxi.pixel-world.save.v1']`，值是 JSON 文本；不做多槽/云同步/加密/跨设备。`localStorage.setItem` 单键原子；若未来改文件落盘，用「临时文件 + rename」原子替换。
- **写入点**：交付完成（`finishActiveTask/finishInPlace` 之后）、施工落成（`stepConstructions` 删项之后）、神恩里程碑、手动「存档」按钮（`index.html:130-148` 加按钮）、每 30s 节流一次、`pagehide/beforeunload` 尽力而为。
- **时机纪律**：只在 `update()` 返回后（命令已全部结算，`simulation.ts:204-221`）序列化，禁止在 update 中途/命令队列非空时写；写前先 `JSON.stringify` 成功再 `setItem`。
- **崩溃恢复**：只保证「最近一次成功写入」；不做 WAL/多份轮转。世界 `now` 冻结，重开从存档时刻继续。
- **坏档**：parse/校验失败 → 不覆盖原槽（先备份到 `...save.v1.corrupt`），提示「存档损坏，已用新世界启动（原档保留）」；不静默重置。写失败（QuotaExceeded 等）→ 提示并暂停自动存档，手动仍可试。
- **接线**：`SandboxScene.create()` 现有 `new Simulation`（`SandboxScene.ts:99-112`）改为「有档→`Simulation.fromSave`；无档→新建」；`main.ts:11-12` 先算重建后的 grid 再起 Phaser。

### 5. 明确不做

多存档、云同步、账号/跨设备、加密、离线时间快进（挂机收益）、存档压缩、文件导入导出、事件流/日报持久化、回退到任意历史快照。

## 实现接口（task-18 新增，不改现有公开语义）

1. `src/world/save.ts`：`WorldSaveV1`、`CURRENT_SCHEMA_VERSION`、`assertSaveV1(raw)`、`migrateSave(raw)`、`MIGRATIONS`。
2. `Simulation.toSave(): WorldSaveV1`（纯函数，**不要复用 `snapshot()`**——后者是派生视图，私有的 path/until/activities 不在里面，见下）。
3. `static Simulation.fromSave(save, deps): Simulation`（先断言/迁移/重建网格，再恢复字段；避免半初始化实例）。
4. `setup.ts` 增 `rebuildGridFromPlacements(placements): WorldGrid`：`new WorldGrid(WORLD_SPEC)` 后按存档顺序对每栋 `grid.place`（复用 `setup.ts:76-114`），再 `assertWorldInvariants`（`:133-158`）。
5. `src/ui/save-store.ts`：localStorage 单槽 `read/write/clear` + 错误回调；`snapshot.ledger` 增 `reconciled: boolean`；状态行（`SandboxScene.ts:366-383`）加「未对账」。

**私有→需暴露 vs 派生**：必须暴露（`toSave` 从实例读）——`villager.path/pendingRoute/pendingBuilding/各 Until/routineIndex`（`simulation.ts:90-105,162-165`）、`activities`/`constructions` 原始态、`weather.intensity/remainingMs`、`nextTaskNumber/nextPendingNumber`、`ledgerDeliveries/mockDeliveries`；纯派生（不入档）——`workstations.elapsedMs/progress`（`:226-236`）、`umbrella`（`:260`）、`ConstructionSnapshot.displayName`（`:238-247`）、`beats`（`:140,176`）、日报（`report.ts:17-71`）、`events.slice(-12)`（`:269`）。

## 风险 / 与现有代码的冲突（实现前须处理）

1. **网格没有快照边界**：`WorldGrid` 在运行时被 `place()` 改 `cells`（`grid.ts:105-121`），但 `snapshot()` 不含 grid；读档只能按 `buildings` 顺序 replay（`setup.ts:53-73`）。顺序、门口被后建建筑覆盖等行为必须成为存档不变量，否则读档后 A*/通行与存档前不同。
2. **`snapshot()` 不是存档**：`Villager` 13 个内部字段只暴露 9 个（`simulation.ts:90-105` vs `types.ts:216-228`），`activities`/施工剩余/天气浓淡/TTL 都只以派生值出现；直接拿快照当档会丢"她走到一半/施工剩 0.8s/雨还剩 20s"，读档表现跳变。这也是 `toSave()` 必须新增的原因。
3. **对账不能直接复用在线 `sync`**：现有 `sync` 只对"新任务"写 status、只在 `complete && 缺席` 时撤单（`simulation.ts:383-398,368-370`），且已交付的台账单会从 `tasks` 移除、完成数只留在 `deliveries`；读档需要"以台账覆盖本地 + 缺席即丢 + 不重复计数 + 不复活"的独立 reconciling 分支，否则要么复活撤单，要么把本地 done 当真相。
4. **前置依赖**：S2-1 多角色仍在改 `WorldSnapshot.villager`→`villagers[]`（`NEXT_PHASE_PLAN.md:108`）。存档 schema 必须以 S2-1 合并后的形状冻结；若 task-18 先开工，v1 预留 `villagers[]` 并在 1→2 迁移时拆单数。

## 验收断言（3 条，QA 可照抄）

1. **续跑等价**：同一 deps 跑 50 拍 → `save = a.toSave()` → `b = Simulation.fromSave(JSON.parse(JSON.stringify(save)), deps2)` → 两者再各跑 50 拍，`JSON.stringify(b.snapshot()) === JSON.stringify(a.snapshot())`（比较 `now/villagers/tasks/pending/props/roads/faith/deliveries/weather.intensity/constructions.remainingMs/activities`；忽略 `events` 与 `ledger.*`）。失败即"可序列化边界漏字段"。
2. **对账不复活/不重复**：存档含台账单 `c1`（in_progress）；读档后首个 `sync({tasks:[], finished:['c1'], complete:true})` → 世界无 `c1`、无 working、`deliveries` 不重复+1；第二个 fixture `sync({tasks:[c1 running]})` → 按台账恢复 assigned 并正常派活；第三个 `complete:false` → 不撤单、保持 `reconciled=false`。反向：存档里 `c1` 已取消但旧档仍含它 → 不得把它复活进 working。
3. **版本/坏档**：`schemaVersion: 99` → 拒绝加载、提示、原槽字节不变、新世界可玩；缺字段/引用不存在 → 拒绝加载不崩溃；旧版本 fixture 经迁移链成功加载；`npm run check` 与 `npm test` 全绿（S1 基线 90/90，`NEXT_PHASE_PLAN.md:96`；task-17 合并后以其新基线为准）。

---
*本 ADR 只写决策，不改代码；task-18 实现须逐条对应 §1–§4 与 3 条断言，并在 `docs/TESTING.md` 记录手工命令。*
