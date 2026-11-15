import { chromium } from '@playwright/test';

const b = await chromium.launch();
const out = {};
for (const vp of [{ n: '1280', w: 1280, h: 800 }, { n: '1440', w: 1440, h: 900 }, { n: '1920', w: 1920, h: 1080 }]) {
  const p = await b.newPage({ viewport: { width: vp.w, height: vp.h } });
  await p.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle' });
  await p.waitForTimeout(1500);
  out[vp.n] = await p.evaluate(() => {
    const pick = (sel, text) => {
      const els = [...document.querySelectorAll(sel)];
      return text ? els.find(e => (e.textContent || '').includes(text)) : els[0];
    };
    const info = el => {
      if (!el) return null;
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return { text: (el.textContent || '').trim().slice(0, 20), fontSize: cs.fontSize, lineHeight: cs.lineHeight, width: Math.round(r.width), height: Math.round(r.height), y: Math.round(r.y), cjkPerLine: Math.round(r.width / parseFloat(cs.fontSize)) };
    };
    const side = document.querySelector('#sidePanel, .side');
    const sections = side ? [...side.children].map(c => {
      const r = c.getBoundingClientRect();
      return { tag: c.tagName + (c.className ? '.' + String(c.className).split(' ')[0] : ''), y: Math.round(r.y), h: Math.round(r.height), text: (c.textContent || '').trim().slice(0, 18) };
    }) : [];
    return {
      h2: info(document.querySelector('.welcome h2')),
      kicker: info(document.querySelector('.welcome .kicker')),
      lede: info(pick('.welcome p', '聊一件开心的小事')),
      note: info(document.querySelector('.welcome-note')),
      suggestion: info(document.querySelector('.suggestions button')),
      composerHint: info(document.querySelector('.composer-foot .hint')),
      sideSections: sections,
      sideVisibleBottom: side ? Math.round(side.getBoundingClientRect().bottom) : null,
    };
  });
  await p.close();
}
console.log(JSON.stringify(out, null, 2));
await b.close();
