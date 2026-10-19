# 运行时实证报告（RUNTIME_VERIFICATION）

- 执行者：qa-runtime（Agent Teams · QA Engineer），任务 `task-2`
- 执行时间：2026-10-19 12:35:08 – 12:38:25 +08:00（Asia/Shanghai）
- 核验对象：`D:\project\neurosama\pixel-world`（独立原型，Phaser 3 + Vite + TS）
- 仓库：`D:\project\neurosama`，分支 `feat/pixel-world-oracle-world`，HEAD `9cd0c5a4a80669849d6cda6f52584e52dea94b85`（commit time 2026-10-19 10:32:04 +0800）
- 沙箱：danger-full-access（已放开）；本次只新增本文件，未修改任何源码/配置/测试/既有文档
- 原则：以下所有"通过"均来自本次实际执行；未执行的写"未验证"并附原因，不以文档声明当证据

---

## 一、环境与版本

| 项 | 值 | 取证命令/来源 |
|---|---|---|
| OS | Windows（PowerShell 7） | 会话环境 |
| Node.js | v22.23.1 | `node -v`（12:35:08） |
| npm | 10.9.8 | `npm -v`（12:35:08） |
| TypeScript | 5.9.3 | `pixel-world/package.json` devDependencies；`tsc --noEmit` 实际运行 |
| Vite | 8.3.4 | build/dev/preview 启动横幅 `VITE v8.3.4` |
| Phaser | 3.90.0 | `pixel-world/package.json` dependencies |
| Python（主服务） | `D:\project\neurosama\.venv\Scripts\python.exe` | 会话共享事实 + 实际调用 |
| Playwright | 主仓库 `D:\project\neurosama\node_modules\@playwright\test` | `scripts/smoke.mjs` 第 19 行相对导入，本机存在 |
| 工作目录（smoke） | `C:\Users\18712\.dsh\bot\workspace` | 让截图落在工作区 `eval\screens`，不污染仓库 |

版本取证（`recon` 下无独立日志，终端输出）：`v22.23.1` / `10.9.8` / `9cd0c5a4…` / `2026-10-19 10:32:04 +0800`。

---

## 二、逐条命令与结果（含退出码与证据路径）

