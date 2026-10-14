import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('./duplex.js', import.meta.url));
const { DuplexGate, EchoGuard } = await import(`data:text/javascript;base64,${source.toString('base64')}`);
const zero = () => new Float32Array(320);
const near = (n, amplitude = .065) => Float32Array.from({ length: 320 }, (_, j) =>
  amplitude * (Math.sin((n * 320 + j) * .071) + .35 * Math.sin((n * 320 + j) * .139)));
const push = (g, f, options = {}) => g.push(f, { playback: true, ...options });
function signal(length) {
  let seed = 9217, filtered = 0;
  return Float32Array.from({ length }, () => {
    seed = (1664525 * seed + 1013904223) >>> 0;
    filtered = .7 * filtered + .3 * (seed / 4294967296 * 2 - 1);
    return filtered * .25;
  });
}

test('EchoGuard suppresses delayed linear echo across 0..200ms search', () => {
  for (const lag of [0, 320, 1160, 3200]) {
    const e = new EchoGuard(), audio = signal(320 * 48);
    let suppressed = 0;
    for (let n = 0; n < 48; n++) {
      const reference = audio.slice(n * 320, (n + 1) * 320);
      const mic = Float32Array.from({ length: 320 }, (_, j) => .55 * (audio[n * 320 + j - lag] || 0));
      const r = e.push(mic, { reference });
      if (n > 18) { assert.equal(r.suppress, true); assert.equal(r.metrics.delayMs, lag / 16); suppressed++; }
    }
    assert.ok(suppressed > 20);
  }
});

test('independent double-talk and fresh near-end onset are not suppressed', () => {
  const e = new EchoGuard(), audio = signal(320 * 50);
  for (let n = 0; n < 50; n++) {
    const reference = audio.slice(n * 320, (n + 1) * 320);
    const speech = near(n);
    const mic = Float32Array.from(reference, (x, j) => .5 * x + (n >= 20 ? speech[j] : 0));
    const r = e.push(mic, { reference });
    if (n >= 20) assert.equal(r.suppress, false, `double-talk frame ${n}`);
  }
});

test('silence, DC, absent/invalid reference fail open and reset correlation history', () => {
  const e = new EchoGuard();
  for (let n = 0; n < 30; n++) {
    const r = e.push(zero(), { reference: zero() });
    assert.equal(r.suppress, false); assert.equal(r.metrics.correlation, 0);
  }
  for (let n = 0; n < 20; n++) assert.equal(e.push(new Float32Array(320).fill(.1), {
    reference: new Float32Array(320).fill(.2) }).suppress, false);
  assert.equal(e.push(near(0)).suppress, false);
  const bad = zero(); bad[2] = NaN;
  assert.equal(e.push(near(0), { reference: bad }).metrics.referenceAvailable, false);
  assert.equal(e.push(near(0), { reference: near(0), referenceAvailable: false }).suppress, false);
  assert.equal(e.samples, 0);
});

test('all modes duck at 120ms, commit at exact voiced duration with owned preroll', () => {
  for (const [mode, duration] of [['headset', 420], ['speaker', 600], ['immediate', 300]]) {
    const g = new DuplexGate();
    for (let n = 0; n < 20; n++) push(g, zero(), { mode });
    let result;
    const reused = zero();
    for (let n = 1; n <= duration / 20; n++) {
      reused.set(near(n)); result = push(g, reused, { mode });
      assert.equal(result.frame, reused);
      assert.equal(result.commit, n === duration / 20);
      if (n < duration / 20) assert.equal(result.duck, n >= 6);
    }
    assert.equal(result.frames.length, 13 + duration / 20);
    assert.ok(result.frames.slice(0, 13).every(f => f.every(x => x === 0)));
    const saved = result.frames.at(-1).slice(); reused.fill(0);
    assert.deepEqual(result.frames.at(-1), saved);
    for (let n = 0; n < 40; n++) {
      const r = push(g, near(n), { mode });
      assert.equal(r.commit, false); assert.equal(r.duck, false); assert.equal(r.candidate, false);
    }
  }
});

test('<=200ms sound only ducks; 180ms quiet restores without commit', () => {
  for (const length of [6, 8, 10]) {
    const g = new DuplexGate();
    for (let n = 0; n < length; n++) assert.equal(push(g, near(n)).commit, false);
    for (let n = 1; n <= 9; n++) {
      const r = push(g, zero());
      assert.equal(r.commit, false); assert.equal(r.duck, n < 9);
      if (n === 9) assert.equal(r.reason, 'recovered');
    }
  }
});

test('short syllable gaps preserve voiced accumulation, not gap duration', () => {
  const g = new DuplexGate(); let r;
  for (let n = 0; n < 10; n++) r = push(g, near(n));
  for (let n = 0; n < 5; n++) r = push(g, zero());
  assert.equal(r.metrics.voicedMs, 200);
  for (let n = 0; n < 10; n++) { r = push(g, near(n)); assert.equal(r.commit, false); }
  r = push(g, near(11)); assert.equal(r.commit, true);
  assert.equal(r.frames.length, 26); assert.equal(r.metrics.voicedMs, 420);
});

test('isolated sample spikes do not duck and sparse noise never accumulates forever', () => {
  const g = new DuplexGate(), spike = zero(); spike[100] = 1;
  for (let n = 0; n < 100; n++) {
    const r = push(g, spike); assert.equal(r.duck, false); assert.equal(r.commit, false);
    assert.equal(r.metrics.impulse, true);
  }
  for (let n = 0; n < 1000; n++) {
    const r = push(g, n % 8 === 0 ? near(n) : zero());
    assert.equal(r.commit, false); assert.ok(r.metrics.candidateMs <= 2000);
    assert.ok(r.metrics.bufferedFrames <= 114);
  }
  assert.equal(g.history.length, 114);
  assert.ok(g.history.every(f => f.length === 320));
});

