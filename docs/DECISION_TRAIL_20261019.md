# 决策留痕：2026-10-19 像素世界基线核验（第一轮）

> **⚠️ 过期快照（2026-10-25 回填标注）**：本文为 2026-10-19 的决策留痕，其后进展以 `git log` 与门禁命令输出为准；现行交付状态见 `docs/DELIVERY_PLAN_20261024.md`。

本轮由用户发起：「AI 原生像素个人助理世界」项目启动，第一阶段只做基线核验，不改代码。
执行：Chief 总协调 + 5 名专项 Bot（架构、运行时 QA、代码审计、美术/演示、Agent 集成），
每项核验只有一个主负责人；所有结论要求文件、运行结果或测试证据。

## 一、本轮已核验的事实（不是决策，是可复现结论）

1. 真实根目录 `D:\project\neurosama`；分支 `feat/pixel-world-oracle-world`；HEAD `9cd0c5a`；`pixel-world/` 工作区干净。
2. Phaser 3.90 + TypeScript 5.9.3 + Vite 8.3.4 **已落地**：`pixel-world/package.json:15-22`、`main.ts:17-33`、`tsconfig.json`。
3. 实测门禁：`npm run check` 通过；`npm test` 59/59；`npm run build` 通过（产物 1.25MB，gzip 337kB）；
   浏览器冒烟沙盒 49/49、台账 10/10、显式离线 7/7。证据：`docs/RUNTIME_VERIFICATION.md`。
4. 进度文档严重滞后：19 张任务卡实际 17 DONE + 2 PARTIAL，文档仍停在 Phase 1 IN_PROGRESS。证据：`docs/TASK_COMPLETION_MATRIX.md`。
5. MVP 1.0 九项：4 DONE / 4 PARTIAL / 2 MISSING（多角色、JSON 存档）。证据：同上 §2。
6. 台账**下行链**真实（REST 全量 + WS 增量 + 断线轮询），**上行链**仅 Mock（`Bridge` 未实例化）。证据：`docs/AGENT_INTEGRATION_STATUS.md`。
7. 存在 P0 级问题：人工交付可绕过台账完成权（`simulation.ts:500-513`）；`/api/executive/adapters` 字段契约错位
   （返回 `agent_id`、无 `strengths`，前端 `projection.ts:141-150` 解析失败 → Agent→铺子映射恒空）。
8. `DEMO_SCRIPT.md`、`ART_SPEC.md`、`REPO_RECON.md` 此前均不存在；本轮已补 `REPO_RECON.md`，
   另两份的真实状态与待补清单见 `docs/DEMO_ART_SPEC_TODO.md`。

## 二、需要用户拍板的决策（本轮不擅自裁决）

> 按 `README_CHIEF_START.md:21-31` 的优先级，「用户最新决定」最高。以下是按证据整理出的问题，
> Chief/Architect 的推荐不等于决定。拍板后由 Chief 写入 `docs/ARCHITECTURE_GAPS.md` D 节并转 accepted。
>
> **用户已拍板（2026-10-19 12:57）**：D1、D2 采纳推荐项（见下，状态改为 accepted）；D3、D4 先逐条讨论；
> D5/D6/D7 暂未定。同时**批准启动 S0（文档对账与工程护栏）**，S1 起暂缓。

### D1 — 引擎与文档定性（**accepted · 2026-10-19 · 用户选 A**）
- 现状：`PROJECT_BRIEF.md:10` 写「Phaser 3 + TS + Vite 已确认」；`docs/PIXEL_WORLD_ARCHITECTURE.md:45-51` 写「不引成熟引擎、v1 Canvas 2D」；代码是 Phaser。三份文档都没有记录「独立原型 + Phaser」的转向 ADR。
- 选项：A（推荐）确认 Phaser 3 为样品 1.0 与近期正式栈，把两份 pixel 笔记标为「历史提案」，补 ADR；B 改回 Canvas 2D/Pixi（`src/world/` 可复用，重写 `src/render/`+`src/scenes/`，49 项冒烟需重跑）；C 暂不裁决（后续 Agent 继续在冲突里猜）。
- 影响：决定渲染层改造是否发生、像素化 3D（E2）是否还有意义。
- **裁决结果（A）**：Phaser 3.90 + TypeScript + Vite 为样品 1.0 与近期正式渲染栈；`PIXEL_WORLD_ARCHITECTURE.md`、`PIXEL_WORLD_RESEARCH.md` 标注为历史提案并保留原文；不启动引擎迁移。本记录即 ADR。

