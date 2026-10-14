// Editor speech has its own local turn. It never submits a conversation turn.
const MAX_AUDIO = 12 * 1024 * 1024;
const GLOBAL_MESSAGES = new Set(['config_ack', 'config', 'pong']);
const identifier = value => typeof value === 'string' && value.length > 0 && value.length <= 256;
const revision = value => Number.isSafeInteger(value) && value >= 0;

export function mountEditorInsightVoice({
  target = window, origin = location.origin, fetchAudio = (...args) => fetch(...args),
  getFrame = () => document.querySelector('iframe[title="个人笔记编辑器"]'),
  available = () => !document.hidden, ensureAudio, beginLocal, enqueueAudio, stopLocal,
  retireServerTurn = () => {}, timers = globalThis, timeoutMs = 45000,
}) {
  let current = null;
  let disposed = false;

  function post(item, state, message) {
    try {
      item.source.postMessage({ type: 'aqi-editor-insight-playback',
        request_id: item.request_id, state, ...(message ? { message } : {}) }, origin);
    } catch (_) { /* The editor may already have closed. */ }
  }
  function trusted(event) {
    const frame = getFrame();
    return event.origin === origin && !!frame?.contentWindow && event.source === frame.contentWindow;
  }
  function frameVisible(frame = getFrame()) {
    return !!frame && !frame.closest?.('[hidden]') &&
      (!frame.getClientRects || frame.getClientRects().length > 0);
  }
  function live(item) {
    return current === item && !disposed && !item.controller.signal.aborted && available() &&
      getFrame()?.contentWindow === item.source && frameVisible();
  }
  function retire(item, state, message, stop = true) {
    if (current !== item) return false;
    current = null;
    timers.clearTimeout(item.timer);
    item.controller.abort();
    // Drop ownership before stopping the queue; an idle/error callback cannot revive it.
    if (stop) stopLocal(item.turn_id);
    post(item, state, message);
    return true;
  }
  function cancel(message) {
    if (!current) return false;
    return retire(current, 'stopped', message);
  }
  function fail(error) {
    if (!current) return false;
    return retire(current, 'error', error?.message || '这条观点暂时无法播报，请阅读文字。');
  }
  async function play(event) {
    const data = event.data;
    if (!identifier(data.request_id) || !identifier(data.insight_id) ||
        !identifier(data.session_id) || !revision(data.draft_revision)) return;
    if (current?.request_id === data.request_id && current.source === event.source) return;
    cancel();
    const item = { ...data, source: event.source, controller: new AbortController(), turn_id: null,
      state: 'preparing', timer: null };
    if (disposed || !available() || !frameVisible()) {
      post(item, 'stopped'); return;
    }
    try {
      // Run during the same-origin click's activation, before awaiting HTTP.
      ensureAudio();
      item.turn_id = beginLocal();
      current = item;
      post(item, 'preparing');
      item.timer = timers.setTimeout(() => {
        if (current === item) retire(item, 'error', '播报等待超时，请稍后再听。');
      }, timeoutMs);
      const response = await fetchAudio(`/api/editor-insights/${encodeURIComponent(data.insight_id)}/speak`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: data.session_id, draft_revision: data.draft_revision }),
        signal: item.controller.signal,
      });
      if (!live(item)) { if (current === item) cancel(); return; }
      if (!response.ok) {
        const message = [404, 409].includes(response.status)
          ? '这条观点已失效，继续编辑后会重新看看。' : '这条观点暂时无法播报，请阅读文字。';
        throw new Error(message);
      }
      const result = await response.json();
      if (!live(item)) { if (current === item) cancel(); return; }
      if (result?.id !== data.insight_id || result.draft_revision !== data.draft_revision ||
          typeof result.audio !== 'string' || !result.audio || result.audio.length > MAX_AUDIO ||
          typeof result.text !== 'string' || !result.text || result.text.length > 2000 ||
          !['mp3', 'wav', 'ogg', 'webm', 'aac', 'flac', 'pcm'].includes(result.format)) {
        throw new Error('播报内容不完整，请阅读文字。');
      }
      timers.clearTimeout(item.timer);
      item.timer = null;
      if (enqueueAudio({ type: 'audio', turn_id: item.turn_id, data: result.audio,
        format: result.format, text: result.text, emotion: 'base', editorInsight: true }) === false) {
        if (current === item) throw new Error('播报没有开始，请阅读文字。');
      }
    } catch (error) {
      if (current === item) retire(item, 'error', error?.message || '这条观点暂时无法播报，请阅读文字。');
      else if (!item.turn_id) post(item, 'error', error?.message || '无法启用播放。');
    }
  }
  function onMessage(event) {
    if (!trusted(event) || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'aqi-editor-insight-play') void play(event);
    if (data.type === 'aqi-editor-insight-cancel' && current &&
        data.session_id === current.session_id && revision(data.draft_revision) &&
        data.draft_revision >= current.draft_revision) cancel();
  }
  target.addEventListener('message', onMessage);
  return {
    get active() { return !!current; },
    ownsTurn: id => !!current && current.turn_id === id,
    cancel, fail,
    playing(id) {
      if (!current || current.turn_id !== id || current.state === 'playing') return;
      current.state = 'playing'; post(current, 'playing');
    },
    finish(id) {
      if (!current || current.turn_id !== id) return false;
      return retire(current, 'done', undefined, false);
    },
    blocksServer(message) {
      if (!current || (message.turn_id == null && GLOBAL_MESSAGES.has(message.type))) return false;
      if (message.type === 'turn_started' && message.turn_id && message.turn_id !== current.turn_id) {
        retireServerTurn(message.turn_id);
      }
      return true;
    },
    dispose() { disposed = true; cancel(); target.removeEventListener('message', onMessage); },
  };
}
