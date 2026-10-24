# OpenUI / Intelligent UI 在小溪的发挥空间（调研报告）

日期 2026-10-22。类型：只读调研（poteto-mode Investigation playbook），交付物是带权衡表的推荐，不是代码变更。

生成方式：multi-agent workflow `wf_03ab97aa30b5453b`。首轮 `wf_e4d0369aa89f47fd` 的五个 phase-A agent 全部被传输错误中断（journal 五项均 failed/aborted，无可复用缓存），整轮重跑。流程为三路本机盘点（web 前端 / pixel-world 等非主前端 / 协议与门禁约束）加两路外部调研（OpenUI 本体 / intelligent UI 全景），四角度机会映射（输出侧、输入侧、快环侧、督办侧），四候选对抗验证，最后一席综合。

主会话复核过的承重事实（2026-10-22，本机）：

- `web/blocks.js` 对 `aria-` 与 `role=` 零命中；`RENDERERS` 七 kind 注册、未知 kind 直接 `return null`。
- `scripts/check_protocol.py:219-222`：`CMD_VERBS = ("ui","say","dispatch","choice")`、`SERVER_CMD_VERBS = ("dispatch",)`、`AQI_BLOCK_KINDS` 七种，均静态纳管。
- `web/app.js:667` 的 `verb === "choice"` 分支只读 `options[index]?.cmd`（673 行），把点击结果作为自由文本重新进 LLM。
- `scripts/check_protocol.py` 复跑 PASS。
- 外部事实：`wandb/openui`（Apache-2.0，W&B，自然语言生成 HTML 再转 React/Svelte/Vue/Web Components，LiteLLM 适配）与 `thesysdev/openui`（MIT，TypeScript，OpenUI Lang 流式优先 DSL，官方称比 JSON 省最多 67% token，口号 "The Open Standard for Generative UI / Intelligent UI"）均真实存在且是两个不同项目。

---

# OpenUI / Intelligent UI 在小溪的发挥空间 —— 最终评审报告

评审日期 2026-10-22 · 依据：调研包（web / world / constraints / openui 四包）+ 本机复跑核验（见文末记录）

## 1. 结论先行

**一句话答案：有，但最值钱的不是「引入 OpenUI」，而是「小溪已经在输出侧实现了 OpenUI 的核心思想，缺的是它的输入侧与渲染质量」；OpenUI 应当作为设计参照与证据来源，不作为依赖引入。**

最值得做的三个点：

1. **输入侧结构化**：把 `choice_pills` 的 option 从 `say:<自由文本>` 升级为确定性意图。这是 OpenUI 动作表达式（`@ToAssistant` / `@Run` 一类）在小溪的唯一本体对应，也是 `docs/DECISION_TRAIL_20261018.md` §二 遗留的最大缺口。需走 `[PROTOCOL_REQUEST]`。
2. **积木可访问性 + Holo-Deck 空态**：纯客户端、零协议改动、当天可完成，直接补上「智能界面」最容易被读屏用户判死刑的一段。
3. **积木动作的可撤销 / 直达入口**：`dispatch:accept` / `dispatch:reassign` 失败只有一行字幕，没有「这单现在到底怎样了」的直达路径；很可能能用既有 `ui:preview` 动词表达，无需新字段。

## 2. 名词对齐

**OpenUI 实际是什么**（调研来源：GitHub API，2026-10-22 快照；两个同名项目必须分开谈）：

- **(A) `wandb/openui`**：2024-03 创建，Apache-2.0，自称「开源版 v0」的**原型工具**。左侧自然语言描述界面（也可贴截图）→ 后端经 LiteLLM 调 LLM 生成 HTML → iframe 实时渲染 → 对话式增量修改 → 导出 React / Svelte / Vue / Web Components。形态是「起一个 web 服务」，不是库。**已实质休眠**：最后一次实质代码提交 2025-05-24，2026-06 两次提交只是 Renovate 给 Actions 做 SHA pinning；零 GitHub Release；官方演示站本机无法连通，因此「现在还能不能跑通」未验证。
- **(B) `thesysdev/openui`**：2024-12 创建，MIT，纯 TS monorepo，口号「The Open Standard for Generative UI / Intelligent UI」，即**生成式 UI 框架**。核心是自研 OpenUI Lang：一门面向行、为流式输出设计的紧凑 DSL（`identifier = Expression`，如 `root = Stack([CardHeader("Q3 revenue","Up 18%"), chart])`）。五步流程：① `defineComponent`（Zod schema）+ `createLibrary` 定义**模型可用的组件白名单**；② CLI 序列化成 spec，再用 `generateSystemPrompt()` 生成系统提示；③ 发给任意 LLM；④ 模型流式吐 OpenUI Lang；⑤ 前端 `<Renderer response={...} library={...} isStreaming />` 边收边渲染。关键设计：**模型只能在白名单组件里拼界面（受控输出）**、Zod 契约保证类型安全、官方基准称比 JSON 方案省 45%–67% token。输入侧也不是纯文本：`@ToAssistant(msg, ctx?)`、`@OpenUrl`、`@Set/@Reset`、`@Run(ref)`、自定义 `@Name(...)`，另有 Query/Mutation 声明式取数。**高度活跃**：2026-10-22 当天仍有提交；npm 近 30 天下载量有硬证据；ADOPTERS.md 列名（GAIA、Productboard 等）；提供 browser-bundle（一个 script + 一个 css，无构建场景，约 900KB JS + 35KB CSS，依赖全内联）。

