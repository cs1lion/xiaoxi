# DEMO_SCRIPT.md v1.0 — 三分钟演示剧本（定稿）

> 版本：v1.0  
> 日期：2026-10-20  
> 状态：用户 2026-10-20 验收通过，定稿 v1.0（S4 收口）  
> 依据：`pixel-agent-chief-starter/docs/DEMO_SCRIPT_TEMPLATE.md` 的 6 个时间槽；实现状态以 `pixel-agent-chief-starter/docs/PROJECT_PROGRESS.md` 及当前 `pixel-world/src` 为准（`docs/DEMO_ART_SPEC_TODO.md` 中“缺失”部分已随 S2/S3 落地，详见下文）。  
> 数据来源标注规则：`Mock/离线` = `?ledger=off` 或纯本地仿真；`live 台账/真实 WS` = 接隔离 8001 实例，真实 WebSocket 推送；`两者` = 世界逻辑与画面不依赖台账，但台账连通时任务来源/交付统计走真实通道。  
> 数字来源纪律：正文所有数字（测试数、调色板数、尺寸、时长等）均标注来源命令或文档路径，不手填估算。计时数字来自 `pixel-world/eval/screens/demo-run-{mock,live}.jsonl`，由脚本生成。  
> 演示环境：`pixel-world/` 目录，`npm run dev` 启动在 `http://127.0.0.1:5188/`（Mock 跑）；live 跑自起隔离 8001 + 临时 Vite 5189（见 `docs/DEMO_RUN_EVIDENCE.md`）。  
> 默认路径：`?ledger=off`（离线 Mock 显式标记，不依赖 8000 服务）

---

## 演示前准备

1. 在 `pixel-world/` 下执行 `npm run dev`，等待 Vite 就绪。
2. 浏览器打开 `http://127.0.0.1:5188/?ledger=off`。
3. 视口固定 **1010×940**（与 smoke 一致），浏览器缩放 **100%**，DevTools 关闭以免遮挡。
4. 确认 `#status` 状态行可见，且包含“台账离线 · 离线 Mock”。
5. 手动导出/导入不依赖台账，`?ledger=off` 也能演示。

---

## 时间槽剧本

### 0–15s：世界启动与状态行

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（`?ledger=off`）；live 台账跑时同段可重复，仅状态行从「台账离线 · 离线 Mock」变为「台账在线」。 |
| **计划时长** | **2.0s**（来源：`pixel-world/eval/screens/demo-run-mock.jsonl` seg0-initial 832→2802ms；`demo-run-live.jsonl` seg0-initial 8697→10686ms） |
| **用户动作** | 打开 `?ledger=off`，不操作，等待页面 `load`。 |
| **画面动作** | 画布出现（原生 480×320，CSS 960×640，整数缩放）；三栋铺子（炼码工坊 / 星图书阁 / 抄书斋）与祭坛就位；**莞尔**、**阿雾** 在祭坛边闲逛；状态行显示双角色摘要、天气、台账状态、交付与神恩计数。 |
| **预期可见证据** | 状态行完整可读；画布无 JS 报错。 |
| **解说词** | 「这是莞尔的世界：480×320 原生分辨率，2 倍整数放大，所有像素边缘干净。默认两位角色——莞尔和阿雾——在祭坛边闲逛。状态行这一行就是世界的‘服务端快照’：角色状态、天气、台账连通性、交付计数、神恩点数，全在这里。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-0-initial-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-0-initial-400pct.png`（Mock 400% canvas）；`pixel-world/eval/screens/demo-live-0-initial-100.png`（live 整页）；smoke 断言 `scripts/smoke.mjs:782-784`（开局 `buildings.length === 3`、`villager.mode === 'idle'`）。 |
| **不可观测标注** | “小人在动”是 10Hz 逻辑驱动的日常作息，静态截图无法直接证明运动；smoke 通过 `mode === 'idle'` 与后续打断断言间接覆盖。 |

**验收判定**：状态行出现「台账离线 · 离线 Mock」/「台账在线」，且画布无报错。

---

### 15–60s：派单→到店→干活→交付（委托闭环）

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`：本地完整闭环（行礼→进铺→交付），来源 `demo-run-mock.jsonl` seg1-mission 2805→10670ms = **7.9s**。<br>`live 台账/真实 WS`：任务来源走真实 `/ws`（4 帧 `drafting→briefing→running→blocked`，id `c_a0efde6024e7`），但**交付为 `blocked` 而非完成**（隔离 8001 无 Key，笔录官无执行体），世界只展示 `blocked_question`，不代答。来源 `demo-run-live.jsonl` seg1-mission 10687→13571ms = **2.9s**。 |
| **计划时长** | Mock **7.9s** / live **2.9s**（来源：`pixel-world/eval/screens/demo-run-mock.jsonl`、`demo-run-live.jsonl`） |
| **用户动作** | 在输入框键入「帮我查一下资料」并按回车，或点击按钮「派活：查资料」。 |
| **画面动作** | 1. 莞尔向祭坛行礼（700ms），气泡「吾神降临！」；2. 走向星图书阁（3 格/秒，整数像素路径）；3. 进屋消失，工坊冒白烟、上方出现黄色进度线；4. 约 4.2s 后门口出现气泡「任务交付！」；5. 冷却 0.9s；6. 回到祭坛，状态行更新交付计数。 |
| **预期可见证据** | 行礼→进铺→铺子运转→交付→回祭坛五步都在同一画面内可追踪；状态行出现「在星图书阁干活」→「正在交付」→ 交付计数 +1。 |
| **解说词** | 「神谕只有一句话，但世界按自己的节拍走：行礼是 700ms 的仪式，走路是 3 格每秒的整数坐标，research 活约 4.2 秒。注意——画面不画中文，名字和气泡全是 DOM overlay，跟 canvas 像素完美对齐。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-1-mission-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-1-mission-400pct.png`（Mock 400%）；`pixel-world/eval/screens/demo-live-1-toWork-100.png`（live 赶路）；`pixel-world/eval/screens/demo-live-1-working-100.png`（live 干活）；smoke `scripts/smoke.mjs:208-453`（research 全闭环断言）。 |
| **节奏说明** | Mock 实测闭环 **7.9s**（`demo-run-mock.jsonl` seg1）；live 实测 **2.9s**（`demo-run-live.jsonl` seg1），但 live 因隔离 8001 无 Key，任务走到 `blocked` 而非交付完成（证据 §3.2：真实 WS 帧 `drafting→briefing→running→blocked`）。若时间充裕，Mock 跑可接着派一单「顺手看一眼」（quick，900ms）让阿雾也动起来，展示双角色独立队列。 |

**验收判定**：
- Mock：7.9s 内看到行礼→进铺→铺子冒烟→交付→状态行「已交付 1 单」；`#reply` 无红字。
- live：2.9s 内看到 `台账在线`、状态行出现 `blocked_question`、角色赶往铺子；**不验收「交付完成」**（隔离 8001 无 Key 导致 blocked）。

