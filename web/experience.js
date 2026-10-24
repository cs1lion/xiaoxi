// Explicit, session-only experience controls. No transcript persistence or automatic sends.
const SOURCE_KEY = 'neurosama:selected-source';
const MAX_AGE = 10 * 60 * 1000;
const modes = {
  off: '普通聊天 / 关闭体验', listen: '听懂原句', expression: '表达练习',
  theatre: '虚构小剧场', focus: '专注陪伴', walk: '散步陪伴',
  winddown: '睡前放松', vent: '倾诉', discuss: '一起讨论',
};
const limits = { text: 3000, translation: 3000, label: 200, source_id: 200 };
const element = (tag, text) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
};
function boundedEntry(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (Object.keys(value).some(key => !Object.hasOwn(limits, key))) return null;
  const result = {};
  for (const [key, limit] of Object.entries(limits)) {
    if (value[key] !== undefined && typeof value[key] !== 'string') return null;
    result[key] = value[key] ?? '';
    if (result[key].length > limit) return null;
  }
  if (!result.text.trim()) return null;
  return result;
}
function boundedSource(value, handoff = false) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (Object.keys(value).some(key => !Object.hasOwn(limits, key) && key !== 'context' && !(handoff && key === 'timestamp'))) return null;
  const selected = Object.fromEntries(Object.keys(limits).filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
  const result = boundedEntry(selected);
  if (!result) return null;
  if (Object.hasOwn(value, 'context')) {
    if (!Array.isArray(value.context) || value.context.length > 4) return null;
    if (value.context.some(entry => !entry || typeof entry !== 'object' || Array.isArray(entry) || Object.keys(limits).some(key => !Object.hasOwn(entry, key)))) return null;
    const context = value.context.map(boundedEntry);
    if (context.some(entry => !entry)) return null;
    const ids = new Set(result.source_id.trim() ? [result.source_id.trim()] : []);
    let size = 0;
    for (const entry of context) {
      size += Object.values(entry).reduce((sum, text) => sum + text.length, 0);
      const id = entry.source_id.trim();
      if (id && ids.has(id)) return null;
      if (id) ids.add(id);
    }
    if (size > 6000) return null;
    result.context = context;
  }
  return result;
}

/**
 * Parent creates #experienceMount, links experience.css, and forwards config_ack to onAck.
 * sendConfig receives the ENTIRE config payload; sendText receives a plain user text.
 * onSwitch runs once per confirmed apply, before sendConfig, so the parent can cancel a turn.
 * importSelection consumes a one-shot sessionStorage handoff (timestamp: Date.now()).
 * Mount/import never send, cancel, or apply. activeMode reports only server-acknowledged mode.
 * reveal expands and focuses the mounted panel; it never selects or applies a mode.
 * Parent calls onDisconnect when this connection closes; refresh mounts a new session.
 */
