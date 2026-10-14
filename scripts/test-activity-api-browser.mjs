// Real workspace/editor -> real routers -> temporary SQLite. Every network request
// is intercepted; no app bootstrap, listener, model, TTS, or production data.
import { chromium, expect } from '@playwright/test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const webRoot = path.join(root, 'web');
const temporaryRoot = path.resolve(os.tmpdir());
const directory = await mkdtemp(path.join(temporaryRoot, 'aqi-activity-browser-'));
const database = path.join(directory, 'assistant.db');
const origin = 'http://activity.test';
const checks = [], requests = [], errors = [], blocked = [];
const call = request => {
  const result = spawnSync(path.join(root, '.venv', 'Scripts', 'python.exe'),
    [path.join(root, 'scripts', 'task_api_bridge.py'), database], {
      input: JSON.stringify(request), encoding: 'utf8', cwd: root,
      env: { ...process.env, PYTHONUTF8: '1' }, timeout: 15000,
    });
  if (result.error || result.status !== 0) throw result.error || Error(result.stderr || `Bridge exited ${result.status}`);
  return JSON.parse(result.stdout);
};
const api = (method, pathname, body) => {
  const response = call({ method, path: pathname, ...(body === undefined ? {} : { body }) });
  assert.equal(response.status, 200, `${method} ${pathname}: ${JSON.stringify(response.body)}`);
  return response.body;
};
const selectionWrites = () => requests.filter(item => item.method === 'POST' && item.path === '/api/notes/selection');

