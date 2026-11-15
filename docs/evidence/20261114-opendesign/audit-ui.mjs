// Read-only UI audit for Xiaoxi /chat: overflow, tap targets, type scale,
// contrast (approx.), avatar aspect handling, and animation/CSS cost signals.
import { chromium } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const viewports = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 834, height: 1112 },
  { name: 'mobile', width: 390, height: 844 },
];

const ratioSource = `function lum(c) {
  const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
}
function ratio(a, b) {
  const l1 = lum(a), l2 = lum(b);
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}`;

const browser = await chromium.launch({ headless: true });
const out = [];
for (const vp of viewports) {
  const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
  await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(2000);
  const data = await page.evaluate(({ ratioSrc }) => {
    const parseColor = s => {
      const m = String(s).match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: p[0], g: p[1], b: p[2], a: p[3] === undefined ? 1 : p[3] };
    };
    const bgOf = el => {
      let cur = el;
      while (cur && cur !== document.documentElement) {
        const c = parseColor(getComputedStyle(cur).backgroundColor);
        if (c && c.a > 0.6) return [c.r, c.g, c.b];
        cur = cur.parentElement;
      }
      const body = parseColor(getComputedStyle(document.body).backgroundColor);
      return body ? [body.r, body.g, body.b] : [255, 255, 255];
    };
    const ratio = new Function('a', 'b', `${ratioSrc}; return ratio(a, b);`);

    const root = document.scrollingElement;
    const all = [...document.querySelectorAll('body *')];
    const described = el => (el.innerText || el.textContent || '').trim().slice(0, 40);

    const overflowX = root.scrollWidth - root.clientWidth;
    const wide = all.filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1);
    }).slice(0, 12).map(el => ({ tag: el.tagName + (el.id ? '#' + el.id : ''), cls: el.className?.toString().slice(0, 40), right: Math.round(el.getBoundingClientRect().right) }));

    const smallTargets = all.filter(el => {
      const c = getComputedStyle(el);
      if (c.display === 'none' || c.visibility === 'hidden') return false;
      const r = el.getBoundingClientRect();
      const interactive = el.matches('button, a, input, select, textarea, [role=button], [onclick], [tabindex]');
      if (!interactive) return false;
      return r.width > 0 && (r.width < 40 || r.height < 40);
    }).slice(0, 20).map(el => ({ tag: el.tagName + (el.id ? '#' + el.id : ''), text: described(el), w: Math.round(el.getBoundingClientRect().width), h: Math.round(el.getBoundingClientRect().height) }));

    const tinyText = all.filter(el => {
      const c = getComputedStyle(el);
      if (c.display === 'none' || c.visibility === 'hidden') return false;
      if (!el.firstChild || el.firstChild.nodeType !== 3) return false;
      const t = (el.textContent || '').trim();
      if (!t) return false;
      const size = parseFloat(c.fontSize);
      return size > 0 && size < 12;
    });
    const tinyTextSamples = tinyText.slice(0, 10).map(el => ({ text: described(el), size: getComputedStyle(el).fontSize }));

    const lowContrast = [];
    for (const el of all) {
      if (!el.firstChild || el.firstChild.nodeType !== 3) continue;
      const t = (el.textContent || '').trim();
      if (!t) continue;
      const c = getComputedStyle(el);
      if (c.display === 'none' || c.visibility === 'hidden' || parseFloat(c.opacity) < 0.1) continue;
      const fg = parseColor(c.color);
      if (!fg) continue;
      const bg = bgOf(el);
      const cr = ratio([fg.r, fg.g, fg.b], bg);
      const size = parseFloat(c.fontSize);
      const large = size >= 24 || (size >= 18.66 && parseInt(c.fontWeight, 10) >= 700);
      const need = large ? 3 : 4.5;
      if (cr < need) lowContrast.push({ text: described(el), size: c.fontSize, ratio: Math.round(cr * 100) / 100, need, color: c.color, bg: `rgb(${bg.join(',')})` });
    }

    const canvases = [...document.querySelectorAll('canvas')].map(c => {
      const r = c.getBoundingClientRect();
      const cx = Math.round(r.width), cy = Math.round(r.height);
      return { id: c.id || null, css: [cx, cy], buffer: [c.width, c.height], dpr: devicePixelRatio, aspectCss: +(cx / (cy || 1)).toFixed(2) };
    });

    const expensive = {};
    for (const el of all) {
      const c = getComputedStyle(el);
      if (c.backdropFilter && c.backdropFilter !== 'none') expensive.backdrop = (expensive.backdrop || 0) + 1;
      if (c.filter && c.filter !== 'none') expensive.filter = (expensive.filter || 0) + 1;
      if (c.animationName && c.animationName !== 'none') expensive.animation = (expensive.animation || 0) + 1;
      if (c.transitionProperty && c.transitionProperty !== 'none' && c.transitionProperty !== 'all') expensive.transition = (expensive.transition || 0) + 1;
      const bw = parseFloat(c.borderTopLeftRadius);
      if (bw > 0) expensive.rounded = (expensive.rounded || 0) + 1;
    }
    const anims = document.getAnimations ? document.getAnimations().length : -1;
    const paintCostly = all.filter(el => {
      const c = getComputedStyle(el);
      return (c.boxShadow && c.boxShadow !== 'none') || (c.filter && c.filter !== 'none');
    }).length;

    return {
      overflowX,
      wide,
      smallTargets,
      tinyTextCount: tinyText.length,
      tinyTextSamples,
      lowContrast: lowContrast.slice(0, 15),
      lowContrastCount: lowContrast.length,
      canvases,
      expensive,
      runningAnimations: anims,
      shadowOrFilterNodes: paintCostly,
      totalNodes: all.length,
    };
  }, { ratioSrc: ratioSource });
  out.push({ viewport: vp, ...data });
  await page.close();
}
await browser.close();
writeFileSync(path.join(import.meta.dirname, 'audit-ui.json'), JSON.stringify(out, null, 2), 'utf8');
for (const v of out) {
  console.log(`[${v.viewport.name} ${v.viewport.width}x${v.viewport.height}] overflowX=${v.overflowX} nodes=${v.totalNodes} tinyText=${v.tinyTextCount} lowContrast=${v.lowContrastCount} anims=${v.runningAnimations} shadowOrFilter=${v.shadowOrFilterNodes} smallTargets=${v.smallTargets.length} canvases=${v.canvases.map(c => `${c.id}:${c.css.join('x')}@${c.aspectCss}`).join(',')}`);
}
