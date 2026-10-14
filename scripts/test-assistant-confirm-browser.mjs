// Real app/confirmation DOM with isolated WebSocket, audio and HTTP fixtures.
// No application server, user data, credentials, microphone or real action runs.
import { chromium } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [], requests = [], checks = [];
page.setDefaultTimeout(8000);
page.on('pageerror', error => errors.push(error.message));
await page.route('http://assistant-confirm.test/**', async route => {
  const url = new URL(route.request().url());
  if (url.pathname === '/static/avatar/index.js') {
    return route.fulfill({ contentType: 'text/javascript', body: `
      const noop = () => {};
      export const avatar = new Proxy({ emotion: {set: noop}, rendererKind:'svg',
        onMessage: message => window.confirmHarness.events.push(message),
        speak: text => window.confirmHarness.spoken.push(text),
      }, { get: (value,key) => value[key] || noop });
      export const boot = async () => {};
    ` });
  }
  if (url.pathname.startsWith('/api/')) {
    requests.push({ path: url.pathname, method: route.request().method() });
    const data = url.pathname === '/api/persona'
      ? { name: '测试', tagline: '离线确认测试', voices: ['base'], default_voice: 'base' }
      : url.pathname === '/api/preferences/proactivity'
        ? { timezone: 'Asia/Shanghai', reminder_enabled: false } : { count: 0, items: [] };
    return route.fulfill({ json: data });
  }
  const relative = url.pathname === '/' ? 'web/index.html' : 'web/' + url.pathname.replace(/^\/static\//, '');
  const absolute = path.resolve(root, relative);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try {
    const body = await readFile(absolute);
    const contentType = absolute.endsWith('.js') || absolute.endsWith('.mjs') ? 'text/javascript'
      : absolute.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({ body, contentType });
  } catch { return route.fulfill({ status: 404, body: 'not found' }); }
});
await page.addInitScript(() => {
  const h = window.confirmHarness = { sent: [], events: [], spoken: [], sources: [], decodeWait: [], holdDecode: false };
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() {
      this.readyState = 0; h.ws = this;
      setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'fixture' }); }, 0);
    }
    send(value) {
      if (this.readyState !== 1) throw Error('socket closed');
      h.sent.push(typeof value === 'string' ? JSON.parse(value) : { binary: true });
    }
    emit(message) { return this.onmessage?.({ data: JSON.stringify(message) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
  const node = () => ({ connect() { return this; }, disconnect() {}, gain: { value: 1 } });
  class AC {
    constructor() { this.sampleRate = 48000; this.currentTime = 0; this.state = 'running'; this.destination = node(); }
    resume() { this.state = 'running'; return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createGain() { return node(); }
    createAnalyser() { return { ...node(), fftSize: 1024, getFloatTimeDomainData(values) { values.fill(0); } }; }
    createBufferSource() {
      return { ...node(), onended: null, stopped: false,
        start() { h.sources.push(this); }, stop() { this.stopped = true; this.onended?.(); } };
    }
    decodeAudioData() {
      const buffer = { duration: 2, length: 16000, numberOfChannels: 1 };
      return h.holdDecode ? new Promise(resolve => h.decodeWait.push(() => resolve(buffer))) : Promise.resolve(buffer);
    }
  }
  window.AudioContext = AC;
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia: () => { throw Error('confirmation test must never request microphone'); },
  } });
  h.finishAudio = () => { for (const source of h.sources) if (!source.stopped) source.onended?.(); };
});
const sent = () => page.evaluate(() => confirmHarness.sent.filter(value => !value.binary));
const emit = message => page.evaluate(value => confirmHarness.ws.emit(value), message);
const resultArea = page.locator('section[aria-label="行动确认与结果"]');
const assistantError = page.locator('.assistant-panel').filter({ has: resultArea }).locator(':scope > .assistant-error');
async function fresh() {
  await page.goto('http://assistant-confirm.test/');
  await page.waitForFunction(() => window.confirmHarness?.ws?.readyState === 1);
  await resultArea.waitFor({ state: 'attached' });
  // The real workspace keeps action cards under the explicit "待办" tab.
  await page.locator('#work-tasks-tab').click();
}
async function textTurn(text) {
  await page.locator('#textInput').fill(text); await page.locator('#sendBtn').click();
  return (await sent()).filter(value => value.type === 'text').at(-1).turn_id;
}
async function offer(suffix) {
  const turn_id = await textTurn(`创建测试事项 ${suffix}`);
  await emit({ type: 'thinking', turn_id });
  await emit({ type: 'assistant_result', turn_id, result: { run_id: `run-${suffix}`, status: 'waiting_confirmation',
    text: `待确认 ${suffix}`, receipts: [], confirmation: { id: `confirmation-${suffix}`, tool: 'task.create', arguments: { title: `测试 ${suffix}` } } } });
  await emit({ type: 'done', turn_id, text: `待确认 ${suffix}` });
  await resultArea.getByRole('button', { name: '确认执行', exact: true }).waitFor().catch(async error => {
    throw new Error(`${error.message}; ${JSON.stringify(await page.evaluate(() => ({
      sent: confirmHarness.sent, events: confirmHarness.events,
      result: document.querySelector('section[aria-label="行动确认与结果"]').textContent,
      error: document.querySelector('.assistant-error').textContent,
    })))}`);
  });
  return turn_id;
}
async function confirm(approve = true) {
  await resultArea.getByRole('button', { name: approve ? '确认执行' : '不执行', exact: true }).click();
  return (await sent()).filter(value => value.type === 'assistant_confirm').at(-1);
}
function completed(suffix, text = `已完成 ${suffix}`) {
  return { run_id: `run-${suffix}`, status: 'completed', text,
    receipts: [{ tool: 'task.create', status: 'succeeded', data: { title: `测试 ${suffix}` } }] };
}
function assertNoRestConfirmation() {
  assert.deepEqual(requests.filter(value => /\/assistant\/runs\/.*\/confirm$/.test(value.path)), [], 'WS path must not fall back to REST');
}

