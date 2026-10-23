/**
 * 三个来源摆件并排截图（正常 2 倍显示比例，不放大），用来判断首次可辨性。
 */
import { chromium } from 'file:///D:/project/neurosama/node_modules/@playwright/test/index.mjs';
import { mkdir } from 'node:fs/promises';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5199/';
const browser = await chromium.launch({ headless: true });
await mkdir('proto/out', { recursive: true });

const shots = [];
for (const kind of ['code', 'research', 'writing']) {
  const page = await browser.newPage({ viewport: { width: 1010, height: 940 } });
  await page.addInitScript(() => localStorage.clear());
  await page.goto(`${BASE}?ledger=off&time=noon`, { waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });
  await page.waitForFunction(
    (k) => {
      const s = window.Oracle.view();
      if (s.props.length > 0) return true;
      if (s.villager.mode === 'idle') window.Oracle.dispatch(k, `测试 ${k}`);
      return false;
    },
    kind,
    { timeout: 90000, polling: 400 },
  );
  const prop = await page.evaluate(() => window.Oracle.view().props[0]);
  const box = await page.locator('canvas').first().boundingBox();
  // 原生 480×320 按 2 倍显示：CSS 尺寸 / 原生尺寸 = 缩放比（不写死 2）。
  const scale = box.width / 480;
  const tile = 16 * scale;
  const groundY = (prop.at.y + 0.8) * tile; // propFootY 的同一口径
  const x = Math.round(box.x + (prop.at.x + 0.5) * tile - tile * 1.5);
  const y = Math.round(box.y + groundY - tile * 2.2);
  const buf = await page.screenshot({ clip: { x, y, width: Math.round(tile * 3), height: Math.round(tile * 3) } });
  shots.push({ kind, prop, b64: buf.toString('base64') });
  console.log(`${kind}: ${prop.kind} / ${prop.label} / 来源 ${prop.sourceTaskKind}`);
  await page.close();
}

// 拼成一张对比图
const page = await browser.newPage({ viewport: { width: 400, height: 200 } });
const html = `<body style="margin:0;background:#0c0f0d;display:flex;gap:8px;padding:8px">
${shots
  .map(
    (s) =>
      `<div style="text-align:center;color:#e8e2d2;font:12px monospace">
        <img src="data:image/png;base64,${s.b64}" style="image-rendering:pixelated;width:128px">
        <div>${s.kind}</div></div>`,
  )
  .join('')}
</body>`;
await page.setContent(html);
await page.waitForTimeout(200);
await page.screenshot({ path: 'proto/out/prop-compare.png' });
await browser.close();
console.log('wrote proto/out/prop-compare.png');
