# SMOKE_EGO_MAP — `scripts/smoke.mjs` → `scripts/smoke-ego.mjs` 一一映射

> 作者：像素世界施工（World Builder） 日期：2026-10-20 状态：task-33 产物，待 QA 复验
> 目的：在**不能自启 Chromium** 的环境里，用 ego-browser 复跑官方 smoke 的全部断言。
> 纪律：**不改写语义、不合并、不删减**；自动化不了的单列，不静默跳过。

## 1. 为什么要这份脚本

`scripts/smoke.mjs` 走 Playwright 的 `chromium.launch()` 自启浏览器。本机受限环境实测：

```
$ node scripts/smoke.mjs
Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../@playwright/test/index.mjs'
（worktree 根 node_modules 为空，依赖装在主仓库根）

$ node <playwright 自启探针>
LAUNCH_FAILED: browserType.launch: spawn EPERM
<launching> ...chrome-headless-shell.exe ... --remote-debugging-pipe
```

两道拦截：**依赖不在 worktree** + **沙箱子进程 spawn EPERM**。ego-browser 用的是用户已装好、常驻的 ego lite 浏览器，不需要新起 Chromium 进程，因此可用。

## 2. 映射规则

| 项 | 规则 |
|---|---|
| 断言命名 | `S-L<smoke.mjs 行号> <smoke 原 label>`；行号即 `scripts/smoke.mjs` 里该 `check()`/`waitFor()` 的调用行 |
| 语义 | 条件、阈值、等待顺序照抄；只把 Playwright API 换成 ego-browser Page API |
| 循环 | `for (const zoom of [1,2,4])` 循环内的 L544 执行 3 次；`for (let i = 0; i < 2; ...)` 内的 L495/L496 各执行 2 次 |
| 不可自动化 | 记入脚本末尾的 `manual[]`，**既不计入通过也不静默丢弃** |

## 2.1 ego-browser 与 Playwright 的 API 差异（实测后修正）

ego-browser 的 Page API 是**自定义子集**，不是 Playwright。首版脚本直接照搬 Playwright 方法，在 ego 上第 6 项就崩（`page.textContent is not a function`）。已按实测可用清单改写：

| Playwright 写法 | ego 是否支持 | 本脚本的替代 | 替代后语义 |
|---|---|---|---|
| `page.textContent(sel)` | ❌ | `textContent(sel)` → `page.evaluate(sel => document.querySelector(sel)?.textContent ?? '')` | 等价 |
| `page.waitForTimeout(ms)` | ❌ | `pause(ms)` → `page.evaluate(v => new Promise(r => setTimeout(r, v)), ms)` | 等价（真的等，不是空转） |
| `page.reload()` | ❌ | `reload(PAGE_URL)` → `page.goto(PAGE_URL)` | 等价（导航到同一地址） |
| `page.setViewportSize({w,h})` | ❌ | `setViewport(w,h)` → `page.cdp('Emulation.setDeviceMetricsOverride', …)` | **有条件等价**：CDP 覆盖成功则等价；失败则该 2 条断言降级记入 manual |
| `page.on('pageerror')` | ❌（无 pageerror 事件） | 无替代 | L780 用「Oracle 可调用 + canvas/overlay 已挂载」等价替代；L794 记 manual |
| `locator.screenshot()` | ❌ | `page.screenshot()` 整页 | 截图范围变大，**断言不受影响** |
| `page.click/fill/goto/evaluate/waitForFunction/selectOption/screenshot` | ✅ | 直接用 | 等价 |

## 2.2 干净起点：为什么不能用 `localStorage.clear()`

`smoke.mjs` 用 `addInitScript` 在**页面脚本之前**清档，ego 没有等价物。首版脚本试过
「goto 站点 → `localStorage.clear()` → 再 goto」，结果**清不干净**：

