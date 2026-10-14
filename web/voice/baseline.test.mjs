import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('./baseline.js', import.meta.url));
const { VoiceBaseline } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

function fixture(options = {}) {
  let now = 1000;
  const baseline = new VoiceBaseline({ clock: () => now, ...options });
  baseline.setContext({ mode: 'speaker', captureKind: 'worklet', referenceAvailable: true });
  const start = (scenario = 'speaker_quiet', device = 'speaker') => {
    assert.equal(baseline.startTrial({ scenario, device }), true);
    return baseline.captureToken();
  };
  const record = (token, type, at, meta = {}, turn_id = 'raw-turn-sensitive') => baseline.record({ type, at: 1000 + at, meta, turn_id }, { token });
  return { baseline, start, record, setNow: value => { now = value; } };
}

test('collection is off until a valid trial with live capture starts', () => {
  const baseline = new VoiceBaseline({ clock: () => 0 });
  assert.equal(baseline.captureToken(), null);
  assert.equal(baseline.record({ type: 'audio_submit' }), false);
  assert.equal(baseline.startTrial({ scenario: 'speaker_quiet' }), false);
  baseline.setContext({ captureKind: 'script' });
  assert.equal(baseline.startTrial({ scenario: 'unknown' }), false);
  assert.equal(baseline.startTrial({ scenario: 'speaker_quiet' }), true);
  assert.equal(baseline.startTrial({ scenario: 'speaker_quiet' }), false);
  assert.equal(baseline.record({ type: 'audio_submit' }), false);
});

test('a captured token isolates late callbacks, including after clear', () => {
  const { baseline, start, record } = fixture();
  const oldToken = start(); record(oldToken, 'turn_begin', 0); baseline.finishTrial();
  const newToken = start();
  assert.notEqual(oldToken, newToken);
  assert.equal(record(oldToken, 'audio_received', 10), false);
  assert.equal(record(newToken, 'audio_received', 10), true);
  baseline.clear(); const afterClear = start();
  assert.notEqual(afterClear, oldToken);
  assert.equal(record(newToken, 'decode_end', 20), false);
});

test('exports retain whitelisted metadata and never raw IDs, text, PCM, settings labels or secrets', () => {
  const { baseline, start } = fixture(); const token = start();
  baseline.record({ type: 'audio_submit', turn_id: 'raw-turn-sensitive', at: 1001,
    text: 'sensitive-text', audio: new Float32Array([.2]), session_id: 'raw-session-sensitive',
    meta: { input: 'voice_auto', chunkIndex: 2, empty: false, text: 'sensitive-text',
      deviceLabel: 'private microphone', key: 'private-key', durationMs: Infinity, queueDepth: -1,
      textStatus: 'arbitrary-secret' } }, { token });
  const exported = baseline.exportData(); const text = JSON.stringify(exported);
  for (const forbidden of ['raw-turn-sensitive', 'raw-session-sensitive', 'sensitive-text',
    'private microphone', 'private-key', 'arbitrary-secret', 'turn_id', 'startedAt', 'trialToken']) assert.equal(text.includes(forbidden), false);
  assert.deepEqual(exported.trials[0].events[0].meta, { input: 'voice_auto', chunkIndex: 2, empty: false });
  assert.equal(exported.trials[0].events[0].turn, 1);
  assert.equal(exported.trials[0].events[0].atMs, 1);
  assert.equal(exported.physicalAcousticMetrics.userStopToAudibleFirstSoundMs, null);
  assert.equal(exported.physicalAcousticMetrics.speakingOnsetToAcousticStopMs, null);
});

test('browser AEC/NS/AGC settings remain unknown unless actual boolean values are supplied', () => {
  const { baseline, start } = fixture(); baseline.setContext({ echoCancellation: 'true', noiseSuppression: null, autoGainControl: false });
  start();
  assert.equal(baseline.snapshot().trials[0].setup.echoCancellation, null);
  assert.equal(baseline.snapshot().trials[0].setup.noiseSuppression, null);
  assert.equal(baseline.snapshot().trials[0].setup.autoGainControl, false);
});

