# TASK_COMPLETION_MATRIX.md

> **⚠️ 过期快照（2026-10-24 回填标注）**：本文审计基线是 2026-10-19 的 HEAD `9cd0c5a`。其后文中多条 MISSING / PARTIAL / P0 结论已被 master 修复（多角色 villagers、JSON 存档、调试面板、adapters strengths、ledger 心跳等；逐项对账未做）。**现状以代码与门禁命令输出为准**，本文只作历史存档；现行交付状态见 `docs/DELIVERY_PLAN_20261024.md`。

> 只读代码审计（task-3）。审计对象：`pixel-agent-chief-starter/docs/PROJECT_PROGRESS.md` 的进度声称、`PROJECT_BRIEF.md` 的 MVP 1.0 九项要求，以及 `PIXEL_WORLD_ROUND1/2/3` 的完成声明，对照 `pixel-world/src`、`pixel-world/test`、`pixel-world/index.html`、`pixel-world/scripts/smoke.mjs` 与主仓库 `server/`。
>
> - 路径基准：`D:\project\neurosama\`，下表证据均为该根目录下的相对路径 + 行号。
> - 仓库状态：分支 `feat/pixel-world-oracle-world`，`git status --porcelain` 50 项未提交改动（只读，未改任何源码/配置/测试/既有文档）。
> - 审计方式：读代码与文档、正则清点测试、grep 关键能力；**未启动服务、未重复运行** `npm run check` / smoke（引用已实测结论：check 通过、smoke 49/49）；`node --test` 聚合在本机因 spawn EPERM 不可用，逐文件可跑。
> - 本次唯一产出即本文件；未修改其它任何文件。

## 0. 结论速览

| 维度 | 结论 |
| --- | --- |
| 阶段/任务卡声称 | **严重滞后**：文档停在 Phase 1 IN_PROGRESS / TASK-1.2 待做，代码实际已完成 Phase 0–6 的 19 张任务卡（17 张 DONE + 2 张 PARTIAL）。 |
| MVP 1.0 必须包含（9 项） | **4 DONE / 3 PARTIAL / 2 MISSING**；缺的是「2–3 个小人」「JSON 存档单槽」，另「生活工坊」「高质量 Mock 神谕」「调试面板」只到部分实现。 |
| 文档与代码事实 | 至少 8 处不一致：README 内部 59/45 自相矛盾、smoke 49/50 口径不一、main.ts 注释 480×396 错误、PHASE 文档「六态」已变七态、架构笔记「不引引擎」与 Phaser 实现相反、`PROJECT_PROGRESS` 声称「待核验」但代码已实现、`REPO_RECON.md` 未产出。 |
| 最大差距 | 广度缺口：MVP 1.0 的两条硬要求（多角色、存档）完全没做；深度上已超出 BRIEF（接真实台账），但不能替代验收目标 4。 |
| 阻塞项 | 无审计阻塞。真实台账（8000）与浏览器冒烟属 task-2/QA 口径，本次只做静态核验；`node --test` 聚合受环境限制。 |

状态图例：**DONE** = 有可定位实现且与要求一致；**PARTIAL** = 有实现但范围/质量/可达性不足；**MISSING** = 代码中无对应物；**未验证** = 需运行证据才能判定。

---

## 1. 阶段与任务卡：声称 vs 代码事实

| 任务/要求 | 声称状态 | 审计结论 | 证据（文件:行） | 缺口与影响 |
| --- | --- | --- | --- | --- |
| Phase 0 / MVP 0.1「已完成」 | 文档记录 DONE（`PROJECT_PROGRESS.md:7`；`source/01:681-682,691-693`；`source/02:989-990,1001-1003`） | **DONE**（功能等价物存在；原始单文件产物不在仓库） | `pixel-world/src/world/simulation.ts:414-464`（dispatch）、`:663-693`（行礼→进铺子→开工）、`:729-752`（交付）；`ROUND1:7` 明确开工时仓库只有 `types.ts`+`grid.ts` 空壳 | 单文件 `main.ts` MVP 0.1 已不存在（现入口 `pixel-world/src/main.ts:1-43` 是 Phaser 工程入口）；「已完成」只能按重构后的等价闭环认定，无法逐行对照旧产物。 |
| Phase 1「IN_PROGRESS」 | 文档记录 IN_PROGRESS（`source/01:682`；`source/02:990`；`PROJECT_PROGRESS.md:8`） | **DONE / 声称过时（低估）** | 1.1–1.4 均有实现：`types.ts:1-221`；`grid.ts:41-286`；`blueprints.ts:29-111`+`prefabs.json:1-58`；`setup.ts:53-158`+`simulation.ts:767-778`。`ROUND2:9-13` 自述 1.4 完成 | 进度文档没有随 `ROUND2/ROUND3` 更新；当前实际已越过 Phase 6。风险：外部读者会按 IN_PROGRESS 重复排期。 |
| TASK-1.1 工程目录重构与数据类型 | 文档记录 IN_PROGRESS（`source/01:696`；`source/02:1009-1012`） | **DONE（路径与文档目标结构不同）** | 类型集中定义：`types.ts:3-221`（`GridPoint/Vec2/Facing/ZoneId/TerrainId/TileCell/PrefabBlueprint/TaskKind/...`）；目录 `src/world` `src/render` `src/scenes` `src/ui` `src/audio`（`README.md:44-68`）；严格编译配置 `tsconfig.json:10`（`strict:true`），`npm run check` 已实测通过 | 文档目标结构是 `src/core/grid/`、`src/core/creator/`、`src/types/`（`source/02:1010`、`:1085-1086`），实际用 `src/world/` 单层；无 `src/types/`。意图满足，命名不满足；后续若按旧工单找文件会找不到。 |
| TASK-1.2 WorldGrid 与 findBestSlot | 文档记录「下一项/PENDING」（`PROJECT_PROGRESS.md:9`；`source/01:697`；`source/02:1013-1016`） | **DONE（API 形状与文档不同；缺直接单测）** | `grid.ts:41-66`（`WorldGrid`、Zone/occupant/walkable）；`:104-131`（`place`、`canPlace`+margin）；`:134-156`（`findBestSlot(prefab, anchor)`）；调用点 `setup.ts:82` | 文档签名是 `findBestSlot(footprint, zone, margin)`（`source/02:1016`），实现改为 `findBestSlot(prefab, anchor)`（zone/margin 从蓝图取）。**`grep findBestSlot` 全仓仅 `grid.ts:134` 与 `setup.ts:82` 两处，`test/grid.test.ts` 没有直接用例**，只被世界构建/扩建场景间接覆盖（`test/world.test.ts:104-109,119-144`）。 |
| TASK-1.3 PrefabRegistry + prefabs.json | 文档记录 PENDING（`source/01:698`；`source/02:1017-1020`） | **DONE（未被声称，实际已做）** | `pixel-world/src/world/prefabs.json:1-58`（4 张蓝图：code_forge/star_archive/ink_house/workshop_plot）；`blueprints.ts:29-111`（边界解析+覆盖校验）；`README.md:56,62` | 无独立 `PrefabRegistry` 类，改为 `ReadonlyMap` + 解析函数；接口达标。属「文档低估」。 |
| TASK-1.4 造物主生成/自动铺路/施工 | 文档记录 PENDING（`source/01:699`；`source/02:1021-1024`） | **DONE（未被声称，实际已做）** | `setup.ts:76-114`（`placeBlueprint`：选址+占格+建实例）、`:117-130`（`paveFrom` 自动铺路）；`simulation.ts:515-537`（build 命令）、`:767-778`（施工倒计时）；渲染脚手架 `SandboxScene.ts:320-353`；测试 `test/world.test.ts:119-144,327-345` | 「LLM 生成侧」按 `ROUND2:35-36` 有意不做，属 BRIEF 允许的后续项；施工/铺路已完备。 |
| Phase 2（2.1 协议 / 2.2 桥接） | 文档记录 PENDING（`source/01:701-703`；`source/02:1026-1035`） | **DONE（且已超出：接真实台账）** | `bridge.ts:13-33`（双向消息联合类型）、`:35-74`（Bridge + toWorldCommand）；`SandboxScene.ts:213-217`（feed 入同一队列）；`ledger.ts:37-186`（REST 全量 + WS 增量 + 轮询兜底） | 运输层没走 WS 到游戏直连，而是 Vite 代理到主服务（`vite.config.ts:13-16`）；协议文档见 `docs/PROTOCOL.md:348-349`。 |
| Phase 3（3.1 A*/Y-Sort、3.2 日常、3.3 高优打断） | 文档记录 PENDING（`source/01:705-708`） | **PARTIAL** | A*：`grid.ts:168-214`；Y-Sort：`buildings.ts:144`、`villager.ts:65`、`decor.ts:60`；日常：`routines.ts:21-78` + `simulation.ts:610-638`；打断：`simulation.ts:414-419,467-482` | 3.3「行为栈/打断恢复」没有实现为栈：她手上有活时**新神谕直接拒绝**（`simulation.ts:414-419`），不会排队或抢占；「恢复」只靠状态机自然回 idle。与文档「高优打断机制（行为栈）」有差距。 |
| Phase 4（4.1 四态、4.2 长短分级） | 文档记录 PENDING（`source/01:710-712`） | **PARTIAL** | 四态类型 `types.ts:123-142`；初始化/运转/冷却 `simulation.ts:157-159,684-691,723-740`；渲染 `buildings.ts:131-169`；长短分级 `tasks.ts:9-25` + `simulation.ts:420-433` | `overheating` 永不触发：`tasks.ts:27-30` 的 `shouldOverheat()` 恒 false，`simulation.ts:702-727` 无进入分支付。这是`ROUND2:32-34` 的有意取舍，但「四态」只能算三态可达。 |
| Phase 5（5.1 天气、5.2 躲雨） | 文档记录 PENDING（`source/01:714-716`） | **DONE** | `weather.ts:20-46`（渐变/自动退场）；`simulation.ts:384-412`（setWeather + scatterToShelter）；渲染 `render/weather.ts:17-69`、伞 `villager.ts:71,82-90`；测试 `test/world.test.ts:277-307` | 只做晴/雨，无雾/雪/昼夜（BRIEF 本来就只要求晴雨，达标）。 |
| Phase 6（6.1 日报、6.2 拟音、6.3 信仰） | 文档记录 PENDING（`source/01:718-721`） | **DONE** | 日报 `report.ts:17-58` + DOM `oracle-panel.ts:61-72` + `index.html:150-157`；拟音 `audio/animalese.ts:17-111`；信仰 `faith.ts:10-54` + `simulation.ts:754-765`；测试 `test/surface.test.ts:35-142`、`test/world.test.ts:311-323` | 神谕石板（最近 10 条神谕+回执，`PIXEL_WORLD_ARCHITECTURE.md:121`）没有实现；日报是「即时快照」而非「每日归档」。 |
| `PROJECT_PROGRESS.md` 首项交付：`REPO_RECON.md` | 「输出 docs/REPO_RECON.md」（`PROJECT_PROGRESS.md:21`；`README_CHIEF_START.md:50`） | **MISSING** | `Test-Path docs/REPO_RECON.md` → False；`Test-Path docs/DECISIONS.md` → False；`PROJECT_PROGRESS.md:14-21` 全部未勾选 | 文档自称「尚未核验、待 Chief 检查」，但仓库里既没有核验报告，也没有对应更新；进度文档与实际代码脱节。 |

---

## 2. MVP 1.0「必须包含」九项

要求原文：`pixel-agent-chief-starter/docs/PROJECT_BRIEF.md:22-32`。

| # | 任务/要求 | 声称状态 | 审计结论 | 证据（文件:行） | 缺口与影响 |
| --- | --- | --- | --- | --- | --- |
| 1 | 一张固定预制地图 | BRIEF 要求 | **DONE** | `setup.ts:12-33`（30×20、16px、7 分区、BORDER=1）；`prefabs.json:1-58`（4 张蓝图）；`blueprints.ts:94-111`（每种活恰好一栋校验）；`assertWorldInvariants` `setup.ts:133-158` | 地图固定为 30×20；无外部 Tiled 底图，靠 `tilemap.ts` 程序绘制（可接受，但「固定预制」表现为代码规格+JSON 蓝图，而非美术底图资产）。 |
| 2 | 2–3 个小人 | BRIEF 要求；README 自认只有 1 个 | **MISSING** | 单数模型：`types.ts:174-186`（`VillagerSnapshot`）、`:188-204`（`WorldSnapshot.villager` 单数）；`simulation.ts:108,141-156`（单个 `villager` 字段）；`main.ts:36-40`（只传一个 `villagerName`）；`README.md:107`「只有一个她」 | 只有莞尔 1 人，7 个 Agent 的活全归她。直接影响 BRIEF 第 2 条与验收目标 1 的「3 秒指出主角/正在工作的工坊」在繁忙时的可读性。工作量：角色数组 + 每角色状态机 + 渲染多实例 + 冲突/排队语义。 |
| 3 | coding、research、生活三个工坊；每个 Idle/Operating | BRIEF 要求 | **PARTIAL** | 三栋接活铺子 `prefabs.json:2-43`（炼码=code、星图=research、抄书=writing）；Idle/Operating `simulation.ts:157-159,684-691`；渲染四态 `buildings.ts:131-169` | 第三间是 `writing`（抄书斋，落在 `agriculture` 区），不是「生活」类；`TaskKind` 只有 code/research/writing/quick（`types.ts:35`），没有生活任务；`living` 区只放不接活建筑（`blueprints.ts:24-25`）。若「生活」= 生活/文化工坊，则语义未对齐；若产品认可 writing 即生活，应在 BRIEF 改口径。 |
| 4 | 高质量 Mock 驱动的神谕路由；LLM 可选、可离线降级 | BRIEF 要求 | **PARTIAL** | 离线路由：`oracle.ts` 的 `parseOracle`（关键词规则→dispatch/complete/weather/unknown）；在线路径：`bridge.ts:39-74` + `ledger.ts:37-186`（真实台账，不是 LLM）；无 LLM 适配器/无 Mock LLM 层（全仓无 openai/anthropic/llm 相关调用） | 核心流程可离线跑（DONE），但「Mock 驱动」只是关键词表，不是可注入的 Mock 神谕/AI 输出；「LLM 可选」完全缺失（无 schema 校验后的 LLM 意图解析、无气泡文本生成）。真实台账接入是比 Mock 更进一步的集成，但不能替代该要求。 |
| 5 | 晴/雨切换 + 户外角色躲雨 | BRIEF 要求 | **DONE** | `weather.ts:20-46`；`simulation.ts:384-412`（下雨瞬间 idle→sheltering、最近屋檐 `routines.ts:47-61`）；TTL 45s `weather.ts:18`；视觉 `render/weather.ts:17-69`、伞 `villager.ts:82-90`；测试 `test/world.test.ts:277-307` | 只对 idle 状态的人做躲雨（`simulation.ts:400-401`：`if (mode !== 'idle') return`）；干活中不躲，符合「她不知道下雨」的设定但需在验收文档写清。 |
| 6 | 模板式 DOM 日报 | BRIEF 要求 | **DONE** | `report.ts:17-58`（从快照推导 headline/lines/date，最多 3 条）；DOM 模板 `index.html:150-157`；填充 `oracle-panel.ts:61-72`；测试 `test/surface.test.ts:35-92` | 是「模板式 DOM」达标；但不是「每日归档」：刷新即重置，没有按天持久化（与存档缺口同源）。 |
| 7 | JSON 存档，单存档槽 | BRIEF 要求；ROUND2 自认未做 | **MISSING** | grep `localStorage\|sessionStorage\|indexedDB\|serialize\|存档\|保存\|restore` 于 `pixel-world/`（ts/mjs/html/json）→ **0 命中**；`main.ts:11-12` 每次启动重新 `buildWorld`；`Simulation` 状态全为 private（`simulation.ts:102-131`），无 `toJSON/fromSnapshot` | 完全没有存档/恢复。影响 BRIEF 验收目标 4（关闭重开恢复世界/编年史/资源账本）。不只是 UI：需要为 `Simulation` 增加可序列化快照与恢复构造（tasks/props/roads/faith/weather/constructions/ledger 状态）。 |
| 8 | 简单上帝调试面板：实体列表与事件流 | BRIEF 要求 | **PARTIAL** | 数据已具备：`WorldEvent` `types.ts:217-221`；事件环形缓冲 `simulation.ts:111,800-803`；快照暴露 `events: this.events.slice(-12)` `simulation.ts:230`；`window.Oracle.view()` `SandboxScene.ts:227-229,250`；`pushLedger()` `SandboxScene.ts:219-225` | 没有可视化面板：`index.html` 无 `#debug`/面板节点，`grep debugPanel/#debug` 0 命中。实体列表=snapshot.buildings/workstations/tasks，事件流=snapshot.events，但只能在 devtools 里手动读；smoke 也是靠 `window.Oracle.view()` 断言，扮演了部分调试角色。 |
| 9 | 一个文本「用户下指令」入口 | BRIEF 要求 | **DONE** | `index.html:130-148`（输入框+提交+预设按钮+世界法则按钮）；`oracle-panel.ts:98-136`（统一走 `Oracle.say`）；`SandboxScene.ts:184-198`（say→parseOracle→enqueue） | 入口达标，且按钮与打字同一通道。无语音输入（BRIEF 明确 MVP 不含，符合）。 |

