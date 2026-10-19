# S4 演示实跑证据（DEMO_RUN_EVIDENCE）

> 作者：像素世界施工（World Builder） 日期：2026-10-19 状态：S4 证据，QA 已独立复验通过（P1 已关闭：派单正则静默降级，Chief/QA 双重复现存证）
> 依据：`docs/S4_DEMO_PLAN.md`、`docs/DEMO_SCRIPT.md` v0.4
> 基线：写定时 `HEAD=2a0e6ea`（S3 定稿与 S4 演示验收计划）；本文所有数字来自运行日志/命令输出，不手填。

## 0. 结论摘要

| 项 | 结果 | 证据 |
|---|---|---|
| Mock 跑（`?ledger=off`） | 7 段全部完成，整段墙钟 **22.5s**（段落跨度 21.7s） | §2 计时表；`eval/screens/demo-run-mock.jsonl` |
| live 台账跑（隔离 8001 + 真实 WS） | 7 段全部完成，整段墙钟 **41.5s**（段落跨度 16.9s，含页面打开前的派单前置） | §3 计时表/WS 帧；`demo-run-live.jsonl` |
| live 派活的真实 WS 帧 | `drafting→briefing→running→blocked` 共 4 帧，同一 id `c_a0efde6024e7`，世界 1.4s 内让她赶往抄书斋 | §3.2 |
| smoke | **114/114 通过**（QA 已独立验证），exit 0；经 CDP 连接外部 Chrome，未 launch | §4 |
| 主服务冷启动（隔离 8001） | 首次 `bootMs=3174`；停实例后冷启 `bootMs=2999`；health 200、`Application startup complete`、WS 可连 | §3.3 |
| 8000 与主 `data/` | 未重启、未写入（前后 health 200，`commissions.db`/`memory.db` mtime 不变） | §3.4 |
| 截图 | **34 张**（mock 18 + live 16）：每段 100% 整页 + 400% canvas | §5 |

## 1. 干净状态重置（可重复执行）

```powershell
# 1) 清两个原点的 localStorage/sessionStorage（不关用户已开的 Chrome，不动 8000）
cd D:\project\neurosama\pixel-world
node scripts/demo-run.mjs --reset-only
# 输出：cleared: http://127.0.0.1:5188 / cleared: http://127.0.0.1:5189

# 2) 清隔离实例残留目录（demo-run live 正常结束会自删；异常中断时用）
Get-ChildItem $env:TEMP -Filter 'aqi-s4-demo-*' -Directory | Remove-Item -Recurse -Force
Get-ChildItem $env:TEMP -Filter 'aqi-e2e-real-frame-*' -Directory | Remove-Item -Recurse -Force
#    他人/QA 自起隔离实例时同理：临时根 %TEMP%\aqi-s4-demo-* 需自行清理
#    （demo-run live 正常结束会自删；异常中断、或手工 demo-live-serve.mjs 起的不会）

# 3) 确认没有残留端口（8000 是用户既有服务，不在此列）
Get-NetTCPConnection -LocalPort 5188,5189,8001 -State Listen -ErrorAction SilentlyContinue
# 期望：无输出

# 4) 重新起跑
npm run dev                        # Mock 跑需要 5188
node scripts/demo-run.mjs --mode mock
# live 跑不需要 5188；demo-run live 会自起隔离 8001 + 临时 Vite 5189
node scripts/demo-run.mjs --mode live
```

说明：`demo-run` 每轮开场用 CDP `Storage.clearDataForOrigin` 清一次原点存储（只清开场，之后读档/导入刷新时不再清），所以上面的第 1 步主要是给人工核对/QA 复跑前用。

## 2. Mock 跑（`?ledger=off`，标注 Mock/离线）

命令：`cd pixel-world && node scripts/demo-run.mjs --mode mock`
日志：`pixel-world/eval/screens/demo-run-mock.jsonl`（每行 `ts/tMs/mode/segment/phase/...`）
生成计时表的命令：`node <temp>/demo-tables.mjs`（读 JSONL，不读截图、不读手填数字）

