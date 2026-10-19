# PROJECT_PROGRESS.md（已核验版）

> 2026-10-19 由 Chief 主持核验并重写。
> **本文件此前状态：「从设计文档迁入，尚未由真实仓库核验」——该状态已结束。**
> 下列每条结论都有本轮实测或 `文件:行号` 证据；没有证据的标 **未验证**。
> 证据索引见文末；完整证据见 `docs/REPO_RECON.md`、`docs/RUNTIME_VERIFICATION.md`、`docs/TASK_COMPLETION_MATRIX.md`、`docs/ARCHITECTURE_GAPS.md`、`docs/AGENT_INTEGRATION_STATUS.md`、`docs/DEMO_ART_SPEC_TODO.md`。

## 状态摘要（2026-10-19 15:40 实测）

- 真实项目根目录：`D:\project\neurosama`；分支 `feat/pixel-world-oracle-world`；HEAD 以 `git log -1` 为准。
- 像素世界技术栈：**Phaser 3.90 + TypeScript 5.9.3 + Vite 8.3.4，已落地并实测可运行**。
- **文档旧声称严重滞后**：旧进度写「Phase 0 / MVP 0.1 已完成，Phase 1 IN_PROGRESS，TASK-1.1 IN_PROGRESS，TASK-1.2 下一项」。真实情况：19 张任务卡全部落地，主体已到 Phase 6 + S2。
- **MVP 1.0 九项**：7 DONE / 2 PARTIAL（三工坊语义、Mock/LLM 神谕层 尚未补齐）。
- 门禁实测（2026-10-19 16:30）：`npm run check` 通过；`npm test` **139/139**；`npm run build` 通过；浏览器冒烟沙盒 **114/114**、台账 **21/21**、显式离线 7/7；`check_protocol.py` PASS、`test_core.py` 64/0。
- S2 全部完成：任务排队（task-10）、多角色 2–3（task-17）、多角色 P3（task-21）、JSON 存档（task-18/23/24，v1 冻结）、调试面板（task-19/25）。下一项 S3 视觉垂直切片，等 D5/D6 拍板。

## 一、阶段与任务卡（核验后）

| 阶段 | 文档旧声称 | 核验结论 | 关键证据 |
| --- | --- | --- | --- |
| Phase 0 / MVP 0.1 | 已完成 | **能力 DONE / 产物不在仓库** | 等价闭环 `src/world/simulation.ts`；Git 历史从 `b280ed6`（模块化版）开始，单文件 MVP 0.1 无留档 |
| Phase 1（1.1 目录与类型 / 1.2 WorldGrid / 1.3 蓝图 / 1.4 造物主） | IN_PROGRESS，1.2 待做 | **全部 DONE** | `types.ts`、`grid.ts`（`findBestSlot`）、`blueprints.ts` + `prefabs.json`、`setup.ts`、`simulation.ts` |
| Phase 2（2.1 协议 / 2.2 桥接） | PENDING | **DONE（且已接真实台账）** | `bridge.ts`、`ledger.ts`、`projection.ts`、`SandboxScene.ts` |
| Phase 3（3.1 A*/Y-Sort / 3.2 日常 / 3.3 打断） | PENDING | **PARTIAL** | A* `grid.ts`；Y-Sort 渲染层；日常 `routines.ts`；3.3 已由排队语义部分覆盖 |
| Phase 4（4.1 四态 / 4.2 长短分级） | PENDING | **PARTIAL** | 四态类型存在，`overheating` 恒不可达（有意取舍） |
| Phase 5（天气 / 躲雨） | PENDING | **DONE** | `weather.ts`、`simulation.ts`、`render/weather.ts`、`villager.ts` |
| Phase 6（日报 / 拟音 / 信仰） | PENDING | **DONE** | `report.ts`、`oracle-panel.ts`、`audio/animalese.ts`、`faith.ts` |

## 二、MVP 1.0 九项必须项（2026-10-19 更新）

