# 真环境验收 · 环境体检

- 生成：2026-11-09T02:17:03
- 命令：`scripts/live_acceptance.py --doctor`（耗时 4115ms）
- 根目录：`D:\project\neurosama`
- 解释器：`D:\project\neurosama\.venv\Scripts\python.exe`

## 结论：未就绪（还差条件）

**未开跑 live。Mock 通过不等于真实通过；不要用 Mock 结果补这里缺的项。**

## 还差什么

| 检查项 | 现状 | 补法 |
| --- | --- | --- |
| `llm.upstream` | 被上游拒绝（HTTP 400） | 检查 Key 是否有效、订阅是否激活、模型是否有权限；这一步失败时不得用 Mock 结果代替真实验收 |
| `tts.engine` | stepaudio TTS 依赖的上游不可用 | 先修上游；TTS 与 LLM 复用 base_url/key |
| `asr.engine` | stepaudio ASR 依赖的上游不可用 | 先修上游；ASR 与 LLM 复用 base_url/key |

## 检查明细

| 检查项 | 必需 | 状态 | 说明 |
| --- | --- | --- | --- |
| `runtime.python` | 否 | ok | 正在用项目 .venv 解释器（sys.executable=D:\project\neurosama\.venv\Scripts\python.exe；venv=D:\project\neurosama\.venv\Scripts\python.exe） |
| `runtime.deps` | 是 | ok | httpx/websockets/numpy 可用（required=['httpx', 'websockets', 'numpy']） |
| `runtime.ffmpeg` | 否 | ok | ffmpeg/ffplay 可用（ffmpeg=C:\ProgramData\chocolatey\bin\ffmpeg.EXE；ffplay=C:\ProgramData\chocolatey\bin\ffplay.EXE） |
| `env.key` | 是 | ok | LLM_API_KEY 已配置（只看是否存在，不打印值）（provider=openai；base_url=https://api.stepfun.com/step_plan/v1；model=step-3.7-flash） |
| `llm.upstream` | 是 | blocked | 被上游拒绝（HTTP 400）（reason=upstream_rejected；excerpt={"error":{"message":"you have no active step plan subscription","type":"request_params_invalid"}}） |
| `tts.engine` | 是 | blocked | stepaudio TTS 依赖的上游不可用（被上游拒绝（HTTP 400）） |
| `asr.engine` | 是 | blocked | stepaudio ASR 依赖的上游不可用（被上游拒绝（HTTP 400）） |
| `asr.input` | 否 | ok | 检测到 14 个麦克风输入设备；用 --mic 可做真人语音 |
| `env.ports` | 否 | info | 用户服务端口状态（本脚本只用独立端口，不重启 8000/8010）（8000=占用，8010=空闲） |
| `io.evidence` | 是 | ok | 证据目录可写（D:\project\neurosama\docs\evidence\20261109-live-acceptance\run-20261109-021659） |
| `io.sandbox` | 是 | ok | 可创建临时 DB 沙盘 |

## Mock 边界

- 无 `LLM_API_KEY` 时服务走 Mock：这只证明代码路径能跑，不证明真实模型、真实语音或真实记忆质量。
- Key 存在但上游返回 400/401/402/403 时，本报告判“被上游拒绝”，不是“有 Key 就通过”。
- 体检只做一次 `max_tokens=1` 的上游探测；真实对话/ASR/TTS/记忆检查点是 live 阶段的事。