### D2 — MVP 1.0 范围冻结（**accepted · 2026-10-19 · 用户选 A**）
- 现状：BRIEF 的必须项缺 2 个硬项（多角色、存档）+ 1 个 UI（调试面板），却已实现多项「暂不包含」（自动铺路、长短分级、四态、拟音、信仰、真实台账）。
- 选项：A（推荐）按 BRIEF 冻结，先补 2–3 小人、JSON 存档、调试面板；B 先做像素化 3D（E2，美术路线否决实验）；C 先做真实 Agent 双向集成（写回/验收）。
- 影响：直接决定下一阶段的施工顺序与演示剧本的第三段是否可演。
- **裁决结果（A）**：按 `PROJECT_BRIEF.md:22-32` 冻结样品 1.0 范围，先补 2–3 个小人、JSON 存档单槽、调试面板 UI；
  像素化 3D（E2）、LLM 造物主生成、完整双向 Agent 集成列为 1.1，需另行批准。已实现的超范围项保留但不扩写。

### D3 — 人工交付的语义（P0 诚信风险）（**accepted · 2026-10-19 · 用户选 A，附三条约束**）
- 现状：`simulation.ts:500-513` 的 `completeManually` 不检查任务来源，可把台账单在本地标记完成并加神恩，违反 `README.md:85`「完成权在台账手里」；无测试覆盖。
- 选项：A（推荐）人工交付只对沙盒单生效，台账单拒绝并提示「完成权在台账」；B 保留现状但 UI 明示「本地标记，不改台账」；C 移除人工交付入口。
- 影响：「世界不撒谎」这条产品原则是否还成立；演示需不需要两段式。
- **裁决结果（A + 条件，2026-10-19）**：台账单拒绝人工完成（完成权唯一来源）；离线 Mock 显式标记（状态行/回执/日报分账）；不改主产品 server。已落地并经 QA 复验（`docs/QA_VERIFICATION_S1.md`）。

### D4 — 台账 Agent 能力映射的修法（涉及主产品服务端，需你批准才动）（**accepted · 2026-10-19 · 用户选 A，条件：现有返回结构零改动**）
- 现状：`GET /api/executive/adapters` 返回 `{agent_id, name, …}`，世界要 `{id, strengths}`（`projection.ts:141-150`）→ 真机 `agents` 恒空，7 Agent 归三类活退化为标题关键词。
- 选项：A（推荐）新增只读端点 `GET /api/executive/roster`（`id/name/strengths`），世界改读它；B 在现有 adapters 响应里补 `id` 与 `strengths`；C 世界放弃能力映射，承认按标题猜。
- 影响：A/B 需要改主产品服务端（属于「不得擅自修改」的范围，必须你批准）；C 不改码但产品承诺作废。
- **裁决结果（A + 条件，2026-10-19）**：新增只读 `GET /api/executive/roster`（现有 `/adapters` 返回结构 byte-identical），前端优先读 roster、404 回退；只读、不写库。已落地并经 QA 复验。

### D5 — 世界内中文的处置（红线级）（**accepted · 2026-10-19 · 用户选 A + 条件**）
- 现状：`README.md:78-79`、`index.html` 声明「中文永远归 DOM」，但渲染层有 6 处 `scene.add.text` 画中文（`villager.ts:32-47`、`buildings.ts:75-80`、`decor.ts:101-108`、`SandboxScene.ts:155-162,345-350`）；截图中「路灯/神谕祭坛/角色名」叠字，400% 下不可读，390px 视口横向溢出。
- 选项：A（推荐）迁到 DOM overlay（工作量中，兑现红线）；B 放宽红线，承认小字号中文进 canvas 并补可读性验收；C 只迁气泡（信息量最大），名字保留 canvas。
- 影响：ART_SPEC 的可读性验收是否成立、3 秒可读性验收目标能否达成。
- **裁决结果（A + 条件，2026-10-19）**：动态中文（名字/气泡/状态行）走 DOM overlay，**静态文字改图标**；overlay 用 canvas 实际位置统一换算，100%–400% 截图验证对齐；若切片评审发现 DOM 风格割裂，凭截图走 ADR 调整。

