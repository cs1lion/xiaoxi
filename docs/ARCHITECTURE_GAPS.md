# ARCHITECTURE_GAPS — 像素世界架构核验报告

> 核验人：Arch（Team `architect`，任务 task-1）｜日期：2026-10-19
> 范围：`D:\project\neurosama`，分支 `feat/pixel-world-oracle-world`，HEAD `9cd0c5a`
> 纪律：只读核验；未修改任何源码、配置、测试或已有文档。本文件为唯一新增交付。
> 证据约定：`文件:行号` 均相对 `D:\project\neurosama`；行号基于 HEAD `9cd0c5a` + 当前工作区。命令输出见 §0.1。
> 未复跑项：`npm run check`、浏览器冒烟 49/49、`node --test` 的 EPERM 限制属已知实测（Lead 共享事实 / QA task-2），本报告不重复运行，引用时标注“已知实测”。

---

## 0. 核验基线与方法

### 0.1 仓库与工具链基线（命令 + 输出）

```
$ git branch --show-current
feat/pixel-world-oracle-world
$ git log -1 --format='%h %ad %s' --date=iso
9cd0c5a 2026-10-19 10:32:04 +0800 feat(world): 像素世界接上真实督办台账
$ git status --porcelain（分类计数）
modified: 35   untracked: 15
$ node -v / npm -v
v22.23.1 / 10.9.8
$ (pixel-world) node_modules/{phaser,vite,typescript}/package.json
phaser = 3.90.0   vite = 8.3.4   typescript = 5.9.3
$ git log --format='%h %ad %s' --date=short -- docs/PIXEL_WORLD_ARCHITECTURE.md
（无输出：该文件未跟踪）
$ git status --porcelain | Select-String '^\?\?'
?? agent-runtime/  ?? docs/DECISION_TRAIL_20261018.md  ?? docs/PIXEL_WORLD_ARCHITECTURE.md
?? docs/PIXEL_WORLD_RESEARCH.md  ?? docs/REVIEW_ROUND4_20261018.{md,html}
?? docs/REVIEW_ROUND5_20261018.{md,html}  ?? pixel-agent-chief-starter/ …
```

结论（事实）：`pixel-world/` 有 3 个提交（`b280ed6` 07:13、`94afcab` 07:30、`9cd0c5a` 10:32，均为 2026-10-19）。文档入库状态用 `git ls-files --error-unmatch` 逐条核验：**未跟踪**——`PROJECT_BRIEF.md`、`source/01`、`source/02`（整个 `pixel-agent-chief-starter/`）、`docs/PIXEL_WORLD_RESEARCH.md`、`docs/PIXEL_WORLD_ARCHITECTURE.md`、`docs/DECISION_TRAIL_20261018.md`、`docs/REVIEW_ROUND4/5_20261018.md`；**已跟踪**——`PIXEL_WORLD_ROUND1/2/3`、`DECISION_TRAIL_20261017.md`。即：带“已确认决策”的基线文档与最新架构笔记不在仓库里，clone 后看到的是“实现已提交、依据未提交”的不对称状态（风险见 B0/C12）。

### 0.2 文档时间线（文件 mtime；不当作作者日期，只当“入盘时间”）

| 文档 | 盘上时间 | 文内日期/出处 |
|---|---|---|
| `docs/PIXEL_WORLD_RESEARCH.md` | 2026-10-18 17:26 | “2026-10-18 · 仅调研与提案”（:3） |
| `docs/PIXEL_WORLD_ARCHITECTURE.md` | 2026-10-18 19:39 | “2026-10-18 · 接 PIXEL_WORLD_RESEARCH.md … 未改代码”（:3） |
| `docs/PIXEL_WORLD_ROUND1_20261018.md` | 2026-10-18 23:10 | “2026-10-18。按用户的三项选择执行”（:3） |
| `docs/PIXEL_WORLD_ROUND2_20261019.md` | 2026-10-19 07:29 | “2026-10-19”（:3） |
| `docs/PIXEL_WORLD_ROUND3_20261019.md` | 2026-10-19 10:22 | “2026-10-19”（:3） |
| `pixel-agent-chief-starter/docs/PROJECT_BRIEF.md` | 2026-10-19 12:26 | 无文内日期 |
| `pixel-agent-chief-starter/docs/source/01-…md` | 2026-10-19 12:26 | “v1.0（对话整理）”（:3） |
| `pixel-agent-chief-starter/docs/source/02-…md` | 2026-10-19 12:26 | 无文内日期（早期讨论存档） |
| `docs/DECISION_TRAIL_20261017/18.md` | 23:48 / 16:35 | 各自日期 |
| `docs/REVIEW_ROUND4/5_20261018.md` | 17:46 / 21:34 | “2026-10-18” |

### 0.3 核验边界

- 未启动任何服务（QA 负责运行时）；未改代码；未 commit/checkout/stash/reset。
- 逻辑断言仅做静态取证（grep / 读码 / 计数），运行期结论引用 QA 与已知实测。
- 推断一律标“推断：”。

---

## 1. 结论摘要（TL;DR）

1. **Phaser 3 是当前有效决策**：`PROJECT_BRIEF.md:10`（“已确认”）+ `README_CHIEF_START.md:26-28` 的优先级 + 已提交代码（`package.json:16`、`main.ts:17-33`）。`PIXEL_WORLD_ARCHITECTURE.md:43-51` 的“不引成熟引擎 / v1 Canvas 2D”是**更早的提案**（:3 “未改代码”），且其首要前提（无构建步骤、与 three.js 抢 rAF）已被“独立 `pixel-world/` 目录 + 自己的 Vite”绕开（`ROUND1:7-14`）。冲突仍在文档层未裁决（B1）。
2. **逻辑/渲染边界总体成立**：`src/world/` 无 `import Phaser`（grep 无命中）、无 `Date.now`/`Math.random`（grep 仅渲染层天气有 `Math.random`），命令单入口队列成立（`simulation.ts:163-183`）。
3. **但边界有两处实质破坏**：渲染层直接读模拟对象 `sim.weatherIntensity()`（`SandboxScene.ts:289`），以及中文/名牌/气泡被画进 canvas（`villager.ts:32-47`、`buildings.ts:75-80`、`decor.ts:101-108`、`SandboxScene.ts:155-162,345-350`），违反 `README.md:78-79` 的硬约束（C1）。
4. **台账投影存在两个静默失真**：`blocked_question` 在 `ledger.ts`→`simulation.ts` 之间被丢弃（`projection.ts:84` → `ledger.ts:175-181` → `simulation.ts:310-319`），以及 `GET /api/executive/adapters` 并不返回 `strengths`，世界的 Agent→活类映射实际恒为空、退化为标题关键词（B6.1/C2）。
5. **P0 诚信风险**：`#finish`“人工交付”可把台账单在本地标记 done（`simulation.ts:500-513` 无 `source` 检查），与 `README.md:85`/`ROUND3:54`“完成权在台账手里”直接冲突（C4）。
6. **MVP 1.0 三项必须项缺失**：2–3 小人、JSON 存档、上帝调试面板（`PROJECT_BRIEF.md:25,30,31`；代码无对应实现，grep 存档/debug 无命中）；同时已实现多项“暂不包含”项（自动铺路、长短任务、四态动画、拟音、信仰摆件），范围管理失衡（B2/B4）。
7. **集成/验证缺口**：`pixel-world` 不在根 `npm test` 与 `scripts/run_gates.py` 中；`check_protocol.py` 只做事件名字面量比对（C9）；世界与产品 `web/` 零接线（`web/` grep 无 pixel-world 命中），仅能靠 Vite dev proxy 连台账（C5/C12）。
8. **未验证而影响裁决的假设**：真实台账生命周期（不注入帧）驱动世界、生产构建的 `/api` `/ws` 同源方案、半开 WS 检测、确定性回放、性能基线、像素化 3D（架构笔记的 E2 实验）等（E 节）。

