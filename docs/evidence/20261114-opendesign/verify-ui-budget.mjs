// Xiaoxi WEB UI budget gate (desktop only; mobile intentionally out of scope).
// Exits 1 while any rule fails. Red baseline recorded in verify-web-budget-baseline.txt.
// Usage: node docs/evidence/20261114-opendesign/verify-ui-budget.mjs
import { chromium } from '@playwright/test';

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8000';
const VIEWPORTS = [
  { name: 'web-1280', width: 1280, height: 800 },
  { name: 'web-1440', width: 1440, height: 900 },
  { name: 'web-1920', width: 1920, height: 1080 },
];
const RULES = {
  maxTinyText: 20,        // visible text nodes below 12px
  minTargetPx: 24,        // WCAG 2.2 AA target size
  sideSlackPx: 8,         // sidebar may need to scroll, but must not hide content silently
  avatarAspect: [0.66, 0.85], // stable character framing across desktop heights
  maxComposerWidth: 1120, // reading/typing measure cap on wide screens
};

const browser = await chromium.launch({ headless: true });
const results = [];

for (const vp of VIEWPORTS) {
  const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
  await page.goto(`${BASE}/chat`, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1800);

  const probe = await page.evaluate((rules) => {
    const rect = el => el.getBoundingClientRect();
    // Visible = the element's own centre point is inside the viewport AND that point
    // resolves to the element or one of its descendants. `hit.contains(el)` is NOT
    // visibility: it means an overlay/ancestor paints over the element.
    const hitTest = el => {
      const r = rect(el);
      if (r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) return false;
      const x = Math.min(Math.max(r.x + r.width / 2, 0), innerWidth - 1);
      const y = Math.min(Math.max(r.y + r.height / 2, 0), innerHeight - 1);
      const hit = document.elementFromPoint(x, y);
      if (!hit) return false;
      return el === hit || el.contains(hit);
    };
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
    const ratio = (a, b) => {
      const l1 = lum(a), l2 = lum(b);
      return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    };
    const bgOf = el => {
      let cur = el;
      while (cur && cur !== document.documentElement) {
        const c = parse(getComputedStyle(cur).backgroundColor);
        if (c && c.a > 0.6) return [c.r, c.g, c.b];
        cur = cur.parentElement;
      }
      const b = parse(getComputedStyle(document.body).backgroundColor);
      return b ? [b.r, b.g, b.b] : [255, 255, 255];
    };

    const all = [...document.querySelectorAll('body *')];
    const textNodes = all.filter(el => el.firstChild && el.firstChild.nodeType === 3 && (el.textContent || '').trim() && hitTest(el));

    const tiny = textNodes.filter(el => parseFloat(getComputedStyle(el).fontSize) < 12);
    const smallTargets = all.filter(el => el.matches('button, a, select, input, textarea, [role=button], [tabindex]') && hitTest(el))
      .filter(el => { const r = rect(el); return r.width > 0 && r.height > 0 && Math.min(r.width, r.height) < rules.minTargetPx; });

    const lowContrast = [];
    for (const el of textNodes) {
      const c = getComputedStyle(el);
      const fg = parse(c.color);
      if (!fg) continue;
      const size = parseFloat(c.fontSize);
      const large = size >= 24 || (size >= 18.66 && parseInt(c.fontWeight, 10) >= 700);
      const need = large ? 3 : 4.5;
      const cr = ratio([fg.r, fg.g, fg.b], bgOf(el));
      if (cr < need) lowContrast.push({ text: (el.textContent || '').trim().slice(0, 16), size: c.fontSize, cr: Math.round(cr * 100) / 100, need });
    }

    const side = document.querySelector('#sidePanel, .side');
    const canvas = [...document.querySelectorAll('canvas')].find(c => !c.id);
    const composer = document.querySelector('.composer');
    return {
      overflowX: document.scrollingElement.scrollWidth - innerWidth,
      tiny: tiny.length,
      tinySamples: tiny.slice(0, 5).map(el => `${getComputedStyle(el).fontSize} ${(el.textContent || '').trim().slice(0, 10)}`),
      smallTargets: smallTargets.map(el => `${el.tagName}${el.id ? '#' + el.id : ''} ${Math.round(rect(el).width)}x${Math.round(rect(el).height)} ${(el.textContent || '').trim().slice(0, 8)}`),
      lowContrast,
      sideSlack: side ? side.scrollHeight - side.clientHeight : 0,
      sideBarPx: side ? side.offsetWidth - side.clientWidth : 0,
      avatarAspect: canvas && rect(canvas).height > 0 ? +(rect(canvas).width / rect(canvas).height).toFixed(2) : null,
      composerWidth: composer ? Math.round(rect(composer).width) : null,
    };
  }, RULES);

  const checks = [
    { name: '无横向溢出', ok: probe.overflowX <= 0, detail: `overflowX=${probe.overflowX}` },
    { name: `<12px 可见文本 ≤ ${RULES.maxTinyText}`, ok: probe.tiny <= RULES.maxTinyText, detail: `${probe.tiny} 处：${probe.tinySamples.join(' / ')}` },
    { name: `对比度不达标 = 0`, ok: probe.lowContrast.length === 0, detail: probe.lowContrast.map(x => `${x.size} "${x.text}" ${x.cr}/${x.need}`).join(' | ') || 'ok' },
    { name: `侧栏内容不被静默截断（slack ≤ ${RULES.sideSlackPx}px）`, ok: probe.sideSlack <= RULES.sideSlackPx, detail: `slack=${probe.sideSlack}px, 滚动条=${probe.sideBarPx}px` },
    { name: `触控/点击目标 ≥ ${RULES.minTargetPx}px`, ok: probe.smallTargets.length === 0, detail: probe.smallTargets.slice(0, 6).join(' | ') || 'ok' },
    { name: `形象卡比例 ${RULES.avatarAspect[0]}–${RULES.avatarAspect[1]}`, ok: probe.avatarAspect === null || (probe.avatarAspect >= RULES.avatarAspect[0] && probe.avatarAspect <= RULES.avatarAspect[1]), detail: `aspect=${probe.avatarAspect}` },
    { name: `输入区宽度 ≤ ${RULES.maxComposerWidth}px`, ok: probe.composerWidth === null || probe.composerWidth <= RULES.maxComposerWidth, detail: `width=${probe.composerWidth}` },
  ];
  results.push({ viewport: vp, checks });
  await page.close();
}

await browser.close();
let failed = 0;
for (const r of results) {
  console.log(`[${r.viewport.name} ${r.viewport.width}x${r.viewport.height}]`);
  for (const c of r.checks) {
    if (!c.ok) failed += 1;
    console.log(`  ${c.ok ? 'PASS' : 'FAIL'}  ${c.name} — ${c.detail}`);
  }
}
console.log(`\n${failed === 0 ? 'ALL PASS' : `${failed} rule(s) failing`}`);
process.exit(failed === 0 ? 0 : 1);
