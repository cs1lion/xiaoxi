# 第五轮 review：durable 接管、结构化输入、形象联动与四个未决项

> **⚠️ 过期快照（2026-10-25 回填标注）**：本文为 2026-10-18 的历史记录，其后进展以 `git log` 与门禁命令输出为准；现行交付状态见 `docs/DELIVERY_PLAN_20261024.md`。

日期：2026-10-18（晚）
范围：pstack-claude 移植、Claude Code 插件系统、durable 接管四场景、门禁、四个断点、
`choice:`/`say:` 输入缺口、3D 形象 commission 联动、记忆与自进化生产效用。
方法：以当前磁盘代码与真实库读数取证；门禁实跑；不采信文档里的「已完成」字样。

---

## 一、已核实完成项

### 1.1 门禁：30 道，0 失败，0 跳过（本次实跑）

`scripts/run_gates.py` 的 `GATES` 共 30 条（`run_gates.py:54-189`），本次完整跑了
3 分 36 秒，报告落在 `tmp/gate_report_review.json`：

```
跑过并断言 : 30
失败       : 0
跳过       : 0
```

其中 24 条在 `ALWAYS_REQUIRED`（`run_gates.py:192-197`），跳过即判失败的口径没有放宽。
新增的 `durable_revive`（`test-revive.ts`，13.1s）确实跑了，不是被 `SKIP` 放过。

### 1.2 四个断点：代码层逐条对上

| 断点 | 实现位置 | 结论 |
|---|---|---|
| 相对路径解析 | `server/assistant/adapters/durable.py:138-145` `_absolute()`；`:103-136` 解析 `python`→`.venv`、`node`→PATH | 成立。只把「相对仓库根且真实存在」的路径改绝对；非路径参数原样 |
| stderr 尾部收集 | `agent-runtime/src/takeover.ts:118-145`（stderr 单独进 `noise`，`slice(-400)`） | **只成立一半**，见 §2.3 |
| 幂等键 `commission_id@attempt` | `agent-runtime/src/runtime.ts:47-51` `runKey()`；`:121-136` create 检查 | 成立。attempt 取台账 `retry_count`（`dispatcher.py:359`），`answer()` 递增（`dispatcher.py:523`） |
| 锁重试预算 20s | `agent-runtime/src/runtime.ts:74-82`：`STALE=10s`、`RETRIES=40 × 500ms = 20s` | 成立。预算 > 过期窗口，`test-revive.ts` 真硬杀后可重开 |

### 1.3 durable 接管四场景：三段成立，硬杀恢复此前不稳

探针 `scripts/probe_takeover_production.py` 覆盖四段：① 派单接管与验收、② 重启对账、
③ 硬杀恢复、④ 内核不可用。磁盘上有 26 次运行的临时目录（`%TEMP%/takeover-prod-*`），
台账共 73 单：23 单 `ready_to_report`、27 单 `EMPTY_RESPONSE`、23 单 `ConnectError`。

按段核对台账，而不是看脚本有没有写那一段：

| 段 | 证据 | 结论 |
|---|---|---|
| ① 派单接管 | 多次 `ready_to_report`（如 `c_ae1b0898f058`），遥测 `calls≥1`、台账有进度步数 | 成立 |
| ② 重启对账 | `takeover-prod-urh1wt25` 第二单 `ready_to_report`，新 service 采纳成果 | 成立（但对重做单曾失效，见 §2.2） |
| ③ 硬杀恢复 | 26 次运行里，`retry=1` 后到 `ready_to_report` 只有 `c_ec4b597f8de7` 一次；其余（`urh1wt25`、`y19lh7dn`、`zqg7n2co` 等）都是 `blocked retry=1 ConnectError` | **此前不稳**，根因见 §2.9 |
| ④ 内核不可用 | `c_98338ab25b4f`：`blocked` + `ConnectError`，没有假装执行 | 成立 |

所以更准确的说法是：**四段的代码与断言都在，③ 的闭环此前只成功过一次**。
「硬杀后监督器把内核拉回来」本身成立（`durable_revive` 13.2s 通过），断的是
「拉回来之后，请示获答的重派不要撞上旧端点」——见 §2.9。

必须同时写明的边界：

1. 这四段是**探针环境**，不是生产。`config/agents_roster.yaml:192` 仍是
   `durable.enabled: false`，本机也没有 `config/*.local.yaml` 覆盖；
   `server/app.py:1221-1223` 在未启用时直接 return。线上是否启用未从本机核验，
   按同一份花名册推定为未启用。
