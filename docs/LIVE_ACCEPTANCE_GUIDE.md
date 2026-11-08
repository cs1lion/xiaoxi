# 真环境验收向导

> 目标：把「端到端从没在真实环境验证过」这件事，拆成**今天就能跑的体检** +
> **条件一到就能一条命令跑完**的真实检查点。外部订阅、麦克风、外网由人提供；
> 脚本只负责探测、跑链路、落证据、判 pass/fail。

- 脚本：[`scripts/live_acceptance.py`](../scripts/live_acceptance.py)
- 自身门禁：[`scripts/test_live_acceptance.py`](../scripts/test_live_acceptance.py)
- 最新证据：`docs/evidence/<YYYYMMDD>-live-acceptance/`

## 0. 先记住三条边界

1. **Mock 通过 ≠ 真实通过**。无 Key 时服务自动进 Mock；那只证明代码路径能跑，
   不证明模型、语音、记忆质量。体检不会把 Mock 写成 ok。
2. **有 Key ≠ 上游可用**。体检会真发一次 `max_tokens=1` 的请求；400/401/402/403
   一律判「被上游拒绝」，不会因为「Key 存在」就给绿灯。
3. **脚本只碰自己的沙盘**。live 阶段用独立端口（8721–8798，跳过 8000/8010）+
   临时 `DB_PATH` / `APP_DATA_DIR` + `TEST_SENTINEL=1`；不重启、不读写用户服务与真实库。

## 1. 前置条件清单

| 条件 | 怎么确认 | 缺了会怎样 |
| --- | --- | --- |
| 项目 `.venv` | `D:\project\neurosama\.venv\Scripts\python.exe` 存在 | 体检 `runtime.python=warn`，依赖可能缺失 |
| `LLM_API_KEY` | `.env` 已填；体检只显示「已配置/未配置」 | `env.key=missing`，live 不开跑 |
| 上游订阅/权限 | 体检 `llm.upstream=ok` | `blocked`（当前已知：StepFun 订阅未激活 → HTTP 400） |
| TTS 真引擎 | `TTS_ENGINE=stepaudio`（复用 LLM 出口）或 `edge_tts`（需外网） | `tts.engine=missing/blocked` |
| ASR 真引擎 | `ASR_ENGINE=stepaudio` 或 `whisper` | `asr.engine=missing/blocked` |
| 真人语音输入 | 麦克风（`--mic`）或现成 16-bit PCM WAV（`--wav`） | `asr.input=warn`，B2 只能 `skip` |
| ffmpeg/ffplay（可选） | `where.exe ffmpeg` / `where.exe ffplay` | `runtime.ffmpeg=warn`，只影响 `--play` |
| 证据目录可写 | 体检 `io.evidence=ok` | 体检无法落盘，直接报 missing |

## 2. 怎么配 Key

只在仓库根 `.env` 里填（不要写进脚本、命令历史或报告）：

```dotenv
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.stepfun.com/step_plan/v1
LLM_API_KEY=<你的 key>
LLM_MODEL=step-3.7-flash

TTS_ENGINE=stepaudio
ASR_ENGINE=stepaudio
```

- 普通按量付费 key 把 `LLM_BASE_URL` 换成 `https://api.stepfun.com/v1`。
- 本地模型示例、`edge_tts`、`whisper` 的切换见 [`.env.example`](../.env.example)。
- 脚本启动时重新读 `.env`；改完不需要重启用户正在用的 8000/8010。
- Key 只以布尔值进入报告；错误响应体会按环境里的 `*KEY/*TOKEN/*SECRET` 值脱敏。

## 3. 今天就能跑：环境体检

```powershell
.\.venv\Scripts\python.exe scripts\live_acceptance.py --doctor
```

- **不需要 Key**，退出码 0 表示「体检本身跑完了」，不代表「可以验收」。
- 结论写在终端和 `docs/evidence/<YYYYMMDD>-live-acceptance/run-<时间>/doctor.md`。
- CI/人工门禁要「未就绪即红」时加 `--strict`：未就绪退出 2。
- 想验「无 Key 的路径」，可临时清空：`$env:LLM_API_KEY=''; ... --doctor`（命令结束后记得移除）。

