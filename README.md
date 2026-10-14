# 有性格、有记忆的语音聊天角色

不是"做一个有记忆的 agent"，而是**做一个能在长期互动里保持角色一致、理解共同经历、
并以合适方式参与对话的角色系统**。所以代码也是按这个顺序组织的：
角色契约 → 记忆契约 → 节奏契约 → 语音 → 验收场景。

第一版主场景：**我个人使用的中文语音聊天角色**。每天聊几次，
记得重要经历，性格稳定。不做直播、游戏控制、虚拟形象、多 agent。

```
配置（角色/记忆/节奏契约）  config/*.yaml
      ↓
提示词组装                server/persona.py
      ↓
对话编排（检索记忆 → 生成 → 节奏后处理 → 记忆抽取）
      ↓
server/dialogue/orchestrator.py
      ↓
记忆层（四类记忆 + 三档确定性 + 真删除）  server/memory/
      ↓
语音：ASR(faster-whisper) / TTS(edge-tts，按情绪切音色)  server/speech/
      ↓
FastAPI + WebSocket + 网页端   server/app.py  web/
```

## 助手与主动提醒（新增）

新增有限步数 Agent Loop、参数绑定人工确认、任务与单次提醒、持久化通知收件箱，以及分层上下文。侧栏「任务与提醒」面板可直接管理事项；自然语言执行复用现有 LLM 配置。所有模型提出的写操作先确认，首版提醒只在文字收件箱投递，服务关闭时不运行调度，不承诺网页关闭后的系统通知。

机制、参考来源、测试方式与当前边界见 [Agent Runtime](docs/AGENT_RUNTIME.md)，产品整体提案见 [搭档设计](docs/COMPANION_ASSISTANT_DESIGN.md)。原记忆数据不自动转换成授权或提醒。

## 快速开始

```powershell
uv venv
uv pip install -r requirements.txt      # 或 uv pip install -e . 的依赖列表
Copy-Item .env.example .env             # 填 LLM_API_KEY
python scripts/run.py --check
# 打开 http://127.0.0.1:8000
```

没有 API key 也能启动：会自动进入离线 mock 模式，全链路（记忆 / 语音 / 打断）都能跑，
只是回复内容是占位文本。

## 三份契约文件

| 文件 | 回答什么问题 |
| --- | --- |
| `config/persona.yaml` | 它是谁、性格怎么影响说话和选择、情绪怎么映射到音色 |
| `config/memory.yaml` | 什么值得记、何时存、何时用、冲突怎么办、不确定怎么办、用户怎么改 |
| `config/pacing.yaml` | 回多长、什么时候追问、什么时候主动、什么时候必须闭嘴 |

### 性格是可观察的规则，不是标签

`persona.yaml` 把性格拆成四层：身份 / 稳定倾向 / 互动方式 / 可变状态，
每条性格都带正例、反例、边界。例如"轻微毒舌"：

- 正例："计划写得挺漂亮，执行呢？"
- 反例：持续羞辱用户；拿敏感经历开玩笑
- 边界：用户表示难受时立刻停止，并且不换成另一种挖苦

还写死了一条优先级：**性格与事实准确性冲突时，准确性优先**。
角色可以不用客服语气，但不能为了人设说假话。

### 记忆分四类、三档确定性

`fact`（事实偏好）/ `episode`（共同经历）/ `todo`（未完成事项）/ `self_state`（角色连续性），
每条都带来源标记，注入提示词时分成三块显示：

```
【你明确说过的】  → 可以直接当事实用
【我的推测】      → 留余地，"你是喜欢 X 对吧？"
【我不确定的】    → 先求证再使用
```

检索不到 ≠ 没发生过；检索到 ≠ 仍然准确。角色被允许说"这个我没什么印象了"。

**删除是真删除**：SQLite 行被删掉，检索索引是从表实时重建的，
不存在"删了但索引里还在"的残留情况。

## 语音：性格从哪来

voice 有两层：

