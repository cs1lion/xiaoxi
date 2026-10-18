# 第二轮功能实测与优化（2026-10-17 晚）

第一轮 [功能实测](FEATURE_TESTS_20261017.md) 把清单跑了一遍，本轮换一种挑法：
只挑**判据可能没有真正上场**的功能。验证方式不是“再跑一遍已有测试”，而是
先证明判据会失败、再修到它失败不了。

级别沿用 [Phase A Frame](PHASE_A_FRAME.md)：R1 = 离线确定性；R2 = 本机真实依赖跑一次；
R3 = 多次重复、按题族统计。本轮最多到 R2，个别到 R3-lite（同一套件完整跑两次）。

## 1. 实测矩阵

| # | 功能 | 命令 | 级别 | 修前 | 修后 / 结论 |
|---|---|---|---|---|---|
| 1 | 情绪标签多轮保留（live） | `run_evals.py --live --case emotion-tag-retention-live` | R2 | 2/3 轮通过；run2 第 4、6 轮空正文 | **12 次运行 / 72 轮全绿**；0 空正文、0 缺标签 |
| 2 | 真实 Worker 六例套件 | `python scripts\probe_worker_suite.py` | R2/R3-lite | 文档只记录 5 例 | **6/6 两次**（230340：224s / 191,556 tokens；232051：540s / 191,885 tokens），含中文、转换与负例 |
| 3 | 跨天陪伴三天验收 | `python scripts\test_cross_day_companion.py` | R1 | **11 failures + 1 error**（判据从提交起就没绿过） | **10/10 通过** |
| 4 | 计划层接真实模型 | `tmp/probe_plan_live.py`、`tmp/probe_plan_e2e.py` | R2 | 复杂目标 1600 预算下 3/3 次 `finish_reason=length`、正文 0 字，计划静默缺席 | 3200 预算下产出 3 步串行计划；简单问候被平凡计划守卫拒绝；真实派单跑完 `accepted` |
| 5 | 自进化质量报告 | `python scripts\eval_self_evolution_quality.py` | R1 | Windows GBK 控制台直接 `UnicodeEncodeError` | 反例捕获 **5/5**；6 轮纵向：子种子 6、边 36、平均相似度 0.103、注意力分散 6/6 |
| 6 | 一键启动（D-17 干净重启） | `python scripts\run.py --port 8011` | R2 | 只验证过“已实现” | 复用已运行网关、应用起在 8011、`/` 与 `/static/app.js` 均 200、退出后网关仍在 |
| 7 | 空检索负向约束（live） | `run_evals.py --live --case hallu-empty-retrieval-live` | R2 | 3/3 | 3/3，两次运行共 18 轮均未编造共同经历 |
| 8 | 事实优先于人设（live） | `run_evals.py --live --case accuracy-over-persona-live` | R2 | — | 3/3；判据已从正则改为去标签后的 custom 判定 |
| 9 | 网关契约 | `python scripts\test_anthropic_gateway.py` | R1 | 30 项 | **72 项全绿**（本轮期间测试集扩充，未改生产协议） |
| 10 | 线程对话契约 | `python scripts\test_thread_conversation.py` | R1 | — | 24/24；在改路由后仍全绿 |
| 11 | 任务计划纯逻辑 | `python scripts\test_task_planning.py` | R1 | 43 项 | **44 项**（新增平凡计划守卫用例） |
| 12 | 全局门禁 | `check_protocol.py` + `test_core.py` + `run_evals.py` + `npm test` | R1 | — | PASS + **40** + **5** + exit 0 |

## 2. 根因与改动

### 2.1 空回复不是“忘了情绪标签”，是推理截断

**证据**：空回复那一轮的原始元数据是 `finish_reason=length`、`chars=0`，
`completion_tokens` 恰好等于预算（900），`reasoning` 有 1332 字。回复不是“模型
不想说话”，是推理把预算吃光了。继续追问计划层时同一现象更明显：1600 预算下
连续 3 次空正文，`reasoning` 3400–3700 字。