### 2.1 附加对照：BRIEF 样品验收目标（`PROJECT_BRIEF.md:38-44`）

| 验收目标 | 审计结论 | 证据/缺口 |
| --- | --- | --- |
| 1. 原生分辨率 3 秒内指出主角、神谕状态、正在工作的工坊 | **未验证** | 无计时/可读性测试；仅 smoke 截图 `eval/screens/`（smoke.mjs:138,149,158,206,220,231）。可读性属人工验收。 |
| 2. 输入研究任务→礼仪响应→走到工坊→动画/进度→日报，无卡死 | **DONE（逻辑+浏览器冒烟）** | `simulation.ts:467-482,663-693`；smoke `scripts/smoke.mjs:127-161`；单测 `test/world.test.ts:197-234` |
| 3. 下雨→雨幕→户外躲避→TTL 恢复 | **DONE** | `simulation.ts:384-412`；`weather.ts:18,20-46`；单测 `test/world.test.ts:277-300`；视觉仅冒烟截图，像素级未验证 |
| 4. 关闭重开后世界/编年史/资源账本恢复 | **MISSING** | 同 MVP #7，无存档机制 |
| 5. LLM 离线时 Mock 仍跑通 | **PARTIAL** | 离线沙盒可跑（`?ledger=off`，`SandboxScene.ts:118-120`；smoke 默认模式），但没有「Mock LLM」层；准确说是「关键词路由可离线跑通」，不是「Mock 神谕可降级」。 |

