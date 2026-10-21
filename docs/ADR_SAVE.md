# ADR_SAVE — 世界存档（D-SAVE-1）

> 状态：**final**（用户 2026-10-19 定稿：6 项按推荐通过 + 5 条硬要求；**§1 frozen**）。Chief 书面确认记于 `DECISION_TRAIL_20261019.md` D-SAVE-1；task-21/S2-1b 已 QA 通过并提交 `2bde7cb`；task-18 按本 ADR 开工（`NEXT_PHASE_PLAN.md:100,110`）。
> 依据：用户原话「只存世界自有事实；读档先与台账对账；带版本号」；`PROJECT_BRIEF.md:30,43`；D2 accepted（`DECISION_TRAIL_20261019.md:35-39`）。
> 一句话：**单槽 JSON 只序列化世界自有事实；台账是真相源、存档不复制；读档先重建世界、再与台账对账；已结算按 id 去重、Mock/台账/角色分账、时钟不混用；高版本/坏档/存储失败一律降级不写坏原档。**

## 决策 D-SAVE-1

单存档槽 + `schemaVersion` + 迁移链 + 读档对账。**冻结规则**：§1.1 运行时快照（已冻结，**不含 settled**）与 §1.2 存档文件（save-only）分离；§1.2 冻结为 `schemaVersion=1`，此后字段增删必须升 v2 + 迁移；task-18 按本 final ADR 实现。
**S1 冻结确认材料**：① Chief 书面确认（`DECISION_TRAIL_20261019.md` D-SAVE-1）；② 规范样例 `docs/save-v1.sample.json`（字段与 §1.2 完全一致）；③ 指纹常量 `SCHEMA_V1_FINGERPRINT = "f53a0736d782d657"`，放在 `src/world/save.ts`，样例 `meta.schemaFingerprint` 同值；④ 算法：取 §1.2/样例的叶子路径（数组记 `[]`，数组本身也记一条；`meta.schemaFingerprint` 不参与），排序后 `\n` 连接、UTF-8 字节做 FNV-1a 64-bit（offset 14695981039346656037、prime 1099511628211），输出 16 位小写 hex；⑤ 指纹测试 `pixel-world/test/save-fingerprint.test.ts`：样例路径哈希==常量、`sample.meta.schemaFingerprint`==常量、`WorldSaveV1` 与 `typeof SAMPLE_V1` 编译期 Equal 断言、变异探针（给样例加字段→哈希变→测试红）——**字段变了但 `schemaVersion` 没升，测试必须失败**；加载时 `meta.schemaFingerprint` 存在且不符→按坏档处理。

### 1. 字段边界：运行时快照 vs 存档文件

**1.1 运行时快照字段（已冻结；`WorldSnapshot` 内没有 `settled`，不要到快照里找/加）**

| 运行时字段 | 出处 | 说明 |
|---|---|---|
| `deliveriesByVillager: readonly VillagerDeliveries[]` | `types.ts:264` | 顺序同 `villagers`；日报/状态行读它 |
| `VillagerDeliveries { villagerId, name, ledger, mock }` | `types.ts:209-214` | 按人分账，台账与 Mock 不合并 |
| `deliveries { ledger, mock }` | `types.ts:262` | 全局汇总，与按人分账并存 |
| `tasks[].workerId` / `villagers[].id` / `villager`(= `villagers[0]`) | `types.ts:104-105,226-247` | 归属与兼容视图 |
| `pending[].villagerId` / `ledger.{connected,lastSyncAt,mock,complete}` | `types.ts:135,266-273` | 队列归属/链接态（链接态不入档） |
| 运行时接口（不存档）：`WorldCommand{kind:'complete',target?}`、`Simulation.completeManually(target?)`、`OracleApi.finish(target?)` | `simulation.ts:47,752`；`oracle-panel.ts:12` | `target`=角色 id 或 name，省略=旧行为 |
| 内部聚合源（不在快照）：`deliveryTally: Map<villagerId,{ledger,mock}>` | `simulation.ts:165,291-299` | 快照按人分账由它派生 |

