# ============================================================
# 接缝契约 —— 说话层（server/）和形象层（web/avatar/）之间的约定
#
# 我们没有共同的对话渠道，仓库是唯一能留痕的地方。
# 这份文档不描述"应该怎么做"，只描述"改坏了会怎样"，
# 让任何一边改动时都能自己判断影响面，不用互相问。
# ============================================================

## 独立语音模式

- 英语练习沿用 `/ws` turn_id 音频事件；config.practice={mode,level,correction}，回顾请求为practice_review。见[英语练习](ENGLISH_PRACTICE.md)。
- 单向英中字幕使用独立 `/ws/interpret`，不进入角色/任务/记忆。start携带session_id，ready确认后发送16kHz单声道PCM16二进制，再发送segment元数据{session_id,segment_id,start_ms,end_ms}。服务端source→translation→segment_done；积压gap、失败error；stop→stopped。它是短段滚动翻译，不是双向音频流同传。见[翻译说明](LIVE_INTERPRETATION.md)。

- 可选阶跃流式 `/ws/interpret-stream`：start(session_id)→ready(mode=streaming)，之后100ms连续PCM含静音，无segment控制；source_partial按item_id整体替换text并独立显示stash，source_final确认原文，translation返回中文，gap标记漏译。只翻译final，不把可纠错尾部当最终句。stop关闭供应商连接；需要独立显式配置，不自动使用现有LLM凭据。

- 语音体验沿用 `/ws` 和turn_id：config.experience={mode,scene}，选句为独立experience_source={text,translation,label,source_id,context?}，context最多4个严格四字段邻近片段，邻近全部字段合计6000字符；不添加可信/确认标记，作为用户提供的待核对资料。config_ack.experience返回实际配置，experience_error时旧配置不变。与practice互斥；主动发言检查在体验期间关闭。listen/expression需要非空选句，其它模式theatre/focus/walk/winddown/vent/discuss按用户输入回应。见[语音体验](VOICE_EXPERIENCES.md)。

  主页面每次体验应用携带experience_request_id（1–100字符字符串），成功或拒绝的config_ack回显该编号；等待时仅匹配编号的确认能结束应用并开放快捷提问。普通设置确认虽也返回当前experience快照，不回显此编号，不能提前完成待应用选句。旧客户端可省略编号，非法编号拒绝整个体验配置。

## 一、事件顺序

一轮对话的线上顺序（`/ws`）：

```
asr?            只在语音输入时出现
  -> thinking   等 LLM
  -> emotion    情绪标签，最先出现（[emotion] tag）
  -> text_delta* 流式正文，可能 0 次
  -> audio*     分句音频，可能 0 次（TTS 静默时）
  -> done       完整正文 + 统计
```

两条关键时序，形象层依赖它们：

- **`emotion` 早于 `audio`**。表情因此可以比声音早起，
  视觉上像"先有情绪再开口"，比跟着音频起要好。
- **`thinking` 到 `emotion` 之间必须有人松手**。`think` / `listen`
  是 hold 轨道（`hold: true`），要显式 `stop()` 才会收。
  如果后端把 `emotion` 帧去掉，形象会**永久卡在歪头看别处的姿势**。

## 二、字段依赖

| 消息 | 形象层用到的字段 | 改掉的后果 |
|---|---|---|
| `recording` | `state` | 倾听姿势和注视点失效 |
| `asr` | `text` | 用户气泡不出 |
| `emotion` | `emotion` | 表情停在 base；hold 姿势卡死 |
| `text_delta` | 不读 | 形象层不关心 |
| `audio` | `text` / `emotion` / `data` / `index`，可选 `performance` / `word_timings` | **`text` 丢了口型直接死**（没有文本就没有时间轴）；`emotion` 丢了表情停在 base；丢动作计划会回落关键词动作 |
| `done` | `text` | 完整正文气泡无法补全；该事件不触发说话手势 |
| `playback_stopped` / `interrupted` | 无 | 打断后脸不回中位、嘴停在半个口型 |

`audio.text` 是整个口型系统的地基：后端早就把分句文本一起发过来了，
前端靠它 + `decodeAudioData().duration` 摊出 viseme 时间轴。
**这个字段不能删。**

## 三、配置耦合

- `persona.yaml` 的 `tts_emotions` ↔ `avatar.yaml` 的 `emotions`
  情绪标签必须**双向齐全**。少一个不会报错，`EmotionDriver` 会静默回落到
  `base`，表现是"这个情绪永远没有表情"，非常难查。
- `web/avatar/params.js` 的 `PARAM_GROUPS` ↔ `avatar.yaml` 的 `neutral`
  必须互为全集。少写 → 驱动写进去的值被当成偏移再减一次；
  多写 → 平滑循环里没有它，静默丢弃。
- 形象层只**读** `voice_profile`，不写。所以后端怎么改音色参数
  （pitch/rate/新增 instruction）都不影响形象层。