try {
  await fresh(); const initial = await offer('approve'); const approved = await confirm();
  assert.notEqual(approved.turn_id, initial);
  assert.deepEqual({ type: approved.type, run_id: approved.run_id, confirmation_id: approved.confirmation_id, approve: approved.approve },
    { type: 'assistant_confirm', run_id: 'run-approve', confirmation_id: 'confirmation-approve', approve: true });
  assert.ok(await resultArea.getByRole('button', { name: '确认执行', exact: true }).isDisabled());
  assert.ok(await resultArea.getByRole('button', { name: '不执行', exact: true }).isDisabled());
  await page.evaluate(() => {
    const controls = document.querySelector('section[aria-label="行动确认与结果"] .assistant-row');
    for (const control of controls.children) control.click();
  });
  assert.equal((await sent()).filter(value => value.type === 'assistant_confirm').length, 1, 'double click does not resubmit');
  await emit({ type: 'thinking', turn_id: approved.turn_id });
  await emit({ type: 'assistant_result', turn_id: approved.turn_id, result: completed('approve') });
  await resultArea.getByText('已完成 approve', { exact: true }).waitFor();
  assert.equal(await resultArea.locator('.assistant-card').count(), 1, 'pending result resolves the callback without rendering twice');
  await emit({ type: 'text_delta', turn_id: approved.turn_id, delta: '测试任务已经创建。' });
  await emit({ type: 'audio', turn_id: approved.turn_id, text: '测试任务已经创建。', data: 'AA==', emotion: 'base' });
  await page.waitForFunction(() => confirmHarness.sources.length === 1);
  assert.equal(await page.locator('#avatarStage').getAttribute('data-interaction'), 'speaking');
  await emit({ type: 'done', turn_id: approved.turn_id, text: '测试任务已经创建。' });
  assert.equal(await page.locator('#avatarStage').getAttribute('data-interaction'), 'speaking', 'done cannot end active audio');
  await page.evaluate(() => confirmHarness.finishAudio());
  await page.waitForFunction(() => document.querySelector('#avatarStage').dataset.interaction !== 'speaking');
  assert.deepEqual(await page.evaluate(() => confirmHarness.spoken), ['测试任务已经创建。']);
  assertNoRestConfirmation(); checks.push('approval is one tagged WS action, one result card and speech follows playback completion');

  await fresh(); await offer('reject'); const rejected = await confirm(false);
  assert.equal(rejected.approve, false);
  await emit({ type: 'assistant_result', turn_id: rejected.turn_id, result: { run_id: 'run-reject', status: 'rejected', text: '已取消测试操作', receipts: [] } });
  await emit({ type: 'done', turn_id: rejected.turn_id, text: '已取消测试操作' });
  await resultArea.getByText('已取消测试操作', { exact: true }).waitFor();
  assert.equal(await resultArea.locator('.assistant-card').count(), 1); assertNoRestConfirmation();
  checks.push('rejection uses the same WS confirmation path without an execution receipt');

  await fresh(); await offer('tts'); const tts = await confirm();
  await emit({ type: 'error', turn_id: tts.turn_id, recoverable: true, stage: 'tts', message: '语音生成失败，文字仍可阅读。' });
  await emit({ type: 'assistant_result', turn_id: tts.turn_id, result: completed('tts') });
  await emit({ type: 'done', turn_id: tts.turn_id, text: '已完成 tts', delivery: { text_status: 'complete', audio_status: 'failed' } });
  await resultArea.getByText('已完成 tts', { exact: true }).waitFor();
  assert.equal(await resultArea.locator('.assistant-card').count(), 1);
  assert.match(await resultArea.innerText(), /succeeded/); assert.equal(await page.evaluate(() => confirmHarness.sources.length), 0);
  assert.equal(await assistantError.textContent(), '');
  checks.push('recoverable TTS failure preserves the confirmed result and reaches done');

  await fresh(); await offer('cancel'); const cancelled = await confirm();
  await page.evaluate(() => { confirmHarness.holdDecode = true; });
  await emit({ type: 'audio', turn_id: cancelled.turn_id, text: '迟到播报', data: 'AA==' });
  await page.waitForFunction(() => confirmHarness.decodeWait.length === 1);
  const newer = await textTurn('新问题');
  await assistantError.filter({ hasText: '不会自动重试' }).waitFor();
  await emit({ type: 'assistant_result', turn_id: cancelled.turn_id, result: completed('cancel', '过期结果不得出现') });
  await emit({ type: 'audio', turn_id: cancelled.turn_id, text: '过期音频不得播放', data: 'AA==' });
  await emit({ type: 'done', turn_id: cancelled.turn_id, text: '过期结束不得改写' });
  await page.evaluate(() => { for (const resolve of confirmHarness.decodeWait.splice(0)) resolve(); });
  await emit({ type: 'done', turn_id: newer, text: '新问题完成' });
  assert.equal(await resultArea.getByText('过期结果不得出现', { exact: true }).count(), 0);
  assert.equal(await page.evaluate(() => confirmHarness.sources.length), 0);
  assert.equal((await sent()).filter(value => value.type === 'assistant_confirm').length, 1);
  assertNoRestConfirmation(); checks.push('new turn rejects pending confirmation and fences stale result/audio/late decode without retry');

  await fresh(); await offer('stop'); const stopped = await confirm();
  await page.locator('#stopBtn').click();
  await assistantError.filter({ hasText: '不会自动重试' }).waitFor();
  await emit({ type: 'assistant_result', turn_id: stopped.turn_id, result: completed('stop', '停止后的过期结果') });
  assert.equal(await resultArea.getByText('停止后的过期结果', { exact: true }).count(), 0);
  assert.equal((await sent()).filter(value => value.type === 'assistant_confirm').length, 1); assertNoRestConfirmation();
  checks.push('explicit stop rejects pending confirmation without retry or late result rendering');

  await fresh(); await offer('close'); const closed = await confirm();
  await page.evaluate(() => confirmHarness.ws.close());
  await assistantError.filter({ hasText: '不会自动重试' }).waitFor();
  await emit({ type: 'assistant_result', turn_id: closed.turn_id, result: completed('close', '断线后的过期结果') });
  await resultArea.getByRole('button', { name: '确认执行', exact: true }).click();
  await assistantError.filter({ hasText: '连接未就绪' }).waitFor();
  assert.equal(await resultArea.getByText('断线后的过期结果', { exact: true }).count(), 0);
  assert.equal((await sent()).filter(value => value.type === 'assistant_confirm').length, 1); assertNoRestConfirmation();
  checks.push('disconnect rejects pending confirmation; explicit offline retry has no WS/REST fallback');

  await fresh(); await offer('missing'); const missing = await confirm();
  await emit({ type: 'done', turn_id: missing.turn_id, text: '服务未交付行动结果' });
  await assistantError.filter({ hasText: '未收到行动结果' }).waitFor();
  assert.equal((await sent()).filter(value => value.type === 'assistant_confirm').length, 1); assertNoRestConfirmation();
  checks.push('done without an action result rejects the wait instead of silently succeeding');
  assert.deepEqual(errors, [], 'no uncaught browser errors');
  console.log(JSON.stringify({ ok: true, checks }, null, 2));
} finally { await browser.close(); }