2. 四段全通过的探针**没有把 stdout 落盘**，只能从台账反推；`VERIFIED` 文本不存在。
3. ① 有 27/73 的 `EMPTY_RESPONSE`，不是「偶发」量级，见 §3.4。

### 1.4 `choice:<index>` 已闭合

- 协议：`docs/PROTOCOL.md:495` 声明 `choice:<index>`（0 起，越界忽略）。
- 前端：`web/app.js:667-678` 有分支，越界/坏 index/无 options 直接 return，
  命中则递归调用该 option 的 `cmd`。
- 门禁：`scripts/check_protocol.py:460-476` 第 7b 条把 `CMD_VERBS` 与 `app.js`
  比对；删掉分支字面量会 FAIL（本次确认每个动词在 app.js 只出现一次，就是分支本身）。

### 1.5 pstack 移植与 Claude Code 插件系统

- `pstack` 是 **Cursor 插件的 claude-code skills 子集**，来源与路径写在
  `.agents/skills/pstack/DSH.md:3`，与磁盘一致。
- 入口是 `.agents/skills/poteto-mode/SKILL.md`，它要求先读 `../pstack/DSH.md`，
  再按 DSH 映射工具；该入口有效。
- 53/54 个 `SKILL.md` 带 `disable-model-invocation: true`，与 DSH.md 的描述基本一致
  （`setup-pstack` 是唯一例外）。
- Claude Code 插件市场**没有参与本工程运行**：`scripts/worker_shims/claude_worker.py:177-184`
  用隔离的 `CLAUDE_CONFIG_DIR`，不加载宿主插件。
- 插件安装记录是真的：`~/.claude/plugins/installed_plugins.json` 里有
  `pstack@cursor-plugins 0.15.25`（commit `09d06eb`），marketplace 指向
  `https://github.com/cursor/plugins.git`。工程里的 `.agents/skills/pstack`
  是从该插件包**手工拷贝**的 skills 子集，不是插件系统在加载。
- **宿主 `~/.claude/settings.json` 不是合法 JSON**（第 8 行 `env` 块用了
  `KEY=value` 而不是 `"KEY": "value"`），且该块里有明文凭据。工程 worker 用隔离
  config dir 不受影响；但宿主 Claude Code 的 `env` 与 `enabledPlugins` 可能整体
  不生效——这正是「插件装了、DSH 仍要手拷」的原因。属于环境层待修，不是仓库问题。

---

## 二、需修正项

### 2.1 已修：适配器 docstring 与幂等键不一致（P2）

`durable.py` 开头还写着「幂等键是 commission_id——重复投递返回同一单」，与实现
（`@attempt`）矛盾。已改为 `commission_id@attempt`，并写清「重投同一次幂等 / 重做拿新单」。

### 2.2 已修：重启对账查错 attempt，重做过的单成果会被扔掉（P0）

`ExecutiveService._adopt_durable` 原来调用 `lookup(commission_id)`，生产闭包
`server/app.py:_durable_lookup` 也**不带 attempt**；侧车 `routes.ts` 默认 `attempt=0`。
后果：一单只要重做过（`retry_count ≥ 1`），内核里的任务是 `@1`，对账查 `@0` 查不到
→ 一律转 BLOCKED，内核已经写完的成果被丢弃。这正好打在「重启对账」最该生效的场景上。

修法：
- `executive.py` 用台账 `retry_count` 作为 attempt 传入，保留单参旧签名回退；
- `app.py` 的 `_durable_lookup.lookup(commission_id, attempt=0)` 把 attempt 带进 query；
- 探针 `_lookup` 同步；
- `test_durable_adapter.py` 新增回归：造一单 `retry_count=1`，假侧车**只在 attempt=1**
  返回 found，并断言收到的 query 是 `["1"]`。修复前这条必失败。

### 2.3 已修：stderr 在「退出码 0 但没交差」分支被丢掉（P1）

`runLocalAgent` 收集了 stderr（`takeover.ts:145` `diagnostics`），但 `runWorker` 只在
`!outcome.ok`（exec 本身失败）时把它拼进错误；**最常见的失败形态**——子进程正常退出、
没调 `submit_artifact`——返回的文案里没有 stderr。断点修了一半。

已修：该分支补 `stderr: <tail>`；`test-takeover.ts` 的 silent shim 现在往 stderr 写
`config missing: AQI_WORKSPACE_ROOT`，断言失败信息里能读到（18 项全绿）。

### 2.4 已修：审批放行仍连死端口（P1）