---

### 60–90s：神谕日报

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（日报内容完全由本地快照推导，不依赖台账）。 |
| **计划时长** | Mock **1.2s**（`demo-run-mock.jsonl` seg2-paper 10672→11881ms）/ live **1.1s**（`demo-run-live.jsonl` seg2-paper 13574→14670ms） |
| **用户动作** | 向下滚动到纸面区域（`#paper`），或口头指出纸面在控制区下方。 |
| **画面动作** | 纸面显示《神谕日报》：报头「神谕日报」、日期「第 X 分 YY 秒」（世界运行时长，非日历日期）、标题「今日有交付」或「世界刚醒来」、正文最多 3 行（按角色分账的交付数、新楼、摆件、天气+角色作息）。 |
| **预期可见证据** | 纸面标题与 `Oracle.report().headline` 一致；正文中可见「莞尔」「阿雾」等名字；出现「交付」「神恩」或「路灯」等关键词。 |
| **解说词** | 「日报只写真发生过的事，没发生的不写。日期是世界运行的 runtime，不是日历——这是有意设计，因为世界没有昼夜节。纸面是纯 DOM，衬线字体，不进 canvas，所以再放大也是清晰的。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-2-paper-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-2-paper-400pct.png`（Mock 400%）；`pixel-world/eval/screens/demo-live-2-paper-100.png`（live 整页）；smoke `scripts/smoke.mjs:444-449`（按角色分账断言）；`scripts/smoke.mjs:504-509`（纸面渲染断言）。 |
| **缺失证据标注** | 旧版 `07-paper.png` / `07-paper-only.png` 标题与当前 milestone 语义不符，已过期，不作为验收证据。当前 `pixel-world/eval/screens/` 内**无 `demo-3-paper.png`**（剧本保留此文件名作为待补拍项，但证据已由 `demo-mock-2-paper-*.png` / `demo-live-2-paper-*.png` 覆盖）。 |

**验收判定**：纸面标题可被口播复述；正文至少包含一条交付记录；字号≥11px，无横向溢出。

---

### 90–125s：雨天

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（天气系统与角色躲雨逻辑均为本地仿真，不依赖台账）。 |
| **计划时长** | Mock **2.8s**（`demo-run-mock.jsonl` seg3-rain 11882→14715ms）/ live **3.0s**（`demo-run-live.jsonl` seg3-rain 14672→17712ms） |
| **用户动作** | 点击按钮「世界法则：下雨」。 |
| **画面动作** | 1. 雨幕 1.8s 淡入，全屏压暗（alpha ≤0.28）；2. 莞尔/阿雾撑伞，跑向最近屋檐；3. 状态行变为「跑到屋檐下躲雨」、天气字段「雨」；4. 45s 后自动放晴，或点击「世界法则：放晴」立即切晴。 |
| **预期可见证据** | 雨滴+ splash 粒子可见；角色轮廓在暗背景下仍可辨；状态行与画面天气一致。 |
| **解说词** | 「天气是 0..1 渐变，1.8 秒淡入，45 秒后自动退场。雨幕和压暗层在世界标签之上，但 DOM overlay 的名字和气泡仍然在最上层可读——这就是中文不进 canvas 的好处。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-3-rain-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-3-rain-400pct.png`（Mock 400%）；`pixel-world/eval/screens/demo-live-3-rain-100.png`（live 整页）；`pixel-world/eval/screens/04-rain.png`（布局同源）；smoke `scripts/smoke.mjs:470-479`（下雨→撑伞→躲雨→放晴断言）。 |
| **实测备注** | live 跑时雨态 165ms 到达（`demo-run-live.jsonl`）；Mock 跑时 151ms 到达。 |