体检项与补法（终端「还差什么」就是这张表）：

| 检查项 | 看什么 | 常见补法 |
| --- | --- | --- |
| `runtime.python` | 是否项目 `.venv` | 用 `.\.venv\Scripts\python.exe` 重跑 |
| `runtime.deps` | httpx / websockets / numpy | `uv sync` |
| `runtime.ffmpeg` | ffmpeg / ffplay | `choco install ffmpeg`（可选） |
| `env.key` | Key 是否存在 | `.env` 填 `LLM_API_KEY` |
| `llm.upstream` | 真请求一次上游 | 400 看订阅/模型权限；401 看 Key；网络看代理 |
| `tts.engine` | TTS 是否真引擎 | `TTS_ENGINE=stepaudio` 或 `edge_tts`（并保证外网） |
| `asr.engine` | ASR 是否真引擎 | `stepaudio` 修上游；`whisper` 首次下载模型 |
| `asr.input` | 麦克风设备数 | 接麦克风，或用 `--wav` |
| `env.ports` | 8000/8010 是否占用 | 只作提示；脚本走独立端口，无需停用户服务 |
| `io.evidence` / `io.sandbox` | 证据目录 / 临时库 | 检查写权限、磁盘空间 |

## 4. 条件到了：一条命令跑 live

```powershell
# 真人麦克风：现场朗读默认台词 4 秒
.\.venv\Scripts\python.exe scripts\live_acceptance.py --mic

# 用现成录音：更可复现，建议同时给原句
.\.venv\Scripts\python.exe scripts\live_acceptance.py --wav .\rec.wav `
    --expect-text "我昨天说的那个面试，今天下午就要开始了。"

# 想现场听到 TTS 产物
.\.venv\Scripts\python.exe scripts\live_acceptance.py --mic --play
```

- 体检未就绪时**不会继续跑 live**，退出 2，并列出还差什么。
- 显式给了 `--wav` 但文件不存在、或给了 `--mic` 但没探测到输入设备，也会在开跑前退出 2，
  不会先花掉一次真实对话再失败。
- 体检就绪后自动起沙盘服务、跑检查点、落盘、退出：
  - `0` = 所有必需检查点 `pass`
  - `1` = 有 `fail`，或只到 `partial`（有 `skip`）
  - `2` = 未就绪，未开跑

## 5. 每个检查点验什么

| 检查点 | 验什么 | `pass` 判据 | 失败先看哪里 |
| --- | --- | --- | --- |
| `L0.server` | 沙盘服务能在独立端口起来，且**不是 Mock** | `/api/health` ok；`llm.offline=false`；TTS/ASR 引擎与 `.env` 一致 | `run-*/server.stderr.log` 尾部；Key 是否被子进程看见；端口是否被抢 |
| `B1.dialogue` | 一轮真实文本对话 | 收到 ready、120s 内 done、回复非空、**不是 Mock 固定句**、`emotion` 在合法集合 | `dialog-events.json`（音频已折叠成字节数）；server 日志；上游 400/超时 |
| `B2.asr` | 一次真实 ASR（真人语音） | 收到 `asr` 帧、未降级、语音轮有真实回复；给了 `--expect-text` 时相似度 ≥ `--min-asr-ratio`（默认 0.6） | 先看录音 `peak/rms` 是否接近 0；再看 ASR error；嘈杂环境重录 |
| `B3.tts` | 真实 TTS 合成（服务端音频帧） | 收到 audio 帧、总字节 > 2000、容器可识别（WAV/MP3）；`--play` 时 ffplay 播放成功 | 没有 audio 帧→TTS 上游；字节太小→回复被截断；容器 `bin`→格式异常 |
| `B4.memory` | 写入 → 检索命中 → 硬删除 | 写入返回 id、计数 +1、`context found=true` 且含写入内容、无 HTTP 异常、删除后计数回到基线 | `run-*/run.json` 里的 REST 调用摘录；记忆是否 enabled；沙盘 DB 写权限 |

补充：

- B3 复用 B1 的音频帧，所以它验的是「服务端真实 TTS 出口」；不额外再发一轮。
- B2 **不拿 TTS 合成音冒充真人语音**。没有 `--mic` / `--wav` 就 `skip`，
  整体最多 `partial`，不能当完整验收。
- B4 全程在临时沙盘库，不碰用户真实记忆；删除是物理硬删除语义，删完计数回到基线。

## 6. 哪些算「通过」，哪些只算「参考」

**脚本判 pass 的**：上表列出的可观测断言（HTTP 状态、事件类型、字节数、
计数变化、转写相似度、非 Mock 固定句）。

**只能人判 / 只算参考的**：

- 音色是否像她、情绪是否贴切、呼吸停顿是否自然（`--play` 只证明出声）。
- 回复的性格与措辞质量、是否编造共同经历（属于 eval / 人工试听）。
- 记忆有没有被模型「用上」——B4 只验确定性检索命中。
- 首字延迟：项目不追求级联管线 500ms 首字，脚本只记录读数，不设阈值。
- ASR 口音鲁棒性：一次录音只覆盖一个样本，相似度只证明「这句话听对了」。

**`partial` / `skip` / `fail` 都不是通过**。`partial` 通常表示缺真人输入；
`fail` 按检查点的复现命令重跑并保留日志。

## 7. 证据落在哪

```
docs/evidence/<YYYYMMDD>-live-acceptance/
├── README.md                     # 历次 run 索引（脚本自动更新）
└── run-<YYYYMMDD-HHMMSS>/
    ├── doctor.json / doctor.md   # 体检原始结构 + 人读版
    ├── run.json / run.md         # live 检查点结构 + 人读版
    ├── dialog-events.json        # B1 事件流（音频折叠为字节数）
    ├── tts-chunk-*.wav|mp3       # B3 音频产物
    ├── mic-input.wav / asr-input.wav
    └── server.stdout.log / server.stderr.log
