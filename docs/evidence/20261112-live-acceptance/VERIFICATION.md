# 2026-11-12 · 真环境验收第一次跑通（Fish TTS/ASR + 讯飞星火 LLM）

- 基线：HEAD `1816007` = `origin/master`。
- 结论：**`overall: pass`**，5 个检查点全 pass（见下表）。
- 供应商：文本用**讯飞星火** `spark-x2.6`（OpenAI 兼容）；语音合成与识别用
  **Fish Audio** `s2.1-pro-free`（TTS 免费档）/ `transcribe-1-pro`（ASR）。
- 凭据在 `.env`（`.gitignore:15`），不入库。

这是本项目自 2026-10-13 建立真环境验收以来**第一次产出 live 读数** ——
`docs/evidence/20261109-live-acceptance/` 的 4 次体检全部 `not-ready`，
`live` 那一列一直空着。

## 1. 检查点读数

run：`run-20261112-112353/run.json`，总耗时 67.2 秒。

| 检查点 | 状态 | 耗时 | 关键读数 |
| --- | --- | ---: | --- |
| `L0.server` | pass | 22736ms | 独立端口 8721 起沙盘服务；`llm.offline=false`（不是 Mock）；health 报 `tts.engine=fish` / `asr.engine=fish` |
| `B1.dialogue` | pass | 20398ms | 真实模型回复「行，累了就不用硬撑着找话题，想聊什么随你，不想聊就当旁边有个安静待着的」；`emotion=base`（在七档合法集合内）；首个 text_delta 15989ms |
| `B3.tts` | pass | 2ms | 收到 2 个 audio 帧，合计 99053 字节，容器均为 mp3 |
| `B2.asr` | pass | 21760ms | 转写「不要铂金，不刻意表演，我今天有点累，随便说两句吧。」，相似度 0.727（≥0.6）；语音轮有真实回复 |
| `B4.memory` | pass | 1649ms | 写入 id=1 → 计数 0→1 → 检索 found=true → **硬删除后计数回基线 0** |

## 2. 录屏

`xiaoxi-live-acceptance.mp4`（9.0 秒，1112×714，h264 + aac，255 KB）。

- 画面：ego-browser（ego lite）驱动真实浏览器访问 `http://127.0.0.1:8765/`，
  输入「我今天有点累，随便说两句吧。」→ 点发送 → 等回复 → 逐秒截帧（18 帧），
  再用 ffmpeg 合成。
- 声音：**同一轮浏览器会话里服务端真实合成并由浏览器收到的两个 mp3 分片**
  （`recording/audio-00.mp3`、`audio-01.mp3`，从页面 WebSocket 的 `audio` 帧抓的
  base64 解出来的），不是麦克风录的环境音。
- 画面里可见：回复「累就歇着，不用硬撑着找话。想说什么我都在，不想说也没事。」
  出现在微光气泡里，右侧立绘在线。
- **更正一处**：先前写的「左侧『关于你的记忆』计数从 0 变成 01 —— 真模型抽取记忆也在这轮里落了盘」
  是**读错了界面**。「01」是那一节的**静态小节号**（空闲帧里就在），而记忆计数始终是
  `0 条当前记忆`；`GET /api/memories` 与直接查 SQLite 都确认库里 0 行。
  即这轮**没有**产生记忆 —— 那句「我今天有点累」是情绪陈述，不是该落盘的事实，
  且抽取走的是 `LLM_EXTRACT_MODEL` 的后台链路，本轮没验它。
- 事件序列（页面 WebSocket 实收）：
  `ready → config_ack → asr → AGENT_PRESENCE → thinking → emotion → text_delta →
  speech_plan → text_delta → audio → audio → done`。

### ego-browser 没有录屏 API

ego lite 暴露的 CDP 只能发命令，取不到事件流：`Page.startScreencast` 能起
（`page.cdp` 返回 null，无错），但 `page.waitForEvent` 只支持 popup 与 download，
`screencastFrame` 收不到。所以路线改成「按节奏截帧 + ffmpeg 合成」。
截图还要求窗口未最小化 —— 最小化时 `page.screenshot()` 报
`Cannot capture a screenshot while the window is minimized`，
用 `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` 恢复后才能截。

复现脚本：`recording/round-06-record-audio.mjs`（ego-browser nodejs 执行）。

## 3. B2 的输入是什么（重要）

`--wav` 喂的是 **Fish 合成的中文语音**，不是真人录音。

原始设计要求真人语音（`docs/LIVE_ACCEPTANCE_GUIDE.md` §5「不拿 TTS 合成音冒充真人语音」）。
这次没有真人录音可用，所以：

- 它证明的是「ASR 链路通、这句普通话被听对了」，
- **不代表**真人在真麦克风上的识别率、口音鲁棒性、噪声环境表现。
- 转写里多出的「不要铂金，不刻意表演」是**当时的 instruction 泄漏**
  （见 §4），不是识别错误。

## 4. 过程中发现并修掉的四个真问题

### 4.1 doctor 把「LLM 通但 TTS/ASR 端点不存在」判成就绪（假绿）

把 LLM 指向讯飞后，doctor 报「stepaudio TTS 配置就绪 / stepaudio ASR 配置就绪」，
而讯飞根本没有 `/audio/speech` 与 `/audio/asr/sse`（都返 404 `no Route matched`）。

根因：stepaudio 分支只看 LLM 的 `chat/completions` 通不通，而 TTS/ASR 是**不同端点**。
修法：加 `probe_stepaudio_speech`（`scripts/live_acceptance.py:402`）/
`probe_stepaudio_asr`（`:412`）真探一次专属端点；
404 判路由不存在，400/422 判端点存在（探针参数不全）。

### 4.2 `TTS_ENGINE=fish` 会静默退回 edge_tts

