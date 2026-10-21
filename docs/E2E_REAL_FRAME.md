# 真帧端到端验证：无 Key Mock × 真实 WS × 浏览器世界（S1-6 / task-14）

> 核验方式：**真实运行验证**（2026-10-19，本机）。一次性覆盖 S1 遗留的三个"未验证"项，
> 并把"上游派活 → 真实 `commission_update` 帧 → 浏览器里的世界动起来"跑通。
> 全过程不启动/不停止/不写入 8000 真实实例，台账与记忆库都在临时目录里。

## 一、结论摘要

| 项 | 结论 | 证据 |
|---|---|---|
| **D4 主服务冷启动** | ✅ 已验证。最小隔离树 + lifespan ON 可冷启动，`Application startup complete`，`/api/health` 200 | 启动 2512ms；`e2e-real-frame.mjs:186-213` |
| **D1 无 Key 自动 Mock** | ✅ 已验证。`LLM_API_KEY=''` 时 `health.llm.offline=true`，且派单链路真能跑：WS 文本 → 慢环 delegate → 台账 drafting→briefing→running→blocked | health JSON；真实帧时间线（见三） |
| **D3 真实 WS `commission_update` 推送** | ✅ 已验证。独立 `/ws` 采集连接收到真实广播帧（非 `pushLedger` 注入），浏览器经 Vite 代理收到同一批帧并驱动世界 | 帧时间线；`pushLedgerCalls=0` |
| **D7 真机执行体交付** | ⚠️ **未验证（记录阻塞）**。全部 Worker 适配器为 `manual`，没有执行体能把单走到 `ready_to_report`；answer 后续跑仍然回到 `blocked` | D7 探测：`answer=200 → running→blocked`，最终 `blocked` |

复跑结果：**23 passed / 0 failed**（脚本自测项），证据 JSON 默认落在
`%TEMP%\aqi-e2e-real-frame-evidence.json`（可用 `E2E_EVIDENCE` 覆盖）。

## 二、隔离策略（为什么安全）

1. **不碰 8000**：只使用临时端口 8001 与临时 Vite 5189；脚本前后各读一次
   `http://127.0.0.1:8000/api/health`，本轮实测 `200 → 200`（未重启/未 kill）。
2. **数据隔离**：把 `server/`、`config/`、`web/index.html` 复制到系统临时目录，
   `data/` 全新；子进程环境 `PYTHONPATH=<临时根>`、`DB_PATH=<临时根>/data/memory.db`、
   `LLM_API_KEY=''`。台账库 `data/commissions.db` 因此也落在临时根，不会写进仓库。
   脚本在结束前比对仓库 `data/commissions.db`、`data/memory.db` 的 mtime：本轮**前后一致**
   （`e2e-real-frame.mjs:397-410`）。
3. **临时文件不进仓库**：临时 Vite 配置写在系统临时目录，且用"纯对象导出"（不 `import 'vite'`），
   避免仓库外配置无法解析依赖的问题；`finally` 里关进程、删临时目录，并断言 8001/5189 已释放。
4. **代码同一性**：验证的是 `cpSync` 复制出来的 `server/` 快照，与仓库逐字节一致；
   本卡没有修改 `server/` 任何文件。

## 三、实测证据

### 3.1 冷启动与无 Key（D4 + D1）

- 启动耗时：**bootMs=2512**（spawn → `/api/health` 首次 200）。
- `/api/health`（真实响应，摘录）：

```json
{"ok":true,
 "llm":{"provider":"openai","model":"deepseek-chat","extract_model":"deepseek-chat","offline":true},
 "tts":{"engine":"edge_tts","model":"stepaudio-2.5-tts","voice":"livelybreezy-female"},
 "asr":{"engine":"whisper","model":"small"},
 "memories":0,"proactivity":false}
```

`llm.offline=true`、`memories=0` 说明是全新数据目录里的离线 Mock 实例；lifespan 完整跑过
（ledger 订阅、scheduler 等），日志含 `Application startup complete`。

### 3.2 真实 WS 帧时间线（D3）

采集连接：`ws://127.0.0.1:8001/ws`（独立于浏览器，只读帧；无 Origin 头，走 task-8 白名单）。
触发：通过 `/ws` 发送文本 `去把便笺整理成三版标题`（委托意图，走慢环）。

| 相对时间 | 状态 | agent_id | blocked_question（截断） |
|---|---|---|---|
| 3316ms | `drafting` | — | — |
| 3316ms | `briefing` | `scribe` | — |
| 3317ms | `running` | `scribe` | — |
| 3317ms | `blocked` | `scribe` | 笔录官 没有配置执行体，本单不会自动执行。… |
| 4247ms | `running` | `scribe` | —（D7 探测：POST answer 后再跑一次） |
| 4247ms | `blocked` | `scribe` | 同上，仍回到 blocked |