**改动**：

- `server/config.py`：`llm_reply_tokens` 默认 900 → 1200；`.env.example` 同步。
  18 轮对照：900 下 1 次空正文，1200 下 0 次；1200 仍偶发（约 1/18），所以救援不能省。
- `server/dialogue/orchestrator.py`：
  - 截断救援改用 `complete_raw` + `reasoning_effort="minimal"` + 600 tokens。
    原实现用默认推理强度 + 512 tokens，第一轮已经证明推理能吃掉 1200，512 必然不够。
  - 救援后仍为空时返回中性兜底句「嗯，我在呢。」，不让用户听到静音；不改情绪判定。
  - 情绪指令补一句：`[motion:…]` 不能代替 `[情绪]`。实测出现过只写 motion 不写标签的轮次。
- `scripts/run_evals.py`：live 层改用 `complete_raw`，落盘 `finish_reason / rescued /
  completion_tokens`；截断时按生产同款救援再要一次短回复。
- `evals/checks.py`：空正文与“缺标签”分开报错，避免把截断误诊成指令遗忘。
- 计划层：`_plan` 预算 1200 → 3200，改用 `complete_json`（自带两次解析重试），
  失败时返回“无计划”而不是把 JSON 异常抛进语音轮次。

**效果**：修后 12 次 live 运行 / 72 轮，空正文 0、缺标签 0；生产 `Orchestrator.respond`
18 轮无空回复；复杂计划从“静默缺席”变成 3 步串行计划。

### 2.2 跨天陪伴的判据从提交起就没绿过

`test_cross_day_companion.py` 在 `733b891` 随功能一起提交，但同一提交没有让实现满足它。
本轮首次实跑是 11 failures + 1 error。原因有五处，都是“说得对但没接上”：

1. **接续说法太窄**：只认「继续/接着 + 几个固定宾语」。「接着昨天的继续」「继续昨天那件事」
   「我们继续吧」「继续推进」都走不到共同事项。
2. **状态快照过期**：上一轮确认过的决定不在缓存摘要里，下一句「把 X 改成 Y」校验失败，
   被回成“你指的是哪条内容”。
3. **新会话里的修正式说法不路由**：「改步行」在已选事项时仍被当成闲聊。
4. **进展洪水淹没决定**：恢复摘要取“最后 4 条 user_stated”，20 条进展就能把决定和
   未决问题挤出摘要，用户听到一个没有决定的事项。
5. **摘要不说版本**：事项钉的稿子版本与稿子当前版本不一致时，恢复摘要不提示。

**改动**（`server/interaction/thread_conversation.py` + `server/assistant/service.py`）：

- 扩充接续/修正词法门，并加引用、转述、假设、否定的护栏（`“把刚才那个记下来”`
  不路由）；「下一步做什么」是提问，不劫走。
- `follows` 时从存储重读当前事项，不再拿上一轮的摘要做校验；确认导致候选消失时
  丢掉过期 focus 继续处理，而不是拒绝整轮；候选版本被界面更新时仍明确提示“已更新”。
- 无显式选择时，只有一个事项才自动恢复，多个候选仍让用户选。
- 恢复摘要优先保留决定/问题，进展只取最近 2 条；有 `note_lookup` 时说明
  「关联稿当前版本 N；事项钉的是版本 M，没有自动切过去」。
- 接续结果附带 `activity` 快照，口述与卡片看到同一份版本信息。

**测试侧的两处修正**（不是放宽判据）：`_run_one_turn` 原先把 `_turn_context` 的
外层返回当成内层数据，导致 `KeyError: 'thread'`；测试也没有像 `app.py` 那样把全局
orchestrator 接到测试服务上。两处都按生产接线修正。

**效果**：10/10 通过，且 `test_thread_conversation.py` 24/24 无回归。

### 2.3 复杂计划静默缺席

