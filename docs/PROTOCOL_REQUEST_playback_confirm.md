# [PROTOCOL_REQUEST] 播放确认闭环（已批准并实施）

**状态：2026-11-09 Role 0 批准（最小集），已实施。**

> **2026-11-12 更正**：本文件原头写「待 Role 0 批准，未实施」，而实现已落地
> （提交 `cba2e94`）。协议侧现写在 `docs/PROTOCOL.md` §六「播放回执 `playback_done`
> （2026-11-09 Role 0 批准，最小集）」；服务端在 `server/app.py` 的
> `Connection.note_playback` 与 `/ws` 接收分支，前端在 `web/app.js` 的
> `reportPlayback`，checker 在 `scripts/check_protocol.py` 的三方认账，
> 契约测试在 `scripts/test_playback_done.py`（8 条）。
> 下面 §1–§9 是**批准前**的原始请求内容，保留作为决策依据，**不再代表现状**。
> 实施范围与当时的具体取舍见下方「2026-11-09 实施记录」。

**批准前的状态（历史）**：本文件只是草案：**未改 `docs/PROTOCOL.md`、未改
`scripts/check_protocol.py`、未写任何业务代码、未重启服务。**
批准前 `playback_confirmed` 继续保持恒 `False`。

**日期**：2026-11-09
**触发点**：`server/dialogue/delivery.py:146` 与
`server/dialogue/orchestrator.py:1243` 都把 `done.delivery.playback_confirmed`
硬编码为 `False`；`docs/PROTOCOL.md` §2 又明写 `done.delivery` 只记录合成状态，
不声明播放完成。

---

## 1. 结论：现有契约不够用，但只差一个客户端事件

`done` 在**服务端交付完成时**就发出，客户端此时通常还在播队列里的音频。
服务端没有「播放已经结束」的输入，所以：

- `playback_confirmed` 只能是 `False`，不是漏填，是**没有信号可填**；
- 复用它需要把 `done` 延后到播放结束——会破坏「`done` 表示生成/交付完成」
  的既有语义，且客户端卡顿/后台挂起会拖住整轮收尾，不可取；
- 现有客户端事件都无法可靠替代（见 §6）。

**最小改动是新增一个客户端→服务端事件**，让客户端在**播放队列清空或被打断时**
回报结果。服务端不需要新增回包事件；是否对外再发一帧由 Role 0 在 §9 决定。

---

## 2. 现状证据

| 事实 | 位置 |
|---|---|
| `done.delivery` 含 `playback_confirmed: False`（非流式主路径） | `server/dialogue/delivery.py:146` |
| 同一字段在流式路径同样恒 `False` | `server/dialogue/orchestrator.py:1243` |
| `done` 在服务端 `finish()` 时发出，同时把 `conn.playing` 置 `False` | `server/app.py:545-551`（`finish`）、`:464`、`:908` |
| 协议明确 `done.delivery` 不声明播放完成 | `docs/PROTOCOL.md` §2（`done` 定义段） |
| 前端没有「播放完成」上报；`audio_end` 是**录音结束**，不是播放结束 | `web/app.js`、`web/voice/*`（`audio_end` 语义） |
| 现有测试把 `playback_confirmed` 恒 `False` 当契约断言 | `scripts/test_*.py` 共 10 处（以 `grep playback_confirmed scripts/test_*.py` 为准） |

`conn.playing` 的真实含义是「服务端正在交付本轮」，不是「客户端正在出声」。
因此空闲计时、ambient 梯度都以**服务端交付结束**为界，而不是以播放结束为界。

---

## 3. 需要新增的东西

### 3.1 客户端 → 服务端事件 `playback_done`

```ts
// 前端 → 服务端，单向；不期待回包
interface PlaybackDone {
  type: "playback_done";
  turn_id: string;            // 必填，关联哪一轮 assistant 回复
  status: "completed" | "stopped" | "failed";
  last_seq?: number;          // 已完整播出的最高 audio.seq（断流/跳播时用于归因）
  played_ms?: number;         // 可选：实际出声时长，用于 telemetry；服务端不得据此计费/扣额
}
```

语义：
- `completed`：该轮所有已收到的 `audio` 片段都播完（队列自然清空）。
- `stopped`：用户打断、切页、主动停止；服务端通常已收到既有 `barge_in`，
  本事件只补「播放确实停了」。
