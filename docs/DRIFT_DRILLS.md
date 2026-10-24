# DRIFT_DRILLS — S3 容错演练报告（issue #29）

**日期：** 2026-10-24 · **分支：** `feat/s3-drift-drills`（基于 master `94b1abe`）
**范围：** 三个故障场景真实复现 → 记录 → 小修（文案/重试/状态复位级）→ 带测试回归。
结构性大发现只立案不实施（见文末立案清单）。

**开工协议红线确认：契约够用，零协议变更。** 三场景的错误路径全部由既有
error 帧承载（`code` / `message` / `recoverable` / `terminal` 已在
PROTOCOL.md 登记，前端 `web/app.js` 的 `case "error"` 已消费）；断线重连是
前端连接层行为，v2 跨重连语义（seq 连接级重算 + id 去重跨连接保留）已由
`web/events-v2.js` 的 `resetConnection()` 预留、协议 §25.1 取舍 1 已写明。
无新事件、无新字段、无新路由，未触发 `[PROTOCOL_REQUEST]`。

## 演练脚手架（可重复）

三个脚本自起真实服务 + Playwright 真页面（先例：`scripts/test-step8-e2e-browser.mjs`），
全部拒绝 8000/8010：

| 脚本 | 端口 | 模式 |
|---|---|---|
| `node scripts/drill-s1-network.mjs [--observe]` | 8033 | 服务中途 taskkill 再同端口重启 |
| `node scripts/drill-s2-badkey.mjs [--observe]` | 8034（401 桩 8043） | 坏 Key 指向本地 401 桩 |
| `node scripts/drill-s3-ws-reconnect.mjs [--observe]` | 8035 | 建单→kill→重启→再建单 |

`--observe` 是演练记录模式（只记录不断言），不带参数是回归模式（断言修复后
行为）。worktree 里没有 `.venv`，脚本自动回落到主仓虚拟环境解释器（只读），
cwd 仍是本仓。演练②会把 worktree `data/` 搬到 `tmp/` 隔离（原因见该节）、
结束后原样还回。

---

## 场景① 断网 / 服务不可达

**复现：** `node scripts/drill-s1-network.mjs --observe`（修复前跑，2026-10-24）

**修复前观察：**
- 断开瞬间：status →「断开」、圆点变灰、subtitle「连接已断开，麦克风已释放。请刷新后重试。」；
  麦克风按钮**未禁用**（可点，点了报「连接还没准备好」→ 状态「语音暂不可用」）。
- 断开期间发文本：消息留在输入框（「连接暂不可用，消息已留在输入框里。」），未静默丢失——这块本来就好。
- **服务回来后：页面永不自动重连。** `app.js` 只建一次 `const ws`，`onclose` 是终态，
  停在「断开」直到用户手动刷新。观察等了 35s 无恢复。

**小修（重试 + 状态复位级）：**
- `web/app.js`：`const ws` → `let ws` + `connectWs()` 工厂（四个 handler 改为函数声明、
  每次连接重新绑定）；`onclose` 按指数退避调度重连，提示改为「正在自动重连……」；
  `onopen` 里 `disposed = false`（`cleanupVoice` 的一次性闩锁，不放开则断过一次线后
  语音链路永久失效）+ `v2Hub.resetConnection()`（§25.1：seq 连接级重算、id 去重保留——
  此前该钩子生产代码零调用）。
- `web/reconnect.js`（新）：纯退避调度（1s 起、×2、15s 封顶、成功归零），
  单测 `web/reconnect.test.mjs`（`node --test web/reconnect.test.mjs`）。

**修复后回归：** `node scripts/drill-s1-network.mjs` 全绿——服务回来后页面自动重连
（status 回「在线」、圆点亮）、subtitle 被服务端 `ready` 帧复位为初始引导、全程无页面错误。
浏览器回归脚本即 `drill-s1-network.mjs` 断言模式。

## 场景② Key 失效（LLM / TTS / ASR）

**复现：** `node scripts/drill-s2-badkey.mjs --observe`（修复前跑，2026-10-24）。
与 Mock 模式（无 Key 自动 offline）严格两条路径：本演练 `LLM_API_KEY=drill-invalid-key`
非空、`LLM_BASE_URL` 指向本地「永远 401」的桩（供应商可达、Key 被拒），
`ASR_ENGINE=TTS_ENGINE=stepaudio`（两者复用同一 llm 客户端）。

**修复前观察：**
- **LLM 401（文本轮）**：帧序 `thinking → error(recoverable) → done`，轮次正常收尾
  （快环不卡死），但 recoverable 文案是「生成中断，**已保留收到的部分文本**。」——
  实际一个字都没收到（done.delivery.text_status=partial、无任何 text_delta），
  文案在撒谎；且 4xx 被退避重试三次，失败拖到 **4176ms**（2026-10-24 实测）。
- **ASR 401（语音轮）**：error 帧 message 原样透出
  `asr 401: {"error":{"message":"Incorrect API key provided",...}}`——
  供应商错误体整段进字幕，既不可读也不该外泄。轮次收尾正常。
- **TTS 401**：按句降级为 recoverable「部分语音合成失败，文字仍可阅读。」+ 
  done.delivery.audio_status=partial/failed，文字仍是权威交付——这条路径本来就对
  （python 回归：`scripts/test_stream_delivery.py` 的 fail_at 用例）。另见下方
  「台账污染的意外观察」。