**intelligent UI 在本报告中指什么**：它有两个所指，本报告只把第二个当对标物。

- (1) OpenAI 2026-10 随 GPT-6 在 ChatGPT 推的 Intelligent UI（原生可流式组件库 + 界面编译器，模型自行决定回答形态：纯文本 / 并排对比 / 可点击示意图 / 带输入框滑块的临时工具），是**闭源产品能力，不向第三方开放，小溪不可接入**。
- (2) Thesys 的 Intelligent UI Generation 产品线（原 C1 API，即 OpenUI / OpenUI Lang），是开放实现，是小溪若做「对话即界面」的真实对标物。

**本报告的工作定义**：intelligent UI = 输出侧「模型按问题自行决定回答形态」+ 输入侧「结构化动作回流」。小溪的输出侧已经成体系（PROTOCOL §23 `aqi_blocks`：七种 kind + 一个 send_turn 信封 + 一个 vanilla 渲染引擎 + 四条产出路径），输入侧 7 个动词里真业务动词只有 2 个。**这个不对称就是本报告全部机会的来源。**

## 3. 权衡表

| 机会名 | 价值 | 工作量 | 主要风险 | 约束冲突 | 验证结论 | 推荐度 |
|---|---|---|---|---|---|---|
| O1 积木可访问性 + Holo-Deck 空态 | 高（读屏/键盘用户唯一入口） | S | 改 aria-live 会改变播报节奏，可能与心流防打扰叠加成过度播报 | `web/index.html`（`#holoDeck` 的 aria-live 所在）属 Role 0；`web/blocks.js` 归 Role 3 | confirmed | 高 |
| O2 输入侧 intent 化（option intent 枚举 / (block_id,index) 服务端查表） | 高（决定「点了要做什么」而不只是「点哪个」） | M | 查表口径漂移；门禁 7b 只查字面量，抓不到「分支在但不读 options」 | 新增 block 字段或 cmd 动词 → 正面触发协议红线，必须 `[PROTOCOL_REQUEST]` 停工等 Role 0 | confirmed | 高（需放行） |
| O3 积木动作可撤销 / 直达入口 | 中高（消除「点了不知道成没成」） | S | 用既有动词表达新语义，口径可能被判「自造」 | 若只用既有 `ui:preview` / `ui:drawer` 则无新字段；若需新动词则触红线 | partial | 中高 |
| O4 `due_at` 客户端中文时间预填 | 中（把「明天下午三点」变成可见可选值） | S–M | 解析错时间就是事实错误，与「准确性优先」正面冲突 | 无协议冲突（`due_at` 已是既有入参）；必须「预填 + 用户确认」，禁止自动提交 | partial | 中 |
| O5 形象层对积木动作的反应 | 中（补 §三「点积木不通知形象层」缺口） | M | avatar 行为契约不在本次调研范围，误加表情比不加更糟 | `web/avatar/` 所有权边界（Role 3 owns web/ 除 `web/avatar/drivers/`）；若走 WS 事件/字段即触红线 | partial | 中（待拍板） |
| O6 pixel-world 生成侧（LLM 造物主 L0–L3） | 高（唯一「世界级」演示点） | L | 「世界不撒谎」与 24 色 ADR 双重边界；生成失败回退链路要重做 | `NEXT_PHASE_PLAN.md` §7 已明确划到计划外需另行批准；S5 又被 `executive.py` 收口阻塞 | confirmed（已停泊） | 低（暂缓） |
| O7 引入 `wandb/openui` 当设计工具 | 低 | S | 休眠仓库 + 演示站本机不通，跑不跑得起来未验证 | 与「不引 React / 无构建」「只走 textContent」既有决定冲突；产物是 HTML iframe，产品侧无落点 | refuted | 不推荐 |
| O8 引入 `thesysdev/openui`（含 browser-bundle） | 中（省掉 prompt 工程与流式解析器） | XL | 约 900KB React runtime 进 vanilla 前端；平行替代 `blocks.js` | 违反 Reuse First / 严禁另起平行替代模块；§23 七 kind 闭集与 check_protocol 三方认账要整体重写；XSS 防线从 textContent 换成 React 转义，现有测试基线全废 | refuted | 不推荐 |
| O9 OpenAI ChatGPT Intelligent UI | — | — | 闭源 | 无开放接口 | refuted | 不可用 |

## 4. 机会清单（按推荐度排序）

### O1 积木可访问性 + Holo-Deck 空态，推荐度高