---

## 3. 文档声称 vs 代码事实：差距清单

| # | 文档声称 | 代码/仓库事实 | 证据 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | README 同一文件写「59 项」（`README.md:25`）又写「test/ 45 项」（`README.md:67`） | 实测清点：`test()` 用例 **59** 个，`assert.*` 断言 **147** 处。明细：`grid.test.ts` 7 用例/20 断言、`ledger.test.ts` 14/32、`surface.test.ts` 13/23、`world.test.ts` 25/72 | 命令：对 `test/*.test.ts` 正则计数；文件见 `pixel-world/test/` | `README.md:67` 是过期数字；`README.md:25` 正确。另外 `ROUND2:54` 的「45 项」在其提交时点成立，`ROUND3:67` 的「59/59」与当前一致。 |
| 2 | smoke「49 项」：`README.md:34`、`ROUND3:68`；但 `ROUND2:56` 写「50 项」 | 静态清点当前 `smoke.mjs`：沙盒路径共 **50** 个标签（26 个 `check()` + 18 处 `waitFor`，其中 2 处在 2 次循环内），台账路径 **11** 个；QA 实测沙盒 **49/49** | `scripts/smoke.mjs:120-252`（沙盒断言）、`:62-107`（台账断言）、`:262-276`（全局 4 条） | 文档计数与运行输出差 1，说明断言增删后未同步；建议文档不写死数字或由脚本输出生成。 |
| 3 | `main.ts` 注释「逻辑分辨率保持 480×396」 | 实际 30×20 格 ×16px = **480×320**（30×16=480，20×16=320） | `pixel-world/src/main.ts:25` vs `setup.ts:12-14,19-20`；`README.md:93` 写的也是 480×320 | 注释错误会误导后续做素材/缩放的人；属低成本修正。 |
| 4 | 架构笔记「v1 Canvas 2D、不引成熟引擎、480×300/30×19」 | 实现是 **Phaser 3.90 + Vite**，480×320/30×20 | `docs/PIXEL_WORLD_ARCHITECTURE.md:45-50,47` vs `pixel-world/src/main.ts:3,17-33`、`setup.ts:12-14`；`PROJECT_BRIEF.md:10` 已确认为 Phaser | 决策层面 BRIEF 优先（`README_CHIEF_START.md:23-29`），但架构笔记未标注「已被 Phaser 取代」，读者会看到相反结论。 |
| 5 | `PROJECT_PROGRESS.md:3,7-10`：「状态从设计文档迁入，尚未由当前仓库核验」「待 Chief 检查」 | 代码已实现 Phase 0–6 的主体，并有 `ROUND1/2/3` 三轮验收记录 | `PROJECT_PROGRESS.md:3-10,14-21`；`ROUND2:9-25`；`ROUND3:62-78` | 进度文档停留在「待核验」，首项交付 `REPO_RECON.md` 也不存在（`Test-Path` False）；对外汇报会低估完成度。 |
| 6 | `ROUND1:22`：「`VillagerMode` 六态」 | 现为 **七态**（新增 `sheltering`） | `types.ts:74-81`；对比 `ROUND1:22` | 历史文档可解释（雨天态是 ROUND2 加的），但当前读者会数错；建议在 ROUND1 加「后续已增 sheltering」。 |
| 7 | README 结尾「这一版没有做的」列了昼夜、真实失败、LLM 生成、多角色、回写 | 与代码一致；其中「多角色」「真实工具失败」确实缺 | `README.md:101-108`；`tasks.ts:27-30`；`main.ts:36-40` | 这是**诚实的自述**；与 BRIEF 的 MVP 要求冲突处（多角色）应在进度文档显式标红。 |
| 8 | 「`src/world/` 可被 `node --test` 直接加载」（`README.md:47`） | 纯逻辑层确实不 import Phaser（grep `import Phaser` 只出现在 `main.ts`、`scenes/`、`render/`），但本机 `node --test` 聚合因 spawn EPERM 不可用；逐文件 `node test/xxx.test.ts` 可跑 | grep `import Phaser`：`src/main.ts:3`、`src/scenes/SandboxScene.ts:6`、`src/render/*.ts` 共 8 处，`src/world/*` 0 处；共享实测事实 | 代码约束成立；文档的命令在当前环境需要改写为逐文件运行，属环境注记。 |
| 9 | 文档中「未改代码」类表述（`docs/PIXEL_WORLD_ARCHITECTURE.md:3`） | 该文档是 10-18 的架构建议（结论「不引引擎」），而同日/次日已有 Phaser 实现落地 | `docs/PIXEL_WORLD_ARCHITECTURE.md:3,43-50`；`docs/PIXEL_WORLD_ROUND1_20261018.md:3,7-8`；git log `b280ed6` | 若把「未改代码」当现状，会误判像素世界未实现；应加「本文为提案，实现见 pixel-world」的横幅。 |

