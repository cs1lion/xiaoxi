# 决策留痕：2026-10-18 durable 接管补齐 + 三条主线 review

本轮开头是用户的一句话要求：先 review 纠正近期错误、查漏补缺，重点评估
pi-durable 的接入是否完备（接管本地 agent、派发、持续监督、验收、中断恢复），
并检查前端 3D 形象交互、OpenUI 式全量结构化输入、记忆与自进化的完成度。
可以制定测试方案并按测试反馈改代码。

执行方式按 pstack 的 poteto-mode（作者 Lauren Tan，本机已装 pstack 0.15.25）。

## 用到并改变了决策的原则

- **Prove It Works**：接管不是读代码推断的，用 DSH + step-3.7-flash 真跑了一单，
  拿到 `out/titles.md` 和 13 条事件、3 次模型调用的 span。
- **Fix Root Causes**：接管失败不是"DSH 不配合"。三个根因分别在路径解析、
  stderr 丢弃、幂等键粒度，都改在源头，没有加兜底分支。
- **Make Operations Idempotent**：幂等键从 commission 改成 commission@attempt。
  重投同一次仍收敛到同一单，重做拿新单并真的再跑一次执行体。
- **Explain the Number**：门禁 28 项全绿且 0 跳过；`run_gates.py` 的跳过即失败
  口径没有放宽。A/B 的 10/10 与 token 差 15 倍都写明了"不能当收益"。
- **Encode Lessons in Structure**：文档声称"启动对账已完成"而代码里没有，这类
  缺口靠新增门禁与断言钉住，不靠文字提醒。

## 一、durable 接入：接管这条线此前是断的，现在通了

### 实测通过的部分

`scripts/probe_takeover_production.py` 走生产链路（`ExecutiveService.delegate`
→ 选将 → 派单 → durable 适配器 → sidecar → DSH shim → DeepSeek Harness）：

- `status=ready_to_report`，初审 0 findings
- 交付文件真实存在（DSH 用自己的 write 工具写的 `out/titles.md`）
- 遥测 `calls=3`、`prompt_tokens=23678`：被接管执行体的模型调用落进了 span
- 台账 13 条事件：逐步记录的，不是跑完才知道发生了什么

`agent-runtime` 侧 `test-takeover.ts` 17 项、`test-takeover-recovery.ts` 9 项全绿，
证明"接管中硬崩溃、重开不重复拉起执行体"成立。

### 修掉的四个缺陷

| 缺陷 | 症状 | 根因 | 修法 |
|---|---|---|---|
| 相对路径 | 整单 2.3s 失败，只有一句"没有交差" | 花名册 `args` 相对仓库根写，sidecar 子进程 cwd 是本单沙箱 | 适配器把 args 解析成绝对路径 |
| stderr 丢失 | 失败原因不可见 | `runLocalAgent` 只读 stdout | 分开收 stderr 尾部，没交差时带进失败信息 |
| 幂等键太粗 | 初审打回后执行体不被再拉起，旧成果被原样再交 | `RunIndex` 只用 `commission_id` 且永不过期 | 键改 `commission_id@attempt`，attempt 取台账 `retry_count` |
| 审批链在生产里没生效 | hook 从不拦任何工具 | `XIAOXI_APPROVAL_TOOLS` 只有测试设过 | 花名册 `durable.approval_tools` 经 `extra_env` 随进程注入 |

### 文档说完成、实际没做的两件

1. **启动对账**：`PI_DURABLE_MIGRATION.md` Phase 2 打了 `[x]`，代码里只有进程
   健康巡检。`recover_interrupted()` 把 running 一律转 BLOCKED，sidecar 里已经
   跑完的单成果被扔掉。已补 `GET /v1/lookup` + `recover_interrupted(durable_lookup=...)`，
   只采纳内核已终态 `completed` 且带 summary 的成果。铁律不变：永不替内核把
   没跑完的单判成功，永不自动 `done`。
2. **接管侧遥测**：`cli_subproc` 会注入 `AQI_TELEMETRY_PROXY`，durable 只从
   sidecar 进程环境读，而监督器从不设它。已改为从密件带进 `RunMeta.telemetry_proxy`。

