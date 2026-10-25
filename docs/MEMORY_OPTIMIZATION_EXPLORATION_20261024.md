# Ask Matt：Markdown 真的是最好的记忆吗？——小溪记忆优化探索报告

> 探索报告 · 2026-10-24 · 由多智能体工作流（理解项目 → 研究开源 → 差距分析 → 对抗式验证 → 综合，13 个 agent）产出，承重论断已对照源码抽查。
> 定位：探索（exploration），不是实施方案。候选均为"可撤销的前端/后端改动 + 需 Role 0 拍板处已显式标注"，落地前仍需各自立项与跑通黄金命令。

---

## 开场：把命题立起来

**Ask Matt：Markdown 真的是最好的记忆吗？**

先把「Markdown 是最好的记忆」这个命题完整立起来——它可审计、可 `diff`、可 review、可手改，备份就是 `git clone`、迁移就是 `rsync`，零供应商锁定、零基础设施，快环无阻塞。对一个需要长期记忆的 Agent，这几乎像是最优解。这条命题在 2026 年的代表正是 **akitaonrails/ai-memory**（Fabio Akita，MIT，Rust 单二进制）：以 git 托管的纯 Markdown wiki 作为唯一事实源，数据库只是可随时重建的派生索引，默认零 LLM，跨 Claude Code / Codex / Cursor 等 20+ agent 交接，口号是 "Your memory is plain markdown."

但对小溪这样的中文语音陪伴 Agent，经过反方"矛盾并存"的实证、HaluMem 六套系统的召回实测、以及项目自身三档置信度与物理硬删红线的检验后，裁决不是全盘肯定或否定，而是**分层成立与否**。

---

## 执行摘要

结论先行：**「Markdown 是最好的记忆」这个命题对小溪作为唯一记忆不成立**；但成立的条件版本是「**Markdown 作为人可审计的策展层与只读派生投影层，SQLite 作为真相源**」——小溪已经处在这个分层，且已独立收敛出三档置信度、负向约束、物理硬删、使用学习、lessons 与 supersession 这些恰是纯 markdown 缺失的**写入期整合语义**。

分三层看：

- **真相层**：纯 markdown 只能 append，反方实证的"同一偏好三个矛盾版本"就是缺 add/update/supersede/expire/delete 语义的后果。
- **索引层**：ai-memory 的「git 树 + SQLite FTS5 派生索引」恰恰证明**检索可换、真相不必动**，与小溪 SQLite 真相源不冲突。
- **装配层**：HaluMem 显示 Mem0/Mem0-Graph 的 Medium 召回仅约 43%、长上下文崩到约 3%、准确性无一超 62%，"该记的不能漏"没有谁解决，**装配必须按会失败设计**。

最重要的三条建议：

1. **（P0）MEM-OPT-03**：用 `ast` 静态契约门 + 单一出口，消灭"只取 block 就丢负向约束"。纯可逆、零协议风险；收益诚实表述为**保护 eval 与未来调用方**，而非"生产最大单点失效"。
2. **（P0）MEM-OPT-05**：默认关闭的 pending usage 清扫器；收益下调为**开放行有界与可观测**，删掉无消费者支撑的"学习信号"说法。
3. **（P1）MEM-OPT-01**：把记忆装配卸载出事件循环；收益是**库锁竞争从冻全局降为拖本轮**（不是首字缩短），必须先产出带 HEAD+日期锚的新 `LATENCY_BASELINE`。

其余：**MEM-OPT-02** 必须先跑 SQL 交叉证据再定性（命中为 0 就从"现存泄露"改写为"契约预留未兑现"）；**MEM-OPT-04** 拆两期，第一期就必须做序列化侧显式排除，否则等于未申请 `[PROTOCOL_REQUEST]` 而外露新字段。

---

## 命题裁决（分层）

**总裁决：命题对小溪作为唯一记忆不成立；作为人可审计策展层 + 派生投影层成立，且小溪已是这个分层。不应为了"markdown 最好"把真相源倒回文本——那会同时丢掉事务性、可查询的置信度字段与硬删能力。**