补充：`README.md` 与 `ROUND3` 均如实写了「只有一个她」「存档未做」（`README.md:107`、`ROUND2:70`），这两条与代码一致，不计为不一致；问题在于 `PROJECT_BRIEF` 仍把它们列为 MVP 1.0 必须项。

---

## 4. ROUND1/2/3 完成声明抽查（7 条）

| 抽查项 | 声称 | 核验结果 | 证据 |
| --- | --- | --- | --- |
| R1 类型模型 | `types.ts` 有 `VillagerMode` 六态、`WorkstationActivity` 四态、`OracleIntent` 三态 | **部分过时**：四态、三态正确；`VillagerMode` 已是七态 | `types.ts:74-81,123-142,207-210`；`ROUND1:22` |
| R2 TASK-1.4 | 「本轮完成」，落在 `setup.ts:placeBlueprint`、`paveFrom`、`simulation.ts:stepConstructions` | **真实** | `setup.ts:76-130`；`simulation.ts:767-778`；`ROUND2:13` |
| R2 扩建样板 | 「只给 `RESIDENT_PREFABS` 开局落位，扩建样板只在 build 命令出现」 | **真实** | `setup.ts:35-36,62-63`；`simulation.ts:515-537`；测试 `test/world.test.ts:68-72,327-345` |
| R2 故障态 | 「四态齐全，`shouldOverheat()` 恒 false，接台账后由真实工具驱动」 | **真实（且是刻意取舍）**：无任何代码把 activity 置为 `overheating` | `tasks.ts:27-30`；`simulation.ts:702-727`（只有 operating→finish / cooling→idle） |
| R3 台账不撒谎 | 「台账的活一律 `determinate:false`，不画百分比；完成权在台账」 | **真实**：唯一置 `determinate` 处按 source 区分，单测断言 `progress === null` | `simulation.ts:684-691`（`determinate: task.source === 'sandbox'`）；`test/ledger.test.ts:141-144`；`simulation.ts:266-326`（sync 完成/撤单） |
| R3 服务端推送 | 「`commission_update` 广播 + 无连接静默丢弃 + 线程安全转换」 | **真实**（server 侧已落地） | `server/app.py:964-980`（`_broadcast_commission`）；`docs/PROTOCOL.md:348-349`；`scripts/check_protocol.py:277`；`pixel-world/src/world/ledger.ts:106-135`（客户端接收） |
| R3 代理 | 「Vite 代理 `/api`、`/ws` 到 8000，避免 CORS」 | **真实** | `pixel-world/vite.config.ts:7-16` |

