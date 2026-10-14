# 台词与动作意图

普通流式对话现在可以在同一次回复生成中提供动作意图；服务端将意图与实际口头文本一起交付，前端在音频真正开始播放后安排骨骼动作。文本增量、音频到达、解码完成之前和 `done` 都不会提前执行说话手势。

链路：`LLM 流式回复 → 剥离内部动作标签 → 校验台词锚点 → audio.performance → PlaybackQueue 解码/播放 → Avatar.speak → PerformanceDirector → VRM 手臂/手腕`。

## 同一次生成中的意图

语言模型仍在最前面提供情绪标签；提示词要求每句口头文本之前提供一到两条保留的动作元数据，没有适合的手势时声明 `neutral`，例如：

```text
[认真][motion:{"intent":"self_reference","anchor":"我觉得","strength":0.4}][motion:{"intent":"explanation","anchor":"这个办法","strength":0.35}]我觉得，这个办法可以试试。
```

`[motion:JSON]` 是内部声明，流式过滤器先于句子切分解析并消费它；它不会进入字幕、TTS 输入或存储的正文。该步骤不额外调用一个动作规划模型，也不等待整轮文本完成。只有预定义意图、对应台词中真实存在的锚点和有限强度数值能够进入动作计划；模型无法声明任意骨骼名称、代码或外部动作。

| 意图 | 现有动作 |
|---|---|
| `greeting` / `farewell` | `wave`，招呼 |
| `explanation` / `question` / `reassurance` | `palm`，摊掌 |
| `disagreement` | `deny`，否定 |
| `self_reference` | `self`，自我强调 |
| `neutral` | 保持自然，不加台词手势 |

`neutral` 交付为有效的 `source: "llm"` 空计划；即使句子含“你好”“不是”等关键词，也不会被关键词规则改成招呼或否定。缺少有效动作标签的回复交付 `source: "fallback"`，兼容原有四种关键词动作和四秒冷却。动作意图是可选增强，模型没有给出可靠锚点时仍能说话。

## 音频消息

元数据属于当前音频片段的 `audio.text`，与 `turn_id`、`index` 一起进入现有串行播放队列。协议示例：

```json
{
  "type": "audio",
  "turn_id": "client-example",
  "index": 0,
  "text": "我觉得，这个办法可以试试。",
  "emotion": "认真",
  "data": "<base64 audio>",
  "performance": {
    "version": 1,
    "source": "llm",
    "cues": [
      {"intent":"self_reference","gesture":"self","anchor":"我觉得","start_char":0,"end_char":3,"strength":0.4},
      {"intent":"explanation","gesture":"palm","anchor":"这个办法","start_char":4,"end_char":8,"strength":0.35}
    ]
  },
  "word_timings": [
    {"text":"我觉得","start":0.1,"end":0.6},
    {"text":"这个办法可以试试","start":0.8,"end":2.4}
  ]
}
```

上面的时间戳仅用于说明字段格式。`start_char` 包含、`end_char` 不包含；两个偏移量都按 Unicode 码点计算，JavaScript 使用 `Array.from(text)`，不能直接用 UTF-16 字符串索引。锚点必须与该范围里的文本逐字相同。服务端强度限制为 `0.15–0.75`，每句最多两条。

长句按最多 60 个码点的无损音频切片交付，偏移量重新定位到各片段的正文；跨越切片边界的锚点被省略，仍保留 `llm` 来源以避免冒出无关的关键词动作。`performance.version` 或来源不受支持的旧消息回到兼容规则。

应用播放入口把线上 `word_timings` 转换为第五参数中的 `wordTimings`：

```js
avatar.speak(text, audioContext, startedAt, decodedDuration, {
  performance: message.performance,
  wordTimings: message.word_timings,
});
```

这里的 `startedAt` 来自实际 `source.start()` 使用的音频时钟，时长来自实际解码结果。播放队列持有未播放的计划；配置尚未完成时，Avatar 的待播放项同样保留这些参数。用户打断后，当前动作、后续动作和待播放项清除；过期轮次消息及迟到的原生解码结果不能恢复旧动作。已有“继续刚才语音”功能只续播声音，保持自然，不重放动作意图。

