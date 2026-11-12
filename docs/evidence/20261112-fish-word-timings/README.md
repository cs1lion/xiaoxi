# 2026-11-12 · Fish 逐字对齐接入 `word_timings`（P4）

## 结论

Fish 的 `POST /v1/tts/stream/with-timestamp` 返回的字级 `alignment.segments`
已经接进服务端 `TTSEngine.word_timings()`，字段语义沿用
`docs/PROTOCOL.md:132`（`[{text,start,end}]`，相对片段秒数）。
**没有新造字段、没有改协议**，所以不需要 ADR。

## 实测形状（真实 Fish 账号）

短文本 `你回来啦。今天过得怎么样？`（4 个 SSE 事件后收齐）：

- `segments` 是**累计**列表：第 1 个事件 1 段，最后 1 个事件 11 段；
- 时间是相对整段音频的**绝对秒数**（0.0 → 3.04s），不是分块局部；
- `chunk_audio_offset_sec` 在短/长文本里**恒为 0.0**，不能当分块偏移加；
- 段粒度是中文**逐字**，且**不含标点**；
- 方括号 instruction（`[语气自然随意] …`）**不进** segments；
- 连流后的 mp3 可解码：ffprobe 2.847s vs 末事件 `audio_duration` 2.786s
  （差值是尾部静音）。

## 端到端探测（真实引擎，非 Mock）

`tmp/verify_fish_timings_live.py` 直接实例化 `TTSEngine`，把引擎钉到 `fish`，
合成后读 `word_timings()`：

| 读数 | 值 |
| --- | --- |
| 音频字节 | 50990（mp3，头 `fffb90c4`） |
| timings 条数 | 11 |
| 首条 | `start 0.0 → end 0.24` |
| 末条 | `start 2.8 → end 3.04` |
| 拼接 == 正文去标点 | **true** |
| 时间单调 | **true** |

原始读数：`live-probe.json`（本目录）。

## 实景验收回归

改用流式端点后重跑一次真环境验收（`run-20261112-144235`）：
`overall: pass`，5/5 检查点。B3 `chunks=1`、`bytes=68126`、容器 `mp3` 可识别，
说明取流方式变了但交付契约没变。

## 复现

```powershell
# 1) 引擎级：真实 Fish 合成 + word_timings 读数
.\.venv\Scripts\python.exe tmp\verify_fish_timings_live.py

# 2) 门禁（纯逻辑，离线）
.\.venv\Scripts\python.exe scripts\test_fish_word_timings.py
.\.venv\Scripts\python.exe scripts\test_fish_engine.py

# 3) 真环境验收（需要 Fish + 讯飞 key，shell 覆盖 TTS_ENGINE=fish/ASR_ENGINE=fish）
.\.venv\Scripts\python.exe tmp\run_p1_live.py without
```

## 未验证

- **浏览器里的唇形/字幕实际对齐效果**：本目录只证明服务端产出了合法 timings；
  前端 `providerMap` 是否采用（而不是退回 `estimated`）需要看浏览器侧
  `alignment` 标记，本轮没有跑 UI 观测。
- **其他文本的标点/数字/英文混排**：数字被 Fish 归一化成中文读法时，
  段文本可能与原文去标点后的字符序列不一致 —— 前端会整份退回估算（安全降级），
  但覆盖率没有统计。
