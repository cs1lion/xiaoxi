/* One optional observation about the current draft; never edits or saves it. */
const MIN_BODY = 80, MIN_CHANGE = 60;
const DEFAULTS = { pause_ms: 12000, cooldown_ms: 90000, max_checks: 5 };
const newId = () => globalThis.crypto?.randomUUID?.() || `editor-${Date.now()}-${Math.random().toString(36).slice(2)}`;
const reasonText = reason => ({
  memory_disabled: '允许阿栖记住已关闭，这次不读取或分析稿件。',
  model_unavailable: '当前模型尚不可用，先按你的节奏继续写。',
  editor_not_enabled: '陪我看稿尚未开启，稿件没有用于建议。',
  sensitive_draft: '这份稿子可能含有凭据等敏感内容，已跳过分析。',
  older_draft_revision: '稿子已经变化，继续编辑后再看。',
  draft_revision_changed: '稿子已经变化，继续编辑后再看。',
  draft_revision_content_changed: '稿子已经变化，继续编辑后再看。',
  session_check_limit: '这次看稿已到上限，先按你的节奏继续写。',
  check_cooldown: '先留一点写作时间，继续编辑后再看。',
  draft_already_checked: '这段稿子刚刚看过，继续写就好。',
  analysis_timeout: '这次建议等待超时，你可以继续写。',
  analysis_unavailable: '这次建议没有取到，你可以继续写。',
  analysis_output_truncated: '这次建议没有完整生成，你可以继续写。',
  analysis_empty_response: '这次没有收到建议，你可以继续写。',
  analysis_invalid_response: '这次建议暂时无法使用，你可以继续写。',
  insight_discussion_output_truncated: '这次回复没有完整生成；你的输入保留，可以重试。',
  insight_discussion_empty_response: '这次没有收到回复；你的输入保留，可以重试。',
  insight_discussion_invalid_reply: '这次回复暂时无法使用；你的输入保留，可以重试。',
  no_salient_insight: '暂时没有值得打断你的观点，继续写。',
  dismissed_anchor: '这条已经放下，继续写就好。',
}[reason] || (/^[\w_]+$/.test(String(reason || '')) ? '建议暂时不可用；你的输入仍保留。' : String(reason || '建议暂时不可用；你的输入仍保留。')));
const make = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const changedCharacters = (before, after) => {
  let start = 0, end = 0;
  while (start < Math.min(before.length, after.length) && before[start] === after[start]) start++;
  while (end < Math.min(before.length, after.length) - start && before[before.length - end - 1] === after[after.length - end - 1]) end++;
  return Math.max(before.length, after.length) - start - end;
};
const boundedNumber = (value, fallback, min, max) => Number.isFinite(value) ? Math.max(min, Math.min(max, value)) : fallback;