---

## A. 已确认决策清单

| # | 决策 | 出处（文件:行） | 代码/运行证据 | 与代码一致？ |
|---|---|---|---|---|
| A-01 | 产品定位：AI Native 像素“盒子世界”，用户以神谕影响世界，Agent 任务具身化 | `PROJECT_BRIEF.md:5,17`；`source/01:27` | `pixel-world/README.md:6-13` 四个验伪判断 | 一致 |
| A-02 | 纯 2D 正交像素、关抗锯齿、整数缩放、像素完美优先 | `PROJECT_BRIEF.md:9`；`source/01:48,96` | `main.ts:23-30`（`pixelArt/roundPixels`、`zoom:2`、`Scale.NONE`） | 一致 |
| A-03 | 技术栈 Phaser 3 + TypeScript + Vite | `PROJECT_BRIEF.md:10`；`source/01:67-76,95`；`README_CHIEF_START.md:39,73` | `package.json:15-22`；`main.ts:17-33`；命令：phaser 3.90.0 / vite 8.3.4 / TS 5.9.3 | 一致（但与两份 pixel 笔记冲突，见 B1） |
| A-04 | 不引重型 ECS，手写“数据组件 + 系统函数”，数据与表现解耦 | `PROJECT_BRIEF.md:13`；`source/01:102,115` | `src/world/` 模块化（`types/grid/simulation/…`），无 ECS 依赖（`package.json:15-22`） | 一致（形态是类 + 纯函数） |
| A-05 | 自研 `WorldGrid` 管运行时空间（分区/占用/留白/A*） | `PROJECT_BRIEF.md:11`；`source/01:98` | `grid.ts:41-251`；`world.test.ts:104-144`（不变量/扩建） | 一致 |
| A-06 | 静态底图用 Tiled（仅底图与 Zone 标注） | `PROJECT_BRIEF.md:11`；`source/01:97` | 实际程序化绘制地形（`render/tilemap.ts:14-34`）；无 Tiled 资产/依赖（`package.json:15-22`） | 未实现（不影响运行） |
| A-07 | Prefab/JSON + Aseprite spritesheet 数据驱动 | `PROJECT_BRIEF.md:12`；`source/01:99` | `prefabs.json:1-58` 有；spritesheet/Aseprite 无，渲染全是 `Graphics` 色块（`render/buildings.ts`、`villager.ts`） | 部分 |
| A-08 | 寻路方案 EasyStar.js | `PROJECT_BRIEF.md:12`；`source/01:101` | 实际自研 4 邻域 A*（`grid.ts:167-214`）；无 easystar 依赖（`package.json:15-22`） | 偏离（功能等价） |
| A-09 | 状态/事件用集中 `GameState` + Phaser 事件机制 | `PROJECT_BRIEF.md:14` | 实际 `Simulation` 类 + 自定义 listener Set（`SandboxScene.ts:59-61,240-248`）；grep `GameState` / `EventEmitter` 无命中 | 偏离 |
| A-10 | Agent 通信异步 WS/消息队列，支持重连、心跳、错误处理 | `PROJECT_BRIEF.md:14`；`README_CHIEF_START.md:82` | `ledger.ts:54-62,106-135` 有 REST+WS+轮询；**无心跳、无超时**；`bridge.ts:1-10` 明确“运输层在别处”（未实现） | 部分 |
| A-11 | 首期伪光照（全局调色 + 发光贴图 + 混合模式） | `PROJECT_BRIEF.md:15`；`source/01:107` | 只有雨幕压暗矩形（`render/weather.ts:42`）与灯的光圈（`render/decor.ts:80-83`）；无全局调色/混合模式 | 部分 |
| A-12 | LLM 只出语义/结构化提案，Schema + 确定性校验，失败降级 | `PROJECT_BRIEF.md:16`；`source/01:277-285,339` | 世界侧无任何 LLM 调用；意图=关键词规则（`oracle.ts:15-51`）；气泡是写死文案（`simulation.ts:475,746`） | 未接入（离线路径成立） |
| A-13 | 不做键鼠玩法，输入=文本神谕 | `PROJECT_BRIEF.md:17`；`source/01:46` | `index.html:130-148` 表单/预设按钮；`ui/oracle-panel.ts:98-136` | 一致 |
| A-14 | 无语音陪伴/TTS；语音只作为用户输入方式 | `PROJECT_BRIEF.md:18`；`README_CHIEF_START.md:84` | 无 TTS；`audio/animalese.ts:37-85` 为 Web Audio 拟音（世界音效，非语音播报） | 一致 |
| A-15 | Mock-first，真实 Agent 后置 | `PROJECT_BRIEF.md:50`；`source/01:643` | 先沙盒（`ROUND1`）后接台账只读（`ROUND3`）；`ledger.ts` 全文件只有 GET（:77-80） | 一致 |
| A-16 | 世界是投影，真相源在 Python；世界只读 | `PIXEL_WORLD_RESEARCH.md:189-203`；`ROUND3:8-21` | `ledger.ts:77-80,143-171` 仅读取；无 POST 调用 | 一致（完成权例外见 C4） |
| A-17 | 完成权在台账：台账说收工才交付，沙盒才由计时器收尾 | `ROUND3:53-55`；`README.md:85` | 自动收尾仅对 `determinate`=sandbox 生效（`simulation.ts:678-691,706-710`）；但 `completeManually` 无 source 检查（`simulation.ts:500-513`） | 部分违反（C4） |
| A-18 | 固定步长 10Hz、逻辑无内部时钟、可确定性回放 | `ROUND1:27,31-32`；`README.md:53` | `simulation.ts:1-5,131,163-183`；场景 100ms 固定步（`SandboxScene.ts:31,168-176`）；逻辑模块无 `Date.now`/`Math.random`（grep） | 一致（无回放器，见 C8） |
| A-19 | 中文归 DOM，不画进 canvas，长文本留纸面 | `README.md:78-79`；`ROUND1:16` | 实际 canvas 有中文：`villager.ts:32-47`（名字/气泡）、`buildings.ts:75-80`、`decor.ts:101-108`、`SandboxScene.ts:155-162,345-350` | 违反（C1） |
| A-20 | 世界规格 30×20 格 / 16px / 480×320 / 固定 2 倍整数放大 | `README.md:93`；`source/01` 未定死该数值 | `setup.ts:12-14,18-31`；`main.ts:27-31`；`main.ts:25` 注释“480×396”为错（实际 320） | 一致（注释有误，B5） |
| A-21 | 单角色；7 个 Agent 的活都归她一人 | `README.md:107`；`ROUND3:81` | `simulation.ts:108` 单 villager；`smoke.mjs` 全程单角色 | 实现一致，但与 `PROJECT_BRIEF.md:25` 的 2–3 小人冲突（B2） |
| A-22 | 天气只两种（晴/雨）+ 躲雨行为 + TTL 自动退场 | `PROJECT_BRIEF.md:28,42`；`source/01:599` | `weather.ts:17-18,30-46`；`simulation.ts:384-412`；`routines.ts:47-61` | 一致（TTL 数值冲突，B6） |
| A-23 | `commission_update` 事件纳入协议/服务端/世界三方校验 | `ROUND3:37-47`；`PROTOCOL.md:348-349` | `app.py:966-1004,1008-1012` 广播；`check_protocol.py:274-296` 静态核对；`ledger.ts:111-121` 消费 | 一致（校验口径弱，C9） |

