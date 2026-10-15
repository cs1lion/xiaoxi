import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const load = async name => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(name, import.meta.url))).toString('base64')}`);
const { PlaybackQueue } = await load('./playback.js');
const { PlaybackCheckpoint } = await load('./checkpoint.js');
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

class FakeClock {
  now = 0; next = 0; tasks = new Map();
  setTimeout = (run, delay) => { const id = ++this.next; this.tasks.set(id, { at: this.now + delay, run }); return id; };
  clearTimeout = id => this.tasks.delete(id);
  async advance(ms) {
    const target = this.now + ms;
    while (true) {
      const entry = [...this.tasks].sort((a, b) => a[1].at - b[1].at).find(([, task]) => task.at <= target);
      if (!entry) break;
      const [id, task] = entry; this.tasks.delete(id); this.now = task.at; task.run(); await flush();
    }
    this.now = target; await flush();
  }
}
class FakeAudioContext {
  currentTime = 0; calls = []; decodes = new Map(); sources = []; peakDecode = 0;
  decodeAudioData(id) {
    this.calls.push(id);
    const promise = new Promise((resolve, reject) => this.decodes.set(id, { resolve, reject }));
    this.peakDecode = Math.max(this.peakDecode, this.decodes.size);
    return promise;
  }
  decoded(id, buffer = { id, duration: 4, length: 16, numberOfChannels: 1 }) {
    const operation = this.decodes.get(id); this.decodes.delete(id); operation.resolve(buffer); return buffer;
  }
  rejected(id) { const operation = this.decodes.get(id); this.decodes.delete(id); operation.reject(Error('bad audio')); }
  createBufferSource() {
    const source = { stopped: false, onended: null,
      start: (at, offset) => { source.startedAt = at; source.offset = offset; source.originalEnd = source.onended; },
      stop: () => { source.stopped = true; },
      end: () => source.onended?.(),
    };
    this.sources.push(source); return source;
  }
}
function setup(options = {}) {
  const context = new FakeAudioContext(), clock = new FakeClock(), errors = [];
  let idle = 0;
  const queue = new PlaybackQueue({ timers: clock,
    decode: item => item.buffer || context.decodeAudioData(item.id),
    play: (buffer, item, done) => {
      const source = context.createBufferSource(); source.buffer = buffer; source.onended = done;
      source.start(context.currentTime, item.offset || 0);
      return () => { source.onended = null; source.stop(); };
    },
    onError: error => errors.push(error), onIdle: () => idle++, ...options });
  return { queue, context, clock, errors, get idle() { return idle; } };
}

test('one next chunk is decoded before playback ends, without decoding the whole queue', async () => {
  const f = setup();
  for (const id of [1, 2, 3, 4]) f.queue.enqueue({ id });
  assert.deepEqual(f.context.calls, [1]);
  f.context.decoded(1); await flush();
  assert.deepEqual(f.context.calls, [1, 2]); assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1]);
  f.context.decoded(2); await flush();
  assert.deepEqual(f.context.calls, [1, 2]); assert.equal(f.idle, 0);
  f.context.sources[0].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2]); assert.deepEqual(f.context.calls, [1, 2, 3]);
  f.context.decoded(3); await flush(); f.context.sources[1].end(); await flush();
  f.context.decoded(4); await flush(); f.context.sources[2].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2, 3, 4]); assert.equal(f.idle, 0);
  f.context.sources[3].end(); await flush();
  assert.equal(f.idle, 1); assert.equal(f.queue.busy, false); assert.equal(f.clock.tasks.size, 0);
});

test('stop drops prefetched audio; late decoding and stale onended cannot revive it', async () => {
  const f = setup(); f.queue.enqueue({ id: 1 }); f.queue.enqueue({ id: 2 });
  f.context.decoded(1); await flush(); const staleEnd = f.context.sources[0].originalEnd;
  f.queue.stop(); assert.equal(f.context.sources[0].stopped, true);
  f.queue.enqueue({ id: 3 }); f.context.decoded(3); await flush();
  f.context.decoded(2); staleEnd(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 3]); assert.equal(f.queue.busy, true); assert.equal(f.idle, 0);
  f.context.sources[1].end(); await flush(); assert.equal(f.idle, 1); assert.equal(f.errors.length, 0);
});

test('uncancellable decodes remain bounded across rapid stops and slot waiting times out', async () => {
  const f = setup({ maxDecode: 2, decodeTimeoutMs: 10 });
  f.queue.enqueue({ id: 1 }); f.queue.stop(); f.queue.enqueue({ id: 2 }); f.queue.stop();
  f.queue.enqueue({ id: 3 }); assert.deepEqual(f.context.calls, [1, 2]);
  await f.clock.advance(10);
  assert.equal(f.errors.length, 1); assert.equal(f.queue.busy, false); assert.equal(f.idle, 1);
  f.context.decoded(1); await flush();
  assert.deepEqual(f.context.calls, [1, 2]);
  f.queue.enqueue({ id: 4 }); f.context.decoded(4); await flush();
  f.context.rejected(2); await flush();
  assert.equal(f.context.peakDecode, 2); assert.deepEqual(f.context.sources.map(s => s.buffer.id), [4]);
  f.context.sources[0].end(); await flush(); assert.equal(f.errors.length, 1); assert.equal(f.idle, 2);
});

test('checkpoint resumes only remaining current audio, never a prefetched future sentence', async () => {
  const f = setup(); const checkpoint = new PlaybackCheckpoint({ clock: () => f.clock.now });
  f.queue.enqueue({ id: 1, text: 'first' }); f.queue.enqueue({ id: 2, text: 'future' });
  const buffer = f.context.decoded(1); await flush(); f.context.decoded(2); await flush();
  f.context.currentTime = 1.5;
  checkpoint.save({ buffer, offset: f.context.currentTime, text: 'first' });
  f.queue.stop(); const staleEnd = f.context.sources[0].originalEnd;
  f.queue.enqueue({ ...checkpoint.take(), resumed: true }); await flush(); staleEnd(); await flush();
  assert.deepEqual(f.context.calls, [1, 2]);
  assert.deepEqual(f.context.sources.map(s => [s.buffer.id, s.offset]), [[1, 0], [1, 1.5]]);
  assert.equal(checkpoint.available, false); assert.equal(f.queue.busy, true);
  f.context.sources[1].end(); await flush(); assert.equal(f.idle, 1);
});

test('prefetch failure is skipped in order and following chunks continue', async () => {
  const f = setup(); for (const id of [1, 2, 3]) f.queue.enqueue({ id });
  f.context.decoded(1); await flush(); f.context.rejected(2); await flush();
  assert.equal(f.errors.length, 0); f.context.sources[0].end(); await flush();
  assert.equal(f.errors.length, 1); f.context.decoded(3); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 3]);
  f.context.sources[1].end(); await flush(); assert.equal(f.idle, 1);
});

test('decode timeout skips a stalled chunk and play timeout stops a source before continuing', async () => {
  const f = setup({ decodeTimeoutMs: 10, playTimeoutMs: 20 });
  for (const id of [1, 2, 3]) f.queue.enqueue({ id });
  await f.clock.advance(10); assert.deepEqual(f.context.calls, [1, 2]);
  f.context.decoded(2); await flush(); f.context.decoded(3); await flush();
  await f.clock.advance(20);
  assert.equal(f.context.sources[0].stopped, true); assert.deepEqual(f.context.sources.map(s => s.buffer.id), [2, 3]);
  f.context.decoded(1); await flush(); f.context.sources[1].end(); await flush();
  assert.equal(f.errors.length, 2); assert.equal(f.idle, 1); assert.equal(f.clock.tasks.size, 0);
});

test('pending item, encoded/raw byte and aggregate limits reject before decoding', async () => {
  const f = setup({ maxPending: 1, maxAudioBytes: 4, maxEncodedBytes: 8, maxPendingBytes: 6 });
  assert.equal(f.queue.enqueue({ id: 1, data: 'AAAA' }), true);
  assert.equal(f.queue.enqueue({ id: 2, data: 'AAAA' }), true);
  assert.equal(f.queue.enqueue({ id: 3, data: 'AAAA' }), false);
  assert.equal(f.queue.enqueue({ id: 4, data: new Uint8Array(5) }), false);
  assert.equal(f.queue.enqueue({ id: 5, data: 'A'.repeat(9) }), false);
  assert.deepEqual(f.context.calls, [1]); assert.equal(f.queue.pendingBytes, 6);
  f.queue.stop(); assert.equal(f.queue.pendingBytes, 0); f.context.decoded(1); await flush();
  assert.equal(f.errors.length, 3); assert.equal(f.clock.tasks.size, 0);
});

test('decoded byte limit skips expanded audio without retaining or playing it', async () => {
  const f = setup({ maxDecodedBytes: 16 });
  f.queue.enqueue({ id: 1 }); f.queue.enqueue({ id: 2 });
  f.context.decoded(1, { id: 1, duration: 4, length: 8, numberOfChannels: 1 }); await flush();
  f.context.decoded(2, { id: 2, duration: 4, length: 4, numberOfChannels: 1 }); await flush();
  assert.equal(f.errors.length, 1); assert.deepEqual(f.context.sources.map(s => s.buffer.id), [2]);
  f.context.sources[0].end(); await flush(); assert.equal(f.idle, 1);
});

test('reentrant stop during play still disposes the cleanup returned afterwards', async () => {
  const clock = new FakeClock(); let stopped = 0, queue;
  queue = new PlaybackQueue({ timers: clock, decode: async item => item,
    play: () => { queue.stop(); return () => stopped++; } });
  queue.enqueue({ duration: 1 }); await flush();
  assert.equal(stopped, 1); assert.equal(queue.busy, false); assert.equal(clock.tasks.size, 0);
});

test('chunk pause_after_ms spaces the next chunk on the injected clock', async () => {
  const f = setup();
  f.queue.enqueue({ id: 1, chunk: { seq: 0, of: 2, chars: 7, pause_after_ms: 260, breath: true } });
  f.queue.enqueue({ id: 2, chunk: { seq: 1, of: 2, chars: 9, pause_after_ms: 120, breath: false } });
  f.context.decoded(1); await flush();
  f.context.decoded(2); await flush();
  f.context.sources[0].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1]);
  assert.equal(f.queue.busy, true, 'a broadcast stays busy through its breath pause');
  await f.clock.advance(200);
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1], 'the pause is not over yet');
  await f.clock.advance(60);
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2], 'the next chunk starts after the pause');
  f.context.sources[1].end(); await flush();
  await f.clock.advance(120);
  assert.equal(f.idle, 1, 'the final pause settles before the queue goes idle');
  assert.equal(f.queue.busy, false); assert.equal(f.clock.tasks.size, 0);
});

test('stop during a breath pause cancels it and never starts the queued chunk', async () => {
  const f = setup();
  f.queue.enqueue({ id: 1, chunk: { pause_after_ms: 300 } });
  f.queue.enqueue({ id: 2 });
  f.context.decoded(1); await flush();
  f.context.sources[0].end(); await flush();
  assert.equal(f.queue.busy, true);
  f.queue.stop();
  assert.equal(f.queue.busy, false);
  await f.clock.advance(500); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1]);
  assert.equal(f.idle, 0, 'a stopped broadcast never reports idle twice');
  f.queue.enqueue({ id: 3 }); f.context.decoded(3); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 3]);
  f.context.sources[1].end(); await flush();
  assert.equal(f.idle, 1); assert.equal(f.errors.length, 0); assert.equal(f.clock.tasks.size, 0);
});

test('items without a usable pause never wait, keeping the legacy schedule', async () => {
  const f = setup();
  for (const item of [{ id: 1 }, { id: 2, chunk: null }, { id: 3, chunk: { pause_after_ms: 0 } },
                      { id: 4, chunk: { pause_after_ms: -5 } }, { id: 5, chunk: { pause_after_ms: 'bad' } }]) {
    f.queue.enqueue(item);
  }
  f.context.decoded(1); await flush();
  f.context.decoded(2); await flush();
  f.context.sources[0].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2], 'no chunk field: the next chunk starts immediately');
  f.context.decoded(3); await flush();
  f.context.sources[1].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2, 3]);
  f.context.decoded(4); await flush();
  f.context.sources[2].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2, 3, 4]);
  f.context.decoded(5); await flush();
  f.context.sources[3].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2, 3, 4, 5]);
  f.context.sources[4].end(); await flush();
  assert.equal(f.idle, 1); assert.equal(f.clock.tasks.size, 0);
});

test('an out-of-contract pause is capped at 400ms instead of stalling playback', async () => {
  const f = setup();
  f.queue.enqueue({ id: 1, chunk: { pause_after_ms: 9999 } });
  f.queue.enqueue({ id: 2 });
  f.context.decoded(1); await flush();
  f.context.decoded(2); await flush();
  f.context.sources[0].end(); await flush();
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1]);
  await f.clock.advance(400);
  assert.deepEqual(f.context.sources.map(s => s.buffer.id), [1, 2]);
  f.context.sources[1].end(); await flush();
  assert.equal(f.idle, 1); assert.equal(f.clock.tasks.size, 0);
});