抽查结论：ROUND2/3 的工程声明经代码核对基本可信；主要偏差集中在「文档状态/计数未同步」和「ROUND1 六态」这类历史文本，不是虚构完成。

---

## 5. 存档 / 调试面板：grep 与实现核验

| 能力 | grep 结果 | 实现情况 | 结论 |
| --- | --- | --- | --- |
| 存档/读档 | `localStorage\|sessionStorage\|indexedDB\|serialize\|deserialize\|存档\|保存\|restore` 在 `pixel-world/**/*.{ts,mjs,html,json}` **0 命中** | 无 save/load 按钮、无快照落盘、`Simulation` 状态 private 且无恢复构造 | **MISSING** |
| 唯一 JSON 解析点 | `pixel-world/src/world/ledger.ts:112-118`（WS 消息 `JSON.parse`）、`test/world.test.ts:19`、`test/surface.test.ts:12`、`test/ledger.test.ts:15`（读 prefabs.json） | 与存档无关 | 不构成存档 |
| 调试面板 | `debugPanel\|#debug\|debugger` **0 命中**；`index.html:120-167` 只有 header/画布/状态行/神谕表单/报纸/footer | 无面板节点、无实体列表/事件流 UI | **MISSING（UI）**；数据侧可读 |
| 已有「准调试」能力 | `window.Oracle.view()` `SandboxScene.ts:227-229,250`；`window.Oracle.pushLedger()` `:219-225`；事件流 `simulation.ts:230`（最近 12 条） | 可在 devtools/smoke 中读快照与事件 | **PARTIAL** 的基础；补面板成本低（数据已全在 `WorldSnapshot`） |