**1.2 存档文件字段（`WorldSaveV1`，`schemaVersion=1`，frozen）**

| 字段 | 源（当前代码） | 说明 |
|---|---|---|
| `now` | `simulation.ts:181,229` | 逻辑时钟；读档原值续跑（§5） |
| `villagers[]` | 内部 `Villager`（`simulation.ts:98-120`） | id/name/mode/pos/facing/visible/path/taskId/bubble+各 Until/routineIndex/routineLabel/inPlaceUntil/pendingRoute/pendingBuildingId（快照缺的由 `toSave()` 补） |
| `buildings[]`（顺序） | `simulation.ts:146` | `PlacedBuilding`；顺序=落位顺序，重建网格用 |
| `roads[]` / `props[]` | `simulation.ts:153-154,283-284` | 铺路与神恩摆件 |
| `tasks[]` | `:148,281`；`types.ts:88-106` | 含 `source`、`workerId`；台账单=未对账投影 |
| `pending[]`（按角色摊平，保留 `villagerId`） | `:178-179,282`；`types.ts:121-136` | 每角色 FIFO |
| `activities`（数组 `{buildingId, activity}`，原始态） | `:149`；`types.ts:161-180` | 不存派生 elapsed/progress |
| `constructions`（数组 `{buildingId, remainingMs, totalMs}`） | `:155`；`types.ts:194-200` | displayName 派生 |
| `weather`（完整 `WeatherState`） | `:158,231` | kind/intensity/remainingMs |
| `faith`（points/milestones） | `:159-160,287` | 世界记忆 |
| `settled[]`（save-only，见 §2.1） | 新增 | `{id, source, workerId, completedAt, completedAtClock}`——去重/分账唯一真相 |
| `nextTaskNumber` / `nextPendingNumber` | `:182,179` | 防 id 复用 |
| `meta` | 新增 | `{ savedAtWallClock, savedReconciled, schemaFingerprint }`，仅诊断/防漂移 |

**不存**：`WorldGrid` 单元格（按 `buildings` 顺序 replay 重建，`grid.ts:105-121`、`setup.ts:53-73,76-114`）；`events` 与日报（派生，`report.ts:17-85`）；`queue/lastResults`；快照派生值（`workstations.elapsedMs/progress`、`umbrella`、`ConstructionSnapshot.displayName`，`simulation.ts:246-299`）；`deliveries`/`deliveriesByVillager` 计数（由 `settled` 重建）；`ledger` 链接态（读档置 `false/null/true/[]`）。

**1.3 补记（2026-10-19，task-22；只澄清语义，不改 §1 冻结字段）**：`SCHEMA_V1_FINGERPRINT` 是**样例** 99 条路径集的冻结指纹，仅用于锁样例与单测；加载校验不以它为比较对象，而是校验「存档实际路径 ⊆ 允许全集（冻结 99 条 + 已声明 4 条可选路径，完整清单见 `save.ts` 的 `SAVE_V1_OPTIONAL_PATHS`：`world.villagers[].path[].x/y`、`world.villagers[].pendingRoute[].x/y`）」，**只检测新增路径**，不等价于值相等或存在性相等；字段删除与类型变化不在路径集校验范围，现由显式形状校验兜底，缺口记 task-32（最小 required 集，不在 v1 内改），v2 再以正式 schema 承接。`SAVE_V1_PATH_SET` 由冻结路径集 + 可选集派生；任何运行时派生的基准值不得冒充冻结常量。

**1.3 补记（2026-10-20，task-32 完成；两级校验分工，task-32 由此关闭）**：v1 加载校验现在是**两级串联**，`pixel-world/src/world/save.ts` 的 `assertSaveV1` 是唯一入口，`parseSaveText` → `validateSaveV1` 被 `read()` 与 `importText()` 共用（不存第二套平行校验）：

