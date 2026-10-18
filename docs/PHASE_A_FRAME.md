# Phase A Frame — 真机 Worker、网关与视觉这条线怎样才算完成

日期：2026-10-17。本文只定义这一轮的完成谓词、范围与严谨度，不重复已有设计文档。执行记录见
[RECON_20261017](RECON_20261017.md)、[DECISION_TRAIL_20261017](DECISION_TRAIL_20261017.md)。

## 1. 为什么需要这一帧

项目设计文档很全，但“真机 Worker + 本地网关 + UI/头像视觉”这条线一直缺一个可证伪的完成谓词。
[REAL_WORKER_PROBE](REAL_WORKER_PROBE.md) 的第一次假通过已经证明：只断言形状会撒谎——Codex
报告“沙箱只读”，交付卡却照样过审。因此本轮不把“文档写了”“接口通了”“看起来不错”当成完成。

现存四个缺口：

1. Claude Code 已安装，但经 `127.0.0.1:15721` 请求 Anthropic 协议时直接 400；网关只有 Codex 一侧。
2. 真实 commission 的初审 `budget_checked=false`：预算规则根本没跑，29k prompt tokens 照样放行。
3. UI 的“正在一起做”深色横幅在暖白主题下几乎是深色文字压深色背景，正文不可读。
4. Avatar 的正面发际线、嘴部可读性与主舞台构图没有验收目标，“好看”仍靠主观印象。

## 2. 完成谓词（Done Predicate）

六条全部满足才算这一轮完成。任何一条不满足，就只能记“未完成/受阻”，不能改口径。

### P1 · 帧本身

- 本文存在，且 [DECISION_TRAIL_20261017](DECISION_TRAIL_20261017.md) 的每条结论都能回指到本文的谓词或证据。
- 证据：文件路径 + 决策记录中的“谓词”列。

### P2 · Codex shim 说得对五种事件

- 确定性测试不调用真实模型，证明 shim 的输出经 `cli_subproc` 映射后只出现
  `progress / token / log / artifact / error` 五种事件；`artifact` 的 `ref` 是真实存在文件的绝对路径。
- 真实 commission 走同一个 dispatcher，产物文件存在且内容匹配，Commission 到 `ready_to_report`。
- 初审必须真的跑过预算检查：`audit.usage.budget_checked=true`，而不是因为 `eval_commands` 漏了
  `token_budget` 而静默跳过。
- 证据命令：
  - `.\.venv\Scripts\python.exe scripts\test_codex_worker_shim.py`
  - `.\.venv\Scripts\python.exe scripts\probe_real_worker.py`

### P3 · 网关层有设计结论，不靠猜

- 记录当前 `15721` 的真实能力边界：OpenAI Responses 可用；Anthropic Messages 返回 400
  “Claude Provider 缺少 base_url 配置”；Claude Code `-p` 复现同一错误。
- 对照 Magpie 的参考实现，给出“采用 / 借鉴 / 不采用”的明确结论、协议矩阵、遥测接入点、
  安全边界与验收命令。
- 若本轮不实现网关，必须把“未实现”写成决策，而不是把 400 写成“暂时不可用”。
- 证据：`docs/GATEWAY_DESIGN.md` + 复现命令输出。

### P4 · UI 视觉审计与至少一个 P0 修复

- 1440×1000 与 390×844 两档真实截图，列出按严重度排序的问题。
- 至少修掉一个 P0（当前是横幅正文对比度），修后重新截图并跑浏览器回归。
- 不得新增顶栏按钮；不得为了截图改业务状态。
- 证据：`eval/visual-audit-20261017/` 修前修后图、`docs/UI_VISUAL_AUDIT.md`、
  `node scripts/test-ui-browser.mjs`。

### P5 · Avatar 3D 视觉审计与方向

- 正面、近景、视线跟随、主舞台四类截图；问题按“轮廓 / 眼睛 / 嘴与表情 / 光影 / 构图”归类。
- 给出可测量的方向（例如主舞台脸部宽度、发际线接缝、默认嘴角、额头高光面积），
  并明确本轮改了什么、没改什么、下一轮怎么验。
- 证据：`docs/AVATAR_VISUAL_AUDIT.md` + 截图 + 相关 VRM/动作测试。

### P6 · 决策轨迹

