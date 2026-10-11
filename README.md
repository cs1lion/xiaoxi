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

打断（barge-in）：网页端播放时监测麦克风能量，你一开口立刻停播并开始新一轮。
也可常驻监听模式。

## 立绘（可选层）

`web/avatar/` 是一套纯前端的 SVG 角色装配：`idle / lip / emotion / gesture` 四层，
配置在 `config/avatar.yaml`。口型不是随机动画——它按中文拼音切成音素时间轴
（`scripts/gen_pinyin_table.py` 生成），再用播放路上的分析器实时 RMS 调幅。

接线点就三处：`GET /api/avatar` 提供配置，`app.js` 里 `boot()` 启动，
再把每条 `audio` 事件里的 `text` 喂给 `avatar.speak(text, ctx, startedAt, duration)`
——服务端已经按句切分并在事件里带上对应文本，所以口型和声音是对齐的。
加载失败只会移除立绘，不影响聊天。

想换 Live2D / VRM 只需替换 `web/avatar/render/`，上层仍然只写 `Param*`。

## 验收

```powershell
python scripts/test_core.py         # 34 项离线确定性检查（不需要网络/API key）
python scripts/eval_scenarios.py    # 9 个场景回归，输出 eval/last_run.json
python scripts/e2e_check.py         # 起服务后跑全链路：文字 + 语音 + 记忆 + 删除
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

## 目录

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
scripts/      run.py / test_core.py / eval_scenarios.py / e2e_check.py
docs/         REQUIREMENTS.md（需求草案）、system_prompt_sample.txt（提示词样例）
```