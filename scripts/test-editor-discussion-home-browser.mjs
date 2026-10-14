// Real homepage/editor modules with fully isolated HTTP, WebSocket, devices and audio.
// The visible prose and replies below are fictional fixtures; no real LLM or DB is used.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const base = 'http://editor-discussion.test';
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const checks = [], errors = [], requests = [], sessions = new Map(), insights = new Map();
let serial = 0;
page.setDefaultTimeout(8000);
page.on('pageerror', error => errors.push(error.message));
await page.clock.install({ time: new Date('2026-10-13T12:00:00Z') });
const bodyText = '我们计划给社区增加每周一次的读书活动，想让更多人愿意留下来交流。现阶段会先邀请老成员参与，再逐步向附近居民开放。我们准备提供阅读材料、安排主持人并收集参加者的建议。活动有了固定节奏后，希望它能够成为大家交换经验和认识邻居的地方。';
const pointText = '先明确你希望谁留下来。老成员和新邻居的顾虑不同，一次活动很难同时解决。';
const layoutOnly = process.argv.includes('--layout-only');
const layouts = [];
await page.route('**/*', async route => {
  const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
  if (url.origin !== base) return route.abort();
  if (pathname === '/static/avatar/index.js') return route.fulfill({ contentType: 'text/javascript', body: `
    const noop=()=>{};
    export const avatar=new Proxy({emotion:{set:noop},rendererKind:'svg',
      onMessage:m=>window.h.avatarEvents.push(m),stopSpeaking:()=>window.h.stopped++,
      speak:text=>window.h.spoken.push(text)}, {get:(o,k)=>o[k]||noop});
    export const boot=async()=>{};` });
  if (pathname.startsWith('/api/')) {
    const payload = request.postData() ? request.postDataJSON() : null;
    requests.push({ path: pathname, method: request.method(), payload });
    const reply = (json, status = 200) => route.fulfill({ status, json });
    if (pathname === '/api/editor-insights/status') return reply({ available: true, pause_ms: 12000, cooldown_ms: 90000, max_checks: 5 });
    if (pathname === '/api/editor-insights/state') { sessions.set(payload.session_id, payload); return reply({ status: 'quiet' }); }
    if (pathname === '/api/editor-insights/analyze') {
      const insight = { id: `fixture-point-${++serial}`, session_id: payload.session_id, draft_revision: payload.draft_revision,
        point: pointText, anchor: payload.body.slice(0, 39), teaser: '先明确你希望谁留下来。', expires_at: await page.evaluate(() => Date.now() / 1000 + 300) };
      insights.set(insight.id, insight); return reply({ status: 'insight', insight, checks: serial });
    }
    if (/^\/api\/editor-insights\/[^/]+\/speak$/.test(pathname)) {
      const insight = insights.get(pathname.split('/')[3]);
      if (!insight || insight.session_id !== payload.session_id || insight.draft_revision !== payload.draft_revision) return reply({ detail: 'superseded' }, 409);
      return reply({ id: insight.id, draft_revision: insight.draft_revision, text: insight.point, audio: 'dGVzdA==', format: 'wav' });
    }
    if (/^\/api\/editor-insights\/[^/]+\/(feedback|dismiss)$/.test(pathname)) return reply({ status: 'quiet', feedback: payload.value });
    if (request.method() !== 'GET') throw Error(`Unexpected persistent write ${pathname}`);
    if (pathname === '/api/persona') return reply({ name: '阿栖', tagline: '一起写，也一起想', voices: ['base'], default_voice: 'base' });
    if (pathname === '/api/notes/selection') return reply({ revision: 0, note_id: null, selected_version: null });
    if (pathname === '/api/threads/selection') return reply({ thread_id: null });
    if (pathname === '/api/activity') return reply({ selection: { note_id: null }, note: null, thread: null });
    return reply({ count: 0, items: [] });
  }
  const absolute = path.resolve(root, pathname === '/' ? 'web/index.html' : `web/${pathname.replace(/^\/static\//, '')}`);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try { return route.fulfill({ body: await readFile(absolute), contentType: /\.(m?js)$/.test(absolute) ? 'text/javascript' : absolute.endsWith('.css') ? 'text/css' : 'text/html' }); }
  catch { return route.fulfill({ status: 404, body: 'fixture asset unavailable' }); }
});
await page.addInitScript(() => {
  const h = window.h = { sent: [], processors: [], sources: [], tracks: [], avatarEvents: [], spoken: [], stopped: 0, holdDecode: false, decodeWait: [] };
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { this.readyState = 0; this.bufferedAmount = 0; h.ws = this; setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'fixture-home' }); }, 0); }
    send(data) { if (this.readyState !== 1) throw Error('fixture socket closed'); h.sent.push(typeof data === 'string' ? JSON.parse(data) : { binary: true, bytes: data.byteLength }); }
    emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
    close() { this.readyState = 3; this.onclose?.({}); }
  }
  window.WebSocket = WS;
  const node = () => ({ connect() { return this; }, disconnect() {}, gain: { value: 1 } });
  class AC {
    constructor() { this.sampleRate = 48000; this.state = 'running'; this.destination = node(); }
    get currentTime() { return performance.now() / 1000; }
    resume() { return Promise.resolve(); } close() { return Promise.resolve(); }
    createGain() { return node(); }
    createAnalyser() { return { ...node(), fftSize: 512, getFloatTimeDomainData: a => a.fill(0) }; }
    createMediaStreamSource() { return node(); }
    createScriptProcessor(size) { const processor = { ...node(), size, context: this, onaudioprocess: null }; h.processors.push(processor); return processor; }
    createBuffer(channels, length, rate) { const data = new Float32Array(length); return { length, duration: length / rate, getChannelData: () => data }; }
    createBufferSource() { return { ...node(), onended: null, stopped: false, start() { h.sources.push(this); }, stop() { this.stopped = true; this.onended?.(); } }; }
    decodeAudioData() { return h.holdDecode ? new Promise(resolve => h.decodeWait.push(resolve)) : Promise.resolve({ duration: 2 }); }
  }
  window.AudioContext = AC;
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: async () => {
    const track = { stopped: false, stop() { this.stopped = true; }, getSettings() { return {}; }, addEventListener() {}, removeEventListener() {} };
    h.tracks.push(track); return { getTracks: () => [track], getAudioTracks: () => [track] };
  } } });
  h.feed = () => {
    const processor = [...h.processors].reverse().find(item => item.onaudioprocess);
    if (!processor) throw Error('No fake microphone processor');
    for (let index = 0; index < 8; index++) {
      const samples = new Float32Array(processor.size); for (let i = 0; i < samples.length; i++) samples[i] = .12 * Math.sin(i * .17);
      processor.onaudioprocess({ playbackTime: processor.context.currentTime, inputBuffer: { length: samples.length, getChannelData: () => samples }, outputBuffer: { getChannelData: () => new Float32Array(processor.size) } });
    }
  };
});
const sent = () => page.evaluate(() => h.sent.filter(message => !message.binary));
const emit = data => page.evaluate(message => h.ws.emit(message), data);
const tick = async duration => { await page.clock.fastForward(duration); await page.evaluate(() => Promise.resolve()); };
const count = suffix => requests.filter(item => item.path.endsWith(`/${suffix}`)).length;
const check = (okay, label) => { assert.ok(okay, label); checks.push(label); };
let frame, editor;
async function chooseInsight(text, first = false) {
  if (!await page.locator('body').evaluate(body => body.classList.contains('is-writing'))) await page.locator('#writingToggle').click();
  await expect(page.locator('body')).toHaveClass(/is-writing/);
  await editor.locator('#title').fill('社区读书活动 · 虚构示例'); await editor.locator('#body').fill(text);
  if (first) { await editor.locator('#editorInsightToggle').click(); await expect(editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true'); }
  await tick(first ? 12001 : 90001); await expect(editor.locator('#editorInsightBubble')).toBeVisible();
  const insight = [...insights.values()].at(-1);
  await editor.locator('#editorInsightBubble').click(); await expect(editor.locator('#editorInsightDiscuss')).toBeVisible();
  await editor.locator('#editorInsightDiscuss').click();
  await expect(page.locator('body')).not.toHaveClass(/is-writing/); await expect(page.locator('#editorDiscussion')).toBeVisible();
  return { insight, context: { insight_id: insight.id, session_id: insight.session_id, draft_revision: insight.draft_revision } };
}
async function sendText(text) {
  await page.locator('#textInput').fill(text); await page.locator('#sendBtn').click();
  return (await sent()).filter(message => message.type === 'text').at(-1);
}
async function answer(turn, insight, text, audio = false) {
  await emit({ type: 'editor_discussion', turn_id: turn.turn_id, insight });
  if (turn.text) await emit({ type: 'asr', turn_id: turn.turn_id, text: turn.text });
  await emit({ type: 'thinking', turn_id: turn.turn_id }); await emit({ type: 'text_delta', turn_id: turn.turn_id, delta: text });
  if (audio) await emit({ type: 'audio', turn_id: turn.turn_id, data: 'dGVzdA==', text, emotion: 'base' });
  await emit({ type: 'done', turn_id: turn.turn_id, text });
  if (audio) { await page.waitForFunction(() => h.sources.some(source => !source.stopped)); await page.evaluate(() => { const source = h.sources.at(-1); source.stopped = true; source.onended?.(); }); }
}
async function layoutGeometry() {
  return page.locator('#editorDiscussion').evaluate(element => {
    const rect = node => { const r = node.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height }; };
    const banner = rect(element), buttons = [...element.querySelectorAll('button')].map(rect), input = rect(document.querySelector('#textInput')), stop = rect(document.querySelector('#stopBtn'));
    const inside = r => r.height > 0 && r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight;
    return { banner, buttons, input, stop, viewport: { width: innerWidth, height: innerHeight }, fits: [banner, ...buttons, input, stop].every(inside), overflow: document.documentElement.scrollWidth > innerWidth, point: { clientHeight: element.querySelector('p').clientHeight, scrollHeight: element.querySelector('p').scrollHeight } };
  });
}
try {
  await page.goto(base); await page.waitForFunction(() => h.ws.readyState === 1); await expect(page.locator('#status')).toHaveText('在线');
  frame = page.frames().find(item => item.url().includes('/static/notes.html')); editor = page.frameLocator('iframe[title="个人笔记编辑器"]');
  await expect(editor.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'false');
  await page.locator('#writingToggle').click(); await editor.locator('#body').fill(bodyText); await tick(120000);
  check(requests.filter(item => item.path.startsWith('/api/editor-insights')).length === 0 && requests.every(item => item.method === 'GET'), 'Default off writes no draft state, analysis, model/audio or persistent data');
  await frame.evaluate(() => { window.sameDraftDocument = 'fixture-preserved'; });
  const first = await chooseInsight(bodyText, true); await tick(100);
  check(await page.locator('#textInput').getAttribute('maxlength') === '1000', 'Discussion composer applies the server follow-up length limit');
  check(await frame.evaluate(() => window.sameDraftDocument) === 'fixture-preserved' && await editor.locator('#body').inputValue() === bodyText, 'Continue discussion exits the wide writing view while retaining the same iframe and unsaved draft');
  await expect(page.locator('#textInput')).toBeFocused();
  check(sessions.get(first.context.session_id).enabled === true && sessions.get(first.context.session_id).draft_revision === first.context.draft_revision && count('analyze') === 1, 'Handoff keeps the server insight scope enabled at the original revision');
  const textTurn = await sendText('为什么？我希望新邻居也愿意参加。');
  assert.deepEqual(textTurn.editor_insight, first.context);
  assert.deepEqual(Object.keys(textTurn).sort(), ['editor_insight', 'text', 'turn_id', 'type']);
  check(textTurn.text === '为什么？我希望新邻居也愿意参加。' && !JSON.stringify(textTurn).includes(bodyText) && !JSON.stringify(textTurn).includes(pointText), 'Explicit text follow-up sends only insight IDs plus the user message, without copying the draft or point into ordinary text');
  await answer(textTurn, first.insight, '可以先选新邻居作为第一轮对象，给他们一个具体的参与理由，例如认识附近的人。老成员则可以担任引导者。', true);
  await expect(page.locator('#chat .bot').last()).toContainText('第一轮对象');
  check(await page.locator('#chat .editor-discussion-message').count() >= 1 && count('discuss') === 0, 'Scoped reply appears through the main chat and shared audio queue, with no separate HTTP model request');
  await mkdir('eval/ui/editor-discussion', { recursive: true });
  await tick(500);
  await page.evaluate(() => { const label = document.createElement('p'); label.id = 'fixtureLabel'; label.textContent = '隔离示例 · 虚构稿件与回复'; label.style.cssText = 'position:fixed;left:50%;top:76px;transform:translateX(-50%);z-index:90;padding:6px 10px;margin:0;border-radius:8px;background:#edf3e6;color:#35503a;font:12px sans-serif;pointer-events:none'; document.body.append(label); });
  await page.screenshot({ path: 'eval/ui/editor-discussion/fictional-home-discussion.png' });
  for (const { width, height } of (layoutOnly ? [{ width: 502, height: 507 }] : [{ width: 390, height: 844 }, { width: 320, height: 844 }, { width: 502, height: 507 }])) {
    await page.setViewportSize({ width, height });
    await expect(page.locator('#stopBtn')).toBeVisible();
    const geometry = await layoutGeometry(); layouts.push({ scenario: 'normal point', ...geometry });
    if (width !== 502) check(geometry.fits && !geometry.overflow, `${width}x${height} discussion banner, return/end, input and stop fit the viewport: ${JSON.stringify(geometry)}`);
    if (width === 390) await page.screenshot({ path: 'eval/ui/editor-discussion/fictional-home-discussion-mobile.png' });
    if (width === 502) {
      const longPoint = `${pointText}我们还需要先明确活动的参与门槛、主持人的职责和读者愿意继续参加的具体理由。`.repeat(5).slice(0, 240);
      await page.locator('#editorDiscussion > p').evaluate((node, text) => { node.textContent = `正在聊这一点：${text}`; }, longPoint);
      const longGeometry = await layoutGeometry(); layouts.push({ scenario: '240-character point', ...longGeometry });
      await page.screenshot({ path: 'eval/ui/editor-discussion/fictional-home-discussion-short-long.png' });
      await writeFile('eval/ui/editor-discussion/short-layout-geometry.json', JSON.stringify(layouts, null, 2));
      check(longGeometry.fits && !longGeometry.overflow, `502x507 long viewpoint keeps return/end, input and stop in view: ${JSON.stringify(longGeometry)}`);
      check(geometry.fits && !geometry.overflow, `502x507 normal viewpoint keeps return/end, input and stop in view: ${JSON.stringify(geometry)}`);
      check(longGeometry.point.scrollHeight > longGeometry.point.clientHeight && (await page.locator('#editorDiscussion > p').textContent()).includes(longPoint), 'A long point keeps its complete text in a bounded internal scroll area');
      await page.locator('#editorDiscussion > p').evaluate((node, text) => { node.textContent = `正在聊这一点：${text}`; }, pointText);
      if (layoutOnly) {
        await page.evaluate(() => { window.layoutRestoreProbe = ['.room-label', '#interactionState', '#companionMount', '.composer-foot'].map(selector => ({ selector, node: document.querySelector(selector) })); });
        check(await page.evaluate(() => layoutRestoreProbe.every(({ node }) => getComputedStyle(node).display === 'none')), 'Short discussion temporarily folds repeated room, state, companion and footer content');
        await page.locator('#editorDiscussion').getByRole('button', { name: '结束讨论', exact: true }).click(); await expect(page.locator('#editorDiscussion')).toBeHidden();
        check(await page.evaluate(() => !document.body.classList.contains('has-editor-discussion') && layoutRestoreProbe.every(({ selector, node }) => document.querySelector(selector) === node && getComputedStyle(node).display !== 'none')), 'Ending discussion restores the original short-page content with the same DOM nodes');
      }
    }
  }
  await page.setViewportSize({ width: 1280, height: 920 });
  await page.locator('#fixtureLabel').evaluate(node => node.remove());

  if (!layoutOnly) {
  const spoof = { type: 'aqi-editor-insight-discuss', ...first.context, point: '不应替换当前讨论', anchor: '伪造原文', expires_at: first.insight.expires_at };
  await page.evaluate(data => { window.postMessage(data, location.origin); window.dispatchEvent(new MessageEvent('message', { origin: 'http://untrusted.test', source: document.querySelector('iframe[title="个人笔记编辑器"]').contentWindow, data })); }, spoof);
  await page.evaluate(() => { const sibling = document.createElement('iframe'); sibling.title = 'untrusted-sibling'; sibling.srcdoc = '<p>same-origin fixture sibling</p>'; sibling.hidden = true; document.body.append(sibling); });
  await page.frameLocator('iframe[title="untrusted-sibling"]').locator('p').waitFor({ state: 'attached' });
  const sibling = page.frames().find(item => item.url() === 'about:srcdoc');
  await sibling.evaluate(([data, origin]) => parent.postMessage(data, origin), [spoof, base]); await tick(20);
  check((await page.locator('#editorDiscussion').innerText()).includes(pointText) && !(await page.locator('#editorDiscussion').innerText()).includes('不应替换'), 'Wrong origin, top-level source and same-origin sibling cannot replace a discussion');

  await page.locator('#micBtn').dispatchEvent('pointerdown'); await page.waitForFunction(() => h.sent.some(message => message.type === 'start'));
  const voiceTurn = (await sent()).filter(message => message.type === 'start').at(-1); assert.deepEqual(voiceTurn.editor_insight, first.context);
  await page.evaluate(() => h.feed()); await page.evaluate(() => document.dispatchEvent(new Event('pointerup')));
  check((await sent()).some(message => message.type === 'audio_end' && message.turn_id === voiceTurn.turn_id) && await page.evaluate(() => h.tracks.every(track => track.stopped)), 'Manual voice start carries the same insight IDs and uses only injected PCM and released fake devices');
  await emit({ type: 'asr', turn_id: voiceTurn.turn_id, text: '那第一场应该怎么安排？' }); await emit({ type: 'thinking', turn_id: voiceTurn.turn_id });
  await page.evaluate(() => { h.holdDecode = true; }); await emit({ type: 'audio', turn_id: voiceTurn.turn_id, data: 'dGVzdA==', text: '不应播出的迟到回复' }); await page.waitForFunction(() => h.decodeWait.length === 1);
  await page.locator('#editorDiscussion').getByRole('button', { name: '回到稿子', exact: true }).click();
  await expect(page.locator('body')).toHaveClass(/is-writing/); await tick(100);
  check(await editor.locator('#body').inputValue() === bodyText && await editor.locator('#editorInsightBubble').isVisible() && sessions.get(first.context.session_id).draft_revision === first.context.draft_revision && count('analyze') === 1, 'Returning to the draft preserves the chosen insight and revision without reevaluation');
  const sourceCount = await page.evaluate(() => h.sources.length);
  await editor.locator('#body').fill(bodyText + '我决定先邀请新邻居。'); await expect(page.locator('#editorDiscussion')).toHaveClass(/is-invalid/);
  await page.evaluate(() => { h.holdDecode = false; h.decodeWait.shift()({ duration: 2 }); });
  await emit({ type: 'text_delta', turn_id: voiceTurn.turn_id, delta: '不应出现的旧回复' }); await emit({ type: 'done', turn_id: voiceTurn.turn_id, text: '不应出现的旧回复' }); await tick(100);
  check(await editor.locator('#editorInsightBubble').isHidden() && await page.evaluate(() => h.sources.length) === sourceCount && !(await page.locator('#chat').innerText()).includes('不应出现的旧回复') && (await sent()).some(message => message.type === 'barge_in' && message.turn_id === voiceTurn.turn_id), 'An actual draft edit cancels the scoped turn and rejects late WebSocket text and native audio decode');
  await page.locator('.writing-header').getByRole('button', { name: '回到聊天', exact: true }).click();
  const textCount = (await sent()).filter(message => message.type === 'text').length;
  await page.locator('#textInput').fill('继续讨论刚才那一点'); await page.locator('#sendBtn').click();
  check((await sent()).filter(message => message.type === 'text').length === textCount && await page.locator('#textInput').inputValue() === '继续讨论刚才那一点', 'Invalid discussion blocks submission and retains text instead of falling back to ordinary chat');
  await page.locator('#editorDiscussion').getByRole('button', { name: '结束讨论', exact: true }).click(); await expect(page.locator('#editorDiscussion')).toBeHidden();
  const normal = await sendText('换个话题，今天想散步。'); check(!Object.hasOwn(normal, 'editor_insight') && await page.locator('#textInput').getAttribute('maxlength') === null, 'Ending discussion restores ordinary text turns and input length without insight context');
  await emit({ type: 'thinking', turn_id: normal.turn_id }); await emit({ type: 'done', turn_id: normal.turn_id, text: '好，去走走。' });

  const second = await chooseInsight(bodyText + '新一轮：' + '先以新邻居为主要参与者，明确他们来参加的理由。'.repeat(5));
  const rejected = await sendText('这个观点还能展开吗？');
  await emit({ type: 'error', turn_id: rejected.turn_id, scope: 'editor_insight', editor_context_valid: false, message: '这条观点已经失效，请重新选择。' });
  await expect(page.locator('#editorDiscussion')).toHaveClass(/is-invalid/);
  check(await page.locator('#textInput').inputValue() === '这个观点还能展开吗？', 'Authoritative rejection before an ASR echo restores the submitted question instead of losing it');
  const rejectedCount = (await sent()).filter(message => message.type === 'text').length; await page.locator('#textInput').fill('为什么失效？'); await page.locator('#sendBtn').click();
  check((await sent()).filter(message => message.type === 'text').length === rejectedCount && await page.locator('#textInput').inputValue() === '为什么失效？', 'Server-revoked context also blocks fallback and keeps the follow-up available');
  await page.locator('#editorDiscussion').getByRole('button', { name: '结束讨论', exact: true }).click();

  const third = await chooseInsight(bodyText + '再一轮：' + '让主持人先准备一组能让陌生人接上话的具体问题。'.repeat(6));
  const failed = await sendText('怎么安排讨论顺序？');
  await page.locator('#textInput').fill('我正在写一个新的问题');
  await emit({ type: 'error', turn_id: failed.turn_id, scope: 'editor_insight', editor_context_valid: true, message: '这次模型暂时不可用。' });
  check(await page.locator('#textInput').inputValue() === '我正在写一个新的问题', 'A late scoped error preserves a newer question already entered by the user');
  await expect(page.locator('#editorDiscussion')).not.toHaveClass(/is-invalid/); const retry = await sendText('再试一次，怎么安排讨论顺序？');
  assert.deepEqual(retry.editor_insight, third.context); check(true, 'A transient model error preserves a valid scope so explicit retry still carries the same IDs');
  await answer(retry, third.insight, '先让每个人说一件最近读到的小事，再选一个大家都好奇的问题。');
  await tick(300001); await expect(page.locator('#editorDiscussion')).toHaveClass(/is-invalid/);
  const expiredCount = (await sent()).filter(message => message.type === 'text').length; await page.locator('#textInput').fill('继续'); await page.locator('#sendBtn').click();
  check((await sent()).filter(message => message.type === 'text').length === expiredCount && await page.locator('#textInput').inputValue() === '继续', 'Expired viewpoint cannot silently continue as ordinary chat');
  check(requests.filter(item => item.method !== 'GET').every(item => item.path.startsWith('/api/editor-insights/')) && count('analyze') === 3, 'The complete flow makes only isolated insight requests and never saves notes, tasks, memory or associations');
  }
  assert.deepEqual(errors, []); check(true, 'No browser runtime errors across the real homepage, workspace, editor and voice modules');
  await writeFile(`eval/ui/editor-discussion/${layoutOnly ? 'short-layout-results' : 'home-results'}.json`, JSON.stringify({ checks, errors, requests, layouts, scope: 'Fictional routes, fake WebSocket/ASR/PCM/audio/avatar. No real model, microphone or production writes; long point is injected into the rendered paragraph for layout verification.' }, null, 2));
  console.log(`PASS ${checks.length} editor discussion homepage browser checks`); for (const label of checks) console.log(`  ${label}`);
} finally { await browser.close(); }
