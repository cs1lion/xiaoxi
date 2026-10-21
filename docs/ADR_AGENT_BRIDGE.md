# ADR：Agent ⇄ 像素世界最小桥接（范围已批准 · 实施待 S5 · v2.7）

- **状态**：**范围已批准，实施待 S5**（v2.7 · 用户 2026-10-19 批准：§4.4 选 A；G2 为范围预批准；7 条实施条件见 §7.1。v2.6→v2.7（2026-10-20 用户实测补缺）：§4.4.1 增加"两条路径的区分"——"同义改写静默不建单"仅 live 路成立，Mock 路（`?ledger=off`）同义句被 `oracle.ts` 接走建成 Mock 单并交付（parseOracle 9/9 已跑 + 用户实测转报）；已知限制验收必须指定路径。历史：v2.5→v2.6 架构复审修订（A6 三重断言/代表性抽样/B2 stub handler/§4.7 触发条件更正与静态声明变体）。首席架构审查官终审通过（2026-10-19）+ 两轮复审修订。**未实现、未承诺；S3/S4 收口前不动 `server/assistant/**`。**
- **日期**：2026-10-19 · **作者**：Agent 集成工程师 · **分派**：Chief（选 A）；**评审**：首席架构审查官三轮（两轮有条件通过 → 终审通过），P1 全闭、P2 并入、勘误 5 条已 patch
- **核验基线**：分支 `feat/pixel-world-oracle-world`，HEAD `5214eb5`。本文只读核验 + 文档提案，**未改任何源码/配置/测试，未 commit**。工作区 task-28 调色板改动进行中，本文不触碰 `pixel-world/src` 与 `docs/ART_PALETTE_AUDIT.md`。
- **上游文档**：`docs/AGENT_INTEGRATION_STATUS.md`（三分类核验）、`docs/PIXEL_WORLD_ROUND3_20261019.md`（台账接入）、`docs/NEXT_PHASE_PLAN.md`（S5 排期）、`docs/DECISION_TRAIL_20261019.md`（D2）。

## 1. 背景与问题

个人 Agent 助理（`server/assistant/`，Python）要驱动像素世界（`pixel-world/`，Phaser 3 + TS，浏览器应用）。要回答三件事：

1. Agent 发起的世界操作今天能不能到达世界？（**能，但只有一条通道**）
2. 世界执行结果今天能不能回到 Agent？（**部分能，仅限台账状态投影**）
3. 最小可用闭环需要动什么边界？

## 2. 现状证据链（静态核验，文件:行；均经本轮复锚）

| # | 事实 | 证据 |
|---|---|---|
| E1 | Agent 侧工具注册是数据类清单，新工具加在这里，不新起调度器 | `server/assistant/service.py:64-89`（`Tool(...)` 列表）、`:90`（`AgentLoop(self._decide, self.tools, …)`） |
| E2 | `Tool` 自带严格校验：类型、必填、12000 字符上限、凭据脱敏；**只查必填、不补默认值** | `server/assistant/loop.py:29-47` |
| E3 | mutation 工具必须过单次确认门（绑定完整 action hash + run_id，重复/过期拒绝；`web/app.js:898` 消费，工具无关、不改前端） | `server/assistant/loop.py:254-261` |
| E4 | 执行先落检查点再副作用；`completed_actions` 按 hash 去重（**仅同一 run 内**） | `server/assistant/loop.py:484-505`、`:249-253` |
| E5 | 超时/取消/步数耗尽都有终态，不假装完成；`TypeError` **不在** handler 异常捕获里（只捕 ValueError/KeyError），会整轮落 `failed` | `server/assistant/loop.py:172-191`、`:263-264`、`:493`、`:497-498` |
| E6 | 台账写库（开票/迁移/记步）后订阅钩子广播 WS `commission_update`，旁路通知、异常吞掉；startup 挂订阅 | `server/interaction/threads.py:703-733`、`server/app.py:966-1003`、`:1374`；契约 `docs/PROTOCOL.md:348-354` |
| E7 | 浏览器世界 REST 全量 + WS 增量 + 断线轮询/重连，投影到 `simulation.sync` | `pixel-world/src/world/ledger.ts`、`projection.ts`、`simulation.ts:58-65`（命令集 `dispatch/complete/cancelPending/setWeather/build/sync`）。下行静态可证、QA 有真帧 e2e（S1-6）；LEDGER 冒烟用合成帧（`AGENT_INTEGRATION_STATUS.md` M4），不作真推证据 |
| E8 | 世界侧语义：台账活不编进度、完成权归台账、撤单停手、离线照常过日子；世界只读不写 | `pixel-world/README.md:81-90`、`:108`；`simulation.ts`（sync 主体） |
| E9 | Agent→台账的现成入口：`ExecutiveService.delegate()`（open→dispatch→start→驱动到终态，失败只在台账留痕不外抛）；app 复用 `assistant.executive`，无需新接线 | `server/assistant/executive.py:166-212`；`server/app.py:950`、`:43-44` |
| E10 | 台账→世界的下行是**唯一真实运输层**；`bridge.ts` 有双向消息形状但全仓只 import `toWorldCommand`，`Bridge` 未被实例化、无 transport | `pixel-world/src/world/bridge.ts:7-8,35-59`；`docs/AGENT_INTEGRATION_STATUS.md` §2.4/§3.2 M1-M2 |
| E11 | richer 世界操作（天气/时间/造物）今天**无入站通道**：`setWeather` 等命令存在，但外部只能 `window.Oracle.feed()` 控制台注入 | `simulation.ts:58-62`、`pixel-world/src/scenes/SandboxScene.ts:314,357-358`；`oracle.ts` 只是浏览器内关键词路由 |
| E12 | 世界→台账写回路由已存在（accept/reject/submit），但前端零写请求，属产品决策 | `server/assistant/executive_api.py:78-107`、`:91-94`；`pixel-world/README.md:108` |
| E13 | 快环意图识别全是正则，`executive_route` 在 `should_route` **之前**且命中即 `return`——派单意图可能被快环先截走 | `server/dialogue/orchestrator.py:60-65`（`_DELEGATION` 5 个交替分支）、`:79-81`；`server/app.py:697-739`、`:740` |
| E14 | 台账状态机：7 状态、`done` 唯一终态、`drafting` 只能去 `briefing|done`；Commission **没有** `interrupted` 状态 | `server/interaction/threads.py:517-526` |
| E15 | 重启收口：`briefing/running/auditing`（IN_FLIGHT）直接 blocked（可选 durable 对账 adopted），`drafting` 重派续跑；LookupError/异常路径都先转 `briefing` 再 block | `server/assistant/executive.py:282-332`（先例 `:319-321`、`:323-327`） |

