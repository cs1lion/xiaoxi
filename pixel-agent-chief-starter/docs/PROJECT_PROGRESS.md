# PROJECT_PROGRESS.md（已核验版）

> 2026-10-19 由 Chief 主持核验并重写。
> **本文件此前状态：「从设计文档迁入，尚未由真实仓库核验」——该状态已结束。**
> 下列每条结论都有本轮实测或 `文件:行号` 证据；没有证据的标 **未验证**。
> 证据索引见文末；完整证据见 `docs/REPO_RECON.md`、`docs/RUNTIME_VERIFICATION.md`、`docs/TASK_COMPLETION_MATRIX.md`、`docs/ARCHITECTURE_GAPS.md`、`docs/AGENT_INTEGRATION_STATUS.md`、`docs/DEMO_ART_SPEC_TODO.md`。

## 状态摘要（2026-10-19 12:38 实测）

- 真实项目根目录：`D:\project\neurosama`；分支 `feat/pixel-world-oracle-world`；HEAD `9cd0c5a`。
- 像素世界技术栈：**Phaser 3.90 + TypeScript 5.9.3 + Vite 8.3.4，已落地并实测可运行**。
- **文档旧声称严重滞后**：旧进度写「Phase 0 / MVP 0.1 已完成，Phase 1 IN_PROGRESS，TASK-1.1 IN_PROGRESS，TASK-1.2 下一项」。真实情况：19 张任务卡 **17 DONE + 2 PARTIAL**，主体已到 Phase 6。
- **MVP 1.0 仍未达标**：九项必须项 = **4 DONE / 4 PARTIAL / 2 MISSING**（详见下表；MISSING：多角色、JSON 存档；调试面板按「数据有、UI 无」计入 PARTIAL）。
- 门禁实测：`npm run check` 通过；`npm test` **59/59**；`npm run build` 通过；浏览器冒烟沙盒 **49/49**、台账 **10/10**、显式离线 **7/7**。

## 一、阶段与任务卡（核验后）

| 阶段 | 文档旧声称 | 核验结论 | 关键证据 |
| --- | --- | --- | --- |
| Phase 0 / MVP 0.1 | 已完成 | **能力 DONE / 产物不在仓库** | 等价闭环 `src/world/simulation.ts:414-464,663-693,729-752`；Git 历史从 `b280ed6`（模块化版）开始，单文件 MVP 0.1 无留档 |
| Phase 1（1.1 目录与类型 / 1.2 WorldGrid / 1.3 蓝图 / 1.4 造物主） | IN_PROGRESS，1.2 待做 | **全部 DONE** | `types.ts:1-221`；`grid.ts:41-256`（`findBestSlot` 在 `:134`）；`blueprints.ts:29-111` + `prefabs.json`；`setup.ts:76-130`、`simulation.ts:515-537,767-778` |
| Phase 2（2.1 协议 / 2.2 桥接） | PENDING | **DONE（且已接真实台账）** | `bridge.ts:13-74`；`ledger.ts:37-186`；`projection.ts`；`SandboxScene.ts:213-217` |
| Phase 3（3.1 A*/Y-Sort / 3.2 日常 / 3.3 打断） | PENDING | **PARTIAL** | A* `grid.ts:168-214`；Y-Sort `buildings.ts:144`、`villager.ts:65`；日常 `routines.ts:21-78`；3.3 无行为栈——手上有活时新神谕**直接拒绝**（`simulation.ts:414-419`） |
| Phase 4（4.1 四态 / 4.2 长短分级） | PENDING | **PARTIAL** | 类型四态 `types.ts:123-142`；`overheating` 永不触发（`tasks.ts:27-30` 恒 false，有意取舍）；长短分级 `tasks.ts:9-25` |
| Phase 5（天气 / 躲雨） | PENDING | **DONE** | `weather.ts:20-46`；`simulation.ts:384-412`；`render/weather.ts`；`villager.ts:71,82-90` |
| Phase 6（日报 / 拟音 / 信仰） | PENDING | **DONE** | `report.ts:17-58` + `oracle-panel.ts:61-72`；`audio/animalese.ts`；`faith.ts:10-54` |

## 二、MVP 1.0 九项必须项

| # | 要求 | 结论 | 证据 / 缺口 |
| --- | --- | --- | --- |
| 1 | 一张固定预制地图 | **DONE** | `setup.ts:12-33`、`prefabs.json`、`assertWorldInvariants`（`setup.ts:133-158`） |
| 2 | 2–3 个小人 | **MISSING** | 单数模型 `types.ts:174-204`、`simulation.ts:108`；README 自认「只有她一个」 |
| 3 | coding/research/生活三工坊（Idle/Operating） | **PARTIAL** | 三栋接活铺子 `prefabs.json:2-43`，但第三间是 `writing`（抄书斋）不是「生活」；`living` 分区空置（`setup.ts:28`） |
| 4 | 高质量 Mock 神谕路由 + LLM 可选 + 离线降级 | **PARTIAL** | 仅关键词路由 `oracle.ts:15-51`；无 LLM 适配器、无 Mock LLM 层；离线路径成立 |
| 5 | 晴/雨 + 户外躲雨 | **DONE** | `weather.ts:20-46`、`simulation.ts:384-412` |
| 6 | 模板式 DOM 日报 | **DONE** | `report.ts` + `index.html:150-157` + `oracle-panel.ts:61-72`；非「每日归档」 |
| 7 | JSON 存档，单存档槽 | **MISSING** | 全仓 grep `save/load/serialize/localStorage` 0 命中；`Simulation` 状态全 private |
| 8 | 上帝调试面板（实体列表 + 事件流） | **PARTIAL** | 事件流数据在（`simulation.ts:230,800-803`），**无 UI**；只能 devtools 读 `window.Oracle.view()` |
| 9 | 一个文本「用户下指令」入口 | **DONE** | `index.html:130-148`、`oracle-panel.ts:98-136` |

