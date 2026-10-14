import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Real notes/editor modules, fully intercepted HTTP, virtual time and fake audio.
// Nothing reaches the running service, a provider, saved notes or chat history.
const origin = 'http://editor-quality-errors.test';
const assets = new Map(await Promise.all(
  ['notes.html', 'notes.js', 'style.css', 'editor-insights.js', 'editor-insights.css'].map(async name =>
    [`/static/${name}`, await readFile(fileURLToPath(new URL(`../web/${name}`, import.meta.url)), 'utf8')]),
));
const title = '仅用于隔离错误状态验收的草稿';
const body = '这份说明准备向新成员介绍一项可以逐步验证的安排。先保持现在的流程，再用少量真实问题检查新入口是否能帮助大家。尚未确定的部分会留在稿件里继续修改，不会因为一次建议或讨论失败而自动保存、删除或提交。\n'.repeat(8);
const analysisCases = [
  ['analysis_output_truncated', '这次建议没有完整生成，你可以继续写。'],
  ['analysis_empty_response', '这次没有收到建议，你可以继续写。'],
  ['analysis_invalid_response', '这次建议暂时无法使用，你可以继续写。'],
];
const discussionCases = [
  ['insight_discussion_output_truncated', '这次回复没有完整生成；你的输入保留，可以重试。'],
  ['insight_discussion_empty_response', '这次没有收到回复；你的输入保留，可以重试。'],
  ['insight_discussion_invalid_reply', '这次回复暂时无法使用；你的输入保留，可以重试。'],
];
const browser = await chromium.launch({ headless: true });
const checks = [], browserErrors = [], unexpectedRequests = [];
const check = (condition, label) => { assert.ok(condition, label); checks.push(label); };

async function fixture({ analysisReason, discussionReason } = {}) {
  const context = await browser.newContext({ viewport: { width: 1100, height: 950 }, serviceWorkers: 'block' });
  const page = await context.newPage();
  const requests = [];
  page.on('pageerror', error => browserErrors.push(error.message));
  await page.clock.install({ time: new Date('2026-10-14T03:00:00Z') });
  await page.addInitScript(() => {
    window.fakeAudio = { constructors: 0, plays: 0, pauses: 0 };
    window.Audio = class {
      constructor() { window.fakeAudio.constructors++; this.onended = null; this.onerror = null; }
      async play() { window.fakeAudio.plays++; }
      pause() { window.fakeAudio.pauses++; }
      removeAttribute() {}
      load() {}
    };
  });
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) {
      unexpectedRequests.push(request.url());
      return route.abort();
    }
    if (assets.has(url.pathname)) {
      return route.fulfill({ body: assets.get(url.pathname), contentType: url.pathname.endsWith('.js') ? 'text/javascript' : url.pathname.endsWith('.css') ? 'text/css' : 'text/html' });
    }
    const payload = request.postData() ? request.postDataJSON() : {};
    requests.push({ path: url.pathname, method: request.method(), payload });
    const reply = (status, json) => route.fulfill({ status, json });
    if (request.method() === 'GET' && url.pathname === '/api/notes') return reply(200, { items: [] });
    if (request.method() === 'GET' && url.pathname === '/api/notes/selection') return reply(200, { note_id: null, revision: 0 });
    if (request.method() === 'GET' && url.pathname === '/api/editor-insights/status') {
      return reply(200, { available: true, reason: '', pause_ms: 12000, cooldown_ms: 90000, max_checks: 5 });
    }
    if (request.method() === 'POST' && url.pathname === '/api/editor-insights/state') {
      return reply(200, { status: 'state', draft_revision: payload.draft_revision, enabled: payload.enabled, checks: 0 });
    }
    if (request.method() === 'POST' && url.pathname === '/api/editor-insights/analyze') {
      if (analysisReason) return reply(200, { status: 'unavailable', reason: analysisReason, checks: 1 });
      return reply(200, { status: 'insight', checks: 1, insight: {
        id: 'fixture-insight', session_id: payload.session_id, draft_revision: payload.draft_revision,
        point: '先说明这一轮要验证什么。让新成员能区分正在试行的安排和已经确定的流程。',
        anchor: payload.body.slice(0, 25), teaser: '先说明这一轮要验证什么。',
        expires_at: await page.evaluate(() => Date.now() / 1000 + 300),
      } });
    }
    if (request.method() === 'POST' && url.pathname === '/api/editor-insights/fixture-insight/speak') {
      return reply(200, { audio: 'ZmFrZQ==', format: 'mp3' });
    }
    if (request.method() === 'POST' && url.pathname === '/api/editor-insights/fixture-insight/discuss' && discussionReason) {
      return reply(502, { detail: discussionReason });
    }
    unexpectedRequests.push(`${request.method()} ${request.url()}`);
    return route.abort();
  });
  await page.goto(`${origin}/static/notes.html`);
  await expect(page.locator('#editorInsightToggle')).toBeEnabled();
  await expect(page.locator('#noteSelectionStatus')).toContainText('尚未关联');
  await page.locator('#title').fill(title);
  await page.locator('#body').fill(body);
  await page.locator('#editorInsightToggle').click();
  await expect(page.locator('#editorInsightToggle')).toHaveAttribute('aria-pressed', 'true');
  const count = stage => requests.filter(item => item.path.endsWith(`/${stage}`)).length;
  const tick = async milliseconds => { await page.clock.fastForward(milliseconds); await page.evaluate(() => Promise.resolve()); };
  return { context, page, requests, count, tick };
}