**验收判定**：点击下雨后 2 秒内看到雨幕与撑伞动作；状态行显示「雨」；切晴后 2 秒内旧雨文案消失。

---

### 125–155s：存档与刷新恢复

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（存档/读档/导出/导入均为本地 localStorage 操作，不依赖台账）。 |
| **计划时长** | Mock **3.3s**（`demo-run-mock.jsonl` seg4-save 14717→17995ms）+ **2.9s**（seg5-export-import 17996→20913ms）= **6.2s**；<br>live **3.4s**（`demo-run-live.jsonl` seg4-save 17720→21132ms）+ **2.7s**（seg5-export-import 21137→23842ms）= **6.1s** |
| **用户动作** | 点击「存档：写入单槽」，再点击「读档：刷新恢复」（或直接 F5）。 |
| **画面动作** | 1. 点击存档后，状态行短暂出现「存档已写入」；2. 页面刷新，世界从 localStorage 单槽恢复；3. 建筑数量、道路、神恩里程碑、按角色交付统计恢复至存档时状态；4. 状态行显示「读档未对账 · 台账离线 · 离线 Mock」；5. 台账自动重连后「读档未对账」消失。 |
| **预期可见证据** | 刷新前后建筑数、道路数、神恩点数一致；`localStorage` 键 `xiaoxi.pixel-world.save.v1` 存在且为 v1 JSON；状态行明确标注未对账。 |
| **解说词** | 「存档是 v1 单槽 JSON，序列化世界自有事实，不存逻辑运行时。刷新后按 prefab 表重解颜色，支持跨版本读取。注意：读档后首个 sync 前，台账单只读展示，不驱动世界——这是对账保护。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-4-save-restore-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-4-save-restore-400pct.png`（Mock 400%）；`pixel-world/eval/screens/demo-live-4-save-restore-100.png`（live 整页）；`pixel-world/eval/screens/demo-mock-5-export-import-100.png`（Mock 导出/导入）；`pixel-world/eval/screens/demo-live-5-export-import-100.png`（live 导出/导入）；smoke `scripts/smoke.mjs:726-755`（保存→刷新→恢复→清档全链路断言）；`pixel-world/src/world/save.ts`（v1 schema）、`pixel-world/src/ui/save-store.ts`（localStorage I/O）。 |

**验收判定**：存档写入成功；刷新后 `buildings.length`、`roads.length`、`faith.milestones`、`deliveriesByVillager` 与存档前一致；状态行出现「读档未对账」。

#### 125–155s 可选：手动导出/导入（task-22）

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（导出/导入均为本地文件与 localStorage 操作，不依赖台账）。 |
| **计划时长** | Mock **2.9s**（`demo-run-mock.jsonl` seg5-export-import 17996→20913ms）；<br>live **2.7s**（`demo-run-live.jsonl` seg5-export-import 21137→23842ms） |
| **用户动作** | 先点击「存档：导出 JSON」；再点击「存档：导入 JSON」，选择刚导出的文件；页面自动刷新。 |
| **画面动作** | 1. 点击导出后，浏览器下载 `xiaoxi-pixel-world-save-YYYYMMDD-HHmmss.json`；2. 点击导入 → 选文件 → 状态行显示「已导入存档… 正在按导入存档重建世界…」；3. 约 400ms 后页面自动刷新，世界按导入档恢复；4. 状态行显示「读档未对账 · 台账离线 · 离线 Mock」。 |
| **预期可见证据** | 导出文件名带本地时间戳；导入后刷新恢复；localStorage 单槽内容与导入文件一致；状态行出现「读档未对账」。 |
| **解说词** | 「这是 v0.2 新增的手动档。导出就是单槽原文，不做二次加工；导入先走和读档同一套校验链，再写单槽、停自动存档、刷新重建。文件名里带本地时间戳，方便你同一目录留多份。」 |
| **证据来源** | UI 按钮文案见 `pixel-world/index.html` 中 `#save-export` / `#save-import`；导出文件名格式见 `pixel-world/src/ui/save-store.ts` 中 `exportFileName`；导入/刷新接线见 `pixel-world/src/main.ts` 中 `saveImport` / `saveImportFile` / `window.location.reload`。 |

**验收判定**：导出文件名为 `xiaoxi-pixel-world-save-YYYYMMDD-HHmmss.json`；导入后刷新恢复；状态行出现「读档未对账」；`pageerror` 为空。

---

### 155–180s：调试面板（可选）