- **是什么**：给 §23 七种积木补 `role` / 可访问名称 / 状态值，并给唯一没有空态文案的容器补一行 hint。
- **在项目哪里发挥**：`web/blocks.js`（376 行，§23 唯一渲染引擎；统一出口 `decorate()` 与 `createHoloDeck()` 的 `mount.hidden = count === 0` 分支）、`web/index.html:139`（`#holoDeck` 的 `aria-live=polite`）、样式落在 `web/almanac.css`。
- **怎么落地**：`alert_card` 的 `level=critical` 用 `role="alert"`；`metric_flame` 数值条用 `role="progressbar"` + `aria-valuenow/min/max`；`swimlane_strip` 当前阶段用 `aria-current="step"`；其余卡片在 `decorate()` 统一按 kind 挂 `role` 与 aria-label。空态 hint 措辞对标 `web/executive.js:427`「还没有派出去的单」。**hint 是 DOM 文本，不是积木、不进 `blocks[]`、不发 WS**，因此不违反「没有真实数据就不摆卡片」。
- **工作量**：S，约 1 天。
- **风险**：改 `#holoDeck` 的 aria-live（critical 要打断需 assertive 或独立 live region）落在 Role 0 文件上；播报节奏变化可能和心流防打扰叠加。
- **约束冲突**：`web/index.html` 属 Role 0（打磨轮临时授权 Role 3 写 app.js / index.html，到期或打标自动收回）；`web/blocks.js` 归 Role 3。
- **验证结论**：**confirmed**。本轮 grep 复核：`web/blocks.js` 对 `aria-|role=` 零命中；`web/app.js` 全库 `innerHTML` 仅 4 处且全是写死的本地模板（`app.js:1793` 语气枚举 option、`web/avatar/dialogue-review-ui.js:17` 复盘面板骨架、`web/workspace.js:5` 便笺 tablist 骨架、design-preview 对比页），无一由远端数据驱动。即「运行时拼装 UI」在小溪不存在，`RENDERERS` 闭集是唯一现成落点（`web/blocks.js:301-321` 已逐行复核：7 kind 注册、未知 kind 直接 `return null`）。对比证据：`executive.js` / `assistant.js` / `threads.js` 都写了 hint 段落，Holo-Deck 是唯一没有的容器。

### O2 输入侧 intent 化，推荐度高（需 Role 0 放行）

- **是什么**：让 `choice_pills` 的每个 option 携带确定性意图，而不是把一句自由文本回灌给 LLM。
- **在项目哪里发挥**：`docs/PROTOCOL.md` §23（block 字段与 cmd 白名单）、`web/app.js:667-676`（`choice:<index>` 现取 `options[index].cmd`）、`server/app.py:598-637`（`_aqi_action` 白名单）、`server/dialogue/aqi_blocks.py`（积木组装）、`scripts/check_protocol.py:219-222`（CMD_VERBS / SERVER_CMD_VERBS / AQI_BLOCK_KINDS 静态纳管）。
- **怎么落地**：两条路，都须 `[PROTOCOL_REQUEST]`。(a) option 加 `intent` 枚举，前端按 intent 分流而非回灌 `say:`；(b) 服务端按 `(block_id, index)` 查表。任一条都要由 Role 0 先落 PROTOCOL.md，并同步 check_protocol 的 CMD_VERBS 与 `web/blocks.js` 三方对账；顺带把 `docs/REVIEW_ROUND5_20261018.md:194` 记的「7b 只查字面量」升级为 `options[index]` 结构断言。
- **工作量**：M，含协议修订与三方对账。
- **风险**：查表口径漂移；自由度一放开就和「没有真实数据就不摆卡片」「禁止编造」两条铁律摩擦。
- **约束冲突**：**正面触发协议红线**（不得自造字段/动词；PROTOCOL.md 只有 Role 0 可改）。
- **验证结论**：**confirmed**。缺口在 `docs/DECISION_TRAIL_20261018.md` §二对账表与 `docs/REVIEW_ROUND5_20261018.md:188` 双向记录；代码侧已复核 `choice` 分支确实只读 `options[index].cmd`，而 `scripts/check_protocol.py:220` 的 `SERVER_CMD_VERBS` 只有 `dispatch` 一项。即 7 个动词里真正形成结构化回流的只有 `dispatch:accept` / `dispatch:reassign` 两个。

### O3 积木动作可撤销 / 直达入口，推荐度中高

- **是什么**：点完验收/换将之后，给一条「去战况看这单现在到底怎样了」的直达路径，并让失败态可重试。
- **在项目哪里发挥**：`web/app.js:677-684`（dispatch 分支，失败只写 `subtitle`）、`web/app.js:902-904`（45s pendingConfirmation 超时）、`web/executive.js`（ops 视图已有 `aqi-block-preview` 高亮定位）。
- **怎么落地**：优先复用既有 `ui:preview:<ref>` / `ui:drawer:ops` 两个动词，把「看这单」作为 option/action 的 cmd 下发，**零新字段**。若判定「用既有动词表达新语义」仍需协议背书，则退回 O2 一起提 `[PROTOCOL_REQUEST]`。
- **工作量**：S。
- **风险**：口径争议（见上）；pendingConfirmation 超时后的重试语义要和服务端「以台账真实状态为准」对齐，不能做成「看起来成功了」。
- **约束冲突**：可能无（纯复用既有动词）；可能有（若需新动词）。
- **验证结论**：**partial**。缺口 confirmed（无直达入口、只有一行字幕）；「复用既有动词是否算契约内加法」**未验证**，需 Role 0 一句话确认。

### O4 `due_at` 中文时间预填，推荐度中

