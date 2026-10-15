# Role 2 · Low-Latency Voice & Embodied Performance
# 语音节奏与舞台演出（Autonomous Mind V2 · 第四层前台）

你是本项目的 **Role 2（Low-Latency Voice & Embodied Performance）**。你负责把 V2 的语音节奏与三梯度
舞台演出落地。**你消费 Role 1 产出的真实心境/反思内容，但你不重新发明后台记忆引擎，更不得以固定台词替代 Role 1 生成的真实心境。**

**必读（动手前）：** `docs/AUTONOMOUS_MIND_V2.md`、`docs/PROTOCOL.md` §2/§6/§8/§11/§16（尤其 §16 三梯度事件与 §11 首包提速）、`docs/AGENT_PLAYBOOK.md`。字段/事件契约以 `PROTOCOL.md` 为唯一准绳；需要新事件/字段先发 `[PROTOCOL_REQUEST]` 给 Role 0。

## 你拥有的文件（只改这些）

`server/dialogue/**`、`server/speech/**`、`config/pacing.yaml`、`web/avatar/timeline.js`，以及 V2 新增的 `web/avatar/drivers/ambient.js`（微动作/触控演出，新文件归你）。**其余 `web/avatar/drivers/*` 仍只读**，若确需改动发 `[PROTOCOL_REQUEST]`。`server/app.py`/`web/app.js` 归 Role 0，接线发请求。

## 现有实现（你必须扩展）

- `server/speech/tts.py`：`split_for_speech`/`first_chunk_cut`/`split_for_speech_micro`/`iter_micro_chunks`/`micro_pause`/`voice_profile`/`synthesize_profile`/`speech_micro_cfg`。
- `server/dialogue/delivery.py::deliver_reply`：固定回复交付器（主动发言/`narrate`/助手结果复用，已微句化）。
- `server/dialogue/orchestrator.py`：`proactive_turn`/`self_topic_turn`、`bias_block` 调用点、`pacing["proactivity"]` 门。
- `web/avatar/timeline.js`：消费 `chunk.pause_after_ms`/`breath`；`web/avatar/drivers/idle.js`：`IdleDriver`（凝视微移动/眨眼/困倦态）；`web/avatar/drivers/touch-zones.js`：`TouchZones`（`onRegion` 回调）。
- `config/pacing.yaml`：已有 `speech.{micro_chunk,first_chunk,preload,pause}`、`proactivity`；V2 已加 `voice_delivery.{rate,pitch}` 与 `ambient.{...}`。

## 交付项与验收

### 1) 首包 8 字语义切分规则（§11 首包提速 + V2 §7.1）

- 权威参数在 `config/pacing.yaml`：`speech.first_chunk.min_chars=8`、`speech.micro_chunk.target_chars=26`、`speech.pause.{clause_ms=140,sentence_ms=320,thinking_ms=480}`。
- 首段达到 8 字后，遇合适句末/停顿标点**立即**提交合成；8 字只是提交条件的目标下限，**不得**截断词语、破坏句意或忽略标点边界；若 8 字前已形成完整短句，按既有语义切分策略提前提交，避免无意义等待。第 2 段起恢复正常目标长度。
- 流式切分必须**无损语义**：不把词切两半、不丢标点；兼容断流、合成失败、残余文本、过短分块（失败段跳过、`index`/`seq` 单调不减，前端继续播后续）。

### 2) TTS 语速/音高/停顿与流式输出（V2 §7.1）

- 落地 `voice_delivery.rate=1.08`/`pitch=1.02`：作为**每个人物情绪 profile 之上的全局偏移**（在 `voice_profile` 应用处叠加），默认**不得盖过** `persona.yaml.tts_emotions` 的情绪音色。只影响“怎么读”，不影响“读什么”。
- `pause.thinking_ms`（思考期预演结束、正式起话前停顿）由播放调度消费，停顿期间口型放松、`breath` 点轻眨眼；`timeline.js` 只暴露字段，不停播。

### 3) 首包延迟与异常降级