| # | 要求 | 结论 | 证据 / 缺口 |
| --- | --- | --- | --- |
| 1 | 一张固定预制地图 | **DONE** | `setup.ts`、`prefabs.json`、`assertWorldInvariants` |
| 2 | 2–3 个小人 | **DONE** | task-17：`villagers[]`、独立状态/队列；默认 2 人，可配置 3 人 |
| 3 | coding/research/生活三工坊（Idle/Operating） | **PARTIAL** | 第三间仍是 `writing`（抄书斋），`living` 分区空置；待 D6 口径裁决 |
| 4 | 高质量 Mock 神谕路由 + LLM 可选 + 离线降级 | **PARTIAL** | 关键词路由 + 离线 mock；无 LLM 适配器层 |
| 5 | 晴/雨 + 户外躲雨 | **DONE** | `weather.ts`、`simulation.ts` |
| 6 | 模板式 DOM 日报 | **DONE** | `report.ts`，已按角色分账 |
| 7 | JSON 存档，单存档槽 | **DONE** | task-18/23/24：v1 schema 冻结、对账、去重、坏档/配额/高版本兜底 |
| 8 | 上帝调试面板（实体列表 + 事件流） | **PARTIAL** | 事件数据已有，**面板 UI 尚未实现**（S2-3） |
| 9 | 一个文本「用户下指令」入口 | **DONE** | `index.html`、`oracle-panel.ts` |

## 三、第一项：只读基线核验（已完成）

- [x] 确认真实项目根目录/仓库与分支 → `D:\project\neurosama`，`feat/pixel-world-oracle-world`。
- [x] 检查 Git 工作区状态和最近提交 → 主产品有既有未提交改动；`pixel-world/` 曾干净；本轮提交按路径隔离。
- [x] 检查 `package.json`、锁文件、Vite 配置、TypeScript 配置与启动脚本 → 全部存在且已实际使用。
- [x] 实际运行当前项目，并记录命令和结果 → 见 `docs/RUNTIME_VERIFICATION.md`。
- [x] 核验 Phase 0 MVP 是否仍存在且可运行 → 能力存在；**单文件产物不在仓库**。
- [x] 核验 TASK-1.1、TASK-1.2 的实际实现与测试 → 均已 DONE；1.2 的 `findBestSlot(prefab, anchor)` 与旧工单签名不同。
- [x] 检查是否已有 `DEMO_SCRIPT.md`、`ART_SPEC.md`、测试和进度文档 → 两份仍只有模板；测试覆盖见上。
- [x] 输出 `docs/REPO_RECON.md` 并向用户汇报 → 已产出（2026-10-19）。

## 四、后续主线（按 2026-10-19 核验重排）

