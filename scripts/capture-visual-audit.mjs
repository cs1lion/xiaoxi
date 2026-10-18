/* Visual audit capture: main workbench (VRM) + drawer/settings + avatar lab.
 * Read-only: uses the running local server, mocks only REST/WS fixtures.
 */
import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const origin = process.env.AQI_ORIGIN || 'http://127.0.0.1:8000';
const out = 'eval/visual-audit-20261017';
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const errors = [];

async function mock(page) {
  await page.route('**/api/preferences/proactivity', r => r.fulfill({ json: { timezone: 'Asia/Shanghai', reminder_enabled: false } }));
  for (const endpoint of ['tasks', 'reminders', 'notifications']) {
    await page.route(`**/api/${endpoint}`, r => r.fulfill({ json: { items: [] } }));
  }
  await page.route('**/api/memories', r => r.fulfill({ json: { count: 0, items: [] } }));
  await page.route('**/api/executive/commissions', r => r.fulfill({ json: { items: [] } }));
  await page.route('**/api/executive/telemetry', r => r.fulfill({ json: { window_seconds: 86400, calls: 0, succeeded: 0, failed: 0, success_rate: null, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, avg_ttft_ms: 0, max_ttft_ms: 0, by_agent: [], token_ranking: [], spinning_alerts: [] } }));
  await page.route('**/api/executive/rubrics', r => r.fulfill({ json: { items: [] } }));
  await page.routeWebSocket('**/ws', socket => socket.onMessage(() => {}));
}

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on('pageerror', e => errors.push('main: ' + e.message));
  await mock(page);
  await page.goto(origin + '/?avatar=seed-daily-lids');
  await page.waitForSelector('#avatarStage canvas', { timeout: 120000 });
  await page.waitForFunction(() => window.__avatarReady === true || document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'), {}, { timeout: 120000 });
  await page.waitForTimeout(3500);
  await page.screenshot({ path: `${out}/main-vrm-desktop.png`, fullPage: true });
  await page.locator('#avatarStage').screenshot({ path: `${out}/main-vrm-avatar.png` });

  await page.click('#drawerToggle');
  await page.waitForTimeout(500);
  await page.click('#drawerOps');
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/main-drawer-ops.png`, fullPage: true });
  await page.click('#drawerClose');

  await page.click('#settingsToggle');
  await page.waitForTimeout(600);
  await page.locator('#settingsPanel').screenshot({ path: `${out}/main-settings.png` });
  await page.click('#settingsToggle');
  await page.waitForTimeout(300);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${out}/main-vrm-mobile.png`, fullPage: true });

  const lab = await browser.newPage({ viewport: { width: 1280, height: 920 } });
  lab.on('pageerror', e => errors.push('lab: ' + e.message));
  await lab.goto(origin + '/static/avatar_lab.html?model=daily-lids');
  await lab.waitForFunction(() => window.__avatarReady === true, {}, { timeout: 120000 });
  await lab.waitForTimeout(2500);
  await lab.screenshot({ path: `${out}/avatar-lab-front.png` });
  await lab.locator('#viewport').screenshot({ path: `${out}/avatar-lab-stage.png` });

  await lab.check('#eyeFocus');
  await lab.waitForTimeout(1200);
  await lab.locator('#viewport').screenshot({ path: `${out}/avatar-lab-face.png` });

  await lab.mouse.move(1100, 420);
  await lab.waitForTimeout(1200);
  await lab.locator('#viewport').screenshot({ path: `${out}/avatar-lab-gaze.png` });

  console.log(JSON.stringify({ out, errors }, null, 2));
} finally {
  await browser.close();
}