---

## B. 文档冲突表

### B0. 冲突裁决规则本身不覆盖 pixel-world 新文档（元冲突）

- 优先级规则在 `README_CHIEF_START.md:21-31`：①用户当前对话最新决定 → ②`PROJECT_BRIEF.md` 中标“已确认” → ③`source/01` 最终决策章节 → ④`source/02` 历史讨论 → ⑤任何未验证旧进度。
- **但**：`docs/PIXEL_WORLD_RESEARCH.md`、`docs/PIXEL_WORLD_ARCHITECTURE.md`、`docs/PIXEL_WORLD_ROUND1~3`、`docs/DECISION_TRAIL_*`、`docs/REVIEW_ROUND4/5` **都不在这条梯子上**。它们是目前离实现最近、日期最新的文档，却只能靠“② brief 已确认”间接压过，规则本身没有说明。
- 影响：任何后续 Agent 遇到“brief 说 A、ROUND3 说 B、ARCH 说 C”时无法机械裁决；`README_CHIEF_START.md:31` 要求“记录冲突、证据和建议，请用户或 Architect 决定”，本报告即在履行该条，但缺少长期规则。
- 另有版本控制缺口：新的两份 pixel 笔记、`DECISION_TRAIL_20261018`、`REVIEW_ROUND4/5` 与整个 `pixel-agent-chief-starter/` 均未跟踪（§0.1 的 `git ls-files` 核验）；clone 后读不到“已确认基线”与最新架构意见。
- 建议裁决问题：D1（是否把 `docs/PIXEL_WORLD_*` 纳入 ADR 体系并补一条优先级规则）。

### B1. 核心冲突：Phaser 3“已确认技术栈” vs “不引成熟引擎、v1 Canvas 2D”

**主张 A（Phaser 3）**
- `PROJECT_BRIEF.md:10`：“当前设计决策为 **Phaser 3 + TypeScript + Vite**。Bevy、Godot 等内容属于历史备选或迁移讨论，不得因此重新启动选型。”
- `source/01:67-76`：§2.2 “**选择 Phaser 3 路线**”，理由含 AI 小镇参考、WebSocket 原生、迭代最快；`source/01:95` 最终技术栈表。
- `source/02:607`：早期讨论中用户原话“Phaser 3的路线了”（历史存档，但属于“用户选择”记录）。
- 代码证据：`package.json:16` `phaser: 3.90.0`；`main.ts:17-33` Phaser.Game；`README_CHIEF_START.md:39` 要求“核验 Phaser 3 + TypeScript + Vite 是否已经是实际技术栈”。

**主张 B（不引引擎 / Canvas 2D v1）**
- `PIXEL_WORLD_RESEARCH.md:163-165`：“**不建议引 Phaser / Pixi。** 场景是静态的…Canvas 2D 手写渲染器 < 500 行就够；引引擎会多 300KB+…还要和现有 three 的 rAF 抢调度。”
- `PIXEL_WORLD_ARCHITECTURE.md:45-51`：“**不引成熟引擎。** … v1：Canvas 2D + 自研 world runtime… v2：渲染后端可换 Pixi… Phaser 留作‘如果三周内必须有个能跑的东西’的快速路”（:39,50）。
- 两份文内日期：`RESEARCH:3` 与 `ARCH:3` 均 2026-10-18，早于 `ROUND1`（:3，10-18 晚）与 `PROJECT_BRIEF` 入盘（10-19 12:26）；`ARCH:3` 明说“只谈架构与取舍，未改代码”，`ARCH:43` 明说“我的建议”。

**冲突原因线索（有证据）**
1. `ARCH:30-33` 的四个硬约束里，第一条是“**没有构建步骤**：`web/` 原生 ES module + importmap … 引入引擎不能顺手把 Vite 带进来”，第三条是“真相源在 Python”，第四条是“角色是 3D 渲 2D”。这些前提是“像素世界嵌进现有 `web/`”。
2. 实际路线把原型放进独立目录：`ROUND1:7-14`——工单要求 Vite + Phaser 3；仓库已有 `pixel-world/` 空壳（`types.ts`、`grid.ts`），“说明这条路线此前已经选定，本轮是接着做，不是重开”；并决定“原型留在 `pixel-world/`，`web/` 一行不动”。**于是 `ARCH` 第一条约束不再成立**（独立目录可以有自己的 Vite）。
3. `ARCH` 的第二条（文字归 DOM）被部分遵守（状态行/报纸/按钮在 DOM），但被 canvas 中文破坏（`villager.ts:32-47` 等，见 A-19/C1）；第三条在 `ROUND3` 用“只读投影 + Vite 代理”落地；第四条（像素化 3D，`ARCH:176` 称其为“最关键的 E2 实验”）**仍未做**（`README.md:112-113` 列为下一步）。
4. 推断：两份笔记是“嵌入现有 web/ 的产品方案”下的技术建议；实际走的是“独立原型优先验证世界运行时”的路线。二者目标不同，因此结论不必然矛盾，但**没有任何文档记录这次转向**（无 ADR；`docs/DECISIONS.md`、`docs/REPO_RECON.md` 均不存在，`README_CHIEF_START.md:47-51` 曾要求产出）。

**按现有规则的裁决**（Arch 判断，非用户决定）
- 依据 `README_CHIEF_START.md:26`（brief 已确认优先）与 `:27`（02 存档最低），以及代码事实（已安装 3.90.0、已 3 次提交），**当前有效技术栈是 Phaser 3**；`PIXEL_WORLD_ARCHITECTURE.md`/`RESEARCH.md` 应标为“历史提案/未采纳（其前提已被独立目录路线绕开）”。
- 但这是“按规则推断”，不是用户书面确认；且 README 规则未覆盖 pixel 文档（B0），故仍需 D1 裁决。

**建议的裁决问题（D1）**
1. 是否正式确认 Phaser 3 为样品 1.0 与后续产品的渲染栈？
2. 是否把 `PIXEL_WORLD_ARCHITECTURE.md`/`RESEARCH.md` 的 Canvas 2D/Pixi 结论标记为“历史提案”，并补一份 ADR 说明“独立原型 + Phaser”的转向理由？
3. 若未来想回到 Canvas 2D/Pixi，是否认可“`src/world/` 可原样复用、只需重写 `src/render/` 与 `src/scenes/`”的迁移边界？

### B2. MVP 1.0 必须项 vs 实际实现

