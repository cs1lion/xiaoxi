import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

// Offline fixture only: never bootstraps app or accesses provider credentials.
let configured = false, mode = 'success', submissions = 0;
let lastBody;
const server = createServer(async (req, res) => {
  if (req.url === '/api/music/capabilities') {
    res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ configured })); return;
  }
  if (req.url === '/api/music/create') {
    submissions++;
    let text = ''; for await (const chunk of req) text += chunk;
    lastBody = JSON.parse(text);
    res.setHeader('Content-Type', 'application/json');
    if (mode === 'wait') { res.on('close', () => {}); return; }
    if (mode === 'error') { res.statusCode = 502; res.end(JSON.stringify({ detail: '<script>secret-provider-key</script>' })); return; }
    res.end(JSON.stringify({ audio_base64: Buffer.from('offline-audio').toString('base64'), mime: 'audio/mpeg', rewritten_lyrics: '<script>unsafe</script>' })); return;
  }
  const name = req.url?.startsWith('/static/') ? req.url.slice('/static/'.length) : null;
  if (!['music.html', 'music.js', 'music.css'].includes(name)) { res.statusCode = 404; res.end(); return; }
  res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
  res.end(await readFile(`web/${name}`, 'utf8'));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  await page.addInitScript(() => {
    window.createdURLs = []; window.revokedURLs = [];
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); window.createdURLs.push(url); return url; };
    URL.revokeObjectURL = url => { window.revokedURLs.push(url); revoke(url); };
  });
  const url = `http://127.0.0.1:${server.address().port}/static/music.html`;
  await page.goto(url);
  await page.getByText('原创音乐服务未启用或未配置专用服务凭据。生成已禁用，不会调用服务商。', { exact: true }).waitFor();
  await page.locator('#permission').check();
  assert.equal(await page.locator('#create').isDisabled(), true);
  await page.locator('#musicForm').evaluate(form => form.dispatchEvent(new Event('submit', { cancelable: true })));
  assert.equal(submissions, 0);
  configured = true;
  await page.reload();
  await page.getByText('原创音乐服务已配置。勾选权限确认后才可手动生成（可能收费）。', { exact: true }).waitFor();
  assert.equal(await page.locator('#create').isDisabled(), true);
  await page.locator('#caption').fill('<img src=x onerror=alert(1)> original');
  await page.locator('#lyrics').fill('Original words');
  await page.locator('#permission').check();
  await page.locator('#create').click();
  await page.locator('#result').waitFor({ state: 'visible' });
  assert.equal(submissions, 1);
  assert.deepEqual(lastBody, { caption: '<img src=x onerror=alert(1)> original', lyrics: 'Original words', user_confirmed: true });
  assert.equal(await page.locator('img').count(), 0);
  assert.equal(await page.locator('script').count(), 1);
  assert.equal(await page.locator('#audio').getAttribute('autoplay'), null);
  await page.locator('#delete').click();
  assert.equal(await page.locator('#audio').getAttribute('src'), null);
  assert.equal(await page.evaluate(() => window.revokedURLs.length), 1);
  await page.locator('#create').click();
  await page.locator('#result').waitFor({ state: 'visible' });
  await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  assert.equal(await page.evaluate(() => window.revokedURLs.length), 2);
  mode = 'error';
  await page.locator('#create').click();
  await page.getByText('音乐生成未完成；已提交的任务可能继续处理并产生费用。', { exact: true }).waitFor();
  assert.equal((await page.locator('body').innerText()).includes('secret-provider-key'), false);
  mode = 'wait';
  await page.locator('#create').click();
  await page.locator('#stop').click();
  await page.getByText('已停止浏览器等待；不能取消服务商已提交任务，仍可能产生费用。请勿立即重复提交。', { exact: true }).waitFor();
  assert.equal(await page.locator('#result').isVisible(), false);
  console.log('PASS music browser: disabled guard, explicit consent, safe errors, Blob deletion/pagehide cleanup, stop notice; offline only');
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise(resolve => server.close(resolve));
}
