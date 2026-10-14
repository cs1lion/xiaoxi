/* Observe the production Avatar loop without creating another animation loop.
 * CPU wall time measures JavaScript/update/submission. Optional WebGL timer
 * queries measure commands submitted by renderer.render and are read only after
 * QUERY_RESULT_AVAILABLE; they do not measure the whole browser/GPU workload. */

const owners = new WeakMap();
const quantile = (values, q) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), position = (sorted.length - 1) * q;
  const lower = Math.floor(position), upper = Math.ceil(position);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (position - lower);
};
export function summarizeFrameTimes(values) {
  const finite = values.filter(value => Number.isFinite(value) && value >= 0);
  return {
    samples: finite.length,
    minMs: finite.length ? Math.min(...finite) : null,
    maxMs: finite.length ? Math.max(...finite) : null,
    meanMs: finite.length ? finite.reduce((sum, value) => sum + value, 0) / finite.length : null,
    p50Ms: quantile(finite, .5), p95Ms: quantile(finite, .95), p99Ms: quantile(finite, .99),
    intervalsAbove16_67Ms: finite.filter(value => value > 1000 / 60).length,
    intervalsAbove33_33Ms: finite.filter(value => value > 1000 / 30).length,
  };
}

function describeEnvironment(renderer) {
  const gl = renderer?.getContext?.(), debug = gl?.getExtension?.('WEBGL_debug_renderer_info');
  const parameter = key => { try { return gl?.getParameter?.(key) ?? null; } catch { return null; } };
  const webglRenderer = parameter(debug?.UNMASKED_RENDERER_WEBGL ?? gl?.RENDERER);
  const webglVendor = parameter(debug?.UNMASKED_VENDOR_WEBGL ?? gl?.VENDOR);
  const software = /swiftshader|software rasterizer|llvmpipe|softpipe|lavapipe|basic render|\bwarp\b/i.test(webglRenderer || '');
  return {
    userAgent: globalThis.navigator?.userAgent ?? null,
    platform: globalThis.navigator?.platform ?? null,
    hardwareConcurrency: globalThis.navigator?.hardwareConcurrency ?? null,
    devicePixelRatio: globalThis.devicePixelRatio ?? null,
    visibilityState: globalThis.document?.visibilityState ?? null,
    webglVersion: parameter(gl?.VERSION), webglRenderer, webglVendor,
    renderingClass: software ? 'software' : webglRenderer ? 'non-software-renderer-reported' : 'unknown',
    hardwareVerified: false,
    canvas: renderer?.domElement ? { width: renderer.domElement.width, height: renderer.domElement.height } : null,
  };
}

function saveMethod(object, name, wrapper) {
  const descriptor = Object.getOwnPropertyDescriptor(object, name), original = object[name];
  object[name] = wrapper;
  return () => {
    // A later owner may have replaced the method. Never overwrite its changes.
    if (object[name] !== wrapper) return;
    if (descriptor) Object.defineProperty(object, name, descriptor);
    else delete object[name];
  };
}

export class AvatarPerformanceProbe {
  constructor(avatar, runtime = {}) {
    this.avatar = avatar;
    this.runtime = {
      now: () => globalThis.performance.now(),
      setTimeout: (callback, delay) => globalThis.setTimeout(callback, delay),
      clearTimeout: id => globalThis.clearTimeout(id), ...runtime,
    };
    this.active = false; this.result = null; this._completion = null;
  }

