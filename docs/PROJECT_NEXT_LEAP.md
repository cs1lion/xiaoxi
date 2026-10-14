# 阿栖的下一次跃迁：共享时刻、共享工作、共享进展

日期：2026-10-13。依据当前工作目录源码、已有验收记录、公开项目的官方 README/文档及指定源码。本文记录产品与工程提案及后续实施；没有安装外部项目或调用新云服务。

2026-10-14 后续实施：[事项依据与版本](SHARED_THREAD_EVIDENCE.md)、[事项资料用途与范围](SHARED_THREAD_MATERIALS.md)已经接通。当前真实验收目标为[两周阅读习惯计划](READING_HABIT_TWO_WEEK_TRIAL.md)，先核验取舍、改口和隔日接续；十四天结果、真实模型语义和电脑外放听感仍待采集。

同日实施更新：有声行动、任务澄清续答、共同事项持久选择与修订确认已落地，见 [SHARED_THREADS.md](SHARED_THREADS.md) 和 [VOICE_ACTION_CONTINUATION.md](VOICE_ACTION_CONTINUATION.md)。本轮又接通了便笺不可覆盖的保存版本、显式版本关联、只读工具和跨连接活动恢复，见 [SHARED_ACTIVITY_CONTINUATION.md](SHARED_ACTIVITY_CONTINUATION.md)。下文第2节接缝分析保留提案形成时的状态；可审阅修改建议、跨对象成果关系、共同观看窗口和全局上下文预算仍属后续工作。

## 1. 全局判断

阿栖已经具备三块有价值的基础：可中断的声音与身体、带范围的相处策略、真实的笔记/事项/执行状态。下一阶段的主线应是：**与你处在同一个活动里，能共同推进真实成果，隔天仍从同一处继续的语音搭档。**

成功的展示可以只是一件小事：今晚把演示稿改好；用户插话时她让话；保存后留下真实版本；明天重启，用“接着昨天”回到原稿和下一步。这个闭环同时让声音、人格、记忆、工具和界面发挥价值。

README 和部分设计文档混有第一版范围及后续实现记录。当前源码已包含 VRM、任务运行时、共同事项、笔记、语音体验、字幕/练习和音乐接口，不能仍按“纯聊天第一版”做全局判断。文档中已过期的 404、未初始化停止异常也不能未经验证当作当前故障。

## 2. 已有资产和真正的集成接缝

| 领域 | 当前已具备 | 下一步的关键接缝 |
|---|---|---|
| 普通语音 | 连续采集、Worklet、两阶段打断、旧轮隔离、流式回复和分句 TTS | 末字到首声、句间空隙、附和与打断、不同分支交付一致性 |
| 角色身体 | SVG/VRM 适配，口型、视线、情绪，实际播放时钟的 PerformanceDirector | 将旧 text_delta/done 手势轨道迁到同一播放时间轴；动作需要人工美术校准 |
| 事实记忆 | 后台抽取、来源标签、版本关系、保守检索 | 原消息证据、独立事实保护、时间/范围、顺序与删除守卫，见 MEMORY_RESEARCH_NEXT |
| 相处策略 | 显式证据、scope、修订、冲突失效、逐轮 broker | 当前需求与场景识别，及策略是否落实到最终用户收到的内容 |
| 行动运行时 | 有限 AgentLoop、持久化 run、确认、工具回执、恢复 | 澄清后续输入、跨轮对象引用、声音表达、结构化产物关系 |
| 共用工作面 | 笔记、任务、“此刻”、共同事项和明确候选确认 | 模型能读笔记；对象之间有稳定引用；同一事项跨重启持续 |
| 共同观看基础 | 已确认字幕、邻近片段、草稿/生效来源区别、选句解释和练习 | 目前是一次性交接；需要同活动的短暂来源窗口及时间定位 |

### 三个应优先修复的体验断点

**行动时声音断开。** [server/app.py](../server/app.py) 的行动分支只发送 assistant_result、emotion、text_delta 和 done，没有普通聊天的 TTS/audio；普通对话和体验模式又有不同交付路径。最有用的一刻变成文字结果，声音搭档的连续性被打断。