- 每条决策写：选项、证据、选择、代价、状态（已验证 / 未验证 / 明确不做）。
- “未验证”必须点名还缺哪一次真实运行，不能写“后续观察”。
- 证据：`docs/DECISION_TRAIL_20261017.md`。

## 3. 范围

**做：**

- 真实 Codex commission 的复跑与预算断言；shim 的确定性协议测试。
- 网关现状取证与设计（不安装 Magpie，不改 `15721` 的配置）。
- 主工作台与 Avatar 的只读视觉审计；低成本、可回归的 P0 视觉修复。
- 与上述改动直接相关的文档、测试、截图、决策记录。

**不做：**

- 不新增产品功能、不改记忆语义、不改协议字段、不迁移渲染引擎。
- 不安装 Magpie、不重配 CC Switch、不把 Claude Code 接进生产花名册。
- 不做 10 条任务套件、不做生产级 Worker 隔离（`danger-full-access` 仍只限探针临时目录）。
- 不做真实麦克风/扬声器的听感与延迟验收。
- 不把单次真实运行说成稳定性结论。

## 4. 严谨度分级

| 级别 | 含义 | 允许的结论 |
|---|---|---|
| R0 | 只对形状/字段做断言，没有真实副作用 | 只能叫“接线检查”，不能叫完成 |
| R1 | 离线确定性测试、假 Worker、受控截图 | 可以叫“机制通过” |
| R2 | 本机真实依赖跑一次（真实 Codex、真实模型、真实浏览器） | 可以叫“本机单次验证”，不能叫稳定 |
| R3 | 多次重复、按题族统计通过率/耗时/成本 | 才能叫“稳定/可投产” |

本轮目标：P2 达到 R1+R2；P3 达到 R1+真实取证；P4/P5 达到 R1（截图 + 回归）；
不追求 R3。所有汇报必须带级别，禁止把 R0 写成“已验证”。

## 5. 失败与停止条件

出现任一条即停止扩张，先修或写受阻：

- shim 测试出现第五种以外的 `type`，或 `artifact.ref` 指向不存在的文件。
- 真实 commission 的 `budget_checked` 仍为 `false`，或产物内容不匹配。
- 在没有 `/v1/messages 200` 证据时声称 Claude Code 可用。
- 修完 UI 后横幅对比度仍低于 WCAG AA 正文 4.5:1。
- 视觉结论没有截图或没有修前/修后对照。
- 把单次 R2 结果写成“稳定”“生产可用”。

## 6. 命令清单

```powershell
.\.venv\Scripts\python.exe scripts\test_codex_worker_shim.py
.\.venv\Scripts\python.exe scripts\probe_real_worker.py
.\.venv\Scripts\python.exe scripts\check_protocol.py
.\.venv\Scripts\python.exe scripts\test_core.py
.\.venv\Scripts\python.exe scripts\run_evals.py
npm test
node scripts\test-ui-browser.mjs
node scripts\capture-visual-audit.mjs
```

## 7. 产出

- 代码：shim 的健壮性修复、确定性测试、探针预算断言、UI P0 修复、心流防打扰接线。
- 文档：本帧、Recon、Gateway 设计、UI 审计、Avatar 审计、功能实测、决策轨迹。
- 证据：真实 commission 的 `result.json`、修前修后截图、测试输出。

## 8. Phase E 结果

| 谓词 | 结果 | 证据 |
|---|---|---|
| P1 帧 | 满足 | 本文 + 决策轨迹 |
| P2 shim + 真机 commission | 满足（R1+R2） | `test_codex_worker_shim.py` 16/16；`probe_real_worker.py` VERIFIED，`budget_checked=true` |
| P3 网关设计 | 满足“设计 + 取证”，未实现 | `GATEWAY_DESIGN.md`；`/v1/messages` 400 已复现 |
| P4 UI | 满足（R1） | 对比度 10.13 / 4.97；390 无溢出；UI 47 checks |
| P5 Avatar | 满足“审计 + 方向”，仅落地地面阴影 | `AVATAR_VISUAL_AUDIT.md`；发际线/嘴未改 |
| P6 决策轨迹 | 满足 | `DECISION_TRAIL_20261017.md` |

**未满足、但已明确记录：** Claude Code 仍不可用；R3 未做；生产隔离未决定；
Avatar 资产级修复未做。这些不阻塞本轮结论，也不被写成“已完成”。
