# 测试清理审计报告（2026-10-22）

> **性质**：只读审计。未修改、删除、重命名任何仓库文件，未执行任何 git 写操作。
> **范围**：`D:/project/neurosama`（master @ `969437d`）+ 11 个关联 worktree。
> **方法**：292 个测试文件全量盘点（`tmp/test-inventory.json`）→ 8 路并行定位（120 条原始结论）
> → 5 路交叉验证（107 条去重结论：74 confirmed / 33 partial / **0 refuted**）→ 2 路对抗复核
> （只杀"建议删除"的结论，9 条裁决：3 条 safe / 6 条 BLOCKED）→ 汇总。
> 每条结论的原始证据在 `.stepcode/workflows/runs/wf_c1339c83ac5b4eed/journal.jsonl`，
> 本报告附录 A 是全部 107 条的可读清单。

---

## 一、口径（所有数字可复算）

| 口径 | 数值 | 说明 |
| --- | --- | --- |
| 测试文件存量（严口径） | **292** | `tmp/test-inventory.json`：.py 133 / .ts 47 / .js·.mjs 110 / 夹具 2 |
| 同上（宽口径，含 `inspect_extract.py` 等误命中） | 303 | worktree 对比用宽口径，两边一致 |
| 被 `run_gates.py` 按文件名引用 | 54 | 其余 238 不在本地 60 道门禁里 |
| `entries` 为空（任何运行入口都不按文件名引用） | **159** | 拆解见下表 |
| ├ `scripts/test_*.py` | 76 | **被 `ci.yaml:73` 的 `for f in scripts/test_*.py` 通配覆盖**，CI 每次 push 都跑 |
| ├ `pixel-world/test/` | 22 | 被 `pixel-world.yml` → `test_pixel_world.py` → `npm run test:inprocess`（`node --test test/*.test.ts`）通配覆盖 |
| ├ `scripts/*.mjs` | 36 | 浏览器/手工探针，**CI、npm test、run_gates 都不跑** |
| ├ `web/` | 24 | 其中 19 个是 `web/avatar/test_*` 家族，**零入口** |
| └ 根目录 `test_integration.js` | 1 | 零入口 |
| 审计结论 | 107 条 | coverage-gap 40 / orphan-never-run 21 / duplicate-overlap 16 / always-skip 10 / weak-assertions 8 / worktree-duplicate 8 / junk-temp 3 / dead-target 1 |

**关键澄清：「不在 run_gates.py」≠「没人跑」。** CI 通配兜底全部 `scripts/test_*.py`，
pixel-world 通配兜底全部 `pixel-world/test/*.test.ts`，根 `npm test`（`package.json:5`）是另一条
显式文件链。本报告的 coverage-gap 指**本地 60 道门禁看不见**，不是 CI 也不跑。
另一个真空白：`npm test` 不含 `test:eyes`，所以 `web/avatar/test_eye_params/expression` 只在
手动 `test:eyes` 里跑，`test_eye_morph/lids` 在 CI 因 `web/models/`（gitignore）必跳。

---

## 二、核心结论（3 条）

1. **几乎没有"该删"的测试。** 107 条结论里支持直接删除的只有 **2 个未跟踪垃圾文件**
   （`nul`、`scripts/.dialogue-regression-10136-eyes.mjs`，其中 `nul` 在报告定稿时已不在工作区，
   实际待删 1 个）。对抗复核把其余删除建议全部拦下：
   5 个 duplicate-overlap 不是重复而是**分层**（WS 协议信封 vs 服务状态机；store-only 鸭子类型
   router vs 完整 service；store 层 parser/expiry/capacity/concurrency vs HTTP 流）；
   6 个 always-skip 全部有 `docs/TESTING.md:55-58` 的"退役/自动恢复"契约兜底。
   唯一真正可执行的结构性动作是 **1 个 merge**，且删前必须先移植两处全仓独家覆盖（§3.4）。
2. **真正的债务是"暗跑"，不是重复。** 61 个测试（36 个 `scripts/*.mjs` + 24 个 `web/` + 1 个根目录）
   零运行入口；另有 10+ 个核心契约只在 CI 通配里跑、本地门禁看不见。其中确认的最有价值缺口：
   记忆抽取契约（25 tests）、preference 解析（28 tests）、投影纯度结构守卫（patch `__import__`
   拒绝一切非 `presentation` 的 `server.*` 导入 + 禁 `sqlite3.connect`）、procedural snapshot 的
   secret redaction 与 symlink 逃逸拒绝（安全相关）、agent-runtime 的 `test-write-file.ts`
   （安全边界）等 7 个测试——**它们 healthy、跑得快、却没有任何门禁兜底**。
   另有 `test_workbuddy_worker_shim.py`（34 tests）：Windows-only 夹具，Linux CI 永远 SKIP，
   又不在 run_gates——**WorkBuddy Worker 在当前 CI 下零覆盖**。
3. **always-skip 家族是休眠合约，不是垃圾。** 夹具缺失是设计好的退役状态，记录在
   `docs/TESTING.md:56`，夹具回来 harness 自动激活；目标模块全部存在。它们在 CI 里 <10ms skip，
   当前价值为 0——保留与否是产品决策，本报告只摆事实、不建议动。

---

## 三、明细

### 3.1 可删（审计时 2 个未跟踪垃圾，对抗复核 safe；其中 1 个已不在工作区）

| 文件 | 证据 |
| --- | --- |
| `nul`（根目录，63 B） | 审计时（2026-10-22 21:25）`git status` = `??`、`git clean -nd` 确认 "Would remove nul"、全仓 42 个 ref 无引用；内容是某次 shell 重定向报错。**报告定稿时该文件已不在工作区**（并行会话或用户已清理，非本审计所为）。若再出现：Windows 设备名文件，删除需用扩展路径 `\\?\D:\project\neurosama\nul` |
| `scripts/.dialogue-regression-10136-eyes.mjs`（12,986 B） | 未跟踪；文件名带 PID 后缀；实为 `test-dialogue-gaze-regressions.mjs` 跑飞时生成的运行时副本，零引用。**仍待删** |

### 3.2 待用户拍板（1 个，对抗复核 BLOCKED）

- `docs/CONTRACT_V2_SCOPE.md.chief-bak`（6,844 B，未跟踪）。字节数与现文档均对不上：
  它含"实施范围：仅 §0–§3.7 / 状态：实施中"等现文档已没有的内容，**部分数据只存在于此文件**。
  且 `docs/CONTRACT_V2_SCOPE_PENDING.md:7-8` 记载用户曾因 `.chief-bak` 被删而不满。
  **先问用户再动**，不要静默删除。

