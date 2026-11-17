# 2026-11-13 · Fish 场景对照（OpenRouter 快模型）

同一套 4 句真人输入（Fish TTS 预合成 → 假麦克风注入）、钉音色（`FISH_TTS_REFERENCE_ID=faccba1a8ac54016bcfc02761285e67f`）、Fish ASR/TTS、轮次不重叠（每轮等播放结束后再送下一句），仅将对话 LLM 从 `spark-x2.6`（讯飞）换为 `openrouter/google/gemini-2.5-flash`。

- 录屏（带声）：[xiaoxi-fish-scenario.mp4](xiaoxi-fish-scenario.mp4)（106s）
- 读数： [manifest.json](manifest.json) · [timeline.json](timeline.json)

## 时延（起点 = 本轮 `out:start`，单位 ms，n=4）

| # | ASR (`audio_end→asr`) | LLM 首字 (`asr→text_delta`) | TTS 首块 (`text_delta→首audio`) | 到首音频 (`start→首audio`) |
| - | ---: | ---: | ---: | ---: |
| 1 | 3279 | 4471 | 5003 | 15234 |
| 2 | 4646 | 5362 | 5670 | 20178 |
| 3 | 3987 | 5547 | 4838 | 17572 |
| 4 | 3913 | 4558 | 5898 | 18230 |
| **均值** | **3956** | **4984** | **5352** | **17804** |

## 与 spark-x2.6 基线对比（`../no-overlap-fixed-voice/manifest.json`，同协议 n=4）

| 指标 | spark-x2.6 均值 (min–max) | gemini-2.5-flash 均值 (min–max) |
| --- | ---: | ---: |
| LLM 首字 | 8724 (7029–9990) | **4984 (4471–5547)** |
| TTS 首块 | 7090 (6595–7512) | 5352 (4838–5898) |
| ASR | 8146 (5030–16121) | 3956 (3279–4646) |
| 到首音频 | 27552 (24270–35594) | **17804 (15234–20178)** |

解读：LLM 首字较该基线约 **-43%**（~3.7s），到首音频均值约 **-9.7s**。但 **n=4 且方差大**；本轮 ASR/TTS 也比基线快（Fish/网络波动，非 LLM 改动所致），总收益是混合因素，不能全部归因于换模型。仍可确认：在同等语音链路下，更快的对话模型能显著压缩“首字”这一大头，但首音频仍受 TTS 首块与 ASR 串行叠加（合计 ~9s 量级）限制。

复现：`SCENARIO_LLM=openrouter SCENARIO_LLM_MODEL=google/gemini-2.5-flash` 启 `tmp/scenario/run_server.py --port 8765`，ego-browser 跑 `C:/Users/18712/ego/xiaoxi-fish-scenario/round-openrouter-gemini.mjs`，再 `python tmp/scenario/build_video.py docs/evidence/20261113-fish-scenario/openrouter-gemini-2.5-flash`。