**真相层：不成立。** Markdown 作为唯一真相源无法原生表达置信度分级、事实何时为真/何时失效、以及从交互结果聚合的学习信号。反方（stopusingmarkdownformemory 一脉）的实证是同一偏好三个矛盾版本并存、重复写入不去重，根因是只有 append、没有 add/update/merge/supersede/expire/delete 六种操作——长期记忆至少需要写入期整合，而 git 只给 transaction time、不给 event time。
需公道的是：反方清单里「整篇塞几千字上下文」是**装配策略失败**而非存储格式失败（向量库无脑 top-k 同样会塞），「规则只是文本可被忽略」是**执行层未把规则编译成代码路径**——这两条不属于 markdown；真正只属于纯 markdown 的是**矛盾并存与不去重**。

**索引层：部分成立。** ai-memory 的两层结构（git 版本化 .md 真相树 + SQLite FTS5 + 本地 embedding 派生索引）给出了全案最可迁移的工程判断——**检索可以换、真相不必动**。对小溪可直接借的是零 LLM 的 SQLite FTS5（中文需处理分词与 query 改写）+ 可选本地 embedding 召回路径，全部离线、无阻塞、契合快环约束；而真相层仍留在 SQLite。

**装配层：不成立为唯一载体，但成立为人审策展层。** HaluMem（Mem0 42.91% / Mem0-Graph 43.28% 的 Medium 召回，Long 降至 3.23% / 2.24%，准确性无一超 62%，并提出 FMR：忽略助手提及但未经用户确认的干扰记忆）证明结构化并没有解决"该记的不能漏"，**装配必须按会失败设计**——未确认记忆默认降权或不注入，不得把检索结果当事实陈述。这同时反证纯 markdown 更糟，因为它连按置信度 SQL 过滤都做不到。Markdown 在小溪的正确位置是 MEMORY.md/USER.md 式小而精的策展层与可导出人审快照，**不是真相源**。

---

## 小溪已独立收敛出的成熟机制

- **三档置信度装配**：`inferred`/`uncertain` 有 72h 硬过期而 `stated` 保守永不过期（`retriever.py:138,158-161`），可按档在 SQL 里过滤——纯 markdown 人肉标签做不到。
- **检索为空显式注入负向约束、禁止脑补共同经历**：`persona.py:224-230`（"编造共同经历是最严重的错误"），加上 `omitted_by_budget` 支路 `orchestrator.py:517-518` 的第二道约束（两者是不同家族）。
- **记忆删除是 SQLite 物理硬删不留索引**：与 ai-memory"永不硬删"相反；目前 `_scrub_deleted_indexes`（`store.py:772-804`）只覆盖 `memory_usage.memory_ids`、`memory_usage.reward_detail`、`lessons.evidence_refs` 三处——正是 MEM-OPT-02 的靶点。
- **使用学习**：`memory_usage` 记注入与结算、Q 值/utility 链路（`retriever.py:162-168` 的 utility 只能对已过相关性门的候选重排，不能创造相关性）。
- **lessons 程序性记忆**：active lessons 有 `retire_overflow` 的 `max_active` 上限压制（默认 24）。
- **supersession**：upsert 链、`supersedes` 指向被删行置 NULL 断链。

**与开源方案的关系**：这些恰是 markdown 阵营缺失的写入期整合语义——小溪已站在"SQLite 真相源 + 写入期整合"一侧，与 Mem0 的 update 阶段、Graphiti/Zep 的双时序模型、Letta 的 Core Memory 字符预算构成**互补参照而非照搬对象**。小溪与它们的差距集中在**时间有效性（MEM-OPT-04）、索引清扫完整性（MEM-OPT-02）、core memory 预算（本轮未覆盖）**，而不是"要不要用 markdown 当真相源"——那个方向是倒退。

---

## 候选优化方案

> 评级：verdict 全部为 **revise**（初稿经对抗式验证后修订）；priority 为 P0/P1/P2。每条已标注触碰的红线与需 Role 0 拍板处。

### MEM-OPT-03 · 负向约束单出口交付 + `ast` 静态契约门 · P0 · 工作量 low