| 级 | 函数 | 防什么 | 判据 |
|---|---|---|---|
| 第一级 **字段级类型/范围/存在性** | `validateSaveV1` | **删字段、换错类型、值越界** | 每个叶子字段都声明「类型 + 范围/枚举」，并枚举 `VILLAGER_MODES` / `TASK_STATUSES` / `TASK_KINDS` / `TASK_SOURCES` / `FACINGS` / `ZONES` / `PROP_KINDS` / `WORK_ACCENTS` / `SILHOUETTES` / `WEATHER_KINDS` / `ACTIVITY_KINDS` / `WORKSHOP_TASKS`；缺失即拒（`requireKey`），**绝不静默补默认值** |
| 第二级 **嵌套键集合冻结** | `assertKeys` | 数组元素裸加字段 | `world.tasks[]` = `SAVE_TASK_KEYS`、`world.settled[]` = `SAVE_SETTLED_KEYS`，逐键一致 |
| 第三级 **路径集合包含** | `collectSavePaths ⊆ SAVE_V1_PATH_SET` | **只防新增字段** | 越界即拒并**指名越界路径**（P2，task-22 定稿） |

关键分工：**第三级只看「路径集合有没有变大」，删字段会让集合合法缩小、路径集合本身拦不住**——所以「删字段/类型变化」这一半必须由第一级逐字段兜住，这正是 §1.3 上一段留下的缺口。两级的边界与依据：字段删除后 `collectSavePaths` 的输出仍是允许集合的子集（例如删掉 `world.weather.intensity` 只剩 `kind`/`remainingMs`，全部合法），故必须另设存在性校验；反过来第一级不关心「多出来的键」，那由第二/第三级负责。允许为 `null` 的字段（`taskId`/`bubble`/`routineLabel`/`pendingBuildingId`/`boundTask`/`buildingId`/`completedAt`/`blockedQuestion`/`workerId`/`weather.remainingMs`）按 `types.ts` 的联合类型放行 null，但**缺键照样拒**。`villagers[].path[]` / `pendingRoute[]` 是合法可选数组：**为空数组合法**，元素存在时必须完整 `{x,y}`。

变异测试证据：`pixel-world/test/save-validate.test.ts` 参数化生成「每条冻结路径 × 删除/错类型/置空」，并断言坏档走「先备份再提示、主槽不变」且加载器不抛未捕获异常；同时锁「样例 / fresh 空数组 / 走路中 / 满数据」四种合法档全部通过。**不改 `schemaVersion`、不改 `SCHEMA_V1_FINGERPRINT`、不改 `docs/save-v1.sample.json`、不动 99+4 路径集**——task-32 因此关闭，v2 再以正式 schema 承接更完整的跨版本语义。

### 2. 对账 + 去重 + 分账（用户点名 ①–③）

