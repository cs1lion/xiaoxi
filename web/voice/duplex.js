// Pure 20ms mono/16k gate. No audio I/O, WS, cancellation, semantic classifier,
// or DSP AEC. Import as an ES module (tests also support non-module packages).
const SIZE = 320, MS = 20, LOW = 40, WINDOW = 160, DELAY = 400;
const PRE = 13, MAX_CANDIDATE = 100, HISTORY = PRE + MAX_CANDIDATE + 1;
const MODES = Object.freeze({ headset: 420, speaker: 600, immediate: 300, manual: Infinity });
function check(frame) {
  if (!(frame instanceof Float32Array) || frame.length !== SIZE)
    throw new TypeError('Expected a 320-sample Float32Array (20ms mono 16kHz)');
}
function positive(value, name) {
  if (!Number.isFinite(value) || value <= 0) throw new RangeError(`${name} must be positive and finite`);
  return value;
}

/**
 * EchoGuard.push(mic, {reference, referenceAvailable = reference != null})
 * returns {suppress, metrics}. reference is synchronous worklet input 1, NOT
 * a playback event timestamp. Mic may already have browser AEC applied.
 * 8-sample box averaging -> 2kHz; 80ms windows; 0..200ms lag in 0.5ms steps.
 * This is a conservative echo detector, not echo subtraction. Nonlinear output,
 * clock drift, AEC waveform changes, >200ms delay and high-frequency-only echo
 * can escape detection. Box averaging is not a production anti-aliasing filter.
 * Missing/invalid reference fails open. Silence and DC are not correlated speech.
 */
export class EchoGuard {
  constructor() {
    this.mic = new Float32Array(WINDOW);
    this.ref = new Float32Array(WINDOW + DELAY);
    this.reset();
  }
  reset() { this.mic.fill(0); this.ref.fill(0); this.samples = 0; }
  push(frame, { reference, referenceAvailable = reference != null } = {}) {
    check(frame);
    if (referenceAvailable && reference != null) check(reference);
    let sum = 0, peak = 0, invalid = 0, refInvalid = false;
    for (let i = 0; i < SIZE; i++) {
      const x = frame[i];
      if (!Number.isFinite(x)) { invalid++; continue; }
      sum += x * x; peak = Math.max(peak, Math.abs(x));
      if (referenceAvailable && reference && !Number.isFinite(reference[i])) refInvalid = true;
    }
    const rms = Math.sqrt(sum / SIZE);
    const metrics = { rms, peak, invalidSamples: invalid, referenceAvailable: false,
      correlation: 0, residualRatio: 1, recentResidualRatio: 1, delayMs: null, gain: 0 };
    if (invalid || !referenceAvailable || !reference || refInvalid) {
      this.reset();
      return { suppress: false, metrics };
    }
    metrics.referenceAvailable = true;
    this.mic.copyWithin(0, LOW); this.ref.copyWithin(0, LOW);
    for (let j = 0; j < LOW; j++) {
      let m = 0, r = 0;
      for (let k = 0; k < 8; k++) { m += frame[j * 8 + k]; r += reference[j * 8 + k]; }
      this.mic[WINDOW - LOW + j] = m / 8;
      this.ref[WINDOW + DELAY - LOW + j] = r / 8;
    }
    this.samples = Math.min(WINDOW + DELAY, this.samples + LOW);
    if (this.samples < WINDOW) return { suppress: false, metrics };
    let sm = 0, mm = 0;
    for (const x of this.mic) { sm += x; mm += x * x; }
    const mean = sm / WINDOW, energy = Math.max(0, mm - sm * mean);
    if (energy / WINDOW < 1e-8) return { suppress: false, metrics };
    let best = 0, bestLag = 0, bestGain = 0, bestMean = 0;
    // ~64k multiply/add pairs per frame, all working buffers fixed-size.
    for (let lag = 0; lag <= Math.min(DELAY, this.samples - WINDOW); lag++) {
      const start = DELAY - lag;
      let sr = 0, rr = 0, mr = 0;
      for (let j = 0; j < WINDOW; j++) {
        const r = this.ref[start + j]; sr += r; rr += r * r; mr += r * this.mic[j];
      }
      const re = Math.max(0, rr - sr * sr / WINDOW);
      if (re / WINDOW < 1e-8) continue;
      const cross = mr - sm * sr / WINDOW;
      const corr = Math.min(1, Math.abs(cross) / Math.sqrt(energy * re));
      if (corr > best) { best = corr; bestLag = lag; bestGain = cross / re; bestMean = sr / WINDOW; }
    }
    let recentEnergy = 0, residual = 0, recentSum = 0, recentSquares = 0;
    for (let j = WINDOW - LOW; j < WINDOW; j++) {
      const x = this.mic[j] - mean;
      const e = x - bestGain * (this.ref[DELAY - bestLag + j] - bestMean);
      recentEnergy += x * x; residual += e * e;
      recentSum += this.mic[j]; recentSquares += this.mic[j] * this.mic[j];
    }
    Object.assign(metrics, { correlation: best, residualRatio: Math.max(0, 1 - best * best),
      recentResidualRatio: recentEnergy > 1e-10 ? residual / recentEnergy : 1,
      delayMs: bestLag / 2, gain: bestGain });
    // Recent residual avoids an old echo-dominated window hiding new double-talk.
    const suppress = best >= 0.96 && metrics.residualRatio <= 0.08 &&
      metrics.recentResidualRatio <= 0.10 && rms >= 0.0005 &&
      (recentSquares - recentSum * recentSum / LOW) / LOW >= 1e-8;
    return { suppress, metrics };
  }
}

