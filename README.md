# 阿栖（小溪）· 有性格、有记忆、会管事的语音聊天角色

做一个能在长期互动里保持角色一致、理解共同经历、并以合适方式参与对话的角色系统。
第一版主场景是我个人使用的中文语音聊天角色：每天聊几次，记得重要经历，性格稳定。
不做直播、游戏控制、多租户。

**V3 起她升维成"贴身总管"**：不亲自干重活，而是精通我的计划与偏好、熟悉麾下 Worker 的
长短处、掌握全链路 API 观测（Token / TTFT / 鬼打墙熔断），并替我把控第一道初审关。

```
配置（角色/记忆/节奏/花名册契约）   config/*.yaml
      ↓  server/persona.py            契约 -> 系统提示词
      ↓  server/dialogue/orchestrator.py   对话编排 + 台前快环意图
      ↓  server/memory/               四类记忆 + 三档确定性 + 真删除
      ↓  server/assistant/            V3 内阁总管
           dispatcher 派单选将 · telemetry 观测网关 · evaluator 自进化初审
           adapters   Worker 可插拔适配层（cli_subproc / http_api / mcp / manual）
      ↓  server/speech/               ASR / TTS（按情绪切音色）
      ↓  server/app.py + web/         FastAPI + WebSocket + 网页端
```

## 快速开始

```powershell
uv venv
uv pip install -r requirements.txt
Copy-Item .env.example .env             # 填 LLM_API_KEY
python scripts/run.py --check
# 打开 http://127.0.0.1:8000
```

没有 API key 也能启动：自动进入离线 mock 模式，全链路（记忆 / 语音 / 打断）都能跑，
只是回复内容是占位文本。

## 契约文件

| 文件 | 回答什么问题 |
| --- | --- |
| `config/persona.yaml` | 它是谁、性格怎么影响说话和选择、情绪怎么映射到音色 |
| `config/memory.yaml` | 什么值得记、何时存、何时用、冲突怎么办、不确定怎么办、用户怎么改 |
| `config/pacing.yaml` | 回多长、什么时候追问、什么时候主动、什么时候必须闭嘴 |
| `config/agents_roster.yaml` | 麾下有哪些 Worker、各绑哪种适配器、初审种子铁律 |

`persona.yaml` 把性格拆成身份 / 稳定倾向 / 互动方式 / 可变状态四层，每条都带正例、反例、
边界，并写死一条优先级：**性格与事实准确性冲突时，准确性优先**。

记忆分四类（`fact` / `episode` / `todo` / `self_state`），注入提示词时按三档确定性分块：

```
【你明确说过的】  → 可以直接当事实用
【我的推测】      → 留余地，"你是喜欢 X 对吧？"
【我不确定的】    → 先求证再使用
```

检索不到 ≠ 没发生过；检索到 ≠ 仍然准确，角色被允许说"这个我没什么印象了"。
**删除是真删除**：SQLite 行被删掉，检索索引从表实时重建，没有"删了但索引里还在"的残留。

## V3 内阁总管

小溪不亲自干活。她照花名册选将、派单、盯调用、把第一道关：

| 层 | 位置 | 干什么 |
| --- | --- | --- |
| 台前快环 | `server/dialogue/orchestrator.py` | 认委托/进度/战况/验收四类意图，1.5s 内松弛接单，绝不阻塞语音流 |
| 幕后慢环 | `server/assistant/dispatcher.py` | 结合记忆组派单密件，按花名册选将，推进督办阶段机 |
| 观测网关 | `server/assistant/telemetry.py` | 记 span、算战况、熔断鬼打墙；本地透明代理 `/internal/telemetry_proxy` |
| 自进化初审 | `server/assistant/evaluator.py` | L1 客观校验 + L2 审美判例，打回上限 2 次；老板挑刺退回会长出新判例 |
| 适配层 | `server/assistant/adapters/` | 统一三方法契约 + 注册表，新增 Agent 类型零改动调度核心 |
| 督办台账 | `server/interaction/threads.py` | Commission 6 阶段状态机与事件留痕 |

督办六阶段：`DRAFTING → BRIEFING → RUNNING → BLOCKED → AUDITING → READY_TO_REPORT → DONE`。
琐事她代答，大事起气泡请示；初审放行才值得叫你，被打回会带毒舌批注。
工作台抽屉里收着《督办与战况》视图（主舞台 0 新增按钮），心流防打扰：你在高频打字时，
新完结的任务只亮胶囊呼吸灯，不出声、不抢焦点。

**新增一种干活 Agent 只需两步**：在 `server/assistant/adapters/` 下写一个
`BaseWorkerAdapter` 子类并 `@register_adapter("类型名")`，再在花名册加一行
`adapter: "类型名"`。`dispatcher.py` 零改动——这条有测试背书
（`scripts/test_agent_adapter_ext.py` 用 Mock 适配器把全程走成事实）。
契约详见 [总设计](docs/EXECUTIVE_SECRETARY_V3.md) 与 [PROTOCOL.md](docs/PROTOCOL.md) §19–§22。

## 语音

voice 有两层：**文本层**把说话风格写进提示词（短句、口语、允许只回一两个字、禁止客服词）；
**声学层**先判定情绪（调侃 / 得意 / 认真 / 收敛 / 轻微不服 / 变小气），再切到
`persona.yaml` 里对应的 `voice / rate / pitch / volume`。收敛时语速慢、音调低、音量小，
这就是"性格"能被听见的地方。

