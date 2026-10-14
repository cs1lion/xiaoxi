import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Every asset and API is intercepted. No live DB, model, audio, microphone or socket.
const files = ['style.css', 'workspace.css', 'writing.css', 'experience.css', 'voice/interaction-state.css', 'companion.css', 'avatar/dialogue-review.css', 'note-proposals.css',
  'assistant.js', 'task-card.js', 'interaction-preferences.js', 'companion.js', 'note-proposals.js', 'ui.js', 'chat-scroll.js', 'context-usage.js'];
const assets = new Map(await Promise.all(files.map(async file => [`/static/${file}`, await readFile(fileURLToPath(new URL(`../web/${file}`, import.meta.url)), 'utf8')])));
const html = (await readFile(fileURLToPath(new URL('../web/index.html', import.meta.url)), 'utf8'))
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '')
  .replace('<div id="assistantMount">', '<iframe id="fixtureEditor" title="测试中的未保存笔记" src="/fixture-editor.html"></iframe><div id="assistantMount">')
  .replace('</body>', `<script type="module">import {bootAssistant,showAssistantResult} from '/static/assistant.js';
window.showResult=showAssistantResult; window.noteUpdates=[];
for(const type of ['aqi-notes-updated','activity-updated','companion-updated'])addEventListener(type,e=>noteUpdates.push({type,detail:e.detail}));
await bootAssistant();</script><script type="module" src="/static/ui.js"></script></body>`);
assets.set('/', html);
assets.set('/fixture-editor.html', '<html><body><textarea id="draft">原编辑器未保存的内容</textarea><script>window.loadedAt=performance.timeOrigin;</script></body></html>');
const requests = [], errors = [], unexpected = [], checks = [];
const check = (condition, label) => { if (!condition) throw Error(label); checks.push(label); };
let note = { id: 'note-1', title: '演示计划 <img src=x onerror=alert(1)>', body: '开场\n原预算五百\n最后总结 <script>unsafe</script>', version: 4 };
let selection = { note_id: note.id, selected_version: 3, current_version: 4, revision: 17 };
let serial = 0, selectionConflict = false, editorLoads = 0, patchPause = null, confirmWarnings = ['事项回执暂未同步 <img src=x onerror=alert(1)>'];
const proposals = new Map(), history = new Map([[4, structuredClone(note)]]);
const changedSources = new Set();
const candidate = extra => {
  const value = { id: `candidate-${++serial}`, version: 1, note_id: note.id, base_note_version: note.version,
    original_title: note.title, original_body: note.body, title: '更新演示 <img src=x onerror=alert(1)>', body: '开场\n新预算六百\n最后总结 <script>unsafe</script>',
    source_text: '把已保存笔记的预算改成六百', expires_at: Date.now() / 1000 + 3600, status: 'pending', ...extra };
  proposals.set(value.id, value); return value;
};
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
    if (assets.has(path)) {
      if (path === '/fixture-editor.html') editorLoads++;
      return route.fulfill({ body: assets.get(path), contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'text/html' });
    }
    const body = req.postData() ? req.postDataJSON() : {}; requests.push({ path, method, body });
    const reply = (status, json) => route.fulfill({ status, json: structuredClone(json) });
    if (path === '/api/interaction/preferences') return reply(200, { items: [] });
    if (path === '/api/preferences/proactivity') return reply(200, { timezone: 'Asia/Shanghai', reminder_enabled: false });
    if (['/api/tasks', '/api/reminders', '/api/notifications'].includes(path)) return reply(200, { items: [] });
    if (path === '/api/notes/note-1' && method === 'GET') return reply(200, note);
    if (path === '/api/notes/selection') {
      if (method === 'POST') {
        if (selectionConflict) { selection.revision++; selectionConflict = false; }
        if (selection.revision !== body.expected_revision) return reply(409, { detail: 'selection_conflict' });
        if (body.note_id !== note.id || !history.has(body.version)) return reply(404, { detail: 'missing_saved_version' });
        selection = { ...selection, note_id: body.note_id, selected_version: body.version, revision: selection.revision + 1 };
      }
      return reply(200, selection);
    }
    const match = path.match(/^\/api\/note-proposals\/([^/]+)(?:\/(confirm|reject))?$/);
    if (match) {
      const c = proposals.get(match[1]), action = match[2];
      if (!c || c.expires_at * 1000 <= Date.now() || c.status !== 'pending') return reply(404, { detail: 'candidate_expired_deleted_or_consumed' });
      if (changedSources.has(c.id) && action !== 'reject') return reply(409, { detail: 'draft_source_changed' });
      if (method === 'GET') return reply(200, c);
      if (body.expected_version !== c.version) return reply(409, { detail: 'candidate_version_conflict' });
      if (method === 'PATCH') {
        if (patchPause?.id === c.id) await patchPause.promise;
        if (note.version !== c.base_note_version) return reply(409, { detail: 'preview_or_note_changed' });
        c.title = body.title.trim(); c.body = body.body.trim(); c.version++;
        return reply(200, c);
      }
      if (action === 'confirm') {
        if (note.version !== c.base_note_version) return reply(409, { detail: 'note_version_conflict' });
        note = { ...note, title: c.title, body: c.body, version: note.version + 1 }; history.set(note.version, structuredClone(note)); c.status = 'confirmed'; return reply(200, { ...note, companion_warnings: confirmWarnings });
      }
      if (action === 'reject') { c.status = 'rejected'; return reply(200, { rejected: true }); }
    }
    unexpected.push(`${method} ${url}`); await route.abort();
  });
  await page.goto('http://note-proposals.test/');
  await page.locator('.assistant-panel:not([data-interaction-preferences])').waitFor({ state: 'attached' });
  const editor = page.frameLocator('#fixtureEditor').locator('#draft'); await editor.fill('编辑器中的独立草稿，不能重载');
  const originalEditorLoads = editorLoads;
  const first = candidate();
  await page.locator('#textInput').fill('聊天中也有草稿'); await page.locator('#textInput').focus();
  await page.evaluate(c => showResult({ status: 'awaiting_confirmation', text: '预览', receipts: [], steps: 0, note_proposal: c }), first);
  const firstCard = page.locator(`[data-note-proposal-id="${first.id}"]`);
  await expect(firstCard).toBeVisible(); await expect(page.locator('#textInput')).toBeFocused();
  await expect(firstCard.locator('.note-proposal-scope')).toContainText('来自个人笔记关联');
  await expect(firstCard.locator('.note-proposal-scope')).toContainText('不会自动归入当前事项');
  check(!first.source_context, 'A personal candidate is explicitly identified without inferring a matter scope');
  check(requests.every(r => r.method === 'GET') && note.version === 4, 'Receiving a candidate writes no note or association and preserves chat focus');
  await expect(firstCard.locator('.note-proposal-columns')).toContainText(note.body);
  check(await firstCard.locator('img, script').count() === 0, 'Original title/body, replacement and source render hostile markup as text');
  const diff = firstCard.locator('.note-proposal-diff'); await diff.locator('summary').click();
  await expect(diff).toContainText('− 移除'); await expect(diff).toContainText('+ 新增');
  await expect(diff).toContainText('原预算五百'); await expect(diff).toContainText('新预算六百');
  check(await firstCard.locator('a').first().getAttribute('href') === '/static/notes.html?note_id=note-1&version=4', 'The original link opens the precise saved base version');
  const titleInput = firstCard.getByRole('textbox', { name: '候选标题', exact: true });
  const bodyInput = firstCard.getByRole('textbox', { name: '候选正文', exact: true });
  await titleInput.fill('我的演示修订'); await bodyInput.fill('开场\n确认预算六百\n保留总结');
  await expect(firstCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeDisabled();
  await firstCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(firstCard.locator('.note-proposal-meta')).toContainText('预览版本 2');
  await expect(firstCard.locator('.note-proposal-columns')).toContainText('确认预算六百');
  check(note.version === 4 && first.version === 2, 'Editing only updates a versioned preview; no saved revision is written');
  const patch = requests.find(r => r.method === 'PATCH');
  check(Object.keys(patch.body).sort().join() === 'body,expected_version,title' && patch.body.expected_version === 1, 'Preview PATCH binds title and body to the exact candidate version');
  await firstCard.getByRole('button', { name: '确认保存新版本', exact: true }).click();
  await expect(firstCard.locator('.note-proposal-meta')).toHaveText('已保存为笔记版本 5');
  await expect(firstCard.locator('.note-proposal-feedback')).toContainText('尚未自动关联');
  await expect(firstCard.getByRole('link', { name: '打开保存版本 5' })).toHaveAttribute('href', '/static/notes.html?note_id=note-1&version=5');
  check(note.version === 5 && note.body === '开场\n确认预算六百\n保留总结' && selection.selected_version === 3, 'Confirmation shows the real returned note version and keeps the old chosen version');
  await expect(firstCard.locator('.note-proposal-warning')).toContainText('笔记已保存');
  await expect(firstCard.locator('.note-proposal-warning')).toContainText('不要重新保存');
  check(await firstCard.locator('.note-proposal-warning img').count() === 0 && await firstCard.getByRole('button', { name: '确认保存新版本', exact: true }).count() === 0, 'Receipt-sync warnings preserve successful save status, safe text and no resave control');
  check(!requests.some(r => r.path === '/api/notes/selection'), 'Saving never even reads or writes selection until explicit association');
  await expect(editor).toHaveValue('编辑器中的独立草稿，不能重载');
  check(editorLoads === originalEditorLoads && await page.evaluate(() => ['aqi-notes-updated','activity-updated','companion-updated'].every(type => noteUpdates.some(e => e.type === type))), 'Save announces note/activity/companion changes without reloading the editor');
  selectionConflict = true;
  const associate = firstCard.getByRole('button', { name: '关联此保存版本', exact: true });
  await associate.click(); await expect(firstCard.locator('.note-proposal-feedback')).toContainText('没有自动重试');
  check(requests.filter(r => r.path === '/api/notes/selection' && r.method === 'POST').length === 1 && selection.selected_version === 3, 'Association CAS conflict sends once and preserves the original selection');
  await associate.click(); await expect(firstCard.locator('.note-proposal-feedback')).toContainText('已明确关联保存版本 5');
  check(selection.selected_version === 5 && requests.filter(r => r.path === '/api/notes/selection' && r.method === 'POST').at(-1).body.expected_revision === 18, 'Explicit association rereads selection and submits the actual saved version with CAS');
  await expect(editor).toHaveValue('编辑器中的独立草稿，不能重载'); check(editorLoads === originalEditorLoads, 'Association leaves the independent editor and its draft intact');
  const conflict = candidate(); await page.evaluate(c => showResult({ note_proposal: c }), conflict);
  const conflictCard = page.locator(`[data-note-proposal-id="${conflict.id}"]`);
  await conflictCard.getByRole('textbox', { name: '候选标题', exact: true }).fill('本地标题');
  await conflictCard.getByRole('textbox', { name: '候选正文', exact: true }).fill('本地正文草稿');
  conflict.version++; conflict.title = '另一个窗口标题'; conflict.body = '另一窗口正文';
  const patchesBefore = requests.filter(r => r.method === 'PATCH').length;
  await conflictCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(conflictCard.getByRole('textbox', { name: '候选标题', exact: true })).toHaveValue('本地标题');
  await expect(conflictCard.getByRole('textbox', { name: '候选正文', exact: true })).toHaveValue('本地正文草稿');
  await expect(conflictCard.locator('.note-proposal-columns')).toContainText('另一窗口正文');
  await expect(conflictCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeDisabled();
  check(requests.filter(r => r.method === 'PATCH').length === patchesBefore + 1, '409 reads current candidate, retains both draft fields and does not replay PATCH');
  await conflictCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(conflictCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeEnabled();
  note = { ...note, body: '其他窗口保存的新原稿', version: note.version + 1 }; history.set(note.version, structuredClone(note));
  const confirmsBefore = requests.filter(r => r.path.endsWith('/confirm')).length;
  await conflictCard.getByRole('button', { name: '确认保存新版本', exact: true }).click();
  await expect(conflictCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeDisabled();
  await expect(conflictCard.getByRole('textbox', { name: '候选正文', exact: true })).toHaveValue('本地正文草稿');
  check(requests.filter(r => r.path.endsWith('/confirm')).length === confirmsBefore + 1 && note.version === 6, 'Saved-note version conflict retains the draft and never retries confirmation');
  await expect(conflictCard.getByRole('button', { name: '更新预览', exact: true })).toBeDisabled();
  await expect(conflictCard.locator('.note-proposal-feedback')).toContainText('原稿已有新版');
  await expect(conflictCard.locator('.note-proposal-current')).toHaveAttribute('href', '/static/notes.html?note_id=note-1&version=6');
  check(conflict.base_note_version === 5 && conflict.original_body !== note.body && conflict.body === '本地正文草稿', 'Base-note conflict keeps the original snapshot, disables update/save and links to the actual current version');
  await conflictCard.getByRole('button', { name: '不保存', exact: true }).click();
  await expect(conflictCard.locator('.note-proposal-feedback')).toContainText('未保存');
  check(note.version === 6 && conflict.status === 'rejected', 'Reject does not save a note revision or change its association');
  const oldBase = candidate({ base_note_version: 5, original_title: history.get(5).title, original_body: history.get(5).body });
  await page.evaluate(c => showResult({ note_proposal: c }), oldBase);
  const oldBaseCard = page.locator(`[data-note-proposal-id="${oldBase.id}"]`);
  await oldBaseCard.getByRole('textbox', { name: '候选正文', exact: true }).fill('旧原稿上保留的本地草稿');
  const basePatchesBefore = requests.filter(r => r.method === 'PATCH').length;
  await oldBaseCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(oldBaseCard.getByRole('button', { name: '更新预览', exact: true })).toBeDisabled();
  await expect(oldBaseCard.getByRole('textbox', { name: '候选正文', exact: true })).toHaveValue('旧原稿上保留的本地草稿');
  await expect(oldBaseCard.getByRole('button', { name: '不保存', exact: true })).toBeEnabled();
  check(requests.filter(r => r.method === 'PATCH').length === basePatchesBefore + 1 && oldBase.base_note_version === 5 && note.version === 6, 'A PATCH base conflict cannot rebase or retry; draft survives and rejection remains available');
  await oldBaseCard.getByRole('button', { name: '不保存', exact: true }).click();
  const live = candidate(); await page.evaluate(c => showResult({ note_proposal: c }), live);
  const liveCard = page.locator(`[data-note-proposal-id="${live.id}"]`), liveBody = liveCard.getByRole('textbox', { name: '候选正文', exact: true });
  await liveBody.fill('正在编辑的正文'); await liveBody.focus(); live.version++; live.body = '新到达的服务器预览';
  await page.evaluate(c => showResult({ note_proposal: c }), live);
  await expect(liveBody).toHaveValue('正在编辑的正文'); await expect(liveBody).toBeFocused();
  check(await liveCard.count() === 1, 'A newer arrival updates preview in place without replacing the draft node or focus');
  await liveCard.getByRole('button', { name: '不保存', exact: true }).click();
  const delayed = candidate(); await page.evaluate(c => showResult({ note_proposal: c }), delayed);
  const delayedCard = page.locator(`[data-note-proposal-id="${delayed.id}"]`), delayedBody = delayedCard.getByRole('textbox', { name: '候选正文', exact: true });
  let releasePatch; patchPause = { id: delayed.id, promise: new Promise(resolve => { releasePatch = resolve; }) };
  await delayedBody.fill('提交时的草稿');
  await delayedCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(delayedCard.getByRole('button', { name: '更新预览', exact: true })).toBeDisabled();
  await delayedBody.fill('请求期间继续输入的新草稿'); await delayedBody.focus(); releasePatch();
  await expect(delayedCard.locator('.note-proposal-columns')).toContainText('提交时的草稿');
  await expect(delayedBody).toHaveValue('请求期间继续输入的新草稿'); await expect(delayedBody).toBeFocused();
  await expect(delayedCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeDisabled();
  check(note.version === 6, 'A delayed preview response keeps newer in-flight typing and focus; only submitted text enters the preview');
  patchPause = null;
  await delayedCard.getByRole('button', { name: '不保存', exact: true }).click();
  const scopedSource = () => ({scope:'thread',thread_id:'原事项 <img src=x onerror=alert(1)>',material_id:'draft-link-1',purpose:'draft',ref:{type:'note',id:note.id,version:note.version}});
  const sourceChanged = candidate({thread_id:'original-matter',source_context:scopedSource()});
  await page.evaluate(c => showResult({ note_proposal:c }), sourceChanged);
  const sourceChangedCard = page.locator(`[data-note-proposal-id="${sourceChanged.id}"]`);
  await expect(sourceChangedCard.locator('.note-proposal-scope')).toContainText('切换事项不会改变本候选归属');
  check(await sourceChangedCard.locator('.note-proposal-scope img').count() === 0, 'Matter source scope displays the original identity as safe text');
  await sourceChangedCard.getByRole('textbox',{name:'候选标题',exact:true}).fill('资料关联改变后保留的标题');
  await sourceChangedCard.getByRole('textbox',{name:'候选正文',exact:true}).fill('资料关联改变后保留的正文');
  changedSources.add(sourceChanged.id);
  const sourcePatchesBefore = requests.filter(r => r.method === 'PATCH').length;
  await sourceChangedCard.getByRole('button',{name:'更新预览',exact:true}).click();
  await expect(sourceChangedCard.locator('.note-proposal-feedback')).toContainText('原事项当前稿的关联已移除');
  await expect(sourceChangedCard.getByRole('textbox',{name:'候选标题',exact:true})).toHaveValue('资料关联改变后保留的标题');
  await expect(sourceChangedCard.getByRole('textbox',{name:'候选正文',exact:true})).toHaveValue('资料关联改变后保留的正文');
  await expect(sourceChangedCard.getByRole('button',{name:'更新预览',exact:true})).toBeDisabled();
  await expect(sourceChangedCard.getByRole('button',{name:'确认保存新版本',exact:true})).toBeDisabled();
  await expect(sourceChangedCard.getByRole('button',{name:'不保存',exact:true})).toBeEnabled();
  check(requests.filter(r => r.method === 'PATCH').length === sourcePatchesBefore + 1 && note.version === 6, 'Changed matter draft source stops a preview write once and preserves both fields without rebinding');
  await sourceChangedCard.getByRole('button',{name:'不保存',exact:true}).click();
  await expect(sourceChangedCard.locator('.note-proposal-feedback')).toContainText('未保存');
  check(sourceChanged.status === 'rejected' && note.version === 6, 'A source-changed proposal remains explicitly rejectable without a note revision');
  const sourceConfirm = candidate({thread_id:'original-matter',source_context:scopedSource()});
  await page.evaluate(c => showResult({note_proposal:c}), sourceConfirm);
  const sourceConfirmCard = page.locator(`[data-note-proposal-id="${sourceConfirm.id}"]`);
  changedSources.add(sourceConfirm.id);
  const sourceConfirmsBefore = requests.filter(r => r.path.endsWith('/confirm')).length;
  await sourceConfirmCard.getByRole('button',{name:'确认保存新版本',exact:true}).click();
  await expect(sourceConfirmCard.locator('.note-proposal-feedback')).toContainText('原事项当前稿的关联已移除');
  await expect(sourceConfirmCard.getByRole('textbox',{name:'候选正文',exact:true})).toHaveValue(sourceConfirm.body);
  await expect(sourceConfirmCard.getByRole('button',{name:'确认保存新版本',exact:true})).toBeDisabled();
  check(requests.filter(r => r.path.endsWith('/confirm')).length === sourceConfirmsBefore + 1 && note.version === 6, 'Changed source rejects confirmation without retrying or adopting the newly selected matter');
  await sourceConfirmCard.getByRole('button',{name:'不保存',exact:true}).click();
  const scoped = candidate({thread_id:'original-matter',source_context:scopedSource()});
  await page.evaluate(c => showResult({note_proposal:c}), scoped);
  const scopedCard = page.locator(`[data-note-proposal-id="${scoped.id}"]`);
  const noteSelectionRequestsBefore = requests.filter(r => r.path === '/api/notes/selection').length;
  const selectedBeforeScoped = structuredClone(selection);
  await scopedCard.getByRole('button',{name:'确认保存新版本',exact:true}).click();
  await expect(scopedCard.locator('.note-proposal-feedback')).toContainText('原事项仍关联原保存版本');
  await expect(scopedCard.locator('.note-proposal-feedback')).toContainText('不会自动添加成果或标记完成');
  const scopedMaterialsUrl = new URL(await scopedCard.getByRole('link',{name:'核对原事项资料',exact:true}).getAttribute('href'),'http://note-proposals.test');
  check(scopedMaterialsUrl.pathname === '/static/threads.html' && scopedMaterialsUrl.searchParams.get('thread_id') === scoped.source_context.thread_id && await scopedCard.getByRole('button',{name:'关联此保存版本',exact:true}).count() === 0 && requests.filter(r => r.path === '/api/notes/selection').length === noteSelectionRequestsBefore && JSON.stringify(selection) === JSON.stringify(selectedBeforeScoped), 'Saving a matter draft links its original matter without any global note association');
  check(note.version === 7 && history.has(7) && scoped.source_context.ref.version === 6, 'A scoped save returns the new note version and preserves the original fixed material identity');
  await expect(editor).toHaveValue('编辑器中的独立草稿，不能重载');
  const expired = candidate({ expires_at: Date.now() / 1000 + 0.3 }); await page.evaluate(c => showResult({ note_proposal: c }), expired);
  const expiredCard = page.locator(`[data-note-proposal-id="${expired.id}"]`);
  await expiredCard.getByRole('textbox', { name: '候选正文', exact: true }).fill('过期仍保留正文');
  await expect(expiredCard.getByRole('button', { name: '更新预览', exact: true })).toBeDisabled();
  await expect(expiredCard.getByRole('textbox', { name: '候选正文', exact: true })).toHaveValue('过期仍保留正文');
  check(note.version === 7, 'TTL expiry disables writes while preserving the editable draft');
  const removed = candidate(); await page.evaluate(c => showResult({ note_proposal: c }), removed);
  const removedCard = page.locator(`[data-note-proposal-id="${removed.id}"]`);
  await removedCard.getByRole('textbox', { name: '候选标题', exact: true }).fill('被删除候选的标题'); proposals.delete(removed.id);
  await removedCard.getByRole('button', { name: '更新预览', exact: true }).click();
  await expect(removedCard.getByRole('textbox', { name: '候选标题', exact: true })).toHaveValue('被删除候选的标题');
  await expect(removedCard.getByRole('button', { name: '确认保存新版本', exact: true })).toBeDisabled();
  check(note.version === 7, '404 keeps the user draft and disables an unavailable candidate');
  const longBody = Array.from({ length: 500 }, (_, i) => `第 ${i + 1} 段 · 演示内容`).join('\n');
  const long = candidate({ original_title: '演示计划', title: '演示计划 · 调整开场', original_body: longBody, body: longBody.replace('第 250 段', '第 250 段改写'), source_text: '调整已保存计划的开场，其余内容保留' });
  await page.evaluate(c => showResult({ note_proposal: c }), long);
  const longCard = page.locator(`[data-note-proposal-id="${long.id}"]`);
  check(await longCard.locator('.note-proposal-compare').evaluate(el => !el.open) && await longCard.locator('.note-proposal-edit').evaluate(el => !el.open), 'Long manuscript comparison and editor start folded');
  await longCard.locator('.note-proposal-diff summary').click();
  await expect(longCard.locator('.note-diff-lines')).toContainText('第 250 段改写');
  check(await longCard.locator('.note-diff-lines').evaluate(el => el.getBoundingClientRect().height <= 261), 'Long line differences stay inside a bounded scroll area');
  await page.setViewportSize({ width: 390, height: 844 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px chat and side-by-side note comparison have no horizontal overflow');
  check(await longCard.getByRole('button', { name: '确认保存新版本', exact: true }).evaluate(el => el.getBoundingClientRect().height >= 44), 'Mobile candidate actions retain 44px touch targets');
  check(errors.length === 0 && unexpected.length === 0 && editorLoads === originalEditorLoads, 'No runtime errors, external requests or independent-editor reloads');
  await page.evaluate(id => { document.querySelectorAll('.note-proposal').forEach(card => { if (card.dataset.noteProposalId !== id) card.remove(); }); }, long.id);
  await longCard.locator('.note-proposal-diff summary').click();
  await mkdir(fileURLToPath(new URL('../eval/ui/notes', import.meta.url)), { recursive: true });
  await longCard.scrollIntoViewIfNeeded();
  await page.screenshot({ path: fileURLToPath(new URL('../eval/ui/notes/revision-proposal-mobile.png', import.meta.url)) });
  console.log(JSON.stringify({ result: 'PASS', scope: 'Synthetic note APIs and actual homepage UI; no production writes, model or audio', checks }, null, 2));
} finally { await browser.close(); }