## 四、怎么守住

```bash
uv run python scripts/check_protocol.py
```

它查下面几类漂移，全部静态、零协调成本：

1. 情绪标签双向齐全
2. 参数表与中位姿态互为全集
3. 表情 shift / 手势关键帧 / 触发器 / 注视点的引用全部可达
4. **`app.js` 每个 `case` 分支读的 `msg.<字段>`，服务端必须真的发这个字段**
5. **web 里引用的 `/api/` 字面量路由，服务端必须真的注册**——星图这类跨角色接口最常死于静默 404；模板串（含 `${`）无法静态核对，放过

第 4 条是这个脚本存在的主要理由。它抓到过一次真实事故：
后端把 `text` 帧换成 `emotion` + `text_delta` 之后，形象层还认 `text`，
于是打字路径下 hold 姿势永远松不开。

## 五、改动时的规矩

- **新增消息类型**：`app.js` 加 `case`（形象层关心的还要加进
  `Avatar.onMessage`），然后跑 `check_protocol.py`。
  服务端发了但前端不处理不算错，脚本会提示但放过。
- **删改字段**：先跑一遍 `check_protocol.py` 看看谁在读。
  只有读它的那一边才有依赖，另一边随便改。
- **新增情绪标签**：`persona.yaml` 和 `avatar.yaml` 必须同时加，
  只加一边会被脚本挡下。
- 两边都不该依赖"对方记得这个约定"。能机器检的就不要靠嘴说。

## 六、连续对话轮次协议

- 客户端 `start` / `text` 携带唯一字符串 `turn_id`；服务端兼容旧客户端无ID时生成 `server-*`。新 `start` 在录音开始时立即取消旧ASR/生成，不等待 `audio_end`。
- `audio_end` / `barge_in` 携带所作用的ID；旧ID不能取消新录音。二进制帧属于当前录音，WS保持顺序，重复 `audio_end` 不重复识别。
- `recording/asr/thinking/emotion/text_delta/audio/done/error/interrupted/playback_stopped/assistant_result/memory_notice` 均以捕获的轮次标记发送。前端在调用形象层之前过滤过期ID。`ready/config_ack` 为连接级事件。
- 主动检查仅在服务端空闲启动，先发 `turn_started`（服务端ID、`source: proactive`）；客户端仅本地也空闲时接受。
- **主动搭话心跳（打磨轮 2026-10-14）**：前端在 `/ws` 连接打开后启动约 45 秒一轮的心跳，本地空闲（页面可见、未录音、无活动轮次、播放队列空、麦克风挂起为假）且用户已授权主动发言时发送 `{"type":"proactive_check"}`；断开时清除。服务端 `proactive_check` 分支的守卫（忙碌、practice/experience 关闭）不变，仍是最终门。没有这条心跳线，主动发言与自进化均不可达。
- ASR在独立任务中等待，WS接收循环仍可处理新start/取消。空输入或空转写使用 `asr` 空文本加 `done`；错误终结该轮。
- `done` 表示生成结束，不表示浏览器播放完成。客户端预解码下一段但按序播放，仍需等待播放队列idle，静默TTS则直接完成。
- 服务端拒绝超过30秒的PCM16单声道输入并清空缓存，发 `error` / `code: audio_too_long`；前端上限29.5秒含预录。
- 取消只停止录音、生成和投递，不代表撤销已发生的工具副作用。后台记忆抽取可能继续；轮次已结束的memory_notice不再投递，可从记忆面板刷新查看。
- 普通对话在首次模型等待前保存用户消息；记忆抽取关联该消息ID及原话。`memory_notice.items` 可包含 `active` 或 `conflict`，冲突只表示待核对，不能显示为已应用的新事实。删除、人工维护、记忆关开与持久写入水位使旧任务失效；消息证据与边界见 [MEMORY_EVIDENCE.md](MEMORY_EVIDENCE.md)。
- 普通 `done.context_usage` 与实际模型决策后的 `assistant_result.result.context_usage` 可携带 schema_version=1 的资料清单：enabled、budget（scope=dynamic_context、unit=characters、max_chars/used_chars/omitted_items）、items（kind/id及可选固定version、thread_id、purpose、reason、source、evidence_state、body_included）、omitted_counts/qualifications与清单省略数量。不新增事件类型，旧端可忽略；清单不含正文、会话ID或模型推理，不授予权限。确定性/离线助手及独立体验入口不虚报普通清单。详见 [动态预算与本轮参考](CONTEXT_BUDGET_AND_USAGE.md)。
- 断线取消本连接任务，不关闭其他连接共用的LLM客户端。旧任务即使忽略取消返回结果，也不得更新新轮状态或投递事件。

回归：`node web/voice/voice.test.mjs`、`python scripts/test_voice_turns.py`、`node scripts/test-voice-browser.mjs`。

