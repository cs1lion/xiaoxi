#!/usr/bin/env node
/**
 * 渲染预算回归：每帧的绘制指令总量必须低于阈值。
 *
 * 为什么需要这条：地形与迷雾曾经各自是一次性画进一个大 Graphics 的，
 * 每帧 Phaser 都要重新批处理全部指令。实测 77 万条，其中 87% 在屏幕外。
 * 烘焙成纹理后应当降到 3 万以下。这条脚本防的是"哪天又有人往大 Graphics 里塞东西"。
 *
 * 跑法：
 *   1. npm run dev
 *   2. node scripts/check-render-budget.mjs
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';

/** 指令上限。烘焙后实测约 2 万；给一倍余量。 */
const MAX_COMMANDS = 30_000;
/** 拖动帧率相对静止帧率的比例下限。 */
const MIN_DRAG_RATIO = 0.9;

const reports = [];
const failures = [];
const check = (ok, label, detail = '') => {
  reports.push({ ok, label, detail });
  if (!ok) failures.push(`${label}${detail === '' ? '' : `（${detail}）`}`);
};

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1010, height: 1100 } });
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

await page.addInitScript(() => localStorage.clear());
await page.goto(`${BASE}?ledger=off&time=noon&debug=1`, { waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 30000 });
await page.waitForTimeout(1200);

const stats = await page.evaluate(() => window.Oracle.renderStats());
check(
  stats.totalCommands < MAX_COMMANDS,
  `绘制指令低于 ${MAX_COMMANDS}`,
  `实际 ${stats.totalCommands}`,
);
check(
  stats.graphicsObjects < 40,
  'Graphics 对象数量正常',
  `实际 ${stats.graphicsObjects}`,
);

const measureFps = async () =>
  page.evaluate(
    () =>
      new Promise((resolve) => {
        let frames = 0;
        const start = performance.now();
        const tick = () => {
          frames += 1;
          const elapsed = performance.now() - start;
          if (elapsed > 1500) resolve((frames / elapsed) * 1000);
          else requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      }),
  );

const idleFps = await measureFps();

const box = await page.locator('canvas').first().boundingBox();
await page.mouse.move(box.x + 400, box.y + 300);
await page.mouse.down();
const dragPromise = measureFps();
for (let i = 0; i < 25; i += 1) {
  await page.mouse.move(box.x + 400 - i * 4, box.y + 300 - i * 3);
  await page.waitForTimeout(50);
}
const dragFps = await dragPromise;
await page.mouse.up();

check(
  dragFps >= idleFps * MIN_DRAG_RATIO,
  '拖动时帧率不明显低于静止',
  `静止 ${idleFps.toFixed(1)} / 拖动 ${dragFps.toFixed(1)}`,
);
// 省电模式：切到后台必须真的降频。用 advanceCount（真正推进世界的帧数），
// 不能用 Phaser 的 update 调用次数——跳帧时它仍然每帧加一，看不出差别。
const countAdvances = async (ms) => {
  const before = await page.evaluate(() => window.Oracle.ecoState().advanceCount);
  await page.waitForTimeout(ms);
  const after = await page.evaluate(() => window.Oracle.ecoState().advanceCount);
  return ((after - before) / ms) * 1000;
};

const foregroundRate = await countAdvances(1500);
await page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
});
await page.waitForTimeout(400);
const backgroundRate = await countAdvances(1500);

await page.evaluate(() => {
  Object.defineProperty(document, 'visibilityState', { value: 'visible', configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
});
await page.waitForTimeout(400);
const restoredRate = await countAdvances(1500);

check(
  backgroundRate <= foregroundRate / 3,
  '切到后台真的降频',
  `前台 ${foregroundRate.toFixed(1)} / 后台 ${backgroundRate.toFixed(1)}`,
);
check(
  restoredRate >= foregroundRate * 0.9,
  '回到前台恢复满帧',
  `恢复 ${restoredRate.toFixed(1)}`,
);

check(errors.length === 0, '全程无 JS 报错', errors.join('; '));

await browser.close();

for (const item of reports) {
  console.log(`${item.ok ? 'ok  ' : 'FAIL'} ${item.label}${item.detail === '' ? '' : `  ${item.detail}`}`);
}
if (failures.length > 0) {
  console.error(`\n渲染预算失败 ${failures.length} 项：`);
  for (const line of failures) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`\n渲染预算：${reports.length} 项通过`);
