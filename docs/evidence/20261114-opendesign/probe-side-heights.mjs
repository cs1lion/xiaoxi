import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const b = await chromium.launch();
const out = {};
for (const vp of [{ w: 1280, h: 800 }, { w: 1440, h: 900 }, { w: 1920, h: 1080 }]) {
  const p = await b.newPage({ viewport: { width: vp.w, height: vp.h } });
  await p.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1800);
  out[`${vp.w}x${vp.h}`] = await p.evaluate(() => {
    const side = document.querySelector('#sidePanel');
    const kids = [...side.children].map(el => {
      const r = el.getBoundingClientRect();
      return {
        tag: el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\s+/).join('.') : ''),
        h: Math.round(r.height),
        children: [...el.children].map(c => Math.round(c.getBoundingClientRect().height)),
      };
    });
    const mem = document.querySelector('#memList');
    return {
      scrollH: side.scrollHeight,
      clientH: side.clientHeight,
      kids,
      memList: mem ? { h: Math.round(mem.getBoundingClientRect().height), scrollH: mem.scrollHeight, rows: mem.children.length } : null,
    };
  });
  await p.close();
}
writeFileSync(path.join(import.meta.dirname, 'side-heights.json'), JSON.stringify(out, null, 2), 'utf8');
console.log(JSON.stringify(out, null, 1));
await b.close();
