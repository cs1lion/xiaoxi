# 真环境验收 · 环境体检

- 生成：2026-11-12T11:04:50
- 命令：`scripts/live_acceptance.py --wav tmp\asr_input.wav --expect-text 我今天有点累，随便说两句吧。 --out docs\evidence\20261112-live-acceptance`（耗时 8216ms）
- 根目录：`D:\project\neurosama`
- 解释器：`D:\project\neurosama\.venv\Scripts\python.exe`

## 结论：可以开跑 live

必需项全部为 ok。下一步：`python scripts/live_acceptance.py --mic`（或 `--wav`）。

## 还差什么

无。

## 检查明细

| 检查项 | 必需 | 状态 | 说明 |
| --- | --- | --- | --- |
| `runtime.python` | 否 | ok | 正在用项目 .venv 解释器（sys.executable=D:\project\neurosama\.venv\Scripts\python.exe；venv=D:\project\neurosama\.venv\Scripts\python.exe） |
| `runtime.deps` | 是 | ok | httpx/websockets/numpy 可用（required=['httpx', 'websockets', 'numpy']） |
| `runtime.ffmpeg` | 否 | ok | ffmpeg/ffplay 可用（ffmpeg=C:\ProgramData\chocolatey\bin\ffmpeg.EXE；ffplay=C:\ProgramData\chocolatey\bin\ffplay.EXE） |
| `env.key` | 是 | ok | LLM_API_KEY 已配置（只看是否存在，不打印值）（provider=openai；base_url=https://maas-api.cn-huabei-1.xf-yun.com/v1；model=spark-x2.6） |
| `llm.upstream` | 是 | ok | 上游可用（HTTP 200，4011ms）（reason=；excerpt={"choices":[{"finish_reason":"length","index":0,"logprobs":null,"matched_stop":null,"message":{"content":"","reasoning_content":"用户","role":"assistant","tool_calls":null}}],"created":1794452687,"id":"cht000d4fdc@dx1a1cdb7039029c4503","metadata":{"weight_version":"default"},"model":"spark-x2.6","object":"chat.completion","usage":{"completion_tokens":1,"prompt_tokens":21,"total_tokens":22}}） |
| `tts.engine` | 是 | ok | fish TTS 真发一次请求成功（s2.1-pro-free，11283 字节，1417ms）（s2.1-pro-free 返回 11283 字节） |
| `asr.engine` | 是 | ok | fish ASR 真发一次请求成功（transcribe-1-pro，1410ms）（transcribe-1-pro 返回转写 '<|speaker:0|> [beep]'） |
| `asr.input` | 否 | ok | 检测到 14 个麦克风输入设备；用 --mic 可做真人语音 |
| `env.ports` | 否 | info | 用户服务端口状态（本脚本只用独立端口，不重启 8000/8010）（8000=空闲，8010=空闲） |
| `io.evidence` | 是 | ok | 证据目录可写（docs\evidence\20261112-live-acceptance\run-20261112-110442） |
| `io.sandbox` | 是 | ok | 可创建临时 DB 沙盘 |

## Mock 边界

- 无 `LLM_API_KEY` 时服务走 Mock：这只证明代码路径能跑，不证明真实模型、真实语音或真实记忆质量。
- Key 存在但上游返回 400/401/402/403 时，本报告判“被上游拒绝”，不是“有 Key 就通过”。
- 体检只做一次 `max_tokens=1` 的上游探测；真实对话/ASR/TTS/记忆检查点是 live 阶段的事。