### 3.3 不动（1 个）

- `.claude/worktrees/confident-roentgen-3771a9/web/ui_nav.test.mjs`：对抗复核判"无引用"，
  但它是**别人 worktree 里未提交的在制文件**，删它等于销毁同事未提交的工作。属该 worktree 内部事务。

### 3.4 唯一可执行的合并（1 个，唯一涉及删除文件的一步）

`scripts/test_thread_continuity.py`（248 L，9 tests OK 4.4s）→ 并入 `scripts/test_cross_day_companion.py`
（11 tests OK 10.8s，已在 `run_gates.py:127` 门 `cross_day` + `ALWAYS_REQUIRED`）。

- 实证重复：harness fork 同源（continuity:53-61 ≡ cross_day:52-60；:80-83 ≡ :46-50；
  cross_day:44 自认 fork）；8000-cap :130 重复于 `test_thread_recall.py:231/:247`。
- **真实近重复约 3 个测试**（:85-130 / :132-156 / :200-208），不是原 finding 说的 6/9。
- **删前必须移植**（缺一不可）：
  1. :210-221 `_note_saved_receipt`——全仓唯一调用，`server/.../service.py:54/487` 靠它接线；
  2. :191-198 真实 `service._read_thread` 长文本/授权路径——其余 `scripts/` 引用全是 stub lambda
     （`test_durable_read_api.py:83`、`test_durable_orchestrator_api.py:57`、
     `test_durable_clarification_cap.py:74`、`test_durable_c3_api.py:96`、`test_durable_authorization_ui.py:145`）；
  3. 独家断言 :114-117、:128-129、:150、:155、:223-244（`numbered_event_ids`，
     `thread_conversation.py:320` 是唯一生产者）。
- 两边跑全绿之后才删 continuity。

### 3.5 建议接门禁 wire-into-gates（零风险纯增量，最可执行的一批）

**(a) 9 个 `scripts/test_*.py` 核心契约**（全部实测跑过，耗时已标）：

| 文件 | 实测 | 价值 |
| --- | --- | --- |
| `test_soul_polish.py` | 9 OK 0.077s | persona/diary/preference 三模块接线 |
| `test_memory_extraction_contract.py` | 25 OK 0.103s | 记忆准确性核心契约（`is_direct_user_evidence`/`canonical_topic`） |
| `test_memory_preference_parser.py` | 28 OK 0.003s | parser/admission 单测，与 e2e 的 `test_memory_preference.py` 分层 |
| `test_memory_presentation.py` | 17 OK 0.004s | **投影纯度结构守卫**（禁跨层 import / 禁裸 sqlite） |
| `test_curiosity_graph.py` | 10 OK 0.921s | starmap graph/node |
| `test_reflect_cycle.py` | 6 OK 0.951s | reflect/evolution 周期（与 soak  harness 近克隆，可观察一轮后再议合并） |
| `test_evolution_soak.py` | 6 OK 1.078s | 离线确定性快回归（名字有误导，不是一次性探针） |
| `test_procedural_memory_eval_snapshot.py` | 22 OK 5.6s | **secret redaction + symlink 逃逸拒绝（安全相关）** |
| `test_assistant_continuation.py` | — | RuntimeStore input-claim 跨两连接原子性**唯一覆盖**（:331-362） |

**(b) 19 个 `web/avatar/test_*` 整族暗跑**（`test_affect/intensity/lipsync/motion/render/hair_physics/
face_shading/speech_plan/ambient/body_poses/body_rig/finger_poses/finger_rig/gesture_poses/gesture_paths/
hand_poses/hand_rig/performance/pose_transition`）：目标全是活模块（如 `index.js:119 EmotionDriver`、
`:819 setAffect`、`render/vrm.js` 的 VrmRig、`drivers/pose-transition.js`）。其中 9 个被
`docs/BODY_COORDINATION.md`、`docs/HAND_POSES.md`、`docs/GESTURE_CALIBRATION.md`、`docs/FINGER_RELAXATION.md`
引用——**文档当它们在跑，其实没有任何入口**。建议合成一个 `test:avatar` 脚本挂进
`package.json:5` 的 test 链，而不是逐个列。删它们等于静默拆掉渲染/驱动层回归网。

**(c) 7 个 agent-runtime 测试不在 run_gates**（逐个实跑均绿）：`test-write-file.ts`（12 passed，
目录逃逸全拒，安全边界）、`test-handin.ts`（7 passed，Windows 双反斜杠规范化）、
`test-tier-model.ts`（9 passed，tier→model 接线）、`test-model-run.ts`（9 passed，
instructions 放置守卫）、`test-nudge.ts`（3 passed，催单救回）、`test-openai-tools.ts`
（5 passed，always-skip 的 `test-live-tools.ts` 的离线对偶——tools 透传目前零自动覆盖）、
`selftest.ts`（8 passed，只能手动 `npm run selftest`）。

**(d) 其他 4 个**：`pixel-world/scripts/check-s5-d.mjs`（CI 只跑 `--self-test`，默认路径 exit 2
没人验）、`pixel-world/scripts/smoke.mjs`（LEDGER 硬检查本地跑、CI 不跑）、
`web/ui_nav.test.mjs`（36 checks PASS，只在 TESTING.md:68 有手动行）、
`web/voice/editor-insight.test.mjs`（不在 `test:voice` 的 6 文件清单里）。

**(e) 1 个平台性缺口**：`test_workbuddy_worker_shim.py`——`:207-210` Windows-only `.cmd` 夹具，
Linux CI 必跳；又不在 run_gates（只有 codex_shim/claude_shim 在）。要么加 Windows runner，
要么在 TESTING.md 标注 local-only。

### 3.6 always-skip 休眠合约（10 条，建议保持现状）