## 3. 决策（本 ADR 的主张）

**D1 — 最小闭环 = 台账下行通道 + `world.*` 工具，零新协议项。**
Agent 侧新增两个工具（提案见 §4），世界侧零改动。派单经 `commission_update` 到达世界（E6/E7），Agent 经台账读接口观察结果。**不新增 WS 事件、不改 REST 形状、不动 `pixel-world/src`。**

**D1-附 — 服务端落点共 5 处编辑（`service.py` 四处 + `executive.py` 一处，全在 `server/assistant/**`，S5 开工时实施）：**
1. `service.py:64-89`：两个 `Tool` 条目（§4.2）。
2. `service.py:92-95`：扩 `should_route` 正则——当前正则无"世界/派单"类词（调用点 `server/app.py:740`），不扩则 `world.dispatch` 是死工具。**风险机制经复审更正**：快环 `executive_route` 先行且命中即 return（E13），不存在"双入口派两张单"；真实问题是**同一派单意图有两套语义**——命中 `_DELEGATION` → 快环立即派（免确认、自动驱动）；未命中的世界措辞 → AgentLoop 工具（要确认、有 receipt）。而"让她去世界里做这单"会被 `_DELEGATION` 第 4 条（`(?:让|叫)…(?:去|来)…(?:做|处理|干|弄|搞|查)`，orchestrator.py:64）吃掉，"派单给世界"不会——**可达性取决于措辞，很脆**。用户 2026-10-19 裁决选 A：只扩"显式点名像素世界"的短语（§4.4）。
3. `service.py` `__init__`：`import asyncio` + `self._world_tasks: set[asyncio.Task] = set()`（后台驱动任务强引用集合，防 GC 吞任务；配套 `_drive_world_commission` 见 §4.2）。
4. `service.py` `close()`（`:568-572`）：关闭时取消 `_world_tasks` 中未完成的任务（批准条件 C5）。
5. `executive.py`：重构出 `begin_commission` / `drive_commission` 两个方法（规格见 §4.5），修掉"打回后停摆 running"（`/run` 只跑一次，`dispatcher.py:309-337`；初审打回回 `running`，`dispatcher.py:543-548`；重试循环目前只在 `delegate()` 里，`executive.py:186-196`）。**为什么不能改用新模块包装、完全不碰 `executive.py`：见 §4.6（用户点名设计问题，结论=不能，附逐行证据）。**

**D2 — 上行回执只做选型比较，不落地。** 完整双向集成属 1.1（D2 裁决），本文为 S5 备料。选型见 §5。

**D3 — 明确不纳入最小闭环的范围**：天气/时间/召唤/扩建等 richer 操作（E11：无入站通道，需新协议事件或新运输层）；世界内验收/打回动作（E12：产品决策）；取消工具（见 §6，取消请回主 UI/语音快环）。

## 4. 最小闭环时序与契约草案

### 4.1 时序（全部复用现有机制，无新组件；含确认门回合）

```
Agent Loop                ExecutiveService            台账/WS              浏览器世界
   │ world.dispatch(title,brief) │                        │                   │
   │◄──── waiting_confirmation ──│（mutation 门，E3：用户点确认前不执行）        │
   │─── confirm ───────────────►│                         │                   │
   │                             │ open→dispatch→start（同步毫秒级，§4.5）      │
   │                             │────────────────────────►│ create/dispatch/start 落库
   │                             │                   订阅钩子广播             │
   │                             │                         │══════════════════►│ commission_update
   │                             │                         │  projection→sync  │ 她行礼→进铺子→运转
   │◄──── receipt（§4.2 DTO）    │                         │                   │
   │ create_task(drive_commission(id))：drive+打回重试（与 /run 同一执行权租约；  │
   │ 并发重复驱动 400 commission_already_running；任务集合+异常兜底 §6）         │
   │                             │  Worker 跑完→auditing→ready_to_report      │
   │                             │────────────────────────►│ 每次落库都广播     │
   │ world.status(commission_id) │                         │══════════════════►│ delivering→toAltar
   │────────────────────────────►│ commission(id) 显式投影（REST 真相）         │
   │◄──── 五态之一（§4.3）        │                         │                   │
```

### 4.2 契约草案（**提案，待 `docs/PROTOCOL.md` 评审 + 用户批准；未实现**）

