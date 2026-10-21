# Pi Durable × 小溪慢环：迁移实施建议

> 范围：`server/assistant/*` + `server/interaction/threads.py` + `config/agents_roster.yaml` 构成的幕后慢环（System 2）。
> 依据：[Pi Durable 发布文](https://earendil.com/posts/pi-durable/)、[pi-durable README](https://github.com/earendil-works/pi/blob/main/packages/durable/README.md)（npm 当前最新 `1.3.0`，`engines.node >=22.19.0`）、本仓库 2026-10-17 工作树。
> 性质：架构建议，不改代码。涉及 `docs/PROTOCOL.md` 与角色边界的部分按 `docs/AGENT_PLAYBOOK.md` 由 Role 0 裁决。

---

## 0. 结论

**不要整体替换慢环；把 Pi Durable 接成慢环的执行内核。**

具体做法：新增一个 Worker 适配器 `adapter: durable`，让"长任务执行体"这一段跑在 Pi Durable 上；Commission 六态状态机、确认单、初审、遥测、记忆装配、协议出口全部留在 Python。

三条理由：

1. **慢环真正的失败域是执行体，不是调度记账。** 秘书循环只有 30 秒预算、4 步、3 次工具；派单链路里 Python 只做组密件、推进状态机、收事件，本身很难跑死。真正会跑死的是外部 Worker（CLI 子进程、远端 Agent、MCP），以及进程重启后没人接手的长任务。这恰好是 Pi Durable 的强项（每步 checkpoint、重启续跑、`replay` 策略、取消树）。
2. **慢环的语义层是护城河，Pi Durable 没有对应物。** JSON 决策契约（`_decide`）、单次确认绑定 action hash、三档置信度记忆装配 + 空检索负向约束、L1/L2 初审、鬼打墙熔断、Commission 六态——挨个搬过去等于重写，换不来任何持久性收益。
3. **协议不允许第二套客户端协议。** Pi Durable 的 `viewState()/watch()` 是给多客户端用的；本项目单用户本地，浏览器只认 `PROTOCOL.md` 里既有 WS 事件。Python 必须继续是唯一说话者，Pi Durable 只能是内部实现细节。

**如果目标只是"崩溃后别丢活"**，B1 的 Python job 表也能做，约 2 周。B2a 用同样的时间换到工具级 replay 与每单隔离。fork、subagent、中途 steer、多端这些当前不是痛点，所以 B2b 与 B3 先不做。决策表见 §7。

一句话：**把 Pi Durable 当"可靠的手"，不要当"大脑"。**

先按目标分岔，再看后文：

| 你的真实目标 | 建议方案 | 预估 | 章节 |
|---|---|---|---|
| 进程崩了别丢活、别重复执行 | B1：Python 内 durable job 表 | ~2 周 | §7 |
| 上述 + 每单独立 Agent、取消树 | **B2a：每单 Node runner** | ~2 周 | §7.1 |
| 上述 + 中途 steer、共享 task graph | B2b：常驻 sidecar（§4 的形态） | 4–6 周 | §4–§6 |
| 多端 / 多人共同 steer 同一会话 | B3：整体替换，conversation 为第一记录 | 10–14 周 | §5 末 |

---

## 1. 现状基线（事实）

慢环核心（不含台前快环）约 5,300 行 Python，连同快环共 6,794 行；配套约 7,000 行测试：

| 层 | 位置 | 行数 | 持久化 | 关键保证 |
|---|---|---:|---|---|
| 台前快环 | `server/dialogue/orchestrator.py` | 1,533 | — | 4 类正则意图，不发第二次模型请求；≤1.5s 反馈 |
| 总管门面 | `server/assistant/executive.py` | 262 | — | 唯一同时握有调度/遥测/初审/台账的接线层 |
| 派单 | `server/assistant/dispatcher.py` | 539 | — | 组密件、选将、驱动状态机 |
| Commission 状态机 | `server/interaction/threads.py` | 727 | `data/commissions.db` | 6 态 + 只追加事件；非法迁移抛 `ValueError` |
| 执行体适配 | `server/assistant/adapters/` | 535 | — | `prepare_context` / `execute` / `cancel` 三方法；新增 Worker 零改核心 |
| 观测 | `server/assistant/telemetry.py` | 453 | `data/telemetry.db` | span 表字段固定（协议 §20）；鬼打墙熔断 |
| 初审 | `server/assistant/evaluator.py` | 398 | `data/executive.db` | L1 客观 + L2 判例，最多打回 2 次 |
| 秘书循环 | `server/assistant/loop.py` | 971 | `data/agent_runtime.db` | 4 步 / 3 工具 / 30s；单次确认 CAS；中断不重放写操作 |
| 运行时 | `server/assistant/runtime.py` | 195 | 同上 | checkpoint / 事件 / 确认 / 澄清等待（TTL 900s） |
| 上下文 | `context.py` + `interaction/context_budget.py` | 573 | — | 字符预算、记忆三档、显式负向约束 |
| 任务/提醒 | `assistant/store.py` + `api.py` 调度器 | 415 | `data/assistant.db` | 每 2 秒扫到期提醒，不调用模型 |

慢环状态散在 5 个 SQLite 库里（`assistant` / `agent_runtime` / `commissions` / `telemetry` / `executive`），另有 `memory` / `notes` / `threads` / `preferences`。**没有跨库事务**，这是现状的既成事实，也是迁移时必须承认的约束。

迁移必须原样保留的既有不变量：

- 快环不阻塞：委托先说完接单句，再起后台任务（`app.py` 的 `conn.spawn`）。
- 写操作单次确认：绑定 action hash、`run_id`、600s TTL、CAS 消费。
- 中断不重放可能已产生副作用的动作（`recover_interrupted()` 只标 `interrupted`）。
- 主舞台顶栏 0 新增按钮；浏览器只说既有 WS 事件。
- 新增 Worker = 改 `config/agents_roster.yaml` + 写适配器，调度核心零改动（`test_agent_adapter_ext.py` 机器断言）。
- 协议 §19 的 Commission 形状、§20 的 span 字段、§22 的五种适配器事件不得漂移。

---

## 2. 为什么"直接替换"会失败：五个断点

### 2.1 决策契约不兼容：JSON action vs 原生 tool calling

当前秘书循环的模型契约是一段严格 JSON：

```python
# server/assistant/service.py:_decide
'动作只有 {"action":"final"或"clarify","text":"中文回复"} '
'或 {"action":"tool_call","tool":"已注册工具名","arguments":{}}。'
```

模型不看见工具 schema，只在 JSON 里"点名"工具；`arguments` 由 `Tool.validate` 严格校验；`clarify` 会把 run 挂成 `waiting_input`，等同一 session 续答；写操作先进 `waiting_confirmation`，确认后才执行。

Pi Durable 的模型侧是原生 function calling：`defineTool` 生成 schema，模型直接产出 tool call，`GenerationTask` 自己循环，工具结果是 `pi.tool-result` entry。**没有"decision schema"这一层。** 要把现有契约搬过去，只有两条路：

- 用一个 `decide` 工具包住 JSON 决策——模型仍然只说 JSON，等于把 Pi Durable 降级成"更贵的 HTTP 客户端"，没有收益；
- 改成原生 tool calling——`_decide` 的 prompt 全部重写，`clarify`/`waiting_confirmation`/`observations` 语义重建，`evals/` 与 `test_assistant_*` 基线全部重跑。

**结论：秘书循环不迁。** Worker 循环本来就没有 JSON 契约（适配器自己决定怎么干活），迁过去是顺的——这也是为什么应该从 Worker 开始。

### 2.2 状态权威：单进程 5 个库 → 双进程两份真相

现状是**一个 Python 进程、5 个库、无跨库事务**。一次 Commission 的 `running → auditing` 要同时写 `commissions.db`（状态 + 事件）和 `telemetry.db`（span），两库没有事务；秘书循环的 checkpoint（`agent_runtime.db`）与它要执行的外部动作也不在同一个库里，只能靠"先落检查点再产生副作用 + `completed_actions` 去重"兜底。

换成 Python + Node 双进程后，任何一次状态推进都变成跨进程调用。Pi Durable 的"transcript + documents 同一次原子 commit"只有在**它拥有状态**时才值钱；如果 Python 仍然拥有 Commission 台账，那 harness 的原子提交只覆盖它自己那份，跨进程仍然要靠 saga/幂等/对账。

两条所有权路线：

| 路线 | 真相在哪 | 好处 | 代价 |
|---|---|---|---|
| A. harness 拥有执行状态与文档 | `data/durable/agent.sqlite` | 真正原子、可 fork、可回放 | Commission/确认/审计要么搬进 documents（协议与测试重写），要么长期对账 |
| **B. harness 只拥有"执行中"状态（推荐）** | Python 台账为权威 | 改动小、可逐 Agent 回退 | 需要幂等 + 启动对账；harness 的 documents 只存临时状态 |

路线 B 的硬要求：**所有 harness → Python 的副作用必须幂等**（用 `commission_id`/`run_id` 做键，Python 侧 CAS 消费）。项目已经有这个传统（确认单 CAS、`requestId` 幂等），继续沿用即可。

### 2.3 上下文装配：能承载，但要过桥

慢环的 prompt 不是"一段系统提示"，而是 `context.py` + `context_budget.py` + 记忆检索 + `response_policy` 组合出来的投影：三档置信度（`stated` / `inferred` / `uncertain`）、检索为空时的显式负向约束、按字符预算整条丢弃而不是截断、凭据脱敏。

Pi Durable 的 `section(name, fn)` 正好是"每请求重渲染"的挂载点，可以把 Python 侧渲染好的块塞进去。但要注意：

- **section 每请求都变会打穿 prompt cache。** 记忆块应按 turn 缓存，同一 turn 内稳定；Briefing Packet 这类不变内容放 `instructions` 或独立 section，避免被 compaction 吃掉。
- **失败必须显式降级。** 记忆桥超时/不可用时，不能返回空字符串了事——按 AGENTS.md，空检索必须注入负向约束。桥的实现要区分"没查到"和"没查成"。
- **compaction 会摘要旧消息。** 密件、约束、验收标准必须放在 section/instructions 里（不被摘要），不能只留在第一条 user message 里。

### 2.4 协议与多端：能力用不上，风险实打实

`watch()` 交付的是 commit 操作流，`viewState()` 是完整视图快照——两者都要求客户端理解 Pi Durable 的数据模型。浏览器侧现在只认 `send_turn` 信封里的既有事件；新增一套事件属于协议变更，`AGENT_PLAYBOOK` 明令禁止"自造 WS 事件"，而 `check_protocol.py` 只把"服务端发了、前端没处理"列为**提示**而不是错误——也就是说这条路一旦走偏，门禁不会替你拦，只能靠人守。

在单用户、本地、单进程的产品形态下，多客户端/多人 steer 的能力收益接近零，但引入的传输层、鉴权、重连语义是实打实的维护成本。

### 2.5 恢复语义冲突

| 场景 | 现状 | Pi Durable 默认 | 迁移要求 |
|---|---|---|---|
| 进程死在工具调用中 | 标 `interrupted`，不重放 | `replay:"safe"` 才重跑；否则给模型 `interrupted` 结果 | 逐工具审计 `replay`，写操作一律不 safe |
| 进程死在模型请求中 | 标 `interrupted`，不重发 | 重发截断请求，部分回答标记 aborted | 可接受；要计入成本与"重复半句" |
| 进程死在确认等待中 | run 标 `interrupted`，确认单过期后拒绝消费 | hook 随 task 一起死 | 重启后作废确认单，转 BLOCKED，不自动批准 |
| 提交产物后崩溃 | 台账已进 `auditing` | 工具重跑可能重复提交 | Python 侧按 `(commission_id, delivery hash)` 幂等 |

---

## 3. 逐项适配矩阵

| # | 现有能力 | 位置 | Pi Durable 对应物 | 裁决 |
|---|---|---|---|---|
| 1 | Commission 六态 + 只追加事件 | `threads.py` | 无（task 只有 pending/running/waiting/completing + 终态 outcome） | **保留**，task 只是执行载体 |
| 2 | Briefing Packet 组包/取景框 | `dispatcher.py` / `commission_context.py` | `instructions` + section | **保留组装逻辑**，改注入方式 |
| 3 | Worker 执行（CLI/HTTP/MCP） | `adapters/*` | conversation + tool tasks + env | **采用**（首个落点） |
| 4 | 适配器三方法契约 | `adapters/base.py` | 无 | **保留**，新增第四种实现 |
| 5 | 单次确认 + action hash + TTL | `runtime.py` | `hook(beforeTool)` + `api.memo` | **改造**：审批权威留 Python |
| 6 | 中断不重放 | `runtime.py` | `replay` 策略 | **映射**：默认 no-replay |
| 7 | 秘书 JSON 决策循环 | `loop.py` / `service.py` | GenerationTask + 原生工具 | **暂不迁**（阶段 4 再议） |
| 8 | `clarify` / `waiting_input` | `runtime.py` | `submit()` 队列 / steer | **保留**（Phase 1 不支持内部澄清） |
| 9 | 提醒准点调度 | `api.py` scheduler | durable task timers | **保留**：准点性优先，别押在可重启的 Node 上 |
| 10 | 自进化 cycle | `loop.py` | background task | **暂缓** |
| 11 | 遥测 span / 鬼打墙 | `telemetry.py` | `pi.usage` + tool result usage | **保留**，harness 用量回填 span |
| 12 | L1/L2 初审 | `evaluator.py` | 无 | **保留**（确定性业务逻辑） |
| 13 | 记忆三档装配 | `memory/*` + `context.py` | section 桥 | **保留** |
| 14 | WS/REST 出口 | `app.py` / `PROTOCOL.md` | `viewState/watch` | **保留**：Python 唯一说话者 |
| 15 | 存储与备份 | `data/*.db` + `backup_local_databases.py` | 新增 `data/durable/agent.sqlite` | **新增并纳入备份** |

---

## 4. 目标架构

```
浏览器 ──既有 WS/REST（PROTOCOL.md，零新增事件）──┐
                                                   ▼
                                   ┌──────────────────────────────┐
                                   │ Fast loop  orchestrator.py    │  ≤1.5s
                                   │ 委托/进度/验收/战况（正则）    │
                                   └──────────┬───────────────────┘
                                              │ conn.spawn(_executive_delegate)
                                              ▼
        ┌─────────────────────────────────────────────────────────────┐
        │ Python 慢环（权威）                                          │
        │ ExecutiveService → Dispatcher → Commission 六态（commissions.db）│
        │ 选将 → Briefing Packet（memory/commission_context.py）        │
        │ TelemetryGateway（span/熔断）  Evaluator（L1/L2）             │
        │ 确认单（agent_runtime.db，CAS） 提醒调度器                    │
        └───────────────┬─────────────────────────────────────────────┘
                        │ registry.create_adapter_for(roster 条目)
                        ▼
        ┌──────────────────────────────┐   loopback HTTP + SSE
        │ DurableAdapter（Python）      │   token / 127.0.0.1 / 随机端口
        │ prepare_context / execute /   │◀────────────────────────────┐
        │ cancel                        │                             │
        └──────────────────────────────┘                             │
                        │                                            │
                        ▼                                            │
        ┌─────────────────────────────────────────────────────────────┐
        │ Pi Durable sidecar（Node 22，唯一拥有 data/durable/*.sqlite）│
        │ Harness + Registry                                          │
        │  ├─ 每个 Commission 一个 conversation（worker agent）        │
        │  ├─ Worker 工具：read_note / search_memory / submit_artifact │
        │  ├─ 审批 hook（beforeTool）+ memo                            │
        │  └─ task checkpoint / resume / abort 树                      │
        └───────────────┬─────────────────────────────────────────────┘
                        │ 工具回调（只读 + 幂等写入）
                        ▼
        ┌─────────────────────────────────────────────────────────────┐
        │ Python 业务接口（loopback /internal/durable/*）              │
        │ 记忆检索、便笺/事项读取、确认单、产物提交、span 上报          │
        └─────────────────────────────────────────────────────────────┘
```

### 4.1 接入点：新增 `adapter: durable`，而不是改 Dispatcher

这是本次建议里最关键的一步。`dispatcher.py` 只认花名册里的 `adapter:` 字符串，经注册表拿实例，驱动三个方法——**新增一种干活方式不需要改调度核心**，`test_agent_adapter_ext.py` 已经把这条钉成机器断言。

于是迁移单位是"某个 Worker 换执行内核"，不是"整个慢环换架构"：

```yaml
# config/agents_roster.yaml
agents:
  - id: scribe
    name: 笔录官
    adapter: durable          # ← 从 manual 换成 durable，其余不动
    strengths: [写作, 整理, 标题, 摘要, 便笺, 笔记, 文案, 改写, 润色]
    cost_tier: cheap
```

好处：灰度按 Agent 走、回退是改一行 YAML、协议零变更、既有测试全部继续有效。

### 4.2 进程与所有权

| 项 | 决策 |
|---|---|
| 启动 | `app.py` 的 `startup()` 里 `asyncio.create_subprocess_exec(node, ...)`，参数含 `--experimental-strip-types` |
| 单实例 | `proper-lockfile` 锁 `data/durable/`（`agent-runtime/package.json` 已预置该依赖） |
| 端口 | 绑定 `127.0.0.1:0`，启动后 stdout 首行回报实际端口（避免端口冲突） |
| 鉴权 | Python 生成一次性 token，经环境变量传入；只监听 loopback |
| 健康 | `GET /healthz` 就绪探针；5s 未就绪则 `durable` 适配器抛 `AdapterError` → 派单转 BLOCKED 并明示"执行内核不可用"，不静默改派、不假装执行 |
| 关闭 | 先 `POST /shutdown`（内部 `harness.close()`），10s 未退再 `taskkill /T /F`（复用 `terminate_process_group`） |
| 崩溃 | 监督重启，10 分钟内最多 3 次；超限则停用并转 BLOCKED（"执行内核不可用"），不假装在干活 |
| 存储 | `data/durable/agent.sqlite`，WAL + `synchronous=NORMAL`；**Python 绝不打开它**（一个 storage 同时只能一个进程） |
| 日志 | `data/durable-sidecar.{out,err}.log`，与现有 `data/*.log` 同目录 |
| 备份 | 扩展 `scripts/backup_local_databases.py`：除 `data/*.db` 外，对 `data/durable/*.sqlite` 做 `backup` + `integrity_check` |

### 4.3 内部 RPC（不是 PROTOCOL）

Python ↔ sidecar 的接口是**内部契约**，不进 `PROTOCOL.md`，但要像 `/internal/telemetry_proxy` 一样登记进 `check_protocol.py` 的 `INTERNAL_ROUTES`，防止路径漂移后静默 404。

| 方法 | 路径 | 语义 | 幂等键 |
|---|---|---|---|
| POST | `/v1/runs` | 建 conversation + 启动一次 Worker 执行，返回 `{task_id, conversation_id}` | `commission_id`（重复请求返回同一 run） |
| GET | `/v1/runs/{task_id}` | 查询 task 状态、outcome、usage | — |
| POST | `/v1/runs/{task_id}/cancel` | 取消（abort 树） | 幂等 |
| POST | `/v1/runs/{task_id}/answer` | BLOCKED 续答（作为 steer/follow-up 投递） | `commission_id:answer:{seq}` |
| GET | `/v1/events?task_id=&since=` | SSE 推送，事件类型严格等于适配器五种 | — |
| POST | `/v1/shutdown` | 优雅关闭 | — |

事件映射必须 1:1，`dispatcher` 才能零改动消费：

| sidecar 事件 | 适配器事件（协议 §22.2） | 来源 |
|---|---|---|
| `progress` | `progress{step_idx,text}` | 工具调用开始/结束、模型轮次 |
| `token` | `token{model,prompt_tokens,completion_tokens,ttft_ms,duration_ms,status_code}` | `pi.usage` 增量 + 流事件时间戳 |
| `artifact` | `artifact{artifact}` | `submit_artifact` 工具结果（唯一终止事件） |
| `log` | `log{text}` | sidecar 日志/工具输出 |
| `error` | `error{text}` | task 失败、超时、取消原因 |

外部 ID 约定：**`run_id` 与 `commission_id` 永远由 Python 生成并对外**，harness 的 `task_id/conversation_id` 只在内部映射表里。这样 `/api/assistant/run/{id}`、`POST /api/executive/commissions/{id}/submit`、§19 的形状全部不动。

### 4.4 Worker conversation 蓝图

每个 Commission 一个 conversation（不是每个 Worker 一个——同一 Worker 的两单必须能并行、互不污染）：

```typescript
const conv = await harness.createConversation({ ownership: { kind: "ownerless" } }, ctx);
await conv.configure({
  model: modelForTier(entry.cost_tier),          // cheap/medium/high → 具体模型
  tools: toolsFor(entry),                        // 只读默认；builder 才给 write/bash
  instructions: packet.instructions,             // 密件正文（含取景框）
  cwd: entry.cwd ?? process.cwd(),
}, ctx);
```

- **密件走 section/instructions，不走第一条 user message**：compaction 只摘要消息，不摘要 section；重启/续跑时密件仍然在。
- **已落地**：`agent-runtime/src/task.ts` 的 `ensureInstructions()` 把密件写进 `pi.agent.instructions`。任务内拿不到 `Conversation.configure`（`ConversationHandle` 只有 submit/abort/waitForIdle），所以走导出的 `configure(tx, conversationId, change)` 包在本任务的 commit 里，并先读一次、值相同就不写——崩溃重跑不会因此多提交一次。`test-model-run.ts` 把"密件不在 user 消息里、在 instructions 里"钉成了断言（改成挪回 user message 会红）。
- **密件渲染与 shim 对齐**：`briefingOf()` 只取 objective / deliverable / constraints 三个字段，和 `scripts/worker_shims/*.py` 的 `prompt_for` 同源。第一版只取了 objective，**把 deliverable 整个丢了**——durable 的 Worker 因此看不见密件里已经写明的职责行；这也是 A/B 早期不公平的来源。引擎自己的交付指令（"必须调用 submit_artifact"）单独一行放在 user message 里，与 shim 尾段同构；没有它模型有时只用文字回答，整单只能以"执行体没交差"失败。
- **记忆块走 section**：`section("memory", () => bridge.memory(commissionId))`，带超时与"没查成/没查到"的区分。
- **模型按 `cost_tier` 映射**：花名册已有三档，Pi Durable 支持 per-conversation model，这是顺手拿到的收益（琐事走廉价档）。
- **`maxTokens` 别继承快环的预算**：`models.ts` 原来硬编码 1200，推理型模型会把整个 completion 预算烧在 reasoning 上——`step-5-preview` 连续三次返回 `finish_reason: "length"`、正文空、无工具调用，于是整单以"执行体没交差"失败，而失败信息里 `said` 也是空的，看不出是预算问题。改成 8000（可用 `XIAOXI_MAX_TOKENS` 覆盖）后同一提示一次交差、实耗 1188 token。**不能读 `.env` 的 `LLM_MAX_TOKENS`**：那是快环短回复的 400，拿来更糟。
- **模型有时只用文字回答**：harness 没有强制工具调用（`ToolChoice` 只有 `auto | none`），`step-5-preview` 大约每五轮有一轮不调 `submit_artifact` 只答散文。`deliverWithModel` 因此在同一 conversation 里催一次（各自独立 `requestId`），`test-nudge.ts` 把这条路钉住了。
- **输出规格由工具 schema 钉死**：`submit_artifact` 的参数必须是 §19 的 `delivery` 形状，L1 初审不用猜字段。
- **终止语义**：`submit_artifact` 与 `block_and_ask` 都必须返回 `control: { terminate: true }`（该轮所有结果都请求终止时 run 结束），否则模型会再跑一轮，把"交差"变成"交差后继续闲聊"，既多花钱又可能改口。

### 4.5 工具面（最小集，先窄后宽）

| 工具 | 用途 | `replay` | 说明 |
|---|---|---|---|
| `read_note` | 读便笺指定版本 | `safe` | 纯读，绑定 `note_id + version` |
| `search_memory` | 检索长期记忆 | `safe` | 走 Python 记忆检索，返回带来源与置信档 |
| `read_thread` | 读共同事项 / 材料引用 | `safe` | 只读投影 |
| `report_progress` | 上报步骤 | `safe` | 事件型，不落业务状态 |
| `submit_artifact` | 交差（唯一正常终止） | **不 safe** | Python 侧按 `(commission_id, delivery hash)` 幂等 |
| `block_and_ask` | 请示（终止当前轮） | **不 safe** | Python 侧按问题文本去重 |
| `bash` / `write` | builder 专用 | **不 safe** | 仅 `cwd` 沙箱内；默认不装 |

不装的：任意网络访问、发邮件、桌面控制、跨 Agent 调用。与 `AGENT_RUNTIME.md` 的"不开放任意文件写入/网页搜索/Shell"保持一致。

### 4.6 `replay` 策略表（迁移前必须冻结）

原则只有一条：**只有纯读可以 `replay:"safe"`；一切有外部副作用或会推进状态机的，一律不 safe。**

| 情况 | 配置 | 重启后的行为 | 对账 |
|---|---|---|---|
| 读便笺/查记忆/读事项 | `safe` | 自动重跑 | 无需 |
| `report_progress` | `safe` | 重跑会重复一条进度 | Python 按 `(commission_id, step_idx)` 去重，重复项不进熔断签名 |
| `submit_artifact` | 不 safe | 给模型 `interrupted`，可重新提交 | Python 幂等，重复提交返回首次结果 |
| `block_and_ask` | 不 safe | 同上 | Python 按问题去重，重复则返回原 `blocked_question` |
| `bash` / `write` | 不 safe | 绝不重跑 | 产物不完整 → BLOCKED，人工核 |

注意：Pi Durable 的"不 safe"语义是**把中断告诉模型**，模型可能选择重试。对 `submit_artifact` 这是安全的（幂等）；对 `bash` 不安全——所以 `bash` 必须配合"产物未提交则不自动重试"的 prompt 约束与 Python 侧熔断。

### 4.7 审批流（确认单）

现状：写操作 → `waiting_confirmation` + `confirmation_id`（action hash、600s TTL、CAS 单次消费）→ 用户在界面点确认 → `resume` 执行。

迁移后：

```
模型调 mutation 工具
   └─ beforeTool hook（sidecar）
        ├─ 计算 action_hash（与 Python 同算法：sorted JSON / sha256）
        ├─ POST /internal/durable/approval  → Python 写 confirmation 行（pending）
        ├─ 阻塞等待（最长 590s，短于 TTL）
        ├─ 用户点确认 → Python CAS 消费 → 返回 approved/rejected
        ├─ api.memo("approval:"+taskId, {confirmation_id, approved})
        └─ approved ? 放行 : { block: "..." }
```

要点：

- **审批权威在 Python**：TTL、CAS、action hash 校验、审计全不动；harness 只负责"暂停与继续"。
- **memo 保证重放不重复询问**：崩溃恢复后 hook 先读 memo，有决定就不再问。
- **拒绝必须终止本轮**，不能让模型换个参数再试。工具返回 `control: { terminate: true }`（或 hook 返回 `block` 后由 `onYield` 收尾），Python 侧转 `cancelled` / `BLOCKED`。
- **崩溃时确认单作废**：sidecar 重启后 `reconcile()` 把所有 pending 确认标 `interrupted`，run 转 BLOCKED，提示"执行内核重启，请重新确认"——绝不自动批准。

### 4.8 观测映射

- `token` 事件：`pi.usage` 按 `provider/model` 累计，取增量；`ttft_ms` 用 pi-ai 流事件里第一个 `text_start`/`text_delta` 的时间差。**测不到就记 0**，按 §20"不得编造"。
- `step_idx`：用 conversation 的轮次/工具调用序号，保持单调。
- 鬼打墙：**留在 Python**。`telemetry.py` 只看 `progress` 文本的归一化签名，连续 3 次即熔断，`cancel()` 走 RPC。不要把语义判断搬进 sidecar。
- 初审用量：`audit.usage` 仍由 Python 从 span 表读；`budget_checked=false` 的语义不变（没 span 就是没查，不读成"查过了"）。

### 4.9 启动对账（必须有）

双进程最大的坑是"一边死了、另一边还停在 running"。启动时执行一次、之后每 60s 一次：

```
for c in commissions(status in {running, auditing, blocked}):
    t = durable.lookup(commission_id=c.id)
    if t is None and c.status == running:
        # 执行内核丢了这一单：按 §19 合法迁移到 blocked，写明原因
        ledger.transition(c.id, "blocked", blocked_question="执行内核重启，这一单需要重跑。要我再试一次吗？")
    elif t.terminal and c.status == running:
        # 任务已终止但台账没推进：读 outcome，走 submit/evaluator 或 blocked
        ...
    elif t.running and c.status in {blocked, done}:
        # 台账已终态但任务还活着：取消，避免幽灵执行
        durable.cancel(t.task_id)
```

铁律：**对账只走 §19 已有的合法迁移，不发明新边；永不自动把单据推进到 `done`。**

### 4.10 存储与保留

- `data/durable/agent.sqlite` 是 sidecar 独占；`backup_local_databases.py` 纳入。
- Pi Durable 不删历史 entry（README 未给出 conversation 级 GC），长期会涨。策略：按季度归档整库文件 + 只保留近 N 单的 conversation（新库冷启动，旧库只读留档）。在 Phase 1 就要把"归档开关"设计进去，别等盘满。
- `data/agent_runtime.db`（秘书循环）保持不动；不要试图把两者合并。

---

## 5. 分期实施

假设：1 名熟悉本仓库的工程师；Node 22.23.1 已在本机；不改协议；每个 Phase 可独立回退。

Phase 1 可以只做 §7.1 的每单 runner。§4.2 到 §4.10 的 sidecar 细节属于 B2b，等确实需要中途 steer 时再落地。

### Phase 0 — 可行性 spike（2–3 人日）

目标：把三个"可能一票否决"的技术点验掉；任一不过就停。

**① 依赖与官方示例。** `agent-runtime/` 里 `npm install`（`package.json` 已锁 `1.3.0`），提交 lockfile；用 `node --experimental-strip-types` 跑通官方示例 `24-child-tasks.ts`。

**② 自定义 OpenAI 兼容端点。** 项目用的是自建/代理端点（`LLM_BASE_URL`），pi-ai 需要手写 provider：

```typescript
import { createProvider, createModels, envApiKeyAuth } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

const provider = createProvider({
  id: "xiaoxi",
  baseUrl: process.env.LLM_BASE_URL,     // 与 server/config.py 的 llm_base_url 同源
  auth: { apiKey: envApiKeyAuth("Xiaoxi LLM", ["LLM_API_KEY"]) },
  models: [/* 手写 Model<'openai-completions'>，需 compat 覆盖非标字段 */],
  api: openAICompletionsApi(),
});
const models = createModels();
models.setProvider(provider);
```

验四件事：流式、中文、**tool calling**、usage。

**③ 崩溃恢复演示。** 跑一个 3 工具任务，`taskkill /F` sidecar，重启，记录哪些工具重跑、模型收到什么。

**Gate（任一不过就停，转 §7 B1）：**

- 端点支持 OpenAI 风格 function calling（不是所有代理都透传 `tools`）；
- 恢复语义符合 §4.6 的预期；
- 固定版本可复现安装（lockfile 提交）。

### Phase 1 — `durable` 适配器 + sidecar（8–10 人日）

| 交付 | 文件（新增为主） |
|---|---|
| 适配器实现 | `server/assistant/adapters/durable.py` + `@register_adapter("durable")` |
| sidecar 骨架 | `agent-runtime/src/main.ts`、`rpc.ts`、`events.ts`、`worker.ts` |
| 进程监督 | `server/assistant/durable_supervisor.py`（spawn/health/restart/shutdown） |
| 内部路由 | `server/app.py` 增 `/internal/durable/*`（确认/记忆/产物/span 回调） |
| 配置 | `config/agents_roster.yaml` 增 `durable:` 块；一个 Agent 切 `adapter: durable` |
| 协议门禁 | `scripts/check_protocol.py` 的 `INTERNAL_ROUTES` 增 `/internal/durable/` |
| 测试 | `scripts/test_durable_adapter.py`（假 sidecar）、`agent-runtime/test/*.test.ts` |

**Shadow 模式**：sidecar 跑完整执行，但 `submit_artifact` 只落日志、不提交初审；Python 路径照常跑。对比两份事件流的步数、token、耗时、产物质量。

**验收**：`test_executive_v3` / `test_agent_adapter_ext` / `test_agent_loop` / `check_protocol` / `test_core` 全绿；杀掉 sidecar 后 Commission 不卡 `running`；重复 `POST /v1/runs` 不产生第二单。

**回退**：把 `adapter: durable` 改回 `manual` / `cli_subproc`，一行。

### Phase 2 — Worker 全量 + 审批 + 观测（8–12 人日）

- [x] `cost_tier → model` 映射生效（`XIAOXI_MODEL_CHEAP/MEDIUM/HIGH`，按 conversation 选模型；`test-tier-model.ts`）
- [x] 审批全链路：hook 拦下 → 待批准清单 → 老板答复后 `/v1/approvals` 按单放行 → 不跨单泄漏（`test-approval-chain.ts`）。此前链子断在最后一环——被拦的工具在 sidecar 存活期内一直拦着。
  - **2026-10-18 修正**：链子在测试里是通的，在生产里从没生效过。`XIAOXI_APPROVAL_TOOLS` 只有测试设过，sidecar 启动时一个工具都没登记，hook 因此从不拦任何调用。现在花名册 `durable.approval_tools` 经 `DurableSupervisor(extra_env=...)` 随进程注入；留空 = 不拦。写操作类工具（`write_file` / `run_worker`）建议列上。
- [x] 成果可核：`write_file` 沙箱内写文件 + `submit_artifact` 的 ref 规范化与验真（`test-write-file.ts` / `test-handin.ts`）
- [x] 遥测回填（`emitUsage` 读 `pi.usage`）、鬼打墙熔断走 RPC 取消
  - **2026-10-18 补接管侧**：`cli_subproc` 会把密件里的 `telemetry.proxy_url` 注成 `AQI_TELEMETRY_PROXY`，durable 接管执行体时只从 sidecar 进程环境读这个变量，而监督器从不设它——被接管执行体的模型调用因此一条 span 都不落。现在 `RunMeta.telemetry_proxy` 从密件带进 sidecar，`shimEnv()` 优先用它。实测 DSH 一单 `calls=3`、`prompt_tokens=23678`。
- [x] 启动对账 + 60s 巡检（`patrol_seconds`，`test_durable_supervisor.py`）
  - **2026-10-18 修正**：这一条此前是虚的。巡检只探进程健康，账实核对根本不存在——`recover_interrupted()` 把 running 一律转 BLOCKED，sidecar 里已经跑完的单成果被扔掉。现已补：`GET /v1/lookup`（按 `commission_id@attempt` 查单，不建单）+ `ExecutiveService.recover_interrupted(durable_lookup=...)` 只采纳内核已终态 `completed` 且带 summary 的成果，其余一律照旧转 BLOCKED。铁律不变：对账永不替内核把没跑完的单判成功，也永不自动 `done`。回归在 `scripts/test_durable_adapter.py`。
  - 对账只覆盖"宿主重启、内核活着"这一种死法。内核自己死了，适配器连不上，那一单如实转 BLOCKED 请示；答一句后靠 attempt 递增真的重新拉起执行体（见下面"接管与重做"）。
- [x] 归档/保留策略（`data/durable/*.sqlite` 纳入 `backup_local_databases.py`）
- [x] 配置接线：`config/agents_roster.yaml` 新增 `durable:` 段（默认 `enabled: false`）
- [ ] **三个 Worker 全量切换**：见下方 A/B 小节末尾的说明。花名册现有四个 `cli_subproc`，切过去是能力回退；当前 `durable.enabled: false`，`switch_agents: [scribe]` 只是"启用后先切文档类"的预备配置，不是已切换。

**验收（A/B）**：同一批 10 个真实委托，同一模型，`cli_subproc` vs `durable`，按 L1 初审通过率、token、端到端耗时、人工盲评比较；`durable` 不得在通过率上显著落后。混沌测试见 §6。

**2026-10-18 A/B（`scripts/probe_durable_ab.py --repeat 10`，两侧同为 `claude-opus-5-5`，同一份密件、同一提示词）**：

| | L1 通过（按配置） | 排除预算闸 | 均时 | 均 token |
|---|---|---|---|---|
| `durable` | **10/10** | 10/10 | 34.3s | 5,193 |
| `cli_subproc` | 2/10 | **10/10** | 64.3s | 80,723 |

**通过率上 `durable` 不落后，Phase 2 的验收线过了。** 但有两件事必须一起读，否则这个 10/10 会被读错：

1. **基线的 8/10 失败全是预算闸，不是能力**：当时 `max_prompt_tokens: 80000` 低于真机 Claude 跑一轮的真实上下文（约 10 万），而 `telemetry.commission_usage` 把一单三次重试的 prompt_tokens **求和**（24–63 万）。**2026-10-20 已收口**：`max_prompt_tokens_per_call=120000` 只做单次质量闸，`max_total_prompt_tokens=400000` 只做成本告警；`test_budget_scope.py` 钉住"重试不翻倍打回"。
2. **`durable` 早期是 7/10，差的 3 单全是"产不出可验证的成果"**：它的工具集原本只有 `submit_artifact` / `block_and_ask`，没有写文件的能力，模型却会编出 `kind: "file"` 的路径（`work/title-3vers/titles.md`、`/tmp/three_titles_workdir/titles.md`、`workspace/titles/three-titles.md`），被项目自己的 `no_unwired_dry_run` 抓住；剩下 7 单能过是因为模型改报 `kind: "note"`，而那条路径**根本不验存在性**——等于"过了但没有可核成果"。

补上 `write_file`（沙箱内写文件，见 §4.5）与 `submit_artifact` 的 ref 规范化之后才是 10/10：
- `write_file` 只认 `RunMeta.workspace` 一个目录，`../`、工作目录外的绝对路径、同名前缀的兄弟目录全部拒绝；基目录不存在时明说"这不是路径问题"，不让模型改路径重试。
- `submit_artifact` 交差前把 file 型 ref 规范化并验真：模型把路径转义两遍产生的 `D:\\project\\...` 会被还原，没写过的文件直接拒收（不记一张指向空气的卡）。

**仍未做**：三个 Worker 全量切换。花名册里现在有**四个** `cli_subproc`（claude / workbuddy / dsh / codex，比本文档写"三个"时多了一个），它们的真机 CLI 自带 Read/Bash/Grep，而 durable 的工具面窄得多。切过去是**能力回退**；当前 `durable.enabled: false`，`switch_agents: [scribe]` 只是启用后先切文档类的预备配置。等确认过那些 Worker 不需要真机工具再谈扩大。

token 差 15 倍（5,193 vs 80,723）不能直接当收益：`durable` 干的活也严格更少（不跑校验脚本、不读仓库），而这个差主要来自两侧的工具面不同，不是引擎效率。

### 能力层收口（2026-10-20）

这一轮把 durable 从“能跑单”补到“像一个可用的后台 Agent”，范围仍是 Worker
执行层，不碰快环与秘书循环：

- **Skills**：`agent-runtime/src/skills.ts` 按 Agent Skills 布局扫描
  `XIAOXI_SKILLS_DIR`（默认 `config/skills`）。system prompt 只放 name/description，
  正文由 `read_skill` 按需读取；支持技能目录内的文本参考文件，路径逃逸会拒绝，
  `disable-model-invocation: true` 不进入模型列表。没有指向 `.agents/skills`：
  那是开发工具的技能，会漏进生产 Worker。
- **MCP**：`agent-runtime/src/mcp.ts` 用 `@earendil-works/pi-mcp@1.3.0` 连接
  `config/mcp.json` 的 stdio/HTTP servers，把 tools 包成 durable 原生工具
  `mcp__<server>__<tool>`。只读注解工具才 `replay:"safe"`；有副作用的工具自动
  登记进审批链。MCP server 只拿到最小环境变量，不继承 sidecar 的 LLM key。
  `run_worker`（按模型给的命令行拉起本地执行体）默认也列入审批。
- **压缩**：`models.ts` 不再把 `contextWindow` 写死为 32768；可用
  `XIAOXI_CONTEXT_WINDOW` / `XIAOXI_MAX_TOKENS` 配置，并由
  `compactionSettings()` 推导 reserve/keep/background。此前的 Pi 默认值
  （reserve 16384、keep 20000）放在 32k 窗口下会出现“切点比阻塞阈值还靠后”的
  矛盾组合，现在启动时断言 `contextWindow - reserve > keep`，不满足直接报错。
- **文件回读**：新增 `read_file`（工作目录内、`replay:"safe"`）。此前 durable
  Worker 只能 `write_file`、不能回读，长任务无法检查自己写出的成果；现在
  `write_file` / `read_file` 共用同一套 `insideSandbox` 边界。
- **密件上下文**：`task.ts` 的 `briefingOf()` 现在把 `memory_block`、`rubrics`、
  `acceptance`、`feedback` 一起写进 `instructions`。此前 durable 模型路径只拿到
  objective/deliverable/constraints，记忆和验收规则都在 Python 密件里空转。
- **接线修正**：`server/app.py` 现在把 `approval_tools`、`switch_agents`、
  `workspace_root`、`endpoint_provider` 真正传给监督器/适配器；此前
  `approval_tools` 从未注入，`endpoint_provider` 未传导致硬杀重启后仍连旧端口，
  `workspace_root` 也没有随 `bind_durable` 下发。`XIAOXI_DURABLE_ENABLED=1`
  点亮，`XIAOXI_DURABLE_SWITCH_AGENTS=scribe` 或 YAML `switch_agents` 决定切换；
  YAML 默认关闭，避免测试反复拉 Node。
- **存储**：`backup_local_databases.py` 已包含 `data/durable/*.sqlite`；新增
  `scripts/archive_durable.py` 做在线冷归档（不停止 sidecar，不动活库），
  只有显式 `--prune-days N` 才删除旧档。自动轮转仍未做。
- **回归**：`test-compaction.ts`（8）、`test-skills.ts`（11）、
  `test-read-file.ts`（5）、`test-mcp.ts`（10）、`test_durable_app_wiring.py`（7），
  以及原有 supervisor/adapter/takeover/revive/approval 全绿。

仍未做：三个（现在是四个）真机 CLI Worker 全量切换——它们自带 Read/Bash/Grep，
切过去是能力回退；代码类继续 `cli_subproc`，文档/整理类先切 `scribe`。真实 MCP
server 的端到端联调也还没做，目前只有 fake client 的单测。

### C1 只读参谋（2026-10-20）

编排者路线的第一段：先让 durable Agent **看得见**记忆、便笺、事项和 Worker
花名册，但不给它任何写权限。

- **宿主接口**：`server/assistant/durable_api.py` 提供
  `/internal/durable/memory/search`、`/notes/search`、`/notes/read`、
  `/threads/read`、`/workers`、`/workers/status`。全部只读；`server/app.py`
  在 sidecar 启动时生成一次性 `XIAOXI_HOST_TOKEN`，经进程环境注入，不落盘。
  未启用 durable 时 token 为空，路由返回 503，不会意外暴露记忆库。
- **sidecar 工具**：`search_memory` / `search_notes` / `read_note` /
  `read_thread` / `list_workers` / `worker_status` / `list_files`。前六个走
  宿主接口，`list_files` 只读本单沙箱；全部 `replay:"safe"`，不进审批。
- **记忆语义**：返回 `stated` / `inferred` / `uncertain` 三档，带
  `requires_confirmation`；空检索显式返回“没查到不等于没发生”，记忆关闭
  返回 `disabled`，检索异常返回 `unavailable`，都不编造。
- **边界**：`workers` 只返回 id/name/adapter/summary/strengths/weaknesses/
  cost_tier/max_concurrent/in_flight/available，不返回 `env`/`executable`/
  token/endpoint；`available` 只是并发容量，不是健康检查。
- **回归**：`test_durable_read_api.py`（14）、`test-host-tools.ts`（10）、
  `test-list-files.ts`（7），并纳入 `check_protocol.py` 的
  `INTERNAL_ROUTES`。

仍未做：C3 的 `accept_delivery`/`reject_delivery`。C1 明确不写台账、不派单、不验收。

### C2 编排者（2026-10-20）

C2 把“理解并派发任务、跟踪、请示”接到 durable，但写权限仍只在 Python：

- **Profile 隔离**：`runtime.ts` 拆成 `Tasks` / `ReadOnly` / `Worker` /
  `Orchestrator` 四个扩展。Worker 默认只选前三个（+Skills/MCP），没有
  `dispatch_task`；Orchestrator 移除 Worker 与 MCP，只保留只读工具、
  Skills 和 C2 工具。Worker 不能派单，编排者不能写文件或跑本地执行体。
- **Lease**：`durable_leases.py` 由宿主按会话签发，模型从 RunMeta 拿不到
  额度字段。首期 `dry_run` 默认、1 并发、3 单/小时、`cost_tier<=medium`、
  TTL 30 分钟、只允许 `scribe`；high 档与 shell Worker 不进入 C2 派单池。
  模型伪造 lease/额度不会生效。
- **派单**：`dispatch_task` -> `/internal/durable/dispatch` ->
  `ExecutiveService.open/dispatch/start`，随后后台 `executive.drive()`；
  工具立即返回 `commission_id`。幂等键由 `(taskId, callId)` 生成，重复调用
  返回同一 Commission；不会直接写 `commissions.db`。
- **跟踪**：`task_status` / `task_events` 只读台账，返回状态、blocked 问题、
  `blocked_question_id`、L1/L2 findings 和交付摘要；只能查当前 lease 派出的单。
- **请示**：`answer_blocked` 只答 clarification。带确认/审批语义的问题直接
  403；它调用 `dispatcher.answer`，**不调用** `_grant_durable_approvals`。
  确认单仍走 action hash + CAS，模型无权代答。同一 `(commission_id,
  blocked_question_id, answer_hash)` 幂等。
- **审计**：dispatch / answer / orchestrator start 都写 telemetry span；
  不新增浏览器 WS 事件。`check_protocol.py` 已登记所有 `/internal/durable/*`。
- **入口**：`POST /internal/durable/orchestrator`（host token）用当前 user
  intent 签发 lease，再向 sidecar 创建 orchestrator profile 的 run。没有
  user intent 不发 lease；记忆/笔记内容不能单独触发派单。
- **回归**：`test_durable_orchestrator_api.py`（22）、
  `test-orchestrator-tools.ts`（10）、`test-profiles.ts`（12）、
  `test-orchestrator-run.ts`（8）；覆盖重复 dispatch、lease 过期/并发/小时
  额度、Worker 越权与自派、确认单误答、dry-run/confirm、任务跟踪与审计
  span、重启后 lease 失效。

### C2.1 硬化（2026-10-20）

C2 第一版的三个安全返工点已经收口：

- **confirm 是真确认**：`ConfirmationStore` 用一次性 `confirmation_id` 绑定
  lease/session/agent/title/brief/thread/intent 的 action hash。`/dispatch`
  在 confirm 模式首次只返回 `confirmation_id`；宿主通过
  `/internal/durable/confirm` 批准/拒绝（不是模型工具），之后 `/dispatch`
  必须带同一个确认号且 title/brief 不变，消费一次即失效。换标题/换 brief
  会得到 `confirmation_action_changed`，不能拿确认号派另一件活。
  `dispatch_task` 工具现在会自己等待宿主确认（最长 120s），批准后用同一
  confirmation_id 自动重试；真实模型不需要理解重试协议。
- **确认单不再靠正则**：`block_and_ask` 增加 `kind: "clarification" |
  "approval"`，错误事件带 `blocked_kind` 穿过 durable SSE / 适配器 / 台账；
  `answer_blocked` 只认 `blocked_kind=clarification`，`approval` 直接 403，
  空 kind 的旧单也拒绝（不猜文本）。`_grant_durable_approvals` 仍完全不在
  C2 路径上。该接口还要求 `lease.mode=auto`，且同一单最多答 3 次澄清；
  超过返回 `clarification_limit`，防止真实模型自问自答循环。
- **无 Worker 不再记 done**：`_fail_dispatch` 对 `drafting` 先转 `briefing`
  再 `blocked`，带请示与 `blocked_kind=clarification`，与 `delegate` 一致；
  回归直接断言台账状态，不只断言 HTTP 409。
- **重启孤儿**：新增“创建 Commission 后宿主崩溃 → 新进程
  `recover_interrupted`”回归，孤儿单转 blocked 并留下请示。

回归更新：`test_durable_orchestrator_api.py`（36）、
`test-orchestrator-tools.ts`（15，含 confirm 自动重试）、`test-profiles.ts`（16）、
`test-orchestrator-run.ts`（8）。

C2 的 `confirm` 需要宿主/老板侧调用 `/internal/durable/confirm`；模型只能拿到
确认号，不能批准。

### C3 验收三层（2026-10-20）

C3 没有把 `accept_delivery` 交给模型，而是拆成 review / propose / commit：

- **review_delivery（只读）**：只对 `ready_to_report` 的单生效，返回
  L1/L2 findings、rubric 命中、artifact checks、推荐结论、blockers 和
  `evidence_hash`。不改变台账。`can_accept` 会拒绝复验失败（例如初审后文件
  被删除）的交付；`artifacts_verified` 只有所有 artifact 都重验且存在才为
  true，note/相对路径等未重验项明确标 false。
- **propose_accept / propose_reject（只读）**：返回 `proposal_id`、建议与
  证据，不改变台账。L1/L2 有 blocker 时 accept 提案直接 409；reject 提案
  可带 `suggested_feedback` 供宿主参考，但模型不写规则。
- **宿主 commit**：`/internal/durable/authorize` 由老板/宿主一次性授权；
  `/internal/durable/accept` 调 `ExecutiveService.accept`，只走
  `ready_to_report -> done`；`/internal/durable/reject` 调
  `ExecutiveService.reject`，保留 `dispatcher.reject` 的判例学习。两个
  commit 都需要 `proposal_id + authorization_id`，幂等、记审计 span。
- **权限**：模型工具面只有 review/propose，没有 accept/reject commit 工具；
  proposal 的 `evidence_hash` 绑定当前 audit findings / blockers /
  artifact_checks / delivery，台账或 artifact 变化后 commit 会被拒绝。
  `source=policy` 的自动授权目前 403，等独立策略引擎（allowlist + L1 + 无
  L2 blocker + lease + kill switch）再开。
- **reject 后续跑**：`/reject` 仍只走 `ExecutiveService.reject` 到
  `running`，不自动 drive；新增回归用宿主 `/run` 路径（`run_commission`）
  证明 running 单真的会重新执行到 `ready_to_report`。
- **状态机**：没有新增边；accept 只从 ready_to_report 到 done，reject 走
  ready_to_report -> running，仍然只由 Dispatcher 迁移。

回归：`test_durable_c3_api.py`（30），覆盖 review/propose 只读、L1 不可
accept、宿主授权 commit、accept/reject 幂等、reject 批注与动作不匹配、
文件删除后 accept 409、findings 变化使旧提案失效、note artifact 不标
verified、reject 后宿主重新执行，以及工作台 `/review` + `/accept` +
`/reject` 入口（验收按钮现在也过 C3 review）。另有
`test_durable_clarification_cap.py`（3）用脚本化 BlockingAdapter 证明
adapter error → blocked_kind → 第 4 次澄清 429，不依赖真实模型。
`test-profiles.ts`（16）与 `test-orchestrator-tools.ts`（14）钉住模型没有
commit 权。

**产品入口（C4 补齐）**：内部 `/internal/durable/authorize` 仍只对宿主
（bearer token）开放；老板现在能在督办抽屉里完成同一条授权链：

- `/api/executive/durable/*` 与 `/internal/durable/*` 在 `app.py` 里共用同一个
  `DurableState`（lease / 确认单 / 提议 / 幂等表）。测试单独建 router 时仍可只
  传一个 `LeaseStore`，向后兼容。
- 抽屉新增“等你拍板”：待批派单（模型在 `confirm` 模式下等待，老板点击后
  `dispatch_task` 自己重试，不需要再发一句话）、待放行工具（展示 tool +
  action_hash 前缀，只放行这一次调用，然后宿主 `answer + resume` 恢复执行）、
  模型验收建议（授权验收/授权打回；打回理由由老板填写，不把模型建议直接
  变成判例）。
- 交付卡新增只读“验收详情”：按 C3 `build_review` 重跑成果复验，展示文件是否
  还在、L1/L2 findings、blockers；`收工` 仍走同一个 review 门禁，409 时把原因
  摊开，而不是静默失败。
- 所有写路由都做同源校验；没有 durable 状态时授权接口 503，前端降级为空，
  不影响旧台账。

### Live probe（2026-10-20，隔离临时库）

`scripts/probe_c3_live.py` 用临时 assistant/durable/workspace 目录起本地宿主
和 sidecar，不碰真实 `data/`：

- **5/5 happy path**：真实 `step-3.7-flash` 按契约 dispatch → scribe 写真实
  文件 → ready_to_report → review → propose → authorize → accept，全部 done。
- **1 次缺素材场景失败**：真实 orchestrator 连续回答 clarification 11 次，
  commission 在 blocked/running 间循环，orchestrator task 240s 未收口。已加
  每单最多 3 次 clarification 的宿主上限，并有单测覆盖第 4 次返回 429。
  加上限后复测同一场景，模型这次直接交了一张“无法取得素材”的说明并被验收，
  没有再次进入 blocked 循环；因此上限本身仍只由单测证明。
- **1 次真实 reject**：宿主授权 reject 成功，`dispatcher.reject` 学到判例
  `boss_8d704292`；随后 `/run` 确实重新执行，但重做结果仍是 blocked（被新
  判例/素材条件挡住），不是 ready_to_report。打回重做链路存在，但“重做后
  一定可验收”未证明。
- **DSH 新工作区 3/3 成功**：`scripts/probe_dsh_workspace.py --tasks 3 --keep`
  在 `tmp/dsh-workspace-*` 下跑 hello.txt / titles.md / notes.md，3 单全部
  ready_to_report，真实文件存在、L1 通过。没有写 `data/commissions`。
- **manager→DSH 2/2 成功**：`probe_c3_live.py --worker dsh --runs 2` 用
  confirm lease（宿主自动批准待确认单），真实 orchestrator 派给 dsh，2 单都
  ready_to_report 并被接受。此前同一路径有 2 次模型拿到 confirmation_id 后只
  转述给老板、不重试；把等待/重试收进 `dispatch_task` 工具后消失。
- **工作台入口**：`/api/executive/commissions/{id}/review` 只读；原有
  accept/reject 按钮现在必须先过 `build_review`，L1/blocker/文件复验失败
  不能从 UI 绕过 C3。

仍未完成：真实模型下的 blocked 上限自然复测（现在由脚本化 Worker 覆盖）、
5–10 单全部路径成功样本、coding Worker 外派的高档/确认策略、C4。

### C4 起步（2026-10-20）

- **真实 MCP**：新增 `agent-runtime/fixtures/mcp-echo-server.mjs` 和
  `test-mcp-live.ts`。不是 fake client：真的 spawn stdio server、initialize、
  tools/list、tools/call、close；9/9 通过。只读工具 replay safe，写工具
  unsafe 且自动进审批。
- **接入引导**：新增 `docs/AGENT_INTEGRATION_GUIDE.md`，覆盖“CLI / durable
  接管 / HTTP / MCP Worker / MCP 工具 / manual”选择、工作区派生、roster/MCP
  配置、审批与验证命令。
- **接入体检**：新增 `scripts/doctor_integration.py`，不联网检查 adapter
  登记、executable/args、workspace、MCP 配置和 skills；当前花名册 6 个
  Worker 全部 OK（codex 仅 enabled=false 警告）。
- **DSH 外派**：`probe_c3_live.py --worker dsh` 在 confirm lease 下 2/2
  成功；`dispatch_task` 现在自己等待宿主确认（120s）并自动重试。

- **长任务 compaction**：`test-compaction-live.ts` 用假端点同时服务正常对话
  和摘要请求；低窗口下第 2 轮自动产生 `pi.compaction`，摘要请求真的发出，
  压缩后对话继续。6/6 通过。
- **周期账实对账**：`ExecutiveService.reconcile_durable()` + app 的
  `_reconcile_loop`（60s，线程内执行）。内核已完成 → 走 submit 采纳；内核
  丢失/失败 → BLOCKED 带请示；仍在跑或查询不可用 → 不动；永不自动 `done`。
  app 启动收口现在也真正带 `durable_lookup`。`test_durable_reconcile.py`
  7/7。

- **混沌/soak**：`test_durable_soak.py` 创建 8 单后硬杀 sidecar，监督器换
  端口重起，8/8 完成；每单 3 条进度不重不漏、artifact 恰好 1 条。27 项断言
  通过。这是短时 chaos，不是 24h soak。
- **审批按调用绑定**：审批从“按工具放行”改成“工具 + 参数哈希一次消费”。
  `/v1/approvals` 返回 `{tool, action_hash}`，Python 按哈希放行；同一工具换
  参数或被消费后重试仍需重新批准。`test-approval-per-call.ts` 5/5，
  `test-approval-chain.ts` 9/9（改成 grant 后 attempt+1 重跑验证真的执行）。

- **存储增长/冷却归档**：`storage_doctor.py` 只读报告活库/归档/备份体积；
  `archive_durable.py --rotate --yes` 在拿到与 sidecar 相同的 proper-lockfile
  目录锁后，先做 integrity-check 快照，再删除活 SQLite/WAL/SHM，下一次
  sidecar 从空库启动。锁被 sidecar 持有时拒绝轮转；不加 `--yes` 只打印计划。
  `test_storage_archive.py` 9/9。轮转会放弃旧库里可续跑的 run，Python 台账
  下次对账会转 BLOCKED 请示，不伪造结果。

- **24h soak harness**：新增 `scripts/soak_durable.py`，可配置时长/并发/提交
  间隔/周期硬杀；采样 `/v1/stats` 的 RSS/heap/handles 与 SQLite/WAL 体积，
  输出 JSON 报告；每单检查 3 条唯一进度 + 1 条 artifact。15 秒 smoke
  （3 并发、3 次硬杀）5/5 完成、0 restart failure。正式 24h 需单独挂机跑。
- **工作台授权面**：`DurableState` 共享 + `/api/executive/durable/*` 产品路由 +
  抽屉“等你拍板”/“验收详情”。`test_durable_authorization_ui.py` 24 项断言，
  含同源拒绝、确认号真正落地、授权后 C3 commit、错哈希不放行、按调用放行后
  恢复执行、驳回只 revoke、未启用 durable 时 503。浏览器侧用临时脚本验证
  3 张卡渲染、确认 POST 与验收详情展开，无 page error。

- **真实模型 compaction 样本**：`agent-runtime/probe-compaction-real.ts` 用
  真实 `step-3.7-flash` 跑了 6 次长会话探针；5 次成功生成 `pi.compaction`
  （另 1 次把输出上限压到 512，摘要自身被截断，说明“压缩输出预算”不能低于
  真实摘要需要）。生产档（32k window / 8k answer / reserve 9024 /
  keepRecent 11872）的结果：
  - 压缩后继续对话：5/5 状态完成；其中 1 次单条消息约 10k tokens，压缩无法
    在消息内部切，下一轮 `stopReason=length`、只输出 1 token，答案不可用。
  - 早期“暗号”标识符：4/5 被摘要保留；1 次在对话被工具失败噪声占满时丢失。
    结论：关键任务事实不能只放在早期用户消息里；密件/约束走系统段，每轮重绘，
    不依赖摘要——与“检索为空必须显式负向约束”是同一条纪律。
  - 近期硬约束复述：有正常输出预算的样本全部正确；模型在裸探针（未配宿主）
    下有时先尝试 search_memory/list_files，失败后回答“无法获取”，即使摘要
    里有答案。产品路径应把“对话内事实先看摘要，不要拿记忆检索代替”写进
    Worker/编排者指令。
  - 边界处置：`read_file` 与 MCP 文本结果都截到 12000 字符（低于
    keepRecentTokens），`test-read-file.ts` 6/6、`test-mcp.ts` 11/11。
    直接粘贴超大文本仍可能触发该边界；产品路径应走文件/MCP 并受同一上限约束。

- **2h 混合负载 soak（2026-10-20 完成）**：`soak_durable.py` 跑满 7200.8s，
  3361 提交 / 3227 完成 / 134 取消 / **0 失败 / 0 检查失败**；7 次硬杀、
  **0 restart failure**；67 次重复派单校验 0 失败。RSS 93.7→91.8 MiB
  （min 80.2、max 118.1，峰值出现在重启窗口），heap 23.9→21.2 MiB，
  handles 恒为 4；SQLite 0.3→19.9 MiB（≈6.1 KB/单；按同一负载外推 24h
  约 240 MiB，真实使用远低于 soak 的 0.47 单/秒）。报告
  `tmp/soak-mixed-2h.json`。

C4 仍未做：24h 连续 soak（需单独挂机；2h 只证明短时稳定与重启正确）；
存储轮转仍是“有锁保护 + 显式确认”的离线操作，不是无人值守自动换库；
自动调度需要额外的停机窗口编排。

### 接管与重做（2026-10-18 新增，实测通过）

durable 不只是"自己的模型"，它还能**接管本机执行体**：sidecar 用 `env.exec` 拉起花名册里配的 `executable/args`，解析它 stdout 上的五行事件协议。这条桥让真机 CLI 的 Read/Bash/Grep 与 durable 的崩溃续跑同时拿到，不必二选一。

实测（`scripts/probe_takeover_production.py`，DSH + step-3.7-flash，走 `ExecutiveService.delegate` 生产链路）：

- `status=ready_to_report`，初审 0 findings，交付 `out/titles.md`
- 遥测 `calls=3`、`prompt_tokens=23678`：DSH 自己的模型调用被记进 span（`AQI_TELEMETRY_PROXY` 由密件带进 sidecar，此前只读 sidecar 进程环境，等于没有）
- 台账事件 13 条：逐步收的，不是跑完才知道发生了什么

三个把接管咬住的缺陷，都已修并落测试：

1. **相对路径**：花名册的 `args` 是相对仓库根写的，而 sidecar 子进程的 cwd 是本单沙箱。`python scripts/worker_shims/dsh_worker.py` 打不开脚本、退出码 0、stderr 一句都看不到，整单只剩"本地执行体没有交差"。现在适配器把 args 解析成绝对路径，把 `python` 解析到 `.venv/Scripts/python.exe`（Windows 上裸 `python` 会落在 Anaconda，numpy 2.x 直接 ImportError），把 `node` 解析到 PATH 上的那个。
2. **stderr 丢失**：`runLocalAgent` 只读 stdout。现在分开收集 stderr 尾部，执行体没交差时把它带进失败信息。
3. **幂等键太粗**：`RunIndex` 只用 `commission_id` 且永不过期，初审打回后的重做拿回上一版已终态的任务，执行体根本不会被再拉起，旧成果被原样再交一次（实测两个 create 同一 taskId、子进程只跑一次）。现在键是 `commission_id@attempt`，attempt 取台账 `retry_count`；重投同一次仍幂等，重做拿新单并真的再跑。

`GET /v1/lookup` 是重启对账的配套：按 `commission_id@attempt` 查单，不建单。

**仍未做**：三个 Worker 全量切换（见上文）。接管之后"要不要切"的答案变了——切过去不再必然等于能力回退，因为真机 CLI 的工具还在。但仍需逐个确认那四个 Worker 的 shim 是否接受 `AQI_WORKDIR` 注入（DSH 接受，`resolve_workdir()` 优先读它）。

### 中断恢复的两个断点（2026-10-18 实测并修复）

`probe_takeover_production.py` 的 ③ 段把 sidecar 硬杀（`taskkill /F /T`），暴露出两处恢复断裂。都不是"运气不好"，都会 100% 复现：

1. **锁的重试预算短于过期窗口。** `proper-lockfile` 只在 mtime 超过 `stale`
   （10s）之后才认锁过期，而重试预算是 `10 × 500ms = 5s`。硬杀留下锁，重试先
   耗尽，`_spawn` 抛 `ELOCKED`，监督器记 `restart_failed` 就放弃了——内核再也
   没被拉回来。改成 `40 × 500ms = 20s > 10s`，并加 `test-revive.ts`（真硬杀 +
   重开）。这条有回归：把 `RETRIES` 调回 10，测试立刻失败。
2. **写死的端点让重试连到死端口。** 内核在新端口重起，花名册里那份 endpoint
   还是绑定时那份，于是"内核拉回来了、重试却报 All connection attempts
   failed"。现在 `DurableSupervisor.current_endpoint()` 作为 `endpoint_provider`
   注入，适配器每次派单前重新取地址；内核不可用时返回 None，适配器照旧抛错。

顺带修掉 `dispatcher.answer()` 不递增 `retry_count`：durable 按 attempt 幂等，
answer 不动计数就会拿回内核里那个已 failed 的任务，执行体一次都不跑。请示获答
是一次新开工，计数必须动（`test_answer_starts_a_new_attempt` 钉住）。

**探针现状**：①②③④ 四段全通过（一次完整运行 4/4）。① 偶发失败，根因是
step-3.7-flash 上游偶发返回空 content（`EMPTY_RESPONSE`），与接管链路无关。

### Phase 3 — 后台长任务（可选，5 人日）

把"读一段时间书再产出"、"自进化 cycle"这类**不要求准点、但可能跑很久**的任务迁进 harness，用 background task + durable timer。提醒/日程**不迁**（见 §3 #9）。

### Phase 4 — 秘书循环（可选，不建议现在，10–15 人日）

只有在 Phase 1–3 稳定运行 4 周、且明确需要"秘书也能被 steer / fork"时才做。改动面：`_decide` 的 JSON 契约 → 原生工具、`waiting_input` → 队列/steer、确认 → hook、`evals/` 基线重跑、`test_assistant_*` 重写。

### 如果坚持"整体替换"

可行，但顺序要反过来且代价明确：

1. 先 Phase 4（秘书循环）——因为它依赖确认/澄清，最难，且会重写评测基线；
2. 再 Phase 2（Worker）；
3. 最后把 Commission 台账搬进 documents（协议 §19 需 Role 0 修订、`test_executive_v3` 重写、抽屉接口改投影）；
4. 总量 10–14 周，且期间不建议并行新功能。

我的建议是不走这条：收益（多端/多人/fork）当前用不上，风险（协议/评测/双真相）全部落在最贵的语义层。

---

## 6. 验证与验收

### 6.1 黄金命令（沿用 `docs/TESTING.md`）

```powershell
.\.venv\Scripts\python.exe scripts\check_protocol.py
.\.venv\Scripts\python.exe scripts\test_core.py
.\.venv\Scripts\python.exe scripts\test_executive_v3.py
.\.venv\Scripts\python.exe scripts\test_agent_adapter_ext.py
.\.venv\Scripts\python.exe scripts\test_agent_loop.py
.\.venv\Scripts\python.exe scripts\run_evals.py
npm test
```

新增门禁：

```powershell
.\.venv\Scripts\python.exe scripts\test_durable_adapter.py   # 假 sidecar：事件映射/幂等/取消
cd agent-runtime
node --experimental-strip-types selftest.ts                   # 硬崩溃后续跑 + 重复投递幂等
node --experimental-strip-types test-model-run.ts             # 模型驱动：artifact + usage + 密件落在 instructions
node --experimental-strip-types test-nudge.ts                 # 只答散文不交差时，催一次能救回来
node --experimental-strip-types test-write-file.ts            # write_file：沙箱内放行、逃逸全拒、真写出文件
node --experimental-strip-types test-handin.ts                # 交差 ref 规范化：双反斜杠还原、没写过的文件拒收
node --experimental-strip-types test-approval-chain.ts        # 审批全链：拦下→登记→按单放行→不跨单泄漏
node --experimental-strip-types test-tier-model.ts            # cost_tier → 模型映射
node --experimental-strip-types test-background.ts            # 后台任务：每步 checkpoint
node --experimental-strip-types test-approval.ts              # 审批门
node --experimental-strip-types reference\24-child-tasks.ts   # 官方示例：failFast / abort 级联 / 重启续跑
```

测试就是 `agent-runtime/*.ts` 本身，没有 `test/` 子目录，也不要 `--test`：
这些脚本自己打印 `=== N passed ===` 并以退出码表态。

### 6.2 混沌矩阵（每次发版前跑）

| 杀点 | 期望 |
|---|---|
| 模型流式中途杀 sidecar | 重启后重发；无重复产物；台账不越级 |
| `submit_artifact` 已发、未回调时杀 | Python 幂等返回首次结果；不产生第二份初审 |
| 审批等待中杀 | 确认单作废；run 转 BLOCKED；不自动批准 |
| `bash` 执行中杀 | 绝不重跑；转 BLOCKED 并说明"可能产生了半成品" |
| Python 先杀、sidecar 后杀 | 重启对账把幽灵任务取消 |
| sidecar 反复崩溃 3 次 | 熔断为"执行内核不可用"，不静默降级 |

断言统一落在三处：`commissions` 表的合法迁移、`commission_events` 的只追加、`agent_telemetry_spans` 无重复计数。

### 6.3 其他

- **24h soak**：20 单混合负载，看 RSS、句柄、孤儿进程、DB 体积。
- **备份/恢复演练**：`backup_local_databases.py` 产出 + 从快照恢复后 sidecar 能 `resume()`。
- **A/B 语义评测**：Worker 输出质量不能用快环的 `evals/` 证明；按 `docs/TESTING.md` 的"机制通过 ≠ 语义合格"纪律，单独做 10 例盲评。
- **协议零漂移**：`check_protocol.py` 必须证明没有新增浏览器可见事件/字段。

---

## 7. 备选方案与 ROI

| 方案 | 工作量 | 覆盖的问题 | 不覆盖 | 建议 |
|---|---|---|---|---|
| **B1. Python 内 durable job 表** | ~2 周 | 进程重启后续跑、幂等、租约、对账 | 中途 steer、fork、子 Agent、多端 | 若只要"别丢活"，选这个 |
| **B2a. 每单 Node runner** | ~2 周 | 崩溃续跑、每单隔离、取消进程树 | 实时 steer、共享 task graph | **第一步推荐** |
| **B2b. 常驻 sidecar** | 4–6 周 | B2a + 实时 steer + 一张 task graph | 多端 | 需要中途插手时再做 |
| **B3. 整体替换** | 10–14 周 | B2b + conversation 为第一记录 | — | 仅在多端/多人成为目标时 |
| **B4. 现状 + 监督进程** | ~3 天 | 进程被拉起、卡死被发现 | 续跑、幂等 | 过渡期兜底 |

B1 的最小形态（如果 Phase 0 gate 不过）：

```sql
CREATE TABLE durable_jobs (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
  status TEXT NOT NULL,            -- queued|running|waiting|done|failed
  lease_owner TEXT, lease_until REAL,
  attempts INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE,
  created_at REAL, updated_at REAL);
```

配一个 `asyncio` worker + 启动扫描 + 租约续期，就能拿到"重启后续跑 + 不重复执行"，不需要第二套运行时。

**成本侧写**：B2b 的 sidecar 常驻约 80 到 150MB RSS。B2a 的 runner 只在跑单时占内存，代价是每单一次 Node 启动。每个 Worker conversation 的 tool schema 会多几百 token 每轮。崩溃重发可能多一次模型调用。最大成本是双运行时的心智负担与发版复杂度，两个 lockfile、两套 CI、两种日志格式。

### 7.1 与直接引入 npm 包的区别

`npm install @earendil-works/pi-durable` 是前提，不是方案。这个包是库，不是服务。README 的入口是 `Harness.open()`，没有 HTTP 服务端。

这个仓库的生产链路是 Python。Node 只跑测试、前端资源与 Playwright。所以把包加进根 `package.json` 之后，运行时没有任何代码调用它。`agent-runtime/package.json` 已经锁了 `1.3.0`，这一步其实已经做了一半。

真正的区别在运行时形态。三种。

| 形态 | 谁运行 Node | 存储 | 崩溃续跑 | 实时 steer | 取消 | 工作量 |
|---|---|---|---|---|---|---|
| A 进程内 | 慢环改写成 TS，Node 当后端 | 一个库 | 有 | 有 | 有 | 10 到 14 周 |
| B 每单 runner | Python 为每单拉起一个 Node 进程 | 每单一个 SQLite | 有，Python 扫描未完成的单再拉起 | 无 | 杀进程树 | 约 2 周 |
| C 常驻 sidecar | 一个长期 Node 进程 | 一个 SQLite | 有，进程自己 resume | 有 | abort 树 | 4 到 6 周 |

B 和 C 用同一个包，同一套 API。C 多出来的只有实时插手和一张跨单任务图。代价是 RPC、监督进程、启动对账和单点故障。

B 的适配方式与 `cli_subproc` 同形。`prepare_context` 写密件。`execute` 拉起 `node run.ts --commission <id>`，读 JSONL 事件流。`cancel` 杀进程树。数据落在 `data/durable/<commission_id>.sqlite`，归档就是删文件，一个单坏了不影响别的单。

B 的续跑由 Python 负责。启动时对每个 `running` 的单检查库里的 task 状态，未完成就重新拉起。BLOCKED 续答不受影响，因为会话持久，重新拉起同一个库再投递一条 follow-up 就能继续。

B 丢掉的是实时 steer。C 留到确实需要"老板中途插话"或"多个 Worker 共享一张任务图"时再做。

按今天的负载，这次替换的净值是降级。升级的只有崩溃续跑一项，代价是第二个运行时和两份真相。三个 Worker 现在都是 `adapter: manual`，长任务执行这条路还没跑起来。真要做，先做 B2a，别先做 B2b。

---

## 8. 风险登记册

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Pi Durable 是 experimental，API 随时变 | 高 | 高 | 锁死 `1.3.0` + lockfile；所有调用收进 `adapters/durable.py` 一个边界；升级必须过混沌矩阵 |
| 自建端点不透传 function calling | 中 | 高 | Phase 0 Gate；不过就转 B1 |
| 双真相（台账 vs task） | 高 | 高 | 所有权矩阵写进文档；启动/周期对账；永不自动 `done` |
| Windows 进程监督（孤儿、端口、锁） | 中 | 中 | `taskkill /T`、`proper-lockfile`、健康探针、日志落 `data/` |
| Prompt/行为回归（原生工具 vs JSON） | 中 | 高 | 只先迁 Worker；A/B 盲评；秘书循环不迁 |
| 成本上升（schema/重试/compaction） | 中 | 中 | `maxRetries` 压低；只读工具才 safe；compaction 阈值按模型覆盖 |
| 安全（工具越权、凭据） | 低 | 高 | loopback + token；默认只读；参数过 `_CREDENTIAL` 脱敏；不装 shell |
| CI 复杂度（两个 Node 工程） | 高 | 低 | 根 `package.json` 增 workspace 脚本；CI 分别 `npm ci` |
| 存储膨胀（entry 不删） | 中 | 中 | 季度归档 + 冷库只读 |

---

## 9. 本周就能做的 5 件事

1. **跑 Phase 0 的 spike**：`npm install` + 官方 `24-child-tasks` + 真实端点 3 工具任务，半天出结论。
2. **冻结 `replay` 策略表**（§4.6）：这是迁移的安全底线，先于任何代码。
3. **把 §4.3 的 RPC 契约写成 `docs/` 草案**，并给 `check_protocol.py` 预留 `INTERNAL_ROUTES`（只加常量，不改检查逻辑）。
4. **在花名册加 `durable:` 配置块但 `enabled: false`**：先让配置形状定下来，调度核心仍然只读 `adapter`。
5. **挑一个 Worker（scribe）做影子对比**：同一份密件，`cli_subproc` vs `durable` 各跑 5 单，比较事件流与产物。

## 10. 需要拍板的三件事

1. **目标到底是什么**：崩溃恢复 / 中途 steer / 多端多人 / 代码简化？答案会直接改变方案（见 §7）。
2. **能否接受一个常驻 Node 进程**（Windows 本地、多一个 lockfile、多一套日志与 CI）？
3. **能否接受 4–6 周不给慢环加新功能**（迁移期冻结 `loop.py`/`dispatcher.py` 的行为变更）？

如果 1 的答案是"只想别丢活"，直接做 B1；如果 1 包含"想给每个 Worker 独立 Agent、崩溃能续跑"，做 B2a；如果还包含"人要能中途插手"，做 B2b；只有 1 明确包含"多端或多人"，B3 才值得。