**2.1 已结算任务 ID 持久化去重（`settled` 只属于存档文件）**：运行时 `WorldSnapshot` **没有** `settled`；task-18 在 `Simulation` 内新增**私有结算日志**（如 `settledLog`，由 `recordDelivery(villagerId, source)`（`simulation.ts:1075-1082`）交付时 append），它不是快照字段、渲染不读。`toSave()` 由该日志生成 `settled`（id 全局唯一，`tN` 与 `c_*` 不冲突）；`fromSave()` 由 `settled` 重建 `deliveryTally` 与私有日志，从而重建快照的 `deliveries`/`deliveriesByVillager`（不单独持久化计数）。写档不变量：`settled` id 唯一；`tasks` 中 `status=done` 的 id 必须在 `settled`（否则按坏档处理）。交付入口先查 `settled`：命中→只收尾、不计数、不重复写；未命中→正常交付并 append。与台账 `finished`：已 settled 的 finished id **不重新交付/不计数**；未 settled 且当前活动→交付一次并 append；`complete===true` 且缺席→丢弃、**不复活**。
**2.2 分账 + 时钟不混用（S2）**：`source` 落每条 `settled`——台账单只有台账 `finished` 才记 ledger；沙盒单永远记 mock（离线/在线一致）；对账**不得**改判来源。`completedAt` 按来源分时钟：**ledger** 用台账事件时间 `closed_at ?? updated_at`（`GET /api/executive/commissions` items 由 `server/interaction/threads.py:596 _project` 原样返回 epoch 秒，×1000），`completedAtClock:'ledger'`；台账时间缺失→`completedAt:null`+`clock:'ledger'`，**禁止用本地时间顶替**。**mock** 用 `Date.now()`（epoch ms），`completedAtClock:'local'`、`source:'mock'`。两种时钟**禁止混算/混排序**；日报时序只用同来源。字符串契约（冻结）：日报 `{name}交付 {total} 单（台账 n / Mock|离线 Mock m）`、多角色 ` / ` 连接、0 单 `{name}交付 0 单`（`report.ts:38-50`）；收尾全闲/全忙/混合三措辞（`report.ts:103-123`）；状态行「默认交付：{name}的单」（`SandboxScene.ts:415-421`）；人工交付回执带角色名+Mock 标签（`simulation.ts:790-796`）。
**2.3 对账失败显示与重试**：快照 `ledger` 增 `reconciled: boolean`；读档后 `connected=false, reconciled=false`，状态行（`SandboxScene.ts:398-421`）显示「读档未对账 · 台账离线 · 离线 Mock」；`meta.savedReconciled` 只诊断。重试不新增逻辑：复用 `ledger.ts:49-58,280` 的 4s 轮询 / 30s REST 对账 / 500ms→5s 退避 / 5s 超时；首个成功 sync 才 `reconciled=true`；`?ledger=off` 永久未对账并明示。对账前台账单只读展示，不派活/不交付/不撤单。
**2.4 对账裁决**：首个完整快照——id 在 `finished`→（未 settled 才）交付一次；id 在活动→以台账覆盖 status/kind/title，workerId 重新归属；id 缺席且 `complete=false`→保留未对账、不撤单。台账仍只读，不写回。

### 3. 版本、迁移、坏档与备份（S4）、高版本（④⑤⑧）

- `schemaVersion` 整数，当前 `CURRENT=1`；加载：`JSON.parse`→形状校验→版本分流→迁移链→`assertSaveV1`→重建世界；任一步抛错→拒绝加载、原档保留。
- **高版本只读**：`> CURRENT` → 拒绝加载并提示；不迁移、不降级、不回写；原槽字节保留；**暂停自动存档**，仅用户显式「覆盖」才写。
- **坏档与备份（S4）**：备份名带时间戳 `...save.v1.corrupt.<epochMs>`，**保留最近 3 份**；配额紧张时**先删最旧备份，再写主存档**；备份失败不得导致主存档写失败（主存档自身失败才按 §4 降级）；坏档不静默重置、不清空。
- 迁移样例 v1→v2（签名 + 断言）：
```ts
type Migration = (raw: Record<string, unknown>) => Record<string, unknown>;
export const MIGRATIONS: Record<number, Migration> = {
  1: (raw) => ({                                  // v1→v2：settled 记录补 completedAtClock（历史记录 0=未知，不伪造）
    ...raw, schemaVersion: 2,
    world: { ...(raw.world as object), settled: (((raw.world as { settled?: {id:string;source:string;workerId:string;completedAt?:number|null}[] }).settled) ?? [])
      .map((r) => ({ ...r, completedAtClock: r.completedAtClock ?? 'local' })) },
  }),
};
// 断言：migrateSave({schemaVersion:1, world:{...核心字段, settled:[{id:'t0',source:'mock',workerId:'v1',completedAt:123}]}})
//   → schemaVersion===2；id/source/workerId/completedAt 不变；completedAtClock==='local'；settled.length===1（不重复/不丢）。
```