**小修（文案 + 重试级）：**
1. `server/dialogue/orchestrator.py`：生成失败时按「是否真的保住了文本」分流——
   `holder["text"] or pending` 均空 → 「这轮生成失败了，一个字都没收到；请稍后重试或
   检查服务配置。」，否则保持原句。
2. `server/app.py` `_transcribe_turn`：ASR 失败 message 过 `_transcribe_error_message()`——
   stepaudio 的 `asr <code>: <供应商响应体>` 映射为「语音识别服务报错（401），这轮
   没听清；请检查 ASR 配置或稍后再试。」（保留状态码、丢掉响应体）；其余异常原样。
3. `server/llm.py` `stream()`：4xx 快速失败（与 `complete_raw` 及其既有测试
   `test_4xx_is_not_retried` 同语义），5xx 仍退避重试。
4. `web/app.js` `done` 帧：`text_status='partial'` 且零文本时字幕提示
   「这轮没有收到回复内容，可能服务出错了；可以再发一次。」——VOICE_FIRST 模式下
   气泡注释路径不跑，字幕是唯一出口，此前零反馈。

**测试：** `scripts/test_stream_delivery.py` 新增 2 例（零文本文案 / 半文本保旧句）；
`scripts/test_llm_client.py` 新增 2 例（stream 4xx 一次失败 / 5xx 重试后成功）；
浏览器回归 `drill-s2-badkey.mjs` 断言模式全绿（含「无供应商原文泄漏」检查）。

**修复后实测（2026-10-24）：** LLM 401 文本轮 **532ms** 收尾（原 4176ms），
recoverable 文案为诚实版本；ASR 401 文案已映射；页面字幕「这轮没有收到回复内容……」。

**台账污染的意外观察（未修，见立案#1）：** 演练②第一次修复后跑时，worktree `data/`
里留着演练③的待验收单，于是「还记得我的猫叫什么名字吗？」这句闲聊被
`executive_route` 的 clarify 分支截走（台账直出、LLM 根本没被调用）。这不是演练脚本的
错——`orchestrator.py` clarify 分支在 `pending > 1` 时**没有意图门槛**，任何话都会
被截成「你手上等着验收的有 N 单」。演练②现已隔离 `data/` 保证确定性；该路由 bug 立案。

## 场景③ WS 断连重连

**复现：** `node scripts/drill-s3-ws-reconnect.mjs --observe`（修复前跑，2026-10-24）

**修复前观察：**
- 断开后旧任务卡仍在屏上（页面内存态，合理）；**服务重启后页面不重连**，新单的
  v2 事件到不了页面（任务卡不渲染），而台账 REST 里这单已 ready_to_report——
  「任务视图与台账」在断连窗口内必然漂移，且页面永远追不上（只能手动刷新）。
- 台账本身在 SQLite 里跨重启持久（A/B 两单都在）——REST 对账的数据源没问题。

**小修：** 无场景③专属代码——场景①的 `connectWs()` + `onWsOpen` 里的
`v2Hub.resetConnection()` 就是本场景的全部缺口（重连 + seq 重算 + id 去重保留）。

**修复后回归：** `node scripts/drill-s3-ws-reconnect.mjs` 全绿——服务重启后页面自动
重连；重连后新单的 v2 事件（新连接 seq 从 1 重新起算）照常渲染成任务卡；旧任务卡
A 无重复（id 去重跨连接活着）；页面视图与台账 REST 一一对应；全程无页面错误。
断连窗口内丢失的 at-most-once 帧由既有设计兜底（REORDER_WINDOW 跳缺口 + ops 视图
激活时的 REST 轮询对账），本演练未发现该兜底失效。

## 立案清单（结构性发现，本票不实施）

| # | issue | 内容 |
|---|---|---|
| 1 | #33 `[drift] executive_route clarify 分支无意图门槛` | `orchestrator.py:1496`：`pending > 1` 时任何输入都返回 clarify，普通对话被全量截断（「Intent alone never routes」的自身设计原则被该分支违反）。演练②实测复现：待验收 ≥2 单时问「还记得我的猫叫什么名字吗？」得到「你手上等着验收的有 N 单」。 |
| 2 | #34 `[drift] WS 重连后 session_id 不延续` | `Connection.__init__` 每连接生成新 `session_id`，客户端无 resume 通道（协议未提供）。自动重连落地后，「传输层无缝、对话上下文静默重置」成为可感知缺口——重连后她不再接续之前的对话线。需 Role 0 决策是否加协议字段。 |

## 验收

黄金门禁（worktree 内，`.venv` 用主仓解释器）：

```
.\.venv\Scripts\python.exe scripts\check_protocol.py    # PASS
.\.venv\Scripts\python.exe scripts\test_core.py         # 全绿
npm ci --ignore-scripts && npm test                     # 全绿
node --test web/reconnect.test.mjs                      # 3 passed
.\.venv\Scripts\python.exe scripts\test_llm_client.py   # 36 passed, 0 failed
.\.venv\Scripts\python.exe scripts\test_stream_delivery.py  # 22 tests OK
node scripts/drill-s1-network.mjs                       # assert mode: 0 failed
node scripts/drill-s2-badkey.mjs                        # assert mode: 0 failed
node scripts/drill-s3-ws-reconnect.mjs                  # assert mode: 0 failed
```

（数字为 2026-10-24 本分支跑出的记录值；以命令实时输出为准。）