export function mountEditorInsights({ editor, getDraft }) {
  const embedded = window.parent !== window;
  const sessionId = newId();
  let revision = 0, enabled = false, active = !embedded, composing = false, discussion = false;
  let settings = { ...DEFAULTS }, checks = 0, lastCheck = -Infinity, lastBody = '';
  let pauseTimer = null, expiryTimer = null, stateTimer = null;
  let analyzeController = null, speechController = null, stateController = null;
  let discussionController = null, feedbackController = null;
  let candidate = null, playbackRequest = null, playbackState = 'idle', localAudio = null, objectUrl = null;
  let disposed = false, statusPending = false, statusGeneration = 0, stateRegistered = false;

  const region = make('section', undefined, 'editor-insights');
  region.setAttribute('aria-label', '阿栖陪你看稿');
  const controls = make('div', undefined, 'editor-insight-controls');
  const toggle = make('button', '陪我看稿：关', 'editor-insight-toggle');
  toggle.id = 'editorInsightToggle'; toggle.type = 'button'; toggle.setAttribute('aria-pressed', 'false');
  const disclosure = make('p', '开启后，当前未保存稿会用于本次建议，不会保存到记忆；停笔后提一条观点，点击才播报。', 'editor-insight-disclosure');
  const feedback = make('p', '', 'editor-insight-feedback'); feedback.id = 'editorInsightFeedback';
  feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const bubble = make('button', '', 'editor-insight-bubble'); bubble.id = 'editorInsightBubble';
  bubble.type = 'button'; bubble.hidden = true; bubble.setAttribute('aria-expanded', 'false');
  const detail = make('div', undefined, 'editor-insight-detail'); detail.id = 'editorInsightDetail'; detail.hidden = true;
  bubble.setAttribute('aria-controls', detail.id);
  const point = make('p', undefined, 'editor-insight-point');
  const anchor = make('blockquote', undefined, 'editor-insight-anchor');
  const detailActions = make('div', undefined, 'editor-insight-actions');
  const stop = make('button', '停止播报'); stop.id = 'editorInsightStop'; stop.type = 'button'; stop.hidden = true;
  const collapse = make('button', '收起'); collapse.id = 'editorInsightCollapse'; collapse.type = 'button';
  const discuss = make('button', '接着聊'); discuss.id = 'editorInsightDiscuss'; discuss.type = 'button';
  const dismiss = make('button', '这条不用'); dismiss.id = 'editorInsightDismiss'; dismiss.type = 'button';
  detailActions.append(stop, collapse, discuss, dismiss);
  const rating = make('div', undefined, 'editor-insight-rating'); rating.setAttribute('aria-label', '这条观点是否有用');
  const useful = make('button', '有用'); useful.id = 'editorInsightUseful'; useful.type = 'button'; useful.setAttribute('aria-pressed', 'false');
  const missed = make('button', '没说到点上'); missed.id = 'editorInsightMissed'; missed.type = 'button'; missed.setAttribute('aria-pressed', 'false');
  const ratingStatus = make('p', '', 'editor-insight-rating-status'); ratingStatus.id = 'editorInsightRatingStatus'; ratingStatus.setAttribute('role', 'status');
  rating.append(useful, missed, ratingStatus);
  const detailText = make('div', undefined, 'editor-insight-detail-text'); detailText.append(point, anchor);
  const localDiscussion = make('section', undefined, 'editor-insight-discussion'); localDiscussion.hidden = true;
  const discussionHint = make('p', '围绕这条观点继续聊；仅提交后才调用模型，对话只在本次看稿中保留。', 'editor-insight-discussion-hint');
  const discussionMessages = make('div', undefined, 'editor-insight-discussion-messages'); discussionMessages.id = 'editorInsightDiscussionMessages'; discussionMessages.setAttribute('aria-live', 'polite');
  const discussionLabel = make('label', '你的想法');
  const discussionInput = make('textarea'); discussionInput.id = 'editorInsightDiscussionInput'; discussionInput.rows = 2; discussionInput.maxLength = 1000; discussionInput.placeholder = '为什么？或说说你想保留的地方…';
  discussionLabel.append(discussionInput);
  const discussionSend = make('button', '发送'); discussionSend.id = 'editorInsightDiscussionSend'; discussionSend.type = 'button';
  const discussionStatus = make('p', '', 'editor-insight-discussion-status'); discussionStatus.id = 'editorInsightDiscussionStatus'; discussionStatus.setAttribute('role', 'status');
  localDiscussion.append(discussionHint, discussionMessages, discussionLabel, discussionSend, discussionStatus); detailText.append(localDiscussion);
  detail.append(detailText, rating, detailActions);
  const floating = make('aside', undefined, 'editor-insight-float'); floating.hidden = true; floating.setAttribute('aria-label', '阿栖的稿件观点');
  floating.append(bubble, detail); document.body.append(floating);
  controls.append(toggle); region.append(controls, disclosure, feedback);
  editor.querySelector('#editorHeading').after(region);
  const contextDetails = make('details', undefined, 'editor-note-context'); contextDetails.open = true;
  contextDetails.append(make('summary', '笔记来源与对话关联'));
  const sourceNode = editor.querySelector('#source'); sourceNode.before(contextDetails);
  for (const id of ['source', 'noteSelectionStatus', 'selectedNoteLink']) {
    const contextNode = editor.querySelector(`#${id}`); if (contextNode) contextDetails.append(contextNode);
  }
  const shortViewport = matchMedia('(max-height:560px)'); let writingMode = false;
  const compactContext = () => { contextDetails.open = !(writingMode && shortViewport.matches); };
  shortViewport.addEventListener('change', compactContext);

  const visible = () => !disposed && active && !document.hidden;
  const eligible = () => getDraft().eligible !== false;
  const effectiveEnabled = () => enabled && visible() && eligible();
  const parentMessage = message => { if (embedded) window.parent.postMessage(message, location.origin); };
  function say(text) { feedback.textContent = text; }
  function updateToggle() {
    toggle.textContent = enabled ? '陪我看稿：开' : '陪我看稿：关';
    toggle.setAttribute('aria-pressed', String(enabled)); toggle.disabled = statusPending;
    document.body.classList.toggle('editor-insight-enabled', enabled);
  }
  async function request(path, payload, signal) {
    const response = await fetch(`/api/editor-insights${path}`, {
      ...(payload === undefined ? {} : { method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' } }),
      cache: 'no-store', signal,
    });
    const result = await response.json();
    if (!response.ok) {
      const error = Error(reasonText(result.detail));
      error.status = response.status; error.reason = result.detail; throw error;
    }
    return result;
  }
  async function syncState() {
    if (!stateRegistered) return;
    clearTimeout(stateTimer); stateTimer = null;
    stateController?.abort(); const controller = new AbortController(); stateController = controller;
    const current = revision;
    try { await request('/state', { session_id: sessionId, draft_revision: current, enabled: enabled && eligible() && (visible() || discussion) }, controller.signal); }
    catch (error) { if (error.name !== 'AbortError' && current === revision && enabled) say('建议连接暂不可用；你的输入仍保留。'); }
    finally { if (stateController === controller) stateController = null; }
  }
  function queueState() { clearTimeout(stateTimer); if (stateRegistered) stateTimer = setTimeout(syncState, 80); }
  function stopLocal() {
    speechController?.abort(); speechController = null;
    if (localAudio) { localAudio.onended = null; localAudio.onerror = null; localAudio.pause(); localAudio.removeAttribute('src'); localAudio.load(); localAudio = null; }
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
  }
  function cancelPlayback({ preserveDiscussion = false } = {}) {
    stopLocal(); playbackRequest = null; playbackState = 'idle'; stop.hidden = true;
    bubble.disabled = false;
    parentMessage({ type: 'aqi-editor-insight-cancel', session_id: sessionId, draft_revision: revision,
      ...(preserveDiscussion ? { reason: 'handoff', preserve_discussion: true } : {}) });
  }
  function clearCandidate() {
    clearTimeout(expiryTimer); expiryTimer = null;
    candidate = null; floating.hidden = true; floating.classList.remove('is-expanded', 'is-discussing'); bubble.hidden = true; bubble.textContent = ''; bubble.setAttribute('aria-expanded', 'false');
    detail.hidden = true; point.textContent = ''; anchor.textContent = '';
    discussionController?.abort(); discussionController = null; feedbackController?.abort(); feedbackController = null;
    localDiscussion.hidden = true; discussionMessages.replaceChildren(); discussionInput.value = ''; discussionSend.disabled = false; discussionStatus.textContent = '';
    useful.disabled = false; missed.disabled = false; useful.setAttribute('aria-pressed', 'false'); missed.setAttribute('aria-pressed', 'false'); ratingStatus.textContent = '';
  }
  function invalidate({ schedule = false, message = '' } = {}) {
    discussion = false;
    revision++; clearTimeout(pauseTimer); pauseTimer = null;
    analyzeController?.abort(); analyzeController = null;
    cancelPlayback(); clearCandidate(); queueState();
    if (message) say(message);
    else if (enabled && !eligible()) say('历史版本仅供查看；载入当前稿后再一起看。');
    else if (enabled && checks >= settings.max_checks) say('这次看稿已到上限，先按你的节奏继续写。');
    else if (enabled) say(visible() ? '继续写，停笔后阿栖再看看。' : '已暂停看稿；回来继续编辑后再看。');
    if (schedule) scheduleAnalysis();
  }
  function scheduleAnalysis() {
    if (!effectiveEnabled() || discussion || composing || checks >= settings.max_checks) return;
    const draft = getDraft();
    if (draft.body.trim().length < MIN_BODY || changedCharacters(lastBody, draft.body) < MIN_CHANGE) return;
    const wait = Math.max(settings.pause_ms, settings.cooldown_ms - (Date.now() - lastCheck));
    pauseTimer = setTimeout(analyze, wait);
  }
  async function analyze() {
    pauseTimer = null;
    if (!effectiveEnabled() || discussion || composing || checks >= settings.max_checks) return;
    const draft = getDraft(), current = revision;
    if (draft.body.trim().length < MIN_BODY || changedCharacters(lastBody, draft.body) < MIN_CHANGE) return;
    await syncState();
    if (current !== revision || !effectiveEnabled() || discussion || composing) return;
    const controller = new AbortController(); analyzeController = controller;
    checks++; lastCheck = Date.now(); lastBody = draft.body;
    say('阿栖在看看这段稿子…');
    try {
      const result = await request('/analyze', { session_id: sessionId, draft_revision: current, title: draft.title, body: draft.body, enabled: true }, controller.signal);
      if (current !== revision || !effectiveEnabled() || discussion || composing || controller.signal.aborted) return;
      if (Number.isInteger(result.checks) && result.checks >= 0 && result.checks <= settings.max_checks) checks = result.checks;
      const insight = result.insight;
      if (result.status === 'insight' && insight && typeof insight.id === 'string' && insight.session_id === sessionId && insight.draft_revision === current &&
          typeof insight.point === 'string' && typeof insight.anchor === 'string' && typeof insight.teaser === 'string' && Number.isFinite(insight.expires_at) && insight.expires_at * 1000 > Date.now()) {
        candidate = insight; updateRating();
        bubble.textContent = insight.teaser.slice(0, 80); bubble.hidden = false; floating.hidden = false;
        bubble.setAttribute('aria-label', `${insight.teaser.slice(0, 80)}，点击听阿栖说`);
        say('阿栖想到一点，想听时再点。');
        expiryTimer = setTimeout(() => invalidate({ message: '刚才的观点已过时，继续编辑后再看。' }), Math.min(2147483647, insight.expires_at * 1000 - Date.now()));
      } else if (result.status === 'quiet') say(reasonText(result.reason || 'no_salient_insight'));
      else if (result.status === 'limit') { checks = settings.max_checks; say('这次看稿已到上限，先按你的节奏继续写。'); }
      else if (result.status === 'cooldown') say('先留一点写作时间，继续编辑后再看。');
      else if (result.status === 'superseded') say('稿子已变化，继续编辑后再看。');
      else say(reasonText(result.reason));
    } catch (error) { if (error.name !== 'AbortError' && current === revision && effectiveEnabled()) say('这次建议没有取到；你的输入仍保留，可以继续写。'); }
    finally { if (analyzeController === controller) analyzeController = null; }
  }
  function playbackFeedback(state, message) {
    playbackState = state;
    const busy = ['preparing', 'playing'].includes(state);
    stop.hidden = !busy; bubble.disabled = busy;
    if (state === 'preparing') say('正在准备播报；你可以随时停止。');
    else if (state === 'playing') say('阿栖在说，你可以停止播报或继续编辑。');
    else if (state === 'done') say('说完了。是否采用，按你的判断。');
    else if (state === 'stopped') say('已停止播报，观点文字仍保留。');
    else if (state === 'error') say(message || '播报暂时不可用，观点文字仍可阅读。');
  }
  async function playLocal(insight, requestId) {
    const controller = new AbortController(); speechController = controller;
    try {
      const result = await request(`/${encodeURIComponent(insight.id)}/speak`, { session_id: sessionId, draft_revision: revision }, controller.signal);
      if (controller.signal.aborted || playbackRequest !== requestId || candidate?.id !== insight.id || !effectiveEnabled()) return;
      const mime = { mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg', m4a: 'audio/mp4', aac: 'audio/aac', flac: 'audio/flac' }[result.format];
      if (!mime || typeof result.audio !== 'string' || result.audio.length > 12 * 1024 * 1024) throw Error('音频格式无效；请阅读观点文字。');
      const bytes = Uint8Array.from(atob(result.audio), c => c.charCodeAt(0));
      objectUrl = URL.createObjectURL(new Blob([bytes], { type: mime }));
      const audio = new Audio(objectUrl); localAudio = audio;
      audio.onended = () => { if (playbackRequest === requestId) { stopLocal(); playbackFeedback('done'); } };
      audio.onerror = () => { if (playbackRequest === requestId) { stopLocal(); playbackFeedback('error'); } };
      await audio.play();
      if (playbackRequest === requestId && localAudio === audio) playbackFeedback('playing');
    } catch (error) { if (error.name !== 'AbortError' && playbackRequest === requestId) { stopLocal(); playbackFeedback('error', error.message); } }
    finally { if (speechController === controller) speechController = null; }
  }
  bubble.onclick = () => {
    if (!candidate || !effectiveEnabled() || composing || ['preparing', 'playing'].includes(playbackState)) return;
    if (candidate.expires_at * 1000 <= Date.now()) { invalidate({ message: '刚才的观点已过时，继续编辑后再看。' }); return; }
    point.textContent = candidate.point; anchor.textContent = candidate.anchor;
    detail.hidden = false; floating.classList.add('is-expanded'); bubble.setAttribute('aria-expanded', 'true');
    playbackRequest = newId(); playbackFeedback('preparing');
    if (embedded) parentMessage({ type: 'aqi-editor-insight-play', request_id: playbackRequest, insight_id: candidate.id, session_id: sessionId, draft_revision: revision });
    else void playLocal(candidate, playbackRequest);
  };
  stop.onclick = () => { cancelPlayback({ preserveDiscussion: discussion }); playbackFeedback('stopped'); };
  collapse.onclick = () => { detail.hidden = true; floating.classList.remove('is-expanded'); bubble.setAttribute('aria-expanded', 'false'); };
  function currentCandidate() {
    if (!candidate || !effectiveEnabled() || composing) return null;
    if (candidate.expires_at * 1000 <= Date.now()) { invalidate({ message: '刚才的观点已过时，继续编辑后再看。' }); return null; }
    return candidate;
  }
  function updateRating() {
    const value = candidate?.feedback;
    useful.setAttribute('aria-pressed', String(value === 'useful')); missed.setAttribute('aria-pressed', String(value === 'missed'));
    ratingStatus.textContent = value === 'useful' ? '已标记有用，反馈只保留在本次看稿。' : value === 'missed' ? '已标记没说到点上，反馈只保留在本次看稿。' : '';
  }
  async function rate(value) {
    const insight = currentCandidate(); if (!insight || feedbackController) return;
    const current = revision, controller = new AbortController(); feedbackController = controller;
    useful.disabled = true; missed.disabled = true; ratingStatus.textContent = '正在记录这次反馈…';
    try {
      await request(`/${encodeURIComponent(insight.id)}/feedback`, { session_id: sessionId, draft_revision: current, value }, controller.signal);
      if (controller.signal.aborted || candidate?.id !== insight.id || revision !== current) return;
      candidate.feedback = value; updateRating();
    } catch (error) { if (error.name !== 'AbortError' && candidate?.id === insight.id && revision === current) ratingStatus.textContent = '反馈暂时没有记下，可以再点一次。'; }
    finally { if (feedbackController === controller) { feedbackController = null; useful.disabled = false; missed.disabled = false; } }
  }
  useful.onclick = () => void rate('useful'); missed.onclick = () => void rate('missed');
  discuss.onclick = () => {
    const insight = currentCandidate(); if (!insight) return;
    discussion = true; clearTimeout(pauseTimer); pauseTimer = null; analyzeController?.abort(); analyzeController = null;
    cancelPlayback({ preserveDiscussion: true });
    if (embedded) {
      say('围绕刚才这条观点，去和阿栖接着聊。');
      parentMessage({ type: 'aqi-editor-insight-discuss', insight_id: insight.id, session_id: sessionId, draft_revision: revision,
        point: insight.point, anchor: insight.anchor, expires_at: insight.expires_at });
    } else {
      localDiscussion.hidden = false; floating.classList.add('is-discussing'); discussionInput.focus(); say('可以围绕这条观点继续聊；改稿后这次讨论会结束。');
    }
  };
  discussionSend.onclick = async () => {
    const insight = currentCandidate(), text = discussionInput.value.trim();
    if (!insight || !discussion || !text || discussionController) return;
    const current = revision, controller = new AbortController(); discussionController = controller;
    discussionSend.disabled = true; discussionStatus.textContent = '阿栖在想…';
    try {
      const result = await request(`/${encodeURIComponent(insight.id)}/discuss`, { session_id: sessionId, draft_revision: current, text }, controller.signal);
      if (controller.signal.aborted || !discussion || candidate?.id !== insight.id || revision !== current) return;
      if (typeof result.text !== 'string' || result.insight_id !== insight.id || result.draft_revision !== current) throw Error('invalid_discussion_response');
      discussionMessages.append(make('p', `你：${text}`, 'editor-insight-discussion-user'), make('p', `阿栖：${result.text}`, 'editor-insight-discussion-reply'));
      if (discussionInput.value.trim() === text) discussionInput.value = '';
      discussionStatus.textContent = '这次讨论仅在当前看稿中保留。';
      detailText.scrollTop = detailText.scrollHeight;
    } catch (error) {
      if (error.name !== 'AbortError' && candidate?.id === insight.id && revision === current) {
        discussionStatus.textContent = ['insight_discussion_output_truncated', 'insight_discussion_empty_response', 'insight_discussion_invalid_reply'].includes(error.reason)
          ? reasonText(error.reason) : '这次没有收到回复；你的输入保留，可以重试。';
      }
    }
    finally { if (discussionController === controller) { discussionController = null; discussionSend.disabled = false; } }
  };
  dismiss.onclick = async () => {
    if (!candidate) return;
    const previous = candidate, current = revision;
    discussion = false; cancelPlayback(); clearCandidate(); say('这条先放下，你继续写。');
    try { await request(`/${encodeURIComponent(previous.id)}/dismiss`, { session_id: sessionId, draft_revision: current }); } catch { /* dismissal stays local even while offline */ }
  };
  toggle.onclick = async () => {
    if (statusPending) return;
    if (enabled) { enabled = false; statusGeneration++; invalidate({ message: '已关闭陪我看稿；草稿保持原样。' }); updateToggle(); await syncState(); return; }
    const generation = ++statusGeneration; statusPending = true; updateToggle();
    try {
      const result = await request('/status');
      if (disposed || generation !== statusGeneration) return;
      if (!result.available) { say(reasonText(result.reason)); return; }
      settings = {
        pause_ms: boundedNumber(result.pause_ms, DEFAULTS.pause_ms, 12000, 120000),
        cooldown_ms: boundedNumber(result.cooldown_ms, DEFAULTS.cooldown_ms, 90000, 3600000),
        max_checks: Math.floor(boundedNumber(result.max_checks, DEFAULTS.max_checks, 1, 5)),
      };
      enabled = true; stateRegistered = true; invalidate({ schedule: true });
    } catch { say('陪我看稿暂时连不上；你的输入仍保留。'); }
    finally { if (generation === statusGeneration) { statusPending = false; updateToggle(); } }
  };
  const onInput = () => invalidate({ schedule: !composing });
  const onCompositionStart = () => { composing = true; invalidate(); };
  const onCompositionEnd = () => { composing = false; invalidate({ schedule: true }); };
  for (const input of editor.querySelectorAll('#title,#body')) {
    input.addEventListener('input', onInput); input.addEventListener('compositionstart', onCompositionStart); input.addEventListener('compositionend', onCompositionEnd);
  }
  const onVisibility = () => invalidate();
  document.addEventListener('visibilitychange', onVisibility);
  const onMessage = event => {
    if (!embedded || event.origin !== location.origin || event.source !== window.parent || !event.data) return;
    const message = event.data;
    if (message.type === 'aqi-editor-mode' && typeof message.writing === 'boolean') { writingMode = message.writing; compactContext(); }
    else if (message.type === 'aqi-editor-visibility' && typeof message.active === 'boolean' && active !== message.active) {
      active = message.active;
      if (discussion && candidate) {
        clearTimeout(pauseTimer); pauseTimer = null; analyzeController?.abort(); analyzeController = null;
        if (!active) cancelPlayback({ preserveDiscussion: true });
        say(active ? '继续讨论，或直接改稿；改稿后这次讨论会结束。' : '正在围绕这条观点讨论；原稿保持在这里。');
      } else invalidate();
    }
    else if (message.type === 'aqi-editor-insight-discussion' && typeof message.active === 'boolean' && candidate &&
             message.insight_id === candidate.id && message.session_id === sessionId && message.draft_revision === revision) {
      discussion = message.active;
      if (!discussion && !visible()) invalidate({ message: '这次讨论已结束；回来继续编辑后再看。' });
      else say(typeof message.message === 'string' ? message.message : discussion ? '正在围绕这条观点讨论；原稿保持在这里。' : '这次讨论已结束，继续写就好。');
    }
    else if (message.type === 'aqi-editor-insight-playback' && message.request_id === playbackRequest && candidate && effectiveEnabled() &&
             ['preparing', 'playing', 'done', 'stopped', 'error'].includes(message.state)) playbackFeedback(message.state, typeof message.message === 'string' ? message.message : '');
  };
  window.addEventListener('message', onMessage);
  document.addEventListener('keydown', event => {
    if (embedded && event.key === 'Escape' && !event.isComposing && !composing && document.body.classList.contains('notes-writing-mode')) {
      event.preventDefault(); parentMessage({ type: 'aqi-editor-exit' });
    }
  });
  const onPageHide = () => { disposed = true; enabled = false; statusGeneration++; invalidate(); void syncState(); };
  window.addEventListener('pagehide', onPageHide);
  parentMessage({ type: 'aqi-editor-ready' });
  return { changed: () => invalidate(), region };
}