export function mountExperience({ sendConfig, sendText, onSwitch }) {
  const mount = document.getElementById('experienceMount');
  if (!mount) throw new Error('Missing #experienceMount');
  if (typeof sendConfig !== 'function' || typeof sendText !== 'function') {
    throw new TypeError('sendConfig and sendText must be functions');
  }
  const panel = element('details');
  panel.className = 'experience-panel panel';
  panel.dataset.experience = 'true';
  panel.append(element('summary', '陪伴体验（手动开启）'));
  panel.append(element('p', '选择后点击「应用体验」，确认重置当前对话上下文。不会自动开口，不保存聊天。原句仅在你明确点击「保存原句到本机」并确认后持久保存。'));
  const status = element('p', '尚未应用体验；等待你的选择。');
  status.className = 'experience-status';
  status.id = 'experienceStatus';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  panel.append(status);
  const activeSource = element('section');
  activeSource.id = 'experienceActiveSource';
  activeSource.className = 'experience-applied-source';
  activeSource.setAttribute('aria-label', '服务器已确认的讨论片段');
  panel.append(activeSource);
  const draftStatus = element('p');
  draftStatus.id = 'experienceDraftStatus';
  draftStatus.className = 'experience-hint';
  draftStatus.setAttribute('role', 'status');
  panel.append(draftStatus);
  const form = element('form');
  form.className = 'experience-fields';
  function field(title, node, id) {
    node.id = id;
    const label = element('label');
    label.htmlFor = id;
    label.append(element('span', title), node);
    form.append(label);
    return node;
  }
  function select(entries) {
    const node = element('select');
    for (const [value, title] of Object.entries(entries)) {
      const option = element('option', title);
      option.value = value;
      node.append(option);
    }
    return node;
  }
  const mode = field('体验模式', select(modes), 'experienceMode');
  const scene = field('虚构场景', select({
    'rain-radio': '雨夜电台', spaceship: '太空船', mystery: '轻悬疑', custom: '自定义虚构场景',
  }), 'experienceScene');
  const customScene = field('自定义场景（最多 1000 字）', element('textarea'), 'experienceCustomScene');
  customScene.maxLength = 1000;
  customScene.rows = 2;
  const origin = field('原句来源说明', select({ manual: '手动提供 / 已手动编辑', recognized: '识别原句（可能有误，请核对）' }), 'experienceOrigin');
  const sourceLabel = field('来源标签（可编辑，最多 200 字）', element('input'), 'experienceSourceLabel');
  sourceLabel.maxLength = 200;
  sourceLabel.value = '手动提供原句';
  const source = field('选中的原句（可编辑，最多 3000 字）', element('textarea'), 'experienceSource');
  source.maxLength = 3000;
  source.rows = 3;
  const translation = field('译文参考（可编辑，最多 3000 字）', element('textarea'), 'experienceTranslation');
  translation.maxLength = 3000;
  translation.rows = 2;
  const sourceNote = element('p', '原句只在「听懂原句 / 表达练习」应用时发送。识别内容不是已核验事实；这里不是发音评分。');
  sourceNote.className = 'experience-hint';
  form.append(sourceNote);
  const contextPreview = element('section');
  contextPreview.id = 'experienceContextPreview';
  contextPreview.setAttribute('aria-label', '邻近原句草稿预览');
  form.append(contextPreview);
  const actions = element('div');
  actions.className = 'experience-actions';
  const button = (title, id) => {
    const node = element('button', title);
    node.type = 'button';
    node.id = id;
    return node;
  };
  const importButton = button('导入选中原句（不应用）', 'experienceImport');
  const discardContext = button('丢弃邻近原句（仅草稿）', 'experienceDiscardContext');
  const clear = button('清除本地原句', 'experienceClearSource');
  const apply = button('应用体验（重置上下文）', 'experienceApply');
  apply.type = 'submit';
  actions.append(importButton, discardContext, clear, apply);
  form.append(actions);
  panel.append(form);
  const prompts = element('div');
  prompts.className = 'experience-actions';
  const explain = button('解释选中的原句', 'experienceExplain');
  const relations = button('看原句与前后字幕的关系', 'experienceRelations');
  const practice = button('用原句练一个新场景', 'experiencePractice');
  const kickoff = button('开始虚构小剧场', 'experienceKickoff');
  prompts.append(explain, relations, practice, kickoff);
  panel.append(prompts, element('p', '小剧场仅为虚构，不代表现实事件。陪伴不等于治疗或安全监护；所有交流都由你主动开始。清除原句仅清除本页面草稿；应用重置对话上下文，不删除服务器持久记忆。'));
  const shelf = element('details');
  shelf.className = 'experience-shelf';
  shelf.append(element('summary', '本机原句收藏（仅明确保存）'));
  shelf.append(element('p', '只保存你明确选择的原句、译文和标签，不保存聊天或生成的虚构故事。同一浏览器 / 网站来源可读取这些收藏；不写入服务器记忆。'));
  const saveSource = button('保存原句到本机', 'experienceSaveSource');
  const clearShelf = button('清空本机原句收藏', 'experienceClearShelf');
  const shelfActions = element('div');
  shelfActions.className = 'experience-actions';
  shelfActions.append(saveSource, clearShelf);
  const shelfList = element('div');
  shelfList.id = 'experienceShelfList';
  shelf.append(shelfActions, shelfList);
  panel.append(shelf);
  mount.replaceChildren(panel);

  let active = 'off';
  let pending = false;
  let sourceId = '';
  let sourceContext;
  let acknowledgedSource = null;
  let submittedSource = null;
  let requestCounter = 0;
  let pendingRequestId = null;
  let disconnected = false;
  const sourceMode = value => value === 'listen' || value === 'expression';
  function draftSource() {
    return boundedSource({ text: source.value, translation: translation.value,
      label: sourceLabel.value, source_id: sourceId,
      ...(sourceContext === undefined ? {} : { context: sourceContext }) });
  }
  function sourceReady(expected) {
    return !disconnected && !pending && active === expected && mode.value === expected
      && acknowledgedSource !== null
      && JSON.stringify(draftSource()) === JSON.stringify(acknowledgedSource);
  }
  function renderActiveSource() {
    activeSource.replaceChildren();
    activeSource.hidden = !sourceMode(active) || !acknowledgedSource;
    if (activeSource.hidden) return;
    activeSource.append(element('strong', `正在讨论的原句 · ${modes[active]}（服务器已确认）`),
      element('p', '提问使用这份已应用片段。下方编辑的是草稿，重新应用后才会替换。'));
    const quote = element('section');
    quote.className = 'experience-quote';
    quote.append(element('strong', acknowledgedSource.label || '选中原句'), element('p', acknowledgedSource.text));
    if (acknowledgedSource.translation) quote.append(element('strong', '译文参考'), element('p', acknowledgedSource.translation));
    activeSource.append(quote);
    if (acknowledgedSource.context?.length) {
      const neighbors = element('details');
      neighbors.append(element('summary', `已应用邻近字幕：${acknowledgedSource.context.length} 条`));
      for (const entry of acknowledgedSource.context) {
        const row = element('section');
        row.className = 'experience-quote';
        row.append(element('strong', entry.label || '邻近字幕'), element('p', entry.text));
        if (entry.translation) row.append(element('p', entry.translation));
        neighbors.append(row);
      }
      activeSource.append(neighbors);
    }
  }
  function renderContext() {
    contextPreview.replaceChildren();
    discardContext.hidden = sourceContext === undefined;
    if (sourceContext === undefined) { contextPreview.hidden = true; return; }
    contextPreview.hidden = false;
    contextPreview.append(element('p', `邻近原句：${sourceContext.length} 条（仅草稿；核对后应用才发送，不保存到收藏）。`));
    for (const entry of sourceContext) {
      const row = element('section');
      row.append(element('strong', entry.label || '邻近原句'), element('p', entry.text), element('p', entry.translation));
      contextPreview.append(row);
    }
  }
  function clearContext() { sourceContext = undefined; renderContext(); }
  discardContext.addEventListener('click', () => {
    clearContext();
    controls();
    status.textContent = '邻近原句已从草稿丢弃；选中原句保留，服务器当前上下文未变。';
  });
  renderContext();
  function controls() {
    scene.parentElement.hidden = mode.value !== 'theatre';
    customScene.parentElement.hidden = mode.value !== 'theatre' || scene.value !== 'custom';
    apply.disabled = disconnected || pending;
    explain.disabled = !sourceReady('listen');
    relations.disabled = !sourceReady('listen') || !acknowledgedSource?.context?.length;
    practice.disabled = !sourceReady('expression');
    kickoff.disabled = disconnected || pending || active !== 'theatre' || mode.value !== 'theatre';
    draftStatus.hidden = !sourceMode(active) && !sourceMode(mode.value);
    draftStatus.textContent = disconnected ? '连接已断开，草稿保留；重新连接后请再次确认应用。'
      : pending ? '正在等待服务器确认；快捷提问暂不可用，请等确认后再提问。'
      : !acknowledgedSource || !sourceMode(active) ? '这份原句草稿尚未生效；选择原句模式并确认应用后再提问。'
      : sourceReady(active) ? '草稿与已应用片段一致，可点击下方快捷提问，也可通过主页面打字或说话。'
      : '草稿或模式已修改，尚未应用；快捷提问已暂停。主页面直接打字或说话仍使用上方已确认片段。';
  }
  mode.addEventListener('change', controls);
  scene.addEventListener('change', controls);
  function manualEdit() {
    origin.value = 'manual';
    sourceId = '';
    clearContext();
    controls();
  }
  for (const node of [source, translation, sourceLabel]) node.addEventListener('input', manualEdit);
  origin.addEventListener('change', () => { if (origin.value === 'manual') manualEdit(); });
  const BOOKMARK_KEY = 'neurosama:source-bookmarks';
  let bookmarks = [];
  try {
    const stored = JSON.parse(localStorage.getItem(BOOKMARK_KEY) || '[]');
    if (Array.isArray(stored)) bookmarks = stored.slice(0, 20).map(value => boundedSource(value)).filter(Boolean).map(({ text, translation, label }) => ({ text, translation, label }));
  } catch { /* Corrupt or unavailable storage never blocks ordinary use. */ }
  function restoreSource(value, recognized = false) {
    clearContext();
    source.value = value.text;
    translation.value = value.translation;
    sourceLabel.value = value.label || (recognized ? '识别原句（请核对）' : '手动提供原句');
    sourceId = recognized ? value.source_id : '';
    origin.value = recognized ? 'recognized' : 'manual';
    controls();
  }
  function renderShelf() {
    shelfList.replaceChildren();
    if (!bookmarks.length) shelfList.append(element('p', '暂无本机原句收藏。最多 20 条；只有明确点击保存才会持久保存。'));
    bookmarks.forEach((item, index) => {
      const row = element('section');
      row.className = 'experience-bookmark';
      row.append(element('strong', item.label || '原句'), element('p', item.text), element('p', item.translation));
      const restore = button('载入草稿（不应用）', `experienceRestore${index}`);
      const remove = button('删除这条本机原句', `experienceDelete${index}`);
      restore.addEventListener('click', () => {
        restoreSource(item);
        status.textContent = '本机收藏已载入草稿；尚未发送或应用，请核对后明确应用。';
      });
      remove.addEventListener('click', () => {
        if (!window.confirm('仅删除这条本机原句收藏，不删除服务器记忆。确认删除？')) return;
        persistShelf(bookmarks.filter((_, i) => i !== index));
      });
      const actions = element('div');
      actions.className = 'experience-actions';
      actions.append(restore, remove);
      row.append(actions);
      shelfList.append(row);
    });
  }
  function persistShelf(next) {
    try {
      if (next.length) localStorage.setItem(BOOKMARK_KEY, JSON.stringify(next));
      else localStorage.removeItem(BOOKMARK_KEY);
      bookmarks = next;
      renderShelf();
      status.textContent = '本机原句收藏已更新；未发送、未应用，也未更改服务器记忆。';
    } catch { status.textContent = '本机收藏更新失败；浏览器存储不可用。未发送或应用。'; }
  }
  saveSource.addEventListener('click', () => {
    const item = boundedSource({ text: source.value, translation: translation.value, label: sourceLabel.value });
    if (!item) { status.textContent = '请先填写有效原句（最多 3000 字），译文最多 3000 字，标签最多 200 字。'; return; }
    if (bookmarks.length >= 20) { status.textContent = '本机收藏已满 20 条，请先删除一条。'; return; }
    if (!window.confirm('将这条原句、译文和标签保存在本机浏览器，刷新后仍保留。同一网站来源可读取；不保存聊天、不写入服务器记忆。确认保存？')) return;
    persistShelf([...bookmarks, { text: item.text, translation: item.translation, label: item.label }]);
  });
  clearShelf.addEventListener('click', () => {
    if (!window.confirm('清空本机原句收藏？不删除服务器记忆，也不修改正在使用的体验。')) return;
    persistShelf([]);
  });
  renderShelf();
  function importSelection() {
    let raw;
    try {
      raw = sessionStorage.getItem(SOURCE_KEY);
      sessionStorage.removeItem(SOURCE_KEY);
    } catch {
      status.textContent = '无法读取本次原句交接；可手动粘贴。';
      return false;
    }
    if (!raw) {
      status.textContent = '没有待导入的原句；可在口译页选择，或手动粘贴。';
      return false;
    }
    let candidate;
    try { candidate = JSON.parse(raw); } catch { /* Invalid one-shot handoff is discarded. */ }
    const imported = boundedSource(candidate, true);
    const age = Date.now() - candidate?.timestamp;
    if (!imported || typeof candidate.timestamp !== 'number' || !Number.isFinite(age) || age < 0 || age > MAX_AGE) {
      status.textContent = '原句交接已过期或内容无效（最长 10 分钟）；未导入，也未应用。';
      return false;
    }
    source.value = imported.text;
    translation.value = imported.translation;
    sourceLabel.value = imported.label || '识别原句（请核对）';
    sourceId = imported.source_id;
    sourceContext = imported.context;
    renderContext();
    origin.value = 'recognized';
    controls();
    panel.open = true;
    status.textContent = `原句已导入本地草稿${sourceContext === undefined ? '' : `，邻近原句 ${sourceContext.length} 条`}，请核对。尚未应用；点击应用并确认才会发送。`;
    return true;
  }
  importButton.addEventListener('click', importSelection);
  clear.addEventListener('click', () => {
    clearContext();
    source.value = '';
    translation.value = '';
    sourceLabel.value = '手动提供原句';
    sourceId = '';
    origin.value = 'manual';
    controls();
    status.textContent = '本地原句草稿已清除。服务器当前上下文未变；可应用关闭体验来重置上下文，持久记忆不会被删除。';
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (disconnected || pending) return;
    const chosenMode = mode.value;
    if (!Object.hasOwn(modes, chosenMode)) return;
    const config = { type: 'config', experience: { mode: chosenMode, scene: '' } };
    let nextSource = null;
    if (sourceMode(chosenMode) && (source.value || translation.value)) {
      nextSource = draftSource();
      if (!nextSource) {
        status.textContent = '原句不能为空白，且原句 / 译文最多 3000 字，标签 / 来源编号最多 200 字。未发送。';
        return;
      }
      config.experience_source = nextSource;
    }
    if (chosenMode === 'theatre') {
      config.experience.scene = scene.value === 'custom' ? customScene.value.trim() : scene.value;
      if (!config.experience.scene || config.experience.scene.length > 1000) {
        status.textContent = '请填写 1–1000 字的虚构场景。未发送。';
        return;
      }
    }
    if (!window.confirm(`应用体验会重置当前对话上下文并取消正在进行的回复，但不会删除服务器持久记忆。${nextSource?.context ? `将发送选中原句和 ${nextSource.context.length} 条邻近原句。` : ''}确认应用？`)) return;
    pending = true;
    pendingRequestId = `experience-${++requestCounter}`;
    config.experience_request_id = pendingRequestId;
    submittedSource = nextSource ? boundedSource(nextSource) : null;
    status.textContent = '已确认切换；等待服务器确认，尚未宣告生效。';
    controls();
    try {
      await onSwitch?.();
      const result = await sendConfig(config);
      if (result === false) throw new Error('配置未发送，请确认已连接。');
    } catch (error) {
      pending = false;
      pendingRequestId = null;
      submittedSource = null;
      status.textContent = `应用未完成：${error instanceof Error ? error.message : '发送失败'}。未确认新体验生效。`;
      controls();
    }
  });
  async function sendPrompt(text, expected) {
    if (disconnected || pending || active !== expected || mode.value !== expected
        || (sourceMode(expected) && !sourceReady(expected))) return;
    try {
      const result = await sendText(text);
      if (result === false) throw new Error('未连接');
    } catch {
      status.textContent = '消息未发送，请检查连接后重试。';
    }
  }
  explain.addEventListener('click', () => sendPrompt('请解释我选中的这句原句，包括意思、语境和表达用法。', 'listen'));
  relations.addEventListener('click', () => {
    if (!acknowledgedSource?.context?.length) return;
    return sendPrompt('请结合已提供的邻近字幕，解释选中原句与前后内容的关系；如有代词或转折，请指出依据，区分原句与邻近字幕。片段不足以确定时请说明，不补写缺失内容。', 'listen');
  });
  practice.addEventListener('click', () => sendPrompt('请围绕选中的原句给我一个新场景，让我尝试用出这个表达。', 'expression'));
  kickoff.addEventListener('click', () => sendPrompt('请开始我们选定的虚构小剧场；明确这是虚构场景，不是现实事件。', 'theatre'));
  function onAck(msg) {
    if (disconnected || !msg || msg.type !== 'config_ack') return false;
    // Ordinary settings also return an experience snapshot. They must not finish
    // a pending source change or re-enable questions before that change is acked.
    if (pending ? msg.experience_request_id !== pendingRequestId
      : Object.hasOwn(msg, 'experience_request_id')) return false;
    const actual = msg.experience ?? msg.config?.experience;
    const error = msg.experience_error ?? msg.error;
    if (error || msg.success === false || msg.ok === false) {
      // Unrelated settings failures are not an experience acknowledgement.
      if (!pending && !actual && !msg.experience_error) return false;
      pending = false;
      pendingRequestId = null;
      submittedSource = null;
      status.textContent = `体验未应用：${typeof error === 'string' ? error : '服务器拒绝配置'}。未确认新体验生效。`;
      controls();
      return false;
    }
    if (!actual || !Object.hasOwn(modes, actual.mode)) return false;
    active = actual.mode;
    const hasActualSource = Object.hasOwn(actual, 'source') || Object.hasOwn(msg, 'experience_source') || Object.hasOwn(msg.config ?? {}, 'experience_source');
    acknowledgedSource = sourceMode(active)
      ? (hasActualSource ? boundedSource(actual.source ?? msg.experience_source ?? msg.config?.experience_source) : (pending ? submittedSource : acknowledgedSource))
      : null;
    pending = false;
    pendingRequestId = null;
    submittedSource = null;
    renderActiveSource();
    status.textContent = `服务器已确认：${modes[active]}${active === 'theatre' && typeof actual.scene === 'string' ? ` · ${actual.scene}` : ''}${acknowledgedSource?.context ? ` · 邻近原句 ${acknowledgedSource.context.length} 条` : ''}。不会自动开始说话。`;
    controls();
    return true;
  }
  function onDisconnect() {
    disconnected = true;
    active = 'off';
    pending = false;
    pendingRequestId = null;
    acknowledgedSource = submittedSource = null;
    renderActiveSource();
    controls();
    status.textContent = '连接已断开，体验已失效；原句草稿保留，刷新并重新确认应用后再提问。';
  }
  renderActiveSource();
  controls();
  function reveal() {
    panel.open = true;
    panel.scrollIntoView({ block: "nearest" });
    panel.querySelector("summary")?.focus({ preventScroll: true });
  }
  return { onAck, onDisconnect, importSelection, activeMode: () => active, reveal };
}