| 段落 | 起 (tMs) | 止 (tMs) | 实测耗时 | 来源 | 关键字段 |
|---|---:|---:|---:|---|---|
| seg0-initial | 832 | 2802 | 2.0s | Mock/离线 | 三铺、双角色、`台账离线 · 离线 Mock`、无存档 |
| seg1-mission | 2805 | 10670 | 7.9s | Mock/离线 | 行礼 → 进铺 → 交付计数 +1（`Mock 交付 1 单`、`神恩 1/3`） |
| seg2-paper | 10672 | 11881 | 1.2s | Mock/离线 | `headline=今日有交付` |
| seg3-rain | 11882 | 14715 | 2.8s | Mock/离线 | 雨 151ms 到达，状态行天气=雨，切晴成功 |
| seg4-save | 14717 | 17995 | 3.3s | Mock/离线 | 槽 `schemaVersion=1`、fingerprint `f53a0736d782d657`；刷新后 `读档未对账` |
| seg5-export-import | 17996 | 20913 | 2.9s | Mock/离线 | 导出 → 导入并自动刷新；恢复后 `读档未对账`、`Mock 交付 1 单`、`神恩 1/3` |
| seg6-debug | 20914 | 22486 | 1.6s | Mock/离线 | 面板展开，实体行数 12 行；关闭正常 |

段落总跨度 **21.7s**；整段墙钟（含开场/收尾）**22.5s**。
与 v0.2 剧本预估的差异（供 v0.3 更新）：15–60s 的委托闭环实测 **7.9s**（剧本写 8–15s）；雨在 0.15s 内就进入雨态（剧本写 1.8s 淡入，截图是淡入完成后补拍）；整段演示实际约 **22s**，不需要凑满 3 分钟。

## 3. live 跑（隔离 8001 + 真实 WS，标注 live 台账/真实 WS）

命令：`cd pixel-world && node scripts/demo-run.mjs --mode live`
日志：`pixel-world/eval/screens/demo-run-live.jsonl`、`eval/screens/demo-live-server.log`、`eval/screens/demo-live-vite.log`

> 顺序说明：live 的派单前置（真实 `/ws` 文本 → 慢环落单）在**打开世界页面之前**执行，这样浏览器首次完整对账就能把这一单补进世界并派活；否则“推送早于对账”会让她只看见单、不动。日志里这条前置也归在 `seg1-mission`。

### 3.1 计时表（来自 `demo-run-live.jsonl`；按实际顺序重排）

| 段落 | 起 (tMs) | 止 (tMs) | 实测耗时 | 来源 | 关键字段 |
|---|---:|---:|---:|---|---|
| seg1 前置（WS 派单） | 3419+ | 7782 | ~4.4s | live 台账/真实 WS | 真实 `/ws` 文本 → 4 帧 `drafting→briefing→running→blocked`，id `c_a0efde6024e7` |
| seg0-initial | 8697 | 10686 | 2.0s | live 台账/真实 WS | `台账在线`；世界已收到该督办（`在等你的答复…`） |
| seg1-mission | 10687 | 13571 | 2.9s | live 台账/真实 WS | 台账单进世界后 `villagerMode=toWork`，**1.4s 内**走向抄书斋并 `working` |
| seg2-paper | 13574 | 14670 | 1.1s | live 台账/真实 WS | `headline=世界刚醒来`（无交付，符合 live 现场） |
| seg3-rain | 14672 | 17712 | 3.0s | live 台账/真实 WS | 雨态 165ms 到达；阿雾跑去躲雨，状态行天气=雨 |
| seg4-save | 17720 | 21132 | 3.4s | live 台账/真实 WS | 槽写入 v1；刷新后恢复并重新对账 |
| seg5-export-import | 21137 | 23842 | 2.7s | live 台账/真实 WS | 导出 2.5KB → 导入 → 刷新恢复，`台账在线` |
| seg6-debug | 23850 | 25570 | 1.7s | live 台账/真实 WS | 面板实体行数 13 行 |

段落跨度 **16.9s**；整段墙钟（含隔离服务冷启动、冷启动复验与收尾）**41.5s**。

### 3.2 真实 WS 帧（独立采集连接，不经过浏览器）

```json
[{"atMs":3673,"id":"c_a0efde6024e7","status":"drafting","agent_id":""},
 {"atMs":3674,"id":"c_a0efde6024e7","status":"briefing","agent_id":"scribe"},
 {"atMs":3674,"id":"c_a0efde6024e7","status":"running","agent_id":"scribe"},
 {"atMs":3675,"id":"c_a0efde6024e7","status":"blocked","agent_id":"scribe"}]
```
帧来自隔离实例 8001 的 `/ws`，浏览器经 Vite 5189 代理收到；世界侧 `tasks` 里出现同一 id、状态 `assigned`、`ledger.connected=true`、`ledger.reconciled=true`，随后 `villagerMode=toWork→working`。