/**
 * new DuplexGate({energyThreshold = .018, fallbackThreshold = .025}?)
 * push(mic, {reference, playback=false, referenceAvailable, mode='headset'})
 * -> {frame: mic, suppress, duck, commit, candidate, reason, metrics, frames}
 * frames is undefined except on the one-shot commit; then it is owned copies
 * of 260ms preroll (250ms rounded up) + the entire bounded candidate interval.
 * candidate/duck begin at 120ms accumulated near-end energy. Syllable gaps
 * <180ms survive; >=180ms silence clears. Commit requires voiced duration
 * 420/600/300ms, >=60% occupancy, and a currently voiced frame. A candidate
 * expires at 2000ms: intermittent noise cannot accumulate indefinitely.
 * Playback tail is protected for 260ms. An onset begun under protection keeps
 * protection until commit, 180ms quiet recovery, or the 2000ms candidate limit;
 * tail expiry never discards a still-valid onset or its preroll.
 * Non-protected onset belongs to EnergyVad. After commit, onset is latched off
 * until reset(); echo analysis continues. reset clears ALL audio/state.
 *
 * Main thread: candidate => duck only, no WS/cancel. commit => cancel playback,
 * startUtterance(frames), adopt VAD active. Current mic is already in frames.
 * Otherwise protected frames stay local; unprotected !suppress goes to VAD.
 * Once committed, feed subsequent !suppress frames to the active utterance.
 * No reference is not evidence of echo: conservative energy fallback only.
 */
export class DuplexGate {
  constructor({ energyThreshold = 0.018, fallbackThreshold = 0.025 } = {}) {
    this.energyThreshold = positive(energyThreshold, 'energyThreshold');
    this.fallbackThreshold = positive(fallbackThreshold, 'fallbackThreshold');
    this.echo = new EchoGuard();
    this.history = Array.from({ length: HISTORY }, () => new Float32Array(SIZE));
    this.reset();
  }
  reset() {
    this.echo.reset(); this.tail = 0; this.committed = false;
    this.count = 0; this.clearCandidate();
    for (const frame of this.history) frame.fill(0);
  }
  clearCandidate() { this.start = -1; this.voicedMs = 0; this.quietMs = 0; }
  push(frame, { reference, playback = false, referenceAvailable = reference != null, mode = 'headset' } = {}) {
    if (!Object.hasOwn(MODES, mode)) throw new RangeError('mode must be headset, speaker, immediate, or manual');
    const echo = this.echo.push(frame, { reference, referenceAvailable });
    // An onset begun during playback/tail owns the handoff until commit,
    // recovery, or bounded expiry, even when the playback tail has elapsed.
    const continuingCandidate = this.start >= 0 && this.count - this.start < MAX_CANDIDATE;
    const protectedNow = Boolean(playback) || this.tail > 0 || continuingCandidate;
    if (playback) this.tail = PRE; else this.tail = Math.max(0, this.tail - 1);
    const index = this.count++;
    // Copy ownership now: callers/worklets may reuse their input buffer.
    // Invalid values are preserved in output.frame, but never in replay copies.
    const slot = this.history[index % HISTORY];
    for (let i = 0; i < SIZE; i++) slot[i] = Number.isFinite(frame[i]) ? frame[i] : 0;
    const threshold = echo.metrics.referenceAvailable ? this.energyThreshold : this.fallbackThreshold;
    const impulse = echo.metrics.rms > 0 && echo.metrics.peak / echo.metrics.rms > 8;
    const voiced = !echo.suppress && !echo.metrics.invalidSamples && !impulse && echo.metrics.rms >= threshold;
    const result = { frame, suppress: echo.suppress, duck: false, commit: false,
      candidate: false, reason: 'idle', frames: undefined,
      metrics: { ...echo.metrics, protected: protectedNow, voiced, impulse, threshold,
        voicedMs: 0, candidateMs: 0, quietMs: 0, bufferedFrames: Math.min(this.count, HISTORY) } };
    if (mode === 'manual') {
      this.clearCandidate();
      result.reason = protectedNow ? 'manual_only' : 'vad';
      return result;
    }
    if (this.committed) { result.reason = 'committed'; return result; }
    if (!protectedNow) { this.clearCandidate(); result.reason = echo.suppress ? 'echo' : 'vad'; return result; }
    if (this.start >= 0 && index - this.start >= MAX_CANDIDATE) this.clearCandidate();
    if (voiced) {
      if (this.start < 0) this.start = index;
      this.voicedMs += MS; this.quietMs = 0;
    } else if (this.start >= 0) {
      this.quietMs += MS;
      if (this.quietMs >= 180) { this.clearCandidate(); result.reason = 'recovered'; }
    }
    const elapsed = this.start < 0 ? 0 : (index - this.start + 1) * MS;
    Object.assign(result.metrics, { voicedMs: this.voicedMs, candidateMs: elapsed, quietMs: this.quietMs });
    result.candidate = this.voicedMs >= 120;
    result.duck = result.candidate;
    if (voiced && this.voicedMs >= MODES[mode] && this.voicedMs / elapsed >= 0.6) {
      result.frames = [];
      for (let i = Math.max(0, this.start - PRE); i <= index; i++)
        result.frames.push(this.history[i % HISTORY].slice());
      this.committed = true; result.commit = true; result.candidate = false;
      result.duck = false; result.reason = 'commit'; this.clearCandidate();
    } else if (result.reason !== 'recovered') {
      result.reason = echo.suppress ? 'echo' : result.candidate ? 'candidate' : this.start >= 0 ? 'onset' : impulse ? 'impulse' : 'protected';
    }
    return result;
  }
}