| 项目 | 内容 |
|---|---|
| **数据来源** | `Mock/离线`（面板数据来自本地只读快照与事件缓存；注入按钮只进沙盒队列，不写台账）。 |
| **计划时长** | Mock **1.6s**（`demo-run-mock.jsonl` seg6-debug 20914→22486ms）；<br>live **1.7s**（`demo-run-live.jsonl` seg6-debug 23850→25570ms） |
| **用户动作** | 点击右上角「⚙ 调试」按钮（或按 D 键）。 |
| **画面动作** | 调试面板从右上角展开（固定定位，360px 宽，390px 视口下不溢出）：1. 角色区列出莞尔/阿雾的 id、mode、taskId；2. 铺子区列出炼码工坊/星图书阁/抄书斋的 activity 与当前任务；3. 任务区列出排队/进行中/已完成；4. 事件流显示最近 50 条（默认全部，可按 tone 过滤：oracle/work/done/info/warn）；5. 连接与存档区显示 schema 版本、保存时间、自动存档状态；6. 注入区（默认折叠）提供「仅调试，不写台账」的 feed / pushLedger 按钮。 |
| **预期可见证据** | 面板实体数量与 `Oracle.view()` 快照一致；暂停后面板冻结；过滤后面板只显示选中 tone；注入按钮可用且 feed 进世界队列。 |
| **解说词** | 「调试面板是纯只读 DOM，4Hz 刷新，不参与 10Hz 逻辑。你可以暂停世界、过滤事件、甚至注入一条外部任务——但注入只进沙盒队列，不写台账，适合演示 Agent 集成。」 |
| **证据来源** | `pixel-world/eval/screens/demo-mock-6-debug-100.png`（Mock 整页）；`pixel-world/eval/screens/demo-mock-6-debug-400pct.png`（Mock 400%）；`pixel-world/eval/screens/demo-live-6-debug-100.png`（live 整页）；`pixel-world/eval/screens/07-debug-panel.png`（展开全貌）；`pixel-world/eval/screens/07-debug-panel-filtered.png`（tone 过滤）；`pixel-world/eval/screens/07-debug-panel-1s.png`（注入区展开）；smoke `scripts/smoke.mjs:577-724`（面板开关、数量一致性、暂停、过滤、注入、窄屏溢出断言）。 |

**验收判定**：面板默认收起；点击后展开；实体行数与快照一致；暂停后内容冻结；过滤后只显示选中 tone；注入区按钮可点击且 feed 进队列。

---

## 3 分钟结束验收清单

> 供用户与 QA 勾选。全绿才算剧本通过。

### 基础可见性

- [ ] **0–15s** 打开 `?ledger=off` 无 `pageerror`；画布显示三栋铺子与两位角色；状态行包含「台账离线 · 离线 Mock」。
- [ ] **0–15s** 状态行可复述出至少：莞尔状态、天气、台账状态、神恩点数。
- [ ] **15–60s** Mock 跑：输入「帮我查一下资料」后 **7.9s** 内完成「行礼 → 进铺 → 铺子运转 → 交付 → 回祭坛」，且 `#reply` 无红字。（来源：`demo-run-mock.jsonl` seg1 2805→10670ms）
- [ ] **15–60s** live 跑：输入神谕后 **2.9s** 内看到 `台账在线`、角色赶往铺子、状态行出现 `blocked_question`；**不验收「交付完成」**（隔离 8001 无 Key，任务为 `blocked`）。（来源：`demo-run-live.jsonl` seg1 10687→13571ms）
  - **live 派单文本用 `去把便笺整理成三版标题` 原文**；同义改写（如「帮我把…」）会静默不建单，属已知限制（见「已知限制」表最后一行），不代表故障。
- [ ] **60–90s** 滚动到纸面可见；标题与 `window.Oracle.report().headline` 一致；正文可见「莞尔」「阿雾」或「交付」「神恩」。
- [ ] **90–125s** 点击「下雨」后 2 秒内画面出现雨幕；角色撑伞；状态行显示「雨」。
- [ ] **125–155s** 点击「存档：写入单槽」后 localStorage 出现 `xiaoxi.pixel-world.save.v1`；刷新后建筑数、道路数、神恩里程碑、交付统计恢复；状态行出现「读档未对账」。
- [ ] **155–180s** 点击「⚙ 调试」后面板展开；角色/铺子/任务/事件行数与 `Oracle.view()` 一致；暂停后面板冻结。

### 技术验收（对照 smoke）

- [ ] 以 `?ledger=off` 跑 `node scripts/smoke.mjs` 全绿（114/114）。
- [ ] 以 `LEDGER=1 node scripts/smoke.mjs` 跑台账模式全绿（21/21）。
- [ ] `python scripts/check_protocol.py` PASS。
- [ ] `python scripts/test_core.py` 64/0。
- [ ] `npm test` 全绿（221/221，2026-10-21 复跑）。
- [ ] `npm run test:inprocess` 全绿（受限环境替代，覆盖核心逻辑与门禁）。

> 注：smoke 需真实浏览器（Playwright / Chromium）；`test:inprocess` 在无浏览器或沙箱受限环境下作为替代。

### 证据链

- [ ] 每段都有至少一张当前截图或 smoke 断言可对应。
- [ ] 旧版 `07-paper.png` / `07-paper-only.png` 未作为验收证据。
- [ ] 若补拍新截图，归档至 `pixel-world/eval/screens/` 并更新本剧本证据列。

---

## 在线 / 离线双路径说明

| 模式 | URL | 状态行显示 | 适用场景 |
|---|---|---|---|
| **离线 Mock（默认）** | `?ledger=off` | `台账离线 · 离线 Mock` | 演示、回归、无 8000 服务时 |
| **台账在线** | `?ledger=on` 或默认（若 8000 可达） | `台账在线` | 需验证真实督办、WS 推送时 |