```python
# 拟加在 server/assistant/service.py:64-89 的 tools 列表（G2 批准后才动）
Tool('world.dispatch', '向像素世界派一单活：经督办台账下行；世界页面在线时她会开工，'
     '在线状态不可确认；title 为一句话任务，brief 为老板原话；凭据类内容不要写进 brief',
     {'title': str, 'brief': str}, ('title',),
     self._world_dispatch, True)          # mutation=True → 过 E3 确认门
Tool('world.status', '只读查询督办状态与回执，不修改、不重跑',
     {'commission_id': str}, ('commission_id',), self._world_status)

# handler 签名必须带默认值：validate 不补默认（E2）、_execute 直接 **arguments（E5），
# 模型只传 title 时缺省 brief 会 TypeError → 整轮 failed。先例：store.py:174。
def _world_dispatch(self, title, brief=""):
    begun = self.executive.begin_commission(title, brief)     # 同步：open→dispatch→start
    self._world_tasks.add(asyncio.create_task(                # 强引用集合，异常兜底
        self._drive_world_commission(begun['commission_id'])))
    return {'id': begun['commission_id'], 'commission_id': begun['commission_id'],
            'status': begun['status'], 'agent_name': begun['agent_name'],
            'world_visible': 'unknown'}

async def _drive_world_commission(self, commission_id):
    """后台驱动：只放 drive，不在轮次里等 Worker。异常不得逃回轮次。"""
    try:
        await self.executive.drive_commission(commission_id)  # drive+打回重试，内部已降级
    except Exception:
        # 只兜"降级本身也炸"：当前一般处于 running/auditing，合法地去处是
        # blocked（threads.py:520-526），经 dispatcher 既有 block 路径
        #（dispatcher.py:484-486）落一张带请示的单，不裸奔。
        try:
            self.executive.dispatcher.block(commission_id, '驱动中断，要我再试一次吗？')
        except Exception:
            pass
    finally:
        self._world_tasks.discard(asyncio.current_task())
```

配套落点（G2 清单内）：`service.py` `__init__` 增加 `import asyncio` 与 `self._world_tasks: set[asyncio.Task] = set()`（后台任务强引用集合，防 GC 吞任务）。

- **receipt DTO**：`{id, commission_id, status, agent_name, world_visible}`——`id == commission_id`（计划验收只读 `data.id`，`service.py:453-456`）；`agent_name` 由 begin **显式解析并返回**：`open()` 的 commission 行只有 `agent_id`、没有 `agent_name`（`threads.py:618-633`），名字要经 `executive._agent_name` 解析（`executive.py:238-242`）——begin 不解析的话，handler 取 `begun['agent_name']` 会 KeyError，被 `loop.py:497` 吞成 failed receipt；`world_visible` 1.0 恒为 `"unknown"`：服务端只知道 `/ws` 有连接（`server/app.py:71`），分不出是不是像素世界页面、也分不清是否 `?ledger=off`。**receipt 文案只能说"已入台账"，不得声称"她已经在世界里开工"**；无法确认世界画面在动，作为独立告警表达，不进状态机。
- `_world_status(commission_id)`：**显式投影** `{id, status, agent_name, blocked_question, delivery_summary, updated_at}`——不返回整行：`_project()` 会解析 `packet`（含 `memory_block` 私密记忆文本，`server/interaction/threads.py:596`）；可复用 `_safe_public`（`service.py:107-161`）做边界。查无此单时显式返回 `{found: false}`，不落成 `failed` + 裸 KeyError 引号串（E5，`loop.py:497-498`）。
- **凭据过滤预期**：brief 含"密码/密钥/api key"等词会触发 `sensitive_tool_arguments`（E2，`loop.py:44-46`）→ 工具层直接拒绝。预期行为：用户换措辞；契约上 brief 只放任务描述。
- 复用而非新造：E2 类型校验、E3 确认门、E4 去重、E5 终态，全部现成。

**权限与副作用边界（批准条件 C4）**：两个工具的唯一写动作是"开一单并驱动"（经 begin/drive，与 `delegate()` 同一把执行权租约）；**不得**直接标记完成——完成权归台账与 Worker（`pixel-world/README.md:85`、验收走 `dispatcher.py:550-555`）；**不得**发神恩/动信仰账本——faith 只由世界在台账交付时自行结算（`README.md:81-89`、`simulation.ts` 交付路径），工具层不碰。

**后台任务纪律（批准条件 C5）**：强引用集合 `_world_tasks`（防 GC 吞任务）；异常记服务端日志（脱敏）并经合法迁移转 blocked；`service.close()`（`service.py:568-572`）时取消未完成任务；并发上限 **2**（`WORLD_DRIVE_CONCURRENCY = 2`，提案值：世界同时只显示一单，`pixel-world/README.md:95`，但台账侧不同 Agent 的单可并行驱动；2 = 一活跃一收尾，防 AgentLoop 多轮次扇出 CLI 子进程；实现期可调并回贴本 ADR）。

### 4.3 五态映射（重切版；对应"不谎报"）

| 状态 | 台账取值 | 语义 |
|---|---|---|
| 已接单 | `drafting` / `briefing` | `drafting` 世界不显示（`projection.ts:73-75` 丢弃），画面要等 `briefing` |
| 执行中 | `running` / `auditing` | 执行体在跑/初审中 |
| 待验收 | `ready_to_report` | **初审过了、等老板验收——不是"成功"**（`PROTOCOL.md:331-334`、`dispatcher.py:550-555`） |
| 已归档 | `done` | 唯一终态（E14，`threads.py:517-519`） |
| 待你答复 | `blocked` | **等你一句话，不是"失败"**（`dispatcher.py:506-526`：answer / reassign） |

