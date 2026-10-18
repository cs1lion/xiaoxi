# 本机四个 Agent 的 headless 接线（2026-10-17 夜）

本文是本轮真机测试的取证底稿。结论都来自本机实跑，不是 README 推断。
上一份 `RECON_20261017.md` 的判断有两处被本文推翻：WorkBuddy 与 DSH 都有
可调用的 headless 入口，只是不在 PATH 上。

## 1. 能力矩阵

| Agent | 版本 | 入口 | 协议 | 本机实跑 | 结论 |
|---|---|---|---|---|---|
| Codex | codex-cli 0.162.1 | `codex exec --json` | OpenAI Responses → `15721` | 写文件、五事件、真机 commission | 可用；Windows 上 `workspace-write` 仍挂住，需 `danger-full-access` |
| Claude Code | 2.1.296 | `claude -p --output-format stream-json` | Anthropic Messages → `15722` | 写文件、工具调用、真机 commission | 可用 |
| WorkBuddy | CodeBuddy CLI 2.161.0 | `WorkBuddy.exe` as Node + `cli/dist/codebuddy-lite-wb.mjs` | OpenAI Chat → `15721/v1` | `-p` 返回 OK；`stream-json` 调 `Write` 写出 note.txt | **新接通，可用** |
| DeepSeek Harness | 0.2.1-rc.2 | `DeepSeek Harness.exe` as Node + `dsh-desktop-host/lib/cli.js headless` | Anthropic Messages → `15722` | 返回 OK；`--json` 调 `write` 写出 note.txt | **新接通，可用** |

四个 Agent 的模型调用最终都落到本机网关：Codex/WorkBuddy 走 15721
（CC Switch 的 OpenAI 出口），Claude/DSH 走 15722（项目的 Anthropic → OpenAI
翻译层）。15722 的上游仍是 15721。

## 2. WorkBuddy

WorkBuddy 桌面版把 CLI 放在 `resources/app.asar.unpacked/cli/`。`bin/codebuddy`
单独跑会缺 `dist/codebuddy`，但用 WorkBuddy 自带的 Electron 以 Node 模式加载
打包好的 CLI 入口可以跑通。

```powershell
$WB = "$env:LOCALAPPDATA\Programs\WorkBuddy"
$env:CODEBUDDY_BASE_URL = "http://127.0.0.1:15721/v1"
$env:CODEBUDDY_API_KEY = "PROXY_MANAGED"
$env:ELECTRON_RUN_AS_NODE = "1"
& "$WB\WorkBuddy.exe" "$WB\resources\app.asar.unpacked\cli\dist\codebuddy-lite-wb.mjs" `
  -p "Reply with exactly: OK" --model step-5-preview --output-format text -y
```

实跑结果：输出 `OK`，退出码 0。

工具验收：

```powershell
& "$WB\WorkBuddy.exe" "$WB\resources\app.asar.unpacked\cli\dist\codebuddy-lite-wb.mjs" `
  -p "Use the Write tool to create note.txt containing exactly OK. Then reply DONE." `
  --model step-5-preview --output-format stream-json -y
```

实跑结果：`assistant.tool_use Write`、`user.tool_result`、`result success`，
`note.txt` 内容为 `OK`，`usage.input_tokens=49167`（累计）、`output_tokens=88`。

要点：

- 它请求的是 `POST {base}/chat/completions`，不是 Anthropic Messages。默认模型
  是 `auto`，StepFun 没有这个模型，必须显式 `--model step-5-preview`。
- 没有 `CODEBUDDY_API_KEY` 时会要求 `/login`；给了 `CODEBUDDY_BASE_URL` +
  `CODEBUDDY_API_KEY` 后完全跳过 WorkBuddy 账号登录。
- 事件形状与 Claude Code 的 `stream-json` 高度同构：`system/assistant/user/result`。
  现有 `claude_worker.py` 的解析器可以复用大半。
- 隔离：`CODEBUDDY_CONFIG_DIR` 可指向独立目录。CLI 仍会在 `~/.codebuddy` 写
  sessions/logs；shim 应把配置目录指到 commission 工作区，并加
  `--no-session-persistence`。

## 3. DeepSeek Harness

DSH 没有把 `dsh` 放进 PATH，但安装目录里有完整 CLI。用安装自带的 Electron
以 Node 模式加载 `dsh-desktop-host/lib/cli.js` 即可。

```powershell
$DSH = "D:\Programs\DeepSeek Harness"
$env:ELECTRON_RUN_AS_NODE = "1"
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:15722"
$env:DEEPSEEK_API_KEY = "local"
& "$DSH\DeepSeek Harness.exe" --expose-internals `
  "$DSH\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js" `
  headless "Reply with exactly: OK"
