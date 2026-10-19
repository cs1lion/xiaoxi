# 下一阶段施工计划与验收标准（NEXT_PHASE_PLAN）

> 2026-10-19 · 基于第一轮基线核验（`docs/REPO_RECON.md` 等 6 份核验文档）。
> **状态：待用户批准。** 批准前不开始大规模编码；不重构个人 Agent 核心、不修改记忆/自进化系统、不更换技术栈。
> 每个代码任务只有一个主负责人；验收一律以「可复现命令 + 可观察现象」为准，不以文档声称或群聊结论为准。

## 0. 进入施工的前置门

| 门 | 内容 | 谁定 |
| --- | --- | --- |
| G0 | 用户确认本计划与 `PROJECT_PROGRESS.md`（已核验版） | 用户 |
| G1 | 拍板 D1–D7（见 `docs/DECISION_TRAIL_20261019.md`），至少 D1/D2/D3 | 用户 |
| G2 | 明确「允许改哪些目录」：默认 `pixel-world/**` + `docs/**`；主产品 `server/**` 的改动（D4-A/B）需单独批准 | 用户 |

**D1 未定时不动的部分**：渲染后端迁移、像素化 3D（E2）。**D2 未定时不动**：新增超出 BRIEF 的世界能力。

---

## 1. 阶段划分总览

```
S0 文档对账与护栏（低风险，可立即做，不依赖决策）
  └─► S1 P0 诚信与集成修复（依赖 D3、D4）
        └─► S2 MVP 硬缺口：多角色 / 存档 / 调试面板（依赖 D2、D6）
              └─► S4 演示与验收（依赖 S1–S3）
S3 视觉垂直切片（与 S2 并行，依赖 D5、D6）
```

| 阶段 | 目标 | 主负责人（单一） | 预估 | 前置 |
| --- | --- | --- | --- | --- |
| S0 | 让文档可信、让像素世界进护栏 | World Builder（文档修复） + Chief（决策记录） | 0.5–1 天 | G0 |
| S1 | 修「世界会撒谎」与「集成交付不了」 | Agent Integration Engineer | 1–2 天 | D3、D4 |
| S2 | 补齐 MVP 1.0 两个硬缺口 + 面板 | World Builder（代码）+ Pixel Art Director（视觉） | 3–5 天 | D2、D6 |
| S3 | 中文出 canvas、调色板、帧动画、证据链 | Pixel Art Director（规范）+ World Builder（实现） | 2–3 天 | D5、D6 |
| S4 | 3 分钟剧本跑通 + 独立复验 + 用户验收 | QA Engineer（复验）+ Chief（主持） | 1–2 天 | S1–S3 全绿 |
| S5（样品 1.0 之后） | 真实 Agent 双向集成（写回/验收/打回） | Agent Integration Engineer | 待评估 | S4 通过 |

---

## 2. S0 — 文档对账与工程护栏（不依赖决策，可先做）

**范围**：只改文档、CI/门禁配置与一行注释；不改运行逻辑。

| # | 任务 | 主负责人 | 产出 | 验收标准 |
| --- | --- | --- | --- | --- |
| S0-1 | 修 `pixel-world/README.md` 内部矛盾：`:25`（59 项）与 `:67`（45 项）；smoke 计数 49/50 口径统一为「以运行输出为准」 | World Builder | README 修订 | 全文数字可由 `npm test` / smoke 输出复现 |
| S0-2 | 修 `pixel-world/src/main.ts:25` 注释「480×396」→「480×320」 | World Builder | 一行注释 | 与 `setup.ts:12-14` 一致 |
| S0-3 | 给 `docs/PIXEL_WORLD_ARCHITECTURE.md`、`PIXEL_WORLD_RESEARCH.md` 加「历史提案，实现已采用 Phaser 3」横幅（D1 批准后） | Architect | 文档横幅 | 读文档不再得出相反结论 |
| S0-4 | `docs/PIXEL_WORLD_ROUND1_20261018.md:22` 补注「后续新增 sheltering，已七态」 | World Builder | 一行注记 | 与 `types.ts:74-81` 一致 |
| S0-5 | 把 `pixel-agent-chief-starter/`、`docs/PIXEL_WORLD_*`、`docs/DECISION_TRAIL_20261018/19` 等基线文档纳入 Git（当前未跟踪） | Chief | 一次只加文档的提交 | `git ls-files` 能查到基线 |
| S0-6 | 按 D7 把 `pixel-world` 纳入 `scripts/run_gates.py` / CI：`tsc --noEmit` + 4 个测试文件 + smoke；`docs/TESTING.md` 写明本环境 `node --test` 行为 | QA Engineer | 门禁项 + 文档 | `.\.venv\Scripts\python.exe scripts\run_gates.py --only pixel_world` 全绿；故意改坏一处能 FAIL |

