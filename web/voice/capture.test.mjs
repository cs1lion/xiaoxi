import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';

const captureURL = new URL('./capture.js', import.meta.url);
// Preserve a file-based import.meta.url while loading browser ESM without package changes.
const source = (await readFile(captureURL, 'utf8')).replaceAll('import.meta.url', JSON.stringify(captureURL.href));
const { createCapture } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const workletSource = await readFile(new URL('./capture-worklet.js', import.meta.url), 'utf8');

function processor(rate = 48000) {
  let Processor;
  const messages = [], transfers = [];
  const scope = vm.createContext({ sampleRate: rate, currentTime: 0, Float32Array,
    AudioWorkletProcessor: class { constructor() { this.port = { onmessage: null,
      postMessage(data, buffers) { transfers.push(buffers.length); messages.push(structuredClone(data, { transfer: buffers })); } }; } },
    registerProcessor(name, ctor) { assert.equal(name, 'neurosama-duplex-capture'); Processor = ctor; },
  });
  vm.runInContext(workletSource, scope);
  return { instance: new Processor(), scope, messages, transfers };
}

for (const rate of [44100, 48000]) test(`worklet ${rate}: aligned mono mix, complete samples, transfer batches, silent output`, () => {
  const { instance, scope, messages, transfers } = processor(rate);
  const size = Math.round(rate * .02), count = size * 128; // Both quantum and frame boundaries end exactly.
  for (let start = 0; start < count; start += 128) {
    scope.currentTime = start / rate;
    const a = Float32Array.from({ length: 128 }, (_, i) => start + i);
    const b = Float32Array.from(a, x => x + 2);
    const r = Float32Array.from(a, x => -x);
    const output = [new Float32Array(128).fill(9), new Float32Array(128).fill(9)];
    assert.equal(instance.process([[a, b], [r]], [output]), true);
    assert.ok(output.every(channel => channel.every(x => x === 0)));
  }
  assert.equal(messages.length, count / size);
  assert.ok(transfers.every(n => n === 2));
  let cursor = 0;
  for (const chunk of messages) {
    assert.equal(chunk.microphone.length, size); assert.equal(chunk.reference.length, size);
    assert.equal(chunk.sampleRate, rate);
    assert.ok(Math.abs(chunk.audioTime - cursor / rate) < 1e-10);
    for (let i = 0; i < size; i++, cursor++) {
      assert.equal(chunk.microphone[i], cursor + 1); assert.equal(chunk.reference[i], cursor ? -cursor : 0);
    }
  }
  assert.equal(cursor, count); assert.equal(instance.used, 0);
});

test('worklet absent input zero-fills; stop cancels tail and posts no further messages', () => {
  const { instance, messages } = processor();
  for (let n = 0; n < 9; n++) instance.process([[new Float32Array(128).fill(3)], []], [[new Float32Array(128)]]);
  assert.equal(messages.length, 1); assert.ok(messages[0].reference.every(x => x === 0));
  assert.equal(instance.used, 192);
  instance.port.onmessage({ data: { type: 'stop' } });
  const out = new Float32Array(128).fill(5);
  assert.equal(instance.process([], [[out]]), false);
  assert.equal(instance.used, 0); assert.equal(messages.length, 1); assert.ok(out.every(x => x === 0));
});

function mockContext({ merger = true, worklet = false, failConnect = null } = {}) {
  const nodes = [];
  function node(type) {
    const n = { type, connections: [], disconnected: false, gain: { value: 1 },
      connect(...args) { if (failConnect === type) throw Error(`${type} connect failed`); this.connections.push(args); },
      disconnect(...args) { this.disconnectArgs = args; this.disconnected = true; } };
    nodes.push(n); return n;
  }
  const context = { sampleRate: 48000, currentTime: 0, destination: {},
    createMediaStreamSource: () => node('mic'), createGain: () => node('gain'),
    createScriptProcessor: (...args) => { const n = node('script'); n.args = args; return n; } };
  if (merger) context.createChannelMerger = count => { assert.equal(count, 2); return node('merger'); };
  if (worklet) context.audioWorklet = { addModule: async url => { assert.equal(url.href, new URL('./capture-worklet.js', captureURL).href); } };
  const reference = node('reference');
  let trackStops = 0;
  const stream = { getTracks: () => [{ stop: () => trackStops++ }] };
  return { context, nodes, reference, stream, node, trackStops: () => trackStops };
}
function audioEvent(start, channels = 2) {
  const data = [Float32Array.from({ length: 1024 }, (_, i) => start + i), Float32Array.from({ length: 1024 }, (_, i) => -(start + i))];
  const output = new Float32Array(1024).fill(7);
  return { playbackTime: start / 48000, inputBuffer: { length: 1024, numberOfChannels: channels, getChannelData: c => data[c] },
    outputBuffer: { numberOfChannels: 1, getChannelData: () => output }, output };
}

