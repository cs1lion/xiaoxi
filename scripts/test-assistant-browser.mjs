import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

// Isolated frontend fixture: no replacement application server or live user DB.
const tasks = [];
const reminders = [];
const notices = [{ id: 'n1', content: '<script>unsafe</script>', category: 'reminder', status: 'pending', missed: false }];
const prefs = { timezone: 'UTC', reminder_enabled: true };
const server = createServer(async (req, res) => {
  if (['/assistant.js', '/task-card.js', '/companion.js', '/note-proposals.js', '/interaction-preferences.js', '/context-usage.js'].includes(req.url)) {
    res.setHeader('Content-Type', 'text/javascript'); res.end(await readFile('web' + req.url, 'utf8')); return;
  }
  if (req.url === '/api/interaction/preferences') { res.setHeader('Content-Type', 'application/json'); res.end('{"items":[]}'); return; }
  if (req.url === '/') {
    res.setHeader('Content-Type', 'text/html');
    res.end('<html><body><aside id="assistantMount"></aside><script type="module">import {bootAssistant,showAssistantResult} from "/assistant.js"; window.showAssistantResult=showAssistantResult; bootAssistant();</script></body></html>'); return;
  }
  let body = ''; for await (const piece of req) body += piece;
  const data = body ? JSON.parse(body) : {};
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/api/tasks' && req.method === 'POST') {
    const task = { ...data, id: 'task1', status: 'open', version: 1 }; tasks.push(task); res.end(JSON.stringify(task)); return;
  }
  if (req.url === '/api/tasks') { res.end(JSON.stringify({ items: tasks })); return; }
  if (req.url === '/api/reminders') { res.end(JSON.stringify({ items: reminders })); return; }
  if (req.url === '/api/notifications') { res.end(JSON.stringify({ items: notices })); return; }
  if (req.url === '/api/preferences/proactivity') { Object.assign(prefs, data); res.end(JSON.stringify(prefs)); return; }
  if (req.url === '/api/notifications/n1/ack') { notices[0].status = 'presented'; res.end('{}'); return; }
  if (req.url?.endsWith('/confirm')) { res.end(JSON.stringify({ status: 'completed', text: '已创建', receipts: [{ tool: 'task.create', status: 'succeeded', data: { title: 'demo' } }] })); return; }
  res.end('{}');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(() => !!document.querySelector('summary'), { timeout: 5000 }).catch(error => { throw new Error(`${error.message}; pageerrors=${JSON.stringify(errors)}`); });
  await page.getByText('任务与提醒', { exact: true }).click();
  await page.getByRole('textbox', { name: '任务名称', exact: true }).fill('周报');
  await page.getByRole('button', { name: '新增任务', exact: true }).click();
  await page.locator('[data-task-id="task1"]').getByText('周报', { exact: true }).waitFor();
  await page.evaluate(() => window.showAssistantResult({ run_id: 'r1', text: '确认操作', receipts: [], confirmation: { id: 'c1', tool: 'task.create', arguments: { title: 'demo' } } }));
  await page.getByRole('button', { name: '确认执行', exact: true }).click();
  await page.getByText('已创建', { exact: true }).waitFor();
  assert.equal(notices[0].status, 'presented');
  assert.equal(tasks.length, 1);
  assert.deepEqual(errors, []);
  assert.equal(await page.locator('script').count(), 1);
  console.log('PASS browser: panel, task creation, confirmation, receipt, inbox ACK, safe text rendering');
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