**S0 不验收**：不要求修 UI、不要求改逻辑；任何行为变化都超出本阶段。

---

## 3. S1 — P0 诚信与集成修复（依赖 D3、D4）

| # | 任务 | 主负责人 | 范围 | 验收标准 |
| --- | --- | --- | --- | --- |
| S1-1 | 人工交付只对沙盒单生效（D3-A）：`simulation.ts:500-513` 增加 `task.source` 检查，台账单拒绝并回执「完成权在台账」 | Agent Integration Engineer | `src/world/simulation.ts` + 测试 | 新单测：台账单 `finish` 被拒且状态不变；沙盒单仍可人工收尾；59+ 全绿 |
| S1-2 | 台账字段契约修复（D4 选定方案）：`adapters`/`roster` 返回 `id`+`strengths`，前端 `projection.ts:141-150` 能解析 | 主产品负责人（需 G2 批准）+ Agent Integration Engineer | `server/executive*` + `src/world/projection.ts` | 真机 `GET` 返回可被前端解析；世界三类活映射非空；契约测试固化字段 |
| S1-3 | `blocked_question` 透传：`ledger.ts:175-181` → `simulation.ts:310-319` 不再丢弃 | Agent Integration Engineer | `ledger.ts`、`simulation.ts`、`projection.ts` | 单测：blocked 单进世界后快照里带问题文本 |
| S1-4 | `pickActive` 接线（D4 语义）：让 blocked→running→auditing 优先级真正生效 | Agent Integration Engineer | `ledger.ts:173-185`、`simulation.ts:345-350` | 多单注入测试：blocked 单优先被铺子接 |
| S1-5 | 台账可靠性：WS 心跳/周期对账 + REST 分页（`limit=50` 不当全量）+ fetch 超时 | Agent Integration Engineer | `src/world/ledger.ts` | 断网/漏推 30s 内状态行转「台账离线」；分页测试覆盖 >50 单 |
| S1-6 | **一次真帧端到端**：主服务开一单 → 世界动起来（不注入帧）→ 台账收工 → 世界交付 | QA Engineer | 测试脚本 + 证据 | 全程录屏/日志 + 断言，写进 `docs/RUNTIME_VERIFICATION.md` 附录 |
| S1-7 | WS 重连指数退避（参考包采纳项 P2）：由固定 4s 改为 500ms 起、×2、上限 5s，连上即重置；与 4s REST 对账并存，互不替代 | Agent Integration Engineer | `src/world/ledger.ts` | 单测覆盖重连间隔序列；断网 30s 内状态行转离线、恢复后转在线；现有 smoke 全绿 |
| S1-8 | `/ws` 同源/Origin 校验（参考包采纳项 P1）：只接受同源 Origin（浏览器页面），脚本/服务端连接按白名单放行；复用 `server/app.py:279-282` 的 same-origin 判断 | Agent Integration Engineer（**需用户单独批准改 server**） | `server/app.py` | 跨源页面连 `/ws` 被拒；同源页面与本地脚本正常；台账 smoke 10/10 不回退 |
| S1-9 | 台账帧契约 fixture 测试（参考包采纳项 P4，缩小版）：录制一份真实 `GET /api/executive/commissions` 与 `commission_update` 帧做 fixture，前端解析与投影断言同源；字段漂移即红 | Agent Integration Engineer | `pixel-world/test/` + fixture 文件 | 故意改 fixture 字段名时测试 FAIL；不改 server 代码 |