### 4. 单槽、写入时机、存储失败与告警（S3/S5，⑥）

- **单槽（S5）**：`localStorage['xiaoxi.pixel-world.save.v1']`；JSON 文本；不做多槽/云/加密/跨设备。Vite 保持 `strictPort: true`（`pixel-world/vite.config.ts:9`）；读不到存档时提示「可能是来源(origin/端口)变了」；**手动导出/导入不在本卡，另立 task-22**。`setItem` 单键原子；未来文件落盘用「临时文件+rename」。
- **写入点**：交付完成、施工落成、神恩里程碑、手动「存档」按钮（`index.html:130-148`）、每 30s 节流、`pagehide` 尽力而为；且**只在 `update()` 返回后**（命令已结算，`simulation.ts:227-244`）序列化。
- **崩溃恢复**：只保证最近一次成功写入；无 WAL/多份轮转。
- **S3 已结算历史**：1.0 **不设硬上限**；`settled.length > 5000` 时状态行/控制台**告警一次**（每会话一次），不截断、不删除。
- **存储失败（S3/S6）**：`getItem/setItem` 全 try/catch；`QuotaExceededError`/`SecurityError` → 关闭自动存档、状态行提示一次、世界继续跑（异常不得抛进 `update` 循环）；手动保存可重试；恢复可用后自动存档重开。**必须测试**：`pixel-world/test/save-quota.test.ts` 模拟 `setItem` 抛 `QuotaExceededError` 与坏档写入，断言不崩、自动存档关闭、提示一次、世界仍推进。

### 5. 关闭期间的时间规则（已批准：冻结）

**冻结**：读档 `now = saved.now`，**不做离线快进、不按真实时间推进**；所有 `*Until/startedAt/remainingMs`（`simulation.ts:98-120,155,158`）与 `now` 同一时间域，因此保持一致。理由：按 wall-clock 推进会让角色瞬移、任务瞬完、雨幕/冷却计时错乱；`meta.savedAtWallClock` 只用于诊断，永不喂逻辑。世界从"关页那一刻"继续。

### 6. 明确不做

多存档、云同步、账号/跨设备、加密、离线挂机收益、存档压缩、事件流/日报持久化、回退任意历史快照；**手动导出/导入 → task-22（另立卡）**。

## 实现接口（task-18，按 final ADR）

1. `src/world/save.ts`：`WorldSaveV1`、`CURRENT_SCHEMA_VERSION=1`、`SCHEMA_V1_FINGERPRINT`、`fingerprintOf(paths)`、`assertSaveV1`、`migrateSave`、`MIGRATIONS`（§3）。
2. `Simulation.toSave(): WorldSaveV1`（纯函数；**不要复用 `snapshot()`**——私有 `villagers[].path/pendingRoute/pendingBuilding/各 Until`（`simulation.ts:98-120`）、`activities`/`constructions`/`weather.intensity` 不在快照；`settledLog` 只在存档层）；`static Simulation.fromSave(save, deps): Simulation`。
3. `setup.ts` 增 `rebuildGridFromPlacements(placements): WorldGrid`：新 `WorldGrid(WORLD_SPEC)` 后按存档顺序 `grid.place`（复用 `setup.ts:76-114`），再 `assertWorldInvariants`（`:133-158`）。
4. `src/ui/save-store.ts`：单槽 read/write/clear + 备份轮转（保留 3、配额先删最旧）+ 错误回调 + origin 提示；`snapshot.ledger.reconciled`；`fromSave` 用 `settled` 重建 `deliveryTally`。
5. 指纹/样例测试：`pixel-world/test/save-fingerprint.test.ts`（读 `docs/save-v1.sample.json`，指纹/Equal/变异探针）；`pixel-world/test/save-quota.test.ts`（§4）。
6. 接线：`SandboxScene.create()`（`:99-112`）有档→`fromSave`；`main.ts:11-12` 先重建 grid 再起 Phaser；台账仍走 `connectLedger`（`:119-136`）。