同一连接还收到 `ready/asr/ui_nav/thinking/emotion/text_delta/error/done` 各 1 条；
`error`（recoverable）来自无网络时 edge-tts 合成失败，**不影响**台账与世界链路。

### 3.3 浏览器世界响应（真帧，无注入）

临时 Vite：`5189` → 代理 `/api`、`/ws` 到 `8001`（`changeOrigin:false`，与 pixel-world 生产配置同规则）。
页面：`http://127.0.0.1:5189/?ledger=on`（`?ledger=on` 走真实台账，不用 `?ledger=off`）。

世界快照（浏览器内 `window.Oracle.view()`，实测）：

```json
{"ledger":{"connected":true,"lastSyncAt":2500,"mock":false,"complete":true},
 "tasks":[{"id":"c_c5e495ba4138","kind":"writing","status":"assigned"}],
 "villagerMode":"toWork",
 "villagerTaskId":"c_c5e495ba4138",
 "status":"莞尔：赶往 抄书斋　　晴　　台账在线　　台账交付 0 单 · Mock 交付 0 单　　神恩 0/3",
 "pushLedgerCalls":0}
```

对应断言：
- 真实帧里的督办 `id` 出现在世界任务清单（不是脚本造的单）；
- 按 `/roster` 的 strengths 归到 `writing`，她走向 **抄书斋**（`toWork`，随后 `working`）；
- **`pushLedgerCalls=0`**：全程没有用 `pushLedger` 注入帧（脚本把该函数包了一层计数）；
- 浏览器 `pageerror` 为 0。

> 补充：脚本在浏览器加载后显式包装 `window.Oracle.pushLedger` 计数，避免"看起来是世界响应、
> 实际是脚本注入"的误判。

### 3.4 D7 探测（未验证，已记录阻塞）

`POST /api/executive/commissions/{id}/answer {"answer":"按短句来"}` → HTTP 200，
随后台账再次 `running→blocked`，最终状态 `blocked`，`blocked_question` 仍是
"笔录官 没有配置执行体…"。原因：无 Key + 全部 Worker 适配器都是 `manual`，
没有执行体能把单推进到 `ready_to_report/done`。**D7（真机执行体交付）本轮未验证**，
只覆盖到真实台账状态流。

## 四、复跑方式

前置：`pixel-world` 依赖已装（Playwright 在仓库根 `node_modules`）、项目 venv 存在、
8001 与 5189 空闲、8000 是否在跑都不影响（脚本只读它的 health）。

```powershell
cd D:\project\neurosama\pixel-world
node scripts/e2e-real-frame.mjs            # 跑完清理临时目录与端口
node scripts/e2e-real-frame.mjs --keep     # 保留临时目录排查
```

产物：
- 控制台：逐项 `ok/FAIL` 与最终 `N passed, M failed`；
- 证据 JSON：`%TEMP%\aqi-e2e-real-frame-evidence.json`（含 health、帧时间线、浏览器快照、
  D7 结果、隔离 mtime、端口释放、8000 前后健康）。

脚本结构（`pixel-world/scripts/e2e-real-frame.mjs`）：
`:1-20` 说明与隔离纪律；`:137` main；`:186-213` 冷启动 + health；
`:216-239` 临时 Vite；`:241-260` 真实 WS 采集；`:262-326` 浏览器与真帧驱动；
`:326-356` 世界响应断言；`:357-372` D7 探测；`:380-410` 清理与隔离证据。

## 五、已知限制

1. **D7 不在本卡范围**：要验证真机执行体交付，需要真实 Worker（`cli_subproc`/`durable` + Key 或本地 CLI）。
2. **TTS/ASR 依赖网络**：无 Key/无网时 edge-tts 会报一条 recoverable error（不影响链路，已在证据里保留）。
3. **端口固定**：8001/5189 被占用时脚本直接 FAIL 前置检查，不会抢占；复跑前确认端口空闲。
4. **验证对象是复制出的 server 快照**：与仓库同步复制、逐字节一致；本卡未改 `server/`。
   若希望"直接在仓库上跑"，需给数据目录加 env 入口（当前不支持，属另一项改动）。
5. `8000` 只做健康探测（GET），不创建/推进任何督办；真实台账数据未被本轮改动。

## 六、与 S1 未验证项对照

| 编号 | 原状态 | 本卡后 |
|---|---|---|
| D4 主服务冷启动 | 未验证 | ✅ 已验证（隔离实例、lifespan ON、2.5s 起服） |
| D1 无 Key 自动 Mock | 未验证 | ✅ 已验证（offline=true + 派单链路跑到 blocked） |
| D3 真实 WS `commission_update` | 未验证 | ✅ 已验证（采集连接 + 浏览器消费同一批真帧） |
| D7 真机执行体交付 | 未验证 | ⚠️ 仍阻塞（manual 适配器无执行体，记录到 D7） |