**S1 出口门**：`npm test` 全绿（含新增用例）；沙盒 smoke 49/49 不回退；台账模式 ≥10/10；真帧端到端 1 次成功；`npm run check` 通过。

**S1 收口记录（2026-10-19）**：参考包采纳的三张卡已完成并经 QA 独立复验，结论见 `docs/QA_VERIFICATION_S1.md`（480 行）：
- task-7（S1-7 指数退避）：**通过**——69/69 测试、49/49 沙盒、10/10 台账、对抗探针 16/16、真实断网 111ms 转离线 / 589ms 恢复。
- task-9（S1-9 契约 fixture）：**通过**——正常 6/6；`agent_id→agentId` 变异 5 fail / exit 1；类型漂移 1 fail；fixture 无敏感字段、与 8000 实响应键集合一致。
- task-8（S1-8 `/ws` Origin）：**通过**——服务器改动仅 `server/app.py` +24 行、仅 `/ws` 头部；离线 11/11、8001 live 5/5、QA 独立握手矩阵 12/12（无 Origin/同源/跨源 403/https 同 netloc/null 403/畸形 403/大小写/空串/代理）、Vite 代理 10/10、`check_protocol.py` PASS、`test_core.py` 64/0；8000 全程未重启。
- 遗留 P3（不阻断）：无 Origin 白名单不等于认证、只比 netloc、依赖 `changeOrigin:false`、拒绝无速率限制、fixture 的 blocked/running 为派生值。后续卡见 §10。

**S1 第二阶段收口（2026-10-19）**：D3（task-11）与 D4（task-12）完成并经 QA 独立复验通过。
- D3 行为探针 18/18：台账单人工完成被拒、沙盒单可收尾、离线 Mock 在快照/状态行/回执/日报四处显式、日报分账不合并；未写台账、未动 server。
- D4 对抗 8/8：`/roster` 200 / 7 条 / 仅 `id,name,strengths,adapter`；8001 与 8000 的 `/adapters` 均 1478 B、byte-identical；旧服务 404 回退链有效；无凭据字段。

**用户指定的 S1 剩余执行顺序（2026-10-19）**：
1. S1-5 周期对账 + 分页截断 + fetch 超时（task-13）
2. S1-6 真帧端到端 + 三项未验证取证：冷启动（D4）、无 Key Mock（D1）、真实 WS `commission_update` 推送（D3）；必须连真实 WS，禁止 `pushLedger` 注入帧（task-14）
3. S1-4 `pickActive` 接线（task-15）
4. S1-3 `blocked_question` 透传，**仅展示、不在世界内回答**（task-16，World Builder）
- task-10（排队）与 task-15/task-16 共用 `simulation.ts`，按用户规则**排后**，不并行。
- D7 在 S1 收口时定；D5/D6 在 S3 开工前定。像素美术先做中文位图字体试验（含许可证确认）与色数统计，只出证据、不碰代码。

**S1 全部收口（2026-10-19 14:05）**：S1 五张卡 + 参考包三卡全部完成并经 QA 独立复验。
- S1-5 周期对账/分页：30s 对账（WS 在线也做）、limit=100 截断标「未取全」不误撤、5s 超时；QA 对抗 16/16，真实 8000 DB 239 行 → 页面提示可复现。
- S1-6 真帧端到端：隔离 8001 冷启动 2.5s、无 Key `offline:true`、真实 WS 6 帧同 id、真浏览器 `pushLedgerCalls=0`；覆盖 D1/D3/D4。D7 上游派活未验证（manual + 无 Key）。
- S1-4 pickActive：blocked→running→auditing，同级稳定，撤单重排；QA 矩阵 16/16。
- S1-3 blocked_question：真帧→快照→状态行，60 码点截断，无回答入口（全程仅 GET）；QA 逻辑 27/27 + 浏览器 17/17，真帧 e2e 复跑 23/23。
- 全量：`npm test` 90/90、沙盒 smoke 53/53、台账 smoke 21/21、`check_protocol.py` PASS、`test_core.py` 64/0；8000 全程未重启，`data/commissions.db` 未写。

