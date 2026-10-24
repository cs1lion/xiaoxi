#!/usr/bin/env node
/**
 * 调色板截图（D6-A / task-28 P2-2）：晴 / 雨 / 夜三态，元素截图，可复现。
 *
 * 为什么要外部 Chrome：
 *   DSH 沙箱禁止子进程命名管道，Playwright 自己 launch 会 `spawn EPERM`；本脚本只做
 *   `connectOverCDP`，浏览器必须由外面先起好（见 pixel-world/docs/E2E_PALETTE.md）。
 *
 * 为什么不用 canvas.toDataURL()：
 *   Phaser AUTO 走 WebGL、未开 preserveDrawingBuffer，`toDataURL()` / `drawImage()`
 *   拿到的都是全黑。所以一律用元素截图；再用 CSS 把 canvas 缩到目标尺寸——
 *   后备缓冲本来就是 480×320，CSS 480×320 即 1:1 出帧；1920×1280 配合
 *   `image-rendering: pixelated` 就是精确 400% 最近邻。全程没有 PNG 重采样。
 *
 * 可复现的三个锁：
 *   1) `window.__RAIN_SEED__` → weather.ts 的确定性种子（同种子同雨滴序列）；
 *   2) `Math.random` 用同一 LCG 覆盖（世界/仿真里的随机也定住）；
 *   3) rAF 帧计数 + 固定时间戳：冻结在第 STOP_AT 帧，雨在 RAIN_CLICK_AT 帧按下。
 *   截图前先开一张热身页，丢掉首帧着色器/合成器的预热差异。
 *
 * 用法与前置见 pixel-world/docs/E2E_PALETTE.md。
 * 环境变量：CDP_URL / PIXEL_WORLD_URL / PALETTE_SEED。
 * 产物：eval/screens/palette-{sunny,rain,night}-{100,canvas,native-480x320,400pct}.png
 *
 * 三种模式：
 *   （默认）        晴/雨/夜调色板三态
 *   --anim          帧动画证据（idle/walk 各两帧 + manifest）
 *   --scenario slice 切片评审：晴/雨/夜同构图，先扩建第 4 栋 workshop，每态 5 档尺寸
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import process from 'node:process';

const CDP_URL = process.env.CDP_URL ?? 'http://127.0.0.1:9333';
const PAGE_URL = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/?ledger=off&debug=1';

/**
 * 固定时相后再截图，否则结果跟着本地钟点漂移、无法复现。
 *
 * `?night=1` 是旧入口，现在等价于 `?time=night`；新脚本一律用 `?time=`。
 * 白天两档必须也显式指定，不能留空——留空就是「此刻真实时间」。
 */
function withPhase(url, phase) {
  if (phase === null) return url;
  const next = new URL(url);
  next.searchParams.set('time', phase);
  return next.toString();
}

function withNight(url, night) {
  return withPhase(url, night ? 'night' : null);
}
const OUT = 'eval/screens';
const SEED = Number(process.env.PALETTE_SEED ?? 20261019);
const FRAME_MS = 1000 / 60;
const STOP_AT = 240;
const RAIN_CLICK_AT = 30;
const TIMEOUT = 30000;

/** 页面初始化脚本：清存档、注入种子、把 rAF 变成可冻结的确定步进。 */
function initScript({ seed, frameMs, stopAt, rainAt, rain, actionAt, action }) {
  try {
    localStorage.clear();
  } catch {
    /* 隐私模式等场景忽略 */
  }
  window.__RAIN_SEED__ = seed;
  let rng = seed >>> 0;
  Math.random = () => {
    rng = (Math.imul(rng, 1664525) + 1013904223) >>> 0;
    return rng / 4294967296;
  };
  const state = { frame: 0, done: false };
  window.__CAPTURE_STATE__ = state;
  const realRaf = window.requestAnimationFrame.bind(window);
  const now = () => state.frame * frameMs;
  performance.now = now;
  window.requestAnimationFrame = (cb) => {
    if (state.frame >= stopAt) {
      state.done = true;
      return 0;
    }
    return realRaf(() => {
      state.frame += 1;
      // 雨在世界跑起来之后按固定帧号按下，避免“点击落在哪一帧”引入差异。
      if (rain && state.frame === rainAt) document.querySelector('#rain')?.click();
      // 动画截图：按固定帧号派活/触发动作，同样不依赖点击时机。
      if (action !== undefined && state.frame === actionAt) {
        document.querySelector(action)?.click();
      }
      cb(now());
    });
  };
}

