import test from 'node:test';
import assert from 'node:assert/strict';
import { AvatarPerformanceProbe } from './performance-probe.js';

function fixture({ gpu = false, inherited = false } = {}) {
  let now = 100, nextTimer = 0; const timers = new Map(), queries = [], deleted = [], events = new Map();
  let disjoint = false, available = false, foreign = false, resultReads = 0, ends = 0;
  const ext = { TIME_ELAPSED_EXT: 11, GPU_DISJOINT_EXT: 12 };
  const gl = {
    RENDERER: 1, VENDOR: 2, VERSION: 3, CURRENT_QUERY: 4, QUERY_RESULT_AVAILABLE: 5, QUERY_RESULT: 6,
    getExtension(name) { return name === 'EXT_disjoint_timer_query_webgl2' && gpu ? ext : null; },
    getParameter(key) { return key === 12 ? disjoint : key === 1 ? 'Unit software rasterizer' : key === 3 ? 'WebGL 2.0' : 'test'; },
    getQuery() { return foreign ? { otherOwner: true } : null; },
    createQuery() { const q = { id: queries.length }; queries.push(q); return q; },
    beginQuery(_target, q) { q.begun = true; }, endQuery() { ends++; },
    getQueryParameter(q, key) {
      if (key === 5) return available;
      resultReads++; assert.ok(available, 'never fetch a result before availability'); return (q.id + 1) * 1e6;
    },
    deleteQuery(q) { deleted.push(q); },
  };
  const render = function () { now += 2; this.info.render.calls = 3; this.info.render.triangles = 42; return 'rendered'; };
  const baseRenderer = { render }, renderer = inherited ? Object.create(baseRenderer) : { render };
  Object.assign(renderer, { getContext: () => gl, info: { render: { calls: 0 }, memory: { geometries: 2 } },
    domElement: { width: 600, height: 600, addEventListener(n, f) { events.set(n, f); }, removeEventListener(n) { events.delete(n); } } });
  const frame = function () { now += 3; return renderer.render(); };
  const avatar = inherited ? Object.create({ _frame: frame }) : { _frame: frame };
  Object.assign(avatar, { _raf: 1, _last: now, svg: { _state: { renderer } } });
  const runtime = {
    now: () => now, setTimeout(fn, ms) { const id = ++nextTimer; timers.set(id, { fn, when: now + ms }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  return { avatar, renderer, runtime, frame, render, gl, queries, deleted, events,
    get resultReads() { return resultReads; }, get ends() { return ends; },
    setAvailable(value) { available = value; }, setDisjoint(value) { disjoint = value; }, setForeign(value) { foreign = value; },
    tick(timestamp, dt = .016) { now = timestamp; avatar._last = timestamp; return avatar._frame(dt); },
    advance(ms) {
      const target = now + ms;
      while (true) {
        const next = [...timers].filter(([, t]) => t.when <= target).sort((a, b) => a[1].when - b[1].when)[0];
        if (!next) break;
        timers.delete(next[0]); now = next[1].when; next[1].fn();
      }
      now = target;
    },
  };
}

test('measures the existing frame, true RAF timestamp and submission CPU wall; restores inherited methods', async () => {
  const f = fixture({ inherited: true }), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start({ gpu: false });
  assert.equal(f.tick(116), 'rendered'); f.tick(176, .05);
  const result = await probe.stop();
  assert.deepEqual(result.rafIntervalsMs, [60]);
  assert.equal(result.avatarFrames[1].dtSeconds, .05, 'clamped driver dt is not mistaken for true frame interval');
  assert.equal(result.summary.avatarFrameCpuWall.meanMs, 5);
  assert.equal(result.summary.renderSubmissionCpuWall.meanMs, 2);
  assert.equal(result.avatarFrames[0].render.calls, 3);
  assert.equal(result.avatarFrames[0].render.triangles, 42);
  assert.equal(result.environment.renderingClass, 'software');
  assert.equal(result.environment.hardwareVerified, false);
  assert.equal(result.measurement.gpuTimeMeasured, false);
  assert.equal(Object.hasOwn(f.avatar, '_frame'), false);
  assert.equal(Object.hasOwn(f.renderer, 'render'), false);
  assert.equal(f.events.size, 0);
});

test('requires normal playback, prevents duplicate ownership, completes once and can be reused', async () => {
  const f = fixture(); f.avatar._raf = 0;
  assert.throws(() => new AvatarPerformanceProbe(f.avatar, f.runtime).start(), /normal Avatar/);
  f.avatar._raf = 1;
  const probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start({ durationMs: 100 });
  assert.throws(() => new AvatarPerformanceProbe(f.avatar, f.runtime).start(), /Another/);
  f.tick(116); f.tick(132); f.advance(100);
  const report = await probe.complete();
  assert.equal(report.stopReason, 'duration-complete');
  assert.equal(f.avatar._frame, f.frame); assert.equal(f.renderer.render, f.render);
  assert.equal(await probe.stop(), report);
  probe.start({ durationMs: 100 });
  assert.equal((await probe.stop()).avatarFrames.length, 0);
});

test('GPU queries are polled without blocking and linked to the actual render submission', async () => {
  const f = fixture({ gpu: true }), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  f.tick(116); f.tick(132);
  assert.equal(f.resultReads, 0); assert.equal(f.ends, 2);
  f.setAvailable(true); f.tick(148);
  const result = await probe.stop();
  assert.equal(result.gpuFrames.length, 3); assert.equal(f.deleted.length, 3);
  assert.equal(result.gpuFrames[1].frameIndex, 1); assert.equal(result.gpuFrames[1].gpuElapsedMs, 2);
  assert.equal(result.avatarFrames[1].renderSubmissions[0].gpuQuery, 'valid');
  assert.equal(result.summary.gpuRenderCommands.p95Ms, 2.9);
  assert.equal(result.measurement.gpuTimeMeasured, true); assert.equal(result.measurement.gpu.reason, null);
});

test('disjoint results are discarded rather than presented as valid GPU timings', async () => {
  const f = fixture({ gpu: true }), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  f.tick(116); f.setDisjoint(true); f.tick(132);
  const result = await probe.stop();
  assert.equal(result.gpuFrames.length, 0); assert.equal(f.resultReads, 0);
  assert.equal(result.measurement.gpu.discardedDisjoint, 1);
  assert.equal(result.avatarFrames[0].renderSubmissions[0].gpuQuery, 'discarded-disjoint');
  assert.match(result.measurement.gpu.reason, /disjoint/); assert.equal(f.deleted.length, 1);
});

test('foreign active GPU queries are respected', async () => {
  const f = fixture({ gpu: true }); f.setForeign(true);
  const probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start(); f.tick(116);
  const result = await probe.stop();
  assert.equal(f.queries.length, 0); assert.equal(f.ends, 0);
  assert.equal(result.measurement.gpu.skippedBusy, 1); assert.match(result.measurement.gpu.reason, /another owner/);
});

test('unavailable GPU results drain until deadline and delete pending queries', async () => {
  const f = fixture({ gpu: true }), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start({ drainMs: 32 }); f.tick(116);
  const complete = probe.stop();
  assert.equal(f.avatar._frame, f.frame, 'wrappers are restored immediately, before asynchronous query drain');
  f.advance(32); const result = await complete;
  assert.equal(f.deleted.length, 1); assert.equal(f.resultReads, 0);
  assert.equal(result.measurement.gpu.discardedTimeout, 1); assert.match(result.measurement.gpu.reason, /deadline/);
});

test('renderer change ends measurement and restores both methods', async () => {
  const f = fixture(), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start(); f.tick(116);
  f.avatar.svg = { _state: { renderer: {} } }; f.tick(132);
  const result = await probe.complete();
  assert.equal(result.stopReason, 'renderer-changed'); assert.equal(result.avatarFrames.length, 1);
  assert.equal(f.avatar._frame, f.frame); assert.equal(f.renderer.render, f.render);
});

test('stop preserves methods replaced by a later owner', async () => {
  const f = fixture(), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  const laterFrame = () => {}, laterRender = () => {};
  f.avatar._frame = laterFrame; f.renderer.render = laterRender;
  await probe.stop(); assert.equal(f.avatar._frame, laterFrame); assert.equal(f.renderer.render, laterRender);
});

test('context loss ends measurement and releases listeners and methods', async () => {
  const f = fixture(), probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  f.events.get('webglcontextlost')(); const result = await probe.complete();
  assert.equal(result.stopReason, 'context-lost'); assert.match(result.measurement.gpu.reason, /context lost/);
  assert.equal(f.events.size, 0); assert.equal(f.avatar._frame, f.frame);
});

test('a stop delivered inside render waits for the frame finally blocks', async () => {
  const f = fixture();
  const render = f.renderer.render;
  f.renderer.render = function (...args) { f.events.get('webglcontextlost')(); return render.apply(this, args); };
  const probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  f.tick(116); f.advance(0);
  const result = await probe.complete();
  assert.equal(result.stopReason, 'context-lost');
  assert.equal(result.avatarFrames.length, 1); assert.equal(result.summary.avatarFrameCpuWall.samples, 1);
  assert.equal(f.avatar._frame, f.frame);
});

test('a frame exception still records CPU duration and allows method restoration', async () => {
  const f = fixture();
  const originalFrame = () => { f.renderer.render(); throw new Error('original frame failed'); };
  f.avatar._frame = originalFrame;
  const probe = new AvatarPerformanceProbe(f.avatar, f.runtime).start();
  assert.throws(() => f.tick(116), /original frame failed/);
  const result = await probe.stop();
  assert.equal(result.avatarFrames.length, 1); assert.equal(result.avatarFrames[0].renderSubmissions.length, 1);
  assert.equal(f.avatar._frame, originalFrame); assert.equal(f.renderer.render, f.render);
});