test('activity and endpoint wait are bounded fixed context and separated in summaries', () => {
  const { baseline, start, record } = fixture();
  baseline.setContext({ activity: 'cafe', endpointWaitMs: 1200 });
  const first = start(); record(first, 'audio_submit', 100, { input: 'voice_auto' });
  record(first, 'playback_start', 500); baseline.finishTrial();
  baseline.setContext({ activity: 'expression', endpointWaitMs: 1800 });
  const second = start(); record(second, 'audio_submit', 100, { input: 'voice_auto' });
  record(second, 'playback_start', 700); baseline.finishTrial();
  const groups = baseline.exportData().summary.groups;
  assert.equal(groups.length, 2); assert.equal(groups[0].activity, 'cafe'); assert.equal(groups[0].endpointWaitMs, 1200);
  assert.equal(groups[1].activity, 'expression'); assert.equal(groups[1].endpointWaitMs, 1800);
  start(); baseline.setContext({ endpointWaitMs: 900 });
  assert.equal(baseline.snapshot().trials.at(-1).invalidReason, 'context_changed');
  baseline.setContext({ activity: 'sensitive unknown title', endpointWaitMs: 5001 });
  assert.equal(baseline.snapshot().context.activity, 'off'); assert.equal(baseline.snapshot().context.endpointWaitMs, null);
});

test('actual microphone settings/reference changes invalidate an active fixed setup', () => {
  const { baseline, start } = fixture(); start(); baseline.setContext({ echoCancellation: false });
  assert.equal(baseline.snapshot().trials.at(-1).invalidReason, 'device_changed');
  start(); baseline.setContext({ referenceAvailable: false });
  assert.equal(baseline.snapshot().trials.at(-1).invalidReason, 'device_changed');
});

test('input timing source is a bounded setup enum and changing it invalidates a trial', () => {
  const { baseline, start } = fixture(); baseline.setContext({ timingSource: 'worklet_input_clock' }); start();
  assert.equal(baseline.snapshot().trials[0].setup.timingSource, 'worklet_input_clock');
  baseline.setContext({ timingSource: 'script_callback_estimate' });
  assert.equal(baseline.snapshot().trials[0].invalidReason, 'device_changed');
  start(); baseline.finishTrial();
  assert.equal(baseline.snapshot().trials[1].setup.timingSource, 'script_callback_estimate');
  baseline.setContext({ timingSource: 'private microphone output clock' });
  assert.equal(baseline.snapshot().context.timingSource, null);
});

test('last energy is coalesced per turn and older arrivals do not replace its latest time', () => {
  const { baseline, start, record } = fixture({ maxEvents: 2 }); const token = start();
  record(token, 'speech_first_energy', 0);
  for (let at = 20; at <= 20000; at += 20) assert.equal(record(token, 'speech_last_energy', at), true);
  assert.equal(record(token, 'speech_last_energy', 19000), false);
  const events = baseline.snapshot().trials[0].events;
  assert.equal(events.length, 2); assert.equal(events.at(-1).atMs, 20000);
  assert.equal(baseline.active, true);
});

test('mode/capture changes invalidate a trial without affecting later ready setup', () => {
  const { baseline, start } = fixture(); start();
  baseline.setContext({ mode: 'immediate' });
  assert.equal(baseline.active, false);
  assert.equal(baseline.snapshot().trials[0].invalidReason, 'mode_changed');
  assert.equal(baseline.snapshot().context.mode, 'immediate');
  start(); baseline.setContext({ captureKind: 'none' });
  assert.equal(baseline.snapshot().trials[1].invalidReason, 'capture_lost');
  baseline.setContext({ captureKind: 'script' }); start(); baseline.setContext({ captureKind: 'worklet' });
  assert.equal(baseline.snapshot().trials[2].invalidReason, 'device_changed');
});

test('event, turn and trial duration limits invalidate instead of silently truncating valid samples', () => {
  for (const [options, action, reason] of [
    [{ maxEvents: 1 }, (record, token) => { record(token, 'turn_begin', 0); record(token, 'audio_submit', 20); }, 'event_limit'],
    [{ maxTurns: 1 }, (record, token) => { record(token, 'turn_begin', 0, {}, 'one'); record(token, 'turn_begin', 20, {}, 'two'); }, 'turn_limit'],
    [{ maxDurationMs: 100 }, (record, token) => record(token, 'audio_submit', 101), 'duration_limit'],
  ]) {
    const { baseline, start, record } = fixture(options); const token = start(); action(record, token);
    assert.equal(baseline.active, false); assert.equal(baseline.snapshot().trials[0].invalidReason, reason);
    assert.equal(baseline.exportData().summary.groups.length, 0);
  }
});

test('malformed event times and identifiers are rejected without polluting the current trial', () => {
  const { baseline, start, record } = fixture(); const token = start();
  assert.equal(record(token, 'unexpected-text-event', 1), false);
  assert.equal(record(token, 'audio_submit', NaN), false);
  assert.equal(record(token, 'audio_submit', -1), false);
  assert.equal(record(token, 'audio_submit', 1, {}, {}), false);
  assert.equal(record(token, 'audio_submit', 1, {}, 'a'.repeat(129)), false);
  assert.equal(baseline.snapshot().trials[0].events.length, 0);
});