const sha = (buffer) => createHash('sha256').update(buffer).digest('hex').slice(0, 16);

async function probeCdp() {
  try {
    const response = await fetch(`${CDP_URL}/json/version`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const info = await response.json();
    return info.Browser ?? 'unknown';
  } catch (error) {
    console.error(`无法连接 CDP 端点 ${CDP_URL}：${error.message}`);
    console.error('这个脚本不会自己 launch Chrome（沙箱会 EPERM）。请先手动起浏览器：');
    console.error(
      '  chrome-headless-shell --headless --remote-debugging-port=9333 --no-sandbox ' +
        '--enable-unsafe-swiftshader --window-size=1010,1400 about:blank',
    );
    console.error('或设置 CDP_URL 指向已经在跑的浏览器。详见 pixel-world/docs/E2E_PALETTE.md。');
    process.exit(1);
  }
}

async function save(file, buffer) {
  await writeFile(file, buffer);
  return sha(buffer);
}

async function shoot(context, name, { rain = false, night = false }) {
  const page = await context.newPage();
  // 视口比页面高：100% 全图用普通截图即可，避免 fullPage 的 captureBeyondViewport 触发重排重渲（会让雨层多走一帧）。
  await page.setViewportSize({ width: 1010, height: 1400 });
  await page.addInitScript(initScript, {
    seed: SEED,
    frameMs: FRAME_MS,
    stopAt: STOP_AT,
    rainAt: RAIN_CLICK_AT,
    rain,
  });
  await page.goto(withPhase(PAGE_URL, night ? 'night' : 'noon'), { waitUntil: 'load' });
  await page.waitForFunction(() => window.__CAPTURE_STATE__ !== undefined, null, { timeout: TIMEOUT });
  await page.waitForFunction((stop) => window.__CAPTURE_STATE__.frame >= stop, STOP_AT, { timeout: TIMEOUT });
  await page.waitForTimeout(300);

  const weather = await page.evaluate(() => window.Oracle.view().weather);
  if (rain !== (weather === 'rain')) {
    await page.close();
    throw new Error(`${name}: 天气状态不符（期望 ${rain ? 'rain' : 'clear'}，实际 ${weather}）`);
  }

  const hashes = {};

  // 100%：带页头的整页，给人看“窗口里其实长这样”。
  // 用高视口 + 普通截图，而不是 fullPage——captureBeyondViewport 会触发重排/重渲，
  // 让冻结的雨层按真实时间多走一帧，复跑就不再字节一致。
  await page.screenshot({ path: `${OUT}/${name}-100.png` });
  hashes['100'] = sha(await readFile(`${OUT}/${name}-100.png`));

  // 去掉页头/内边距，让 canvas 落在整数像素上，元素截图才是精确 960×640。
  await page.addStyleTag({
    content:
      'header{display:none!important}body{padding:0!important;align-items:flex-start!important;gap:0!important}',
  });
  await page.waitForTimeout(100);
  hashes.canvas = await save(`${OUT}/${name}-canvas.png`, await page.locator('canvas').screenshot());

  // 原生 480×320：CSS 缩到后备缓冲尺寸，1:1 出帧。
  await page.addStyleTag({ content: 'canvas{width:480px!important;height:320px!important}' });
  await page.waitForTimeout(100);
  hashes['native-480x320'] = await save(
    `${OUT}/${name}-native-480x320.png`,
    await page.locator('canvas').screenshot(),
  );

  // 400%：CSS 放大 4×，image-rendering:pixelated 保证最近邻。
  await page.addStyleTag({ content: 'canvas{width:1920px!important;height:1280px!important}' });
  await page.waitForTimeout(100);
  hashes['400pct'] = await save(`${OUT}/${name}-400pct.png`, await page.locator('canvas').screenshot());

  const frame = await page.evaluate(() => window.__CAPTURE_STATE__.frame);
  await page.close();
  return { name, weather, frame, hashes };
}

const label = await probeCdp();
console.log(`CDP：${CDP_URL}（${label}）`);
console.log(`页面：${PAGE_URL}`);
console.log(`种子：${SEED}；冻结帧：${STOP_AT}；雨点击帧：${RAIN_CLICK_AT}；帧步长：${FRAME_MS.toFixed(3)}ms`);
console.log('方法：元素截图（非 toDataURL）；原生 = CSS 480×320 的 1:1；400% = CSS 1920×1280 + pixelated');

/* ---------------------------------------------------------------- 动画证据（task-29） */
/* 证据要回答的是「12fps 采样在动」，不是「世界在动」。所以：
 *   - 冻结条件用 simulation 时间（`Oracle.view().now`），不是 rAF 帧计数；
 *   - a/b 两帧只差一个 12fps 帧（≈83.3ms），帧号写进 manifest；
 *   - idle 选「同一取整像素」的两帧（位置没变，差只可能来自姿态）；
 *   - walk 做不到同位置，所以用「以 v0 取整像素为中心的裁剪图」对比，位移从对比里扣掉；
 *   - 证据摘要同时落盘 `eval/screens/anim-manifest.txt`，不只打 stdout。
 */

const ANIM_TILE = 16;
const ANIM_FPS = 12;
const ANIM_FRAME_MS = 1000 / ANIM_FPS;
const ANIM_CROP = { halfW: 26, halfH: 34, width: 52, height: 40 };
const WALK_MODES = new Set(['toWork', 'toAltar', 'sheltering']);

function animInitScript({ seed, pendingClicks }) {
  try {
    localStorage.clear();
  } catch {
    /* 忽略 */
  }
  window.__RAIN_SEED__ = seed;
  let rng = seed >>> 0;
  Math.random = () => {
    rng = (Math.imul(rng, 1664525) + 1013904223) >>> 0;
    return rng / 4294967296;
  };
  const state = { frames: 0, frozen: false };
  window.__ANIM_STATE__ = state;
  const clicks = (pendingClicks ?? []).map((item) => ({ ...item, done: false }));
  window.__PENDING_CLICKS__ = clicks;
  const realRaf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (cb) => {
    if (state.frozen) return 0;
    return realRaf((t) => {
      state.frames += 1;
      // 按 simulation 时间在页面内点击：从 Node 侧轮询再点会差一个模拟步，
      // 雨天相位就对不上了（复跑 sha 不一致）。
      const view = globalThis.Oracle?.view?.();
      if (view !== undefined && view !== null) {
        for (const click of clicks) {
          if (!click.done && view.now >= click.atNow) {
            document.querySelector(click.selector)?.click();
            click.done = true;
          }
        }
      }
      cb(t);
    });
  };
}

const pixelOf = (value) => {
  const rounded = Math.round(value);
  return rounded === 0 ? 0 : rounded;
};

/** 快照 → 帧号/像素坐标（与 villager-frames.ts 同一公式；脚本不 import TS）。 */
function poseOf(info) {
  const frameCount = WALK_MODES.has(info.mode) ? 4 : 2;
  const step = Math.floor(info.now / ANIM_FRAME_MS);
  return {
    ...info,
    step,
    frameCount,
    frame: ((step % frameCount) + frameCount) % frameCount,
    px: pixelOf((info.pos.x + 0.5) * ANIM_TILE),
    py: pixelOf((info.pos.y + 1) * ANIM_TILE),
  };
}

async function openWorld(context, { actionAt } = {}) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 1010, height: 1400 });
  await page.addInitScript(animInitScript, { seed: SEED });
  await page.goto(PAGE_URL, { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: TIMEOUT });
  if (actionAt !== undefined) {
    await page.waitForFunction((t) => window.Oracle.view().now >= t, actionAt, {
      timeout: TIMEOUT,
      polling: 16,
    });
    await page.evaluate(() => document.querySelector('[data-kind="code"]')?.click());
  }
  return page;
}

