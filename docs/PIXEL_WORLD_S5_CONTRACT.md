# 像素世界 S5 服务端接入合同

- **状态**：**服务端已落地**（2026-10-20）；世界端真帧 e2e 进行中（2026-10-21：A/B/C/E/F/G/H + blocked 可见通过，**D 未达成**，根因是 worker 单次 prompt 421K > 120K，owner=Agent Integration；验收卡见 `docs/S5_D_ACCEPTANCE.md`）。本文是交接合同，不是新协议。
- **服务端落地**：`server/assistant/executive.py`（`begin_commission` / 公开 `agent_name`，`delegate` 改用 begin+drive）、`server/assistant/service.py`（`world.dispatch` / `world.status`、世界短语路由、`_world_tasks`、并发 2、close 取消）。回归 `scripts/test_world_bridge.py` 71 项全绿。
- **真实模型探测（2026-10-20，临时库）**：`scripts/probe_world_live.py` 12/12 通过——世界派单句真正选中 `world.dispatch` 并先停在确认门；批准前台账没有新单、批准后恰好一单；回执 `world_visible="unknown"`；状态句选中 `world.status` 并返回指定单；普通提醒请求没有误选世界工具。
- **日期**：2026-10-20
- **适用**：World Builder、Agent Integration、QA、Role 0
- **上游**：`tmp/wt-pixel-world/docs/ADR_AGENT_BRIDGE.md`（范围批准版 v2.7）、`docs/PROTOCOL.md`、`docs/PIXEL_WORLD_S5_HANDOFF.md`（如有）

## 0. 阶段与命名

- durable 迁移的 C1–C4 已收口；**目前没有定义 C5**。
- 本文说的是**像素世界接入 S5**（`NEXT_PHASE_PLAN.md` 的 S5）。
- ADR 里的 “C5” 是**后台任务纪律**这条批准条件，不是阶段名。两个团队沟通时统一写 **S5**。

## 1. 一句话边界

**服务端开单并驱动，台账是唯一真相；像素世界只读投影，不是第二个台账、不是执行体。**

- 世界页面只消费现有 `GET /api/executive/commissions` 与 WS `commission_update`。
- 世界不能直接标记完成、不能调用 `/accept`、`/submit`、不能写 `commissions.db`。
- 世界内的验收/打回、天气/时间/造物、取消工具都**不在 S5 最小闭环**。

## 2. 服务端交付

### 2.1 `world.dispatch(title, brief="")`（mutation）

- 用途：把一件明确的活交给像素世界背后的督办台账；世界页面在线时投影出来。
- `title` 必填，`brief` 可省略（handler 必须有 `brief=""` 默认值，`loop.py` 的 `handler(**arguments)` 不会补默认）。
- **必须过现有 mutation 确认门**：模型先收到 `waiting_confirmation`，老板确认后才执行；模型无权自批。
- 成功回执（receipt DTO）：

```json
{
  "id": "<commission_id>",
  "commission_id": "<commission_id>",
  "status": "drafting|briefing|running|blocked",
  "agent_name": "笔录官",
  "world_visible": "unknown"
}
```

- `id == commission_id`；`world_visible` 在 S5 恒为 `"unknown"`——服务端只知道有 WS 连接，分不清是不是像素世界页面、也分不清 `?ledger=off`。
- **文案纪律**：只能说“已入台账”，不能说“她已经在世界里开工”。世界画面是否真的在动，服务端不可知。
- 忙时回执（不建单）：`{"status": "busy", "commission_id": "", "agent_name": "", "world_visible": "unknown", "retryable": true, "message": "世界正在收尾两单，这单先不派，等一会儿再试。"}`

### 2.2 `world.status(commission_id)`（只读）

- 只返回显式投影，不返回整行（整行里的 `packet.memory_block` 含私密记忆）：

```json
{
  "found": true,
  "id": "<commission_id>",
  "status": "blocked",
  "agent_name": "笔录官",
  "blocked_question": "……",
  "delivery_summary": "……",
  "updated_at": 1760000000.0,
  "context_is_untrusted_data": true,
  "grants_authorization": false
}
```

- 查无此单返回 `{"found": false}`，不抛裸 `KeyError` 把整轮 AgentLoop 打成 `failed`。

### 2.3 `ExecutiveService.begin_commission` / `drive`

- `begin_commission(title, brief="", thread_id="", intent="", acceptance=None)`（同步）：
  `open → dispatch → start`，返回 `{commission_id, status, agent_id, agent_name}`。
- 异常全部内部处理，不向工具 handler 抛：
  - 选不出将（`LookupError`）：`drafting → briefing → blocked`，带 `NO_WORKER_QUESTION`，返回 blocked 行；
  - 其他异常：`drafting → briefing`，`briefing/running/auditing → blocked` 带请示，返回当前行。
- `drive(commission_id)` **已存在**（C2 加的，含初审打回重试循环），S5 直接复用，不再新造 `drive_commission`。
  - ADR §4.5 的“打回后停摆”由这个循环覆盖；实施时只做 `delegate()` 的 `begin + drive` 组合，保持返回形状不变。
- `_start_shadow` 只在 `begin_commission` 内调用一次；`delegate()` 不再在驱动结束后重复调用。
- `delegate()` 前后逐条比对（字段/类型/默认值/异常路径/台账落库/shadow 次数），差异写进交付报告。

### 2.4 意图分流（Approved A）

`should_route` 只扩**显式点名像素世界**的短语，匹配必须短语级：

```text
像素世界 | 世界里 | 小镇 | pixel world（大小写不敏感）
```

