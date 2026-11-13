# 2026-11-13 · Fish 真环境真实打断（barge-in）录屏

**推荐看修正版（不抢话）**：`clean/xiaoxi-fish-barge-in-clean.mp4`（82.5s）+ `clean/manifest.json`。
第一版 `xiaoxi-fish-barge-in.mp4` 演示了“可连续打断/恢复”，但第 3 句送入过早，额外打断了插话轮次（见下文），不适合作为“插话后安静等回复”的示例。

- 目的：在小溪**正在说话/播放**时注入用户语音，验证打断链路（`out:barge_in` → 停止播放/中断 → 新一轮接上）。
- 环境：隔离服务 `http://127.0.0.1:8765`（sandbox DB，`TEST_SENTINEL=1`），LLM 讯飞 spark-x2.6，TTS/ASR Fish，Fish `reference_id=faccba1a8ac54016bcfc02761285e67f`。
- 输入：`tmp/scenario/human-01/03/04.wav`（Fish 合成「女大学生」音色）经假麦克风注入，连续对话开启。
- 录屏：`xiaoxi-fish-barge-in.mp4`（56.3s，1280×820，2550 帧，含真实用户声与小溪 TTS）。

## 结果（manifest 关键事件）

时间为相对 `videoT0` 的毫秒：

| ms | 事件 | turn(末6位) | 说明 |
| ---: | --- | --- | --- |
| 1327 | out:start / 4067 out:audio_end / 12148 asr | 70bdab | 用户：「我今天有点累，随便说两句吧。」 |
| 27226,27239 | audio / 27255 done | 70bdab | 小溪开始回复并下发音频（「行，累了就不用硬撑着找话…」） |
| 28178(墙钟标记) | 注入 human-03 | — | **在 70bdab 播放中途插话** |
| 28787 | out:barge_in / out:start | 70bdab→5bdb94 | 客户端发起打断，切到新 turn |
| 28790 | playback_stopped | 70bdab | 70bdab 播放被停掉（旧回复未播完即中断） |
| 31947 | out:audio_end | 5bdb94 | 插话音频送完 |
| 34640 | 注入 human-04（下一句） | — | 过早送入，导致 5bdb94 也被打断 |
| 34926 | out:barge_in | 5bdb94 | 再次打断（发生在 5bdb94 尚未产出 asr/回复时） |
| 34936 | interrupted / 34941 playback_stopped | 5bdb94 | 5bdb94 被中断 |
| 42094 | asr | aad625 | 「那就听你的，我明天还要早起，先这样吧。」 |
| 53217 | audio / 53229 done | aad625 | 新一轮正常完成：「那先歇着，别熬太晚，明天早起别赶得慌。」 |

`markers`: `seenBarge=true, seenStop=true, turn2Done=true, t3Done=true`。

## 结论

- **打断链路生效**：在小溪播报中注入用户语音后，前端发出 `out:barge_in`，旧 turn 收到 `playback_stopped`，后续回复由新 turn 承接。
- **可连续打断**：被打断后的处理中 turn（5bdb94，尚未到 asr）再次遇到输入时也触发 `out:barge_in` + `interrupted/playback_stopped`，系统最终恢复并完成 aad625。
- 录制里第 3 句送入偏早（距第 2 句开始约 6.5s，5bdb94 仍在处理），因此演示成了“两次打断 + 恢复”；若要“插话后安静等一轮完成”，应在确认新 turn `done` 且播放 idle 后再送下一句。

## 修正版（clean）关键事件

`clean/xiaoxi-fish-barge-in-clean.mp4`：只打断一次，插话轮次完整播完后才发下一句。

| ms | 事件 | turn | 说明 |
| ---: | --- | --- | --- |
| 7189 | asr | 2e9d45 | 「我今天有点累，随便说两句吧。」 |
| 21716 | audio / 21729 done | 2e9d45 | 小溪开始回复并在播：「不用硬撑，想说什么就说…」 |
| 23232 | **out:barge_in** | 2e9d45→1f1ac0 | 播放中途插话，发起打断 |
| 23248 | **playback_stopped** | 2e9d45 | 旧回复播放被停掉 |
| 28765 | asr / 43971 done | 1f1ac0 | 插话轮次「晚饭还没想好吃什么…」→ 完整回复并结束 |
| 52672 | out:start（下一句） | a27adc | **上一轮结束并留出播放缓冲后**才发起 |
| 60657 | asr / 72482 done | a27adc | 「那就听你的，我明天还要早起…」→「行，早点歇着…」正常完成 |

结论：打断后新轮次能独立完成；“下一句要等上一句（含播放）真正结束”必须由等待播放 idle 保证，固定抢时机会再次打断。

## 产物

| 文件 | 说明 |
| --- | --- |
| `xiaoxi-fish-barge-in.mp4` | 录屏（带用户声 + 小溪 TTS） |
| `manifest.json` | 全部 WS 事件与时间戳、音频分片索引、markers |
| `timeline.json` | 音轨落点 |
| `audio/seg-00*.mp3` | 小溪侧原始 TTS 分片 |

原始帧未入库（mp4 已含画面）。凭据仅在 `.env`（gitignored），本目录不含 key。
