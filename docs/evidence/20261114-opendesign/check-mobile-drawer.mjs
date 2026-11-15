// Mobile drawer + avatar plate geometry check (read-only interactions).
import { chromium } from '@playwright/test';
import path from 'node:path';

const OUT = path.resolve(import.meta.dirname);
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle', timeout: 45000 });
await page.waitForTimeout(1500);

const before = await page.evaluate(() => {
  const c = [...document.querySelectorAll('canvas')].find(x => !x.id);
  const chain = [];
  let el = c;
  for (let i = 0; i < 6 && el; i++, el = el.parentElement) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    chain.push({ tag: el.tagName + (el.id ? '#' + el.id : ''), cls: (el.className || '').toString().slice(0, 40), w: Math.round(r.width), h: Math.round(r.height), aspect: st.aspectRatio, objectFit: st.objectFit, overflow: st.overflow });
  }
  return { avatarCanvas: c ? { w: Math.round(c.getBoundingClientRect().width), h: Math.round(c.getBoundingClientRect().height), buf: [c.width, c.height] } : null, chain };
});
console.log('avatar before:', JSON.stringify(before, null, 2));

await page.locator('#sidebarToggle').click();
await page.waitForTimeout(1200);
const after = await page.evaluate(() => {
  const s = document.querySelector('#sidebar, aside, .sidebar');
  const r = s.getBoundingClientRect();
  const clipped = [...s.querySelectorAll('select, button, input, a')].filter(el => {
    const b = el.getBoundingClientRect();
    return b.width > 0 && (b.right > innerWidth + 1 || b.left < r.left - 1 || b.width < 40);
  }).slice(0, 12).map(el => ({ tag: el.tagName + (el.id ? '#' + el.id : ''), text: (el.textContent || '').trim().slice(0, 14), rect: [Math.round(el.getBoundingClientRect().left), Math.round(el.getBoundingClientRect().width)] }));
  return { drawer: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], scrollHeight: document.scrollingElement.scrollHeight, clipped };
});
console.log('drawer after toggle:', JSON.stringify(after, null, 2));
await page.screenshot({ path: path.join(OUT, 'chat-mobile-drawer.png') });
await browser.close();
