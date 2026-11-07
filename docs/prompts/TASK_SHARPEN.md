# 任务开工模板 · sharpen 三视角 + 客观验收条件

日期 2026-11-06 · 基线 HEAD `41734eb` · 一手依据：
`.agents/skills/pstack/sharpen/SKILL.md:7`（全文 7 行，唯一一段正文）、
`why/SKILL.md:41-47,49,62,74`、`how/SKILL.md:13,15-26,38`、
`docs/HANDOFF_20261105.md:76-78`（项目红线）。

**性质**：这是仓库内的 prompt 模板，**不是硬约束，也没有机制保证会话自动加载它**
（`scripts/test_poteto_loading.py` 只验路径与入库状态，不验「会话真的读进上下文」）。
真正强制的是第 4 节的机器门；本模板只是让开工前该想的事有个固定落点。
按 `correct` 的优先级——单一标准写法 > linter 接 CI > 写进 markdown 是最后手段——
本文件属于最后手段，不许把它当成「已经防住了」。

---

## 1. 三视角（开工前必答，写进任务卡）

对齐 `sharpen/SKILL.md:7` 原文，不臆造档位体系（上游正文没有 A/B/C 三档，
那是 `AGENTS.md:28,40` 的本地命名）：

| 视角 | 要回答的问题 | 原文锚点 |
|---|---|---|
| ① 顶尖老手 | 这个领域的老手接到这活，一定会做但我会漏掉哪几步？他们的**一手资料**（raw sources）、**真实方法**（real methods）、以及**外部校验**（outside check）分别是什么？ | `:7` |
| ② 用户真正要什么 | 字面需求之外，真正想要的结果是什么？哪些选择属于用户、我不该替他拍？ | `:7` |
| ③ 规格过定 / 欠定 | 哪些约束其实不服务目标（该松开），哪些缺失的约束必须补上？哪些是用户该自己决定的、保持开放？ | `:7` |

**两条硬留**（`:7` 末句，别在裁剪时丢掉）：

- **始终保留一个 outside check that the result is right** —— 允许删掉让工作变大的东西，不允许删掉「客观证明做对了」这一条。
- **Don't hand back a rewritten prompt** —— 不要把「改写后的 prompt」当交付物交还；三视角是想清楚后直接干活，不是把活推回去。

## 2. 第一性原理：只看一手资料

判断依据**只能是**源码 `file:line`、提交号、工单原文、协议原文，不引用被消化过的二手总结
（含本项目自己写的 HANDOFF / 交接文档——它们只能用来指路，不能用来当证据）。

可用证据源（本机实测，2026-11-06）：

| 源 | 取法 | 状态 |
|---|---|---|
| Source control | `git log` / `git show`，提交信息里 37 条带 `issue #N` | ✅ 唯一保证可用（`why/SKILL.md:49,62`） |
| Issue-ticket | GitHub issues via `gh`（`AGENTS.md:149-151`、`docs/agents/issue-tracker.md`） | ✅ |
| Long-form docs | `docs/` 下 6 份 ADR + `DECISION_TRAIL_*.md` | ✅ |

**不可用就标 gap，不假装查过**（`why/SKILL.md:74` 原口径：*"Flag it as a gap, not a choice"*）：
Real-time chat / Infra observability / Error-exception tracking / Product analytics warehouse
四类在本环境无 MCP，写 `not_measured: 无对应 MCP`，不要写成「已排除」。

## 3. 倒推外框（architect）

从「使用者未来会怎么用」倒推，先定系统外框与分工，再写细节。设计标准照抄
`architect/SKILL.md:17-21,35`：预设下一个接手的 AI **只看得到眼前一个文件、只会照抄最近的例子走最省力路径**，
所以好架构要满足「就算它无脑照抄，改出来对整个系统也还是对的」。
本项目的对应零件是 `scripts/check_protocol.py` 的 12 类静态接缝 +
`scripts/check_memory_contract.py` 的 AST 绑定——**先确认改动落在已有接缝里，再动手**。

## 4. 客观验收条件（每条任务必须填全，缺一条不许开工）

这是本模板唯一强制的部分。五条都能被机器判定，不接受「看起来没问题」：

| # | 判据 | 机器门 / 证据形态 |
|---|---|---|
| A | 每条事实主张带 `file:line` 或提交号 | 人工可读 + 决策 trail 的 `evidence` 字段 |
| B | **先红后绿**：先定死一条会红的测试，红灯出现才准写实现 | `scripts/check_probe.py` 的 `probe_self_check()` 四判据：破坏版必须红、还原后必须绿、还原必须逐字节复原、注入必须真的发生（`check_probe.py:34-36,104-113,146-157,169-177,185-190,220-238`）；对应门禁 `check_probe`、`process_trail_probe` |
| C | 改动范围可被机械判定 | `python scripts/check_staged_scope.py <声明路径…>` exit 0 |
| D | 全套回归不降 | `check_protocol.py` / `test_core.py` / `run_evals.py` / `npm test` 全绿 + `ci_test_shard.py --workers 4` 并集=全集且 0 失败 |
| E | 决策可追溯 | `docs/process/trail.jsonl` 每条记录都有 `evidence` **和** `verification`，由 `scripts/check_process_trail.py` 判红 |

**B 的意义是防作弊**：先写代码再补测试，AI 会生一个配合错误逻辑的假测试交差。
项目里已有的两处同类装置是 `scripts/test_evals_guards.py:532-549`（专杀「负向断言在空文本恒真」的真空绿）
与 `scripts/test_gate_coverage.py:96-135`（新增脚本不登记即红）。

## 5. 不许承诺的四件事（物理封死，2026-11-06 复查）

| 环节 | 封死原因 |
|---|---|
| 跨家族模型比稿 / 互审 / 决策表复核 | `DSH.md:16,30` 无 per-subagent 模型选择；`.env` 只有 StepFun 一个 provider，judge 同源 |
| 云端 10 个独立 AI 群测 + 自动合并 | `DSH.md:58` 无 cloud agent；单作者锁 + Always-pause |
| GUI 录屏当 CI 门禁 | `ci.yaml:9` 明令浏览器回归不进 CI；重启 8000/8010 是红线 |
| Notion / Slack / Sentry / 数仓证据源 | 无对应 MCP，按 `why/SKILL.md:74` 标 gap |

这四项只能写 `not_measured` + 人工复核，**不能造一个看起来跑过的假门禁**。

---

## 6. 任务卡（复制填空）

```
任务：
① 老手会补的步骤 / 一手资料 / outside check：
② 用户真正要的结果 / 属于用户的选择：
③ 规格判定（过定 / 欠定 / 该松开的约束）：
倒推外框（使用者怎么用 → 系统分工 → 落在哪个已有接缝）：
爆炸半径：python scripts/run_gates.py --risk
决策 trail：docs/process/trail.jsonl 追加一行（evidence + verification 必填）

验收 A：file:line / commit 证据清单：
验收 B：先红的测试 = ______；破坏方式 = ______；probe_self_check 结果 = ______
验收 C：check_staged_scope.py 声明路径 = ______
验收 D：黄金四命令 + ci_test_shard.py --workers 4 = ______
验收 E：check_process_trail.py = ______
```
