# 2026-11-13 · Fish 引擎真环境场景验收（模拟真人输入 + 录屏）

## 一句话结论

**Fish 的 TTS 与 ASR 都能用**，两者的耗时都不是这一轮「13–16 秒才有声音」的主因：

| 环节 | 本轮实测 | 谁在吃时间 |
| --- | --- | --- |
| Fish ASR（一句 2.3–4.3s 语音） | 2.05 / 2.08 / 2.14 / 2.31 s | — |
| 讯飞 spark-x2.6 首字 | 5.01 / 5.60 / 5.71 / 4.13 s | **主因** |
| Fish TTS 首块（含流式+逐字对齐端点） | 3.23 / 4.01 / 4.34 / 3.32 s | 次因 |

四轮对话全部 `finished=true`、用户气泡无标签污染、TTS 音频与正文逐字一致。
所以「Fish 好不好用」的答案是：**语音这一层合格，慢的是 LLM**；要压时延得先动模型档位/首字策略，换 TTS 或 ASR 引擎收益有限。

产物：`xiaoxi-fish-scenario.mp4`（71.25s，带真实用户声与小溪 TTS 声，不是配音）。

## 怎么造出「真人在说话」

1. 用 Fish TTS 合成 4 条中文语段当**输入**（`tmp/scenario/human-0N.wav`）。
   音色选公共模型「女大学生」`5c353fdb…`：普通真人音色，非名人克隆。
   选女声是为了和引擎默认音色（F0≈148–188 Hz）明显区分——录屏里能听出是两个人在说话。
2. 用 `Page.addScriptToEvaluateOnNewDocument` 在应用脚本之前 stub `navigator.mediaDevices.getUserMedia`，
   返回 `AudioContext.createMediaStreamDestination()` 的假麦克风流；音频在真实点击手势之后播放，
   绕开 autoplay 限制。
3. 页面开「连续对话」，之后走的就是**产品真实路径**：VAD 判定开口/停顿 → PCM 上行 → WS → Fish ASR → LLM → Fish TTS 下行。

| # | 合成文本 | 时长 | Fish ASR 回读 | 相似度 |
| --- | --- | --- | --- | --- |
| 1 | 我今天有点累，随便说两句吧。 | 2.32s | 我今天有点累，随便说两句吧。 | 1.000 |
| 2 | 你还记得我上次说的那只猫吗？它今天一直趴在窗台上。 | 4.27s | 你还记得我上次说的那只猫吗？它今天一直趴在窗台上。 | 1.000 |
| 3 | 晚饭还没想好吃什么，你有什么建议吗？ | 2.88s | 晚饭还没想好吃什么，你有什么建议吗？ | 1.000 |
| 4 | 那就听你的。我明天还要早起，先这样吧。 | 3.67s | 那就听你的，我明天还要早起，先这样吧。 | 0.947 |

第 4 条的 0.947 只差在停顿处被断成逗号，**内容一字不差**。

## 四轮对话（录屏里的实际内容）

| 轮 | 用户（ASR 结果） | 小溪回复 |
| --- | --- | --- |
| 1 | 我今天有点累，随便说两句吧。 | 累就歇会儿，不用硬撑着找话，有想说的随时讲。 |
| 2 | 你还记得我上次说的那只猫吗？它今天一直趴在窗台上。 | 这个我没什么印象，你说的是哪只猫，可以跟我讲讲。 |
| 3 | 晚饭还没想好吃什么，你有什么建议吗？ | 想省事就煮碗面，加个蛋和青菜，几分钟能搞定，也省得你费神去挑。 |
| 4 | 那就听你的，我明天还要早起，先这样吧。 | 行，早点休息，养足精神比什么都要紧。 |

第 2 轮顺带验到了设计约束：沙盒库为空、检索为空时，小溪回「我没什么印象」并反问，
**没有脑补共同经历**——这正是「检索为空必须注入负向约束」那条要求在真链路上的样子。

## 时延读数

起点 = 服务端收到 `start` 帧（VAD 判定开口）。`audio_end` 含 900ms 的停顿判定，
所以它比真实语尾晚约 0.9s。

| 轮 | 语音结束 | ASR 结果 | 首字 | 首段音频 | done | 播放完 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2.74s | 4.79s | 9.81s | 13.04s | 13.06s | 15.48s |
| 2 | 4.42s | 6.50s | 12.10s | 16.11s | 16.13s | 18.44s |
| 3 | 3.16s | 5.30s | 11.01s | 15.36s | 15.38s | 17.72s |
| 4 | 3.80s | 6.11s | 10.24s | 13.56s | 13.58s | — |

拆成三段看更清楚（相邻两列的差）：

| 轮 | Fish ASR | 讯飞首字 | Fish TTS 首块 |
| --- | --- | --- | --- |
| 1 | 2.05s | 5.01s | 3.23s |
| 2 | 2.08s | 5.60s | 4.01s |
| 3 | 2.14s | 5.71s | 4.34s |
| 4 | 2.31s | 4.13s | 3.32s |

n=4，讯飞 spark-x2.6 的方差本来就大（首字 5–7s 属常见），**这组数不能当基准**，
只能当「哪一段贵」的量级判断。

## 默认形象：下载的 スクミズ・シロ

