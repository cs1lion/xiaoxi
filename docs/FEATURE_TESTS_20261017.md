# 功能实测与优化记录（2026-10-17）

> 同日第二轮按“判据是否真正上场”重挑了一组功能，包含 live 情绪截断、跨天陪伴、
> 计划层真实模型、真实 Worker 六例与一键启动。记录见
> [OPTIMIZATION_ROUND2_20261017](OPTIMIZATION_ROUND2_20261017.md)。

按 [Phase A Frame](PHASE_A_FRAME.md) 的严谨度分级，挑一组“这条线真正依赖”的功能做实测。
目的不是把所有测试再跑一遍，而是找出**形状通过、判据没上场**的地方，并按效果修。

## 1. 实测矩阵

| # | 功能 | 命令 | 级别 | 修前 | 修后 / 结论 |
|---|---|---|---|---|---|
| 1 | Codex shim 五种事件 | `python scripts\test_codex_worker_shim.py` | R1 | 无此测试 | **16/16**；`progress/log/token/artifact/error` 全覆盖，artifact ref 真实存在 |
| 2 | 真实 commission 派单 | `python scripts\probe_real_worker.py` | R2 | VERIFIED，但 `budget_checked=false` | **VERIFIED**，`budget_checked=true`，29,354 tokens，61.3s |
| 3 | Claude Code headless | `claude -p "Reply with exactly: OK"` | R2 | 400 “Claude Provider 缺少 base_url 配置” | **OK，退出码 0**；经 15722 网关 |
| 4 | Anthropic 网关协议 | `python scripts\test_anthropic_gateway.py` | R1 | 无网关 | **30/30**；文本/工具/流式事件全过 |
| 5 | Claude Worker shim | `python scripts\test_claude_worker_shim.py` | R1 | 无此测试 | **16/16**；五种事件 + 真实 artifact ref |
| 6 | 真机 Claude commission | `python scripts\probe_claude_worker.py` | R2 | 不可用 | **VERIFIED**，36,284 tokens，17.5s，Write 写出 `out/note.txt` |
| 7 | 生产花名册 Claude | `python scripts\probe_roster_claude.py` | R2 | 只有 manual Agent | **选中 `claude`**，ready_to_report，74,269 tokens，25.8s |
| 8 | UI 横幅/胶囊可读性 | `node scripts\audit-ui-metrics.mjs` | R1 | 标题 1.04:1，提示 2.11:1 | **10.13:1 / 4.97:1**；胶囊深底亮字 |
| 9 | 移动端布局 | 同上 + `test-companion-browser.mjs` | R1 | 头像压卡 22px | 头像 96×160，卡片留 2px；390 无横向溢出 |
| 10 | 心流防打扰（§23.6） | `node scripts\test-aqi-blocks-browser.mjs` | R1 | 打字时卡片仍立即弹出（队列未接线） | **12 checks passed**；挂起/补弹/报数/熄灯全通 |
| 11 | 三态胶囊材质 | `node scripts\test-presence-capsule-browser.mjs` | R1 | `blur(10px)`，测试要求 24px + saturate | **9 checks passed** |
| 12 | 语音/双工/播放 | `npm test` | R1 | — | **exit 0**；voice/playback/duplex/capture/checkpoint/baseline 全绿 |
| 13 | VRM / 眼神 / 动作 | `npm test`（含 `test:vrm`、`test:dialogue-gaze`） | R1 | — | **exit 0**；10 项 VRM 映射 + 129 项交流眼神等全绿 |
| 14 | 主 UI 浏览器契约 | `node scripts\test-ui-browser.mjs` | R1 | — | 47 checks，无页面错误 |
| 15 | 界面领航 | `node scripts\test-ui-nav-browser.mjs` | R1 | — | **PASS**，16 checks |
| 16 | 共同事项 | `node scripts\test-companion-browser.mjs` | R1 | — | **PASS**，31 checks |
| 17 | 语音浏览器链路 | `node scripts\test-voice-browser.mjs` | R1 | — | **ok: true**，37 checks |
| 18 | 协议 / 核心 / 离线评测 | `check_protocol.py` + `test_core.py` + `run_evals.py` | R1 | — | PASS / 40 / 5 |
| 19 | 督办与适配器 | `test_executive_v3.py` + `test_agent_adapter_ext.py` | R1 | — | 221 / 68，全绿 |
| 20 | WorkBuddy Worker | `test_workbuddy_worker_shim.py` | R1 | 无此测试 | **34/34**；工具调用、失败、隔离全过 |
| 21 | DSH Worker | `test_dsh_worker_shim.py` | R1 | 无此测试 | **43/43**；多步 token、失败、超时全过 |
| 22 | 网关完整契约 | `test_anthropic_gateway.py` | R1 | — | **72/72**；Anthropic/Responses/Chat、路由、fallback、脱敏 |
| 23 | 三 Worker 同题真机 | `probe_live_workers.py --workers claude,workbuddy,dsh --cases note --repeat 1` | R2 | dsh 首次 FAIL（沙箱/熔断/重试） | claude PASS 20.4s/36,572；workbuddy PASS 31.7s/30,808；dsh 修复后 PASS 19.9s/23,783 |

## 2. 按效果做的优化

