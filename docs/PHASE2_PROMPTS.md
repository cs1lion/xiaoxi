# Phase 2 — 第一批角色派单（Role 1 / 2 / 3）

**签发：** Role 0（Chief Architect），2026-10-14，基线 `v0.5-phase1-freeze`（commit `a6620c5`）。
**用法：** 下面每个角色的 Prompt 是一个整体，原样复制作为该 Agent 的第一条消息发出。契约以 `docs/PROTOCOL.md` 第十至十三节为准；规则以 `docs/AGENT_PLAYBOOK.md` 为准。两份文档与本文冲突时，以协议和宪章为准。

---

## Prompt A — Role 1（记忆内化与自进化 Agent）

```text
你是 neurosama 项目的 Role 1（记忆内化与自进化）。第一阶段已封盘（tag v0.5-phase1-freeze），现在执行第二阶段 F1/F3/F4-底层。

【必读】按顺序完整阅读：docs/AGENT_PLAYBOOK.md、docs/PROTOCOL.md（重点第十、十二、十三节）、server/memory/manager.py、server/persona.py、server/assistant/loop.py、config/memory.yaml、config/persona.yaml、server/memory/store.py。

【文件读写白名单】
- 可写：server/memory/**、server/persona.py、server/assistant/loop.py、config/memory.yaml、config/persona.yaml、scripts/test_episodic_diary.py（新建）
- 可读：全仓库
- 禁止写：server/app.py、web/**、docs/PROTOCOL.md、scripts/check_protocol.py、server/dialogue/**、server/speech/**、config/pacing.yaml。确需跨界时，停止越界并输出 [PROTOCOL_REQUEST]（含目标文件、原因、建议改法），等 Role 0 处理。

【协议对齐要点】（docs/PROTOCOL.md 第十至十三节，字段名以协议为准）
- diary/drift/spark 三种结构与全部上限；bias_block(drifts, sparks) 纯函数契约（空输入返回空串、包"不可信数据"边界、latent 火花禁止外泄）。
- WS 事件 diary_notice、self_evolution 的精确形状；self_state 结构与 self_evolution 配置段（落 config/memory.yaml，默认 enabled:false）。
- /api/memory/diary、/api/memory/starmap、/api/memory/starmap/node/{id} 的请求参数与响应结构。

【任务 F1：按天日记与偏置】
1. server/memory/ 新增 diary 模块：当天消息 → 第一人称日记的合成与程序化校验（date/mood/energy/长度上限/source_message_ids 必须真实存在，任一不满足拒绝入库），SQLite 新表 + 向后兼容迁移（旧库直接打开可用，不重置不丢数据）。
2. drift/spark 的落库、状态流转（latent→surfaced→spent）与上限控制（drift≤8 轮生效、latent≤5）。
3. persona.py 增加 bias_block()，附单元测试；调用方（orchestrator 动态提示词）由 Role 2 按协议接入，你不要改 server/dialogue/。
4. diary_notice 事件的 payload 构造函数放在你的模块里；实际发送点在 orchestrator/app.py，输出 [PROTOCOL_REQUEST] 给 Role 0 接线。

【任务 F3：后台自进化循环】
1. assistant/loop.py 增加自治模式：空闲（距上轮 ≥ idle_minutes）时基于好奇心种子复盘最近日记/记忆，产出 taste/worldview 更新与新 spark；每轮有界（模型调用数、状态变更数都设上限），全程落 trace，失败静默降级不影响主对话。
2. self_state 持久化在记忆库（独立表或 subject="self" 记忆，二选一，在实现说明里写清理由）；自进化写入严禁 subject="user"。
3. self_evolution 事件 payload 构造 + [PROTOCOL_REQUEST] 接线；主动开口（source:"self_topic"）的文本生成函数放你的模块（第一人称、≤120字、只基于 taste/worldview/spark，不补充节点外事实），orchestrator 固定回复交付器的入口由 Role 2/0 提供。
4. config/memory.yaml 增加 self_evolution 段，默认全关。

【任务 F4：星图底层数据】
1. server/memory/ 实现 starmap 图谱查询（协议第十三节）：nodes/edges/stats/truncated，limit 硬上限 400，记忆关闭时返回空图而非报错；节点详情 node/{id} 返回全文，不存在 404。
2. REST 路由用 create_*_router 工厂模式（参照 server/assistant/api.py），把工厂和 include_router 的确切 diff 放进 [PROTOCOL_REQUEST] 交 Role 0 合入 server/app.py。

【验收命令】（全部执行，结果附汇报）
1. .\.venv\Scripts\python.exe scripts\check_protocol.py → PASS
2. .\.venv\Scripts\python.exe scripts\test_core.py → 38 passed / 0 failed（基线零回归）
3. .\.venv\Scripts\python.exe scripts\test_memory_api.py、scripts\test_memory_orchestrator.py、scripts\test_agent_loop.py、scripts\test_memory_store_contract.py → 全绿
4. 新建 .\.venv\Scripts\python.exe scripts\test_episodic_diary.py：覆盖日记校验拒绝（空证据/超长/逐字引用）、bias_block 空输入与上限、spark 状态机、starmap 结构与截断、记忆关闭空图 → 全绿
5. 改动数据库结构前后各跑一次 .\.venv\Scripts\python.exe scripts\backup_local_databases.py

【禁止】发明协议外事件或字段；把 latent spark 写进任何提示词/事件/接口；日记当 stated 事实参与检索；自进化写 subject="user"；修改白名单外文件。
```

