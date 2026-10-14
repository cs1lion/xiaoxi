import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const { mountEditorInsightVoice } = await import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL('./editor-insight.js', import.meta.url))).toString('base64')}`);
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };
const good = { id: 'point-1', draft_revision: 3, text: '这里缺少支持结论的例子。', audio: 'dGVzdA==', format: 'wav' };
const play = { type: 'aqi-editor-insight-play', request_id: 'click-1', insight_id: 'point-1',
  session_id: 'editor-1', draft_revision: 3 };
function harness() {
  const listeners = new Map(), sent = [], requests = [], stops = [], audio = [], retired = [];
  const source = { postMessage: (data, origin) => sent.push({ ...data, origin }) };
  let frame = { contentWindow: source, closest: () => null, getClientRects: () => [{}] };
  let available = true, next = 0, unlocks = 0;
  const target = { addEventListener: (type, fn) => listeners.set(type, fn),
    removeEventListener: type => listeners.delete(type) };
  const timers = { next: 0, tasks: new Map(), setTimeout(fn) { const id = ++this.next; this.tasks.set(id, fn); return id; },
    clearTimeout(id) { this.tasks.delete(id); } };
  const voice = mountEditorInsightVoice({ target, origin: 'http://editor.test', getFrame: () => frame,
    available: () => available, ensureAudio: () => { unlocks++; }, beginLocal: () => `local-${++next}`,
    enqueueAudio: item => { audio.push(item); return true; }, stopLocal: id => stops.push(id),
    retireServerTurn: id => retired.push(id), timers,
    fetchAudio: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve, reject })),
  });
  return { voice, sent, requests, stops, audio, retired, timers,
    get unlocks() { return unlocks; }, get listeners() { return listeners; },
    hidden: value => { available = !value; }, replaceFrame: () => { frame = { contentWindow: {} }; },
    emit: (data = play, overrides = {}) => listeners.get('message')?.({ origin: 'http://editor.test', source, data, ...overrides }),
    resolve: (index = 0, result = good, status = 200) => requests[index].resolve({ ok: status === 200, status,
      json: async () => result }),
  };
}

test('only the exact same-origin notes iframe can request speech', async () => {
  const h = harness();
  h.emit(play, { source: {} }); h.emit(play, { origin: 'http://other.test' });
  h.emit({ ...play, draft_revision: -1 }); h.emit({ ...play, insight_id: '' });
  assert.equal(h.requests.length, 0); assert.equal(h.unlocks, 0);
  h.emit(); assert.equal(h.requests.length, 1); assert.equal(h.unlocks, 1);
  assert.deepEqual(JSON.parse(h.requests[0].options.body), { session_id: 'editor-1', draft_revision: 3 });
  assert.equal(h.requests[0].url, '/api/editor-insights/point-1/speak');
  h.resolve(); await flush();
  assert.deepEqual(h.audio.map(a => [a.text, a.turn_id, a.emotion]), [[good.text, 'local-1', 'base']]);
  assert.deepEqual(h.sent.map(s => s.state), ['preparing']);
  h.voice.playing('local-1'); h.voice.playing('local-1'); h.voice.finish('local-1');
  assert.deepEqual(h.sent.map(s => s.state), ['preparing', 'playing', 'done']);
  assert.equal(h.stops.length, 0);
});

test('draft cancellation aborts HTTP and fences responses that ignore abort', async () => {
  const h = harness(); h.emit();
  h.emit({ type: 'aqi-editor-insight-cancel', session_id: 'wrong', draft_revision: 4 });
  h.emit({ type: 'aqi-editor-insight-cancel', session_id: 'editor-1', draft_revision: 2 });
  assert.equal(h.voice.active, true);
  h.emit({ type: 'aqi-editor-insight-cancel', session_id: 'editor-1', draft_revision: 4 });
  assert.equal(h.requests[0].options.signal.aborted, true);
  h.resolve(); await flush(); assert.equal(h.audio.length, 0);
  assert.deepEqual(h.sent.map(s => s.state), ['preparing', 'stopped']);
  assert.deepEqual(h.stops, ['local-1']);
});

test('a second click replaces the local turn and duplicate click does not request twice', async () => {
  const h = harness(); h.emit(); h.emit(); assert.equal(h.requests.length, 1);
  h.emit({ ...play, request_id: 'click-2', insight_id: 'point-2' });
  assert.equal(h.requests[0].options.signal.aborted, true);
  h.resolve(); h.resolve(1, { ...good, id: 'point-2' }); await flush();
  assert.deepEqual(h.audio.map(a => a.turn_id), ['local-2']);
  assert.deepEqual(h.sent.map(s => [s.request_id, s.state]), [
    ['click-1', 'preparing'], ['click-1', 'stopped'], ['click-2', 'preparing']]);
  h.voice.cancel();
});

test('ordinary stop and delayed JSON cannot revive local audio', async () => {
  const h = harness(); let release;
  h.emit(); h.requests[0].resolve({ ok: true, status: 200, json: () => new Promise(resolve => { release = resolve; }) });
  await flush(); h.voice.cancel(); release(good); await flush();
  assert.equal(h.audio.length, 0); assert.equal(h.voice.active, false);
});

test('local turn ignores all server dialogue messages including guessed matching turn id', () => {
  const h = harness(); h.emit();
  for (const type of ['audio', 'done', 'thinking', 'interrupted', 'error', 'ready', 'memory_notice']) {
    assert.equal(h.voice.blocksServer({ type, turn_id: 'local-1' }), true);
    assert.equal(h.voice.blocksServer({ type }), true);
  }
  assert.equal(h.voice.blocksServer({ type: 'config_ack' }), false);
  assert.equal(h.voice.blocksServer({ type: 'config_ack', turn_id: 'other' }), true);
  assert.equal(h.voice.blocksServer({ type: 'turn_started', turn_id: 'proactive-1' }), true);
  assert.deepEqual(h.retired, ['proactive-1']);
  h.voice.cancel(); assert.equal(h.voice.blocksServer({ type: 'done' }), false);
});

test('expired and revision-conflicted points show a failure without audio', async () => {
  for (const status of [404, 409, 502, 504]) {
    const h = harness(); h.emit(); h.resolve(0, {}, status); await flush();
    assert.equal(h.audio.length, 0); assert.equal(h.sent.at(-1).state, 'error');
    assert.ok(h.sent.at(-1).message); assert.equal(h.voice.active, false);
  }
});

test('response identity, audio size and format are validated', async () => {
  for (const result of [{ ...good, id: 'other' }, { ...good, draft_revision: 4 },
    { ...good, audio: '' }, { ...good, audio: 'a'.repeat(12 * 1024 * 1024 + 1) },
    { ...good, format: 'invalid' }, { ...good, text: '' }]) {
    const h = harness(); h.emit(); h.resolve(0, result); await flush();
    assert.equal(h.audio.length, 0); assert.equal(h.sent.at(-1).state, 'error');
  }
});

test('hidden editor and replaced iframe cannot play delayed audio', async () => {
  const hidden = harness(); hidden.hidden(true); hidden.emit();
  assert.equal(hidden.requests.length, 0); assert.equal(hidden.sent.at(-1).state, 'stopped');
  const h = harness(); h.emit(); h.replaceFrame(); h.resolve(); await flush();
  assert.equal(h.audio.length, 0); assert.equal(h.sent.at(-1).state, 'stopped');
});

test('HTTP timeout, playback failure and disposal abort local work exactly once', async () => {
  const h = harness(); h.emit(); [...h.timers.tasks.values()][0]();
  h.resolve(); await flush(); assert.equal(h.audio.length, 0);
  assert.equal(h.sent.at(-1).state, 'error'); assert.deepEqual(h.stops, ['local-1']);
  h.emit({ ...play, request_id: 'click-2' }); h.voice.fail(new Error('decode failed'));
  assert.equal(h.sent.at(-1).message, 'decode failed'); assert.equal(h.voice.active, false);
  h.emit({ ...play, request_id: 'click-3' }); h.voice.dispose();
  assert.equal(h.requests[2].options.signal.aborted, true); assert.equal(h.listeners.size, 0);
  assert.deepEqual(h.stops, ['local-1', 'local-2', 'local-3']);
});