- [x] **S0 文档对账**：README 计数、`main.ts` 注释、历史提案横幅、ROUND1 注记；`npm run check` 通过。
- [x] 决策：D1/D2/D3/D4/D7 已定并落地；D5 中文出 canvas、D6 尺寸/调色板待 S3 前定。
- [x] **参考包采纳三卡**：task-7 重连指数退避、task-9 契约 fixture、task-8 `/ws` Origin；证据 `docs/QA_VERIFICATION_S1.md`。
- [x] D3 人工交付只对沙盒单生效 + 离线 Mock 显式标记。
- [x] D4 台账 Agent 能力映射：只读 `/api/executive/roster`，现有返回结构零改动。
- [x] **S1 收口**：参考包三卡 + D3/D4 + 剩余四项（周期对账/分页、真帧端到端、pickActive、blocked_question）。
- [x] **S2-5 任务排队 + 取消未出发行礼**（task-10）。
- [x] **S2-1 多角色 2–3**（task-17）；P3 收口（task-21：日报归属、手动完成对象）。
- [x] **S2-2 存档 ADR**（task-20，`docs/ADR_SAVE.md`，final，§1 frozen）。
- [x] **S2-2 JSON 存档单槽 + 恢复**（task-18/23/24：刷新恢复、对账三分支、settled 去重、坏档/配额/高版本、v1 指纹冻结；P3：配额暂停无运行时恢复接线、DEBUG 面板未做）。
- [x] **S2-3 调试面板 UI**（task-19/25，QA 通过：实体/事件/暂停/过滤/注入区；P3：390px 页面级溢出另开卡）。
- [x] **S3-2a 调色板审计**（task-26，QA 通过：世界 71 唯一/越界 55、UI 17/越界 9、并集 86；报告 `docs/ART_PALETTE_AUDIT.md`，只报告不阻断）。
- [x] **S3-1 中文迁 DOM overlay**（task-27，QA 最终通过、P2 关闭：附录 A 与 `--md` 实跑逐行 132/132 全等；canvas 中文 0 处、对齐误差 0.00–0.40px）。
- [x] **S3-2b 调色板收敛到 16+8**（task-28，QA 最终通过：世界 16/UI 8/共享 1/并集 23、越界 0/0、`--strict` 0；`test:inprocess` 150/150；读档按 prefabId 重解 palette（gap A）；截图脚本 `scripts/capture-palette.mjs` 同种子可复现；P3：3 秒指认待用户评审、smoke CDP 运行属作者侧证据）。
- [x] **S3-3 帧动画 + 12fps**（task-29，QA 最终通过：帧号 = `floor(snapshot.now/(1000/12)) % 帧数`，idle 2 帧 / walk 4 帧；整数像素与全整数姿态偏移；确定性回放含小人真走路 300 步快照相等；`test:inprocess` 161/161；证据 `eval/screens/anim-manifest.txt`；P3：smoke/动画截图 sha 复跑在 QA 沙箱记未验证、人眼辨识待用户评审）。
- [x] **D7-A 门禁接线**（task-31，QA 通过：本地 `--strict` 门禁 3/3 PASS、CI 模式 `--report-only` 不挡配色预算但 check/test 仍参与退出码；门禁 test 步改 `test:inprocess` 适配受限沙箱；对抗探针门禁级 FAIL/PASS 翻转演示）。
- [x] S3-4 垂直切片评审：证据通过 QA 技术验收（`capture-palette --scenario slice`，15 张 + manifest，三态严格同源 0 不匹配）；**用户已判定「四栋能分清、雨天可读」**；1.0 保持世界 16 色 + UI 8 色，夜景 16 色并团留 1.1（18 色 ADR）。ART_SPEC/DEMO_SCRIPT 草案进行中。
- [ ] **S2-2b 存档手动导出/导入**（task-22，排在 S3 后）。
- [ ] 定稿并批准 `DEMO_SCRIPT.md` v0.1 与 `ART_SPEC.md` v0.1（用 `docs/DEMO_ART_SPEC_TODO.md` 的清单）。
- [ ] 3 分钟演示跑通 + QA 独立复验 + 用户验收后，才进入 Agent 正式双向集成。

## 五、进度更新规则（保留）

每次更新需注明日期、任务 ID、状态变化、代码/文档证据、运行命令与测试结果。未运行、未验证或未提供证据，不可标为 DONE。

## 六、证据索引

| 文档 | 内容 |
| --- | --- |
| `docs/REPO_RECON.md` | 仓库结构、启动/测试方法、入口与调用链、未知项 |
| `docs/RUNTIME_VERIFICATION.md` | 逐条命令、退出码、日志、截图、文档声称 vs 实测 |
| `docs/TASK_COMPLETION_MATRIX.md` | 19 张任务卡与 MVP 九项逐项判定 |
| `docs/ARCHITECTURE_GAPS.md` | 决策清单、文档冲突、架构风险、待裁决项 |
| `docs/DEMO_ART_SPEC_TODO.md` | DEMO_SCRIPT / ART_SPEC 待补清单 |
| `docs/AGENT_INTEGRATION_STATUS.md` | 台账集成真实/仅 Mock/缺失三分类、风险 |
| `docs/ADR_SAVE.md` + `docs/save-v1.sample.json` | 存档 v1 冻结规范与样例 |
| `docs/QA_VERIFICATION_S1.md` | 全部任务的独立复验记录 |
| `docs/DECISION_TRAIL_20261019.md` | 本轮结论与用户拍板事项 |
| `docs/NEXT_PHASE_PLAN.md` | 下一阶段施工计划与验收标准 |