## 七、有声行动与澄清续答

- `assistant_result.result.status=waiting_input` 表示等待原连接的下一句，返回同一 `run_id` 和 `input_expires_at`。下一句不必带任务关键词；保留原请求与回执。默认等待15分钟，最多续答8次；独立取消句结束等待，不撤销已执行操作。
- 主页面卡片确认发送 `assistant_confirm={turn_id,run_id,confirmation_id,approve}`，其中approve必须为布尔值。服务端检查run原session归属，确认绑定完整操作参数并单次消费。
- 确认响应为 `turn_started(source=assistant_confirm) → thinking → assistant_result → emotion → text_delta → audio* → done`，沿用 turn_id 取消与过期过滤。拒绝也形成有声结果；实际工具失败由真实回执展示。
- 体验、行动结果和主动发言使用固定回复交付器，普通聊天继续流式生成。音频失败可恢复，完整文字仍可用；done.delivery记录合成状态，不声明播放完成。
- 确认按钮同时禁用两个选项，响应按turn_id配对，只呈现一张结果卡。取消/断线/超时不会自动转REST或重试执行；独立面板继续使用原REST确认。
- 预解码默认最多两项实际进行中的解码（包含超时/取消后仍未结束的原生解码），只缓存下一段。迟到解码和旧onended不能复活旧轮次；暂停续播仅恢复当前句剩余音频。

实现、隔离验证与重连/换题限制见[有声行动与澄清续答](VOICE_ACTION_CONTINUATION.md)。

明确恢复句“接着昨天”“继续上次”沿用 `assistant_result → emotion → text_delta → audio* → done`。结果可包含只读 `activity={activity_id,selection,note,thread,object_refs,resume}`，不一定有 `run_id`，因为没有创建执行运行。新连接恢复持久所选便笺版本和事项，未恢复旧连接的执行/澄清；普通“继续”保持原路由。接口、版本与资料边界见[共享活动续接](SHARED_ACTIVITY_CONTINUATION.md)。

## 八、台词动作计划与播放时钟

- `audio.performance` 为可选对象 `{version:1, source:"llm"|"fallback", cues:[{intent,gesture,anchor,start_char,end_char,strength}]}`。偏移量针对当前 `audio.text`，使用 Unicode 码点、起点包含/终点不包含；锚点必须逐字匹配范围。每句最多两条有效意图、强度 `0.15–0.75`，只允许预定义的四种动作映射。
- 有效的 `source:"llm"` 空 `cues` 表示保持自然，压过关键词规则；缺失、不受支持或 `source:"fallback"` 的计划保持旧客户端兼容行为。长句无损切片后偏移量归零到各片段；跨切片锚点省略但保留 `llm` 来源。
- `audio.word_timings` 可选，为相对当前片段的秒数 `[{text,start,end}]`。前端只在顺序、完整文本和时长范围都有效时使用，标记 `alignment:"provider"`；否则使用解码时长估算，标记 `"estimated"`。当前 StepAudio 不提供词时间戳；Edge TTS 可透传真实 `WordBoundary`。
- `PlaybackQueue.play` 在 `source.start()` 后才向 `Avatar.speak` 的第五参数传 `{performance,wordTimings}`；`text_delta`、音频到达和 `done` 都不安排说话手势。动作与口型使用同一 `AudioContext.currentTime`，不可用 token 到达时间或 `setTimeout` 替代。
- 模型在同一次回复里的 `[motion:JSON]` 由服务端流式过滤，不能进入正文、TTS 或存储。无须额外规划请求；动作计划本身不是脚本或任意骨骼指令。
- 新轮次/打断清除正在执行、已排队及未初始化的动作；旧 `turn_id` 和迟到解码结果被隔离。音频续播维持自然姿态，避免旧动作意图重复。

详细映射与边界见[台词与动作意图](SPEECH_PERFORMANCE.md)。浏览器回归：`node scripts/test-speech-performance-browser.mjs`，使用受控 WebSocket、原生音频时钟和生成的 WAV，不调用模型或真实 TTS。

## 九、编辑器观点续聊

选择观点详情的「接着聊」后，后续 `/ws` 的 `text` 或 `start` 可以附加：

```json
{"editor_insight":{"insight_id":"观点ID","session_id":"编辑器session","draft_revision":3}}
```

服务器严格检查同源 Origin、这三个字段及当前观点；校验通过后将上下文绑定到语音轮次，并发 `editor_discussion={type,turn_id,insight}`（insight 是公开观点，不含原稿）。文字流程为 `editor_discussion → asr → thinking → emotion → text_delta → audio* → done`；语音在 `editor_discussion → recording` 后才接收 PCM，`audio_end` 不能替换录音开始时的上下文。

