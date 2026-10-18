# 真实 Worker 探针：第一次端到端跑通

本文记录 2026-10-17 的第一次真机验证。对象是慢环的派单链路，从 Commission 建单到交付卡过审。

## 判定谓词

一条命令能完整证明下面四件事，才算通过。

- 调度器把密件交给真实的 CLI Agent，而不是 mock。
- Agent 在受控目录里写出真实文件。
- 它的 JSONL 能映射成协议 §22 的五种事件。
- 交付卡过 L1，Commission 进入 `ready_to_report`。

命令：

```powershell
.\.venv\Scripts\python.exe scripts\probe_real_worker.py
```

## 结果

最近一次运行的状态。

| 项 | 值 |
|---|---|
| Commission 状态 | `ready_to_report` |
| 初审 | `passed=true`，`layer=none`，0 条 findings |
| 预算检查 | `calls=1`，`prompt_tokens=29354`，`budget_checked=true` |
| 成果 | `tmp/real-worker-20261017-222337/out/note.txt`，内容 `OK` |
| 端到端耗时 | 61.3 秒 |
| Prompt Tokens | 29,354，1 次调用 |
| 证据 | `tmp/real-worker-20261017-222337/result.json` |

链路是 `ExecutiveService.delegate` → `Dispatcher` → `cli_subproc` → `scripts/worker_shims/codex_worker.py` → Codex CLI → 本地网关 `127.0.0.1:15721`。

## 预算检查曾经被静默跳过

第一次真机跑通时，初审 `passed=true`，但 `audit.usage.budget_checked=false`。原因不是遥测缺失：
同一次初审有 `calls=1`、`prompt_tokens=29352`。真正的原因是探针给 `real_codex` 的
`eval_commands` 只有 `required_fields`、`artifacts_exist`，没有 `token_budget`——预算检查被
合法地跳过了。29k tokens 对 12k 的默认预算，等于没查。

修法：

- 探针给 `real_codex` 加 `token_budget`，并把这一单的 `dispatch.max_prompt_tokens` 调到 40000。
  理由是一个完整 CLI Agent 自带约 20k 的指令开销，12k 的聊天预算对它没有意义；
  40k 留出余量，同时让检查真的跑。
- 探针的 `VERIFIED` 条件加上 `budget_checked is True`，不再只看状态和文件。
- 复跑结果：`budget_checked=true`、`prompt_tokens=29354`、仍 `ready_to_report`。

这不是“预算规则失效”，而是“探针忘了让规则上场”。形状通过不等于判据通过。


## 第一次跑出的是假通过

第一次看起来成功了。状态是 `ready_to_report`，初审通过，交付卡有 summary、intercepted 和 artifacts。

但 Codex 实际报告的是"沙箱只读，所有命令都被拒绝"。交付卡里的 `ref` 指向 `state/commissions.db`，那是调度器自己写的运行库，不是任务成果。初审照样放行。

两个缺陷叠在一起。

1. shim 扫描整个工作目录，把 `state/` 也当成了产出。
2. `_check_artifacts_exist` 只检查 `ref` 是非空字符串，从不确认文件是否存在。规则写的是"可达的 ref"，代码只检查了形状。

这正是 `no_unwired_dry_run` 要拦的假交付。mock 测试发现不了，因为 mock 的 `ref` 是假的，而断言也只看形状。

## 修复

shim 三处改动。

- 产出必须落在独立的 `out/` 目录，扫描只看这个目录。
- `ref` 一律写绝对路径。
- 没有产出文件就发 `error`，不再拼一张空交付卡。

初审一处改动。`kind=file` 的成果现在检查 `Path(ref).is_file()`，文件不存在直接 blocker。

回归。

- `scripts/test_executive_v3.py` 新增"文件成果不存在被打回"，216 项全过。
- `scripts/test_core.py` 38 项全过。
- `scripts/check_protocol.py` PASS。
- `scripts/probe_real_worker.py` 在更严的闸门下仍然 VERIFIED。

本轮又补了两处健壮性，避免“换一次版本就坏”：

- `find_codex()` 不再硬编码 `bin/e78bad9887fa50cf/codex.exe`。顺序是 `AQI_CODEX_EXE` →
  最新的 `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` → PATH。Codex 更新会换 hash 目录，
  硬编码路径会让 worker 在升级后突然“executable not found”。
- `token` 事件的 `ttft_ms` 不再写 0。Codex JSONL 不暴露首字延迟，按协议 §20 用
  `duration_ms` 填两个字段，避免把“没测到”读成“瞬间返回”。

