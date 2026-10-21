# 小溪 (Xiaoxi)

个人中文语音 Agent：台前语音快环 (System 1) + 幕后 Agent 慢环 (System 2)。

## 开工必读
- 多 Agent 协作：先读 `docs/AGENT_PLAYBOOK.md`（角色白名单、红线确认、完工标准）
- 接口契约：`docs/PROTOCOL.md`（不得自造 WS 事件 / API 字段）
- 测试命令：`docs/TESTING.md`

## 验证（改完必须跑，全绿才算完成）
- `python scripts/check_protocol.py`
- `python scripts/test_core.py`（离线，无需 API Key）
- `npm test`
- 评测：`.\.venv\Scripts\python.exe scripts\run_evals.py`（离线层，纳入黄金命令）；live 评测需要 LLM_API_KEY，用 `--live` 手动跑，换模型或改 Prompt 装配逻辑时必须跑一次
- 无 API Key 时自动进入 Mock 模式，可跑通全链路

## 不可违反的设计约束
- 性格与事实准确性冲突时，准确性优先
- 记忆检索按三档置信度装配；检索为空必须显式注入负向约束，禁止用常识脑补共同经历
- 记忆删除是 SQLite 物理硬删除，不留索引
- 快环（语音流）内不得出现阻塞调用，重活一律异步卸载到慢环
- 新增 Worker 只改 `config/agents_roster.yaml` 与适配器，调度核心零改动
- 每轮 Assistant 示例都带合法 `[情绪]` 标签

## 非目标
- 不追求在级联管线下压进 500ms 首字延迟

## 全局默认：poteto-mode（A 档 · 自主性全开）

所有 Bot 默认按 poteto-mode 工作。开工前按序读：

1. `.agents/skills/pstack/DSH.md`（工具名映射，它覆盖一切写错的工具名）
2. `.agents/skills/pstack/poteto-mode/SKILL.md`（正文）
3. 命中的 playbook 与 principle 叶子文件，按路径直读（它们不在 skill 目录里列名）

**路径必须是绝对路径 `D:\project\neurosama\.agents\...`。** `.agents/` 在
`.gitignore:55`，不会进 worktree；在 `tmp/wt-*` 里用相对路径会指向不存在的目录。
worktree 里的 `AGENTS.md` 也可能落后（取决于它检出的提交），规矩以仓库根这份为准。

**自主性档位：A（全开）**。原文「Just do it」「Never Block on the Human」生效：
可逆工作、团队消息、工单更新、跑评测，不问直接做，做完报告。

**仍然要停下的**（不可逆，原文 Always-pause 列表）：强推共享分支、部署、删数据、给客户发消息。
**项目叠加的额外红线**（比 poteto 更严，项目优先）：改 `server/assistant/executive.py`、加调色板颜色（须 ADR）、
重启用户正在用的 8000/8010。这三条即使可逆也要先问。

**已知残缺**（DSH.md 已认 + 实测补充）：

- 无 per-subagent 模型选择（不要指定 grok / claude 模型）
- **Graphite 不是阻塞项**（2026-10-21 实测更正）：四个相关 playbook 里 `gt` 的直接调用 0 处，
  本机无 `gt` 也不影响。`gh stack` 是未安装的 `gh` 扩展，同样无人调用。
  **不要标 `skip: no Graphite in DSH`** —— 这条理由一度写进本文件和 DSH.md，是错的。
- **无 `deslop` / `create-skill` / `control-cli` / `control-ui` 四个 skill**
  （poteto 要求 commit 前跑 deslop、agent 文案跟 create-skill，这两步只能标
  `skip: skill not installed in DSH`）
- **`bun` 未安装**，但两个 playbook 的阻塞点不同，不要混：
  `orchestrate` 靠 `bun`（`orch/orch.ts` 与 `watch-pr/watch-pr` 都是 `#!/usr/bin/env bun`），
  标 `skip: no bun in DSH`；`autopilot-full` / `autopilot-stack` **完全不碰 bun**
  （四个 playbook 里 bun 提及 0 次），它们卡在第 2 步要 Cursor cloud agent，标 `skip: no cloud agents in DSH`。
  `babysit` 的 GitHub 路径因 `watch-pr` 是 bun 脚本而跑不了。
  `multi-phase-plan` 第 6 步能跑（`check-plan.mjs` 走 node）。
- `claude-code.md` 的**存在与可解析是两件事**：文件在 `.agents/skills/pstack/docs/harness/claude-code.md`
  （2026-10-21 实测 **2044 字节 / 25 行**，指回 DSH.md 的指针），但**21 处叶子引用全部是死链**。
  **严格口径：live 0 / dead 21** —— 21 处全是相对路径形式的死链，0 处可解析。
  （`DSH.md` 里提到该文件名的两处是散文，不是链接：`:36` 拿文件名当主语、`:37` 说"用 grep 搜这个词"。
  把「出现了文件名」算成「活的链接」，会让任何提及都被计数。）
  叶子写 `../../docs/harness/claude-code.md`，从 `.agents/skills/pstack/<leaf>/` 出发会解析成
  `.agents/skills/docs/harness/claude-code.md`，多退了一层；实际只需 `../docs/...`。
  修法两条都碰 bundle 布局（挪文件或改 21 个文件），而 `DSH.md:10` 要求 bundle 只读，所以**不修**。
  **DSH.md 整体替代它，不依赖这些链接。**
  （判定这类链接必须用真实路径解析：手写正则很容易把 `../../` 里的一个 `../` 吃掉，
  于是每处都被解析成 `../docs/...` 而“存在”——**一个 bug 的校验器给出的「全 OK」，比「没找到」更危险**。）
- `/loop` 是 Cursor 内置、盘上无 skill，但 `schedule_create` 的 `every_seconds` 能覆盖。

依赖这些的步骤标 `skip: <reason>`，不要静默跳过，也不要为了跑通而去装。

**停下优先级冲突**：`docs/AGENT_PLAYBOOK.md` §「开工前协议红线确认」要求新增字段时
输出 `[PROTOCOL_REQUEST]` 并停工等 Role 0。这是规范的一部分，**poteto 的
「Never Block on the Human」在此不生效**。同理 PROTOCOL.md 的不得自造 WS 事件/字段、以及上面三条项目红线，都照旧拦。

**与项目既有规矩的关系**：poteto 只管「怎么做」；本文件上面的验证命令、设计约束、协议红线不因它放宽。
冲突时以本文件其余部分为准。

## 修改本文件
仅 Role 0 可改。保持在一页内，细节放进 docs/ 并从这里指过去。