**问题。** 检索为空时注入的负向约束（`persona.py:216-230`）依赖调用方同时拿到 `block` 与 `found` 并正确传给 persona 拼装层；`build_context` 返回三元组（`retriever.py:174-206`，第三值 `found_anything` 的 docstring 明确说漏注会让模型"confabulate a shared memory"），任何只取第一项的新调用方会静默丢掉负向约束。**诚实重述**：生产快环 `orchestrator.py:572` 是解包三元组后丢弃 `_found`、改由 `_memory_records(block)` 解析 status、用 `memory` 的 status 决定 `memory_found`——生产路径用 block 自身当地基，不依赖第三返回值透传；真实暴露面在 **eval 与黄金命令路径**（run_evals、eval_memory、eval_personalization）与未来新调用方。全库 `build_context(` / `context_with_status(` 调用点 12+ 处。

**方案。**
1. **前置静态契约门为最高价值项**：新增 `scripts/check_memory_contract.py`，用 `ast`（非正则）解析全库调用点，断言返回值被解包为三项或改走 `deliver_context`，漏一项即使检查失败，并纳入黄金命令；上线前必须拿现有 12+ 调用点实测全绿，否则第一天假红。
2. `retriever.py` 增加唯一出口 `deliver_context(...)` 返回 `MemoryContext`（frozen dataclass：block/used_ids/found/provenance_lines），只透传 found 标志；`build_context` 保留为薄包装不破坏现有调用方；负向约束文本与 persona 引用同一常量、persona 仍是唯一注入点，杜绝第二真值源。
3. 负向约束文本**不下沉** `presentation.py`（违背其纯记录投影层定位），改放中立共享常量模块或留在 `persona.py`。
4. 要么把 `omitted_by_budget` 支路文案一并纳入约束家族单出口，要么显式限定范围为"检索为空"一种并文档标注另一种独立存在。

**触碰文件**：`server/memory/presentation.py`、`server/memory/retriever.py`、`server/memory/manager.py`、`server/persona.py`、`server/app.py`、`scripts/run_evals.py`、`scripts/bench_voice_loop.py`、新增 `scripts/check_memory_contract.py`。
**红线**：不新增对外字段、不改 WS 事件/三档置信度/provenance、不碰 executive.py。唯一红线：不得把负向约束文本经 API/WS 外露给前端展示（那才触发 `[PROTOCOL_REQUEST]`）。**收益需诚实**：该文案当前只存在于 persona.py 一处、并无漂移，单出口是**前瞻性收益**而非现存 bug。
**收益**：把"漏传 found 就丢负向约束"从依赖程序员记得传参，变成漏传就 CI 红。纯可逆，可直接开工。

### MEM-OPT-05 · pending usage 过期清扫器（独立 sweeper + 独立挂点，默认关）· P0 · 工作量 low

**问题。** `settle_pending`（`feedback.py:138-162`）只在 `observe_turn` 被触发且按 session 隔离时运行；会话若不再产生新 user message，`memory_usage` 中 `settled_at IS NULL` 的行永远不结算。**两处失实须纠正**：配置白名单在代码里（`feedback.py:30-37,64-69`）而非 `config/memory.yaml`，只往 yaml 加键会被静默丢弃成死配置；完全复用既有 expired 分支不成立——`pending_usage` 是 session 隔离且必须传 `before_message_id`，sweeper 需要跨 session、按 `created_at` 过滤的新查询。**收益也被夸大**：全仓 `settled_at IS NULL` 的唯一读者是 `settle_pending` 自己，排序与 Q 值链路只读 `memories.utility`，sweeper 不产生任何学习信号；本地 `memory_usage` 为 0 行、效用在生产里一次都没被结算过。