let browser;
try {
  // Seed a separately selected thread through its real API. Each bridge call
  // reconstructs stores, so all following activity reads test persistence too.
  let thread = api('POST', '/api/threads', { title: '批量重命名练习' });
  thread = api('PATCH', `/api/threads/${thread.id}`, {
    expected_revision: thread.revision, status: 'active', next_step: '核对输入目录',
  });
  api('POST', '/api/threads/selection', { thread_id: thread.id });
  assert.equal(api('GET', '/api/threads/selection').thread_id, thread.id);

  // Preserve the real index markup and CSS, loading only the modules under test.
  // Chat/voice/avatar bootstrap is removed from this isolated test document.
  const index = (await readFile(path.join(webRoot, 'index.html'), 'utf8'))
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,
      script => script.includes('src="/static/workspace.js"') ? script : '');
  assert.match(index, /src="\/static\/workspace\.js"/);
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await context.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { blocked.push(request.url()); return route.abort(); }
    if (url.pathname.startsWith('/api/')) {
      const permitted = /^\/api\/(?:activity|notes(?:\/[^/]+(?:\/(?:delete|history))?)?|threads(?:\/(?:selection|[^/]+(?:\/materials(?:\/[^/]+(?:\/remove)?)?)?))?)$/;
      if (!permitted.test(url.pathname)) { blocked.push(request.url()); return route.abort(); }
      const data = { method: request.method(), path: url.pathname + url.search };
      if (request.postData()) data.body = request.postDataJSON();
      const response = call(data);
      requests.push({ ...data, status: response.status });
      return route.fulfill({ status: response.status, json: response.body });
    }
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: index });
    if (!url.pathname.startsWith('/static/')) { blocked.push(request.url()); return route.abort(); }
    const absolute = path.resolve(webRoot, decodeURIComponent(url.pathname.slice('/static/'.length)));
    if (!absolute.startsWith(webRoot + path.sep)) { blocked.push(request.url()); return route.abort(); }
    const contentType = absolute.endsWith('.js') ? 'text/javascript' : absolute.endsWith('.css') ? 'text/css' : 'text/html';
    try { return await route.fulfill({ body: await readFile(absolute), contentType }); }
    catch { blocked.push(request.url()); return route.fulfill({ status: 404, body: 'Missing local asset' }); }
  });

  await page.goto(`${origin}/`);
  await expect(page.locator('#work-now')).toContainText('核对输入目录');
  await page.locator('#work-notes-tab').click();
  const editor = page.frameLocator('iframe[title="个人笔记编辑器"]');
  const ready = () => expect(editor.locator('#new')).toBeEnabled();
  await ready();
  await editor.locator('#title').fill('重命名练习第一版');
  await editor.locator('#body').fill('版本一原文：先列出输入文件。');
  await editor.locator('#save').click();
  await expect(editor.locator('#status')).toHaveText('笔记已保存。');
  await ready();
  const note = api('GET', '/api/notes').items[0];
  assert.equal(note.version, 1);
  assert.equal(api('GET', '/api/notes/selection').note_id, null);
  assert.equal(selectionWrites().length, 0, 'Saving is not association');
  await editor.locator('#associateNote').click();
  await expect(editor.locator('#status')).toContainText('已明确关联保存版本 1');
  await ready();
  await expect(page.locator('#work-now')).toContainText('本事项尚无当前稿');
  let activity = api('GET', '/api/activity');
  const identity = activity.activity_id;
  assert.equal(typeof identity, 'string');
  assert.ok(identity.length > 0, 'Persistent activity identity is nonempty');
  assert.equal(activity.note, null, 'Personal association never fills the selected matter draft');
  assert.equal(activity.material_source.scope, 'thread');
  assert.equal(activity.material_source.status, 'unbound');
  assert.equal(activity.selection.revision, null, 'Matter display selection is not personal CAS');
  assert.equal(api('GET', '/api/notes/selection').note_id, note.id);
  assert.deepEqual(activity.object_refs, [{ type: 'thread', id: thread.id, version: thread.revision }]);
  assert.equal(selectionWrites().length, 1);
  checks.push('Personal editor association persists without assigning ownership to the selected matter');

  let materials = api('POST', `/api/threads/${thread.id}/materials`, {
    expected_revision: thread.revision, note_id: note.id, version: 1, purpose: 'draft',
  });
  thread = api('GET', `/api/threads/${thread.id}`);
  let draft = materials.items[0];
  assert.equal(draft.purpose, 'draft');
  assert.equal(draft.version, 1);
  await page.reload();
  await expect(page.locator('#work-now')).toContainText('本事项当前稿：重命名练习第一版');
  await page.locator('#work-notes-tab').click();
  await ready();
  await expect(editor.locator('#body')).toHaveValue('版本一原文：先列出输入文件。');
  activity = api('GET', '/api/activity');
  assert.deepEqual(activity.object_refs, [
    { type: 'note', id: note.id, version: 1 }, { type: 'thread', id: thread.id, version: thread.revision },
  ]);
  assert.equal(activity.selection.status, 'selected');
  assert.equal(selectionWrites().length, 1);
  checks.push('Only an explicit draft material link makes saved v1 the matter current draft');

  await editor.locator('#title').fill('重命名练习第二版');
  await editor.locator('#body').fill('版本二原文：补充重名检查。');
  await editor.locator('#save').click();
  await expect(editor.locator('#status')).toHaveText('笔记已保存。');
  await ready();
  activity = api('GET', '/api/activity');
  assert.equal(activity.activity_id, identity);
  assert.equal(activity.selection.selected_version, 1);
  assert.equal(activity.selection.current_version, 2);
  assert.equal(activity.note.title, '重命名练习第一版');
  assert.equal(activity.note.status, 'stale');
  assert.equal('body' in activity.note, false);
  assert.equal(JSON.stringify(activity).includes('版本一原文'), false);
  assert.equal(JSON.stringify(activity).includes('版本二原文'), false);
  assert.equal(selectionWrites().length, 1);
  await page.reload();
  await expect(page.locator('#work-now')).toContainText('重命名练习第一版');
  await expect(page.locator('#work-now')).toContainText('当前已有保存版本 2');
  await page.locator('#work-notes-tab').click();
  await ready();
  await expect(editor.locator('#body')).toHaveValue('版本一原文：先列出输入文件。');
  assert.equal(await editor.locator('#body').evaluate(element => element.readOnly), true);
  await expect(editor.locator('#save')).toBeDisabled();
  await expect(editor.locator('#delete')).toBeDisabled();
  await expect(editor.locator('#noteSelectionStatus')).toContainText('当前已有保存版本 2');
  assert.equal(selectionWrites().length, 1, 'Save and reload never promote the version');

  await editor.locator('#openCurrentNote').click();
  await ready();
  await expect(editor.locator('#body')).toHaveValue('版本二原文：补充重名检查。');
  assert.equal(await editor.locator('#body').evaluate(element => element.readOnly), false);
  assert.equal(api('GET', '/api/notes/selection').selected_version, 1, 'Loading v2 still does not associate it');
  await editor.locator('#associateNote').click();
  await expect(editor.locator('#status')).toContainText('已明确关联保存版本 2');
  await ready();
  await expect(page.locator('#work-now')).toContainText('重命名练习第一版');
  assert.equal(api('GET', '/api/notes/selection').selected_version, 2);
  assert.equal(api('GET', '/api/activity').selection.selected_version, 1,
    'Changing personal selection never promotes a matter draft');
  materials = api('PATCH', `/api/threads/${thread.id}/materials/${draft.id}`, {
    expected_revision: thread.revision, version: 2,
  });
  thread = api('GET', `/api/threads/${thread.id}`);
  draft = materials.items[0];
  await page.reload();
  await expect(page.locator('#work-now')).toContainText('本事项当前稿：重命名练习第二版');
  await page.locator('#work-notes-tab').click();
  await ready();
  await expect(editor.locator('#body')).toHaveValue('版本二原文：补充重名检查。');
  activity = api('GET', '/api/activity');
  assert.equal(activity.activity_id, identity);
  assert.equal(activity.selection.selected_version, 2);
  assert.equal(activity.selection.stale, false);
  assert.equal(activity.selection.status, 'selected');
  checks.push('Saving and personally selecting v2 preserve matter v1 until its own material version is explicitly changed');

  await editor.locator('#clearNoteSelection').click();
  await expect(editor.locator('#status')).toContainText('已取消笔记关联');
  await ready();
  await expect(editor.locator('#body')).toHaveValue('版本二原文：补充重名检查。');
  activity = api('GET', '/api/activity');
  assert.equal(activity.activity_id, identity);
  assert.equal(activity.note.id, note.id, 'Clearing personal selection preserves matter draft');
  assert.equal(activity.selection.selected_version, 2);
  assert.equal(api('GET', '/api/notes/selection').note_id, null);
  api('POST', `/api/threads/${thread.id}/materials/${draft.id}/remove`, {
    expected_revision: thread.revision,
  });
  thread = api('GET', `/api/threads/${thread.id}`);
  activity = api('GET', '/api/activity');
  assert.equal(activity.note, null);
  assert.equal(activity.selection.note_id, null);
  assert.equal(activity.selection.status, 'unbound');
  assert.equal(api('GET', `/api/notes/${note.id}`).version, 2, 'Clearing association preserves the note');
  assert.equal(api('GET', `/api/notes/${note.id}/history`).items.length, 2);
  checks.push('Personal clear preserves matter draft; explicit material unlink preserves both saved note versions');
  // Rebind both scopes before deletion: personal selection clears, whereas the
  // matter keeps a removable missing material instead of adopting another note.
  await editor.locator('#associateNote').click();
  await expect(editor.locator('#status')).toContainText('已明确关联保存版本 2');
  await ready();
  materials = api('POST', `/api/threads/${thread.id}/materials`, {
    expected_revision: thread.revision, note_id: note.id, version: 2, purpose: 'draft',
  });
  thread = api('GET', `/api/threads/${thread.id}`);
  draft = materials.items[0];
  await editor.locator('#delete').click();
  await expect(editor.locator('#status')).toHaveText('笔记已删除。');
  await ready();
  activity = api('GET', '/api/activity');
  assert.equal(activity.activity_id, identity);
  assert.equal(activity.selection.status, 'missing');
  assert.equal(activity.materials[0].id, draft.id);
  assert.equal(activity.materials[0].status, 'missing');
  assert.equal(activity.note, null);
  assert.equal(api('GET', '/api/notes/selection').status, 'none');
  assert.deepEqual(activity.object_refs, [{ type: 'thread', id: thread.id, version: thread.revision }]);
  assert.equal(call({ method: 'GET', path: `/api/notes/${note.id}?version=1` }).status, 404);
  assert.equal(call({ method: 'GET', path: `/api/notes/${note.id}/history` }).status, 404);
  await page.reload();
  await page.locator('#work-notes-tab').click();
  await ready();
  await expect(editor.locator('#title')).toHaveValue('');
  await expect(editor.locator('#noteSelectionStatus')).toContainText('尚未关联');
  await expect(page.locator('#work-now')).toContainText('核对输入目录');
  await expect(page.locator('#work-now')).toContainText('本事项当前稿已被删除或不可用');
  api('POST', `/api/threads/${thread.id}/materials/${draft.id}/remove`, {
    expected_revision: thread.revision,
  });
  assert.equal(api('GET', '/api/activity').selection.status, 'unbound');
  checks.push('Deleting a note clears personal selection and leaves a removable missing matter link without fallback');

  assert.deepEqual(blocked, [], 'All static and API requests stay isolated');
  assert.deepEqual(errors, [], 'No frontend errors');
  assert.equal(requests.every(request => request.status === 200), true);
  console.log(`PASS: ${checks.length} real API/activity browser scenarios`, checks);
} finally {
  if (browser) await browser.close();
  assert.equal(path.dirname(path.resolve(directory)), temporaryRoot, 'Cleanup stays inside the temporary root');
  assert.match(path.basename(directory), /^aqi-activity-browser-/, 'Cleanup targets only this generated test directory');
  await rm(directory, { recursive: true, force: true });
}