async function freezeAt(page, target) {
  await page.waitForFunction((t) => window.Oracle.view().now >= t, target, {
    timeout: TIMEOUT,
    polling: 16,
  });
  await page.evaluate(() => {
    window.__ANIM_STATE__.frozen = true;
  });
  await page.waitForTimeout(120);
  return page.evaluate(() => {
    const view = window.Oracle.view();
    const villager = view.villagers[0];
    return {
      now: view.now,
      mode: villager.mode,
      pos: { x: villager.pos.x, y: villager.pos.y },
      rafFrames: window.__ANIM_STATE__.frames,
    };
  });
}

/** 侦察页：同一条种子时间线，采样 (now, mode, pos)，挑“只差一帧”的目标时刻。
 *  from 之前不采：那几帧还没渲染出世界，冻结出来是空白/初始画面。
 */
async function sampleTimeline(context, { actionAt, from = 500, until }) {
  const page = await openWorld(context, { actionAt });
  const samples = [];
  for (;;) {
    const info = await page.evaluate(() => {
      const view = window.Oracle.view();
      const villager = view.villagers[0];
      return { now: view.now, mode: villager.mode, pos: { x: villager.pos.x, y: villager.pos.y } };
    });
    if (info.now >= from) samples.push(poseOf(info));
    if (info.now >= until) break;
    await page.waitForTimeout(24);
  }
  await page.close();
  return samples;
}

