# 网关层设计 — 对照 Magpie 的本地 Worker 网关

日期：2026-10-17。本文设计小溪的 Worker 网关层，参考 [yetone/magpie](https://github.com/yetone/magpie)
的公开 README 与 [workbuddy-to-dsh](https://github.com/Ianzhyh/workbuddy-to-dsh) 的公开说明。
**本轮没有安装 Magpie、没有改 `15721` 的配置。** Magpie 的能力描述来自其 README，不等同于本机实测。

## 0. 实施状态（2026-10-17 22:50 追加）

方案 C 已落地并跑通，不再是纸面设计：

| 项 | 结果 |
|---|---|
| 代码 | `server/gateway/anthropic_proxy.py`，监听 `127.0.0.1:15722` |
| 协议 | Anthropic Messages，流式 + 非流式；文本、tool_use、tool_result |
| 上游 | `127.0.0.1:15721/v1/chat/completions`，模型 `step-5-preview` |
| 工具 | 26 个 Claude Code 工具定义已能翻译；Claude 实际调用 `Write` / `Bash` 写出文件 |
| 文本验收 | `claude -p "Reply with exactly: OK"` → **OK**，退出码 0 |
| 工具验收 | `claude -p "用 Write 创建 note.txt"` → 文件存在，内容 `OK` |
| 真机 commission | `probe_claude_worker.py` → `ready_to_report`，36,284 tokens，17.5s |
| 生产花名册 | `probe_roster_claude.py` → 选中 `claude`，`ready_to_report`，74,269 tokens，25.8s |
| 自动启动 | `scripts/run.py` 在 8000 主服务前拉起 15722 网关；退出时只关自己拉起的那个 |
| 回归 | `test_anthropic_gateway.py` 30/30；`test_claude_worker_shim.py` 16/16 |

限制仍在：图片、thinking、prompt caching、Anthropic beta 头、多账号/配额都不翻译；
`MAX_OUTPUT` 默认 16k；Claude Code 会把响应模型当成 `claude-opus-5-5` 估算成本，
显示的美元数不是真实上游价格。生产 Worker 仍是 `--dangerously-skip-permissions`，
文件权限隔离没有在这一层解决。

## 0.1 实现状态（2026-10-17 夜）

`15722` 从 Anthropic 单协议翻译层扩成统一 loopback 网关。仍然只绑 `127.0.0.1`，
不安装 Magpie，不做调度，不碰 `15721`。

| 项 | 状态 |
|---|---|
| Anthropic Messages | 保留 `/v1/messages` 与 `/v1/messages/count_tokens`，30 项旧契约不变 |
| OpenAI Chat | 新增 `/v1/chat/completions`：按路由表改写 `model` 后转发，流式按原字节透传 |
| OpenAI Responses | 新增 `/v1/responses`：透明反代到 `AQI_GATEWAY_RESPONSES_UPSTREAM`，模型与字节都不改 |
| 模型路由 | 新增：`AQI_GATEWAY_ROUTES` 精确表 > `flash/haiku/lite` 关键词 > 主模型；回给客户端的是它请求的模型名 |
| 上游降级 | 新增 `AQI_GATEWAY_FALLBACK_UPSTREAM`；只在连接错误与 5xx 时切，4xx 不切；`x-aqi-upstream` 头标明实际服务者 |
| 观测 | 新增脱敏请求日志：方法、路径、客户端、请求模型、上游模型、上游、状态、耗时、可用时的 token；不记 header、prompt、响应正文 |
| `/v1/models` | 新增：列出本网关服务的模型与上游映射；上游不可达时仍返回本地清单，不报错 |
| `/health` | 扩展：upstream、fallback、routes、协议矩阵 |
| 回归 | `test_anthropic_gateway.py` 72/72（30 旧 + 42 新）；`test_claude_worker_shim.py` 16/16 |

仍未实现（本轮明确不做）：

- 图片、thinking、prompt caching、Anthropic beta 头仍不翻译。
- 无 per-agent 配额、无多账号轮换、无成本计费。
- 不改写任何 Agent 自己的配置文件（Magpie 的迁移式接线没做）。
- 网关不直接写 `agent_telemetry_spans`；span 仍由 Worker shim 上报，单写者规则未变。
- Responses 入站不做模型路由：Codex 的 provider 私有模型名只由 `15721` 认识。

## 1. 结论先行

1. **不把 Magpie 装进本轮依赖。** 它是成熟的参考实现和 fallback 基准，但引入一个外部 Go 网关会改变
   部署、配置所有权和遥测落库路径，收益不足以支撑“让 Claude Code 能当 worker”这一个缺口。
2. **当前已实现最小翻译层（方案 C）。** 保留 CC Switch 的 `15721` 给 Codex，另起
   `127.0.0.1:15722` 做 Anthropic Messages → OpenAI Chat 的本地翻译，支持文本与工具调用。
   它只解决“协议不通”，不假装已经有多 Agent 路由。
3. **目标态是方案 B：小溪自己的薄网关。** 只有当出现第二个协议、第二个 Provider 或需要按
   Agent/任务路由与统一计费时，才在 `server/gateway/` 里实现；协议翻译先做文本流，再做工具调用。
4. **Magpie 是方案 A 的 benchmark，不是依赖。** 如果以后要“一台网关喂多个 Agent + 配额/成本 +
   Agent 配置重写”，先按本文的验收矩阵对 Magpie 做一次本机对照，再决定是否替换方案 B。

一句话：**先补协议缺口，再谈路由；先让一单真实任务闭环，再谈多 Agent 平台。**

## 2. Magpie 值得借鉴什么

Magpie README 的公开事实（未本机验证）：

| 能力 | 事实 | 对小溪的意义 |
|---|---|---|
| 单点网关 | `127.0.0.1:3425`，一个本地端口服务所有 Agent | 与“一个调度器喂多个 Worker”同构 |
| 四协议 | OpenAI Chat、OpenAI Responses、Anthropic Messages、Gemini，互相翻译 | Codex 要 Responses，Claude Code 要 Anthropic；这正是当前缺口 |
| 流式/工具/推理 | README 明说 streaming、tool calls、reasoning 一起翻译 | 不是简单改 URL，难点在事件流与工具调用 |
| 路由组 | `smart/order/rotate/usage/pace`，配额耗尽自动换账号/模型 | 多 Provider、多账号时的稳定性来源 |
| Agent 配置重写 | 原子改写每个 Agent 自己的配置，保留注释/顺序/格式 | 减少手工改 `settings.json` / `config.toml` |
| 可观测 | token、成本、缓存、上下文、会话、OTLP 导出 | 对应 `agent_telemetry_spans` 的上游 |
| 插件/中间件 | `onRequest/onEvent/onResponse`，模型映射、系统提示、敏感词 | 可在网关层做预算/脱敏，而不是改每个 Worker |
| 覆盖范围 | 明确列出 WorkBuddy、DeepSeek Harness、Claude Code、Codex | 说明“GUI Agent 也能被本地网关接出来” |
| 许可 | MIT | 可参考，但仍是外部运行时 |

Magpie 的路线是“**以 Agent 配置为中心**”：它替你改每个 Agent 的配置，让它们都指向一个网关。
小溪的路线是“**以 Commission 为中心**”：调度器决定谁干活、干多久、交什么。两者不冲突，
但控制面不同。直接装 Magpie 会让“谁拥有 Agent 配置”和“谁写遥测”出现双主。

## 3. 现状与差距

| 项 | 现状 | 差距 |
|---|---|---|
| OpenAI Responses | `15721` 可用；Codex 用 `wire_api="responses"` + `PROXY_MANAGED` | 只覆盖 Codex |
| Anthropic Messages | `15722` 已可用（文本 + 工具，流式/非流式）；`15721` 仍返回 400 | Claude Code 已能当 Worker |
| 模型 | `15722` 统一映射到 `step-5-preview` | 无按 Agent/任务路由 |
| 遥测 | `agent_telemetry_spans` 由 dispatcher 落库 | 网关不直接写 span；Claude shim 从 `result.usage` 上报，Codex 从 `turn.completed` 上报 |
| 预算 | 生产花名册 80k；`token_budget` 已列入 `claude` 的 `eval_commands` | 真实 CLI 的固定开销已计入；仍非按 Agent 精细预算 |
| 凭据 | `15722` 用本地占位 token `local`；不落盘到 Packet | 无网关级 key、无 per-agent 限额 |
| WorkBuddy | Electron，无 CLI | 需要 `workbuddy-to-dsh` 这类桥，或 GUI 自动化；本轮不做 |
| 网络 | 只监听本机 | Magpie 的 LAN 共享/远程模式明确不在范围内 |

## 4. 需求

- **R1 单点**：所有 Worker 只记一个 loopback endpoint，不各自散落 Provider 配置。
- **R2 协议**：至少 OpenAI Responses（Codex）与 Anthropic Messages（Claude Code）；Chat Completions 作为通用兜底。
- **R3 路由**：能按 `agent_id` / 任务类型 / 成本档选模型；上游失败能降级，但不得把失败伪装成成功。
- **R4 遥测**：每次上游调用落一条 span，字段与 `agent_telemetry_spans` 一致；**同一次调用只记一次**。
- **R5 凭据**：凭据不进入 Briefing Packet、不进日志、不写进 `result.json`；日志脱敏。
- **R6 边界**：默认只绑 `127.0.0.1`；不做 LAN 共享；网关不获得比 Worker 更大的文件权限。
- **R7 失败语义**：上游 4xx/5xx、超时、流中断都要映射成适配器 `error` 事件；`artifact` 之后不得再报成功。
- **R8 兼容**：不新增 WS 事件/API 字段；继续走 `cli_subproc` 的五行协议与 `POST /submit`。

## 5. 方案对比

### 方案 A：直接采用 Magpie

**做法**：安装 Magpie，Provider 指向现有 StepFun/CC Switch，Codex/Claude Code 配置改到 `3425`。

- 优点：四协议、路由、配额、成本、配置重写都是现成的；WorkBuddy/DSH 也在支持列表里。
- 代价：多一个外部运行时与升级面；遥测要另接 OTLP 或插件，不能直接写 `agent_telemetry_spans`；
  Agent 配置所有权从项目转移到 Magpie；隐私/账号发现需要额外关闭项。
- 适用：已经确定要长期维护多 Agent、多 Provider、多账号，且接受外部依赖。

### 方案 B：小溪自己的薄网关（目标态）

**做法**：`server/gateway/` 作为独立 loopback 进程（或 app 内挂载），暴露
`/v1/responses`、`/v1/messages`、`/v1/chat/completions`；内部做协议翻译、模型路由、span 落库。

- 优点：遥测直接写现有 SQLite；预算/脱敏/路由与 Commission 绑定；不引入新运行时。
- 代价：流式与工具调用翻译是硬活；需要自己维护协议差异与回归；容易变成“第二个调度器”。
- 适用：第二个协议/Provider 已成为常态，且需要按 Commission 统一计费与预算。

### 方案 C：最小 Anthropic 翻译层（现在）

**做法**：保留 `15721` 给 Codex；新增一个只监听 loopback 的小端点，把 Anthropic Messages 请求
翻译成 OpenAI 兼容请求（文本 + SSE 流），把响应翻译回 Anthropic 事件。先不做工具调用、不做多账号。

- 优点：最小改动让 Claude Code 的 `-p` 可用；不动 Codex 链路；失败边界清晰。
- 代价：只解决一个协议；工具调用/长上下文/多模态要后续补；仍是第二个代理进程。
- 适用：当前“让 Claude Code 能当 worker”的单一缺口。

### 方案 D：WorkBuddy 桥

`workbuddy-to-dsh` 这类项目把 WorkBuddy 已登录模型暴露成本地 OpenAI/Anthropic 接口。
本轮不采用：WorkBuddy 没有 CLI，桥接的稳定性、许可与账号条款都未验证；先把它列为候选，
等真的需要第三个 Worker 时再做。

## 6. 推荐路线

```text
现在：Codex ──> 15721 (CC Switch, Responses) ──> StepFun
      Claude ─> 15722 (Anthropic→OpenAI 最小翻译) ──> 15721 / 直接 Provider

目标：Codex / Claude / 其他 ──> 15722 (小溪薄网关：协议翻译 + 路由 + span) ──> Providers
```

**落地的顺序：**

1. **先取证**：把 `/v1/messages` 的 400 与 `claude -p` 的 400 固化成可复现命令（已完成，见 Recon）。
2. **再补洞**：方案 C 先做文本流，再补工具调用；验收是 `claude -p "Reply OK"` 返回 OK、
   Claude 能用 `Write`/`Bash` 写出真实文件，且 Codex 探针不回归（已完成）。
3. **最后才路由**：当出现第二个 Provider 或需要按 Agent 预算时，才把方案 C 扩成方案 B；
   Magpie 作为对照实现，先跑同一组验收再决定是否采用（未做）。

## 7. 协议矩阵（已实现的部分）

| 客户端 | 入站 | 出站 | 已实现 | 后续 |
|---|---|---|---|---|
| Codex | OpenAI Responses | Provider Responses | 透传（走 15721） | 推理档位、路由 |
| Claude Code | Anthropic Messages | OpenAI Chat | **文本 + SSE + tool_use/tool_result** | thinking、图片、caching |
| 通用 Worker | OpenAI Chat | Provider Chat | 文本 | 多模态 |

关键映射：

- Anthropic `system`、`messages[].content`（text/tool_use/tool_result）→ OpenAI `messages`（含 `tool_calls` / `role:tool`）。
- Anthropic `tools[].input_schema` → OpenAI `tools[].function.parameters`；`tool_choice` 的 auto/any/tool/none 已映射。
- Anthropic SSE `text_delta` / `input_json_delta` ← OpenAI `delta.content` / `delta.tool_calls[].function.arguments`。
- 结束事件保留 `stop_reason`：`tool_calls` → `tool_use`，`length` → `max_tokens`，`stop` → `end_turn`。
- 错误：上游 4xx/5xx 保留状态码，正文截断后作为 `error` 返回；不得重试成“成功”。
- 第一版**不翻译** thinking / image / cache_control / Anthropic beta；这些内容被丢弃或扁平化，
  不假装支持。

## 8. 遥测与预算

- **当前实现**：网关不直接写 `agent_telemetry_spans`；Claude shim 从 Claude Code 的
  `result.usage` 发一条 `token` 事件，dispatcher 落 span。Claude 的 `input_tokens` 由网关在
  `message_start` 里回填上游首个 chunk 的 `prompt_tokens`，所以 74,269 这类数字是上游真实读数，
  不是 0，也不是按字符估算。
- **目标态**：网关每次上游调用记一条：`commission_id, agent_id, step_idx, model,
  prompt_tokens, completion_tokens, ttft_ms, duration_ms, status_code, error_snippet`。
  `ttft_ms` 取首个响应字节/首个文本增量；非流式时与 `duration_ms` 相等（不编造 0）。
- **单写者规则**：同一条调用只能有一个 span 来源。当前是 Worker（shim）写；若将来网关直接写，
  `cli_subproc` 注入的 Worker 不得再发 `token`。用 `AQI_TELEMETRY_OWNER=gateway|worker` 明确。
- 预算检查读真实 span，而不是 Worker 自报；`audit.usage.budget_checked` 必须能区分
  “查过且通过”“查过且超”“没有 span 未查”。生产花名册的 `max_prompt_tokens` 已从 12k
  调到 80k，反映 CLI Agent 的固定开销。
- 当前发现的坑：`cli_subproc` 会注入 `AQI_TELEMETRY_PROXY` / `OPENAI_BASE_URL`，但 Codex shim
  用的是 `AQI_GATEWAY_URL`，两者没接上；方案 C/B 落地时必须统一，否则“代理记了、shim 又记了”
  或“两边都没记”。

## 9. 安全边界

- 只绑 `127.0.0.1`；不默认开 LAN 共享；不做远程 Magpie 模式。
- 网关 token 用一次性/环境注入，不落盘到 Briefing Packet、`result.json`、日志。
- 日志脱敏沿用 `telemetry.py` 的 `api_key/token/password/secret/authorization/cookie` 规则。
- 网关只转发模型请求，不扩大 Worker 的文件权限；Worker 的隔离仍是独立问题
  （`danger-full-access` 只允许探针临时目录）。
- 任何“Claude Code 可用”的结论必须附 `/v1/messages` 200 与 `claude -p` 成功输出。

## 10. 验收

```powershell
# 0. 起网关（或直接 python scripts/run.py，它会自动拉起）
.\.venv\Scripts\python.exe -m uvicorn server.gateway.anthropic_proxy:app `
  --host 127.0.0.1 --port 15722

# 1. 离线协议契约
.\.venv\Scripts\python.exe scripts\test_anthropic_gateway.py
.\.venv\Scripts\python.exe scripts\test_claude_worker_shim.py

# 2. Anthropic 侧真实调用
curl.exe -s -o NUL -w "%{http_code}" -X POST http://127.0.0.1:15722/v1/messages `
  -H "content-type: application/json" -H "x-api-key: local" `
  -d '{"model":"claude-opus-5-5","max_tokens":32,"messages":[{"role":"user","content":"hi"}]}'
# 期望 200

$env:CLAUDE_CONFIG_DIR="$PWD\tmp\claude-config"
$env:ANTHROPIC_BASE_URL="http://127.0.0.1:15722"
$env:ANTHROPIC_AUTH_TOKEN="local"
claude -p "Reply with exactly: OK" --output-format text
# 期望 OK，退出码 0

# 3. 真机 commission（会花 token）
.\.venv\Scripts\python.exe scripts\probe_claude_worker.py
.\.venv\Scripts\python.exe scripts\probe_roster_claude.py
# 期望两者都 VERIFIED，且 budget_checked=true

# 4. Codex 侧不回归
.\.venv\Scripts\python.exe scripts\probe_real_worker.py
```

## 11. 明确不做

- 不安装 Magpie、不改 CC Switch、不开 LAN/远程网关。
- 不实现多账号/配额轮换/成本计费。
- 不做 WorkBuddy 桥、不做 Gemini、不做多模态。
- 不把网关当成 Worker 隔离方案；文件权限问题不在这一层解决。
- 不因为“Magpie 支持”就声称本项目已支持。

## 12. 落地记录（2026-10-17 22:50 更新）

方案 C 已实现并验证。没有安装 Magpie，没有改 `15721`。

- 进程：`server/gateway/anthropic_proxy.py`，监听 `127.0.0.1:15722`，与主应用分离；
  `scripts/run.py` 会自动拉起并只关闭自己拉起的那个。
- 非流式：`POST /v1/messages` 返回 200，`content[0]` 为 text 或 tool_use。
- 流式：`message_start`、`content_block_start/delta/stop`、`message_delta`、`message_stop`
  已实现；文本与 `input_json_delta` 都走同一套 translator。
- 工具：Anthropic `tools` → OpenAI `functions`；`tool_use` / `tool_result` 双向映射；
  Claude 实际调用 `Write` 与 `Bash` 写出 `out/note.txt`。
- Claude Code：隔离 `CLAUDE_CONFIG_DIR` 后，`claude -p 'Reply with exactly: OK'` 退出码 0，输出 `OK`。
- 真机派单：`probe_claude_worker.py` 36,284 tokens / 17.5s；`probe_roster_claude.py`
  74,269 tokens / 25.8s，均 `ready_to_report` 且 `budget_checked=true`。
- 隔离：探针与生产 Worker 都写独立工作目录；没有改 `~/.claude/settings.json`。
- 已知限制：图片/thinking/caching/beta 不翻译；输出上限 16k；Claude 按 `claude-opus-5-5`
  估算成本，显示的美元数不是上游真实价格；`--dangerously-skip-permissions` 仍是最大风险。


未做：把 15722 纳入生产启动、把 Claude 接进花名册、span 落库、工具调用翻译。