出处：`PROJECT_BRIEF.md:22-32`（必须包含）、`:38-44`（验收目标）；对照代码。

| MVP 1.0 必须项（brief 行） | 实际 | 证据 |
|---|---|---|
| 一张固定预制地图（:24） | DONE | `setup.ts:18-31` 写死分区；`world.test.ts:104-109` |
| 2–3 个小人（:25） | **MISSING**（只有 1 个） | `simulation.ts:108` 单 `villager`；`README.md:107`“多角色…只有一个她” |
| coding/research/生活 三工坊，各 Idle/Operating（:26） | PARTIAL：三栋接活铺子存在（code/research/writing），但“生活”靠 `writing` 映射、`living` 分区空置；状态从 2 种扩到 4 种，其中 `overheating` 不可达 | `prefabs.json:2-43`（`code_forge/star_archive/ink_house`）；`setup.ts:28` 有 `living` 分区但无蓝图使用；`types.ts:123-142` 四态；`tasks.ts:27-30` `shouldOverheat()` 无调用者（grep） |
| 高质量 Mock 神谕路由；LLM 可选；核心可离线降级（:27） | PARTIAL：只有关键词规则路由，无 LLM 路径/降级开关；离线=唯一模式 | `oracle.ts:1-5`（自述“这是世界自己的意图路由器，不是 LLM”）、`oracle.ts:15-51`；grep 无 LLM/fetch 调用（`ledger.ts` 仅台账 GET） |
| 晴/雨 + 躲雨（:28） | DONE | `weather.ts:30-46`；`simulation.ts:384-412`；`routines.ts:47-61`；`smoke.mjs:200-209`（已知实测 49/49） |
| 模板式 DOM 日报（:29） | DONE | `report.ts:17-58`；`oracle-panel.ts:61-72`；`index.html:150-157` |
| JSON 存档，单存档槽（:30） | **MISSING** | 全 `src/` grep `存档|save|load|serialize|localStorage` 无实现；`README.md:101-108`“这一版没有做的”也未提存档 |
| 上帝调试面板：实体列表 + 事件流（:31） | **MISSING**（数据有、面板无） | 事件流存在（`types.ts:198`、`simulation.ts:800-803`，快照只给最近 12 条 `:230`）；`window.Oracle.view()` 暴露快照（`SandboxScene.ts:250`），但 `index.html` 无任何调试面板 DOM；grep `debug` 无命中 |
| 文本“用户下指令”入口（:32） | DONE | `index.html:130-148`；`ui/oracle-panel.ts:108-136` |

**超出 MVP 1.0 范围但已实现**（与 `PROJECT_BRIEF.md:34-36`、`source/01:595-603` 的“不做/简化”清单对照）：

| 已实现项 | 文档原意 | 证据 |
|---|---|---|
| 自动铺路 `paveFrom` + 施工动画 | brief:36 “暂不包含自动修路”；source/01:595 “不做自动修路” | `setup.ts:117-130`；`simulation.ts:515-537,767-778`；`ROUND2:13` 自述本轮完成 |
| 长短任务分级（quick vs workshop） | source/01:597 “不做长短任务分级” | `tasks.ts:9-14`；`simulation.ts:414-434` |
| 工坊四态（含 Overheating/Cooling） | source/01:597 “不做 Overheating”；brief:26 只要 Idle/Operating | `types.ts:123-142`；`buildings.ts:138-169`；`ROUND2:32-34` 解释“故障态有、触发恒假” |
| 程序化拟音（哇啦哇啦） | brief 未列；属 Phase 6（source/01:720） | `audio/animalese.ts:37-85`；`SandboxScene.ts:292-299` |
| 信仰/神恩摆件（累积改变） | source/01:605 “经济只保留数据结构与占位接口” | `faith.ts:10-41`；`simulation.ts:755-765`；`ROUND2:29-31` |
| 真实台账接入（只读投影） | 不在 MVP 1.0 列表；属产品目标 | `ledger.ts`、`projection.ts`；`ROUND3:8-21` |

**验收目标对照**（`PROJECT_BRIEF.md:38-44`）：
- :40 验收 1（3 秒识别）——DONE（`smoke.mjs:110-161` 的屏幕断言）；但“3 秒内能指出”这条属于人因判断，未做计时/受试者实验（E16）。
- :41 验收 2（研究任务全链路无卡死）——DONE（`world.test.ts:197-234`、`smoke.mjs:127-161`）。
- :42 验收 3（下雨 + 躲雨 + TTL 恢复）——DONE（`world.test.ts:277-300`；TTL 数值见 B6）。
- :43 验收 4（关闭重开后世界/编年史/账本恢复）——**不可达**：无存档（B2 存档行）；无编年史/资源账本模块（只有 `faith` 计数，`types.ts:200-202`）。
- :44 验收 5（LLM 离线 Mock 跑通）——**只能算“无 LLM 时全链路可跑”**（这是事实，`smoke.mjs` 全离线）；但“Mock 替代 LLM”的降级路径不存在（无 LLM 接入、无 Mock LLM 层）。

### B3. 已确认技术细则 vs 实际代码

| 决策（出处） | 期望 | 实际 | 判定 |
|---|---|---|---|
| Tiled 静态底图（`PROJECT_BRIEF.md:11`；`source/01:97`） | 有 tilemap 资产/加载 | 无 Tiled 文件；地形按 `WorldGrid` 程序化绘制 | 未实现 |
| EasyStar.js 寻路（`PROJECT_BRIEF.md:12`；`source/01:101`） | easystar 依赖 | 自研 A*（`grid.ts:167-214`），无该依赖 | 偏离 |
| 集中 `GameState` + Phaser 事件（`PROJECT_BRIEF.md:14`） | 单例状态 + EventEmitter | `Simulation` 类 + listener Set | 偏离 |
| WS 通信支持重连/心跳/错误处理（`PROJECT_BRIEF.md:14`） | 心跳 + 超时 | `ledger.ts` 有重连（:123-131）与轮询兜底（:58-62），**无心跳、fetch 无超时**；`app.py` 无 ping/pong（grep） | 部分 |
| 伪光照三层（`PROJECT_BRIEF.md:15`；`source/01:107`） | 全局调色 + 发光贴图 + 混合 | 仅雨幕压暗（`render/weather.ts:42`）+ 灯光圈（`decor.ts:80-83`） | 部分 |
| `OracleIntent` 结构化协议（`source/01:313-321`，含 channel/task_type/target_role/urgency/display_summary） | 结构化 JSON + 降级 | `types.ts:206-210` 只有 `dispatch/complete/unknown` 三态，天气单独一支（`oracle.ts:27-29`）；无 channel/urgency/display_summary | 简化偏离 |
| Aseprite 帧标签动画（`source/01:100,218`） | spritesheet 动画 | 全 `Graphics` 色块手绘 + 2px 抖动（`render/villager.ts:68-79`） | 未实现（原型可接受） |

### B4. “世界规格/预算”数字冲突