两种模式下，世界逻辑、动画、天气、存档、调试面板均正常工作。区别仅在于任务来源与交付统计归属（台账交付 vs Mock 交付）。

---

## 证据资产复用与补拍计划

### S4 实跑截图（`pixel-world/eval/screens/`，共 34 张，gitignored）

> 来源：`docs/DEMO_RUN_EVIDENCE.md` §5。每段两张：`-100.png`（100% 整页，含页头/DOM）、`-400pct.png`（400% canvas，隐藏 DOM，仅画布像素）。

| 模式 | 时间槽 | 100% 整页 | 400% canvas |
|---|---|---|---|
| Mock | 0–15s 初始 | `demo-mock-0-initial-100.png` | `demo-mock-0-initial-400pct.png` |
| Mock | 15–60s 委托闭环 | `demo-mock-1-mission-salute-100.png`<br>`demo-mock-1-mission-working-100.png`<br>`demo-mock-1-mission-delivered-100.png` | `…-400pct.png`（对应三张） |
| Mock | 60–90s 日报 | `demo-mock-2-paper-100.png` | `demo-mock-2-paper-400pct.png` |
| Mock | 90–125s 雨天 | `demo-mock-3-rain-100.png` | `demo-mock-3-rain-400pct.png` |
| Mock | 125–155s 存档恢复 | `demo-mock-4-save-restore-100.png` | `demo-mock-4-save-restore-400pct.png` |
| Mock | 125–155s 导出/导入 | `demo-mock-5-export-import-100.png` | `demo-mock-5-export-import-400pct.png` |
| Mock | 155–180s 调试面板 | `demo-mock-6-debug-100.png` | `demo-mock-6-debug-400pct.png` |
| Live | 0–15s 初始 | `demo-live-0-initial-100.png` | `demo-live-0-initial-400pct.png` |
| Live | 15–60s 委托闭环 | `demo-live-1-mission-toWork-100.png`<br>`demo-live-1-mission-working-100.png` | `…-400pct.png`（对应两张） |
| Live | 60–90s 日报 | `demo-live-2-paper-100.png` | `demo-live-2-paper-400pct.png` |
| Live | 90–125s 雨天 | `demo-live-3-rain-100.png` | `demo-live-3-rain-400pct.png` |
| Live | 125–155s 存档恢复 | `demo-live-4-save-restore-100.png` | `demo-live-4-save-restore-400pct.png` |
| Live | 125–155s 导出/导入 | `demo-live-5-export-import-100.png` | `demo-live-5-export-import-400pct.png` |
| Live | 155–180s 调试面板 | `demo-live-6-debug-100.png` | `demo-live-6-debug-400pct.png` |

### 可直接复用的旧截图（背景证据，非 S4 新拍）

| 截图文件 | 对应时间槽 | 用途 |
|---|---|---|
| `pixel-world/eval/screens/01-salute.png` | 0–15s、15–60s | 开场布局、状态行、行礼动作（v0.2 证据） |
| `pixel-world/eval/screens/02-working.png` | 15–60s | 进铺干活、冒烟、进度线（v0.2 证据） |
| `pixel-world/eval/screens/03-deliver.png` | 15–60s | 交付气泡、冷却、状态行更新（v0.2 证据） |
| `pixel-world/eval/screens/04-rain.png` | 90–125s | 雨天、撑伞、躲雨、压暗（v0.2 证据） |
| `pixel-world/eval/screens/07-debug-panel.png` | 155–180s | 调试面板展开全貌（v0.2 证据） |
| `pixel-world/eval/screens/07-debug-panel-filtered.png` | 155–180s | tone 过滤（v0.2 证据） |
| `pixel-world/eval/screens/07-debug-panel-1s.png` | 155–180s | 注入区展开（v0.2 证据） |

### 已过期的旧截图（不作为验收证据）

| 截图文件 | 原因 |
|---|---|
| `pixel-world/eval/screens/07-paper.png` | 旧版标题「第 1 次神恩」与当前 v1 milestone 语义不符（`docs/DEMO_ART_SPEC_TODO.md` §1.2）。 |
| `pixel-world/eval/screens/07-paper-only.png` | 同上，已过期。 |

### 需要补拍的新截图

> S4 实跑 34 张已覆盖全部 7 段 × Mock/live × 100%/400%，以下为**剧本保留的待补拍项**（供后续 demo 视频或额外角度使用，非验收必需）。

| 建议文件名 | 对应时间槽 | 拍摄要点 |
|---|---|---|
| `demo-0-initial.png` | 0–15s | 纯首屏，无任何交互，展示画布+三铺+双角色+状态行「台账离线 · 离线 Mock」。 |
| `demo-3-paper.png` | 60–90s | 滚动到纸面后的全页或纸面特写，证明标题/日期/正文与当前代码一致。 |
| `demo-5-save-restore.png` | 125–155s | 点击「读档：刷新恢复」后的首屏，重点拍状态行「读档未对账」与画布恢复的建筑/道路。 |
| `demo-5-export-import.png` | 125–155s | 导出按钮 + 下载文件名 + 导入后刷新状态行。 |

### 可复用的 Smoke 用例（无截图时作为替代证据）