  start({ durationMs = 5000, name = 'interactive', metadata = {}, gpu = true, drainMs = 750 } = {}) {
    if (this.active || this._finishing) throw new Error('Performance probe is already running');
    if (owners.has(this.avatar)) throw new Error('Another performance probe owns this Avatar');
    if (!this.avatar?._raf || typeof this.avatar._frame !== 'function') throw new Error('Start normal Avatar playback before measuring performance');
    if (!Number.isFinite(durationMs) || durationMs < 100 || durationMs > 120000) throw new Error('durationMs must be between 100 and 120000');
    if (!Number.isFinite(drainMs) || drainMs < 0 || drainMs > 5000) throw new Error('drainMs must be between 0 and 5000');
    this.active = true; this.result = null; this.name = name; this.metadata = metadata;
    this.startedAt = new Date().toISOString(); this.started = this.runtime.now();
    this.durationMs = durationMs; this.drainMs = drainMs; this.rafIntervalsMs = [];
    this.avatarFrames = []; this.gpuFrames = []; this.pending = []; this.previousRaf = null;
    this.currentFrame = null; this.submissionIndex = 0; this.restorers = [];
    this.rig = this.avatar.svg; this.renderer = this.rig?._state?.renderer || this.rig?.renderer || null;
    this.environment = describeEnvironment(this.renderer);
    this.gpu = { requested: !!gpu, available: false, extension: null, reason: gpu ? 'WebGL2 timer query extension unavailable' : 'GPU sampling disabled',
      discardedDisjoint: 0, discardedTimeout: 0, skippedBusy: 0, skippedBackpressure: 0, errors: [] };
    this.gl = this.renderer?.getContext?.() || null;
    this.ext = gpu && this.gl && typeof this.gl.createQuery === 'function'
      ? this.gl.getExtension('EXT_disjoint_timer_query_webgl2') : null;
    if (this.ext) {
      this.gpu.available = true; this.gpu.extension = 'EXT_disjoint_timer_query_webgl2'; this.gpu.reason = null;
    } else if (!this.gl) this.gpu.reason = 'Current renderer has no WebGL context';
    else if (gpu && typeof this.gl.createQuery !== 'function') this.gpu.reason = 'Current context is not WebGL2';
    this._completion = new Promise(resolve => { this.resolve = resolve; });
    owners.set(this.avatar, this);
    const originalFrame = this.avatar._frame, probe = this;
    const frameWrapper = function (...args) {
      if (probe.avatar.svg !== probe.rig) { probe.stop('renderer-changed'); return originalFrame.apply(this, args); }
      const began = probe.runtime.now();
      // Avatar._start assigns the rAF callback timestamp to _last before _frame.
      const raf = Number.isFinite(this._last) ? this._last : began;
      if (probe.previousRaf !== null && raf > probe.previousRaf) probe.rafIntervalsMs.push(raf - probe.previousRaf);
      probe.previousRaf = raf;
      const sample = { frameIndex: probe.avatarFrames.length, timestampMs: began - probe.started,
        rafTimestampMs: raf - probe.started, dtSeconds: args[0], renderSubmissions: [] };
      probe.currentFrame = sample;
      try { return originalFrame.apply(this, args); }
      finally {
        sample.cpuWallMs = probe.runtime.now() - began;
        sample.render = { ...(probe.renderer?.info?.render || {}) };
        sample.memory = { ...(probe.renderer?.info?.memory || {}) };
        probe.avatarFrames.push(sample); probe.currentFrame = null;
        probe._pollQueries();
      }
    };
    this.restorers.push(saveMethod(this.avatar, '_frame', frameWrapper));
    if (typeof this.renderer?.render === 'function') {
      const originalRender = this.renderer.render;
      const renderWrapper = function (...args) {
        // Only Avatar-owned submissions belong in this sample.
        if (!probe.currentFrame) return originalRender.apply(this, args);
        const began = probe.runtime.now(), submission = { submissionIndex: probe.submissionIndex++, cpuWallMs: null };
        const query = probe._beginQuery(submission);
        try { return originalRender.apply(this, args); }
        finally {
          submission.cpuWallMs = probe.runtime.now() - began;
          probe.currentFrame.renderSubmissions.push(submission);
          if (query) probe._endQuery(query, submission);
        }
      };
      this.restorers.push(saveMethod(this.renderer, 'render', renderWrapper));
    }
    this._onContextLost = () => { this.gpu.reason = 'WebGL context lost during measurement'; this.stop('context-lost'); };
    this.renderer?.domElement?.addEventListener?.('webglcontextlost', this._onContextLost);
    this.timer = this.runtime.setTimeout(() => { this.stop('duration-complete'); }, durationMs);
    return this;
  }

  complete() {
    if (!this._completion) throw new Error('Start the performance probe first');
    return this._completion;
  }

  stop(reason = 'user-stopped') {
    if (!this.active) return this._completion || Promise.resolve(this.result);
    this.active = false; this._finishing = true; this.stopReason = reason;
    this.elapsedMs = this.runtime.now() - this.started;
    this.runtime.clearTimeout(this.timer);
    this.restorers.reverse().forEach(restore => restore());
    this.renderer?.domElement?.removeEventListener?.('webglcontextlost', this._onContextLost);
    if (owners.get(this.avatar) === this) owners.delete(this.avatar);
    this.drainDeadline = this.runtime.now() + this.drainMs;
    const drain = () => {
      // Context loss can be delivered synchronously from renderer.render. Let
      // its finally blocks finish before producing a report or deleting queries.
      if (this.currentFrame) { this.runtime.setTimeout(drain, 0); return; }
      this._pollQueries();
      if (!this.pending.length || this.runtime.now() >= this.drainDeadline) {
        this.gpu.discardedTimeout += this.pending.length;
        this._deletePending(); this._finish();
      } else this.runtime.setTimeout(drain, 16);
    };
    drain();
    return this._completion;
  }