**方案。**
1. `feedback.py` 新增 `sweep_expired_usage(store, *, now=None, batch=None)`：只扫 `settled_at IS NULL` 且 `created_at` 早于 `now - max_settle_age_hours` 的行，逐行 `store.settle_usage(id, reward=None, reason=expired)`（不传 invalidate，不推高 write_epoch），整体 try/except 只记 trace。
2. `store.py` 新增 `expired_pending_usage(before_ts, limit)` 与 `count_open_usage()`。
3. **挂点从 `loop.py` 挪到 `server/app.py` 的 `_maybe_self_evolution` 开头**、SelfEvolutionCycle 之前，只受 `feedback.enabled + sweep_enabled` 门控并用 `last_sweep_at` 节流——避免 reflect 正常返回 ok 时 sweeper 永远不执行，也避免被 self_evolution 的 LLM 节流饿死。
4. `FEEDBACK_DEFAULTS` 增加 `sweep_enabled=False`、`sweep_batch=200`、`sweep_interval_minutes=30`，默认关，缺配置时行为与今天逐字节一致。
5. 测试写进 `scripts/test_memory_self_evolution.py` 的 FeedbackTests（`test_core.py` 无任何 feedback/settle 测试），覆盖：开放行被记 expired 且 utility_n 不变、窗口内不扫、幂等、批上限、enabled=False 一行不扫、无后续用户消息（following 为 None）分支。

**触碰文件**：`server/memory/feedback.py`、`server/memory/store.py`、`server/app.py`、`config/memory.yaml`、`scripts/test_memory_self_evolution.py`、`docs/MEMORY_SELF_EVOLUTION.md`。
**红线**：不改奖励语义、不新增 WS/API 字段、不动 executive.py、不新增 Worker；同步写留在慢环异步路径，实现时保持不传 invalidate=True 并用 batch 限制单轮事务数。**触发源风险须写入工单**：`_proactive_check` 由客户端事件驱动、无服务端定时器，用户关浏览器即无触发，开放轮次有界在真实部署下无硬保证。
**收益**：下调为 `memory_usage` 开放行有界 + `count_open_usage` 可观测、离线可断言上限。纯可逆，默认关，可直接开工。

### MEM-OPT-01 · 快环记忆装配整体卸载到 executor · P1 · 工作量 medium

**问题。** `orchestrator._turn_context` 是同步函数，在 `stream_respond` 中、首个 `yield thinking` 之前直接跑在事件循环线程（`orchestrator.py:648`，首个 yield 在 654）：`manager.retriever.build_context` 做同步 SQLite 全表读加 TF-IDF fit/transform（`retriever.py:131-132`）、`manager.lessons_for` 对 active lessons 做 difflib 扫描、`mark_used` 同步写库，全部计入首字路径；库锁被占时阻塞的是整个事件循环而非当前连接，违反"快环内不得出现阻塞调用"红线。基线参照 `docs/LATENCY_BASELINE.md`（HEAD 94b1abe，2026-10-24）turn→thinking p50 仅 35.3ms，大头在装配周边；规模上 issue #31 外推 3k 条约 200ms 冻结点。

**方案。**
1. `orchestrator.py:648` 改为 `await asyncio.to_thread(self._turn_context, ...)`，保留同步版供文本通道与测试；已核对 `_turn_context` 体只涉 SQLite、context_broker、limit_context、guard_for 与 JSONL trace，不接触 loop 绑定对象，可安全跨线程。
2. 同段 `store_recent`/`capture_write_epoch`/`store_message` 并入同一次 to_thread 批次，批次内严格保持原序：`capture_write_epoch` 先于 `store_message` 是异步抽取防硬删护栏语义，`mark_used` 必须晚于 `store_message`。
3. 计时测试驱动 `stream_respond` 真实入口、spy `_turn_context` 断言其执行线程非事件循环线程——不得直接调 `_turn_context`，否则删掉 to_thread 包装测试照样绿；并补 `_turn_context` 抛异常仍向上传播的回归。
4. 评估专用单线程 executor，避免与 ASR 的 `anyio.to_thread` 争抢默认线程池。
5. 验收用 `scripts/bench_voice_loop.py` 指标 A/B/C 出新 `LATENCY_BASELINE`（写 HEAD+日期锚），并明确 mock 口径约六成首字是桩节奏。
6. `limit_context` 以 `_render_turn_context` 当测量回调导致的每轮重复同步读库另立单，本轮不动。

