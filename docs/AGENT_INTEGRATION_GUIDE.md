# Agent / 工具接入指南

面向要往小溪里加 Worker、Agent 或工具的人。先判断“对方是什么形态”，再选连接方式；不要为每一种 Agent 改调度核心。

## 1. 先选形态

| 对方是什么 | 用什么接 | 配置位置 | 不适合 |
|---|---|---|---|
| 本地 CLI Agent，自带 Read/Bash/Grep（DSH、Claude Code、Codex、WorkBuddy） | `cli_subproc`；需要崩溃续跑时用 `durable` 接管 | `config/agents_roster.yaml` 的 `adapter/executable/args/env` | 只提供几个工具函数的服务 |
| 远端 HTTP Agent，返回 JSON 或 SSE 事件 | `http_api` | roster `endpoint/method/headers` | 没有事件/artifact 契约的普通 API |
| MCP Server，整个 server 就是干一件活 | `mcp` 适配器 | roster `executable/args/tool` | 想把 server 的多个工具分别挂给模型 |
| MCP Server，想把多个工具挂给模型 | `config/mcp.json` + `durable` 模型 Worker | `mcpServers` 的 `command/args/env` 或 `url/headers` | 不要写进 roster 当 Worker |
| 自带模型、想用我们的工具/技能 | `durable`（不配 executable） | roster `adapter: durable` + `config/skills` / `config/mcp.json` | 需要真机 Bash/Grep 的 coding 任务 |
| 只能人工处理 | `manual` | roster `adapter: manual` | 期望自动执行 |

判断口诀：

1. **它能自己干活、带工具吗？** 能 → CLI/durable 接管，别重写它的工具。
2. **它只暴露工具函数吗？** 是 → MCP 工具或 `http_api`，不要当 Worker。
3. **需要崩溃续跑/长任务吗？** 是 → `durable`；CLI 用 durable 接管。
4. **需要隔离写盘吗？** durable 配 `workspace_root`；CLI shim 配 `AQI_WORKSPACE_ROOT`；MCP server 配 `cwd`。
5. **需要审批吗？** 写工具列 `durable.approval_tools`；MCP 写工具自动进审批；shell Worker 用 confirm lease 每单确认。

## 2. 工作区

生产默认在 roster 的 `AQI_WORKSPACE_ROOT`（当前 `data/commissions`）下，每个 Commission 一棵树：

```text
data/commissions/<commission_id>-<YYYYMMDD-HHMMSS>/
└── out/                 # Worker 的交付物
```

- durable 适配器用 `workspace_root + commission_id + 时间戳` 建目录，见 `server/assistant/adapters/durable.py` 的 `_workspace`。
- sidecar 把目录写进 `RunMeta.workspace`；`write_file/read_file/list_files` 只能在这个目录内。
- 接管 CLI 时，`takeover.ts` 注入 `AQI_WORKDIR` / `AQI_OUTDIR`；shim 的 `resolve_workdir()` 解析。
- `cli_subproc` 的 shim 用 `AQI_WORKSPACE_ROOT + AQI_COMMISSION_ID` 分配。
- 探针/测试可以显式覆盖 workspace_root 到 `tmp/` 或系统临时目录，不要写真实 `data/commissions`。

## 3. 加一个 CLI Worker

```yaml
- id: my-cli
  name: 我的 CLI
  adapter: cli_subproc
  executable: "python"
  args: ["scripts/worker_shims/my_worker.py"]
  timeout: 420
  env:
    AQI_WORKSPACE_ROOT: "data/commissions"
  summary: 一句话说明它适合干什么
  strengths: [关键词1, 关键词2]
  weaknesses: [不适合什么]
  cost_tier: medium
  max_concurrent: 1
  eval_commands: [required_fields, artifacts_exist, token_budget, acceptance_met]
```

shim 必须在 stdout 上说 PROTOCOL §22 的五种事件（progress/token/artifact/log/error），最终交 `artifact`。参考 `scripts/worker_shims/dsh_worker.py`。

## 4. 加 durable 接管