## 三、第一项：只读基线核验（已完成）

- [x] 确认真实项目根目录/仓库与分支 → `D:\project\neurosama`，`feat/pixel-world-oracle-world`，HEAD `9cd0c5a`。
- [x] 检查 Git 工作区状态和最近提交 → 35 个已修改文件（主产品，与像素世界无关）+ 若干未跟踪；`pixel-world/` 干净；`master` 领先 origin 20。
- [x] 检查 `package.json`、锁文件、Vite 配置、TypeScript 配置与启动脚本 → 全部存在且已实际使用。
- [x] 实际运行当前项目，并记录命令和结果 → 见 `docs/RUNTIME_VERIFICATION.md`（tsc / 59 项测试 / build / dev 5188 / smoke 49+10+7）。
- [x] 核验 Phase 0 MVP 是否仍存在且可运行 → 能力存在（49 项浏览器断言）；**单文件产物不在仓库**。
- [x] 核验 TASK-1.1、TASK-1.2 的实际实现与测试 → 均已 DONE；1.2 的 `findBestSlot(prefab, anchor)` 与旧工单签名不同、无直接单测。
- [x] 检查是否已有 `DEMO_SCRIPT.md`、`ART_SPEC.md`、测试和进度文档 → 两份均**不存在**（只有模板）；测试 59 用例；进度文档即本文件。
- [x] 输出 `docs/REPO_RECON.md` 并向用户汇报 → 已产出（2026-10-19）。

## 四、后续主线（按 2026-10-19 核验重排）

- [x] **S0 文档对账（2026-10-19 完成）**：README 计数、`main.ts:25` 注释、两份架构笔记历史提案横幅、ROUND1 七态注记；4 文件 6 行，`npm run check` 通过。
- [ ] 用户拍板待定决策：D1/D2 已定；D3 人工交付、D4 台账 Agent 映射、D5 中文出 canvas、D6 尺寸/调色板、D7 纳入门禁仍待定（见 `docs/DECISION_TRAIL_20261019.md`）。
- [x] **参考包采纳三卡（2026-10-19 完成，QA 复验通过）**：task-7 重连指数退避、task-9 契约 fixture、task-8 `/ws` Origin 校验；证据 `docs/QA_VERIFICATION_S1.md`。
- [ ] P0 剩余：人工交付只对沙盒单生效（D3）、台账 `adapters` 字段契约（D4）、周期对账/心跳、一次真帧端到端。
- [ ] MVP 硬缺口：多角色 2–3、JSON 存档单槽 + 恢复、调试面板 UI。
- [ ] 视觉垂直切片：中文迁出 canvas、调色板收敛 + 校验器、idle/walk 帧动画 + 12fps 采样、证据链重建。
- [ ] 定稿并批准 `DEMO_SCRIPT.md` v0.1 与 `ART_SPEC.md` v0.1（用 `docs/DEMO_ART_SPEC_TODO.md` 的 A1–A12 / B1–B12 清单）。
- [ ] 3 分钟演示跑通 + QA 独立复验 + 用户验收后，才进入 Agent 正式双向集成（写回/验收/打回）。

## 五、进度更新规则（保留）

每次更新需注明日期、任务 ID、状态变化、代码/文档证据、运行命令与测试结果。未运行、未验证或未提供证据，不可标为 DONE。

## 六、证据索引

| 文档 | 内容 |
| --- | --- |
| `docs/REPO_RECON.md` | 仓库结构、启动/测试方法、入口与调用链、未知项 |
| `docs/RUNTIME_VERIFICATION.md` | 逐条命令、退出码、日志、截图、文档声称 vs 实测 |
| `docs/TASK_COMPLETION_MATRIX.md` | 19 张任务卡与 MVP 九项逐项判定 |
| `docs/ARCHITECTURE_GAPS.md` | 决策清单、文档冲突、架构风险、待裁决项 |
| `docs/DEMO_ART_SPEC_TODO.md` | DEMO_SCRIPT / ART_SPEC 待补 A1–A12、B1–B12 |
| `docs/AGENT_INTEGRATION_STATUS.md` | 台账集成真实/仅 Mock/缺失三分类、风险 R1–R12 |
| `docs/DECISION_TRAIL_20261019.md` | 本轮结论与需用户拍板事项 |
| `docs/NEXT_PHASE_PLAN.md` | 下一阶段施工计划与验收标准 |
