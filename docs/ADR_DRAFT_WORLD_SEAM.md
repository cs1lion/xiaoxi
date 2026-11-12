# ADR 草案：pixel-world-engine 与小溪的事件接缝契约

> **归档（2026-11-12）**：本 ADR 属于 `pixel-world` 工程。该工程已于 `7c13fd4`
> 移出本仓，由 [`cs1lion/pixel-world-engine`](https://github.com/cs1lion/pixel-world-engine) 维护。
> 本文仅作历史记录保留，**不约束当前仓库**；现行决策见 `docs/PROTOCOL.md`、
> `docs/AGENT_PLAYBOOK.md`、`docs/process/ENGINEERING-DISCIPLINE.md`。


- **状态**：草案，待 Role 0 拍板（本文不执行任何改动）
- **日期**：2026-10-27
- **决策范围**：`cs1lion/xiaoxi` 与 `cs1lion/pixel-world-engine` 两仓之间的接缝契约；不改本仓 WS 事件集，不新增 PROTOCOL.md 字段
- **上游**：用户 2026-10-24 拍板 pixel-world 分家（`docs/DELIVERY_PLAN_20261024.md:10`）；2026-10-25 迁仓落地（`pixel-world/MIGRATED.md`，源提交 `05db039`）
- **关联**：issue #54（迁移交接票）；engine 仓 #5「世界改读事件日志：清掉台账语义」已关闭

## 一句话

世界引擎已按自己的路线改用事件日志（engine #5 已关闭），小溪侧还没有「给世界读的事件日志」这个供产面；本 ADR 草案把接缝契约的三个选项、代价与建议摆清楚，等拍板后施工。

## 背景（实测事实）

分家已发生，且两侧进度不对称：

- 本仓 `pixel-world/` 全量保留但不再演进，只作历史契约查证（`pixel-world/MIGRATED.md:8-9`）；渲染与模拟内核在 engine 仓。
- engine 仓 #5 已于 2026-10-27 前关闭，验收项含「`src/world/` 里不再有 `ledger.ts` / `projection.ts` 的台账语义」「世界改变时写回 `kind: 'world.changed'`」「能从空日志启动、从有内容日志恢复」（`gh issue view 5 --repo cs1lion/pixel-world-engine` 实拉）。即世界侧已经不看小溪的 commission 台账。
- 本仓现有的是 commission 台账加 v2 广播：`server/broadcast/v2.py` 的 `V2BroadcastHub`/`CommissionEventBridge` 把台账变更广播给前端消费（`server/broadcast/v2.py:49/124`），`docs/PROTOCOL.md` §25 登记的是前端消费契约。这是「给 UI 的事件流」，不是「给世界模拟器的可回放事件日志」。

缺口：engine 仓要读的日志，本仓还没有对应的供产面；两仓对「接缝」的想象目前没有共同契约。

## 选项

### 选项一：本仓新增只增不改的世界事件日志（append-only log）

小溪把「世界可能关心的事实」写成一条只增不改的日志（新表或新文件），engine 侧按自己的节奏拉取或订阅。

- 代价：新存储、新保留策略、新对账（谁读过到哪里）；本仓多一个需要维护的真相源。
- 收益：engine 的「从日志恢复」得到真实供产；闭环（`world.changed` 回写）有落点。
- 风险：日志与台账双真相源，漂移时要回答「谁是真相」。

### 选项二：以现有 v2 广播为接缝，本仓不加新存储

把 `server/broadcast/v2.py` 的广播同时开放给 engine（本地 WS 或文件落地由部署决定），engine 侧自行决定保留与回放。

- 代价：v2 事件当前按前端消费形状设计（ envelop 化、按连接 fanout），engine 要用得做适配；回放语义不在本仓保证。
- 收益：本仓零新存储；接缝就是现有契约的延伸。
- 风险：为前端优化的事件形状未必适合模拟器回放；本仓无法控制 engine 看到的历史完整性。

### 选项三：明确「两仓只共享构建产物，不共享运行时可观测状态」

engine 独立运行（自带日志与恢复），小溪只把世界当前状态快照发给它（或干脆各跑各的，`/world` 挂本仓冻结产物）。

- 代价：放弃「世界因小溪的活而变」的闭环；`world.changed` 回写无处落地。
- 收益：零接缝契约、零新存储、两仓彻底解耦；符合「个人助理自用」的最小面。
- 风险：pixel-world 退回纯装饰，与用户「世界被记住」的愿景（`docs/ADR_WORLD_ALIVE.md:17`）相悖。

## 建议

按用户「日常真用、不出差错」的第一优先级，建议**选项三为默认、选项一为愿景**：先承认两仓运行态解耦（世界读 engine 自己的日志，小溪不供产运行态），把「定制化世界生成器」愿景（`docs/ROADMAP.md:5-14`）留在 engine 仓演进；若将来要闭环，再按选项一立 ADR 并新增事件日志存储。理由：选项一在当前使用强度下属于超前投入，双真相源的漂移成本真实存在；选项三零新债。

待拍板的具体问题：

1. 接缝选选项一、二还是三（或组合）。
2. 若选一：日志的真相源归属（台账还是日志）、保留策略、engine 的拉取方式。
3. `/world` 挂载点的产物来源（本仓冻结 dist 还是 engine 构建物流转进来），当前无定义。
4. `docs/ROADMAP.md:3` 的「现行状态指针」修正口径（现指向自述不再是现行依据的 `docs/DELIVERY_PLAN_20261024.md`）。

## 非目标

- 不在本 ADR 中改 `docs/PROTOCOL.md`、不动 WS 事件集、不新增 API 字段。
- 不为 engine 仓的功能排期（其 backlog 见该仓 issue 列表）。
- 不删除本仓 `pixel-world/` 历史存档。