## 风险 / 与现有代码的冲突

1. **网格无快照边界**：`grid.place` 改 cells（`grid.ts:105-121`）但快照不含 grid；只能按 `buildings` 顺序 replay，顺序/门口覆盖必须成为存档不变量，否则读档后 A*/通行与存档前不同。
2. **快照 ≠ 存档**：`Villager` 21 字段仅 10 个进快照（`simulation.ts:98-120` vs `types.ts:226-240`）；`activities`/施工剩余/雨幕浓淡、私有 `settledLog` 都不在快照 → 必须 `toSave()` 显式导出，别在 `WorldSnapshot` 上补字段。
3. **对账不能复用在线 sync**：现有 `sync`（`simulation.ts:324-408`）只对新任务写 status、`complete && 缺席`才撤单；已交付台账单会从 `tasks` 移除、完成数只留聚合（改 `settled` 后解决）→ 需独立 reconciling 分支（覆盖本地 + 缺席即丢 + 不重复计数 + 不复活）。
4. **字段边界易混**：`settled` 是 save-only；若实现者去快照加 `settled`，会改变已冻结运行时 schema——本 ADR 明确禁止。
5. **schema 漂移**：指纹测试 + 样例 + 编译期 Equal 是防"字段改了忘升版本"的唯一机械护栏；跳过它会重复本轮"文档/代码漂移"问题（`ARCHITECTURE_GAPS` C12）。

## 验收断言（5 条，QA 可照抄）

1. **续跑等价**：跑 50 拍→`save=a.toSave()`→`b=Simulation.fromSave(JSON.parse(JSON.stringify(save)), deps2)`→各再跑 50 拍，`JSON.stringify(b.snapshot())===JSON.stringify(a.snapshot())`（比较 `now/villagers/tasks/pending/props/roads/faith/deliveries/deliveriesByVillager/weather.intensity/constructions.remainingMs/activities`；忽略 `events` 与 `ledger.*`）。
2. **去重/分账/不复活**：存档含已结算 `c1`（ledger）与 `t1`（mock）；读档后 `sync({tasks:[],finished:['c1'],complete:true})` → 不重复交付/计数；`sync({tasks:[c1 running]})` 但 `c1` 已 settled → 不复活；`c2` 未结算且 complete=true 缺席 → 丢弃不复活；Mock/ledger 计数分别等于 `settled` 对应 source 条数，`deliveriesByVillager` 按 workerId 一致；`completedAtClock` 与 source 一致、不混算；日报/状态行字符串符合 §2.2。
3. **指纹与样例**：`docs/save-v1.sample.json` 能通过 `assertSaveV1`；其叶子路径哈希 == `SCHEMA_V1_FINGERPRINT`；`meta.schemaFingerprint` 同值；给样例副本加/删一个字段 → 哈希变化、指纹测试红；`WorldSaveV1` 与样例编译期 Equal；`schemaVersion:99` 拒绝加载、原槽不变。
4. **坏档备份（S4）**：制造坏档 → 生成带时间戳备份；连续 4 次坏档 → 只留最近 3 份；模拟配额紧张 → 先删最旧备份再写主存档，主存档成功；不静默重置、原字节可取证。
5. **存储失败与告警（S3/S5）**：模拟 `setItem` 抛 `QuotaExceededError` → 自动存档关闭、提示一次、世界继续、无未捕获异常，恢复后自动存档重开；`settled.length>5000` → 状态行/控制台告警一次；`strictPort:true` 下读不到存档提示 origin/端口可能变了；`npm run check` + `npm test` 全绿（task-21 基线 106/106）。

---
*本 ADR 只写决策，不改代码；task-18 实现须逐条对应 §1–§5 与 5 条断言，并在 `docs/TESTING.md` 记录手工命令；`docs/save-v1.sample.json` 与指纹常量一经冻结不得随代码顺手修改。*
