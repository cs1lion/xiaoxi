import { chromium, expect } from '@playwright/test';

// Read-only verification of the running homepage and its real JS import graph.
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [], failures = [], writes = [], socketMessages = [];
  let parameterRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => failures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`));
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') {
      writes.push(`${route.request().method()} ${route.request().url()}`);
      return route.fulfill({ status: 403, json: { detail: 'read_only_bootstrap_test' } });
    }
    await route.continue();
  });
  // An expired unversioned module must never enter the updated import graph.
  await page.route('**/static/avatar/params.js*', async route => {
    if (!new URL(route.request().url()).searchParams.has('v')) {
      return route.fulfill({ contentType: 'text/javascript', body: 'export const PARAM_TABLE = {};' });
    }
    parameterRequests += 1;
    await route.continue();
  });
  page.on('response', response => {
    if (new URL(response.url()).pathname === '/static/avatar/params.js' && response.headers()['cache-control'] !== 'no-store') {
      errors.push('Parameter module may be cached across code updates');
    }
  });
  await page.routeWebSocket('**/ws', socket => socket.onMessage(message => socketMessages.push(message)));
  const homepage = await page.goto('http://127.0.0.1:8000/');
  expect(homepage.headers()['cache-control']).toBe('no-store');
  await expect(page.locator('.assistant-panel:not([data-interaction-preferences])')).toBeAttached();
  await expect(page.locator('#companionMount .companion-heading')).toBeVisible();
  await expect(page.locator('#textInput')).toBeVisible();
  await page.locator('#work-tasks-tab').click();
  await expect(page.locator('.assistant-panel:not([data-interaction-preferences])')).toBeVisible();
  const unexpectedSocketMessages = socketMessages.filter(message => {
    try { return JSON.parse(message).type !== 'config'; } catch { return true; }
  });
  if (errors.length || failures.length || writes.length || unexpectedSocketMessages.length) throw Error(JSON.stringify({ errors, failures, writes, unexpectedSocketMessages }));
  expect(parameterRequests).toBe(1);
  console.log(JSON.stringify({ result: 'PASS', scope: 'Live homepage bootstrap; GET only; mocked WebSocket; no model, audio, mic or user writes', errors, failures, writes,
    parameterRequests,
    companion: await page.locator('#companionMount').innerText(), taskMount: 'Mounted in the workspace task tab' }, null, 2));
} finally { await browser.close(); }