协议无 `failed` 状态；"结果未知"不列为状态——服务端台账始终可读，不可知的只是"世界画面会不会动"（§4.2 `world_visible`）。

### 4.4 意图分流：选项 A 已获用户批准（2026-10-19）

| 选项 | 做法 | 代价 |
|---|---|---|
| **A（推荐）** | 不扩通用派单词，只扩"显式点名像素世界"的短语（像素世界/世界里/小镇/pixel world 等），且这些短语不被 `_DELEGATION` 命中；未被快环命中的世界措辞才进 AgentLoop 工具路径（需确认、有 receipt） | 需一组正反测试语料作为 G2 验收，证明既有分流零变化 |
| B | 接受两套入口：快环立即派（免确认）与慢环工具派（需确认）并存，写进产品说明 | 同一意图两种语义，用户体感不一致；无测试语料门槛 |

**A 的验收语料（已运行验证 · 2026-10-20 · `scripts/verify_world_routing_corpus.py` · 73/73 PASS · A/B 两类断言）**：
- 正例·查询类（已验证：当前无快环命中、无 `should_route` 命中 → 今天落普通对话）：去像素世界里看看她在干什么 / 像素世界现在什么情况 / 世界里的铺子都开着吗 / 去看看像素世界 / 像素世界里有新的活吗 / 把 pixel world 的状态报给我 / 刷新一下像素世界的画面 / 像素世界需不需要我帮忙 / 帮我去像素世界那边瞅瞅 / 像素世界 offline 了吗
- 正例·派单类（已验证：不被 `_DELEGATION` 命中 + 含世界短语 → 扩词后可到 `world.dispatch`）：让像素世界接一单把脚本写完 / 派一单给像素世界里的她：整理便笺 / 像素世界接一单，把便笺整理成三版标题
- 反例（已验证：均不含世界短语 → 扩词按构造不可能改变其分流；括号内为实测当前分流）：安排人把便笺做成三版（快环 delegation）/ 让她去把这份稿子改了（快环 delegation）/ 盯着这个单做完再叫我（快环 delegation）/ 进度怎么样了（快环 progress）/ 战况烧了多少 token（快环 telemetry）/ 收工（快环 accept）/ 不行打回重做（快环 reject）/ 建个任务明天下午三点评审（显式 should_route 命中，task 原路）/ 取消提醒（显式 should_route 命中，reminder 原路）/ 便笺整理一下（显式正则未命中；便笺会话路径未纳入本次核验）
- 反例·日常句（用户点名必须含；已验证：当前落普通对话、不含世界短语 → 短语级扩词后仍须不变）：世界杯开始了吗 / 你的世界观是什么 / 昨晚世界杯看了没 / 她最近世界观变了不少。**这也是"扩词必须短语级（像素世界/世界里/小镇/pixel world）、不能用裸"世界"二字"的证据——裸"世界"会误伤这四条。**
- **已验证范围（2026-10-20 · `scripts/verify_world_routing_corpus.py` · 73/73 PASS）**：
  - **A 类·当前分流**（正则级，已证，41 条）：快环五意图 + 显式 should_route + resume，正则逐字转写自 `orchestrator.py:60-76`、`service.py:95,99-101`；
  - **B 类·扩词后进 AgentLoop**：**B1 构造级**（已证）——should_route 增加世界短语后，13 条正例改判 True、14 条反例 `after==before` 逐条一致（差分铁律）；**B2 契约级 mock**（已证，非真实 AgentLoop）——mock 模型对派单正例返回 `world.dispatch`，经 `loop.py:29-47` 同款校验后**调用 ADR §4.2 形状的 stub handler**（`world_dispatch_stub(title, brief="")`），断言 receipt DTO（`id==commission_id`、`world_visible="unknown"`）、`mutation` 先过 `waiting_confirmation` 再执行、仅传 title 时经 stub handler 不 TypeError，并以**无默认值反事实签名**确认缺省必炸（证明 `brief=""` 是必要屏障，非同义反复）。
  - **未验证**：note/thread 会话自有路由正则未纳入；**真实 AgentLoop 内的工具选中仍属 S5 实现期验收**（B2 只是契约级 mock）。
- **差分测试（批准条件 C3，S5 验收门）**：除本语料外，须对**现有语料与历史消息样本**（从消息表抽样）跑改动前后 `should_route` 输出**逐条一致**；反例集必须包含"世界杯/世界观"类日常句（上文四条即基线）。
- **修正记录**：初版两条反例声称与实测不符，已改——"安排人把便笺整理成三版标题"不命中 `_DELEGATION`（"整理"不在第 2 分支动词表），换成"安排人把便笺做成三版"；"便笺整理一下"显式正则不命中，声称降级为"未纳入本次核验"。

#### 4.4.1 派单正则边界与验收语料（同义改写静默不建单 · 用户 2026-10-20 指派 A）

**既存事实（仅 live 路成立，见下文"两条路径的区分"）**：派单只由 `_DELEGATION`（`server/dialogue/orchestrator.py:60-65`）判定，第一支要求「`去(把|帮我|替我)…`」句式；不命中 → `executive_route` 返回 None（`server/app.py:697-739` 的 `if route is not None` 不成立）→ 继续走 `should_route`/普通对话 → **不建单、无任何提示**；离线模式最终以通用闲聊句收尾（`server/llm.py:247-255` 回复池，Chief 锚点 `:250` 在池内——"静默"的最终形态）。证据：S4 双重复现（Chief `c_d34c824bc869`、QA `c_e6ecc8ce2da4`；反例「帮我把…」0 帧）——**转报未验证**：工作区内全部 commissions.db 均查无这两个 ID（主库 `data/commissions.db` 239 行、最新写入 10-19 10:43，疑为隔离演示库已清理）；本轮自证见下述语料。

