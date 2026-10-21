# 仓库调查报告（REPO_RECON）

> TASK-000 · 2026-10-19 · 执行：Chief（总协调）+ 5 名专项 Bot
> 性质：**只读核验 + 实测**。本轮未修改任何源码、配置、测试或既有文档；新增文件仅本报告与同批核验文档。
> 证据口径：每条结论附「文件:行号」或「命令 + 输出」。没有运行证据的一律标 **未验证**。

---

## 0. 一句话结论

真实仓库是 `D:\project\neurosama`（GitHub `cs1lion/xiaoxi`），主产品是「小溪」个人语音 Agent；像素世界是其中**独立子工程** `pixel-world/`，Phaser 3 + TypeScript + Vite 已真实落地并可运行。文档记录的进度**严重滞后于代码**：`PROJECT_PROGRESS.md` 停在「Phase 1 IN_PROGRESS / TASK-1.2 待做」，代码实际已推进到 Phase 6 主体（19 张任务卡：17 DONE + 2 PARTIAL）。但离 `PROJECT_BRIEF` 定义的 MVP 1.0 仍差两个硬项：**多角色（2–3 个小人）** 与 **JSON 存档**。

---

## 1. 仓库身份与位置

| 项 | 事实 | 证据 |
| --- | --- | --- |
| 真实项目根目录 | `D:\project\neurosama` | `git rev-parse --show-toplevel` → `D:/project/neurosama` |
| 远程 | `git@github.com:cs1lion/xiaoxi.git` | `git remote -v` |
| 当前分支 | `feat/pixel-world-oracle-world`，跟踪 `origin/feat/pixel-world-oracle-world` | `git branch -vv` |
| HEAD | `9cd0c5a feat(world): 像素世界接上真实督办台账` | `git log --oneline -1` |
| 主产品 | 「小溪 Xiaoxi」：FastAPI + 零构建原生 ES module 前端 + Python Worker | `README.md:1`、`requirements.txt`、`web/index.html` |
| 像素世界 | 独立原型 `pixel-world/`，**不属于**主仓库零构建前端 | `pixel-world/README.md:1-4`、`pixel-world/vite.config.ts:3-4` |
| 文档启动包 | `pixel-agent-chief-starter/`（用户提供的文档包，非源码） | `pixel-agent-chief-starter/WORKSPACE_NOTE.txt` |

三个工程边界必须分清（否则会重复造工程、或把文档包当源码根）：

1. **主仓库 `D:\project\neurosama`**：已存在的个人 Agent 产品，Python 真相源，`web/` 零构建。
2. **`pixel-world/`**：像素世界原型，自带 `package.json` / `node_modules` / `vite.config.ts`，与主仓库前端互不引入（`pixel-world/vite.config.ts:3-4` 注释明说「主库一行不受影响」）。
3. **`pixel-agent-chief-starter/`**：只有文档与任务模板，**不含源码**（`WORKSPACE_NOTE.txt` 原文）。

---

## 2. Git 状态与最近提交

### 2.1 最近提交（当前分支）

```
9cd0c5a feat(world): 像素世界接上真实督办台账
94afcab feat(pixel-world): 补完工单剩余任务卡（造物主/桥接/作息/四态/天气/日报/拟音）
b280ed6 feat(pixel-world): 神谕闭环原型跑通（逻辑层可离线测）
df36bed docs: 补上两条走不通的修法，避免下一个人重复试
31e1177 docs: 记录四个"视觉层缺口"与一个并行改动引入的红门禁
...
```

`git log -- pixel-world` 显示：**像素世界的 Git 历史从 `b280ed6` 开始**，`b280ed6` 时已是模块化版本。文档所称「Phase 0 / MVP 0.1 单文件 `main.ts` + `index.html`」**不在本仓库 Git 历史中**（见 §6.3）。

### 2.2 工作区状态：不干净，且是两条并行工作流

- 已修改 35 个文件（`git diff --stat`：35 files changed, 1642 insertions(+), 522 deletions(-)），集中在**主产品**：`server/assistant/*`、`server/dialogue/orchestrator.py`、`server/memory/evolution.py`、`web/avatar/render/vrm.js`、`web/stage-fx.*`、`scripts/*`、`config/*.yaml`。
- 未跟踪目录/文件：`agent-runtime/`、`web/fonts/`、`docs/DECISION_TRAIL_20261018.md`、`docs/PIXEL_WORLD_*`、`docs/REVIEW_ROUND4/5_*`、`pixel-agent-chief-starter/`、若干 `scripts/probe_takeover_*.py` 等。
- **`pixel-world/` 干净**：`git status --porcelain pixel-world` 无输出。
- 另有：`master` 领先 `origin/master` 20 个提交；`stash@{0}: On master: 待确认`；3 个额外 worktree（`.claude/worktrees/confident-roentgen-3771a9`、两个 `WorkBuddy` worktree）。

