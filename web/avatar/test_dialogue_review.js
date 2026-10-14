import test from 'node:test';
import assert from 'node:assert/strict';
import { DialogueReviewRecorder, DIALOGUE_REVIEW_TEXT_LIMITS } from './dialogue-review.js';

function fixture(options = {}) {
  let clock = 1000;
  const recorder = new DialogueReviewRecorder({ now: () => clock, ...options });
  return { recorder, set: value => { clock = value; }, tick: value => { clock += value; } };
}
const avatar = () => ({
  dialogueFrame: { state: 'thinking', phase: 'averted', age: .7, phaseAge: .2,
    style: 'natural', intent: 'hesitant', active: true, owner: 'dialogue', suspended: false },
  dialogueExpressionFrame: { cue: 'thinking-brow', appliedValues: { ParamBrowLY: .02 },
    protectedKeys: ['ParamEyeLSmile', 'ParamEyeLLower'] },
  eyeFrame: { params: { ParamEyeBallX: .23, ParamAngleX: .015, ParamBrowLY: .04,
    ParamEyeLOpen: 1, ParamMouthOpenY: 0 }, blink: 0, preview: 'none' },
  emotion: { current: 'base', matchingMode: false }, gazeMode: 'natural', rendererKind: 'vrm',
  audioCtx: { currentTime: 2.25, state: 'running' },
  svg: { eyeLidMode: 'calibrated', getEyeCapabilities: () => ({ available: true,
    profile: 'seed-lids', channels: { lowerLid: { available: true, approximate: true,
      mode: 'independent', description: 'do not duplicate descriptions' } } }) },
});

test('default is inactive and neither events nor frames implicitly authorize a turn', () => {
  const { recorder } = fixture();
  assert.equal(recorder.beginTurn({ id: 'a', input: 'private' }), false);
  assert.equal(recorder.recordEvent({ type: 'text_delta', turn_id: 'a', text: 'private' }), false);
  assert.equal(recorder.recordFrame(avatar()), false);
  recorder.start();
  assert.equal(recorder.recordEvent({ type: 'thinking', turn_id: 'a' }), false);
  assert.equal(recorder.recordFrame(avatar()), false);
  assert.equal(recorder.snapshot().current, null);
  assert.deepEqual(recorder.snapshot().turns, []);
});

test('start and stop keep completed turns; repeated current ID does not restart its clock', () => {
  const { recorder, tick } = fixture();
  recorder.start({ model: 'daily-lids', token: 'secret' });
  assert.deepEqual(recorder.snapshot().metadata, { model: 'daily-lids' });
  recorder.beginTurn({ id: 'a', input: 'first' }); tick(50);
  assert.equal(recorder.beginTurn({ id: 'a', input: 'duplicate' }), true);
  assert.equal(recorder.snapshot().current.input, 'first');
  assert.equal(recorder.snapshot().current.startedAt, 1000);
  recorder.start({ style: 'restrained' });
  assert.equal(recorder.snapshot().turns[0].reason, 'restarted');
  assert.equal(recorder.snapshot().turns[0].durationMs, 50);
  recorder.beginTurn({ id: 'b' }); tick(25);
  const stopped = recorder.stop();
  assert.equal(stopped.recording, false);
  assert.equal(stopped.current, null);
  assert.deepEqual(stopped.turns.map(turn => turn.id), ['a', 'b']);
  assert.equal(stopped.turns[1].reason, 'stopped');
  assert.equal(recorder.recordEvent({ type: 'done', text: 'late' }), false);
});

test('new authorized turns finish their predecessor and reject old events and end IDs', () => {
  const { recorder, tick } = fixture();
  recorder.start(); recorder.beginTurn({ id: 'a' }); tick(10);
  recorder.beginTurn({ id: 'b', source: 'lab', scenario: 'interrupted' });
  assert.equal(recorder.snapshot().turns[0].reason, 'superseded');
  assert.equal(recorder.recordEvent({ type: 'text_delta', turn_id: 'a', text: 'stale' }, 99999999), false);
  assert.equal(recorder.endTurn('a'), false);
  assert.equal(recorder.snapshot().current.id, 'b');
  assert.equal(recorder.snapshot().current.durationMs, 0);
  assert.equal(recorder.snapshot().current.source, 'lab');
  assert.equal(recorder.snapshot().current.scenario, 'interrupted');
  assert.equal(recorder.endTurn('b'), true);
  assert.equal(recorder.beginTurn({ id: 'b' }), false);
});