同一个 CLI 条目，把 `adapter` 改成 `durable`，保留 `executable/args/env`；运行时由 `bind_durable` 注入 sidecar 端点和 token，sidecar 用 `env.exec` 拉起它。

```yaml
adapter: durable
# executable/args/env 原样保留
workspace_root: data/commissions
```

这样拿到的是：真实 CLI 的 Read/Bash/Grep + durable 的 checkpoint/续跑/事件流。不要同时写一个“用模型重写 CLI 工具”的 Worker。

## 5. 加 HTTP Agent

```yaml
adapter: http_api
endpoint: "http://127.0.0.1:9000/v1/run"
method: POST
headers:
  authorization: "Bearer ..."
```

响应是 JSON 时按一次性产出；`text/event-stream` 时按 SSE 事件解析。最终必须有 `{"type":"artifact","artifact":{...}}`。

## 6. 加 MCP

### 6.1 整个 MCP Server 当一个 Worker

```yaml
adapter: mcp
executable: "npx"
args: ["-y", "@modelcontextprotocol/server-filesystem", "."]
tool: run_commission
```

适配器只做 initialize → tools/list → tools/call；结果必须放在 text content 的 `{"type":"artifact",...}` 块里。

### 6.2 把 MCP 工具挂给模型

写 `config/mcp.json`，不要写 roster：

```json
{
  "mcpServers": {
    "docs": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "env": { "DOCS_TOKEN": "${DOCS_TOKEN}" },
      "approval": "mutating"
    }
  }
}
```

- 工具名：`mcp__<server>__<tool>`。
- 只读注解（`readOnlyHint: true`）才 `replay:"safe"`；有副作用工具自动进审批。
- server 只拿到最小环境变量，不继承 sidecar 的 LLM key。
- 工具 schema 会进每轮上下文；工具很多时改用 Codemode 或先 deferred（后续 C4）。

## 7. 发现、派单与审批

- orchestrator 用 `list_workers` / `worker_status` 看花名册、能力、成本档、当前负载；`available` 是并发容量，不是健康检查。
- 派单用 `dispatch_task`，agent_id 必须在 lease 的 `allowed_agents` 内；默认只允许 scribe。
- `run_worker` 会按模型给的命令行拉起本地执行体，默认在审批表里；不要把它当普通工具放开。
- `approval_tools` 只列额外内置工具；MCP 写工具自动加。确认单走 action hash + CAS，模型不能代答。
- shell/high 档 Worker 需要每单确认；当前 C2 首期只开 scribe，DSH 的 confirm 探针属于测试配置。

## 8. 接入后怎么验证

```powershell
python scripts/doctor_integration.py --worker my-cli
node agent-runtime/test-mcp-live.ts
python scripts/test_durable_adapter.py
python scripts/check_protocol.py
```

- `doctor_integration.py` 检查 adapter 是否登记、executable/args/workspace/MCP 配置是否齐全。
- `test-mcp-live.ts` 用本地 stdio fixture 验证真实 MCP 握手与 tools/call。
- 真机 Worker 先用 `scripts/probe_dsh_workspace.py` 或 `probe_c3_live.py` 在临时工作区跑，不要直接写生产台账。

## 9. 安全边界

- Python 是唯一台账写者；Worker 只通过适配器事件回传。
- 工具写操作必须在沙箱内；`workspace_root` 留空意味着不允许写文件。
- MCP server 的 command/env 是本地代码执行面，接入前审查来源。
- 不要把 host token、LLM key 或确认单放进模型可见的 prompt。

## 10. 像素世界（S5）

- 世界只是台账的只读投影，不是第二个台账；不新增 WS 事件或 REST 字段。
- 服务端工具：`world.dispatch(title, brief)`（mutation，过确认门）与 `world.status(commission_id)`（只读）。
- 派单仍走 `begin_commission → 台账 → commission_update`；世界不能调用 `/accept`、`/submit`，也不能自己标完成。
- 工具只在老板明确提到“像素世界/世界里/小镇/pixel world”时使用；普通派遣仍走既有快派路径。
- 详细 DTO、已知限制和验收矩阵见 `docs/PIXEL_WORLD_S5_CONTRACT.md`。