适配器已经用 `endpoint_provider` 每次取活端点，但
`ExecutiveService._grant_durable_approvals` 仍读绑定时写死的 `entry["endpoint"]`。
内核硬杀后换端口重起，老板答复后的 `/v1/approvals` 会静默打空，被拦的工具继续拦着。
已改为优先调用 `entry["endpoint_provider"]()`，取不到才回落。

### 2.5 已修：`switch_agents` 切换后写盘根丢失（P1，直接影响未决项 1）

`bind_durable` 只把 `{endpoint, token}` update 进 Worker。`cli_subproc` 的写盘根在
`env.AQI_WORKSPACE_ROOT`，durable 适配器只认顶层 `workspace_root`（`durable.py:84-101`）。
一旦用 `switch_agents: [dsh]` 切过去，`_workspace()` 返回空串，sidecar 的 `write_file`
拒绝一切写入——能跑、能交一段文字，但拿不到真实文件成果，看起来像「接管成功」。

已修：绑定时先从 Worker 自身 `workspace_root` 或 `env.AQI_WORKSPACE_ROOT` 继承；
新增 `test_switch_agent_inherits_workspace_root`（3 条断言）。

### 2.6 已修：宿主环境可全局绕过审批（P2）
`agent-runtime/src/approval.ts:61` 读 `XIAOXI_APPROVED_TOOLS`，而生产从不设置它，
只有测试用。但 `DurableSupervisor._spawn` 会 `dict(os.environ)` 全量继承；如果开发者
shell 里残留这个变量，整个 sidecar 的所有单都不再需要确认。已在 `_spawn` 里显式 pop，
生产的放行只能走 `/v1/approvals` 按单生效。

### 2.7 已修：探针的 VERIFIED 可能是假阳性（P2）

原探针有两个洞：
- ② 在「内核没终态」时 `return finish(verdicts)`，此时 ③④ 根本没跑，但只要前面
  全过就打印 `VERIFIED`；
- ③ 打印 `runs_before → runs_after`，却不参与断言；只看 `status=ready_to_report`
  会把「拿回旧终态任务原样再交」也判成通过——正是幂等键要防的事。

已改：② 早退记 `False`；③ 只有确实进过 `blocked` 才要求 `runs_after > runs_before`；
`finish()` 要求至少 9 条断言，少一段就是 `NOT VERIFIED`。

### 2.8 已修：热重载会把运行时注入的 durable 端点冲掉（P1，预防）

`reload_roster()` 整份替换 `evaluator.roster` / `dispatcher.roster`，而
`endpoint` / `token` / `endpoint_provider` 是 `bind_durable` 运行时注入的，YAML 里没有。
一旦热重载发生，durable 单会在下一次派单时报「没配 endpoint」转 BLOCKED。
已改为按 agent id 迁移这四个运行时字段；不动 `adapter`，所以「改回 cli_subproc」
的回退仍然生效。新增 `test_reload_keeps_runtime_binding`（3 条断言）。
说明：`reload_roster()` 目前没有调用点，这是为接线留下的坑先补上。

### 2.9 已修：硬杀重启窗口里把旧端点当活的（P1，③ 不稳的根因）

台账把问题指出来了：26 次运行里，硬杀后 `retry=1` 能回到 `ready_to_report` 的只有
一次；其余都是 `blocked retry=1 ConnectError`。根因不在锁，在「重启窗口」：

1. sidecar 被 `taskkill` 后，`_watch` 要等 `process.wait()` 返回、再 `_spawn`；
2. 这段窗口里 `DurableSupervisor.endpoint` 还是旧端口，`available` 还是 `True`；
3. `answer()` 立刻重派 → 适配器 `_endpoint()` 拿到旧端口 → `ConnectError`；
4. 20s 的锁重试预算只保证「内核最终能起来」，不保证「起来之前不把旧地址发出去」。

修法两层：
- `current_endpoint()` 先看进程是否活着（`process.returncode is None`），进程已死就
  返回 None，不再返回缓存端口；
- 适配器 `_endpoint_wait()` 在端点提供者返回 None 时**有界轮询 20s**（与锁预算同量级），
  覆盖「被硬杀留下的锁要等满 10s 才过期」这段；超时仍抛 `AdapterError`，不返回假端点。
- 同时修了 `test_durable_adapter.py` 的对账 regression（§2.2）。

这条修完后，③ 才谈得上「稳定」。仍需一次真机探针复跑留读数（要真实 LLM，见 §1.3 边界）。

### 2.10 待修（结构性，不改本轮）