test('frame throttling uses elapsed milliseconds, independent of render frame count', () => {
  for (const fps of [30, 60, 144]) {
    const { recorder, set } = fixture();
    recorder.start(); recorder.beginTurn({ id: fps });
    const rendered = avatar();
    for (let frame = 0; frame <= fps; frame++) {
      set(1000 + frame * 1000 / fps);
      recorder.recordFrame(rendered);
    }
    const frames = recorder.snapshot().current.frames;
    assert.ok(frames.length >= 9 && frames.length <= 11, `${fps} fps produced ${frames.length} samples`);
    for (let i = 1; i < frames.length; i++) assert.ok(frames[i].t - frames[i - 1].t >= 100);
    assert.equal(frames[0].t, 0);
  }
  const { recorder } = fixture();
  recorder.start(); recorder.beginTurn({ id: 'boundary' });
  assert.equal(recorder.recordFrame(avatar(), 1000), true);
  assert.equal(recorder.recordFrame(avatar(), 1099.999), false);
  assert.equal(recorder.recordFrame(avatar(), 1100), true);
});

test('the default clock records valid real runtime timestamps without a separate timer', () => {
  const recorder = new DialogueReviewRecorder();
  const before = performance.now();
  recorder.start(); recorder.beginTurn({ id: 'runtime' });
  assert.equal(recorder.recordFrame(avatar()), true);
  assert.equal(recorder.recordFrame(avatar()), false);
  const current = recorder.snapshot().current;
  assert.ok(current.startedAt >= before && current.startedAt <= performance.now());
  assert.ok(current.frames[0].t >= 0 && Number.isFinite(current.frames[0].t));
});

test('generated text and early done do not become spoken text or end the turn', () => {
  const { recorder, tick } = fixture();
  recorder.start(); recorder.beginTurn({ id: 'reply', input: 'question' });
  recorder.recordEvent({ type: 'text_delta', turn_id: 'reply', delta: '第一句。' }); tick(1);
  recorder.recordEvent({ type: 'text_delta', text: '第二句。' }); tick(1);
  recorder.recordEvent({ type: 'done', turn_id: 'reply', text: '第一句。第二句。' });
  const current = recorder.snapshot().current;
  assert.equal(current.generatedText, '第一句。第二句。');
  assert.equal(current.spokenText, '');
  assert.equal(current.events[2].type, 'done');
  assert.deepEqual(recorder.snapshot().turns, []);
});

test('actual sentence playback, gaps and interruption remain separately reconstructible', () => {
  const { recorder, tick } = fixture();
  const rendered = avatar();
  recorder.start(); recorder.beginTurn({ id: 'reply' });
  recorder.recordEvent({ type: 'text_delta', delta: '一句。两句。尚未播放。' });
  tick(100); rendered.dialogueFrame.state = 'speaking'; rendered.dialogueFrame.phase = 'speaking';
  rendered.eyeFrame.params.ParamMouthOpenY = .8;
  recorder.recordEvent({ type: 'playback-start', turn_id: 'reply', text: '一句。', meta: { clipId: 'one', audioTime: 2.25 } });
  recorder.recordFrame(rendered);
  tick(500); recorder.recordEvent({ type: 'playback-end', turn_id: 'reply', text: '一句。', meta: { clipId: 'one' } });
  rendered.dialogueFrame.state = 'preparing'; rendered.dialogueFrame.phase = 'attending';
  rendered.eyeFrame.params.ParamMouthOpenY = 0; recorder.recordFrame(rendered);
  tick(300); recorder.recordEvent({ type: 'playback-start', turn_id: 'reply', text: '两句。', meta: { clipId: 'two' } });
  rendered.dialogueFrame.state = 'speaking'; recorder.recordFrame(rendered);
  tick(100); recorder.recordEvent({ type: 'interrupted', turn_id: 'reply' });
  recorder.recordEvent({ type: 'playback-end', turn_id: 'reply', meta: { clipId: 'two', reason: 'interrupted' } });
  recorder.endTurn('reply', 'interrupted');
  const turn = recorder.snapshot().turns[0];
  assert.equal(turn.spokenText, '一句。\n两句。');
  assert.equal(turn.generatedText, '一句。两句。尚未播放。');
  assert.equal(turn.reason, 'interrupted');
  assert.deepEqual(turn.frames.map(frame => frame.state), ['speaking', 'preparing', 'speaking']);
  assert.equal(turn.frames[1].params.ParamMouthOpenY, 0);
  assert.equal(turn.events.filter(event => event.type === 'playback-start').length, 2);
  assert.equal(turn.events.filter(event => event.type === 'playback-end').length, 2);
  assert.equal(recorder.recordEvent({ type: 'playback-start', turn_id: 'reply', text: 'late' }), false);
});