**验收语料（已运行验证 · `scripts/verify_world_routing_corpus.py` A5/A6 组 · 属 73/73 组成部分；进 S5 验收门，不进 1.0）**：
- 建单组（`_DELEGATION` 命中 → `executive_route` 派单）：你去把便笺整理成三版标题 / 去把脚本写完 / 安排人把便笺做成三版 / 让她去把稿子改了
- 静默组（不命中 → route=None → 不建单、无提示；**代表性抽样**，覆盖「帮我把…」「整理一下便笺」「你去把…」以外的常见同义说法，不穷尽）：帮我把便笺整理成三版标题 / 整理一下便笺 / 把这堆便笺弄成三版 / 辛苦你把便笺整理一下 / 麻烦整理下便笺 / 便笺整理成三版标题。**A6 三重断言**：不命中 `_DELEGATION` + 显式 `should_route` 不命中 + 无世界短语——三者俱全才配"静默落普通对话"标签（只验快环不命中会漏掉本应进 `should_route` 的句子）。

**差分测试新增要求（C3 扩展）**：对每个静默组反例，必须断言「**不建单 + 有可见反馈**」，或明确记录为已知限制（当前=已知限制：无反馈，见 §4.7 方案 C）。**每条断言必须标注路径（live/mock），不得只写"不建单"**——理由见下。

**两条路径的区分（用户实测发现 · 2026-10-20 · 本节最重要的一条）**：

| 路径 | 同义句（如「帮我把便笺整理成三版标题」）的命运 | 证据 |
|---|---|---|
| **live 路**（`?ledger=on`，世界吃 server 台账） | server `_DELEGATION` 不命中 → 不建单、世界无输入——"同义改写静默不建单"**只在这条路成立** | 上引 A6 三重断言（不命中 `_DELEGATION` + `should_route` 不命中 + 无世界短语）；`orchestrator.py:60-65`、`app.py:697-739` |
| **Mock 路**（`?ledger=off`，世界自己的关键词路由器） | 被 `parseOracle` 接走 → **建成 Mock 单并按沙盒计时器交付**——静默不建单在这条路**不成立** | `pixel-world/src/world/oracle.ts:23-27`（writing 规则含「便笺」「整理」）、`:33-54`（`parseOracle`）；用户实测建成并交付（Chief 转报，未附单号）；parseOracle 路由决策本轮已跑 **9/9 PASS**：6 条静默组全接走为 `dispatch/writing`、3 条对照日常句落 `unknown`（scratch `tmp/verify_mock_oracle.ts`，未入库；"建成并交付"为 simulation 行为，用户实测覆盖） |

**因此：本已知限制的验收必须指定路径，否则 Mock 路的成功交付会被误读为限制不存在。** 落地要求：① 语料表每条标 `live`/`mock` 预期（本版 A6 属 live 断言；Mock 断言见上表右列）；② §4.7 方案 C 的"已知限制"表述只覆盖 live 路——Mock 路同义句照样建成 Mock 单，演示话术必须单独说明，避免"世界很灵"（Mock）与"live 路静默"两种观感对不上；③ Mock 路断言的永久归宿应是像素世界自己的测试套件（`pixel-world/test/`，现由 scratch 临时承担；纳入属于 World Builder 范围，S5 前不动 `pixel-world/**`）。

### 4.5 `begin_commission` / `drive_commission` 重构规格（`executive.py`，S5 实施；范围已批准）

- `begin_commission(title, brief='', thread_id='', intent='', acceptance=None)`（同步）：`open → dispatch → start`，返回 `{commission_id, status, agent_id, agent_name}`（`agent_name` 显式解析，§4.2；dispatch 的 `intent/acceptance` 语义保持，`executive.py:183`）。
- **异常全部内部处理、不向 handler 抛**：`dispatcher.py:259` 抛的是裸 `LookupError`，不在 `_execute` 的 `(ValueError, KeyError)` 捕获里（`loop.py:497`）——外抛会让"选不出将"整轮落 failed，与 §6"receipt 如实报待你答复"冲突。具体：
  - `LookupError`（选不出将）：若 `drafting` 先 `transition("briefing")` 再 `block(NO_WORKER_QUESTION)`（`drafting` 只能去 `briefing|done`，E14；先例 `executive.py:319-321`），**返回 blocked 行**；
  - 其他异常同 `executive.py:202-209`（drafting→briefing；briefing/running/auditing→blocked 带请示），**返回当前行**。
- `drive_commission(commission_id)`（异步）：drive + 打回重试循环（`executive.py:185-196` 主体）；初审耗尽/适配器失败转 blocked 由 dispatcher 既有路径负责（`dispatcher.py:348-409`、`:543-548`）；**不调 `_start_shadow`、不向调用方抛**。
- `delegate()` 重构为 `begin_commission(text 标题化, text) → await drive_commission(id) → 返回终态`，**返回形状不变**（`{commission_id, status, agent_id, agent_name}`）。
- `_start_shadow`（`executive.py:210`）在 `begin_commission` 内统一调用一次——delegate 与 service wrapper 两条路径都经 begin，天然只调一次；不得放进 drive（drive 可被重复触发）。
- **验收（G2 批准条件）**：现有 executive/ledger 回归全绿（`scripts/test_executive_v3.py`、`scripts/test_agent_adapter_ext.py` 等）+ `delegate()` 返回形状契约测试；handler 只同步 begin、只把 drive 放后台——**不在 handler 里等 Worker**（真实执行 17s~数分钟，`docs/GATEWAY_DESIGN.md:19-20`，会撑爆 AgentLoop 30s 预算，E5）。同步 begin 顺带解决"选不出将发生在 receipt 之后、`agent_name` 为空、世界看不到（drafting 被丢）"。
- **`delegate()` 前后比对（批准条件 C2）**：除契约测试外，必须对重构**前后**的 `delegate()` 逐条比对——返回值字段/类型/默认值、四条路径（正常终态 / LookupError / 一般异常 / 打回重试）的可观测行为（台账落库状态、事件表追加、shadow 启动次数）——比对结果贴回本 ADR 与交付报告；任何差异要么修复要么经用户批准，不得静默。