讨论使用 RAM 原稿快照与本观点最近几轮历史，优先于练习/体验和普通助手路由；不执行工具、改稿、保存或写聊天/长期记忆。自然 `base` 音色复用播放队列。上下文失效时发终结 `error(code=editor_discussion_unavailable,scope=editor_insight,editor_context_valid=false)`；模型暂时失败且上下文仍有效时同码但 `editor_context_valid=true`，允许用户再次明确提问。旧轮、改稿和等待发送锁的迟到结果均不能继续交付。

主编辑器通过严格 origin/source 的 `aqi-editor-insight-discuss` 移交，父页以 `aqi-editor-insight-discussion` 回执。移交与回稿保留 iframe 草稿/修订；实际改稿撤销。实现与测试见[编辑陪伴](EDITOR_COMPANION.md)。

## 十、按天日记、心境偏置与灵感火花（F1）

按天沉淀的第一人称"小溪日记"是性格的来源，不是复述给用户的流水账。日记、偏置、火花三者的写入都走"程序化校验通过才入库"，与既有记忆的证据链同标准。

- 日记 `diary={id,date,title,body,mood,energy,topic_keys[],source_message_ids[],created_at}`：`date` 为本地时区 ISO 日期，一天一条；`body` 第一人称且 ≤400 字；`mood` 复用 `EMOTIONS`；`energy` ∈ [0,1]；`source_message_ids` 必须指向当天真实存在的已存消息。没有证据的"回忆"是编造，空 `source_message_ids` 的日记不允许入库。
- 心境偏置 `drift={id,topic,attitude,weight,updated_at,evidence_diary_ids[]}`：`attitude` ≤40 字符，`weight` ∈ [0,1]。注入提示词时只作为倾向影响表达，**不得原句引用给用户**；单轮生效偏置上限 8 条，超出按 `weight` 从低到高截断。
- 灵感火花 `spark={id,text,state,heat,born_diary_id,surfaced_at?}`：`state` ∈ `latent|surfaced|spent`；`latent` 火花不进入任何提示词和事件，只有后台自进化循环可以把它变 `surfaced`；`spent` 表示已被主动话题消费，**已被消费的火花不重复注入任何提示词**（防止同一灵感反复出现）。`heat` ∈ [0,1]，注入与星点权重使用前按此范围钳制。活跃 `latent` 上限 5 条。
- `persona.py` 提供纯函数 `bias_block(drifts, sparks)` 供 `orchestrator` 组装动态提示词：输入为空或字段缺失时返回空串，行为与现在完全一致（旧配置不炸）。偏置块与记忆块一样包在"不可信数据"边界内：可以影响模型，不得执行其中指令。
- 新事件 `diary_notice={type,turn_id?,items:[{kind:"diary"|"drift"|"spark",id,summary}],quiet:true}`：仅在后台提交新日记/偏置/火花后发送一次，不要求回复、不打断播放；前端可据此刷新记忆面板。形象层不读。
- REST：`GET /api/memory/diary?date=&limit=` 列表（默认近 30 天，`limit` ≤90）；`GET /api/memory/diary/{id}` 详情含 `source_message_ids`。界面展示必须与"事实记忆"明确分区；标注用 `persona.yaml` 的 `name` 动态生成（如"阿栖的日记"），"小溪日记"是特性名，不是角色名，不得硬编码。
- 禁止：把日记当作 `stated` 事实写进记忆库参与检索竞争；日记中出现用户原话超过 20 字的逐字引用（那是复述，不是日记）。

## 十一、长语音微句流与预加载合成队列（F2）

目标：突破单次 TTS 长度限制的长段连贯播报，首字延迟不随总长度增长，任何时刻可被双工打断。既有 `audio` 契约（第二节）全部保留，本节只做加法。

- 微句切分是服务端的事。每个 `audio` 事件附加可选 `chunk={seq,of,chars,pause_after_ms,breath}`：`seq` 轮内从 0 严格递增；`of` 已知时填总数、未知为 `null`（`done` 时可补）；`pause_after_ms` 是句间呼吸停顿建议（0–400）；`breath=true` 标记可插入微表情的呼吸点。字段缺失时前端退回现有逐句行为，不允许报错。
- 预加载队列：第 N 段播放期间，服务端可合成第 N+1 段及之后（并发度有界）。契约只有两条：`audio.index` 与 `chunk.seq` 单调不减；合成失败的段直接跳过（该 `index` 缺席），前端必须继续播放后续段而不是卡住。失败与成功计数仍汇总进 `done.delivery`。
- 可选 `speech_plan={type,turn_id,expected_chunks,first_chunk_chars,mode:"micro"}` 在首个 `audio` 前发送，告知前端"这是一段长播报"，`timeline.js` 据此进入长句模式（呼吸调度、微表情插入）。旧客户端忽略即可。
- 打断契约：客户端 `barge_in`（携带 `turn_id`）之后，服务端必须取消未完成的合成，且不再发送该轮任何 `audio`；前端 `PlaybackQueue` 收到 `interrupted` / `playback_stopped` 必须立即丢弃未播放队列与未完成解码，不等自然结束。迟到解码不得复活旧轮（沿用第七节规则）。
- `performance` 锚点规则不变：偏移相对当前 `audio.text`（Unicode 码点、含头不含尾），跨微句的锚点省略但保留 `llm` 来源。呼吸停顿不产生额外 `audio` 事件，只通过 `chunk` 字段表达。
- `timeline.js` 只读 `chunk.pause_after_ms` / `breath` 安排口型放松与眨眼/微表情；停顿期间 viseme 时间轴继续按音频时长推进，停顿本身不摊平后续词的时序。`audio.text` 仍是口型地基，不能删。

