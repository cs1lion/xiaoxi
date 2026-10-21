# agent-runtime

慢环的执行内核。一个长驻 Node 进程持有唯一的 pi-durable Harness 与 SQLite
存储；Python 通过内部 RPC 递派单密件、听标准事件。它不面向浏览器，也不改
`docs/PROTOCOL.md`：Python 仍然是唯一说话者。

设计依据：`docs/PI_DURABLE_MIGRATION.md`（§4.2 进程与所有权、§4.3 内部 RPC）。
接入其他 Agent/工具先看 `docs/AGENT_INTEGRATION_GUIDE.md`；`scripts/doctor_integration.py`
可在不联网的情况下检查花名册与 MCP 配置。

## 现状

Phase 0/1/2 的执行内核都在，且已经接上真实模型与本地 CLI 接管：

- 任务定义 `xiaoxi.run`，模型路径与本地执行体接管路径二选一。
- `POST /v1/runs` 按 `commission_id@attempt` 幂等：重投同一单返回同一任务，
  初审打回后的重做拿新任务并真的再拉一次执行体。
- `GET /v1/events` 用 SSE 推标准事件，`POST /v1/runs/{id}/cancel` 取消。
- 审批：`beforeTool` 拦下 → 待批准清单 → Python 按单放行（`/v1/approvals`）。
- 上下文：密件、记忆块、验收规则与上一轮批注写进 `instructions`（compaction
  不摘要 section）；压缩阈值由 `models.ts` 按窗口推导，见下。
- Skills：扫描 `XIAOXI_SKILLS_DIR`（默认 `config/skills`），只广告
  name/description，正文用 `read_skill` 按需读取。
- 存储：`scripts/storage_doctor.py` 只读报告体积；`scripts/archive_durable.py
  --rotate --yes` 只有拿到 sidecar 目录锁才归档并换新库。
- MCP：读取 `XIAOXI_MCP_CONFIG`（默认 `config/mcp.json`），把 server 的
  tools 包成 durable 原生工具，命名 `mcp__<server>__<tool>`；只读工具可安全
  重放，有副作用的工具自动进入审批。
- C1 只读参谋：`search_memory` / `search_notes` / `read_note` / `read_thread` /
  `list_workers` / `worker_status` / `list_files`。前六个走宿主
  `XIAOXI_HOST_URL` + token 的 `/internal/durable/*`，最后一个只读本单沙箱；
  都不写台账、不授予权限。
- C3 验收分层：orchestrator 只有 `review_delivery` / `propose_accept` /
  `propose_reject` 三个只读工具；没有 accept/reject commit 工具。验收由宿主
  通过 `/internal/durable/authorize` 一次性授权后，再调
  `/internal/durable/accept` 或 `/reject`。模型不能把单直接推到 `done`。
- C2 编排者 profile：只有 `orchestrator` 对话能拿到
  `dispatch_task` / `task_status` / `task_events` / `answer_blocked`；Worker
  默认没有这些工具。lease 由宿主按会话签发，模型不能指定或伪造额度；首期
  只允许 scribe，默认 dry-run，1 并发、3 单/小时、TTL 30 分钟。confirm 模式
  会返回一次性 `confirmation_id`，由宿主 `/internal/durable/confirm` 批准，
  绑定原 title/brief 的 action hash；`blocked_kind=approval` 的问题模型不能
  代答。`dispatch_task` 会自己等待宿主确认（最长 120s），批准后自动带同一
  confirmation_id 重试，模型不承担重试协议。

监督器在 Python 侧：`server/assistant/durable_supervisor.py`（起/探活/收尸）。
生产接线由 `server/app.py` 的 `_start_durable_if_enabled()` 完成；YAML 默认关闭，
`.env` 里 `XIAOXI_DURABLE_ENABLED=1` 点亮，`XIAOXI_DURABLE_SWITCH_AGENTS=scribe`
或花名册的 `switch_agents` 决定切哪些 Worker。

## 跑起来

```bash
npm install --ignore-scripts          # 精确钉版本，Node >= 22.19
node --experimental-strip-types src/main.ts   # stdout 首行回一句 PORT <n>
```

环境变量：

| 变量 | 作用 |
|---|---|
| `XIAOXI_RUNTIME_DIR` | sidecar 的 SQLite/日志目录，默认 `data/durable` |
| `XIAOXI_RUNTIME_TOKEN` | 设了就要求 `Authorization: Bearer <token>` |
| `XIAOXI_SKILLS_DIR` | 产品技能目录；多个用系统路径分隔符。默认 `config/skills` |
| `XIAOXI_MCP_CONFIG` | MCP server JSON，默认 `config/mcp.json` |
| `XIAOXI_CONTEXT_WINDOW` | 真实模型的上下文窗口，默认 `32768` |
| `XIAOXI_MAX_TOKENS` | 单次回答预算，默认 `8000` |
| `XIAOXI_RESERVE_TOKENS` | 压缩时为回答预留的 token；默认按答案预算推导 |
| `XIAOXI_KEEP_RECENT_TOKENS` | 压缩后保留的近期上下文；默认按窗口推导 |
| `XIAOXI_BACKGROUND_TOKENS` | 后台压缩提前量；默认 `4096` |
| `XIAOXI_HOST_URL` | 宿主只读/编排接口地址；app 启动时自动注入 |
| `XIAOXI_HOST_TOKEN` | 宿主接口令牌；app 启动时生成，不落盘 |