**D7 落地（2026-10-19，commit `b656488`）**：`.github/workflows/pixel-world.yml` 路径触发 CI + `scripts/test_pixel_world.py` 本地独立门禁（`npm run check` + `npm test`，不依赖浏览器/端口），启用前连跑 3 次 PASS（约 3s/次，90/90）。`scripts/run_gates.py` 的接入**暂缓**：该文件含 53 行主产品既有未提交改动，按路径加入一行会混合提交；等 durable 线收尾后接入或由用户批准一次混合提交。浏览器 smoke 按路径触发/本地手跑，不进稳定门禁。

**S2 顺序（用户指定，2026-10-19）**：task-10 任务排队 → task-17 多角色 2–3 → task-20 存档 ADR（一页，先于实现）→ task-18 JSON 存档单槽 + 恢复 → task-19 调试面板。
**S2 进度（2026-10-19）**：task-10 已完成并经 QA 复验（96/96、沙盒 59/59、台账 21/21）；task-17 已完成并经 QA 复验（**100/100、沙盒 66/66、台账 21/21、test_core 64/0**）；task-21（S2-1b P3：日报归属/手动完成对象）已 QA 独立复验通过并提交 `2bde7cb`（106/106、沙盒 74/74、台账 21/21、test_core 64/0）；task-20 ADR 已按用户裁决补齐 8 项核对并对齐 task-21 冻结字段（`docs/ADR_SAVE.md`，仍 proposed；待用户回答 6 个细节后 final 并锁定 §1）；task-18 待 ADR final 后开工。

---

## 4. S2 — MVP 1.0 硬缺口（依赖 D2、D6）

| # | 任务 | 主负责人 | 范围 | 验收标准 |
| --- | --- | --- | --- | --- |
| S2-1 | **多角色 2–3 个小人**：`WorldSnapshot.villager` 单数改角色数组；`Simulation` 内按 id 管理；渲染多实例；例行作息/派活/交付归属到具体角色 | World Builder | `types.ts`、`simulation.ts`、`routines.ts`、`render/villager.ts`、`SandboxScene.ts`、测试 | smoke 新增断言：2 个角色各自有独立 routine；一个在工坊、一个在祭坛；派活只叫一个且不串状态；旧单角色断言改造后全绿 |
| S2-2 | **JSON 存档单槽 + 恢复**：`Simulation` 增 `toSave()/fromSave()`（tasks/props/roads/faith/weather/constructions + 角色位置/路径/计时 + 等待队列；台账链接态不入档）；`#save/#load` 或自动槽。**决策见 `docs/ADR_SAVE.md`（D-SAVE-1）；用户原则批准，task-21 已合并（`2bde7cb`），schema 字段已对齐冻结清单；待用户回答 6 个细节后 final/锁定，adr final + QA 后才开工** | World Builder | `simulation.ts`、`main.ts`、`index.html` 按钮、测试 | 刷新页面后建筑/道路/信仰/任务/角色位置一致（BRIEF 验收 4）；读档先与台账对账、未对账不驱动台账单；损坏/高版本不崩、有可读提示 |
| S2-3 | **上帝调试面板 UI**：基于 `window.Oracle.view()` + `snapshot.events`（最近 50 条，可暂停/过滤），显示实体列表与事件流 | World Builder | `index.html`、新 `src/ui/debug-panel.ts` | 面板能开关；实体/事件实时更新；DOM 面板不遮挡世界，390px 视口不溢出 |
| S2-4 | 生活工坊语义对齐（D6/`ARCHITECTURE_GAPS` D6）：按裁决改 `writing`→生活口径或新增蓝图 | World Builder | `prefabs.json`、`blueprints.ts`、`projection.ts` | 三工坊与 BRIEF 一一对应；不破坏世界不变量 |
| S2-5 | 任务排队 + 取消未出发行礼（参考包采纳项 P3）：忙碌时新单进等待队列而非直接拒绝；任务在出发前结束时取消行礼计时器、清路线；队列在状态行/回执可见 | World Builder | `simulation.ts`、`types.ts`、`oracle-panel.ts` | 单测覆盖「忙碌时排队」「排队单被撤」「未出发即结束」；smoke 新增两条断言；不破坏确定性回放 |

