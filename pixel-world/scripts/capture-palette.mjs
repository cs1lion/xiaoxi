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
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import process from 'node:process';

const CDP_URL = process.env.CDP_URL ?? 'http://127.0.0.1:9333';
const PAGE_URL = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/?ledger=off';
const OUT = 'eval/screens';
const SEED = Number(process.env.PALETTE_SEED ?? 20261019);
const FRAME_MS = 1000 / 60;
const STOP_AT = 240;
const RAIN_CLICK_AT = 30;
const TIMEOUT = 30000;

/** 页面初始化脚本：清存档、注入种子、把 rAF 变成可冻结的确定步进。 */
function initScript({ seed, frameMs, stopAt, rainAt, rain }) {
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
  await page.goto(PAGE_URL, { waitUntil: 'load' });
  await page.waitForFunction(() => window.__CAPTURE_STATE__ !== undefined, null, { timeout: TIMEOUT });
  await page.waitForFunction((stop) => window.__CAPTURE_STATE__.frame >= stop, STOP_AT, { timeout: TIMEOUT });
  await page.waitForTimeout(300);

  const weather = await page.evaluate(() => window.Oracle.view().weather);
  if (rain !== (weather === 'rain')) {
    await page.close();
    throw new Error(`${name}: 天气状态不符（期望 ${rain ? 'rain' : 'clear'}，实际 ${weather}）`);
  }
  if (night) {
    await page.evaluate(() => {
      const canvas = document.querySelector('canvas');
      // 1.0 没有昼夜系统：这里只是只读试验用的整体压暗，截图不参与验收标准。
      if (canvas !== null) canvas.style.filter = 'brightness(0.55)';
    });
    await page.waitForTimeout(120);
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