- **是什么**：把「明天下午三点」在客户端解析成 `datetime-local` 的可见值，让用户改一个字再提交，而不是塞进 description。
- **在项目哪里发挥**：`web/task-card.js`（现只有 `datetime-local` 手动选值）与任务创建链路。
- **怎么落地**：纯客户端解析 + 预填 + **必须展示解析结果供用户确认**，绝不自动提交；解析失败就留空并明说「没听懂时间」。
- **工作量**：S–M。
- **风险**：解析错时间就是事实错误，正面撞「事实准确性优先于性格」；缓解只能靠「可见可改」。
- **约束冲突**：无协议冲突（`due_at` 已是既有入参）。
- **验证结论**：**partial**。缺口 confirmed（`DECISION_TRAIL_20261018.md` §二 记录自然语言落在 description、未验证）；预填方案与解析准确率**未验证**。

### O5 形象层对积木动作的反应，推荐度中（待拍板）

- **是什么**：点积木 / 胶囊 / 验收 / 换将后让立绘有反应，补上 §三 缺口。
- **在项目哪里发挥**：`web/app.js:1878-1886`（`window` 的 `aqi-touch` 只认 `region=head|face`）与形象层渲染入口。
- **怎么落地**：先做纯本地 `CustomEvent` 广播（不新增 WS 事件/字段），由形象层自行决定要不要接；是否接通走 Role 0 口径确认。
- **工作量**：M。
- **风险**：avatar 行为契约不在本次调研范围内；`web/avatar/drivers/` 明确不在 Role 3 权限内。
- **约束冲突**：`web/avatar/` 所有权边界；若改走 WS 即触协议红线。
- **验证结论**：**partial**。缺口 confirmed（`DECISION_TRAIL_20261018.md` §三 与 `REVIEW_ROUND5_20261018.md:189` 双向记录「打字、点积木、点胶囊、验收、打回都不通知形象层」）；本地事件豁免口径**未验证**。

### O6 pixel-world 生成侧，推荐度低（暂缓）

已停泊，证据充分：`pixel-world/src/world/oracle.ts` 开头自述「这是世界自己的意图路由器，不是 LLM」；`prefabs.json` 是 4 份手写蓝图，无 LLM 适配器、无 prompt、无模型产出解析/校验；`docs/NEXT_PHASE_PLAN.md` §7 把「自由生成世界、LLM 造物主生成侧、布局评分器」明确划到计划外需另行批准；S5「世界里点铺子做验收」又被 `executive.py` 收口阻塞（该文件 durable 改动由用户本人处理，团队不得触碰）。设计侧其实已就绪（`docs/PIXEL_WORLD_ARCHITECTURE.md` §4 的 L0 人写规格 / L1 手写模板 / L2 手绘组件 / L3 LLM 只输出 JSON，经校验器到缓存到实例化，校验失败退回 L1），缺的是批准而非能力。**验证结论：confirmed（已停泊）。**

## 5. 不推荐或暂缓的

- **引入 `wandb/openui`（O7），不推荐**。它是原型玩具而非库，产物是 HTML iframe；仓库已实质休眠（最后实质代码提交 2025-05-24，之后只有 Renovate 的 SHA pinning），零 Release，官方演示站本机无法连通，「现在还能不能跑通」未验证；即便能跑，产物形态与「vanilla JS 无框架无构建」「只走 textContent」两条既有决定直接冲突，产品侧没有落点。
- **引入 `thesysdev/openui`（O8），不推荐**。它确实解决了小溪最贵的那段（prompt 工程 + 流式解析 + 受控组件白名单），但小溪已经用自己的方式解决了同一件事：`RENDERERS` 闭集 + 服务端保守校验 + 未知 kind 安全降级 + `check_protocol.py` 三方认账。引入它等于**另起一个平行渲染引擎**，违反 Reuse First / 严禁另起平行替代模块；§23 七 kind 闭集与 `AQI_BLOCK_KINDS` 对账要整体重写；XSS 防线要从「只走 textContent」换成 React 转义，现有 `web/test_blocks.mjs` 等基线全废；browser-bundle 虽满足「无构建」，但会把约 900KB React runtime 塞进 vanilla 前端。收益不抵成本，且用户已明确否决过「直接搬入一整套 React 特效库」（`docs/UI_DESIGN.md:59`）。
- **OpenAI ChatGPT Intelligent UI（O9），不可用**。闭源产品能力，不向第三方开放。
- **pixel-world 生成侧（O6），暂缓**。不是不能做，是「停泊 + 阻塞」双重在身：`NEXT_PHASE_PLAN.md` §7 需另行批准，S5 被 `executive.py` 阻塞；动它还会同时碰「世界不撒谎」与 24 色 ADR（新增第 25 色必须再走 ADR + 用户批准）。
- **「让模型自由生成积木 Schema」，暂缓**。当前闭集正是「没有真实数据就不摆卡片」和「禁止编造」两条铁律的执行机制；放开自由度前必须先回答「如何保证模型不摆占位卡」，该问题在调研包的 open questions 里同样未决。

## 6. 建议的第一步：1 到 2 天 spike

**题目：积木可访问性 + Holo-Deck 空态（O1）**，同日并行起草 O2 的 `[PROTOCOL_REQUEST]`（只提申请，不写实现）。