**S2 出口门**：BRIEF 九项至少 7 DONE（多角色、存档、调试面板转 DONE）；BRIEF 验收 1–5 全部可演示或明确标注限制；
`npm run build` 体积增幅 <15%；存档字段有版本号。

---

## 5. S3 — 视觉垂直切片（与 S2 并行，依赖 D5、D6）

| # | 任务 | 主负责人 | 验收标准 |
| --- | --- | --- | --- |
| S3-1 | 世界内中文迁出 canvas（D5-A）：名字/气泡/招牌/祭坛/施工/摆件标签改 DOM overlay 或像素字体位图 | Pixel Art Director（规范）+ World Builder（实现） | `grep "scene.add.text"` 无中文；400% 放大标签可读；状态行/回执/报纸仍在 DOM |
| S3-2 | 调色板收敛到预算（D6）+ 校验器：`palette.ts` 单一来源，渲染层禁止硬编码 `0x` 色值（约 72 色 → 16/24） | Pixel Art Director | 校验脚本能拦住越界色值；截图取色统计 ≤上限 |
| S3-3 | 帧动画与帧率语义：idle/walk 至少 2 帧，渲染 12fps 采样、逻辑仍 10Hz | Pixel Art Director | 动画槽清单落地；`smoke` 截图可见两帧差异 |
| S3-4 | 证据链重建：固定视口/模式/命名，拍 7 段剧本截图 + 纸面 + 400% + 390px；清理过期 `07-paper*` | QA Engineer | 截图清单可复现；文档不再出现「49 张截图」的错误说法 |
| S3-5 | `ART_SPEC.md` v0.1 定稿（用 `DEMO_ART_SPEC_TODO.md` B1–B12） | Pixel Art Director | B1–B12 全部有结论；验收清单 B 全绿 |

---

## 6. S4 — 演示与验收

| # | 任务 | 主负责人 | 验收标准 |
| --- | --- | --- | --- |
| S4-1 | `DEMO_SCRIPT.md` v0.1 定稿（用 `DEMO_ART_SPEC_TODO.md` A1–A12），3 分钟逐段、含离线降级与失败演示 | Chief（主持）+ 美术导演 | 每段有「用户做什么/世界看到什么/证据」；无「不可演」段落 |
| S4-2 | 两模式各跑 3 分钟：`?ledger=off` 纯离线 + 台账在线；全流程无 console 报错 | QA Engineer | 录像/日志 + 时间戳；状态行在线/离线文案正确 |
| S4-3 | 独立复验：另起一人按剧本黑盒跑，不复用作者环境 | QA Engineer | 复验报告写进 `docs/RUNTIME_VERIFICATION.md` |
| S4-4 | 用户验收签字，更新 `PROJECT_PROGRESS.md` 与决策留痕 | Chief | 用户确认 |

**S4 出口门（即样品 1.0 验收）**：
1. 原生分辨率 3 秒内指出主角、神谕状态、正在工作的工坊（人工计时）。
2. 输入研究任务 → 行礼 → 进工坊 → 动画/进度 → 日报，无卡死。
3. 下雨 → 雨幕 → 躲雨 → TTL 恢复（TTL 值按 D6 定）。
4. 关闭重开，世界/编年史/资源恢复（S2-2 后必须可测）。
5. LLM/Mock 离线时全部流程走通。
6. `npm run check`、`npm test`、`npm run build`、smoke 沙盒/台账、`run_gates.py --only pixel_world` 全绿。

---

## 7. 明确不在本计划内（需另行批准）

- 自由生成世界、LLM 造物主生成侧、布局评分器。
- 完整动态 Light2D、昼夜季节、多存档/云同步。
- 像素化 3D（E2）与主产品 VRM 形象合并——D1/D2 未定前不启动。
- 主产品 Agent 核心、记忆、自进化、任务调度核心的任何重构。
- 真实 Agent 写回/验收/打回（S5，样品 1.0 之后）。
- 语音输入、TTS、主动播报（BRIEF 明确不做）。

