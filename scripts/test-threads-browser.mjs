import { chromium, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

// Fully offline browser test: all documents, assets and API calls use synthetic routes.
const assets = new Map(await Promise.all(['html','js','css'].map(async ext => [`/static/threads.${ext}`, await readFile(fileURLToPath(new URL(`../web/threads.${ext}`, import.meta.url)), 'utf8')])));
const browser = await chromium.launch({headless:true});
const checks = [], errors = [], requests = [];
const threads = new Map(), proposals = new Map(), histories = new Map(); let selected = null, serial = 0, eventSerial = 0, proposalSerial = 0, materialSerial = 0;
const notes = new Map([
  ['material-note', [{id:'material-note',title:'方案 <img src=x onerror=alert(1)>',version:1},{id:'material-note',title:'方案第二稿',version:2}]],
  ['other-note', [{id:'other-note',title:'参考资料',version:1}]],
]);
const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
function captureVersion(item, entry, {evidence = {state:'manual'}, object_refs = []} = {}) {
  const versions = histories.get(entry.id) || [];
  entry.version = versions.length + 1; entry.evidence = structuredClone(evidence); entry.object_refs = structuredClone(object_refs);
  versions.push({version:entry.version,previous_version:entry.version > 1 ? entry.version - 1 : null,text:entry.text,source:entry.source,evidence:entry.evidence,object_refs:entry.object_refs,created_at:1000+entry.version});
  histories.set(entry.id,structuredClone(versions));
}
try {
  const page = await browser.newPage({viewport:{width:1200,height:900}});
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), pathname = url.pathname, method = req.method();
    if (assets.has(pathname)) return route.fulfill({contentType:pathname.endsWith('.js')?'text/javascript':pathname.endsWith('.css')?'text/css':'text/html',body:assets.get(pathname)});
    if (!pathname.startsWith('/api/threads') && !pathname.startsWith('/api/thread-proposals/') && !pathname.startsWith('/api/notes')) throw Error(`Unexpected network request: ${url}`);
    const body = req.postData() ? req.postDataJSON() : {};
    requests.push({path:pathname,method,body});
    const reply = (status, data) => route.fulfill({status,json:structuredClone(data)});
    if (pathname === '/api/notes' && method === 'GET') {
      const query = url.searchParams.get('query') || '';
      return reply(200,{items:[...notes.values()].map(versions => versions.at(-1)).filter(note => note.title.includes(query))});
    }
    if (pathname.startsWith('/api/notes/')) {
      const [, rawId, action] = pathname.match(/^\/api\/notes\/([^/]+)(?:\/(history))?$/) || [];
      const versions = notes.get(decodeURIComponent(rawId || ''));
      if (!versions) return reply(404,{detail:'note missing'});
      if (method !== 'GET') throw Error('Material UI must never mutate notes');
      return reply(200,action === 'history' ? {items:[...versions].reverse()} : versions.at(-1));
    }
    if (pathname.startsWith('/api/thread-proposals/')) {
      const [, pid, action] = pathname.match(/^\/api\/thread-proposals\/([^/]+)(?:\/(confirm|reject))?$/) || [];
      const proposal = proposals.get(pid), item = threads.get(proposal?.thread_id);
      if (!proposal || !item) return reply(404,{detail:'missing proposal'});
      if (proposal.status !== 'pending' || body.expected_version !== proposal.version) return reply(409,{detail:'proposal version conflict'});
      if (method === 'PATCH' && !action) { proposal.text = body.text; proposal.version++; proposal.expected_revision = item.revision; return reply(200,proposal); }
      if (action === 'reject') { proposal.status = 'rejected'; return reply(200,{rejected:true}); }
      if (action === 'confirm') {
        if (proposal.expected_revision !== item.revision) return reply(409,{detail:'revision conflict'});
        const entry = {id:`event-${++eventSerial}`,kind:proposal.kind,text:proposal.text,source:'user_stated',source_id:null};
        captureVersion(item,entry,{evidence:{state:'user_confirmed',request_text:proposal.source_text,session_id:'private-session-origin',captured_at:1000},object_refs:proposal.object_refs});
        item.events.push(entry); item.revision++; proposal.status = 'confirmed'; return reply(200,item);
      }
      return reply(400,{detail:'unsupported proposal request'});
    }
    if (pathname === '/api/threads/selection') {
      if (method === 'POST') { if (body.thread_id && !threads.has(body.thread_id)) return reply(404,{detail:'missing'}); selected = body.thread_id; }
      return reply(200,{thread_id:selected});
    }
    if (pathname === '/api/threads') {
      if (method === 'GET') return reply(200,{items:[...threads.values()]});
      const item = {id:`thread-${++serial}`,title:body.title,status:'discussing',next_step:'',allow_callbacks:false,revision:1,events:[],materials:[]}; threads.set(item.id,item); return reply(201,item);
    }
    const [, id, segment, eid, tail] = pathname.match(/^\/api\/threads\/([^/]+)(?:\/([^/]+))?(?:\/([^/]+))?(?:\/(history|remove))?$/) || [];
    const item = threads.get(id); if (!item) return reply(404,{detail:'missing'});
    if (segment === 'materials') {
      const response = () => ({thread_id:id,revision:item.revision,items:item.materials.map(link => {
        const versions = notes.get(link.note_id), exact = versions?.find(note => note.version === link.version), latest = versions?.at(-1);
        return {...link,title:exact?.title || null,current_version:latest?.version || null,status:!exact ? 'missing' : latest.version !== link.version ? 'stale' : 'selected'};
      })});
      if (method === 'GET') return reply(200,response());
      if (body.expected_revision !== item.revision) return reply(409,{detail:'revision conflict'});
      const link = eid ? item.materials.find(value => value.id === eid) : null;
      if (eid && !link) return reply(404,{detail:'missing material'});
      if (tail === 'remove') { item.materials = item.materials.filter(value => value.id !== eid); item.revision++; return reply(200,response()); }
      const candidate = {...(link || {}),note_id:link?.note_id || body.note_id,version:body.version ?? link?.version,purpose:body.purpose ?? link?.purpose};
      if (!notes.get(candidate.note_id)?.some(note => note.version === candidate.version)) return reply(404,{detail:'note version missing'});
      if (!['reference','draft','artifact'].includes(candidate.purpose)) return reply(400,{detail:'invalid purpose'});
      if (item.materials.some(value => value.id !== eid && value.note_id === candidate.note_id && value.version === candidate.version)) return reply(400,{detail:'同一笔记版本已有用途，请先修改原关联'});
      if (candidate.purpose === 'draft' && item.materials.some(value => value.id !== eid && value.purpose === 'draft')) return reply(400,{detail:'已有当前稿，请先调整旧用途或解除关联'});
      if (!link && item.materials.length >= 12) return reply(400,{detail:'material limit'});
      if (link) Object.assign(link,candidate,{updated_at:1001});
      else item.materials.push({...candidate,id:`material-${++materialSerial}`,thread_id:id,created_at:1000,updated_at:1000});
      item.revision++; return reply(200,response());
    }
    if (segment === 'events' && tail === 'history' && method === 'GET') {
      const entry = item.events.find(event => event.id === eid);
      if (!entry) return reply(404,{detail:'missing event'});
      return reply(200,{items:histories.get(eid) || [{version:1,previous_version:null,text:entry.text,source:entry.source,evidence:entry.evidence || {state:entry.source === 'tool_verified' ? 'tool_verified' : 'legacy'},object_refs:entry.object_refs || [],created_at:1000}]});
    }
    if (segment === 'proposals') {
      if (method === 'GET') return reply(200,{items:[...proposals.values()].filter(proposal => proposal.thread_id === id && proposal.status === 'pending')});
      const proposal = {id:`proposal-${++proposalSerial}`,version:1,thread_id:id,expected_revision:item.revision,kind:'decision',text:body.utterance.replace(/^记个决定[:：]\s*/,''),source_text:body.utterance,status:'pending',expires_at:'2099-01-01T00:00:00Z',object_refs:[{type:'note',id:'draft-note <&>',version:2}]};
      proposals.set(proposal.id,proposal); return reply(201,proposal);
    }
    if (method === 'GET') return reply(200,item);
    if (body.expected_revision !== item.revision) return reply(409,{detail:'revision conflict'});
    if (segment === 'delete') { threads.delete(id); if (selected === id) selected = null; return reply(200,{ok:true}); }
    if (segment === 'events') {
      if (method === 'POST') { const entry = {id:`event-${++eventSerial}`,kind:body.kind,text:body.text,source:'user_stated',source_id:null}; captureVersion(item,entry); item.events.push(entry); }
      else { const entry = item.events.find(event => event.id === eid); if (!entry || entry.source !== 'user_stated') return reply(403,{detail:'read only'}); entry.text = body.text; captureVersion(item,entry); }
    } else for (const key of ['title','status','next_step','allow_callbacks']) if (key in body) item[key] = body[key];
    item.revision++; return reply(200,item);
  });
  await page.goto('http://threads.test/static/threads.html');
  await expect(page.locator('#selection-label')).toContainText('暂不关联');
  await page.locator('#new-title').fill('小作品 <img src=x onerror=alert(1)>');
  await page.getByRole('button',{name:'新建',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('项目已创建');
  const item = [...threads.values()][0];
  check(item && selected === null && item.allow_callbacks === false,'Create does not select or opt in to callbacks');
  check(await page.locator('#detail-heading img').count() === 0,'User markup renders as text, never HTML');
  await page.locator('#select-thread').click(); await expect(page.locator('#selection-label')).toContainText('小作品');
  check(selected === item.id,'Explicit global selection persisted');
  await page.locator('#title').fill('周末小作品'); await page.locator('#thread-status').selectOption('active');
  await page.locator('#next-step').fill('画第一张草图'); await page.locator('#allow-callbacks').check();
  await page.getByRole('button',{name:'保存项目设置',exact:true}).click(); await expect(page.locator('#notice')).toHaveText('项目设置已保存。');
  check(item.title === '周末小作品' && item.status === 'active' && item.next_step === '画第一张草图' && item.allow_callbacks,'Title, status, next step and explicit callback opt-in saved');
  check(!requests.some(request => request.path.startsWith('/api/notes')),'Opening a matter does not search notes or use the global note selection');
  await page.getByRole('button',{name:'查找笔记',exact:true}).click();
  await expect(page.locator('#material-search-results button')).toHaveCount(2);
  await page.locator('[data-note-id="material-note"]').click();
  await expect(page.locator('#material-link-form')).toBeVisible();
  await page.locator('#material-version').selectOption('1'); await page.locator('#material-purpose').selectOption('draft');
  check(item.materials.length === 0 && !(await page.locator('#material-link-form img').count()),'Selecting and reviewing a saved note version does not associate it or render title markup');
  await page.locator('#link-material').click(); await expect(page.locator('#notice')).toContainText('已将指定笔记保存版本关联');
  const firstMaterial = item.materials[0];
  check(firstMaterial.note_id === 'material-note' && firstMaterial.version === 1 && firstMaterial.purpose === 'draft' && selected === item.id,'Explicit material association records this matter, fixed version and purpose without altering global selections');
  const firstMaterialCard = page.locator(`[data-material-id="${firstMaterial.id}"]`);
  await expect(firstMaterialCard).toContainText('本事项仍使用指定的版本 1');
  check(new URL(await firstMaterialCard.locator('a').getAttribute('href'),'http://threads.test').searchParams.get('version') === '1','Material link opens the explicitly chosen historical version');
  notes.get('material-note').push({id:'material-note',title:'方案第三稿',version:3});
  await page.locator('#refresh').click(); await expect(firstMaterialCard).toContainText('笔记已有版本 3');
  check(firstMaterial.version === 1,'Later note saves do not promote the linked version');
  await firstMaterialCard.getByRole('button',{name:'修改版本或用途',exact:true}).click();
  await firstMaterialCard.locator('[data-field="version"]').selectOption('2');
  await firstMaterialCard.locator('[data-field="purpose"]').selectOption('artifact');
  await firstMaterialCard.getByRole('button',{name:'保存资料关联修改',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('资料版本与用途已明确更新');
  check(firstMaterial.version === 2 && firstMaterial.purpose === 'artifact' && item.status === 'active','Explicit version and purpose edits do not mark the matter completed');
  await page.locator('[data-note-id="other-note"]').click(); await page.locator('#material-purpose').selectOption('draft');
  item.revision++; // Another window changes the matter after the user chose a note.
  const materialPostsBefore = requests.filter(request => request.path === `/api/threads/${item.id}/materials` && request.method === 'POST').length;
  await page.locator('#link-material').click(); await expect(page.locator('#notice')).toContainText('未重试提交');
  await expect(page.locator('#material-note-title')).toContainText('参考资料');
  await expect(page.locator('#material-purpose')).toHaveValue('draft');
  check(item.materials.length === 1 && requests.filter(request => request.path === `/api/threads/${item.id}/materials` && request.method === 'POST').length === materialPostsBefore + 1,'Material conflict retains the selected note and purpose without replaying a write');
  await page.locator('#link-material').click(); await expect(page.locator('#notice')).toContainText('已将指定笔记保存版本关联');
  const secondMaterial = item.materials[1];
  await page.locator('[data-note-id="material-note"]').click(); await page.locator('#material-version').selectOption('2'); await page.locator('#material-purpose').selectOption('reference');
  await page.locator('#link-material').click(); await expect(page.locator('#notice')).toContainText('同一笔记版本已有用途');
  check(item.materials.length === 2,'Duplicate fixed versions cannot silently acquire another purpose');
  await page.locator('#material-version').selectOption('3'); await page.locator('#material-purpose').selectOption('draft');
  await page.locator('#link-material').click(); await expect(page.locator('#notice')).toContainText('已有当前稿');
  check(item.materials.filter(link => link.purpose === 'draft').length === 1,'Second current draft is refused with a route to change or unlink the prior draft');
  await page.locator('#cancel-material').click();
  await firstMaterialCard.getByRole('button',{name:'修改版本或用途',exact:true}).click();
  await firstMaterialCard.locator('[data-field="purpose"]').selectOption('reference'); item.revision++;
  await firstMaterialCard.getByRole('button',{name:'保存资料关联修改',exact:true}).click(); await expect(page.locator('#notice')).toContainText('未重试提交');
  await expect(firstMaterialCard.locator('[data-field="purpose"]')).toHaveValue('reference');
  check(firstMaterial.purpose === 'artifact','Conflict preserves material editing fields and never implicitly changes purpose');
  await firstMaterialCard.getByRole('button',{name:'保存资料关联修改',exact:true}).click(); await expect(page.locator('#notice')).toContainText('资料版本与用途已明确更新');
  notes.delete('other-note'); await page.locator('#refresh').click();
  const missingMaterialCard = page.locator(`[data-material-id="${secondMaterial.id}"]`);
  await expect(missingMaterialCard).toContainText('保存版本已不可用');
  await expect(missingMaterialCard.getByRole('button',{name:'修改版本或用途',exact:true})).toBeDisabled();
  check(await missingMaterialCard.locator('a').count() === 0,'Deleted note links remain visible for cleanup without exposing a stale open link');
  await missingMaterialCard.getByRole('button',{name:'解除关联',exact:true}).click(); await expect(page.locator('#notice')).toContainText('笔记及保存版本未删除');
  await firstMaterialCard.getByRole('button',{name:'解除关联',exact:true}).click(); await expect(page.locator('#notice')).toContainText('笔记及保存版本未删除');
  check(item.materials.length === 0 && notes.get('material-note').length === 3 && !requests.some(request => request.path.startsWith('/api/notes') && request.method !== 'GET'),'Removing associations does not delete note bodies, versions or global selection');
  const sibling = {id:'scope-only-thread',title:'另一事项',status:'discussing',next_step:'',allow_callbacks:false,revision:1,events:[],materials:[]};
  threads.set(sibling.id,sibling); await page.locator('#refresh').click();
  await page.locator('[data-note-id="material-note"]').click();
  await page.locator(`[data-thread-id="${sibling.id}"]`).click();
  await expect(page.locator('#detail-heading')).toHaveText('另一事项'); await expect(page.locator('#material-link-form')).toBeHidden();
  check(await page.locator('#materials [data-material-id]').count() === 0 && selected === item.id,'Viewing another matter clears an uncommitted material choice and does not inherit associations or alter chat selection');
  await page.locator(`[data-thread-id="${item.id}"]`).click(); await expect(page.locator('#detail-heading')).toHaveText('周末小作品');
  threads.delete(sibling.id);
  await page.locator('#event-kind').selectOption('decision'); await page.locator('#event-text').fill('先做纸上草图');
  await page.getByRole('button',{name:'添加记录',exact:true}).click(); await expect(page.locator('#notice')).toContainText('记录已添加');
  check(item.events[0].source === 'user_stated','Add decision remains unverified self-report');
  await expect(page.locator('.event')).toContainText('用户自述 · 未经核验');
  check(!requests.some(request => request.path.includes('/events/') && request.path.endsWith('/history')),'History is fetched only after an explicit expand action');
  const firstEvent = page.locator(`[data-event-id="${item.events[0].id}"]`);
  await firstEvent.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  await expect(firstEvent.locator('.event-history')).toContainText('记录版本 1 · 首个保存版本');
  await expect(firstEvent.locator('.event-history')).toContainText('依据：手动提交或更正');
  check(await firstEvent.locator('.event-request').count() === 0,'Manual submissions do not invent a confirmed source request');
  await page.getByRole('button',{name:'更正自述',exact:true}).click(); await page.locator('#edit-text').fill('更正：先做数字草图');
  await page.getByRole('button',{name:'保存更正',exact:true}).click(); await expect(page.locator('#notice')).toContainText('用户自述已更正');
  check(item.events[0].text === '更正：先做数字草图','Correct user event with expected revision');
  await firstEvent.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  await expect(firstEvent.locator('.event-revision')).toHaveCount(2);
  await expect(firstEvent.locator('.event-history')).toContainText('记录版本 2 · 更正自版本 1');
  check((await firstEvent.locator('.event-revision p').allTextContents()).includes('先做纸上草图'),'Revisions retain the original saved text and predecessor relation');
  const historyRequests = requests.filter(request => request.path.endsWith('/history')).length;
  await firstEvent.getByRole('button',{name:'收起依据与修订',exact:true}).click();
  await firstEvent.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  check(requests.filter(request => request.path.endsWith('/history')).length === historyRequests + 1,'Reopening history reads fresh snapshots instead of cached provenance');
  item.events.push({id:'receipt-1',kind:'receipt',text:'工具执行完成',source:'tool_verified',source_id:'run-123',evidence:{state:'tool_verified',session_id:'private-receipt-session'},object_refs:[{type:'note',id:'saved-note <&>',version:3},{type:'note',id:'ignored',version:0},{type:'task',id:'not-supported',version:1}]}); item.revision++;
  await page.locator('#refresh').click(); await expect(page.locator('[data-event-id="receipt-1"]')).toContainText('来源标识：run-123');
  const receiptCard = page.locator('[data-event-id="receipt-1"]');
  check(await receiptCard.getByRole('button',{name:'更正自述',exact:true}).count() === 0,'Tool receipt displays provenance and remains uneditable');
  await receiptCard.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  await expect(receiptCard.locator('.event-history')).toContainText('依据：工具回执 · 只读');
  await expect(receiptCard.locator('.event-note-link')).toHaveCount(1);
  const receiptHref = await receiptCard.locator('.event-note-link').getAttribute('href');
  const receiptUrl = new URL(receiptHref,'http://threads.test');
  check(receiptUrl.pathname === '/static/notes.html' && receiptUrl.searchParams.get('note_id') === 'saved-note <&>' && receiptUrl.searchParams.get('version') === '3','Linked note opens the fixed saved version with encoded ID');
  check(!(await receiptCard.textContent()).includes('private-receipt-session'),'History hides internal source session identifiers');
  item.events.push({id:'legacy-1',kind:'progress',text:'旧记录 <svg onload=alert(1)>',source:'user_stated',source_id:'proposal:expired'}); item.revision++;
  await page.locator('#refresh').click();
  const legacyCard = page.locator('[data-event-id="legacy-1"]');
  await legacyCard.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  await expect(legacyCard.locator('.event-history')).toContainText('旧记录，原始依据未知');
  check(await legacyCard.locator('svg,.event-request').count() === 0,'Legacy labels cannot imply evidence from an old source ID or execute markup');
  item.title = '另一窗口更新'; item.revision++;
  const before = requests.filter(r => r.method === 'PATCH').length;
  await page.locator('#title').fill('陈旧覆盖'); await page.getByRole('button',{name:'保存项目设置',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('未重试提交'); await expect(page.locator('#title')).toHaveValue('另一窗口更新');
  check(item.title === '另一窗口更新' && requests.filter(r => r.method === 'PATCH').length === before+1,'409 refreshes latest version without replaying mutation');
  check(await page.locator('#event-text').getAttribute('maxlength') === '4000' && await page.locator('#edit-text').getAttribute('maxlength') === '4000','Event create and correction share 4000-character limit');
  await expect(page.getByText('允许在相关对话中回顾',{exact:true})).toBeVisible();
  const eventCount = item.events.length, revisionBeforeProposal = item.revision;
  const utterance = '记个决定：保留文字入口 <img src=x onerror=alert(1)>';
  await page.locator('#proposal-utterance').fill(utterance); await page.getByRole('button',{name:'生成候选预览',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('已生成候选预览');
  const proposal = [...proposals.values()][0];
  const candidate = page.locator(`[data-proposal-id="${proposal.id}"]`);
  await expect(candidate.locator('.proposal-source')).toHaveText(`原话：${utterance}`);
  await expect(candidate.locator('.event-note-link')).toHaveText('打开此次关联的笔记版本 2');
  check(new URL(await candidate.locator('.event-note-link').getAttribute('href'),'http://threads.test').searchParams.get('version') === '2','Preview exposes the fixed note reference before confirmation');
  check(item.events.length === eventCount && item.revision === revisionBeforeProposal,'Propose creates a pending preview without writing events or thread revision');
  check(await candidate.locator('img').count() === 0,'Proposal source and preview render hostile markup as safe text');
  await page.locator('#refresh').click(); await expect(candidate).toBeVisible();
  check(requests.some(r => r.path === `/api/threads/${item.id}/proposals` && r.method === 'GET'),'Refresh lists pending proposals for the viewed thread');
  await candidate.locator('textarea').fill('保留文字入口和键盘操作');
  await expect(candidate.getByRole('button',{name:'确认保存',exact:true})).toBeDisabled();
  await candidate.getByRole('button',{name:'更新预览',exact:true}).click(); await expect(page.locator('#notice')).toContainText('候选预览已更新');
  check(proposal.version === 2 && proposal.source_text === utterance && item.events.length === eventCount && item.revision === revisionBeforeProposal,'Revision increments proposal version, preserves source and does not save');
  const staleStatus = await page.evaluate(async id => (await fetch(`/api/thread-proposals/${id}/confirm`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_version:1})})).status, proposal.id);
  check(staleStatus === 409 && item.events.length === eventCount,'Old preview version cannot confirm');
  await expect(candidate.getByRole('button',{name:'确认保存',exact:true})).toBeEnabled();
  item.revision++; // Another window changes the thread between preview and confirmation.
  const confirmsBefore = requests.filter(r => r.path.endsWith('/confirm')).length;
  await candidate.getByRole('button',{name:'确认保存',exact:true}).click(); await expect(page.locator('#notice')).toContainText('未重试提交');
  await expect(candidate).toBeVisible(); await expect(candidate.getByRole('button',{name:'确认保存',exact:true})).toBeDisabled();
  check(requests.filter(r => r.path.endsWith('/confirm')).length === confirmsBefore + 1 && item.events.length === eventCount,'Revision conflict retains candidate and never blindly retries confirmation');
  await candidate.getByRole('button',{name:'更新预览',exact:true}).click(); await expect(page.locator('#notice')).toContainText('候选预览已更新');
  check(proposal.version === 3 && proposal.expected_revision === item.revision,'Explicit re-preview with unchanged text rebinds current revision');
  await candidate.getByRole('button',{name:'确认保存',exact:true}).click(); await expect(page.locator('#notice')).toContainText('候选已确认保存');
  check(item.events.length === eventCount + 1 && item.events.at(-1).text === '保留文字入口和键盘操作' && item.events.at(-1).source === 'user_stated','Confirmation writes exactly the revised preview as self-report');
  await expect(candidate).toHaveCount(0);
  const confirmedCard = page.locator(`[data-event-id="${item.events.at(-1).id}"]`);
  await confirmedCard.getByRole('button',{name:'查看依据与修订',exact:true}).click();
  await expect(confirmedCard.locator('.event-request')).toHaveText(`确认时的请求原文（保存内容可能经改写）：${utterance}`);
  await expect(confirmedCard.locator('.event-history')).toContainText('用户确认的记录请求 · 未经事实核验');
  await expect(confirmedCard.locator('.event-note-link')).toHaveText('打开当时关联的笔记版本 2');
  check(await confirmedCard.locator('img').count() === 0 && !(await confirmedCard.textContent()).includes('private-session-origin'),'Confirmed original requests stay literal and hide session identifiers');
  await page.locator('#proposal-utterance').fill('记个决定：不保存这一条'); await page.getByRole('button',{name:'生成候选预览',exact:true}).click(); await expect(page.locator('#notice')).toContainText('已生成候选预览');
  const rejected = [...proposals.values()].at(-1), beforeRejectRevision = item.revision;
  const rejectedCard = page.locator(`[data-proposal-id="${rejected.id}"]`);
  // Simulate a second window updating the proposal; local draft survives the conflict reload.
  await rejectedCard.locator('textarea').fill('本地草稿'); rejected.text = '另一窗口预览'; rejected.version++;
  await rejectedCard.getByRole('button',{name:'更新预览',exact:true}).click(); await expect(page.locator('#notice')).toContainText('未重试提交');
  await expect(rejectedCard.locator('textarea')).toHaveValue('本地草稿'); await expect(rejectedCard.locator('.proposal-preview')).toContainText('另一窗口预览');
  await expect(rejectedCard.getByRole('button',{name:'确认保存',exact:true})).toBeDisabled();
  await rejectedCard.getByRole('button',{name:'拒绝',exact:true}).click(); await expect(page.locator('#notice')).toContainText('候选已拒绝');
  check(rejected.status === 'rejected' && item.revision === beforeRejectRevision && item.events.length === eventCount + 1,'Reject removes candidate without a thread or event write');
  await expect(rejectedCard).toHaveCount(0);
  await page.locator('#clear-selection').click(); await expect(page.locator('#selection-label')).toContainText('暂不关联');
  check(selected === null,'Explicit null selection clears association');
  await page.locator('#select-thread').click(); await expect(page.locator('#selection-label')).toContainText('另一窗口更新');
  await page.locator('#delete-thread').click(); await expect(page.locator('#notice')).toContainText('陪伴项目记录已删除');
  check(threads.size === 0 && selected === null,'Confirmed delete clears record and selection');
  await expect(page.locator('#detail')).toBeHidden(); await expect(page.locator('#selection-label')).toContainText('暂不关联');
  check(!requests.some(r => /\/api\/(tasks|runs|chat)/.test(r.path)),'Delete never touches source task, run or chat endpoints');
  await page.locator('#new-title').fill('外部删除测试'); await page.getByRole('button',{name:'新建',exact:true}).click(); await expect(page.locator('#notice')).toContainText('项目已创建');
  await page.locator('#select-thread').click(); await expect(page.locator('#selection-label')).toContainText('外部删除测试');
  threads.clear(); // Deliberately leave a dangling selection to exercise defensive repair.
  await page.locator('#refresh').click(); await expect(page.locator('#selection-label')).toContainText('暂不关联'); await expect(page.locator('#detail')).toBeHidden();
  check(selected === null,'Externally deleted selection is safely cleared');
  const deepOriginal = {id:'deep-original',title:'原事项资料审阅',status:'discussing',next_step:'',allow_callbacks:false,revision:1,events:[],materials:[]};
  const deepSelected = {id:'deep-selected',title:'之后聊天的另一个事项',status:'discussing',next_step:'',allow_callbacks:false,revision:1,events:[],materials:[]};
  threads.set(deepOriginal.id,deepOriginal); threads.set(deepSelected.id,deepSelected); selected = deepSelected.id;
  const selectionWritesBeforeDeepLink = requests.filter(request => request.path === '/api/threads/selection' && request.method === 'POST').length;
  await page.goto(`http://threads.test/static/threads.html?${new URLSearchParams({thread_id:deepOriginal.id})}`);
  await expect(page.locator('#notice')).toContainText('已打开链接指定的原事项');
  await expect(page.locator('#detail-heading')).toHaveText(deepOriginal.title);
  await expect(page.locator('#selection-label')).toContainText(deepSelected.title);
  check(selected === deepSelected.id && requests.filter(request => request.path === '/api/threads/selection' && request.method === 'POST').length === selectionWritesBeforeDeepLink,'A matter deep link opens the original detail without changing the different global chat selection');
  threads.delete(deepOriginal.id);
  await page.goto(`http://threads.test/static/threads.html?${new URLSearchParams({thread_id:deepOriginal.id})}`);
  await expect(page.locator('#notice')).toContainText('原事项不存在或已删除'); await expect(page.locator('#detail')).toBeHidden();
  check(selected === deepSelected.id && requests.filter(request => request.path === '/api/threads/selection' && request.method === 'POST').length === selectionWritesBeforeDeepLink,'A missing deep-link target never falls back to the currently selected matter or writes selection');
  threads.delete(deepSelected.id);
  await page.goto(`http://threads.test/static/threads.html?${new URLSearchParams({thread_id:deepOriginal.id})}`);
  await expect(page.locator('#notice')).toContainText('原事项不存在或已删除');
  check(selected === deepSelected.id && requests.filter(request => request.path === '/api/threads/selection' && request.method === 'POST').length === selectionWritesBeforeDeepLink,'Read-only deep-link navigation also leaves an externally deleted global selection untouched');
  await page.setViewportSize({width:390,height:844});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'Mobile viewport has no horizontal overflow');
  check(errors.length === 0,'No browser runtime errors');
  console.log(JSON.stringify({result:'PASS',scope:'Offline Playwright, synthetic API; no server or production writes',checks,errors},null,2));
} finally { await browser.close(); }
