# P0② 离线 ASR 兜底 + 服务端 VAD — 证据

- 分支：`feat/p0-offline-voice-and-eval-20261108`（自 `master` @ `e036a14`，未 push）
- 日期：2026-11-08
- 范围：`server/speech/vad.py`（新增）、`server/app.py`（仅 `_transcribe_turn` 失败分支）、
  `scripts/test_asr_language.py`、`scripts/test_voice_turns.py`
- 未改动：`docs/PROTOCOL.md`、`server/assistant/executive.py`、8000/8010 服务

## 方案前提 / 反例 / 失效条件

- 前提 1：ASR 失败里「够不到后端」这一类（缺包、无模型、下载/网络失败、401/403/429/5xx）
  可以降级；坏音频（400）与其它异常必须仍是终结错误。
- 前提 2：服务端 VAD 只需在 20ms 帧序列上回答「有没有说话声」，不需要模型与麦克风。
- 反例：把全部异常吞成降级会掩盖真 bug；把静音也当降级会在每次空录音时刷提示。
- 失效条件：极低信噪比 / 远场麦克风会误判；持续 120ms 以上的白噪声会被当成说话声。
  因此文案只说「检测到 / 没检测到说话声」，不声称转写过内容。

## 降级形态（只用既有帧，无新增字段）

`audio_end` → ASR 抛「不可用」类异常 → `error`（`recoverable: true`，`message` 明说
「语音识别暂时不可用（离线或本机没有语音模型）」+ VAD 判定）→ `done`（空文本）。

- 不下发 `asr` 帧：空文本也会被前端读成「正常识别过」。
- 连接、麦克风循环、下一轮输入都不受影响；字幕保留降级文案。
- 静音时文案为「这轮也没有检测到说话声」，不伪造「你没说话」以外的结论。

## 改前 / 改后同口径读数

同一命令、同一测试文件；改前 = `master` 临时 worktree（e036a14）只补 `vad.py` 与测试，
`app.py` 保持原样；改后 = 本分支。

| 命令 | 改前（master） | 改后（本分支） |
| --- | --- | --- |
| `python scripts/test_asr_language.py` | 6 errors（无 `server/speech/vad.py`） | 8 tests OK |
| `python scripts/test_voice_turns.py` | 35 tests, 3 errors（`KeyError: 'recoverable'` ×2、`KeyError: '_asr_unavailable'`） | 35 tests OK |
| `python scripts/check_protocol.py` | — | PASS（22 消息类型字段全对得上） |
| `python scripts/test_core.py` | — | 134 passed, 0 failed |
| `npm test` | — | 5 pass, 0 fail |
| `python scripts/run_evals.py` | — | 16 passed, 0 failed |
| `python scripts/run_gates.py --only voice_turns,asr_language` | — | 2 跑过并断言，0 失败，0 跳过 |

新增断言清单（离线、帧序列）：

- `VadTests`：数字静音 / 静音-说话-静音 / 稳态底噪 / 60ms 短促音 / 空与奇数长度 / 8kHz 帧长。
- `test_only_unreachable_asr_degrades`：4 类不可用异常降级，3 类（含 400、坏 PCM）不降级。
- `test_asr_unavailable_with_speech_degrades_honestly`：可恢复 error、无 `asr` 帧、轮次收尾、连接可复用。
- `test_asr_unavailable_without_speech_says_no_voice`：VAD 判静音时文案不冒充识别。

## 未验证（不估算）

- 真实断网 / 真缺 `faster_whisper` 在本机 8000 服务上的端到端演练：未做。为避免占用用户正在用的
  8000/8010，改用隔离 WS 用例注入同类异常；真实进程级演练依赖停机窗口。
- 真麦克风远场信噪比下的 VAD 准确率：未测，需要录音夹具。