- `PIXEL_WORLD_ARCHITECTURE.md:47`：“一屏 480×300、30×19 块”。
- 实际：`setup.ts:12-14` `30×20`、480×320；`README.md:93` 480×320；`main.ts:25` 注释写 480×396（三者不一致，代码=320）。
- `ARCH:73` 世界预算：3 区域 / 6 角色 / 每区 8 交互物 / 12–16 色 / 4 天气 / 每角色 4 动画槽；实际：7 分区（`setup.ts:22-30`）、1 角色、2 天气（`types.ts:72`）、7 种 villager 模式（`types.ts:74-81`）。`README.md:93-96` 已按实际写（“7 个分区…1 个角色…四态”），说明 README 与 ARCH 之间也没对齐。
- `README.md:25` “npm test 纯逻辑，59 项” vs `README.md:67` “test/ 45 项”；实际逐文件 `test()` 计数 = 7+14+13+25 = **59**（命令：`Select-String -Pattern '\btest\('` 计数；与 `ROUND3:68` 的 59 一致）。`ROUND2:56` 写 45、`ROUND3:68` 写 59，README:67 是旧数字残留。

### B5. 天气 TTL 三个值

- `source/01:611`：“输入‘下雨’，…10 秒后（TTL）自动转晴。”
- `PIXEL_WORLD_ARCHITECTURE.md:110`：`set_weather` “10–30 分钟自动退场”。
- 实际：`weather.ts:18` `RAIN_DURATION_MS = 45_000`（45 秒）；`PROJECT_BRIEF.md:42` 只写“TTL 后恢复”，无数字。
- 影响：验收标准 3 的“TTL”无法对齐；演示节奏（45s）与产品设想（10–30min）差两个量级。

### B6. 台账集成契约的文档声称 vs 代码

**B6.1 “adapters 提供 strengths” 不成立**
- 文档：`ROUND3:8` 称 `GET /api/executive/adapters` 提供 `agent_id → strengths`，用于把 7 个 Agent 归到三类活。
- 服务端实际：`executive_api.py:118-122` 返回 `{types, agents}`；`agents` 来自 `executive.adapter_bindings()`（`executive.py:247-259`），每条是 `{agent_id, name, **describe()}`；`base.py:93-95` 的 `describe()` 只有 `{adapter, config_keys}`。**没有 `id`，也没有 `strengths`。**
- 世界端实际：`projection.ts:141-150` 的 `parseAgentRow` 要求 `id`（缺失即 throw）；`ledger.ts:159-171` 对每条解析失败 `continue` 静默跳过 → `agents` 恒为空；`kindForAgent(undefined, …)` 只用标题关键词，兜底 `writing`（`projection.ts:105-116`）。
- 命令证据：`Select-String -Pattern 'strengths' server\…` 仅命中 `dispatcher.py:181-182`（选将内部打分），无任何端点暴露。
- 结论：能力映射**静默退化为标题匹配**；`ledger.test.ts:53-64` 只测了纯函数 `kindForAgent`，没测真实载荷。世界不报错，但“按能力归铺子”的产品承诺未落地。

**B6.2 “blocked 优先”只存在于纯函数，未接入运行路径**
- 文档：`ROUND3:59-60`“`blocked` 优先被挑出来（要请示）…同级按 id 稳定排序”；`projection.ts:93-103` 也实现了 `pickActive`。
- 实际：`project()` 返回的 `active`（`projection.ts:90`）**没有消费者**——`ledger.ts:173-185` 只发布 `tasks/finished/connected`；`simulation.ts:345-350` 用 `this.tasks.find(...)` 取**插入顺序第一条**。grep `pickActive` 仅命中 `projection.ts` 自身（命令见 §0.1 同法）。
- 另：`blockedQuestion` 在 `ledger.ts:175-181` 被丢弃，`simulation.ts:310-319` 也不存储 → 世界看不出“她在等人回答”。与“世界不撒谎”（`PIXEL_WORLD_RESEARCH.md:82-83`、`README.md:13`）冲突。

### B7. “完成权在台账”与人工交付的冲突

- 文档：`ROUND3:54-55`“**完成权在台账手里。** 台账说收工她才交付…沙盒的活才由计时器收尾”；`README.md:85` 同义。
- 代码：`simulation.ts:500-513` `completeManually()` 不检查 `task.source`，对台账单直接 `finishActiveTask()` → `status='done'`、`accrueFaith()`、铺子冷却（:729-752）。入口在 `index.html:140`“现实完成：交付”与文本“交付”（`oracle.ts:15`）。
- 测试缺口：`ledger.test.ts` 覆盖了 sync 收工/撤单/离线（:146-205），**没有**“台账运行中按人工交付”的断言。
- 后果：世界显示已交付、信仰+1，而台账仍 running；属于 P0 诚信风险（C4）。`ROUND3` 的纪律条目在代码里只对“自动收尾”成立。

### B8. 流程基线文档缺失（与启动包的“第一次交付”要求冲突）

- `README_CHIEF_START.md:45-51` 要求第一次交付包含 `PROJECT_PROGRESS.md`、`docs/REPO_RECON.md`、`docs/DECISIONS.md`/ADR；`:71` 要求先批准 `DEMO_SCRIPT.md`；`:72` 要求 `ART_SPEC.md`。
- 实际：仓库只有模板 `pixel-agent-chief-starter/docs/DEMO_SCRIPT_TEMPLATE.md`、`ART_SPEC_TEMPLATE.md`；`DEMO_SCRIPT.md`、`ART_SPEC.md`、`REPO_RECON.md`、`DECISIONS.md` 均不存在（glob 证据）。`PROJECT_PROGRESS.md:3` 自述“尚未由当前真实仓库核验”。
- 后果：样品 1.0 的“验收剧本/视觉规范/进度基线”三件套缺位，验收边界只能从 brief 推断（影响 D2/D3）。

---

## C. 架构风险

> 严重度：P0=会导致“世界撒谎/验收失败”，P1=扩展或集成会卡住，P2=维护/回归风险。

### C1. 逻辑/渲染边界有两处实质破坏（P1）
- **渲染读逻辑内部**：`SandboxScene.ts:289` 直接调用 `this.sim.weatherIntensity()`；`WorldSnapshot` 只有 `weather: Weather`（`types.ts:199`），没有强度。渲染层因此依赖 `Simulation` 实例，而不是 `README.md:63` 声称的“`src/render/` 只读 `WorldSnapshot`”。
- **中文画进 canvas**：`villager.ts:32-47`（名字 + 气泡 11px）、`buildings.ts:75-80`（店名）、`decor.ts:101-108`（摆件名 9px）、`SandboxScene.ts:155-162`（祭坛）、`:345-350`（“施工中 N%”）全部是 canvas 文本，违反 `README.md:78-79` 与 `ROUND1:16` 的硬约束。
- 影响：换渲染器/做 DOM 覆盖层时这些文本要重做；小字号中文可读性未验证（E11）。建议：把 `weatherIntensity` 下沉进快照字段，把名牌/气泡移到 DOM 覆盖层或明确放宽约束（D7）。

### C2. 台账投影在运输层丢字段，信息在途中蒸发（P1）
- `projection.ts:84` 产出 `blockedQuestion`、`:82` 产出 `agentName`；`ledger.ts:173-185` 的 `publish()` 只保留 `commissionId/kind/title/agentName`；`simulation.ts:310-319` 建 `TaskRecord` 时只存 `id/kind/summary`。
- 结果：`blocked` 与 `running` 在世界里不可区分（`blocked` 只影响从未被消费的 `pickActive` 排序）；世界没有“请示”表达，与 PROTOCOL §19 六阶段语义（`PROTOCOL.md:331-332`）不对齐。
- 这类“中间层丢字段”正是 `REVIEW_ROUND4_20261018.md:197` 总结的同类问题（“注释/commit 声称有效果与真的有效果三次脱节”），建议把 `LedgerView` 当作契约来测，而不是只测纯函数。