/** 从采样里挑候选对：帧号必须不同；idle 还要求取整像素相同。按时间间隔排序。
 *  祭坛图形深度是 ALTAR+ay+40，站在祭坛格上的小人会被它盖住（截图里等于隐身），
 *  所以把祭坛矩形附近的位置整体排除，保证证据里真的看得到 v0。
 */
function pickPairs(samples, { walking }) {
  const occluded = (px, py) => px >= 220 && px <= 292 && py >= 128 && py <= 194;
  const wanted = samples.filter(
    (sample) => WALK_MODES.has(sample.mode) === walking && !occluded(sample.px, sample.py),
  );
  const pairs = [];
  for (let i = 0; i < wanted.length; i += 1) {
    for (let j = i + 1; j < wanted.length; j += 1) {
      const a = wanted[i];
      const b = wanted[j];
      if (a.frame === b.frame || a.frameCount !== b.frameCount) continue;
      if (!walking && (a.px !== b.px || a.py !== b.py)) continue;
      const gap = Math.abs(b.now - a.now);
      if (gap > 500) continue;
      const cost = walking ? gap + Math.abs(b.px - a.px) + Math.abs(b.py - a.py) : gap;
      pairs.push({ a, b, cost });
    }
  }
  return pairs.sort((left, right) => left.cost - right.cost);
}

async function captureFrame(context, name, target, { actionAt } = {}) {
  const page = await openWorld(context, { actionAt });
  const info = poseOf(await freezeAt(page, target));
  await page.screenshot({ path: `${OUT}/${name}-full.png` });
  await page.addStyleTag({
    content:
      'header{display:none!important}body{padding:0!important;align-items:flex-start!important;gap:0!important}canvas{width:480px!important;height:320px!important}',
  });
  await page.waitForTimeout(100);
  const canvas = page.locator('canvas');
  const box = await canvas.boundingBox();
  const fullHash = await save(`${OUT}/${name}.png`, await canvas.screenshot());
  const clip = {
    x: Math.round(box.x + info.px - ANIM_CROP.halfW),
    y: Math.round(box.y + info.py - ANIM_CROP.halfH),
    width: ANIM_CROP.width,
    height: ANIM_CROP.height,
  };
  const cropHash = await save(`${OUT}/${name}-pose.png`, await page.screenshot({ clip }));
  await page.close();
  return { name, ...info, target, clip, fullHash, cropHash };
}

