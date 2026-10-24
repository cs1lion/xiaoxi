/**
 * 摆件来源的真机验证：交付足够多单，直到祭坛边落下一件，然后读状态行。
 *
 * 只验两条：摆件带上了来源（类型/单号/摘要），纸面写得出是哪一单换的。
 */
import { chromium } from 'file:///D:/project/neurosama/node_modules/@playwright/test/index.mjs';

const BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5199/';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1010, height: 940 } });
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

await page.addInitScript(() => localStorage.clear());
await page.goto(`${BASE}?ledger=off&time=noon`, { waitUntil: 'load' });
await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 20000 });

const lines = [];
await page.waitForFunction(
  () => {
    const snap = window.Oracle.view();
    const oracle = window.Oracle;
    if (snap.props.length > 0) return true;
    // 她空闲时派一单短的；忙碌时什么也不做，等下一拍。
    if (snap.villager.mode === 'idle') {
      oracle.dispatch('research', `查一下向量库选型 ${Math.floor(snap.now / 1000)}`);
    }
    return false;
  },
  null,
  { timeout: 90_000, polling: 500 },
);

const result = await page.evaluate(() => {
  const snap = window.Oracle.view();
  return {
    props: snap.props.map((p) => ({
      kind: p.kind,
      label: p.label,
      sourceTaskKind: p.sourceTaskKind,
      sourceTaskId: p.sourceTaskId,
      sourceSummary: p.sourceSummary,
    })),
    report: window.Oracle.report(),
    status: document.querySelector('#status')?.textContent ?? '',
  };
});

lines.push(`props: ${JSON.stringify(result.props, null, 2)}`);
lines.push(`report: ${JSON.stringify(result.report.lines, null, 2)}`);
lines.push(`status: ${result.status}`);
lines.push(`pageerrors: ${errors.length ? errors.join('; ') : 'none'}`);

// 截图：祭坛边应有一棵带书的树
const canvas = page.locator('canvas').first();
await canvas.screenshot({ path: 'proto/live-prop-source.png' });

console.log(lines.join('\n\n'));
await browser.close();