### D6 — 尺寸/调色板/帧率口径（ART_SPEC 定稿前提）（**accepted · 2026-10-19 · 用户选 A + 条件**）
- 冲突：瓦片 16px（实现）vs 32px（`source/01:200`）；原生分辨率 480×320（实现/README）vs 480×300（架构笔记）vs `main.ts:25` 注释 480×396；调色板实现约 72 色 vs 预算 12–16 色；12fps 是「动画采样」还是「渲染限帧」未定义。
- 选项：A（推荐）以实现为准：16px tile / 480×320 / 调色板硬上限 16（世界 16 + UI 8 双表） / 12fps 定义为动画采样、渲染跟随屏幕；B 按设计文档重排尺寸（需重画全部素材与蓝图）；C 放宽调色板到 24/32。
- 影响：精灵规格、锚点、建筑尺寸、E2 实验、调色板校验器是否要做。
- **裁决结果（A + 条件，2026-10-19）**：16px tile / 480×320 / 世界 16 色 + UI 8 色；色数口径**排除运行时色调与透明度叠加**；校验器先只报告、CI 不阻断；12fps 只管动画采样，位置取整、整数缩放；若 16 色做不出可读的雨天/夜景，凭截图走 ADR 调整。S3 顺序：校验器 → 中文迁 DOM → 调色板收敛 → 帧动画 → 切片评审。

### D7 — 工程护栏（低风险，建议一并拍板）（**accepted · 2026-10-19 · 用户选 A，分两步落地**）
- 选项：A（推荐）把 `pixel-world` 纳入 `scripts/run_gates.py` 与 CI（`tsc --noEmit` + 4 个测试文件 + smoke），`docs/TESTING.md` 写明本环境 `node --test` 的行为；B 只在 CI 加独立 job；C 维持人工 QA。
- 影响：59 项断言是否成为回归护栏。
- **裁决结果（A，分两步，2026-10-19）**：
  1. 已落地（commit `b656488`）：`.github/workflows/pixel-world.yml` 路径触发（只 `pixel-world/**` + 脚本 + 自身）+ `scripts/test_pixel_world.py` 本地独立门禁（`npm run check` + `npm test`，不依赖浏览器/端口）；启用前连跑 3 次 PASS（每次约 3s，90/90）。
  2. **暂缓**：`scripts/run_gates.py` 接入。原因：该文件是主产品 35 个既有未提交改动之一（53 行 durable 改动），按路径加入一行会把既有改动混入门禁提交；等 durable 线收尾后一行接入，或由用户批准一次混合提交。浏览器 smoke 按路径触发/本地手跑，不放进稳定门禁。