| 用例 | 覆盖范围 |
|---|---|
| `scripts/smoke.mjs:782-784` | 开局三铺+闲逛 |
| `scripts/smoke.mjs:208-453` | 派活→行礼→进铺→干活→交付→日常→短活 |
| `scripts/smoke.mjs:470-479` | 下雨→撑伞→躲雨→放晴 |
| `scripts/smoke.mjs:481-502` | 扩建→施工→落成→神恩 |
| `scripts/smoke.mjs:444-509` | 日报按角色分账+纸面渲染 |
| `scripts/smoke.mjs:526-573` | DOM overlay 100/200/400% 贴合 |
| `scripts/smoke.mjs:726-755` | 存档→刷新恢复→清档→新世界 |
| `scripts/smoke.mjs:577-724` | 调试面板开关/实体数/暂停/过滤/注入/窄屏 |

---

## 干净状态重置步骤

> 目的：每次演示/复验前回到同一初始态，避免旧存档、旧台账状态、旧浏览器缓存干扰。  
> 适用范围：Mock 跑与 live 跑均需执行。  
> 来源：`docs/DEMO_RUN_EVIDENCE.md` §1（命令已实跑，输出 `cleared: http://127.0.0.1:5188 / cleared: http://127.0.0.1:5189`）。

1. **关闭 dev server 与外部 Chrome**
   - 在 `pixel-world/` 目录执行 `Ctrl+C` 终止 `npm run dev`。
   - 关闭所有已打开的 `http://127.0.0.1:5188/` 标签页（Mock 跑）或 `http://127.0.0.1:5189/` 标签页（live 跑）。
   - 确认端口释放：`Get-NetTCPConnection -LocalPort 5188,5189,8001 -State Listen`（Windows）无监听。

2. **清 localStorage 键**
   - 执行 `cd pixel-world && node scripts/demo-run.mjs --reset-only`。
   - 输出应包含 `cleared: http://127.0.0.1:5188` 与 `cleared: http://127.0.0.1:5189`。
   - 若需人工核对：打开 DevTools → Application → Local Storage → 对应 origin → 确认 `xiaoxi.pixel-world.save.v1` 及其 `.corrupt.*` 已删除。

3. **隔离 8001 数据副本重置（仅 live 跑）**
   - 确认操作的是**隔离实例 8001**，不碰 8000（证据：`docs/DEMO_RUN_EVIDENCE.md` §3.4，`commissions.db`/`memory.db` mtime 不变）。
   - live 跑正常结束会自删临时目录（`%TEMP%\aqi-s4-demo-*`）；异常中断时执行：
     ```powershell
     Get-ChildItem $env:TEMP -Filter 'aqi-s4-demo-*' -Directory | Remove-Item -Recurse -Force
     Get-ChildItem $env:TEMP -Filter 'aqi-e2e-real-frame-*' -Directory | Remove-Item -Recurse -Force
     ```
   - 记录重置命令/脚本、时间戳、结果（成功/失败）。

4. **重开页面确认初始态**
   - Mock 跑：`npm run dev` → 打开 `http://127.0.0.1:5188/?ledger=off`。
   - live 跑：执行 `node scripts/demo-run.mjs --mode live`（脚本自起 8001 + 5189）。
   - 状态行应出现「台账离线 · 离线 Mock」（Mock）或「台账在线」（live）。
   - 画布应显示 3 栋铺子、2 位角色（莞尔 + 阿雾）在祭坛边闲逛。
   - `window.Oracle.view().buildings.length === 3`、`window.Oracle.view().faith.milestones === 0`。

---

## 已知限制

> 以下状态已根据 `docs/DEMO_RUN_EVIDENCE.md` §6/§7 更新，并经 QA 独立复验通过（v1.0 定稿）。

