// Opt-in, page-local trial metadata. Never retains PCM, transcript, provider keys,
// raw turn/session IDs, device labels, or arbitrary event properties in exports.
export const BASELINE_SCENARIOS = Object.freeze({
  speaker_quiet: '外放 · 安静对话',
  speaker_echo: '外放 · 播放时保持安静',
  speaker_keyboard_fan: '外放 · 键盘 / 风扇',
  speaker_soft: '外放 · 轻声说话',
  speaker_interrupt_short: '外放 · 短句打断',
  speaker_interrupt_long: '外放 · 完整句打断',
  recognition_correction: '识别 · 句内改口',
  long_explanation: '长解释 · 接缝与续听',
});
const MODES = ['headset', 'speaker', 'immediate', 'manual'];
const DEVICES = ['speaker', 'headset'];
const CAPTURES = ['none', 'worklet', 'script'];
const TIMING_SOURCES = ['worklet_input_clock', 'script_callback_estimate'];
const ACTIVITIES = ['off', 'free', 'cafe', 'interview', 'theatre', 'listen', 'expression'];
const EVENTS = new Set(['turn_begin', 'speech_first_energy', 'speech_last_energy',
  'audio_submit', 'asr_received', 'audio_received', 'decode_start', 'decode_end',
  'decode_error', 'playback_start', 'playback_end', 'playback_stop_requested',
  'gate_onset', 'gate_candidate', 'gate_commit', 'turn_done', 'turn_error',
  'turn_cancel', 'queue_rejected']);
const INPUTS = ['voice_auto', 'voice_manual', 'text', 'confirmation', 'proactive', 'resume'];
const OUTCOMES = Object.freeze({
  interruption: ['unrated', 'correct', 'false', 'missed', 'not_attempted'],
  recognition: ['unrated', 'correct', 'needs_correction', 'failed'],
  seam: ['unrated', 'smooth', 'gap', 'overlap', 'cutoff'],
});
const INVALID = ['mode_changed', 'device_changed', 'context_changed', 'capture_lost', 'disconnected',
  'event_limit', 'turn_limit', 'duration_limit', 'clock_invalid'];
const META_ENUMS = {
  input: INPUTS, source: ['vad', 'duplex', 'manual', 'ws', 'playback'],
  reason: ['automatic', 'manual', 'limit', 'natural', 'cancelled', 'resume', 'error'],
  textStatus: ['complete', 'partial', 'limit_reached', 'none'],
  audioStatus: ['synthesized', 'partial', 'failed', 'none'],
};
const META_NUMBERS = { chunkIndex: 10000, durationMs: 180000, queueDepth: 128,
  successfulChunks: 10000, failedChunks: 10000, voicedMs: 30000, candidateMs: 30000 };
const META_BOOLEANS = ['empty', 'resumed', 'referenceAvailable'];

function cleanMeta(meta) {
  const result = {};
  if (!meta || typeof meta !== 'object') return result;
  for (const [key, values] of Object.entries(META_ENUMS)) {
    if (values.includes(meta[key])) result[key] = meta[key];
  }
  for (const [key, max] of Object.entries(META_NUMBERS)) {
    const value = meta[key];
    if (Number.isFinite(value) && value >= 0 && value <= max &&
        (key === 'durationMs' || Number.isInteger(value))) result[key] = Math.round(value * 10) / 10;
  }
  for (const key of META_BOOLEANS) if (typeof meta[key] === 'boolean') result[key] = meta[key];
  return result;
}
function cleanContext(context = {}) {
  const result = {
    mode: MODES.includes(context.mode) ? context.mode : 'headset',
    captureKind: CAPTURES.includes(context.captureKind) ? context.captureKind : 'none',
    timingSource: TIMING_SOURCES.includes(context.timingSource) ? context.timingSource : null,
    referenceAvailable: typeof context.referenceAvailable === 'boolean' ? context.referenceAvailable : null,
    activity: ACTIVITIES.includes(context.activity) ? context.activity : 'off',
    endpointWaitMs: Number.isFinite(context.endpointWaitMs) && context.endpointWaitMs >= 100 && context.endpointWaitMs <= 5000
      ? Math.round(context.endpointWaitMs) : null,
  };
  for (const key of ['echoCancellation', 'noiseSuppression', 'autoGainControl']) {
    result[key] = typeof context[key] === 'boolean' ? context[key] : null;
  }
  return result;
}
function stats(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  return sorted.length ? { n: sorted.length, p50: sorted[Math.ceil(sorted.length * .5) - 1],
    p95: sorted[Math.ceil(sorted.length * .95) - 1] } : { n: 0, p50: null, p95: null };
}
function rounded(value) { return Math.round(value * 10) / 10; }