选它的理由：唯一「零协议改动、当天可验证、直接补 intelligent UI 短板」的一项；且能顺便把「结构断言」思想先在前端单测落地，为将来 O2 的门禁升级做样板。

**Day 0 基线（先跑，全绿才动手）**。注意本机 shell 是 POSIX，反斜杠路径会 MODULE_NOT_FOUND，命令一律用正斜杠：

```
.venv/Scripts/python.exe scripts/check_protocol.py
.venv/Scripts/python.exe scripts/test_core.py
npm test
npm run test:blocks            # 即 node web/test_blocks.mjs
node web/aqi-flow.test.mjs
node web/ui_nav.test.mjs
node web/executive-state.test.mjs
node scripts/test-aqi-blocks-browser.mjs
.venv/Scripts/python.exe scripts/run_gates.py --allow-skip
.venv/Scripts/python.exe scripts/run_evals.py        # 离线层
```

（基线今日已复跑为绿；具体计数以命令实时输出为准，不写死。）

**改动面**：

- `web/blocks.js`：`decorate()` 统一按 kind 挂 `role` + aria-label；`renderAlertCard` 对 `level=critical` 用 `role="alert"`；`renderMetricFlame` 数值条 `role="progressbar"` + `aria-valuenow/min/max`；`renderSwimlaneStrip` 当前阶段 `aria-current="step"`。**仍只走 textContent，铁律不变。**
- `web/blocks.js` `createHoloDeck()`：`mount.hidden = count === 0` 与 MAX_CARDS 回收分支补一行 hint 文案（对标 `web/executive.js:427`）。hint 是 DOM 文本，不是积木、不进 `blocks[]`、不发 WS。
- `web/test_blocks.mjs`：新增两条断言，「RENDERERS 每个 kind 的输出都带 role 与可访问名称」「空态 hint 不含任何编造数据」。这就是 `REVIEW_ROUND5:194` 建议的结构断言版 7b，先在前端落地，**不动 `check_protocol.py`**。
- `web/index.html:139`（**Role 0 文件**）：`#holoDeck` 现为 `aria-live=polite`；critical 要打断需改 assertive 或加独立 live region。列为 spike 交办项，由 Role 0 决定或授权，不自作。
- 改完前端必须杀旧服务进程再重启再做浏览器回归（`docs/TESTING.md:34-35`），否则 StaticFiles 缓存目录列表会让新文件假 404、回归假失败。

**验收（exit criteria）**：上述门禁命令复跑全绿；产出「7 kind 乘 role / 名称 / 状态值」a11y 矩阵一页纸；一次 NVDA / VoiceOver 人工抽查（**未验证**，需用户环境，spike 内只做记录不做判定）。

**同日并行（不写码）**：起草 `[PROTOCOL_REQUEST]`，含两问。(a) 是否给 option 加 `intent` 枚举或改 `(block_id,index)` 服务端查表，口径由谁定；(b) 「用既有 `ui:preview` 动词承载『看这单』」是否算契约内加法（决定 O3 是否需要走协议）。

## 7. 待用户拍板的点

1. **「OpenUI」指哪一个**：`wandb/openui`（休眠原型工具）还是 `thesysdev/openui`（活跃开放标准）？本报告结论对两者都是「不引入」，但若用户想要前者当内部设计工具，需另行确认它现在还能不能跑（未验证）。
2. **是否提 O2 的 `[PROTOCOL_REQUEST]`**，以及 intent 枚举 vs 服务端查表的口径由谁定。这是唯一触及协议红线的机会。
3. **O3 的口径**：用既有 `ui:preview` 动词表达「看这单」是否算契约内加法，还是要走协议。
4. **`web/index.html` 的 aria-live 是否改 assertive / 是否给 critical 告警独立 live region**（Role 0 文件，且影响播报打扰阈值）。
5. **模型侧自由度边界**：是否允许模型自由生成积木 Schema（当前闭集），以及如何与「没有真实数据就不摆卡片」「禁止编造」调和。
6. **O5 的边界**：3D 形象层新增纯本地 CustomEvent 输入是否豁免协议红线；`web/avatar/drivers/` 的权限怎么走。
7. **pixel-world 生成侧是否解除停泊**（`NEXT_PHASE_PLAN.md` §7），以及与主前端立绘合并的 D1/D2 美术路线。后者是像素世界与主产品唯一的接点。
8. **`check_protocol.py` 7b 是否升级为 `options[index]` 结构断言**（`REVIEW_ROUND5:194` 已记未决）。

---

---

## 第三轮（2026-10-23）：按建议实施，六项全部落地

用户批准「按建议来」。实施顺序为先修两条缺陷，再 N1/N2/N12，最后 N14。六个待改点由六个只读 agent 并行产出精确补丁规格（带逐字锚点），再由四个实施 agent 分两批落地，两批都跑完全套验证。`web/app.js` 被 N1 与 N14 同时触及，因此这两项串行实施，其余四项并行。

### 落地清单

