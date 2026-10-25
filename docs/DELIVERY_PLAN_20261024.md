# 交付计划（2026-10-24 拍板版）

**Status:** 用户当日拍板四项方向。本文是 2026-10-24 的交付快照，不再是现行执行依据（2026-10-25 回填）：v1.0-daily 之后的进展不回填本文，现行状态以 `git log` 与门禁命令输出为准。
**性质:** 记录 2026-10-24 的决策快照。带日期的数字只在当日成立；进度一律以命令输出为准。

## 已拍板的方向（用户 2026-10-24 原话口径）

1. **交付定义：自己日常真用。** 优先级：不出差错 > 好用 > 漂亮。演示能力作为副产物自然存在，不做演示优先的取舍。
2. **慢环：接一个真 Worker 打样。** 挑一个 Worker 走真执行全链路，其余维持 manual/HITL。durable 运行时保持默认关。
3. **pixel-world：当前实现冻结为默认版本。** 世界回写、外部 Agent transport、LLM 造物主生成侧继续停泊。后续愿景（用户原话）：做成支持定制化的世界生成器并能真实运行，可能需要按用户想法模板搜集资料的 skill，在造物主与世界运行规则基础上引入其他组件——记入 roadmap，不进本次交付。
4. **界面：先出分级清单再定。** 按证据（使用路径、测试覆盖、耦合度）提「核心可见 / 收进二级 / 演示专用」三级清单，用户只做否决。

## 事实基线（2026-10-24 核验）