async function runAnim(context) {
  // idle：不派活，等她自己闲逛（从 800ms 起采样，跳过还没渲染的开局帧）；
  // walk：在 sim 500ms 派一条代码活。
  const idlePairs = pickPairs(await sampleTimeline(context, { from: 800, until: 8000 }), {
    walking: false,
  });
  const walkPairs = pickPairs(await sampleTimeline(context, { actionAt: 500, from: 800, until: 6000 }), {
    walking: true,
  });
  if (idlePairs.length === 0) throw new Error('没找到“同位置、差一帧”的 idle 采样对');
  if (walkPairs.length === 0) throw new Error('没找到“行走中、差一帧”的采样对');

  const results = [];
  const take = async (name, pairs, { actionAt } = {}) => {
    for (let i = 0; i < Math.min(pairs.length, 4); i += 1) {
      const pair = pairs[i];
      const a = await captureFrame(context, `${name}-a`, pair.a.now, { actionAt });
      const b = await captureFrame(context, `${name}-b`, pair.b.now, { actionAt });
      const framesDiffer = a.frame !== b.frame;
      const samePlace = a.px === b.px && a.py === b.py;
      // 真正的证据：帧号不同 +（idle 要求同位置）+ 像素确实变了。
      const pixelsDiffer = a.fullHash !== b.fullHash && a.cropHash !== b.cropHash;
      if (framesDiffer && pixelsDiffer && (name === 'anim-walk' || samePlace)) {
        results.push(a, b);
        return;
      }
      console.warn(
        `[${name}] 候选 ${i} 被弃：framesDiffer=${framesDiffer} pixelsDiffer=${pixelsDiffer} ` +
          `samePlace=${samePlace} a(frame ${a.frame}@${a.now} ${a.fullHash}/${a.cropHash}) ` +
          `b(frame ${b.frame}@${b.now} ${b.fullHash}/${b.cropHash})`,
      );
    }
    throw new Error(
      `${name}: 4 次候选都没凑出“帧号不同${name === 'anim-idle' ? '且同位置' : ''}且像素不同”的两帧`,
    );
  };

  await take('anim-idle', idlePairs);
  await take('anim-walk', walkPairs, { actionAt: 500 });
  return results;
}

/* ---------------------------------------------------------------- 切片评审（task-30 / E-16） */
/* 晴/雨/夜同构图：先扩建第 4 栋 workshop，再等 sim 时间到固定 target 冻结 rAF，
 * 逐个改 canvas 的 CSS 尺寸截 5 档（100 全页 / 200% / canvas / 原生 / 400%）。
 * 建筑数、天气、相机固定都在截图前硬断言，不满足就直接非 0 退出并打印快照。 */

const SLICE = { buildAt: 500, rainAt: 800, targetNow: 6000 };

async function openSliceWorld(context, { rain = false, night = false } = {}) {
  const page = await context.newPage();
  await page.setViewportSize({ width: 1010, height: 1400 });
  // 扩建与下雨都按 sim 时间在页面 rAF 内点击，避免 Node 轮询带来的一个模拟步误差。
  const pendingClicks = [{ selector: '[data-build="workshop_plot"]', atNow: SLICE.buildAt }];
  if (rain) pendingClicks.push({ selector: '#rain', atNow: SLICE.rainAt });
  await page.addInitScript(animInitScript, { seed: SEED, pendingClicks });
  await page.goto(withPhase(PAGE_URL, night ? 'night' : 'noon'), { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: TIMEOUT });
  return page;
}

