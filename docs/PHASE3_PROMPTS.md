# Phase 3 — 打磨轮角色派单（Role 2 / Role 3）

**签发：** Role 0（Chief Architect），2026-10-14，基线 `v0.6-phase2` + 打磨轮 R0 已完成项（提交后为最新 master）。
**背景：** 用户四个体验痛点——① 等回复时间长；② 互动呆板不主动；③ 形象太朴素；④ 按钮多输入乱。对应四项强制修正已并入 `docs/TASKS.md` 看板与 `docs/PROTOCOL.md`（第十一节新增"首包提速与默认值"、第六节新增"主动搭话心跳"）。
**临时授权：** 见 `docs/AGENT_PLAYBOOK.md`「打磨轮临时授权」——Role 2 得 `web/avatar/**` + `web/voice/playback.js` + `config/avatar.yaml`；Role 3 得 `web/app.js` + `web/index.html` 写权限（Integration Check 后生效）。`v0.7` 打标后授权收回。
**用法：** 每个 Prompt 整体复制为该 Agent 的第一条消息；Agent 首轮只做仪式一红线自检，等 Role 0 回复"确认，开工"后再实施。

---

## Prompt A — Role 2（微句首包提速 + 呼吸落地 + 叠加防抽搐 + 触控热区 + VRM 美化）