`server/speech/tts.py` 的引擎分支原本只有 edge_tts / stepaudio / silent，
其余值落进 `else` 静默用 edge_tts。若只改 doctor 而不改服务端，
「engine 配了 fish」就是假绿。已给服务端加真 fish 分支
（`server/speech/tts.py:531` 的 `_fish()`、`server/speech/asr.py:237` 的 `_fish()`），
缺 Key 时**报错**而不是换引擎。

### 4.3 Fish 的方括号会念出 instruction 的后半截

persona 的中文 instruction 原样进方括号时，七档里 **base 与 得意 3/3 泄漏** ——
「不要播音腔，不刻意表演」被当成正文念了出来。

这与 Fish 官方文档一致：方括号描述要简洁，
「Don't make bracket descriptions so long that they interrupt readability」。

修法：只取**第一子句**（逗号前）。修后七档全部 0/3 泄漏，
且七档音频字节仍两两不同（情绪分化保留）。

### 4.4 B4 的硬删除其实没被断言，且真值陷阱把它判成了没删掉

两处：

1. 断言列表里**没有**「硬删除后计数回基线」这一条 —— checkpoint 名字叫
   「记忆写入 → 检索命中 → 硬删除」，但删不掉也算 pass。已补。
2. `deleted_ok` 用了 `int(final.get("count") or -1)` —— **0 是合法计数**
   （记忆删光后 count 就是 0），`0 or -1` 得到 -1，把「删干净了」判成「没删掉」。
   实测：硬删除**其实成功**（`removed_rows=1`、memories 表 0 行），但报 False。
   已改用 `_as_int()`（`0` → `0`，缺失 → `-1`）。

另：同一段还有一处 `{**memory_log.calls[-8:], ...}` —— `calls` 是 list，
`**list` 抛 `TypeError: 'list' object is not a mapping`，B4 一跑到就崩，
整个 live 段在最后一步炸掉，前三个检查点的读数全丢。这条路径自 2026-10-13
以来从没真跑过（订阅阻塞），所以一直没暴露。已修为 `{"calls": ...}`。

## 5. 确定性：一处先前结论要更正

先前（`docs/FISH_XFYUN_CLOSED_LOOP_20261112.md` §1.1）说 Fish TTS **非确定性**。
复核后确认：**这个结论是对的，但当时的证据链不完整**。

- 裸 httpx 调用：4 种 payload 配置（带/不带 temperature、top_p、prosody、normalize）
  全部 3/3 摘要不同。
- 生产 payload `synthesize_profile`：同一个 `TTSEngine` 连跑 4 次摘要**完全相同** ——
  但那是 **LRU 缓存命中**（`hit=3, miss=1`），根本没发第二次 HTTP。
- 每次新建 `TTSEngine`（缓存是空的）：4/4 摘要都不同。

结论：Fish TTS 在 API 层非确定，缓存是唯一让重复请求字节一致的东西。
`server/speech/tts.py:479` 的 `_cache_key` 假设「同 key 同 bytes」在 Fish 下
**只对缓存命中成立**，这是接进主链路前要重审的点（本次只做验收替身，未接主链路）。

## 6. 复现命令

```powershell
$env:LLM_BASE_URL="https://maas-api.cn-huabei-1.xf-yun.com/v1"
$env:LLM_API_KEY="<讯飞 key>"
$env:LLM_MODEL="spark-x2.6"
$env:LLM_EXTRACT_MODEL="spark-x2.6"
$env:TTS_ENGINE="fish"
$env:ASR_ENGINE="fish"
$env:FISH_API_KEY="<fish key>"
.\.venv\Scripts\python.exe scripts\live_acceptance.py --wav tmp\asr_input.wav --expect-text "我今天有点累，随便说两句吧。"
```

录屏（浏览器侧）：

```powershell
# 1) 起服务（独立端口，不碰 8000/8010）
$env:TTS_ENGINE="fish"; $env:ASR_ENGINE="fish"; $env:PORT="8765"
.\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8765
# 2) ego-browser 驱动 + 截帧 + 抓音频
ego-browser nodejs -e "await import('file:///C:/Users/18712/ego/xiaoxi-20261112/round-06-record-audio.mjs')"
# 3) 合成视频
.\.venv\Scripts\python.exe tmp\mux_recording.py tmp\record\frames2 "tmp\record\frames2\audio-00.mp3,tmp\record\frames2\audio-01.mp3" tmp\record\xiaoxi-live-acceptance.mp4
```

## 7. 未验证

| 项 | 状态 | 原因 |
| --- | --- | --- |
| 真人语音的 ASR 识别率 | **未验证** | B2 用的是 Fish 合成音（§3） |
| TTS 音色 / 情绪听感 | **未验证** | 脚本只验字节与容器；「像不像她」是人工判断 |
| 首字延迟的可用性 | **未验证** | B1 首个 text_delta 15989ms（讯飞 + 思考模式），比 edge_tts 慢；项目不追 500ms 首字，但这个数要单独记 |
| Fish 逐字对齐接入 `word_timings` | **未做** | 需要流式重构快环交付路径，按 `AGENTS.md` 应先出 ADR |
| Fish 引擎进主链路 | **未做** | 本次只做验收替身；非确定性 + 免费档无 TTFA/DPA 保证，见 §5 |
| 讯飞的记忆抽取质量 | **未测** | 契约形状已验（`response_format` + memories 结构），内容质量未测 |
| 浏览器会话里的记忆抽取 | **未发生** | 该轮输入是情绪陈述，没产生记忆；抽取链路（`LLM_EXTRACT_MODEL` 后台任务）本轮没验到 |
| 长文本合成 | **未测** | 实测 500 字符 65.6 秒；项目微句切分（26/60 字）下不踩到，但绕过切分会 |
