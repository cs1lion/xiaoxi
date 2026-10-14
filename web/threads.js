const $ = id => document.getElementById(id);
const statuses = {discussing:'讨论中',waiting:'等待中',active:'推进中',done:'已完成',paused:'已暂停'};
const kinds = {decision:'决定',question:'疑问',progress:'进展'};
const materialPurposes = {reference:'参考资料',draft:'当前稿',artifact:'成果'};
let items = [], current = null, selection = null, editing = null, busy = false, dirty = false, proposals = [];
let materials = [], materialsThread = null, noteResults = [], materialDraft = null;
const materialEditorDrafts = new Map();
const proposalDrafts = new Map();
const proposalPath = id => `/api/thread-proposals/${encodeURIComponent(id)}`;
const notify = text => { $('notice').textContent = text; };
const threadPath = id => `/api/threads/${encodeURIComponent(id)}`;
async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers:{'Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) { const error = new Error(typeof data.detail === 'string' ? data.detail : `请求未完成（${response.status}）`); error.status = response.status; throw error; }
  return data;
}
function updateControls() {
  document.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = busy; });
  for (const proposal of proposals) {
    const card = [...$('proposals').children].find(element => element.dataset.proposalId === proposal.id);
    if (!card) continue;
    const draft = card.querySelector('textarea');
    card.querySelector('[data-action="confirm"]').disabled = busy || draft.value !== proposal.text || proposal.expected_revision !== current?.revision;
    card.querySelector('[data-action="preview"]').disabled = busy || !draft.value.trim();
  }
  $('clear-selection').disabled = busy || !selection;
  $('select-thread').disabled = busy || !current || selection === current.id;
  $('select-thread').textContent = current && selection === current.id ? '已关联之后的对话' : '关联之后的对话';
  $('link-material').disabled = busy || !current || materialDraft?.thread_id !== current.id || !Number.isSafeInteger(materialDraft?.version) || materialDraft.version < 1;
  for (const material of materials) {
    const card = [...$('materials').children].find(element => element.dataset.materialId === material.id);
    if (card) card.querySelector('[data-material-action="edit"]').disabled = busy || material.status === 'missing';
  }
}
async function run(action) {
  if (busy) return;
  busy = true; updateControls();
  try { await action(); }
  catch (error) {
    if (error.status === 409 || error.status === 404) {
      try { await refresh(); notify(error.status === 409 ? '内容已在其他窗口修改。已刷新最新版本，未重试提交；请核对后重新填写并提交。资料选择和候选仍保留；请明确点击更新预览以绑定最新项目版本，再确认保存。' : '项目或资料可能已被删除，已刷新列表和对话关联。'); }
      catch { current = null; selection = null; renderDetail(); renderSelection(); notify('刷新失败，已停用旧详情。请点击刷新后再操作；没有自动重试提交。'); }
    } else notify(`未完成：${error.message}。可刷新检查当前状态；不会自动重试。`);
  } finally { busy = false; updateControls(); }
}
function node(tag, text, className) { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; }
function evidenceLabel(record) {
  const state = record.evidence?.state || (record.source === 'tool_verified' ? 'tool_verified' : 'legacy');
  return {user_confirmed:'依据：用户确认的记录请求 · 未经事实核验', manual:'依据：手动提交或更正 · 未经事实核验', legacy:'依据：旧记录，原始依据未知', tool_verified:'依据：工具回执 · 只读'}[state] || '依据：原始依据未知';
}
function displayTime(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString('zh-CN');
}
function appendObjectRefs(container, refs, label = '打开当时关联的笔记版本') {
  let count = 0;
  for (const ref of Array.isArray(refs) ? refs : []) {
    if (ref?.type !== 'note' || typeof ref.id !== 'string' || !ref.id.trim() || ref.id.length > 256 || ref.id.includes('\0') || !Number.isSafeInteger(ref.version) || ref.version < 1) continue;
    const link = node('a', `${label} ${ref.version}`, 'event-note-link');
    link.href = `/static/notes.html?${new URLSearchParams({note_id:ref.id, version:String(ref.version)})}`;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; container.append(link); count++;
  }
  if (count) container.append(node('small', '关联资料不表示该笔记支持记录结论。'));
}
function versionOptions(select, versions, selected) {
  select.replaceChildren();
  const choices = (Array.isArray(versions) ? versions : []).filter(item => Number.isSafeInteger(item?.version) && item.version > 0);
  if (!choices.some(item => item.version === selected) && Number.isSafeInteger(selected) && selected > 0) choices.push({version:selected});
  for (const choice of choices) {
    const option = node('option', `版本 ${choice.version}${typeof choice.title === 'string' ? ` · ${choice.title}` : ''}`); option.value = String(choice.version); select.append(option);
  }
  select.value = String(selected);
}
function renderMaterialSearch() {
  $('material-search-results').replaceChildren();
  for (const note of noteResults) {
    const button = node('button', undefined, 'material-result quiet'); button.type = 'button'; button.dataset.noteId = note.id;
    button.append(node('strong', note.title), node('small', `目前保存版本 ${note.version}`));
    button.addEventListener('click', () => {
      if (materialDraft && !window.confirm('有待关联的资料选择，确定改选其他笔记吗？')) return;
      run(async () => {
        const targetId = current.id;
        const history = await api(`/api/notes/${encodeURIComponent(note.id)}/history`);
        materialDraft = {thread_id:targetId,note_id:note.id,title:note.title,version:note.version,purpose:'reference',versions:history.items || []};
        renderMaterialDraft(); notify('已选择笔记保存版本。核对用途与版本后，点击关联到本事项才会保存。');
      });
    });
    $('material-search-results').append(button);
  }
}
function renderMaterialDraft() {
  const visible = !!current && materialDraft?.thread_id === current.id;
  $('material-link-form').hidden = !visible; $('material-preview').replaceChildren();
  if (!visible) return;
  $('material-note-title').textContent = `将关联到「${current.title}」：${materialDraft.title}`;
  versionOptions($('material-version'), materialDraft.versions, materialDraft.version);
  $('material-purpose').value = materialDraft.purpose;
  appendObjectRefs($('material-preview'), [{type:'note',id:materialDraft.note_id,version:materialDraft.version}], '打开将关联的笔记版本');
}
function renderMaterials() {
  if (!current) { materials = []; noteResults = []; materialDraft = null; materialEditorDrafts.clear(); }
  $('materials').replaceChildren(); renderMaterialSearch(); renderMaterialDraft();
  if (!current) return;
  for (const material of materials) {
    const card = node('article', undefined, 'material-item'); card.dataset.materialId = material.id;
    card.append(node('strong', material.title || '笔记（保存版不可用）'), node('small', `${materialPurposes[material.purpose] || '用途未知'} · 固定版本 ${material.version}`));
    if (material.status === 'missing') card.append(node('p', '关联的笔记或保存版本已不可用。仍可解除关联。', 'hint'));
    else {
      if (material.status === 'stale') card.append(node('p', `笔记已有版本 ${material.current_version}；本事项仍使用指定的版本 ${material.version}。`, 'hint'));
      appendObjectRefs(card, [{type:'note',id:material.note_id,version:material.version}], '打开本事项关联的笔记版本');
    }
    const actions = node('div', undefined, 'actions');
    const edit = node('button', '修改版本或用途', 'quiet'); edit.type = 'button'; edit.dataset.materialAction = 'edit';
    edit.addEventListener('click', () => run(async () => {
      if (materialEditorDrafts.has(material.id)) { materialEditorDrafts.delete(material.id); renderMaterials(); return; }
      const history = await api(`/api/notes/${encodeURIComponent(material.note_id)}/history`);
      materialEditorDrafts.set(material.id, {version:material.version,purpose:material.purpose,versions:history.items || []}); renderMaterials();
    }));
    const remove = node('button', '解除关联', 'quiet'); remove.type = 'button'; remove.dataset.materialAction = 'remove';
    remove.addEventListener('click', () => run(async () => {
      await api(`${threadPath(current.id)}/materials/${encodeURIComponent(material.id)}/remove`, 'POST', {expected_revision:current.revision});
      materialEditorDrafts.delete(material.id); await refresh(); notify('已解除本事项的资料关联；笔记及保存版本未删除。');
    }));
    actions.append(edit, remove); card.append(actions);
    const draft = materialEditorDrafts.get(material.id);
    if (draft) {
      const form = node('form', undefined, 'material-edit-form');
      const versionLabel = node('label', '固定保存版本'), version = node('select'); version.dataset.field = 'version'; versionOptions(version, draft.versions, draft.version); versionLabel.append(version);
      const purposeLabel = node('label', '在本事项中的用途'), purpose = node('select'); purpose.dataset.field = 'purpose';
      for (const [value, label] of Object.entries(materialPurposes)) { const option = node('option', label); option.value = value; purpose.append(option); } purpose.value = draft.purpose; purposeLabel.append(purpose);
      version.addEventListener('change', () => { draft.version = Number(version.value); }); purpose.addEventListener('change', () => { draft.purpose = purpose.value; });
      const save = node('button', '保存资料关联修改'); save.type = 'submit';
      form.append(versionLabel, purposeLabel, save);
      form.addEventListener('submit', event => { event.preventDefault(); run(async () => {
        await api(`${threadPath(current.id)}/materials/${encodeURIComponent(material.id)}`, 'PATCH', {expected_revision:current.revision,version:draft.version,purpose:draft.purpose});
        materialEditorDrafts.delete(material.id); await refresh(); notify('本事项的资料版本与用途已明确更新。');
      }); });
      card.append(form);
    }
    $('materials').append(card);
  }
  if (!materials.length) $('materials').append(node('p', '本事项还没有关联资料。可查找已有笔记，再明确指定版本与用途。', 'hint'));
}
async function loadMaterials() {
  const targetId = current?.id || null;
  if (materialsThread !== targetId) { materialDraft = null; materialEditorDrafts.clear(); noteResults = []; $('material-query').value = ''; }
  materialsThread = targetId;
  if (!targetId) { materials = []; return; }
  const result = await api(`${threadPath(targetId)}/materials`);
  if (result.thread_id !== targetId) throw Error('无法识别资料归属，请刷新后重试');
  materials = (Array.isArray(result.items) ? result.items : []).filter(item => item.thread_id === targetId);
  for (const id of materialEditorDrafts.keys()) if (!materials.some(item => item.id === id)) materialEditorDrafts.delete(id);
}
function renderEventHistory(container, history) {
  container.replaceChildren();
  for (const entry of Array.isArray(history) ? history : []) {
    if (!entry || typeof entry !== 'object') continue;
    const row = node('article', undefined, 'event-revision');
    const version = Number.isSafeInteger(entry.version) && entry.version > 0 ? entry.version : '未知';
    const previous = Number.isSafeInteger(entry.previous_version) && entry.previous_version > 0 ? ` · 更正自版本 ${entry.previous_version}` : ' · 首个保存版本';
    row.append(node('h4', `记录版本 ${version}${previous}`), node('p', typeof entry.text === 'string' ? entry.text : ''), node('small', evidenceLabel(entry)));
    if (entry.evidence?.state === 'user_confirmed' && typeof entry.evidence.request_text === 'string') row.append(node('p', `确认时的请求原文（保存内容可能经改写）：${entry.evidence.request_text}`, 'event-request'));
    const saved = displayTime(entry.created_at), captured = displayTime(entry.evidence?.captured_at);
    if (saved) row.append(node('small', `此版本保存于：${saved}`));
    if (captured && entry.evidence?.state === 'user_confirmed') row.append(node('small', `记录请求采集于：${captured}`));
    appendObjectRefs(row, entry.object_refs);
    container.append(row);
  }
  if (!container.children.length) container.append(node('p', '暂无可审阅的版本。', 'hint'));
}
function renderSelection() {
  const selected = items.find(item => item.id === selection);
  $('selection-label').textContent = selection ? `之后的对话关联：${selected?.title || '所选项目（未在列表中）'}` : '之后的对话：暂不关联项目';
}
function renderList() {
  $('thread-list').replaceChildren();
  for (const item of items) {
    const button = node('button', undefined, 'thread-item'); button.type = 'button'; button.dataset.threadId = item.id;
    button.setAttribute('aria-current', String(current?.id === item.id));
    button.append(node('strong', item.title), node('small', `${statuses[item.status] || item.status}${selection === item.id ? ' · 对话已关联' : ''}`));
    button.addEventListener('click', () => { if (!discard()) return; run(async () => { current = await api(threadPath(item.id)); await Promise.all([loadProposals(), loadMaterials()]); renderDetail(); renderList(); notify('已打开详情。打开项目不会改变对话关联。'); }); });
    $('thread-list').append(button);
  }
  if (!items.length) $('thread-list').append(node('p','还没有项目。先给想做的事起个名字吧。','hint'));
}
async function loadProposals() {
  proposals = current ? ((await api(`${threadPath(current.id)}/proposals`)).items || []).filter(proposal => proposal.status === 'pending' && proposal.thread_id === current.id) : [];
}
function renderProposals() {
  $('proposals').replaceChildren();
  for (const proposal of current ? proposals : []) {
    const card = node('article', undefined, 'proposal'); card.dataset.proposalId = proposal.id;
    card.append(node('h4', `${kinds[proposal.kind] || proposal.kind} · 候选版本 ${proposal.version}`));
    card.append(node('p', `原话：${proposal.source_text}`, 'proposal-source'));
    card.append(node('p', `服务器预览：${proposal.text}`, 'proposal-preview'));
    appendObjectRefs(card, proposal.object_refs, '打开此次关联的笔记版本');
    const expiry = new Date(typeof proposal.expires_at === 'number' ? proposal.expires_at * 1000 : proposal.expires_at);
    card.append(node('small', `有效期至：${Number.isNaN(expiry.getTime()) ? proposal.expires_at : expiry.toLocaleString('zh-CN')}`));
    if (proposal.expected_revision !== current.revision) card.append(node('p', '项目版本已变化，请更新预览后再确认保存。', 'hint'));
    const label = node('label', '候选草稿');
    const draft = node('textarea'); draft.rows = 3; draft.maxLength = 4000;
    draft.value = proposalDrafts.get(proposal.id) ?? proposal.text; label.append(draft); card.append(label);
    draft.addEventListener('input', () => { proposalDrafts.set(proposal.id, draft.value); updateControls(); });
    const actions = node('div', undefined, 'actions');
    for (const [action, text] of [['preview','更新预览'],['confirm','确认保存'],['reject','拒绝']]) {
      const button = node('button', text); button.type = 'button'; button.dataset.action = action;
      button.addEventListener('click', () => run(async () => {
        if (action === 'preview') {
          const updated = await api(proposalPath(proposal.id), 'PATCH', {expected_version:proposal.version, text:draft.value});
          proposals = proposals.map(item => item.id === updated.id ? updated : item); proposalDrafts.delete(proposal.id);
          renderProposals(); notify('候选预览已更新；尚未写入记录，请核对后确认保存。');
        } else {
          if (action === 'confirm' && (draft.value !== proposal.text || proposal.expected_revision !== current?.revision)) return;
          await api(`${proposalPath(proposal.id)}/${action}`, 'POST', {expected_version:proposal.version});
          proposalDrafts.delete(proposal.id); await refresh();
          notify(action === 'confirm' ? '候选已确认保存为用户自述记录（未经核验）。' : '候选已拒绝；没有写入记录。');
        }
      }));
      actions.append(button);
    }
    card.append(actions); $('proposals').append(card);
  }
  if (!proposals.length) $('proposals').append(node('p', '暂无待确认候选。', 'hint'));
}
function renderDetail() {
  dirty = false; editing = null; $('event-edit-form').hidden = true; $('event-text').value = ''; $('proposal-utterance').value = '';
  $('detail').hidden = !current; $('empty').hidden = !!current;
  renderProposals();
  renderMaterials();
  $('events').replaceChildren();
  if (!current) return;
  $('detail-heading').textContent = current.title; $('revision').textContent = `项目记录 · 版本 ${current.revision}`;
  $('title').value = current.title; $('thread-status').value = current.status; $('next-step').value = current.next_step || ''; $('allow-callbacks').checked = current.allow_callbacks === true;
  for (const event of current.events || []) {
    const verified = event.source === 'tool_verified';
    const editable = event.source === 'user_stated' && Object.hasOwn(kinds, event.kind);
    const card = node('article', undefined, `event${verified ? ' verified' : ''}`); card.dataset.eventId = event.id;
    card.append(node('span', kinds[event.kind] || '工具回执', 'event-tag'), node('p', event.text || ''));
    card.append(node('small', verified ? '来源：工具核验 · 只读回执' : event.source === 'user_stated' ? '来源：用户自述 · 未经核验' : `来源：${event.source || '未知'} · 只读`));
    if (event.source_id) card.append(node('small', `来源标识：${event.source_id}`));
    card.append(node('small', evidenceLabel(event)));
    const historyButton = node('button', '查看依据与修订', 'quiet'); historyButton.type = 'button'; historyButton.dataset.action = 'history'; historyButton.setAttribute('aria-expanded', 'false');
    const historyPanel = node('section', undefined, 'event-history'); historyPanel.hidden = true; historyPanel.setAttribute('aria-label', '记录依据与修订历史');
    historyButton.addEventListener('click', () => run(async () => {
      if (!historyPanel.hidden) { historyPanel.hidden = true; historyButton.setAttribute('aria-expanded', 'false'); historyButton.textContent = '查看依据与修订'; return; }
      const result = await api(`${threadPath(current.id)}/events/${encodeURIComponent(event.id)}/history`);
      renderEventHistory(historyPanel, result.items); historyPanel.hidden = false; historyButton.setAttribute('aria-expanded', 'true'); historyButton.textContent = '收起依据与修订';
    }));
    card.append(historyButton, historyPanel);
    if (editable) {
      const button = node('button','更正自述','quiet'); button.type = 'button';
      button.addEventListener('click', () => { if (!discard()) return; editing = event.id; $('edit-text').value = event.text; $('event-edit-form').hidden = false; $('edit-text').focus(); });
      card.append(button);
    }
    $('events').append(card);
  }
  if (!(current.events || []).length) $('events').append(node('p','暂无记录。只保留你明确提交的内容，以及有来源的工具回执。','hint'));
}
async function refresh({repairSelection = true} = {}) {
  const [list, selected] = await Promise.all([api('/api/threads'), api('/api/threads/selection')]);
  items = list.items || []; selection = selected.thread_id || null;
  // Validate the explicit global selection even if it is outside a paginated list.
  if (selection) {
    try { const selectedThread = await api(threadPath(selection)); if (!items.some(item => item.id === selection)) items.push(selectedThread); }
    catch (error) { if (error.status !== 404) throw error; if (repairSelection) selection = (await api('/api/threads/selection', 'POST', {thread_id:null})).thread_id || null; }
  }
  if (current) { try { current = await api(threadPath(current.id)); } catch (error) { if (error.status !== 404) throw error; current = null; } }
  await Promise.all([loadProposals(), loadMaterials()]);
  renderList(); renderSelection(); renderDetail();
}
function hasDrafts() { return proposals.some(proposal => proposalDrafts.has(proposal.id) && proposalDrafts.get(proposal.id) !== proposal.text); }
function discard() { return !(dirty || hasDrafts() || $('proposal-utterance').value || materialDraft || materialEditorDrafts.size) || window.confirm('有未保存的输入或资料选择，确定放弃这些输入吗？'); }
async function mutate(path, method, payload, message) {
  await api(path, method, {expected_revision:current.revision, ...payload});
  await refresh(); notify(message);
}
$('create-form').addEventListener('submit', event => {
  event.preventDefault(); if (!discard()) return;
  const title = $('new-title').value.trim(); if (!title) return notify('请填写项目名称。');
  run(async () => { current = await api('/api/threads','POST',{title}); $('new-title').value = ''; await refresh(); notify('项目已创建；尚未关联对话。'); });
});
$('refresh').addEventListener('click', () => { if (discard()) run(async () => { await refresh(); notify('已刷新最新项目和全局关联。'); }); });
$('select-thread').addEventListener('click', () => run(async () => {
  if (!current) return;
  const result = await api('/api/threads/selection','POST',{thread_id:current.id}); selection = result.thread_id || null; renderList(); renderSelection(); notify('已明确关联之后的对话与助手运行；没有授权任何行动。');
}));
$('clear-selection').addEventListener('click', () => run(async () => { await api('/api/threads/selection','POST',{thread_id:null}); selection = null; renderList(); renderSelection(); notify('已取消之后的对话关联。已有记录不受影响。'); }));
$('details-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current) return;
  const title = $('title').value.trim(); if (!title) return notify('请填写项目名称。');
  run(() => mutate(threadPath(current.id),'PATCH',{title,status:$('thread-status').value,next_step:$('next-step').value.trim(),allow_callbacks:$('allow-callbacks').checked},'项目设置已保存。'));
});
$('proposal-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current) return;
  const utterance = $('proposal-utterance').value.trim(); if (!utterance) return notify('请填写明确的记录请求。');
  run(async () => {
    const proposal = await api(`${threadPath(current.id)}/proposals`, 'POST', {utterance});
    proposals.push(proposal); $('proposal-utterance').value = ''; renderProposals();
    notify('已生成候选预览，尚未写入记录。请核对后确认保存。');
  });
});
$('material-search-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current) return;
  run(async () => {
    const result = await api(`/api/notes?${new URLSearchParams({query:$('material-query').value.trim()})}`);
    noteResults = (Array.isArray(result.items) ? result.items : []).filter(note => typeof note?.id === 'string' && Number.isSafeInteger(note.version) && note.version > 0);
    renderMaterialSearch(); notify(noteResults.length ? '已找到笔记。选中后核对固定版本与用途；尚未关联。' : '未找到匹配笔记，可换一个关键词。');
  });
});
$('material-version').addEventListener('change', () => { if (!materialDraft) return; materialDraft.version = Number($('material-version').value); renderMaterialDraft(); });
$('material-purpose').addEventListener('change', () => { if (materialDraft) materialDraft.purpose = $('material-purpose').value; });
$('cancel-material').addEventListener('click', () => { materialDraft = null; renderMaterialDraft(); updateControls(); notify('已取消资料选择，没有保存关联。'); });
$('material-link-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current || materialDraft?.thread_id !== current.id) return;
  run(async () => {
    const draft = {...materialDraft};
    await api(`${threadPath(draft.thread_id)}/materials`, 'POST', {expected_revision:current.revision,note_id:draft.note_id,version:draft.version,purpose:draft.purpose});
    materialDraft = null; await refresh(); notify('已将指定笔记保存版本关联到本事项；用途不表示工具核验或事项完成。');
  });
});
$('event-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current) return; const text = $('event-text').value.trim(); if (!text) return notify('请填写记录内容。');
  run(() => mutate(`${threadPath(current.id)}/events`,'POST',{kind:$('event-kind').value,text},'记录已添加，标记为用户自述（未经核验）。'));
});
$('event-edit-form').addEventListener('submit', event => {
  event.preventDefault(); if (!current || !editing) return; const text = $('edit-text').value.trim(); if (!text) return notify('请填写更正内容。');
  run(() => mutate(`${threadPath(current.id)}/events/${encodeURIComponent(editing)}`,'PATCH',{text},'用户自述已更正；这不是工具核验。'));
});
$('cancel-edit').addEventListener('click', () => { if (!discard()) return; editing = null; $('event-edit-form').hidden = true; dirty = false; });
$('delete-thread').addEventListener('click', () => {
  if (!current || !window.confirm('确定删除此陪伴项目记录？此操作不可撤销，只清除陪伴记录，不会删除来源任务、运行或聊天。若已关联，也会取消关联。')) return;
  run(async () => { await api(`${threadPath(current.id)}/delete`,'POST',{expected_revision:current.revision}); current = null; await refresh(); notify('陪伴项目记录已删除。来源任务、运行和聊天未被删除。'); });
});
for (const id of ['details-form','event-form','event-edit-form']) $(id).addEventListener('input', () => { dirty = true; });
window.addEventListener('beforeunload', event => { if (dirty || hasDrafts() || $('proposal-utterance').value || materialDraft || materialEditorDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
async function initialize() {
  const requestedId = new URLSearchParams(window.location.search).get('thread_id');
  await refresh({repairSelection:requestedId === null});
  if (requestedId === null) return;
  if (!requestedId.trim()) { notify('指定的事项链接缺少有效 ID。没有改变对话关联。'); return; }
  try {
    current = await api(threadPath(requestedId));
    await Promise.all([loadProposals(), loadMaterials()]);
    renderDetail(); renderList(); notify('已打开链接指定的原事项。没有改变之后的对话关联。');
  } catch (error) {
    current = null; materials = []; proposals = []; renderDetail(); renderList();
    if (error.status === 404) notify('链接指定的原事项不存在或已删除。没有打开其他事项，也没有改变对话关联。');
    else notify(`未能打开链接指定的原事项：${error.message}。没有改变对话关联，请刷新后重试。`);
  }
}
run(initialize);
