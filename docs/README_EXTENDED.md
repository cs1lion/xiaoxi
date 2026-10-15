# README 外篇 · 施工细节

这里是 `README.md` 放不下的部分：环境变量、契约文件、语音与立绘接线点、完整测试命令、目录地图。
读者只需要 README；动手改代码的人需要这一页。

## 环境变量

见 [`.env.example`](../.env.example)，要点：

- `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL`：任何 OpenAI 兼容接口（DeepSeek / Moonshot / Qwen / OpenAI，或本地 Ollama `http://127.0.0.1:11434/v1`）。
- `LLM_EXTRACT_MODEL`：记忆抽取专用模型，后台异步跑，不在响应关键路径。
- `TTS_ENGINE=stepaudio|edge_tts|silent`，`ASR_ENGINE=stepaudio|whisper`；本地 ASR 时 `ASR_MODEL=tiny/base/small/medium`，CPU 上 `small` 是速度与准确率的平衡点。
- `HF_ENDPOINT`：huggingface.co 不通时指向 hf-mirror.com（本地 ASR 才需要）。
- `HOST` / `PORT`：默认 `127.0.0.1:8000`。

## 契约文件（config/*.yaml）

| 文件 | 回答什么问题 |
| --- | --- |
| `config/persona.yaml` | 它是谁、性格怎么影响说话和选择、情绪怎么映射到音色 |
| `config/memory.yaml` | 什么值得记、何时存、何时用、冲突怎么办、不确定怎么办、用户怎么改 |
| `config/pacing.yaml` | 回多长、什么时候追问、什么时候主动、什么时候必须闭嘴 |
| `config/agents_roster.yaml` | 有哪些 Worker、各绑哪种适配器、初审种子铁律 |
| `config/response_examples.json` | Few-Shot 示例；每轮 Assistant 回复都必须带合法 `[情绪]` 标签 |

`persona.yaml` 把性格拆成身份 / 稳定倾向 / 互动方式 / 可变状态四层，每条都带正例、反例、边界。

记忆注入提示词时按三档确定性分块：

```text
【你明确说过的】  → 可以直接当事实用
【我的推测】      → 留余地，"你是喜欢 X 对吧？"
【我不确定的】    → 先求证再使用
```

检索不到 ≠ 没发生过；检索到 ≠ 仍然准确，角色被允许说"这个我没什么印象了"。
**删除是真删除**：SQLite 行被删掉，检索索引从表实时重建，没有"删了但索引里还在"的残留。

## 语音两层

- 文本层：说话风格写进提示词（短句、口语、允许只回一两个字、禁止客服词）。
- 声学层：先判定情绪，再切 `persona.yaml` 里对应的 `voice / rate / pitch / volume`。

连续对话时本地检测开口与停顿，确认开口才上传，停顿约 900ms 自动收句；新轮次取消旧回复。
全双工实验版见 [FULL_DUPLEX.md](FULL_DUPLEX.md)——**不能可靠理解附和与打断语义，也不是专业 DSP 回声消除**，建议戴耳机。

### 替换本地 TTS 引擎（CosyVoice2 / IndexTTS2 / GPT-SoVITS）

只改 `server/speech/tts.py` 的 `synthesize()`，上层只关心 `(text, emotion) -> bytes`：

```python
async def synthesize(self, text: str, emotion: str = "base") -> bytes:
    prompt = EMOTION_PROMPTS[emotion]      # "用带点嘲讽、懒洋洋的语气说"
    return await cosy_voice.infer(text, spk_id, prompt)
```

内置的 `EMOTION_INSTRUCT` 映射表可把情绪标签转成自然语言控制指令。

## 立绘层（可选）

- SVG 立绘是纯前端装配（`idle / lip / emotion / gesture` 四层），口型按中文拼音切成音素时间轴再用实时 RMS 调幅，所以口型和声音对齐。
- 接线点三处：`GET /api/avatar` 提供配置、`app.js` 里 `boot()` 启动、把每条 `audio` 事件的 `text` 喂给 `avatar.speak(...)`。加载失败只移除立绘，不影响聊天。
- 切 3D 渲染管线：把 `.vrm` 模型放到 `web/models/`，`config/avatar.yaml` 里改渲染器，前端走 Three.js + `@pixiv/three-vrm` 管线，接管 BlendShape 表情、眼球注视点 (Gaze) 与音素口型 (Viseme)。依赖已固定版本放在 `web/vendor/`，运行时不依赖 CDN；需 WebGL2。
- 实验室页面 `http://127.0.0.1:8000/static/avatar_lab.html`，聊天页启用 `?avatar=vrm`。

完整的 RMS 口型驱动、视线标定与协议规范见 [docs/](.) 目录（`AVATAR_CALIBRATION_PLAN.md` / `GESTURE_PATHS.md` / `HAND_POSES.md` 等）。

## 完整测试命令

全局门禁（任何改动后必跑，使用项目虚拟环境 `.\.venv\Scripts\python.exe`）：

```powershell
python scripts/check_protocol.py            # 协议契约静态校验（字段/路由/引用漂移）
python scripts/test_core.py                 # 基线 38 passed / 0 failed
```

V3 编排相关：

```powershell
python scripts/test_executive_v3.py         # 督办台账 / 观测网关 / 自进化初审 / 台前意图
python scripts/test_agent_adapter_ext.py    # Worker 适配器可插拔（Mock 适配器跑通全程）
python scripts/test_agent_loop.py           # 慢环主循环
python scripts/test_config_hot.py           # 配置热重载（协议 §17）
node web/executive-state.test.mjs           # 心流防打扰纯逻辑（零 DOM）
```

CI（`.github/workflows/ci.yaml`）在 push/PR 时跑 `check_protocol.py` + 全部 `scripts/test_*.py` + `npm test`。
浏览器回归与 `eval_*.py --live` 需要本地服务、Playwright 和真实模型凭据，按 [TESTING.md](TESTING.md) 在本地执行。
`scripts/inspect_extract.py` 是排查记忆问题的入口：把抽取拆成模型原文 → 规则解析 → 实际入库 → 事后召回四步，并区分三种"没抽到"。

## 目录地图

```text
server/dialogue/     台前快环：对话编排、流式交付、情绪判定
server/assistant/    幕后慢环：调度、遥测、评估器、Worker 适配器（cli_subproc/http_api/mcp/manual）
server/memory/       四类记忆、三档确定性、混合检索、硬删除
server/interaction/  共同事项与督办台账、偏好、参与度、提案
server/speech/       ASR / TTS / 流式解释
server/config_hot.py 配置无痕热重载（协议 §17）
web/                 工作台前端：语音、立绘、抽屉、图谱
scripts/             run.py / check_protocol.py / test_*.py / eval_*.py / inspect_extract.py
eval/                验收场景与输出（gitignore）
docs/                PROTOCOL.md 接缝契约 · AGENT_PLAYBOOK.md 协作宪章 · TESTING.md · TASKS.md
```