**风险**：像素世界与主产品改动混在同一分支的工作区。任何 `git add -A` / 全量提交都会把未审阅的主产品改动与像素世界绑在一起。本轮所有验证均在「当前工作区内容」上完成，但像素世界本身在这 3 个提交后没有未提交改动，可信度较高。

---

## 3. 目录结构（真实）

### 3.1 主仓库

| 路径 | 职责 | 状态 |
| --- | --- | --- |
| `server/` | FastAPI 应用（`app.py`、`assistant/`、`executive` 台账、`dialogue/`、`memory/`、`speech/`） | 已存在，本轮只读，未运行冷启动 |
| `web/` | 主前端，原生 ES module + importmap，three.js/VRM 形象 | 已存在，本轮未运行其测试 |
| `scripts/` | Python 门禁与 Node 浏览器回归（`check_protocol.py`、`test_core.py`、`run_gates.py`…） | 已存在 |
| `config/` | YAML 契约（角色、节奏、花名册、avatar） | 已存在 |
| `docs/` | 设计与决策文档（约 100 份 + 本轮新增核验文档） | 已存在 |
| `pixel-world/` | 像素世界原型（见 3.2） | 已存在 |

### 3.2 `pixel-world/`

```
src/world/      纯逻辑，不 import Phaser，可被 node 直接加载（types 193 行、simulation 750 行、grid 258 行、
                setup 139、blueprints 192、ledger 170、projection 137、oracle 45、tasks 24、routines 70、
                weather 42、faith 48、report 67、bridge 65、prefabs.json 58）
src/render/     只读 WorldSnapshot：tilemap 96、buildings 153、villager 116、decor 104、weather 63、smoke 59、palette 36
src/scenes/     唯一 Phaser 场景 SandboxScene.ts 344 行
src/ui/         DOM 神谕面板 oracle-panel.ts 124 行
src/audio/      程序化拟音 animalese.ts 103 行
src/main.ts     工程入口 37 行
test/           4 个纯逻辑测试文件：grid 7 / ledger 14 / surface 13 / world 25 = 59 用例 / 147 断言
scripts/        smoke.mjs 浏览器闭环回归
```

### 3.3 文档启动包 `pixel-agent-chief-starter/`

`README_CHIEF_START.md`、`docs/PROJECT_BRIEF.md`、`docs/PROJECT_PROGRESS.md`、`docs/DEMO_SCRIPT_TEMPLATE.md`、`docs/ART_SPEC_TEMPLATE.md`、`docs/source/01-…md`（790 行设计决策）、`docs/source/02-…md`（早期讨论存档）、`tasks/TASK-000-REPO-RECON.md`。

---

## 4. 技术栈核验：Phaser 3 / TypeScript / Vite 已落地（有文件证据）

| 层 | 事实 | 证据 |
| --- | --- | --- |
| 渲染引擎 | **Phaser 3.90.0**（已安装于 `pixel-world/node_modules/phaser`，含源码与类型） | `pixel-world/package.json:16`、`pixel-world/node_modules/phaser/package.json` |
| 构建 | **Vite 8.3.4**（dev server 5188、proxy `/api`、`/ws` → 8000） | `pixel-world/package.json:20`、`pixel-world/vite.config.ts:5-18` |
| 语言/类型 | **TypeScript 5.9.3**，`strict: true`、`noUnusedLocals/Parameters`、`verbatimModuleSyntax`、`erasableSyntaxOnly`、`moduleResolution: bundler` | `pixel-world/package.json:19`、`pixel-world/tsconfig.json:1-25` |
| 像素设置 | `pixelArt: true`、`roundPixels: true`、`scale: NONE` + `zoom: 2`、`image-rendering: pixelated` | `pixel-world/src/main.ts:18-31`、`pixel-world/index.html:32` |
| 逻辑分辨率 | 30×20 格 × 16px = **480×320** | `pixel-world/src/world/setup.ts:12-14` |
| 主产品栈 | Python 3.12.4（`.venv`）+ FastAPI；`web/` 零构建，three 0.180 + `@pixiv/three-vrm` 3.5.5（根 `package.json`） | `requirements.txt:1-2`、根 `package.json:35-37`、`.venv/Scripts/python.exe --version` → 3.12.4 |
| 测试工具 | 根 `@playwright/test` 1.61.1（供 `pixel-world/scripts/smoke.mjs` 复用） | 根 `package.json:40`、`smoke.mjs:12` |

**结论**：文档包要求核验的「Phaser 3 + TypeScript + Vite」不是计划，而是**已落地的实际技术栈**。同时存在一份 2026-10-18 的架构笔记结论相反（「不引成熟引擎，v1 用 Canvas 2D」，`docs/PIXEL_WORLD_ARCHITECTURE.md:43-50`）——该冲突由 `ARCHITECTURE_GAPS.md` 专项处理，按 README_CHIEF_START §2 的优先级，`PROJECT_BRIEF.md` 的「已确认 Phaser 3」优先。