test('missing reference still allows sustained near-end, not blanket suppression', () => {
  const g = new DuplexGate(); let r;
  for (let n = 0; n < 21; n++) {
    r = push(g, near(n), { referenceAvailable: false }); assert.equal(r.suppress, false);
  }
  assert.equal(r.commit, true);
});

test('playback tail protects 260ms without hard-banning genuine speech', () => {
  const g = new DuplexGate();
  for (let n = 0; n < 12; n++) push(g, near(n));
  let r;
  for (let n = 0; n < 9; n++) r = g.push(near(n), { playback: false });
  assert.equal(r.commit, true);
  g.reset(); push(g, zero());
  for (let n = 0; n < 13; n++) assert.equal(g.push(zero()).metrics.protected, true);
  r = g.push(near(0)); assert.equal(r.metrics.protected, false); assert.equal(r.reason, 'vad');
  for (let n = 0; n < 50; n++) { r = g.push(near(n)); assert.equal(r.commit, false); assert.equal(r.duck, false); }
});

test('reset removes old candidates, audio and committed latch', () => {
  const g = new DuplexGate();
  for (let n = 0; n < 20; n++) push(g, near(n));
  g.reset(); assert.equal(g.echo.samples, 0);
  assert.ok(g.history.every(f => f.every(x => x === 0)));
  for (let n = 0; n < 20; n++) assert.equal(push(g, near(n)).commit, false);
  assert.equal(push(g, near(21)).commit, true);
  g.reset(); assert.equal(push(g, near(22)).candidate, false);
});

test('invalid mic is returned verbatim, never voiced or replayed as nonfinite', () => {
  const g = new DuplexGate(), bad = zero(); bad[0] = NaN; bad[5] = Infinity;
  const r = push(g, bad); assert.equal(r.frame, bad); assert.equal(r.suppress, false);
  assert.equal(r.metrics.invalidSamples, 2); assert.equal(r.metrics.voiced, false);
  let commit;
  for (let n = 0; n < 21; n++) commit = push(g, near(n));
  assert.equal(commit.commit, true);
  assert.ok(commit.frames.every(f => f.every(Number.isFinite)));
  assert.throws(() => g.push(new Float32Array(319)), TypeError);
  assert.throws(() => g.push(zero(), { mode: 'balanced' }), RangeError);
  assert.throws(() => new DuplexGate({ energyThreshold: NaN }), RangeError);
});

test('long playback echo never commits and storage remains bounded', () => {
  const g = new DuplexGate(), audio = signal(320 * 350);
  let suppressions = 0;
  const buffers = [...g.history];
  for (let n = 0; n < 350; n++) {
    const reference = audio.slice(n * 320, (n + 1) * 320);
    const mic = Float32Array.from(reference, x => x * .6);
    const r = push(g, mic, { reference });
    assert.equal(r.commit, false); assert.equal(r.duck, false);
    if (r.suppress) suppressions++;
    assert.ok(r.metrics.bufferedFrames <= 114);
  }
  assert.ok(suppressions >= 340);
  assert.ok(g.history.every((f, i) => f === buffers[i]));
});

test('onset at playback end survives tail expiry with all candidate frames', () => {
  for (const mode of ['headset', 'speaker']) {
    const g = new DuplexGate();
    for (let n = 0; n < 20; n++) push(g, zero(), { mode });
    const duration = mode === 'headset' ? 21 : 30;
    const expected = [];
    for (let n = 0; n < duration; n++) {
      const f = near(n); expected.push(f);
      const r = g.push(f, { playback: n === 0, mode });
      assert.equal(r.metrics.protected, true);
      assert.equal(r.commit, n === duration - 1);
      if (r.commit) {
        assert.equal(r.frames.length, 13 + duration);
        assert.deepEqual(r.frames.slice(13), expected);
      }
    }
  }
});

test('late-tail short sound recovers to VAD and continuation has a bounded lifetime', () => {
  const g = new DuplexGate(); push(g, zero());
  for (let n = 0; n < 12; n++) g.push(zero());
  for (let n = 0; n < 6; n++) {
    const r = g.push(near(n)); assert.equal(r.metrics.protected, true); assert.equal(r.commit, false);
  }
  for (let n = 0; n < 9; n++) assert.equal(g.push(zero()).commit, false);
  const free = g.push(near(0)); assert.equal(free.metrics.protected, false); assert.equal(free.reason, 'vad');
  g.reset(); push(g, near(0));
  for (let n = 1; n < 100; n++) {
    const r = g.push(n % 8 === 0 ? near(n) : zero());
    assert.equal(r.metrics.protected, true); assert.equal(r.commit, false);
  }
  assert.equal(g.push(near(100)).metrics.protected, false);
});

test('manual-only mode does not duck or commit during playback and releases after tail', () => {
  const g = new DuplexGate();
  for (let n = 0; n < 200; n++) {
    const r = g.push(near(n), {playback:true,mode:'manual'});
    assert.equal(r.duck,false); assert.equal(r.commit,false); assert.equal(r.metrics.protected,true);
  }
  for (let n = 0; n < 13; n++) assert.equal(g.push(near(n),{mode:'manual'}).metrics.protected,true);
  assert.equal(g.push(near(14),{mode:'manual'}).metrics.protected,false);
});