### C3. 派活优先级排序未接线（P1）
- 证据同 B6.2：`pickActive`（`projection.ts:93-103`）无消费者，`simulation.ts:345-350` 取插入序首条。
- 后果：`blocked`/`running`/`auditing` 的优先级只在单测里成立（`ledger.test.ts:79-88`），运行时不成立；多单并发时“先派哪条”不可预期（取决于 REST 返回顺序 + WS 到达顺序）。
- 建议：由 `ledger.ts` 发布 `active`（或排序后的 tasks），`simulation` 只按序取；补一条“运行路径”测试。

### C4. 人工交付绕过台账完成权（P0）
- 证据链：`index.html:140` 按钮 → `oracle-panel.ts:122` → `parseOracle` complete（`oracle.ts:35`）→ `simulation.ts:500-513` 无 source 检查 → `finishActiveTask`（:729-752）写 done + 信仰。
- 与 `README.md:85`、`ROUND3:54` 冲突；无测试覆盖（B7）。
- 后果：演示中按一下“交付”即可让台账单在世界里“完成”，而台账仍在跑；之后 WS 再报 running 时，`sync` 因 `tasks.some(id)` 为真而不会纠正（`simulation.ts:308-309`），世界会长期与真相不一致。
- 建议：`completeManually` 仅对 `source === 'sandbox'` 生效；对台账单返回“完成权在台账”并保持 working。

### C5. 台账通信的可靠性缺口（P1）
- `ledger.ts:54-62`：`start()` 先 `await refresh()`，之后再 `openSocket()` 和 `setInterval`。若首次 REST 请求挂起（`fetch` 无超时，:137-141），`start()` 永不返回 → **WS 与轮询兜底都不会启动**，世界停留在“离线”。
- `ledger.ts:58-62`：WS `readyState===OPEN` 时跳过轮询（:102-104 只检查 readyState）；无心跳（`app.py` grep 无 ping/pong），半开连接期间世界会一直显示“台账在线”。
- 并发刷新：interval 里 `void this.refresh()`（:60）无 in-flight 守卫，慢请求可能并发；后到的旧快照可能覆盖新状态（`absorbCommissions` 整份替换，:143-157）。
- 部署形态：`vite.config.ts:13-16` 只有 `server.proxy`，**没有 `preview.proxy`**；`vite.config.ts:18` 只配 build。生产/预览构建下 `/api`、`/ws` 无同源落点（E3/D9）。
- 影响：真实集成时“大屏演示突然离线/假在线”是最可能的事故形态。

### C6. 外部 Agent 桥只有“形状”，没有“运输”（P1）
- `bridge.ts:1-10` 自述“运输层（WebSocket / postMessage / 终端）在别处”；实际仓库内没有实现。
- 消息集只有 3 种下行 + 4 种上行（`bridge.ts:13-33`），没有工具进度、阻塞提问、取消/重试、心跳；与 `source/01:134,285` 的 `Assigned→Saluting→…` 状态机、`PROTOCOL.md:331-332` 六阶段不对齐。
- `SandboxScene.feed()` 只做 `enqueue`（:213-217）；`Oracle` 上的 `feed` 类型只声明 `task.assigned`（`ui/oracle-panel.ts:13-18`）。真实 Agent 接入前要重做这一层（与 `README_CHIEF_START.md:82`“外部 Agent 通信不得阻塞渲染主循环”不冲突，但“能通”未验证，E1）。

### C7. 运行期状态校验缺失（P1）
- 仅有启动时 `assertWorldInvariants`（`setup.ts:133-158`，在 `buildWorld:71` 调用一次）：检查铺子数量/分区不重叠/门口可达。
- `PIXEL_WORLD_ARCHITECTURE.md:71` 要求“世界快照必须能过一遍校验（槽位不越界、角色不重叠、调色板不超限…）”；实际 `snapshot()`（`simulation.ts:185-235`）与 `update()`（:168-183）都没有校验环节，也没有 `restore()`/回滚。
- 入口校验只有 `parseCommissionRow`（`projection.ts:118-139`，部分字段宽松：`status` 任意字符串、`step_idx` 非数字归 0）与 `parseBlueprintTable`（蓝图边界严格）。外部命令（`feed`/`sync`）没有运行期 schema 库，靠 TypeScript 编译期类型；浏览器里类型不设防。

### C8. “可确定性回放”只有前提，没有回放器（P2）
- 前提成立：逻辑层无内部时钟、无随机（`simulation.ts:1-5,131`；grep 证据：`src/world` 无 `Date.now`/`Math.random`），命令在 `update()` 开头统一结算（:168-183）。
- 但：没有命令日志/存档/回放器（grep 存档无命中），无法跨进程重放；场景层 `accumulator` 对 `delta` 做 `min(delta,250)`、每帧最多 5 步（`SandboxScene.ts:166-176`），卡顿时会**丢弃逻辑时间**，与“固定步长 = 真实时间”不等价。
- 渲染层 `render/weather.ts:30-31,54-55` 用 `Math.random`（只影响视觉，不影响世界事实）——如果未来做“截图级回放”会不一致。
- `ROUND1:27` 的“可确定性回放”目前只能由测试夹具（`world.test.ts:47-63` 固定 `STEP=100`）证明，属于**单元级**成立。

### C9. 验证/门禁覆盖缺口（P1）
- 根目录 `package.json:7-10` 的 `npm test` 不包含 `pixel-world`；`scripts/run_gates.py` 中 grep `pixel` **无任何命中**（命令 §0.1 同法）→ 像素世界的 59 项逻辑断言与 `tsc` 不在主仓库门禁里。
- `check_protocol.py:274-296` 的“第 11 类”只核对 `commission_update` 字符串是否出现在 `PROTOCOL.md`、服务端 `emitted_types()`、`ledger.ts` 三处；**不校验 payload 形状**（`ledger.ts` 里 `'commission_update'` 字面量在 `:120`，所以改名会报警，但字段改坏不会）。
- `pixel-world/package.json:12` 的 `npm test` 是 `node --test`，在本环境因 `spawn EPERM` 不可用（已知实测）→ 实际只能逐文件直跑；`README.md:25` 仍写 `npm test`，新人会踩坑。
- 后果：`pixel-world` 的回归保护依赖 QA 手工逐文件执行；`ROUND3` 的“台账 10/10”也依赖起的 8000 服务（E1）。

### C10. 单角色/单任务吞吐与产品承诺不符（P1）
- 世界同一时刻只接一单（`simulation.ts:414-419` `isFree`、`:541-543`），台账多条时其余排队且**世界不可见**（`ROUND3:82-83` 自述“排队中的单在台账里看得见，世界里看不出来”）。
- 7 个 Agent 的活全归一人（`ROUND3:81`；`README.md:107`），与 `PROJECT_BRIEF.md:25` 的 2–3 小人、`PIXEL_WORLD_RESEARCH.md:29` 的“9 个角色铺子”设想都不一致。
- 影响：真实台账一忙，世界就变成“永远在干一件事、其余排队”，可读性（产品第一价值）下降。