## 对齐与实际能力

- 完整、顺序一致的 `word_timings` 可以使用供应商时间戳。每项 `{text,start,end}` 的时间单位为秒，相对于当前音频片段；多字单词内部仍按字符均分，不代表音素级测量。
- Edge TTS 的 `WordBoundary` 会缓存为上述字段。当前 StepAudio HTTP TTS 只返回音频，没有词时间戳，因此动作使用实际音频时长、字符和标点权重估算位置。
- 不完整、乱序、文本不匹配或超出音频范围的时间戳整段退回估算，避免把错误数据当成精确对齐。
- `PerformanceDirector.sample()` 明确返回 `alignment: "provider"` 或 `"estimated"`，并保留 `intent`、`anchor`、`strength`、`source`。它使用 `AudioContext.currentTime`；挂起音频时钟不会因浏览器渲染继续而推进手势。

手势在锚点前最多 250 毫秒开始准备，随后缓入、短暂停留并收回；相邻动作避免同时拉动一条手臂。通用姿势按强度和“减少动态效果”缩放骨骼角度；日常角色使用[校准后的两档姿势](GESTURE_CALIBRATION.md)，强度控制伸展与摆幅，避免普通语音强度把屈肘和转掌缩没。语义接入没有新增手指动画、肩肘权重、衣服蒙皮或头发物理，也不证明真实声音里的每个词已经精确对齐。

动作切换与取消另有[根据当前姿态衔接的过渡层](POSE_TRANSITIONS.md)：保留上述语音时间表，渲染器从实际手臂朝向插值到当前目标。音频挂起时语义动作进度不推进；已取消动作的身体回收仍可按渲染帧时间完成。

日常角色的[分步抬手与收回路线](GESTURE_PATHS.md)将同一动作包络映射为准备和表达两段姿态；四种目标姿势与本协议的台词时间安排保持原值。

## 本地检查

实验室：`http://127.0.0.1:8000/static/avatar_lab.html?model=daily`，使用动作意图示例的静音时间轴预览，并显示当前锚点及“估算对齐”。演示会从眼部近景切到半身视角，便于看到手臂。实验室预设不是实时 LLM 生成或 TTS。

前端刷新即可加载；已运行且未启用 `--reload` 的后端需要重启，普通聊天才会开始发送新的动作计划。使用 `scripts/run.py` 启动。独立 8001 端口的启动检查已验证最新完整 FastAPI 应用与真实 WebSocket 边界，使用临时数据库、受控回复和生成的 WAV，检查后自动关闭；报告为 `eval/avatar/speech-performance/service-startup.json`。

```powershell
node web/avatar/test_performance.js
.venv\Scripts\python.exe scripts/test_speech_performance.py
node scripts/test-speech-performance-browser.mjs
```

浏览器回归使用真实主页面、真实 `PlaybackQueue`、Avatar、日常造型 VRM 和原生 `AudioContext`；WebSocket 受控，音频是脚本生成的 16 kHz WAV 音调，不调用 LLM/TTS、麦克风或数据写入。已验证 9 组检查，覆盖文字/完成事件不提前动、解码等待、实际播放转交、带 emoji 的码点范围、两个独立锚点及强度、显式自然姿态、估算时间、打断及过期轮次，以及实验室示例的两个锚点/自然姿态/打断交互。

另做了一次收紧提示词后的真实回复模型格式检查：模型为“我觉得”声明了 `self_reference`，服务端保留正确锚点并移除了内部标签。结果在 `eval/avatar/speech-performance/live-format.json`；该检查隔离记忆写入、跳过真实 TTS，只证明这一条回复的格式与解析链路，不是模型遵守率或真实语音对齐验收。

证据：`eval/avatar/speech-performance/results.json` 与同目录姿态截图。截图捕获时临时挂起真实音频时钟，避免软件 WebGL 截图耗时超过短动作。测试能证明运行时和消息边界；真实台词语义选择、扬声器听感、设备性能以及袖口穿插仍需要相应场景验证。