| 文件 | 跳过机制 | 状态 |
| --- | --- | --- |
| `test_memory_eval.py` | :36-38 门 `memory_live_cases.json`（仓库无此夹具） | 实测 `Ran 0 tests OK (skipped=1)`；TESTING.md:55 已列 |
| `test_procedural_memory_eval.py` | :28-30 门 `procedural_memory_eval_cases.json` | 同上，TESTING.md:56 |
| `test_procedural_memory_induction.py` | :29-31 与 _eval 共用夹具 | TESTING.md:56（原 finding 称"未列入"有误，已更正） |
| `test_procedural_memory_source_projection.py` | :52-54 共用夹具 | TESTING.md:56；目标模块独立存在 |
| `test_procedural_memory_diagnostics.py` | :54-56 独立夹具 | 有独立结果存档 |
| `test_procedural_memory_boundaries.py` | :68-70 独立夹具 | 无结果存档；若团队决定收缩退役家族，这是第一个候选——**必须先改 TESTING.md:56 再删文件** |
| `agent-runtime/test-live-tools.ts` | 无 `LLM_API_KEY` 即跳 | 不接门禁是**正确的**：ALWAYS_REQUIRED 门禁 SKIP 会让整份报告失败 |
| `web/avatar/test_eye_morph.js` / `test_eye_lids.js` | 读 gitignore 的 `web/models/*.vrm` | keep；本地有模型时可手动跑 |
| `scripts/test_workbuddy_worker_shim.py` | Windows-only 夹具 | 见 §3.5(e)，不是纯休眠而是平台缺口 |

### 3.7 重复/弱断言但保留（分层是刻意设计，列出主要判断）

- `test_assistant_websocket.py` vs `test_assistant_continuation.py`：同名场景逐条对应，但
  websocket:85-95 的 `assert_spoken`（emotion < text_delta < audio 时序、base64 音频字节、
  `playback_confirmed`）在服务层不可观测——协议信封 vs 状态机的分层。
- `test_assistant_ui_contract.py` vs `test_assistant_api.py`：前者 :15 挂 store-only 鸭子类型
  router，是后者从不走的接线契约；:29-30（stale version 400）、:37-39（reopen 不得复活已取消
  reminder）是独家回归。
- `test_thread_proposals.py` vs `test_proposals_integration.py`：HTTP 侧仅 2 场景重叠；
  parser / 过期 / 容量驱逐 / 每操作并发五个 store 层场景只在此文件。
- `test_orchestrator-run.ts:113`：`(view?.events ?? []).length >= 0` 是恒真断言（weak-assertions，
  confirmed）——建议改为有意义的阈值，**不是删文件**。
- `test_task_fsm.py`（478/478 健康）：5 个子检查被"零业务 import"单检查严格包含，结构冗余，
  可精简；`probe_task_fsm_mutations.py` 是证明其断言会咬人的变异探针，只被自己 docstring 引用。
- `pixel-world/test/save.test.ts` 与 `save-validate.test.ts` 共用 `legalSaves()` 夹具（重复 confirmed），
  但前者是 P2 集成场景，合并价值低；`surface.test.ts` 的 `toWorldCommand` 是死 API（dead-target
  partial）但只占 2 个 test，不值得单独动。

### 3.8 worktree 存量（宽口径）

| Worktree | 跟踪测试数 | 独有 | 缺 main 的 | 判断 |
| --- | --- | --- | --- | --- |
| `tmp/wt-qa`、`tmp/wt-uiux` | 303 / 303 | 无 | 0 | 与 main 测试集完全一致，纯重复结账 |
| `tmp/wt-pixel-world` | 303 | 1（`ready-to-report-stuck.test.ts`） | 1 | **main 没有这个测试**，清前须确认已进 main |
| `tmp/wt-art` | 298 | 无 | 5 | 陈旧结账 |
| `tmp/wt-intent` / `wt-intent1` | 295 / 296 | 无 | 8 / 7 | 陈旧结账 |
| `tmp/head-check` | 206 | 无 | 97 | 严重过期 |
| 两个 WorkBuddy worktree | 2 / 2 | 无 | 301 | 10-11 日的空壳结账；其一有 9 个未提交文件 |
| `.claude/worktrees/confident-roentgen-3771a9` | 197 | 无 | 106 | 2 个未提交新文件，删 worktree 即永久丢失 |
| `.claude/worktrees/optimistic-clarke-71e4fd` | 316 | **19 个 pixel-world 测试** + 1 未跟踪 | 6 | 独有存量最大，疑似未合并分支 |

**红旗**：3 个 worktree（wt-pixel-world、confident-roentgen、optimistic-clarke）持有 main 不存在
的测试文件，清理前必须逐个确认已在 main，否则是静默丢覆盖。其余 9 个无独有测试，可回收磁盘
（删 worktree 属用户决策，本报告不代劳）。
另：`tmp/` 下 106 个 `eval-fastloop-*` 残留目录（本轮实测），属 AGENTS.md 待办第 3 条。

---

## 四、对抗复核拦下的"看似该删"（必须保留）

| 文件 | 为什么看着像孤儿 | 为什么必须留 |
| --- | --- | --- |
| `test_integration.js`（根目录 36 行纯 print） | 无断言、无引用 | **唯一**在跑 `web/avatar/drivers/discourseGesture.js` 真实 API（`getBackchannelGesture/update/generate/feedTextDelta`）的手工 harness；浏览器回归只用 stub 替换它。该删的判决被对抗复核推翻 |
| `web/starmap/test_starmap_browser.mjs` | 与 `test_starmap.mjs` 同名不同代 | PlaybackQueue 视频时序、390px 窄屏 + 滑动手势、narrate 按钮 busy/idle 动态 DOM 只在它里面 |
| `docs/CONTRACT_V2_SCOPE.md.chief-bak` | 未跟踪备份 | 含只存在于此的数据；用户有过被删 `.chief-bak` 的不悦记录 |
| 两个 WorkBuddy worktree、`.claude/worktrees/*` | 重复存量 | 有未提交改动，删即永久丢失 |
| 6 个 always-skip | 实测 `Ran 0 tests` | TESTING.md:55-58 自动恢复契约，夹具回来即激活 |

---

## 五、执行顺序与纪律

1. **第一批（零风险，纯增量）**：§3.5 全部接门禁。已在 CI 或本就是活功能，接入 `run_gates.py`
   只增本地覆盖。逐个接入并跑 `python scripts/run_gates.py` 确认增量耗时（最慢
   `test_procedural_memory_eval_snapshot.py` 约 6s）。avatar 家族一次性挂 `test:avatar` 进
   `package.json:5`。
2. **第二批（只决策，不动文件）**：§3.6 always-skip 家族。接受休眠合约就保持现状；要收缩从
   `test_procedural_memory_boundaries.py` 开始，且先改 `docs/TESTING.md:56` 再删文件。
