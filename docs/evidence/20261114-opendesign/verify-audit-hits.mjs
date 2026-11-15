// Verify whether suspicious audit hits are actually visible to a user.
import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const browser = await chromium.launch({ headless: true });
for (const vp of [{ name: 'desktop', width: 1440, height: 900 }, { name: 'mobile', width: 390, height: 844 }]) {
  const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
  await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1500);
  const res = await page.evaluate(() => {
    const hiddenAncestor = el => {
      let cur = el;
      while (cur && cur !== document.body) {
        const c = getComputedStyle(cur);
        if (c.display === 'none' || c.visibility === 'hidden' || parseFloat(c.opacity) === 0) return `${cur.tagName}${cur.id ? '#' + cur.id : ''}`;
        if (cur.hasAttribute('hidden') || cur.getAttribute('aria-hidden') === 'true') return `${cur.tagName}${cur.id ? '#' + cur.id : ''}[hidden]`;
        cur = cur.parentElement;
      }
      return null;
    };
    const probe = (el) => {
      const r = el.getBoundingClientRect();
      const cx = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
      const cy = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
      const hit = document.elementFromPoint(cx, cy);
      return {
        label: `${el.tagName}${el.id ? '#' + el.id : ''} "${(el.textContent || '').trim().slice(0, 18)}"`,
        rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
        hiddenAncestor: hiddenAncestor(el),
        hitSelfOrDescendant: hit ? (el === hit || el.contains(hit) || hit.contains(el)) : false,
      };
    };
    const zoomBtns = [...document.querySelectorAll('button')].filter(b => ['放大', '缩小', '复位'].includes((b.textContent || '').trim()));
    const narrow = [...document.querySelectorAll('select, button, input')].filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.width < 40 && r.height > 20;
    }).slice(0, 14);
    return {
      zoom: zoomBtns.map(probe),
      narrow: narrow.map(probe),
      sidebarVisible: (() => {
        const s = document.querySelector('#sidebar, aside, .sidebar');
        if (!s) return null;
        const r = s.getBoundingClientRect();
        return { rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], hidden: hiddenAncestor(s) };
      })(),
    };
  });
  console.log(`=== ${vp.name} ===`);
  console.log(JSON.stringify(res, null, 2));
  await page.close();
}
await browser.close();