**澄清后可能接不上。** [service.py](../server/assistant/service.py) 的 should_route 每句按关键词判断，context 没有传近期 history；[loop.py](../server/assistant/loop.py) 将 clarify 与 final 一样标 completed。“提醒我……→几点？→明天下午三点”缺少 waiting_input 对象和待填参数状态。这是源码接缝，尚未在真实模型/生产会话中测得发生率。

**资料和成果彼此没有稳定关联。** 行动工具目前主要是任务与提醒，没有 note.read/search；共同事项选择在 [participation.py](../server/interaction/participation.py) 中是进程内全局。笔记来源塞进任务 description；事项回执只记录工具、状态和 run:index，缺少 task/note/artifact 的结构化引用。隔天能找回文件和知道下一步，需要显式工作状态而不是再给模型塞更多画像。

另外，[service.py](../server/assistant/service.py) 在 ContextBuilder 预算完成后追加 policy 与共同事项，而 broker 的 policy 已包含同一事项，可能重复且超出总预算。需要最终统一的上下文预算，不把每个模块的局部预算当整轮预算。

## 3. 真正值得借鉴的公开实现

已固定默认分支 HEAD，以便之后复核；以下“已支持”指上游资料或源码所示能力，不能等同本项目已接入或已复现其效果。

### AIRI：最接近本项目的角色平台