**触碰文件**：`server/dialogue/orchestrator.py`、`scripts/test_core.py`、`scripts/bench_voice_loop.py`、`docs/CONTEXT_BOUNDARIES.md`、`docs/LATENCY_BASELINE.md`。
**红线**：不碰 executive.py、不新增 WS 事件/API 字段、不动三档置信度/硬删除/负向约束语义，无需 `[PROTOCOL_REQUEST]`。残留风险须写入工单：批次内写顺序语义；并发 turn 批次线程级交错；difflib"最多 1000 条"实为读取上界，active lessons 被 `max_active`（默认 24）压制，不得夸大单项成本；跨线程安全目前只有代码走查依据，无并发回归覆盖。
**收益（诚实口径）**：本单收益**不是首字延迟缩短**（messages 不齐就无法开流，首字基本不变），而是**事件循环隔离**（barge_in 亚毫秒取消路径与其他连接的音频流不被冻结）与库锁竞争时从冻全局降为只拖慢本轮；验收以带 HEAD+日期锚的新基线前后对比为准，不写无锚毫秒数。

### MEM-OPT-02 · 硬删除索引清扫扩展到全部 memory id 引用点 · P1 · 工作量 medium

**问题（须先证伪）。** `_scrub_deleted_indexes`（`store.py:772-804`）目前只清 3 处。初稿指认的 `seeds.source_refs`、`reflections.source_refs`、`mood_state.source_refs`、`graph_edges` 与 `commission_context` 留存 memory id，被对抗评审逐条推翻：`commission_context.py` 是纯函数模块不落库；`graph_edges` 记忆节点用 `mem:` 前缀且 starmap 已按端点存在性过滤；四条生产路径的写入方取值域今天都不产 `memory:`。因此当前问题表述必须**降级为"契约预留但未兑现"的潜在面**——`PROTOCOL.md:222` 与 §18.2 唯一指定指向 memory 的字段是 `seeds.related_refs`，而它在生产中全程为 `[]`。**落地前必须先跑 SQL 交叉核对真实命中数；若命中为 0，本单只做注册表基建与 related_refs 靶点的契约兑现，优先级随之下调。**

**方案。**
1. 先补证据：跑一条 SQL 把 memories 全部 id 与 `seeds.source_refs`/`seeds.related_refs`/`reflections.source_refs`/`mood_state.source_refs`/`graph_edges.source,target`/`lessons.evidence_refs` 交叉，贴出真实命中数。
2. `store.py` 增加**声明式注册表**，每行必须带**删除策略**（保留审计行只摘索引 / 清端点 / 删行），不能只有表名-列名形态三元组；`memory_usage` 保留行只摘索引的现行取舍写进注册表注释。
3. 靶点换成 `seeds.related_refs`（PROTOCOL.md 唯一指定指向 memory 的字段），删掉对 `commission_context.py` 的指认。
4. `graph_edges` 单独处理、不塞进同一张注册表：清 `memory:` 端点不改变星图输出；真正要定的是行级策略——一端 `memory:`、另一端活 seed 时删整行会摧毁 seed 侧真实的自进化边，建议**只清端点不删行**，并补 `parent_child`/`epiphany` 不受影响的回归。
5. 回归测试挂 `scripts/test_memory_self_evolution.py`，硬删后断言任何表查不到该 id。
6. 同步改 `docs/MEMORY_SELF_EVOLUTION.md` §3.5 保证范围与 `docs/PROTOCOL.md` §14.1/§14.2/§18.2 的 `memory:` 命名空间讨论。

**触碰文件**：`server/memory/store.py`、`server/memory/seeds.py`、`server/memory/diary.py`、`server/memory/starmap.py`、`scripts/test_memory_self_evolution.py`、`docs/MEMORY_SELF_EVOLUTION.md`、`docs/PROTOCOL.md`。
**红线**：本单是**兑现**而非违犯物理硬删除红线，属可逆后端改动，按 poteto-mode 直接做，**明确不需要 `[PROTOCOL_REQUEST]`**。需 Role 0 的只剩一条：是否把 `memory_usage` 审计行本身纳入删除。另注意 `lessons.evidence_refs` 的 `memory:` 分支在生产可能无真实写入方，不得以"已有先例"论证；`_scrub_deleted_indexes` 每次删除对 `memory_usage` 与 `lessons` 全表扫描，扩表后删除延迟随行数线性增长，需量级评估与分批策略（delete 属管理端显式动作，不触碰快环红线）。
**收益**：忘掉一条记忆后任何表都不可 join 回该 id，消除"删了但仍能被引用"的隐私与幻觉双风险；若 SQL 证据为 0，收益收敛为注册表基建 + related_refs 靶点兑现，**不得宣称修复现存泄露**。