test('voice timing summaries include endpoint wait and separate text modalities and trial environments', () => {
  const { baseline, start, record } = fixture();
  for (const [last, submit, play] of [[100, 1000, 1500], [100, 1000, 2000]]) {
    const token = start();
    record(token, 'speech_last_energy', last); record(token, 'audio_submit', submit, { input: 'voice_auto' });
    record(token, 'asr_received', submit + 100); record(token, 'playback_start', play, { durationMs: 1000 });
    baseline.finishTrial();
  }
  const textToken = start(); record(textToken, 'turn_begin', 0, { input: 'text' });
  record(textToken, 'audio_submit', 10, { input: 'text' }); record(textToken, 'playback_start', 50); baseline.finishTrial();
  const otherToken = start('speaker_soft'); record(otherToken, 'audio_submit', 10, { input: 'voice_manual' });
  record(otherToken, 'playback_start', 200); baseline.finishTrial();
  const groups = baseline.exportData().summary.groups;
  const voice = groups.find(group => group.input === 'voice_auto');
  assert.deepEqual(voice.metrics.speechLastEnergyToPlaybackScheduledMs, { n: 2, p50: 1400, p95: 1900 });
  assert.deepEqual(voice.metrics.audioSubmitToPlaybackScheduledMs, { n: 2, p50: 500, p95: 1000 });
  assert.deepEqual(voice.metrics.audioSubmitToAsrReceivedMs, { n: 2, p50: 100, p95: 100 });
  assert.equal(groups.find(group => group.input === 'text').metrics.audioSubmitToPlaybackScheduledMs.n, 0);
  assert.equal(groups.find(group => group.scenario === 'speaker_soft').input, 'voice_manual');
});

test('gate proxies distinguish initial energy onset from the later candidate and stop request', () => {
  const { baseline, start, record } = fixture(); const token = start('speaker_interrupt_long');
  record(token, 'turn_begin', 0, { input: 'voice_auto' });
  record(token, 'gate_onset', 100); record(token, 'gate_candidate', 220); record(token, 'gate_commit', 700);
  record(token, 'playback_stop_requested', 710); baseline.finishTrial({ interruption: 'correct' });
  const metrics = baseline.exportData().summary.groups[0].metrics;
  assert.equal(metrics.gateOnsetToPlaybackStopRequestedMs.p50, 610);
  assert.equal(metrics.gateCandidateToPlaybackStopRequestedMs.p50, 490);
});

test('recovered candidate or unrelated manual stop never masquerades as a committed interruption', () => {
  const { baseline, start, record } = fixture(); const token = start('speaker_interrupt_long');
  record(token, 'turn_begin', 0, { input: 'text' });
  record(token, 'gate_onset', 100); record(token, 'gate_candidate', 220);
  record(token, 'gate_onset', 1000); record(token, 'gate_candidate', 1120); record(token, 'gate_commit', 1600);
  record(token, 'playback_stop_requested', 1610); baseline.finishTrial();
  let metrics = baseline.exportData().summary.groups[0].metrics;
  assert.equal(metrics.gateOnsetToPlaybackStopRequestedMs.p50, 610);
  assert.equal(metrics.gateCandidateToPlaybackStopRequestedMs.p50, 490);
  const nextToken = start('speaker_interrupt_long'); record(nextToken, 'gate_onset', 100, {}, 'another');
  record(nextToken, 'playback_stop_requested', 1000, {}, 'another'); baseline.finishTrial();
  metrics = baseline.exportData().summary.groups.find(group => group.input === 'unknown').metrics;
  assert.equal(metrics.gateOnsetToPlaybackStopRequestedMs.n, 0);
});

test('decode and scheduled chunk gaps report browser operations, allowing overlap but excluding resumed/cancelled seams', () => {
  const { baseline, start, record } = fixture(); const token = start('long_explanation');
  record(token, 'turn_begin', 0, { input: 'voice_auto' });
  record(token, 'decode_start', 0, { chunkIndex: 0 }); record(token, 'decode_end', 20, { chunkIndex: 0 });
  record(token, 'decode_start', 10, { chunkIndex: 1 }); record(token, 'decode_end', 25, { chunkIndex: 1 });
  record(token, 'playback_start', 30, { durationMs: 1000 });
  record(token, 'playback_start', 1025, { durationMs: 1000 });
  record(token, 'playback_start', 2050, { durationMs: 1000 });
  record(token, 'playback_stop_requested', 2300);
  record(token, 'playback_start', 2500, { durationMs: 500, resumed: true });
  record(token, 'playback_start', 3100, { durationMs: 500 });
  baseline.finishTrial({ seam: 'gap' });
  const metrics = baseline.exportData().summary.groups[0].metrics;
  assert.deepEqual(metrics.decodeStartToEndMs, { n: 2, p50: 15, p95: 20 });
  assert.deepEqual(metrics.scheduledChunkGapMs, { n: 2, p50: -5, p95: 25 });
});