### 中断恢复：两种死法要分开说

- **宿主重启、内核活着**：对账接回内核已完成的成果。这是本轮补的那条路。
- **内核自己死了**：适配器连不上，那一单如实转 BLOCKED 请示。答一句之后靠
  attempt 递增真的重新拉起执行体。内核不可用时派单同样转 BLOCKED 并说明，
  不假装执行。

`scripts/probe_takeover_production.py` 的 ②③④ 段覆盖这三种。本轮只拿到 ① 的
完整实测（`ready_to_report` + 遥测 + 13 条事件）；②③④ 段在探针能跑之后补录。
② 的离线等价物已落在 `scripts/test_durable_adapter.py`（假 sidecar，20 项）。

### 网关上游：少一个外部依赖

15722 网关默认上游是 `127.0.0.1:15721`（CC Switch，另一个产品的进程）。它没起时
网关 `/health` 照样 200，但每次 Worker 调用 502 `upstream primary unavailable`。
DSH 接管第一次实测就是这样失败的。`scripts/run.py` 现在把默认上游落在 `.env`
的 `LLM_BASE_URL`，显式设 `AQI_GATEWAY_UPSTREAM` 仍优先。回归
`scripts/test_gateway_env.py`（6 项）。

## 二、OpenUI 式全量结构化输入：输出侧已成体系，输入侧只有两个动词

协议 §23 把**输出**收口得很干净：七种积木 kind、一个信封、一个渲染引擎、
四条产出路径共用。输入侧的对账表：

| 输入点 | 现状 | 证据 |
|---|---|---|
| 督办验收 / 换将 | 已结构化 | `aqi_action` 白名单只认 `dispatch:accept` / `dispatch:reassign`，`server/app.py:_aqi_action` |
| 写操作确认 | 已结构化 | `assistant_confirm` 带 `run_id` / `confirmation_id` / `approve`，`web/app.js:confirmAssistant` |
| 胶囊选项 | **半结构化** | `choice_pills` 的 cmd 多是 `say:<文本>`，点击后作为一轮自由文本重新进 LLM，`web/app.js:handleAqiAction` |
| `choice:<index>` | **协议写了、实现没有** | §23.3 声明了它，`handleAqiAction` 没有这个分支，点击落进"未知业务动词：忽略" |
| 任务到期时间 | 部分结构化 | `task.create` 接受 `due_at`，但自然语言"明天下午三点"仍落在 description，`docs/OPTIMIZATION_ROUND2_20261017.md` 记着这条未验证 |
| 记忆删除 / 修订 | 已结构化 | `DELETE /api/memories/{id}`、`PATCH /api/memories/{id}`，`web/app.js:1417,1461` |
| 设置项、抽屉导航 | 本地结构化 | `ui:drawer:<view>` / `ui:preview:<ref>` / `ui:starmap` |
| 触摸角色 | 半结构化 | `touch` 只带 `region`（head/face），服务端选台词样本，不带意图 |

本轮修掉 `choice:<index>`：前端补分支（越界与坏 index 忽略），并在
`scripts/check_protocol.py` 加第 7b 条——协议 §23.3 的 cmd 动词必须在前端有分支，
少一个就让门禁报警。验证过：把 `choice` 分支改名，门禁立刻 FAIL。

## 三、3D 形象交互：说话侧很厚，输入侧只有触摸一条线

已真接线的（有调用方，不是死模块）：

- 双态镜头 `StageCamera` 读 `lip.speaking`，静默中景、开口近景，
  `web/avatar/render/vrm.js:371,754`
- 消息驱动表情/注视：`recording` 起倾听姿势并开面部镜像、`thinking` 转思考注视、
  `interrupted` 收嘴，`web/avatar/index.js:568-610`
- 语篇手势与 backchannel：用户说话时自动点头，`web/avatar/index.js:700-712`
- 触摸热区：摸头点头、戳脸有三级反应（含"傲娇"），并经 `aqi-touch` 事件送到
  服务端选真实台词样本走语音链路，`web/avatar/index.js:350-368`、`web/app.js:1878`