> 证据目录：`C:\Users\18712\.dsh\bot\workspace\recon\`（临时日志，不在仓库内）。
> 每条命令均记录退出码；`npm` 的 banner 走 stderr，日志为 `*>&1` 合并采集。

### 2.1 环境版本与 commit

```powershell
node -v; npm -v
git -C D:\project\neurosama log -1 --format=%H%n%ci
git -C D:\project\neurosama rev-parse --abbrev-ref HEAD
```

- 退出码：0；时间：2026-10-19 12:35:08 +08:00
- 输出：`v22.23.1` / `10.9.8` / `9cd0c5a4a80669849d6cda6f52584e52dea94b85` / `2026-10-19 10:32:04 +0800` / `feat/pixel-world-oracle-world`

### 2.2 `npm run check`（tsc --noEmit）

```powershell
cd D:\project\neurosama\pixel-world
npm run check
```

- 退出码：**0**；墙钟耗时 **1894 ms**；完成于 12:35:15
- 输出尾部（完整日志仅 54 B，tsc 静默通过）：

```
> xiaoxi-pixel-world@0.1.0 check
> tsc --noEmit
```

- 证据：`recon\check.log`

### 2.3 `npm test`（`node --test test/*.test.ts`，复验已知失败说法）

```powershell
cd D:\project\neurosama\pixel-world
npm test
```

- 退出码：**0**；墙钟耗时 **847 ms**；完成于 12:35:24
- 输出尾部：

```
1..59
# tests 59
# suites 0
# pass 59
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 297.4748
```

- 证据：`recon\npm-test.log`（10,284 B，含 59 条 `ok N - …`）
- **重要偏差**：会话共享事实称"`node --test` 会因 spawn EPERM 失败"；本次在 danger-full-access 下 **未复现**，`npm test` 59/59 通过、退出码 0。该说法不能作为当前环境证据（见 §六 D2）。

### 2.4 四个测试文件逐个直跑（pass/fail 与耗时）

命令（工作目录 `D:\project\neurosama\pixel-world`）：

```powershell
node test\grid.test.ts
node test\ledger.test.ts
node test\surface.test.ts
node test\world.test.ts
```

| 文件 | 退出码 | pass | fail | node 内部 duration | 墙钟（含启动） | 证据 |
|---|---|---|---|---|---|---|
| `test\grid.test.ts` | 0 | 7 | 0 | 11.7591 ms | 181 ms | `recon\test-grid.log` |
| `test\ledger.test.ts` | 0 | 14 | 0 | 31.4905 ms | 165 ms | `recon\test-ledger.log` |
| `test\surface.test.ts` | 0 | 13 | 0 | 22.1686 ms | 161 ms | `recon\test-surface.log` |
| `test\world.test.ts` | 0 | 25 | 0 | 39.8329 ms | 183 ms | `recon\test-world.log` |
| **合计** | **0** | **59** | **0** | — | — | `recon\test-results.json` |

- 四个文件最终都输出 `# tests N / # pass N / # fail 0`，退出码均为 0；完成于 12:35:20
- 汇总：**59 passed / 0 failed**。与 `README.md:25` 的"59 项"一致；与 `README.md:67` 的"test/ 45 项纯逻辑断言"**不一致**（少记 14 项，文档滞后）。

### 2.5 `npm run build`（tsc + vite build）与产物检查

```powershell
cd D:\project\neurosama\pixel-world
npm run build
```

- build 前 `dist/` 不存在；退出码：**0**；墙钟耗时 **2541 ms**；完成于 12:35:31
- 输出尾部：

```
vite v8.3.4 building client environment for production...
transforming...
✓ 30 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                    4.20 kB │ gzip:   1.95 kB
dist/assets/index-ByulgrBX.js  1,247.72 kB │ gzip: 336.96 kB
✓ built in 512ms
(!) Some chunks are larger than 500 kB after minification. ...
```

- 产物（`D:\project\neurosama\pixel-world\dist\`）：

| 文件 | 字节 | gzip | 说明 |
|---|---|---|---|
| `dist\index.html` | 4,204 | 1.95 kB | 入口，`<script type="module" crossorigin src="./assets/index-ByulgrBX.js">` |
| `dist\assets\index-ByulgrBX.js` | 1,247,728 | 336.96 kB | Phaser 打包主 bundle |
| 合计 | **1,251,932 B** | — | 无 sourcemap |

- 直接检查：`dist/index.html` 引用的 `./assets/index-ByulgrBX.js` 实际存在（相对路径 `base: './'`，可直接静态托管）；文件齐全、无 404 引用。
- 观察：单 chunk > 500 kB 警告（Phaser 体积），非阻断。
- 证据：`recon\build.log`

### 2.6 `npm run preview` + 浏览器加载构建产物

```powershell
cd D:\project\neurosama\pixel-world
npm run preview -- --host 127.0.0.1 --port 4174 --strictPort
# 另一终端：node recon\preview-check.mjs
```

- preview 启动成功（日志 `recon\qa-preview.log`），`GET http://127.0.0.1:4174/` → **200**（12:37:38）
- Playwright 加载构建产物（`recon\preview-check.mjs`）：退出码 **0**，耗时 3361 ms；结果：
  - `window.Oracle` 存在；`buildings=3`；`#app canvas` 存在（Phaser 已渲染）
  - `ledger.connected=true`（preview 复用 `server.proxy`，`/api`、`/ws` 代理到 8000）
  - 状态行：`莞尔：去看了一眼农田　晴　台账在线　已交付 0 单　神恩 0/3`
  - 页面 JS 报错 0、console error 0
  - 截图：`08-preview-build.png`
- 证据：`recon\preview-check.log`
- 收口：preview 进程已 kill，4174 端口已释放（12:38:25 检查无监听）

### 2.7 `npm run dev`（5188）+ HTTP 探测

```powershell
cd D:\project\neurosama\pixel-world
npm run dev -- --host 127.0.0.1 --port 5188 --strictPort
```

- 后台启动，日志落盘 `recon\qa-vite.log`；`VITE v8.3.4 ready in 394 ms`
- 首次探测：`GET http://127.0.0.1:5188/` → **200**，`content-type=text/html`，4953 B（12:35:51.824）
- 12:38:18 复核：`/` 与 `/?ledger=off` 均 **200**、4953 B
- 证据：`recon\qa-vite.log`、`recon\http-probe.log`
- 收口：dev 进程已 kill，5188 无监听（12:38:25 复核，连接被拒）

### 2.8 浏览器冒烟 · 沙盒模式（49 项）

```powershell
cd C:\Users\18712\.dsh\bot\workspace
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
```

- 退出码：**0**；墙钟耗时 **33,037 ms**；12:35:56 左右开始，12:36:29 完成
- 输出尾部（脚本按 `?ledger=off` 打开，见 smoke.mjs:258）：

```
神谕闭环：49 项通过
  ok  页面加载无 JS 报错
  ...（49 项逐项见 §三 3.1）
```

- 证据：`recon\smoke-sandbox.log`（49 条 `ok` 已逐项核对，`(Select-String '^\s+ok\s').Count = 49`）
- 截图：6 张，落盘 `C:\Users\18712\.dsh\bot\workspace\eval\screens\`（见 §四）

### 2.9 显式 `?ledger=off` 离线模式校验

```powershell
cd C:\Users\18712\.dsh\bot\workspace
node recon\offline-check.mjs     # 打开 http://127.0.0.1:5188/?ledger=off
```

- 退出码：**0**；耗时 5065 ms；完成于 12:36:51
- 实测结果（JSON）：`ledger.connected=false`、`ledger.lastSyncAt=null`、状态行含"台账离线"
  （实际状态行：`莞尔：去看了一眼农田　晴　台账离线　已交付 0 单　神恩 0/3`）；
  离线模式下短活 `quick` 能开始（working）→ 结束（idle）→ 状态 done；页面无 JS 报错
- 截图：`07-ledger-off.png`
- 证据：`recon\offline-check.log`

### 2.10 台账模式（`LEDGER=1`，10 项）

前置：主服务 8000 已在线（详见 §2.11）。命令：

```powershell
cd C:\Users\18712\.dsh\bot\workspace
$env:LEDGER = '1'
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
Remove-Item Env:LEDGER
```

- 退出码：**0**；墙钟耗时 **2,887 ms**；完成于 12:36:57
- 输出（10 项逐项见 §三 3.2）：

```
台账模式：10 项通过
  ok  页面加载无 JS 报错
  ...
```

- 证据：`recon\smoke-ledger.log`
- 范围限制：其中"上游单进了世界的活动清单 / 她停下日常去干上游派的活 / 台账说收工她走交付"三项由脚本
  在页面内 `window.Oracle.pushLedger()` 注入一帧 `commission_update` 驱动（smoke.mjs:74-106，脚本注释自述
  "等价于服务端真发了一帧"）。因此**服务端真实 WS 推送链路未被本次验证**，只验证了客户端接收/投影/命令队列。

### 2.11 主服务（8000）状态与启动尝试

```powershell
# 参考 docs/TESTING.md:31-32、scripts/run.py
cd D:\project\neurosama
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe scripts\run.py --check --no-gateway
```

- 退出码：**0**；耗时 364 ms；完成于 12:37:33
- 输出：

```
LLM: openai/step-3.7-flash key=有
TTS: stepaudio   ASR: small
  主应用已在运行：http://127.0.0.1:8000（不重复启动）
```

- 关键事实：8000 上的服务**不是本次 QA 启动的**，在本次会话前已存在：
  - `Get-NetTCPConnection -LocalPort 8000` → PID **22996**，LISTEN 创建于 **2026-10-18 16:40:37**
  - `Get-Process -Id 22996` → `python`，StartTime **2026-10-18 16:40:34 +08:00**
  - 命令行：`"C:\Users\18712\anaconda3\python.exe" -m uvicorn server.app:app --host 127.0.0.1 --port 8000`，
    父进程 PID 40528 为 `D:\project\neurosama\.venv\Scripts\python.exe -m uvicorn server.app:app --host 127.0.0.1 --port 8000`
- 8000 实际健康状态：`GET /api/health` → 200：
  `{"ok":true,"llm":{"provider":"openai","model":"step-3.7-flash",...,"offline":false},...}`
  → 该实例使用**真实 API Key、offline=false**，不是 Mock 模式。
- `GET /api/executive/commissions` → 200，35,817 B（被台账冒烟实际使用）
- 证据：`recon\main-service-check.log`、`recon\http-probe.log`
- 结论：台账冒烟复用既有 8000 服务完成；**"无 API Key 自动 Mock"的运行时路径本次未验证**（见 §六 D1）。

---

## 三、冒烟逐项

### 3.1 沙盒模式（`node scripts/smoke.mjs`，49/49，退出码 0）

来源：`recon\smoke-sandbox.log`，逐项原文：

```
  ok  页面加载无 JS 报错
  ok  世界里摆了三栋铺子
  ok  开局她在闲逛
  ok  没人管的时候她会自己走动
  ok  认不出的输入被拒
  ok  认不出的输入没有触发神谕
  ok  界面回了世界结算后的神谕回执
  ok  她停下手里的事行礼
  ok  行礼时头上有字
  ok  她走进铺子开始干活
  ok  进屋后人不在门外
  ok  恰好一台铺子在运转
  ok  开工的是接代码活的那栋
  ok  活干完，她出门交付
  ok  交付时人重新出现在画面里
  ok  台账里这单已完成
  ok  铺子进入冷却
  ok  她回到祭坛恢复闲逛
  ok  回到祭坛后手上没有单
  ok  第二单能派出去
  ok  手上有活时新神谕被拒，理由回到界面
  ok  被拒的神谕没有进台账
  ok  人工交付也能结束这单
  ok  人工交付写进台账
  ok  交付完回到日常
  ok  日常作息给出了一句可读的状态
  ok  日常状态不是被神谕打断的
  ok  短活当场开始
  ok  短活没占用任何铺子
  ok  短活自己结束
  ok  神谕让世界下雨了
  ok  下雨时她撑起伞
  ok  户外的她当场跑去躲雨
  ok  跑到屋檐下停住
  ok  神谕让天放晴
  ok  造物主落下一栋新楼
  ok  新楼处于施工中
  ok  新楼自动铺出了一条路
  ok  施工结束，新楼落成
  ok  第 1 单短活开始
  ok  第 1 单短活结束
  ok  第 2 单短活开始
  ok  第 2 单短活结束
  ok  神恩攒够，世界多了一件东西
  ok  里程碑记了一次
  ok  日报写了交付数
  ok  报纸把标题渲染到了纸面上
  ok  报纸写了落地的东西
  ok  外部 Agent 的消息进了同一条命令队列
```

### 3.2 台账模式（`LEDGER=1`，10/10，退出码 0）

来源：`recon\smoke-ledger.log`，逐项原文：

```
台账模式：10 项通过
  ok  页面加载无 JS 报错
  ok  世界里摆了三栋铺子
  ok  开局她在闲逛
  ok  台账连接建立
  ok  世界拿到过一次台账快照
  ok  状态行读得出台账状态
  ok  上游单进了世界的活动清单
  ok  她停下日常，去干上游派的活
  ok  台账说收工，她走交付
  ok  收工的单退出活动清单
```

### 3.3 显式离线模式附加校验（7/7，退出码 0）

来源：`recon\offline-check.log`，原文 checks：

```
ledger.connected === false
ledger.lastSyncAt === null
状态行含“台账离线”：莞尔：去看了一眼农田　　晴　　台账离线　　已交付 0 单　　神恩 0/3
离线模式下短活能开始（working）
离线模式下短活能结束（idle）
短活状态 done
页面无 JS 报错
```

---

## 四、截图清单

路径：`C:\Users\18712\.dsh\bot\workspace\eval\screens\`（本次全部为 **2026-10-19 12:36–12:37** 新生成；
仓库内 `pixel-world\eval\screens\` 的旧图未改动）。

| 文件 | 大小 (B) | 生成时间 | 来源 | 内容（脚本断点） |
|---|---|---|---|---|
| `01-salute.png` | 33,504 | 12:36:01 | sandbox smoke | 停下行礼、头上有字 |
| `02-working.png` | 32,875 | 12:36:05 | sandbox smoke | 进屋开工、铺子运转 |
| `03-deliver.png` | 34,548 | 12:36:10 | sandbox smoke | 出门交付、铺子冷却 |
| `04-rain.png` | 36,319 | 12:36:21 | sandbox smoke | 下雨撑伞、躲雨 |
| `05-build.png` | 37,133 | 12:36:22 | sandbox smoke | 扩建工地、自动铺路 |
| `06-faith.png` | 34,974 | 12:36:28 | sandbox smoke | 神恩落地、摆件出现 |
| `07-ledger-off.png` | 32,558 | 12:36:51 | offline-check | 显式 `?ledger=off`、台账离线状态行 |
| `08-preview-build.png` | 33,038 | 12:37:49 | preview-check | `vite build` 产物经 preview 加载 |

说明：01–06 由 `smoke.mjs` 的 6 个 `page.screenshot()` 断点写出；07、08 为 QA 附加校验临时脚本写出。
核对截图为 1010×940 视口、headless Chromium（smoke.mjs:29）。

---

## 五、文档声称 vs 实测对照表

| # | 声称 | 出处 | 本次实测 | 判定 |
|---|---|---|---|---|
| 1 | `npm run check` 类型检查通过 | README.md:26 | 退出码 0，tsc 无输出 | ✅ 一致 |
| 2 | `npm test` 纯逻辑 59 项 | README.md:25 | `npm test` 59 pass / 0 fail，退出码 0（297 ms） | ✅ 一致 |
| 3 | `test/` 45 项纯逻辑断言 | README.md:67 | 实际 4 文件共 59 个 `test()`，59 pass / 0 fail | ❌ 不一致（文档少记 14 项，滞后） |
| 4 | `node scripts/smoke.mjs` 49 项 | README.md:34 | 49/49 通过，退出码 0 | ✅ 一致 |
| 5 | `LEDGER=1 node scripts/smoke.mjs` 10 项 | README.md:35 | 10/10 通过，退出码 0 | ✅ 一致 |
| 6 | 默认接台账，离线用 `?ledger=off` | README.md:42 | `?ledger=on`（台账 smoke）connected=true、状态行"台账在线"；`?ledger=off` connected=false、状态行"台账离线" | ✅ 一致（离线/在线两态均实测） |
| 7 | `npm run dev` 起在 5188 | README.md:20 | 200 OK，Vite 8.3.4 ready in 394 ms | ✅ 一致 |
| 8 | `npm run build` = tsc + vite build | README.md:27 | 退出码 0，30 modules，dist 2 文件共 1,251,932 B | ✅ 一致（另有 >500 kB chunk 警告） |
| 9 | 无 API Key 应自动 Mock | 任务说明 / 主仓库文档口径 | 未起无 Key 实例做运行时验证；既有 8000 为真实 Key（offline=false）。配置级：`LLM_API_KEY=''` 时 `settings.llm_api_key=False`；`server/llm.py:48` `offline = provider=="mock" or not api_key`，105/170 行走 `_mock()` | ⚠️ 未验证（仅配置级证据） |
| 10 | WS `commission_update` 增量推进 | README.md:38-40 | 台账 smoke 的推送来自页面内 `pushLedger()` 注入帧，未驱动真实服务端 WS 推送 | ⚠️ 部分验证（客户端侧已验，服务端推送未验） |
| 11 | 已知 `node --test` 因 spawn EPERM 失败 | 会话共享事实 | 本次 `npm test` 59/59 通过、退出码 0 | ❌ 未复现（沙箱放开后该限制消失，见 §六 D2） |
| 12 | 浏览器用主仓库根目录 Playwright | smoke.mjs:17-19 | 从 `D:\project\neurosama\node_modules\@playwright\test` 成功导入并运行 | ✅ 一致 |

---

## 六、未能验证项与原因（不作伪）

| 编号 | 未验证项 | 具体原因 / 已知证据边界 |
|---|---|---|
| D1 | **无 API Key 自动 Mock 的运行时行为** | 8000 端口在本次会话前已被既有服务占用（PID 22996，启动于 2026-10-18 16:40:34），且 `/api/health` 显示 `offline:false`（真实 Key）。QA 未另起第二实例，以免与既有服务/数据库/调度器争用。已做配置级取证：`LLM_API_KEY=''` 时 `settings.llm_api_key=False`（离线），`server/llm.py:48` 据此置 `offline=True` 并走 `_mock`（105、170 行）；但"无 Key 时进程能起来、健康检查显示 offline=true"的端到端证据缺失。 |
| D2 | **受限沙箱下 `node --test` 的 spawn EPERM 失效路径** | 当前沙箱为 danger-full-access，子进程管道未被限制，`npm test` 直接 59/59 通过，无法复现共享事实所述 EPERM。若需复现需切回受限沙箱模式，本次未执行。 |
| D3 | **服务端真实 WS `commission_update` 推送链路** | `smoke.mjs` 台账档用页面内注入帧模拟上游推送（脚本注释亦自述），未抓取真实 WS 帧；本次只证客户端接收→投影→命令队列→任务状态变化。 |
| D4 | **主服务从零启动（干净环境）** | 8000 已有实例在跑，`scripts/run.py` 检测到健康后直接返回"不重复启动"，因此未实际拉起新进程，也未验证冷启动时长/迁移/端口占用失败路径。 |
| D5 | **`npm install` 从零安装** | `pixel-world/node_modules` 已存在，本次未删除重装（避免破坏工作区）。 |
| D6 | **构建单 chunk > 500 kB 的优化效果** | 只记录警告存在，未做拆包/优化验证（非本任务范围）。 |
| D7 | **真实台账上游派活** | 未创建真实督办单；只读取存量数据 + 注入帧，不需要也不应写主服务。 |

阻塞项：无。任务 6 的 10 项台账结果已取得（复用既有 8000 服务）；D1 属"环境不具备无 Key 实例"的取证边界，不是超时或依赖缺失。

---

## 七、进程/端口收口记录

| 对象 | 是否 QA 启动 | 收口动作 | 12:38:25 复核 |
|---|---|---|---|
| Vite dev，端口 **5188**（npm PID 44856 → vite PID 45832） | 是 | `job_kill` 终止后台任务 | 无监听；`GET /` 连接被拒 |
| Vite preview，端口 **4174** | 是 | `job_kill` 终止后台任务 | 无监听 |
| uvicorn 主服务，端口 **8000**（PID 22996） | **否**（2026-10-18 16:40 已存在） | 未终止（非本 QA 启动；可能被其他成员/用户使用） | 仍 LISTEN，PID 22996 |
| Vite，端口 **5189**（PID 49848） | **否**（他人进程） | 未触碰 | 仍存在 |

- `Get-NetTCPConnection -State Listen` 过滤 5188/4174/8000 复核结果：`5188`、`4174` 无残留；`8000` 为既有服务。
- 若验收要求 8000 也关闭，请由启动方（Lead/用户）协调后执行；QA 无该进程所有权，擅自 kill 会中断他人验证。
- 证据：`recon\cleanup.log`、`recon\http-probe.log`

---

## 八、复现命令清单（可直接照抄）

```powershell
# 0) 版本
node -v; npm -v
git -C D:\project\neurosama log -1 --format=%H%n%ci

# 1) 类型检查
cd D:\project\neurosama\pixel-world
npm run check

# 2) 纯逻辑测试：整包 + 逐文件（逐文件用于在本环境排除 spawn 限制干扰）
npm test
node test\grid.test.ts
node test\ledger.test.ts
node test\surface.test.ts
node test\world.test.ts

# 3) 构建与产物检查
npm run build
Test-Path dist\index.html
Get-ChildItem dist -Recurse -File | Select-Object FullName,Length

# 4) 构建产物浏览器校验（可选，另开终端）
npm run preview -- --host 127.0.0.1 --port 4174 --strictPort
cd C:\Users\18712\.dsh\bot\workspace
node recon\preview-check.mjs      # 需先确保 8000 在跑，否则台账降级为离线
# 结束后 Ctrl+C / kill 4174

# 5) dev + 沙盒冒烟（截图落工作区，不污染仓库）
cd D:\project\neurosama\pixel-world
npm run dev -- --host 127.0.0.1 --port 5188 --strictPort
# 另开终端：
cd C:\Users\18712\.dsh\bot\workspace
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
node recon\offline-check.mjs

# 6) 台账冒烟（需 8000 在线）
$env:LEDGER = '1'
node D:\project\neurosama\pixel-world\scripts\smoke.mjs
Remove-Item Env:LEDGER

# 7) 主服务（若 8000 未起）
cd D:\project\neurosama
.\.venv\Scripts\python.exe scripts\run.py --check --no-gateway   # 只检查/报告
.\.venv\Scripts\python.exe scripts\run.py --port 8000            # 真启动（会顺带起网关）

# 8) 收口
Get-NetTCPConnection -LocalPort 5188,4174,8000 -State Listen -ErrorAction SilentlyContinue
```

> 注意：`LEDGER=1` 与 dev 代理固定指向 `http://127.0.0.1:8000`（`vite.config.ts:14-15`），换端口需同步改代理或环境变量。

---

## 九、给下一环的结论摘要（数字为准）

1. 类型检查：**exit 0**（1.894 s）。
2. 纯逻辑测试：**59 passed / 0 failed**（4 文件 7+14+13+25；`npm test` 与逐文件直跑结果一致）。
3. 构建：**exit 0**，30 modules，产物 2 文件共 **1,251,932 B**；preview 加载后 canvas 正常、页面零报错。
4. dev 5188：**200 OK**；沙盒冒烟 **49/49**；台账冒烟 **10/10**；显式 `?ledger=off` 附加校验 **7/7**。
5. README 数字：59（test）✅、49（smoke）✅、10（LEDGER）✅；`test/ 45 项`（README.md:67）❌ 已滞后。
6. 共享事实中"`node --test` spawn EPERM 失败"**未复现**（危险全开沙箱下 59/59 通过）。
7. 未验证：无 Key 自动 Mock 的运行时行为、真实服务端 WS 推送、主服务冷启动、`npm install` 从零。
8. 端口：自启的 5188/4174 已关闭并复核无监听；8000 为会话前既有服务（PID 22996），未由 QA 启动，保持运行。