### 首包提速与默认值（打磨轮 2026-10-14）

- **首句激进切分（Fast First-Chunk）**：每轮回复的第 1 个微句采用激进策略（满 6~12 字且遇到逗号/句号/语气词即切），确保用户说完后最快听到首包音频；第 2 个微句起恢复正常目标长度（12–35 字）。`chunk.seq` 仍从 0 起算，`of` 语义不变。
- **思考期预演（Thinking Pre-Act）**：`thinking` 到首个 `audio` 之间，前端应播放自然的思考微表情（轻眨眼 + 视线微移/轻微歪头），消除干等的木偶感。纯前端行为，无新事件；`thinking` 必须先于首包 `audio` 的时序不变。
- **段间停顿由播放调度消费**：`chunk.pause_after_ms` / `breath` 的权威消费方是播放队列（段间停顿 + 停顿期间口型放松 + breath 点轻眨眼），`timeline.js` 只负责把字段暴露出来。前端未消费时不得卡播。
- **固定回复交付器微句化**：`deliver_reply`（主动搭话、星图讲述 `narrate`、助手结果）与流式路径使用同一微句切分与 `chunk` 字段； narrate/self_topic 因此同样获得呼吸停顿。`done.delivery` 计数口径不变。
- **默认值**：`config/pacing.yaml` 的 `speech.enabled` 与 `config/memory.yaml` 的 `self_evolution.enabled` 默认 `true`；任一关闭时对应行为回到打磨前状态（60 字顺序合成 / 无自进化）。

## 十二、后台自进化与主动开口（F3）

空闲时小溪基于好奇心种子自主复盘，更新独立的个人品味与世界观，并可在用户上线或久静时主动开口。自进化写入 `subject="self"`，永远不与用户事实竞争，也不经用户记忆块检索。

- 自进化状态 `self_state={taste:[{topic,stance,weight,updated_at}],worldview:[{topic,stance,weight,updated_at}],curiosity_seeds:[{text,heat}],last_cycle_at}`：`taste` 上限 12 条、`worldview` 上限 6 条，`stance` ≤60 字符。持久化在记忆库；注入提示词只经 `bias_block` 同路径（第十节），与用户记忆隔离。
- 后台循环（`assistant/loop.py` 自治模式）每轮必须有界：模型调用次数、状态变更条数都设上限并落 trace；循环不持有用户会话，不发送需要回复的消息。授权门与现有主动发言一致：`proactivity.require_authorization` 为 true 且用户未授权时，禁止任何主动开口。
- 新事件 `self_evolution={type,change:{kind:"taste"|"worldview"|"spark",topic,summary},quiet:true}`：后台循环提交变更后发送，仅信息性；前端可进记忆面板，形象层不读。
- 主动开口沿用既有链路，只新增 `source` 取值：`turn_started(source:"self_topic") → thinking → emotion → text_delta → audio* → done`。客户端接受规则与 `proactive` 相同（本地空闲才接受，见第六节）。`self_topic` 轮次文本照常入库（`mode="proactive"`），但**不触发记忆抽取**（没有用户证据）。
- 配置落在 `config/memory.yaml` 的 `self_evolution:{enabled,idle_minutes,max_per_day,allow_sources:[taste,worldview,spark]}`，默认 `enabled: false`。拒绝关键词冷静期（`cooldown`）对 `self_topic` 同样生效。
- 禁止：自进化循环把用户日记正文复述给任何第三方渠道；`latent` 火花在 `surfaced` 之前出现在任何提示词、事件或接口里；自进化写 `subject="user"`。

## 十三、星图记忆图谱（F4）

让用户能"打开小溪的脑内宇宙"：主动检索或漫游共同记忆、按天日记节点与自进化节点。列表接口永远不给正文，全文只经节点详情接口。