| 项 | 文件 | 性质 |
|---|---|---|
| 缺陷一 同传流式缺 offline 闸门 | `server/speech/stream_interpretation.py`、`scripts/test_stream_interpretation.py`、`docs/LIVE_INTERPRETATION.md` | 缺陷修复 |
| 缺陷二 parseOracle 抢词 | `pixel-world/src/world/oracle.ts`、`pixel-world/test/world.test.ts` | 缺陷修复 |
| N2 同传能力预检 | `web/interpret.js`、`web/interpret.html`、`scripts/test-interpret-browser.mjs` | 新行为 |
| N12 上下文纯函数单测进门禁 | `web/context-budget.test.mjs`（新）、`web/context-usage.test.mjs`（新）、`web/context-usage.js`、`package.json` | 测试补缺 |
| N1 记忆元信息 | `web/app.js`、`web/interaction-preferences.js`、`scripts/test-voice-browser.mjs`、`scripts/test-preferences-browser.mjs` | 展示既有字段 |
| N14 陪伴体验入口 | `web/index.html`、`web/drawer.js`、`web/experience.js`、`web/app.js`、`scripts/test-experience-entry-browser.mjs`（新）、`scripts/run_gates.py`、`docs/VOICE_EXPERIENCES.md` | 新入口 |

### 两条缺陷的修法

- 同传流式：在 `setup()` 的 `stream_factory()` 之前加闸门，`llm is None or getattr(llm, "offline", False)` 时直接 `fail("llm_failed", gen)` 并返回。落在建适配器之前，离线时不建立阶跃连接、不发 ready、translator 不启动。复用既有 `llm_failed` 错误码，零协议改动；新增错误码与分段路径的 `offline` 对称化另开单。
- parseOracle：新增 `leads(lower, words)` 判定，五组世界动词从全文 `includes` 改为「命令词必须打头」。派活词 `TASK_RULES` 保持全文匹配不变，因为派错单可撤销、收错单会造假交付，代价不对称。评估并否掉了首尾边界匹配与长度阈值两种规则，理由记在补丁注释里。

### 验证结果（2026-10-23，主会话复跑）

- `scripts/run_gates.py --allow-skip`：62 道门禁跑过并断言，0 失败，0 跳过。新增的 `experience_entry_browser` 为 PASS 非 SKIP。
- `scripts/check_protocol.py` PASS。`scripts/test_core.py` 92 passed 0 failed。`npm test` 退出码 0。
- `pixel-world` 的 `npm test` 239 项，230 passed，0 failed，9 skipped（9 个 skip 全是既有的调色板 dL* 未达标项，与本次无关）。`npm run check` 与 `npm run test:inprocess` 均退出码 0。
- `scripts/test_stream_interpretation.py` 14 项 OK（含新增的 offline 闸门用例）。`scripts/test_interpretation.py` 15 项 OK。
- `scripts/test-interpret-browser.mjs`、`test-experience-browser.mjs`、`test-experience-entry-browser.mjs`、`test-aqi-blocks-browser.mjs`、`test-ui-nav-browser.mjs`、`test-voice-browser.mjs`、`test-preferences-browser.mjs` 全部通过。
- `pixel-world/scripts/smoke.mjs` 未跑，它需要 vite dev server 在 5188，本机没有起，没有为了跑它而起服务。

### 偏离规格之处

- N2 的规格里两行测试谓词把 `textContent` 写成了函数调用，实施者按事先授权修正为属性读取，业务代码一行未动。
- N14 的用例 2 原本断言 `#experienceStatus` 为某句固定初始文案，实际 `web/app.js` 启动时就调 `importSelection()`，无交接时会把状态改写。实施者改为断言入口前后状态文本不变且匹配 `/尚未应用|没有待导入/`，没有为了让断言变绿而去改业务代码。

### 所有权与待办

- `web/index.html` 与 `web/app.js` 归 Role 0，本轮依据 `docs/AGENT_PLAYBOOK.md:88` 的打磨轮临时授权写入。授权至 v0.7 打标止，打标即收回，合并前需 Role 0 做 Integration Check。
- `scripts/run_gates.py` 在本轮开工前已有他人未提交改动（新增 `config_hot_consumers` 门禁并改 `ALWAYS_REQUIRED`）。本次只在其后追加 `experience_entry_browser` 一条，未动他人 hunk。
- 未提交 git。`server/app.py`、`server/dialogue/orchestrator.py`、`server/memory/manager.py`、`server/memory/retriever.py` 四个文件的未提交改动是他人在途工作，本轮未触碰。
- 遗留：`docs/ARCHITECTURE_GAPS.md` 与 `docs/ADR_AGENT_BRIDGE.md` 里引用 `oracle.ts` 行号的三处因新增 13 行已过期，本轮未改文档。

---

## 第二轮（2026-10-22 晚）：别的可做的

用户判定：thesysdev/openui 可用性存疑，且对像素世界方向无用。本轮用五个只读 subagent 补盘点了第一轮没深挖的面：语音体验类（interpret、music、voice_taste、explore、experience、aqi-flow、mode-strip、stage-fx）、知识陪伴类（notes、threads、starmap、editor-insights、note-proposals、workspace、glimmer_bubble、drawer）、停泊与计划四文档全量、像素世界方向、外部技术扫描。除标注外，各条判断来自 subagent 的 file:line 证据，主会话只复核了其中两条。

### thesys 与像素世界：用户的判断成立，且比「没用」更彻底

