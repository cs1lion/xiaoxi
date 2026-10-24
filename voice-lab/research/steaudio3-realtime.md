# StepAudio 3 Realtime / Chat 一手资料调研

> 调研日期：2026-10-24 ｜ 资料截止：当日 platform.stepfun.com 文档 + arXiv 2609.14005 + 官方 demo `0812a2d`（2026-06-09）
> 口径：只记事实。文档没写的，一律写「文档未说明」，不从 OpenAI Realtime 约定外推。
> 本文属 voice-lab 探索区，不代表进入路线图；出口只有「ADR 候选」或「不做结论」。

## 一句话结论

阶跃把 Realtime 设计成**自带大脑的完整闭环**（服务端持有对话历史、自己做推理、自己发声），而本项目的设计是**快环只搬运、判断与记忆装配在自己手里**；在「文本注入发声」「字级时间戳」「输出硬约束」三处，阶跃的文档（含官方 demo 的代码）都没有给出可依赖的接口，因此 realtime 无法退化为「只有耳朵和嘴的身体」，接入的前提是接受它替本项目做决策。

## 资料来源

| 类型 | 链接 |
| - | - |
| 事件面权威 | [双向实时语音 API](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)（全文 [llms-full.txt](https://platform.stepfun.com/docs/llms-full.txt)） |
| 开发指南 | [实时对话](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) ｜ [语音对话](https://platform.stepfun.com/docs/zh/guides/developer/audio-chat.md) |
| 模型页 | [StepAudio 3 Realtime](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md) ｜ [语音模型列表](https://platform.stepfun.com/docs/zh/guides/models/audio.md) ｜ [模型列表](https://platform.stepfun.com/docs/zh/guides/models/list.md) |
| Chat API | [Chat Completions](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md) |
| 音色 | [官方音色详情](https://platform.stepfun.com/docs/zh/api-reference/audio/system-voices.md) ｜ [音频合成指南](https://platform.stepfun.com/docs/zh/guides/developer/tts.md) |
| 商务 | [API 定价](https://platform.stepfun.com/docs/zh/guides/pricing/details.md) ｜ [速率限制](https://platform.stepfun.com/docs/zh/guides/pricing/rate-limits.md) |
| 条款 | [用户协议](https://platform.stepfun.com/docs/zh/agreement/userservice.md) ｜ [隐私政策](https://platform.stepfun.com/docs/zh/agreement/userprivacy.md) |
| 技术报告 | [arXiv 2609.14005](https://arxiv.org/abs/2609.14005)（[HTML 全文](https://arxiv.org/html/2609.14005v1)） |
| 官方代码 | [Step-Realtime-Console](https://github.com/stepfun-ai/Step-Realtime-Console) @ `0812a2dd82b94602ea380c73468abb1718e28053` |

---

## 1. WebSocket 会话生命周期

| 项 | 事实 | 出处 |
| - | - | - |
| 端点 | `wss://api.stepfun.com/v1/realtime` | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| Step Plan 端点 | `wss://api.stepfun.com/step_plan/v1/realtime`，「接口参数与开放平台完全一致」；但 Step Plan 语音模型接入页的**模型表只列 `stepaudio-2.5-realtime`，未列 stepaudio-3** | [Step Plan 语音模型接入](https://platform.stepfun.com/docs/zh/step-plan/integrations/audio-api.md) |
| 模型选择 | 连接时 query 参数 `model=…`（必带） | [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 鉴权 | 请求头 `Authorization: Bearer YOUR_STEP_API_KEY`；**只支持 header，未见 query param / token 协议的支持说明** | [API 参考请求头](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 可用模型 ID | `stepaudio-3-realtime-preview`、`stepaudio-2.5-realtime`、`step-1o-audio`、`step-audio-2`、`step-audio-2-mini`、`step-audio-r1.5` | [API 参考请求参数](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 握手后首事件 | 服务端自动发 `session.created`，含默认 session 配置 | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 配置更新 | 随时可发 `session.update` → 回 `session.updated`；「任何字段都可能随时更新，除了 `voice`」 | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| voice 锁定时点 | 模型**首次以音频响应后**，该会话的 `voice` 不可再改 | [开发指南 会话生命周期](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 会话最长时长 | **30 分钟**（文档正文写明） | [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 错误后会话 | `error` 事件「会话会继续保留」 | [API 参考 错误事件](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 关闭 / 重连语义 | **文档未说明**：无服务端关闭码约定、无重连后历史是否延续、无 session 恢复接口 | — |
| 服务端 session TTL（闲置多久被回收） | **文档未说明**。`max_idle_timeout` / `pong_timeout` 只出现在 `stepaudio-2.5-asr-stream` 的错误码表，**不属 realtime 文档** | [ASR 流式文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md) |

官方 demo 的连接与重连事实（代码 > 散文）：

- 中转服务器构造 `wss://api.stepfun.com/v1/realtime?model=${model}`，在 header 注入 `Authorization: Bearer ${apiKey}` — [hooks.server.ts:107,120-121](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/hooks.server.ts#L107)
- 浏览器原生 WS 不能带 header，故 demo **强制走服务端中转**；README 进一步建议生产环境也用中转以保护 Key — [README「WebSocket 中转说明」](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/README.md)
- **demo 没有断线重连逻辑**，只有：10 秒连接超时、连不上即向客户端造一个假 `type:'error'` 消息并关闭、上游非 1000 关闭码即转成 `connection_closed` 错误 — [hooks.server.ts:158,193](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/hooks.server.ts#L158)
- 并发/限流是在 **HTTP 建连阶段**以 401 / 429 字符串匹配暴露的，不是 WS 事件 — [hooks.server.ts:236-241](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/hooks.server.ts#L236)

## 2. 上下文控制

`conversation.item.create` 与 `conversation.item.delete` 都在 API 参考中有独立小节，逐字为：

- **注入任意条目**：支持。原文「此事件既可用于填充对话的 "历史记录"，也可用于在途中添加新消息项，**但当前限制是它无法填充 Assistant 音频消息**」— [API 参考 会话消息添加](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **删除**：支持。发 `conversation.item.delete`（`item_id`）→ 回 `conversation.item.deleted`；条目不存在则回 error — [API 参考 会话消息删除](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **截断（truncate）**：**realtime 文档 0 次出现** `conversation.item.truncate` / `conversation.item.truncated`。对 [llms-full.txt](https://platform.stepfun.com/docs/llms-full.txt) 做 `conversation.item.truncate` 计数：**0 次**（全平台文档）— 见第 15 节
- **无读取接口**：文档未提供 `conversation.item.retrieve` / `.list` 之类事件，客户端无法向服务端查询当前历史 — 对 llms-full.txt 计数均为 0
- **角色支持面**：`role=system` 仅 `input_text`；`role=user` 支持 `input_text` 与 `input_audio`；`role=assistant` 「支持文本内容的消息项」（原文，未明确能否注入 assistant 文本）— [API 参考 response.output_item.added](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **历史归属**：文档明确「上下文管理：内置，自动管理对话历史」，并在 Chat vs Realtime 对比表中把 Realtime 标为「内置」、Chat Completions 标为「需自行维护 messages 列表」→ **服务端为权威持有方**；「是否可全量由客户端提供 / 是否自动滑窗截断」**文档未说明**；**realtime 文档未给出任何上下文窗口 token 数** — [StepAudio 3 Realtime 模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md)
- **每轮记忆注入槽位**：**不存在专用字段**。可选路径只有三条：把检索结果作为 `input_text` 用 `conversation.item.create` 注入历史、塞进 `instructions`、或做成 `retrieval` 工具（见第 8 节，但知识库必须是平台侧的 vector store）— 由 [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) 的字段集合直接推得；文档没有「每轮附加上下文」字段

## 3. 人设 / 指令配置

- `instructions` 即 system message。原文：可指导「回答内容和格式」与「音频行为」，举例 `"要非常简洁"`、`"在你的声音中注入情感"`、`"经常大笑"`；**并明确「不能保证模型会遵循这些说明」** — [API 参考 session.update](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **情绪/语气可用自然语言表达**，无 API 层情绪枚举：API 参考未列任何情绪参数。技术报告侧支持「理解语气、情绪、副语言线索」（[模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md)）
- 中文支持：默认系统指令示例即中文，且文档称「擅长中文，英文，以及多种其他语言的对话」— [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)。旧代 step-1o-audio 模型卡写明「支持中文、英语、重口音普通话输入，支持中、英、日语、粤语和四川话输出」；**StepAudio 3 的输入/输出语种表文档未说明** — [模型列表](https://platform.stepfun.com/docs/zh/guides/models/list.md)
- 官方 demo 的默认人设里**有情绪标签写法，但用中文圆括号**：`用（情绪）开始你的回复`，枚举为 `高兴1/高兴2/生气1/生气2/悲伤1/低语1`，并规定标签互斥且有优先级（RAP > 方言/语种 > 情绪 > 语速）— [src/lib/index.ts `defaultInstruction`](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/index.ts#L34)
- `step-audio-2-mini` 特例：音色需在 `instructions` 结尾追加「请使用默认男声/女声与用户交流」— [API 参考 voice 字段](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)

## 4. 服务端 VAD 与话轮

`session.turn_detection`（**API 参考原文写「ServerVAD 参数，默认关闭」**）：

| 字段 | 类型 | 默认 | 原文语义 |
| - | - | - | - |
| `type` | string | — | 当前仅支持 `server_vad`，配置后开启服务端 VAD |
| `prefix_padding_ms` | integer | 500 | 检测到用户语音开始时回溯添加的前置音频时长 |
| `silence_duration_ms` | integer | 100 | 静音达到该时长则认为用户语音输入结束 |
| `energy_awakeness_threshold` | integer | 2500 | 能量唤醒阈值，范围 **0–5000**，音频能量超过即认为开始说话 |

出处：[API 参考 session.update](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)

- **注意与开发指南自相矛盾**：开发指南「语音活动检测」章称「**默认情况下，实时会话启用 VAD**」，可通过 `turn_detection: null` 关闭；API 参考则称默认关闭。两处均为官方文档，口径不一致，**需实测确认**。
- **阈值口径与 ASR 不同代**：本项目现用的 `stepaudio-2.5-asr-stream` 文档给的是 `threshold`（0–1 概率，「值越大越严格」，示例 0.6）；realtime 给的是 `energy_awakeness_threshold`（0–5000 能量）。字段名、值域、语义**三者都不同，不可迁移调参经验** — [ASR 流式文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md)
- `input_audio_buffer.speech_started`：字段 `audio_start_ms`、`item_id`；原文「**一般用于打断场景**」— [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- `input_audio_buffer.speech_stopped`：正文列 `response_id`、`item_id`，但 **sample 里出现 `audio_end_ms`**（正文未列该字段）— 同上
- **附和（backchannel）与真打断的区分**：**协议层完全没有对应字段或枚举**。能力只写在模型页散文（「支持自然打断、**附和识别**和话权协调」）和技术报告里；`speech_started` 不携带任何「这是打断还是附和」的判定 — [模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md)
- 技术报告侧（模型内部机制，非 API 可观测）：音频按 **320 ms 块**组织，每块后跟一个状态或文本 token；模型据双路音频流 + 对话历史判断「继续听 / 开始说 / 继续说 / 让出话权」；明确称「简短附和表示持续投入而不请求话权转移，实质性请求或纠正才构成打断意图」，另有背景人声拒绝 — [arXiv §5.1–5.3](https://arxiv.org/html/2609.14005v1)
- Full Duplex Bench（AA 子集）自评分：Pause 98.9 / Turn Taking 100.0 / User Interruption 99.0 / Backchannel 98.0，Overall 98.9（基线 GPT-realtime-2 High 95.3、Qwen Audio 3.0 Realtime Plus 98.4）— [arXiv 表 3](https://arxiv.org/html/2609.14005v1)
- API 参考侧的打断机制只有 `response.cancel`，描述为「取消正在进行的响应」→ 回 `response.cancelled`。**「检测到打断后服务端是否立即停止吐音频」文档未说明**（无自动停发的表述）；官方 demo 的做法是**客户端自己在 `speech_started` 时清空本地播放缓冲**（见第 15 节）

## 5. 输出形态

- **同一轮工具调用 + 口语音**：**支持，且是文档推荐做法**。原文：「函数调用和语音可能会出现在同一轮……模型可能会先输出："让我先查查天气" 的口水话，然后再创建函数调用。这样的好处是，如果有比较复杂的函数调用，可以利用语音播放的时间来并行处理函数调用的生成。**但是您需要注意要在语音播放完后才调用 `response.create` 来触发最终回复**」— [开发指南 自定义 Tool Call](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
- 工具事件链：`conversation.item.created`(type=`function_call`, 带 `call_id`/`name`) → `response.function_call_arguments.delta` → `.done` → 客户端 `conversation.item.create`(type=`function_call_output`) → `response.create` — 同上
- 注意 delta 示例的字段名是 **`arguments`**（`{"type":"response.function_call_arguments.delta","call_id":…,"arguments":"{\"location\":\"北","name":…}`），与 OpenAI 的 `delta` 字段名不同 — 同上
- **输出硬约束成固定标签 / JSON**：**realtime 侧做不到 API 级约束**。对 API 参考整节做计数：`response_format` **0**、`json_schema` **0**、`json_object` **0**；`session.update` 可配置字段仅 `modalities`/`instructions`/`voice`/`turn_detection`/`input_audio_format`/`output_audio_format`/`tools`，**文档未列 `temperature`、`tool_choice`、`response_format`**。JSON Schema 严格约束只属于 Chat Completions 的 `response_format.type=json_schema` + `strict:true` — [JSON Mode 指南](https://platform.stepfun.com/docs/zh/guides/developer/json-mode.md)、[Chat API](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md)
- 因此 `[情绪]` 标签只能靠 `instructions` 软约束，而文档自己写了「不能保证模型会遵循这些说明」——**与本项目「每轮必须带合法 `[情绪]`」的硬约束直接冲突**。
- **原生音频下的并行文本轨**：有。`response.audio_transcript.delta` / `response.audio_transcript.done`（字段 `transcript`）。`response.audio.done` 与 `response.done` **不含音频字节**，只含转录文本；音频只在 `response.audio.delta` 里 — [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- `session.created` 示例里出现 `max_response_output_tokens`（一处为字符串 `"4096"`，一处为数字 `4096`，文档自身不一致）；该字段在 `session.update` 参数说明中**未列出** — 同上

## 6. 工具调用 / 边想边说 / 后台任务

| 能力 | 事实 | 出处 |
| - | - | - |
| 思考流事件 | `response.thinking.delta`（`delta`）、`response.thinking.done`（`thinking` 完整思考文本）；`response.content_part.added/done` 的 `part.type` 支持 `text`/`audio`/`thinking` | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| **思考是否可关 / 可调** | **文档未说明**。API 参考与开发指南均未出现 `reasoning`/`thinking` 开关字段（`reasoning=true` 只出现在 model 对象里）；模型页称「自适应推理」由模型自选 | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 边想边说（技术报告） | Think-While-Speaking：私有推理与口语输出并行；Adaptive Thinking 在各能力类目的思考触发率 51.5%–82.0%；相对强制 no-think，Reasoning 类目提升最大（11.37 分） | [arXiv §6.3.1 表](https://arxiv.org/html/2609.14005v1) |
| **后台任务期间对话继续** | 散文 + 技术报告明确：Voice Agent「可以在意图明确后调用工具或**执行后端任务，任务运行期间对话仍可继续**，结果返回后自然接入交流」；技术报告 §7.2 称用户可询问进度、追加要求、切话题，结果回来后并入上下文。**但 API 参考里没有任何异步任务/progress 事件** —— 0 次 `background`，无 task id 字段，无进度事件 | [模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md)、[arXiv §7](https://arxiv.org/html/2609.14005v1) |
| **官方给出的延迟数字** | **文档与报告均未给出 ms 级首字/端到端延迟指标**。散文只有「极低延迟」；技术报告给的是基准分（MMSU 90.6、AA Full-Duplex Overall 98.9、τ-Voice macro 56.0%），并在 API 页注明「Artificial Analysis 榜单数据基于内部参数配置，实际 API 服务表现可能与榜单结果存在差异」 | [API 参考 NOTE](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[arXiv 摘要](https://arxiv.org/abs/2609.14005) |
| 架构规模 | MoE，总参约 196B、每 token 激活约 11B，语言骨干基于 Step 3.7 Flash | [arXiv §3](https://arxiv.org/html/2609.14005v1) |
| τ-Voice 分项 | Airline 60.0 / **Retail 37.7** / Telecom 70.2，macro 56.0（GPT-Realtime-2.1 High macro 45.7） | [arXiv 表 9](https://arxiv.org/html/2609.14005v1) |

## 7. 音频格式

- **输出侧（权威）**：`output_audio_format` **当前仅支持 `pcm16`**；输入侧 `input_audio_format` 同样仅 `pcm16` — [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **采样率文档未说明**：API 参考通篇未给数字。官方 demo 代码给出实际取值 **24000 Hz、单声道**：`const sampleRate = 24000` 同时用于麦克风输入（WavRecorder）与播放（WavStreamPlayer）；SDK 侧 `defaultFrequency = 24_000` — [+page.svelte:19-21](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/routes/%2Bpage.svelte#L19)、[conversation.js:19](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/conversation.js#L19)
- 开发指南的浏览器播放示例亦写 24000（注释标「示例用」）— [开发指南 SimplePCMPlayer](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
- **分块**：输入 base64 音频，「每个块的大小不能超过 15 MB」；chunk 大小影响延迟（ASR 页建议减小分块降延迟，开发指南建议 20–30 ms 小块提交）— [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)、[ASR 流式文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md)
- **输入/输出能否不同格式**：字段分离（`input_audio_format` / `output_audio_format`，且各有会话级与响应级两层），但**两者当前各自只支持 `pcm16`，文档未说明确实可以用不同值** — [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
- **API 参考未列 `g711_ulaw` / `g711_alaw`**；这两个值只出现在 demo 里（且是从 OpenAI SDK 原样继承的硬编码，见下）— [client.js AudioFormatType](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L7)
- 音频格式不一致的另一处：Chat Completions 端到端语音模型流式输出是「`pcm`（24kHz, 单声道, 16bit）」，非流式是 `wav`；而 realtime 文档只写 `pcm16`，命名与描述方式两代不同 — [Chat API](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md)、[语音对话指南](https://platform.stepfun.com/docs/zh/guides/developer/audio-chat.md)

## 8. 内置 web_search 与 retrieval

- **工具类型声明不一致**：`session.update.tools[].type` 的参数说明写「**总是为 `function`、`retrieval`**」，但开发指南给出 `type:"web_search"` 的完整示例。两处都是官方文档，字段说明漏了 `web_search` — [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
- 开发指南原文：「目前 StepFun Realtime API 内置了以下工具：web_search 和 retrieval」
- **web_search**：`{"type":"web_search"}` 即启用；`options.top_k` 默认 5，`options.timeout_seconds` 默认 3；「模型在回答需要搜索的问题时会自动调用该工具，您不需要做其他额外的逻辑处理」。计费 0.04 元/次 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)、[API 定价](https://platform.stepfun.com/docs/zh/guides/pricing/details.md)
- **retrieval**：`{"type":"retrieval","function":{"description":"…触发域描述…","options":{"vector_store_id":"…","prompt_template":"…{{knowledge}}…{{query}}…"}}}`；`vector_store_id` **必填**，需**提前在平台创建**；`function.description` 用于让模型判断是否走该库；未给 `prompt_template` 则用默认模板 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
- **能否指向客户自己的知识库**：**不能指向客户自有外部存储**。检索发生在阶跃侧：知识库由平台 Vector Store API 创建与管理（[创建知识库](https://platform.stepfun.com/docs/zh/api-reference/vector-stores/create.md)、[知识库指南](https://platform.stepfun.com/docs/zh/guides/developer/retrieval.md)），本项目 SQLite 里的记忆**必须先复制上传到阶跃**才能被 `retrieval` 命中；这与「记忆物理硬删除、不留索引」的项目约束相冲。
- 自建检索的替代路径：用 `type:"function"` 自定义工具，由本项目自己查 SQLite 并用 `function_call_output` 回填 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)。但触发时机与注入位置由模型决定，**不是每轮固定槽位**。
- web_search 与 retrieval 同时开启会互相抢调用，文档给的缓解办法仍是用 `instructions` 引导（不保证生效）— [开发指南 最佳实践](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)

## 9. 限制与商务条款

| 项 | 事实 | 出处 |
| - | - | - |
| 限免状态 | `stepaudio-3-realtime-preview` / `stepaudio-3-chat-preview` 定价表状态 = **限时免费**，输入/输出价格均为 `—` | [API 定价](https://platform.stepfun.com/docs/zh/guides/pricing/details.md) |
| **限免到期日** | **文档未说明具体日期**。只写「限免到期后该预览版本将下线，并新增正式付费版本」，全文档检索无任何截止日期 | [模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md) |
| 继任价格 | **未公布**。可对标的现成档位：`stepaudio-2.5-realtime` 输入 10 / 缓存命中 2 / 输出 70 元·百万 tokens；`stepaudio-2.5-chat` 输入 10 / 缓存命中 2 / 输出 25 | [API 定价](https://platform.stepfun.com/docs/zh/guides/pricing/details.md) |
| **并发** | 个人账户按**累计现金充值**分档：V0(<¥100) **5** ｜ V1 ¥100–499 → 20 ｜ V2 ¥500–1,999 → 30 ｜ V3 ¥2,000–9,999 → 40 ｜ V4(≥¥10,000) → **130**（RPM 100→2600，TPM 50 万→1300 万）。代金券不计入 | [API 速率限制](https://platform.stepfun.com/docs/zh/guides/pricing/rate-limits.md) |
| 并发档位是否按模型区分 | **文档未说明**（速率限制页只给账户级表格，未按模型拆列；也未说明 30 分钟会话是否计入并发） | 同上 |
| 企业认证账户 | 档位需联系对接商务 | 同上 |
| **realtime 上下文窗口大小** | **文档未说明**。模型列表给 `step-5-preview` 1M、`step-3.7-flash` 256K 等**文本模型**的上下文，StepAudio 3 Realtime 卡片只有「交互模态 语音↔语音」和能力标签，**无 token 上限**；`stepaudio-2.5-chat` 卡片同样未标 | [模型列表](https://platform.stepfun.com/docs/zh/guides/models/list.md) |
| 会话最长时长 | 30 分钟（见第 1 节）；另有旧代 step-1o-audio 卡片写「单次互动时长最长 30 分钟，可处理音频时长最长 70 分钟」 | [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)、[模型列表](https://platform.stepfun.com/docs/zh/guides/models/list.md) |
| 实名认证 | 个人账户需先完成实名认证才能按累计充值分档 | [速率限制](https://platform.stepfun.com/docs/zh/guides/pricing/rate-limits.md) |
| 商用与合规 | 用户协议：输入内容权利归用户方，但**「本平台运行过程中所产生的一切数据由我们享有合法权利，未经我们事先同意，任何人不得为本协议约定之外的目的获取、使用」**（§5.4）；不得把平台服务转售/转租/再授权给第三方（§1.1.2）；基于本服务向最终用户提供的，需对最终用户行为负责（§4.3）；须自行保证传输含个人信息的合法性（§6.4） | [用户协议](https://platform.stepfun.com/docs/zh/agreement/userservice.md) |
| AI 生成内容标识 | 协议 §2.2.4：阶跃会按《人工智能生成合成内容标识办法》对生成内容加显式/隐式标识，用户传播时**不得恶意删除、篡改、伪造、隐匿标识** | 同上 |
| **数据留存期限 / 是否用于训练** | **未给 API 层可选项、未给具体留存天数**。隐私政策仅称「仅在为提供服务所必需的期间内保留」，注销/超期后删除或匿名化；境内存储、不出境；委托第三方含「内容安全审核服务」「数据库存储服务」。**没有任何条款说明 API 输入/输出音频是否用于模型训练，也没有 opt-out 开关** | [隐私政策](https://platform.stepfun.com/docs/zh/agreement/userprivacy.md) |
| 内容安全 | 错误码含 `risk` / `risk_blocked`（内容安全/被拦截）——但这两个码写在 **ASR 流式文档**的错误表里；realtime 文档的错误类型为 `invalid_request_error`、`server_error`，码示例 `invalid_param`、`invalid_value` | [ASR 流式文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md)、[API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |

## 10. 文档自己写明的失败模式 / 限制

1. **`conversation.item.create` 无法填充 Assistant 音频消息**（原文「当前限制是它无法填充 Assistant 音频消息」）— [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
2. **`voice` 一旦模型发出过音频就锁死**，同会话不能换音色 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
3. **`instructions` 不保证被遵循** — [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
4. **VAD 依赖尾部静音帧**：「如果您提交的音频末尾没有包含足够的静音帧，VAD 可能无法准确识别语音结束点，从而导致系统未能及时触发自动回复」；append 片段过长会抬高延迟，建议 20–30 ms 小块 — [开发指南 更灵活的 VAD](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
5. **web_search 与 retrieval 互相抢调用**，需用 instructions 引导 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
6. **工具口水话后必须等语音播完再 `response.create`**，否则时序错乱 — [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
7. **`stepaudio-2.5-chat` 只支持 `text` 模态，选 `audio` 会报错** — [Chat API](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md)
8. 旧代 Chat 音频上下文限制（同族约束，供参照）：请求 base64 后总大小 ≤10 MB（约 7.5 MB wav）、最多 30 段声音、单段 >25 s 自动分段、`step-1o-audio` 不支持上下文传 wav — [语音对话指南](https://platform.stepfun.com/docs/zh/guides/developer/audio-chat.md)
9. **Step Plan 语音模型接入表未收录 StepAudio 3**（只有 `stepaudio-2.5-*`）— [Step Plan 音频接入](https://platform.stepfun.com/docs/zh/step-plan/integrations/audio-api.md)
10. 榜单口径自认：「Artificial Analysis 榜单数据基于内部参数配置，实际 API 服务表现可能与榜单结果存在差异，相关配置将在后续 API 版本中逐步开放」— [API 参考 NOTE](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)
11. 技术报告自认的短板：**多轮约束遵循与 retail 工具任务仍有缺口**（原文「The results also identify remaining gaps in multi-turn constraint following and retail tool-use tasks」）；Adaptive Thinking 的选择策略存在错配（Reasoning 类思考率仅 59.5% 却是收益最大项，Faithfulness 79.2% 却只涨 1.72 分）；口语输出的严格校验**不消除**因私有推理不完整导致的错误；τ-Voice Retail 37.7 为三域最低 — [arXiv 摘要/§6.3/§7.4](https://arxiv.org/html/2609.14005v1)
12. Model Lab 通用免责：「Model Lab 中的模型在快速迭代中，建议用于测试环境及本地开发使用，**不建议用于生产环境**」（注：此条针对 Model Lab，StepAudio 3 preview 未明确套用同一表述，但同为 preview 态）— [模型列表](https://platform.stepfun.com/docs/zh/guides/models/list.md)

## 11. 与 OpenAI Realtime API 的命名对齐与偏离

README 原文坦白：demo 的 `openai-realtime-api-beta` 目录是「基于 OpenAI 提供的实时语音 SDK 的**修改版本**……**阶跃星辰致力于与其他 AI 厂商共建生态，因此采用了兼容 OpenAI 的 API 接口规范**」，且该库「自 2024 年 12 月起就没有再更新」「以硬编码方式固定了一部分模型信息」，故内嵌进仓库改 — [README](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/README.md)

**可验证的一致处**（事件名与结构与 OpenAI 同形）：`session.update/created/updated`、`input_audio_buffer.append/commit/clear/committed/cleared`、`input_audio_buffer.speech_started/speech_stopped`、`conversation.item.create/delete/created/deleted`、`response.create/cancel/created/done`、`response.output_item.added/done`、`response.content_part.added/done`、`response.audio.delta/done`、`response.audio_transcript.delta/done`、`response.text.delta/done`、`response.function_call_arguments.delta/done`、`error{type,code,message,param,event_id}`。

**可验证的偏离处**（以下每条都以阶跃自己的文档/代码为准，不是推测）：

| # | 偏离 | 证据 |
| - | - | - |
| 1 | **VAD 参数不同**：阶跃用 `energy_awakeness_threshold`（0–5000，默认 2500）与 `silence_duration_ms` **默认 100**、`prefix_padding_ms` 默认 500；**无 OpenAI 的 `threshold`（0–1）** | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 2 | **`speech_stopped` 字段不一致**：正文写 `response_id` + `item_id`，sample 却是 `audio_end_ms` + `item_id` | 同上 |
| 3 | **`conversation.item.truncate` / `.truncated` 在阶跃文档 0 次出现**，但 demo SDK 照旧发/收该事件 | 对 [llms-full.txt](https://platform.stepfun.com/docs/llms-full.txt) 全文计数 0；代码 [client.js:656](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L656)、[conversation.js:80](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/conversation.js#L80) |
| 4 | **demo 发送了 API 参考未文档化的 session 字段**：`client.js` 默认 session 里带 `input_audio_transcription:null`、`tool_choice:'auto'`、`temperature:0.8`、`max_response_output_tokens:4096`，而 [API 参考 `session.update`](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) 的参数字段只列 `modalities/instructions/voice/turn_detection/input_audio_format/output_audio_format/tools` —— 前三个字段（`input_audio_transcription`/`tool_choice`/`temperature`）**在该页 0 命中**（已 grep 确认）。这些是 OpenAI SDK 残留字段，服务端是否接受/忽略 **文档未说明**；仅 `max_response_output_tokens` 出现在 `session.created` 示例里 | [client.js defaultSessionConfig:222-227](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L222) |
| 5 | **`tools[].type` 多了内置枚举** `web_search` / `retrieval`（OpenAI 无），且字段说明只写 `function`/`retrieval`，与开发指南示例自相矛盾 | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 6 | **多出思考流事件** `response.thinking.delta` / `.done`，以及 `content_part` 支持 `type:"thinking"` | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 7 | **`function_call_arguments.delta` 的字段名是 `arguments`**，非 OpenAI 的 `delta` | [开发指南示例](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 8 | **`rate_limits.updated` 只在生命周期表里出现，无事件详情小节**（字段未定义） | [开发指南事件表](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| 9 | **阶跃两份文档自相矛盾：`response.output_item.created` 只出现在开发指南的生命周期表**，API 参考只有 `.added`/`.done` 且有独立小节，**无 `.created` 事件定义**；`rate_limits.updated` 同样只在表里出现、无字段定义小节。**注意：官方 demo 代码并不监听 `.created`**（`client.js` 只注册 `server.response.output_item.added` / `.done`），所以这条不是 SDK 残留，而是文档自己的缺口 | [开发指南生命周期表](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) vs [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[client.js:339,376](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L339) |
| 10 | **文档化 `error` 后「会话会继续保留」**，与 OpenAI 部分错误断连的行为不同；且 demo 把 401/429 处理放在 HTTP 建连阶段而非 WS error 事件 | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[hooks.server.ts:236](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/hooks.server.ts#L236) |
| 11 | demo 的浏览器分支仍用 OpenAI 的 **WebSocket 子协议鉴权** `['realtime', 'openai-insecure-api-key.…', 'openai-beta.realtime-v1']`，而阶跃只支持 header —— 该分支在阶跃侧不可用 | [api.js:68-71](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/api.js#L68) |
| 12 | demo 的 Node 分支**把 URL 硬编码回 `wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01`**，未用传入的 `this.url`——直接复用该 SDK 会连到 OpenAI 而非阶跃 | [api.js Node 分支](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/api.js#L113) |

**结论性事实**：SDK 是 OpenAI 库的改本，所以「demo 里出现某事件」**不能**当作阶跃服务端支持该事件的证据；第 3 条（truncate）正是这种「SDK 有、文档无」的实例。

## 12. 文本注入发声（能否「就说我给定的这段」）

- **无「客户端提供 assistant 文本 → 服务端照着念」的接口**。文档未提供任何把外部文本强制转成该轮语音的字段：`response.create` 只有 `modalities` / `input_audio_format` / `output_audio_format`（会话级与响应级两层），`conversation.item.create` 的 `role=assistant`「支持文本内容的消息项」，但：
  - `response.output_item.added` 的 content 说明只逐项写明 system→`input_text`、user→`input_text`/`input_audio`，assistant 一句含糊的「支持文本内容的消息项」，**未说明注入 assistant 文本后是否会被朗读**；
  - 且原文明确 `conversation.item.create` **「无法填充 Assistant 音频消息」**。
- 文档中最接近的是「开场白」技巧，其做法是**改写 `instructions` 让模型自己复述**：「请你原样无修改地输出下面的话：…」+ 客户端主动 `response.create` — [开发指南 实现开场白](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)
- **判定**：**文档未说明 / 不支持**逐字播报。可依赖的最强手段只是 prompt 层「原样输出」，而同一文档又声明「不能保证模型会遵循这些说明」（[API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)）。本项目 `deliver_reply` 固定交付器要求文本与语音严格一致，这条不成立。

## 13. 音频与文本的时间对齐

- **Realtime 输出侧无任何字/词级时间戳**。API 参考的 `response.audio_transcript.delta/done`、`response.text.delta/done`、`response.content_part.done` 都只有文本或 base64 音频，**无 `words`、无时间戳、无对齐字段**；对 realtime 章节做 `words` / `timestamp` / 「时间戳」计数均为 **0**。
- 同厂商**有**字级时间戳的是 ASR：`stepaudio-2.5-asr-stream` 的 `conversation.item.input_audio_transcription.delta/completed` 带 `words[{word,start,end,final}]`（单位秒），且需显式 `enable_timestamp_align=true` 才精确 — [ASR 流式文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md)。**本项目已经在用这套**（`server/speech/step_stream.py` 解析 `words`）。
- TTS 侧文档同样未给实时合成输出的 `WordBoundary`（`words` 只出现在音频文件识别 / 转写场景，见「语音模型列表」的「分句与字/词级时间戳」条）— [语音模型列表](https://platform.stepfun.com/docs/zh/guides/models/audio.md)
- **判定**：**文档未说明**。项目 `PROTOCOL.md` 第 131 行已自记「当前 StepAudio 不提供词时间戳；Edge TTS 可透传真实 `WordBoundary`」，与本次一手核对一致。口型依赖 `audio.word_timings`，realtime 只能走「estimated」分支。

## 14. 音色可控性

| 项 | 事实 | 出处 |
| - | - | - |
| realtime 音色指定 | `session.update.voice`（string），也可在 `response.create` 里设 `voice` | [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)、[开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| **官方音色清单是否对 realtime 明示** | **没有**。API 参考只给「支持官方音色 + 自定义音色，自定义传音色 ID，可用[查询音色列表](https://platform.stepfun.com/docs/zh/api-reference/audio/list-voice.md)查看」，未给 realtime 的枚举表 | 同上 |
| 文档点名的音色 | `step-audio-2-mini`：`qingchunshaonv`、`wenrounansheng`；`step-audio-2`：上述两 + `elegantgentle-female`、`livelybreezy-female`；示例另出现 `linjiajiejie` | [API 参考 voice 字段](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| **`stepaudio-3-realtime-preview` 支持哪些音色** | **文档未说明**（模型页与 API 参考均未给该 ID 的音色表） | — |
| **与 stepaudio-2.5-chat 的音色表** | Chat API 只把 `voice` 参数限定在 `step-1o-audio`/`step-audio-2`/`step-audio-2-mini`/`step-audio-r1.5`，**`stepaudio-2.5-chat` 根本不接受 audio 模态**（选 audio 报错），所以两者不存在共享音色的问题 | [Chat API](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md) |
| **与 TTS 音色是否同一套** | **不写明是否同一套，但存在交叠**。[官方音色详情](https://platform.stepfun.com/docs/zh/api-reference/audio/system-voices.md) 接口 `model` 参数**目前仅支持 `step-tts-2`**，不覆盖 realtime；[TTS 指南](https://platform.stepfun.com/docs/zh/guides/developer/tts.md) 的场景推荐表里 `livelybreezy-female`、`wenrounansheng`、`qingchunshaonv`、`elegantgentle-female` 等 ID 同时列给 `stepaudio-2.5-tts` / `step-tts-2` / `step-tts-mini`——即 **TTS 与 realtime 使用同一批音色 ID 字符串**，但没有任何文档声明两者的音色集合等价或可互换 | 同上 |
| **TTS 的情绪机制 ≠ realtime** | `step-tts-2`/`step-tts-mini` 有**离散标签**：`voice_label.emotion`（高兴/非常高兴/悲伤/生气/非常生气/撒娇/恐惧/惊讶/兴奋/钦佩/困惑）+ `voice_label.style`（慢速/极慢/快速/极快、冷漠/尴尬/沮丧/骄傲/温柔/甜美/豪爽/严肃/傲慢/老年/吼叫/阴阳怪气/磕巴），11 情绪 + 17 风格；**realtime 无 `voice_label` / `emotion` / `style` 参数**，只能靠 `instructions` 自然语言 | [TTS 指南 官方音色清单](https://platform.stepfun.com/docs/zh/guides/developer/tts.md)、[API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 复刻 | 官方音色清单为 TTS 侧；音色复刻见 [create-voice](https://platform.stepfun.com/docs/zh/api-reference/audio/create-voice.md)。`stepaudio-2.5-realtime` **官方音色清单文档未说明**（只说明自定义音色传音色 ID、并称其复刻效果「与 StepAudio 2.5 TTS 相当」，且实际复刻时用 2.5 TTS 作复刻模型） | [API 参考 voice 字段](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) |
| 会话内换音色 | 一旦该会话模型出过音频，`voice` 锁死 | [开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md) |
| **对本项目的影响（事实）** | `config/persona.yaml` 的 `tts_emotions` 7 档情绪**全部共用 `livelybreezy-female`，靠 `instruction` 自然语言调语气**（rate/pitch 是给 edge_tts 的）。这一套与 realtime 的 `instructions` 软约束**语义同构**，但 realtime 不能按句切 `voice`，且「情绪只改语气不改音色」这条约束在 realtime 侧由 voice 锁死自动满足 | 本项目 `config/persona.yaml:246-289` |

## 15. 打断时的音频截断语义

- **API 参考未定义任何「已播报到第 N ms」的事件**。`response.cancel` → `response.cancelled`，原文只说「取消正在进行的响应」。
- `response.audio.done` 原文注明「当 Response **中断**、不完整或取消时也会发出」；`response.content_part.done` 与 `response.output_item.done` 同样列出 `interrupted` / `incomplete` / `cancelled` 状态。**这些事件均不携带截断偏移**。
- `response.done` 的 `status` 枚举为 `completed, cancelled, failed, incomplete`，`status_details` 在文档示例里是 `null`；**文档未定义 `status_details` 的结构，未出现 OpenAI 的 `reason:"interruption"`**。
- **`conversation.item.truncate` / `conversation.item.truncated` 在阶跃全部文档中出现 0 次**（对 [llms-full.txt](https://platform.stepfun.com/docs/llms-full.txt) 全文计数）。
- **官方 demo 的做法暴露了这一真空**，代码可作证据：
  - SDK 的 `cancelResponse(id, sampleCount)` 会发 `response.cancel`，然后**照 OpenAI 协议发 `conversation.item.truncate{item_id, content_index, audio_end_ms}`**，其中 `audio_end_ms = sampleCount / 24000 * 1000` — [client.js:651-662](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L651)
  - `conversation.item.truncated` 收到后按 `audio_end_ms * 24000/1000` 切掉本地音频 — [conversation.js:80-90](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/conversation.js#L80)
  - 页面在 `speech_started` 触发 `conversation.interrupted` → `wavStreamPlayer.interrupt()` 本地停播清缓冲，再 `cancelResponse(trackId, offset)` — [+page.svelte:374-377](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/routes/%2Bpage.svelte#L374)
  - **即：截断偏移完全由客户端自己算（本地播放进度 + 24kHz 样本数换算），不来自服务端**。该 truncate 请求在阶跃服务端是否被接受，文档未说明、demo 也未证明其生效。
- 因此「用户实际听到哪里」**服务端不提供**；「在途音频块是否作废」也**文档未说明**（客户端只能自己丢缓冲）。
- 本项目 `PROTOCOL.md` 第十一节「打断契约」以及 `playback_stopped` / `interrupted` 事件需要知道客户端真实播放位置——这一位置 realtime 侧只能自测。

## 16. stepaudio-3-chat 能否输出音频

- **模型页对 StepAudio 3 Chat 的定义排除了音频输出**：能力标签只有「语音输入、文本对话、上下文理解、工具调用」；原文「适合按轮次提交语音或文本并**获取文本回复**的场景」；「与 StepAudio 3 Realtime 共享 Audio 3 系列的声音理解与推理能力；**如需语音流式输出和全双工交互，请使用 Realtime API**」— [模型页](https://platform.stepfun.com/docs/zh/guides/models/stepaudio-3-realtime.md)
- **[语音对话指南](https://platform.stepfun.com/docs/zh/guides/developer/audio-chat.md) 的音频输出与 `stepaudio-3-chat` 无关**：它的「核心参数」模型表只有 `step-audio-r1.5`/`step-audio-2`/`step-audio-2-mini`/`step-1o-audio` 四个**旧代** ID，音频流式输出（`modalities:["text","audio"]` + `audio:{voice,format:"pcm"}` → 24kHz 单声道 16bit，SSE 里 `delta.audio.data` / `delta.audio.transcript`）全部是这套旧模型的示例；该页**通篇未出现 `stepaudio-3-chat-preview`**。
- **[Chat API 参考](https://platform.stepfun.com/docs/zh/api-reference/chat/chat-completion-create.md) 的 `audio` 参数按模型 ID 白名单限定**：「只在支持端到端模型场景的模型下生效（`step-1o-audio`/`step-audio-2`/`step-audio-2-mini`/`step-audio-r1.5`）」——**`stepaudio-3-chat-preview` 不在这四个 ID 里**；同页 `modalities` 说明另点名 `stepaudio-2.5-chat` 仅支持 `text`。
- **判定**：**文档未说明** `stepaudio-3-chat-preview` 能输出音频，且 `audio` 参数的模型白名单**不含**它 → 按文档字面读，StepAudio 3 Chat = **语音/文本入、文本出**；能出音频的是旧代 `step-audio-2` 系列。
- 工具调用方面：StepAudio 3 Chat 的能力标签含「工具调用」；旧代 `step-audio-2` 已证实可在返回 `tool_calls` 的同时带语音（transcript 如「让我帮您查一下北京的天气」+ `audio.data`），`step-audio-r1.5` 不支持工具调用 — [语音对话指南](https://platform.stepfun.com/docs/zh/guides/developer/audio-chat.md)。**3-chat 是否能工具与音频同轮，文档未说明**（因其音频输出本身未确认）。
- 结构化输出：Chat 侧有 `response_format` = `text` / `json_object` / `json_schema`（+ `strict:true`）——**realtime 没有这套**。本项目 `[情绪]` 标签若要硬约束，只有 Chat 这条路可走，而 Chat 又不出音频，形成结构性矛盾。

## 17. stepaudio-2.5 系列与 StepAudio 3 realtime 的事件面对比

同一厂商两代 WS 协议不一致，本身就是接入成本。逐项对照：

| 维度 | `stepaudio-2.5-asr-stream`（本项目在用，[文档](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream.md)） | Realtime API（[文档](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md)） | 差异性质 |
| - | - | - | - |
| 端点 | `wss://api.stepfun.com/v1/realtime/asr/stream` | `wss://api.stepfun.com/v1/realtime` | 路径不同 |
| 模型指定 | 藏在 `session.audio.input.transcription.model` | **连接时 query `model=`** | 位置完全不同 |
| session 配置形状 | 三层嵌套 `session.audio.input.{format,transcription,turn_detection}` | **扁平** `session.{modalities,instructions,voice,input_audio_format,output_audio_format,turn_detection,tools}` | 不可互换 |
| 音频格式声明 | 结构化 `{type:"pcm",codec:"pcm_s16le",rate:16000,bits:16,channel:1}` | 单字符串 `"pcm16"`（**采样率不在字段里**） | 同厂商两代表达方式不同，16000 vs（代码事实 24000） |
| VAD 字段 | `turn_detection.{type,silence_duration_ms,threshold}`，`threshold` 0–1 示例 0.6 | `turn_detection.{type,prefix_padding_ms,silence_duration_ms,energy_awakeness_threshold}`，能量 0–5000 | **字段名/值域/语义三者全变**；`threshold` 在 realtime 不存在 |
| `silence_duration_ms` 默认 | 800（文档示例值，未标默认） | **100（文档标默认）** | 默认值差 8 倍 |
| `session.created/updated` 结构 | 带 `meta:{session_id,timestamp}` | **无 `meta`**，带 `session.id/object/model/max_response_output_tokens` | 响应体不同构 |
| 转录增量 | `…transcription.delta` 有 `text`/`stash`/`words`（字级时间戳） | **无 `.delta`**，只有 `…transcription.completed`，**无 `words`** | **本项目已在用的 stash 纠错与字级时间戳在 realtime 不存在** |
| `speech_started` | 带 `meta` + `audio_start_ms` + `item_id` | `audio_start_ms` + `item_id`（无 `meta`） | 少一层 `meta` |
| `speech_stopped` | 带 `audio_start_ms` + `item_id`（原文字段名如此） | 正文写 `response_id`+`item_id`，sample 给 `audio_end_ms` | **两代字段含义/命名都不一致** |
| 事件面额外能力 | 无 assistant 输出事件 | 多 `response.audio.delta`、`response.audio_transcript.*`、`response.text.*`、`response.thinking.*`、`response.function_call_arguments.*`、`conversation.item.create/delete`、`response.create/cancel` | realtime 事件面大一圈 |
| 错误码表 | `max_idle_timeout`、`pong_timeout`、`risk`/`risk_blocked` 等（有 idle/心跳超时码） | 类型 `invalid_request_error`/`server_error`，示例码 `invalid_param`/`invalid_value` | 码表不同；realtime 侧无 idle/pong 码 |

**关键事实**：两代协议**事件名相同但 payload 结构不同**（`meta` 层的有无、`audio_start_ms` vs `audio_end_ms`、嵌套 vs 扁平）。本项目 `server/speech/step_stream.py` 现有的解析逻辑（读 `event['type']`、`item_id`、`stash`、`words`，并做 `_read_event` 大小/类型校验）**不能直接复用**到 realtime；`words` 字段在 realtime 输出侧根本不存在（见 §13）。

## 18. 对本项目接入的直接障碍清单

以下每条都是**已核实的事实**，不含建议与架构判断。「核实方式」标明是文档原文、全文计数、还是代码阅读。

| # | 障碍 | 核实方式 | 撞上的项目约束 |
| - | --- | --- | --- |
| B1 | **realtime 无「念这段文本」接口**：`conversation.item.create` 明文「无法填充 Assistant 音频消息」；最接近的只是改 `instructions` 让模型「原样无修改地输出」，而同文档声明 instructions「不能保证被遵循」 | 文档原文（API 参考 + 开发指南开场白节） | 固定回复交付器 `deliver_reply`（PROTOCOL 的 narrate / proactive / mumble / self_topic 全走它）要求文本与语音一致；**realtime 不能只当「有耳朵和嘴的身体」** |
| B2 | **服务端 0 次出现 `conversation.item.truncate`/`.truncated`**；截断偏移由 demo 在客户端自算（本地播放进度 ÷24000） | 对 llms-full.txt 全文计数 = 0 + 读 SDK 代码 | 第十一节打断契约、`playback_stopped`、「续播被打断句」需要「用户实际听到哪里」 |
| B3 | **realtime 输出侧无字/词级时间戳**：realtime 章节 `words`/`timestamp`/「时间戳」计数 = 0；`audio_transcript.done` 只给整段 `transcript` | 全文计数 | `audio.word_timings` 与口型；PROTOCOL:131 已自记「当前 StepAudio 不提供词时间戳」→ 只能恒走 `alignment:"estimated"` |
| B4 | **realtime 无 `response_format` / `json_schema` / `json_object`**（计数 0），也无 `temperature` / `tool_choice`；输出格式只能靠 `instructions` 软约束，且文档自认不保证遵循 | 对 realtime 章节计数 + 字段枚举 | AGENTS.md「每轮 Assistant 示例都带合法 `[情绪]` 标签」；形象层解析 `[情绪]` |
| B5 | **API 参考称 VAD「默认关闭」、开发指南称「默认启用」** —— 官方两份文档自相矛盾 | 两份文档原文对读 | 快环话轮判定；换引擎前必须实测，不能照文档写 |
| B6 | **VAD 参数与本项目现用 ASR 代完全不兼容**：realtime 只有 `energy_awakeness_threshold`（0–5000，默认 2500），无 `threshold`（0–1）；`silence_duration_ms` 默认 100 vs 现用 800 | 两代文档字段表 | 已调好的 VAD 参数无法迁移；`server/speech/step_stream.py:94-96` |
| B7 | **附和与真打断的区分在协议层不可观测**：`speech_started` 无分类字段；能力只在模型页散文与技术报告（320 ms 块 + 状态 token，bench 自评分）里，API 无对应枚举或置信度 | 文档原文 + 字段表 | 反附和误打断 / 漏打断；快环「不能阻塞」下的 barge-in 判定 |
| B8 | **`retrieval` 只能指向阶跃平台的 `vector_store_id`**（「需提前在平台创建」），不能指向客户自有存储；`prompt_template` 默认文案还硬编码「如果文档中没用答案则告诉用户找不到」 | 开发指南参数详解表 + 默认模板原文 | 记忆检索三档置信度装配、**记忆 SQLite 物理硬删除不留索引**（数据须先复制到平台侧，删除后平台侧副本不受本项目控制） |
| B9 | **`web_search` 不在权威事件文档里**：[API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) 的 `tools[].type` 字段说明只写「总是为 `function`、`retrieval`」，且该页 `web_search` 出现次数 = **0**（已把 `web\_search` 归一化后计数）；`web_search` 只出现在[开发指南](https://platform.stepfun.com/docs/zh/guides/developer/realtime.md)（归一化 4 次）。即「是否真有 `type:"web_search"` 这个枚举」两份文档说法不一致，权威页根本没写 | 逐文件归一化计数 + 字段说明 vs 示例对读 | 不得自造 WS 事件/字段（PROTOCOL 红线）；照任一份文档写都可能写出非法枚举 |
| B10 | **会话硬顶 30 分钟** | 开发指南原文「实时会话的最长持续时间为 30 分钟」 | 陪伴型长驻会话；文档未给续期/接力方案 |
| B11 | **`voice` 在该会话首次出声后锁死** | 开发指南 + API 参考两处原文 | 「同一情绪只改语气不改音色」可满足，但**任何音色级 A/B、换角色、动态音色都不可能在同会话内做**；persona 调参需重连 |
| B12 | **限免到期日与继任价格均未公布**（定价表两行全 `—`／「限时免费」；全文检索无截止日期），继任预览名将「下线并新增正式付费版本」 | 定价表 + 模型页 NOTE 原文 | 商务不可预算；模型 ID 会再变（`-preview` 后缀必换） |
| B13 | **realtime 上下文窗口 token 数文档未给**；模型列表的 1M/256K 只标文本模型，StepAudio 3 Realtime 卡片无上下文项 | 逐卡片核对 | 上下文预算与用量核算（`CONTEXT_BUDGET_AND_USAGE.md`）无依据 |
| B14 | **并发是账户级、按累计现金充值分档**：V0 仅 5 并发，需 ¥100–¥10,000 才升到 20–130；**未按模型拆列，也未说明 30 分钟长会话如何计入并发** | 速率限制表原文 | 多用户/多端同时在线；企业档「联系商务」不可自助 |
| B15 | **官方 demo 无重连**：只有 10 s 连接超时 + 非 1000 关闭码转一条伪造 `type:'error'` 然后关闭；鉴权 401/429 在 HTTP 建连阶段字符串匹配 | 读 hooks.server.ts | 快环稳定性；不能拿 demo 当可靠参考实现 |
| B16 | **浏览器不能直连**：只支持 header 鉴权，故必须服务端中转；demo README 亦建议生产环境中转以免 Key 外泄 | API 参考请求头 + README | 现有 `/ws` 网关架构需新增/改造上游中转 |
| B17 | **官方 SDK 的 Node 分支把 URL 硬编码为 `wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01`**（忽略传入 `this.url`），浏览器分支还用 OpenAI 子协议 `openai-insecure-api-key.*` 传 Key | 读 api.js:68-71,113 | 直接引入该 SDK 会**静默连到 OpenAI**；README 自述该库自 2024-12 起未更新、模型信息硬编码 |
| B18 | **权威事件文档不完整**：把 API 参考全部 `type:` 小节列出来共 **32 个事件**，其中**没有** `response.function_call_arguments.delta/.done`（只在开发指南出现）、**没有** `rate_limits.updated`、**没有** `response.output_item.created`、**没有** `response.cancelled` 小节（仅在 `response.cancel` 正文里被提一句）；而官方 demo 代码反而**不**监听 `.created`（只注册 `.added`/`.done`）。事件面被切在两份文档里，两份都不全 | 对 [API 参考](https://platform.stepfun.com/docs/zh/api-reference/realtime/chat.md) 逐 `type:` 小节枚举 + [client.js:339,376](https://github.com/stepfun-ai/Step-Realtime-Console/blob/0812a2dd82b94602ea380c73468abb1718e28053/src/lib/openai-realtime-api-beta/lib/client.js#L339) | 本项目要按 PROTOCOL 契约枚举事件；照任一份文档写都会漏事件，限流也不可见 |
| B19 | **两代 WS 协议不同构**：ASR 用 `session.audio.input.*` 三层嵌套 + `meta{session_id,timestamp}` + 结构化 `{type,codec,rate,bits,channel}`；realtime 用扁平 `session.*` + 无 `meta` + 字符串 `"pcm16"`，且 realtime 侧**没有** ASR 的 `…transcription.delta` / `stash` / `words` | 两代文档字段表逐行对读 | 现有 `step_stream.py` 解析层零复用；同一厂商内部即需两套适配器 |
| B20 | **StepAudio 3 Chat 的音频输出未被文档确认**：`audio` 参数按模型 ID 白名单生效（四个旧代 ID，不含 `stepaudio-3-chat-preview`）；`stepaudio-2.5-chat` 更是明确「仅支持 `text`，选 `audio` 会报错」；模型页直接写「如需语音流式输出…请使用 Realtime API」 | Chat API 参数原文 + 模型页原文 | 想用「3-chat 出文本 + 自有 TTS」这条中间路线，缺文档依据 |
| B21 | **文档自记技术报告短板**：多轮约束遵循与 retail 工具任务存在缺口；τ-Voice Retail 37.7（三域最低）；Adaptive Thinking 触发率与收益错配；口语严格校验不消除私有推理不完整导致的错误 | arXiv 摘要 / §6.3.1 / §7.4 原文 | 「性格与事实准确性冲突时准确性优先」；工具型任务可靠性 |
| B22 | **无任何 ms 级延迟数字**：文档与报告只有「极低延迟」散文与基准分；API 页另注明榜单基于内部配置、实际服务表现可能有差异、相关配置「将在后续 API 版本中逐步开放」 | API 参考 NOTE + 报告核对 | 非目标条款（不追求压 500ms）之外，连基线延迟都需自建测量 |
| B23 | **后台任务「对话继续」只在散文/报告层，API 层无对应事件**：`background`/task id/进度事件在 realtime 章节计数 0 | 全文计数 + 事件枚举 | 重活异步卸载到慢环的既有契约；无法观测任务生命周期 |
| B24 | **数据与条款缺口**：隐私政策无 API 层留存天数、无「是否用于训练」条款、无 opt-out 开关；用户协议 §5.4 主张「平台运行所产生的一切数据」归阶跃且禁止约定外使用；§1.1.2 禁止把平台服务转售/再授权给第三方；§2.2.4 要求 AI 生成内容标识不得删除篡改 | 协议与隐私政策原文 | 语音是本人敏感个人信息；含他人声音的复刻音色；个人陪伴场景的对外分发 |
| B25 | **`speech_stopped` 自身字段说明与示例不一致**（正文 `response_id`/`item_id`，sample `audio_end_ms`/`item_id`）；`max_response_output_tokens` 一处 `"4096"` 字符串一处 4096 数字，且不在 `session.update` 参数表中 | 同文档内对读 | 不得自造字段；照文档实现会解析到不存在的字段 |
| B26 | **`stepaudio-3-realtime-preview` 的官方音色清单未公布**；[官方音色详情接口](https://platform.stepfun.com/docs/zh/api-reference/audio/system-voices.md) 的 `model` 参数**仅支持 `step-tts-2`**，查不到 realtime 音色；TTS 侧的离散 `voice_label.emotion`（11 情绪）/`style`（17 风格）在 realtime **不存在** | API 参考未给清单 + system_voices 参数表 | `persona.yaml` 的 `tts_emotions` 7 档映射需重做（realtime 侧无标签可用，只有自然语言），音色表可否平移无文档依据 |

---

## 附：未能从一手资料回答的问题

| 问题 | 状态 |
| - | --- |
| 重连语义、服务端 session TTL、关闭码约定 | 文档未说明（demo 也无重连） |
| realtime 采样率数值 | API 文档未写；24000 来自 demo 代码，属**工程证据**而非契约 |
| 上下文窗口 token 数 / 自动截断策略 / 能否全量客户端供给 | 文档未说明 |
| 检测打断后服务端是否立即停发音频 | 文档未说明（无自动停发表述，也无截断偏移事件） |
| 文本注入发声（逐字播报） | 文档未说明 / 按字面不支持 |
| 输出音频的字/词级时间戳 | 文档未说明（计数 0） |
| `stepaudio-3-realtime-preview` 音色清单、及其与 TTS 音色是否同一集合 | 文档未说明 |
| `stepaudio-3-chat-preview` 能否输出音频 | 文档未说明；`audio` 参数白名单不含它，倾向「不能」 |
| 限免到期日 / 继任价格 / realtime 上下文上限 | 文档未说明 |
| API 音频数据留存天数、是否用于训练、可否 opt-out | 文档未说明（条款无相关表述） |
| 并发档是否按模型区分、30 分钟会话如何计入并发 | 文档未说明 |
| 思考模式开关（`reasoning`/`thinking` 控制字段） | 文档未说明 |
| VAD 默认开还是关 | 两份官方文档互相矛盾，需实测 |
| 附和/打断分类是否在事件里可观测 | 协议层无字段（能力只在模型与基准层描述） |