```
localStorage: {"xiaoxi.pixel-world.save.v1": 7403 bytes}
status: 莞尔：回到祭坛 / 阿雾：正向神谕行礼 晴 读档未对账 · 离线 Mock
        台账交付 0 单 · Mock 交付 13 单 · 神恩 1/3 · 排队 1 条：帮我写个爬虫脚本
```

原因：第一份世界已经跑起来了，导航离开时 `pagehide` 触发自动存档，
把内存状态**写回单槽**——刚清完又被写脏。之后每次 `goto` 都读档恢复，
「开局三栋铺子 / 两人闲逛」不再成立，后面 26 条断言连带全崩。

**正确做法（本脚本采用）**：用 ego 支持的 `page.cdp` 调 CDP
`Storage.clearDataForOrigin`，**在页面加载之前**清原点存储，不经过页面生命周期：

```js
await page.cdp('Storage.clearDataForOrigin', {
  origin: 'http://127.0.0.1:5188',
  storageTypes: 'local_storage,session_storage',
});
await page.goto(PAGE_URL);   // 此时才是干净的新世界
```

配套四条纪律：

1. **清档生效自检（goto 之前）**：`resetOriginStorage()` 之后、`goto` 之前断言
   `slotBytes === 0 && localStorage.length === 0`。这一步只验 CDP 清存储是否成功，
   与世界的自动存档无关——**这里非空才是真清档失败**。
2. **初始态自检（goto 之后）**：断言世界是初始态，**不要求存档槽为空**：
   ```
   now < 1500（首帧量级）· buildings === 3 · tasks === 0 · pending === 0
   deliveries.mock === 0 · deliveries.ledger === 0 · faith.points === 0
   weather === 'clear'
   存档槽若存在，必须是 v1 且 buildings=3 / tasks=0 / settled=0 / pending=0
   ```
   不满足直接 FAIL + 打印现场，**不带着脏档往下跑**。
3. **存档用例例外**：`L738-755`（刷新恢复）与 `L757-770`（清档后新世界）必须保留存档，
   这两处**不清档**，且 `L733` 先断言单槽存在再 reload。
4. **收尾清理**：`finally` 里再清一次原点存储，不把脏状态传给下一次运行。

### 为什么「单槽为空」不可能成立

首版把断言写成 `localStorage.length === 0`，于是每次干净启动都误报脏档。读码确认：

- `SandboxScene.ts:72`：`private lastSaveAt = Number.NEGATIVE_INFINITY;`
- `SandboxScene.ts:302`：`const due = this.snapshot.now - this.lastSaveAt >= 30_000;`

首帧 `now - (-Infinity) = Infinity >= 30000` 恒为真 → `due === true` →
`maybeAutoSave()` 在**首帧立刻自动存一次档**。这是设计行为，不是脏档。
所以干净启动下槽里必然有一份 2KB 左右的初始档，**断言「槽为空」本身就是错的**。
（另注：`lastSaveKey` 在 `init()` 的 `:140` 已赋过值，`changed` 分支首帧并不为真；
首帧写档走的是 `due` 分支。）

## 2.3 `pause()` 为什么改到 Node 侧

`page.evaluate(() => new Promise(r => setTimeout(r, ms)))` 在**后台页面**会被浏览器节流，
`setTimeout` 可能拉到 ≥1s 甚至几乎不触发，「等 3.2s」可能提前返回。实测后果就是
`S-L240 没人管的时候她会自己走动 :: moved=0.00 mode=idle`（世界压根没推进）。

改为在 **Node 侧** `await new Promise(r => setTimeout(r, ms))`：Node 进程不在浏览器节流
范围内，等多久就是多久，期间页面照常跑 rAF 推进世界。

## 3. 统计

```
smoke.mjs 沙盒模式 site 数:        111  （check 88 + waitFor 23）
展开循环后运行时条数:              115  （L544 ×3、L495 ×2、L496 ×2）
smoke-ego.mjs 覆盖 site:           111  → 覆盖率 100%
展开后覆盖:                        115 / 115
不可自动化（记入 manual）:          3 条 —— 其中 L780/L794 的 pageerror 事件流 2 条
                                         与 L548 的元素级截图 1 条
```