### 3.3 主服务冷启动（隔离 8001，lifespan ON）

- 首次冷启动：`bootMs=3174`（wall 3214ms），`health.ok=true`，`startupComplete=true`。
- 停实例：`stoppedMs=1`（8001 立即无监听）。
- 冷启第二次：`bootMs=2999`（wall 3023ms），`health.ok=true`，`offline:true`（无 Key），`startupComplete=true`。
- WS：冷启后新连接 `wsConnected=true`；10s 内无新 `commission_update`（本轮没有新派单，属预期，不是失败）。
- 隔离数据：临时根 `%TEMP%\aqi-s4-demo-*`，复制 `server/ config/ web/index.html`，`data/` 为空，`DB_PATH` 指向临时根；结束后目录删除、8001/5189 均无监听。

### 3.4 8000 与主 data/ 未被触碰

```
realServerBefore={"status":200}   realServerAfter={"status":200}
commissionsBefore=1792377786785.2737  commissionsAfter=1792377786785.2737   # mtime 不变
memoryBefore=1792415334293.8804       memoryAfter=1792415334293.8804        # mtime 不变
teardown: port8001=false port5189=false
```
8000 是用户既有实例（PID 22996），本轮只读 health，未重启、未 stop。

**8000 时间盒口径（S4 复验窗口）**：窗口内 8000 存活，作者侧只读 `/api/health` 与 db mtime，未 stop、未重启；本窗口核对范围是 `data/commissions.db` 与 `data/memory.db` 两个 mtime（前后完全一致，见上）。窗口后 8000 自行退出，**非本团队动作**。作者侧撰写本文时实测：8000 已无监听；当前主服务为 **8010**（pid 38092，python，启动于 2026-10-17 23:54:46），`/api/health` 200、`llm.offline=false`（模型 `step-3.7-flash`）、`memories=1`、`proactivity=true`。S4 之后若要连真机台账演示，需重新确认主服务端口并单独批准。

## 4. smoke（QA 已独立验证 114/114）

- 命令（脚本本体不改）：把 `scripts/smoke.mjs` 的 `chromium.launch()` 换成
  `chromium.connectOverCDP('http://127.0.0.1:9333')`（临时副本，仅在 %TEMP%），
  然后 `node <temp>/smoke-cdp.mjs`。
- 结果：**神谕闭环：114 项通过**，exit 0；结束后共享 Chrome 仍存活（脚本只关自己的页面）。
- 作者侧跑法：本机沙箱禁止 Chromium 自启（`spawn EPERM`），所以用 loader hook 把
  `chromium.launch()` 换成 `connectOverCDP` 连外部 Chrome；**未改任何项目文件**
  （hook 只活在 %TEMP% 的临时副本里）。
- QA 侧：已在自己环境独立复跑官方 smoke 脚本，**114/114 通过**；本条不再是「待复验项」。

## 5. 截图清单（`pixel-world/eval/screens/`，gitignored）

每个段落两张：`demo-<mode>-<seg>-100.png`（100% 整页）与 `-400pct.png`（400% canvas，隐藏 DOM，仅画布像素）。共 **34 张**：mock 18、live 16（live 无行礼/交付两张，因为 live 是台账单；mock 有 salute/working/delivered 三张）。

| 模式 | 段 | 100% 文件 | 400% 文件 |
|---|---|---|---|
| mock | 0-initial | `demo-mock-0-initial-100.png` | `demo-mock-0-initial-400pct.png` |
| mock | 1-salute / 1-working / 1-delivered | `demo-mock-1-mission-*-100.png` | `…-400pct.png` |
| mock | 2-paper / 3-rain / 4-save-restore / 5-export-import / 6-debug | `demo-mock-<seg>-100.png` | `…-400pct.png` |
| live | 0-initial / 1-toWork / 1-working / 2-paper / 3-rain / 4-save-restore / 5-export-import / 6-debug | `demo-live-<seg>-100.png` | `…-400pct.png` |