| 限制项 | 当前状态 | 证据位置 | 说明 |
|---|---|---|---|
| **live 台账（隔离 8001）** | ✅ **已当场验证** | `docs/DEMO_RUN_EVIDENCE.md` §3.1–3.2；`pixel-world/eval/screens/demo-run-live.jsonl`；`pixel-world/eval/screens/demo-live-*.png` | 隔离 8001 实跑第二遍，真实 WS 推送，`ledger=on` 截图已落盘。 |
| **smoke 需真实浏览器** | ✅ **QA 已独立验证 114/114** | `docs/DEMO_RUN_EVIDENCE.md` §4；`scripts/smoke.mjs`（CDP 连接外部 Chrome）；smoke 输出 **114/114**，exit 0 | 作者侧沙箱禁止 Chromium 自启，用 loader hook 换成 `connectOverCDP` 连外部 Chrome 跑官方脚本（未改项目文件）；**QA 已在自己环境独立复跑 114/114**。 |
| **主服务冷启动（隔离实例）** | ✅ **已当场验证** | `docs/DEMO_RUN_EVIDENCE.md` §3.3；隔离 8001 冷启动日志 | 首次 `bootMs=3174`；停实例后冷启 `bootMs=2999`；health 200、`Application startup complete`、WS 可连。**8000 未重启、未写入**（§3.4）。 |
| **live 服务无 Key 导致 blocked** | ✅ **已当场验证** | `docs/DEMO_RUN_EVIDENCE.md` §3.2、§7.1 | 隔离 8001 无 Key（`health.llm.offline=true`）；真实 WS 帧 `drafting→briefing→running→blocked`（id `c_a0efde6024e7`）；世界展示 `blocked_question`，不代答。**对 8000 实机演示需单独批准**（会写主台账）。 |
| **计时比剧本短** | ✅ **已当场验证** | `docs/DEMO_RUN_EVIDENCE.md` §2/§3；`pixel-world/eval/screens/demo-run-{mock,live}.jsonl` | Mock 整段墙钟 **22.5s**（段落跨度 21.7s）；live 段落跨度 **16.9s**、墙钟 **41.5s**（含两次冷启动）。不凑满 3 分钟。 |
| **派单意图只由正则判定，同义改写静默降级（仅 live 路）** | ✅ **已定稿（双路径已区分）** | `pixel-world/src/world/oracle.ts:23-27`（Mock 路）；`server/dialogue/orchestrator.py:60-65`（live 路）；`docs/DEMO_RUN_EVIDENCE.md` §7.7 | **两条路径不同，不能混为一谈。** ① **Mock 路**（`?ledger=off`）：世界自己的关键词路由器 `TASK_RULES` 里 `writing` 规则含「便笺」「整理」（`oracle.ts:23-27`），所以「帮我把便笺整理成三版标题」被**世界本地接住** → 建 Mock 单 → 正常交付（用户 2026-10-20 实跑确认）。② **live 路**（隔离 8001）：由 server 的 `_DELEGATION` 正则判定（`orchestrator.py:60-65`，第一支要求「去(把\|帮我\|替我)…」开头），「帮我把…」**不命中** → 不建单、无任何提示（静默降级）；live 路世界根本收不到这句话，自然什么都看不到。**要演示这个弱点必须走 live 路，Mock 路看不到。** |

**说明**：
- 除上表外，其余段落（Mock 跑、存档、调试面板、12fps、DOM overlay）均已通过 `test:inprocess` 180/180 与 smoke 逻辑断言覆盖。
- 证据文档已回并已复验：QA 独立复验通过（smoke 114/114、P1 关闭），详见 `docs/DEMO_RUN_EVIDENCE.md`；本剧本据此定稿 v1.0。

---

## 实测计时表

> 数字来自 `pixel-world/eval/screens/demo-run-{mock,live}.jsonl`，由脚本生成，不手填。  
> Mock 整段墙钟 **22.5s**（段落跨度 21.7s）；live 段落跨度 **16.9s**、墙钟 **41.5s**（含两次冷启动）。  
> 来源：`docs/DEMO_RUN_EVIDENCE.md` §2/§3。

| 时间槽 | Mock 实测耗时 | Mock 起止 (tMs) | live 实测耗时 | live 起止 (tMs) | 证据 |
|---|---|---|---|---|---|
| 0–15s 初始 | 2.0s | 832 → 2802 | 2.0s | 8697 → 10686 | `demo-run-mock.jsonl` seg0；`demo-run-live.jsonl` seg0 |
| 15–60s 委托闭环 | 7.9s | 2805 → 10670 | 2.9s | 10687 → 13571 | `demo-run-mock.jsonl` seg1；`demo-run-live.jsonl` seg1 |
| 60–90s 日报 | 1.2s | 10672 → 11881 | 1.1s | 13574 → 14670 | `demo-run-mock.jsonl` seg2；`demo-run-live.jsonl` seg2 |
| 90–125s 雨天 | 2.8s | 11882 → 14715 | 3.0s | 14672 → 17712 | `demo-run-mock.jsonl` seg3；`demo-run-live.jsonl` seg3 |
| 125–155s 存档恢复 | 3.3s | 14717 → 17995 | 3.4s | 17720 → 21132 | `demo-run-mock.jsonl` seg4；`demo-run-live.jsonl` seg4 |
| 125–155s 导出/导入 | 2.9s | 17996 → 20913 | 2.7s | 21137 → 23842 | `demo-run-mock.jsonl` seg5；`demo-run-live.jsonl` seg5 |
| 155–180s 调试面板 | 1.6s | 20914 → 22486 | 1.7s | 23850 → 25570 | `demo-run-mock.jsonl` seg6；`demo-run-live.jsonl` seg6 |
| **段落跨度合计** | **21.7s** | — | **16.9s** | — | — |
| **整段墙钟** | **22.5s** | — | **41.5s** | — | 含 live 两次冷启动（`bootMs=3174` + `bootMs=2999`） |

> 注：live 段落跨度 16.9s 不含前置派单（pre-WS 4.4s，`demo-run-live.jsonl` 3419→7782ms）；该前置在打开世界页面之前执行，属于 live 跑特有前置，不计入演示段落时长。

---

## 数字来源纪律（正文引用）

以下数字均已标注来源命令或文档路径，不手填估算：

