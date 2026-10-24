# probe/ — 一次性实测探针（throwaway，允许脏）

连 `wss://api.stepfun.com/v1/realtime`，喂中文录音，看事件，回答 README「结论」区记录的三个问题。
写完即可扔；不 import `server/`、`web/`、`config/`；不进 `run_gates.py` / `package.json`。

## 三个问题 → 三个模式

| 模式 | 问题 | 判定落在哪个事件字段 |
|---|---|---|
| `--backchannel` | 服务端能否区分「嗯嗯/对」(继续) 与「等等/停」(停) | `input_audio_buffer.speech_started` 是否带分类/置信度字段 |
| `--text-injection` | 能否让服务端逐字念我方给定文本 | `response.audio_transcript.*` 是否等于目标文本 |
| `--alignment` | 打断后是否告知「实际播到第 N ms」；播报音频有无字级时间戳 | `response.cancelled`/`.done` 是否带偏移；`transcript` 是否带 `words[]` |

## 三道闸（缺一即不跑，绝不偷偷连网/计费）

1. **opt-in 闸**：不带 `--live` → 拒绝（exit 3）。付费音频能力必须显式开。
2. **凭据闸**：只读 `voice-lab/.env` 的 `STEP_REALTIME_API_KEY`；缺失 → BLOCKED（exit 4），**绝不回退主 `LLM_API_KEY` / 订阅额度**。
3. **上限闸**：`MAX_SESSIONS=3`、`MAX_SESSION_MS=60000` 写死在脚本顶部；触顶即停并打印已用量。

原始事件全部落 `voice-lab/outputs/`（gitignore，不入库）。

## 离线自测（今天就能跑，不需要凭据、不联网）

```bash
.\.venv\Scripts\python.exe -I voice-lab/probe/realtime_probe.py --selftest
```

验证三件事：事件字段判定器（有/无分类字段分别判「可判定」/「协议层不可观测」）、逐字比对、
`wav → pcm16 24k mono` 帧切分。用来证明探针本身没坏——把「判定器判错」和「服务端不支持」分开。

## 录音准备（Q1 用）

`voice-lab/probe/recordings/*.wav`（ffmpeg 可转任意源格式；无 ffmpeg 时只收 24k 单声道 16-bit wav）：
- 附和组：`嗯嗯.wav`、`对.wav`、`是是是.wav`（期望助手继续说）
- 打断组：`等等.wav`、`停一下.wav`、`别说了.wav`（期望助手立即停）
脚本按文件名字典序喂，两组结果各记 `speech_started` 的事件字段与延迟。

## 当前状态

**2026-10-24 下午已完成实跑**（主人授权：preview 限免、主 key 复制进 voice-lab/.env 使用；
探针代码仍只读该 .env，不读根 .env）。约 9 个会话、合成语音 ≈155s，原始事件在 `outputs/`。
结果与改判记录在 `voice-lab/README.md` 结论区「同日补」一节。要点：
- 端点矩阵：`/v1/realtime`+3-preview 可用；`step_plan` 路由对 3-preview 404、对 2.5 返回 402。
- Q2 硬路径静默零音频；软路径（instructions 复述）逐字播出一次。
- Q1 speech_started 字段两轮一致：无分类字段；打断时服务端不停播。
- Q3 无字级时间戳、无服务端偏移；`conversation.item.truncate` 未写文档但服务端实现（回显我方数值）。
- 脏行为：cancel 后仍在途 audio.delta；时段内服务端直接掐 TCP（无 close frame）。

复跑命令不变（`--selftest` / `--live --<mode>`），换 key 或换正式收费版模型时再坐实一次即可。