- `failed`：解码/自动播放被浏览器拦截/设备错误；**不得**被当成用户拒绝或情绪信号。

### 3.2 服务端行为（建议最小版）

1. 校验 `turn_id` 是**当前或上一轮** assistant 轮次；陈旧/未知 `turn_id` 直接忽略
   （记录 debug 日志），不得取消当前轮、不得解锁资源。
2. 只有 `status=completed|stopped` 时把该轮标记为「播放已确认」；`failed` 记录但不确认。
3. 更新连接级空闲基准：把 `last_spoke_at` 从「服务端交付结束」推进到「播放结束」，
   供 ambient/proactivity 的空闲判定使用（这是本事件最主要的行为收益）。
4. 不因该事件重发 `done`；`done.delivery.playback_confirmed` 保持 `False`，
   **不要**在 `done` 里承诺它（`done` 发得比播放早，字段语义已由协议定死）。

### 3.3 是否需要服务端回包

建议**不新增**服务端事件：确认结果留在服务端状态与 telemetry 即可。
若 Role 0 要求前端/评测可见，再讨论是否发一帧 `playback_confirmed`
（届时需要定义 `turn_id`、`status`、`confirmed` 三字段，属于第二个新增）。

---

## 4. 下游消费者

| 消费者 | 用途 | 现状 |
|---|---|---|
| `server/app.py` 连接状态 | 把 `busy/playing` 的空闲起点从「交付完成」改成「播放结束」 | 现在 `finish()` 即置 `playing=False` |
| ambient / proactivity 调度 | 避免 mumble/proactive 在本轮语音尾部仍在播时插入 | 目前按 `last_spoke_at`（服务端起话时间）+ 梯度秒数判空闲 |
| 评测 / telemetry | 区分「生成了但没播」与「播完」；统计跳过率、失败率 | 现在只有 `audio` 计数，没有播放结果 |
| 前端音频队列 | 事件的生产者；播放清空/被打断时上报 | 现在无上报 |

---

## 5. 不加会怎样

1. **指标永远失真**：`playback_confirmed` 恒 `False`，评测无法区分
   「合成成功但用户没听到」（切页、自动播放拦截、设备静音）与「正常播完」。
2. **可能盖住自己的尾音**：`ambient.mumble_seconds` 从 `last_spoke_at` 起算，
   而 `last_spoke_at` 在**起话**时就写入了；一轮较长的语音还没播完，
   服务端就可能认为「用户已经安静够久」而插入 mumble/proactive。
3. **打断归因不清**：`barge_in` 只能说明「用户开始说话」，不能说明
   旧轮播放是否真的停下；补 `stopped` 才能区分「打断成功」与「停不下来」。
4. **测试继续固化一个空字段**：10 处断言把恒 `False` 当契约，未来接线时
   会同时改协议、实现和测试，成本只会更高。

---

## 6. 替代方案（为什么都不够）

| 方案 | 问题 |
|---|---|
| 复用 `barge_in` 当「播放停止」 | `barge_in` 是用户开始说话的即时信号，不代表旧轮音频已停；且 `completed`（自然播完）没有对应事件 |
| 复用 `proactive_check` 心跳当隐式确认 | 心跳默认 45 秒一次，粒度太粗；没有 `turn_id`；无法区分「播完」「切页」「解码失败」，会把空闲误判成播放完成 |
| 复用 `playback_stopped` | 这是**服务端 → 客户端**的停止指令（`server/app.py:572`），方向相反，不能当客户端回执 |
| 复用 `audio_end` | 语义是**用户录音结束**，不是 assistant 播放结束；混用会让 VAD 与轮次边界同时失真 |
| 把 `done` 延后到播放结束再发 | 破坏「`done` = 生成/交付完成」的协议语义；客户端后台挂起/卡顿会拖住服务端收尾与资源释放 |
| 什么都不加，删掉 `playback_confirmed` 字段 | 协议面最干净，但放弃播放结果指标，第 5 节的「盖住尾音」问题仍在；适合作为「明确不做」的备选，而不是默认 |

---

## 7. 兼容性与边界

- **纯增量**：旧客户端不发 `playback_done`，服务端行为与现在完全一致
  （`playback_confirmed` 仍 `False`，空闲基准仍按交付结束）。
- 服务端必须对未知/多余 `turn_id` 幂等：同一轮重复上报只取第一次终态。
- 该事件**只作状态与 telemetry 输入**：不得触发工具、不得改记忆、不得扣额度、
  不得作为「用户已读」的凭证。