## 8. 风险与回滚

| 风险 | 触发条件 | 应对 |
| --- | --- | --- |
| 工作区混杂导致误提交 | 有人 `git add -A` | 提交只加 `pixel-world/**` 与本轮 docs；提交前 `git status --porcelain \| Select-String pixel-world` 核对 |
| 主服务改动影响现有产品 | S1-2 动 `server/` | 独立分支 + 只读端点优先；改前跑主产品门禁（`check_protocol.py`、`test_core.py`） |
| 多角色改造回归 | S2-1 改单数模型 | 先改类型再改渲染；每步 `npm test` + smoke；保留单角色开关一个迭代 |
| 存档格式返工 | S2-2 字段定太早 | 存档带 `schemaVersion`；先覆盖当前快照字段，迁移函数后置 |
| 视觉改造拖期 | S3 与 S2 抢同一批文件 | 以文件为界：S3 只动 `render/`、`ui/`、`index.html`；S2 动 `world/`；交叉处由 Chief 排期 |
| 沙箱/环境限制误判为缺陷 | `node --test`、Vite 需子进程 | 统一用全权限环境跑门禁；`docs/TESTING.md` 记录环境差异 |

---

## 9. 参考代码包评估（2026-10-19，只读）

**来源**：用户提供的 Claude 参考包（`files.zip` → `pixel-world.zip`），已解压到临时目录
`C:\Users\18712\.dsh\bot\workspace\ref-pocket\`，**未复制进仓库、未新建工程、未替换现有实现**。

**定位**：Python agent ⇄ WebSocket 中继 ⇄ Phaser 3 的最小骨架，只读参考。

**验证状态：全部按「未验证」处理。** 本轮只做静态阅读：未运行其 Python（本机无该包所需 pydantic/websockets 环境组合验证）、
未对 `WorldScene.ts` 做 Phaser 类型检查、未端到端联调；其自身 README 也声明只有 `grid.ts`、`bridge.ts` 与类型文件过了 tsc。
因此下文「采纳」只表示**做法值得吸收**，不等于该代码已验证或可直接搬用。

| # | 参考做法（文件:行） | 现有实现 | 判定 | 理由 | 去向 |
| --- | --- | --- | --- | --- | --- |
| R1 | 协议单一事实来源：Pydantic → JSON Schema → TS 类型（`protocol/events.py:1-68`、`export_schema.py:17-29`） | `docs/PROTOCOL.md`（65KB）+ `check_protocol.py` 11 类检查 + 前端手写 `ledger/projection` 解析 | **不采纳（当前阶段）** | 主协议事件远多于参考包的 4 种；全量建模 + `json2ts` 属跨主产品核心改动；参考包生成物本身是手写占位、codegen 未经验证；D4 类字段漂移可用契约 fixture 低成本兜住 | 1.1 之后若重构主协议再评估；当前用 R4（S1-9）兜漂移 |
| R2 | 本地中继限制浏览器 Origin（`server/bridge.py:21-26,58`） | 主服务 `/ws` 无 Origin 校验（`ARCHITECTURE_GAPS` C11，P1） | **采纳** | 同源判断现成（`server/app.py:279-282`），防其它网页偷连本地端口，改动小 | **S1-8 / task-8**（需用户单独批准改 server） |
| R3 | 入站先校验；非法事件只回发送者（`bridge.py:41-50`） | 世界对台账只读，目前**没有**浏览器→服务端上行通道 | **不适用 / 待定** | 等 S5 真实双向集成时再采纳 | S5 |
| R4 | 同进程直接 `publish`（`bridge.py:33-35`） | 已有等价实现：`CommissionStore` 订阅 → `app.py` 广播（ROUND3） | **不采纳（已具备）** | 无需重复实现 | — |
| R5 | 小人状态机逐帧在 Scene 里决策（`WorldScene.ts:41-180`） | `simulation.ts` 固定步长 + 命令队列，逻辑/渲染分离 | **不采纳实现；采纳其中 2 个语义** | 逐帧 Scene 状态机会破坏确定性回放与「渲染只读快照」约束；但「任务排队」与「未出发即取消行礼」（`:166`）」是真实缺口 | **S2-5 / task-10** |
| R6 | 逻辑网格/像素坐标分离、BFS、脚底 Y 排序（`grid.ts:1-80`、`WorldScene.ts:102`） | 已有更完整：分区/占用/A*/槽位/不变量；Y-Sort 三处已实现 | **不采纳** | 现有 A* 强于 BFS；参考包 32×24@20px 与现有 30×20@16px 规格冲突；Y-Sort 已具备 | — |
| R7 | 断线指数退避重连（`web/src/bridge.ts:17-44`） | `ledger.ts:123-131` 固定 4s 重连、无退避 | **采纳** | 小改动、直接改善断线恢复；与 4s REST 对账互补，不替代 | **S1-7 / task-7** |
| R8 | 协议版本字段 `v: 1`（`events.py:21`） | `commission_update` 无版本字段 | **待定** | 加版本字段属跨模块契约变更，需主服务配合；先由 R4 fixture 覆盖 | 待定 |
| R9 | `scale: FIT` 自适应缩放（`web/src/main.ts`） | 固定 2 倍整数缩放（`main.ts:27-31`） | **不采纳** | 违反像素完美/整数缩放纪律（`PROJECT_BRIEF.md:9`） | — |
| R10 | `mock_send.py` 事件时序脚本（`:39-51`） | DOM 预设按钮 + smoke 注入帧 | **不采纳** | 演示控制已在 DOM；但其事件时序可作 `DEMO_SCRIPT.md` 的参考素材 | DEMO_SCRIPT 参考 |

**去向汇总**

- **并入 S1（台账契约与映射）**：task-7（指数退避）、task-8（`/ws` Origin，需批准改 server）、task-9（契约 fixture 测试）。
- **放后续**：task-10（S2 任务排队 + 取消未出发行礼）。
- **不采纳**：R1（当前阶段）、R4（已具备）、R6（已更强）、R9、R10。
- **待定**：R1 的 codegen 方向（1.1 后）、R3（S5）、R8（版本字段）。

**任务卡规则**：每个采纳项一张卡、单一主负责人，**QA 复验后才算完成**。
**用户已批准（2026-10-19）**：task-7、task-9、task-8 三张卡开工；其中 task-8 的服务器改动**严格限定**为 `/ws` 的 Origin 校验，
不得触碰 `server/assistant`、记忆、自进化与调度核心。

---

## 10. 审查发现的后续卡（未启动，需用户/排期批准）

| # | 事项 | 证据 | 风险 | 建议 |
| --- | --- | --- | --- | --- |
| B-1 | `/ws/proactive`（`server/app.py:1231-1258`）与 `/ws/interpret*`（`:80-86`）仍无同源校验；跨源页可切 proactivity、触发 TTS/LLM | 首席架构审查（2026-10-19） | P2 安全：与 task-8 同源风险，但涉及语音/主动性链路 | 单开一张卡，把同源校验抽成公共依赖，统一覆盖所有 `/ws*` 端点；需用户批准改 server |
| B-2 | `scripts/test_voice_turns.py` / `test_experience_websocket.py` 在 HEAD 即因隔离装载缺 `_connections` 报 NameError（`app.py:1031`） | 架构 + Agent Integration 实跑确认 | P3 工程：红门禁不能当回归基线，会掩盖真实回归 | 单开一张卡修测试 harness（注入 `_connections=set()`），不动业务代码 |
| B-3 | 无 Origin 请求放行（本地脚本友好）在 DNS rebinding 场景仍有残余风险 | 架构审查 | P3 安全 | 若要更严：加 Host allowlist（127.0.0.1/localhost），列为产品化阶段评估 |
| B-4 | 台账 fixture 当前真实 8000 只有 done 状态，blocked/running 为派生值 | task-9 交付说明 | P3 证据 | 等真实台账出现 blocked/running 单时补录真实 fixture |