影响：BRIEF 验收目标 4「关闭重开后恢复」当前无法通过；调试面板缺失使「世界不撒谎」的自证只能靠测试与 devtools。

---

## 6. 测试覆盖：单测行为 vs 只有 UI/未覆盖的行为

**单测（`test/*.test.ts`，59 用例 / 147 断言）实际覆盖：**

- 蓝图解析与拒绝（`test/world.test.ts:68-100`）、世界落位/不变量/扩建（`:104-144`）。
- 网格占用/留白/A*（`test/grid.test.ts:12-86`）。
- 神谕关键词收敛（`test/world.test.ts:148-169`）、日常作息（`:173-193`）、神谕→工作→交付→回祭坛（`:197-234`）、短活/拒单/队列时序（`:236-273`）、天气与躲雨状态（`:277-307`）、信仰里程碑（`:311-323`）、造物主扩建（`:327-345`）。
- 日报/日记纯函数、拟音音节表、桥接形状（`test/surface.test.ts:35-142`）。
- 台账投影/排序/坏数据拒绝/同步派活/收工/撤单/离线（`test/ledger.test.ts:53-215`）。

**只有浏览器 smoke（或只在画面/DOM 上）验证的行为：**

- DOM 报纸渲染（`oracle-panel.ts:61-72`；smoke `scripts/smoke.mjs:233-238`）。
- 输入框/按钮/交付按钮/天气按钮的绑定（`oracle-panel.ts:108-136`；smoke `:128-135,164-182,192-204`）。
- 雨幕、压暗、伞的视觉（`render/weather.ts:17-69`、`villager.ts:82-90`）；smoke 只断言快照 `umbrella`/`mode`（`:202-204`），像素效果未验证。
- 建筑状态视觉与进度条/烟（`buildings.ts:131-169`、`smoke.ts` 粒子）；无单测。
- 状态行中文文案（`SandboxScene.ts:355-372`）；smoke 只在台账模式断言含「台账在线/离线」（`:66-70`）。
- `feed()` 外部 Agent 入队（`smoke.mjs:241-252`）；桥接类本身有单测，但「场景里真的生效」只有 smoke。
- 台账运输层（`ledger.ts`）：**没有 LedgerClient 单测**，只有 projection+simulation 单测；REST/WS/重连逻辑靠 `LEDGER=1` smoke（`:62-107`，且推送用注入帧模拟）。

