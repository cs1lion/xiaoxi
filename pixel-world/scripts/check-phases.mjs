#!/usr/bin/env node
/**
 * 四刻可辨性回归：走真实渲染器，逐刻截图并量像素差。
 *
 * 为什么不用单测：亮度与像素差是渲染结果，只有真跑一遍才算数。
 * 判据来自 ADR_WORLD_ALIVE §2「验收阈值」，数字改动必须同步改这里的常量。
 *
 * 跑法：
 *   1. npm run dev（默认 5188；其它端口用 PIXEL_WORLD_URL 指过去）
 *   2. node scripts/check-phases.mjs
 *
 * 截图落在 eval/screens/phase-<id>.png，失败时可直接看。
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir, writeFile } from 'node:fs/promises';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';
const OUT = 'eval/screens';
const PHASES = ['dawn', 'noon', 'dusk', 'night'];

/**
 * 采样区：整屏。
 *
 * 原先是固定的建筑带（y 176..468），因为单屏时代建筑总在同一位置。
 * 大世界里相机可以停在任意位置，固定带可能整块都是草地或天空，
 * 差异就被稀释到门槛以下——实测晨/昏只剩 0.6%。改成整屏，
 * 并把四刻截图统一对准祭坛（相机开局即对准，脚本不再依赖建筑位置）。
 */
const REGION_TOP = 0;
const REGION_BOTTOM = 640;

const reports = [];
const failures = [];
const check = (ok, label, detail = '') => {
  reports.push({ ok, label, detail });
  if (!ok) failures.push(`${label}${detail === '' ? '' : `（${detail}）`}`);
};

/** 纯相位：每刻至少与其它刻在建筑区有明显差异。 */
const MIN_REGION_DIFF = 0.03;
/** 亮度必须严格递减：晨 > 午 > 昏 > 夜。 */
const MIN_LUMA_STEP = 1.5;

await mkdir(OUT, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1010, height: 940 } });

const shots = {};
const pageErrors = [];
for (const phase of PHASES) {
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(`${phase}: ${error.message}`));
  await page.addInitScript(() => localStorage.clear());
  await page.goto(`${BASE}?ledger=off&time=${phase}&debug=1`, { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });
  // 渐变 FADE_MS = 900，等两倍确保拍到终态。
  await page.waitForTimeout(2200);

  const actual = await page.evaluate(() => window.Oracle.currentPhase());
  check(actual === phase, `?time=${phase} 真的停在${phase}`, `实际 ${actual}`);

  const buffer = await page.locator('canvas').first().screenshot();
  await writeFile(`${OUT}/phase-${phase}.png`, buffer);
  shots[phase] = buffer;
  await page.close();
}
check(pageErrors.length === 0, '四刻页面无 JS 报错', pageErrors.join('; '));

/** 用浏览器解码 PNG 取像素，避免在 Node 里引额外依赖。 */
const decoder = await context.newPage();
const stats = {};
for (const phase of PHASES) {
  const url = `data:image/png;base64,${shots[phase].toString('base64')}`;
  stats[phase] = await decoder.evaluate(async (src) => {
    const img = new Image();
    await new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = reject;
      img.src = src;
    });
    const canvas = document.createElement('canvas');
    canvas.width = img.width;
    canvas.height = img.height;
    const g = canvas.getContext('2d');
    g.drawImage(img, 0, 0);
    const px = g.getImageData(0, 0, img.width, img.height).data;
    const lum = (r, gg, b) => 0.2126 * r + 0.7152 * gg + 0.0722 * b;
    const values = new Float64Array(px.length / 4);
    let sum = 0;
    let r = 0;
    let gg = 0;
    let b = 0;
    for (let k = 0, i = 0; k < px.length; k += 4, i += 1) {
      values[i] = lum(px[k], px[k + 1], px[k + 2]);
      sum += values[i];
      r += px[k];
      gg += px[k + 1];
      b += px[k + 2];
    }
    const n = values.length;
    return {
      width: img.width,
      height: img.height,
      values: Array.from(values),
      mean: sum / n,
      warmth: (r - b) / n,
    };
  }, url);
}
await decoder.close();

// 亮度严格递减
for (let i = 0; i < PHASES.length - 1; i += 1) {
  const a = stats[PHASES[i]];
  const b = stats[PHASES[i + 1]];
  check(
    a.mean - b.mean >= MIN_LUMA_STEP,
    `${PHASES[i]} 必须明显亮于 ${PHASES[i + 1]}`,
    `${a.mean.toFixed(2)} vs ${b.mean.toFixed(2)}，差 ${(a.mean - b.mean).toFixed(2)}`,
  );
}

// 两两建筑区差异
for (let i = 0; i < PHASES.length; i += 1) {
  for (let j = i + 1; j < PHASES.length; j += 1) {
    const a = stats[PHASES[i]];
    const b = stats[PHASES[j]];
    const w = a.width;
    let changed = 0;
    let total = 0;
    for (let y = REGION_TOP; y < Math.min(REGION_BOTTOM, a.height); y += 1) {
      for (let x = 0; x < w; x += 1) {
        const k = y * w + x;
        total += 1;
        if (Math.abs(a.values[k] - b.values[k]) >= 8) changed += 1;
      }
    }
    const share = changed / Math.max(1, total);
    check(
      share >= MIN_REGION_DIFF,
      `${PHASES[i]} 与 ${PHASES[j]} 在建筑区可辨`,
      `${(share * 100).toFixed(1)}% 像素变化，门槛 ${(MIN_REGION_DIFF * 100).toFixed(0)}%`,
    );
  }
}

// 夜雨必须隐藏萤火。产品没有 `?rain=` 入口——天气只能由神谕或台账改，
// 所以这里走真实操作路径：说一句「下雨吧」，等雨上屏再拍。
const nightRain = await (async () => {
  const page = await context.newPage();
  page.on('pageerror', (error) => pageErrors.push(`night+rain: ${error.message}`));
  await page.addInitScript(() => localStorage.clear());
  await page.goto(`${BASE}?ledger=off&time=night&debug=1`, { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });
  await page.waitForTimeout(400);

  const said = await page.evaluate(() => window.Oracle.say('下雨吧'));
  check(said.ok === true, '神谕「下雨吧」被认出来了', said.message);

  // 雨渐变 1.8 秒；等足 3 秒确保 intensity 拉满。
  await page.waitForTimeout(3000);
  const weather = await page.evaluate(() => window.Oracle.view().weather);
  check(weather === 'rain', '夜里真的下起了雨', `weather=${weather}`);

  const buffer = await page.locator('canvas').first().screenshot();
  await writeFile(`${OUT}/phase-night-rain.png`, buffer);
  await page.close();
  return { buffer, weather };
})();
check(
  nightRain.weather === 'rain' && !nightRain.buffer.equals(shots.night),
  '夜雨与晴夜画面不同（雨幕生效）',
  '两张截图完全相同说明雨没上屏',
);

await browser.close();

for (const item of reports) {
  console.log(`${item.ok ? 'ok  ' : 'FAIL'} ${item.label}${item.detail === '' ? '' : `  ${item.detail}`}`);
}
if (failures.length > 0) {
  console.error(`\n四刻可辨性失败 ${failures.length} 项：`);
  for (const line of failures) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`\n四刻可辨性：${reports.length} 项通过`);