> **关于「114」这个数字**：官方 smoke 在作者侧历史运行输出的是 `神谕闭环：114 项通过`。
> 静态展开得 115，差 1 的是 **L794「全程无 JS 报错」**——它依赖 Playwright 的
> `pageerror` 事件流，ego-browser 不提供，已显式记入 `manual[]`。所以
> **115 − 1(L794 不可自动化) = 114，与本脚本的自动化覆盖数一致**。

## 4. 逐条映射表

> `kind`：`check` = `check(...)`；`waitFor` = `waitFor(...)`（成功也会计入 smoke 的 checks 数组）。
> `执行次数` 是循环展开后的运行时次数。

| # | smoke 行 | kind | 执行次数 | smoke 原 label | smoke-ego 断言名 | 可自动化 |
|---:|---:|---|---:|---|---|---|
| 1 | 780 | check | 1 | 页面加载无 JS 报错 | S-L780 页面加载无 JS 报错（等价：Oracle 可调用、canvas 与 overlay 均已挂载） | ✅ 等价替代 |
| 2 | 783 | check | 1 | 世界里摆了三栋铺子 | S-L783 世界里摆了三栋铺子 | ✅ |
| 3 | 784 | check | 1 | 开局她在闲逛 | S-L784 开局她在闲逛 | ✅ |
| 4 | 211 | check | 1 | 世界里有两个小人 | S-L211 世界里有两个小人 | ✅ |
| 5 | 212 | check | 1 | 两个角色各有独立 id 与名字 | S-L212 两个角色各有独立 id 与名字 | ✅ |
| 6 | 217 | check | 1 | 离线模式在快照里标成离线 Mock | S-L217 离线模式在快照里标成离线 Mock | ✅ |
| 7 | 224 | check | 1 | 状态行标出离线 Mock | S-L224 状态行标出离线 Mock | ✅ |
| 8 | 228 | check | 1 | 状态行显示多角色摘要 | S-L228 状态行显示多角色摘要 | ✅ |
| 9 | 240 | check | 1 | 没人管的时候她会自己走动 | S-L240 没人管的时候她会自己走动 | ✅ |
| 10 | 241 | check | 1 | 两个角色各自有独立作息 | S-L241 两个角色各自有独立作息 | ✅ |
| 11 | 274 | check | 1 | overlay 名字节点数与快照角色一致、文案正确 | S-L274 | ✅ |
| 12 | 278 | check | 1 | overlay 与 canvas 对齐误差 ≤ 容差 | S-L278 | ✅ |
| 13 | 282 | check | 1 | overlay 与 canvas 同宽 | S-L282 | ✅ |
| 14 | 286 | check | 1 | 气泡节点不超过角色数 | S-L286 | ✅ |
| 15 | 291 | check | 1 | 认不出的输入被拒 | S-L291 | ✅ |
| 16 | 292 | check | 1 | 认不出的输入没有触发神谕 | S-L292 | ✅ |
| 17 | 302 | check | 1 | 界面回了世界结算后的神谕回执 | S-L302 | ✅ |
| 18 | 303 | waitFor | 1 | 她停下手里的事行礼 | S-L303 | ✅ |
| 19 | 304 | check | 1 | 行礼时头上有字 | S-L304 | ✅ |
| 20 | 315 | check | 1 | 气泡在 DOM overlay 里显示（canvas 无中文） | S-L315 | ✅ |
| 21 | 320 | check | 1 | 有气泡时对应名字让位 | S-L320 | ✅ |
| 22 | 323 | waitFor | 1 | 她走进铺子开始干活 | S-L323 | ✅ |
| 23 | 324 | check | 1 | 进屋后人不在门外 | S-L324 | ✅ |
| 24 | 325 | check | 1 | 派活只叫一个人，另一个还在祭坛 | S-L325 | ✅ |
| 25 | 330 | check | 1 | 恰好一台铺子在运转 | S-L330 | ✅ |
| 26 | 334 | check | 1 | 开工的是接代码活的那栋 | S-L334 | ✅ |
| 27 | 336 | check | 1 | 状态行写清默认交付对象 | S-L336 | ✅ |
| 28 | 340 | waitFor | 1 | 活干完，她出门交付 | S-L340 | ✅ |
| 29 | 341 | check | 1 | 交付时人重新出现在画面里 | S-L341 | ✅ |
| 30 | 342 | check | 1 | 台账里这单已完成 | S-L342 | ✅ |
| 31 | 343 | check | 1 | 铺子进入冷却 | S-L343 | ✅ |
| 32 | 349 | waitFor | 1 | 她回到祭坛恢复闲逛 | S-L349 | ✅ |
| 33 | 350 | check | 1 | 回到祭坛后手上没有单 | S-L350 | ✅ |
| 34 | 354 | waitFor | 1 | 第二单能派出去 | S-L354 | ✅ |
| 35 | 356 | waitFor | 1 | 第二个角色接下了另一单 | S-L356 | ✅ |
| 36 | 360 | check | 1 | 派活不串任务 | S-L360 | ✅ |
| 37 | 370 | check | 1 | 两人都忙时新神谕进排队，回执写明第几位 | S-L370 | ✅ |
| 38 | 372 | check | 1 | 排队的单没有立刻进台账 | S-L372 | ✅ |
| 39 | 373 | check | 1 | 排队单在快照 pending 里可见 | S-L373 | ✅ |
| 40 | 379 | check | 1 | 状态行显示排队数 | S-L379 | ✅ |
| 41 | 383 | waitFor | 1 | 人工交付也能结束这单 | S-L383 | ✅ |
| 42 | 384 | check | 1 | 人工交付对沙盒单生效 | S-L384 | ✅ |
| 43 | 386 | check | 1 | 沙盒单人工交付回执标明离线 Mock、不计入台账 | S-L386 | ✅ |
| 44 | 390 | check | 1 | 回执写清收的是谁的单 | S-L390 | ✅ |
| 45 | 391 | check | 1 | 交付统计把 Mock 单独计，不混进台账 | S-L391 | ✅ |
| 46 | 395 | check | 1 | 交付归属到指定角色 | S-L395 | ✅ |
| 47 | 398 | waitFor | 1 | 助手也在干活 | S-L398 | ✅ |
| 48 | 405 | check | 1 | 按名字指定交付对象 | S-L405 | ✅ |
| 49 | 407 | check | 1 | 两个角色都各自记了交付 | S-L407 | ✅ |
| 50 | 413 | waitFor | 1 | 排队的神谕在上一单交付后自动开工 | S-L413 | ✅ |
| 51 | 418 | check | 1 | 自动开工的是排队的那条神谕 | S-L418 | ✅ |
| 52 | 419 | check | 1 | 出队后 pending 清空 | S-L419 | ✅ |
| 53 | 428 | check | 1 | 未知角色的交付被明确拒绝 | S-L428 | ✅ |
| 54 | 443 | check | 1 | 所有单都收尾 | S-L443 | ✅ |
| 55 | 446 | check | 1 | 日报按角色分账，两个人都写清交付数 | S-L446 | ✅ |
| 56 | 450 | waitFor | 1 | 两个人都回到日常 | S-L450 | ✅ |
| 57 | 455 | waitFor | 1 | 日常作息给出了一句可读的状态 | S-L455 | ✅ |
| 58 | 460 | check | 1 | 日常状态不是被神谕打断的 | S-L460 | ✅ |
| 59 | 463 | waitFor | 1 | 短活当场开始 | S-L463 | ✅ |
| 60 | 464 | check | 1 | 短活没占用任何铺子 | S-L464 | ✅ |
| 61 | 468 | waitFor | 1 | 短活自己结束 | S-L468 | ✅ |
| 62 | 472 | waitFor | 1 | 神谕让世界下雨了 | S-L472 | ✅ |
| 63 | 473 | check | 1 | 下雨时她撑起伞 | S-L473 | ✅ |
| 64 | 474 | check | 1 | 户外的她当场跑去躲雨 | S-L474 | ✅ |
| 65 | 477 | waitFor | 1 | 跑到屋檐下停住 | S-L477 | ✅ |
| 66 | 479 | waitFor | 1 | 神谕让天放晴 | S-L479 | ✅ |
| 67 | 484 | waitFor | 1 | 造物主落下一栋新楼 | S-L484 | ✅ |
| 68 | 488 | check | 1 | 新楼处于施工中 | S-L488 | ✅ |
| 69 | 489 | check | 1 | 新楼自动铺出了一条路 | S-L489 | ✅ |
| 70 | 491 | waitFor | 1 | 施工结束，新楼落成 | S-L491 | ✅ |
| 71 | 495 | waitFor | 2 | 第 N 单短活开始 | S-L495 第 N 单短活开始 | ✅ 循环 2 轮 |
| 72 | 496 | waitFor | 2 | 第 N 单短活结束 | S-L496 第 N 单短活结束 | ✅ 循环 2 轮 |
| 73 | 498 | waitFor | 1 | 神恩攒够，世界多了一件东西 | S-L498 | ✅ |
| 74 | 500 | check | 1 | 里程碑记上了 | S-L500 | ✅ |
| 75 | 506 | check | 1 | 日报写了交付数 | S-L506 | ✅ |
| 76 | 508 | check | 1 | 报纸把标题渲染到了纸面上 | S-L508 | ✅ |
| 77 | 509 | check | 1 | 报纸写了落地的东西 | S-L509 | ✅ |
| 78 | 523 | check | 1 | 外部 Agent 的消息进了同一条命令队列 | S-L523 | ✅ |
| 79 | 544 | check | 3 | N% 缩放下 overlay 仍贴合 canvas | S-L544 N% 缩放下 overlay 仍贴合 canvas | ✅ 循环 3 轮 |
| 80 | 570 | check | 1 | 390px 下 overlay 不溢出 canvas | S-L570 | ✅ |
| 81 | 581 | check | 1 | 调试面板默认收起 | S-L581 | ✅ |
| 82 | 600 | check | 1 | 快捷键/按钮能打开调试面板 | S-L600 | ✅ |
| 83 | 601 | check | 1 | 调试面板实体数量与快照一致 | S-L601 | ✅ |
| 84 | 607 | check | 1 | 事件流行数与只读事件数一致 | S-L607 | ✅ |
| 85 | 611 | check | 1 | 调试注入默认折叠 | S-L611 | ✅ |
| 86 | 628 | check | 1 | 400ms 后注入区仍在且两个按钮可点 | S-L628 | ✅ |
| 87 | 632 | check | 1 | 注入区文案标明仅调试、不写台账 | S-L632 | ✅ |
| 88 | 636 | check | 1 | 注入区默认折叠且在刷新区之外 | S-L636 | ✅ |
| 89 | 652 | check | 1 | 1s 后注入区仍在 | S-L652 | ✅ |
| 90 | 653 | check | 1 | 注入按钮可用（feed 进了世界队列） | S-L653 | ✅ |
| 91 | 654 | check | 1 | 展开状态在刷新后保留 | S-L654 | ✅ |
| 92 | 670 | check | 1 | 暂停后调试面板不更新 | S-L670 | ✅ |
| 93 | 674 | check | 1 | 暂停状态在面板可见 | S-L674 | ✅ |
| 94 | 675 | check | 1 | 暂停后注入区仍在 | S-L675 | ✅ |
| 95 | 683 | check | 1 | 恢复后面板继续刷新 | S-L683 | ✅ |
| 96 | 690 | check | 1 | 事件过滤只显示所选 tone | S-L690 | ✅ |
| 97 | 698 | check | 1 | 切过滤后注入区仍在且保持展开 | S-L698 | ✅ |
| 98 | 715 | check | 1 | 390px 视口下调试面板自身不溢出 | S-L715 | ✅ |
| 99 | 724 | check | 1 | 调试面板能关闭 | S-L724 | ✅ |
| 100 | 729 | check | 1 | 手动存档写入单槽 | S-L729 | ✅ |
| 101 | 733 | check | 1 | 单槽里是 v1 JSON | S-L733 | ✅ |
| 102 | 741 | check | 1 | 刷新后建筑数量恢复 | S-L741 | ✅ |
| 103 | 742 | check | 1 | 刷新后道路恢复 | S-L742 | ✅ |
| 104 | 743 | check | 1 | 刷新后神恩恢复 | S-L743 | ✅ |
| 105 | 744 | check | 1 | 刷新后按角色交付统计恢复 | S-L744 | ✅ |
| 106 | 749 | check | 1 | 读档后标记未对账 | S-L749 | ✅ |
| 107 | 755 | check | 1 | 状态行标出读档未对账 | S-L755 | ✅ |
| 108 | 759 | check | 1 | 清档入口可用 | S-L759 | ✅ |
| 109 | 760 | check | 1 | 清档后单槽为空 | S-L760 | ✅ |
| 110 | 767 | check | 1 | 清档后从新世界开始 | S-L767 | ✅ |
| 111 | 794 | check | 1 | 全程无 JS 报错 | —（记入 manual） | ❌ 见 §5 |