### 4.6 设计问题（用户点名 · 2026-10-19）：为什么不能用新模块包装 begin/drive、完全不碰 `executive.py`？

**结论：不能。** begin 的开心路径可以只调 public API，但三条关键行为在 facade 公共面上不存在；纯包装层要么做不到，要么退化到把本轮已修掉的坑请回来。

| 需要的行为 | 公共面上有什么 | 卡在哪（文件:行） |
|---|---|---|
| begin：open→dispatch→start | `ExecutiveService.open/dispatch/start` 均 public（`executive.py:156-164`） | 开心路径可达 |
| begin 失败兜底（选不出将 → drafting→briefing→blocked） | facade **没有** public `block` | `delegate()` 自己也要伸手：`executive.py:200-209` 用 `self.ledger.transition` + `self.dispatcher.block`（后者在 `dispatcher.py:484-486`）。包装层只能 `executive.dispatcher.block(...)`——从新模块够取内部协作者，掏空 facade 边界，还要复刻 `drafting→briefing→blocked` 迁移顺序（`threads.py:520-521`） |
| drive + 打回重试循环 | facade 只有 `run_commission`（`executive.py:265-267` → `dispatcher.py:309-337`）：**单次**驱动、要求 running、租约冲突即 400 | 重试循环只 inline 在 `delegate()`：`executive.py:185-196`（`ledger.get` `:186/:196`、`evaluator.MAX_RETRY` `:189-190`、`dispatcher.retry_with_feedback` `:194`、`_drive` `:185/:195`）。**没有"给已有 commission id 驱动到终态（含重试）"的公共入口**——纯公共 API 包装 = 初审打回后（`dispatcher.py:543-548` 回 running）无人再驱动，单子卡 running，正是 P1-2 修掉的坑 |
| shadow 启动 | `_start_shadow` 私有（`executive.py:214-219`，delegate 在 `:210` 调），依赖 `self.shadow`（`:52`）与 `self._shadow_tasks` | 包装层调私有方法或索性不调；不调则工具派的单静默丢掉 durable shadow 运行，与 `delegate()` 行为不一致 |
| "失败不外抛"策略 | `_drive` 私有（`executive.py:227-236`）：异常吞掉、降级进台账 | 包装层复刻该策略 = 复刻核心逻辑，违反 `AGENT_PLAYBOOK` 工程纪律 1/2（Reuse First、先诊断后微创） |
| `dispatch()`/`start()` 返回形状不含 `agent_name` | `dispatch` 返回 `{"commission","packet"}`（`executive.py:159-161`）、`start` 返回台账行（`:163-164`）——都没有 `agent_name`；名字只能由私有 `_agent_name` 解析（`executive.py:238-242`，delegate 在 `:211-212` 用） | 包装层要自己解析花名册才能填 receipt 的 `agent_name`——又一"纯 public 包装做不到/做不全"的点（架构审查官补证 · 2026-10-20） |

**若坚持"新模块"**：唯一不退化的做法是新模块只放工具 handler + 任务集合，同时在 `executive.py` 增加一个 public `drive_commission`（包住既有循环）——但这仍然碰 `executive.py`，且不减少改动量，只是把抽取出的代码换个文件放；按 Reuse First，工具 handler 放 `service.py:64-89`（与既有 12 个工具同源）比新开模块更符合现有架构。**ADR 现案（模块内抽取 + `delegate()` 改为组合 begin/drive，行为保持）即为最小正确解。**

### 4.7 静默降级的可见提示（评估 · 用户 2026-10-20 指派 B · 只评估、不出代码）

背景：§4.4.1 静默组——用户说"帮我把便笺整理成三版标题"，快环不建单也无提示，最终以普通对话收尾。三个方案的评估：