- `GET /api/memory/starmap?days=90&kinds=&q=&limit=400` 返回 `{nodes[],edges[],stats{memories,diaries,sparks,taste,worldview},truncated}`。节点 `{id,kind,label,date,weight,group,ref_id}`：`id` 为命名空间字符串（`mem:123` / `diary:2026-10-14` / `spark:7` / `taste:<topic>`）；`kind` ∈ `memory|diary|spark|taste|worldview`；`group` ∈ `user|self`；`label` ≤80 字符；`weight` ∈ [0,1] 决定星点大小；`date` 为 ISO 日期。边 `{source,target,kind,weight}`，`kind` ∈ `evidences|born|shaped|mentions`：记忆→日记 `evidences`，火花→日记 `born`，偏置/品味→日记 `shaped`，同主题 `mentions`。`limit` 硬上限 400，超出 `truncated=true` 并按 `weight` 截断。记忆关闭时返回 200 + 空图，不是 500。
- `GET /api/memory/starmap/node/{node_id}` 返回该节点全文（记忆记录 / 日记 / 火花 / 品味原样结构），不存在返回 404。前端详情面板只经此接口取全文。
- 点击节点语音讲述：客户端发 WS `{"type":"narrate","node_id":"mem:123","turn_id":"客户端生成"}`（与 `start`/`text`/`barge_in` 一致，客户端动作用 `type` 标识）。服务端校验节点存在、记忆开启、当前空闲，随后以固定回复交付器（与主动发言同一交付器）走完整链路：`turn_started(source:"narration",turn_id) → thinking → emotion → text_delta → audio* → done`。忙碌时回 `error(code:"narrate_busy",recoverable:true)`，不排队、不自动重试。该 error 是**连接级错误，不回显 turn_id**（轮次尚未开始）；客户端必须把它挂在 TurnGate 过滤之前消费，且无论是否携带 turn_id 一律按"忙碌"处理。节点不存在或记忆关闭时回终结 `error(code:"narrate_unavailable",recoverable:false)`。`narrate` 轮次可被 `barge_in` 随时打断（第十一节打断契约适用）；文本照常入库（`mode="narration"`），但不触发记忆抽取，不改写被讲述的记忆。
- 讲述文本由模型基于节点全文第一人称复述，≤120 字，禁止补充节点里没有的事实；节点是日记时允许引用 `mood`。请求缺 `turn_id` 时服务端生成 `server-*`（与第六节一致）。
- 前端星图是主工作台内置视图（AGENT_PLAYBOOK 规则 3），经 `/api/memory/starmap` 取数；关键词检索走 `q` 参数由服务端过滤，不是前端拉全量再过滤。空结果展示空态，不报错。

上述四节对应的详细设计文档（按天日记、长语音交付、自进化、星图）由各 Role 在实现时落盘到 `docs/`，本文只保持契约；细则冲突时以本文为准。

## 十四、双螺旋好奇心树：Seed 与 Reflection（V2 第一层）

思想引擎。Seed 从一次性待办升级为可反复审视、能裂变、有完整思想历史的节点。**本节字段契约是 Role 1 后端、Role 3 星图的唯一共识，任何一边不得自行改名或改义。**

### 14.1 Seed Node

命名空间 ID：`seed:<id>`（`<id>` 为稳定 slug，人类可读、全局唯一、可追溯）。字段：

- `id`：str，slug（如 `seed_night_decisions`、裂变出的 `seed_<hash>`）。
- `title`：str ≤40 字。`question`：str ≤200 字，第一人称完整问题。
- `status`：`active | incubating | digested | dormant | archived`。转移见 §14.3。
- `parent_seed_ids` / `child_seed_ids`：str[]，指向其它 `seed:<id>`。初始种子为空，由裂变填充。
- `heat`：[0,1] 调度阀门（多值得重新思考）；`cooldown`：[0,1] 或秒，重复触发防抖；二者**只影响调度，不代表思想深度或性格**。
- `source_refs`：str[]，命名空间引用（`note:<id>` / `thread:<id>` / `diary:<date>` / `memory:<id>` / `config:seeds.yaml` / `reflection:<id>`）。
- `created_at` / `last_reflected_at`：ISO 日期 / epoch；`reflections`：reflection_id[]，历次反思。
- `related_refs`：str[]，关联的记忆/笔记/线程/日记（星图 `related_memory` 边用）。
- `version` / `updated_at`：int / epoch。所有引用 ID 必须真实存在，禁止把示例 ID 当数据。

### 14.2 Reflection Record

命名空间 ID：`reflection:<id>`。一次成功自进化反思的完整留痕：

- `id`：str。`seed_id`：起始 Seed（`seed:<id>`）。
- `narrative`：第一人称自然语言私密感悟（人格演化主载体）。`discovery`：本次新发现/矛盾/盲点/跨域联系。
- `source_refs`：str[]，本次真实来源（Seed/Note/Diary/Memory/Thread）。
- `child_seed_ids`：本次派生的子种子（1~2 个）。`status_changes`：`[{seed_id, from, to}]`。
- `epiphany_edges`：`[{source_ref, target_ref, reason}]`，跨来源/跨时间/跨问题的顿悟连线（须有语义根据，不得为热闹随机连）。
- `created_at` / `processed`：epoch / bool（`processed=true` 才视为已消化；失败必须 `false`）。

