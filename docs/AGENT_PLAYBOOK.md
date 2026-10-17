# AGENT PLAYBOOK — Multi-Agent Collaboration Charter

**Status:** active from phase 2 (2026-10-14, after `v0.5-phase1-freeze`)
**Owner:** Role 0 (Chief Architect) — the only role allowed to amend this file and `docs/PROTOCOL.md`.

## Purpose

Phase 2 runs as three parallel agents over one shared checkout. This charter is the
contract that keeps them from colliding: every agent owns an exclusive file
whitelist, every cross-module change goes through `docs/PROTOCOL.md`, and every
change is verified before it is called done.

## 工程纪律：防臃肿与重构克制协议（最高优先级，先于本宪章其余条款）

Role 0 做架构设计与向下派单时、所有执行角色实施时，必须遵守：

1. **优先复用现有架构（Reuse First）**：引入任何新机制前，必须先全面盘点当前代码库（如 `server/interaction/threads.py`、`server/assistant/loop.py`、`server/dialogue/orchestrator.py`、既有 drivers 与前端组件）。禁止重复造轮子——现有模块能通过轻量扩展承载的需求，严禁另起炉灶写平行替代模块。
2. **先诊断、后微创（Diagnose Before Surgery）**：发现旧实现存在缺陷或不满足新需求时，绝不允许直接推倒重写或进行大范围无关联重构。必须先在方案中明确指出：① 旧实现的问题所在；② 它的负面影响；③ 保持向后兼容的**最小改动方案（Minimal-Patch Solution）**。
3. **严禁无限叠加补丁（Anti-Patch-Creep）**：如果发现某个逻辑已经叠了多层临时补丁，必须在本次设计中主动发起一次局部的"外科手术式清理"（用优雅的规范替代历史补丁），而不是继续在上面堆砌新的 `if-else`。
4. **行为变化必带回归验证（Test-Driven Safety）**：任何涉及业务行为、状态流转或接口契约的修改，必须在任务单中硬性绑定对应的自动化测试用例（Python 或 Node 单测）。验收标准硬门槛：`scripts/check_protocol.py` 与全套测试套件必须 100% 绿灯通过，方可宣告完成。

### Role 0 派单自检清单（每个任务单必须包含）

- 现有资产盘点结论：要复用/扩展哪些现有模块，为什么不需要新模块。
- 若确需新模块：先诊断后微创三件套（旧实现问题、负面影响、最小改动方案）。
- 绑定的测试用例清单与验收命令（对应 `docs/TESTING.md` 黄金命令 + 新增用例）。
- 验收门槛明示：全套绿灯才算完成。

## Global Rules (all agents must obey)

1. **Protocol First.** Any cross-module or frontend/backend change must first read
   and follow `docs/PROTOCOL.md`. Inventing unregistered WebSocket events or API
   fields is forbidden. If the protocol does not cover what you need, stop and
   emit a `[PROTOCOL_REQUEST]` to Role 0 instead of guessing.
2. **Strict File Ownership.** Never modify files outside your own scope. If a task
   seems to require an out-of-scope edit, stop the crossing operation and emit
   `[PROTOCOL_REQUEST]` with the exact file, the reason, and the proposed change.
   Role 0 decides: amend the scope, or Role 0 makes the edit directly.
3. **No Orphan HTML.** Do not create standalone preview `.html` pages under `web/`.
   All new UI must be integrated as a view or component of the existing main
   workbench (`web/index.html` + `web/app.js`).
4. **Verification Before Done.** After finishing a task you must run
   `python scripts/check_protocol.py` (use `.\.venv\Scripts\python.exe` on this
   machine) plus your domain's test scripts, and report zero regressions. A task
   without a passing verification run is not done.

## Roles & Scopes (file read/write whitelists)

### Role 0 — Chief Architect
- **Owns:** `docs/PROTOCOL.md`, `docs/AGENT_PLAYBOOK.md`, `scripts/check_protocol.py`, `server/app.py`, `web/index.html`, `web/app.js`, `AGENTS.md`, `REVIEW.md`, `evals/`, `scripts/run_evals.py`
- **Duties:** protocol design and amendments, cross-role conflict arbitration,
  `[PROTOCOL_REQUEST]` triage, integration merges, release tagging.
- **May read:** everything. **May write:** only the owned list above (plus
  housekeeping docs such as `docs/PARKED_TASKS.md`).

### Role 1 — Memory & Self-Evolution
- **Owns:** `server/memory/`, `server/persona.py`, `server/assistant/loop.py`, `config/memory.yaml`, `config/persona.yaml`
- **Duties:** episodic diary synthesis ("小溪日记"), attitude drift / latent sparks
  injection into `persona.py`, autonomous idle self-evolution loop (Self-Taste /
  Worldview), star-map graph data queries.

### Role 2 — Speech, Pacing & Neuro-Delivery
- **Owns:** `server/dialogue/`, `server/speech/`, `config/pacing.yaml`, `web/avatar/timeline.js`
- **Duties:** long-reply micro-chunking, lookahead TTS queue, breath / micro-expression
  pause markers, duplex interruption and queue cleanup.

