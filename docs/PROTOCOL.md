# ============================================================
# 接缝契约 —— 说话层（server/）和形象层（web/avatar/）之间的约定
#
# 我们没有共同的对话渠道，仓库是唯一能留痕的地方。
# 这份文档不描述"应该怎么做"，只描述"改坏了会怎样"，
# 让任何一边改动时都能自己判断影响面，不用互相问。
# ============================================================

## 一、事件顺序

一轮对话的线上顺序（`/ws`）：

```
asr?            只在语音输入时出现
  -> thinking   等 LLM
  -> emotion    情绪标签，最先出现（[emotion] tag）
  -> text_delta* 流式正文，可能 0 次
  -> audio*     分句音频，可能 0 次（TTS 静默时）
  -> done       完整正文 + 统计
```

两条关键时序，形象层依赖它们：

- **`emotion` 早于 `audio`**。表情因此可以比声音早起，
  视觉上像"先有情绪再开口"，比跟着音频起要好。
- **`thinking` 到 `emotion` 之间必须有人松手**。`think` / `listen`
  是 hold 轨道（`hold: true`），要显式 `stop()` 才会收。
  如果后端把 `emotion` 帧去掉，形象会**永久卡在歪头看别处的姿势**。

## 二、字段依赖

| 消息 | 形象层用到的字段 | 改掉的后果 |
|---|---|---|
| `recording` | `state` | 倾听姿势和注视点失效 |
| `asr` | `text` | 用户气泡不出 |
| `emotion` | `emotion` | 表情停在 base；hold 姿势卡死 |
| `text_delta` | 不读 | 形象层不关心 |
| `audio` | `text` / `emotion` / `data` / `index` | **`text` 丢了口型直接死**（没有文本就没有时间轴）；`emotion` 丢了表情停在 base |
| `done` | `text` | 文本特征手势（哈哈/嗯/不对）永不触发 |
| `playback_stopped` / `interrupted` | 无 | 打断后脸不回中位、嘴停在半个口型 |

`audio.text` 是整个口型系统的地基：后端早就把分句文本一起发过来了，
前端靠它 + `decodeAudioData().duration` 摊出 viseme 时间轴。
**这个字段不能删。**

## 三、配置耦合

- `persona.yaml` 的 `tts_emotions` ↔ `avatar.yaml` 的 `emotions`
  情绪标签必须**双向齐全**。少一个不会报错，`EmotionDriver` 会静默回落到
  `base`，表现是"这个情绪永远没有表情"，非常难查。
- `web/avatar/params.js` 的 `PARAM_GROUPS` ↔ `avatar.yaml` 的 `neutral`
  必须互为全集。少写 → 驱动写进去的值被当成偏移再减一次；
  多写 → 平滑循环里没有它，静默丢弃。
- 形象层只**读** `voice_profile`，不写。所以后端怎么改音色参数
  （pitch/rate/新增 instruction）都不影响形象层。

## 四、怎么守住

```bash
uv run python scripts/check_protocol.py
```

它查上面四类漂移，全部静态、零协调成本：

1. 情绪标签双向齐全
2. 参数表与中位姿态互为全集
3. 表情 shift / 手势关键帧 / 触发器 / 注视点的引用全部可达
4. **`app.js` 每个 `case` 分支读的 `msg.<字段>`，服务端必须真的发这个字段**

第 4 条是这个脚本存在的主要理由。它抓到过一次真实事故：
后端把 `text` 帧换成 `emotion` + `text_delta` 之后，形象层还认 `text`，
于是打字路径下 hold 姿势永远松不开。

## 五、改动时的规矩

- **新增消息类型**：`app.js` 加 `case`（形象层关心的还要加进
  `Avatar.onMessage`），然后跑 `check_protocol.py`。
  服务端发了但前端不处理不算错，脚本会提示但放过。
- **删改字段**：先跑一遍 `check_protocol.py` 看看谁在读。
  只有读它的那一边才有依赖，另一边随便改。
- **新增情绪标签**：`persona.yaml` 和 `avatar.yaml` 必须同时加，
  只加一边会被脚本挡下。
- 两边都不该依赖"对方记得这个约定"。能机器检的就不要靠嘴说。