### C11. `/ws` 广播无鉴权/无源校验（P1，安全）
- `app.py:1008-1012`：`await ws.accept()` 后直接加入 `_connections`；`app.py:966-984` 向所有连接广播 `commission_update`（含 `title/agent_id/blocked_question` 等）。
- 这意味着任意本机页面/进程连 `ws://127.0.0.1:8000/ws` 即可订阅督办标题与提问内容；同时世界依赖这个“全量广播”通道，不能简单关掉。
- 世界侧不调用写路由，故服务端写路由的同源校验（`vite.config.ts:11-12` 注释：“origin.netloc 必须等于 host”）未在原型链路验证；原型靠代理绕过。世界目前只读（A-16），但将来做“点铺子验收”（`README.md:116-117`）就必须先定同源/鉴权方案（D9）。

### C12. 文档—代码漂移是机制性问题（P2）
- 本轮核验已发现 6 类漂移：引擎（B1）、MVP 范围（B2）、技术细则（B3）、数字（B4/B5）、集成契约（B6）、完成权（B7）。
- 项目自身也有先例：`REVIEW_ROUND4_20261018.md:197` “注释/commit 声称有效果与真的有效果之间已经三次脱节”；`DECISION_TRAIL_20261018.md:49-53` “文档说完成、实际没做的两件”。根因是“文档声称”没有被机械检查（C9）。
- 建议：把“文档里的每条完成声明必须附命令/文件证据”写进模板；`docs/TESTING.md` 与 `run_gates` 增加 pixel-world 门（D8）。

---

## D. 需要用户裁决的事项

> 每条给出选项与影响；Arch 的推荐不等于决定。

**D1. 技术栈裁决（Phaser 3 vs Canvas 2D/Pixi）**
- 选项：
  - A（推荐）：确认 Phaser 3 为样品 1.0 + 近期正式栈；把 `PIXEL_WORLD_ARCHITECTURE.md`/`RESEARCH.md` 的“不引引擎”标为历史提案，补 ADR 记录“独立原型 + Phaser”的转向。
  - B：改回 Canvas 2D/Pixi v1：`src/world/` 可复用，但需重写 `src/render/`+`src/scenes/`，工期与 `ARCH:47` 的“<500 行”估算无直接可比性（当前渲染层已有 7 个模块、687 行；命令：`Get-ChildItem src/render/*.ts` 行数求和）。
  - C：暂不裁决，只承认“原型用 Phaser、产品渲染后端待定”。
- 影响：A 最小代价且与代码一致；B 换渲染层，回归要重跑 49 项冒烟；C 会让后续 Agent 继续在 B1 冲突里猜。

**D2. MVP 1.0 范围冻结：先补必须项，还是继续超出项？**
- 选项：
  - A（推荐）：按 `PROJECT_BRIEF.md:22-32` 冻结，优先补 2–3 小人、JSON 存档、调试面板；其余（多角色/3D/真实 Agent 写回）列为 1.1。
  - B：先做“像素化 3D 角色”（`ARCH:176` 的 E2 实验，指架构笔记编号，非本报告 E2 条）——它是美术路线的否决实验，但会推迟 MVP 必须项。
  - C：先做真实 Agent 双向集成（含写回/验收），把世界变成产品的一扇窗（`ROUND3:80-86` 的下一步）。
- 影响：决定 QA 的验收清单（存档/恢复、多角色）与演示剧本（D3）。

**D3. 演示保底与“世界不撒谎”的取舍（人工交付）**
- 选项：
  - A（推荐）：`#finish` 只对沙盒单生效；台账单被拒并提示“完成权在台账”；演示时用 `?ledger=off` 走沙盒闭环。
  - B：保留现状（允许人工结束台账单），但在 UI 明示“本地标记，不改台账”，并接受世界与台账暂时不一致。
  - C：移除人工交付入口，完全依赖计时器/台账。
- 影响：A 修 C4、演示需两段式；B 演示最顺但违反 README:85，真实集成后会造成“假完成”；C 会让离线演示无法人工收尾。

**D4. 派活优先级语义**
- 选项：A（推荐）接入 `pickActive`（blocked→running→auditing，同级 id 稳定）；B 保持“台账返回顺序/插入顺序”；C 由服务端排序（REST 返回即优先级）。
- 影响：A 让 blocked 单优先被世界表达（但需同时把 `blocked_question` 传进世界，C2）；B 简单但多单时不可预期；C 需要改服务端接口（跨模块）。

**D5. 天气 TTL 与压暗参数**
- 选项：45 秒（当前，`weather.ts:18`，演示友好）/ 10 秒（`source/01:611` 验收原文）/ 10–30 分钟（`ARCH:110` 产品设想）。
- 影响：验收脚本第 3 条（brief:42）按哪个写；演示节奏与“世界不打扰用户”的体感。

**D6. “生活工坊”映射与 `living` 分区**
- 现状：三栋接活铺子 `code_forge/star_archive/ink_house`（`prefabs.json:2-43`），`ink_house` 在 `agriculture` 分区；`living` 分区（`setup.ts:28`）无蓝图使用。
- 选项：A 认定“抄书斋=生活工坊”（writing），`living` 留作装饰/宿舍；B 新增“生活”蓝图并重排三工坊；C 把 `writing` 改名为 `life` 并迁移分区。
- 影响：`PROJECT_BRIEF.md:26` 的“coding/research/生活”能否宣称 DONE（直接进 task-3 矩阵）。

**D7. 中文进 canvas 的硬约束**
- 选项：A（推荐）维持硬约束，把名字/气泡/摆件名迁移到 DOM 覆盖层（或只在 canvas 保留数字/英文）；B 放宽 `README.md:78-79`，承认小字号中文进 canvas 并补可读性验收；C 只迁气泡（信息量最大），名字保留 canvas。
- 影响：渲染层改造量、可访问性、以及 `ROUND1:16` 的“文字归纸面”承诺。

**D8. pixel-world 是否纳入主仓库门禁**
- 选项：A（推荐）`scripts/run_gates.py` 增加 `pixel_world` 项（逐文件直跑 4 个 test + `tsc --noEmit`），并在 `docs/TESTING.md` 写明本环境不能用 `node --test`；B 只在 CI 加独立 job；C 维持人工 QA。
- 影响：A 把 59 项断言变成回归护栏，也把“npm test 不可用”的环境事实写进文档；B 需要 CI 配置所有权；C 依赖人。

**D9. 生产同源/部署方案**
- 选项：A 主服务静态托管 `pixel-world/dist`（同源，`/api` `/ws` 直接可用）；B Tauri 壳内嵌（`source/01:108` 曾定 Tauri 后期）；C 独立端口 + 主服务加 CORS/鉴权；D 仅作 dev 演示，不做生产。
- 影响：`vite.config.ts:13-16` 的 dev-only 代理是否够；`/ws` 鉴权（C11）与写回同源策略（D3 后续）都绑定这个决定。