尺寸核验（`python <temp>/check-demo-shots.py`，读 PNG 头部）：
- `-400pct.png` 全部 **1920×1280**（34 张里 17 张，0 个尺寸不符）；
- `-100.png` 全部 **995 宽**整页（视口 1010×940，fullPage 截图扣掉 15px 滚动条；高度 1359–1398 随内容变化）。

## 6. 三项未验证项的处置结果（S4 口径）

| 项 | 处置 | 证据 | 结论 |
|---|---|---|---|
| live 台账 / 真实 WS | 隔离 8001 实跑第二遍 | §3.1–3.2、`demo-run-live.jsonl`、`demo-live-*.png` | ✅ 当场验证（隔离实例、真实 WS 推送、`ledger=on`） |
| smoke | QA 独立复跑官方 smoke 脚本（CDP 外部 Chrome） | §4，114/114 | ✅ QA 已验证 114/114 |
| 主服务冷启动 | 隔离 8001：停实例→冷启→health→WS | §3.3 | ✅ 当场验证（8000 未动） |

## 7. 已知限制 / 演示说明要点

1. **live 服务是无 Key 的离线 Mock 服务**：`health.llm.offline=true`；台账链路真实，但任务会走到 `blocked` 并带 `blocked_question`（笔录官没有配置执行体），世界只展示问题、不代答（D7 真机执行体仍未验证）。
2. **8000 主实例未被用于本轮演示**：所有 live 证据来自隔离 8001；对 8000 实机演示需单独批准（会写主台账）。
3. **smoke 跑法**：作者侧沙箱无法 launch Chromium（`spawn EPERM`），用 loader hook 换成 `connectOverCDP` 连外部 Chrome 跑官方脚本，未改项目文件；**QA 已独立复跑 114/114**。
4. **计时比剧本短**：Mock 全段约 21s、live 段落 16.9s；v0.3 剧本应按实测更新，不凑满 3 分钟。
5. **截图口径**：100% 是整页（含页头/DOM，给人看窗口实际效果）；400% 是隐藏 DOM 后的 canvas，只用于画布像素检查。
6. **Chrome 生命周期**：所有脚本只关自己开的页面；共享的 headless CDP Chrome 未被关闭。
7. **派单意图只由 `_DELEGATION` 正则判定，同义改写会静默降级**：`server/dialogue/orchestrator.py:60-65` 第一支要求「去(把|帮我|替你)…」开头，命中才走 `kind=delegation` 建单（`server/app.py:727-729`）。换成「帮我把…」等不以「去」开头的说法时，`executive_route` 返回 None，整轮**静默降级**为普通对话——不建单、无任何提示，只听到一句离线 mock 回复（`server/llm.py:250`）。该链路**不依赖 LLM Key**（`delegate()` 只读写台账，`server/assistant/executive.py:166-212`）。佐证：Chief 2026-10-19 23:00 独立复现（自建临时副本 + 自写 WS 探针，未用本文脚本）同 payload 得 id `c_d34c824bc869` 4 帧 + REST items=1，反例「帮我把…」0 帧；QA 三文本对照另得 id `c_e6ecc8ce2da4`。演示/复跑时派单文本必须用 `去把便笺整理成三版标题` 原文。

## 8. 复跑命令汇总

```powershell
cd D:\project\neurosama\pixel-world

node scripts/demo-run.mjs --reset-only                 # 干净重置（清两个原点）
npm run dev                                            # Mock 跑需要
node scripts/demo-run.mjs --mode mock                  # Mock 证据
node scripts/demo-run.mjs --mode live                  # live 隔离证据（自起 8001/5189）
npm run test:inprocess                                 # 单测：180/180（14 个测试文件）
.\.venv\Scripts\python.exe ..\scripts\test_core.py     # 主产品离线测试；必须用 venv python
                                                       # （系统 anaconda 会 numpy ImportError）
python ..\scripts\test_pixel_world.py                  # 门禁：check + test:inprocess + audit
```
live 跑若需把隔离实例留在后台给 QA 观察：
`node scripts/demo-live-serve.mjs`（打印并落盘 `eval/screens/demo-live-env.json`：`tempRoot/serverPid/vitePid/health`；停止用 `node scripts/demo-live-serve.mjs --stop`）。
注意：**在本会话里子进程会随命令进程一起被收掉**（作者侧实测：命令结束后 8001/5189 无监听），所以跨会话观察请让 QA 在自己的终端里执行上面这条命令；脚本本身不碰 8000、不写仓库 `data/`。