test('frame evidence copies rendered parameters, ownership, emotion, cues and audio clock', () => {
  const { recorder } = fixture();
  const rendered = avatar();
  recorder.start(); recorder.beginTurn({ id: 'frame' }); recorder.recordFrame(rendered);
  const frame = recorder.snapshot().current.frames[0];
  assert.equal(frame.params.ParamEyeBallX, .23);
  assert.equal(frame.params.ParamAngleX, .015);
  assert.equal(frame.appliedValues.ParamBrowLY, .02);
  assert.equal(frame.cue, 'thinking-brow'); assert.equal(frame.intent, 'hesitant');
  assert.equal(frame.capabilities.profile, 'seed-lids');
  assert.equal(frame.capabilities.channels.lowerLid.approximate, true);
  assert.equal(frame.capabilities.channels.lowerLid.description, undefined);
  assert.equal(frame.audioTime, 2.25);
  rendered.eyeFrame.params.ParamEyeBallX = -.4;
  rendered.dialogueExpressionFrame.protectedKeys.push('ParamBrowLY');
  assert.equal(recorder.snapshot().current.frames[0].params.ParamEyeBallX, .23);
  assert.deepEqual(recorder.snapshot().current.frames[0].protectedKeys, ['ParamEyeLSmile', 'ParamEyeLLower']);
});

test('bounded samples stop growing while later playback events can still be captured', () => {
  const { recorder, tick } = fixture({ maxSamples: 2 });
  recorder.start(); recorder.beginTurn({ id: 'samples' });
  recorder.recordFrame(avatar()); tick(100); recorder.recordFrame(avatar());
  tick(100); assert.equal(recorder.recordFrame(avatar()), false);
  assert.equal(recorder.recordEvent({ type: 'playback-end', text: 'ended' }), true);
  const current = recorder.snapshot().current;
  assert.equal(current.frames.length, 2); assert.equal(current.truncated.frames, true);
  assert.equal(current.events[0].type, 'playback-end');
});

test('duration limit completes a long turn at the exact cutoff and does not silently restart it', () => {
  const { recorder, tick } = fixture({ maxDurationMs: 120000 });
  recorder.start(); recorder.beginTurn({ id: 'long' }); recorder.recordFrame(avatar());
  tick(119999); assert.equal(recorder.recordEvent({ type: 'thinking' }), true);
  tick(5001); assert.equal(recorder.recordFrame(avatar()), false);
  const snapshot = recorder.snapshot();
  assert.equal(snapshot.recording, true); assert.equal(snapshot.current, null);
  assert.equal(snapshot.turns[0].durationMs, 120000);
  assert.equal(snapshot.turns[0].endedAt, 121000);
  assert.equal(snapshot.turns[0].reason, 'duration-limit');
  assert.equal(snapshot.turns[0].truncated.duration, true);
  assert.equal(recorder.beginTurn({ id: 'long' }), false);
  assert.equal(recorder.recordEvent({ type: 'playback-start', turn_id: 'long', text: 'late' }), false);
  assert.equal(recorder.beginTurn({ id: 'next' }), true);
});

test('snapshot alone also enforces the real elapsed duration cutoff', () => {
  const { recorder, tick } = fixture({ maxDurationMs: 10 });
  recorder.start(); recorder.beginTurn({ id: 'background' }); tick(10);
  const snapshot = recorder.snapshot();
  assert.equal(snapshot.current, null);
  assert.equal(snapshot.turns[0].reason, 'duration-limit');
});