/**
 * All `at` values use the same performance.now() clock, in milliseconds.
 * Root integration maps capture.audioTime to that clock before record().
 * speech_* and gate_onset are energy-detector proxies, not physical speech.
 * playback_* report browser scheduling/callbacks, not heard speaker sound.
 */
export class VoiceBaseline {
  constructor({ clock = () => performance.now(), maxTrials = 80, maxEvents = 800,
    maxTurns = 120, maxDurationMs = 600000, onUpdate = () => {} } = {}) {
    if (typeof clock !== 'function' || typeof onUpdate !== 'function') throw new TypeError('Expected clock and onUpdate functions');
    for (const value of [maxTrials, maxEvents, maxTurns, maxDurationMs]) {
      if (!Number.isInteger(value) || value < 1) throw new RangeError('Baseline bounds must be positive integers');
    }
    Object.assign(this, { clock, maxTrials, maxEvents, maxTurns, maxDurationMs, onUpdate });
    this.trials = []; this.current = null; this.nextTrial = 0; this.discardedTrials = 0;
    this.context = cleanContext(); this.turnIds = new Map(); this.latestEnergy = new Map(); this.trialToken = null;
  }
  get active() { return this.current?.status === 'active'; }
  captureToken() { return this.active ? this.trialToken : null; }
  notify() { this.onUpdate(this.snapshot()); }
  setContext(context = {}) {
    const next = cleanContext({ ...this.context, ...context });
    if (this.active && next.mode !== this.current.setup.mode) this.invalidate('mode_changed');
    else if (this.active && (next.activity !== this.current.setup.activity || next.endpointWaitMs !== this.current.setup.endpointWaitMs)) this.invalidate('context_changed');
    else if (this.active && this.current.setup.captureKind !== 'none' && next.captureKind === 'none') this.invalidate('capture_lost');
    else if (this.active && ['captureKind', 'timingSource', 'referenceAvailable', 'echoCancellation', 'noiseSuppression', 'autoGainControl'].some(key => next[key] !== this.current.setup[key])) this.invalidate('device_changed');
    this.context = next;
    this.notify();
  }
  startTrial({ scenario, device = 'speaker', ...context } = {}) {
    if (this.active || !Object.hasOwn(BASELINE_SCENARIOS, scenario) || !DEVICES.includes(device)) return false;
    const setup = { ...cleanContext({ ...this.context, ...context }), device };
    // Device experiments require a live local microphone capture, not a claim
    // based on a requested getUserMedia option or the browser test harness.
    if (setup.captureKind === 'none') return false;
    const startedAt = this.clock();
    if (!Number.isFinite(startedAt)) return false;
    this.turnIds.clear(); this.latestEnergy.clear();
    const trial = { trial: ++this.nextTrial, scenario, setup, status: 'active',
      startedAt, durationMs: 0, events: [], sequence: 0,
      outcomes: { interruption: 'unrated', recognition: 'unrated', seam: 'unrated' },
      invalidReason: null };
    this.trials.push(trial); this.current = trial; this.trialToken = Object.freeze({});
    if (this.trials.length > this.maxTrials) { this.trials.shift(); this.discardedTrials++; }
    this.notify(); return true;
  }
  record({ type, turn_id = null, at = this.clock(), meta } = {}, { token } = {}) {
    // Every caller passes the token captured at its source operation. Audio
    // decode/playback callbacks keep the token on their queue item, so callbacks
    // from a completed/cancelled trial cannot enter a later active trial.
    if (!this.active || token !== this.trialToken || !EVENTS.has(type)) return false;
    const trial = this.current;
    const elapsed = at - trial.startedAt;
    if (!Number.isFinite(elapsed) || elapsed < 0) return false;
    if (elapsed > this.maxDurationMs) { this.invalidate('duration_limit'); return false; }
    let turn = null;
    if (turn_id !== null && turn_id !== undefined) {
      if (typeof turn_id !== 'string' || !turn_id || turn_id.length > 128) return false;
      turn = this.turnIds.get(turn_id);
      if (!turn) {
        if (this.turnIds.size >= this.maxTurns) { this.invalidate('turn_limit'); return false; }
        turn = this.turnIds.size + 1; this.turnIds.set(turn_id, turn);
      }
    }
    // Last energy updates arrive once per frame. Retain only the latest marker
    // per turn, rather than storing a microphone-level history or silent frames.
    const previous = type === 'speech_last_energy' ? this.latestEnergy.get(turn) : null;
    if (previous && elapsed < previous.atMs) return false;
    if (!previous && trial.events.length >= this.maxEvents) { this.invalidate('event_limit'); return false; }
    const event = { seq: ++trial.sequence, type, turn, atMs: rounded(elapsed), meta: cleanMeta(meta) };
    if (previous) Object.assign(previous, event);
    else {
      trial.events.push(event);
      if (type === 'speech_last_energy') this.latestEnergy.set(turn, event);
    }
    trial.durationMs = Math.max(trial.durationMs, event.atMs);
    // Frame-rate energy updates should not redraw the UI at microphone rate.
    if (type !== 'speech_last_energy') this.notify();
    return true;
  }
  finishTrial(outcomes = {}) {
    if (!this.active) return false;
    for (const [key, values] of Object.entries(OUTCOMES)) {
      this.current.outcomes[key] = values.includes(outcomes[key]) ? outcomes[key] : 'unrated';
    }
    return this.end('complete');
  }
  invalidate(reason) {
    if (!this.active || !INVALID.includes(reason)) return false;
    this.current.invalidReason = reason; return this.end('invalid');
  }
  cancel() { return this.active ? this.end('cancelled') : false; }
  end(status) {
    const trial = this.current;
    const elapsed = this.clock() - trial.startedAt;
    if (Number.isFinite(elapsed) && elapsed >= 0) {
      trial.durationMs = Math.max(trial.durationMs, rounded(elapsed));
      if (elapsed > this.maxDurationMs && status === 'complete') { status = 'invalid'; trial.invalidReason = 'duration_limit'; }
    }
    else { status = 'invalid'; trial.invalidReason = 'clock_invalid'; }
    trial.status = status; this.current = null; this.trialToken = null; this.turnIds.clear(); this.latestEnergy.clear();
    this.notify(); return true;
  }
  clear() {
    this.trials = []; this.current = null; this.trialToken = null; this.turnIds.clear(); this.latestEnergy.clear();
    this.nextTrial = 0; this.discardedTrials = 0; this.notify();
  }
  snapshot() {
    return {
      active: this.active, context: { ...this.context }, discardedTrials: this.discardedTrials,
      trials: this.trials.map(trial => ({ trial: trial.trial, scenario: trial.scenario,
        setup: { ...trial.setup }, status: trial.status, durationMs: trial.durationMs,
        invalidReason: trial.invalidReason, outcomes: { ...trial.outcomes },
        events: [...trial.events].sort((a, b) => a.seq - b.seq).map(event => ({ ...event, meta: { ...event.meta } })) })),
    };
  }
  exportData() {
    const snapshot = this.snapshot();
    return { schemaVersion: 1, source: 'local_browser_trial_metadata',
      timingBasis: 'performance_clock_energy_and_browser_schedule_proxies',
      physicalAcousticMetrics: { userStopToAudibleFirstSoundMs: null,
        speakingOnsetToAcousticStopMs: null, status: 'not_measured' },
      ...snapshot, summary: summarizeBaseline(snapshot.trials) };
  }
}

