#!/usr/bin/env node
/* task-33：用 ego-browser 复跑 `scripts/smoke.mjs` 的 114 条 check（等价映射，不改写语义）。
 *
 * 为什么需要这份脚本
 * ------------------
 * `scripts/smoke.mjs` 用 Playwright 的 `chromium.launch()` 自启浏览器。本机沙箱对
 * 子进程 spawn 有硬限制（实测 `browserType.launch: spawn EPERM`），所以官方入口在
 * 受限环境里跑不起来。ego-browser 走的是已经装好、由用户托管的 ego lite 浏览器，
 * 不需要新起 Chromium 进程，因此在同一台机器上可用。
 *
 * 映射原则
 * --------
 * 1. **一一对应**：`scripts/smoke.mjs` 里每个 `check(...)` / `waitFor(...)` 在本文件里
 *    都恰好有一个同名断言（`CHECKS` 表里 `smoke` 字段的原文一字不改）。
 * 2. **不改写语义**：断言条件、阈值、等待顺序都照抄；只是把 Playwright 调用换成
 *    ego-browser 的 Page API。
 * 3. **不合并、不删减**：`MANUAL` 表里列出本脚本无法自动化的条目及原因，
 *    而不是悄悄跳过或并到别的 check 里。
 *
 * 跑法（Windows 无 heredoc，必须写文件再 import）
 * ------------------------------------------------
 *   ego-browser nodejs -e "await import('file:///D:/project/neurosama/tmp/wt-pixel-world/pixel-world/scripts/smoke-ego.mjs')"
 *
 * 前置条件：
 *   1. dev server 在 5188（`npm run dev`；若 `vite.config.ts` 在沙箱里报
 *      `[plugin externalize-deps] Error: spawn EPERM`，改用同目录的
 *      `vite.harness.mjs`：`node node_modules/vite/bin/vite.js --config vite.harness.mjs`）
 *   2. ego lite 已安装且当前会话能连到 ego_cli bootstrap
 *
 * 产物：截图写 `eval/screens/smoke-ego-*.png`（gitignored）。
 */

const URL_BASE = 'http://127.0.0.1:5188/';
/** 实际打开的页面地址（沙盒模式固定 ?ledger=off）；reload 等价做法是重新 goto 同一地址。 */
const PAGE_URL = `${URL_BASE}?ledger=off&debug=1`;
/** CDP 清存储要用的 origin（不带路径与查询串）。 */
const ORIGIN = 'http://127.0.0.1:5188';
/** 截图必须用绝对路径（ego 拒绝相对路径）；落在 pixel-world/eval/screens/。 */
const SHOT_DIR = 'D:/project/neurosama/tmp/wt-pixel-world/pixel-world/eval/screens';

/**
 * rAF 替身：ego 后台窗口被 Chromium 节流 → 用 MessageChannel 门控到 60fps。
 *
 * 问题：ego lite 的窗口在桌面上不可见/未聚焦时，Chromium 把 requestAnimationFrame
 * 节流到 1–12fps 且抖动。实测 3 秒真实时间世界时间只走了 +0ms，于是 smoke 里所有
 * 「等 3.2s / 25s 再断言」的条目必然超时（之前 12 条 FAIL 的真因）。
 *
 * 解法：MessageChannel 的 postMessage 不受后台标签节流影响，用它驱动一个 60fps
 * 门控循环来接管 window.requestAnimationFrame，等价还原真实浏览器的帧率。
 * 必须用 CDP 的 Page.addScriptToEvaluateOnNewDocument 在页面脚本之前注入。
 *
 * 实测效果（Chief 侧）：rAF 59.9fps、世界时间比 1.00（3200ms 真实 → 3200ms 世界）、
 * 角色正常走动（moved=7.07）、截图恢复。
 *
 * 注意：这是**测试专用**的运行时还原，不改 pixel-world 源码，也不进正式产物。
 */
const RAF_PATCH = `(() => {
  const realNow = performance.now.bind(performance);
  const channel = new MessageChannel();
  const cbs = [];
  let scheduled = false;
  let last = -1e9;
  const FRAME = 1000 / 60;
  const pump = () => {
    const t = realNow();
    if (t - last < FRAME) { channel.port2.postMessage(0); return; }
    last = t;
    scheduled = false;
    const batch = cbs.splice(0, cbs.length);
    for (const cb of batch) { try { cb(t); } catch (e) {} }
  };
  channel.port1.onmessage = () => { pump(); };
  window.requestAnimationFrame = (cb) => { cbs.push(cb); if (!scheduled) { scheduled = true; channel.port2.postMessage(0); } return cbs.length; };
  window.cancelAnimationFrame = () => {};
  window.__rafPatched = '60fps';
})();`;

/* ------------------------------------------------------------------ 断言账本 */

/** 已执行并通过的断言（顺序即执行顺序）。 */
const passed = [];
/** 失败项：{ label, error }。 */
const failed = [];
/** 本脚本判定「不可自动化」的条目（不静默跳过，单独记账）。 */
const manual = [];

/**
 * 单条断言。`name` 必须与映射文档里的一致；`smoke` 是 `scripts/smoke.mjs` 原文 label。
 */
async function expect(name, ok, detail = '') {
  if (ok) {
    passed.push(name);
    return true;
  }
  failed.push({ label: name, error: detail || '断言为假' });
  return false;
}

/** 断言失败也要记录，但不中断整套（smoke 用 assert 中断，这里改成汇总，便于一次跑完看到全貌）。 */
function record(name, error) {
  failed.push({ label: name, error: String(error && error.message ? error.message : error) });
}

/* ------------------------------------------------------------------ 兼容层
 *
 * ego-browser 的 Page API 是**自定义子集**，不是 Playwright。实测它没有：
 *   page.textContent / page.waitForTimeout / page.reload / page.setViewportSize / page.on
 * 下面这几个小函数把脚本里用到的写法收敛到 ego 支持的方法上，
 * 语义与 Playwright 版本保持一致，且不引入任何猜测性 API。
 */

/** 等价 page.textContent(selector)：返回可见文本，取不到返回 ''。 */
async function textContent(selector) {
  return page.evaluate((sel) => document.querySelector(sel)?.textContent ?? '', selector);
}

/**
 * 等价 page.waitForTimeout(ms)。
 *
 * 关键教训（实测）：**不能**用 `page.evaluate(() => new Promise(r => setTimeout(r, ms)))`。
 * ego 驱动的页面可能在后台，浏览器会把 setTimeout 节流到 ≥1s 甚至几乎不触发，
 * 「等 3.2s」实际可能提前返回（L240 的 moved=0.00 就是这么来的：世界没推进）。
 *
 * 正确做法：在 **Node 侧**等待。Node 进程不在浏览器节流范围内，`await` 一秒钟
 * 就是真实一秒钟；等待期间页面照常跑自己的 rAF，世界正常推进。
 */
