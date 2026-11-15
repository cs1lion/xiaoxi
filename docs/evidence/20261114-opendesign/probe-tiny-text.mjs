import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle' });
await p.waitForTimeout(1500);
const out = await p.evaluate(() => {
  const path = el => {
    const parts = [];
    let cur = el;
    for (let i = 0; i < 4 && cur && cur !== document.body; i++, cur = cur.parentElement) {
      let s = cur.tagName.toLowerCase();
      if (cur.id) s += '#' + cur.id;
      else if (cur.className && typeof cur.className === 'string') s += '.' + cur.className.trim().split(/\s+/).slice(0, 2).join('.');
      parts.unshift(s);
    }
    return parts.join(' > ');
  };
  const visible = el => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return false;
    if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return false;
    const x = Math.min(Math.max(r.x + r.width / 2, 0), innerWidth - 1);
    const y = Math.min(Math.max(r.y + r.height / 2, 0), innerHeight - 1);
    const hit = document.elementFromPoint(x, y);
    return !!hit && (el === hit || el.contains(hit));
  };
  const groups = {};
  for (const el of document.querySelectorAll('body *')) {
    if (!el.firstChild || el.firstChild.nodeType !== 3) continue;
    const t = (el.textContent || '').trim();
    if (!t || !visible(el)) continue;
    const size = getComputedStyle(el).fontSize;
    if (parseFloat(size) >= 12) continue;
    const key = `${size} | ${path(el)}`;
    groups[key] = groups[key] || { count: 0, sample: t.slice(0, 14) };
    groups[key].count += 1;
  }
  return Object.entries(groups).sort((a, b) => b[1].count - a[1].count);
});
console.log(JSON.stringify(out, null, 2));
writeFileSync(path.join(import.meta.dirname, 'tiny-text-groups.json'), JSON.stringify(out, null, 2), 'utf8');
await b.close();
