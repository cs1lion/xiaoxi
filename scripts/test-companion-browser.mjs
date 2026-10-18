import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Actual homepage DOM and UI modules, entirely synthetic routes. No live user DB,
// model, paid speech, microphone or WebSocket is involved.
const files = ['style.css', 'workspace.css', 'writing.css', 'experience.css', 'voice/interaction-state.css', 'companion.css', 'almanac.css', 'avatar/dialogue-review.css',
  'companion.js', 'note-proposals.js', 'note-proposals.css', 'assistant.js', 'task-card.js', 'interaction-preferences.js', 'ui.js', 'chat-scroll.js', 'context-usage.js'];
const assets = new Map(await Promise.all(files.map(async name => [`/static/${name}`, await readFile(fileURLToPath(new URL(`../web/${name}`, import.meta.url)), 'utf8')])));
const index = (await readFile(fileURLToPath(new URL('../web/index.html', import.meta.url)), 'utf8'))
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
  .replace('</body>', `<script type="module">
import {bootCompanion} from '/static/companion.js';
import {bootAssistant,showAssistantResult} from '/static/assistant.js';
window.showResult = showAssistantResult;
await bootCompanion(); await bootAssistant();
</script><script type="module" src="/static/ui.js"></script></body>`);
assets.set('/', index);
const browser = await chromium.launch({ headless: true });
const requests = [], errors = [], unexpected = [], checks = [];
const check = (condition, text) => { if (!condition) throw Error(text); checks.push(text); };
const threads = new Map(), proposals = new Map();
let selected = null, serial = 0, eventSerial = 0, proposalSerial = 0;
const createThread = title => {
  const thread = { id: `thread-${++serial}`, title, status: 'active', next_step: '列出演示提纲',
    revision: 1, allow_callbacks: false, events: [], updated_at: serial };
  threads.set(thread.id, thread); return thread;
};
const first = createThread('周五演示 <img src=x onerror=alert(1)>');
first.events = [{ id: 'decision-1', kind: 'decision', text: '先演示文字入口', source: 'user_stated' },
  { id: 'receipt-1', kind: 'tool_receipt', text: JSON.stringify({ tool: 'note.update', status: 'succeeded', object_ref: { type: 'note', id: 'note-1', version: 5 }, href: 'https://evil.test/never-open' }), source: 'tool_verified', source_id: 'run-101' }];