### D-SAVE-1 — 存档方案（**accepted（final）· 2026-10-19 · schema 已冻结**）
- 交付：`docs/ADR_SAVE.md`（task-20；单槽 JSON、只存世界自有事实、读档与台账对账、`schemaVersion` + 迁移链）。
- **用户裁决**：原则批准；**先修多角色 P3（task-21：日报归属、手动完成对象），QA 通过后再开 task-18**；两者不并行；`schemaVersion=1` 的字段清单在 task-21 合并后锁定。
- **ADR 需补齐（用户点名核对）**：①已结算任务 ID 持久化去重；②Mock 与台账来源分开记账；③对账失败的显示与重试；④坏档先备份再提示；⑤高版本存档只读；⑥存储失败（配额/异常）降级；⑦关闭期间的时间规则；⑧迁移测试样例（v1→v2 签名 + 断言）。
- **ADR 补写完成（2026-10-19）**：`docs/ADR_SAVE.md` 94 行，八点全部覆盖（settled 去重表、分账、对账失败显示与重试、坏档备份、高版本只读、存储失败降级、关闭期间冻结、v1→v2 迁移样例）。仍有 6 个细节待用户拍板：
  - S1 冻结确认方式：推荐 task-21 QA 后由 Chief 书面确认 §1 字段锁定并留痕，用户可随时复核。
  - S2 `settled` 是否 v1 就带 `completedAt`：推荐 v1 带（避免为单字段升 v2）。
  - S3 已结算历史长度：推荐 1.0 不设硬上限，超过 5000 条仅告警（设上限有重复计数风险）。
  - S4 坏档备份保留：推荐带时间戳、保留最近 3 份（单 `.corrupt` 会被下次覆盖）。
  - S5 单槽 localStorage 绑 origin（5188）：推荐 1.0 接受，演示固定 `npm run dev`；换端口/预览不共享属已知限制。
  - S6 关闭期间冻结世界：推荐接受（MVP 不做离线推进）；将来要做挂机另立 v2 + 离线推进规则。
- **用户最终裁决（2026-10-19，6 项全按推荐 + 5 条补充）**：
  1. S1 冻结确认：Chief 书面确认，并留冻结的 v1 样例存档 + 指纹测试（字段变了但版本号没升 → 测试失败）。
  2. S2：`completedAt` 台账来源取台账时间戳，Mock 来源取本地时间并标记，不混用。
  3. S3：1.0 不设硬上限，定义告警阈值（>5000 告警一次），并测试存储写满失败路径（配额异常 → 停自动存档、提示、世界继续）。
  4. S4：坏档带时间戳备份、保留 3 份；配额紧张时先删最旧备份再写主存档，主存档写入不得失败。
  5. S5：Vite 保持 `strictPort`；读不到存档提示可能是 origin/端口变化；手动导出/导入另立 task-22（已建，排在 task-18 后）。
  6. S6：接受关闭期间冻结世界。
- 状态：ADR 转 final 后 task-18 开工；task-21 已确认不碰存档字段并提交 `2bde7cb`。

## 三、Chief 按现有规则代行的临时判断（可被用户推翻）

1. **Phaser 3 已由用户确认**（D1-A，2026-10-19）：按正式渲染栈对待，不启动引擎迁移。
2. **本轮不改任何源码/配置/测试**；只新增核验文档，并修正启动包里的 `PROJECT_PROGRESS.md`（它自己声明「待 Chief 核验并更新」）。
3. **文档中所有「已完成」不再直接采信**：本轮只承认有文件:行号或运行证据的状态；「未验证」保持未验证（如无 Key Mock 路径、服务端真实 WS 推送）。
4. **不触碰个人 Agent 核心、记忆与自进化系统**：主产品 `server/assistant/*`、`server/memory/*` 的 35 个未提交改动只记录、不评价、不修改。
5. **S0 已批准并执行中**（2026-10-19）：只改文档与一行注释；S0-6（纳入门禁）等 D7，S1 等 D3/D4。

## 四、本轮产出（新增文件，未提交 Git）

| 文件 | 作者 | 用途 |
| --- | --- | --- |
| `docs/REPO_RECON.md` | Chief | 仓库调查报告（结构/启动/测试/入口/未知项） |
| `docs/RUNTIME_VERIFICATION.md` | QA Bot | 逐条命令与实测证据 |
| `docs/TASK_COMPLETION_MATRIX.md` | 代码审计 Bot | 任务卡与 MVP 九项逐项判定 |
| `docs/ARCHITECTURE_GAPS.md` | 架构 Bot | 决策清单/文档冲突/风险/待裁决项 |
| `docs/DEMO_ART_SPEC_TODO.md` | 美术导演 Bot | DEMO_SCRIPT / ART_SPEC 待完善事项 |
| `docs/AGENT_INTEGRATION_STATUS.md` | Agent 集成 Bot | 台账集成三分类与风险 |
| `docs/DECISION_TRAIL_20261019.md` | Chief | 本文件 |
| `docs/NEXT_PHASE_PLAN.md` | Chief | 下一阶段计划与验收标准 |
| `pixel-agent-chief-starter/docs/PROJECT_PROGRESS.md` | Chief | 进度文档改写为「已核验版」 |