新增 `scripts/test_codex_worker_shim.py`：用假 Codex 进程输出 Codex 形状的 JSONL，
经真实 `cli_subproc` 适配器断言五种事件全部可达，且 `artifact.ref` 指向真实绝对文件。
它不花钱、不联网，能在每次改动后跑。


## 本机 Agent 现状

| Agent | 状态 | 结论 |
|---|---|---|
| Codex | 可用，走 `127.0.0.1:15721` 网关 | 已接成 `cli_subproc` worker |
| Claude Code | 2.1.296 已装，请求 15721 报 `Claude Provider 缺少 base_url 配置` | 网关的 Anthropic 一侧没配好，暂时不可用 |
| WorkBuddy | 5.8.3 已装，Electron 应用，没有 CLI | 只能走 GUI 或 Magpie 之类的适配层 |
| DeepSeek Harness | 0.2.1-rc.2 已装，PATH 上没有 `dsh` 命令 | 它是编排者，不是 worker |
| Magpie | 未安装 | 参考它的本地网关效果，一台网关喂多个 Agent |

网关 `15721` 已经是 Codex 的本地出口。它返回 Codex 模型目录，接受 `PROXY_MANAGED` 作为占位 token。Claude Code 的 `settings.json` 也指向它，但 Anthropic 协议那一侧缺配置。Magpie 做的正是这层协议翻译和模型路由，它还明确支持 WorkBuddy 和 DeepSeek Harness。

## 安全边界

Codex 的 Windows 沙箱在 `workspace-write` 下会挂住，探针改用 `danger-full-access` 才跑通。这个模式对整台机器可写。

现在的约束只有两条。

- 工作目录是 `tmp/real-worker-*`，用完可删。
- 只有探针显式设置 `AQI_CODEX_SANDBOX=danger-full-access`，shim 的默认值仍是 `workspace-write`。

接进生产花名册前必须先决定隔离方案。可选的有 WSL、cua sandbox、专用 Windows 用户，或者把 worker 限制成只产出文本。

## 5 单套件

`scripts/probe_worker_suite.py` 跑 6 条真实任务（含 Claude Code 一例与负例），每条独立工作区。
2026-10-17 晚完整跑了两次，均 **6/6**：

| 用例 | 结果 | 230340 耗时 | 说明 |
|---|---|---|---|
| create_note | ready_to_report | 36.9 s | 产物内容 `OK` |
| create_json | ready_to_report | 51.2 s | 合法 JSON |
| transform | ready_to_report | 34.7 s | 读 `input.txt` 写 `upper.txt` |
| chinese | ready_to_report | 46.2 s | 中文原样写入 |
| claude_summary | ready_to_report | 25.3 s | Claude Code 经本地网关执行 |
| missing_input | blocked | 30.2 s | 负例：缺输入不产文件，转 blocked |

汇总：224 秒 / 191,556 prompt tokens（230340）；540 秒 / 191,885（232051）。
两次都是 R2 重复，不是按题族的 R3 统计。

两个发现。

1. **40k 预算不够。** Codex 自带约 29k 系统提示，读一个输入文件就过 40k，transform 被 L1 以 token 膨胀打回，停在 running。探针预算改到 80k。真实 Worker 的预算要按 Agent 基线设，不能沿用花名册的 12k。
2. **失败会被写成"错误文件"。** missing_input 第一次把 `[ERROR] Source file not found` 写进 `out/secret_copy.txt`，初审照样放行。现在 Worker 契约要求失败时回 `FAILED: <reason>` 且不产文件，shim 把 FAILED 和不产文件都映射成 error 事件。

缺的仍是验收标准本身。L1 只能查"文件在不在"，查不了"内容对不对"。计划层已经能把
`acceptance` 数据化并交给 `AgentLoop` 判（见 [第二轮实测](OPTIMIZATION_ROUND2_20261017.md)），
但还没接进 Worker 密件的初审链路。

还有一条行为要知道。初审打回后 `delegate` 现在会按 `Evaluator.MAX_RETRY` **有界自动重试**，
每次重试前把上一轮 `scolding` 写回密件；`test_executive_v3` 锁定了重试上限与终态。
不要再按旧文档把它改回“停在 running 等 /run”。

## 下一步

1. 用 Magpie 或等价网关补齐 Anthropic 协议，让 Claude Code 可当 worker。方案对比与验收矩阵见
   [GATEWAY_DESIGN](GATEWAY_DESIGN.md)：本轮只设计，不安装 Magpie、不改 15721。
2. 把探针扩成 10 条真实任务的小套件，按 Agent 记录通过率、耗时、Token。
3. 给 WorkBuddy 做适配层，优先确认它有没有可调用的本地接口。
4. 决定 worker 的隔离方案，然后才允许写操作型 task 进入生产花名册。