1. **文本层** —— 说话风格写在提示词里：短句、口语、允许只回一两个字、禁止客服词。
2. **声学层** —— 每句话先判定情绪（调侃 / 得意 / 认真 / 收敛 / 轻微不服 / 变小气），
   再切到 `persona.yaml` 里对应的 `voice / rate / pitch / volume`。
   收敛时语速慢、音调低、音量小，这就是"性格"能被听见的地方。

默认 TTS 用 edge-tts（不需要显卡，中文音色自然，带情感参数）。
想换本地模型，只改 `server/speech/tts.py` 的 `synthesize()`，上层只关心 `(text, emotion) -> mp3`：

```python
# 换成 CosyVoice2 / IndexTTS2 / GPT-SoVITS
async def synthesize(self, text: str, emotion: str = "base") -> bytes:
    prompt = EMOTION_PROMPTS[emotion]      # "用带点嘲讽、懒洋洋的语气说"
    return await cosy_voice.infer(text, spk_id, prompt)
```

连续对话第一版：开启后本地检测开口与停顿，确认开口才上传（含预录），停顿约900ms自动收句；语音与文字新轮次会取消旧回复，过期转写、事件及解码结果不能复活旧音频。`done`只表示生成结束，播放完毕才结束该轮。

保留按住说话与键盘操作。“停止回复”保留连续模式本地监听；关闭连续对话取消未提交的自动录音，释放麦克风（手动按住时需先松开）。断线、切后台、离开页面也会释放。前端29.5秒、服务端30秒录音上限。

已深入到[全双工实验版](docs/FULL_DUPLEX.md)：AudioWorklet优先双路采集；疑似开口先压低回复音量、持续讲话才取消旧轮；播放参照回声判据；耳机/外放/快速策略；本地句内续播及指标。**不能可靠理解附和与打断语义，也不是专业DSP回声消除**，建议戴耳机；真实设备声学效果仍需验收。连续对话基础用法见[连续语音说明](docs/CONTINUOUS_VOICE.md)。

## 立绘（可选层）

`web/avatar/` 是一套纯前端的 SVG 角色装配：`idle / lip / emotion / gesture` 四层，
配置在 `config/avatar.yaml`。口型不是随机动画——它按中文拼音切成音素时间轴
（`scripts/gen_pinyin_table.py` 生成），再用播放路上的分析器实时 RMS 调幅。

接线点就三处：`GET /api/avatar` 提供配置，`app.js` 里 `boot()` 启动，
再把每条 `audio` 事件里的 `text` 喂给 `avatar.speak(text, ctx, startedAt, duration)`
——服务端已经按句切分并在事件里带上对应文本，所以口型和声音是对齐的。
加载失败只会移除立绘，不影响聊天。

形象后端现支持 SVG 和 **VRM + MToon（3 渲 2 实验版）**，共用 `Param*` 驱动，VRM 通过适配层映射骨骼、视线和表情。

### 3 渲 2 实验版

启动现有 Python 服务后打开：

- `http://127.0.0.1:8000/static/avatar_lab.html`：独立实验室，支持 SVG 对照、情绪/动作、减少动态效果、本地 VRM 导入（不上传）、静音口型与本地音频测试。
- `http://127.0.0.1:8000/?avatar=vrm`：聊天页启用 3 渲 2；也可在侧栏“形象”手动切换。普通首页仍默认 SVG。

依赖已以固定版本放在 `web/vendor/`，运行时不依赖 CDN。更新或重建依赖：

```powershell
npm ci
npm run vendor:avatar
npm run test:vrm
# 浏览器回归需要已有服务运行在 127.0.0.1:8000
npx playwright install chromium
node scripts/test-avatar-browser.mjs
```