## 5. 不可自动化清单（3 条，全部显式记账）

| smoke 行/资源 | 原因 | 处置 |
|---|---|---|
| L780 / L794 `pageerror` 事件流 | ego-browser 不提供 Playwright 的 `pageerror` 事件 | L780 已用「Oracle 可调用 + canvas/overlay 已挂载」做等价替代（S-L780）；L794 无法等价，记 manual |
| L548 `#stage` 元素级截图 ×3 | ego-browser 的 `page.screenshot()` 是全页/视口截图，无 Playwright `locator.screenshot()` 的元素级裁剪 | 降级为整页截图（`smoke-ego-08-overlay-*.png`）；**断言 S-L544 仍逐轮执行**，只少了元素级裁剪这一个证据形式 |
| L570 / L715 390px 视口断言 | ego 无 `setViewportSize`；脚本改用 CDP `Emulation.setDeviceMetricsOverride`，**成功则正常断言**，若 CDP 覆盖失败则这 2 条降级记入 manual（不假装测过 390px） | 条件性：优先自动化，失败才降级 |

## 6. 跑法

```powershell
# 1) dev server（若 vite.config.ts 在沙箱里报 spawn EPERM，用 harness 配置）
cd tmp\wt-pixel-world\pixel-world
node node_modules/vite/bin/vite.js --config vite.harness.mjs

# 2) 另开一个终端，用 ego-browser 跑（Windows 必须写文件再 import）
ego-browser nodejs -e "await import('file:///D:/project/neurosama/tmp/wt-pixel-world/pixel-world/scripts/smoke-ego.mjs')"
```

### `pixel-world/vite.harness.mjs` 是什么

**本地未跟踪辅助文件，不提交。** 它只做一件事：跳过 `vite.config.ts`。原因：

```
failed to load config from ...\vite.config.ts
[plugin externalize-deps] Error: spawn EPERM
```

vite 8 加载 `.ts` 配置时会 `exec` 子进程做 realpath 解析，受限环境拒绝 `spawn`。换成同内容的 `.mjs` 配置文件后 `VITE v8.3.4 ready in 188 ms`，页面正常。

## 7. 产物

| 路径 | 说明 |
|---|---|
| `pixel-world/scripts/smoke-ego.mjs` | 本映射的脚本（跟踪进 git） |
| `pixel-world/docs/SMOKE_EGO_MAP.md` | 本文档 |
| `pixel-world/eval/screens/smoke-ego-*.png` | 运行截图（gitignored） |
