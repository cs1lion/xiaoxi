import { chromium } from 'playwright';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const root = path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'));
const shots = path.join(root, 'shots');
fs.mkdirSync(shots, { recursive: true });

const pages = [
  { id: 'a-nocturne', file: 'dir-a-nocturne.html', selectors: ['.side', '.topbar', '.room-label', '.companion-strip', '.hero h2', '.lede', '.sug', '.window', '.mode-line', '.input-bar'] },
  { id: 'b-almanac', file: 'dir-b-almanac.html', selectors: ['.side', '.topbar', '.room-label', '.editorial h2', '.lede', '.slip', '.sug', '.plate', '.mode-line', '.input-slip'] },
  { id: 'c-courtyard', file: 'dir-c-courtyard.html', selectors: ['.side', '.topbar', '.room-label', '.plaque', '.hero h2 .small', '.hero h2 .big', '.lede', '.stone', '.arch', '.mode-line', '.dock'] },
];

const views = [
  { name: '1440x1000', width: 1440, height: 1000 },
  { name: '390x844', width: 390, height: 844 },
];

const out = [];
const browser = await chromium.launch();
for (const pageDef of pages) {
  for (const view of views) {
    const page = await browser.newPage({ viewport: { width: view.width, height: view.height }, deviceScaleFactor: 1 });
    const errors = [];
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('console', (msg) => { if (msg.type() === 'error') errors.push(msg.text()); });
    await page.goto(pathToFileURL(path.join(root, pageDef.file)).href, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(350);
    const size = await page.evaluate(() => ({
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      bodyScrollWidth: document.body.scrollWidth,
    }));
    const metrics = await page.evaluate((selectors) => {
      const items = {};
      for (const selector of selectors) {
        const el = document.querySelector(selector);
        if (!el) { items[selector] = null; continue; }
        const cs = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        items[selector] = {
          fontFamily: cs.fontFamily,
          fontSize: cs.fontSize,
          fontWeight: cs.fontWeight,
          lineHeight: cs.lineHeight,
          letterSpacing: cs.letterSpacing,
          color: cs.color,
          background: cs.backgroundColor,
          borderRadius: cs.borderRadius,
          box: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        };
      }
      return {
        title: document.title,
        bodyFont: getComputedStyle(document.body).fontFamily,
        items,
      };
    }, pageDef.selectors);
    const file = path.join(shots, `${pageDef.id}-${view.name}.png`);
    await page.screenshot({ path: file, fullPage: false });
    const overflow = size.scrollWidth > view.width + 1;
    out.push({ id: pageDef.id, view: view.name, overflow, ...size, errors, metrics });
    console.log(`${pageDef.id} ${view.name}: scrollWidth=${size.scrollWidth} scrollHeight=${size.scrollHeight} overflow=${overflow} errors=${errors.length}`);
    await page.close();
  }
}
await browser.close();
fs.writeFileSync(path.join(root, 'metrics.json'), JSON.stringify(out, null, 2));
console.log('wrote metrics.json');

{
  const browser2 = await chromium.launch();
  for (const view of views) {
    const page = await browser2.newPage({ viewport: { width: view.width, height: view.height }, deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(path.join(root, 'index.html')).href, { waitUntil: 'load' });
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(350);
    const size = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, scrollHeight: document.documentElement.scrollHeight }));
    await page.screenshot({ path: path.join(shots, `comparison-${view.name}.png`), fullPage: true });
    console.log(`comparison ${view.name}: scrollWidth=${size.scrollWidth} scrollHeight=${size.scrollHeight}`);
    await page.close();
  }
  await browser2.close();
}
if (out.some((row) => row.overflow || row.errors.length)) process.exitCode = 1;