- 渲染器：`web/avatar/render/vrm.js`；纯参数映射：`web/avatar/render/vrm-mapping.js`。
- 示例为 pixiv 官方 VRM1 Constraint Twist Sample（约 10.8 MB），不是最终角色；来源、固定 blob 与模型内嵌授权见 `web/models/README.md`。
- 切换采用后台准备、成功后替换；加载失败保留当前形象，首次 VRM 失败时保留 SVG。不影响语音和对话。模型切换不持久化，本地模型只在实验室本次页面有效。
- VRM 标准表情只能近似原本的挑眉、圆唇等行为；不支持的形状会降级。嘴型使用现有拼音时间轴，不代表强制对齐或完整中文音素识别。
- 本版使用 WebGL；需浏览器支持 WebGL2。支持自包含的 VRM 0.x/1.x；外部纹理、压缩扩展、特殊模型尚未全面验证。
- 浏览器测试覆盖加载、情绪、口型、打断、SVG/VRM 切换、失败保留、移动视口和聊天入口；截图输出到 `eval/avatar/`。自动化使用无头 Chromium，不等同于真实设备性能测试或人工美术验收。

## 验收

```powershell
python scripts/test_core.py          # 34 项离线确定性检查（不需要网络/API key）
python scripts/inspect_extract.py    # 单独看"记忆抽取"这一环：原文 -> 解析 -> 入库 -> 召回
python scripts/eval_scenarios.py     # 9 个场景回归，输出 eval/last_run.json
python scripts/e2e_check.py          # 起服务后跑全链路：文字 + 语音 + 记忆 + 删除
```

`inspect_extract.py` 是排查记忆问题的入口。它把抽取拆成四步打出来：
模型原文 → 规则解析 → 实际入库 → 事后召回，并且区分三种"没抽到"：
**模型调用失败** / **模型说没什么好记** / **抽到了但被规则挡下**（会打印挡下的理由）。
以前这三种都表现为"记忆是空的"，没法区分。

```
python scripts/inspect_extract.py --dry      # 不调模型，只验证规则层（离线可用）
python scripts/inspect_extract.py --text "我周五要面试"   # 单条试
python scripts/inspect_extract.py --full     # 打印模型原始 JSON
```

`eval/scenarios.json` 里的场景直接来自需求草案的"第二步"：
第一次聊天、隔天回来、一周后提旧事、偏好改变、被纠正、情绪不好、
被问不存在的经历、拒绝后不追问、删除后不再提。每个场景都写了
**期望行为 / 不希望出现的行为 / 可机器检查的规则**。

没有 API key 时 `eval_scenarios.py` 的语义项必然不过（离线 mock 不理解中文语义），
这是预期行为；接上真模型后它才是有意义的回归。

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

**为什么不用 stepaudio-2.5-chat 做对话**：它语气更活，但实测会无视「你没有任何相关记忆」的显式提示，
去编造共同经历（"你问我有没有女朋友"）。flash 在同样的探针下 0/3 编造。
性格改由 TTS 的 `instruction` 承载，音色固定、情绪变。

### 两条不能省的提示词约束

```python
# 1. 检索为空时必须明说。留空 = 告诉模型"这里没约束"，它会自己编一段填上。
"【关于这个问题，你没有任何相关记忆】不要使用「我记得」「我们之前」…"

# 2. few-shot 必须每轮都带，且示例本身要带 [情绪] 标签。
#    只在指令里要求而不演示，模型会照示例的格式学，从而忽略指令。
```

## 已知限制

- 对话接口本身有 ~2s 首字延迟地板（空提示词也要 1.95s），提示词只占其中约 1s
- 情绪命中率约 7/10，「收敛」有过用倾向——模型自己的判断，不是规则保证
- 长回复的 TTS 按句串行合成，整段播完仍比文本长

```
config/       三份契约（角色 / 记忆 / 节奏）+ avatar.yaml
server/
  persona.py  契约 -> 系统提示词
  llm.py      OpenAI 兼容客户端（含离线 mock）
  memory/     store(SQLite) / retriever(混合检索) / extractor(抽取) / manager(冲突与用户管理)
  dialogue/   编排：节奏后处理、追问抑制、主动发言授权
  speech/     asr.py / tts.py
  app.py      REST + WebSocket + 静态页
web/          网页端（录音、打断、字幕、记忆面板）+ avatar/ 立绘
eval/         验收场景
scripts/      run.py / test_core.py / inspect_extract.py / eval_scenarios.py / e2e_check.py
docs/         REQUIREMENTS.md（需求草案）、system_prompt_sample.txt（提示词样例）
```