test('retention includes current turn and only keeps the configured newest completed turns', () => {
  const { recorder } = fixture({ maxTurns: 2 }); recorder.start();
  for (const id of ['a', 'b', 'c']) {
    recorder.beginTurn({ id });
    assert.ok(recorder.snapshot().turns.length + Number(!!recorder.snapshot().current) <= 2);
    recorder.endTurn(id);
  }
  assert.deepEqual(recorder.snapshot().turns.map(turn => turn.id), ['b', 'c']);
  recorder.beginTurn({ id: 'd' });
  assert.deepEqual(recorder.snapshot().turns.map(turn => turn.id), ['c']);
});

test('text and events are bounded independently and explicitly flag truncation', () => {
  const { recorder } = fixture(); recorder.start();
  recorder.beginTurn({ id: 'bounded', input: '问'.repeat(9000) });
  recorder.recordEvent({ type: 'text_delta', text: '答'.repeat(30000) });
  recorder.recordEvent({ type: 'playback-start', text: '声'.repeat(30000) });
  for (let i = 0; i < 700; i++) recorder.recordEvent({ type: 'thinking' });
  const current = recorder.snapshot().current;
  assert.equal(current.input.length, DIALOGUE_REVIEW_TEXT_LIMITS.input);
  assert.equal(current.generatedText.length, DIALOGUE_REVIEW_TEXT_LIMITS.response);
  assert.equal(current.spokenText.length, DIALOGUE_REVIEW_TEXT_LIMITS.response);
  assert.equal(current.events.length, DIALOGUE_REVIEW_TEXT_LIMITS.events);
  for (const key of ['input', 'generatedText', 'spokenText', 'events', 'eventText']) assert.equal(current.truncated[key], true);
});

test('snapshots cannot mutate recorded turns, metadata, frames or limits', () => {
  const { recorder } = fixture(); recorder.start({ style: 'natural' });
  recorder.beginTurn({ id: 'isolated', input: '原文' }); recorder.recordFrame(avatar());
  const snapshot = recorder.snapshot();
  snapshot.metadata.style = 'tampered'; snapshot.current.input = 'changed';
  snapshot.current.frames[0].params.ParamEyeBallX = 99; snapshot.limits.maxTurns = 999;
  const actual = recorder.snapshot();
  assert.equal(actual.metadata.style, 'natural'); assert.equal(actual.current.input, '原文');
  assert.equal(actual.current.frames[0].params.ParamEyeBallX, .23); assert.equal(actual.limits.maxTurns, 12);
  recorder.endTurn('isolated');
  const finished = recorder.snapshot(); finished.turns[0].input = 'mutated';
  assert.equal(recorder.snapshot().turns[0].input, '原文');
});

test('clear deletes all saved text and metadata but preserves the recording switch', () => {
  const { recorder } = fixture(); recorder.start({ page: 'lab' });
  recorder.beginTurn({ id: 'old', input: 'secret input' });
  recorder.recordEvent({ type: 'text_delta', text: 'secret reply' }); recorder.endTurn('old');
  recorder.beginTurn({ id: 'current', input: 'more secret' });
  const cleared = recorder.clear();
  assert.equal(cleared.recording, true); assert.equal(cleared.current, null);
  assert.deepEqual(cleared.turns, []); assert.deepEqual(cleared.metadata, {});
  assert.ok(!JSON.stringify(cleared).includes('secret'));
  recorder.stop(); assert.equal(recorder.clear().recording, false);
});