### Role 3 — Star-Map & Companion UI
- **Owns:** `web/` (excluding `web/avatar/drivers/`), `server/interaction/`
- **Duties:** star-map data bridge, frontend star-map visualization (node roaming,
  keyword search, click-to-narrate), integration as a built-in workbench view.
- **Boundary:** `web/avatar/drivers/` belongs to nobody in phase 2 — treat it as
  read-only and raise `[PROTOCOL_REQUEST]` if you believe it must change.

## Cross-role contracts

- New WebSocket events, API routes, and payload schemas are defined **only** in
  `docs/PROTOCOL.md`. `scripts/check_protocol.py` is the mechanical gate: if your
  change adds or renames an event, tell Role 0 so the checker and protocol stay in
  sync.
- Shared frontend state (workbench views, playback UI) is coordinated through
  `web/app.js`; Role 3 proposes, Role 0 merges.
- If two roles need the same file, the earlier committed task keeps ownership and
  the later one goes through Role 0.

## 打磨轮临时授权（2026-10-14 起，至 v0.7 打标止）

- **Role 2 临时获得 `web/avatar/**` 全量写权限**（含 `drivers/`、`render/`、`timeline.js`、`index.js`、测试），以及 `web/voice/playback.js` 与其测试 `web/voice/playback.test.mjs`（微句段间停顿调度的权威位置）、`config/avatar.yaml`（舞台打光与材质参数）。`web/avatar/drivers/` 平时的"无主"状态在此期间归 Role 2。`web/voice/` 其余文件仍归 Role 3。
- **Role 3 临时获得 `web/app.js` 与 `web/index.html` 写权限**，用于界面大瘦身与胶囊输入框重构；提交前必须经 Role 0 做 Integration Check（检查点：协议字段读取不漂移、既有 WS case 不回归、check_protocol 第四类通过）。Role 0 已于打磨启动前在 app.js 落入主动搭话心跳与两个默认开关，Role 3 从该状态基础上改。
- 临时授权到期或打标 `v0.7` 后自动收回，白名单恢复本文上方正文。越权编写按 Global Rules 第 2 条处理。

## 协作流程（三个仪式，缺一不可）

### 1. 开工前协议红线确认（Protocol Red-Line Check）

派单后、写第一行业务代码前，执行 Agent 必须在自己的第一条回复里回答：

- 现有 `docs/PROTOCOL.md` 契约（对应章节列在派单 Prompt 里）对本次任务**够不够用**？
- 如果需要新字段 / 新事件 / 新路由，逐个写出来，并说明为什么现有契约装不下。

结论只有两种：**契约够用**（附上你将用到的字段清单）或 **需要补充**（输出
`[PROTOCOL_REQUEST]` 并停工等 Role 0）。Role 0 未确认前不动业务代码。这一步拦
 掉绝大多数"前端捏造字段、后端另起炉灶"的接口不匹配。

### 2. 局部集成前的对齐演练（Integration Check）

完成后**不直接提交**。把对接面交给 Role 0 与对方角色对一遍，字段名、类型、
取值集合全部对上才允许合并：

| 对接面 | 产出方 | 消费方 | 核对内容 |
|---|---|---|---|
| `bias_block()` 签名与返回 | Role 1 | Role 2 | 空输入行为、上限、边界包裹 |
| `/api/memory/starmap` 请求/响应 | Role 1 | Role 3 | query 参数、nodes/edges 字段、404/空图行为 |
| `audio.chunk` 字段与 narrate 链路 | Role 2 | Role 3 | seq/index 单调、打断后零 audio、`narrate_busy` |

**收敛规则**：同一任务单审查满 3 轮，停止逐行返工，由 Role 0 重新评估方案；第 2 轮起只审新增 diff（详见 `REVIEW.md`）。

### 3. 里程碑打标（Milestone Tagging）

每个 F 目标测试全绿并通过集成对齐后，Role 0 **立即**打小 tag（`v0.6-phase2-diary`
/ `v0.6-phase2-speech-chunking` / `v0.6-phase2-self-evolution` / `v0.6-phase2-starmap`），
并在 `docs/TASKS.md` 勾选对应条目。演进必须是阶梯式上升，不是混沌式叠加。

## Definition of done (every role)

1. Code complete within owned files only.
2. `python scripts/check_protocol.py` passes (via `.venv`).
3. Domain tests pass (see per-role prompts for exact commands).
4. Protocol-relevant changes are reflected in `docs/PROTOCOL.md` **by Role 0**
   before merge.
5. Commit message references the phase-2 feature id (F1 diary / F2 long voice /
   F3 self-evolution / F4 star-map).
6. 开工前完成了协议红线确认，结论记录在汇报里。
7. `docs/TESTING.md` 里属于自己角色的黄金命令全部跑通，结果附在汇报里。
8. 对接面 payload / 类型签名写进汇报，供 Role 0 做 Integration Check。
9. 状态变更（进行中 / 完成 / 阻塞）报告给 Role 0，由 Role 0 更新 `docs/TASKS.md`。
10. 涉及记忆装配 / Prompt / 模型切换的改动，必须跑离线评测（`scripts\run_evals.py`）；换模型或改 Prompt 装配逻辑还需手动跑一次 live 评测（`--live`）并附结果。