### 2.1 真实 commission：让预算规则真的上场

- **现象**：29,354 prompt tokens 的单子过审，`budget_checked=false`。不是遥测缺失，是探针的
  `eval_commands` 没列 `token_budget`。
- **改动**：探针加 `token_budget`；`dispatch.max_prompt_tokens` 从 12k 调到 40k（CLI Agent 自带
  ~20k 指令开销）；`VERIFIED` 条件加 `budget_checked is True`。
- **效果**：同一单 `budget_checked=true` 且仍过审；预算规则从“没查”变成“查过且通过”。

### 2.2 Codex shim：换版本不坏、首字不撒谎

- **现象**：二进制路径硬编码到某个 hash 目录；`ttft_ms` 写 0。
- **改动**：`find_codex()` 按环境变量 → 最新安装 → PATH 解析；`ttft_ms=duration_ms`。
- **效果**：确定性测试 16/16；真实 commission 仍 VERIFIED。

### 2.3 UI：先修可读性，再修重叠

- **现象**：深色 Holo 表面继承亮色主题文字；移动端头像压建议卡。
- **改动**：横幅改暖纸卡；胶囊保留深色玻璃但改亮字并加强 blur/saturate；移动端头像 96×160 右对齐。
- **效果**：对比度 1.04/2.11 → 10.13/4.97；390 `scrollWidth=390`，头像与卡片留 2px。

### 2.4 心流防打扰：把现成模块接上，并修正导排条件

- **现象**：`web/aqi-flow.js` 有纯逻辑与单测，但 `app.js` 从未 import；浏览器测试“打字不弹卡”必失败。
- **改动**：
  1. `app.js` 引入 `createAqiFlow` / `aqiPendingMessage`；
  2. 监听真实 `input`（IME 主力路径）与 `aqi-notes-typing`，快捷键不续安静期；
  3. `handleAqiBlocks` 安静时 `hold` + 胶囊报数，停手后按原序 `drain`；
  4. 修正导排条件：**安静时继续等，不安静才补弹**（第一版写反了，测试抓出来）。
- **效果**：`test-aqi-blocks-browser.mjs` 从 10 项后失败 → 12 项全过。

### 2.5 Avatar：只做能落地的一处

- **现象**：主舞台脚下阴影几乎不可见。
- **改动**：`::before` 阴影从 `#7b906127`/20px 改为 `#4f5f3d38`/24px + 2px blur。
- **效果**：截图可见轻微落地感；发际线、嘴、光影不做无对照的大改，列入下一轮实验。

### 2.6 三 Worker 同题：DSH 的三连修

- **首跑**：claude PASS、workbuddy PASS、dsh FAIL（`sandbox denied write access`）。
- **修一（权限）**：dsh profile 与生产花名册补 `DSH_PERMISSION_MODE=danger-full-access`；
  手工复测能写 `out/note.txt`。
- **次跑**：dsh 仍 FAIL，`spinning_guard` + 重试后 “produced no file”，但文件其实存在。
  根因两条：
  1. `TelemetryGateway.record_event` 把 `token` 事件也喂给 spinning guard；token 事件没有
     action/arguments/text，第三条 usage 的签名与前两条相同，三步骤 Worker 被误判鬼打墙。
     已修为“token 只落 span，不参与步骤熔断”。
  2. shim 的 `files_created(outdir, since)` 只收本次 mtime；重试时第一次写出的文件被排除，
     于是“有文件却报没产出”。已修为“同一 commission 工作区里的已有文件就是产物”。
- **再三跑**：dsh PASS，19.9s / 23,783 tokens。三 Worker 同题全部 PASS。
- **教训**：熔断器、重试、产物扫描三处都容易把“已经做完”判成“没做”；只看初审状态不够，
  必须用外部 oracle 对真实文件验收。

## 3. 没有优化的（以及为什么）

| 项 | 原因 | 下一步 |
|---|---|---|
| Claude Code 可用 | 需要 Anthropic→OpenAI 翻译层；本轮只设计 | 方案 C：最小文本流翻译 + `/v1/messages 200` 验收 |
| 发际线/嘴/额头阴影 | 需要改 VRM 资产/材质，当前没有三角度对照 | 复制模型，先改前发内缘与默认 morph，拍三角度对照 |
| 两枚模式胶囊合并 | 涉及信息架构与可达性，不宜在视觉修复里顺手改 | 先定“一个状态 + 一个开关”的结构，再动 |
| 主舞台说话推近 | 需要相机状态与语音播放时钟联动 | 先做静默/说话两档镜头，再测是否挡内容 |
| 生产 Worker 隔离 | `danger-full-access` 只允许探针临时目录 | 单独决策 WSL / cua sandbox / 专用用户 |
| 10 条任务套件 | 需要预算与题族设计，不是本轮范围 | 在网关与隔离定后做 R3 |

## 4. 结论

本轮的净收益是三个“判据真正上场”：

1. 真实 commission 的预算检查从跳过变成执行；
2. UI 的可读性从 1:1 变成可读，并且移动端不再压可点区域；
3. 心流防打扰从“有模块没接线”变成浏览器可验证的行为。

其余结论都停在 R1/R2，不升级为“稳定”或“生产可用”。
