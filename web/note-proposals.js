/* A proposed note revision is never a saved note or a new chat association. */
import { appendContextUsage } from './context-usage.js';
const cards = new Map();
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
const path = id => `/api/note-proposals/${encodeURIComponent(id)}`;
const noteUrl = (id, version) => `/static/notes.html?${new URLSearchParams({ note_id: id, version: String(version) })}`;
async function api(url, method = 'GET', body) {
  const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) { const error = Error(typeof result.detail === 'string' ? result.detail : `请求未完成（${response.status}）`); error.status = response.status; throw error; }
  return result;
}
function button(text, action) { const result = node('button', text); result.type = 'button'; result.addEventListener('click', action); return result; }
function announce(note, selection) {
  const detail = { note_id: note.id, version: note.version, selection };
  for (const type of ['aqi-notes-updated', 'activity-updated', 'companion-updated']) window.dispatchEvent(new CustomEvent(type, { detail }));
  // The existing notes iframe owns its unsaved editor. Never navigate or reload it.
}
function expiry(candidate) {
  if (!candidate.expires_at) return Infinity;
  return typeof candidate.expires_at === 'number' ? candidate.expires_at * 1000 : Date.parse(candidate.expires_at);
}
function dirty(state) { return state.title.value !== state.candidate.title || state.body.value !== state.candidate.body; }
function controls(state) {
  const unavailable = state.unavailable || state.candidate.status !== 'pending' || expiry(state.candidate) <= Date.now();
  state.update.disabled = state.busy || state.resolved || unavailable || state.sourceConflict || state.baseConflict || !state.title.value.trim() || !state.body.value.trim();
  state.confirm.disabled = state.busy || state.resolved || unavailable || state.sourceConflict || state.baseConflict || state.stale || dirty(state);
  state.reject.disabled = state.busy || state.resolved || unavailable;
  state.read.disabled = state.busy || state.resolved;
  state.title.readOnly = Boolean(state.busy && state.lockEditing);
  state.body.readOnly = Boolean(state.busy && state.lockEditing);
  if (state.busy || state.resolved) return;
  if (unavailable) state.feedback.textContent = '候选已过期、删除或处理。标题和正文草稿仍保留，请重新提出修改请求；没有自动重试保存。';
  else if (state.sourceConflict) state.feedback.textContent = '原事项当前稿的关联已移除、替换或改变用途。候选草稿已保留；请核对原事项资料后重新提出修改，也可以不保存。';
  else if (state.baseConflict) state.feedback.textContent = '原稿已有新版，当前候选仍基于原保存版本。草稿已保留；请打开并明确关联当前版后重新提出修改。没有自动重试保存。';
  else if (state.stale) state.feedback.textContent = '笔记或候选版本已变化。草稿仍保留；请点击更新预览，核对差异后再确认，没有自动重试保存。';
  else if (state.errorMessage) state.feedback.textContent = state.errorMessage;
  else if (dirty(state)) state.feedback.textContent = '草稿已修改。请先更新预览，再核对差异并确认保存。';
}
function lineChanges(before, after) {
  const a = before.split(/\r?\n/), b = after.split(/\r?\n/);
  const changes = [];
  if (a.length * b.length > 90000) {
    // Bound memory for long notes. The changed middle is a valid replacement,
    // with the identical prefix/suffix retained, even when it isn't minimal.
    let start = 0, endA = a.length, endB = b.length;
    while (start < endA && start < endB && a[start] === b[start]) { changes.push(['same', a[start]]); start++; }
    while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
    for (let i = start; i < endA; i++) changes.push(['remove', a[i]]);
    for (let i = start; i < endB; i++) changes.push(['add', b[i]]);
    for (let i = endA; i < a.length; i++) changes.push(['same', a[i]]);
    return changes;
  }
  const table = Array.from({ length: a.length + 1 }, () => new Uint16Array(b.length + 1));
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  let i = 0, j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { changes.push(['same', a[i]]); i++; j++; }
    else if (i < a.length && (j === b.length || table[i + 1][j] >= table[i][j + 1])) changes.push(['remove', a[i++]]);
    else changes.push(['add', b[j++]]);
  }
  return changes;
}
function renderDiff(state) {
  const c = state.candidate, changes = lineChanges(c.original_body, c.body);
  const additions = changes.filter(([kind]) => kind === 'add').length, removals = changes.filter(([kind]) => kind === 'remove').length;
  const titleChanged = c.title !== c.original_title;
  state.diffSummary.textContent = `查看差异 · ${titleChanged ? '标题已改 · ' : ''}正文新增 ${additions} 行、移除 ${removals} 行`;
  state.diffContent.replaceChildren();
  if (titleChanged) for (const [label, text, kind] of [['原标题', c.original_title, 'remove'], ['新标题', c.title, 'add']]) {
    const row = node('p', undefined, `note-diff-${kind}`); row.append(node('span', label), node('span', text)); state.diffContent.append(row);
  }
  if (!additions && !removals) state.diffContent.append(node('p', '正文没有变化。'));
  else {
    let skipped = 0;
    for (let i = 0; i < changes.length; i++) {
      const [kind, text] = changes[i];
      const nearChange = changes.slice(Math.max(0, i - 2), i + 3).some(([type]) => type !== 'same');
      if (kind === 'same' && !nearChange) { skipped++; continue; }
      if (skipped) { state.diffContent.append(node('p', `… ${skipped} 行未改动`, 'note-diff-gap')); skipped = 0; }
      const row = node('p', undefined, `note-diff-${kind}`);
      row.append(node('span', kind === 'add' ? '+ 新增' : kind === 'remove' ? '− 移除' : '  保留'), node('span', text || '（空行）'));
      state.diffContent.append(row);
    }
    if (skipped) state.diffContent.append(node('p', `… ${skipped} 行未改动`, 'note-diff-gap'));
  }
}
function updatePreview(state, candidate) {
  state.candidate = candidate;
  state.meta.textContent = `基于保存版本 ${candidate.base_note_version} · 候选预览版本 ${candidate.version} · 尚未保存`;
  const context = candidate.source_context;
  state.scope.textContent = context?.scope === 'thread'
    ? `来自原事项 ${context.thread_id} 的当前稿；切换事项不会改变本候选归属。`
    : '来自个人笔记关联；本候选不会自动归入当前事项。';
  state.source.textContent = candidate.source_text;
  state.oldTitle.textContent = candidate.original_title; state.oldBody.textContent = candidate.original_body;
  state.newTitle.textContent = candidate.title; state.newBody.textContent = candidate.body;
  state.originalLink.href = noteUrl(candidate.note_id, candidate.base_note_version);
  state.originalLink.textContent = `查看原保存版本 ${candidate.base_note_version}`;
  renderDiff(state);
  if (state.timer) clearTimeout(state.timer);
  const remaining = expiry(candidate) - Date.now();
  if (remaining > 0 && remaining < 2147483647) state.timer = setTimeout(() => controls(state), remaining + 10);
  controls(state);
}
async function readPreview(state) {
  try {
    const [current, note] = await Promise.all([api(path(state.candidate.id)), api(`/api/notes/${encodeURIComponent(state.candidate.note_id)}`)]);
    updatePreview(state, current);
    if (note?.id !== current.note_id || !Number.isInteger(note.version)) throw Error('无法确认当前笔记版本，请核对笔记页。');
    state.baseConflict = note.version !== current.base_note_version;
    state.currentLink.hidden = !state.baseConflict;
    state.currentLink.href = noteUrl(note.id, note.version);
    state.currentLink.textContent = `打开当前保存版本 ${note.version}，核对后明确关联`;
  } catch (error) { if (error.status === 404) state.unavailable = true;
    if (error.message === 'draft_source_changed') state.sourceConflict = true; throw error; }
}
async function associateSaved(state, note, associateButton) {
  if (state.associating) return;
  state.associating = true; associateButton.disabled = true;
  try {
    const selection = await api('/api/notes/selection');
    if (!Number.isInteger(selection.revision)) throw Error('无法确认当前关联版本，请重新核对后再关联。');
    const result = await api('/api/notes/selection', 'POST', { note_id: note.id, version: note.version, expected_revision: selection.revision });
    state.feedback.textContent = `已明确关联保存版本 ${result.selected_version}。原笔记编辑器及其未保存输入保持原样。`;
    associateButton.textContent = '此保存版本已关联';
    state.associated = true; announce(note, result);
  } catch (error) {
    state.feedback.textContent = error.status === 409 ? '对话的笔记关联已在其他窗口变更。没有自动重试，原编辑器保持原样；请核对后再点击关联。'
      : `未关联：${error.message}。已保存版本仍可打开；没有自动重试，原编辑器保持原样。`;
  } finally { state.associating = false; associateButton.disabled = Boolean(state.associated); }
}
function saved(state, note) {
  state.resolved = true;
  if (state.timer) clearTimeout(state.timer);
  state.edit.remove(); state.actions.remove();
  state.meta.textContent = `已保存为笔记版本 ${note.version}`;
  const inThread = state.candidate.source_context?.scope === 'thread';
  state.feedback.textContent = `保存版本 ${note.version} 已写入。${inThread?'原事项仍关联原保存版本，请在原事项资料中明确更新；此次保存不会自动添加成果或标记完成。':'尚未自动关联之后的对话；原笔记编辑器保持原样。'}`;
  for (const warning of note.companion_warnings || []) {
    if (typeof warning !== 'string') continue;
    state.card.append(node('p', `笔记已保存，事项回执同步遇到问题：${warning}。请核对事项；不要重新保存这份修改。`, 'note-proposal-warning'));
  }
  const actions = node('div', undefined, 'note-proposal-actions');
  const link = node('a', `打开保存版本 ${note.version}`); link.href = noteUrl(note.id, note.version); link.target = '_blank'; link.rel = 'noopener';
  actions.append(link);
  if (inThread) { const materials = node('a', '核对原事项资料'); materials.href = `/static/threads.html?${new URLSearchParams({thread_id:state.candidate.source_context.thread_id})}`; actions.append(materials); }
  else { const associateButton = button('关联此保存版本', () => associateSaved(state, note, associateButton)); actions.append(associateButton); }
  state.card.append(actions);
  cards.delete(state.candidate.id); announce(note);
}
async function act(state, action) {
  if (state.busy || state.resolved) return;
  controls(state);
  const control = action === 'update' ? state.update : action === 'confirm' ? state.confirm : action === 'read' ? state.read : state.reject;
  if (control.disabled) return;
  state.busy = true; state.lockEditing = ['confirm', 'reject'].includes(action); state.errorMessage = ''; controls(state);
  try {
    if (action === 'read') {
      await readPreview(state); state.stale = true;
      state.feedback.textContent = '已读取服务器预览，草稿保持原样。请点击更新预览后核对并确认。';
    } else if (action === 'update') {
      const submitted = { title: state.title.value, body: state.body.value };
      const candidate = await api(path(state.candidate.id), 'PATCH', { expected_version: state.candidate.version, ...submitted });
      updatePreview(state, candidate); state.stale = false;
      if (state.title.value === submitted.title) state.title.value = candidate.title;
      if (state.body.value === submitted.body) state.body.value = candidate.body;
      state.feedback.textContent = '预览已更新，尚未保存。请核对原稿、新稿和差异，再确认保存。';
    } else if (action === 'confirm') {
      const note = await api(`${path(state.candidate.id)}/confirm`, 'POST', { expected_version: state.candidate.version });
      if (note?.id !== state.candidate.note_id || !Number.isInteger(note.version) || note.version < 1) throw Error('无法识别保存回执，请通过笔记页核对；不要重复确认。');
      saved(state, note);
    } else {
      await api(`${path(state.candidate.id)}/reject`, 'POST', { expected_version: state.candidate.version });
      state.resolved = true; if (state.timer) clearTimeout(state.timer);
      state.actions.remove(); state.feedback.textContent = '未保存这份修改。笔记、对话关联和原编辑器保持原样。';
      cards.delete(state.candidate.id);
    }
  } catch (error) {
    if (error.message === 'draft_source_changed') {
      state.sourceConflict = true;
      state.feedback.textContent = '原事项当前稿关联已变化。草稿保留，请核对原事项资料后重新提出修改。';
    } else if ([409, 404].includes(error.status)) {
      state.stale = true;
      try { await readPreview(state); } catch { if (error.status === 404) state.unavailable = true; }
      state.feedback.textContent = '版本已变化或候选不可用。草稿仍保留；请更新预览并重新核对，没有自动重试保存。';
    } else {
      if (action === 'confirm') state.stale = true;
      state.errorMessage = `未完成：${error.message}。草稿仍保留，没有自动重试保存。`;
      state.feedback.textContent = state.errorMessage;
    }
  } finally { state.busy = false; controls(state); }
}
export function showNoteProposalResult(result) {
  const candidate = result?.note_proposal, chat = document.querySelector('#chat');
  if (!candidate?.id || !chat) return false;
  const existing = cards.get(candidate.id);
  if (existing) {
    if (candidate.version > existing.candidate.version) { existing.stale = true; updatePreview(existing, candidate); }
    appendContextUsage(existing.card, result.context_usage);
    return true;
  }
  const card = node('article', undefined, 'msg bot note-proposal'); card.dataset.noteProposalId = candidate.id; card.setAttribute('aria-label', '笔记修改候选');
  const meta = node('p', undefined, 'note-proposal-meta');
  const scope = node('p', undefined, 'note-proposal-scope');
  const sourceDetails = node('details', undefined, 'note-proposal-source'); sourceDetails.append(node('summary', '查看修改请求'));
  const source = node('p'); sourceDetails.append(source);
  const originals = node('details', undefined, 'note-proposal-compare'); originals.open = Math.max(candidate.original_body.length, candidate.body.length) < 700;
  originals.append(node('summary', '对照原稿与新稿'));
  const columns = node('div', undefined, 'note-proposal-columns');
  const oldTitle = node('h4'), newTitle = node('h4'), oldBody = node('pre'), newBody = node('pre');
  for (const [label, title, body] of [['原稿', oldTitle, oldBody], ['候选新稿', newTitle, newBody]]) { const column = node('section'); column.append(node('small', label), title, body); columns.append(column); }
  originals.append(columns);
  const diff = node('details', undefined, 'note-proposal-diff'), diffSummary = node('summary'), diffContent = node('div', undefined, 'note-diff-lines'); diff.append(diffSummary, diffContent);
  const originalLink = node('a'); originalLink.target = '_blank'; originalLink.rel = 'noopener';
  const currentLink = node('a', undefined, 'note-proposal-current'); currentLink.target = '_blank'; currentLink.rel = 'noopener'; currentLink.hidden = true;
  const edit = node('details', undefined, 'note-proposal-edit'); edit.open = candidate.body.length < 700; edit.append(node('summary', '编辑候选标题与正文'));
  const titleLabel = node('label', '候选标题'), title = node('input'); title.maxLength = 160; title.value = candidate.title; titleLabel.append(title);
  const bodyLabel = node('label', '候选正文'), body = node('textarea'); body.maxLength = 20000; body.rows = 4; body.value = candidate.body; bodyLabel.append(body); edit.append(titleLabel, bodyLabel);
  const actions = node('div', undefined, 'note-proposal-actions'), feedback = node('p', '这是修改预览，尚未写入笔记。', 'note-proposal-feedback'); feedback.setAttribute('role', 'status');
  const state = { candidate, card, meta, scope, source, oldTitle, oldBody, newTitle, newBody, diffSummary, diffContent, originalLink, currentLink, edit, title, body, actions, feedback,
    busy: false, stale: false, unavailable: false, resolved: false };
  state.update = button('更新预览', () => act(state, 'update')); state.confirm = button('确认保存新版本', () => act(state, 'confirm'));
  state.reject = button('不保存', () => act(state, 'reject')); state.read = button('读取最新预览', () => act(state, 'read'));
  actions.append(state.update, state.confirm, state.reject, state.read);
  card.append(node('h3', '笔记修改 · 候选预览'), meta, scope, sourceDetails, originals, diff, originalLink, edit, actions, feedback, currentLink);
  appendContextUsage(card, result.context_usage);
  chat.append(card); cards.set(candidate.id, state);
  const edited = () => { state.errorMessage = ''; controls(state); };
  title.addEventListener('input', edited); body.addEventListener('input', edited);
  updatePreview(state, candidate); return true;
}