**完全未覆盖：** 存档/恢复（不存在）、调试面板（不存在）、多角色（不存在）、Web Audio 实际播放（`createAnimalese` 无测试，只有 `syllablePlan` 纯函数）、移动端/缩放、断网重连。

---

## 7. 真实完成度摘要

1. **阶段看：远超文档声称。** 文档停在「Phase 1 IN_PROGRESS / TASK-1.2 下一项」，代码实际把文档 19 张任务卡推进到 **17 DONE + 2 PARTIAL**（PARTIAL 是 3.3 无行为栈、4.1 的 overheating 不可达）；其中 Phase 1 的 1.3/1.4 是在文档里仍标 PENDING 时就已经做完的。
2. **MVP 1.0 看：广度不达标。** 九项硬要求 = **4 DONE / 3 PARTIAL / 2 MISSING**。DONE：固定地图、晴雨+躲雨、模板 DOM 日报、文本指令入口；PARTIAL：三工坊（第三间是 writing 不是「生活」）、Mock 神谕（只有关键词路由，无 LLM/Mock 层）、调试面板（数据有、UI 无）；MISSING：2–3 个小人、JSON 存档单槽。
3. **深度看：已超出 BRIEF 的 Mock-first 阶段。** 真实台账已接通（`ledger.ts`/`projection.ts`/`simulation.ts:sync` + 服务端广播），并守住了「不编进度、完成权在台账、台账是唯一真相、映射只在一处」四条纪律；这是三轮里最有价值的部分，但不能替代 BRIEF 的验收目标 4（存档）与多角色。
4. **文档可信度看：工程报告可信，进度文件失真。** ROUND2/3 的完成声明经抽查 6 条全部成立；失真的是 `PROJECT_PROGRESS.md`（未核验、未更新、首项交付缺失）、`README` 内部计数、`main.ts` 注释、架构笔记与实现的路线冲突。
5. **质量看：测试结构健康。** 59 个纯逻辑用例 / 147 条断言分层清晰，覆盖了状态机、投影、台账边界与拒绝路径；弱在「传输层与视觉层只有 smoke」「计数写在文档里会漂」「没有存档/多角色所以对应测试自然缺失」。
6. **一句话：** 一个「单角色 + 接真实台账 + 无存档/无调试面板」的像素世界运行时，功能上已越过 Phase 1 到 Phase 6，但离 PROJECT_BRIEF 定义的 MVP 1.0 仍差两个硬项（多角色、存档）和三个半项（生活工坊语义、Mock/LLM 神谕层、调试面板 UI）。