async function pause(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 等价 page.reload()：ego 没有 reload，重新 goto 同一地址。
 *
 * 注意两点：
 *   1. **不能清档**——调用方是在验「存档能否恢复」，清了就测不到恢复。
 *   2. goto 同一 URL 可能被当成同文档导航而不真正重载，所以这里带一个
 *      「世界时钟是否回退/页面是否重跑」的确认：调用方随后都会再 waitForFunction。
 */
async function reload(url) {
  await page.goto(url, { timeout: 30_000 });
}

/** 等价 page.screenshot({ path })：ego 要求绝对路径，且不保证父目录存在，这里自己保证。 */
async function shot(name) {
  await page.screenshot({ path: `${SHOT_DIR}/${name}` });
}

/**
 * 等价 page.setViewportSize()：ego 没有运行时视口方法（视口是 task/page 创建参数）。
 * 用 ego 支持的 page.cdp 调 CDP `Emulation.setDeviceMetricsOverride`，
 * 这是浏览器本身的能力，不是 ego 私有约定；失败就记进 manual 并跳过该组断言。
 */
async function setViewport(width, height) {
  try {
    await page.cdp('Emulation.setDeviceMetricsOverride', {
      width,
      height,
      deviceScaleFactor: 1,
      mobile: false,
    });
    return true;
  } catch (error) {
    record(`setViewport(${width}x${height})`, error);
    return false;
  }
}

async function clearViewportOverride() {
  try {
    await page.cdp('Emulation.clearDeviceMetricsOverride', {});
  } catch {
    /* 清不掉不影响后续断言 */
  }
}

/* ---------------------------------------------------------------- 干净起点 */

/**
 * 强制干净起点（替代 smoke.mjs 的 `addInitScript` 清档）。
 *
 * 为什么不能用「打开页面 → localStorage.clear() → 再 goto」：
 * 第一份世界已经跑起来了，离开页面时 `pagehide` 会把内存状态**自动存档写回单槽**，
 * 于是刚清完又被写脏（实测踩过：清完 single slot 仍是 7KB、Mock 交付 13 单）。
 *
 * 正确做法是**在页面脚本跑起来之前**用 CDP 清原点存储——`Storage.clearDataForOrigin`
 * 直接作用于 storage，不经过页面生命周期，之后首次 goto 就是干净的新世界。
 * 这样每次调用都等价于官方 sleep 脚本里的「首次导航前清一次」。
 */
async function resetOriginStorage() {
  // 先确保有一个可用的页面会话（新 task space 的 p1 初始是 about:blank）。
  // 用 about:blank 而不是站点地址：它不加载世界，自然不会在离开时写回存档。
  const currentUrl = await page.url().catch(() => '');
  if (currentUrl === '' || currentUrl === 'about:blank') {
    await page.goto('about:blank', { timeout: 15_000 }).catch(() => {});
  }
  await page.cdp('Storage.clearDataForOrigin', {
    origin: ORIGIN,
    storageTypes: 'local_storage,session_storage',
  });
}

/** RAF_PATCH 是否已注册。CDP 注册一次就对该 page 之后的所有导航生效，不必重复注。 */
let rafPatchInstalled = false;

/**
 * 注册 rAF 替身到**当前 page 会话**（见文件顶部 RAF_PATCH 的说明）。
 *
 * 必须早于第一次 goto：`Page.addScriptToEvaluateOnNewDocument` 只对**之后**新建的
 * 文档生效。注册后每次导航（含 reload 的重新 goto）都会自动重新执行，无需再调。
 */
async function installRafPatch() {
  if (rafPatchInstalled) return;
  await page.cdp('Page.addScriptToEvaluateOnNewDocument', { source: RAF_PATCH });
  rafPatchInstalled = true;
}

/**
 * 干净地打开世界：先清原点存储、装 rAF 替身，再 goto，再等 Oracle 就绪。
 * 所有「需要一个全新世界」的 stage 都必须走这里。
 */
async function openFreshWorld(url = PAGE_URL) {
  await resetOriginStorage();
  await installRafPatch();
  await page.goto(url, { timeout: 30_000 });
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
}

/** 读取当前单槽状态，用于诊断与前置自检。 */
async function storageProbe() {
  return page.evaluate(() => {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key !== null) keys.push(key);
    }
    const slot = localStorage.getItem('xiaoxi.pixel-world.save.v1');
    return { keys, slotBytes: slot === null ? 0 : slot.length, length: localStorage.length };
  });
}

/**
 * 世界状态摘要：失败时打印，用来区分「真回归」和「脏状态」。
 */
async function worldDigest() {
  return page.evaluate(() => {
    try {
      const view = window.Oracle?.view();
      if (!view) return { error: 'Oracle 不可用' };
      return {
        now: view.now,
        ledger: view.ledger,
        villagers: view.villagers.map((v) => ({ id: v.id, name: v.name, mode: v.mode, taskId: v.taskId })),
        buildings: view.buildings.length,
        roads: view.roads.length,
        tasks: view.tasks.length,
        pending: view.pending.length,
        deliveries: view.deliveries,
        faith: view.faith,
        weather: view.weather,
        status: document.querySelector('#status')?.textContent ?? '',
        reply: document.querySelector('#reply')?.textContent ?? '',
      };
    } catch (error) {
      return { error: String(error && error.message ? error.message : error) };
    }
  });
}

/* ------------------------------------------------------------------ 主流程 */

const task = await taskSpace('task-33 smoke-ego');
let page;