| 项 | 现状 | 判断 |
|---|---|---|
| `say:<text>` 仍是自由文本回灌 | `web/app.js:659-666`：把选项文本塞回输入框，再走一轮 LLM | 缺口仍在。`choice:<index>` 只解决「点哪个」，不解决「点了要做什么」。要确定性分支，option 必须带 `intent` 枚举或 `(block_id, index)` 服务端查表 |
| 3D 形象层不感知 commission | `web/executive.js` 只画卡片；`web/avatar/index.js` 只监听 `aqi-touch` | 成立。派单/打回/验收都不通知形象层，她只在说话时有反应 |
| 记忆效用未在生产结算 | `data/memory.db`：`memories=1`、`memory_usage=0`、`utility_n=0`、`utility=0.5`、`last_reward_at=None` | 成立。机制有单测，生产零结算；需要一次端到端脚本证明「效用改变过排序」 |
| 自进化从未跑过 | `self_state` 有 1 行但 `last_cycle_at=None`、`cycles_today=0`；`reflections=0` | 成立。注意原文「三张表全空」不准确：是**有行无内容** |
| pstack 移植不可复现 | `.gitignore:50-53` 忽略 `.agents/` 与 `.claude/` | `DSH.md` 只在本机。别人 clone 拿不到；CI 也不检查。若算工程能力，需安装脚本或纳入库 |
| `SKILL.md` 死链 | 20+ 个 `SKILL.md` 指向 `../../docs/harness/claude-code.md`，该文件原本不存在 | 已补一个指针文件到 `DSH.md`（本机生效）；根因是 bundle 按 Claude Code 目录结构写、DSH 只拷贝了 skills |
| 门禁 7b 口径偏弱 | 只查 `"choice"` 字面量是否出现在 app.js | 能抓「整段被删」，抓不到「分支在但不读 options」。建议加 `options[index]` 结构断言 |
| 宿主 `~/.claude/settings.json` 非法 | `env` 块写成 `KEY=value`，json 解析在 line 8 失败；块内含明文 token | 工程不受影响（隔离 config dir），但宿主的 `env` / `enabledPlugins` 可能不生效；凭据应移出并轮换 |

### 2.11 文档与事实的三处出入

1. `docs/DECISION_TRAIL_20261018.md:151` 写「28 道门禁」；当前是 30 道（多了
   `durable_revive`、`voice_browser` 等），报告口径已变。
2. 同文件 `:64-65` 写「本轮只拿到 ① 的完整实测」；`PI_DURABLE_MIGRATION.md:492`
   写「四段全通过」。两句都对，但**时间点不同且未标注**，读者会以为互相矛盾。
3. 同文件 `:145` 写「self_state 三张表全空」；实际 `self_state` 有 1 行默认值，
   是**内容为空**，不是没有行。

---

## 三、待决策结论（四项，直接拍）

### 3.1 `durable.enabled` 与 `switch_agents`：**不切，保持 false / []，另开一条试点**

依据：

1. **仓库与本地覆盖都没启用**。`enabled: false` 时 `app.py` 直接 return；四场景是
   探针自己起 supervisor 跑的。先把「线上真启用」当成一次发布，而不是改一行 YAML。
2. **能力面确实窄**。durable 只有 `write_file` / `submit_artifact` / `block_and_ask`
   / `run_worker`（`runtime.ts:20-25`），没有 Read/Bash/Grep。四个 `cli_subproc`
   Worker 的 shim 都依赖真机 CLI 工具。
3. **`AQI_WORKDIR` 兼容只验过 DSH**。`takeover_worker()` 是探针手工补
   `workspace_root` 的；生产继承刚刚才补上（§2.5），没有跑过真机。
4. **A/B 不能当切换依据**。`durable 10/10` 对 `cli_subproc 2/10`，但基线 8/10 死于
   预算闸（`PI_DURABLE_MIGRATION.md:446-448`），两侧 token 差 15 倍主要是重试求和与
   真机系统提示，不是能力差。

**建议动作**：保持 `enabled: false`、`switch_agents: []`；新增一条
`dsh-durable` 试点条目（`adapter: durable` + `workspace_root: data/commissions`），
只让明确「不需要 Bash/Read」的整理类委托走它。观察两周再谈全局切换。
若一定要切，只切 `dsh`（唯一验证过 `resolve_workdir()` 优先读 `AQI_WORKDIR`），
`claude` / `workbuddy` / `codex` 不切。

### 3.2 `approval_tools` 填什么：**`write_file`，先不含 `run_worker`**

依据：

- `submit_artifact` 是交差动作，拦它等于所有单永远交不了差；
  `block_and_ask` 是求助通道，拦它等于模型无法请示（`approval.ts:55-58` 的设计前提
  就是「被拦后模型能问」）。
