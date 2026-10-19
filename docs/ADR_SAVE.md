# ADR_SAVE — 世界存档（D-SAVE-1）

> 状态：**proposed · 用户 2026-10-19 原则批准；schemaVersion=1 字段清单待 task-21（多角色 P3：日报归属/手动完成对象）合并 + QA 通过后锁定 → 届时改 final**；task-18 在 field-freeze 后才开工（`DECISION_TRAIL_20261019.md:71-74`、`NEXT_PHASE_PLAN.md:100`）。
> 依据：用户原话「只存世界自有事实；读档先与台账对账；带版本号」；`PROJECT_BRIEF.md:30,43`；D2 accepted（`DECISION_TRAIL_20261019.md:35-39`）。
> 一句话：**单槽 JSON 只序列化世界自有事实；台账是真相源、存档不复制；读档先重建世界、再与台账对账；已结算按 id 去重、Mock/台账/角色分账；高版本/坏档/存储失败一律降级不写坏原档。**

## 决策 D-SAVE-1

单存档槽 + `schemaVersion` + 迁移链 + 读档对账。**字段冻结规则**：§1 为候选清单；task-21 合并、QA 通过后由用户/Chief 确认 §1 即 `schemaVersion=1`，本 ADR 随之 final；冻结后任何字段增删必须升 v2 + 迁移；冻结前 task-18 不得发布对外格式（`DECISION_TRAIL_20261019.md:73`）。

### 1. 存什么（`WorldSaveV1` 候选字段，待冻结）

| 字段 | 源（当前代码；行号以 task-21 合并后为准） | 说明 |
|---|---|---|
| `now` | `simulation.ts:179,224` | 世界逻辑时钟；读档原值续跑（见 §5） |
| `villagers[]` | 内部 `Villager`（`simulation.ts:98-120`），2–3 个 | id/name/mode/pos/facing/visible/path/taskId/bubble+各 Until/routineIndex/routineLabel/inPlaceUntil/pendingRoute/pendingBuildingId |
| `buildings[]`（顺序） | `simulation.ts:146,186` | `PlacedBuilding`；顺序=落位顺序，重建网格用 |
| `roads[]` / `props[]` | `simulation.ts:153-154,278-279` | 铺路与神恩摆件 |
| `tasks[]` | `:148,276`；`types.ts:88-106` | 含 `source`、`workerId`（归属）；台账单=未对账投影 |
| `pending[]`（按角色摊平） | `:175-177,277`；`types.ts:121-136` | 每角色 FIFO 队列 + `villagerId` |
| `activities`（原始态） | `:149`；`types.ts:161-180` | 只存原始 `WorkstationActivity`，不存派生 elapsed/progress |
| `constructions`（remaining/total） | `:155`；`types.ts:194-200` | displayName 派生 |
| `weather`（完整 `WeatherState`） | `:158,281` | kind/intensity/remainingMs，雨幕 TTL 连续 |
| `faith`（points/milestones） | `:159-160,282` | 世界记忆 |
| `settled[]` | 新增，见 §2.1 | **已结算记录（id/source/workerId）——去重与分账的唯一真相** |
| `nextTaskNumber` / `nextPendingNumber` | `:177,180` | 防读档后 id 复用 |
| `meta` | 新增 | `{ savedAtWallClock, savedReconciled }`，仅诊断，不驱动世界 |

**不存**：`WorldGrid` 单元格（按 `buildings` 顺序 replay `grid.place` 重建，`grid.ts:105-121`、`setup.ts:53-73,76-114`）；`events` 环形缓冲与日报（派生，`report.ts:17-71`）；`queue/lastResults`；快照派生值（`workstations.elapsedMs/progress` `:242-254`、`umbrella`、`ConstructionSnapshot.displayName`）；`deliveries`/`deliveriesByVillager`（改由 `settled` 派生，见 §2.2）；`ledger` 链接态（`connected/lastSyncAt/complete/order`，`:165-173`）读档置 `false/null/true/[]`。

### 2. 对账 + 去重 + 分账（用户点名 ①–③）