test('fallback uses 1024 and merger, preserves aligned samples, mutes, detaches only reference edge', async () => {
  const m = mockContext(), chunks = [];
  const capture = await createCapture({ context: m.context, stream: m.stream, referenceNode: m.reference, onChunk: c => chunks.push(c) });
  assert.equal(capture.kind, 'script'); assert.equal(capture.timingSource, 'script_callback_estimate'); assert.equal(capture.hasReference, true);
  const proc = m.nodes.find(n => n.type === 'script'), gain = m.nodes.find(n => n.type === 'gain');
  assert.deepEqual(proc.args, [1024, 2, 1]); assert.equal(gain.gain.value, 0);
  assert.equal(proc.connections[0][0], gain);
  for (let i = 0; i < 15; i++) {
    const event = audioEvent(i * 1024); m.context.currentTime = (i + 1) * 1024 / m.context.sampleRate;
    proc.onaudioprocess(event); assert.ok(event.output.every(x => x === 0));
  }
  assert.equal(chunks.length, 16);
  let cursor = 0;
  for (const c of chunks) {
    assert.equal(c.microphone.length, 960); assert.equal(c.reference.length, 960);
    assert.ok(Math.abs(c.audioTime - cursor / 48000) < 1e-10);
    for (let i = 0; i < 960; i++, cursor++) { assert.equal(c.microphone[i], cursor); assert.equal(c.reference[i], -cursor); }
  }
  const late = proc.onaudioprocess;
  capture.stop(); capture.stop(); late(audioEvent(0));
  assert.equal(chunks.length, 16); assert.equal(proc.onaudioprocess, null);
  assert.deepEqual(m.reference.disconnectArgs, [m.nodes.find(n => n.type === 'merger'), 0, 1]);
  assert.ok(m.nodes.every(n => n.disconnected)); assert.equal(m.trackStops(), 0);
});

for (const withLength of [true, false]) test(`fallback input timing ignores future output playbackTime (${withLength ? 'buffer' : 'channel fallback'} length)`, async () => {
  const m = mockContext(), chunks = [];
  const capture = await createCapture({ ...m, onChunk: chunk => chunks.push(chunk) });
  const proc = m.nodes.find(node => node.type === 'script');
  m.context.currentTime = 10;
  const event = audioEvent(0, 1); event.playbackTime = 10.5;
  if (!withLength) delete event.inputBuffer.length;
  proc.onaudioprocess(event);
  assert.equal(chunks.length, 1);
  assert.ok(Math.abs(chunks[0].audioTime - (10 - 1024 / 48000)) < 1e-10);
  assert.ok(chunks[0].audioTime + chunks[0].microphone.length / 48000 <= m.context.currentTime);
  assert.ok(event.output.every(sample => sample === 0));
  // The unfinished 64 samples retain their earlier start across callbacks.
  m.context.currentTime += 1024 / 48000;
  const next = audioEvent(1024, 1); next.playbackTime = 500;
  if (!withLength) delete next.inputBuffer.length;
  proc.onaudioprocess(next);
  assert.ok(Math.abs(chunks[1].audioTime - (10 - 1024 / 48000 + 960 / 48000)) < 1e-10);
  assert.equal(chunks[1].microphone[0], 960); capture.stop();
});

test('fallback without merger reports no reference and emits zeros', async () => {
  const m = mockContext({ merger: false }), chunks = [];
  const cap = await createCapture({ ...m, referenceNode: m.reference, onChunk: c => chunks.push(c) });
  assert.equal(cap.hasReference, false); assert.equal(m.reference.connections.length, 0);
  const proc = m.nodes.find(n => n.type === 'script');
  assert.deepEqual(proc.args, [1024, 1, 1]); proc.onaudioprocess(audioEvent(0, 1));
  assert.ok(chunks[0].reference.every(x => x === 0)); cap.stop();
});

test('fallback setup failure disconnects every owned node without stopping stream', async () => {
  const m = mockContext({ failConnect: 'gain' });
  await assert.rejects(createCapture({ ...m, referenceNode: m.reference, onChunk() {} }), /connect failed/);
  assert.ok(m.nodes.every(n => n.disconnected)); assert.equal(m.trackStops(), 0);
});