test('lightweight list and single-turn lookup are isolated and include current status', () => {
  const { recorder, tick } = fixture();
  recorder.start(); recorder.beginTurn({ id: 'done', input: 'first', source: 'chat' });
  recorder.recordFrame(avatar()); recorder.endTurn('done');
  recorder.beginTurn({ id: 'current', input: 'second', scenario: 'long-thinking', source: 'lab' });
  tick(20); recorder.recordFrame(avatar());
  const rows = recorder.listTurns();
  assert.deepEqual(rows.map(row => row.current), [false, true]);
  assert.equal(rows[1].durationMs, 20); assert.equal(rows[1].frameCount, 1);
  assert.equal(rows[1].frames, undefined); assert.equal(rows[1].generatedText, undefined);
  rows[1].input = 'tampered'; rows[0].reason = 'tampered';
  assert.equal(recorder.listTurns()[1].input, 'second');
  assert.equal(recorder.listTurns()[0].reason, 'complete');
  const single = recorder.getTurn('current');
  single.frames[0].params.ParamEyeBallX = 99;
  assert.equal(recorder.getTurn('current').frames[0].params.ParamEyeBallX, .23);
  assert.equal(recorder.getTurn('done').input, 'first');
  assert.equal(recorder.getTurn('missing'), null);
  recorder.clear(); assert.deepEqual(recorder.listTurns(), []);
  assert.equal(recorder.getTurn('done'), null);
});

test('lightweight read methods also retire a turn whose time limit has expired', () => {
  for (const method of ['listTurns', 'getTurn']) {
    const { recorder, tick } = fixture({ maxDurationMs: 10 });
    recorder.start(); recorder.beginTurn({ id: 'expired' }); tick(20);
    const value = method === 'listTurns' ? recorder.listTurns()[0] : recorder.getTurn('expired');
    assert.equal(value.durationMs, 10); assert.equal(value.reason, 'duration-limit');
    assert.equal(recorder.current, null);
  }
});

test('raw audio, credential metadata, binary text and arbitrary fields are never copied', () => {
  const { recorder } = fixture(); recorder.start({ token: 'credential', data: 'rawaudio', model: 'daily' });
  recorder.beginTurn({ id: 'safe' });
  assert.equal(recorder.recordEvent({ type: 'audio', data: 'rawaudio' }), false);
  recorder.recordEvent({ type: 'playback-start', text: '正常文字', audio: 'rawaudio',
    meta: { audioTime: 2, token: 'credential', headers: { Authorization: 'credential' }, data: 'rawaudio' } });
  recorder.recordEvent({ type: 'text_delta', text: 'data:audio/wav;base64,' + 'A'.repeat(1000) });
  const rendered = avatar(); rendered.eyeFrame.params.token = 'credential';
  rendered.eyeFrame.params.Unknown = 55; recorder.recordFrame(rendered);
  const saved = JSON.stringify(recorder.snapshot());
  for (const forbidden of ['credential', 'rawaudio', 'base64', 'AAAA', 'Authorization', 'Unknown']) assert.ok(!saved.includes(forbidden), forbidden);
  assert.equal(recorder.snapshot().current.generatedText, '[binary data omitted]');
});

test('bad clock values, nonfinite parameters, malformed IDs and capability errors remain JSON safe', () => {
  const { recorder, set } = fixture({ maxTurns: NaN, maxSamples: Infinity, sampleIntervalMs: -50 });
  recorder.start();
  assert.equal(recorder.beginTurn({ id: {} }), false);
  assert.equal(recorder.beginTurn({ id: NaN }), false);
  assert.equal(recorder.beginTurn({ id: '' }), false);
  assert.equal(recorder.beginTurn({ id: 'safe' }), true);
  set(NaN); const rendered = avatar();
  rendered.audioCtx.currentTime = Infinity; rendered.eyeFrame.blink = NaN;
  rendered.eyeFrame.params.ParamEyeBallX = Infinity;
  rendered.svg.getEyeCapabilities = () => { throw new Error('unsupported'); };
  assert.equal(recorder.recordFrame(rendered, NaN), true);
  set(900); recorder.recordEvent({ type: 'thinking' }, -Infinity);
  const snapshot = recorder.snapshot();
  assert.equal(snapshot.current.durationMs, 0);
  assert.equal(snapshot.current.frames[0].audioTime, null);
  assert.equal(snapshot.current.frames[0].blink, null);
  assert.equal(snapshot.current.frames[0].params.ParamEyeBallX, undefined);
  assert.equal(snapshot.current.frames[0].capabilities.available, false);
  assert.doesNotThrow(() => JSON.stringify(snapshot));
  assert.equal(snapshot.limits.maxTurns, 12); assert.equal(snapshot.limits.maxSamples, 1200);
  assert.equal(snapshot.limits.sampleIntervalMs, 1);
});