---

## Prompt B — Role 2（Neuro 式长语音与演出编排 Agent）

```text
你是 neurosama 项目的 Role 2（长语音与演出编排）。第一阶段已封盘（tag v0.5-phase1-freeze），现在执行第二阶段 F2，并配合 F1/F3 的提示词接入。

【必读】按顺序完整阅读：docs/AGENT_PLAYBOOK.md、docs/PROTOCOL.md（重点第二、七、八、十一节及第十/十二节的接入点）、server/dialogue/orchestrator.py、server/dialogue/delivery.py、server/speech/tts.py、web/avatar/timeline.js、config/pacing.yaml。

【文件读写白名单】
- 可写：server/dialogue/**、server/speech/**、config/pacing.yaml、web/avatar/timeline.js、scripts/test_micro_delivery.py（新建）
- 可读：全仓库
- 禁止写：server/app.py、web/app.js、web/index.html、docs/PROTOCOL.md、scripts/check_protocol.py、server/memory/**、server/persona.py、server/assistant/**。确需跨界时输出 [PROTOCOL_REQUEST]。

【协议对齐要点】（docs/PROTOCOL.md 第十一节）
- audio 事件新增可选 chunk={seq,of,chars,pause_after_ms,breath}；可选 speech_plan 事件；index/seq 单调不减；失败段跳过不卡播；barge_in 后该轮零 audio；performance 锚点规则不变；audio.text 仍是口型地基。
- 接入点：orchestrator 动态提示词路径调用 persona.bias_block()（Role 1 交付，协议第十节）——只读调用，不改 persona.py；bias_block 未就绪时按空输入行为等价现状。

【任务 F2：微句流 + 预加载队列】
1. orchestrator._pump_audio 改造：微句切分（子句边界优先、目标 12-25 字、60 硬顶，split_for_speech 增加微句模式而非改旧签名）；audio 事件附 chunk 字段；首段前发可选 speech_plan。
2. 预加载：播放中合成后续段（并发度有界，建议 2）；单段失败跳过该 index；barge_in/取消时取消未完成合成，保证 interrupted 之后零迟到 audio；done.delivery 计数口径不变。
3. tts.py：长文本按微句送入，synthesize(text, emotion) 签名保持兼容（delivery.py 与 orchestrator 共用）；如需流式能力，新增方法不改旧签名；silent/offline 引擎行为不变。
4. timeline.js：读 chunk.pause_after_ms/breath 做呼吸停顿与微表情插入；字段缺失时与现状完全一致；viseme 时间轴仍以 audio.text + 解码时长为准，停顿不摊平后续词时序。
5. config/pacing.yaml 增加 speech 段（micro_chunk 目标/上限、预加载并发、停顿范围），默认值保证"关闭新特性时等价现状"。

【验收命令】（全部执行，结果附汇报）
1. .\.venv\Scripts\python.exe scripts\check_protocol.py → PASS
2. .\.venv\Scripts\python.exe scripts\test_core.py → 38 passed / 0 failed
3. .\.venv\Scripts\python.exe scripts\test_voice_turns.py、scripts\test_reply_delivery.py、scripts\test_speech_performance.py → 全绿
4. 新建 .\.venv\Scripts\python.exe scripts\test_micro_delivery.py（用假 TTS/注入 fake，不调用真实供应商）：切分边界（标点/引号/超长单句）、index 与 seq 单调、失败跳过、打断后零迟到 audio、chunk 缺失时契约不变 → 全绿
5. node scripts\test-speech-performance-browser.mjs 与 npm run test:voice → 全绿（动作锚点与语音链路零回归）

【禁止】改动 audio.text/emotion/data 既有语义；发明协议外事件；真实 TTS 供应商调用进测试；修改白名单外文件。
```

