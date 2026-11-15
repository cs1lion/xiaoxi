import { chromium } from '@playwright/test';
const out = 'docs/evidence/20261114-opendesign';
const viewports = [[1280,800],[1440,900],[1920,1080]];
const b = await chromium.launch();
for (const [w,h] of viewports) {
  const p = await b.newPage({ viewport: { width: w, height: h } });
  await p.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle', timeout: 45000 });
  await p.waitForTimeout(1800);
  await p.screenshot({ path: `${out}/web-${w}-w6-after.png`, fullPage: true });
  await p.close();
}
await b.close();
console.log('ok');