```

实跑结果：输出 `OK`，退出码 0。

工具验收用 `headless --json`，事件序列可直接映射到协议 §22：

```
session → status(turn_start) → status(step_start) → tool_call(write)
→ tool_result → status(step_end, usage) → text → final
```

实跑结果：`note.txt` 内容为 `OK`。

要点：

- DSH 默认 provider 是 `deepseek-official`，模型 `deepseek-flash`；没有 key 时报
  `MISSING_CREDENTIAL`。把 `DEEPSEEK_BASE_URL` 指向 15722、`DEEPSEEK_API_KEY`
  给占位 token 后，请求走 Anthropic 协议，由 15722 翻译到 StepFun。
- `DEEPSEEK_BASE_URL` 指到 15721 会报 “Claude Provider 缺少 base_url 配置”，
  因为 15721 把 Anthropic 请求路由到未配置的 Claude Provider。
- `--json` 的 `status.step_end.usage` 是每步真实用量，`final.text` 是最终回复。
- `DSH_HOME`、`DSH_PROFILE_DIR`、`DSH_PERMISSION_MODE` 可用于隔离与权限；
  当前 headless profile 自带工具（read/write/bash）。
- **Windows 默认沙箱下 `pwsh` 不可用（2026-10-17 补测）**：默认模式跑命令会报
  `SetNamedSecurityInfoW failed (Win32 5): grantWrite(<workspace>)`，`write`/`read`
  仍正常。设 `DSH_PERMISSION_MODE=danger-full-access` 后 `pwsh` 恢复（实测
  `echo OK` 返回 completed），要跑命令的 Worker 必须在花名册里显式设这一项。
- `TMP`/`TEMP` 不能指向 workspace 内部：DSH 会报 "Windows ACL temp root must be
  outside the workspace" 并拒绝执行命令，shim 因此不改写临时目录。

## 4. 对慢环的含义

`config/agents_roster.yaml` 现在只需要增加两条 `cli_subproc` 条目，各配一个
shim。调度核心不需要改：`dispatcher.py` 只认 `adapter:` 字符串。

建议的四个真机 Worker：

| id | shim | 适合的任务 | 成本档 |
|---|---|---|---|
| `claude` | `claude_worker.py` | 通用实现、工具调用、代码 | high |
| `codex` | `codex_worker.py` | 实现与命令；需要 `danger-full-access` | high |
| `workbuddy` | `workbuddy_worker.py` | 文档、表格、办公任务；工具面宽 | medium |
| `dsh` | `dsh_worker.py` | 多步任务、长上下文；事件最规整 | medium |

Codex 的生产隔离仍是未解决项。在隔离方案落地前，它只进测试花名册，
不进默认生产花名册；WorkBuddy 与 DSH 可以先进生产，但 shim 必须限制
工作目录并把失败如实上报。

## 5. 复跑命令

```powershell
.\.venv\Scripts\python.exe scripts\test_claude_worker_shim.py
.\.venv\Scripts\python.exe scripts\test_codex_worker_shim.py
# 新增后
.\.venv\Scripts\python.exe scripts\test_workbuddy_worker_shim.py
.\.venv\Scripts\python.exe scripts\test_dsh_worker_shim.py
```

真实调用会花 token，按 `docs/TESTING.md` 的纪律，探针不属于回归门禁。

## 6. 23:40 三 Worker 同题实测

用 `probe_live_workers.py --workers claude,workbuddy,dsh --cases note --repeat 1`
跑同一件“在 out 创建 note.txt”任务，外部 oracle 验收：

| Worker | 结果 | 耗时 | Prompt Tokens | 备注 |
|---|---|---|---|---|
| claude | **PASS** | 20.4s | 36,572 | ready_to_report |
| workbuddy | **PASS** | 31.7s | 30,808 | ready_to_report |
| dsh | **FAIL** | 18.0s | 0 | `status=blocked`；报“sandbox denied write access” |

DSH 的失败不是网关或 shim 解析问题：默认沙箱在本机拒绝写工作目录。用同一 shim
手工复测，补 `DSH_PERMISSION_MODE=danger-full-access` 后写 `out/note.txt` 成功，
事件流为 `session → step → tool pwsh → tool read → 3 × token → artifact`。

补权限后第一次复跑仍失败，暴露出两个真 bug：

1. **spinning guard 把 token 事件当成步骤**：`TelemetryGateway.record_event` 对
   `token` 事件也调用 `guard.observe`，而 token 事件没有 action/arguments/text，
   第三条 usage 的签名与前两条完全相同，于是三步骤 Worker 被误判“鬼打墙”。
   已修：token 事件只落 span，不喂步骤熔断；API 调用重复仍由
   `TelemetryGateway.proxy` 单独判。
2. **重试把已有产物当成“没产出”**：shim 的 `files_created(outdir, since)` 只收
   `mtime >= 本次开始` 的文件；第一次尝试写出的 `out/note.txt` 在第二次尝试里
   不满足 mtime，重试就被判“produced no file”。已修：同一 commission 工作区里的
   已有文件就是产物，重试也认。
3. 另外把 DSH 的 progress 文本加上序号与 tool 参数摘要，避免多次 `pwsh`/重复
   step 号被误判为同一步。

修完后单跑 `dsh note`：**PASS，19.9s，23,783 tokens**。