计划层的 prompt 很长，还要带工具目录，推理长度显著高于普通对话。1600 预算下
复杂目标连续 3 次返回空正文，`AgentLoop._build_plan` 捕获异常后退回自由循环——
表面上“计划失败不阻塞任务”，实际是**计划功能在真正需要它的目标上从未上场**。

**改动**：

- 预算 3200，并用 `complete_json` 的解析重试。
- 平凡计划守卫补一条：单步、无执行器、验收条件只有 `{"status":"succeeded"}` 时
  仍视为无计划。实测「在吗」会拿到这样一份“计划”，不加这条会把问候变成计划驱动。

**证据**：

- 复杂目标：3 步串行（`note.search` → `task.create` → `reminder.create`），依赖链正确。
- 真实派单：`task.create` 走确认门，批准后步骤 `accepted`，计划 `complete=true`。
- 发现的边界：`task.create` 没有 `due_at` 参数，「明天下午三点」只能进
  `description`。这是产品契约缺口，本轮不改 schema，记录在未验证项。

### 2.4 Windows 控制台把评测报告打断

`eval_self_evolution_quality.py` 打印 `✓/✗`，Windows GBK 控制台直接
`UnicodeEncodeError`，报告跑不完。改为在 `main()` 里把 stdout 钉成 UTF-8
（与 `probe_worker_suite.py` 同款），文件编码不动。

### 2.5 文档与实现漂移

- `REAL_WORKER_PROBE.md` 仍写“5 条套件”“初审打回不会自动重试”；当前是 6 条，
  `delegate` 已按 `MAX_RETRY` 有界自动重试。本轮按实跑结果更新。
- `TESTING.md` 把 live 情绪失败写成“多轮指令遗忘”；实际根因是推理截断。本轮更正，
  并写明修复证据与抽样口径。

## 3. 没有做 / 仍未验证

| 项 | 状态 | 还缺什么 |
|---|---|---|
| Worker 稳定性（R3） | 只到 R3-lite | 同一套件完整跑两次 6/6，但没有 10 条题族、没有按 Agent 的通过率/耗时分布 |
| 生产 Worker 隔离 | 仍未解决 | `danger-full-access` 的风险没有变化；WSL / sandbox / 专用用户 / 只产出文本，四选一 |
| 任务的结构化时点 | 未验证 | `task.create` 不接受 `due_at`；「明天下午三点」落在 description，需决定是扩 schema 还是强制走 `reminder.create` |
| 自进化语义质量（live） | 未实装 | `eval_self_evolution_quality.py --live` 仍是占位；离线只证明闸门与统计量 |
| 真实麦克风/扬声器 | 未验证 | 耳机与外放两套设备、中文流式 ASR、打断/误打断 |
| 长对话视觉 | 未验证 | 长历史、真实语音播放中的截图与可读性 |
| 计划失败/恢复的真实模型路径 | 未验证 | 脚本化测试覆盖状态机；真实模型的失败分类与用户决策话术未跑 |
| 情绪标签 72 轮 ≠ 稳定 | R2 抽样 | 12 次运行未覆盖不同模型/温度；换模型需重跑 live |

## 4. 证据索引

- 情绪：`evals/results/20261017T15*.json`（含 `turn_meta`）、`server/config.py:111`、
  `server/dialogue/orchestrator.py:970-995,1470-1495`
- Worker 套件：`tmp/worker-suite-20261017-230340/summary.json`、
  `tmp/worker-suite-20261017-232051/summary.json`
- 跨天：`scripts/test_cross_day_companion.py`、`server/interaction/thread_conversation.py`
- 计划：`server/assistant/planning.py`、`server/assistant/loop.py:548-566`、
  `server/assistant/service.py:430-470`、`tmp/probe_plan_live.py`、`tmp/probe_plan_e2e.py`
- 自进化：`scripts/eval_self_evolution_quality.py`
- 启动：`scripts/run.py`
- 网关：`scripts/test_anthropic_gateway.py`、`docs/GATEWAY_DESIGN.md`
- 全局门禁：`check_protocol.py`、`test_core.py`、`run_evals.py`、`npm test`