---

## 5. 启动 / 测试 / 构建方法（本轮实测）

全部在 `D:\project\neurosama\pixel-world`，Node v22.23.1 / npm 10.9.8。命令与结果详见 `docs/RUNTIME_VERIFICATION.md`（含逐条日志、退出码、截图）。

| 用途 | 命令 | 实测结果 |
| --- | --- | --- |
| 依赖安装 | `npm install` | node_modules 已存在；**从零安装未验证** |
| 类型检查 | `npm run check` | exit 0（tsc 静默，1.894s） |
| 逻辑测试 | `npm test`（`node --test test/*.test.ts`） | **59/59 passed**，exit 0（全权限下可跑；受限沙箱下会因 `spawn EPERM` 失败） |
| 逐文件测试 | `node test/world.test.ts` 等 4 个 | 25 / 14 / 13 / 7 全过 |
| 构建 | `npm run build` | exit 0，30 modules，产物 1,251,932 B（gzip 336.96 kB），有 >500kB chunk 警告（非阻断） |
| 开发服务器 | `npm run dev` → `http://127.0.0.1:5188` | HTTP 200，1s 内就绪 |
| 浏览器闭环（沙盒） | `node scripts/smoke.mjs` | **49/49 通过**，33.0s，截图 6 张 |
| 显式离线 | `?ledger=off` + smoke 附加校验 | 7/7，状态行显示「台账离线」 |
| 浏览器闭环（台账） | `LEDGER=1 node scripts/smoke.mjs` | **10/10 通过**，2.9s（复用会话前已存在的 8000 服务） |
| 构建产物预览 | `npm run preview`（4174） | 可加载，canvas / 3 栋铺子 / `window.Oracle` 正常，零报错 |

**注意**：`node --test` 的 `spawn EPERM` 是**当前沙箱模式的限制**，不是项目缺陷；放宽为全权限后 `npm test` 一次通过 59/59。文档里的测试命令无需改写。

---

## 6. 代码入口与调用链

### 6.1 启动链

```
index.html（DOM：标题 / #app / 神谕输入条 / 预设按钮 / 报纸）
  → src/main.ts:11  parseBlueprintTable(prefabs.json)
  → src/main.ts:12  buildWorld(blueprints)          // setup.ts：30×20、7 分区、落位、自动铺路、不变量校验
  → src/main.ts:18  new Phaser.Game({ 480×320, pixelArt, zoom 2, scale NONE })
  → src/main.ts:36  game.scene.add('SandboxScene', SandboxScene, true, { world, blueprints, villagerName: '莞尔' })
  → src/main.ts:33  bindOraclePanel(() => window.Oracle)   // DOM 面板与场景解耦
```

### 6.2 运行时链（逻辑/渲染分离）

- `SandboxScene.ts:31` 固定逻辑步长 `LOGIC_STEP_MS = 100`（10Hz）；`update()` 每帧只读快照重画，不做业务判断。
- **唯一命令入口**：外部只能 `sim.enqueue()`，命令在下一逻辑拍统一结算（`simulation.ts`）；`SandboxScene.ts:184-198` 的 `say()` 把文本 → `parseOracle()` → `enqueue()`。
- **对外 API**（`SandboxScene.ts:231-252`）：`window.Oracle = { say, view, feed, pushLedger }`。`view()` 返回快照，`feed()` 收外部 Agent 消息，`pushLedger()` 注入台账帧。
- **台账链**：`?ledger=off` 显式沙盒（`SandboxScene.ts:119-120`）；否则 `LedgerClient`（`ledger.ts`）REST 全量 `GET /api/executive/commissions` + WS 增量 + 断线轮询；`projection.ts` 是台账字段 → 世界事实的唯一映射点；`simulation.sync` 把快照落进世界命令队列。
- **DOM 归 DOM**：中文状态行、回执、报纸、输入框全在 `index.html` + `oracle-panel.ts`，canvas 只画世界（README §三条硬约束第 3 条）。

### 6.3 关于「MVP 0.1」的真相

- 设计基线里的「Phase 0 / MVP 0.1」= 单文件 `src/main.ts` + `index.html` 的概念验证（`source/01:690-693`、附录 A `:763-774`），功能是：单场景、祭坛/工坊色块、小人闲逛、`window.Oracle.sendTask()/completeTask()`、工坊冒烟粒子。
- **本仓库不存在这份单文件产物**：`pixel-world` 的 Git 历史从 `b280ed6`（已是模块化版本）开始；`ROUND1:7` 也记载「仓库里已有 `pixel-world/` 空壳（只有 `types.ts` 与 `grid.ts`）」。
- 因此「MVP 0.1 已完成」只能按**功能等价物**认定：`simulation.ts:414-464`（dispatch）、`:663-693`（行礼→进铺子→开工）、`:729-752`（交付）已完全覆盖并超出 MVP 0.1 的能力；**无法逐行对照旧产物**。
- 结论：MVP 0.1 的**能力存在且可运行**（49 项浏览器断言覆盖），但**产物不在仓库**。这是文档与仓库之间第一条必须向用户说明的差距。

