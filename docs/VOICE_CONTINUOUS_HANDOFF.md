# 连续语音第一版：暂停接入记录

状态：已恢复并完成第一版前后端接入。本文件保留早期暂停记录；以下“待接入”描述为历史记录，最新使用方式和限制以 CONTINUOUS_VOICE.md 为准。最终验证：15项模块测试、25项服务端fake隔离测试、13项离线浏览器模拟检查全部通过；协议检查、git diff --check通过。未进行真实麦克风、扬声器和云模型端到端声学验证。

## 目标

连续对话开启后，本地判断开口，保留约250ms预录，确认开口才上传；停顿约900ms自动收句；重新开口取消旧ASR/生成/播放；迟到WebSocket事件、解码和onended不能污染新轮次。保留按住说话，权限申请途中松手不得迟到开始录音；关监听/断线释放麦克风。

第一版只使用能量VAD和浏览器回声消除，不具备语义附和识别。耳机与扬声器需要实际设备验证。服务端done不代表播放结束。停止播报不撤销已执行操作。

## 已准备的独立文件

- web/voice/pcm.js：流式重采样/PCM16/固定帧。
- web/voice/vad.js：开口、停顿、预录、29.5秒上限及等待安静再启动。
- web/voice/playback.js：有序解码播放与generation失效。
- web/voice/turns.js：轮次门禁。
- web/voice/microphone.js：麦克风权限生命周期。
- web/voice/voice.test.mjs：模块回归。
- scripts/test-voice-browser.mjs：真实页面模块+mock WebSocket/AudioContext/getUserMedia的离线集成测试草案；需接入后调整运行，当前不是已通过的测试。
- scripts/test_voice_turns.py：服务端验收测试草案；WS实现暂停并撤回后暂不可通过，不应宣称已完成。

模块测试已由主线程复验：`node web/voice/voice.test.mjs`，12/12通过。模块仍需集成审查：TurnGate全局消息当前仅ready/pong/config，需补真实config_ack及助手通知；retired集合需有上限；PlaybackQueue缺解码超时和队列上限，目前串行解码与播放可能增加句间隙，应评估有序预解码但不能破坏取消隔离。

## 已约定的协议

- start/text：客户端生成唯一字符串turn_id。start立刻取消旧ASR/生成。
- audio_end/barge_in：携带对应turn_id；barge_in针对被取消的旧轮，之后start/text使用新ID。
- 所有轮次事件回显捕获的turn_id，在前端avatar.onMessage之前拒收过期事件。
- 旧客户端缺turn_id时，服务端兼容分配ID。
- ASR在独立task执行，不阻塞WS接收新start/barge_in。
- 空识别：asr空+done；异常error终结本轮。
- 服务端PCM16单声道30秒硬限制，超限清缓冲error，不送ASR。前端29.5秒包括预录，留边界余量。
- 主动轮次仅空闲启动，先发turn_started携服务端生成ID；前端只有真正空闲且未申请手动录音时接受，否则忽略。
- done仅表示生成完成；所有待解码/播放音频结束后才成为idle。

## 继续时必须先检查

1. 重新read web/app.js、server/app.py、web/index.html、web/ui.js及助手接入；这些文件正在另一任务中变化。不要恢复旧版文件。
2. 保留助手面板bootAssistant/showAssistantResult、avatar启动与新UI逻辑。
3. 先集成客户端状态机，再匹配服务端轮次协议。检查assistant_result也需轮次标签，任务执行取消语义不能与停止播报混淆。
4. 修改check_protocol.py时注意其静态字典字段提取看不到统一send_turn追加的turn_id；应补充契约检查，不能删测试规避。
5. 运行模块测试、服务端隔离测试、Playwright离线集成测试；不得用个人data库做测试。
6. 最后实际设备验证漏字、误打断、停嘴延迟、长停顿和中文口语修正。

## 约束

现有工作树大量用户改动，不要git checkout/reset。共享入口改动暂停时只精确撤回本次自身尚未接完的片段。独立浏览器测试不需要启动替代应用服务器，路由由Playwright拦截。