---

## Prompt C — Role 3（星图记忆与统一工作台 UI Agent）

```text
你是 neurosama 项目的 Role 3（星图记忆与统一工作台 UI）。第一阶段已封盘（tag v0.5-phase1-freeze），现在执行第二阶段 F4 前端。

【必读】按顺序完整阅读：docs/AGENT_PLAYBOOK.md、docs/PROTOCOL.md（重点第十三节，及第六/七节的 turn 与打断规则）、docs/UI_DESIGN.md、web/app.js（消息 switch 与视图切换）、web/index.html、web/avatar/timeline.js（只读，Role 2 领地）。

【文件读写白名单】
- 可写：web/**（不含 web/avatar/drivers/，该目录视为只读）、server/interaction/**、web/starmap/test_starmap.mjs（新建，单测）
- 可读：全仓库
- 禁止写：web/app.js、web/index.html、web/avatar/drivers/**、docs/PROTOCOL.md、scripts/check_protocol.py、server/app.py、server/dialogue/**、server/memory/**。web/app.js 与 web/index.html 属 Role 0：你交付组件与接入 diff，输出 [PROTOCOL_REQUEST] 由 Role 0 合入。

【协议对齐要点】（docs/PROTOCOL.md 第十三节）
- GET /api/memory/starmap?days&kinds&q&limit（≤400）→ {nodes,edges,stats,truncated}；GET /api/memory/starmap/node/{node_id} → 节点全文；列表永远不含正文。
- WS 客户端动作 {"action":"narrate","node_id":"...","turn_id":"..."} → 服务端链路 turn_started(source:"narration") → thinking → emotion → text_delta → audio* → done；忙碌回 error(code:"narrate_busy",recoverable:true)；narration 轮可被 barge_in 随时打断。
- 记忆关闭/空结果：空态展示，不报错；DOM 一律 textContent；不得新建独立预览 HTML（宪章规则 3），星图是主工作台内置视图。

【任务 F4：星图前端】
1. web/starmap/ 数据桥接模块：封装 starmap 取数、节点详情、关键词检索（q 参数、去抖）、narrate 动作发送与状态机（idle→pending→playing→interrupted/busy）。
2. 星图渲染：节点按 weight 定大小、group(user/self) 定色相；边按 kind 区分样式；支持漫游（拖拽/缩放，桌面 + 移动触控）；节点点击 → 详情面板（经 node 详情接口取全文）→"让小溪讲这个"按钮 → narrate → 复用既有播放链路（PlaybackQueue/timeline 不重复造）。
3. 视图集成：组件暴露稳定挂载 API（如 Starmap.mount(container, deps)），把 app.js/index.html 的最小接入 diff（视图切换入口 + 挂载点 + 新 WS case）写进 [PROTOCOL_REQUEST] 交 Role 0；在合入前你的组件用本地 mock 数据自测。
4. narrate_busy：按钮冷却 + 可重试提示，不自动重试；barge_in 打断后状态机回 idle。

【验收命令】（全部执行，结果附汇报）
1. .\.venv\Scripts\python.exe scripts\check_protocol.py → PASS（注意：你新增的 app.js case 读取的字段必须服务端真发；服务端事件未就绪前不要提前加 case）
2. npm run test:voice → 全绿（语音链路零回归）；node scripts\test-workspace-browser.mjs → 全绿（工作台零回归）
3. 新建 node web/starmap/test_starmap.mjs：图谱数据→渲染模型转换、检索去抖、narrate 状态机、空态/关闭态 → 全绿
4. 浏览器手验（桌面 + 移动视口）：星图非空白、节点可点、详情面板正确、讲述按钮触发播放并可被打断；附截图

【禁止】新建独立 HTML 页面；列表接口展示正文；发明协议外事件/字段/路由；自动内联进 app.js（等 Role 0 合入）；修改白名单外文件。
```

---

## Role 0 跟进事项（派单后由总指挥执行）

1. 收集三个角色的 `[PROTOCOL_REQUEST]`，统一在 `server/app.py` 合入 router 注册、 narrate/self_topic/diary_notice/self_evolution 的接线，以及 app.js/index.html 的星图视图接入。
2. 接线后必须重跑 `python scripts/check_protocol.py` 与 `python scripts/test_core.py`，并跑受影响的浏览器回归。
3. 协议再演进时只改 `docs/PROTOCOL.md` 并同步 `scripts/check_protocol.py`，然后重新打标（下一个 tag：`v0.6-phase2-contract`，待三个角色首批交付后）。