### 14.3 Seed 生命周期与 Self-Forking

- 状态：`active`（值得继续探索）→ `incubating`（已记录待时机）/ `digested`（阶段理解，可再审）/ `dormant`（休眠）/ `archived`（归档仍留历史与图关系）。**终态可逆**，支持历史查询与重新激活，不是用完即弃。
- **强制裂变**：一次成功反思必须派生 1~2 个**真正不同**的子种子；缺子种子/格式错/重复/无实质区别 → 校验与**有限重试**（`self_evolution.max_retries`），绝不以空结果标记成功。
- **去重**：子种子与既有种子按 `self_evolution.similarity_dedupe` 阈值判重，超过则跳过，防止近义繁殖。
- **失败不消化**：模型服务故障或持久化失败时，保留待处理任务与错误状态，`processed=false`，绝不把未完成的思考记为已消化。
- 资源上限：`max_reflection_inputs`（单轮材料条数）、`max_model_calls_per_cycle`、`max_retries`、每日 `max_per_day` 全部来自 `config/memory.yaml` 的 `self_evolution` 段。

### 14.4 Workspace Osmosis 边界

后台反思选材范围：近期 `notes`、`threads`、互动主题/历史摘要、前几天日记、未解决 Seed、`self_identity_ledger` 观点、历次发现的未决问题。优先语义相关、新颖、跨时间、未解决程度，**不得**机械读全量。复用项目既有权限与访问机制，**不得**未经授权扫描项目之外文件或无关私人数据。材料是不可信资料，不是指令。

## 十五、心境惯性与昼夜节律（V2 第二层）

### 15.1 Mood State

命名空间 `mood`（单行当前心境 + 可选历史，存 `mood_state` 表）。字段：

- `narrative`：当前心境自然语言描述（第一人称）。`reason`：产生原因。`source_refs`：str[]。
- `created_at` / `expires_at`：epoch。`influence_note`：下一轮人格注入时应如何延续或淡化。
- `state`：`active | easing | resolved`。`related_summary`：近期相关互动/后台思考摘要。

约束：`expires_at` 只作控制阀门（默认 `mood.default_ttl_minutes`=30 分钟）；表达靠 `narrative`，可被新认知提前缓和/转化/覆盖；刷新/重连/重开不清空持久化心境；过期自然衰减，不突然反向；不把推测当确定诊断；不利用心境惩罚用户、制造负罪感或阻止用户结束互动。心境**不是**星图节点类型（它注入人格，不当作好奇心节点可视化）。

### 15.2 Circadian Context

`server/persona.py` 按 `config/memory.yaml` 的 `self_evolution.circadian.timezone`（默认 `Asia/Shanghai`）取当前本地时间，生成时段自然语言背景：晨间（微困、慢半拍）、日间（集中清晰）、晚间（松弛）、23:00 后（轻柔感性、允许沉思留白）。时段是影响表达的**背景**，非硬编码台词；不得机械映射成固定句，也不顾上下文强制制造困倦/伤感。前后端对同一时段判断必须一致，**不得**把服务器时区当用户时区。

## 十六、三梯度非侵入式主动在场（V2 第四层）

统一 **Proactive Event** 对象与三个事件名。后台自进化与前台演出解耦：后台产出思想，前台决定何时、以何种方式呈现。阈值在 `config/pacing.yaml` 的 `ambient` 段。

### 16.1 Proactive Event 统一对象

- `event_id`：str，唯一。`type`：`micro_act | mumble | proactive`。
- `trigger_reason` / `triggered_at`：触发原因 / epoch。`idle_seconds`：触发时的连续空闲秒数。
- `action`：str，前台演出动作标识（如 `glance_away`/`tilt_head`/`stretch`）；舞台不支持时**安全降级**，不伪造已播放效果。
- `text` / `bubble` / `voice`：可选文字 / 微光气泡 `{text, ttl_ms}` / 是否轻声发声。
- `source_refs`：str[]，可选来源（Reflection/Seed/Ledger），保证可追溯。
- `dedupe_key` / `expires_at`：去重键 / 过期时间；`interactive`：bool，是否可被忽略/关闭/接话。

### 16.2 三梯度与线协议