- **不得**匹配裸“世界”：`世界杯`、`世界观` 等日常句必须保持原分流。
- 正例（派单/查询）进 AgentLoop，由模型选择 `world.dispatch` / `world.status`；快环 `_DELEGATION` 先命中者保持原语义，不重复派单。
- 差分铁律：除新增世界短语外，现有 `should_route` 与快环五意图对既有语料逐条不变。
- 语料与断言复用 `tmp/wt-pixel-world/scripts/verify_world_routing_corpus.py`（73/73），落地到主仓测试时直接 import 真实正则，不转写副本。

### 2.5 后台任务纪律（ADR C5）

- `self._world_tasks: set[asyncio.Task]` 强引用，`add_done_callback(discard)`。
- `service.close()` 取消所有未完成任务。
- 并发上限 `WORLD_DRIVE_CONCURRENCY = 2`；超过时返回 busy 回执，不建第三单。
- 驱动异常：记服务端日志（只记异常类型，脱敏），经合法迁移落 `blocked`，不裸奔。
- handler **只同步 begin，只把 drive 放后台**；不在工具轮次里等 Worker（会话预算 30s，真实执行可达数分钟）。

## 3. 世界团队可依赖 / 不可做

**可以依赖**

- 台账 REST 全量 + WS 增量，字段以 `docs/PROTOCOL.md` 与真实响应为准。
- `commission_update` 的现有形状；断线用 REST 对账，旁路通知不补发。
- `?ledger=on` 是验收路径；`?ledger=off` 是本地 Mock 路径，不能代表接入成功。

**不可做**

- 不新增 WS 事件、不改 REST 字段；需要世界 → Agent 的回执先走 `[PROTOCOL_REQUEST]` / U2 评审。
- 不直接写台账；不调用 `/accept`、`/submit` 冒充老板/Worker。
- 不把 `drafting` 当“已接单”显示（世界投影会丢弃它，画面要等 `briefing`）。
- 不在世界侧复制派单正则或另造意图判定（双真相源）。
- 不把 Mock 路建成并交付的单当作 live 路证据。

## 4. 验收矩阵

| 层 | 断言 | 命令/证据 |
|---|---|---|
| 服务端单元 | `begin_commission` 正常 / 无将 / 异常；`delegate()` 形状不变；shadow 恰一次 | `scripts/test_world_bridge.py` |
| 工具契约 | receipt `id==commission_id`、`world_visible="unknown"`、仅传 title 不 TypeError、mutation 先确认 | 同上 |
| 状态投影 | 只含公开字段；无单 `{found:false}`；不含 packet/memory | 同上 |
| 路由差分 | 13 正例 `should_route` 改判 True；14 反例 before==after；快环五意图不变 | 同上 |
| 后台纪律 | 强引用、并发 2、close 取消、异常落 blocked | 同上 |
| 协议 | 零新事件/零新 REST 形状 | `python scripts/check_protocol.py` |
| 真实模型 | 派单/状态句选中对应工具；确认门前不落单；普通请求不误选 | `scripts/probe_world_live.py`（手动，产生 API 费用） |
| 世界端 | `tsc --noEmit` + 世界测试 + smoke | `pixel_world` 门禁（S0-6，待接入根 `run_gates.py`） |
| 真帧 e2e | `?ledger=on`：派单可见 → blocked 可见 → 完成/撤单 → 重连 | 录屏 + 日志 + 断言 |
| 全量 | `test_core.py` + `npm test` + 根门禁 | `python scripts/run_gates.py` |

**世界端验收必须标路径（live/mock）**；同义句在 `?ledger=off` 会被 `oracle.ts` 接走并建 Mock 单，这是已知差异，不作为 live 路证据。

## 5. 已知限制（S5 不修）

- 世界没开时，派单仍然会进台账并执行；`world_visible` 只能是 `unknown`。
- 浏览器世界在线状态不可确认；不要承诺“画面上一定在动”。
- `world.status` 需要 `commission_id`；S5 不提供全量列表。查询类话语由模型基于上下文回答，或请老板看世界页面。
- 同义改写（“帮我把便笺整理成三版标题”）在 live 路仍可能静默落普通对话；这是已记录的已知限制，S5 不扩通用派单词。
- 世界内验收/打回、取消工具、天气/时间/造物不在 S5。
- durable 侧的长任务压缩、2h/24h soak 与本合同独立；不要在同一窗口改 `server/assistant/executive.py` 的 durable 段。

## 6. 分工、合并与开工前置

| 事项 | Owner | 说明 |
|---|---|---|
| `server/assistant/service.py`、`server/assistant/executive.py` | Agent Integration（本主仓） | 外部世界团队不直接改；需要时提 PR + 服务端 owner 评审 |
| `pixel-world/**`、世界测试 | World Builder | 只在自己的分支/目录内改 |
| 真帧 e2e / 独立复验 | QA | 不接受“文档说通了” |
| 协议、合并、tag | Role 0 | 零新协议项也要走 Integration Check |
| `scripts/run_gates.py` 的 `pixel_world` 门禁 | Role 0 + QA | S0-6 先接，再谈 S5 |

**唯一权威代码树**：主工作区的 `pixel-world/` 目前只有 `eval/` 与 `node_modules/`，真实源码在 `tmp/wt-pixel-world/` / `feat/pixel-world-oracle-world`。S5 开工前必须先定：合入主仓、submodule，还是独立仓；不要两边各改一份。

**C1 前置门（ADR §7.1）**：已通过。C4 的 durable/授权改动先作为独立提交 `c60c374` 落库（未带视觉/自进化并行改动），S5 的 `executive.py`/`service.py` 改动在其后单独成层。