---

## 7. 已核验事实（证据充分，可当基线）

1. 根目录/分支/HEAD/远程如上；`pixel-world` 工作区干净。
2. Phaser 3.90 / TS 5.9.3 / Vite 8.3.4 真实落地，`strict` 通过。
3. 类型检查、59 项逻辑测试、构建、开发服务器、沙盒浏览器闭环 49/49、台账模式 10/10、显式离线 7/7 全部**实测通过**。
4. Phase 0–6 的 19 张任务卡中 17 DONE、2 PARTIAL（3.3 无行为栈、4.1 `overheating` 不可达，均为文档已声明的有意取舍）；TASK-1.1/1.2 已实现（目录与 API 形状与旧工单不同）。
5. MVP 1.0 九项 = 4 DONE / 3 PARTIAL / 2 MISSING（MISSING：多角色、JSON 存档）。
6. 台账下行链（REST 全量 + WS 增量 + 断线轮询）是真实实现；前端 `projection.ts` 是唯一映射点。
7. `ROUND1/2/3` 的工程完成声明抽查 6 条全部成立；失真集中在 `PROJECT_PROGRESS.md` 与 README 的数字/注释。

## 8. 未验证 / 未知（不许当事实）

| # | 未验证项 | 原因 | 由谁补 |
| --- | --- | --- | --- |
| U1 | **无 API Key 时自动 Mock 的运行时路径** | 8000 上是 2026-10-18 起即存在的真实 Key 实例（`offline:false`），未重启 | QA（下一阶段，需协调重启服务） |
| U2 | **服务端真实 WS `commission_update` 推送链路** | 台账冒烟用页面内 `pushLedger` 注入帧，只验了客户端接收/投影/队列 | QA + 集成（需主服务协作） |
| U3 | 主服务冷启动与全量门禁（`run_gates.py` 23 道门） | 与本轮像素世界范围无关，未跑 | 主产品线负责人 |
| U4 | `npm install` 从零安装 | 依赖已存在 | 下一阶段 CI 步骤 |
| U5 | 3 秒可读性（BRIEF 验收目标 1） | 需人工验收，无计时测试 | 美术导演 + 用户 |
| U6 | `/api/executive/adapters` 真实返回与前端字段是否匹配 | 静态核验发现 `agent_id` vs `id` 错位（P0），未在真机跑一次 | 集成负责人（下一阶段首项） |

## 9. 风险与建议

- **R1 工作区混杂**：像素世界提交与主产品 35 个未提交改动同处一个工作区/分支。建议像素世界后续提交只 `git add pixel-world docs/<本轮文档>`，禁止 `-A`。
- **R2 文档失真**：`PROJECT_PROGRESS.md` 停留在「待核验」；README 内 59/45 自相矛盾；`main.ts:25` 注释 480×396 错误；架构笔记与 Phaser 实现路线相反。已被本轮文档记录，需在施工前修掉，否则下一轮 Bot 会按错基线排期。
- **R3 MVP 硬缺口**：多角色、存档两条完全未做，直接影响验收目标 4；「生活工坊」语义（writing vs 生活）、「Mock/LLM 神谕层」、调试面板 UI 三个半项需要定口径。
- **R4 集成 P0**：`adapters` 字段错位导致真机 `agents` 恒空；WS 漏推无周期对账会「显示在线但世界不动」；REST `limit=50` 被当全量。演示前必须修。
- **R5 命名漂移**：主产品叫「小溪」，原型里角色/标题出现「莞尔」（README 标题、`main.ts:36`）、「艾尔」（`package.json:5` description）、「阿栖」（ROUND1 明确不进此世界）。DEMO/ART 规范定稿前必须统一角色世界设定。

---

**附：本轮配套核验文档**

- `docs/RUNTIME_VERIFICATION.md`（运行时实证，492 行）
- `docs/TASK_COMPLETION_MATRIX.md`（任务/阶段完成度矩阵，168 行）
- `docs/ARCHITECTURE_GAPS.md`（架构冲突与风险）
- `docs/DEMO_ART_SPEC_TODO.md`（DEMO_SCRIPT / ART_SPEC 待完善事项）
- `docs/AGENT_INTEGRATION_STATUS.md`（像素世界 × 台账集成状态，290 行）
- `docs/DECISION_TRAIL_20261019.md`（本轮决策与待拍板项）