  _beginQuery(submission) {
    if (!this.ext || !this.gpu.available) return null;
    const gl = this.gl;
    try {
      if (gl.getParameter(this.ext.GPU_DISJOINT_EXT)) { this._discardDisjoint(); return null; }
      if (gl.getQuery(this.ext.TIME_ELAPSED_EXT, gl.CURRENT_QUERY)) { this.gpu.skippedBusy++; return null; }
      if (this.pending.length >= 120) { this.gpu.skippedBackpressure++; return null; }
      const query = gl.createQuery();
      if (!query) throw new Error('createQuery returned null');
      try { gl.beginQuery(this.ext.TIME_ELAPSED_EXT, query); }
      catch (error) { gl.deleteQuery(query); throw error; }
      submission.gpuQuery = 'pending';
      return query;
    } catch (error) { this._disableGpu(error); return null; }
  }

  _endQuery(query, submission) {
    try {
      this.gl.endQuery(this.ext.TIME_ELAPSED_EXT);
      this.pending.push({ query, submission, frameIndex: this.currentFrame.frameIndex, timestampMs: this.currentFrame.timestampMs });
    } catch (error) { this.gl.deleteQuery(query); this._disableGpu(error); }
  }

  _pollQueries() {
    if (!this.ext || !this.pending.length) return;
    try {
      if (this.gl.getParameter(this.ext.GPU_DISJOINT_EXT)) { this._discardDisjoint(); return; }
      const retained = [];
      for (const item of this.pending) {
        if (!this.gl.getQueryParameter(item.query, this.gl.QUERY_RESULT_AVAILABLE)) { retained.push(item); continue; }
        const elapsedNs = this.gl.getQueryParameter(item.query, this.gl.QUERY_RESULT);
        this.gl.deleteQuery(item.query);
        if (Number.isFinite(elapsedNs) && elapsedNs >= 0) {
          item.submission.gpuQuery = 'valid'; item.submission.gpuElapsedMs = elapsedNs / 1e6;
          this.gpuFrames.push({ frameIndex: item.frameIndex, submissionIndex: item.submission.submissionIndex,
            timestampMs: item.timestampMs, gpuElapsedMs: elapsedNs / 1e6 });
        } else item.submission.gpuQuery = 'invalid-result';
      }
      this.pending = retained;
    } catch (error) { this._disableGpu(error); }
  }

  _deletePending(status = 'discarded-timeout') {
    for (const item of this.pending) { item.submission.gpuQuery = status; try { this.gl.deleteQuery(item.query); } catch {} }
    this.pending = [];
  }
  _discardDisjoint() { this.gpu.discardedDisjoint += this.pending.length; this._deletePending('discarded-disjoint'); }
  _disableGpu(error) {
    this.gpu.available = false; this.gpu.reason = `Timer query failed: ${error.message}`;
    this.gpu.errors.push(error.message); this._deletePending('discarded-error');
  }
  _finish() {
    if (!this.gpuFrames.length && !this.gpu.reason) this.gpu.reason = this.gpu.discardedDisjoint
      ? 'All available timer queries were invalidated by GPU disjoint events'
      : this.gpu.discardedTimeout ? 'Timer results did not become available before the drain deadline'
        : this.gpu.skippedBusy ? 'GPU timer queries were occupied by another owner' : 'No GPU timer samples were submitted';
    this.result = {
      version: 2, name: this.name, metadata: this.metadata, startedAt: this.startedAt,
      completedAt: new Date().toISOString(), requestedDurationMs: this.durationMs,
      elapsedMs: this.elapsedMs, stopReason: this.stopReason, environment: this.environment,
      visibilityStateAtCompletion: globalThis.document?.visibilityState ?? null,
      rafIntervalsMs: this.rafIntervalsMs, avatarFrames: this.avatarFrames, gpuFrames: this.gpuFrames,
      summary: {
        observedAvatarFramesPerSecond: this.elapsedMs > 0 ? this.avatarFrames.length / (this.elapsedMs / 1000) : null,
        rafCadence: summarizeFrameTimes(this.rafIntervalsMs),
        avatarFrameCpuWall: summarizeFrameTimes(this.avatarFrames.map(frame => frame.cpuWallMs)),
        renderSubmissionCpuWall: summarizeFrameTimes(this.avatarFrames.flatMap(frame => frame.renderSubmissions.map(item => item.cpuWallMs))),
        gpuRenderCommands: summarizeFrameTimes(this.gpuFrames.map(frame => frame.gpuElapsedMs)),
      },
      measurement: {
        source: 'Existing Avatar requestAnimationFrame loop; actual Avatar._frame and renderer.render',
        rafSource: 'Avatar._last assigned from the production rAF callback timestamp',
        cpuWallIncludes: 'driver update, VRM update, renderer.render WebGL submission, and enabled timer-query instrumentation',
        gpuTimeMeasured: this.gpuFrames.length > 0, gpu: this.gpu,
        gpuScope: 'Elapsed GPU commands inside individual renderer.render calls; excludes browser composition and other GPU work',
        interpretation: 'Observed playback on the recorded browser and renderer. CPU wall time is not GPU time; headless results do not establish interactive-device performance.',
      },
    };
    this._finishing = false; this.resolve(this.result);
  }
}