test('onChunk failure stops capture, reports once, and blocks reentrant/late delivery', async () => {
  const m = mockContext(); let errors = 0, calls = 0;
  const cap = await createCapture({ ...m, onChunk() { calls++; throw Error('consumer'); }, onError() { errors++; } });
  const proc = m.nodes.find(n => n.type === 'script'), handler = proc.onaudioprocess;
  handler(audioEvent(0)); handler(audioEvent(1024)); cap.stop();
  assert.equal(errors, 1); assert.equal(calls, 1); assert.equal(proc.onaudioprocess, null);
});

test('worklet module cache, node topology, runtime error and late handlers', async () => {
  const m = mockContext({ worklet: true }); let loads = 0, errors = 0, chunks = 0;
  const original = m.context.audioWorklet.addModule;
  m.context.audioWorklet.addModule = async url => { loads++; await original(url); };
  globalThis.AudioWorkletNode = class {
    constructor(ctx, name, options) {
      assert.equal(ctx, m.context); assert.equal(name, 'neurosama-duplex-capture');
      assert.equal(options.numberOfInputs, 2); assert.equal(options.numberOfOutputs, 1);
      const n = m.node('worklet');
      n.port = { postMessage(value) { this.sent = value; }, close() { this.closed = true; } };
      return n;
    }
  };
  try {
    const opts = { ...m, referenceNode: m.reference, onChunk() { chunks++; }, onError() { errors++; } };
    const [a, b] = await Promise.all([createCapture(opts), createCapture(opts)]);
    assert.equal(loads, 1); assert.equal(a.kind, 'worklet'); assert.equal(a.timingSource, 'worklet_input_clock'); assert.equal(a.hasReference, true);
    const n = m.nodes.find(n => n.type === 'worklet'), late = n.port.onmessage, failure = n.onprocessorerror;
    late({ data: {} }); assert.equal(chunks, 1);
    failure(); failure(); late({ data: {} }); a.stop(); b.stop();
    assert.equal(errors, 1); assert.equal(chunks, 1); assert.equal(n.port.onmessage, null);
    assert.equal(n.port.closed, true); assert.deepEqual(n.port.sent, { type: 'stop' });
  } finally { delete globalThis.AudioWorkletNode; }
});

test('module rejection falls back without leaks and later calls may retry', async () => {
  const m = mockContext({ worklet: true }); let loads = 0;
  m.context.audioWorklet.addModule = async () => { loads++; throw Error('module denied'); };
  globalThis.AudioWorkletNode = class { constructor() { throw Error('must not construct'); } };
  try {
    for (let i = 0; i < 2; i++) {
      const cap = await createCapture({ ...m, onChunk() {} }); assert.equal(cap.kind, 'script'); cap.stop();
    }
    assert.equal(loads, 2); assert.ok(m.nodes.filter(n => n.type !== 'reference').every(n => n.disconnected));
  } finally { delete globalThis.AudioWorkletNode; }
});

test('pending module load allocates no graph; epoch owner can stop immediately after resolve', async () => {
  const m = mockContext({ worklet: true }); let finish;
  m.context.audioWorklet.addModule = () => new Promise(resolve => { finish = resolve; });
  globalThis.AudioWorkletNode = class { constructor() {
    const n = m.node('worklet'); n.port = { postMessage() {}, close() { this.closed = true; } }; return n;
  } };
  try {
    const pending = createCapture({ ...m, onChunk() { assert.fail('unexpected callback'); } });
    await Promise.resolve();
    assert.equal(m.nodes.length, 1);
    finish(); const capture = await pending; capture.stop();
    assert.ok(m.nodes.filter(n => n.type !== 'reference').every(n => n.disconnected));
    assert.equal(m.trackStops(), 0);
  } finally { delete globalThis.AudioWorkletNode; }
});

test('stop inside fallback callback prevents remaining chunks of the same event', async () => {
  const m = mockContext(); m.context.sampleRate = 8000;
  let calls = 0;
  const capture = await createCapture({ ...m, onChunk() { calls++; capture.stop(); } });
  m.nodes.find(n => n.type === 'script').onaudioprocess(audioEvent(0));
  assert.equal(calls, 1);
});

test('partial worklet graph failure closes port before fallback', async () => {
  const m = mockContext({ worklet: true, failConnect: 'worklet' });
  globalThis.AudioWorkletNode = class { constructor() {
    const n = m.node('worklet'); n.port = { postMessage() {}, close() { this.closed = true; } }; return n;
  } };
  try {
    const cap = await createCapture({ ...m, referenceNode: m.reference, onChunk() {} });
    const n = m.nodes.find(n => n.type === 'worklet');
    assert.equal(cap.kind, 'script'); assert.equal(n.disconnected, true); assert.equal(n.port.closed, true);
    assert.equal(n.port.onmessage, null); cap.stop(); assert.equal(m.trackStops(), 0);
  } finally { delete globalThis.AudioWorkletNode; }
});