function readSliceState(page) {
  return page.evaluate(() => {
    const view = window.Oracle.view();
    const canvas = document.querySelector('canvas');
    const rect = canvas === null ? null : canvas.getBoundingClientRect();
    return {
      now: view.now,
      weather: view.weather,
      buildingCount: view.buildings.length,
      instanceIds: view.buildings.map((item) => item.instanceId),
      canvasBox:
        rect === null
          ? null
          : { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
      scroll: { x: window.scrollX, y: window.scrollY },
      frames: window.__ANIM_STATE__.frames,
    };
  });
}

async function shootSlice(context, name, { rain = false, night = false }) {
  const page = await openSliceWorld(context, { rain, night });
  await page.waitForFunction((t) => window.Oracle.view().now >= t, SLICE.targetNow, {
    timeout: TIMEOUT,
    polling: 16,
  });
  await page.evaluate(() => {
    window.__ANIM_STATE__.frozen = true;
  });
  await page.waitForTimeout(150);

  const info = await readSliceState(page);
  const problems = [];
  if (info.buildingCount !== 4) problems.push(`建筑数 ${info.buildingCount} != 4`);
  if (!info.instanceIds.some((id) => id.startsWith('workshop_plot'))) {
    problems.push(`没有 workshop_plot：${info.instanceIds.join(',')}`);
  }
  if (rain && info.weather !== 'rain') problems.push(`雨态 weather=${info.weather}`);
  if (!rain && info.weather === 'rain') problems.push('晴/夜态却是 rain');
  if (info.canvasBox === null || Math.round(info.canvasBox.width) !== 960 || Math.round(info.canvasBox.height) !== 640) {
    problems.push(`canvas 不是 960×640：${JSON.stringify(info.canvasBox)}`);
  }
  if (info.scroll.x !== 0 || info.scroll.y !== 0) {
    problems.push(`页面滚动偏移：${JSON.stringify(info.scroll)}`);
  }
  if (problems.length > 0) {
    console.error(`[${name}] 断言失败：${problems.join('；')}`);
    console.error(JSON.stringify(info, null, 2));
    await page.close();
    throw new Error(`slice ${name} 断言失败`);
  }

  if (night) {
    const nightOn = await page.evaluate(() => window.Oracle.nightEnabled());
    if (!nightOn) {
      await page.close();
      throw new Error(`${name}: ?night=1 没有打开夜景渲染态`);
    }
  }

  const hashes = {};
  // 100%：带页头的整页（尺寸随视口）。先拍它，因为后面要挪布局。
  // 这一档保留 overlay/DOM，用户要看的就是“窗口里实际长这样”。
  hashes['100'] = await save(`${OUT}/${name}-100.png`, await page.screenshot());

  // canvas / 200% / 原生 / 400% 这四档只要画布像素，必须把压在画布上的 DOM 藏掉：
  // 元素截图是“整个页面渲染后裁到元素包围盒”，overlay 名字/气泡、调试面板会一起被截进来，
  // 逐像素比对就会出现非 0 差异。
  // 用 visibility:hidden 而不是 display:none——不参与布局，canvas 位置/尺寸不会变。
  // 另外去掉页头/内边距并左对齐，让 canvas 的 boundingBox 落在整数像素上；
  // 否则元素截图会多出 1px（960×641 / 480×321），原生帧就不是精确 480×320。
  await page.addStyleTag({
    content:
      'header{display:none!important}body{padding:0!important;align-items:flex-start!important;gap:0!important}' +
      '#world-overlay,#debug-panel,#debug-toggle{visibility:hidden!important}',
  });
  await page.waitForTimeout(100);
  const canvas = page.locator('canvas');
  const box = await canvas.boundingBox();
  if (
    box === null ||
    Math.round(box.width) !== 960 ||
    Math.round(box.height) !== 640 ||
    !Number.isInteger(box.x) ||
    !Number.isInteger(box.y)
  ) {
    console.error(`[${name}] canvas 元素不是整数像素 960×640：${JSON.stringify(box)}`);
    await page.close();
    throw new Error(`slice ${name} 元素尺寸不对`);
  }
  const canvasBox = { x: box.x, y: box.y, width: box.width, height: box.height };

  // 隐藏 overlay 后复核：取一次画布区域，确认没有 DOM 叠在上面（后续差异=0 靠这个前提）。
  hashes.canvas = await save(`${OUT}/${name}-canvas.png`, await canvas.screenshot());
  await page.addStyleTag({ content: 'canvas{width:960px!important;height:640px!important}' });
  await page.waitForTimeout(100);
  hashes['200pct'] = await save(`${OUT}/${name}-200pct.png`, await canvas.screenshot());
  await page.addStyleTag({ content: 'canvas{width:480px!important;height:320px!important}' });
  await page.waitForTimeout(100);
  hashes['native-480x320'] = await save(`${OUT}/${name}-native-480x320.png`, await canvas.screenshot());
  await page.addStyleTag({ content: 'canvas{width:1920px!important;height:1280px!important}' });
  await page.waitForTimeout(100);
  hashes['400pct'] = await save(`${OUT}/${name}-400pct.png`, await canvas.screenshot());
  await page.close();
  return { name, rain, night, targetNow: SLICE.targetNow, ...info, canvasBox, hashes };
}

async function runSlice(context) {
  return [
    await shootSlice(context, 'slice-sunny', {}),
    await shootSlice(context, 'slice-rain', { rain: true }),
    await shootSlice(context, 'slice-night', { night: true }),
  ];
}

await mkdir(OUT, { recursive: true });
const browser = await chromium.connectOverCDP(CDP_URL);
const context = browser.contexts()[0];
if (context === undefined) {
  console.error('CDP 浏览器里没有可用的 context（确认启动时没加 --no-startup-window）。');
  process.exit(1);
}

// 热身：第一张页面会带上首帧着色器/合成器预热差异，先跑一张丢掉，保证复跑可比。
const warm = await context.newPage();
await warm.goto(PAGE_URL, { waitUntil: 'load' });
await warm.waitForFunction(() => window.Oracle !== undefined, null, { timeout: TIMEOUT }).catch(() => {});
await warm.close();

const scenarioIndex = process.argv.indexOf('--scenario');
const scenario = scenarioIndex >= 0 ? process.argv[scenarioIndex + 1] : null;
if (scenario === 'slice') {
  const captures = await runSlice(context);
  await browser.close();
  const lines = [
    `# 切片评审 manifest（task-30 / E-16；种子 ${SEED}；页面 ${PAGE_URL}）`,
    `# 冻结：sim now ≥ ${SLICE.targetNow} 后停 rAF；扩建点击 ${SLICE.buildAt}ms，下雨点击 ${SLICE.rainAt}ms`,
    '# 相机：main.ts 固定 Scale.NONE + zoom 2 → canvas 960×640；页面 scroll 必须为 0',
    '# night = render mode (?night=1)：deepShade 冷暗 + 窗/门/灯/祭坛暖光，不是 brightness 滤镜',
    '# DOM：100 档保留页头/overlay（给人看窗口实际效果）；其余四档 hide #world-overlay/#debug-panel/#debug-toggle（visibility:hidden，不重排）',
    '# 200pct 与 canvas 等价：canvas=960×640=native×2（Scale.NONE + zoom 2）',
    '# state\train\tnight\ttargetNow\tactualNow\tweather\tbuildingCount\tinstanceIds\tcanvasBox\tscroll\t100\tcanvas\t200pct\tnative-480x320\t400pct',
  ];
  for (const capture of captures) {
    lines.push(
      [
        capture.name,
        capture.rain ? 'rain' : 'dry',
        capture.night ? 'render:night' : 'none',
        capture.targetNow,
        capture.now,
        capture.weather,
        capture.buildingCount,
        capture.instanceIds.join(','),
        capture.canvasBox === null
          ? 'null'
          : `${Math.round(capture.canvasBox.width)}x${Math.round(capture.canvasBox.height)}`,
        `${capture.scroll.x},${capture.scroll.y}`,
        capture.hashes['100'],
        capture.hashes.canvas,
        capture.hashes['200pct'],
        capture.hashes['native-480x320'],
        capture.hashes['400pct'],
      ].join('\t'),
    );
  }
  await writeFile(`${OUT}/slice-manifest.txt`, `${lines.join('\n')}\n`);
  for (const line of lines) console.log(line);
  console.log(`产物：${OUT}/slice-{sunny,rain,night}-{100,200pct,canvas,native-480x320,400pct}.png`);
  process.exit(0);
}

const animMode = process.argv.includes('--anim');
if (animMode) {
  const captures = await runAnim(context);
  await browser.close();
  const lines = [
    `# 动画证据 manifest（种子 ${SEED}，页面 ${PAGE_URL}）`,
    `# 冻结：等 simulation now ≥ 目标值后停 rAF；帧号 = floor(now / ${ANIM_FRAME_MS.toFixed(4)}ms) % 帧数`,
    `# 裁剪：以 v0 取整像素为中心 ${ANIM_CROP.width}×${ANIM_CROP.height}，walk 的位移由此扣除`,
    '# name\tframe/frames\tmode\tpos\tpx,py\tnow\tstep\tfullSha\tposeSha',
  ];
  for (const capture of captures) {
    lines.push(
      [
        capture.name,
        `${capture.frame}/${capture.frameCount}`,
        capture.mode,
        `(${capture.pos.x.toFixed(2)},${capture.pos.y.toFixed(2)})`,
        `${capture.px},${capture.py}`,
        capture.now,
        capture.step,
        capture.fullHash,
        capture.cropHash,
      ].join('\t'),
    );
  }
  const idle = captures.filter((item) => item.name.startsWith('anim-idle'));
  const walk = captures.filter((item) => item.name.startsWith('anim-walk'));
  lines.push(
    `# idle 位置相同：${
      idle.length === 2 && idle[0].px === idle[1].px && idle[0].py === idle[1].py ? '是' : '否'
    }`,
  );
  lines.push(
    `# walk 位移（像素）：${
      walk.length === 2 ? `dx=${walk[1].px - walk[0].px} dy=${walk[1].py - walk[0].py}` : 'n/a'
    }（裁剪已按各自位置对齐）`,
  );
  const measured = captures.filter((item) => item.now > 500 && item.rafFrames > 0);
  const ratio =
    measured.reduce((sum, item) => sum + item.now / item.rafFrames, 0) / Math.max(1, measured.length);
  await writeFile(`${OUT}/anim-manifest.txt`, `${lines.join('\n')}\n`);
  for (const line of lines) console.log(line);
  // 时钟测量只打 stdout：rAF 帧数受调度抖动影响，进 manifest 会让“复跑字节一致”失效。
  console.log(
    `# 时钟测量：simulation-now / rAF 帧数 = ${ratio.toFixed(2)} ms/帧 ` +
      '（headless rAF 真实节拍；冻结已改用 sim 时间，不依赖它）',
  );
  console.log(`产物：${OUT}/anim-{idle,walk}-{a,b}.png 与 -pose.png，摘要 ${OUT}/anim-manifest.txt`);
  process.exit(0);
}


const results = [];
results.push(await shoot(context, 'palette-sunny', {}));
results.push(await shoot(context, 'palette-rain', { rain: true }));
results.push(await shoot(context, 'palette-night', { night: true }));
await browser.close();

for (const result of results) {
  const parts = Object.entries(result.hashes)
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
  console.log(`${result.name}: weather=${result.weather} frame=${result.frame} ${parts}`);
}
console.log(`产物目录：${OUT}/palette-{sunny,rain,night}-{100,canvas,native-480x320,400pct}.png`);