3. **第三批（唯一删文件的一步）**：§3.4 的 merge，前置移植条件缺一不可，两边跑全绿后才删。
4. **随时可做**：删 §3.1 剩余的 1 个未跟踪垃圾（`scripts/.dialogue-regression-10136-eyes.mjs`）；§3.2 先问用户。
5. **纪律**：本报告只做审计，未动任何文件。任何删除动作前跑齐
   `python scripts/check_protocol.py`、`python scripts/run_gates.py`（全绿）、
   `python scripts/test_core.py`、`npm test`；删 worktree 前先核对 §3.8 的独有文件是否已在 main。

---

## 附录 A：107 条审计结论完整清单

判定口径：verdict=confirmed/partial（0 refuted）；"原建议"为定位审计员的建议，"对抗复核"列
safe=可删 / BLOCKED=拦下 / -=未送对抗。完整证据见 journal（run `wf_c1339c83ac5b4eed`）。

| # | 文件 | 类别 | 复核判定 | 原建议 | 对抗复核 | 置信 | 证据（摘要，可回查 journal） |
|---|---|---|---|---|---|---|---|
| 1 | `pixel-world/test/surface.test.ts` | dead-target | partial | investigate | - | high | pixel-world/test/surface.test.ts:9 `import { Bridge, toWorldCommand } from '../src/world/bridge.ts'`；:291 `const bridge = new Bridge();`、:298 `cons... |
| 2 | `docs/CONTRACT_V2_SCOPE.md.chief-bak` | junk-temp | confirmed | delete | BLOCKED | high | 字节数精确吻合：python os.path.getsize 得 CONTRACT_V2_SCOPE.md=11324、.chief-bak=6844。.chief-bak:4「实施范围：仅 §0 – §3.7」、:6「状态：实施中」、:40「一律冻结」；现文档:4「§4–§9 自 2026-... |
| 3 | `nul` | junk-temp | partial | delete | safe | high | git status 确认 `?? nul`（未跟踪）。用扩展路径 \\?\D:\project\neurosama\nul 以二进制读取：实际 63 字节，内容为 b'/usr/bin/bash: line 1: type: tmptest-inventory.json: not found... |
| 4 | `scripts/.dialogue-regression-10136-eyes.mjs` | junk-temp | confirmed | delete | safe | high | git status shows `?? scripts/.dialogue-regression-10136-eyes.mjs`; `git ls-files --error-unmatch` errors (untracked); `git grep` for the filename =... |
| 5 | `scripts/test-avatar-stop.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node scripts/test-avatar-stop.mjs` → 两条 PASS、exit=0。51 行，约 20 条 assert（doesNotReject/equal/deepEqual），import ../web/avatar/index.js 的 Avatar（EX... |
| 6 | `scripts/test-context-usage-browser.mjs` | orphan-never-run | confirmed | investigate | - | high | .workbuddy/memory/2026-10-16.md:109 原文：「2 红＝test-context-usage-browser（`.msg.bot .context-usage` 未挂载）、test-editor-discussion-home-browser（editor-di... |
| 7 | `scripts/test-editor-discussion-home-browser.mjs` | orphan-never-run | confirmed | investigate | - | high | .workbuddy/memory/2026-10-16.md:109 同条记载其为第二个稳定红灯（editor-discussion 渲染，根因 server/app.py）。240 行 Playwright，import { chromium, expect }；:9 base='http... |
| 8 | `scripts/test-face-shading-ui-browser.mjs` | orphan-never-run | confirmed | investigate | - | medium | 61 行 Playwright，:22 page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=daily')（路由存在）。测 #face-shading 复选框、#seed/#daily 切换、calibration UI ... |
| 9 | `scripts/test-home-bootstrap-browser.mjs` | orphan-never-run | confirmed | wire-into-gates | - | medium | 47 行，import { chromium, expect } from '@playwright/test'。:32 page.goto('http://127.0.0.1:8000/')，:19 route '**/static/avatar/params.js*'（经 /static→... |
| 10 | `scripts/test-interaction-state.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node scripts/test-interaction-state.mjs` → 打印「PASS 7 interaction state priority checks」exit=0。10 行、7 条 assert.equal（:3-9），覆盖 interactionState 优... |
| 11 | `scripts/test-seed-chat.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 16 行，import { chromium } from '@playwright/test'。:6 page.goto('http://127.0.0.1:8000/?avatar=seed')，:7 等 #avatarRendererStatus 含「半身表演已启用」，:9 切 svg ... |
| 12 | `scripts/test-starmap-v2-browser.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 134 行，import { chromium } from '@playwright/test'，文件头:1「V2 星图浏览器手验（协议 §18）」。:97 `await import('/static/starmap/bridge.js')`——经 server/app.py:1326 `... |
| 13 | `web/avatar/test_affect.js` | orphan-never-run | confirmed | wire-into-gates | - | high | `git grep -n test_affect -- .` = 0 hits (file itself tracked: `git ls-files web/avatar/` includes web/avatar/test_affect.js; `git log --oneline -1 ... |
| 14 | `web/avatar/test_ambient.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node --test web/avatar/test_ambient.mjs` → # tests 6 / # pass 6 / # fail 0 / # skipped 0，exit 0（0 skipped 已核实）。 |
| 15 | `web/avatar/test_face_shading.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node --experimental-default-type=module --test` → # tests 6 / # pass 6 / # fail 0 / # skipped 0，exit 0。断言量更正为「39 处 assert 调用」（部分在 for 循环内，实际执行次... |
| 16 | `web/avatar/test_hair_physics.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node --experimental-default-type=module --test web/avatar/test_hair_physics.js` → # tests 4 / # pass 4 / # fail 0 / # skipped 0，exit 0。文件含 4 个 ... |
| 17 | `web/avatar/test_intensity.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node web/avatar/test_intensity.js` → exit 0，18 项全 ok（情绪表 7 项 base/调侃/得意/认真/收敛/轻微不服/变小气，最低增益 0.560、最高 0.918）。test_intensity.js:22 读 config/avata... |
| 18 | `web/avatar/test_lipsync.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node web/avatar/test_lipsync.js` → exit 0，约 26 项 ok（t=0/t=dur/超时长/空文本/duration=0；open∈[0,1] 全文本全采样点；无孤立大跳变；b/p/m  closure 中部 open≈0；提前量 >20ms 且... |
| 19 | `web/avatar/test_motion.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 exit 0，含「19Hz @ 60fps 不发散」「dt=5s 一步到位且不发散」「f=1.2 zeta=1 无过冲」「静置会犯困、互动会回神」等。关于「唯一 grep 命中」需修正口径：`git grep` 对该文件名 0 命中（因 .agents/ 在 .gitignore:55 ... |
| 20 | `web/avatar/test_render.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 exit 0，约 25 项 ok（全 1 / 全 -1 / 笑眼+张口 / 眼球到底四组极端参数各 2 项；虹膜缩放 6 项含「中位不缩放」「+1 放大」「-1 收小」「关于虹膜中心对称」「与瞳孔缩放同时生效」；长时运行 2 项）。 |
| 21 | `web/avatar/test_speech_plan.js` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 exit 0，9/9 ok。注意：该文件不是 node:test，而是自带 `test()` 帮助函数(:5)，跑法是 `node web/avatar/test_speech_plan.js`；挂门禁时不要套 `--test`（会触发 MODULE_TYPELESS_PACKAGE_J... |
| 22 | `web/starmap/test_starmap_v2.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node web/starmap/test_starmap_v2.mjs` → # tests 3 / # pass 3 / # fail 0 / # skipped 0，exit 0（V2 渲染模型 / epiphany 边保留 reason / dangling 边丢弃）。 |
| 23 | `web/test-capsule-drawer-browser.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 174 行 Playwright，文件头:1-3「打磨轮 U-1~U-6 的界面契约回归：胶囊输入栏、模式状态条、工作台抽屉、设置面板…桌面 + 390px 各三张」。:27 routeWebSocket('**/ws') 受控 WS + :19-25 只读 API 存根隔离，:40 page... |
| 24 | `web/test_glimmer_bubble.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node web/test_glimmer_bubble.mjs` → # tests 7 / # pass 7 / # fail 0 / # skipped 0，exit 0（协议 §16.2 气泡状态机：含「update on a hidden bubble is a no-op」... |
| 25 | `web/voice/editor-insight.test.mjs` | orphan-never-run | confirmed | wire-into-gates | - | high | 实测 `node --test web/voice/editor-insight.test.mjs` → # tests 9 / # pass 9 / # fail 0 / # skipped 0，exit 0；126 行、35 处 assert。9 组覆盖同源 notes iframe 门禁... |
| 26 | `agent-runtime/selftest.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran `node --experimental-strip-types selftest.ts` → 8 passed, 0 failed. 8 checks :71,:72,:75,:79,:80,:81,:87,:88; parent spawns 3 children (crash :... |
| 27 | `agent-runtime/test-handin.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran → 7 passed, 0 failed. 7 checks at :40,:42,:47,:48,:53,:59,:66. Exclusive Windows coverage confirmed: :56-:60 `real.replace(/\\/g,"\\\\")` doubl... |
| 28 | `agent-runtime/test-model-run.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran → 9 passed, 0 failed. 9 checks :131,:132,:135,:139,:150,:151,:152,:154,:157. The exclusive instructions-placement block is at :150-:158 (findin... |
| 29 | `agent-runtime/test-nudge.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran → 3 passed, 0 failed. 3 checks at :115,:116,:119: :115 completes after one nudge, :116 artifact from the second (nudged) turn, :119 no error ev... |
| 30 | `agent-runtime/test-openai-tools.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran → 5 passed, 0 failed. 5 checks :141,:144,:147,:149,:162: streaming Chinese, toolcall start/end events, args parsed to object, usage read, tool-... |
| 31 | `agent-runtime/test-tier-model.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran → 9 passed, 0 failed. 9 checks :36,:40,:42,:43,:44,:48,:68,:70,:72 covering modelForTier override/fallback/no-guess (:36-:44), modelsFromEnv ca... |
| 32 | `agent-runtime/test-write-file.ts` | coverage-gap | confirmed | wire-into-gates | - | high | Ran `node --experimental-strip-types test-write-file.ts` → 12 passed, 0 failed. 12 checks: escape rules :44-:49 (relative-path allow, ../ reject, a... |
| 33 | `pixel-world/scripts/check-s5-d.mjs` | coverage-gap | confirmed | investigate | - | high | .github/workflows/pixel-world.yml:51-53 `D acceptance checker self-test` / working-directory: pixel-world / `node scripts/check-s5-d.mjs --self-tes... |
| 34 | `pixel-world/scripts/smoke.mjs` | coverage-gap | confirmed | wire-into-gates | - | high | pixel-world/scripts/smoke.mjs:25-26 注释「台账模式：先起主服务（8000），再 LEDGER=1 node scripts/smoke.mjs」+ `const LEDGER = process.env.LEDGER === '1'`；:805-811 `i... |
| 35 | `scripts/probe_task_fsm_mutations.py` | coverage-gap | confirmed | wire-into-gates | - | high | Header :2, :12-:13 confirm: it mutates server/task internals, requires the test to go red and hit expected assertions, restores bytes in finally, a... |
| 36 | `scripts/test-experience-browser.mjs` | coverage-gap | partial | wire-into-gates | - | high | 用 python 解析 tmp/test-inventory.json：零 entries 共 159 个；其中 node 侧(.mjs/.js)=61 个、9101 行（与 finding 一致✓）；web/avatar 零 entries=19 个（✓）；*-browser.mjs 零 e... |
| 37 | `scripts/test_contract.py` | coverage-gap | confirmed | keep | - | high | Ran `./.venv/Scripts/python.exe scripts/test_contract.py` → PASS 278/278, exit 0. git grep confirms it is NOT in scripts/run_gates.py, package.json... |
| 38 | `scripts/test_core.py` | coverage-gap | partial | keep | - | high | Ran `./.venv/Scripts/python.exe scripts/test_core.py` → 92 passed, 0 failed. It IS in run_gates.py:60 (the `core` gate) and in ALWAYS_REQUIRED (:33... |
| 39 | `scripts/test_curiosity_graph.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_curiosity_graph.py:16-17 imports server.memory.store.MemoryStore and `from server.memory import seeds as S`; :31/:53/:64 lazily import... |
| 40 | `scripts/test_evolution_soak.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_evolution_soak.py:30-39 ScriptedProvider (raises self.item if it is an Exception, else returns scripted JSON); runtime `Ran 6 tests in... |
| 41 | `scripts/test_memory_extraction_contract.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_memory_extraction_contract.py:18 `from server.memory import extractor` (lazy, inside a guard); runtime `Ran 25 tests in 0.103s ... OK`... |
| 42 | `scripts/test_memory_preference_parser.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_memory_preference_parser.py:15-18 imports server.memory.extractor and server.memory.preference (PreferenceValidationError, normalize_p... |
| 43 | `scripts/test_memory_presentation.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_memory_presentation.py:21-27 `forbid_app_imports` raises AssertionError('pure projection imported application module: ...') for any se... |
| 44 | `scripts/test_procedural_memory_eval_snapshot.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_procedural_memory_eval_snapshot.py:308-321 — `member.symlink_to(external)` inside try/except OSError -> self.skipTest; :320 `if not me... |
| 45 | `scripts/test_reflect_cycle.py` | coverage-gap | partial | wire-into-gates | - | high | scripts/test_reflect_cycle.py:57-69 vs scripts/test_evolution_soak.py:58-73: same setUp shape and same SelfEvolutionCycle(store=..., provider=..., ... |
| 46 | `scripts/test_self_evolution_quality.py` | coverage-gap | partial | keep | - | high | scripts/run_gates.py:135-138 `"self_evolution": {"cmd": [str(PY), "scripts/test_self_evolution_quality.py"] ...}` and :332 lists 'self_evolution' i... |
| 47 | `scripts/test_self_evolution_send.py` | coverage-gap | partial | keep | - | high | scripts/run_gates.py:147-150 `"self_evolution_send": {"cmd": [str(PY), "scripts/test_self_evolution_send.py"] ...}` and :332 in ALWAYS_REQUIRED; ru... |
| 48 | `scripts/test_soul_polish.py` | coverage-gap | confirmed | wire-into-gates | - | high | scripts/test_soul_polish.py:16-19 imports; runtime `Ran 9 tests in 0.077s ... OK`. Line numbers verified: server/persona.py:191 (dynamic_prompt), s... |
| 49 | `scripts/test_task_fsm.py` | coverage-gap | confirmed | keep | - | high | Ran → PASS 478/478, exit 0. git grep confirms absent from scripts/run_gates.py. It IS executed by ci.yaml:73 `for f in scripts/test_*.py`. Referenc... |
| 50 | `web/avatar/test_body_poses.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 `node --experimental-default-type=module --test web/avatar/test_body_poses.js` → # tests 9 / # pass 9 / # fail 0 / # skipped 0，exit 0。 |
| 51 | `web/avatar/test_body_rig.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 4 / # pass 4 / # fail 0 / # skipped 0，exit 0。 |
| 52 | `web/avatar/test_eye_expression.js` | coverage-gap | confirmed | wire-into-gates | - | high | web/avatar/test_eye_expression.js:1-4 只 import assert、node:test、`./drivers/eye-expression.js`，无 fs / models 读取。package.json:16 test:eyes 收录；:5 npm ... |
| 53 | `web/avatar/test_eye_params.js` | coverage-gap | confirmed | wire-into-gates | - | high | web/avatar/test_eye_params.js:2-4 只 import `node:assert/strict`、`./params.js`、`./drivers/emotion.js`，全文无 `readFileSync`、无 `models/`。package.json:16... |
| 54 | `web/avatar/test_finger_poses.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 9 / # pass 9 / # fail 0 / # skipped 0，exit 0。9 组覆盖模型范围限定、24 个常规关节镜像 curl、仅右手张开、包络连续门控、reduced motion、畸形数值有界、采样不污染后续目标。 |
| 55 | `web/avatar/test_finger_rig.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 4 / # pass 4 / # fail 0 / # skipped 0，exit 0。 |
| 56 | `web/avatar/test_gaze.js` | coverage-gap | partial | wire-into-gates | - | high | scripts/check_protocol.py:417 「注视点的坐标被 web/avatar/test_gaze.js 抄了一份」、:421 `test_gaze = ROOT / "web" / "avatar" / "test_gaze.js"`、:422-428 `if test_... |
| 57 | `web/avatar/test_gesture_paths.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 11 / # pass 11 / # fail 0 / # skipped 0，exit 0，与 docs/GESTURE_PATHS.md:35 声称的 11 项一致。 |
| 58 | `web/avatar/test_gesture_poses.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 12 / # pass 12 / # fail 0 / # skipped 0，exit 0，与 docs/GESTURE_CALIBRATION.md:38 声称的 12 项一致。 |
| 59 | `web/avatar/test_hand_poses.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 13 / # pass 13 / # fail 0 / # skipped 0，exit 0。 |
| 60 | `web/avatar/test_hand_rig.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 → # tests 6 / # pass 6 / # fail 0 / # skipped 0，exit 0。 |
| 61 | `web/avatar/test_performance.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 exit 0。「1 pass」同为 node --test 的文件级计数（该文件是裸脚本，:125 末尾 console.log 收尾）。实际体量：43 处 assert，覆盖音频开始前不动、音频时钟采样、冷却、自然结束、预览、倾听/打断清除、语义锚点与 provider 时间戳、时钟跳... |
| 62 | `web/avatar/test_pose_transition.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 exit 0。「1 pass」是 node --test 把整个文件算作 1 个 test 的结果（该文件用自带 `test()` 帮助函数 :9，不是 node:test 注册）。实际体量：12 个自定义 test 调用、41 处 assert。挂门禁时两种跑法都行（`node --t... |
| 63 | `web/avatar/test_stage_camera.js` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 `node --experimental-default-type=module --test web/avatar/test_stage_camera.js` → # tests 6 / # pass 6 / # fail 0 / # skipped 0，exit 0。零 DOM（无 ... |
| 64 | `web/ui_nav.test.mjs` | coverage-gap | confirmed | wire-into-gates | - | high | docs/TESTING.md:68 `node web\ui_nav.test.mjs`；docs/PROTOCOL.md:591 「回归：...`node web/ui_nav.test.mjs`」（git grep 全仓仅此 2 处文档 + 文件自身 :3）。package.json:5... |
| 65 | `web/voice/editor-insight.test.mjs` | coverage-gap | confirmed | wire-into-gates | - | high | 实测 `node --test web/voice/editor-insight.test.mjs` → # tests 9 / # pass 9 / # fail 0。文件用 node:test + data: URL 加载 ./editor-insight.js（:1-5），零外部依赖。i... |
| 66 | `agent-runtime/test-live-tools.ts` | always-skip | confirmed | keep | - | high | test-live-tools.ts:13-17 `const apiKey=process.env.LLM_API_KEY??""; if(apiKey===""){console.log("SKIP live tools: LLM_API_KEY 未配置");process.exit(0)... |
| 67 | `scripts/test_memory_eval.py` | always-skip | confirmed | keep | - | high | scripts/test_memory_eval.py:36-38 fixture gate; runtime `Ran 0 tests in 0.001s / OK (skipped=1)` exit 0; `git ls-files eval/research/*.json` = 0 re... |
| 68 | `scripts/test_procedural_memory_boundaries.py` | always-skip | partial | investigate | - | medium | scripts/test_procedural_memory_boundaries.py:68-70 gate on procedural_memory_boundary_cases_v1.json; runtime `Ran 0 tests ... OK (skipped=1)` exit ... |
| 69 | `scripts/test_procedural_memory_diagnostics.py` | always-skip | confirmed | keep | - | medium | scripts/test_procedural_memory_diagnostics.py:54-56 gate; runtime skipped=1 exit 0; docs/TESTING.md:56 lists `_diagnostics.py`; scripts/eval_proced... |
| 70 | `scripts/test_procedural_memory_eval.py` | always-skip | confirmed | keep | - | high | scripts/test_procedural_memory_eval.py:28-30 gate; runtime `Ran 0 tests ... OK (skipped=1)` exit 0; docs/TESTING.md:56 lists it; scripts/eval_proce... |
| 71 | `scripts/test_procedural_memory_induction.py` | always-skip | partial | investigate | - | medium | scripts/test_procedural_memory_induction.py:29-31 gate shares procedural_memory_eval_cases.json; runtime `Ran 0 tests ... OK (skipped=1)` exit 0. C... |
| 72 | `scripts/test_procedural_memory_source_projection.py` | always-skip | partial | investigate | - | medium | scripts/test_procedural_memory_source_projection.py:52-54 gate shares procedural_memory_eval_cases.json (also read directly at :183); runtime `Ran ... |
| 73 | `scripts/test_workbuddy_worker_shim.py` | always-skip | confirmed | investigate | - | high | test_workbuddy_worker_shim.py:205-:211 `if os.name != "nt": print("SKIP workbuddy shim: Windows-only .cmd launcher fixture"); return 0`. ci.yaml:23... |
| 74 | `web/avatar/test_eye_lids.js` | always-skip | confirmed | keep | - | high | web/avatar/test_eye_lids.js:6 import readFileSync；:23 `readFileSync(new URL('../models/${asset}.vrm', import.meta.url))`，fixture() 默认资产 seed-daily-... |
| 75 | `web/avatar/test_eye_morph.js` | always-skip | confirmed | keep | - | high | web/avatar/test_eye_morph.js:5 import readFileSync；:16 `readFileSync(new URL('../models/${asset}.vrm', import.meta.url))`（fixture() 内），:82 同样一行（第二个... |
| 76 | `agent-runtime/test-approval.ts` | duplicate-overlap | partial | merge | - | medium | Confirmed not wired: no test-approval.ts in run_gates.py (only approval-per-call :211 and approval-chain :216); ci.yaml runs agent-runtime only `np... |
| 77 | `agent-runtime/test-mcp-live.ts` | duplicate-overlap | partial | merge | - | medium | grep: test-mcp-live.ts has 9 checks (:41,:43,:46,:47,:48,:49,:53,:54,:56), not 8; and 6 duplicate the unit file (finding said 5) — :43 name prefix/... |
| 78 | `agent-runtime/test-orchestrator-run.ts` | duplicate-overlap | partial | merge | - | medium | Overlap confirmed: run:108 lease==="lease-e2e" ↔ tools:84 lease==="lease-1"; run:109 session==="session-e2e" ↔ tools:85 session==="sess-1"; run:110... |
| 79 | `pixel-world/test/ledger.test.ts` | duplicate-overlap | partial | keep | - | medium | pixel-world/test/ledger.test.ts:4-22 import project/parseCommissionRow/parseAgentRow/kindForAgent、LedgerClient/reconnectDelay、Simulation/ledgerComp... |
| 80 | `pixel-world/test/palette-budget.test.ts` | duplicate-overlap | partial | merge | - | high | pixel-world/test/palette-budget.test.ts:117-133 硬编码 8 个文件（../src/main.ts、render/buildings.ts、decor.ts、smoke.ts、tilemap.ts、villager.ts、weather.ts、sc... |
| 81 | `pixel-world/test/save.test.ts` | duplicate-overlap | confirmed | merge | - | high | pixel-world/test/save.test.ts:316-358 三条 P2 契约测试（fresh+running / 走路中 / 满数据，全部只 `assert.doesNotThrow(() => assertSaveV1(...))`，另加 `assert.equal(save... |
| 82 | `pixel-world/test/terrain-edges.test.ts` | duplicate-overlap | partial | keep | - | medium | pixel-world/test/terrain-edges.test.ts:167-177 `function lstar(hex: number)` 与 pixel-world/test/terrain-edge-visibility.test.ts:50-60 的函数体逐字节相同；dl(... |
| 83 | `scripts/test_assistant_continuation.py` | duplicate-overlap | partial | keep | - | high | scripts/test_assistant_continuation.py:14-16 `from server.assistant.loop import AgentLoop, Tool` / `from server.assistant.runtime import RuntimeSto... |
| 84 | `scripts/test_assistant_ui_contract.py` | duplicate-overlap | partial | keep | - | low | scripts/test_assistant_ui_contract.py:15 `app.include_router(create_router(SimpleNamespace(store=store)))` vs scripts/test_assistant_api.py:21 `cre... |
| 85 | `scripts/test_assistant_websocket.py` | duplicate-overlap | confirmed | keep | - | medium | scripts/test_assistant_websocket.py:97-102 vs scripts/test_assistant_continuation.py:41-45 (same 3 scripted decisions); :108-116 vs :52-58 (same st... |
| 86 | `scripts/test_claude_worker_shim.py` | duplicate-overlap | partial | merge | - | medium | grep 证实四文件均有 check/make_fake/build_adapter/collect 且签名一致（claude:73/83/107/126、codex:68/78/102/119、dsh:140/150/174/194、workbuddy:152/162/170/194），`g... |
| 87 | `scripts/test_task_fsm.py` | duplicate-overlap | confirmed | merge | - | high | test_task_fsm.py:1467 `check("零业务 import", business == [], str(business))` where `business` collects every `server`/`server.*` import at :1442-1449... |
| 88 | `scripts/test_thread_continuity.py` | duplicate-overlap | partial | merge | - | high | Harness fork: scripts/test_thread_continuity.py:53-61 vs scripts/test_cross_day_companion.py:52-60 (identical confirm_http), :80-83 vs :46-50 (iden... |
| 89 | `scripts/test_thread_proposals.py` | duplicate-overlap | partial | keep | - | low | scripts/test_thread_proposals.py:26-36 is setUp + the proposal() helper (no scenario). The HTTP twin of test_proposals_integration.py:26-36 (test_p... |
| 90 | `web/starmap/test_starmap_browser.mjs` | duplicate-overlap | partial | delete | BLOCKED | medium | web/starmap/test_starmap_browser.mjs（305 行，:2 自述覆盖「narrate_busy 冷却、检索去抖、三类空态、390px 无横向溢出」）。scripts/test-starmap-v2-browser.mjs 只有 134 行，NODES 只用 se... |
| 91 | `web/test-capsule-drawer-browser.mjs` | duplicate-overlap | partial | merge | - | medium | git grep -n "test-capsule-drawer-browser" 全仓 tracked 文件 0 命中（唯一命中是未跟踪的 tmp/test-inventory.json:3135 自身）；package.json、docs/TESTING.md、scripts/run_ga... |
| 92 | `agent-runtime/test-background.ts` | weak-assertions | confirmed | investigate | - | high | agent-runtime/test-background.ts:33 `const started = Date.now();`；:41 `await runtime.harness.waitForIdle(context);`；:43 `check("空闲等待不等后台任务", true);... |
| 93 | `agent-runtime/test-compaction-live.ts` | weak-assertions | confirmed | investigate | - | high | agent-runtime/test-compaction-live.ts:102 `const compaction = page.items.find((entry) => entry.kind === "pi.compaction");`、:103 `if (compaction !==... |
| 94 | `agent-runtime/test-host-tools.ts` | weak-assertions | partial | investigate | - | medium | grep confirms exactly 10 `check(` at :81,:83,:86,:88,:90,:93,:96,:99,:104,:108. Stub-literal render checks: :81 ("用户喜欢短标题"/"可能喜欢短句" from stub :37),... |
| 95 | `agent-runtime/test-orchestrator-run.ts` | weak-assertions | confirmed | investigate | - | high | test-orchestrator-run.ts:113 tautology confirmed. Real assertions at :102,:103,:106,:108,:109,:110,:112. It is 1 of 8 checks, so the file is otherw... |
| 96 | `agent-runtime/test-orchestrator-tools.ts` | weak-assertions | partial | investigate | - | medium | grep confirms 15 `check(` (:82,:84,:85,:86,:88,:91,:95,:98,:102,:105,:108,:112,:113,:118,:120), not 13. Stub-echo cluster = 7 (not 6): :82 ("c_1"),... |
| 97 | `agent-runtime/test-profiles.ts` | weak-assertions | partial | keep | - | high | grep counts exactly 16 `check(` in test-profiles.ts (:38,:39,:40,:41,:42,:55,:56,:58,:59,:60,:63,:66,:67,:68,:69,:70), all member assertions on `ru... |
| 98 | `pixel-world/test/no-cjk-canvas.test.ts` | weak-assertions | confirmed | investigate | - | medium | pixel-world/test/no-cjk-canvas.test.ts:20-28 第一条测试对 walk(src) 全量文件断言 `/\.add\.text\(/\.text\(/g` 匹配数为 null（offenders deepEqual []）。:29-46 第二条：先 `if... |
| 99 | `test_integration.js` | weak-assertions | confirmed | delete | BLOCKED | high | test_integration.js 实测 35 行（finding 写 36，差 1）。全文 import 只有 web/avatar/index.js（:1），`console.log` 出现在 :12/:22/:24/:27/:31/:33/:35，无任何 assert / node:... |
| 100 | `.claude/worktrees/confident-roentgen-3771a9` | worktree-duplicate | partial | delete | BLOCKED | high | `git merge-base --is-ancestor 4f016ad master` = 是（已并入）；`git cat-file -e master:scripts/test-ui-nav-browser.mjs` 与 `master:web/ui_nav.test.mjs` 均 ON... |
| 101 | `.claude/worktrees/confident-roentgen-3771a9/web/ui_nav.test.mjs` | worktree-duplicate | confirmed | delete | safe | high | `git worktree list` 确认 .claude/worktrees/confident-roentgen-3771a9 在册（4f016ad [claude/confident-roentgen-3771a9]）。在该 worktree 内 `git status --porce... |
| 102 | `.claude/worktrees/optimistic-clarke-71e4fd` | worktree-duplicate | partial | investigate | - | high | 逐个 `git cat-file -e master:pixel-world/test/<n>.test.ts`：19 个 only_here（camera/creator-schema/day-phase/decree/director/eco-mode/faith/fog-drift/fo... |
| 103 | `.claude/worktrees/optimistic-clarke-71e4fd/pixel-world/test/` | worktree-duplicate | partial | investigate | - | high | `git worktree list`：该 worktree 实为 0406956 [claude/optimistic-clarke-71e4fd]，不是 finding 写的 878bbdf。存量数字核对无误：tmp/worktree-inventory.json 的 only_here ... |
| 104 | `C:/Users/18712/WorkBuddy/Worktrees/neurosama/master-1aeee42a` | worktree-duplicate | partial | delete | BLOCKED | high | git worktree list：HEAD 73101ac [workbuddy/master-1aeee42a]；`git merge-base --is-ancestor 73101ac master` = 是（已并入）。但 `git -C <path> status --porcela... |
| 105 | `C:/Users/18712/WorkBuddy/Worktrees/neurosama/master-39cd7e0f` | worktree-duplicate | partial | delete | BLOCKED | high | HEAD 73101ac（与 master-1aeee42a 同一 SHA，worktree list 证实），已并入 master。`git -C <path> status --porcelain / wc -l` = 9：`A eval/scenarios.json` + 7 个 ` M... |
| 106 | `tmp/wt-pixel-world` | worktree-duplicate | partial | investigate | - | high | git worktree list：HEAD 9d0aa3f [feat/pixel-world-work]；`git rev-list --count master..feat/pixel-world-work` = 1；`git cat-file -e master:pixel-world... |
| 107 | `tmp/wt-pixel-world/pixel-world/test/ready-to-report-stuck.test.ts` | worktree-duplicate | confirmed | merge | - | high | `git worktree list`：tmp/wt-pixel-world = 9d0aa3f [feat/pixel-world-work]；`git show --stat --format="" 9d0aa3f` → `pixel-world/test/ready-to-report-... |