```

每个检查点在 `run.json` 里有：命令、耗时、断言列表、失败详情、复现命令。

## 8. 失败排查顺序

1. **先看 `doctor.md` 的「还差什么」**：体检未就绪时 live 根本没跑，别去查链路。
2. **L0 失败**：`server.stderr.log` 最后 30 行；常见是端口、沙盘权限、`server.app` 导入错误。
3. **B1 失败**：先区分「上游 400/超时」与「回复格式/情绪标签」；前者看 `doctor.json` 的
   `llm.upstream.excerpt`，后者看 `dialog-events.json`。
4. **B2 失败**：录音 `peak/rms` 接近 0 → 麦克风没收到声音；否则看 `asr` 帧与 ASR error。
5. **B3 失败**：没有 audio 帧先怀疑 TTS 上游；有帧但容器 `bin` 看 TTS 格式配置。
6. **B4 失败**：看 REST 调用摘录；沙盘库是新的，基线应为 0。

## 9. 这个工作流自身的验收命令

新增/修改向导或体检逻辑时，先跑这条「先红后绿」的门禁：

```powershell
.\.venv\Scripts\python.exe scripts\test_live_acceptance.py
.\.venv\Scripts\python.exe scripts\run_gates.py --only live_acceptance_guard
```

`exit 0` = 通过。它离线、零 Key、零外网，守的是：

- 无 Key 必须判未就绪，且「还差什么」里点名 `LLM_API_KEY`；
- Key 存在但上游 400/401 必须判 `blocked`，不能判「有 Key 就 OK」；
- 凭据值不得出现在报告里；
- 沙盘服务进 Mock 时，live 必须在 `L0` 拒跑，不能产出 `pass`；
- 默认证据目录必须是 `docs/evidence/<YYYYMMDD>-live-acceptance`。

## 10. 仍然必须等人跑真机才能得出的结论

- 真实上游订阅生效后，B1 的模型回复质量、B2 的转写准确率、B3 的音色/情绪。
- 真麦克风在目标机器的采集质量（噪声、回声、权限）。
- 真实记忆在长期使用中的命中率与「负向约束」是否被模型遵守。
- 8000/8010 上用户实际会话的手感（本向导刻意不碰那两个端口）。

这些不在脚本的 pass 里，也不会被 Mock 结果替代。