**D10. Agent 能力映射修复方向（B6.1）**
- 选项：A 新增只读端点（如 `GET /api/executive/roster` 暴露 `id/name/strengths`），世界改读它；B 在 `adapter_bindings` 里补 `id`（=agent_id）与 `strengths`；C 世界放弃能力映射，只用标题关键词（现状，但要在文档里承认）。
- 影响：A/B 是服务端小改 + 世界解析小改；C 不改码但产品承诺（7 Agent 归三类活）作废。

---

## E. 仍未验证的假设

> 说明：“未验证”= 本报告没有运行证据；原因写在每条末尾。已知实测项（`npm run check`、smoke 49/49）不混入。

| # | 假设 | 为什么未验证 / 如何验证 |
|---|---|---|
| E1 | 真实督办生命周期（不注入帧）能驱动世界：服务端 `create→briefing→running→ready_to_report` 经 WS 推送后，世界按预期派活/交付 | `ROUND3` 的台账 10/10 使用 `pushLedger` 注入帧（`smoke.mjs:72-106`），`ROUND3:75-77` 自述“注入帧不写主服务”；未验证真实写库→广播链路。需 QA 起 8000 + 真实 POST 建单。 |
| E2 | `GET /api/executive/adapters` 真实响应确实不含 `strengths`（B6.1 的结论来自代码阅读，未抓真实 200 响应） | 需 QA 在 LEDGER 模式抓一次网络响应；预期 `{types, agents:[{agent_id,name,adapter,config_keys}]}`。 |
| E3 | 生产/预览构建下 `/api`、`/ws` 可达 | `vite.config.ts` 只有 `server.proxy`（:13-16），无 `preview.proxy`；未部署验证。 |
| E4 | 半开 WS 会被及时检测 | `ledger.ts` 无应用层心跳，`app.py` grep 无 ping/pong；未做断网/休眠实验。 |
| E5 | 逻辑时间与真实时间在卡顿时仍可接受 | `SandboxScene.ts:166` `min(delta,250)` + 每帧最多 5 步会丢弃逻辑时间；未测低帧率场景。 |
| E6 | 确定性回放可跨进程复现 | 无命令日志/存档/回放器（grep 无命中）；只在单测固定步长下成立（C8）。 |
| E7 | 关闭重开恢复世界/编年史/账本 | 功能不存在（B2）；属“未实现”而非“未验证”，列此以免被旧文档误导。 |
| E8 | 2–3 小人、多 Agent 并发 | 未实现（B2/C10）；`ROUND3:81` 承认一人干全部活。 |
| E9 | `overheating`/`cooling` 视觉分支可用 | `overheating` 状态无任何写入点：`tasks.ts:27-30` 的 `shouldOverheat()` grep 无调用者；`cooling` 只在交付后置位（`simulation.ts:736-740`），故障分支未跑过。 |
| E10 | 像素化 3D（E2 实验）美术路线成立 | 未做；`ARCH:176` 称“最关键、能否决方向”，`README.md:112-113` 列为下一步。 |
| E11 | 中文小字在 canvas 的可读性 | 气泡 11px、摆件名 9px（`villager.ts:41`、`decor.ts:103`），`RESEARCH:171` 称像素中文 <12px 不可读；未做可读性验收。 |
| E12 | 可访问性（等价列表视图、`prefers-reduced-motion`） | `RESEARCH:217-218` 列为 P1 对策；代码无等价列表/降级动效（grep 无 `prefers-reduced-motion`）。 |
| E13 | 浏览器/分辨率矩阵 | 只测 `1010×940` viewport（`smoke.mjs:29`）；固定 2x、无 resize（`main.ts:27-31`）；小屏/高 DPI 未验证。 |
| E14 | 台账 `limit=50` 截断不影响世界 | 世界不传 `limit`（`ledger.ts:78` 调 `/api/executive/commissions`），服务端默认 `limit=50`（`executive_api.py:51-53`）；督办 >50 时世界只见前 50，未做数据量实验。 |
| E15 | 主仓库门禁能保护 pixel-world | `run_gates.py` 无 pixel-world（grep 无命中），根 `package.json:7-10` 不含它；`ROUND3:66-73` 的“通过”是人工跑的命令。 |
| E16 | “3 秒内能指出主角/神谕状态/工坊”（`PROJECT_BRIEF.md:40`）成立 | 现有证据只是 `smoke.mjs:110-161` 的程序断言，不是人因计时；未做受试者/眼动实验，也未在不同分辨率下重复。 |

---

## 附录：核验命令与命中摘要（可复现）

```powershell
# 版本与基线
git branch --show-current; git log -1 --format='%h %ad %s' --date=iso
node -v; npm -v
Get-Content pixel-world/node_modules/{phaser,vite,typescript}/package.json | ConvertFrom-Json | % version

# 边界与随机源
Select-String -Path pixel-world/src/world/*.ts -Pattern 'import .*phaser' -CaseSensitive:$false   # 无命中
Select-String -Path pixel-world/src/**/*.ts -Pattern 'Math\.random|Date\.now|localStorage|存档|save|debug' -CaseSensitive:$false
# → world 内无；render/weather.ts:30,31,54,55 有 Math.random；无存档/调试面板

# 未接线符号
Select-String -Path pixel-world/src/**/*.ts,pixel-world/test/*.ts -Pattern 'pickActive|shouldOverheat|randomWalkableNear|weatherIntensity'
# → pickActive 仅 projection.ts:90,94；shouldOverheat 仅定义 tasks.ts:28；weatherIntensity 被 SandboxScene.ts:289 直接调用

# 测试计数（59 = 7+14+13+25）
Get-ChildItem pixel-world/test/*.test.ts | % { "$($_.Name): $((Select-String -Path $_.FullName -Pattern '\btest\(' -AllMatches).Matches.Count)" }

# 门禁覆盖
Select-String -Path scripts/run_gates.py -Pattern 'pixel' -CaseSensitive:$false        # 无命中
Get-Content pixel-world/package.json                                                   # :12 node --test
Select-String -Path server/**/*.py -Pattern 'strengths'                                # 仅 dispatcher.py:181-182
Select-String -Path web/*.js,web/*.html -Pattern 'pixel-world' -CaseSensitive:$false   # 无命中（无产品接线）

# 文档入库状态（逐条）
foreach ($f in @('pixel-agent-chief-starter/docs/PROJECT_BRIEF.md','docs/PIXEL_WORLD_ARCHITECTURE.md','docs/PIXEL_WORLD_RESEARCH.md','docs/PIXEL_WORLD_ROUND1_20261018.md')) {
  if ((git ls-files --error-unmatch $f 2>$null) -and $LASTEXITCODE -eq 0) { "TRACKED   $f" } else { "UNTRACKED $f" }
}
# → PROJECT_BRIEF / ARCHITECTURE / RESEARCH = UNTRACKED；ROUND1 = TRACKED
```

**已知实测（未复跑，引用）**：`npm run check` 通过；`node scripts/smoke.mjs` 49/49 通过（Vite 5188）；`node --test` 在本环境因 spawn EPERM 不可用、逐文件直跑可跑（Lead 共享事实 / QA task-2）。

---

*本报告只新增 `docs/ARCHITECTURE_GAPS.md`，未修改任何源码、配置、测试或既有文档。task-3（完成度矩阵）、task-2（运行时实证）、task-5（集成连通度）与本报告互补；A/B 节的完成度判定以 task-3 的逐项矩阵为准。*
