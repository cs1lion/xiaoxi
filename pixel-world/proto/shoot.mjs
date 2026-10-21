// 原型在 worktree 里跑，worktree 不带 node_modules；借主仓库已装的那份。
import { chromium } from 'file:///D:/project/neurosama/node_modules/@playwright/test/index.mjs';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 720, height: 1480 } });
const errs = [];
page.on('pageerror', (e) => errs.push(e.message));
await page.goto(new URL('../proto/day-phase.html', import.meta.url).href);
await page.waitForFunction(() => document.documentElement.dataset.ready === '1');
await page.screenshot({ path: 'proto/out-phases.png', fullPage: true });
console.log('errors:', errs.length ? errs : 'none');

const native = await page.evaluate(() =>
  [...document.querySelectorAll('canvas')].map((c) => c.toDataURL('image/png')),
);
const { writeFileSync } = await import('node:fs');
const ids = ['dawn', 'noon', 'dusk', 'night'];
ids.forEach((id, i) => {
  writeFileSync(`proto/out-${id}.png`, Buffer.from(native[i].split(',')[1], 'base64'));
});
console.log('wrote', ids.join(', '));
await browser.close();