const second = createThread('周末小作品');
const makeProposal = (thread, operation = 'add_event', extra = {}) => {
  const proposal = { id: `proposal-${++proposalSerial}`, thread_id: thread.id, operation,
    kind: 'decision', version: 1, expected_revision: thread.revision,
    source_text: '把刚才那段记下来', text: '先做三个演示页面 <img src=x onerror=alert(1)>',
    expires_at: Date.now() / 1000 + 3600, status: 'pending', ...extra };
  proposals.set(proposal.id, proposal); return proposal;
};
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (assets.has(path)) return route.fulfill({ body: assets.get(path), contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html' });
    const body = req.postData() ? req.postDataJSON() : {};
    requests.push({ path, method, body });
    const reply = (status, json) => route.fulfill({ status, json: structuredClone(json) });
    if (path === '/api/interaction/preferences') return reply(200, { items: [] });
    if (path === '/api/preferences/proactivity') return reply(200, { timezone: 'Asia/Shanghai', reminder_enabled: true });
    if (['/api/tasks', '/api/reminders', '/api/notifications'].includes(path)) return reply(200, { items: [] });
    if (path === '/api/threads/selection') {
      if (method === 'POST') { if (body.thread_id && !threads.has(body.thread_id)) return reply(404, { detail: 'missing' }); selected = body.thread_id; }
      return reply(200, { thread_id: selected });
    }
    if (path === '/api/threads') {
      if (method === 'GET') return reply(200, { items: [...threads.values()] });
      return reply(201, createThread(body.title));
    }
    const proposalMatch = path.match(/^\/api\/thread-proposals\/([^/]+)(?:\/(confirm|reject))?$/);
    if (proposalMatch) {
      const proposal = proposals.get(proposalMatch[1]), action = proposalMatch[2], thread = threads.get(proposal?.thread_id);
      if (!proposal || !thread || proposal.status !== 'pending' || proposal.expires_at * 1000 <= Date.now()) return reply(404, { detail: 'expired_deleted_or_consumed' });
      if (body.expected_version !== proposal.version) return reply(409, { detail: 'preview_changed' });
      if (method === 'PATCH' && !action) {
        proposal.text = body.text; proposal.version++; proposal.expected_revision = thread.revision;
        return reply(200, proposal);
      }
      if (action === 'reject') { proposal.status = 'rejected'; return reply(200, { rejected: true }); }
      if (action === 'confirm') {
        if (thread.revision !== proposal.expected_revision) return reply(409, { detail: 'thread_changed' });
        if (proposal.operation === 'next_step') thread.next_step = proposal.text;
        else if (proposal.operation === 'revise_event') thread.events.find(event => event.id === proposal.target_event_id).text = proposal.text;
        else thread.events.push({ id: `event-${++eventSerial}`, kind: proposal.kind, text: proposal.text, source: 'user_stated' });
        thread.revision++; proposal.status = 'confirmed'; return reply(200, thread);
      }
    }
    const threadMatch = path.match(/^\/api\/threads\/([^/]+)(?:\/(proposals))?$/);
    if (threadMatch) {
      const thread = threads.get(threadMatch[1]); if (!thread) return reply(404, { detail: 'missing_thread' });
      return reply(200, threadMatch[2] ? { items: [...proposals.values()].filter(p => p.thread_id === thread.id && p.status === 'pending' && p.expires_at * 1000 > Date.now()) } : thread);
    }
    unexpected.push(`${method} ${url}`); await route.abort();
  });
  await page.goto('http://companion.test/');
  await expect(page.locator('#companionMount')).toContainText('把一件事');
  check(requests.every(req => req.method === 'GET'), 'Initial boot is read-only');
  await page.locator('#textInput').fill('已有聊天草稿');
  await page.getByRole('button', { name: '选择 / 新建', exact: true }).click();
  const select = page.getByRole('combobox', { name: '已有共同事项' });
  await select.selectOption(first.id);
  check(selected === null, 'Browsing existing choices does not change global selection');
  await page.getByRole('button', { name: '关联对话', exact: true }).click();
  await expect(page.locator('#companionMount h2')).toHaveText(first.title);
  await expect(page.locator('#companionMount')).toContainText('列出演示提纲');
  await expect(page.locator('#companionMount')).toContainText('先演示文字入口');
  await expect(page.locator('#companionMount')).toContainText('工具核验 · 来源 run-101');
  await expect(page.locator('#companionMount')).toContainText('笔记已保存 · 版本 5');
  await expect(page.locator('#companionMount a')).toHaveAttribute('href', '/static/notes.html?note_id=note-1&version=5');
  check(!await page.locator('#companionMount a').getAttribute('href').then(value => value.includes('evil.test')), 'Verified note receipts show returned version and only build a fixed local saved-note URL');
  check(await page.locator('#companionMount img, #companionMount script').count() === 0, 'Summary user titles, decisions and tool receipts use safe text');
  await page.locator('#companionMount').getByRole('button', { name: '继续这件事', exact: true }).click();
  await expect(page.locator('#textInput')).toHaveValue('已有聊天草稿');
  await page.locator('#textInput').fill('');
  await page.locator('#companionMount').getByRole('button', { name: '继续这件事', exact: true }).click();
  await expect(page.locator('#textInput')).toHaveValue('继续这件事');
  check(!requests.some(req => /\/(chat|runs|tts|speech)/.test(req.path)), 'Continue prepares a draft without chat, model or audio requests');
  await page.locator('#textInput').focus();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('companion-updated')));
  await expect(page.locator('#textInput')).toBeFocused();
  await page.evaluate(thread => window.showResult({ text: '当前事项摘要', companion: { thread } }), structuredClone(first));
  await expect(page.locator('.companion-result')).toContainText(first.title);
  await expect(page.locator('#textInput')).toBeFocused();
  check(!await page.locator('#sidePanel').evaluate(el => el.classList.contains('is-open')), 'Companion results stay in main chat and do not open the mobile sidebar or steal focus');
  await page.evaluate(choice => window.showResult({ text: '选择一件事继续', companion: { thread: null, choices: [choice] } }), second);
  await page.getByRole('button', { name: `关联「${second.title}」`, exact: true }).click();
  await expect(page.locator('#companionMount h2')).toHaveText(second.title);
  check(selected === second.id && new URL(page.url()).pathname === '/', 'Choice card explicitly associates an ID on the homepage without navigation');
  await page.locator('#textInput').fill('');
  await page.locator('.companion-result').first().getByRole('button', { name: '继续这件事', exact: true }).click();
  await expect(page.locator('.companion-result').first().locator('.companion-feedback')).toContainText('请先关联');
  await expect(page.locator('#textInput')).toHaveValue('');
  check(selected === second.id, 'An old summary cannot prepare continuation for a different selected thread');
  await page.getByRole('button', { name: '取消关联', exact: true }).click();
  await expect(page.locator('#companionMount h2')).toContainText('把一件事');
  check(selected === null, 'Explicit cancel clears global selection');
  await page.getByRole('textbox', { name: '新共同事项名称' }).fill('准备新演示');
  await page.getByRole('button', { name: '创建并关联', exact: true }).click();
  await expect(page.locator('#companionMount h2')).toHaveText('准备新演示');
  const created = threads.get(selected);
  check(created.allow_callbacks === false && requests.find(req => req.path === '/api/threads' && req.method === 'POST').body.title === '准备新演示', 'Explicit create and associate makes one thread, with no callback opt-in');
  const proposal = makeProposal(created, 'add_event', {object_refs:[{type:'note',id:'original-note <&>',version:3}]});
  await page.locator('#textInput').focus();
  await page.evaluate(p => window.showResult({ text: '候选已准备', proposal: p }), proposal);
  const card = page.locator(`[data-proposal-id="${proposal.id}"]`);
  await expect(card.getByRole('link', {name:'当时关联的笔记版本 3'})).toBeVisible();
  check(await card.getByRole('link', {name:'当时关联的笔记版本 3'}).getAttribute('href') === '/static/notes.html?note_id=original-note+%3C%26%3E&version=3', 'Candidate exposes encoded fixed note version before confirmation');
  await expect(card).toBeVisible(); await expect(page.locator('#textInput')).toBeFocused();
  check(created.events.length === 0 && await card.locator('img, script').count() === 0, 'Candidate appears in chat, safe text, and no event is written before confirmation');
  await card.locator('textarea').fill('三个页面，第二个重点讲交互');
  await expect(card.getByRole('button', { name: '确认保存', exact: true })).toBeDisabled();
  await card.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(card.locator('.companion-preview p')).toHaveText('三个页面，第二个重点讲交互');
  await expect(card.getByRole('button', { name: '确认保存', exact: true })).toBeEnabled();
  check(proposal.version === 2 && created.events.length === 0, 'Editing increments preview version but does not save');
  await card.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(card.locator('.companion-feedback')).toContainText('已保存');
  await expect(page.locator('#companionMount')).toContainText('三个页面，第二个重点讲交互');
  check(created.events.length === 1 && created.events[0].text === proposal.text && created.events[0].source === 'user_stated', 'Exact confirmed preview is saved once and summary refreshes');
  const revision = makeProposal(created, 'revise_event', { target_event_id: created.events[0].id, original_text: '绑定预览时的决定原文', text: '第二个改为展示错误修正' });
  await page.evaluate(p => window.showResult({ proposal: p }), revision);
  const correction = page.locator(`[data-proposal-id="${revision.id}"]`);
  await expect(correction.locator('h3')).toContainText('更正');
  await expect(correction.locator('.companion-original')).toContainText('绑定预览时的决定原文');
  check(!await correction.locator('.companion-original').textContent().then(text => text.includes('三个页面，第二个重点讲交互')), 'Correction shows the proposal-bound original snapshot rather than a live event');
  await correction.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(correction.locator('.companion-feedback')).toContainText('已保存');
  check(created.events.length === 1 && created.events[0].text === revision.text, 'Correction edits the existing decision rather than adding another event');
  const next = makeProposal(created, 'next_step', { kind: 'progress', original_text: '绑定预览时的下一步', text: '周四先试讲一遍' });
  await page.evaluate(p => window.showResult({ proposal: p }), next);
  const nextCard = page.locator(`[data-proposal-id="${next.id}"]`);
  await expect(nextCard.locator('h3')).toContainText('更新下一步');
  await expect(nextCard.locator('.companion-original')).toContainText('绑定预览时的下一步');
  await nextCard.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(page.locator('#companionMount')).toContainText('周四先试讲一遍');
  check(created.next_step === next.text && created.events.length === 1, 'Next step confirmation updates next_step, without inventing a completed action');
  const conflict = makeProposal(created);
  await page.evaluate(p => window.showResult({ proposal: p }), conflict);
  const conflictCard = page.locator(`[data-proposal-id="${conflict.id}"]`);
  await conflictCard.locator('textarea').fill('我的本地草稿');
  conflict.text = '另一窗口的新预览'; conflict.version++;
  const patchesBefore = requests.filter(req => req.method === 'PATCH').length;
  await conflictCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(conflictCard.locator('textarea')).toHaveValue('我的本地草稿');
  await expect(conflictCard.locator('.companion-preview p')).toHaveText('另一窗口的新预览');
  await expect(conflictCard.locator('.companion-feedback')).toContainText('没有自动重试');
  await expect(conflictCard.getByRole('button', { name: '确认保存', exact: true })).toBeDisabled();
  check(requests.filter(req => req.method === 'PATCH').length === patchesBefore + 1, '409 reads latest preview, preserves user draft, and never retries writes');
  await conflictCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(conflictCard.getByRole('button', { name: '确认保存', exact: true })).toBeEnabled();
  created.revision++;
  const confirmationsBefore = requests.filter(req => req.path.endsWith('/confirm')).length;
  await conflictCard.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(conflictCard.getByRole('button', { name: '确认保存', exact: true })).toBeDisabled();
  await expect(conflictCard.locator('textarea')).toHaveValue('我的本地草稿');
  check(requests.filter(req => req.path.endsWith('/confirm')).length === confirmationsBefore + 1, 'Thread revision conflict requires explicit preview refresh and does not replay confirmation');
  await conflictCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(conflictCard.getByRole('button', { name: '确认保存', exact: true })).toBeEnabled();
  const beforeReject = created.revision;
  await conflictCard.getByRole('button', { name: '不保存', exact: true }).click();
  await expect(conflictCard.locator('.companion-feedback')).toContainText('未保存');
  check(conflict.status === 'rejected' && created.revision === beforeReject, 'Reject resolves the candidate without changing thread data');
  const concurrentPreview = makeProposal(created);
  await page.evaluate(p => window.showResult({ proposal: p }), concurrentPreview);
  const concurrentCard = page.locator(`[data-proposal-id="${concurrentPreview.id}"]`);
  await concurrentCard.locator('textarea').fill('同时修改事项后重新预览');
  created.revision++;
  await concurrentCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(concurrentCard.getByRole('button', { name: '确认保存', exact: true })).toBeEnabled();
  check(concurrentPreview.expected_revision === created.revision, 'Successful preview fetches current thread revision even if another window changed it');
  await concurrentCard.getByRole('button', { name: '不保存', exact: true }).click();
  await expect(concurrentCard.locator('.companion-feedback')).toContainText('未保存');
  const beforeExpiry = created.revision;
  const expired = makeProposal(created, 'add_event', { expires_at: Date.now() / 1000 + 0.2 });
  await page.evaluate(p => window.showResult({ proposal: p }), expired);
  const expiredCard = page.locator(`[data-proposal-id="${expired.id}"]`);
  await expiredCard.locator('textarea').fill('过期后也保留的草稿');
  await expect(expiredCard.getByRole('button', { name: '确认保存', exact: true })).toBeDisabled();
  await expect(expiredCard.getByRole('button', { name: '更新预览', exact: true })).toBeDisabled();
  await expect(expiredCard.locator('textarea')).toHaveValue('过期后也保留的草稿');
  check(created.revision === beforeExpiry, 'Expired preview preserves local draft and has no write');
  const missing = makeProposal(created);
  await page.evaluate(p => window.showResult({ proposal: p }), missing);
  const missingCard = page.locator(`[data-proposal-id="${missing.id}"]`);
  await missingCard.locator('textarea').fill('被删除候选的草稿'); proposals.delete(missing.id);
  await missingCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(missingCard.locator('textarea')).toHaveValue('被删除候选的草稿');
  await expect(missingCard.locator('.companion-feedback')).toContainText('已过期');
  check(created.revision === beforeExpiry, '404 preserves draft and disables stale candidate');
  // Background refresh must preserve caret and node identity for an active edit.
  const pending = makeProposal(created, 'add_event', { text: '开场先展示文字入口，再演示错误修正', source_text: '记个决定：开场先展示文字入口，再演示错误修正' });
  await page.evaluate(p => window.showResult({ proposal: p }), pending);
  const pendingCard = page.locator(`[data-proposal-id="${pending.id}"]`);
  await pendingCard.locator('textarea').fill('切换前的草稿'); await pendingCard.locator('textarea').focus();
  await page.evaluate(() => window.dispatchEvent(new CustomEvent('companion-updated')));
  await expect(pendingCard.locator('textarea')).toBeFocused();
  await expect(pendingCard.locator('textarea')).toHaveValue('切换前的草稿');
  await page.setViewportSize({ width: 390, height: 844 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px homepage has no horizontal overflow');
  check(await pendingCard.getByRole('button', { name: '更新预览', exact: true }).evaluate(el => el.getBoundingClientRect().height >= 44), 'Mobile candidate controls keep 44px touch targets');
  await page.setViewportSize({ width: 502, height: 507 });
  await page.locator('.companion-manager').evaluate(el => { el.open = false; });
  // This fixture omits the avatar runtime; make its existing stage opaque and
  // hit-testable to detect actual CSS paint order. If the current mobile layout
  // keeps them apart, no overlap means the panel cannot be covered — that is a
  // pass, not a failed fixture.
  check(await page.evaluate(() => {
    const panel = document.querySelector('#companionMount'), avatar = document.querySelector('#avatarStage');
    Object.assign(avatar.style, { display: 'block', pointerEvents: 'auto', background: '#233321', opacity: '1' });
    const p = panel.getBoundingClientRect(), a = avatar.getBoundingClientRect();
    const left = Math.max(p.left, a.left), right = Math.min(p.right, a.right);
    const top = Math.max(p.top, a.top), bottom = Math.min(p.bottom, a.bottom);
    if (right <= left || bottom <= top) return true;
    return Boolean(document.elementFromPoint((left + right) / 2, (top + bottom) / 2)?.closest('#companionMount'));
  }), 'On a narrow short screen the companion panel paints above an overlapping avatar');
  const collapsedMax = await page.locator('#companionMount').evaluate(el => parseFloat(getComputedStyle(el).maxHeight));
  await page.locator('.companion-manager').evaluate(el => { el.open = true; });
  const expandedMax = await page.locator('#companionMount').evaluate(el => parseFloat(getComputedStyle(el).maxHeight));
  check(expandedMax > collapsedMax, 'Opening management gives its scroll area more room on short mobile screens');
  const createControl = page.getByRole('button', { name: '创建并关联', exact: true });
  await createControl.scrollIntoViewIfNeeded();
  check(await createControl.evaluate(el => {
    const button = el.getBoundingClientRect(), panel = el.closest('#companionMount').getBoundingClientRect();
    return button.top >= panel.top && button.bottom <= panel.bottom;
  }), 'The create button can be brought fully into the short-screen management area');
  check(await page.locator('#textInput').evaluate(el => {
    const r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= innerHeight;
  }), 'Expanded mobile management keeps the composer on screen');
  await page.locator('#avatarStage').evaluate(el => { el.removeAttribute('style'); });
  await page.setViewportSize({ width: 390, height: 844 });
  check(requests.filter(req => req.method !== 'GET').every(req => req.path.startsWith('/api/threads') || req.path.startsWith('/api/thread-proposals')), 'All writes target only isolated synthetic companion APIs');
  if (errors.length || unexpected.length) throw Error(JSON.stringify({errors, unexpected}));
  check(true, 'No runtime errors or network requests outside the offline fixture');
  await page.evaluate(id => {
    document.querySelector('.companion-manager').open = false;
    document.querySelectorAll('.companion-result, .companion-proposal').forEach(card => { if (card.dataset.proposalId !== id) card.remove(); });
  }, pending.id);
  await page.locator('#textInput').fill('下一步先练习开场，怎么样？');
  await pendingCard.locator('textarea').fill('开场先展示文字入口，第二页演示错误修正');
  await page.locator('#textInput').focus();
  await mkdir(fileURLToPath(new URL('../eval/ui', import.meta.url)), { recursive: true });
  await page.setViewportSize({ width: 1200, height: 900 });
  await page.screenshot({ path: fileURLToPath(new URL('../eval/ui/companion-home-desktop.png', import.meta.url)) });
  await page.setViewportSize({ width: 390, height: 844 });
  await pendingCard.getByRole('button', { name: '更新预览', exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: fileURLToPath(new URL('../eval/ui/companion-home-mobile.png', import.meta.url)) });
  console.log(JSON.stringify({ result: 'PASS', scope: 'Offline Playwright; actual homepage DOM, synthetic API; no live DB, model, audio or mic', checks }, null, 2));
} finally { await browser.close(); }