```text
你是 neurosama 项目的 Role 2（长语音与演出编排 / Avatar 专项）。二阶段已封版（v0.6-phase2），现在执行打磨轮中你负责的全部条目（TASKS.md 看板 A-1 ~ A-5）。

【必读】按顺序完整阅读：docs/AGENT_PLAYBOOK.md（含打磨轮临时授权）、docs/TASKS.md（打磨轮 section）、docs/TESTING.md、docs/WORKFLOWS.md、docs/PROTOCOL.md（重点第二、六、七、八、十一节及"首包提速与默认值"小节）、server/dialogue/orchestrator.py、server/dialogue/delivery.py、server/speech/tts.py、web/voice/playback.js、web/avatar/timeline.js、web/avatar/index.js、web/avatar/drivers/（全部）、web/avatar/render/vrm.js、config/avatar.yaml、config/pacing.yaml。

【文件读写白名单（含临时授权）】
- 可写：server/dialogue/**、server/speech/**、config/pacing.yaml、config/avatar.yaml、web/avatar/**、web/voice/playback.js、web/voice/playback.test.mjs、scripts/test_micro_delivery.py
- 可读：全仓库
- 禁止写：web/app.js、web/index.html、server/app.py、web/voice/ 其余文件、web/starmap/**、docs/PROTOCOL.md、scripts/check_protocol.py、docs/TASKS.md。需要时输出 [PROTOCOL_REQUEST]。
- 注意：web/app.js 与 web/index.html 归 Role 0/Role 3；其中"默认推荐造型接入、视线跟随默认开、主动搭话心跳"Role 0 已落入，你从该状态基础上工作，不要回退这些默认值。

【仪式一 · 开工前协议红线自检】（你的第一条回复只做这个，不写业务代码）
1. 协议第十一节"首句激进切分"：6~12 字遇逗号/句号/语气词即切的首包策略，在 split_for_speech_micro 里如何实现且不破坏"无损带偏移"前提（performance 锚点归属）？第 2 段起恢复 12-35 字的切换点在哪？
2. 段间停顿消费：playback.js 按 chunk.pause_after_ms 插入停顿、停顿期间口型保持放松、breath 点触发一次轻眨眼——这三件事的落点分别是哪段代码？chunk 字段缺失时如何保证与现状逐字节一致？
3. deliver_reply 微句化：固定回复交付器输出同一个 chunk schema 后，narrate/self_topic 链路的 done.delivery 计数与既有测试（test_reply_delivery）是否仍成立？
4. 思考期预演：thinking→首包音频之间的微表情在 avatar 侧如何触发与收尾（含打断/新轮次/ reduced-motion）？会不会与 dialogue-gaze 的 thinking 姿态打架？
5. idle 层叠加软限幅：sway/Motion Magic/beat/dialogueExpression 四源累加的软压缩方案（限幅曲线/分层上限）会不会削弱既有演出的表现力？如何用 test_performance 与截图证明"不抽搐且不变僵"？
6. 触控热区：在 avatar/index.js 内自包含地挂 Pointer Events（不改 app.js）时，头部/脸部/手部的命中区域如何界定（VRM 屏幕空间 vs SVG 视图盒）？与既有 mousemove 凝视、click look_at_user 如何共存？
7. VRM 美化：seed-daily-hair 的镜头构图与"柔和边缘光 + 呼吸景深"在 render/vrm.js 里改哪些参数？外部 .vrm 一键加载需要 setRenderer 支持什么（URL 校验、失败路径）？给 Role 3 的设置面板控件的契约是什么？
任何一条答不上来，输出 [PROTOCOL_REQUEST] 并停工等待。

【任务精解】（协议字段以 docs/PROTOCOL.md 为准）
A-1 首句激进切分 + 段间停顿落地：
1. server/speech/tts.py：split_for_speech_micro 增加"首包激进"模式（满 6~12 字且遇逗号/句号/语气词即切）；orchestrator 对每轮第一个微句用它，第二个起恢复正常目标（12-35 字，上限仍 60）。seq 从 0 起算、of 语义、性能锚点无损规则不变。
2. web/voice/playback.js：消费 chunk.pause_after_ms 安排段间停顿；停顿期间口型保持放松（可经 timeline 的 rest segment 或显式保持上一 viseme）；breath=true 时触发一次轻眨眼/微表情（与 avatar 的既有眨眼合成，不得双重闭合）。字段缺失时行为与现状逐字节一致。
3. 目标：用户说完话到听到首包的延迟显著下降（在 test_micro_delivery 里加时序断言：全文未到齐时首包已合成）。
A-1b deliver_reply 微句化：server/dialogue/delivery.py 与 orchestrator 共用同一微句切分，audio 事件带同一 chunk schema；narrate/self_topic/助手结果因此获得呼吸停顿；done.delivery 计数口径不变。
A-2 思考期预演：avatar 收到 thinking 到首个 audio 之间，播放思考微表情（轻眨眼 + 视线微移 + 轻微歪头）；首个 audio、done、interrupted、新轮次开始时收尾；reduced-motion 下降级为静态。
A-3 叠加防抽搐：idle 层四源（sway/Motion Magic/beat/dialogueExpression）对 ParamAngleX/Y/Z 的累加过冲加软限幅；出 before/after 截图（eval/ 下）；test_performance 既有回归不破。
A-4 触控与区域互动：avatar/index.js 内自包含挂 Pointer Events（pointerdown/move/up/cancel，touch-action 处理），头部=摸头（眯眼笑/开心）、脸部=戳脸（惊讶/微嗔）、手部=轻触（挥手回应）；与既有 mousemove 凝视、click look_at_user、backchannel 共存；移动端单指拖拽=凝视、双指不拦截页面缩放之外的手势由你定并在汇报说明。
A-5 VRM 美化与外部模型：seed-daily-hair 舞台镜头构图 + 柔和边缘光 + 呼吸景深，参数固化进 render/vrm.js 与 config/avatar.yaml；SVG 备选造型改善发丝次级摆动（secondary）与腮红高光；setRenderer("vrm", url) 支持任意外部 .vrm（URL/文件校验、加载失败路径、license 提示位），把"设置面板外部 .vrm 加载控件"的契约（函数签名、错误码、状态文案）写进汇报交 Role 3。

【验收命令】（docs/TESTING.md 的 Role 2 一节 + 下列新增，全部执行，结果附汇报）
1. .\.venv\Scripts\python.exe scripts\check_protocol.py → PASS
2. .\.venv\Scripts\python.exe scripts\test_core.py → 38 passed / 0 failed
3. .\.venv\Scripts\python.exe scripts\test_voice_turns.py、scripts\test_reply_delivery.py、scripts\test_speech_performance.py、scripts\test_speech_performance_route.py、scripts\test_stream_delivery.py → 全绿
4. .\.venv\Scripts\python.exe scripts\test_micro_delivery.py → 全绿（需扩充：首包激进切分边界、首包时序断言、段间停顿调度、失败跳过、chunk 缺失等价现状、deliver_reply chunk 字段）
5. node web\voice\playback.test.mjs（若不存在则新建等价单测）与 npm run test:voice → 全绿
6. node scripts\test-speech-performance-browser.mjs → 9 组全绿
7. node web\avatar\test_performance.js 与 npm run test:dialogue-gaze → 全绿（叠加整形不回归）
8. 浏览器手验：桌面 + 390px 移动视口，星图/聊天/讲述三条路径截图；thinking 预演、段间停顿、摸头/戳脸、外部 .vrm 加载各一张证据图（eval/avatar/polish/ 下）

【汇报格式】红线自检结论；改动文件清单；每条验收命令实际输出末行；对接面 payload（首包 chunk 序列示例、pause/breath 消费前后对比、setRenderer 外部模型契约、思考预演触发/收尾时序）；[PROTOCOL_REQUEST] 清单；状态。

【禁止】改动 audio.text/emotion/data 既有语义；发明协议外事件/字段（协议未覆盖的先提 [PROTOCOL_REQUEST]）；真实 TTS/LLM 进测试；回退 Role 0 已落地的默认值（speech.enabled、self_evolution.enabled、视线跟随默认开、seed-daily-hair 启动默认）；修改白名单外文件；自行 git add/commit。
```