**2.1 已结算任务 ID 持久化去重**：存档只存 `settled: { id, source:'ledger'|'mock', workerId }[]`，id 全局唯一（`tN` 与 `c_*` 不冲突）；`deliveries` 与 `deliveriesByVillager`（`types.ts:202-214`）读档时由 `settled` 聚合，**不再单存计数**。写档不变量：`settled` id 唯一；`tasks` 中 `status=done` 的 id 必须在 `settled`（否则按坏档处理）。交付入口先查 `settled`：命中→只收尾、不计数、不重复写；未命中→正常交付并 append 一条。与台账 `finished` 的关系：`finished` 里已 `settled` 的 id **不重新交付/不计数**（覆盖"上次已交付、本次快照仍带 done"）；未 settled 且当前活动→交付一次并 append；`complete===true` 且缺席→丢弃、**不复活**（沿用 `simulation.ts` sync 现有撤单语义）。
**2.2 Mock 与台账分开记账**：`source` 落进每条 `settled` 记录——台账单只有台账 `finished` 才记 ledger；沙盒单永远记 mock（离线/在线一致）。对账**不得**把 mock 记录改判 ledger（或反向）；`workerId` 随记录落盘，读档后按人重建 `deliveriesByVillager`（task-21 的归属字段定稿后锁定）。快照 `deliveries` 只是汇总视图，不是可写真相。
**2.3 对账失败显示与重试**：快照 `ledger` 增 `reconciled: boolean`；读档后 `connected=false, reconciled=false`，状态行（`SandboxScene.ts:366-383`）显示「读档未对账 · 台账离线 · 离线 Mock」；`meta.savedReconciled` 记录写档时是否已对账（诊断）。重试不新增逻辑：复用 `ledger.ts:49-58,280` 的 4s 轮询 / 30s REST 对账 / 500ms→5s 退避 / 5s 超时；首个成功 sync 才 `reconciled=true`；`?ledger=off` 永久未对账并在状态行明示。对账前台账单只读展示，不派活/不交付/不撤单。
**2.4 对账裁决**：首个完整快照——id 在 `finished`→（未 settled 才）交付一次；id 在活动→以台账覆盖 status/kind/title，workerId 由世界重新归属；id 缺席且 `complete=false`→保留未对账、不撤单。台账仍只读，不写回。

### 3. 版本、迁移、坏档、高版本（用户点名 ④⑤⑧）

- `schemaVersion` 整数，当前 `CURRENT=1`；加载：`JSON.parse`→形状校验→版本分流→迁移链→`assertSaveV1`→重建世界。`MIGRATIONS: Record<number, (raw) => raw>` 逐级执行，任一步抛错→拒绝加载、原档保留。
- **高版本只读**：`> CURRENT` → 拒绝加载并提示「存档来自更新版本」；**不迁移、不降级、不回写**；原槽字节保留；**暂停自动存档**（防止 v1 覆盖），仅用户显式「覆盖」才写。
- **坏档**：先原样备份到 `...save.v1.corrupt`（单备份，失败则不覆盖原槽），再提示「存档损坏，用新世界启动，原档已保留」；不静默重置、不清空、不自动覆盖。
- 迁移样例 v1→v2（签名 + 断言）：
```ts
type Migration = (raw: Record<string, unknown>) => Record<string, unknown>;
export const MIGRATIONS: Record<number, Migration> = {
  1: (raw) => ({                                  // v1→v2：settled 记录补 completedAt（0=未知，不伪造）
    ...raw, schemaVersion: 2,
    world: { ...(raw.world as object), settled: (((raw.world as { settled?: {id:string;source:string;workerId:string}[] }).settled) ?? [])
      .map((r) => ({ ...r, completedAt: 0 })) },
  }),
};
// 断言：migrateSave({schemaVersion:1, world:{...核心字段, settled:[{id:'c1',source:'ledger',workerId:'v0'}]}})
//   → schemaVersion===2；settled[0].id/source/workerId 不变；completedAt===0；settled.length===1（不重复/不丢）。
```

### 4. 单槽、写入时机、存储失败（用户点名 ⑥）

- **单槽**：`localStorage['xiaoxi.pixel-world.save.v1']`；JSON 文本；不做多槽/云/加密/跨设备。`localStorage.setItem` 单键原子；未来改文件落盘用「临时文件+rename」。
- **写入点**：交付完成、施工落成、神恩里程碑、手动「存档」按钮（`index.html:130-148`）、每 30s 节流、`pagehide` 尽力而为；且**只在 `update()` 返回后**（命令已结算，`simulation.ts:221-239`）序列化，禁止 update 中途/命令队列非空时写。
- **崩溃恢复**：只保证最近一次成功写入；无 WAL/多份轮转。
- **存储失败**：`getItem/setItem` 全部 try/catch；`QuotaExceededError`/`SecurityError`（隐私模式）→ 关闭自动存档、状态行提示一次、世界继续跑（异常不得抛进 `update` 循环）；手动保存可重试，恢复可用后自动存档重新开启。

### 5. 关闭期间的时间规则（用户点名 ⑦）

