import test from 'node:test';
import assert from 'node:assert/strict';
// Data-URL loading works without altering the project's package.json module type.
import { readFile } from 'node:fs/promises';
const load = async name => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(name, import.meta.url))).toString('base64')}`);
const { StreamingResampler, SampleFramer, pcm16 } = await load('./pcm.js');
const { EnergyVad } = await load('./vad.js');
const { PlaybackQueue } = await load('./playback.js');
const { TurnGate } = await load('./turns.js');
const { MicrophoneLease } = await load('./microphone.js');
const tick = () => new Promise(resolve => setImmediate(resolve));
const frame = (value = 0) => new Float32Array(320).fill(value);

test('4096 callback consumes its entire second half', () => {
  const r = new StreamingResampler(48000);
  const input = new Float32Array(4096); input.fill(1, 2048);
  const out = r.push(input);
  assert.equal(out.length, 1365);
  assert.equal(out.at(-1), 1);
  assert.equal(r.flush().length, 1);
});
test('fractional resampling preserves phase and all sample contributions', () => {
  for (const rate of [44100, 48000, 16000, 8000]) {
    const data = Float32Array.from({ length: 18001 }, (_, i) => Math.sin(i / 40));
    const full = new StreamingResampler(rate);
    const expected = [...full.push(data), ...full.flush()];
    const split = new StreamingResampler(rate); const actual = [];
    for (let i = 0; i < data.length; i += 4096) actual.push(...split.push(data.slice(i, i + 4096)));
    actual.push(...split.flush());
    assert.deepEqual(actual, expected);
    assert.equal(actual.length, Math.ceil(data.length * 16000 / rate));
  }
});
test('framing preserves the tail; PCM is little endian and clipped', () => {
  const f = new SampleFramer(3);
  assert.deepEqual(f.push(Float32Array.of(1, 2)), []);
  assert.deepEqual([...f.push(Float32Array.of(3, 4))[0]], [1, 2, 3]);
  assert.deepEqual([...f.flush()], [4]);
  const view = new DataView(pcm16(Float32Array.of(-2, 0, 2)));
  assert.equal(view.getInt16(0, true), -32768); assert.equal(view.getInt16(4, true), 32767);
});
test('silence stays local; onset includes pre-roll and pauses are conservative', () => {
  const v = new EnergyVad();
  for (let i = 0; i < 3000; i++) assert.equal(v.push(frame()).frames, undefined);
  for (let i = 0; i < 7; i++) assert.equal(v.push(frame(.06)).start, undefined);
  const start = v.push(frame(.06));
  assert.equal(start.start, true); assert.equal(start.frames.length, 20);
  for (let i = 0; i < 5; i++) v.push(frame(.06));
  for (let i = 0; i < 44; i++) assert.equal(v.push(frame()).end, false);
  const end = v.push(frame()); assert.equal(end.end, true); assert.equal(end.valid, true);
});
test('playback requires stronger and longer onset, not semantic recognition', () => {
  const v = new EnergyVad();
  for (let i = 0; i < 100; i++) assert.equal(v.push(frame(.025), true).start, undefined);
  for (let i = 0; i < 14; i++) assert.equal(v.push(frame(.08), true).start, undefined);
  assert.equal(v.push(frame(.08), true).start, true);
});
test('short transient is invalid; max duration waits for quiet before rearming', () => {
  const v = new EnergyVad({ maxMs: 1000 });
  for (let i = 0; i < 8; i++) v.push(frame(.06));
  let ended;
  for (let i = 0; i < 45; i++) { const e = v.push(frame()); if (e.end) ended = e; }
  assert.equal(ended.valid, false);
  v.reset();
  for (let i = 0; i < 50; i++) { const e = v.push(frame(.06)); if (e.limit) ended = e; }
  assert.equal(ended.limit, true);
  for (let i = 0; i < 100; i++) assert.equal(v.push(frame(.06)).start, undefined);
  for (let i = 0; i < 45; i++) v.push(frame());
  for (let i = 0; i < 7; i++) v.push(frame(.06));
  assert.equal(v.push(frame(.06)).start, true);
});
test('decode and play preserve server order and idle follows real ending', async () => {
  const decoded = [], played = [], ends = []; let idle = 0;
  const q = new PlaybackQueue({ decode: async x => { decoded.push(x); return x; },
    play: (b, _, end) => { played.push(b); ends.push(end); return () => {}; }, onIdle: () => idle++ });
  q.enqueue(1); q.enqueue(2); await tick();
  assert.deepEqual(decoded, [1, 2]); assert.deepEqual(played, [1]); assert.equal(idle, 0);
  ends.shift()(); await tick(); assert.deepEqual(played, [1, 2]);
  ends.shift()(); await tick(); assert.equal(q.busy, false); assert.equal(idle, 1);
});
test('late decode and ended after stop cannot revive or disturb a new generation', async () => {
  let resolveOld; const played = []; let newEnd; let idle = 0;
  const q = new PlaybackQueue({ decode: x => x === 'old' ? new Promise(r => resolveOld = r) : Promise.resolve(x),
    play: (b, _, end) => { played.push(b); newEnd = end; return () => {}; }, onIdle: () => idle++ });
  q.enqueue('old'); q.stop(); q.enqueue('new'); await tick();
  resolveOld('old'); await tick(); assert.deepEqual(played, ['new']); assert.equal(q.busy, true);
  const staleEnd = newEnd; q.stop(); q.enqueue('newer'); await tick();
  staleEnd(); await tick(); assert.equal(q.busy, true); assert.equal(idle, 0);
  newEnd(); await tick(); assert.equal(idle, 1);
});
test('bad audio does not hang following chunks', async () => {
  let errors = 0, idle = 0; const played = [];
  const q = new PlaybackQueue({ decode: async x => { if (!x) throw Error('decode'); return x; },
    play: (b, _, done) => { played.push(b); done(); }, onError: () => errors++, onIdle: () => idle++ });
  q.enqueue(0); q.enqueue(1); await tick();
  assert.equal(errors, 1); assert.deepEqual(played, [1]); assert.equal(idle, 1);
});
test('permission release and rapid reopen dispose only obsolete tracks', async () => {
  const requests = []; const stopped = [];
  const lease = new MicrophoneLease(() => new Promise(resolve => requests.push(resolve)));
  const old = lease.open(); lease.close(); const fresh = lease.open();
  const stream = name => ({ getTracks: () => [{ stop: () => stopped.push(name) }] });
  requests[0](stream('old')); assert.equal(await old, null);
  requests[1](stream('fresh')); const acquired = await fresh;
  assert.equal(await lease.open(), acquired); assert.deepEqual(stopped, ['old']);
  lease.close(); assert.deepEqual(stopped, ['old', 'fresh']);
});
test('denied permission does not prevent a later explicit retry', async () => {
  let attempts = 0;
  const lease = new MicrophoneLease(async () => { if (!attempts++) throw Error('denied'); return { getTracks: () => [] }; });
  await assert.rejects(lease.open(), /denied/);
  assert.ok(await lease.open()); lease.close();
});
test('all stale tagged events are blocked before consumers; proactive only when idle', () => {
  const gate = new TurnGate(); gate.begin('client-a'); gate.begin('client-b');
  for (const type of ['audio', 'done', 'emotion', 'error', 'assistant_result', 'memory_notice']) {
    assert.equal(gate.accept({ type, turn_id: 'client-a' }), false);
    assert.equal(gate.accept({ type }), false);
    assert.equal(gate.accept({ type, turn_id: 'client-b' }), true);
  }
  assert.equal(gate.accept({ type: 'turn_started', turn_id: 'server-a' }, true), false);
  gate.clear(); assert.equal(gate.accept({ type: 'turn_started', turn_id: 'server-a' }, true), false);
  assert.equal(gate.accept({ type: 'turn_started', turn_id: 'server-b' }, false), false);
  assert.equal(gate.accept({ type: 'turn_started', turn_id: 'server-c' }, true), true);
  assert.equal(gate.id, 'server-c');
});
test('config acknowledgments remain global and retired IDs are bounded', () => {
  const gate = new TurnGate(); gate.begin('active');
  assert.equal(gate.accept({ type: 'config_ack' }), true);
  for (let i = 0; i < 1000; i++) gate.accept({ type: 'turn_started', turn_id: `ignored-${i}` }, false);
  assert.equal(gate.retired.size, 256);
  assert.equal(gate.id, 'active');
});
test('stuck decode times out; bounded queue rejects overload without hanging', async () => {
  let errors = 0; const played = [];
  let idle;
  const complete = new Promise(resolve => idle = resolve);
  const q = new PlaybackQueue({ maxItems: 1, decodeTimeoutMs: 10,
    decode: x => x === 0 ? new Promise(() => {}) : Promise.resolve(x),
    play: (buffer, _, done) => { played.push(buffer); done(); },
    onError: () => errors++, onIdle: idle });
  q.enqueue(0); assert.equal(q.enqueue(1), true); assert.equal(q.enqueue(2), false);
  await complete;
  assert.equal(errors, 2); assert.deepEqual(played, [1]); assert.equal(q.busy, false);
});
test('missing onended times out and stops the source', async () => {
  let stopped = 0, errors = 0, idle;
  const complete = new Promise(resolve => idle = resolve);
  const q = new PlaybackQueue({ playTimeoutMs: 10, decode: async () => ({ duration: .01 }),
    play: () => () => stopped++, onError: () => errors++, onIdle: idle });
  q.enqueue('audio'); await complete;
  assert.equal(stopped, 1); assert.equal(errors, 1); assert.equal(q.busy, false);
});
