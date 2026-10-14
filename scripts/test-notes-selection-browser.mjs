// Real notes/workspace modules, fully routed HTTP fixtures. No live server or user DB.
import { chromium, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const origin = 'http://127.0.0.1:8743';
const browser = await chromium.launch({ headless: true });
const checks = [];
const note = (id, title, body, version = 1) => ({ id, title, body, version, source: 'user', created_at: 1, updated_at: version });
const initialNotes = () => new Map([
  ['chosen', [note('chosen', '昨天的自动化练习', '版本一：整理输入文件')]],
  ['latest', [note('latest', '今天新建的其他笔记', '另一件无关的事情')]],
]);

async function fixture() {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(8000);
  const state = {
    notes: initialNotes(), deleted: new Set(), revision: 0, selected: null, requests: [], errors: [],
    selectionConflict: false, patchHold: null, activityHold: null, thread: null, activityOverrides: null,
  };
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.fixtureActivityConsumed = 0;
    window.fetch = async (...args) => {
      const response = await originalFetch(...args);
      if (String(args[0]) === '/api/activity') {
        const originalJson = response.json.bind(response);
        response.json = async () => {
          const data = await originalJson();
          setTimeout(() => { window.fixtureActivityConsumed++; }, 0);
          return data;
        };
      }
      return response;
    };
  });
  const selection = () => {
    const saved = state.selected;
    const current = saved && !state.deleted.has(saved.note_id) ? state.notes.get(saved.note_id)?.at(-1) : null;
    return { note_id: saved?.note_id || null, selected_version: saved?.version || null,
      current_version: current?.version || null, revision: state.revision,
      activity_id: 'activity-stable', missing: !!saved && !current,
      stale: !!current && current.version !== saved.version, status: saved ? current ? current.version !== saved.version ? 'stale' : 'selected' : 'missing' : 'none', selected_at: saved ? 1 : null };
  };
  const activity = () => {
    const selected = selection();
    const saved = selected.note_id && !selected.missing ? state.notes.get(selected.note_id)?.find(value => value.version === selected.selected_version) : null;
    return { activity_id: selected.activity_id, selection: selected,
      note: saved ? { id: saved.id, title: saved.title, selected_version: saved.version, current_version: selected.current_version, status: selected.status, source: 'user', updated_at: saved.updated_at } : null,
      thread: state.thread, object_refs: saved ? [{ type: 'note', id: saved.id, version: saved.version }] : [],
      resume: { text: saved ? `从保存版本 ${saved.version} 继续。` : '暂未选择继续内容。', object_refs: [] }, ...state.activityOverrides };
  };
  await page.route(`${origin}/**`, async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (url.pathname.startsWith('/api/')) {
      state.requests.push({ path: url.pathname, method, body });
      const reply = (json, status = 200) => route.fulfill({ json, status });
      if (url.pathname === '/api/activity' && method === 'GET') {
        const snapshot = activity();
        if (state.activityHold) { const hold = state.activityHold; state.activityHold = null; hold.started = true; await hold.promise; }
        return reply(snapshot);
      }
      if (url.pathname === '/api/tasks' && method === 'GET') return reply({ items: [{ id: 'unrelated-latest-task', title: '最新更新的无关任务', status: 'open', updated_at: 99999 }] });
      if (url.pathname === '/api/notes/selection') {
        if (method === 'GET') return reply(selection());
        if (state.selectionConflict) { state.selectionConflict = false; state.revision++; return reply({ detail: 'selection revision conflict' }, 409); }
        if (body.expected_revision !== state.revision) return reply({ detail: 'selection revision conflict' }, 409);
        if (body.note_id !== null) {
          const current = state.notes.get(body.note_id)?.at(-1);
          if (!current || state.deleted.has(body.note_id) || current.version !== body.version) return reply({ detail: 'note version conflict' }, 409);
        }
        state.selected = body.note_id === null ? null : { note_id: body.note_id, version: body.version };
        state.revision++;
        return reply(selection());
      }
      if (url.pathname === '/api/notes') {
        if (method === 'GET') {
          const query = url.searchParams.get('query') || '';
          return reply({ items: [...state.notes.values()].map(versions => versions.at(-1)).filter(value => !state.deleted.has(value.id) && `${value.title}${value.body}`.includes(query)).reverse().map(value => ({ ...value, excerpt: value.body })) });
        }
        const saved = note(`created-${state.notes.size}`, body.title, body.body);
        state.notes.set(saved.id, [saved]); return reply(saved);
      }
      const match = url.pathname.match(/^\/api\/notes\/([^/]+)(\/delete)?$/);
      if (match) {
        const id = decodeURIComponent(match[1]), versions = state.notes.get(id), current = versions?.at(-1);
        if (!current || state.deleted.has(id)) return reply({ detail: 'note not found' }, 404);
        if (method === 'GET') {
          const version = url.searchParams.get('version');
          const saved = version === null ? current : versions.find(value => value.version === Number(version));
          return saved ? reply(saved) : reply({ detail: 'version not found' }, 404);
        }
        if (body.version !== current.version) return reply({ detail: 'note version conflict' }, 409);
        if (match[2]) { state.deleted.add(id); return reply({ deleted: true }); }
        const saved = note(id, body.title, body.body, current.version + 1); versions.push(saved);
        if (state.patchHold) { const hold = state.patchHold; state.patchHold = null; hold.started = true; await hold.promise; }
        return reply(saved);
      }
      return reply({ detail: `Unexpected fixture request ${method} ${url.pathname}` }, 500);
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh-CN"><head><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/static/style.css"></head><body><main><div id="assistantMount"></div><section id="companionMount"></section></main><script type="module" src="/static/workspace.js"></script></body></html>` });
    const relative = url.pathname.replace(/^\/static\//, '');
    const absolute = path.resolve(root, 'web', relative);
    if (!absolute.startsWith(path.join(root, 'web') + path.sep)) return route.abort();
    try {
      return route.fulfill({ body: await readFile(absolute), contentType: absolute.endsWith('.js') ? 'text/javascript' : absolute.endsWith('.css') ? 'text/css' : 'text/html' });
    } catch { return route.fulfill({ status: 404, body: 'not found' }); }
  });
  return { page, state, selection, activity };
}
const writes = state => state.requests.filter(value => value.path === '/api/notes/selection' && value.method === 'POST');
const ready = async page => expect(page.locator('#new')).toBeEnabled();
const opened = async (page, id) => { await page.locator(`.note[data-note-id="${id}"]`).click(); await ready(page); await expect(page.locator('#source')).toContainText('保存版本'); };
const hold = () => { let resolve; const promise = new Promise(value => { resolve = value; }); return { promise, resolve, started: false }; };

try {
  const one = await fixture(); const { page, state } = one;
  await page.goto(`${origin}/static/notes.html`); await ready(page);
  assert.equal(await page.locator('#title').inputValue(), '');
  await expect(page.locator('#noteSelectionStatus')).toContainText('尚未关联');
  await opened(page, 'chosen');
  await page.locator('#body').fill('版本二：保存修改'); await expect(page.locator('#associateNote')).toBeDisabled();
  await page.locator('#save').click(); await ready(page);
  assert.equal(state.notes.get('chosen').at(-1).version, 2);
  assert.equal(writes(state).length, 0, 'open/edit/save do not associate');
  await page.reload(); await ready(page); assert.equal(await page.locator('#title').inputValue(), '', 'reload without selection does not choose latest note');
  await opened(page, 'chosen'); await page.locator('#associateNote').click(); await ready(page);
  assert.deepEqual(writes(state).at(-1).body, { note_id: 'chosen', version: 2, expected_revision: 0 });
  await page.reload(); await ready(page);
  assert.equal(await page.locator('#body').inputValue(), '版本二：保存修改');
  await expect(page.locator('#associateNote')).toHaveText('当前保存版本已关联');
  checks.push('opening and saving never select; explicit version/CAS selection survives reload');

  state.notes.get('chosen').push(note('chosen', '昨天的自动化练习（新版）', '版本三：另一窗口保存', 3));
  await page.reload(); await ready(page);
  assert.equal(await page.locator('#body').inputValue(), '版本二：保存修改');
  assert.equal(await page.locator('#body').evaluate(element => element.readOnly), true);
  await expect(page.locator('#save')).toBeDisabled(); await expect(page.locator('#delete')).toBeDisabled(); await expect(page.locator('#prepare')).toBeDisabled();
  await expect(page.locator('#noteSelectionStatus')).toContainText('当前已有保存版本 3');
  assert.equal(writes(state).length, 1);
  await page.locator('#openCurrentNote').click(); await ready(page);
  assert.equal(await page.locator('#body').inputValue(), '版本三：另一窗口保存');
  assert.equal(await page.locator('#body').evaluate(element => element.readOnly), false);
  assert.equal(one.selection().selected_version, 2, 'opening current version does not upgrade selected version');
  await page.locator('#associateNote').click(); await ready(page); assert.equal(one.selection().selected_version, 3);
  checks.push('stale selection restores exact historical body read-only; upgrade requires explicit association');

  const patch = hold(); state.patchHold = patch;
  await page.locator('#body').fill('版本四：提交的内容'); await page.locator('#save').click();
  await expect.poll(() => patch.started).toBe(true);
  await page.locator('#body').fill('提交后继续输入的未保存内容'); patch.resolve(); await ready(page);
  assert.equal(state.notes.get('chosen').at(-1).body, '版本四：提交的内容');
  assert.equal(await page.locator('#body').inputValue(), '提交后继续输入的未保存内容');
  await expect(page.locator('#associateNote')).toBeDisabled(); await expect(page.locator('#prepare')).toBeDisabled();
  assert.equal(one.selection().selected_version, 3);
  checks.push('typing during save preserves newer draft and cannot associate it as saved work');

  await page.locator('#save').click(); await ready(page); // version 5, current draft explicitly saved
  state.selectionConflict = true;
  await page.locator('#associateNote').click(); await ready(page);
  await expect(page.locator('#status')).toContainText('其他窗口');
  assert.equal(await page.locator('#body').inputValue(), '提交后继续输入的未保存内容');
  await expect(page.locator('#associateNote')).toBeDisabled();
  const conflicts = writes(state).length;
  await page.locator('#refreshNoteSelection').click(); await ready(page);
  assert.equal(writes(state).length, conflicts, 'refresh only reads and never silently retries');
  await page.locator('#associateNote').click(); await ready(page);
  assert.equal(one.selection().selected_version, 5);
  checks.push('selection conflict preserves fields, requires refresh and never silently retries');

  await page.locator('#body').fill('取消关联时保留的草稿');
  await page.locator('#clearNoteSelection').click(); await ready(page);
  assert.equal(one.selection().note_id, null);
  assert.equal(await page.locator('#body').inputValue(), '取消关联时保留的草稿');
  await page.locator('#save').click(); await ready(page); await page.locator('#associateNote').click(); await ready(page);
  await page.locator('#delete').click(); await ready(page);
  assert.equal(one.selection().missing, true);
  await expect(page.locator('#noteSelectionStatus')).toContainText('已被删除');
  await page.reload(); await ready(page); assert.equal(await page.locator('#title').inputValue(), '');
  await expect(page.locator('#clearNoteSelection')).toBeEnabled();
  assert.equal(one.selection().note_id, 'chosen', 'deletion does not switch to another note');
  await page.locator('#clearNoteSelection').click(); await ready(page); assert.equal(one.selection().note_id, null);
  checks.push('clear preserves draft; deleted selection remains missing until explicitly cleared');
  assert.deepEqual(state.errors, []); await page.close();

  const two = await fixture(); const workspace = two.page, store = two.state;
  store.selected = { note_id: 'chosen', version: 1 }; store.revision = 1;
  store.notes.get('chosen').push(note('chosen', '较新的保存版本', '版本二：不能自动用于继续', 2));
  store.thread = { id: 'thread-chosen', title: '已经明确选择的事项', status: 'active', revision: 7, next_step: '先确认输入目录' };
  await workspace.goto(`${origin}/`);
  await expect(workspace.locator('#work-now')).toContainText('昨天的自动化练习');
  await expect(workspace.locator('#work-now')).toContainText('保存版本 1');
  await expect(workspace.locator('#work-now')).toContainText('当前已有保存版本 2');
  await expect(workspace.locator('#work-now')).toContainText('先确认输入目录');
  assert.equal(await workspace.getByRole('link', { name: '打开已关联的保存版本', exact: true }).getAttribute('href'), '/static/notes.html?note_id=chosen&version=1');
  assert.equal(await workspace.getByRole('link', { name: '查看当前保存版本并决定是否关联' }).getAttribute('href'), '/static/notes.html?note_id=chosen&version=2');
  assert.equal(store.requests.filter(value => value.path === '/api/tasks').length, 0, 'workspace never chooses the most recently updated task');
  assert.equal(writes(store).length, 0);
  checks.push('workspace uses selected activity metadata and exact version links, never latest task');

  await workspace.locator('#work-notes-tab').click();
  const editor = workspace.frameLocator('iframe[title="个人笔记编辑器"]');
  await expect(editor.locator('#new')).toBeEnabled();
  await editor.locator('#openCurrentNote').click(); await expect(editor.locator('#new')).toBeEnabled();
  await editor.locator('#body').fill('工作区切换时保留的未保存草稿');
  await expect(workspace.locator('#work-note-draft')).toBeVisible();
  await expect(workspace.locator('#work-note-draft')).toContainText('未保存修改');
  const frameElement = await workspace.locator('iframe').elementHandle();
  await workspace.locator('#work-tasks-tab').click(); await workspace.locator('#work-notes-tab').click();
  assert.equal(await editor.locator('#body').inputValue(), '工作区切换时保留的未保存草稿');
  assert.equal(await frameElement.evaluate(element => element === document.querySelector('iframe')), true);
  await workspace.locator('#work-notes-tab').press('ArrowLeft');
  await expect(workspace.locator('#work-now-tab')).toHaveAttribute('aria-selected', 'true');
  assert.equal(await workspace.locator('.shared-workspace > [role=tabpanel]:visible').count(), 1);
  await expect(workspace.locator('#work-now')).not.toContainText('工作区切换时保留');
  checks.push('iframe document and draft survive tabs; local warning reveals no draft body in activity');

  await workspace.locator('#work-notes-tab').click();
  await editor.locator('#save').click(); await expect(editor.locator('#new')).toBeEnabled();
  await expect(workspace.locator('#work-note-draft')).toBeHidden();
  await workspace.locator('#work-now-tab').click();
  await expect(workspace.locator('#work-now')).toContainText('当前已有保存版本 3');
  assert.equal(two.selection().selected_version, 1);
  assert.equal(store.notes.get('chosen').at(-1).body, '工作区切换时保留的未保存草稿');
  assert.equal(writes(store).length, 0);
  checks.push('iframe explicit save refreshes activity but leaves its selected version fixed');

  const baseline = store.requests.filter(value => value.path === '/api/activity').length;
  await workspace.evaluate(() => {
    window.dispatchEvent(new MessageEvent('message', { origin: location.origin, source: window, data: { type: 'aqi-notes-updated', dirty: false } }));
    window.dispatchEvent(new MessageEvent('message', { origin: 'https://invalid.example', source: document.querySelector('iframe').contentWindow, data: { type: 'aqi-notes-updated', dirty: false } }));
  });
  // A valid iframe event creates a positive barrier after the two rejected messages.
  await workspace.frames().find(value => value.url().includes('notes.html')).evaluate(() => parent.postMessage({ type: 'aqi-notes-updated', dirty: true, body: 'must never render from message' }, location.origin));
  await expect.poll(() => store.requests.filter(value => value.path === '/api/activity').length).toBe(baseline + 1);
  await workspace.waitForFunction(count => window.fixtureActivityConsumed >= count, baseline + 1);
  await expect(workspace.locator('#work-note-draft')).toBeVisible();
  await expect(workspace.locator('#work-now')).not.toContainText('must never render');
  checks.push('iframe refresh messages require matching origin/source and reload server metadata');

  const consumedBefore = await workspace.evaluate(() => window.fixtureActivityConsumed);
  const delayed = hold(); store.activityHold = delayed;
  await workspace.getByRole('button', { name: '刷新此刻' }).click();
  await expect.poll(() => delayed.started).toBe(true);
  store.selected = { note_id: 'latest', version: 1 }; store.revision++;
  await workspace.evaluate(() => window.dispatchEvent(new CustomEvent('companion-updated')));
  await expect(workspace.locator('#work-now')).toContainText('今天新建的其他笔记');
  delayed.resolve();
  // The counter increments in the next task after JSON parsing and rendering.
  await workspace.waitForFunction(before => window.fixtureActivityConsumed >= before + 2, consumedBefore);
  await expect(workspace.locator('#work-now')).toContainText('今天新建的其他笔记');
  assert.equal(await workspace.locator('#work-now').getByText(/关联笔记：昨天/).count(), 0);
  checks.push('late activity response cannot overwrite a newer explicit selection');

  store.activityOverrides = { note: { id: 'latest', title: '<img src=x onerror="window.injected=1">', selected_version: 1, current_version: 1 }, resume: { text: '<script>window.injected=1</script>' } };
  await workspace.getByRole('button', { name: '刷新此刻' }).click();
  await expect(workspace.locator('#work-now')).toContainText('<img src=x');
  assert.equal(await workspace.locator('#work-now img, #work-now script').count(), 0);
  assert.equal(await workspace.evaluate(() => window.injected), undefined);
  store.activityOverrides = null; store.selected = null; store.thread = null;
  await workspace.getByRole('button', { name: '刷新此刻' }).click();
  await expect(workspace.locator('#work-now')).toContainText('尚未关联笔记');
  assert.equal(await workspace.locator('#work-now a').count(), 0);
  await workspace.setViewportSize({ width: 390, height: 844 });
  assert.equal(await workspace.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(store.errors, []);
  checks.push('metadata renders as text; empty selection remains empty and mobile tabs fit');
  await workspace.close();

  const fixed = await fixture(); fixed.state.selected = { note_id: 'chosen', version: 1 }; fixed.state.revision = 1;
  await fixed.page.goto(`${origin}/static/notes.html`); await ready(fixed.page);
  await fixed.page.locator('#body').fill('版本二：自己保存的新版');
  await fixed.page.locator('#save').click(); await ready(fixed.page);
  await fixed.page.locator('#refreshNoteSelection').click(); await ready(fixed.page);
  assert.equal(fixed.selection().selected_version, 1); assert.equal(fixed.selection().current_version, 2);
  assert.equal(writes(fixed.state).length, 0);
  await fixed.page.reload(); await ready(fixed.page);
  assert.equal(await fixed.page.locator('#body').inputValue(), '版本一：整理输入文件');
  assert.equal(await fixed.page.locator('#body').evaluate(element => element.readOnly), true);
  await expect(fixed.page.locator('#noteSelectionStatus')).toContainText('当前已有保存版本 2');
  assert.deepEqual(fixed.state.errors, []); await fixed.page.close();
  checks.push('v1 selection stays v1 after own v2 save, explicit refresh and reload');
  console.log(`PASS: ${checks.length} isolated notes/activity browser checks`, checks);
} finally { await browser.close(); }
