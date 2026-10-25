# 让小溪成为能干活、能跟踪、能验收的助手：能力强化探索

- 日期：2026-10-24
- 口径：HEAD `2b08798`，当日逐文件 read / search_files 自查；未逐行读的文件在文中标 inferred
- 方法：ask-matt 式探索。ask-matt 的做法是听完状况描述后指出该走哪条路、为什么不是另一条、之后接什么。本次按同一形态组织：5 路现状盘点 → 4 路外部研究 → 路由器定优先级 → 3 个角度出方案 → 评审席合并 → 2 个对抗核查者（证据核查 + 红线可行性）→ 成文。核查标 refuted 的两处已按 correction 修正本文
- 范围：只做研究与方案，不改任何代码。涉及 executive.py 与协议字段的部分按规矩停工等 Role 0

## 一段话回答

还能做的努力集中在四个空洞上，按「无红线优先、被门控的并行准备」排序：一、外部工具通路从 0 到 1——`adapter:mcp` 已注册并接进派单链（server/assistant/adapters/mcp.py:26、adapters/__init__.py:19、dispatcher.py:428），但花名册 8 个 worker 无一条 mcp 条目（HEAD `2b08798`，2026-10-24 读 config/agents_roster.yaml:53,63,77,95,120,141,169,188），补一个真实可跑的 mock stdio server 与 roster 条目即可端到端打通，这是其余一切的地基；二、验收兜底接通——server/verify 的三种确定性 check（server/verify/check.py:62-66）与慢环零接通，把其中两种桥接进 evaluator 的 findings，并补上被承诺却全仓不存在的 scripts/test_verify.py（server/verify/check.py:30、verifier.py:30）；三、跟踪可观测——MCP 适配器补 tools/list 与 PROGRESS 事件，dispatcher 三个出口补 TOOL_EXECUTION 终态，只使用 PROTOCOL.md:637 既有 payload 字段；四、收口与持久化——非 durable 单「初审打回后 running 且无人驱动」需要收口扫描，progress 需要落库或只读复盘面，这两项分别被 executive.py 红线与 [PROTOCOL_REQUEST] 协议门门控，未获批前只合入测试文件与 sidecar 内部只读路由。前两项无协议门、不碰红线，应立刻做；后两项的不越线部分可并行推进。

## 一、结论先行

小溪今天已经具备完整的「派单—执行—初审—台账—广播」骨架，缺的不是架构而是三条通路的最后一公里：外部工具面为空、确定性验收闲置、进度没有终态与持久层。

按投入产出与红线风险排序，可做的努力是：

1. **点亮 MCP Worker 通路（P0）**。今天小溪操作外部工具的能力为零：`McpBridgeAdapter` 已注册（server/assistant/adapters/mcp.py:26），花名册里却没有一个可派的 mcp 条目（HEAD `2b08798`，2026-10-24 读 config/agents_roster.yaml:53,63,77,95,120,141,169,188，adapter 全为 manual / cli_subproc）。写一个纯标准库的 stdio mock server、加一条 roster 条目、补行为测试与门禁接线，就能把「通过 MCP 操作外部工具」从 0 变 1。派单跑在 spawn 出的后台任务（server/app.py:801-833，:833 为 `conn.spawn(_executive_delegate(...))`），不违反快环不阻塞约束。
2. **接通确定性 Verifier（P1）**。server/verify 只读文件、禁 socket/subprocess（server/verify/check.py:28-30），三种确定性 check（check.py:62-66）却与慢环零接通；核对逻辑在 evaluator 另有一份实现（server/assistant/evaluator.py:115-134,184-253）。桥接两种 check 进既有 findings 形状，同时补上被 check.py:30 与 verifier.py:30 承诺、全仓却不存在的 scripts/test_verify.py。
3. **解锁工具广度与进度终态（P1）**。适配器 docstring 自称有 tools/list 却未实现（mcp.py:3-4 vs execute:54-98 只有 initialize 与 tools/call）；MCP 路 progress 为空，execute 只 yield 一条 LOG（mcp.py:79），而 LOG 被 dispatcher 整体丢弃（dispatcher.py:453-482 无 log 分支）；TOOL_EXECUTION 只发 status="running"、无终态、无 steps_total（dispatcher.py:462-465），而 broadcast/v2.py:507-523 与 PROTOCOL.md:637 早能承载。
4. **收口与 progress 持久化（P1/P2）**。非 durable 单的收口扫描被显式跳过（executive.py:409-410），四条驱动入口都是单趟执行、不经 drive() 的重试网（executive.py:269-288 对照 488-507,541-543）；progress 的 detail 只进 WS 不落库，commission_events 只追加 opened/transition/step 三种 kind（threads.py:580-582,676,735,749）。这两项分别被 executive.py 红线与协议门门控，未获批前只合入测试文件与 sidecar 内部只读路由。

一句话：先做 M1 与 M2 两条不越线的路，把「能派、能验」立起来；M4/M3 紧随解锁广度与可观测；M5/M6 的被门控部分准备好不越线子集，等 Role 0 放行。

## 二、现状盘点：小溪今天已有什么

