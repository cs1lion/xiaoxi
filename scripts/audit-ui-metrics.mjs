/* Read-only UI metrics for the visual audit: overflow, contrast, avatar box, type scale. */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = process.env.AQI_ORIGIN || 'http://127.0.0.1:8000';
const out = 'eval/visual-audit-20261017';
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
await page.route('**/api/preferences/proactivity', r => r.fulfill({ json: { timezone: 'Asia/Shanghai', reminder_enabled: false } }));
for (const endpoint of ['tasks', 'reminders', 'notifications']) await page.route(`**/api/${endpoint}`, r => r.fulfill({ json: { items: [] } }));
await page.route('**/api/memories', r => r.fulfill({ json: { count: 0, items: [] } }));
await page.routeWebSocket('**/ws', socket => socket.onMessage(() => {}));
await page.goto(origin + '/?avatar=seed-daily-lids');
await page.waitForSelector('#avatarStage canvas', { timeout: 120000 });
await page.waitForTimeout(3000);

const metrics = await page.evaluate(() => {
  const parse = value => {
    const m = value.match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const parts = m[1].split(',').map(Number);
    return { r: parts[0], g: parts[1], b: parts[2], a: parts[3] ?? 1 };
  };
  const composite = (fg, bg) => ({
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a), a: 1,
  });
  const lum = c => {
    const chan = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
    return 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b);
  };
  const contrast = (a, b) => {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  };
  const info = selector => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const cs = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return {
      selector, text: (el.textContent || '').trim().slice(0, 80),
      color: cs.color, background: cs.backgroundColor,
      fontSize: cs.fontSize, lineHeight: cs.lineHeight,
      width: Math.round(rect.width), height: Math.round(rect.height),
      x: Math.round(rect.x), y: Math.round(rect.y),
    };
  };
  const panel = document.querySelector('.companion-panel');
  const hint = document.querySelector('.companion-hint');
  const heading = document.querySelector('.companion-heading h2');
  const panelBg = composite(parse(getComputedStyle(panel).backgroundColor), { r: 245, g: 245, b: 239, a: 1 });
  const contrastOf = el => {
    if (!el) return null;
    const fg = parse(getComputedStyle(el).color);
    return Number(contrast(composite(fg, panelBg), panelBg).toFixed(2));
  };
  return {
    viewport: { width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth },
    panel: info('.companion-panel'), hint: info('.companion-hint'), heading: info('.companion-heading h2'),
    contrast: { hint: contrastOf(hint), heading: contrastOf(heading) },
    avatarStage: (() => { const el = document.querySelector('#avatarStage'); const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }; })(),
    presence: info('#presenceCapsule'), modeStrip: info('#modeStrip'),
    composer: info('.composer'), suggestion: info('.suggestions button'),
    bodyFont: getComputedStyle(document.body).fontFamily,
  };
});

await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(900);
const mobile = await page.evaluate(() => {
  const el = document.querySelector('#avatarStage');
  const r = el.getBoundingClientRect();
  const card = document.querySelector('.suggestions button')?.getBoundingClientRect();
  return {
    viewport: { width: innerWidth, height: innerHeight, scrollWidth: document.documentElement.scrollWidth },
    avatarStage: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height), display: getComputedStyle(el).display },
    firstSuggestion: card ? { x: Math.round(card.x), y: Math.round(card.y), width: Math.round(card.width), height: Math.round(card.height) } : null,
  };
});

const payload = { capturedAt: new Date().toISOString(), url: page.url(), metrics, mobile };
await writeFile(`${out}/metrics.json`, JSON.stringify(payload, null, 2));
console.log(JSON.stringify(payload, null, 2));
await browser.close();