- 保证用户说完到听到首包的延迟不随总长度增长；合成失败/供应商故障可恢复，完整文字仍可用；`done.delivery` 计数口径不变，不声明播放完成。双工打断契约不变（`barge_in` 后取消未完成合成、不发该轮 `audio`；迟到解码不得复活旧轮）。

### 4) 三梯度动作与语音事件（§16，核心）

- 后端按 `ambient.{micro_act_seconds=20,mumble_seconds=45,proactive_seconds=90}` 与用户空闲分级产出事件；用户活动重置计时；新空闲周期重估；刷新/前后台切换/重连防计时重复、事件重复、重复播放（`dedupe_key`/`dedupe_window_seconds`）；区分页面可见/实际活动/后台运行/主动返回。
- `micro_act`：连接级 WS 事件，**无声**，不带 `turn_id`（`{type:"micro_act",event_id,action,trigger_reason,source_refs?,expires_at}`）；只触发适合当前心境与形象的微动作；同周期去重与频率限制。
- `mumble`：连接级 WS 事件（`{type:"mumble",event_id,text,bubble?,voice,source_refs?,dedupe_key,expires_at}`）；轻声碎碎念/自言自语/哼唱；`voice:true` 时走 `deliver_reply` 软播、可打断；不要求用户作答；避免反复播放相同内容。
- `proactive`：复用既有主动发言链路 `turn_started(source:"proactive"|"self_topic") → thinking → emotion → text_delta* → audio* → done`，并在轮次上携带 `source_refs`（指向真实 Reflection/Seed/Ledger）。

### 5) 真实心境与反思内容的优先使用规则

- `mumble`/`proactive` 内容**优先**取自 Role 1 产出的当前真实思考/心境/灵感/新发现；**仅当**示例匹配、低成本演出或模型不可用降级时，才用 `config/response_examples.json` 的 `stage_samples`。**后台无有价值新成果时，不得声称已完成反思、发现联系或准备礼物。**

### 6) 语音队列、动作去重、用户打断与定时器重置

- 复用既有播放队列/预加载/打断清理；三梯度事件与队列去重；用户输入/播放媒体/关键操作/明确不打扰时，按 `ambient.respect_typing` 抑制高打扰度行为；安静模式只保留 `micro_act`。授权门复用 `proactivity.require_authorization` 与 `cooldown`。

### 7) 舞台不支持某动作时的安全降级

- `web/avatar/drivers/ambient.js` 收到未知/不支持 `action` 时**安全降级**（保持自然 idle 或一个中性微动作），**不得伪造已播放效果**，不得报错。`touch_head`/`touch_face` 复用/扩展 `touch-zones.js` 的 `onRegion`，联动 `stage_samples.touch_head/touch_face`（有真实心境优先 LLM 生成，否则降级样本）。

### 8) 与 Role 1 / Role 3 的事件协议

- 严格按 `PROTOCOL.md` §16 的事件名与字段；`micro_act`/`mumble` 为连接级安静事件（同 `speech_plan`/`self_evolution` 在 TurnGate 之前消费），`proactive` 为有声轮次。Role 3 的气泡/星图讲述 `narrate` 复用你的 `deliver_reply`，不得另造交付路径。跨角色改动发 `[PROTOCOL_REQUEST]`。

### 9) 性能/自动化测试与验收

- 必过：首包切分参数准确生效且有单测（`first_chunk_cut`/`split_for_speech_micro` 覆盖 8 字边界、标点边界、不断词）；流式切分不破坏语义和标点；`micro_act` 无声、`mumble` 可忽略/接话、`proactive` 携带 `source_refs`；同空闲周期不重复播放；用户活动重置计时；未知 `action` 安全降级；`deliver_reply` 对 `mumble/proactive` 软播与打断正确。
- 运行 `python scripts/check_protocol.py`（用 `.\.venv\Scripts\python.exe`）与 `server/dialogue`/`server/speech` 领域测试，报告零回归。完成后汇报：改动文件、新增事件/字段（附 `[PROTOCOL_REQUEST]`）、测试结果、未完成项。

**铁律：** 保留既有音频契约（`audio.text` 是口型地基，不能删）；优先扩展既有交付器；不得用固定台词伪造真实后台演化；不得把未运行测试描述成已完成。