- **221/221**：`npm run test:inprocess` 测试总数（`docs/DEMO_SCRIPT.md` 技术验收清单；2026-10-21 复跑；本节数字随测试增长而变，以实跑为准）。
- **24/8/1/31**：调色板审计结果（世界 24 色 / UI 8 色 / 共享 1 色 / 并集 31 色）。世界层 2026-10-21 从 16 扩到 24 色，用户批准。来源 `docs/ADR_PALETTE_24.md`、`docs/ART_PALETTE_AUDIT.md`，复跑 `node scripts/audit-palette.mjs --strict`（exit 0）。
- **114/114**：smoke 沙盒模式断言总数，来源 `scripts/smoke.mjs` 与 `docs/RUNTIME_VERIFICATION.md`。
- **21/21**：smoke 台账模式断言总数，来源 `scripts/smoke.mjs`。
- **64/0**：`python scripts/test_core.py` 通过/失败数，来源 `docs/RUNTIME_VERIFICATION.md`。
- **139/139**：`npm test` 通过数（S2 基线），来源 `docs/PROJECT_PROGRESS.md` 与 `docs/RUNTIME_VERIFICATION.md`。
- **480×320 / 960×640 / 16px / 30×20**：原生分辨率与网格规格，来源 `pixel-world/src/main.ts` 中 `WIDTH`/`HEIGHT`；`pixel-world/src/world/setup.ts` 中 `GRID_WIDTH`/`GRID_HEIGHT`/`TILE_PX`。
- **10Hz / 12fps / 4Hz**：逻辑/动画/面板刷新率，来源 `pixel-world/src/scenes/SandboxScene.ts` 中 `LOGIC_STEP_MS`；`pixel-world/src/render/villager-frames.ts` 中 `animStep`；`pixel-world/src/ui/debug-panel.ts` 中 `DEBUG_REFRESH_MS`。
- **700ms / 3 格/秒 / 4200ms / 900ms / 1400ms / 3200ms / 45s**：仿真常量，来源 `pixel-world/src/world/simulation.ts` 与 `pixel-world/src/world/weather.ts`。
- **2–3 个角色**：默认 2（莞尔 + 阿雾），可配置 3，来源 `pixel-world/src/world/simulation.ts` 中 `DEFAULT_ASSISTANT_NAME` / `villagerNames`。
- **22.5s / 21.7s**：Mock 整段墙钟 / 段落跨度，来源 `pixel-world/eval/screens/demo-run-mock.jsonl` 与 `docs/DEMO_RUN_EVIDENCE.md` §2。
- **41.5s / 16.9s**：live 整段墙钟（含两次冷启动）/ 段落跨度，来源 `pixel-world/eval/screens/demo-run-live.jsonl` 与 `docs/DEMO_RUN_EVIDENCE.md` §3。
- **7.9s / 2.9s**：Mock / live 委托闭环实测耗时，来源 `pixel-world/eval/screens/demo-run-mock.jsonl` seg1 / `demo-run-live.jsonl` seg1。
- **bootMs=3174 / bootMs=2999**：隔离 8001 首次/二次冷启动耗时，来源 `docs/DEMO_RUN_EVIDENCE.md` §3.3。
- **34 张截图**：mock 18 + live 16，来源 `docs/DEMO_RUN_EVIDENCE.md` §5。

---

## 附：关键实现参数速查（讲者备忘）

| 参数 | 当前值 | 来源 |
|---|---|---|
| 原生分辨率 | 480×320（30×20 格 × 16px） | `pixel-world/src/main.ts` 中 `WIDTH`/`HEIGHT`；`pixel-world/src/world/setup.ts` 中 `GRID_WIDTH`/`GRID_HEIGHT`/`TILE_PX` |
| CSS 显示 | 960×640（固定 2 倍，`image-rendering: pixelated`） | `pixel-world/src/main.ts` 中 `scale` 配置 |
| 逻辑节拍 | 10Hz（`LOGIC_STEP_MS = 100`） | `pixel-world/src/scenes/SandboxScene.ts` 中 `LOGIC_STEP_MS` / `update` 循环 |
| 动画采样 | 12fps，idle 2 帧 / walk 4 帧 | `pixel-world/src/render/villager-frames.ts` 中 `animStep` / `IDLE_POSES` / `WALK_POSES` |
| 角色数 | 默认 2（莞尔 + 阿雾），可配置 3 | `pixel-world/src/world/simulation.ts` 中 `DEFAULT_ASSISTANT_NAME` / `villagerNames` |
| 活类型 | code / research / writing / quick | `pixel-world/src/world/tasks.ts` 中 `TaskKind` |
| 雨天 TTL | 45s 自动放晴 | `pixel-world/src/world/weather.ts` 中 `clearAfterMs` |
| 存档 | localStorage 单槽，v1 schema 冻结；task-22 新增导出/导入 | `pixel-world/src/world/save.ts` / `pixel-world/src/ui/save-store.ts` 中 `SaveStore` / `exportFileName` |
| 调试面板 | 固定右上角，4Hz 刷新，只读 | `pixel-world/src/ui/debug-panel.ts` 中 `bindDebugPanel` / `DEBUG_REFRESH_MS` |
| 中文策略 | 动态名字/气泡走 DOM overlay，不进 canvas | `pixel-world/src/render/world-overlay.ts` 中 `createWorldOverlay` / `sync` |
| 调色板 | 世界 24 色 + UI 8 色 + 共享 1 色 = 并集 31 色（2026-10-21 从 16 扩到 24，用户批准） | `docs/ADR_PALETTE_24.md`、`docs/ART_PALETTE_AUDIT.md` |