| 能力面 | 今天已有什么 | 证据（file:line） |
|---|---|---|
| 派单链 | delegate → drive() 重试网 → dispatcher.run_commission → 适配器 | server/assistant/executive.py:220-243（delegate）、:269-288（drive 的重试 while）、dispatcher.py:391-419（run_commission） |
| 执行权唯一性 | 租约制：`_claim_run` 失败即 `commission_already_running`，threading.Lock + set | server/assistant/dispatcher.py:391-419（:410-411）、:547-556 |
| 快环不阻塞 | 快环只用配置样本或台账直出回答，慢环另起后台任务 | server/app.py:799-800 注释、:801-833（:833 spawn） |
| Worker 花名册 | 8 个 worker，adapter 为 manual / cli_subproc；adapter_types 索引注明 manual / cli_subproc / http_api / mcp / durable 五类 | config/agents_roster.yaml:53,63,77,95,120,141,169,188；索引 :209-214 |
| MCP 适配器 | `McpBridgeAdapter` 已注册，initialize + tools/call + artifact 提取 + cancel 杀进程组 | server/assistant/adapters/mcp.py:26、:54-98、:135-151、:100-103 |
| durable 内核 | agent-runtime sidecar 已支持 Skills、MCP、compaction、read/write_file、approval_tools 确认链；工具按 `mcp__<server>__<tool>` 注册 | config/agents_roster.yaml:240-254；agent-runtime/src/mcp.ts:153 |
| durable MCP 配置面 | config/mcp.json 是空对象，即不连任何 server | read_file config/mcp.json 全文 3 行：`{"mcpServers": {}}`；roster:243-245 注释 |
| 真机 Worker | claude / workbuddy / dsh / sandbox / codex 五个 shim，走行协议 | scripts/worker_shims/*.py；roster:89-203 |
| 初审验收 | 三档置信度铁律 + seed_rules + eval_commands；L1 客观校验含 artifacts_exist（判 `Path(ref).is_file()`） | server/assistant/evaluator.py:24、:78、:115-134、:184-253、:508-536；config/agents_roster.yaml:263-286 |
| 确定性 Verifier | server/verify 三种 check、Reader 注入、passed 三态 bool/None | server/verify/check.py:44-50、:62-66；server/verify/result.py:29-49 |
| 状态机与台账 | §19 合法迁移集六组；commission_events 三种 kind | server/interaction/threads.py:531-538、:681-738、:580-582,676,735,749；docs/PROTOCOL.md:328-345 |
| 事件协议 v2 | TOOL_EXECUTION payload 为 `tool,status,steps_done?,steps_total?,detail?`，至多一次可丢 | docs/PROTOCOL.md:637；server/broadcast/v2.py:507-523（:511 注明可丢） |
| 周期巡检 | `_reconcile_loop` 周期调 `reconcile_durable`，且只在 durable 绑定时创建 | server/app.py:1524-1534、:1634-1637 |
| 门禁 | run_gates 的 GATES 表与 ALWAYS_REQUIRED；check_protocol 比对两边 SIDECAR_ROUTES 并纳管 INTERNAL_ROUTES | scripts/run_gates.py:54-394、:397-411；scripts/check_protocol.py:187-208、:396-401、:550-553 |

已核实的一处归属纠偏（对抗核查标 refuted，采纳其 correction）：「跳过非 durable」的代码在 `ExecutiveService.reconcile_durable`（server/assistant/executive.py:409-410），不在 `_reconcile_loop`（server/app.py:1524-1531）里；后者只是周期调用者，本身不含 adapter 判断。实质结论不变。

## 三、差距分析：离「操作外部工具 + 下达命令 + 跟踪 + 验收」还差什么

| 目标能力 | 今天到哪一步 | 还差什么 | 证据 |
|---|---|---|---|
| 操作外部工具（MCP） | 适配器写完了，花名册没条目，零行为测试 | 一个真实可跑的 MCP server + roster 条目 + 行为测试 + 门禁接线；tools/list 发现；durable 侧 config/mcp.json 仍为空 | mcp.py:26 vs roster 无 mcp 条目；mcp.py:3-4 docstring 自称 tools/list 而 execute:54-98 未实现；config/mcp.json 全文 3 行 |
| 下达命令 | 能派、能跑、有租约、初审打回有重试上限 | 四条单趟入口（answer / resume / reassign / REST /run）不经 drive() 重试网；初审打回把单迁回 running 后若驱动来自这四条，就 running 且无人驱动，只能重启收口；非 durable 单没有收口扫描 | executive.py:488-507,541-543；app.py:705-710；executive_api.py:145-152；dispatcher.py:650（submit 未过即 transition running, retry_delta+1）；executive.py:409-410；app.py:1634-1637 |
| 跟踪命令进度 | 有一条 TOOL_EXECUTION running 广播；sidecar 侧事件本就持久化 | 无终态（只有 running）、无 steps_total；适配器 LOG 被 dispatcher 整体丢弃；progress 不落库，commission_events 只有三种 kind；重连后从 0 重放而非断点续读 | dispatcher.py:462-465、:453-482；threads.py:580-582,676,735,749；broadcast/v2.py:511；durable.py:230-232（只传 task_id）vs agent-runtime/src/rpc.ts:189-190（已支持 since）；runtime.ts:260-266 与 rpc.ts:105-114（/v1/runs/{id} 已回传全部 events，inferred 于 docs.ts:7 的 RunEvents doc 未逐行读） |
| 验收结果 | evaluator 有文件存在性、token 预算、acceptance 规则、铁律四层 | server/verify 的三种确定性 check 闲置；被承诺的 scripts/test_verify.py 不存在，verify 包零测试覆盖；跑测试 / 语义类验收仍被 verify 明确排除 | check.py:54-60（SKIPPED_CHECKS 含 exit_code_zero、rubric、ask_user、schema_valid）；check.py:30 与 verifier.py:30 的承诺；evaluator.py:115-134,184-253 |
| 外部工具可观测 | durable 侧 toolsFor 已实现 | toolsFor 无任何 HTTP 出口，只被测试消费；宿主拿不到工具目录 | agent-runtime/src/runtime.ts:67,276；test-profiles.ts:37,54（全仓 grep 结果） |

结论：四项目标能力里，「下达命令」基本可用但不可恢复，「操作外部工具」是零，「跟踪」有信号无终态无持久层，「验收」有实现但确定性兜底闲置。

## 四、外部参照：四个方向的可用模式

### 4.1 MCP 生态

- 协议版本时间线：2024-11-05 初版（initialize 握手 + 有状态会话）→ 2025-03-26 → 2025-11-25 → 2026-07-28 最大修订，转向无状态，移除 initialize/initialized 握手与 Mcp-Session-Id，版本与能力改由每个请求的 `_meta` 携带，新增 server/discover。来源：[MCP Specification Version Timeline](https://hidekazu-konishi.com/entry/mcp_specification_version_timeline.html)、[MCP 2026-07-28 修订：协议走向无状态](https://m.toutiao.com/article/7685336484085350947/)、官方 Key Changes：[modelcontextprotocol.io 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)。
- 对小溪的含义：适配器 pin `2024-11-05`（mcp.py:23），mock server 按该版实现即可端到端跑通；真连现代 server 时需要版本协商，这是后续 effort，不在本次范围（inferred，基于上述外部研究）。

### 4.2 命令跟踪

- MCP 的进度模式：客户端在 tools/call 的 `_meta.progressToken` 挂追踪 ID，服务端干活期间以 `notifications/progress` 通知推送 progress / total / message，最终结果仍回原请求；行为良好的客户端会为每条进度通知重置请求超时。来源：[MCP 进度通知：看见任务进展，还要等待最终结果](https://blog.csdn.net/QWERTYwqj/article/details/167279489)、[MCP 事件模型：通知类型、消息格式、幂等性](https://blog.csdn.net/sinat_25548781/article/details/168024319)。
- 可借鉴点：进度走通知通道、结果走响应通道，二者不混。小溪侧对应「PROGRESS 事件走 dispatcher 的 progress 分支，artifact 走 artifact 分支」，不要用 LOG 传进度（dispatcher.py:453-482 无 log 分支）。

### 4.3 验收验证

- 分层模式：unit tests / deterministic metrics / LLM-as-a-judge rubrics / integration tests 四层并行，用确定性断言与布尔 judge 检查替换主观打分。来源：[Google 工程师讲解如何系统评测 AI 智能体](https://aihot.news/items/ojpe4mr67tjbrt5atyi6hyapl)。
- 交付物验收四阶：Evidence → Verification → Evaluation → Acceptance，支持 PASS / FAIL / NEED_REVIEW 三态，明确「模型说自己完成了，不等于任务真的完成了」。来源：[AI Agent 任务完成度如何评估：从 Response Evaluation 到 Deliverable Evaluation](https://developer.aliyun.com/article/1769672)。
- 与小溪的呼应：server/verify 的 passed 三态（result.py:33-40，None 表示不归我判）与上述 NEED_REVIEW 同构；evaluator 的 artifacts_exist 判 `Path(ref).is_file()`（evaluator.py:126-133）就是 state_check 的最小实现。

### 4.4 ask-matt 路由

- Matt Pocock 的 skills 集合里，/ask-matt 不是又一个干活命令，而是内置导航：读项目里的 agent 文档与路径，回答「下一步该用哪个命令、按什么顺序、之后接什么」。来源：[mattpocock/skills](https://github.com/mattpocock/skills)、[Claude Code 最强 Skills 路由器：/ask-matt 完全上手指南](https://www.cnblogs.com/lincats/p/21936334.html)。
- 对小溪的启示：快环的 executive_route 已经是「导航不干活」的形态（app.py:799-800，慢环另起后台任务）。ask-matt 模式可借鉴的是「路由决策本身要有据可查」——小溪的选将打分只认 strength / load / cost 三项（roster:14-18，dispatcher.py:246-251），这一点已经对齐；缺的是路由理由可解释，目前没有把命中关键词回传给调用方（inferred）。

Sources: [hidekazu-konishi.com MCP timeline](https://hidekazu-konishi.com/entry/mcp_specification_version_timeline.html)；[toutiao MCP 2026-07-28](https://m.toutiao.com/article/7685336484085350947/)；[modelcontextprotocol.io changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog.md)；[CSDN MCP 进度通知](https://blog.csdn.net/QWERTYwqj/article/details/167279489)；[CSDN MCP 事件模型](https://blog.csdn.net/sinat_25548781/article/details/168024319)；[aihot Google agent eval](https://aihot.news/items/ojpe4mr67tjbrt5atyi6hyapl)；[aliyun Deliverable Evaluation](https://developer.aliyun.com/article/1769672)；[mattpocock/skills](https://github.com/mattpocock/skills)；[cnblogs ask-matt](https://www.cnblogs.com/lincats/p/21936334.html)

## 五、方案：P0 / P1 / P2 努力清单

每条含目标、改动文件、验收方式、风险、红线检查。优先级含义：P0 = 无红线无协议门、且是其他项的前置；P1 = 无红线或仅内部面，部分依赖 P0；P2 = 落库/迁移/API 面部分被协议门门控。

### M1（P0）点亮 MCP Worker 通路端到端

- **目标**：把「通过 MCP 操作外部工具」从 0 变 1，是其余一切的前提。
- **改动文件**：
  - `scripts/mcp_servers/echo_server.py`（新建）：纯标准库 stdio JSON-RPC MCP server，实现 initialize / tools/list / tools/call；run_commission 在本单临时目录写真实产物文件，返回 `{"type":"artifact","artifact":{...}}` 的 text content 块（契约见 mcp.py:7-8 与 _extract_artifact:135-151）；行协议写法照 scripts/worker_shims/sandbox_worker.py:33-39。
  - `config/agents_roster.yaml`（新增条目）：在 sandbox 条目（:167-180）之后加 `mcp_echo`，adapter: mcp、executable: python、args: [scripts/mcp_servers/echo_server.py]、timeout: 60、strengths: [MCP, 外部工具, 桥接, 调用]、cost_tier: cheap、max_concurrent: 1、eval_commands: [required_fields, artifacts_exist, token_budget, acceptance_met]。选将是 strengths 子串命中计分（dispatcher.py:246-251），关键词须避开 dsh / claude 已占的检索/代码/命令（roster:156,109）。
  - `scripts/test_mcp_adapter.py`（新建）：① initialize → tools/call → artifact；② 无 artifact content 块 → error 事件（mcp.py:85-89）；③ executable 不存在 → AdapterError（mcp.py:67-68）与超时路径（mcp.py:91-93）；④ cancel 幂等杀进程组（mcp.py:100-103 + base.py:108-143）；另加一条端到端：按 scripts/test_agent_adapter_ext.py:154-160 的 build_service 造 ExecutiveService，intent 带 mcp_echo 独有关键词 → delegate → commission 到 done 且 evaluator artifacts_exist 通过（该 check 判 `Path(ref).is_file()`，evaluator.py:126-133，故 echo_server 必须回真实文件 ref）。
  - `scripts/run_gates.py`：GATES 增 `mcp_adapter`（表 :54-394）并加入 ALWAYS_REQUIRED（:397-411）；否则新测试不在门禁视线内（:87-94 注释已把「写了不跑 = 死接线」列为事故模式）。
- **验收方式**：`.venv\Scripts\python.exe scripts\test_mcp_adapter.py` 全绿；`scripts\doctor_integration.py` 不报 executable 不存在（已对 mcp 校验 executable 与 args 路径，doctor_integration.py:61-77）；`scripts\run_gates.py --only protocol,core,evals,npm,adapter_ext,mcp_adapter` 全绿且无 SKIP。
- **风险**：适配器 pin protocolVersion 2024-11-05（mcp.py:23），mock server 按该版实现即可；真连现代 server 时 2026-07-28 已移除 initialize 握手（外部研究，见第四节来源）需另做版本协商，不在本 effort。echo_server 只写临时目录、不联网、不碰 data/commissions 之外路径。
- **红线检查**：未新增任何 WS 事件 / API 字段；未改 server/assistant/executive.py；未加调色板颜色；未重启 8000/8010；派单跑在 spawn 出的后台任务、不在快环阻塞（app.py:801-833 :833 spawn）。

### M2（P1）接通确定性 Verifier 到慢环验收

- **目标**：让「验收结果」有确定性兜底。今天慢环验收与 server/verify 零接通（对 server/assistant 检索 verify 的 import 0 命中，仅 service.py:839 一条注释出现 verify 字样），核对文件在 evaluator 另有一套实现（evaluator.py:115-134,184-253）；check.py:30 与 verifier.py:30 承诺的 scripts/test_verify.py 全仓不存在。
- **改动文件**：
  - `server/assistant/acceptance_verify.py`（新建）：把 evaluator 的 acceptance 规则翻译成 server/contract 的 Criterion/CheckSpec，调 verifier.py:70-107 的 verify()，把 CriterionResult.evidence（result.py:29-49）渲染成既有 _finding 形状（evaluator.py:78,180-181；findings 每项 `{rule_id, severity, detail}` 由 PROTOCOL.md:343 规定，rule_id 取值域自由）。passed 三态（result.py:33-40）里 None 必须渲染成「未判定 + note」，绝不变成失败 finding。
  - 同文件映射只做 verify 真实现的三种（check.py:62-66）中可对应的两种：artifact_exists / artifact_name → file_exists_nonempty；artifact_json → syntax_ok(lang=json)。第三种 csv_assert 在 evaluator 无对应规则，本 effort 不映射（对抗核查指出的小遗漏，在此补明）。artifact_contains / artifact_regex / artifact_max_bytes 留在 evaluator 原实现，不造第二份真相源。
  - `server/assistant/evaluator.py`：_check_acceptance（:378-387）末尾追加一类来自 verify 的 finding；不改 review 返回形状（:508-536）、不改 findings 截断（:526）、不改 passed 判定口径（:527）。
  - `scripts/test_verify.py`（新建，被承诺却缺失）：三种 check 正反例 + reader 注入（check.py:44-50）+ 「本包不 import socket/subprocess/sqlite3」静态断言 + evaluator 桥接用例（含 passed=None 不被当成失败）。
  - `scripts/run_gates.py`：GATES 增 `verify` 并入 ALWAYS_REQUIRED。
  - `docs/TESTING.md`：全局门禁清单补一行 scripts/test_verify.py。
- **验收方式**：`scripts\test_verify.py` 绿；`scripts\test_executive_v3.py` 绿；一次带 acceptance 的验收里能在 findings 中看见 verify 的 evidence 文本；`scripts\check_protocol.py` 绿（证明没加字段）。
- **风险**：桥接只覆盖两种 check；verify 明确不做 exit_code_zero（check.py:57）与 rubric，跑测试 / 语义类验收仍被协议门挡着，不在本 effort 越线。
- **红线检查**：findings 是既有形状（PROTOCOL.md:343），无新字段、无新 WS 事件；未碰 executive.py；新增文件与门禁均可回退。

### M3（P1）MCP 适配器补 tools/list 与 progress 终态

- **目标**：解锁外部工具广度与进度可观测。
- **改动文件**：
  - `server/assistant/adapters/mcp.py`：execute 在 initialize 之后加 tools/list；按 config.tool 在选择集校验存在性，不存在就发 error 事件并带可用工具名（不静默回落）；tools/call 的 arguments 从 4 个键（:82-83）扩为整份 worker_packet，使 attempt（dispatcher.py:442-443）与 deliverable/acceptance 摘要能到 Worker；tools/call 前后各 yield 一条 PROGRESS（事件类型早已存在，base.py:23）——LOG 被 dispatcher 整体丢弃（:453-482 无 log 分支），不要用 LOG 传进度。
  - `server/assistant/dispatcher.py`：_run_adapter 三个出口（artifact 落地 / error 中止 / block）各补一次 publish_tool_execution(status="done"|"failed")，并尽量带 steps_total；只使用既有入参（v2.py:507-523 已支持；PROTOCOL.md:637 payload 就是 tool,status,steps_done?,steps_total?,detail?）。这是调用方改动，不是按 agent 类型开分支，dispatcher.py:276-283「零侵入新增 Worker」约束仍成立。
  - `scripts/test_mcp_adapter.py`：补 initialize / tools-list / tools-call / cancel 四条行为路径，并断言 dispatcher 驱动 mcp worker 后 TOOL_EXECUTION 出现终态行。
  - `web/events-v2.js`：不改，仅确认并留注释：applyTool 把 TOOL_EXECUTION 当追加流水消费（:229-239），测试已出现过 status:"ok"（web/events-v2.test.mjs:328），终态行不破坏归约。
- **验收方式**：`scripts\test_mcp_adapter.py` 覆盖四路径与终态断言；`node web\events-v2.test.mjs` 与 `.venv\Scripts\python.exe scripts\test_v2_broadcast.py` 绿；`scripts\check_protocol.py` 不报新字段。
- **风险**：协议未枚举 status 取值域（PROTOCOL.md:637 只列字段名），终态字符串是约定而非契约，建议在 PR 说明中显式标注并请 Role 0 确认是否值得补枚举（补枚举属协议修订，仍走 Role 0）；改 dispatcher 属调度核心改动，虽可回滚但需 review；tools/list 拉取失败要降级成 error 事件，不能假装只有一个工具；适配器 pin 2024-11-05（mcp.py:23）与现代 server 兼容性需确认（inferred）。
- **红线检查**：无新 WS 事件 / API 字段（终态只是既有 status 字段的新取值）；dispatcher.py 不在项目红线清单内（红线是 executive.py、调色板、重启 8000/8010）；未新增按 agent 类型的分支。

### M4（P1）把 config/mcp.json 变成有主的工具面，并让工具目录可观测

- **目标**：durable sidecar 已会消费 config/mcp.json 并把每个工具包成 `mcp__<server>__<tool>`（agent-runtime/src/mcp.ts:153），但配置是空对象；已实现的 toolsFor 无任何 HTTP 出口。
- **改动文件**：
  - `config/mcp.json`：填入第一个条目 `echo`，command: python、args: [scripts/mcp_servers/echo_server.py]、approval: mutating（缺省值本来就是 mutating，mcp.ts:148，显式写出让闸门可见）；env 只继承最小集（mcp.ts:101-112），不会把 LLM Key 漏给 server。
  - `agent-runtime/src/rpc.ts`：新增 GET /v1/runs/{id}/tools，调 runtime.toolsFor（实现已存在却只被测试消费），放 authorized() 之后（:71-74）。
  - `agent-runtime/src/routes.ts`：SIDECAR_ROUTES 增加该路由（现表 :4-14）。
  - `server/assistant/adapters/durable.py`：SIDECAR_ROUTES 同步增加同一路由（现表 :34-44，:32-33 注释明写两边必须逐项一致）；仿 lookup（:264-293）加同步只读 tools(commission_id, attempt=0)。
  - `agent-runtime/test-mcp.ts`：加断言 toolsForServerAsync 产出的工具名带 `mcp__echo__` 前缀，且非 readOnlyHint 的工具被 addApprovalTools 收编（mcp.ts:153-157）；该文件已是 run_gates 的 durable_mcp 门禁（run_gates.py:308-312,410）。
  - `config/agents_roster.yaml`：durable 段在 mcp_config: config/mcp.json（:245）旁补注释，不改任何代码路径。
- **验收方式**：`node --experimental-strip-types agent-runtime/test-mcp.ts`（cwd=agent-runtime）绿；`node --experimental-strip-types agent-runtime/test-profiles.ts` 绿；`scripts\check_protocol.py` 绿——它专职比对两边 SIDECAR_ROUTES（check_protocol.py:396-401），少改一边会直接判红。
- **风险**：新路由是 sidecar 内部接口、不面向浏览器；工具目录故意不广播到前端，避免为新可观测面发明 WS 字段；真 server 命令行在 Windows 上要能起进程（doctor 只校验存在性，不校验能否握手）。
- **红线检查**：无新 WS 事件 / 公开 API 字段；新路由属 sidecar 内部面（与 durable_api.py:224 的 /internal/durable 同类，由 check_protocol.py:187-208 的 INTERNAL_ROUTES 同类纳管）；未碰 executive.py。两点来自对抗核查的保留意见：一是 `/v1/runs/{id}` 的 {id} 是 taskId 而 readRun 不回 conversationId，直接调 toolsFor(conversationId) 会拿不到参数，实现时需补 taskId→conversationId 桥接（让 RunView 一并返回 conversationId，或新增 toolsForTask，或 durable 侧走 commission_id→/v1/lookup 取 conversationId 再查）；二是 playbook 文本可被严格解读为要求新 API 路由走 PROTOCOL_REQUEST，稳妥做法是合入前向 Role 0 报备一句「新增两条纯内部只读路由」，不要静默合入。

### M5（P1）非 durable 单巡检兜底与「running 且无人驱动」收口

- **目标**：「下达命令」今天能跑但不可恢复。初审打回把单迁回 running 后若驱动来自四条单趟入口，就 running 且无人驱动，只能重启收口。
- **改动文件**：
  - `scripts/test_stalled_running.py`（新建，本 effort 唯一可立即合入的文件）：造一个 driver 已死、status=running 的 cli_subproc 单，触发收口扫描，断言它被转 blocked 且 blocked_question 非空（合法迁移集见 threads.py:681-738 与 PROTOCOL.md §19）；当前实现下该测试应复现「没人收口」这一缺口——写成显式记录缺口而不是假绿。
  - `server/assistant/executive.py`【红线：获批后再改】：在 reconcile_durable（:390-445）之外加一个不依赖 durable 的收口扫描：running 且 dispatcher._active_runs 无租约的单转 blocked；并把 answer / resume / reassign / REST /run 四条单趟入口改走 drive() 的重试网（:269-288）；不改 §19 合法迁移集、不新增状态。
  - `server/app.py`【红线：随放行】：把该扫描从 durable 分支里挪出来，独立 asyncio 任务周期跑（现 :1634-1637 只在 durable 启用时创建）；间隔复用 roster durable.patrol_seconds 或新增配置键（新增键要过 audit_config_keys 白名单，见 run_gates.py:87-94 注释）。
- **验收方式**：未获批时只合入测试文件并在汇报中记录缺口复现。获批后：`scripts\test_stalled_running.py`、`scripts\test_restart_recovery.py`、`scripts\test_durable_reconcile.py` 全绿，`scripts\test_core.py` 与 `scripts\test_executive_v3.py` 无回归。
- **风险**：收口会把长任务误判成停滞（真机 CLI 单次 timeout 420s，roster:98），阈值必须显著大于适配器 timeout 且只对非 durable 适配器生效；自动重驱动会与 _active_runs 租约冲突，必须先查租约再动，否则重复开工。
- **红线检查**：主体改动落在 executive.py，属 AGENTS.md 明列红线「改 executive.py 前必须先问」——未获 Role 0 放行前只合入测试文件，代码改动停工等待；app.py 的循环挪位同理随放行一起批。

### M6（P2）progress 持久化复盘与停滞检测

- **目标**：「跟踪命令进度」今天没有持久层。适配器 log 事件被 dispatcher 整体丢弃（dispatcher.py:453-482 无 log 分支），progress 的 detail 只进 WS 不落库，commission_events 只追加 opened/transition/step（threads.py:580-582,740-744），TOOL_EXECUTION 至多一次可丢（broadcast/v2.py:511）。sidecar 侧事件本就持久化（docs.ts:7 的 RunEvents doc；task.ts:11-20 按 step 幂等提交，inferred 未逐行读），/v1/runs/{id} 已回传全部 events（runtime.ts:260-266、rpc.ts:105-114）。
- **改动文件**：
  - `server/assistant/adapters/durable.py`：execute 的 /v1/events 流带上 since 游标（现 :230-232 只传 task_id；rpc.ts:189-190 已支持 since），使重连后从断点续读。适配器内部改动，不触协议。
  - `server/assistant/durable_api.py`：新增 GET /internal/durable/runs/{commission_id}，复用现成 _require_token 与 router prefix（:224），内部转调 durable 适配器的只读查询；内核被重置或归档时必须显式回「内核里没有这一单」，不能回空列表假装没进展。
  - `scripts/check_protocol.py`：INTERNAL_ROUTES（:187-193）加入该路由，保持「服务端自用路由显式纳管」的既有做法（:550-553）。
  - `server/assistant/executive_api.py`【协议门】：把 progress 查询暴露成 REST/WS 前，任何新路由或新响应字段都属 API 面，按 AGENT_PLAYBOOK 协议红线确认先输出 [PROTOCOL_REQUEST] 并停工等 Role 0。
  - `server/interaction/threads.py`【协议门】：给 commission_events 增第四种 kind 持久化 progress detail，表上 kind 是自由 TEXT（:580-582）schema 层不需迁移，但语义属 §19 台账规约，先走 [PROTOCOL_REQUEST]。
  - `server/assistant/executive.py`【协议门】：停滞检测把「既不重复也不产出」的 running 单在阈值后转 blocked；安全第一步是只报不转——信号并入既有 telemetry（telemetry.py:282-284 的 spinning_alerts 同层面），不做状态迁移；若需改 executive.py 与 M5 一并请批。
- **验收方式**：未获批部分落地后 `scripts\test_durable_adapter.py` 与 `scripts\check_protocol.py` 绿；一次「起流 → 断流 → 重连」后能从上一次 step 续读而非从 0 重放；内部只读查询能返回某单完整事件序列。获批部分：新 kind 落地后 check_protocol 仍绿且 §19 合法迁移集不变。
- **风险**：sidecar 作为唯一真相源时，内核重置/归档后复盘为空——必须显式区分「没有这一单」与「没有进展」；停滞阈值与 TTL 都是行为变更，误判会把在跑的长单打死；只报不转的版本没有这个风险，应作为默认第一步。
- **红线检查**：#1-#3 不加 WS 事件、不加公开 API 字段；#4-#6 触及 API 面与 §19 台账事件语义，按协议红线确认先输出 [PROTOCOL_REQUEST] 停工等 Role 0；#6 若需改 executive.py 与 M5 一并请批。

## 六、实施顺序与依赖

| 顺序 | 项 | 优先级 | 前置 | 门控状态 |
|---|---|---|---|---|
| 1 | M1 点亮 MCP 通路 | P0 | 无 | 无红线、无协议门，可立即开工 |
| 2 | M2 接通 Verifier | P1 | 无硬依赖（软：让 M1 的产出立刻有确定性验收兜底） | 无红线、无协议门 |
| 3 | M4 config/mcp.json 落地 + 工具目录可观测 | P1 | 依赖 M1 的 echo_server | 无红线；新路由走 sidecar 内部面，合入前向 Role 0 报备一句 |
| 4 | M3 tools/list + progress 终态 | P1 | 硬依赖 M1；软前置 M2 | dispatcher.py 改动可回退但需 review |
| 5 | M5 非 durable 收口 | P1 | 与 M1-M4 无硬依赖，可并行 | 测试文件可立即合入；executive.py / app.py 改动停工等放行 |
| 6 | M6 progress 持久化 | P2 | 无硬依赖 | since 游标 / 内部只读路由 / INTERNAL_ROUTES 三项可立即做；新 kind 与 REST/WS 暴露等 [PROTOCOL_REQUEST] |

依赖小结：M1 是 M3、M4 的前置；M2 是 M3 的软前置（先有验收网再解锁广度）；M5、M6 分别被 executive.py 红线与 PROTOCOL_REQUEST 门控，可在 M1-M4 推进期间并行准备其不越线部分。

两条被门控的项若都需改 executive.py，合并为一次请批，减少对 Role 0 的打断次数。

## 七、风险与不做的事

### 明确不做（已评估并否决）

| 不做的事 | 理由 |
|---|---|
| 把全部验收迁到 server/verify（含 rubric 与 exit_code_zero） | verify 只实现三种确定性 check（check.py:62-66），并明确不做 exit_code_zero（check.py:57）与 rubric；硬迁会在 evaluator 之外造第二份真相源，还会把语义/测试类验收推向协议门。只桥接可对应的两种，其余留 evaluator 原实现（M2）。 |
| 为 MCP 工具目录新增 WS 字段广播到前端 | 会自造 WS 事件/字段，违反 PROTOCOL.md 唯一契约红线；可观测需求改用 sidecar 内部只读路由满足（M4 的 /v1/runs/{id}/tools、M6 的 /internal/durable/runs/{id}），不发明前端字段。 |
| 按 status 改 web/events-v2.js 的归约逻辑 | 现有 applyTool 已把 TOOL_EXECUTION 当追加流水消费（events-v2.js:229-239），终态行不破坏归约（测试已出现 status:"ok"，events-v2.test.mjs:328）；改前端需前后端再对齐，超出范围，仅确认并留注释（M3）。 |
| 未获批前直接给 commission_events 加新 kind / 建旁路表 / 上 blocked TTL 自动迁移 | 触及 §19 台账事件语义与自动状态迁移，须先输出 [PROTOCOL_REQUEST] 停工等 Role 0；未清红线前实现即越线。改用 M6 的 sidecar 只读路径替代。 |
| 未获批前改 server/assistant/executive.py 与 server/app.py 的巡检循环 | AGENTS.md 明列红线；未放行前只合入 scripts/test_stalled_running.py 复现缺口（M5）。 |

### 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| MCP 协议版本代差：适配器 pin 2024-11-05（mcp.py:23），现代 server 已移除 initialize 握手（外部研究） | 真连外部 server 时握手失败 | mock server 按旧版实现；版本协商列为后续 effort，不混入本次 |
| 停滞误判：真机 CLI 单次 timeout 420s（roster:98） | 收口把在跑长单打死 | 阈值显著大于适配器 timeout，且只对非 durable 适配器生效；第一步只报不转（M6） |
| 自动重驱动与租约冲突 | 重复开工 | 先查 _active_runs 租约再动（dispatcher.py:391-419,547-556） |
| 新门禁不接进 run_gates | 新测试变成死接线 | 每次新增测试文件必须同步 GATES 与 ALWAYS_REQUIRED（run_gates.py:87-94 的事故模式注释） |
| 两边 SIDECAR_ROUTES 不一致 | 运行时 404，静态检查才报警 | 改一边必改另一边，靠 check_protocol.py:396-401 兜底 |
| sidecar 内核重置/归档后复盘为空 | 把「没有这一单」误读成「没有进展」 | 内部只读查询必须显式区分两者（M6） |

### 不可违反的红线（本方案全量自检）

不自造 WS 事件 / API 字段（PROTOCOL.md 是唯一契约，新增字段须走 [PROTOCOL_REQUEST]）；不改 server/assistant/executive.py（M5/M6 涉及部分停工等 Role 0）；不加调色板颜色；不重启 8000/8010；快环内不出现阻塞调用（派单走 spawn 后台任务，app.py:833）。

## 八、验证方式：改完后跑哪些命令

黄金命令（AGENTS.md 规定，改完必须全绿）：

| 命令 | 覆盖 | 对应 effort |
|---|---|---|
| `.venv\Scripts\python.exe scripts\check_protocol.py` | 协议静态检查：SIDECAR_ROUTES 两边一致（:396-401）、INTERNAL_ROUTES 纳管（:550-553）、无自造字段 | M3 / M4 / M6 |
| `.venv\Scripts\python.exe scripts\test_core.py` | 离线核心用例，无回归 | 全部 |
| `npm test` | 前端与 Node 侧用例，含 web/events-v2.test.mjs | M3 / M4 |
| `.venv\Scripts\python.exe scripts\run_evals.py` | 离线评测层 | 全部 |

分 effort 的聚焦验证：

| effort | 命令 |
|---|---|
| M1 | `.venv\Scripts\python.exe scripts\test_mcp_adapter.py`；`.venv\Scripts\python.exe scripts\doctor_integration.py`（不报 executable 不存在）；`.venv\Scripts\python.exe scripts\run_gates.py --only protocol,core,evals,npm,adapter_ext,mcp_adapter` |
| M2 | `.venv\Scripts\python.exe scripts\test_verify.py`；`.venv\Scripts\python.exe scripts\test_executive_v3.py`；`.venv\Scripts\python.exe scripts\check_protocol.py` |
| M3 | `.venv\Scripts\python.exe scripts\test_mcp_adapter.py`；`node web\events-v2.test.mjs`；`.venv\Scripts\python.exe scripts\test_v2_broadcast.py`；`.venv\Scripts\python.exe scripts\check_protocol.py` |
| M4 | `node --experimental-strip-types agent-runtime/test-mcp.ts`（cwd=agent-runtime）；`node --experimental-strip-types agent-runtime/test-profiles.ts`；`.venv\Scripts\python.exe scripts\check_protocol.py` |
| M5 | `.venv\Scripts\python.exe scripts\test_stalled_running.py`；获批后加跑 `scripts\test_restart_recovery.py`、`scripts\test_durable_reconcile.py`、`scripts\test_core.py`、`scripts\test_executive_v3.py` |
| M6 | `.venv\Scripts\python.exe scripts\test_durable_adapter.py`；`.venv\Scripts\python.exe scripts\check_protocol.py`；一次「起流→断流→重连」的续读断言 |