## 五、Gate

- 第一轮（基线核验）：**已完成**，报告见同批 6 份核验文档。
- **S0（文档对账与护栏）：用户已批准，进行中**（2026-10-19）。S0-1~S0-5 开工；S0-6（纳入门禁）等 D7。
- **S1（P0 诚信与集成修复）：已完成（2026-10-19）**。参考包采纳项 task-7（WS 指数退避）、task-9（契约 fixture）、
  task-8（`/ws` Origin 校验）三张卡已完成并经 QA 独立复验通过，证据见 `docs/QA_VERIFICATION_S1.md`；
  task-8 服务器改动仅 `server/app.py` +24 行、只限 `/ws`，未触碰 assistant/memory/自进化。
  D3、D4、参考包三卡与 S1 剩余四项（周期对账/分页、真帧端到端、pickActive、blocked_question）已全部完成并经 QA 复验；未验证仅 D7 上游派活。
- 在 D3/D4 拍板前，不开始大规模编码；不重构 Agent 核心；不更换已确认的技术栈（D1 已定）。

## 六、后续决策追加（S1–S3，2026-10-19）

- **D3-A / D4-A**：人工交付只对沙盒单生效；新增只读 `GET /api/executive/roster`，`/adapters` 返回结构零改动。
- **D7-A**：像素世界独立门禁（tsc + 测试 + smoke）按路径触发；`scripts/run_gates.py` 因含主产品既有未提交改动，接入暂缓。
- **存档 D-SAVE-1**：final，v1 schema 冻结；只存世界自有事实、读档先与台账对账、带版本号；冻结样例 `docs/save-v1.sample.json`、指纹 `f53a0736d782d657`（99 条路径）。
- **D5-A / D6-A**（15:57 批准）：动态中文走 DOM、静态文字改图标、overlay 按画布实际位置换算；16px tile / 480×320 / 世界 16 色 + UI 8 色 / 12fps 只管动画采样；色数口径排除运行时叠加与透明度；校验器先只报告；切片评审后凭截图走 ADR。
- **task-28 收敛裁决**：workshop 屋顶 `#450f43` → W13 `#30506b`，不加第 17 色；token 以语义名（`WORLD.ink` 等）为唯一命名，W01–W16 只作文档索引；`--strict` 收敛后 exit 0；`--strict` 接本地 gate 另开卡。
- **夜景试验结论**：16 色下路径/石面并团（W05/W06 1.27:1，压暗后更弱）；1.0 不做昼夜，若 1.1 做夜景需 18 色并走 ADR，不擅自加色。
- **G2 / S5**（2026-10-19）：用户批准「范围预批准」——S3/S4 收口前不动 `server/assistant/**`；S5 开工前重贴 `git status`，`executive.py` 未提交 durable 改动若仍在则停下报告；§4.4 选 A（只扩显式点名世界的短语 + 改动前后 `should_route` 差分测试）；落点 5 处编辑（`service.py` 4 + `executive.py` 1）；ADR 状态「范围已批准，实施待 S5」。
- **S3-4 切片评审**（2026-10-19，用户判定）：看 E-16 截图后判定「四栋能分清、雨天可读」——S3 垂直切片成立。**1.0 保持世界 16 色 + UI 8 色，不新增颜色**；夜景 16 色并团（路径/石面 1.10–1.32:1）不阻塞 1.0，若 1.1 做昼夜再走 18 色 ADR。DOM overlay 与 canvas 尺度用户未提异议。
- **S4 演示验收 · live 派单链路争议裁决**（2026-10-19）：QA 复现不出施工的 4 帧 `commission_update`，我以 Chief 身份独立复现（自有临时副本 + `.venv` python 直持 uvicorn + 独立 WS 探针）确认**施工证据成立**：payload `{"type":"text","text":"去把便笺整理成三版标题","turn_id":…}` → 4 帧（drafting→briefing→running→blocked）+ REST items=1；同句改成「帮我把…」→ 0 帧、静默走普通对话。真因是 `_DELEGATION`（`server/dialogue/orchestrator.py:60-65`）第一支要求「去(把|帮我|替你)…」，**派单意图只由正则判定、同义改写会静默降级且无提示**——与 S5 ADR §4.4 选 A（只扩显式短语 + 改动前后差分测试）互为印证，该弱点写入 S4 已知限制。
- **S4 验收方式与两项决定**（2026-10-20，用户）：用户选 B——**自己跑一遍演示**（含一次同义改写派单、刷新恢复、导出导入）后再定稿；已知限制 7 条逐条指定负责人与落点；「同义改写静默不建单」**并入 S5 §4.4 验收语料**并评估可见提示（Agent 集成出方案、架构复核，S5 前不写代码）；**8000 实机演示暂不批准**（live 演示继续用隔离 8001，完整闭环看 Mock 遍）。
- **主产品改动现状**（2026-10-20 核对，`d2b8ea9`）：主产品既有未提交改动 **39 项**（server/web/scripts/config 等）**全程未动**；`server/assistant/executive.py` 相对 HEAD 为 **+136/−5** 的 durable 改动（未提交，用户自有线），我方 `roster_view` 已在 S1 提交前移出该文件（当前无残留）。**S5 前置**：开工前重贴 `git status`；若 `executive.py` 的 durable 改动仍未提交 → 停下报告；G2 范围已批（`service.py` 4 处 + `executive.py` 1 处，5 处编辑）；§4.4 选 A + 差分测试 + 派单正则反例语料；QA 真帧端到端复验（禁注入帧）。
- **计数勘误与 S5 阻塞认定**（2026-10-20，用户要求只读核对）：主产品**已跟踪**未提交改动实为 **35 项**（server 8 / web 6 / scripts 15 / config 2 / docs 2 / 根 2），与第一轮基线一致、全程未动；此前口头说的「39」是统计时把 `scripts/` 下 5 个未跟踪文件和 `web/fonts/`（共 6 项 ??）误计入，特此更正。`server/assistant/executive.py` 现为 **+136/−5**（未提交）。**用户明示：`executive.py` 的主产品改动由用户本人处理，团队不得触碰；S5 在该文件收口前一律标记为「被阻塞」**（G2 第 5 处编辑正落在该文件）。
- **task-32 立项**（2026-10-20，用户批准）：存档字段级校验 + 变异测试。硬性要求：不改 `schemaVersion`、冻结样例与指纹零改动、样例仍可读；每个叶子路径声明类型+范围/枚举；对每个字段路径做「删除/错类型/置空」三变异，加载器不得抛未捕获异常且必须走坏档备份路径；读档与导入共用同一校验器；**不得静默补默认值**；更新 `docs/ADR_SAVE.md` §1.3。与既有「路径 ⊆」校验合成两级防线（前者防新增字段，后者防删除/类型/越界）。
- **存档指纹语义**（task-22，2026-10-19）：加载校验 = 「存档实际路径 ⊆ 冻结 99 条 + 4 条可选路径」，**只检测新增路径**；`SCHEMA_V1_FINGERPRINT` 仅用于锁样例与单测，运行时派生的全集哈希不得冒充冻结常量；删除/类型变化不在路径集校验范围，记 task-32（最小 required 集），v2 以正式 schema 承接。
- **S3 收口与 S4 进入**（2026-10-19，用户选 A）：ART_SPEC v0.2 定稿；DEMO_SCRIPT v0.2 批准进入 S4，**演示验收通过后再定稿 v1.0**。S4 硬性要求：每段操作步骤 + 预期画面 + **实测计时**（总时长以实测为准，不凑满 3 分钟）；每段标注 live 台账/真实 WS/Mock；live 台账、smoke、主服务冷启动三项未验证项要么当场验证留证、要么写已知限制；提供干净状态重置步骤；QA 独立复验**至少两遍**（Mock + 真实台账）并附 100%/400% 整页截图；文档数字必须由脚本输出生成或引用；QA 通过后用户亲自验收。
