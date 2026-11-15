import { chromium } from '@playwright/test';

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto('http://127.0.0.1:8000/chat', { waitUntil: 'networkidle' });
await p.waitForTimeout(1500);
const r = await p.evaluate(() => {
  const parse = s => {
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 };
  };
  const lum = c => {
    const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, bb) => {
    const l1 = lum(a), l2 = lum(bb);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const out = {};
  const tabs = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && (e.textContent || '').trim() === '此刻');
  for (const t of tabs) {
    const c = getComputedStyle(t);
    const fg = parse(c.color);
    let bg = null, cur = t;
    while (cur && cur !== document.documentElement) {
      const x = parse(getComputedStyle(cur).backgroundColor);
      if (x && x.a > 0.6) { bg = x; break; }
      cur = cur.parentElement;
    }
    out['此刻'] = {
      size: c.fontSize,
      color: c.color,
      bg: bg ? `rgb(${bg.r},${bg.g},${bg.b})` : null,
      ratio: fg && bg ? Math.round(ratio([fg.r, fg.g, fg.b], [bg.r, bg.g, bg.b]) * 100) / 100 : null,
      rect: (() => { const x = t.getBoundingClientRect(); return [Math.round(x.x), Math.round(x.y), Math.round(x.width), Math.round(x.height)]; })(),
    };
  }
  const lede = document.querySelector('.welcome .lede, .welcome > p');
  if (lede) {
    const cs = getComputedStyle(lede);
    const w = lede.getBoundingClientRect().width;
    out.lede = { width: Math.round(w), fontSize: cs.fontSize, maxWidth: cs.maxWidth, cjkPerLine: Math.round(w / parseFloat(cs.fontSize)) };
  }
  const msgs = [...document.querySelectorAll('.msg')].map(m => {
    const cs = getComputedStyle(m);
    return { maxWidth: cs.maxWidth, width: Math.round(m.getBoundingClientRect().width), fontSize: cs.fontSize };
  });
  out.messages = msgs.slice(0, 3);
  const side = document.querySelector('#sidePanel, .side');
  out.side = { clientW: side.clientWidth, scrollH: side.scrollHeight, clientH: side.clientHeight, scrollbarPx: side.offsetWidth - side.clientWidth };
  const inp = document.querySelector('#textInput');
  if (inp) {
    const cs = getComputedStyle(inp);
    out.input = { width: Math.round(inp.getBoundingClientRect().width), fontSize: cs.fontSize, height: Math.round(inp.getBoundingClientRect().height) };
  }
  return out;
});
console.log(JSON.stringify(r, null, 2));
await b.close();