### MEM-OPT-04 · 落地时间有效性 valid_from/valid_until（两期）· P2 · 工作量 high

**问题。** `docs/MEMORY_SELF_EVOLUTION.md` §6.4 与 `docs/MEMORY_RESEARCH_NEXT.md` 均列为未完成项：没有 valid_from/valid_until，"现在在哪工作"与"去年在哪工作"无法区分；当前唯一时间手段是 inferred/uncertain 的 72h 硬过期（`retriever.py:158-161`），stated 永不过期。初稿三处硬伤：valid_from 只取消息时间实现的是 asserted_at 而非事实有效时间（三时间纪律：区分消息时间/写入时间/事实有效时间）；与 supersession 的交互未定义而它恰是问题场景主路径；加列会经 `to_dict` 自动外露到 `/api/memories`，属**潜在 `[PROTOCOL_REQUEST]` 违犯**——在这份代码里外露是默认后果，不是可选项。

**方案（分两期）。**
- **第一期（纯内部、零对外形状变化）**：加列遵循仓库惯例（写入 SCHEMA 与 additions 字典即可，不升 SCHEMA_VERSION）；**序列化侧显式排除**，确保 `/api/memories` 响应逐字段不变——这一步不是可选项，不做就等于未申请 `[PROTOCOL_REQUEST]` 而外露新字段；valid_from 按三时间纪律重做（仅当原始消息含可解析显式时间表达时填事件时间，否则 NULL，不得用消息时间冒充）；valid_until 只由代码级废止措辞门触发（对齐 `preference_change_allowed`）；检索过滤三条硬规则（时点检测只认原始 user message、NULL 永不排除、过滤置于 top_k 截断之前）；supersession 交互本期明确不做；评测收窄为离线程序行为。
- **第二期**：待 Role 0 定 `/api/memories` 外露口径后再议。

**触碰文件**：`server/memory/store.py`、`server/memory/manager.py`、`server/memory/extractor.py`、`server/memory/retriever.py`、`server/memory/presentation.py`、`config/memory.yaml`、`scripts/run_evals.py`、`docs/MEMORY_RESEARCH_NEXT.md`、`docs/MEMORY_SELF_EVOLUTION.md`。
**红线**：潜在违犯——给 Memory 加字段即经 `to_dict → snapshot → GET /api/memories` 自动外露，而 PROTOCOL.md 未登记该端点响应结构、check_protocol.py 只覆盖已登记 API 拦不住；必须先做序列化侧显式排除，任何外露先 `[PROTOCOL_REQUEST]`。schema 升版与迁移回滚需 Role 0 确认。NULL 语义、时点检测误报率、与 supersession/conflict 的行为矩阵均未定义；迁移验收只允许在合成临时副本上进行。
**收益（诚实口径）**：第一期可交付"两条并存 active 事实按证据时点分辨"；**"换工作/去年在哪工作"的重述场景在 supersession 交互未做前收益落空**（旧行已被 superseded、status 门先剔除，时点查询仍只得空结果 + 负向约束），该收益表述已收回。旧查询路径逐字节一致，保证现有评测集不回归。

---

## 推荐排序（优先级 × 可行性）

1. **MEM-OPT-03**（P0/low，纯可逆，直接开工）
2. **MEM-OPT-05**（P0/low，默认关，直接开工）
3. **MEM-OPT-01**（P1/medium，先出新 LATENCY_BASELINE 再动）
4. **MEM-OPT-02**（P1/medium，先跑 SQL 证据，命中为 0 则降级为 PARKED）
5. **MEM-OPT-04**（P2/high，两期，第一期先做序列化侧排除）

---

## 风险与需 Role 0 拍板