**冻结**：读档 `now = saved.now`，**不做离线快进、不按真实时间推进**；所有 `*Until/startedAt/remainingMs`（`simulation.ts:98-120,149,155,158`）与 `now` 同一时间域，因此保持一致。理由：按 wall-clock 推进会让角色瞬移、任务瞬完、雨幕/冷却计时错乱；`meta.savedAtWallClock` 只用于诊断，永不喂逻辑。世界从"关页那一刻"继续。

### 6. 明确不做

多存档、云同步、账号/跨设备、加密、离线挂机收益、存档压缩、文件导入导出、事件流/日报持久化、回退任意历史快照。

## 实现接口（task-18，冻结后再写）

1. `src/world/save.ts`：`WorldSaveV1`、`CURRENT_SCHEMA_VERSION`、`assertSaveV1`、`migrateSave`、`MIGRATIONS`（签名见 §3）。
2. `Simulation.toSave(): WorldSaveV1`（纯函数；**不要复用 `snapshot()`**——私有 `villagers[].path/pendingRoute/pendingBuilding/各 Until`（`simulation.ts:98-120`）、`activities`/`constructions`/`weather.intensity` 不在快照里，直接用快照会丢续跑状态）；`static Simulation.fromSave(save, deps): Simulation`。
3. `setup.ts` 增 `rebuildGridFromPlacements(placements): WorldGrid`：新 `WorldGrid(WORLD_SPEC)` 后按存档顺序 `grid.place`（复用 `setup.ts:76-114`），再 `assertWorldInvariants`（`:133-158`）。
4. `src/ui/save-store.ts`（单槽 read/write/clear + 错误回调）；`snapshot.ledger.reconciled`；`settled` 聚合出 `deliveries`/`deliveriesByVillager`。
5. 接线：`SandboxScene.create()`（`:99-112`）有档→`fromSave`；`main.ts:11-12` 先重建 grid 再起 Phaser；台账仍走 `connectLedger`（`:119-136`）。

## 风险 / 与现有代码的冲突

1. **网格无快照边界**：`grid.place` 改 cells（`grid.ts:105-121`）但快照不含 grid；只能按 `buildings` 顺序 replay，顺序/门口覆盖必须成为存档不变量，否则读档后 A*/通行与存档前不同。
2. **快照 ≠ 存档**：`Villager` 21 字段仅 10 个进快照（`simulation.ts:98-120` vs `types.ts:226-240`）；`activities`/施工剩余/雨幕浓淡只在派生里 → 直接用快照当档会跳变，必须 `toSave()` 显式导出。
3. **对账不能复用在线 sync**：现有 `sync`（`simulation.ts:324-408`）只对新任务写 status、`complete && 缺席`才撤单；已交付台账单会从 `tasks` 移除、完成数只留计数（改 `settled` 后解决）→ 需独立 reconciling 分支（覆盖本地 + 缺席即丢 + 不重复计数 + 不复活）。
4. **冻结依赖**：task-21（S2-1b）仍在改 `report.ts`/`completeManually` 与归属字段（`DECISION_TRAIL_20261019.md:73`），§1 的 `settled.workerId`/`deliveriesByVillager` 形状以它合并后为准；task-18 不得并行。

## 验收断言（3 条，QA 可照抄）

1. **续跑等价**：跑 50 拍→`save=a.toSave()`→`b=Simulation.fromSave(JSON.parse(JSON.stringify(save)), deps2)`→各再跑 50 拍，`JSON.stringify(b.snapshot())===JSON.stringify(a.snapshot())`（比较 `now/villagers/tasks/pending/props/roads/faith/settled 派生 deliveries+deliveriesByVillager/weather.intensity/constructions.remainingMs/activities`；忽略 `events` 与 `ledger.*`）。
2. **去重/分账/不复活**：存档含已结算 `c1`（ledger）与 `t1`（mock）；读档后 `sync({tasks:[],finished:['c1'],complete:true})` → 不重复交付/计数；`sync({tasks:[c1 running]})` 但 `c1` 已 settled → 不复活；第二个存档 `c2` 未结算且 complete=true 缺席 → 丢弃不复活；Mock 与 ledger 计数分别等于 `settled` 中对应 source 条数，`deliveriesByVillager` 按 workerId 一致。
3. **版本/坏档/存储失败**：`schemaVersion:99` → 拒绝加载、提示、原槽字节不变、自动存档暂停；坏 JSON/缺字段/引用不存在 → 先备份 `.corrupt` 再提示、不静默重置；`setItem` 抛 QuotaExceeded（模拟）→ 世界继续、无未捕获异常；`npm run check` + `npm test` 全绿（task-21 合并后基线）。

---
*本 ADR 只写决策，不改代码；task-18 实现须逐条对应 §1–§5 与 3 条断言，并在 `docs/TESTING.md` 记录手工命令。*
