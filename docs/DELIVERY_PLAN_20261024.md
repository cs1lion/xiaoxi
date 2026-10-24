# 交付计划（2026-10-24 拍板版）

**Status:** 用户当日拍板四项方向，本文为唯一执行依据。执行中的状态回填到本文各 Phase 的复选框。
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
- [x] 0.3 五条 ahead 分支全并入（UIUX_AUDIT 冲突取 HEAD 侧：对家是带本机绝对路径的旧版，`git cherry` 证实补丁已在 master）；合并后四门禁复跑全绿再推；已并入分支与 tmp/wt-* worktree 清理，远端同步删除；backup/* 与 stash@{1} 保留。**例外**：`.claude/worktrees/confident-roentgen-3771a9` 里有 8 个文件的真实未提交工作（另一 session 在飞），保留不动，待其认领或过期后再议。
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

- [ ] 2.1 延迟与卡顿实测：基准脚本量首字延迟、打断响应、记忆检索耗时。不追求 500ms 红线保留，目标只有「不卡不炸」。
- [ ] 2.2 记忆管理 UI：「何时说过 / 停用」能力（日常真用高频痛点；PARKED_TASKS §A Memory 项）。事实有效期全量、逐条引用链按需再排。
- [ ] 2.3 容错演练：断网、Key 失效、WS 断连重连的真实场景过一遍（Mock 模式掩盖的路径），error 态文案补全。
- [ ] 2.4 每阶段出口跑 run_gates 全绿并记录（带 HEAD 锚）。

## Phase 3 — 界面分级瘦身

- [ ] 3.1 出分级清单（证据：使用路径、测试覆盖、耦合度；合并后的 uiux-audit / art-audit 两分支结论作为输入）。预判供否决：核心可见＝语音对话＋舞台＋记忆侧栏＋ops 抽屉；收二级＝interpret/practice/experience/writing/companion/music；演示专用＝avatar-lab、starmap、pixel-world 入口；待删＝card_preview、notepad_preview、design-preview。
- [ ] 3.2 用户一次性否决（一次问答，不来回）。
- [ ] 3.3 执行：导航重排＋视觉统一（24 色口径；调色板变更仍须 ADR）＋空态/错误态补全。stash@{1}（style.css 大改）在此决定吸收或丢弃。

## Phase 4 — 交付验收与打标

- [ ] 4.1 全量黄金命令 + run_gates 全绿，结果带 HEAD 与日期锚记录。
- [ ] 4.2 连续一天真实使用 dogfood，修复清单快速消化。
- [ ] 4.3 roadmap 落档：pixel-world 世界生成器愿景（见「已拍板」第 3 条）写入 PARKED_TASKS 或独立 ADR。
- [ ] 4.4 打 tag（建议 v1.0-daily）并附交付说明：怎么开机、怎么用、已知边界。

## 多 session 分工

- 每 Phase 一个独立 session，票据化执行（to-tickets 形态，GitHub issues 或 .scratch/ 本地票据），每票自带验收命令与 blocking edges。
- 文件所有权沿用 AGENT_PLAYBOOK 角色白名单：1.1 归 web 侧角色、1.2/1.3 归慢环侧角色、2.x 归稳定性专项、3.x 归 UI 侧（有打磨轮临时授权先例）。跨角色走 [PROTOCOL_REQUEST]，红线三条（executive.py、调色板、8000/8010 重启）不因本计划放宽。
- Phase 1 与 Phase 2 可两 session 并行（server/task 与基准/容错文件面基本不相交）；Phase 3 依赖 0.3 合并的审计分支；Phase 4 收口必须单 session 全局看。

## 本计划的边界

- 不含：pixel-world 生成器与新组件（roadmap）、IntentGate 接入（已否）、durable 全面接真、500ms 延迟目标、给第三方分发的安装工程。