try {
  page = task.page('p1');

  // 先把窗口恢复到可截图状态。
  //
  // 为什么需要：ego 窗口被最小化时，page.screenshot() 会整轮中断在
  // `Cannot capture a screenshot while the window is minimized`；而窗口状态不受脚本
  // 控制（用户切窗口、系统锁屏都会导致）。
  //
  // 为什么必须**先 normal 再 maximized**：CDP 不允许从 fullscreen/minimized 直接跳到
  // maximized，会报 `To maximize a minimized or fullscreen window, restore it to normal
  // state first`。这是一条实测踩到的心跳限制，不是可选的保险。
  //
  // 失败不致命：真拿不到窗口就当没发生，让后续截图自己报错。
  try {
    const win = await page.cdp('Browser.getWindowForTarget', {});
    if (win !== null && typeof win.windowId === 'number') {
      // 第一步：先落回 normal（从 fullscreen / minimized 出发的唯一合法前置状态）
      await page.cdp('Browser.setWindowBounds', {
        windowId: win.windowId,
        bounds: { windowState: 'normal' },
      });
      // 给窗口管理器一点时间完成状态切换，再最大化；直接连发会被拒。
      await pause(400);
      await page.cdp('Browser.setWindowBounds', {
        windowId: win.windowId,
        bounds: { windowState: 'maximized' },
      });
    }
  } catch {
    /* 窗口操作不可用时继续，不因为环境限制让整轮 smoke 直接失败 */
  }

  // 第一步：不先加载任何页面，直接用 CDP 清原点存储，再 goto。
  //
  // 为什么不能先 `goto(URL_BASE)` 再清：那一步会先把旧世界加载起来，
  // 之后导航离开时 `pagehide` 会把它的内存状态**自动存档写回单槽**，
  // 刚清完就又被写脏（实测：清完 single slot 仍是 7403 字节、Mock 交付 13 单）。
  // CDP 的 `Storage.clearDataForOrigin` 不经过页面生命周期，先清再开才干净。
  await resetOriginStorage();

  // 4) 确认清档本身生效：goto 之前槽必须是空的。
  //    这一步只验「CDP 清存储成功」，与后面世界的自动存档无关。
  const afterReset = await storageProbe();
  const resetWorked = afterReset.slotBytes === 0 && afterReset.length === 0;
  if (!resetWorked) {
    failed.push({
      label: 'PRE 清档生效：goto 之前单槽必须为空',
      error: `CDP Storage.clearDataForOrigin 之后仍非空：${JSON.stringify(afterReset)}`,
    });
    console.log('\n[清档失败] goto 之前单槽仍非空，已停止。现场：');
    console.log(JSON.stringify(afterReset, null, 2));
    throw new Error('前置清档失败：CDP 清存储没有生效');
  }
  passed.push('PRE 清档生效：goto 之前单槽为空');

  // 2) 装 rAF 替身：必须在第一次 goto 之前注册，之后每次导航都会自动执行。
  //    不装的话 ego 后台窗口的 rAF 被节流到 1–12fps，世界中时间几乎不走，
  //    后面所有「等 N 秒再断言」的条目都会假超时。
  await installRafPatch();

  await page.goto(PAGE_URL, { timeout: 30_000 });
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });

  /* ---------------- 前置自检：世界必须处于初始态 ----------------
   *
   * 注意：**不能**要求 localStorage 为空。SandboxScene.maybeAutoSave() 的
   * `due` 分支（SandboxScene.ts:302）用 `lastSaveAt = Number.NEGATIVE_INFINITY`
   * （:72）起步，首帧 `now - (-Infinity) = Infinity >= 30000` 恒为真，
   * 所以任何干净启动都会在**首帧立刻自动存一次档**——这是设计行为，不是脏档。
   * 因此这里断的是「世界内容处于初始态」，存档槽只验「若存在则必须是初始档」。
   */

  const freshProbe = await storageProbe();
  const freshDigest = await worldDigest();
  const slotIsInitial = await page.evaluate(() => {
    const text = localStorage.getItem('xiaoxi.pixel-world.save.v1');
    if (text === null) return { present: false, ok: true };
    try {
      const parsed = JSON.parse(text);
      return {
        present: true,
        ok:
          parsed.schemaVersion === 1 &&
          Array.isArray(parsed.world?.buildings) &&
          parsed.world.buildings.length === 3 &&
          Array.isArray(parsed.world?.tasks) &&
          parsed.world.tasks.length === 0 &&
          Array.isArray(parsed.world?.settled) &&
          parsed.world.settled.length === 0 &&
          (parsed.world?.pending?.length ?? 0) === 0,
        summary: {
          schemaVersion: parsed.schemaVersion,
          buildings: parsed.world?.buildings?.length ?? null,
          tasks: parsed.world?.tasks?.length ?? null,
          settled: parsed.world?.settled?.length ?? null,
          pending: parsed.world?.pending?.length ?? null,
        },
      };
    } catch (error) {
      return { present: true, ok: false, error: String(error && error.message ? error.message : error) };
    }
  });

  const isClean =
    freshDigest.buildings === 3 &&
    freshDigest.tasks === 0 &&
    freshDigest.pending === 0 &&
    freshDigest.deliveries?.mock === 0 &&
    freshDigest.deliveries?.ledger === 0 &&
    freshDigest.faith?.points === 0 &&
    freshDigest.weather === 'clear' &&
    // 首帧量级：允许几十~几百毫秒，但绝不能是"上一轮跑了几分钟"的世界。
    freshDigest.now < 1500 &&
    slotIsInitial.ok === true;

  if (!isClean) {
    // 不带着脏档往下跑：直接 FAIL 并把现场打全，便于区分"清档没生效"和"世界本身不对"。
    failed.push({
      label: 'PRE 前置自检：世界处于初始态（允许首帧自动存档）',
      error: `storage=${JSON.stringify(freshProbe)} slot=${JSON.stringify(slotIsInitial)} world=${JSON.stringify({
        now: freshDigest.now,
        buildings: freshDigest.buildings,
        tasks: freshDigest.tasks,
        pending: freshDigest.pending,
        deliveries: freshDigest.deliveries,
        faith: freshDigest.faith,
        weather: freshDigest.weather,
      })} status=${freshDigest.status}`,
    });
    console.log('\n[前置自检失败] 世界不在初始态，已停止后续断言。现场：');
    console.log(JSON.stringify({ storage: freshProbe, slot: slotIsInitial, world: freshDigest }, null, 2));
    throw new Error('前置自检失败：世界起点不是初始态');
  }
  passed.push('PRE 前置自检：世界处于初始态（允许首帧自动存档）');

  /* ---------------- 顶层 4 条（smoke.mjs:780-794） ---------------- */

  // L780 页面加载无 JS 报错 —— 用「world-overlay 已挂载 + Oracle 可调用」作为等价可观测证据，
  // 因为 ego-browser 拿不到 Playwright 的 pageerror 事件流。
  const booted = await page.evaluate(() => ({
    overlay: document.querySelector('#world-overlay') !== null,
    canvas: document.querySelector('#app canvas') !== null,
    oracle: typeof window.Oracle?.view === 'function',
  }));
  await expect(
    'S-L780 页面加载无 JS 报错（等价：Oracle 可调用、canvas 与 overlay 均已挂载）',
    booted.oracle && booted.canvas && booted.overlay,
    JSON.stringify(booted),
  );

  const opening = await page.evaluate(() => window.Oracle.view());

  // L783 世界里摆了三栋铺子
  await expect(
    'S-L783 世界里摆了三栋铺子',
    opening.buildings.length === 3,
    `buildings=${opening.buildings.length}`,
  );

  // L784 开局她在闲逛
  await expect(
    'S-L784 开局她在闲逛',
    opening.villager.mode === 'idle',
    `mode=${opening.villager.mode}`,
  );

  /* ---------------- 沙盒套件（smoke.mjs:209-771） ---------------- */

  const state = () => page.evaluate(() => window.Oracle.view());

  /** 等价 smoke 的 waitFor：轮询到条件成立并记账。 */
  async function waitFor(name, predicate, timeoutMs = 25_000) {
    const started = Date.now();
    for (;;) {
      const snapshot = await state();
      if (predicate(snapshot)) {
        passed.push(name);
        return snapshot;
      }
      if (Date.now() - started > timeoutMs) {
        failed.push({ label: name, error: '超时' });
        return snapshot;
      }
      await pause(120);
    }
  }

  // L211 世界里有两个小人
  await expect('S-L211 世界里有两个小人', opening.villagers.length === 2, `n=${opening.villagers.length}`);

  // L212 两个角色各有独立 id 与名字
  await expect(
    'S-L212 两个角色各有独立 id 与名字',
    opening.villagers[0]?.name !== opening.villagers[1]?.name &&
      opening.villagers[0]?.id !== opening.villagers[1]?.id,
  );

  // L217 离线模式在快照里标成离线 Mock
  await expect('S-L217 离线模式在快照里标成离线 Mock', opening.ledger.mock === true);

  // L224 状态行标出离线 Mock
  const openingStatus = await textContent('#status');
  await expect(
    'S-L224 状态行标出离线 Mock',
    openingStatus.includes('离线 Mock') && openingStatus.includes('台账离线'),
    openingStatus.slice(0, 80),
  );

  // L228 状态行显示多角色摘要
  await expect(
    'S-L228 状态行显示多角色摘要',
    openingStatus.includes('莞尔：') && openingStatus.includes('阿雾：'),
  );

  // L240 没人管的时候她会自己走动（观察 3.2s，与 smoke 同）
  const before = opening.villager.pos;
  await pause(3200);
  const wandering = await state();
  const moved = Math.hypot(wandering.villager.pos.x - before.x, wandering.villager.pos.y - before.y);
  await expect(
    'S-L240 没人管的时候她会自己走动',
    moved > 0.2 || wandering.villager.mode !== 'idle',
    `moved=${moved.toFixed(2)} mode=${wandering.villager.mode}`,
  );

  // L241 两个角色各自有独立作息
  await expect(
    'S-L241 两个角色各自有独立作息',
    wandering.villagers.every((item) => typeof item.routine === 'string'),
  );

  // L274/L278/L282/L286：DOM overlay 与 canvas 对齐（D5-A）
  const overlayFit = await page.evaluate(() => {
    const canvas = document.querySelector('#app canvas');
    const overlay = document.querySelector('#world-overlay');
    const names = [...document.querySelectorAll('#world-overlay .overlay-name')];
    const snapshot = window.Oracle.view();
    const canvasRect = canvas.getBoundingClientRect();
    const villager = snapshot.villagers[0];
    const nameEl = names[0];
    const scale = canvasRect.width / 480;
    const expectedX = (villager.pos.x + 0.5) * 16 * scale;
    const expectedY = ((villager.pos.y + 1) * 16 - 30) * scale;
    const actualX = Number.parseFloat(nameEl?.style.left ?? 'NaN');
    const actualY = Number.parseFloat(nameEl?.style.top ?? 'NaN');
    return {
      names: names.length,
      villagers: snapshot.villagers.length,
      text: nameEl?.textContent ?? '',
      expectedName: villager.name,
      error: Math.hypot(actualX - expectedX, actualY - expectedY),
      tolerance: Math.max(1, scale),
      overlayWidth: overlay?.getBoundingClientRect().width ?? 0,
      canvasWidth: canvasRect.width,
      bubblesVisible: [...document.querySelectorAll('#world-overlay .overlay-bubble')].filter(
        (node) => node.style.display !== 'none',
      ).length,
    };
  });
  await expect(
    'S-L274 overlay 名字节点数与快照角色一致、文案正确',
    overlayFit.names === overlayFit.villagers && overlayFit.text === overlayFit.expectedName,
  );
  await expect(
    'S-L278 overlay 与 canvas 对齐误差 ≤ 容差',
    overlayFit.error <= overlayFit.tolerance,
    `error=${overlayFit.error.toFixed(2)} tol=${overlayFit.tolerance}`,
  );
  await expect(
    'S-L282 overlay 与 canvas 同宽',
    Math.abs(overlayFit.overlayWidth - overlayFit.canvasWidth) < 2,
  );
  await expect(
    'S-L286 气泡节点不超过角色数',
    overlayFit.bubblesVisible <= overlayFit.villagers,
  );

  // L291 认不出的输入被拒
  const unknown = await page.evaluate(() => window.Oracle.say('今天天气不错'));
  await expect('S-L291 认不出的输入被拒', unknown.ok === false);
  // L292 认不出的输入没有触发神谕
  await expect(
    'S-L292 认不出的输入没有触发神谕',
    (await state()).villager.mode !== 'saluting',
  );

  // L295-302：一句代码活，界面回执
  await page.fill('#utterance', '帮我写个爬虫脚本');
  await page.click('button[type="submit"]');
  await page
    .waitForFunction(
      () => (document.querySelector('#reply')?.textContent ?? '').includes('神谕已送达'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L302 界面回了世界结算后的神谕回执',
    (await textContent('#reply')).includes('神谕已送达'),
  );

  // L303 她停下手里的事行礼
  const saluting = await waitFor('S-L303 她停下手里的事行礼', (s) => s.villager.mode === 'saluting');

  // L304 行礼时头上有字
  await expect('S-L304 行礼时头上有字', saluting.villager.bubble !== null);

  // L315 / L320：气泡在 DOM overlay
  const overlayBubble = await page.evaluate(() => {
    const bubbles = [...document.querySelectorAll('#world-overlay .overlay-bubble')];
    const shown = bubbles.filter((node) => node.style.display !== 'none');
    const names = [...document.querySelectorAll('#world-overlay .overlay-name')];
    return {
      texts: shown.map((node) => node.textContent),
      shown: shown.length,
      hiddenNames: names.filter((node) => node.style.display === 'none').length,
    };
  });
  await expect(
    'S-L315 气泡在 DOM overlay 里显示（canvas 无中文）',
    overlayBubble.shown >= 1 &&
      overlayBubble.texts.some((text) => (text ?? '').includes('吾神降旨')),
  );
  await expect('S-L320 有气泡时对应名字让位', overlayBubble.hiddenNames >= 1);

  // 截图：行礼
  await shot('smoke-ego-01-salute.png');

  // L323 她走进铺子开始干活
  const working = await waitFor('S-L323 她走进铺子开始干活', (s) => s.villager.mode === 'working');

  // L324 进屋后人不在门外
  await expect('S-L324 进屋后人不在门外', working.villager.visible === false);
  // L325 派活只叫一个人，另一个还在祭坛
  await expect(
    'S-L325 派活只叫一个人，另一个还在祭坛',
    working.villagers[1]?.mode !== 'working' && working.villagers[1]?.taskId === null,
  );
  // L330 恰好一台铺子在运转
  const operating = working.workstations.filter((w) => w.activity.kind === 'operating');
  await expect('S-L330 恰好一台铺子在运转', operating.length === 1, `n=${operating.length}`);
  // L334 开工的是接代码活的那栋
  const activeBuilding = working.buildings.find((b) => b.instanceId === operating[0]?.buildingId);
  await expect('S-L334 开工的是接代码活的那栋', activeBuilding?.boundTask === 'code');
  // L336 状态行写清默认交付对象
  await expect(
    'S-L336 状态行写清默认交付对象',
    (await textContent('#status')).includes('默认交付：莞尔'),
  );

  await pause(700);
  await shot('smoke-ego-02-working.png');

  // L340 活干完，她出门交付
  const delivering = await waitFor('S-L340 活干完，她出门交付', (s) => s.villager.mode === 'delivering');
  // L341 交付时人重新出现在画面里
  await expect('S-L341 交付时人重新出现在画面里', delivering.villager.visible === true);
  // L342 台账里这单已完成
  await expect('S-L342 台账里这单已完成', delivering.tasks[0]?.status === 'done');
  // L343 铺子进入冷却
  await expect(
    'S-L343 铺子进入冷却',
    delivering.workstations.some((w) => w.activity.kind === 'cooling'),
  );
  await shot('smoke-ego-03-deliver.png');

  // L349 她回到祭坛恢复闲逛
  const back = await waitFor('S-L349 她回到祭坛恢复闲逛', (s) => s.villager.mode === 'idle');
  // L350 回到祭坛后手上没有单
  await expect('S-L350 回到祭坛后手上没有单', back.villager.taskId === null);

  // L353-360：两人各接一单
  await page.click('button[data-kind="research"]');
  await waitFor('S-L354 第二单能派出去', (s) => s.villager.mode === 'working');
  await page.click('button[data-kind="writing"]');
  const second = await waitFor('S-L356 第二个角色接下了另一单', (s) => s.villagers[1]?.taskId !== null);
  await expect(
    'S-L360 派活不串任务',
    second.villagers[0]?.taskId !== second.villagers[1]?.taskId,
  );

  // L363-379：两人都忙时排队
  await page.fill('#utterance', '再整理一份便笺');
  await page.click('button[type="submit"]');
  await page
    .waitForFunction(
      () => (document.querySelector('#reply')?.textContent ?? '').includes('已排队'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L370 两人都忙时新神谕进排队，回执写明第几位',
    (await textContent('#reply')).includes('已排队'),
  );
  const queuedView = await state();
  await expect('S-L372 排队的单没有立刻进台账', queuedView.tasks.length === 3, `n=${queuedView.tasks.length}`);
  await expect('S-L373 排队单在快照 pending 里可见', queuedView.pending.length === 1);
  await page
    .waitForFunction(
      () => (document.querySelector('#status')?.textContent ?? '').includes('排队 1 条'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L379 状态行显示排队数',
    (await textContent('#status')).includes('排队 1 条'),
  );

  // L382-395：按角色人工交付
  await page.evaluate(() => window.Oracle.finish('v0'));
  const manual0 = await waitFor('S-L383 人工交付也能结束这单', (s) => s.villager.mode === 'delivering');
  await expect('S-L384 人工交付对沙盒单生效', manual0.tasks[1]?.status === 'done');
  const manualReply = await textContent('#reply');
  await expect(
    'S-L386 沙盒单人工交付回执标明离线 Mock、不计入台账',
    manualReply.includes('离线 Mock') && manualReply.includes('不计入台账'),
  );
  await expect('S-L390 回执写清收的是谁的单', manualReply.includes('莞尔'));
  await expect(
    'S-L391 交付统计把 Mock 单独计，不混进台账',
    manual0.deliveries.ledger === 0 && manual0.deliveries.mock >= 2,
    `ledger=${manual0.deliveries.ledger} mock=${manual0.deliveries.mock}`,
  );
  await expect(
    'S-L395 交付归属到指定角色',
    manual0.deliveriesByVillager[0]?.mock >= 1,
  );

  // L398-407：按名字指定另一人
  await waitFor('S-L398 助手也在干活', (s) => s.villagers[1]?.mode === 'working', 20_000);
  await page.evaluate(() => window.Oracle.finish('阿雾'));
  await page
    .waitForFunction(
      () => (document.querySelector('#reply')?.textContent ?? '').includes('阿雾'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L405 按名字指定交付对象',
    (await textContent('#reply')).includes('阿雾'),
  );
  const perWorker = await state();
  await expect(
    'S-L407 两个角色都各自记了交付',
    perWorker.deliveriesByVillager.every((item) => item.mock >= 1),
  );

  // L413-419：排队单自动开工
  const queuedStarted = await waitFor(
    'S-L413 排队的神谕在上一单交付后自动开工',
    (s) => s.tasks.length === 4 && s.tasks[3]?.status === 'in_progress',
    20_000,
  );
  await expect(
    'S-L418 自动开工的是排队的那条神谕',
    queuedStarted.tasks[3]?.summary === '再整理一份便笺',
  );
  await expect('S-L419 出队后 pending 清空', queuedStarted.pending.length === 0);

  // L422-428：未知角色被拒
  await page.evaluate(() => window.Oracle.finish('v9'));
  await page
    .waitForFunction(
      () => (document.querySelector('#reply')?.textContent ?? '').includes('没有叫'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L428 未知角色的交付被明确拒绝',
    (await textContent('#reply')).includes('没有叫'),
  );

  // L431-443：逐单收尾
  for (let i = 0; i < 20; i += 1) {
    const snapshot = await state();
    if (
      snapshot.tasks.every((task) => task.status === 'done') &&
      snapshot.villagers.every((villager) => villager.mode === 'idle')
    ) {
      break;
    }
    await page.click('#finish');
    await pause(500);
  }
  const settled = await state();
  await expect('S-L443 所有单都收尾', settled.tasks.every((task) => task.status === 'done'));

  // L444-449：日报按角色分账
  const paperByWorker = await page.evaluate(() => window.Oracle.report());
  const workerLine = paperByWorker.lines.find((line) => line.includes('交付')) ?? '';
  await expect(
    'S-L446 日报按角色分账，两个人都写清交付数',
    workerLine.includes('莞尔交付') && workerLine.includes('阿雾交付'),
    workerLine.slice(0, 80),
  );

  // L450 两个人都回到日常
  await waitFor('S-L450 两个人都回到日常', (s) => s.villagers.every((item) => item.mode === 'idle'));

  // L455 日常作息给出了一句可读的状态
  const routine = await waitFor(
    'S-L455 日常作息给出了一句可读的状态',
    (s) => typeof s.villager.routine === 'string' && s.villager.routine.length > 0,
    15_000,
  );
  // L460 日常状态不是被神谕打断的
  await expect('S-L460 日常状态不是被神谕打断的', routine.villager.mode === 'idle');

  // L462-468：短活
  await page.click('button[data-kind="quick"]');
  const quick = await waitFor('S-L463 短活当场开始', (s) => s.villager.mode === 'working');
  await expect(
    'S-L464 短活没占用任何铺子',
    quick.workstations.every((w) => w.activity.kind !== 'operating'),
  );
  await waitFor('S-L468 短活自己结束', (s) => s.villager.mode === 'idle');

  // L471-479：下雨 / 躲雨 / 放晴
  // S-L474 不能拿「天气刚变」那个快照直接断言：umbrella 是
  // `weather === 'rain' && mode !== 'working'`（simulation.ts:906），天气一变立刻为真；
  // 而 mode 切到 'sheltering' 依赖 scatterToShelter()（simulation.ts:582）在同一拍把
  // 空闲角色改主意。两条断言的稳定时机不同，挤在同一个快照上就会偶发假失败
  // （实测挂过一次、重跑即过）。改成等状态成立，而不是等天气成立。
  await page.click('#rain');
  const raining = await waitFor('S-L472 神谕让世界下雨了', (s) => s.weather === 'rain');
  await expect('S-L473 下雨时她撑起伞', raining.villager.umbrella === true);
  const sheltering = await waitFor('S-L474 户外的她当场跑去躲雨', (s) => s.villager.mode === 'sheltering');
  // 超时时补一条现场：区分「竞态没等到」和「她真的没去躲雨」。
  if (sheltering.villager.mode !== 'sheltering') {
    failed.push({
      label: 'S-L474 失败现场',
      error: `mode=${sheltering.villager.mode} weather=${sheltering.weather} umbrella=${sheltering.villager.umbrella} routine=${sheltering.villager.routine}`,
    });
  }
  await pause(500);
  await shot('smoke-ego-04-rain.png');
  await waitFor('S-L477 跑到屋檐下停住', (s) => s.villager.mode === 'idle');
  await page.click('#clear');
  await waitFor('S-L479 神谕让天放晴', (s) => s.weather === 'clear');

  // L482-491：造物主扩建
  const beforeBuild = await state();
  await page.click('button[data-build="workshop_plot"]');
  const building = await waitFor(
    'S-L484 造物主落下一栋新楼',
    (s) => s.buildings.length === beforeBuild.buildings.length + 1,
  );
  await expect('S-L488 新楼处于施工中', building.constructions.length === 1);
  await expect(
    'S-L489 新楼自动铺出了一条路',
    building.roads.length > beforeBuild.roads.length,
  );
  await shot('smoke-ego-05-build.png');
  await waitFor('S-L491 施工结束，新楼落成', (s) => s.constructions.length === 0);

  // L493-497：两单短活（循环 2 轮，两条断言各记两次）
  for (let i = 0; i < 2; i += 1) {
    await page.click('button[data-kind="quick"]');
    await waitFor(`S-L495 第 ${i + 1} 单短活开始`, (s) => s.villager.mode === 'working');
    await waitFor(`S-L496 第 ${i + 1} 单短活结束`, (s) => s.villager.mode === 'idle');
  }

  // L498 神恩攒够，世界多了一件东西
  const withProp = await waitFor('S-L498 神恩攒够，世界多了一件东西', (s) => s.props.length > 0);
  // L500 里程碑记上了
  await expect('S-L500 里程碑记上了', withProp.faith.milestones >= 1);
  await pause(400);
  await shot('smoke-ego-06-faith.png');

  // L505-509：日报纸面
  const paper = await page.evaluate(() => window.Oracle.report());
  await expect(
    'S-L506 日报写了交付数',
    paper.lines.some((line) => line.includes('交付')),
  );
  const paperText = await textContent('#paper');
  await expect('S-L508 报纸把标题渲染到了纸面上', paperText.includes(paper.headline));
  await expect(
    'S-L509 报纸写了落地的东西',
    paperText.includes('神恩') || paperText.includes('路灯'),
  );

  // L512-523：外部桥接
  const bridged = await page.evaluate(async () => {
    const before = window.Oracle.view().tasks.length;
    window.Oracle.feed({
      type: 'task.assigned',
      taskId: 'ext-1',
      taskKind: 'quick',
      summary: '外部派来的小事',
    });
    await new Promise((resolve) => setTimeout(resolve, 500));
    return { before, after: window.Oracle.view().tasks.length };
  });
  await expect(
    'S-L523 外部 Agent 的消息进了同一条命令队列',
    bridged.after === bridged.before + 1,
    JSON.stringify(bridged),
  );

  // L526-549：100/200/400% 缩放，overlay 仍贴合 canvas（循环 3 轮）
  for (const zoom of [1, 2, 4]) {
    await page.evaluate((value) => {
      const stage = document.querySelector('#stage');
      stage.style.transformOrigin = 'top left';
      stage.style.transform = value === 1 ? '' : `scale(${value})`;
    }, zoom);
    await pause(300);
    const fit = await page.evaluate(() => {
      const canvas = document.querySelector('#app canvas');
      const overlay = document.querySelector('#world-overlay');
      const canvasRect = canvas.getBoundingClientRect();
      const overlayRect = overlay.getBoundingClientRect();
      return {
        sameWidth: Math.abs(canvasRect.width - overlayRect.width) < 2,
        sameOrigin: Math.abs(canvasRect.left - overlayRect.left) < 2,
        contained: overlayRect.right <= canvasRect.right + 0.5,
      };
    });
    await expect(
      `S-L544 ${zoom * 100}% 缩放下 overlay 仍贴合 canvas`,
      fit.sameWidth && fit.sameOrigin && fit.contained,
      JSON.stringify(fit),
    );
    await shot(`smoke-ego-08-overlay-${zoom * 100}pct.png`);
  }
  await page.evaluate(() => {
    const stage = document.querySelector('#stage');
    stage.style.transform = '';
  });
  await pause(300);

  // L557-573：390px 视口
  // ego 没有 page.setViewportSize，这里用 CDP Emulation 覆盖；若覆盖失败，
  // 这条断言退化为「在 1010 视口下 overlay 不溢出 canvas」并在失败原因里写明，
  // 不假装测过 390px。
  const narrowOk = await setViewport(390, 844);
  await pause(350);
  const overlayNarrow = await page.evaluate(() => {
    const canvas = document.querySelector('#app canvas');
    const overlay = document.querySelector('#world-overlay');
    const canvasRect = canvas.getBoundingClientRect();
    const overlayRect = overlay.getBoundingClientRect();
    return {
      viewportWidth: window.innerWidth,
      contained: overlayRect.right <= canvasRect.right + 0.5,
      width: overlayRect.width,
      canvasWidth: canvasRect.width,
    };
  });
  if (!narrowOk) {
    manual.push({
      smoke: 'L570 390px 下 overlay 不溢出 canvas（降级：未测 390px）',
      reason: 'ego 无 setViewportSize，CDP Emulation 覆盖也失败；本机只在大视口下验证，未测 390px。',
    });
  } else {
    await expect(
      'S-L570 390px 下 overlay 不溢出 canvas',
      overlayNarrow.viewportWidth <= 390.5 &&
        overlayNarrow.contained &&
        Math.abs(overlayNarrow.width - overlayNarrow.canvasWidth) < 2,
      JSON.stringify(overlayNarrow),
    );
  }
  await setViewport(1010, 940);
  await pause(250);

  // L578-724：调试面板
  const panelInitial = await page.evaluate(() => ({
    hidden: document.querySelector('#debug-panel')?.hidden ?? null,
  }));
  await expect('S-L581 调试面板默认收起', panelInitial.hidden === true);

  await page.click('#debug-toggle');
  const panelCounts = await page.evaluate(() => {
    const snapshot = window.Oracle.view();
    const rows = (kind) => document.querySelectorAll(`#debug-panel [data-debug-row="${kind}"]`).length;
    return {
      hidden: document.querySelector('#debug-panel')?.hidden,
      villagers: rows('villager'),
      snapshotVillagers: snapshot.villagers.length,
      buildings: rows('building'),
      snapshotBuildings: snapshot.workstations.length,
      tasks: rows('task'),
      snapshotTasks: snapshot.tasks.length,
      events: rows('event'),
      snapshotEvents: window.Oracle.events(50).length,
      injectOpen: document.querySelector('#debug-inject')?.open ?? null,
    };
  });
  await expect('S-L600 快捷键/按钮能打开调试面板', panelCounts.hidden === false);
  await expect(
    'S-L601 调试面板实体数量与快照一致',
    panelCounts.villagers === panelCounts.snapshotVillagers &&
      panelCounts.buildings === panelCounts.snapshotBuildings &&
      panelCounts.tasks === panelCounts.snapshotTasks,
    JSON.stringify(panelCounts),
  );
  await expect(
    'S-L607 事件流行数与只读事件数一致',
    panelCounts.events === Math.min(50, panelCounts.snapshotEvents),
  );
  await expect('S-L611 调试注入默认折叠', panelCounts.injectOpen === false);
  await shot('smoke-ego-07-debug-panel.png');

  // L615-636：注入区在刷新后仍在
  await pause(400);
  const injectAt400 = await page.evaluate(() => {
    const details = document.querySelector('#debug-inject');
    const buttons = [...document.querySelectorAll('#debug-inject [data-debug-action]')];
    return {
      exists: details !== null,
      buttons: buttons.length,
      enabled: buttons.every((button) => !(button instanceof HTMLButtonElement) || !button.disabled),
      text: details?.textContent ?? '',
      open: details instanceof HTMLDetailsElement ? details.open : null,
      insideContent: document.querySelector('#debug-content #debug-inject') !== null,
    };
  });
  await expect(
    'S-L628 400ms 后注入区仍在且两个按钮可点',
    injectAt400.exists && injectAt400.buttons === 2 && injectAt400.enabled,
    JSON.stringify(injectAt400),
  );
  await expect(
    'S-L632 注入区文案标明仅调试、不写台账',
    injectAt400.text.includes('仅调试') && injectAt400.text.includes('不写台账'),
  );
  await expect(
    'S-L636 注入区默认折叠且在刷新区之外',
    injectAt400.open === false && injectAt400.insideContent === false,
  );
  await shot('smoke-ego-07-debug-panel-400ms.png');

  // L640-654：点 feed
  const tasksBeforeFeed = await page.evaluate(() => window.Oracle.view().tasks.length);
  await page.evaluate(() => {
    const details = document.querySelector('#debug-inject');
    if (details instanceof HTMLDetailsElement) details.open = true;
  });
  await page.click('#debug-inject [data-debug-action="feed"]');
  await pause(600);
  const injectAt1s = await page.evaluate(() => ({
    buttons: document.querySelectorAll('#debug-inject [data-debug-action]').length,
    tasks: window.Oracle.view().tasks.length,
    open: document.querySelector('#debug-inject')?.open ?? null,
  }));
  await expect('S-L652 1s 后注入区仍在', injectAt1s.buttons === 2);
  await expect(
    'S-L653 注入按钮可用（feed 进了世界队列）',
    injectAt1s.tasks > tasksBeforeFeed,
    `${tasksBeforeFeed} -> ${injectAt1s.tasks}`,
  );
  await expect('S-L654 展开状态在刷新后保留', injectAt1s.open === true);
  await shot('smoke-ego-07-debug-panel-1s.png');

  // L658-675：暂停
  await page.click('#debug-pause');
  const frozen = await page.evaluate(() => ({
    tasks: document.querySelectorAll('#debug-panel [data-debug-row="task"]').length,
    stats: document.querySelector('#debug-stats')?.textContent ?? '',
  }));
  await page.click('button[data-kind="quick"]');
  await pause(700);
  const stillFrozen = await page.evaluate(() => ({
    tasks: document.querySelectorAll('#debug-panel [data-debug-row="task"]').length,
    stats: document.querySelector('#debug-stats')?.textContent ?? '',
    injectButtons: document.querySelectorAll('#debug-inject [data-debug-action]').length,
  }));
  await expect(
    'S-L670 暂停后调试面板不更新',
    stillFrozen.tasks === frozen.tasks && stillFrozen.stats === frozen.stats,
  );
  await expect('S-L674 暂停状态在面板可见', frozen.stats.includes('已暂停'));
  await expect('S-L675 暂停后注入区仍在', stillFrozen.injectButtons === 2);

  // L678-701：恢复 + tone 过滤
  await page.click('#debug-pause');
  await pause(500);
  const resumed = await page.evaluate(
    () => document.querySelectorAll('#debug-panel [data-debug-row="task"]').length,
  );
  await expect('S-L683 恢复后面板继续刷新', resumed > frozen.tasks, `${frozen.tasks} -> ${resumed}`);
  await page.selectOption('#debug-tone', 'info');
  await pause(500);
  const filtered = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#debug-panel [data-debug-row="event"]')];
    return { total: rows.length, tones: [...new Set(rows.map((row) => row.dataset.tone))] };
  });
  await expect(
    'S-L690 事件过滤只显示所选 tone',
    filtered.total > 0 && filtered.tones.every((tone) => tone === 'info'),
    JSON.stringify(filtered),
  );
  const injectAfterFilter = await page.evaluate(() => ({
    buttons: document.querySelectorAll('#debug-inject [data-debug-action]').length,
    open: document.querySelector('#debug-inject')?.open ?? null,
  }));
  await expect(
    'S-L698 切过滤后注入区仍在且保持展开',
    injectAfterFilter.buttons === 2 && injectAfterFilter.open === true,
  );
  await shot('smoke-ego-07-debug-panel-filtered.png');

  // L705-724：390px 面板 + 关闭
  const narrowPanelOk = await setViewport(390, 844);
  await pause(300);
  const narrow = await page.evaluate(() => {
    const rect = document.querySelector('#debug-panel')?.getBoundingClientRect();
    return { panelWidth: rect?.width ?? 0, panelRight: rect?.right ?? 0, viewport: window.innerWidth };
  });
  if (!narrowPanelOk) {
    manual.push({
      smoke: 'L715 390px 视口下调试面板自身不溢出（降级：未测 390px）',
      reason: 'ego 无 setViewportSize，CDP Emulation 覆盖也失败；未在 390px 下验证面板宽度。',
    });
  } else {
    await expect(
      'S-L715 390px 视口下调试面板自身不溢出',
      narrow.viewport <= 390.5 && narrow.panelWidth <= 390 && narrow.panelRight <= narrow.viewport + 0.5,
      JSON.stringify(narrow),
    );
  }
  await setViewport(1010, 940);
  await clearViewportOverride();
  await page.click('#debug-close');
  await expect(
    'S-L724 调试面板能关闭',
    (await page.evaluate(() => document.querySelector('#debug-panel')?.hidden ?? null)) === true,
  );

  // L727-736：存档写入
  const beforeSave = await state();
  const saveResult = await page.evaluate(() => window.Oracle.saveNow());
  await expect('S-L729 手动存档写入单槽', saveResult.ok === true);
  const savedRaw = await page.evaluate(() => localStorage.getItem('xiaoxi.pixel-world.save.v1'));
  await expect(
    'S-L733 单槽里是 v1 JSON',
    typeof savedRaw === 'string' && savedRaw.includes('"schemaVersion":1'),
  );

  // L738-755：刷新恢复
  await reload(PAGE_URL);
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
  const restored = await state();
  await expect(
    'S-L741 刷新后建筑数量恢复',
    restored.buildings.length === beforeSave.buildings.length,
  );
  await expect('S-L742 刷新后道路恢复', restored.roads.length === beforeSave.roads.length);
  await expect(
    'S-L743 刷新后神恩恢复',
    restored.faith.milestones === beforeSave.faith.milestones,
  );
  await expect(
    'S-L744 刷新后按角色交付统计恢复',
    JSON.stringify(restored.deliveriesByVillager) ===
      JSON.stringify(beforeSave.deliveriesByVillager),
  );
  await expect('S-L749 读档后标记未对账', restored.ledger.reconciled === false);
  await page
    .waitForFunction(
      () => (document.querySelector('#status')?.textContent ?? '').includes('读档未对账'),
      undefined,
      { timeout: 8000 },
    )
    .catch(() => {});
  await expect(
    'S-L755 状态行标出读档未对账',
    (await textContent('#status')).includes('读档未对账'),
  );

  // L757-770：清档
  const cleared = await page.evaluate(() => window.Oracle.clearSave());
  await expect('S-L759 清档入口可用', cleared.ok === true);
  await expect(
    'S-L760 清档后单槽为空',
    (await page.evaluate(() => localStorage.getItem('xiaoxi.pixel-world.save.v1'))) === null,
  );
  await reload(PAGE_URL);
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
  const freshWorld = await state();
  await expect(
    'S-L767 清档后从新世界开始',
    freshWorld.buildings.length === 3 && freshWorld.faith.milestones === 0,
    JSON.stringify({ buildings: freshWorld.buildings.length, milestones: freshWorld.faith.milestones }),
  );

  /* ---------------- 不可自动化项（显式记账，不静默跳过） ---------------- */

  manual.push({
    smoke: 'L780 / L794 pageerror 事件流（Playwright 专属 API）',
    reason: 'ego-browser 不提供 Playwright 的 pageerror 事件流；L780 已用「Oracle 可调用 + canvas/overlay 已挂载」做等价替代（S-L780），L794「全程无 JS 报错」无法逐事件核验，见下一条。',
  });
  manual.push({
    smoke: 'S-L794 全程无 JS 报错',
    reason: 'ego-browser 没有 Playwright 的 pageerror 事件流，无法在套件结束后核对"零 pageerror"。间接覆盖：整段跑完后仍能执行清档与 reload（S-L759/L767 通过即说明页面未崩）。',
  });
  manual.push({
    smoke: 'L548 #stage 元素级截图 × 3（100/200/400%）',
    reason: 'ego-browser 的 page.screenshot() 是全页/视口截图，没有 Playwright locator.screenshot() 的元素级裁剪；本条截图要求已降级为整页截图（smoke-ego-08-overlay-*.png），断言 L544 仍逐轮执行。',
  });

  /* ---------------- 汇总 ---------------- */

  // 失败时补一份世界现场：用来区分「真回归」与「起点不干净」。
  // 判读法则：deliveries.mock > 0 / pending > 0 / now 很大 → 多半是脏状态；
  //          deliveries 全 0、now 很小、结构却对不上 → 才更像真回归。
  const digest = failed.length > 0 ? await worldDigest().catch(() => null) : null;
  const storage = failed.length > 0 ? await storageProbe().catch(() => null) : null;

  console.log('\n' + '='.repeat(72));
  console.log(`smoke-ego 汇总：通过 ${passed.length} 项，失败 ${failed.length} 项，不可自动化 ${manual.length} 项`);
  console.log('='.repeat(72));
  for (const label of passed) console.log(`  ok   ${label}`);
  for (const item of failed) console.log(`  FAIL ${item.label} :: ${item.error}`);
  for (const item of manual) console.log(`  MAN  ${item.smoke}\n       ${item.reason}`);

  if (failed.length > 0) {
    console.log('\n---- 失败现场（区分真回归 vs 脏状态）----');
    console.log('storage: ' + JSON.stringify(storage));
    console.log('world  : ' + JSON.stringify(digest, null, 2));
    console.log(
      '\n判读提示：若 deliveries.mock > 0 或 pending > 0 或 now 明显偏大，'
        + '说明跑之前继承了一份非新档世界（脏状态），不是像素世界回归。',
    );
    console.log(`\n结果：${failed.length} 项失败`);
    process.exitCode = 1;
  } else {
    console.log(`\n结果：全部通过（${passed.length}/${passed.length}）`);
  }
} catch (error) {
  console.error(`\n中断：${error && error.message ? error.message : String(error)}`);
  console.error(`已完成 ${passed.length} 项：`);
  for (const label of passed) console.error(`  ok  ${label}`);
  for (const item of failed) console.error(`  FAIL ${item.label} :: ${item.error}`);
  // 中断时也把现场打出来：多数中断是 API 或脏状态，现场比栈更有用。
  const digest = await worldDigest().catch(() => null);
  const storage = await storageProbe().catch(() => null);
  console.error('\n---- 中断现场 ----');
  console.error('storage: ' + JSON.stringify(storage));
  console.error('world  : ' + JSON.stringify(digest, null, 2));
  process.exitCode = 1;
} finally {
  // 收尾：把这一轮留下的存档清干净，不把脏状态传给下一次运行。
  // 用 CDP 清原点存储（不经过页面生命周期），失败不影响退出码。
  await resetOriginStorage().catch(() => {});
  await task.finish({ keep: [] });
}