- `pixel-world/src/world/oracle.ts:1-5` 自述「这是世界自己的意图路由器，不是 LLM」。`:33-54` 是一张关键词表加顺序匹配，输出 6 个闭集动词。
- `prefabs.json` 是 4 份手写蓝图，`blueprints.ts:30-60` 逐字段硬校验，失败直接 throw，没有回退路径。
- 全 src grep `llm|openai|anthropic|claude|gpt|fetch\(|http` 零命中。LLM 接入点不是留空，是不存在这个缝。
- 世界的可见元素全部来自 `WorldSnapshot` 一个纯函数，每个字段的形状与取值域全被锁死：24 色 ADR、4 份蓝图、7 态状态机、6 动词神谕。生成式 UI 的前提是「同一份数据有多种合理呈现，让模型选」，这里可选项数量是 1。
- 这个世界该有的「生成」是人改 JSON，不是模型生成。`prefabs.json` 加 `blueprints.ts` 已经是「模型可用组件白名单」的最强形态，JSON 是外部数据，边界硬校验，人改 JSON 就能长世界。
- 真要上 LLM，唯一有意义的落点是 `docs/PIXEL_WORLD_ARCHITECTURE.md:94` 的 L3，模型只输出 `{prefab_id, placement, palette}`。它已被 `docs/NEXT_PHASE_PLAN.md:159` 划到计划外，且它一次都不产生「界面」，所以对本题无关。

### 两条已确认缺陷，优先于任何新功能

| 缺陷 | 证据 | 修法 | 工作量 |
|---|---|---|---|
| 同传流式路径缺 offline 闸门 | `server/speech/stream_interpretation.py` 对 `offline` 零命中（主会话复核）；`server/speech/interpretation.py:134-136` 有闸门。若 LLM 处于离线 mock，`llm.py:239-255` 的角色台词可能当译文上屏 | 与 `interpretation.py:134-136` 同构补闸门，复用 `llm_failed` 错误码则零契约。`docs/LIVE_INTERPRETATION.md:28` 已承诺「未配置时明确报错，不静默退回」 | S |
| 像素世界 parseOracle 抢词 | `pixel-world/src/world/oracle.ts:15,37,48`（主会话复核）。完成、天气、撤销三组词先于任务规则做整句 includes 匹配，「帮我写一份完成报告」返回 complete，单派不出去；「查一下下雨的历史数据」返回 weather:rain | 给三组词加边界判定，补 4 到 6 条对抗用例到 `pixel-world/test/world.test.ts`。同类坑作者只为 QUICK_PREFIXES 防过（`:20-21` 注释） | S |

### 零协议改动、现在就能做（按推荐度排序）

| # | 机会 | 位置 | 工作量 | 一句话理由 |
|---|---|---|---|---|
| N1 | 记忆面板补「何时说过 / 被当过上下文 N 次」 | `web/app.js:1427-1456`、`web/interaction-preferences.js:45-48` | S | 字段早在 `/api/memories` 载荷里（`server/memory/store.py:299-302` 加 `to_dict()`），纯渲染即可兑现 `docs/PARKED_TASKS.md:61` 停泊项的一半。`use_count` 文案必须带「不代表模型引用正确」 |
| N2 | 同传能力预检加提前禁用 | `web/interpret.js`、`web/interpret.html` | S | 现在授权麦克风之后才每段报 offline。`web/music.js:80-90` 有 capabilities 预检先例，照抄即可 |
| N3 | 同传逐段延迟加队列深度 | `web/interpret.js:179,201` | S | 数据都在同一条 translation 消息里，现在被全局覆写 |
| N4 | 同传双语对照阅读模式 | `web/interpret.js`、`web/interpret.css` | S | 堆叠改并排，DOM 结构与 class 不动，测试不受影响。窄屏降级回堆叠 |
| N5 | 试听回到默认语气 | `web/voice_taste.js`、`web/app.js:634-637` 旁加分支 | S 到 M | 复用既有 `config.emotion` 字段，零新字段。现在听完 4 条要自己去设置里选 |
| N6 | 试音场景预设与盲听模式 | `web/voice_taste.html`、`web/voice_taste.js` | S | 不同句型下 rate 与 pitch 的听感差异比同一句重复 4 次信息量大 |
| N7 | 音乐可感知等待 | `web/music.js:52` | S | 只显示已等秒数，不显示假进度条。`music_api.py` 无进度通道，编百分比就是编造 |
| N8 | 笔记版本时间轴加任意两版对照 | `web/notes.js`、`web/notes.html` | M | `/history` 与 `?version=` 都是既有 REST，页面内却没有历史入口。diff 算法从 `web/note-proposals.js:46` 抽出复用 |
| N9 | 事项列表检索过滤与状态分组 | `web/threads.js:177-187` | S | 数据已在内存，列表上限 100 且无检索（`docs/SHARED_THREADS.md:50`） |
| N10 | 星图 kind 过滤器加时间窗 | `web/starmap/index.js:70,202` | S 到 M | 服务端与桥都支持 `kinds` 与 `days`，UI 从不传。图例 chips 已有计数，改成可点是最短路径 |
| N11 | 看稿预算常驻可见 | `web/editor-insights.js:160-161` | S | 剩余次数、冷却、暂停原因现在只在 say() 里闪过一句 |
| N12 | context-budget 与 context-usage 纯函数单测进 npm test | 新增 `web/context-budget.test.mjs`、`package.json:5` | S 到 M | `package.json:5` 的 npm test 不含这两个文件，`scripts/run_gates.py` 也没纳管，是测试覆盖缺口 |
| N13 | 行内字符级 diff 与分块采纳 | `web/note-proposals.js:70-93,158-199` | M | 中文整段通常是一行，行级 LCS 退化成整行删加整行增。服务端候选本就是最多 8 处局部替换，UI 却只能整份确认 |
| N14 | 陪伴体验补一个真实入口 | `web/index.html:200-208`、`web/mode-strip.js:11-18`、`web/experience.js` | S 到 M | 8 个陪伴模式现在只有「从同传页带一句回来」一条路进得去，抽屉导航没有这一项。放抽屉不放主舞台，守 `web/ui_nav.js:5` 的零新增按钮铁律 |
| N15 | 心流守卫扩到她正在说话时 | `web/aqi-flow.js:26-28`、`web/app.js:581-583` | S | 播报中弹卡片同时抢听觉与视觉。但 `docs/PROTOCOL.md:528` 把守卫定义为「正在打字或写作宽屏时」，扩判据是契约内语义变更，需 Role 0 一句话 |