test('resume audio cannot become the first response audio timing sample', () => {
  const { baseline, start, record } = fixture(); const token = start();
  record(token, 'audio_submit', 100, { input: 'voice_auto' });
  record(token, 'playback_start', 120, { resumed: true, durationMs: 200 });
  record(token, 'decode_start', 110, { resumed: true, chunkIndex: 0 });
  record(token, 'decode_end', 111, { resumed: true, chunkIndex: 0 });
  record(token, 'playback_start', 500, { durationMs: 200 }); baseline.finishTrial();
  assert.equal(baseline.exportData().summary.groups[0].metrics.audioSubmitToPlaybackScheduledMs.p50, 400);
  assert.equal(baseline.exportData().summary.groups[0].metrics.decodeStartToEndMs.n, 0);
});

test('only manual completed-trial outcomes count false/missed/correction, with no fabricated hourly rate', () => {
  const { baseline, start, record } = fixture();
  for (const interruption of ['correct', 'missed', 'false', 'not_attempted', 'unrated']) {
    const token = start(); record(token, 'gate_commit', 10); baseline.finishTrial({ interruption, recognition: 'needs_correction', seam: 'smooth' });
  }
  start(); baseline.invalidate('disconnected'); start(); baseline.cancel(); start();
  const summary = baseline.exportData().summary;
  assert.deepEqual(summary.trials, { complete: 5, invalid: 1, cancelled: 1, active: 1 });
  assert.deepEqual(summary.interruptionOpportunities, { attemptedTrials: 2, noIntentTrials: 2,
    falseTrials: 1, missedTrials: 1, unratedTrials: 1, ratePerHour: null });
  assert.equal(summary.outcomes.recognition.needs_correction, 5);
  assert.equal(summary.outcomes.seam.smooth, 5);
});

test('invalid outcome values stay unrated and exported snapshots do not mutate the collector', () => {
  const { baseline, start, record } = fixture(); const token = start(); record(token, 'turn_begin', 0, { input: 'voice_auto' });
  baseline.finishTrial({ interruption: 'private user string', recognition: {}, seam: 'smooth' });
  const snapshot = baseline.snapshot(); snapshot.trials[0].events[0].meta.input = 'secret';
  snapshot.trials[0].setup.mode = 'secret'; snapshot.trials[0].outcomes.seam = 'secret';
  const exported = baseline.exportData();
  assert.equal(exported.trials[0].outcomes.interruption, 'unrated');
  assert.equal(exported.trials[0].outcomes.recognition, 'unrated');
  assert.equal(exported.trials[0].outcomes.seam, 'smooth');
  assert.equal(exported.trials[0].events[0].meta.input, 'voice_auto');
  assert.equal(exported.trials[0].setup.mode, 'speaker');
});

test('trial retention is bounded and explicitly reports discarded samples', () => {
  const { baseline, start } = fixture({ maxTrials: 2 });
  for (let i = 0; i < 4; i++) { start(); baseline.finishTrial(); }
  const data = baseline.exportData();
  assert.deepEqual(data.trials.map(trial => trial.trial), [3, 4]); assert.equal(data.discardedTrials, 2);
  assert.equal(data.summary.trials.complete, 2);
});

test('nonfinite closing clock makes a trial invalid', () => {
  const { baseline, start, setNow } = fixture(); start(); setNow(NaN); baseline.finishTrial();
  assert.equal(baseline.snapshot().trials[0].status, 'invalid');
  assert.equal(baseline.snapshot().trials[0].invalidReason, 'clock_invalid');
});

test('finishing a quiet trial after its duration bound cannot create a valid sample', () => {
  const { baseline, start, setNow } = fixture({ maxDurationMs: 1000 }); start(); setNow(2001);
  baseline.finishTrial({ interruption: 'not_attempted' });
  assert.equal(baseline.snapshot().trials[0].invalidReason, 'duration_limit');
  assert.equal(baseline.exportData().summary.trials.complete, 0);
});