try {
  for (const [reason, expected] of analysisCases) {
    const f = await fixture({ analysisReason: reason });
    try {
      await f.page.locator('#body').focus();
      const before = await f.page.locator('#body').evaluate(input => {
        input.setSelectionRange(31, 31); input.scrollTop = 46;
        return { start: input.selectionStart, end: input.selectionEnd, scroll: input.scrollTop };
      });
      await f.tick(12001);
      await expect(f.page.locator('#editorInsightFeedback')).toHaveText(expected);
      check(f.count('analyze') === 1, `${reason}: one paused-draft analysis only`);
      check(await f.page.locator('#title').inputValue() === title && await f.page.locator('#body').inputValue() === body, `${reason}: original title and body preserved`);
      check(await f.page.locator('#body').evaluate((input, old) => document.activeElement === input && input.selectionStart === old.start && input.selectionEnd === old.end && input.scrollTop === old.scroll, before), `${reason}: focus, caret and scroll preserved`);
      await expect(f.page.locator('#editorInsightBubble')).toBeHidden();
      await expect(f.page.locator('#editorInsightDetail')).toBeHidden();
      check(f.count('speak') === 0 && await f.page.evaluate(() => fakeAudio.plays) === 0, `${reason}: no speech request or audio playback`);
      check(!expected.includes('没有值得打断'), `${reason}: failure is not presented as a quiet judgment`);
      await f.tick(120000);
      check(f.count('analyze') === 1 && f.count('discuss') === 0, `${reason}: no automatic retry or discussion`);
    } finally { await f.context.close(); }
  }

  for (const [reason, expected] of discussionCases) {
    const f = await fixture({ discussionReason: reason });
    try {
      await f.tick(12001);
      await expect(f.page.locator('#editorInsightBubble')).toBeVisible();
      await f.page.locator('#editorInsightBubble').click();
      await expect.poll(() => f.count('speak')).toBe(1);
      await expect.poll(() => f.page.evaluate(() => fakeAudio.plays)).toBe(1);
      await f.page.locator('#editorInsightStop').click();
      await f.page.locator('#editorInsightDiscuss').click();
      const text = '为什么？我想保留这个暂时没有决定的部分。';
      await f.page.locator('#editorInsightDiscussionInput').fill(text);
      const before = {
        speak: f.count('speak'), plays: await f.page.evaluate(() => fakeAudio.plays),
        messages: await f.page.locator('#editorInsightDiscussionMessages > *').count(),
      };
      await f.page.locator('#editorInsightDiscussionSend').click();
      await expect(f.page.locator('#editorInsightDiscussionStatus')).toHaveText(expected);
      await expect(f.page.locator('#editorInsightDiscussionSend')).toBeEnabled();
      check(f.count('discuss') === 1 && f.requests.find(item => item.path.endsWith('/discuss')).payload.text === text, `${reason}: one explicit scoped discussion request`);
      check(await f.page.locator('#editorInsightDiscussionInput').inputValue() === text, `${reason}: failed discussion input preserved`);
      check(await f.page.locator('#editorInsightDiscussionMessages > *').count() === before.messages, `${reason}: no user or assistant transcript committed on failure`);
      check(f.count('speak') === before.speak && await f.page.evaluate(() => fakeAudio.plays) === before.plays, `${reason}: failure produces no new speech or playback`);
      check(await f.page.locator('#title').inputValue() === title && await f.page.locator('#body').inputValue() === body, `${reason}: discussion failure does not alter the draft`);
      await f.tick(120000);
      check(f.count('discuss') === 1 && f.count('analyze') === 1, `${reason}: no automatic retry or new analysis`);
      check(f.requests.every(item => item.method === 'GET' || item.path.startsWith('/api/editor-insights/')), `${reason}: no note, task or chat-history writes`);
    } finally { await f.context.close(); }
  }
  check(browserErrors.length === 0, 'No browser errors');
  check(unexpectedRequests.length === 0, 'All requests intercepted; no unexpected or external endpoint');
  console.log(JSON.stringify({ result: 'PASS', checks: checks.length, analysis_cases: analysisCases.length, discussion_cases: discussionCases.length,
    scope: 'Isolated HTTP only, real notes/editor modules, virtual clock, fake audio; no running service, providers or production writes' }, null, 2));
} finally { await browser.close(); }