### 需 Role 0 或需先定口径

| 项 | 卡点 |
|---|---|
| N14、N15 及一切碰 `web/app.js`、`web/index.html` 的改动 | 两文件归 Role 0（`docs/AGENT_PLAYBOOK.md:67-72`）。打磨轮临时授权按自述至 v0.7 打标止，tag 未打故仍在期，但认定权在 Role 0，未验证 |
| O2 输入侧 intent 化 | 正面触发协议红线，须 `[PROTOCOL_REQUEST]` |
| O3、N5、ED-1 直写稿版的「契约内加法」口径 | 复用既有字段与 postMessage 类型是否算自造，需 Role 0 一句话 |
| O5、N16 星点跳记忆面板（本地 CustomEvent） | 本地事件豁免口径未验证，与 O5 同一问题 |
| 记忆「停止使用」与逐轮使用审计 | 无路由无字段，新增即触 `docs/AGENT_PLAYBOOK.md:76-79` |
| 音乐音喜好建议填空 | 客户端关键词匹配会绕过 `server/interaction/broker.py:98-133` 的保守装配，且违反 `docs/PERSONALITY_MEMORY_RESEARCH.md:47`「记忆关闭须立即停止个性化」。要走服务端投影加协议 |
| 同传置信度与字级时间戳 | 服务端已算出即弃（`server/speech/asr.py:140-143`、`server/speech/step_stream.py:174-183`），下发即新增 WS 字段 |
| 发明义务免责声明 | `docs/PARKED_TASKS.md:73` 要求产品决策二选一，disclaimer 分支可先落 |
| notepad_preview 孤儿处置 | `docs/TASKS.md:80` 是既有决定，删或移要 Role 0 拍板 |
| 三个内置 Agent 接真执行体 | 配置级可做（`config/agents_roster.yaml`），但本机是否具备可执行文件与凭据未验证，否则变成新的假接线 |

### 外部技术一句话结论

对这套约束组合，subagent 扫过的 11 个候选没有一个值得作为依赖引入。真正值钱的是三样零依赖可抄的东西：A2UI 与 Open-JSON-UI 对「服务端描述、客户端闭集、不执行远端代码」的公开论证，给现有 `RENDERERS` 路线做外部背书；AG-UI 的事件与共享状态分类学；LiveKit、OpenAI Realtime 与 Amazon APL 的语音前端状态机。其中 APL 是「服务端下发结构化描述、客户端闭集渲染」最成熟的公开工程样本，与小溪的七 kind 闭集几乎同构。

### 本轮复跑核验记录（2026-10-22，本机；前四条经主会话复核，后两条为 workflow 内记录、主会话未复跑）

- `web/blocks.js` 对 `aria-|role=` 零命中；`RENDERERS` 7 kind 注册与未知 kind `return null` 已逐行复核（:301-321）。
- 全库 `innerHTML` 仅 4 处且均为写死的本地模板（`web/app.js:1793`、`web/avatar/dialogue-review-ui.js:17`、`web/workspace.js:5`、design-preview 对比页），无一由远端数据驱动。
- `web/app.js:667-676` `choice` 分支确认只读 `options[index].cmd`；`web/app.js:677-684` dispatch 分支确认失败仅写 `subtitle`。
- `scripts/check_protocol.py:219-222` 确认 `CMD_VERBS` / `SERVER_CMD_VERBS` / `AQI_BLOCK_KINDS` 三项静态纳管。
- 门禁基线复跑（workflow 内记录，主会话未复跑）：`test_core.py` 无 failed、`npm test` 退出码 0（具体计数以实时输出为准，不写死）。主会话只复跑了 `check_protocol.py`，结果 PASS。
- shell 注意：本机为 POSIX shell，`web\test_blocks.mjs` 这类反斜杠路径会 MODULE_NOT_FOUND，命令一律用正斜杠。
