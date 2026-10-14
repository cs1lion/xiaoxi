# 有声行动与澄清续答：2026-10-13 实施记录

本轮让行动从一句命令发展为能接续、有真实结果、能被打断的对话。例如：

```text
用户：提醒我练英语。
助手：几点提醒你？                  waiting_input，同一 run
用户：九点半。
助手：先确认一下这个操作的内容。    waiting_confirmation，尚未创建提醒
用户：点击「确认执行」
助手：根据实际回执说明结果并播报。  completed / failed / timed_out 等真实状态
```

这条状态与声音链路已通过隔离测试；示例中的时间解析由真实模型负责，测试使用预设决策和时间，不能据此宣称真实模型一定理解「九点半」。

## 已实现

- `server/dialogue/delivery.py` 负责已经生成的文字交付，不再调用模型或工具。体验、行动结果和主动发言复用它，输出 `emotion → text_delta → audio* → done`。普通聊天保留并行模型生成和分句 TTS，沿用同一音频与 delivery 协议。
- 选定语气影响合成；角色事件情绪独立。静默模式不发空音频；TTS 失败发可恢复错误，保留完整文字和 done。默认每次拉取音频限 35 秒、128 块、总量 16 MiB、单块原始数据 9 MiB。
- `clarify` 持久化为 `waiting_input`。原请求、助手澄清、用户续答分别保存；同一连接的下一句即使没有行动关键词也能继续原 run。默认等待 15 分钟，最多续答 8 次，每次最多 4 个决策、全 run 最多 3 次工具执行。
- 澄清领取使用 SQLite 事务和状态比较，保留已执行回执与工具预算。相同 run 内成功写操作按完整参数 hash 去重；确认仍绑定具体参数、单次消费。上下文重新读取偏好，但固定原共同事项；原事项删除后不会接到后来选择的事项。
- 主页面确认按钮使用 `assistant_confirm` WebSocket 事件。服务端检查原 run 的连接 session；点击后生成一个新 voice turn，执行或拒绝一次，再呈现卡片并播报结果。独立任务面板保留原 REST 确认方式。
- 浏览器只预解码下一段音频，仍按顺序播放。默认最多两个实际未结束的解码任务；无法取消的旧 `decodeAudioData` 也占用名额。队列有数量、字节和超时边界；打断丢弃预解码结果，暂停续播只续当前句剩余部分。
- 取消抵抗测试覆盖忽略取消的模型、TTS 和解码器。旧声音和结果不进入新 turn；异步工具若已产生成功副作用，先保存真实回执，再记录 interrupted，不自动重做。

`done.delivery.playback_confirmed` 仍为 false。合成完成与浏览器真正播完不同，尚未增加客户端播放确认。

## 当前边界

- 澄清自动续接适用于原 session。存储可跨服务重构保留等待，但主页面新建 WebSocket 会取得新 session；刷新、重连后的自动恢复尚未实现。
- 独立的「取消」「算了」「不用了」等句子确定性结束等待。自然换话题仍交给模型在原 run 理解，保留原场景和共同事项；尚未声称具备可靠的自动换题分类。
- 到期查询立即排除旧等待；旧 run 的数据库状态在领取或下次发布等待时更新，没有每条等待的后台到期定时器。
- 停止声音不会撤销已执行操作。确认等待超时、连接断开或被打断时不会自动转 REST 重试，任务实际状态可从面板核对。
- 提醒收件箱的后台通知仍为文字；本轮主动发言有声音，不等于已经实现后台提醒的语音投递。
- 真实麦克风、中文时间语义、供应商延迟与句间静音尚待真实环境测量。统一上下文总预算仍待补齐；所选便笺保存版本与跨连接活动恢复已在后续阶段实施，见 [共享活动续接](SHARED_ACTIVITY_CONTINUATION.md)，它不恢复旧连接的澄清或执行。

## 验证

Python 集合 194 项通过，覆盖固定回复与普通流式交付、体验、语音轮次、真实 assistant 续接与 WebSocket、执行循环、存储、上下文、共同事项和 HTTP。使用临时数据库、内存记录、假模型及假 TTS，不导入主 app 的生产数据库初始化。

Node 54 项通过，覆盖播放/预解码、录音、双工门控与暂停续播。真实页面的隔离 Playwright fixture 三组通过：原连续语音、独立面板 REST 确认、主页面 WS 确认（7条路径）。浏览器运行产品代码，网络和音频设备边界为假实现，不能用其计时推断供应商或真实扬声器表现。协议与 JavaScript 语法检查通过。

```powershell
$env:DB_PATH=':memory:'; $env:LLM_PROVIDER='mock'; $env:LLM_API_KEY=''
.\.venv\Scripts\python.exe -m unittest scripts.test_reply_delivery scripts.test_stream_delivery scripts.test_experience scripts.test_voice_turns scripts.test_experience_websocket scripts.test_practice_websocket scripts.test_assistant_websocket scripts.test_assistant_continuation scripts.test_agent_loop scripts.test_assistant_store scripts.test_assistant_context scripts.test_companion_integration scripts.test_assistant_api
node web/voice/voice.test.mjs
node web/voice/playback.test.mjs
node web/voice/duplex.test.mjs
node web/voice/capture.test.mjs
node web/voice/checkpoint.test.mjs
node scripts/test-voice-browser.mjs
node scripts/test-assistant-browser.mjs
node scripts/test-assistant-confirm-browser.mjs
.\.venv\Scripts\python.exe scripts/check_protocol.py
```

未重启正在运行的生产服务，未调用付费模型或 TTS。