| 方案 | 改动面（文件:行） | 回归风险 | 验收方式 | 需用户批准 |
|---|---|---|---|---|
| **A 快环侧提示**：`executive_route` 返回 None 但命中"弱委派特征"时回一句明示（"这句像派单，但没派下去；要我派就说『去把…』"） | `server/dialogue/orchestrator.py`（弱委派识别 + 新 route kind，如 `delegation_hint`）+ `server/app.py:727-732`（kind 分支：只发文本、不 spawn delegate；复用 `_present_reply` 既有路径，**无新协议事件**）；**或 hint 逻辑下移 service 层**（+1 文件 `server/assistant/service.py`：在 `should_route`（`app.py:740`）之后、`assistant.start` 之前才看得见便笺/事项路由状态） | **高**：① **干净让位做不到（除非下移 service 层）**——便笺/事项路由发生在 `assistant.start` 内部（`service.py:383,391` 的 note/thread conversation 判定），orchestrator 拿不到该状态；插在快环的 hint 会劫走「帮我把便笺改一下」这类本应进便笺会话的句子；② 与 12 个既有工具的交互——提示轮次不得触发工具；③ 需独立正反语料 + 差分 | 每条弱委派句→出提示且不建单；既有 73 条语料 + 历史样本路由输出逐条不变；演示脚本可演 | **是**（`server/**`，主产品语音路径行为变更） |
| **B 像素世界侧提示**：调试面板/状态行显示"派单未建单" | **不可行（如所述）**：世界只能收 `commission_update` 帧与 REST 快照（`ledger.ts`），"没建单"是**非事件**，任何通道都不会通知世界；`?ledger=off` 时更无从得知。server-free 唯一变体是在世界侧复制派单正则（`oracle.ts:31-51` 有自建关键词路由先例）——意图判定双真相源，违反协议单一事实来源纪律，且神谕面板输入是界内操作、覆盖不了麦克风原话 | 变体=高（双真相源漂移） | n/a（不采纳） | 世界内不需要；若要世界可知，必须服务端广播新事件 → 回到 A 的改动面 + 协议评审 |
| **C 不加提示 + 文档声明 + server-free 静态声明（现状 + 新变体，已并入 C）** | `docs/`（演示脚本/FAQ；Role 0/Chief 领地）+ **世界页状态行固定一行**「这里只显示已建单；刚派的单没出现，请回主界面确认」：`pixel-world/index.html:213`（`#status` 静态落点）/ `pixel-world/src/scenes/SandboxScene.ts:497 statusLine()` 固定补充行——零检测、零 server、零回归，使"只改 `pixel-world/**`"的选项有可核清单 | 零 | QA 复核：文档声明与实测一致（静默组→普通对话、无帧、无提示）；静态行在 `?ledger=on/off` 两模式均在且不随状态变化 | 否（`pixel-world/**` 内 + docs） |

**推荐：C（1.0/S5 最小闭环，含静态声明变体）**。理由：零回归、零协议变更，且 G2 范围预批准只覆盖 5 处世界桥接编辑、**不含** orchestrator/service 语音路径的行为变更——推 A 等于推荐一个未获批准的主产品改动；§4.4.1 差分要求（"不建单 + 有可见反馈"**或**记录为已知限制）以"已知限制 + 静态声明"形式闭合。

**关于 A 的触发条件（按复审更正）**：S4 双重复现（Chief `c_d34c…`、QA `c_e6ecc…`）证明的是**现象存在且可复现**（技术事实），不是"真实用户因此误解/卡住"（产品证据）——原触发条件"若 S4 演示中真实用户被卡住"与之矛盾，已废除。新触发条件：**外部演示 / 真实用户自由输入再次命中静默组且造成误解（须附日期 + 单号或录屏）**。触发后按 A 实施，门槛 = 用户批准 + hint 下移 service 层（或接受高回归风险并明示）+ 独立语料 + 差分全绿。用户若在知悉本评估后直接选择 A，亦是合法决策（A 本就必须用户批准）。

**B 不采纳**（世界对非事件不可感知；server-free 变体 = 双真相源）。

## 5. 上行回执选型比较（S5 备料，本 ADR 不落地；U1 已按复审改判）

| 方案 | 做法 | 证据/成本 | 判定 |
|---|---|---|---|
| U1 | 复用 commission 写路由 | 路由与同源校验已存在（`executive_api.py:78-107`）；世界页面走 Vite 代理同源（`pixel-world/vite.config.ts:10-18`），写校验可通过 | **收窄**：仅用于"世界 UI 里代老板点验收/打回"这一种交互。`/accept` 是老板验收权、`/submit` 是 Worker 交 artifact——世界既不是老板也不是执行体，通用回执=借权+伪造 delivery，不用作世界上行 |
| U2 | 新增 WS 事件（如 `world_event`）由世界广播 | 需改 `docs/PROTOCOL.md` + `check_protocol.py` + 用户批准；世界原生事件（`task.delivered`、`world.rained`，`bridge.ts:26-33`）表达不成 commission 迁移 | **要回执就得 U2**：门槛不是"无法表达为迁移"，而是"只要世界需要向 Agent 回传事件，就必须 U2 + 协议评审" |
| U3 | `bridge.ts` 运输层（postMessage / 新本地端点） | 新服务面或宿主页耦合；`Bridge` 未被实例化（E10） | 不推荐：与 U2 重复且引入新边界 |

最小闭环保持世界只读（E8：`README.md:84-90`、`:108`）。

## 6. 失败模式与回滚