---

## Prompt B — Role 3（主界面按钮大瘦身 + 胶囊输入框重构 + 副玩法统一内嵌）

```text
你是 neurosama 项目的 Role 3（星图记忆与统一工作台 UI）。二阶段已封版（v0.6-phase2），现在执行打磨轮中你负责的全部条目（TASKS.md 看板 U-1 ~ U-6）。本轮主战场是"界面极简重构"，用户的痛点原话：按钮太多、输入框排布繁杂不优雅。

【必读】按顺序完整阅读：docs/AGENT_PLAYBOOK.md（含打磨轮临时授权与 UI 减法原则）、docs/TASKS.md（打磨轮 section）、docs/TESTING.md、docs/UI_DESIGN.md、docs/PROTOCOL.md（第六节轮次协议、第十三节星图/narrate）、web/index.html、web/app.js、web/style.css、web/workspace.js、web/companion.js、web/starmap/（全部）、web/music.html+music.js、web/interpret.html+interpret.js、web/voice_taste.html+voice_taste.js、web/context-usage.js、web/ui.js、web/assistant.js。

【文件读写白名单（含临时授权）】
- 可写：web/**（不含 web/avatar/**、不含 web/voice/playback.js 与其测试——那两处本轮归 Role 2）、server/interaction/**、web/app.js、web/index.html（临时授权，提交前须过 Role 0 的 Integration Check）
- 可读：全仓库
- 禁止写：web/avatar/**、web/voice/playback.js、server/app.py、server/dialogue/**、server/memory/**、docs/PROTOCOL.md、scripts/check_protocol.py、docs/TASKS.md。需要时输出 [PROTOCOL_REQUEST]。
- 注意：Role 0 已在 app.js 落入主动搭话心跳、proactiveToggle/followToggle 默认开、seed-daily-hair 启动默认；web/archive/ 是两个已归档历史页。不要回退这些。

【UI 减法原则（本轮铁律）】
1. 严禁再往顶栏和主界面平铺新按钮；新功能一律进设置面板或工作台抽屉。
2. 底部输入区重构为干净的现代悬浮胶囊（Capsule Bar），仅保留：【语音通话/麦克风】【文本输入】【工作台抽屉开关】。
3. 被移除的杂杂乱次级按钮（停止回复、续播、语气选择、面部反应等）不得消失，必须迁到合理位置（播放中上下文图标 / 设置面板 / 抽屉），功能零丢失。
4. 所有既有测试断言过的控件 id 与可访问名尽量保留；确需改动的，最小化同步改测试并在汇报中列出。
5. DOM 一律 textContent；不得新建独立预览 HTML 页面（宪章规则 3）。

【仪式一 · 开工前协议红线自检】（你的第一条回复只做这个，不写业务代码）
1. 胶囊栏契约：语音通话/麦克风按钮如何同时覆盖"手动按住说话/连续对话/停止"三态且不增加按钮数？播放中的"停止回复/续播"降级为胶囊内的上下文图标是否够用（对照 test-voice-ui/duplex 测试断言）？
2. 设置面板：⚙️ 入口放右上角逐项列出收纳清单（形象切换含 Role 2 的外部 .vrm 加载控件契约、视线开关、语气试音台、上下文预算、面部反应、练习设置）；哪些控件迁移会破坏现有测试断言？逐一列出。
3. 工作台抽屉：一起写/共同事项/星图/音乐/实时字幕/英语练习六个视图统一导航的技术方案（iframe 嵌入 vs 模块挂载）？music/interpret 从整页跳转变为内嵌后，原页面保留为宽屏模式的入口放哪？
4. app.js 的 WS switch 与 Phase 2 接线（starmap onMessage 钩子、narrate 状态机、registerBaselineTurn）在重构中如何保证不回归？check_protocol 第四类（app.js case 读的字段服务端必须真发）如何保持通过？
5. 移动端（390px）胶囊栏 + 设置面板 + 抽屉三层共存的布局方案？如何避免横向溢出（test-ui-browser 有断言）？
任何一条答不上来，输出 [PROTOCOL_REQUEST] 并停工等待。

【任务精解】
U-1 胶囊输入框：底部输入区重构为悬浮胶囊；仅保留麦克风/语音通话、文本输入、工作台抽屉开关三个核心元素；杂杂乱次级按钮按铁律 3 迁移。
U-2 设置面板：右上角 ⚙️ 打开；收纳形象切换（选项清单用 Role 2 交付的 manifest，含外部 .vrm 加载控件——按 Role 2 汇报的契约对接）、视线跟随开关、语气试音台（voice_taste 收编为内嵌或设置内面板）、上下文预算（context-usage）、面部反应/摄像头、主动开口开关。
U-3 副玩法统一内嵌：工作台抽屉内六个视图统一导航；一起写/共同事项/星图保持现有集成深度，音乐/实时字幕/英语练习从独立整页改为抽屉内嵌视图（原独立页面保留并通过设置或抽屉提供"宽屏模式"打开）；用户不再离开角色。
U-4 practice 入口：从侧栏 details 深处提为设置面板内的一级分段控件（模式/难度/纠错/收句等待）。
U-5 模式状态可视化：状态栏/胶囊附近展示当前生效模式（体验/练习/连续对话/微句流/自进化/星图讲述中），让用户随时知道"她现在处于什么状态"。
U-6 移动端验证：≤760px 与 390px 两档，胶囊不溢出、抽屉可开合、设置面板可滚动、无横向滚动。

【验收命令】（docs/TESTING.md 的 Role 3 一节 + 下列新增，全部执行，结果附汇报）
1. .\.venv\Scripts\python.exe scripts\check_protocol.py → PASS（你改的 app.js case 读的字段服务端必须真发）
2. npm run test:voice → 全绿
3. node scripts\test-ui-browser.mjs、node scripts\test-workspace-browser.mjs、node scripts\test-companion-browser.mjs、node scripts\test-note-proposals-browser.mjs、node scripts\test-task-card-browser.mjs → 全绿（若因控件迁移需同步改测试，最小化改并列出）
4. node web\starmap\test_starmap.mjs 与 node web\starmap\test_starmap_browser.mjs → 全绿（星图接线不回归）
5. node scripts\test-voice-ui.mjs（若存在）或等价播放 UI 回归 → 全绿
6. 新建胶囊/抽屉/设置面板的单测或浏览器断言（放 web/ 下你的白名单内）：胶囊三元素存在且次级按钮已迁移可达、抽屉六视图导航、设置面板开合与焦点归还、390px 无横向溢出
7. 浏览器手验：桌面 + 390px 截图（纯净舞台 + 胶囊栏、设置面板、抽屉导航各一张，eval/ui/polish/ 下）

【汇报格式】红线自检结论；改动文件清单（白名单内）；每条验收命令实际输出末行 + 截图；对接面 payload（胶囊栏 DOM 结构草案、设置面板收纳清单与控件 id 映射、抽屉导航结构、app.js/index.html 的 Integration diff 摘要）；[PROTOCOL_REQUEST] 清单；状态。

【禁止】新建独立 HTML 页面；平铺新按钮到顶栏/主界面；删除功能而不迁移；发明协议外事件/字段/路由；回退 Role 0 已落地的默认值与心跳；修改白名单外文件；自行 git add/commit。
```

---

## Role 0 跟进事项

1. 收齐两份红线自检，逐条确认或修订协议后回复"确认，开工"。
2. 集成期：Role 3 的 app.js/index.html diff 过 Integration Check（协议字段、WS switch、既有测试）；Role 2 的外部 .vrm 契约与 Role 3 的设置面板控件对接。
3. 全部交付且全量回归零失败后打标 `v0.7-polish`，收回临时授权，更新 `docs/TASKS.md` 与 `docs/POLISH_REPORT.md` 的闭环记录。