默认 TTS 用 edge-tts（不需要显卡，中文音色自然，带情感参数）。想换本地模型，
只改 `server/speech/tts.py` 的 `synthesize()`，上层只关心 `(text, emotion) -> mp3`：

```python
# 换成 CosyVoice2 / IndexTTS2 / GPT-SoVITS
async def synthesize(self, text: str, emotion: str = "base") -> bytes:
    prompt = EMOTION_PROMPTS[emotion]      # "用带点嘲讽、懒洋洋的语气说"
    return await cosy_voice.infer(text, spk_id, prompt)
```

连续对话本地检测开口与停顿，确认开口才上传，停顿约 900ms 自动收句；新轮次取消旧回复。
全双工实验版见 [FULL_DUPLEX.md](docs/FULL_DUPLEX.md)——**不能可靠理解附和与打断语义，
也不是专业 DSP 回声消除**，建议戴耳机。

## 立绘（可选层）

`web/avatar/` 是纯前端的 SVG 角色装配（`idle / lip / emotion / gesture` 四层），
口型按中文拼音切成音素时间轴再用实时 RMS 调幅，所以口型和声音是对齐的。
接线点三处：`GET /api/avatar` 提供配置、`app.js` 里 `boot()` 启动、把每条 `audio` 事件的
`text` 喂给 `avatar.speak(...)`。加载失败只移除立绘，不影响聊天。

形象后端同时支持 **VRM + MToon（3 渲 2 实验版）**，共用 `Param*` 驱动。
实验室 `http://127.0.0.1:8000/static/avatar_lab.html`；聊天页启用 `?avatar=vrm`。
依赖已固定版本放在 `web/vendor/`，运行时不依赖 CDN；需 WebGL2，VRM 标准表情只能近似
挑眉、圆唇等行为，不支持的形状会降级。

## 验收

```powershell
python scripts/check_protocol.py            # 协议契约静态校验（字段/路由/引用漂移）
python scripts/test_core.py                 # 38 项离线确定性检查（不需要网络/API key）
python scripts/test_executive_v3.py         # V3 督办/观测/初审/台前意图
python scripts/test_agent_adapter_ext.py    # V3 Worker 适配器可插拔
python scripts/test_config_hot.py           # 配置热重载（协议 §17）
node web/executive-state.test.mjs           # V3 心流防打扰纯逻辑
npm test                                    # Node 单测全集（无浏览器/无模型依赖）
```

CI 在 push/PR 时跑 `check_protocol.py` + 全部 `scripts/test_*.py` + `npm test`。
浏览器回归与 `eval_*.py --live` 需要本地服务、Playwright 和真实模型凭据，按
[TESTING.md](docs/TESTING.md) 在本地执行。`scripts/inspect_extract.py` 是排查记忆问题的
入口：把抽取拆成模型原文 → 规则解析 → 实际入库 → 事后召回四步，并区分三种"没抽到"。

## 环境变量

见 `.env.example`。要点：

- `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`：任何 OpenAI 兼容接口
  （DeepSeek / Moonshot / Qwen / OpenAI，或本地 Ollama `http://127.0.0.1:11434/v1`）。
- `ASR_MODEL`：`tiny / base / small / medium`。CPU 上 `small` 是速度和准确率的平衡点。
- `HF_ENDPOINT`：huggingface.co 不通时指向 hf-mirror.com。
- `TTS_ENGINE=edge_tts|silent`。

## 当前配置与实测

```
对话  step-3.7-flash      流式，情绪由模型自报 [情绪] 标签
抽取  step-3.7-flash      后台异步，不在响应关键路径
TTS   stepaudio-2.5-tts   instruction = 自然语言情绪指令
ASR   stepaudio-2.5-asr  0.9s 转写 3.4s 音频
```

实测：场景回归 9/9，离线自检 38/38，短回复首音 ~3.5s、长回复首音 5.8s（全文 16s）。

**为什么不用 stepaudio-2.5-chat 做对话**：它语气更活，但实测会无视
「你没有任何相关记忆」的显式提示，去编造共同经历。flash 在同样的探针下 0/3 编造。
性格改由 TTS 的 `instruction` 承载，音色固定、情绪变。

两条不能省的提示词约束：**检索为空时必须明说**（留空等于告诉模型"这里没约束"，
它会自己编一段填上）；**few-shot 必须每轮都带且示例本身带 `[情绪]` 标签**
（只在指令里要求而不演示，模型会照示例的格式学，从而忽略指令）。

## 已知限制

- 对话接口本身有 ~2s 首字延迟地板（空提示词也要 1.95s），提示词只占其中约 1s
- 情绪命中率约 7/10，「收敛」有过用倾向——模型自己的判断，不是规则保证
- 长回复的 TTS 按句串行合成，整段播完仍比文本长
- V3 三个内置 Worker 仍是 `adapter: manual`，只组密件不执行；花名册里已留
  `cli_subproc` / `http_api` 接线示例，改成真命令即可跑，无需改 Python

## 其余目录

```
server/interaction/  threads(共同事项+督办台账) / preferences / participation / proposals
server/config_hot.py  配置无痕热重载（协议 §17）
docs/                 PROTOCOL.md 接缝契约 · AGENT_PLAYBOOK.md 协作宪章 · TESTING.md · TASKS.md
scripts/              run.py / check_protocol.py / test_*.py / eval_*.py / inspect_extract.py
eval/                 验收场景与输出（gitignore）
```