- 前端上报失败不得阻塞播放或下一轮（fire-and-forget，失败仅本地日志）。
- 不新增 HTTP 路由、不改 `done` 字段集合、不改 `audio` 字段集合。

---

## 8. 验收（批准后实施时）

1. `scripts/check_protocol.py`：登记 `playback_done` 命令与字段，未知字段仍忽略。
2. 新增离线契约测试：合法 `turn_id` 置确认；陈旧 `turn_id` 忽略；`failed` 不确认；
   重复上报幂等；旧客户端无事件时行为不变（`playback_confirmed` 仍 `False`）。
3. 浏览器夹具：模拟播完、打断、解码失败三种终态，断言服务端状态与
   ambient 空闲起点变化（不新增面板可见字段）。
4. 现有 10 处 `playback_confirmed=False` 断言保留为「`done` 不声明播放」的契约，
   不因新增事件而改成 `True`。

---

## 9. 需要 Role 0 决定的问题

1. **批不批 §3.1 的 `playback_done` 客户端事件与四个字段？** 若只批最小集，
   建议保留 `turn_id` + `status`，`last_seq`/`played_ms` 可后置。
2. **§3.3 要不要服务端回包？** 不回包则评测只能读服务端 telemetry；
   回包则多一个服务端事件，需再次确认 `done` 不承载播放字段的原则不变。
3. **空闲基准是否改为播放结束？** 这会改变 ambient/proactivity 的实际触发时刻；
   若不改，事件只服务 telemetry，行为收益为零。
4. **不批的备选**：是否接受「删除 `playback_confirmed` 字段、明确不做播放确认」
   ——这是零协议改动的方案，但第 5 节问题保留。

---

## 10. 2026-11-09 实施记录（批准后）

Role 0 批准**最小集** + **空闲基准改为播放结束**。落地提交 `cba2e94`。

**批了什么**

| §9 的问题 | 裁决 |
| --- | --- |
| 1. 批不批 `playback_done` 与四个字段？ | **批最小集**：只 `turn_id` + `status`（`completed` / `stopped` / `failed`）。`last_seq` / `played_ms` **后置** |
| 2. 要不要服务端回包？ | **不回包**。结果留在服务端状态与 telemetry，前端不新增事件 |
| 3. 空闲基准是否改为播放结束？ | **改**。`completed` / `stopped` 才把连接空闲基准 `last_spoke_at` 从「交付结束」推进到「播放结束」；`failed` 只记录、不推进 |
| 4. 备选（删字段不做） | 不采纳 |

**没改什么**

- `done.delivery.playback_confirmed` **仍恒 `False`** —— `done` 发得比播放早，
  协议不承载播放结果（§1 的前提没变）。现有 10 处 `playback_confirmed=False`
  断言**一条没改**，它们守的仍是「done 不声明播放」这个契约。
- 不新增 HTTP 路由、不改 `done` 字段集合、不改 `audio` 字段集合。

**落地位置**

| 层 | 位置 |
| --- | --- |
| 协议 | `docs/PROTOCOL.md` §六「播放回执 `playback_done`（2026-11-09 Role 0 批准，最小集）」 |
| 服务端 | `server/app.py` 的 `Connection.note_playback` 与 `/ws` 接收分支（`elif kind == "playback_done"`） |
| 前端 | `web/app.js` 的 `reportPlayback`：队列自然清空 → `completed`，`stopPlayback` → `stopped`，解码/播放失败 → `failed` |
| checker | `scripts/check_protocol.py` 的三方认账（PROTOCOL.md / app.js / app.py）——**不是**加进 `emitted_types()` / `frontend_cases()`，那是 client→server 方向 |
| 契约测试 | `scripts/test_playback_done.py`（8 条）：合法 turn_id 置确认、陈旧 turn_id 忽略、`failed` 不确认、重复上报幂等、旧客户端无事件时行为不变 |
| 浏览器夹具 | `scripts/test-voice-browser.mjs` 两条可观测断言：自然播完报 `completed`、被打断报 `stopped` |

**幂等与边界**：陈旧或未知 `turn_id` 静默忽略（不取消当前轮、不解锁资源）；
同一轮重复上报只取第一次终态；`failed` **不得**被当成用户拒绝或情绪信号。
