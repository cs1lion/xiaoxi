// Desktop web audit for Xiaoxi /chat (mobile intentionally excluded).
// Screenshots + layout metrics + real contrast failures + focus-visible check.
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const OUT = path.resolve(import.meta.dirname);
const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const VIEWPORTS = [
  { name: 'web-1280', width: 1280, height: 800 },
  { name: 'web-1440', width: 1440, height: 900 },
  { name: 'web-1920', width: 1920, height: 1080 },
];

const browsers = await chromium.launch({ headless: true });
const report = [];

for (const vp of VIEWPORTS) {
  const page = await browsers.newPage({ viewport: { width: vp.width, height: vp.height } });
  await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, `${vp.name}.png`) });

  const data = await page.evaluate(() => {
    const rect = el => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom), right: Math.round(r.right) }; };
    const vis = el => {
      let cur = el;
      while (cur && cur !== document.body) {
        const c = getComputedStyle(cur);
        if (c.display === 'none' || c.visibility === 'hidden' || parseFloat(c.opacity) === 0) return false;
        if (cur.hasAttribute('hidden') || cur.getAttribute('aria-hidden') === 'true') return false;
        cur = cur.parentElement;
      }
      return true;
    };
    const parse = s => { const m = String(s).match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p[3] ?? 1 }; };
    const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
    const cr = (a, b) => { const l1 = lum(a), l2 = lum(b); return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05); };
    const bgOf = el => { let cur = el; while (cur && cur !== document.documentElement) { const c = parse(getComputedStyle(cur).backgroundColor); if (c && c.a > 0.6) return [c.r, c.g, c.b]; cur = cur.parentElement; } const b = parse(getComputedStyle(document.body).backgroundColor); return b ? [b.r, b.g, b.b] : [255, 255, 255]; };

    const side = document.querySelector('#sidePanel, .side');
    const chat = document.querySelector('#chat, .chat');
    const plate = document.querySelector('.plate-inner');
    const avatar = [...document.querySelectorAll('canvas')].find(c => !c.id);
    const composer = document.querySelector('.composer');
    const topbar = document.querySelector('.topbar');

    const sideRect = side && rect(side);
    const sideStyle = side && getComputedStyle(side);
    const cutOff = side ? [...side.querySelectorAll('*')].filter(el => {
      if (!vis(el)) return false;
      const r = el.getBoundingClientRect();
      if (r.height === 0) return false;
      return r.bottom > sideRect.bottom - 2;
    }).slice(0, 8).map(el => ({ tag: el.tagName + (el.id ? '#' + el.id : ''), text: (el.textContent || '').trim().slice(0, 24), bottom: Math.round(el.getBoundingClientRect().bottom) })) : [];

    // Longest line estimate for reading measure (chars per line) on body copy.
    const measure = el => {
      if (!el) return null;
      const cs = getComputedStyle(el);
      const w = el.getBoundingClientRect().width;
      const fs = parseFloat(cs.fontSize);
      const cjk = [...el.textContent].filter(ch => /[\u4e00-\u9fa5]/.test(ch)).length;
      const total = el.textContent.trim().length;
      return { width: Math.round(w), fontSize: fs, cjkRatio: total ? +(cjk / total).toFixed(2) : 0, charsPerLineCJK: Math.round(w / fs) };
    };

    const overlaps = (a, b) => a && b && !(a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y);
    const seal = document.querySelector('.plate-seal');
    const meta = document.querySelector('.plate-meta');
    const cap = document.querySelector('.plate-cap');

    const lowContrast = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!vis(el) || !el.firstChild || el.firstChild.nodeType !== 3) continue;
      const t = (el.textContent || '').trim();
      if (!t) continue;
      const c = getComputedStyle(el);
      const fg = parse(c.color);
      if (!fg) continue;
      const size = parseFloat(c.fontSize);
      const large = size >= 24 || (size >= 18.66 && parseInt(c.fontWeight, 10) >= 700);
      const need = large ? 3 : 4.5;
      const r = cr([fg.r, fg.g, fg.b], bgOf(el));
      if (r < need) lowContrast.push({ text: t.slice(0, 20), size: c.fontSize, ratio: Math.round(r * 100) / 100, need, color: c.color });
    }

    const histogram = {};
    for (const el of document.querySelectorAll('body *')) {
      if (!vis(el) || !el.firstChild || el.firstChild.nodeType !== 3 || !(el.textContent || '').trim()) continue;
      const s = parseFloat(getComputedStyle(el).fontSize);
      histogram[s] = (histogram[s] || 0) + 1;
    }

    return {
      page: { scrollHeight: document.scrollingElement.scrollHeight, viewport: innerHeight, horizontalOverflow: document.scrollingElement.scrollWidth - innerWidth },
      topbar: topbar && rect(topbar),
      side: sideRect, sideScroll: side ? { clientHeight: side.clientHeight, scrollHeight: side.scrollHeight, overflowY: sideStyle.overflowY } : null,
      cutOff,
      chat: chat && rect(chat),
      composer: composer && rect(composer),
      plate: plate && rect(plate),
      avatar: avatar ? { ...rect(avatar), aspect: +(avatar.getBoundingClientRect().width / avatar.getBoundingClientRect().height).toFixed(2), buffer: [avatar.width, avatar.height] } : null,
      gaps: {
        topbarToRoom: (() => { const room = document.querySelector('.room-label'); return room && topbar ? Math.round(room.getBoundingClientRect().top - topbar.getBoundingClientRect().bottom) : null; })(),
        welcomeToComposer: (() => { const w = document.querySelector('.welcome'); const c = composer; return w && c ? Math.round(c.getBoundingClientRect().top - w.getBoundingClientRect().bottom) : null; })(),
        plateToComposer: plate && composer ? Math.round(composer.getBoundingClientRect().top - plate.getBoundingClientRect().bottom) : null,
      },
      measure: {
        welcomeLede: measure(document.querySelector('.welcome .lede, .welcome > p')),
        botMessage: measure(document.querySelector('.msg.bot')),
        sideNote: measure(side && side.querySelector('p, .note')),
      },
      overlap: {
        sealVsMeta: overlaps(seal && seal.getBoundingClientRect(), meta && meta.getBoundingClientRect()),
        sealVsCap: overlaps(seal && seal.getBoundingClientRect(), cap && cap.getBoundingClientRect()),
      },
      lowContrast,
      fontHistogram: histogram,
      nodeCount: document.querySelectorAll('body *').length,
    };
  });

  // Focus visibility: tab through controls and see whether anything visibly changes.
  const focus = await page.evaluate(async () => {
    const seen = [];
    const els = [...document.querySelectorAll('button, input, select, a[href], [tabindex]:not([tabindex="-1"])')].filter(el => el.getBoundingClientRect().width > 0);
    for (const el of els.slice(0, 12)) {
      el.focus();
      const cs = getComputedStyle(el);
      seen.push({ tag: el.tagName + (el.id ? '#' + el.id : ''), outline: `${cs.outlineStyle} ${cs.outlineWidth}`, shadow: cs.boxShadow.slice(0, 40), isFocusVisible: el.matches(':focus-visible') });
    }
    return seen;
  });

  report.push({ viewport: vp, ...data, focus });
  await page.close();
}
await browsers.close();
writeFileSync(path.join(OUT, 'audit-web.json'), JSON.stringify(report, null, 2), 'utf8');
for (const r of report) {
  console.log(`\n[${r.viewport.name} ${r.viewport.width}x${r.viewport.height}] nodes=${r.nodeCount} pageScroll=${r.page.scrollHeight}/${r.page.viewport} hOverflow=${r.page.horizontalOverflow}`);
  console.log(`  side=${JSON.stringify(r.side)} scroll=${JSON.stringify(r.sideScroll)} cutOff=${r.cutOff.length}`);
  console.log(`  plate=${JSON.stringify(r.plate)} avatar=${JSON.stringify(r.avatar)}`);
  console.log(`  composer=${JSON.stringify(r.composer)} gaps=${JSON.stringify(r.gaps)}`);
  console.log(`  overlap=${JSON.stringify(r.overlap)} lowContrast=${r.lowContrast.length}`);
  console.log(`  focusVisible=${r.focus.filter(f => f.isFocusVisible).length}/${r.focus.length} outlineNone=${r.focus.filter(f => f.outline === 'none 0px').length}`);
}