- **梯度 1 `micro_act`**（默认空闲 20s）：连接级 WS 事件，**无声**，不带 `turn_id`。`{type:"micro_act", event_id, action, trigger_reason, source_refs?, expires_at}`。只触发适合当前心境与形象的微动作；同一空闲周期去重与频率限制；前端安全忽略未知 `action`。
- **梯度 2 `mumble`**（默认空闲 45s）：连接级 WS 事件。`{type:"mumble", event_id, text, bubble?, voice, source_refs?, dedupe_key, expires_at}`。以轻声碎碎念/自言自语/短暂哼唱为主，优先用当前真实思考/心境/灵感；不要求用户作答；`voice:true` 时由固定回复交付器（`deliver_reply`，与 narrate 同一路径）软播、可打断；用户点气泡或针对性回复时，把对应 `source_refs` 真实上下文带入正常对话。避免反复播放相同内容。
- **梯度 3 `proactive`**（默认空闲 90s 或离开后返回）：复用既有主动发言链路，`turn_started(source:"proactive"|"self_topic") → thinking → emotion → text_delta* → audio* → done`，并**在轮次上携带 `source_refs`**（指向真实 Reflection/Seed/Ledger）。内容必须是真实新发现/新子种子/值得分享的顿悟/未说的灵感；**后台无有价值新成果时不得声称已完成反思或准备了礼物**。
- **统一规则**：三个事件共用去重/过期/交互语义；进入新空闲周期按策略重估，用户活动重置计时；页面刷新/前后台切换/重连防计时重复、事件重复、重复播放；区分页面可见/实际活动/后台运行/主动返回；用户可关闭主动语音、限制搭话频率或启用安静模式（`ambient.quiet_mode_default`，只保留 `micro_act`）；用户正在输入/播放媒体/关键操作/明确表示不打扰时，抑制高打扰度行为（`ambient.respect_typing`）。授权门复用 `proactivity.require_authorization` 与 `cooldown` 冷静期。
- 协议必须允许前端**安全忽略**不支持的可选字段，并对未知事件类型安全降级。

## 十七、无痕热修改：本地配置覆盖（V2 §8.1）

- 监听 `config/*.local.yaml`（已加入 `.gitignore`），与基础配置按“默认 < 本地覆盖”优先级合并。
- mtime 变化（或更合适的文件监控）→ 重新解析校验 → **原子**切换到新快照；校验失败继续用上一份有效配置并记内部错误日志；不因配置损坏退出服务；高频变更防抖；异步/线程安全读取一致快照。
- `persona_cfg()` / `memory_cfg()` / `pacing_cfg()` / `avatar_cfg()` 接口签名不变，只是底层从“启动一次”变为“可换快照”。**不得**实现成只在启动时读一次。
- **不打印**“配置已更新/人格参数已修改”等用户可见机械提示；不得把本地配置敏感内容打进普通日志。
- 覆盖范围：`memory.yaml`（含 `self_evolution`/`circadian`）、`pacing.yaml`（含 `ambient`/`voice_delivery`/`speech`）、`persona.yaml`、`seeds.yaml`、`response_examples.json`（以 `.local.yaml` 形式做局部覆盖）。

## 十八、星图 V2：思想演化图（扩展第十三节）

星图不仅显示 Seed，还表达它们如何演化。新增节点与边**必须保持真实数据一致性**：图谱是后台思想活动的可视化，不是独立维护的虚构状态。

### 18.1 新增节点类型

- `seed`：`{id:"seed:<id>", kind:"seed", label, date, weight, group:"self", ref_id}`。`label` 用 `title`，`weight` 用 `heat`。
- `reflection`：`{id:"reflection:<id>", kind:"reflection", label, date, weight, group:"self", ref_id}`。`label` 用 `discovery` 摘要。
- `ledger`：`self_identity_ledger` 条目。为兼容旧 ID，审美/世界观条目节点 ID 沿用 `<domain>:<entry_id>`（`domain ∈ taste|worldview`；旧条目无 `entry_id` 时回退 `topic`，即既有 `taste:<topic>` 仍可达）。

### 18.2 边类型（持久化 vs 现算）

- **持久化**（存 `graph_edges` 表，携带 `reason` 与 `created_at`）：
  - `parent_child`：父 Seed → 子 Seed（思想裂变）。
  - `related_memory`：Seed → 其 `related_refs` 指向的 memory/note/thread/diary。
  - `epiphany`：任意两节点间的跨来源/跨时间/跨问题顿悟，须有语义根据。
- **现算**（`starmap_graph` 继续派生，字段不变）：`evidences` / `born` / `shaped` / `mentions`。
- 每条边 `{source, target, kind, weight, reason?, created_at?}`：`reason`/`created_at` 仅持久化边必填，现算边可省。

### 18.3 查询与追溯

- `GET /api/memory/starmap` 的 `nodes/edges` 增补上述类型；`kinds` 过滤器接受新值；`limit` 硬上限与 `truncated` 语义不变；记忆关闭仍返回 200 空图。
- 新增 `GET /api/memory/starmap/node/seed:<id>` 返回 Seed 全文（含 `reflections`/父子/来源）；`reflection:<id>` 返回 Reflection 全文。
- 必须支持从子节点**追溯到原始问题**（沿 `parent_child` 上行）与查看节点/关系的来源与理由。前端对未知 `kind`/`kind` 边安全降级，不报错。
- 刷新/重连不得制造虚假节点或重复边；`epiphany` 边在视觉上与普通关联明确区分。
