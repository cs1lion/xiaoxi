/* Shared work stays in chat. Previews and continuation never submit a chat turn. */
import { appendContextUsage } from './context-usage.js';
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
const threadPath = id => `/api/threads/${encodeURIComponent(id)}`;
const proposalPath = id => `/api/thread-proposals/${encodeURIComponent(id)}`;
const kinds = { decision: '决定', question: '疑问', progress: '进展' };
const operations = { add_event: '记下', revise_event: '更正', next_step: '更新下一步' };
const statuses = { discussing: '讨论中', waiting: '等待中', active: '推进中', done: '已完成', paused: '已暂停' };
const cards = new Map(), knownThreads = new Map();
let mount, summary, notice, manager, chooser, titleInput, createButton, selectButton, clearButton;
let selectedId = null, items = [], managementBusy = false, refreshSerial = 0;
async function api(path, method = 'GET', body) {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : `请求未完成（${response.status}）`);
    error.status = response.status; throw error;
  }
  return data;
}
function button(text, callback) {
  const element = node('button', text); element.type = 'button';
  element.addEventListener('click', callback); return element;
}
function changed(thread) {
  if (thread) knownThreads.set(thread.id, thread);
  window.dispatchEvent(new CustomEvent('companion-updated', { detail: { thread } }));
}
function latest(thread, predicate) { return [...(thread.events || [])].reverse().find(predicate); }
function detail(label, text, source) {
  const line = node('div', undefined, 'companion-fact');
  line.append(node('span', label), node('p', text || '尚未记录'));
  if (source) line.append(node('small', source));
  return line;
}
function receiptDisplay(receipt) {
  if (!receipt) return {};
  let value;
  try { value = JSON.parse(receipt.text); } catch { return { text: receipt.text }; }
  if (!value || typeof value !== 'object' || typeof value.tool !== 'string') return { text: receipt.text };
  const ref = value.object_ref;
  if (value.tool === 'note.update' && value.status === 'succeeded' && ref?.type === 'note' && typeof ref.id === 'string' && Number.isInteger(ref.version) && ref.version > 0) {
    return { text: `笔记已保存 · 版本 ${ref.version}`, href: `/static/notes.html?${new URLSearchParams({ note_id: ref.id, version: String(ref.version) })}` };
  }
  const tools = { 'task.create': '创建任务', 'task.update': '更新任务', 'note.update': '更新笔记' };
  const statuses = { succeeded: '已完成', failed: '未完成', cancelled: '已取消' };
  return { text: `${tools[value.tool] || value.tool} · ${statuses[value.status] || value.status || '结果待核对'}` };
}
function facts(thread) {
  const group = node('div', undefined, 'companion-facts');
  const decision = latest(thread, event => event.kind === 'decision');
  const receipt = latest(thread, event => event.source === 'tool_verified');
  const display = receiptDisplay(receipt);
  const receiptLine = detail('最近执行回执', display.text, receipt ? `工具核验${receipt.source_id ? ` · 来源 ${receipt.source_id}` : ''}` : undefined);
  if (display.href) { const link = node('a', '打开保存版本'); link.href = display.href; link.target = '_blank'; link.rel = 'noopener'; receiptLine.append(link); }
  group.append(detail('下一步', thread.next_step),
    detail('最近决定', decision?.text, decision ? '用户自述 · 未经核验' : undefined),
    receiptLine);
  return group;
}
function continueDraft(thread, feedback = notice) {
  const input = document.querySelector('#textInput');
  if (!input) return;
  if (thread.id !== selectedId) {
    if (feedback) feedback.textContent = '这份摘要属于另一件事。请先关联这件事，再准备继续；输入草稿保持原样。';
    return;
  }
  if (input.value.trim()) {
    if (feedback) feedback.textContent = '输入框已有草稿，已保留。你可以继续输入，再按发送。';
    return;
  }
  input.value = '继续这件事';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  if (feedback) feedback.textContent = '已放入输入框，可以改写；按发送后才会继续。';
}
function renderSummary() {
  if (!summary) return;
  const thread = knownThreads.get(selectedId);
  summary.replaceChildren();
  const header = node('div', undefined, 'companion-heading');
  const heading = node('div'); heading.append(node('small', '正在一起做'), node('h2', thread?.title || '把一件事，一起往下推进'));
  const headerActions = node('div', undefined, 'companion-actions');
  if (thread) headerActions.append(button('继续这件事', () => continueDraft(thread)));
  headerActions.append(button(thread ? '选其它' : '选择 / 新建', () => { manager.open = !manager.open; }));
  header.append(heading, headerActions);
  summary.append(header);
  if (thread) {
    summary.append(facts(thread));
    const actions = node('div', undefined, 'companion-actions');
    actions.append(node('small', `${statuses[thread.status] || thread.status} · 之后的对话已关联`));
    summary.append(actions);
  } else summary.append(node('p', '选择或新建共同事项，决定和下一步就能在对话里接上。', 'companion-hint'));
  clearButton.disabled = managementBusy || !selectedId;
  updateProposalControls();
}
function renderChooser() {
  if (!chooser) return;
  const previous = chooser.value;
  chooser.replaceChildren(node('option', '选择已有事项'));
  chooser.firstChild.value = '';
  for (const item of items) {
    const option = node('option', item.title); option.value = item.id; chooser.append(option);
  }
  chooser.value = items.some(item => item.id === previous) ? previous : selectedId || '';
  if (chooser.selectedIndex < 0) chooser.value = '';
}
function updateManagementControls() {
  for (const control of [chooser, titleInput, createButton, selectButton]) if (control) control.disabled = managementBusy;
  if (selectButton) selectButton.disabled = managementBusy || !chooser.value || chooser.value === selectedId;
  if (clearButton) clearButton.disabled = managementBusy || !selectedId;
}
async function manage(action, feedback = notice) {
  if (managementBusy) return;
  managementBusy = true; updateManagementControls();
  try { await action(); }
  catch (error) { if (feedback) feedback.textContent = `未完成：${error.message}。请检查后重试，没有自动重复提交。`; }
  finally { managementBusy = false; updateManagementControls(); }
}
async function associate(id) {
  const data = await api('/api/threads/selection', 'POST', { thread_id: id });
  selectedId = data.thread_id || null;
  await refresh();
  if (notice) notice.textContent = selectedId ? '已关联之后的对话。可以输入目标或继续讨论。' : '已取消之后的对话关联。';
}
async function refresh() {
  if (!mount) return;
  const serial = ++refreshSerial;
  const [list, selection] = await Promise.all([api('/api/threads'), api('/api/threads/selection')]);
  if (serial !== refreshSerial) return;
  items = list.items || [];
  for (const thread of items) knownThreads.set(thread.id, thread);
  selectedId = selection.thread_id || null;
  if (selectedId && !items.some(item => item.id === selectedId)) {
    try {
      const thread = await api(threadPath(selectedId));
      if (serial !== refreshSerial) return;
      knownThreads.set(thread.id, thread); items.push(thread);
    } catch (error) {
      if (serial !== refreshSerial) return;
      if (error.status !== 404) throw error;
      selectedId = null;
    }
  }
  renderChooser(); renderSummary(); updateManagementControls();
  if (selectedId) {
    const pending = await api(`${threadPath(selectedId)}/proposals`);
    if (serial !== refreshSerial) return;
    for (const proposal of pending.items || []) renderProposal(proposal);
  }
}
function expiryTime(proposal) {
  if (!proposal.expires_at) return Infinity;
  return typeof proposal.expires_at === 'number' ? proposal.expires_at * 1000 : Date.parse(proposal.expires_at);
}
function updateProposalControls() {
  for (const state of cards.values()) {
    const revision = knownThreads.get(state.proposal.thread_id)?.revision;
    const expired = state.unavailable || expiryTime(state.proposal) <= Date.now();
    const stale = state.stale || (revision !== undefined && revision !== state.proposal.expected_revision);
    const dirty = state.draft.value !== state.proposal.text;
    state.previewButton.disabled = state.busy || expired || !state.draft.value.trim();
    state.confirmButton.disabled = state.busy || expired || stale || dirty;
    state.rejectButton.disabled = state.busy || expired;
    state.draft.disabled = state.busy;
    if (expired && !state.resolved) state.message.textContent = '候选已过期或不可用。草稿仍保留，可复制后在对话里重新提出。';
    else if (stale && !state.busy && !state.resolved) state.message.textContent = state.conflict ? '版本已变化，已读取最新预览；草稿仍保留。请明确更新预览后再确认，没有自动重试。' : '事项或候选版本已变化。草稿仍保留；请点击更新预览，核对后再确认保存。';
    else if (dirty && !state.busy && !state.resolved) state.message.textContent = '草稿已修改，先更新预览，再确认保存。';
  }
}
function updateProposalView(state, proposal) {
  state.proposal = proposal;
  state.heading.textContent = `${operations[proposal.operation || 'add_event'] || '记下'}${proposal.operation === 'next_step' ? '' : ` · ${kinds[proposal.kind] || proposal.kind}`} · 候选预览`;
  state.serverPreview.textContent = proposal.text;
  state.source.textContent = `原话：${proposal.source_text || ''}`;
  state.refs.replaceChildren();
  for (const ref of proposal.operation === 'next_step' ? [] : (proposal.object_refs || [])) {
    if (ref?.type !== 'note' || typeof ref.id !== 'string' || !Number.isSafeInteger(ref.version) || ref.version < 1) continue;
    const link = node('a', `当时关联的笔记版本 ${ref.version}`);
    link.href = `/static/notes.html?${new URLSearchParams({note_id:ref.id, version:String(ref.version)})}`;
    link.target = '_blank'; link.rel = 'noopener'; state.refs.append(link);
  }
  state.target.textContent = `事项：${knownThreads.get(proposal.thread_id)?.title || proposal.thread_id} · 预览版本 ${proposal.version}`;
  const original = proposal.operation === 'revise_event' || proposal.operation === 'next_step' ? proposal.original_text : null;
  state.original.hidden = original === null || original === undefined;
  state.original.textContent = original === null || original === undefined ? '' : `${proposal.operation === 'next_step' ? '当前下一步' : '将更正这条记录'}：${original || '尚未设置'}`;
  if (state.expiryTimer) clearTimeout(state.expiryTimer);
  const remaining = expiryTime(proposal) - Date.now();
  if (remaining > 0 && remaining < 2147483647) state.expiryTimer = setTimeout(updateProposalControls, remaining + 10);
  updateProposalControls();
}
async function refreshProposal(state) {
  const id = state.proposal.thread_id;
  const [thread, pending] = await Promise.all([api(threadPath(id)), api(`${threadPath(id)}/proposals`)]);
  knownThreads.set(thread.id, thread);
  const latestProposal = (pending.items || []).find(item => item.id === state.proposal.id);
  if (latestProposal) updateProposalView(state, latestProposal);
  else state.unavailable = true;
  renderSummary(); updateProposalControls();
}
async function proposalAction(state, action) {
  if (state.busy || state.resolved) return;
  updateProposalControls();
  const control = action === 'preview' ? state.previewButton : action === 'confirm' ? state.confirmButton : state.rejectButton;
  if (control.disabled) return;
  state.busy = true; updateProposalControls();
  try {
    const base = proposalPath(state.proposal.id);
    if (action === 'preview') {
      const updated = await api(base, 'PATCH', { text: state.draft.value, expected_version: state.proposal.version });
      state.stale = false; state.conflict = false; updateProposalView(state, updated);
      state.draft.value = updated.text;
      try {
        const thread = await api(threadPath(updated.thread_id));
        knownThreads.set(thread.id, thread); renderSummary(); updateProposalView(state, updated);
      } catch { state.stale = true; }
      state.message.textContent = '预览已更新，尚未保存。请核对上方预览，再确认保存。';
    } else {
      const result = await api(`${base}/${action}`, 'POST', { expected_version: state.proposal.version });
      state.resolved = true;
      if (state.expiryTimer) clearTimeout(state.expiryTimer);
      state.controls.remove(); state.draft.parentElement.remove();
      state.message.textContent = action === 'confirm' ? '已保存。用户自述记录未经工具核验。' : '未保存这条候选。';
      cards.delete(state.proposal.id);
      if (action === 'confirm') { changed(result); renderSummary(); }
    }
  } catch (error) {
    if ([409, 404].includes(error.status)) {
      state.stale = true; state.conflict = true;
      try { await refreshProposal(state); }
      catch { if (error.status === 404) state.unavailable = true; }
      state.message.textContent = state.unavailable ? '候选已过期、删除或处理。草稿仍保留，可复制后重新提出。没有自动重试。' : '版本已变化，已读取最新预览；草稿仍保留。请明确更新预览后再确认，没有自动重试。';
    } else state.message.textContent = `未完成：${error.message}。草稿仍保留，没有自动重复提交。`;
  } finally { state.busy = false; updateProposalControls(); }
}
function renderProposal(proposal) {
  const chat = document.querySelector('#chat');
  if (!chat || !proposal?.id) return false;
  const existing = cards.get(proposal.id);
  if (existing) { if (proposal.version >= existing.proposal.version) updateProposalView(existing, proposal); return true; }
  const card = node('article', undefined, 'msg bot companion-proposal');
  card.dataset.proposalId = proposal.id; card.setAttribute('aria-label', '共同事项候选');
  const heading = node('h3'), target = node('small', undefined, 'companion-hint'), source = node('p', undefined, 'companion-source'), original = node('p', undefined, 'companion-original'), refs = node('div', undefined, 'companion-source');
  const previewLabel = node('div', undefined, 'companion-preview');
  const serverPreview = node('p'); previewLabel.append(node('small', '确认保存将使用这份预览'), serverPreview);
  const label = node('label', '编辑候选草稿'), draft = node('textarea');
  draft.rows = 3; draft.maxLength = 4000; draft.value = proposal.text; label.append(draft);
  const controls = node('div', undefined, 'companion-actions'), message = node('p', '尚未保存；修改后请先更新预览。', 'companion-feedback');
  message.setAttribute('role', 'status');
  const state = { card, heading, target, source, original, refs, serverPreview, draft, controls, message, proposal, stale: false, busy: false, unavailable: false, resolved: false };
  state.previewButton = button('更新预览', () => proposalAction(state, 'preview'));
  state.confirmButton = button('确认保存', () => proposalAction(state, 'confirm'));
  state.rejectButton = button('不保存', () => proposalAction(state, 'reject'));
  controls.append(state.previewButton, state.confirmButton, state.rejectButton);
  card.append(heading, target, source, refs, original, previewLabel, label, controls, message); chat.append(card);
  cards.set(proposal.id, state); draft.addEventListener('input', updateProposalControls);
  updateProposalView(state, proposal);
  return true;
}
export function showCompanionResult(result) {
  if (result?.confirmation || result?.receipts?.length) {
    if (result.companion?.thread || result?.receipts?.length) changed(result.companion?.thread);
    return false;
  }
  if (!document.querySelector('#chat') || (!result?.proposal && !result?.companion)) return false;
  if (result.companion?.thread) knownThreads.set(result.companion.thread.id, result.companion.thread);
  if (result.proposal) {
    renderProposal(result.proposal);
    appendContextUsage(cards.get(result.proposal.id)?.card, result.context_usage);
  }
  else {
    const card = node('article', undefined, 'msg bot companion-result');
    const thread = result.companion?.thread;
    const choices = result.companion?.choices || [];
    if (!thread && !choices.length) card.append(node('p', '请先在上方选择或新建共同事项，再继续讨论。'));
    const feedback = node('p', undefined, 'companion-feedback'); feedback.setAttribute('role', 'status');
    if (thread) {
      card.append(node('h3', thread.title), facts(thread));
      if (thread.id !== selectedId) card.append(button('关联这件事', () => manage(async () => {
        await associate(thread.id); feedback.textContent = '已关联这件事。点继续会准备草稿，按发送后才会开始。';
      }, feedback)));
      card.append(button('继续这件事', () => continueDraft(thread, feedback)));
    }
    for (const choice of choices) {
      if (!choice?.id) continue;
      card.append(button(`关联「${choice.title}」`, () => manage(async () => {
        await associate(choice.id); feedback.textContent = '已关联。你可以在输入框继续，按发送后才会开始。';
      }, feedback)));
    }
    card.append(feedback); appendContextUsage(card, result.context_usage); document.querySelector('#chat').append(card);
  }
  changed(result.companion?.thread);
  return true;
}
export async function bootCompanion() {
  if (mount) return;
  mount = document.querySelector('#companionMount');
  if (!mount) return;
  mount.className = 'companion-panel';
  summary = node('div'); notice = node('p', undefined, 'companion-feedback'); notice.setAttribute('role', 'status');
  manager = node('details', undefined, 'companion-manager'); manager.append(node('summary', '选择、创建与对话关联'));
  const selectionRow = node('div', undefined, 'companion-actions');
  chooser = node('select'); chooser.setAttribute('aria-label', '已有共同事项');
  chooser.addEventListener('change', updateManagementControls);
  selectButton = button('关联对话', () => manage(() => associate(chooser.value)));
  clearButton = button('取消关联', () => manage(() => associate(null)));
  selectionRow.append(chooser, selectButton, clearButton);
  const createForm = node('form', undefined, 'companion-actions');
  titleInput = node('input'); titleInput.maxLength = 200; titleInput.placeholder = '如：准备周五的演示'; titleInput.setAttribute('aria-label', '新共同事项名称');
  createButton = node('button', '创建并关联'); createButton.type = 'submit'; createForm.append(titleInput, createButton);
  createForm.addEventListener('submit', event => {
    event.preventDefault(); const title = titleInput.value.trim();
    if (!title) { notice.textContent = '先给想一起做的事起个名字。'; return; }
    manage(async () => {
      const thread = await api('/api/threads', 'POST', { title });
      knownThreads.set(thread.id, thread); titleInput.value = ''; await associate(thread.id);
    });
  });
  manager.append(selectionRow, createForm, button('刷新事项', () => manage(async () => {
    await refresh(); notice.textContent = '已读取最新事项和对话关联。候选草稿保持原样。';
  })));
  mount.append(summary, manager, notice); renderSummary();
  window.addEventListener('companion-updated', () => { refresh().catch(error => { notice.textContent = `事项刷新失败：${error.message}。可以点击刷新事项重试。`; }); });
  try { await refresh(); }
  catch (error) { notice.textContent = `暂时读不到共同事项：${error.message}。聊天仍可继续，可展开后刷新。`; }
}