`GET /v1/stats`（Bearer token）返回 sidecar 的 RSS/heap/handles/uptime，供 soak
采样；`scripts/soak_durable.py` 是独立挂机的 chaos/soak harness。
| `XIAOXI_DURABLE_LEASE_MODE` | C2 lease 模式：`dry_run`（默认）/`confirm`/`auto` |

压缩参数必须满足 `contextWindow - reserveTokens > keepRecentTokens`，且
`reserveTokens >= maxTokens`；不满足时 sidecar 启动即报错，不会带着一个会
反复溢出的配置继续跑。

### Skills

每个技能一个目录，内含 `SKILL.md`，frontmatter 至少写 `name` 与
`description`（Agent Skills 规范）。`description` 决定模型什么时候加载它；
正文只在调用 `read_skill` 时进入上下文。`disable-model-invocation: true`
的技能不会出现在模型列表里。支持 `references/` 等目录内的文本文件。

```text
config/skills/
└── title-polish/
    ├── SKILL.md
    └── references/style.md
```

不要把 `XIAOXI_SKILLS_DIR` 指向 `.agents/skills`：那是开发工具的技能目录，
会漏进生产 Worker 的 prompt。

### MCP

`config/mcp.json` 使用常见的 `mcpServers` 形状：

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

支持 stdio（`command`/`args`/`cwd`/`env`）与 Streamable HTTP（`url`/`headers`）。
`approval` 可取 `all` / `mutating`（默认）/ `none`；只读注解
（`readOnlyHint: true`）的工具才可 `replay: "safe"`。MCP server 只拿到最小
环境变量，不会继承 sidecar 的 `LLM_API_KEY`。花名册的 `approval_tools`
默认列了 `run_worker`（它会按模型给的命令行拉起本地执行体），MCP 的写工具
会自动加入同一张审批表。

## 自检

```bash
node --experimental-strip-types selftest.ts          # 崩溃续跑 + 重复投递幂等
node --experimental-strip-types test-openai-tools.ts # 自定义 OpenAI 兼容端点的工具调用
node --experimental-strip-types test-compaction.ts    # 压缩阈值与窗口一致性
node --experimental-strip-types test-compaction-live.ts # 多轮真实触发 pi.compaction + 摘要请求
node --experimental-strip-types test-skills.ts        # 渐进披露 + 路径沙箱
node --experimental-strip-types test-read-file.ts     # 工作目录内回读 + 逃逸拒绝
node --experimental-strip-types test-list-files.ts    # 目录列举：有界、只读、沙箱
node --experimental-strip-types test-host-tools.ts    # 记忆/便笺/事项/Worker 只读工具 + token
node --experimental-strip-types test-profiles.ts      # Worker / Orchestrator 工具面隔离
node --experimental-strip-types test-orchestrator-tools.ts # C2 工具：lease 来自 RunMeta
node --experimental-strip-types test-orchestrator-run.ts   # 模型调 dispatch_task 的端到端
node --experimental-strip-types test-block-kind.ts         # block_and_ask 的 kind 字段
node --experimental-strip-types test-approval-per-call.ts  # 审批绑定参数哈希、一次性消费
node --experimental-strip-types test-mcp.ts           # MCP 工具包装 + 审批
node --experimental-strip-types reference/24-child-tasks.ts  # 官方示例，验证装得上跑得动
```

## 两条踩过的 API 事实

- **phase 的返回值被忽略。** 状态变化必须 `await runtime.commit(() => next, ctx)`；
  只写文档的相位会被判"没有耐久进度"并 fault。
- **`harness.commit` 返回的是活 overlay，提交一结束就失效。** 需要往外传的值
  要在回调里先取成普通值。

## 所有权

库**不**保护你免受第二次打开：两个进程同时开同一个 `agent.sqlite` 都能建任务，
而且不报错。所以 `proper-lockfile` 锁目录是正确性要求，不是优化。Python 侧绝不
打开这个库文件。

## reference/

上游原文，仅供对照，不被生产代码 import：

- `22-subagent-foreground.ts`、`23-subagent-background.ts`、`24-child-tasks.ts`
  来自 `earendil-works/pi` 的 `packages/durable/test/examples/`，只把相对 import
  改成了 npm 包名。
- `vacation.ts` 来自 `v1.0.0` 的
  `packages/coding-agent/src/experimental/vacation/vacation.ts`。
