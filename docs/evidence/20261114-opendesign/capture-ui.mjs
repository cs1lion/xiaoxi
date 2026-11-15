// Capture Xiaoxi UI screenshots + console errors for the OpenDesign audit.
// Read-only: no interaction with memory APIs, no mutation.
import { chromium } from '@playwright/test';
import path from 'node:path';

const OUT = path.resolve(import.meta.dirname);
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const shots = [
  { name: 'chat-desktop', url: '/chat', width: 1440, height: 900 },
  { name: 'chat-mobile', url: '/chat', width: 390, height: 844 },
  { name: 'interpret-desktop', url: '/interpret.html', width: 1440, height: 900 },
  { name: 'notes-desktop', url: '/notes.html', width: 1440, height: 900 },
];

const browser = await chromium.launch({ headless: true });
const report = [];
for (const shot of shots) {
  const page = await browser.newPage({ viewport: { width: shot.width, height: shot.height } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e.message)));
  page.on('console', m => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });
  let status = null;
  try {
    const resp = await page.goto(`${BASE}${shot.url}`, { waitUntil: 'networkidle', timeout: 45000 });
    status = resp ? resp.status() : null;
  } catch (e) {
    errors.push(`goto: ${e.message}`);
  }
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, `${shot.name}.png`), fullPage: false });
  const metrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    const paint = performance.getEntriesByType('paint');
    return {
      domNodes: document.querySelectorAll('*').length,
      title: document.title,
      canvas: document.querySelectorAll('canvas').length,
      images: document.querySelectorAll('img').length,
      scripts: document.querySelectorAll('script').length,
      bodyText: (document.body.innerText || '').slice(0, 400),
      domContentLoaded: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
      loadEvent: nav ? Math.round(nav.loadEventEnd) : null,
      firstPaint: paint.find(p => p.name === 'first-paint')?.startTime ?? null,
    };
  });
  report.push({ ...shot, status, ...metrics, errors });
  await page.close();
}
await browser.close();
console.log(JSON.stringify(report, null, 2));