- [项目](https://github.com/moeru-ai/airi)，代码 MIT，固定 `155860e6ac0938539bd5dbece1ee3324f19e198b`。
- [表情工具源码](https://github.com/moeru-ai/airi/blob/155860e6ac0938539bd5dbece1ee3324f19e198b/packages/stage-ui-live2d/src/tools/expression-tools.ts)：expression_set 工具调用前检查实际 model/expression store 是否加载。
- [插件事件来源契约](https://github.com/moeru-ai/airi/blob/155860e6ac0938539bd5dbece1ee3324f19e198b/packages/plugin-protocol/src/types/events.ts)：有插件、模块实例和来源身份。
- [VRM runtime 官方开发记录](https://airi.moeru.ai/docs/en/blog/DevLog-2026.03.14/)：讨论场景 ownership、按 scope 缓存与绑定阶段门控。

借鉴点是让身体动作成为有类型、有状态检查、有结果的能力。模型选择“轻点头”，身体适配器在实际发声/倾听阶段执行；渲染实例生命周期留在各窗口。Memory Alaya 仍 WIP，部分游戏集成是 PoC，不能将其当成熟长期记忆全套答案。

### Open-LLM-VTuber：一轮输出的文本、声音、动作契约

- [项目](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber)，代码 MIT，固定 `992309c0aa19845960228f880013d4685fde93b5`。Live2D 示例模型另有许可。
- [output_types.py](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/agent/output_types.py)：Actions 包含 expressions、pictures、sounds，可随文本输出同行。
- [conversation_handler.py](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/conversations/conversation_handler.py)：有独立 interruption 入口。

借鉴输出契约，形成一句话的文本、音频、动作与取消关系。其 README 明确长期记忆暂时移除，保留聊天日志；不能当 memory 标杆。README 的打断功能也不能证明已能语义区分附和。

### Pipecat：把实时语音拆成可以测量与替换的节点

- [项目](https://github.com/pipecat-ai/pipecat)，BSD-2-Clause，固定 `24007373eea11403047a681e9ea0470ddef1fc89`。
- [话轮策略示例](https://github.com/pipecat-ai/pipecat/blob/24007373eea11403047a681e9ea0470ddef1fc89/examples/turn-management/turn-management-interruption-config.py)：UserTurnStrategies、Silero VAD、STT、上下文聚合和 Pipeline。
- [最小词数策略](https://github.com/pipecat-ai/pipecat/blob/24007373eea11403047a681e9ea0470ddef1fc89/src/pipecat/turns/user_start/min_words_user_turn_start_strategy.py)：基于转录词数，可使用 interim。

借鉴节点和事件接口，分别测 VAD、收句、转录、模型、TTS、播放。min_words=3 是词数门槛，不能直接拿来当中文语义判断；“停”这样的短命令还要有快速通道。先接一个适配器做对照，再决定是否替换整个语音后端。

### LiveKit Agents：附和、真正打断和误打断恢复

- [项目](https://github.com/livekit/agents)，Apache-2.0，固定 `fe4a0b6a7d25b3d85b71037740f4fd30c782e350`。
- [官方话轮文档](https://docs.livekit.io/agents/build/turns/)：adaptive interruption 分析音频以区分真正打断和 conversational backchannel；也有 resume_false_interruption 与恢复等待参数。
- [interruption.py](https://github.com/livekit/agents/blob/fe4a0b6a7d25b3d85b71037740f4fd30c782e350/livekit-agents/livekit/agents/inference/interruption.py)：独立检测器、16kHz 输入、连续帧门槛及远端推理超时。
- [avatar 示例](https://github.com/livekit/agents/blob/fe4a0b6a7d25b3d85b71037740f4fd30c782e350/examples/avatar/README.md)：RPC 切换脸/声音/人设，工具触发动作。

可研究的惊喜：用户“嗯嗯”时她点头并继续；“等等”时她停下并看回来；误打断后接回未播完的话。adaptive 是 Cloud 等受支持配置能力，不能当成离线 SDK 内置免费分类器；avatar 示例也可能依赖外部服务。中文、弱声、外放回声必须用本项目设备和保留集验证。

### AG-UI：模型和界面共用一个工作状态

- [项目与固定 README](https://github.com/ag-ui-protocol/ag-ui/blob/e6f1485e3ab407f02e85f5d3d9dc25f58f11f118/README.md)，MIT。
- 官方定义是 agent-human event protocol，支持双向状态同步、前端工具和多种 transport。

最值得借鉴的是共享状态与事件：阿栖改第二段时，同一份编辑器展示修改建议；保存后任务和事项看到同一个产物版本。先定义本项目状态/事件适配层，可继续使用原生 JS 与 FastAPI，是否采用 SDK 另由实际收益决定。

### OpenClaw：前台共处与后台工作的统一控制面

- [项目与固定 README](https://github.com/openclaw/openclaw/blob/fc4e122aac62d58ccb52e11cf559d054ac40b2fe/README.md)，MIT。Gateway 管理 sessions、tools、events 和 channel connections。
- [固定 heartbeat 文档](https://github.com/openclaw/openclaw/blob/fc4e122aac62d58ccb52e11cf559d054ac40b2fe/docs/gateway/heartbeat.md)：定期及事件驱动回合，完成事件回到原工作的会话；重置后的旧会话结果不能落到新会话。

借鉴“任务可以在后台推进，但完成结果回到原共同活动”以及静默/投递规则。先让明确任务完成、失败和到期提醒驱动有用通知；不从定时随机寒暄开始。无需同时接入几十个外部聊天渠道。

### Screenpipe：给搭档一个真实的“这里”

- [固定 README](https://github.com/screenpipe/screenpipe/blob/46e35e0fd55039aa4218866d62060b07f7c3f5e8/README.md) 描述屏幕/音频→本地历史→检索与 agent 上下文，还包括 accessibility tree、OCR、时间线等。
- 当前 README 标为 **source-available**，不能沿用旧印象当成默认 MIT 开源；本轮未成功读取完整 LICENSE.md，不判断具体复制/分发条款。

这里借鉴“时间化的来源窗口”：用户明确点“看这里”，绑定所选文档段落、画面或已确认字幕，使“刚才那句/这个按钮/第二段”有共同指向。首版可以只共享所选内容或单次画面；全天记录并不是本项目体验成立的前提。

## 4. 三个足够打动人的产品体验

### A. 昨天的我们，今天能继续

用户：“接着昨天那个演示。”

阿栖：“昨天定了样例目录，稿子停在第二段。我们先补前后对比？”同一工作面打开便笺 v2、昨日明确决定和唯一下一步。用户说“行，先写这段”，修改建议出现；保存后形成 v3 与事项进展。

第二天的惊喜来自真实对象和成果的连续性。最低实现需要：持久化所选事项/便笺、对象引用、只读资料工具、待确认修改建议、真实保存版本。无需先扩成无限自治 agent。

### B. 她懂得“嗯嗯”和“等等”是两回事

阿栖解释时，用户轻声“嗯嗯”，她轻点头继续；用户“等等，换简单一点”，声音和相应动作停下，保留当前对象。用户“接着说”，从明确句段续接，已经完成的操作不再执行。

先让所有路径共享可取消交付器与实际播放时间轴，再接语义判定；点头反馈可以走轻量身体通道，不必每次等待完整 LLM 回答。声音片段播完的 ACK 只证明播放完成，不证明用户理解或同意。

### C. 共同观看，顺手变成你的下一次练习

用户明确共享一段课程。阿栖安静陪看；用户：“刚才为什么用 would？”系统固定已确认来源窗口，解释该句。用户：“这句留着，明天换个场景练。”预览原句、来源和练习目标，确认后成为可继续的活动。

已有字幕、邻近上下文、来源确认和练习入口可以复用；新增的是同一个活动会话和片段时间。采集时间与视频播放时间分开，没有播放器连接时不声称能暂停或跳回原视频。

## 5. 一个统一运行时，三条工作通道

保留目前 FastAPI、原生前端、SQLite 和各模型适配器，先补集成层。

```text
用户语音 / 所选资料 / 任务完成事件
                 ↓
ActivitySession：当前共同活动与对象引用
                 ↓
轻量反馈 ── 说话/动作/附和/取消
对话与行动 ── 澄清/方案/草稿/工具/回执
后台整理 ── 带证据记忆/索引/明确任务
                 ↓
TurnOutcome → 句段交付、编辑器修改建议、真实回执卡
                 ↓
播放状态 / 产物版本 / 事项进展 → 下次接续
```

轻量反馈优先用本地规则、身体状态和经过核验的短反馈；高层语义再由模型决定。后台工作有 run_id、归属活动、预算、取消和结果事件。三个通道是职责划分，不要求三个人设或三个长期运行模型。

建议最小契约（ObjectRef 与只读活动快照已部分实施，其余仍为设计）：

```text
ObjectRef      {type, id, version}
ActivitySession {id, selected_thread, selected_note, source_window, pending_input}
InteractionEvent {id, activity_id, turn_id, source, sequence, object_refs, payload}
TurnOutcome    {turn_id, speech_segments, artifacts, receipts, pending_input}
SpeechSegment  {turn_id, segment_id, text, audio_ref, presentation, state}
```

稳定对象 ID 和版本连接不同数据库，无需立即把数据库合并。ActivitySession 保存“此刻一起做什么”；memory 保存可检索的长期信息；task/run 保存行动真相。工具结果引用真实对象与验证范围，模型表达不能自行把草稿标成已保存或把提醒标成任务完成。

身体意图使用有限 vocabulary，例如 listen、yield_turn、acknowledge、explain、celebrate；渲染器在适当句段/播放阶段执行。身体工具由真实实例状态门控，避免模型直接生成任意骨骼角度。

所有上下文最终在一个组装器内计入总预算；工作快照短而明确，原文和历史按 ObjectRef 读取，不重复注入整份共同事项。

## 6. 发展顺序与验收

### 第一轮：让一次对话完整可靠

1. 普通聊天、体验、工具结果接同一可取消交付器；行动成功和失败都能自然播报。
2. clarify 进入 waiting_input，保存槽位与对象；下一句继续该运行，用户可取消或换事。
3. 统一句段播放状态与头像时钟，预解码下一段，补最终上下文总预算。

验收：“提醒我明晚练英语→几点？→九点半→确认”完整接续、有声且真实；打断后旧音频/动作不复活，已执行操作不重复。测用户末字到首声 p50/p95、句间静音、短停止命令停播、任务路径有声率、澄清续接成功率。现 submitToFirstAudio 从提交后计时，须补收句等待和真实播放起点。

### 第二轮：让一次共同工作产生可接续成果

1. 持久化 activity 中所选事项与便笺；先增加 note.search/read、thread.read、run.receipt。
2. 选中段落→带来源修改建议→显式保存，通过既有 CAS 保留版本。
3. 结构化引用连接 note/task/thread/run；“此刻”展示明确共同活动，而非最新更新任务。
4. 修普通记忆证据与独立事实保护；重启后生成只含已确认状态的简短接续。

验收：三日演示，第一日选便笺并改稿，第二日重启说“接着昨天”，第三日提醒回到原稿并完成检查。必须看到正确产物版本、来源、下一步、声音与真实状态；不能只用人工塞答案测试记忆。

### 第三轮：让共同活动有即时性

1. 使用同一 ActivitySession 接入已确认字幕窗口，先做明确提问式共同观看。
2. 耳机条件下评估中文流式 ASR、附和/打断识别，再测真实外放。
3. 将确定的后台完成、失败、提醒事件投递回原活动，遵守用户选择的静默/播报时机。
4. 收到真实反馈后再考虑桌面壳、所选画面理解和更多工具。

验收：“刚才/这一句/第二段”定位、ASR修订后来源正确、解释后回到活动、未知内容承认缺依据；附和误抢话和停止漏检按题族记录。Cloud 与本地方案成本/延迟分别报告。

## 7. 角色感怎样随着工程一起成长

角色的辨识度可以稳定落在三件事：懂得把负担切成一步、知道何时让话、对真实共同成果有连续反应。共同梗和习惯来自可引用的经历与明确偏好；先保证“我知道我们做到哪”，再增加表达变化。

可以建设一个轻量成果页：已保存稿件、明确决定、做完的练习和验证过的行动。它同时是用户看得见的共同历史、记忆证据源与跨天入口。旧草稿、模型想法、未播完的句段各自保留状态，不能自动算成双方已达成的经历。

继续保留声音剧场等轻松体验；长期世界状态与真实任务状态分区。桌面透明窗口、更多皮肤、游戏控制、多角色协作、模型微调和额外向量框架后置，等主闭环能用测量和用户体验证明。

## 8. 本轮交付与边界

- 本文提供全局发展判断、当前源码接缝、7个公开实现的一手核验和三轮路线。
- 对话中的两个可点场景是概念预演，采用虚构内容；不访问项目 API，不执行工具、不写记忆或真实便笺。
- 预演检查了 7 个状态变化、1024/736/390/320 四种宽度，无页面错误与横向溢出，并查看了渲染截图。
- 没有复现上游 benchmark、没有使用 LiveKit Cloud/adaptive 或实际 avatar 服务、没有做新的真实麦克风/模型/生产验收。

原稿修改闭环现已落地：**修改建议绑定已保存原稿，形成可审阅差异，确认后保存新版本，并向原共同事项追加精确版本成果回执**，见[实施记录](NOTE_REVISION_PROPOSALS.md)。保存与后续对话关联分为两个明确操作，旧候选不会自动改用新原稿。下一阶段优先使用真实原稿验证改写质量与跨天接续，同时测真实供应商、麦克风和扬声器的延迟与打断体验，再决定是否扩展共同观看等入口。

用户反馈后进一步把编写体验作为重点：主页「一起写」进入宽编辑区，明确开启「陪我看稿」后，停笔时偶尔浮出一个引用原文的具体观点，点击才展开与播报。无需先保存或关联即可讨论当前草稿，参见[编辑陪伴实施记录](EDITOR_COMPANION.md)。下一步应评估观点是否值得听、是否打扰和是否有助于形成判断，避免继续堆积编辑管理功能。

2026-10-13 实施进展：有声行动与确认结果、同session澄清续答、下一段音频预解码已落地，见[实施记录](VOICE_ACTION_CONTINUATION.md)。普通聊天保留原流式路径；重连后所选便笺版本与共同事项恢复已落地，未重新绑定旧执行。统一上下文总预算、自然换题边界和真实设备表现仍需后续处理，第一轮的所有设计目标尚未全部验收。