export function summarizeBaseline(trials) {
  const groups = new Map();
  const outcomes = {
    interruption: Object.fromEntries(OUTCOMES.interruption.map(name => [name, 0])),
    recognition: Object.fromEntries(OUTCOMES.recognition.map(name => [name, 0])),
    seam: Object.fromEntries(OUTCOMES.seam.map(name => [name, 0])),
  };
  const metricNames = ['speechLastEnergyToPlaybackScheduledMs', 'audioSubmitToPlaybackScheduledMs',
    'audioSubmitToAsrReceivedMs', 'gateOnsetToPlaybackStopRequestedMs',
    'gateCandidateToPlaybackStopRequestedMs', 'decodeStartToEndMs', 'scheduledChunkGapMs'];
  let complete = 0, invalid = 0, cancelled = 0, active = 0;
  for (const trial of trials) {
    if (trial.status !== 'complete') {
      if (trial.status === 'invalid') invalid++;
      else if (trial.status === 'cancelled') cancelled++;
      else active++;
      continue;
    }
    complete++;
    for (const key of Object.keys(outcomes)) outcomes[key][trial.outcomes[key]]++;
    const byTurn = new Map();
    for (const event of trial.events) {
      if (event.turn === null) continue;
      if (!byTurn.has(event.turn)) byTurn.set(event.turn, []);
      byTurn.get(event.turn).push(event);
    }
    for (const events of byTurn.values()) {
      events.sort((a, b) => a.atMs - b.atMs || a.seq - b.seq);
      const submit = events.find(event => event.type === 'audio_submit');
      const begin = events.find(event => event.type === 'turn_begin');
      const input = submit?.meta.input || begin?.meta.input || 'unknown';
      const key = JSON.stringify({ scenario: trial.scenario, ...trial.setup, input });
      if (!groups.has(key)) groups.set(key, { scenario: trial.scenario, ...trial.setup,
        input, samples: Object.fromEntries(metricNames.map(name => [name, []])) });
      const group = groups.get(key);
      const add = (name, value) => { if (Number.isFinite(value) && (name === 'scheduledChunkGapMs' || value >= 0)) group.samples[name].push(rounded(value)); };
      const plays = events.filter(event => event.type === 'playback_start' && !event.meta.resumed);
      const initialPlay = plays.find(event => !submit || event.atMs >= submit.atMs);
      if (submit && initialPlay && ['voice_auto', 'voice_manual'].includes(input)) {
        add('audioSubmitToPlaybackScheduledMs', initialPlay.atMs - submit.atMs);
        const energies = events.filter(event => event.type === 'speech_last_energy' && event.atMs <= submit.atMs);
        if (energies.length) add('speechLastEnergyToPlaybackScheduledMs', initialPlay.atMs - energies.at(-1).atMs);
        const asr = events.find(event => event.type === 'asr_received' && event.atMs >= submit.atMs);
        if (asr) add('audioSubmitToAsrReceivedMs', asr.atMs - submit.atMs);
      }
      for (const commit of events.filter(event => event.type === 'gate_commit')) {
        const stop = events.find(event => event.type === 'playback_stop_requested' && event.atMs >= commit.atMs);
        if (!stop) continue;
        // A failed candidate may recover before a later genuine interruption.
        // Match the onset belonging to this commit, not the turn's earliest one.
        const onset = events.filter(event => event.type === 'gate_onset' && event.atMs <= commit.atMs).at(-1);
        if (onset) add('gateOnsetToPlaybackStopRequestedMs', stop.atMs - onset.atMs);
        const candidate = events.filter(event => event.type === 'gate_candidate' &&
          event.atMs <= commit.atMs && (!onset || event.atMs >= onset.atMs)).at(-1);
        if (candidate) add('gateCandidateToPlaybackStopRequestedMs', stop.atMs - candidate.atMs);
      }
      for (const event of events.filter(value => value.type === 'decode_start' && !value.meta.resumed && Number.isInteger(value.meta.chunkIndex))) {
        const end = events.find(value => value.type === 'decode_end' && value.meta.chunkIndex === event.meta.chunkIndex && value.atMs >= event.atMs);
        if (end) add('decodeStartToEndMs', end.atMs - event.atMs);
      }
      for (let i = 1; i < plays.length; i++) {
        const previous = plays[i - 1], next = plays[i];
        if (!Number.isFinite(previous.meta.durationMs)) continue;
        const stopped = events.some(event => event.type === 'playback_stop_requested' && event.atMs >= previous.atMs && event.atMs <= next.atMs);
        const resumed = events.some(event => event.type === 'playback_start' && event.meta.resumed && event.atMs >= previous.atMs && event.atMs <= next.atMs);
        if (!stopped && !resumed) add('scheduledChunkGapMs', next.atMs - previous.atMs - previous.meta.durationMs);
      }
    }
  }
  return { trials: { complete, invalid, cancelled, active }, outcomes,
    interruptionOpportunities: { attemptedTrials: outcomes.interruption.correct + outcomes.interruption.missed,
      noIntentTrials: outcomes.interruption.false + outcomes.interruption.not_attempted,
      falseTrials: outcomes.interruption.false, missedTrials: outcomes.interruption.missed,
      unratedTrials: outcomes.interruption.unrated, ratePerHour: null },
    groups: [...groups.values()].map(({ samples, ...group }) => ({ ...group,
      metrics: Object.fromEntries(Object.entries(samples).map(([name, values]) => [name, stats(values)])) })) };
}