- 文件 `web/models/1610339914738271513.vrm`（VRM0，27.2MB，作者 Mayura / VRoid Hub，
  VRM Public License 1.0，**非商用**）。`web/models/` 被 gitignore，不入库。
- `web/app.js` 默认选项改为 `vroid-mayura`，加载失败回落 `seed-daily-hair`，
  `?avatar=vroid-mayura` 可在没有该文件的环境复现回退路径。
- 真浏览器验证文案：`已启用下载形象 スクミズ・シロ · © Mayura · VRoid Hub（VRM Public License 1.0，非商用）`。
  VRM0 经 `VRMUtils.rotateVRM0(vrm)` 正常渲染（白发蓝瞳水手服+光环，见 `stills/`）。

## 本轮发现

1. **Fish 返回的 WAV 头不可信**：`data` chunk size 写成 `0xFFFFFF00`（4294967040），
   `wave.open()` 会把 2.18s 的文件读成 48695.77s。真实可读帧数只能按文件长度算。
   夹具脚本已内置 ffmpeg 重封装成 48k 单声道 `pcm_s16le`，原始头读数留在
   `tmp/scenario/scenario.json` 的 `raw` 字段。
   产品侧不受影响（TTS 走 mp3，ASR 上传我们自己造的 WAV），但**任何直接 `wave.open` Fish 响应的代码都会错**。
2. **Fish ASR 会把语气事件写成方括号标签**：实测返回 `…吃什么，[疑问]你有什么建议吗？`，
   标签直接进了用户气泡与 LLM 输入。已按先红后绿修复
   （`server/speech/asr.py::_fish` 清 1–6 字的纯字母/汉字短标签，带数字的 `[2]` 与长句方括号不动），
   `scripts/test_fish_engine.py` 16 条全过（原 14 条 + 新增 2 条）。
   修复后本轮的 4 条 ASR 结果均无标签。
3. **TTS 分片拼接与正文逐字相等**：本轮 4 轮（其中 1 轮 2 个音频分片）
   `"".join(分片文本) == done 正文` 全部为 true。
   上一轮第 3 轮曾出现分片少末字（`…顺口` vs `…顺口的`），本轮未复现，**仍列为未定位观察**。

## 复现

```powershell
# 1) 造 4 条模拟真人语音（--reuse 只重算时长与 ASR，不消耗 TTS 额度）
.\.venv\Scripts\python.exe tmp\scenario\gen_human_wavs.py --reuse

# 2) 起隔离服务（8765，绝不碰用户正在用的 8000/8010；凭据只从 .env 读）
.\.venv\Scripts\python.exe tmp\scenario\run_server.py --port 8765

# 3) 录屏（另开一个终端；ego-browser 脚本在仓库外）
ego-browser nodejs -e "await import('file:///C:/Users/18712/ego/xiaoxi-fish-scenario/round-01-scenario.mjs')"

# 4) 合成带声音的 mp4（帧时间轴按 manifest 里的真实 epoch 时间戳）
.\.venv\Scripts\python.exe tmp\scenario\build_video.py
```

联调期间还用过 `tmp/scenario/dump_manifest.py`（把人话与时延打成 UTF-8 文本，
PowerShell 管道按 GBK 解码会乱码）和 `tmp/scenario/pitch_probe.py`（粗测 F0 区分音色）。

## 产物与文件

| 文件 | 是什么 |
| --- | --- |
| `xiaoxi-fish-scenario.mp4` | 71.25s，1280×820，3438 帧，含 4 段用户声 + 4 轮小溪 TTS（5 个分片），`mean_volume -25.2dB / max -2.9dB` |
| `manifest.json` | 全部 WS 事件（78 条，含毫秒时间戳）、逐帧时间、4 段输入语音起点、5 个 TTS 分片的到达时刻与字节数 |
| `timeline.json` | 每条音轨落点：用户 887 / 16194 / 34621 / 52348 ms，小溪 14345 / 32888 / 49145 / 66496 ms |
| `audio/seg-00N.mp3` | 小溪侧真实下发的 TTS 分片（未重编码） |
| `stills/*.jpg` | 4 张关键帧（启动、轮 1 说话中、轮 2 回复、收尾） |

录屏原始帧有 3438 张、248MB，**没有入库**：mp4 已经把它们压进去了，
manifest 保留每帧时间戳，需要重来就重跑第 3 步。`narration.wav`（6.8MB）
也能由 manifest + audio + 输入 wav 重新生成，同样没有入库。

## 边界与未验证

- **不是延迟基准**：n=4，单人、安静、无噪声无回声，没有真麦克风。真实设备上的 VAD/回声消除没测。
- **没测打断**：4 轮都是等小溪说完再开口，barge-in 路径未被这套夹具覆盖。
- **服务版本号是脏的**：`/api/health` 报 `commit a4a319f`，但本轮跑的工作区还带了
  `server/speech/asr.py` 与 `web/app.js` 的未提交改动，**这个 commit 不代表录屏时的代码**。
- 凭据（Fish / 讯飞 / OpenRouter）只落在 `.env`（`.gitignore:15`），本目录不含任何真实 key。
- stepaudio 路径本轮没跑（`.env` 默认指向它但凭据未激活）；本轮全部是 Fish + 讯飞。