| 失败模式 | 行为 | 依据 |
|---|---|---|
| 同 run 内重复派单 | Loop 层 hash 去重 → receipt `skipped/already_executed`；驱动层租约拒绝第二个执行体 | E4；`PROTOCOL.md:344` |
| **跨轮/换措辞重复派单** | hash 去重**不保护**（仅同 run 内），台账 `create` 不去重（`threads.py:618-625`）。缓解：派单前按 title/thread 在 TTL 窗口查进行中的单，命中则回已存在的 `commission_id` 而非开第二张；或引入 `run_id+action_hash` 幂等键（S5 评估） | E4 复审补 |
| Worker 失败/选不出将 | 适配器失败/初审耗尽由 dispatcher 转 `blocked`（`dispatcher.py:348-409`、`:543-548`）；`begin_commission` 自身的 LookupError/异常走 §4.5 的 briefing→blocked 路径（`executive.py:197-209` 只覆盖 delegate 原路径）。receipt 如实报"待你答复"，不谎报成功 | §4.5 |
| 漏推/断线 | 旁路通知不补发，REST 为真相；`world.status` 读 REST 不受影响 | `PROTOCOL.md:351-354` |
| **后台任务异常** | `create_task` 须进任务集合并 try/except → 失败转 `blocked` 写请示（经 §4.5 的合法迁移）；否则单子卡 `running` 只留 warning | 复审补 |
| **打回后无驱动** | `/run` 只跑一次，打回回 `running` 后停摆；由 `drive_commission` 的重试循环覆盖 | `dispatcher.py:309-337,543-548`；§4.5 |
| **取消** | 用户说"算了"停不掉已派单；服务端有 `/cancel`（`executive_api.py:91-94`）。快环 `_executive_finish` 已能处理验收/打回（`server/app.py:955-961`），但取消仍只走主 UI/`/cancel`；最小闭环不加第三个工具，`world.cancel` 是否进 1.0 留 S5 | 复审补 |
| **超时/重启** | `/run` 无超时，适配器挂住则永远"执行中"。重启收口（`recover_interrupted`，`executive.py:282-332`）：IN_FLIGHT（briefing/running/auditing）直接 blocked（可选 durable 对账 adopted），`drafting` 重派续跑（resumed）；Commission 无 `interrupted` 状态（E14）。`app.py:1374-1383` 是 subscribe/recover 挂载位置 | 复审补 |
| 世界浏览器未开 | 服务端工具不可见（连接是浏览器主动建立的）：receipt 仍为"已入台账"，`world_visible: "unknown"`，世界不会动。已知限制，演示脚本需声明 | E7 |
| richer 操作请求（下雨/天黑） | 最小闭环**不支持**，工具层直接拒绝并说明原因，不猜不降级 | E11、D3 |

**回滚**：删除工具条目与 5 处服务端编辑（`service.py` 四处 + `executive.py` 重构）即回到现状；无数据迁移、无协议变更、台账事件表只追加不改写。

## 7. 边界与批准状态

| 门 | 内容 | 状态 |
|---|---|---|
| G1/D2 | 完整双向 Agent 集成属 1.1，本文为 S5 备料，不推进到 1.0 | 已裁定，遵守 |
| G2 | `server/assistant/**` 5 处编辑（D1-附） | **范围已批准（预批准，2026-10-19）**；实施待 S5，条件见 §7.1 |
| 意图分流 | §4.4 选项 A：只扩显式点名世界的短语 + 语料/差分验收 | **已决：A（用户 2026-10-19）** |
| 协议 | 本文零新 WS 事件/API 字段；S5 若要世界事件回执，先走 `docs/PROTOCOL.md` 评审（U2） | 无待决协议项 |
| 产品 | 世界内验收/打回（E12）是否做；`world.cancel` 是否进 1.0 | 待用户 |

### 7.1 批准记录与实施条件（用户 2026-10-19 · 范围预批准，非 diff 批准）

| # | 条件 | 落点 |
|---|---|---|
| C1 | S3/S4 收口前**不得动 `server/assistant/**`**；S5 开工前重新贴 `git status`——若 `executive.py` 的 durable 改动仍未提交，**停下报告**，不许带着混合归属开工（2026-10-19 实测：`executive.py` 未提交 136+/5-，见 `git diff --stat`；用户口径"155 行"与实测的差为 durable 线推进所致，执行以"存在未提交 durable 改动即停"为准） | 流程门 |
| C2 | `delegate()` 重构**前后逐条比对**（字段/类型/默认值/异常路径），结果贴出 | §4.5 |
| C3 | 分流验收加**差分测试**：现有语料 + 历史消息样本改动前后 `should_route` 输出逐条一致；反例必含"世界杯/世界观"类日常句 | §4.4 |
| C4 | 两个新工具只能经 `delegate()` 同款路径派单；**不得**直接标记完成、**不得**发神恩 | §4.2 |
| C5 | 后台任务：强引用集合、异常日志、关闭时取消、并发上限（值=2，§4.2） | §4.2 / D1-附-3/4 |
| C6 | 提交纪律：单独提交、按路径 add、**提交前贴 diff 给用户批准**；不得 stash/reset/checkout/clean | 流程门 |
| C7 | 本 ADR 状态=「范围已批准，实施待 S5」，批准日期与条件清单在档 | 本节 |

## 8. S5 排期依赖

`NEXT_PHASE_PLAN.md`：S5（真实 Agent 双向集成）排在 S3/S4 之后，主负责人 Agent 集成工程师。前置链：S3/S4 收口 → **C1 门**（重贴 `git status`，durable 改动未提交则停）→ 实施 5 处编辑 → **C2/C3 验收**（delegate 前后比对 + 分流差分）→ 世界事件回执方案（U2 + 协议评审）→ QA 独立复验（真帧端到端，禁注入帧）→ **C6 提交门**（diff 用户批准后单独提交）。

## 9. 结论

最小闭环（Agent 派单 → 世界执行 → 台账观察）**用现有通道即可成立：零新协议项、零新 REST/WS 端点、零 `pixel-world/src` 改动**；服务端共 5 处编辑（全在 `server/assistant/**`，S5 实施）：两个工具 + `should_route` 短语级扩词（A 方案，语料 73/73 已验证 + 差分门）+ `__init__`/`close()` 后台任务纪律（强引用/日志/取消/并发上限 2）+ `begin_commission`/`drive_commission` 行为保持型重构（修掉打回后停摆，`delegate()` 返回形状不变；"新模块包装零碰 executive.py"经 §4.6 逐行论证不可行）。上行回执不做通用化：世界保持只读，U1 仅收窄为"世界 UI 代老板点验收/打回"，世界原生事件回执留 U2 + 协议评审。天气/时间/造物等 richer 操作今天无入站通道，明确留在 S5。