- `write_file` 是唯一有真实外向副作用的窄口（写盘），沙箱已限制到每单目录，审批是
  第二道闸，符合 defense in depth。
- `run_worker` 会让执行体再拉起一个真机 CLI（`replay: "unsafe"`），风险等级更高，
  但它是「接管」的核心能力；默认拦会让试点单全部卡住。先放开、观察，等有一次滥用
  样本再收紧。

配置：`durable.approval_tools: "write_file"`。同时保留「留空 = 不拦」的语义。

### 3.3 预算口径：**按次为质量闸，按单为成本闸，两者分开**

依据：

- `evaluator._check_token_budget`（`evaluator.py:151-159`）拿
  `telemetry.commission_usage` 的 **SUM(prompt_tokens)** 与 `max_prompt_tokens` 比。
  这是「整单累计」，但配置项名与 L1 规则文案都像「单次」。
- 整单求和会惩罚重试：初审打回一次，预算就翻倍，最后打回的其实是「模型被要求改」，
  不是「上下文膨胀」。A/B 里真机 Claude 24–63 万就是这么来的。
- 真机 CLI 一轮系统提示 + 工具目录约 10 万，`80000` 即使按单次也偏紧。

**建议口径**：
- `max_prompt_tokens`（单次调用上限）= **120000**，用于 `token_budget` 质量闸；
  telemetry 需补 `max_prompt_tokens_per_call`（按 span 取 MAX，不是 SUM）。
- 新增 `max_total_prompt_tokens`（整单累计）= **400000**，只进告警/抽屉，不打回。
- 预算文案改成「单次上下文上限」，避免下一个人再按整单理解。

### 3.4 step-3.7-flash 空 content：**上游瞬态，换模型优先，不做无限重试**

依据：

- 错误文本 `EMPTY_RESPONSE: DeepSeek Messages returned no content` 来自 DSH 的
  重试分类表（`~/.dsh/storages/.../session-*.json` 里有
  `["EMPTY_RESPONSE","RATE_LIMIT","SERVER","TIMEOUT","TRANSPORT"]`），不是 durable
  适配器造的；`maxTokens=8000` 解决的是 `finish_reason: length` 那一类，**不是这一类**。
- 27/73 的派单撞上它，比例不低；它不是「偶发到可以忽略」。
- 同一提示词下 `step-5-preview` 已实测一次交差（1188 token，
  `PI_DURABLE_MIGRATION.md:272`）。

**建议动作**（按优先级）：
1. durable 执行体的 `LLM_MODEL` 换 `step-5-preview`（或等价的非 reasoning 小模型），
   先消掉这一类失败；
2. 若必须用 3.7-flash：在 `_run_adapter` 对 `EMPTY_RESPONSE` 做**一次**自动重试，
   `attempt+1`（拿新单），不无限重试、不跨错误类型；
3. 不建议再调大 `max_tokens`（已 8000，空 content 与预算无关）；
4. 把该错误分类写进 telemetry 的 `status_code/error`，否则抽屉里只看到「没交差」。

---

## 附：本轮改动清单

| 文件 | 改动 |
|---|---|
| `server/assistant/adapters/durable.py` | docstring 幂等键改正；`_endpoint_wait()` 有界等待重起 |
| `server/assistant/executive.py` | 对账带 attempt；审批放行取活端点；绑定继承 workspace_root；热重载保留运行时绑定 |
| `server/app.py` | `_durable_lookup` 带 attempt query |
| `server/assistant/durable_supervisor.py` | 清除 `XIAOXI_APPROVED_TOOLS`；`current_endpoint()` 检查进程存活 |
| `agent-runtime/src/takeover.ts` | exit 0 无交差分支带 stderr |
| `agent-runtime/test-takeover.ts` | 新增该分支断言（17→18） |
| `scripts/test_durable_adapter.py` | 新增 attempt 回归 + switch 继承 workspace + 热重载保留绑定（20→28） |
| `scripts/probe_takeover_production.py` | ② 早退记 False；③ 重跑进断言；finish 要求 9 条 |
| `.agents/skills/pstack/docs/harness/claude-code.md` | 补死链指针（本机，`.agents/` 被忽略） |

**一句话**：四个断点的代码都已落地，但真正卡住硬杀恢复的不是锁、是重启窗口里旧端点
仍被当成活的（已补）；对账还必须带 attempt，否则重做过的单重启后白干（已补）。
生产仍不该切 durable，先把试点单和预算口径定下来。