---

## 8. 下一阶段最该先补的 5 项（按价值/依赖排序）

1. **多角色 2–3 小人（MVP 硬缺口，工作量最大）。** 把 `WorldSnapshot.villager` 单数改为角色数组，`Simulation` 内的单个 `villager` 字段拆为按 id 管理的集合，渲染 `createVillager/updateVillager` 多实例，`sandbox/delivery/faith` 归属到具体角色。验收：smoke 能断言「2 个角色各自有独立 routine」「一个在工坊、一个在祭坛」。
2. **JSON 存档单槽 + 恢复（验收目标 4，依赖 #1 的数据模型定型）。** 给 `Simulation` 增加 `toSnapshot()/restore()`（tasks/props/roads/faith/weather/constructions/ledger 状态 + 角色位置/状态），加一个 `#save/#load` 或自动槽；用现有 `WorldSnapshot` 做序列化基底（它已是纯数据，`types.ts:188-204`）。验收：刷新页面后建筑/道路/信仰/任务/角色位置一致。
3. **上帝调试面板（成本最低、验收收益高）。** 基于已有 `window.Oracle.view()` 与 `snapshot.events`（`SandboxScene.ts:250`、`simulation.ts:230`）在 DOM 加一个折叠面板：实体列表（buildings/workstations/tasks）、事件流（tone+time+text）、按钮调用 `pushLedger/feed`。不改逻辑层即可满足 MVP #8。
4. **高质量 Mock 神谕层 + LLM 可选适配器。** 在 `oracle.ts` 外包一层 `OracleRouter`：Mock 档走可配置规则/样例表，LLM 档只产出 `{kind,taskKind,summary,weather}` 并过 schema 校验，失败回退 Mock；同时补「神谕石板」最近 10 条神谕+回执（`PIXEL_WORLD_ARCHITECTURE.md:121`）。验收：断网时 smoke 全绿；注入坏 JSON 时世界不动且回执可读。
5. **文档对账与验收脚本化。** 更新 `PROJECT_PROGRESS.md`（写入本轮真实状态与证据）、补交 `docs/REPO_RECON.md`、把 `README.md:25/34/67` 的 59/49/45 统一（用运行时输出生成计数）、修 `main.ts:25` 的 480×396、给架构笔记加「已被 Phaser 实现取代」横幅、把 `ROUND1:22` 的「六态」改为七态。验收：文档中每个数字都能由命令复现。

---

## 9. 审计限制与未验证项

- 未启动 `pixel-world` 服务，未重复跑 smoke/LEDGER 模式；浏览器行为结论基于 `scripts/smoke.mjs` 代码与已实测事实，真实像素效果以 QA/task-2 为准。
- `npm run check` / `npm run build` 未由本次重跑（共享事实：check 通过）；本审计只静态核对 `tsconfig.json:10` 与类型使用。
- 台账真实连通（8000 端口、真实督办形状）属 task-5/QA 口径，本文只核对投影/同步/传输层的代码路径与协议文档。
- Git 工作区不干净（50 项未提交改动），本次未区分「未提交改动」与「已提交内容」对上述行为的影响；引用行号均以当前工作区文件为准。