- `ui_nav.gaze_target` 让形象看用户指的方向，`web/app.js:1220`

结构性缺口（用事实描述，不给方案）：

- 用户输入到角色动作的映射只有一条：`touch` → region。打字、点积木、
  点胶囊、验收、打回都不通知形象层。
- 角色对"小溪自己在干活"没有表现：commission 状态变化（派单/打回/验收）
  不到形象层，她只在说话时有反应。
- `config/avatar.yaml` 的 `gaze.targets` 有 `side`（侧目看抽屉），由 ui_nav 消费；
  其余注视点都在说话链路上。

## 四、记忆与自进化：机制层有测试背书，生产数据是零

代码与测试的真实状态：

- 效用反馈（MemRL）链路完整：`mark_used` 记注入 → `settle_pending` 用紧邻的
  下一条用户消息结算 → `reinforce_memory` 更新 Q。显式确认/纠正才算奖励，
  沉默与换话题不算，`server/memory/feedback.py:106-135`
- 检索两阶段：相关性门槛过后乘 `[1-w, 1+w]` 有界效用因子，默认 w=0.30，
  Q 按观测次数向 0.5 收缩，`server/memory/retriever.py:72-91`
- 程序性记忆 lessons 有完整生命周期与近义合并、按证据退役，
  `server/memory/lessons.py`
- 自进化 cycle 有 idle 门控、每日上限、模型调用上限，`server/assistant/loop.py:772-781`

读真实库得到的数字（只读查询，未写入）：

```
memory.db: memories=1, memory_usage=0, lessons=0, diaries=0, sparks=0
           sessions=498, messages=731
telemetry.db: agent_telemetry_spans=0
agent_runtime.db: agent_runs=0, agent_events=0
```

唯一那条记忆 `use_count=10`、`utility_n=0`、`last_reward_at=None`。也就是说
**效用在生产里一次都没被结算过**：`memory_usage` 表 0 行，Q 值从未偏离 0.5。
机制有单元测试（`test_memory_self_evolution.py` 覆盖 rerank、归因、幂等、
过期、硬删除清理），但没有任何测试证明"效用在真实对话里改变过检索排序"。

自进化同理：`self_state` 三张表全空，`last_cycle_at=None`，`cycles_today=0`。
WS 通知链路的坑已在 `scripts/test_self_evolution_send.py` 修掉并落门禁
（事件被二次翻译，每一条都被静默丢弃）。

## 五、门禁

`scripts/run_gates.py` 从 23 道涨到 28 道，新增的四条都是此前只写在
`docs/TESTING.md` 里、报告里根本没有的：

- `durable_adapter`（含重启对账）
- `durable_supervisor`
- `durable_takeover`、`durable_recovery`（agent-runtime 侧，`cwd` 支持）
- `gateway_env`

本轮末态：28 项跑过并断言，0 失败，0 跳过。

## 六、仍未做（需要拍板或更多实测）

1. `probe_takeover_production.py` 的 ②③④ 段跑完并留读数。分类器不可用时
   探针跑不了，本轮只拿到 ①。
2. 四个 `cli_subproc` Worker 是否切 durable。接管之后"切过去等于能力回退"
   这个前提变了（真机 CLI 的工具还在），但要逐个确认 shim 接受 `AQI_WORKDIR`
   注入。DSH 接受（`resolve_workdir()` 优先读它）。
3. `durable.approval_tools` 填什么。留空 = 不拦任何工具。写操作类
   （`write_file` / `run_worker`）建议列上，但这会改变现有行为，等确认。
4. 预算口径：`telemetry.commission_usage` 把一单所有重试的 prompt_tokens 求和，
   与"单次 Commission 预算"不是一回事。A/B 里基线的 8/10 失败全出于此。
   要 Role 0 定按次还是按单。
5. 效用/自进化缺生产数据。可以先跑一次 `evals/cases/lesson-injection-offline.yaml`
   确认注入路径，再决定要不要做一次端到端验收脚本。