- **MEM-OPT-02 证据缺口**：至今没有任何 SQL 证明 seeds/reflections/mood_state/graph_edges 现存行含 memory id；必须先跑交叉查询再定性，命中为 0 就把 problem 从"现存泄露"改写为"契约预留但未兑现"，否则按证据不足降级为 PARKED。
- **Role 0**：是否把 `memory_usage` 审计行本身纳入删除（现行取舍是保留行只摘索引，改了会丢失"那一轮为什么注入过"的解释力）。
- **Role 0**：PROTOCOL.md §14.1/§14.2/§18.2 的 `memory:` 命名空间保留还是收窄——收窄命名空间是比扩大清扫更根本的改法。
- **Role 0**：MEM-OPT-04 的 `/api/memories` 响应结构未在 PROTOCOL.md 登记，加字段是否算新增对外字段需口径确认；任何外露先 `[PROTOCOL_REQUEST]`。
- **Role 0（如坚持升版）**：MEM-OPT-04 schema v4→v5 与迁移回滚策略；仓库惯例是加列不升 SCHEMA_VERSION。
- **MEM-OPT-01 测量缺口**：lessons_for/difflib 每轮耗时无实测；"库锁冻结全局事件循环"是推理非实测；跨线程安全只有代码走查无并发回归；验收数字必须是带 HEAD+日期锚的新基线。
- **MEM-OPT-01 工程风险**：ASR 已占用默认线程池（`anyio.to_thread`），并发多轮装配可能排队在其后。
- **MEM-OPT-03 校验器风险**：静态门必须 ast 而非正则，且须对现有 12+ 调用点实测全绿再纳入黄金命令，否则第一天假红。
- **MEM-OPT-05 触发源与语义风险**：`_proactive_check` 由客户端事件驱动、无服务端定时器，关浏览器即无触发；aging-only 判据对长会话的误判等价性无文档。
- **mock 口径限制**：约六成首字延迟是桩节奏，所有延迟收益声明都受此限制；live 评测需 LLM_API_KEY 用 `--live` 手动跑，本轮未跑，不得把离线重放表述为真实模型成绩。

---

## 本轮未覆盖（留待后续立项）

- `limit_context` 以 `_render_turn_context` 当测量回调导致的每轮重复同步读库——基线显示那才是 36ms 里的大头。
- 过去时点查询是否允许读 superseded 行（恰是"去年在哪工作"场景主路径）。
- 慢环自身的同步写（save_self_state/add_spark 等）是否也卸载进 executor。
- **HaluMem FMR**（忽略助手提及但用户未确认的干扰记忆）的落地机制——研究中识别的关键约束，本轮无对应候选。
- SQLite FTS5 + 本地 embedding 的快环召回升级（借 ai-memory，中文分词与 query 改写待处理）。
- 可导出 markdown 快照供人审阅与备份（借 ai-memory 可审计性，且不引入多写入方）。
- 腾讯云四层装配授权映射（L0 原始对话 / L1 事实偏好约束事件 / L2 场景知识块 / L3 Core/Persona）与 Letta Core Memory 字符上限 + 超限驱逐。
- MEM-OPT-04 第二期（`/api/memories` 外露口径定下之后）。
- 本轮未跑任何 SQL 数据盘点、未产出 LATENCY_BASELINE 新基线、未跑 live 评测。

---

## 来源

- akitaonrails/ai-memory（"Your memory is plain markdown"）：https://github.com/akitaonrails/ai-memory ；https://akitaonrails.com/en/2026/09/02/ai-memory-2-0-best-memory-system-for-agents-and-teams/
- 反方 stopusingmarkdownformemory：https://segmentfault.com/a/1190000047685525 ；https://blog.csdn.net/weixin_42615290/article/details/161065186
- Mem0：https://arxiv.org/abs/2504.19413 ；https://github.com/mem0ai/mem0
- HaluMem（质量基准）：https://github.com/MemTensor/HaluMem ；https://arxiv.org/abs/2511.03506
- EvoMemBench：https://arxiv.org/abs/2510.08191 ；https://www.aminer.cn/pub/68f6d87c163c01c850b8c943/
- Letta/MemGPT：https://github.com/letta-ai/letta
- Zep/Graphiti（双时序）：https://github.com/getzep/graphiti ；https://github.com/getzep/zep
- Hindsight（retain/recall/reflect）：https://github.com/vectorize-io/hindsight ；https://aihot.virxact.com/items/oactlhauxke4xq1kl9xt97qol
- 小溪 issue #31（装配规模外推）：https://github.com/cs1lion/xiaoxi/issues/31
