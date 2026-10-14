// A chosen observation scopes explicit follow-up turns until the user ends it.
const id = value => typeof value === 'string' && value.length > 0 && value.length <= 100;
const rev = value => Number.isSafeInteger(value) && value >= 0;

export function mountEditorDiscussion({
  target = window, origin = location.origin,
  getFrame = () => document.querySelector('iframe[title="个人笔记编辑器"]'),
  available = () => !document.hidden, now = () => Date.now(), timers = globalThis,
  onStart = () => {}, onChange = () => {}, onInvalidate = () => {}, onEnd = () => {},
}) {
  let current = null, expiry = null, disposed = false;
  const same = value => current && value?.insight_id === current.insight_id &&
    value.session_id === current.session_id && value.draft_revision === current.draft_revision;
  const publicContext = () => { if (!current) return null; const { source, ...value } = current; return value; };
  function post(active, message) {
    try { current?.source.postMessage({ type: 'aqi-editor-insight-discussion', active,
      insight_id: current.insight_id, session_id: current.session_id,
      draft_revision: current.draft_revision, ...(message ? { message } : {}) }, origin); } catch (_) {}
  }
  function invalidate(message = '稿子已经变化，回到稿子后再选择观点讨论。') {
    if (!current || current.invalid) return false;
    current.invalid = message;
    timers.clearTimeout(expiry); expiry = null;
    post(false, message);
    onInvalidate(publicContext()); onChange(publicContext());
    return true;
  }
  function end() {
    if (!current) return false;
    post(false);
    const previous = publicContext();
    current = null; timers.clearTimeout(expiry); expiry = null;
    onEnd(previous); onChange(null);
    return true;
  }
  function onMessage(event) {
    const frame = getFrame();
    if (disposed || event.origin !== origin || !frame?.contentWindow ||
        event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'aqi-editor-insight-cancel') {
      if (data.preserve_discussion === true && data.reason === 'handoff') return;
      if (current && data.session_id === current.session_id && rev(data.draft_revision) &&
          data.draft_revision >= current.draft_revision) invalidate();
      return;
    }
    if (data.type !== 'aqi-editor-insight-discuss' || !id(data.insight_id) || !id(data.session_id) ||
        !rev(data.draft_revision) || typeof data.point !== 'string' || !data.point || data.point.length > 800 ||
        typeof data.anchor !== 'string' || !data.anchor || data.anchor.length > 120 ||
        !Number.isFinite(data.expires_at) || data.expires_at * 1000 <= now()) return;
    if (!available() || frame.closest?.('[hidden]') ||
        (frame.getClientRects && frame.getClientRects().length === 0)) {
      event.source.postMessage({ type: 'aqi-editor-insight-discussion', active: false,
        insight_id: data.insight_id, session_id: data.session_id, draft_revision: data.draft_revision,
        message: '连接尚未就绪，请稍后再接着聊。' }, origin);
      return;
    }
    if (same(data) && !current.invalid) { post(true); onStart(publicContext()); return; }
    end();
    current = { insight_id: data.insight_id, session_id: data.session_id,
      draft_revision: data.draft_revision, point: data.point, anchor: data.anchor,
      expires_at: data.expires_at, invalid: '', source: event.source };
    post(true);
    expiry = timers.setTimeout(() => invalidate('刚才的观点已过期，回到稿子后再看看。'),
      Math.min(2147483647, data.expires_at * 1000 - now()));
    onChange(publicContext()); onStart(publicContext());
  }
  target.addEventListener('message', onMessage);
  return {
    get context() { return publicContext(); },
    requestContext() {
      if (!current) return null;
      if (getFrame()?.contentWindow !== current.source) invalidate('编辑器已重新打开，请重新选择观点。');
      if (current.expires_at * 1000 <= now()) invalidate('刚才的观点已过期，回到稿子后再看看。');
      if (current.invalid) throw new Error(current.invalid);
      return { insight_id: current.insight_id, session_id: current.session_id, draft_revision: current.draft_revision };
    },
    acceptInsight(insight) {
      if (!same({ insight_id: insight?.id, session_id: insight?.session_id,
                   draft_revision: insight?.draft_revision }) || current.invalid) return false;
      if (typeof insight.point !== 'string' || typeof insight.anchor !== 'string') return false;
      current.point = insight.point; current.anchor = insight.anchor;
      onChange(publicContext()); return true;
    },
    invalidate, end,
    dispose() { disposed = true; end(); target.removeEventListener('message', onMessage); },
  };
}