- 主仓 master 领先 origin/master 67 个提交未推送；工作区约 26 个文件未提交，为连贯的收尾批（interpret 流式离线闸门、陪伴面板入口、记忆 created_at 展示、四个新测试已挂 package.json 与 run_gates）；stash@{0} 与该批同文件集（同步前暂存又恢复）。
- 未并入 master 的分支：docs/art-audit（ahead 9）、docs/uiux-audit（ahead 4）、feat/pixel-world-work（ahead 1）、feat/pixel-world-oracle-world（ahead 1）、fix/s5-d-worker-routing（ahead 1）；两条 backup/* 保留不动。其余分支与 worktree 已干净并入。
- 事件协议 step8：Role 0 已于 2026-10-23 确认并入 PROTOCOL.md §25；后端 emitter 已落（提交 80a7e06、0437303），**前端消费（落地顺序第 4 步）未完成**。
- 意图路由：IntentGate 接不接 _run_turn 用户已定「不接」，不再复议。
- 三份 2026-10-19 审计文档滞后于代码（多角色、存档、调试面板、心跳等已修），未加过期标注。

## Phase 0 — 版本收敛与真相对齐（可逆，随时开工）

- [x] 0.1 主仓在飞批验证后落袋：四门禁全绿后按逻辑拆成六个提交（interpret 离线闸门 / 陪伴入口 / 上下文预算护栏 / config §17 热重载 / 状态文档 / 残件处置）。npm 红点系 node_modules 丢失 three（环境问题，`npm install` 补齐）。stash@{0} 核验为已落地内容的旧形态（pixel-world 部分被 bba023d 演进版取代），已弃。
- [x] 0.2 推送 master 到 origin。
- [x] 0.3 五条 ahead 分支全并入（UIUX_AUDIT 冲突取 HEAD 侧：对家是带本机绝对路径的旧版，`git cherry` 证实补丁已在 master）；合并后四门禁复跑全绿再推；已并入分支与 tmp/wt-* worktree 清理，远端同步删除；backup/* 与 stash@{1} 保留。**例外**：`.claude/worktrees/confident-roentgen-3771a9` 里有 8 个文件的真实未提交工作（另一 session 在飞），保留不动，待其认领或过期后再议。（2026-10-25 复查为 15 个未提交文件，仍保留不动。）
- [x] 0.4 文档真相对齐：三份 10-19 审计加过期横幅；TASKS.md 打磨轮加口径说明（不追溯补勾，对账归 Phase 3）；README 计数改「以运行输出为准」；DEMO_ART_SPEC_TODO 加覆盖标注。
- [x] 0.5 残件处置：chief-bak 归档 `docs/history/`；`web/style.css.bak` 删；一次性采集点文件移 `tmp/` 停车；`agent/`（vendored skills 副本）加 gitignore；四个新测试随 0.1 入册。

**执行记录（2026-10-24）**：HEAD `e91bf3d` 起收口；四门禁（check_protocol / test_core 92 / npm test / evals 离线 6）全绿；origin/master 同步。

**出口条件：** 工作区干净、origin 同步、文档与代码一致、run_gates 全绿。

## Phase 1 — 慢环垂直切片（真 Worker 打样）

- [x] 1.1 step8 前端消费：9 类事件 + 5 个前端命令接入 app.js 与抽屉 ops 视图（issue #20，`web/events-v2.js` + ops 视图事件区；单测 28/28 入 npm test 链）。
- [x] 1.2 打样 Worker 全链：roster 增 `sandbox` CLI 工（issue #21），调度核心与适配器零改动；W1（issue #25）补齐了此前缺位的发送点接线（v2 信封广播进 /ws、五命令接收端）——0437303 只建了 emitter 发射层，真实发送点从未接进 app.py，这是拆票时才发现的事实缺口。
- [x] 1.3 契约 v2 §4 状态机收口（issue #22）：迁移表与规范逐边零差集，收口落在非法组合拒绝，顺手修了 resume_to 非法目标与 BLOCKED 丢 reason 两个真缺陷；3b 权限-风险跨字段校验落地（L5/L6 → 至少 high）。
- [x] 1.4 浏览器 e2e 单缝 tracer（issue #23）：真服务（独立端口）+ 真页面 + 真子进程，13/13 绿，变异自证「断链即红」，入 run_gates 为 `step8_e2e_browser` 门禁。
- [x] T5 收口（issue #24）：发送点↔事件对账入票、四个新套件全部入册（task_fsm / contract_v2 / v2_broadcast 进 run_gates，events-v2 进 npm test 链）、run_gates 全绿留档。live 评测判定：本切片零 Prompt/装配改动，按规矩不触发。

**Blocking edges：** 实际执行顺序 1.1（#20）∥ 1.3（#22）∥ 打样后端（#21）→ W1 接线（#25）→ 1.4（#23）→ T5（#24）；与计划的差异仅在于拆出了 W1（发送点接线是拆票后暴露的缺口）。执行方式：T1/T2/T4 由后台 agent 完成初版、集成 session 复核集成；W1 与 T3 因模型并发上限三次中断后由集成 session 亲手收口。

## Phase 2 — 日常真用稳定性

- [x] 2.1 延迟与卡顿实测（issue #27）：`scripts/bench_voice_loop.py` + `docs/LATENCY_BASELINE.md`（Mock 口径：首字 p50≈95ms，六成为 Mock 桩节奏；打断亚毫秒；检索毫秒级）。实测挖出 #30 检索假命中（幻觉风险）与 #31 快环内索引重建线性涨，另案跟踪。
- [x] 2.2 记忆管理 UI（issue #28）：「何时说过」（证据链引用与时间）+ 软停用（`disabled` 列，retriever 单点出三档装配，不碰 updated_at 与硬删）。遗留 #32：冷启动破冰绕过停用门（归 Role 2 域）。
- [x] 2.3 容错演练（issue #29）：`docs/DRIFT_DRILLS.md` 三场景——断线自动重连（指数退避封顶 15s + v2 seq 重算/id 去重保留）、坏 Key 4xx 快败与文案止损（不回显供应商响应体）、WS 重连后任务视图与台账一致。三份演练脚本双模式（observe 记录/回归断言）入册门禁。立案 #33（clarify 分支无意图门槛）、#34（重连不续 session_id，需 Role 0 决策）。
- [x] 2.4 出口门禁：run_gates 全绿留档（带 HEAD 锚，见 Phase 2 收口提交）；新套件入册——memory_manage、三个 drill 门禁、npm 链补 memory-panel 与 reconnect。

**Phase 2 收口记录**：S1/S2 后端由 agent 完成、S3 由两任 agent 暂存+集成 session 复验收口；五张立案票（#30-#34）待排期，见交付汇报。

## Phase 3 — 界面分级瘦身

- [x] 3.1 出分级清单（证据：使用路径、测试覆盖、耦合度；合并后的 uiux-audit / art-audit 两分支结论作为输入）。预判供否决：核心可见＝语音对话＋舞台＋记忆侧栏＋ops 抽屉；收二级＝interpret/practice/experience/writing/companion/music；演示专用＝avatar-lab、starmap、pixel-world 入口；待删＝card_preview、notepad_preview、design-preview。
- [x] 3.2 用户一次性否决（2026-10-24 晚：「继续吧」＝零否决项，按草案定稿）。
- [x] 3.3a P3a 分级落地（issue #37，master `56ce458`）：盘点发现主界面本就无多余直达入口；归档三件（card_preview 全家/notepad_preview 全家/design-preview 工作区）连同测试账目；task-card 集成测试改借归档页当宿主。
- [~] 3.3b P3b 视觉统一（2026-10-24 晚启动；完成范围与验收证据未回填，2026-10-25 核。原记录勾选与「进行中」并存，自相矛盾，现按未完成记。）
- 附加落地（2026-10-24 晚，排期呈报后用户「继续」）：F1 #35 检索假命中（ngram(2,3)）、F2 #36 clarify 意图门槛、F3 #32 破冰停用门、F4 #34 重连续会话（协议 §六新增 resume，Role 0 批准）全部收口；#31 索引重建择机未动。

## Phase 4 — 交付验收与打标

- [x] 4.1 全量黄金命令 + run_gates 全绿，结果带 HEAD 与日期锚记录。
- [x] 4.2 dogfood：**以合成语音自动化替代人工**（用户 2026-10-24 授权）。SAPI Huihui 合成一天语段 → Playwright 假麦克风 → 常驻真实服务，产出 `docs/DOGFOOD_REPORT_20261024.md`。真机验证：真实 ASR、负向约束不脑补、记忆写入并被引用、断线重连 resume 回原会话、打断正常。发现并立案 #40（委托路由对 ASR 词形脆弱 + 落空文案误导 + 语音确认口径待拍板）。
- [x] 4.3 roadmap 落档：`docs/ROADMAP.md`（pixel-world 世界生成器愿景、IntentGate 已否、#31 择机、voice-lab 停泊）。
- [x] 4.4 交付说明：`docs/USAGE.md`（开机、日常操作、已知边界、出错时看哪里）。**已打 tag `v1.0-daily`**（提交 `ebee94d`，2026-10-24）。
- [x] 4.5 交付后修复：#40 D1 委托路由对 ASR 词形脆弱（should_route 拆分 `_delegation_request`，四条独立成立；顺带修好隔离装载器漏编译同步函数导致的静默超时）；#39 生产库哨兵（TEST_SENTINEL=1 时 DB_PATH 指 data/ 即启动失败，入 run_gates）。

**Phase 4 附注**：dogfood 前按项目红线征得用户同意重启了 8000 到当前 master（原进程为 2026-10-21 启动，已漂移出 `/api/memories` 500 的不可服务状态，重启后 200）。

## 多 session 分工

- 每 Phase 一个独立 session，票据化执行（to-tickets 形态，GitHub issues 或 .scratch/ 本地票据），每票自带验收命令与 blocking edges。
- 文件所有权沿用 AGENT_PLAYBOOK 角色白名单：1.1 归 web 侧角色、1.2/1.3 归慢环侧角色、2.x 归稳定性专项、3.x 归 UI 侧（有打磨轮临时授权先例）。跨角色走 [PROTOCOL_REQUEST]，红线三条（executive.py、调色板、8000/8010 重启）不因本计划放宽。
- Phase 1 与 Phase 2 可两 session 并行（server/task 与基准/容错文件面基本不相交）；Phase 3 依赖 0.3 合并的审计分支；Phase 4 收口必须单 session 全局看。

## 本计划的边界

- 不含：pixel-world 生成器与新组件（roadmap）、IntentGate 接入（已否）、durable 全面接真、500ms 延迟目标、给第三方分发的安装工程。

## 2026-10-25 回填（只读 review 发现；当日只改文档，未执行任何写操作）

- `git rev-list --count v1.0-daily..HEAD` = 13：tag 之后的提交（世界接管入口 `05db039`、意图 B1/B2、world 批复动作 `e7ca5b5`、voice-lab 收口 `66c743f`、pixel-world 迁仓标注 `249a071`）均不在本文记录内。
- `git status --short --branch`：master  ahead origin 5 个；未提交 17 项（5 个 M + 12 个 ??）。Phase 0 出口条件「工作区干净、origin 同步」当前不成立。
- pixel-world 已于 2026-10-25 整体迁往独立仓 `cs1lion/pixel-world-engine`（`pixel-world/MIGRATED.md`，源提交 `05db039`）；迁仓无交接票，母票 #45 未关，#48/#51/#52 在本仓查无交付证据（关票时点晚于 HEAD）。
- 门禁复跑（2026-10-25 两轮）：第一轮在 HEAD `249a071` 实跑，`check_protocol.py` EXIT 0、`test_core.py` 92 通过 0 失败、`run_evals.py` 离线层 7 通过 0 失败（10-24 口径为 6）；`scripts/run_gates.py` 的 GATES 实有 74 条（10-25 方案稿记 71）。第二轮在含本轮修复的工作区实跑，`npm test` EXIT 0 全链零失败。两轮都含未提交改动，不是对某个提交的干净复跑。
- 本轮修复（2026-10-25，全部可逆）：文档漂移与矛盾按上文各条修正；`run_gates.py:433` 交叉引用 #39 改 #41；D2 派单台词追加「确认按钮在战况抽屉的卡片上，点一下才开工」（`server/dialogue/orchestrator.py` delegation 分支，`scripts/test_executive_v3.py` 绑断言；README 两处「语音确认」同步改实）；`test_aqi_blocks.py` 一例陈旧测试修正（#33 收口后 clarify 只在验收相关输入触发，原测试输入「好了吗」已不触发），并补多单待验收时非验收输入落闲聊的回归断言。
- 分家补交付（2026-10-25）：交接票 #54 建（源提交 `05db039`/`249a071`、新仓 12 张 open issue 快照、#45 八子票逐条处置与证据）；#45 母票已关并指向 #54，#48–#53 各补一条复核评论；ROADMAP/README/USAGE/TASKS 的分家相关陈述已修。常驻 8000 仍跑 `05db039` 之前的代码（`/` 返旧聊天页、`/chat` 404），重启属项目红线，待用户批准后备份（`scripts/backup_local_databases.py`）再重启（`docs/TESTING.md:89-91`）。
- 8000 重启（2026-10-25，用户批准）：在线备 6 库（完整性 ok，快照 `data/backups/20261025T084403442211Z`）后重启。暴露并处置两个环境事实：常驻进程原用 anaconda3 python，其 NumPy 2.5.1 与编译版 scipy 冲突，重启即 `ImportError: numpy.core.multiarray failed to import`，改用 `docs/USAGE.md` 记载的 `.venv` 解释器；`.venv` 相对 `uv.lock` 缺语音栈，已按锁补齐（faster-whisper 1.2.1 / edge-tts 7.2.8 / ctranslate2 4.5.0）。验证：`/` 返世界页「莞尔的世界 · 神谕沙盒」、`/chat` 200、`/api/memories` 200、health 200；whisper 模型缓存（small/tiny）在位。入口反转（`05db039`）至此在运行态生效。
- open issue 9 个（`gh issue list --state open`）：#45、#41、#31、#19、#15、#14、#12、#10、#9。其中 #12/#10/#14/#19 为「已修未关」，关闭与否待 Role 0 拍板；#15 正确地开着（24 色重建后占比数据需重测）。
- 遗留待办：#31 索引重建（快环内同步，`server/memory/retriever.py:82-89`，内容变化后首次检索触发，非每轮）；评测报告层（`scripts/evals_report.py` 与 `docs/EVAL_MECHANISM_PLAN.md`）未入 git、无 issue。
