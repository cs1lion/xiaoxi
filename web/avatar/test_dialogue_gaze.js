import assert from 'node:assert/strict';
import test from 'node:test';
import { DialogueGazeDirector, DIALOGUE_GAZE_DEFAULTS, DIALOGUE_GAZE_STYLES } from './drivers/dialogue-gaze.js';
import { DIALOGUE_INTENT_PROFILES } from './drivers/dialogue-intent.js';

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const viewer = output => {
  assert.ok(output.command);
  assert.equal(output.command.x, 0);
  assert.equal(output.command.y, 0);
};
const waiting = () => {
  const director = new DialogueGazeDirector();
  director.setState('thinking', { newTurn: true });
  return director;
};

test('idle does not seize an untouched gaze target; sample is isolated', () => {
  const director = new DialogueGazeDirector();
  for (let i = 0; i < 5; i++) {
    const output = director.update(1);
    assert.equal(output.active, false);
    assert.equal(output.command, null);
    assert.equal(output.phase, 'idle');
  }
  director.setState('listening');
  const output = director.update(0);
  viewer(output);
  output.command.x = 99;
  assert.equal(director.sample().command.x, 0);
});

test('listening and monitoring attend to the viewer with bounded quiet micro motion', () => {
  const director = new DialogueGazeDirector();
  director.setState('listening');
  const first = director.update(0);
  viewer(first);
  assert.equal(first.phase, 'attending');
  assert.ok(first.command.drift < .1);
  assert.ok(first.command.intervalScale > 1);
  assert.equal(director.update(10).command, null);
  director.setState('monitoring');
  assert.equal(director.update(0).command, null, 'same target is not redundantly written');
});

test('a first waiting glance has a delay, bounded hold, return and sparse repeat', () => {
  const director = waiting();
  const [first, second] = director.sample().plan.glances;
  assert.ok(first.start >= .315 && first.start <= .385);
  assert.ok(first.end - first.start >= .64 && first.end - first.start <= .92);
  assert.ok(second.start - first.start >= 5.76 && second.start - first.start <= 10.4);
  viewer(director.update(first.start - .01));
  assert.equal(director.sample().phase, 'waiting');
  const away = director.update(.02);
  assert.equal(away.phase, 'averted');
  close(away.command.x, first.x); close(away.command.y, first.y);
  assert.ok(Math.abs(away.command.x) >= .246 && Math.abs(away.command.x) <= .318);
  assert.equal(director.update((first.end - first.start) / 2).command, null);
  const returned = director.update((first.end - first.start) / 2);
  assert.equal(returned.phase, 'returned'); viewer(returned);
  assert.equal(director.update(second.start - first.end - .02).command, null);
  const repeated = director.update(.02);
  assert.equal(repeated.phase, 'averted');
  close(repeated.command.x, second.x);
});

test('thinking to preparing retains its first waiting clock and same-state updates are idempotent', () => {
  const director = waiting();
  director.update(.3);
  director.setState('thinking');
  close(director.age, .3);
  director.setState('preparing');
  const away = director.update(.1);
  assert.equal(away.phase, 'averted');
  close(away.age, .1);
  director.setState('preparing');
  close(director.age, .1);
  const end = director.sample().plan.glances[0].end;
  assert.equal(director.update(end - .4 + .01).phase, 'returned');
});

test('emotion, text deltas and generation done never start speech or prematurely return a glance', () => {
  const director = waiting();
  director.update(.4);
  for (const type of ['emotion', 'text_delta', 'done']) {
    director.onEvent({ type });
    const output = director.update(0);
    assert.notEqual(output.state, 'speaking');
    assert.equal(output.phase, 'averted');
    assert.equal(output.command, null);
  }
});

test('the actual audio start returns attention, never the schedule or a UI speaking label', () => {
  const director = waiting(), clock = { currentTime: 10 };
  director.update(.4);
  assert.equal(director.startSpeech(clock, 10.5, 1), true);
  director.setState('speaking');
  let output = director.update(0);
  assert.equal(output.state, 'preparing');
  assert.equal(output.phase, 'averted');
  assert.equal(output.command, null);
  clock.currentTime = 10.499;
  assert.equal(director.update(0).state, 'preparing');
  clock.currentTime = 10.5;
  output = director.update(0);
  assert.equal(output.state, 'speaking'); viewer(output);
  close(output.command.drift, DIALOGUE_GAZE_DEFAULTS.speakingDrift);
});

test('frame time cannot make frozen audio start; suspended AudioContext does not make start zero audible', () => {
  const director = waiting(), clock = { currentTime: 0, state: 'suspended' };
  director.startSpeech(clock, 0, 1);
  assert.notEqual(director.update(20).state, 'speaking');
  clock.state = 'running';
  assert.equal(director.update(0).state, 'speaking');
});

test('forward queued fragments preserve live speech and inter-sentence gaps keep viewer attention', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, .5, .5);
  director.startSpeech(clock, 2, .7);
  clock.currentTime = .5;
  assert.equal(director.update(0).state, 'speaking');
  director.setState('preparing');
  assert.equal(director.update(0).state, 'speaking', 'audio has priority over UI preparation');
  clock.currentTime = 1;
  const gap = director.update(10);
  assert.equal(gap.state, 'preparing');
  assert.equal(gap.phase, 'attending'); viewer(gap);
  assert.equal(director.update(10).phase, 'attending', 'no replayed thinking for the next sentence');
  clock.currentTime = 2;
  assert.equal(director.update(0).state, 'speaking');
});

test('a skipped complete audio fragment records spoken history without a one-frame speech flash', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, .5, .5);
  clock.currentTime = 2;
  const output = director.update(5);
  assert.equal(output.state, 'thinking');
  assert.equal(output.phase, 'attending'); viewer(output);
});

test('newTurn clears speech and history, including a simultaneous speaking request', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, 0, 1);
  director.update(0);
  director.setState('preparing');
  director.endSpeech({ turnPending: true });
  assert.equal(director.update(.4).phase, 'attending');
  director.setState('thinking', { newTurn: true });
  assert.equal(director.update(.4).phase, 'averted');
  director.startSpeech(clock, 0, 1);
  director.update(0);
  director.setState('speaking', { newTurn: true });
  assert.equal(director.update(0).state, 'preparing', 'cancelled audio cannot justify new speaking state');
});

test('ending speech cancels future clips, and final completion releases viewer only once', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.update(.4);
  director.startSpeech(clock, 5, 1);
  director.endSpeech();
  const release = director.update(0);
  viewer(release);
  assert.equal(release.active, false);
  close(release.command.drift, .1);
  clock.currentTime = 5.5;
  const later = director.update(20);
  assert.equal(later.state, 'idle');
  assert.equal(later.command, null);
});

test('endSpeech with a pending turn also cancels a scheduled clip without simulating audible speech', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, 10, 1);
  director.endSpeech({ turnPending: true });
  clock.currentTime = 10.5;
  const output = director.update(.4);
  assert.equal(output.state, 'preparing');
  assert.equal(output.phase, 'averted');
});

test('interrupt and playback stop retract all clips and waiting plans immediately', () => {
  for (const type of ['interrupted', 'playback_stopped']) {
    const director = waiting(), clock = { currentTime: 0 };
    director.update(.4);
    director.startSpeech(clock, 5, 1);
    director.onEvent({ type });
    const output = director.update(0);
    assert.equal(output.state, 'yielding'); viewer(output);
    clock.currentTime = 5.1;
    assert.equal(director.update(20).state, 'yielding');
    assert.equal(director.sample().command, null);
  }
});

test('temporary ducked yielding retains audible clips and can resume speaking until an actual interruption', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, 0, 3);
  director.update(0);
  director.setState('yielding');
  clock.currentTime = .5;
  const ducked = director.update(.5);
  assert.equal(ducked.state, 'yielding');
  assert.equal(ducked.phase, 'yielding'); viewer(ducked);
  director.setState('speaking');
  assert.equal(director.update(0).state, 'speaking');
  director.setState('yielding');
  director.stop('interrupted');
  clock.currentTime = 1;
  director.setState('speaking');
  assert.equal(director.update(0).state, 'preparing');
  assert.notEqual(director.update(.1).phase, 'speaking');
});

test('empty recognized speech returns to idle and retracts future audio', () => {
  for (const text of ['', '   ']) {
    const director = waiting(), clock = { currentTime: 0 };
    director.update(.4);
    director.startSpeech(clock, 5, 1);
    director.onEvent({ type: 'asr', text });
    const output = director.update(0);
    assert.equal(output.state, 'idle');
    assert.equal(output.active, false); viewer(output);
    clock.currentTime = 5.5;
    assert.equal(director.update(6).state, 'idle');
    assert.equal(director.sample().command, null);
  }
});

test('recoverable delivery errors preserve audio, while fatal errors retract attention and queued clips', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.startSpeech(clock, 1, 2);
  director.update(.4);
  director.onEvent({ type: 'error', recoverable: true });
  assert.equal(director.update(0).phase, 'averted');
  clock.currentTime = 1;
  assert.equal(director.update(0).state, 'speaking');
  director.onEvent({ type: 'error', recoverable: true });
  assert.equal(director.update(0).state, 'speaking');
  director.startSpeech(clock, 5, 1);
  director.onEvent({ type: 'error', recoverable: false });
  const release = director.update(0);
  assert.equal(release.state, 'idle'); viewer(release);
  clock.currentTime = 5.5;
  assert.equal(director.update(5).state, 'idle');
  assert.equal(director.sample().command, null);
});

test('generic stop cancels pending audio and never grabs an initially idle target', () => {
  const director = new DialogueGazeDirector(), clock = { currentTime: 0 };
  director.startSpeech(clock, 4, 1);
  director.stop('disconnect');
  clock.currentTime = 4.5;
  assert.deepEqual(director.update(8), { active: false, state: 'idle', phase: 'idle', phaseAge: 8,
    age: 8, style: 'natural', intent: 'neutral', plan: null, glance: null, command: null });
});

test('recording and thinking event duplicates are idempotent; a new turn id retracts queued speech', () => {
  const director = new DialogueGazeDirector(), clock = { currentTime: 0 };
  director.onEvent({ type: 'recording', state: true, turn_id: 'first' });
  director.update(.2);
  director.onEvent({ type: 'recording', state: true, turn_id: 'first' });
  close(director.age, .2);
  director.onEvent({ type: 'asr', turn_id: 'first' });
  director.update(.3);
  director.onEvent({ type: 'thinking', turn_id: 'first' });
  assert.equal(director.update(.1).phase, 'averted');
  director.startSpeech(clock, 3, 1);
  director.onEvent({ type: 'thinking', turn_id: 'second' });
  assert.equal(director.update(.1).phase, 'waiting');
  clock.currentTime = 3.5;
  assert.notEqual(director.update(0).state, 'speaking');
});

test('an external suspension advances plans and recovers only the current target', () => {
  const director = waiting();
  director.update(.4);
  const hidden = director.update(2, { suspended: true });
  assert.equal(hidden.phase, 'returned');
  assert.equal(hidden.active, false);
  assert.equal(hidden.command, null);
  const recovered = director.update(0);
  assert.equal(recovered.phase, 'returned'); viewer(recovered);
  assert.equal(director.update(0).command, null);
});

test('an unchanged target is resubmitted when manual or expression ownership ends', () => {
  const director = new DialogueGazeDirector();
  director.setState('listening');
  director.update(0);
  assert.equal(director.update(3, { suspended: true }).command, null);
  viewer(director.update(0));
  assert.equal(director.update(0).command, null);
});

test('suspended cancellation defers one release and cannot replay its previous away target', () => {
  const director = waiting();
  director.update(.4);
  director.update(0, { suspended: true });
  director.stop();
  assert.equal(director.update(2, { suspended: true }).command, null);
  const released = director.update(0);
  assert.equal(released.active, false); viewer(released);
  assert.equal(director.update(0).command, null);
});

test('actual audio can start and end during suspension without resurrecting a thinking glance', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.update(.4);
  director.startSpeech(clock, .5, .5);
  clock.currentTime = .6;
  assert.equal(director.update(.2, { suspended: true }).state, 'speaking');
  clock.currentTime = 1.5;
  assert.equal(director.update(2, { suspended: true }).command, null);
  const recovered = director.update(0);
  assert.equal(recovered.phase, 'attending'); viewer(recovered);
});

test('reduced motion scales displacement and micro radius without forcing any face channels', () => {
  const director = waiting();
  const ordinary = director.update(.4);
  const reduced = director.update(0, { reducedMotion: true });
  close(reduced.command.x, ordinary.command.x * .45);
  close(reduced.command.y, ordinary.command.y * .45);
  close(reduced.command.drift, ordinary.command.drift * .45);
  assert.deepEqual(Object.keys(reduced.command).sort(), ['drift', 'headDelayScale', 'headFollowScale', 'headRecruitScale', 'intervalScale', 'x', 'y']);
  assert.deepEqual(Object.keys(reduced).sort(), ['active', 'age', 'command', 'glance', 'intent', 'phase', 'phaseAge', 'plan', 'state', 'style']);
  assert.equal(director.update(0, { reducedMotion: true }).command, null);
});

test('disable cancels future speech and releases once; enable does not replay a previous glance', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.update(.4);
  director.startSpeech(clock, 5, 1);
  director.setEnabled(false);
  const release = director.update(0);
  assert.equal(release.active, false); viewer(release);
  director.setEnabled(false);
  assert.equal(director.update(2).command, null);
  clock.currentTime = 5.5;
  director.setEnabled(true);
  const enabled = director.update(0);
  assert.equal(enabled.state, 'idle');
  assert.equal(enabled.command, null);
});

test('invalid inputs cannot poison state, target, tuning or clocks', () => {
  const director = new DialogueGazeDirector({ aversionX: Infinity, aversionY: NaN, enabled: 'no' });
  assert.equal(director.enabled, true);
  assert.equal(director.setState('bogus'), false);
  for (const message of [null, false, 'speech', { type: 'unknown' }]) director.onEvent(message);
  director.configure({ aversionX: 100, aversionY: -100, aversionDuration: -1, repeatInterval: 0,
    listenDrift: -1, waitingDrift: 100, reducedMotionScale: 0, speakingIntervalScale: Infinity });
  assert.equal(director.cfg.aversionX, .5);
  assert.equal(director.cfg.aversionY, -.35);
  assert.equal(director.cfg.aversionDuration, .25);
  assert.equal(director.cfg.repeatInterval, 2);
  assert.equal(director.cfg.listenDrift, 0);
  assert.equal(director.cfg.waitingDrift, .2);
  assert.equal(director.cfg.reducedMotionScale, .1);
  assert.equal(director.cfg.speakingIntervalScale, DIALOGUE_GAZE_DEFAULTS.speakingIntervalScale);
  for (const [clock, start, duration] of [[null, 0, 1], [{ currentTime: NaN }, 0, 1],
    [{ currentTime: 0, state: 'closed' }, 0, 1], [{ currentTime: 0 }, -1, 1],
    [{ currentTime: 0 }, 0, 0], [{ currentTime: 0 }, Infinity, 1], [{ currentTime: 0 }, 0, Infinity]])
    assert.equal(director.startSpeech(clock, start, duration), false);
  director.setState('thinking');
  for (const dt of [NaN, Infinity, -1]) director.update(dt);
  assert.equal(director.age, 0);
  for (let i = 0; i < 2; i++) {
    const output = director.update(Number.MAX_VALUE);
    assert.ok(Number.isFinite(output.age));
    for (const value of Object.values(output.command || {})) assert.ok(Number.isFinite(value));
  }
});

test('clock replacement and backward schedules retract old stream clips', () => {
  const director = waiting(), oldClock = { currentTime: 0 }, newClock = { currentTime: 0 };
  director.startSpeech(oldClock, 5, 1);
  director.startSpeech(newClock, 2, 1);
  oldClock.currentTime = 5.5;
  assert.notEqual(director.update(0).state, 'speaking');
  newClock.currentTime = 2;
  assert.equal(director.update(0).state, 'speaking');
  director.startSpeech(newClock, 10, 1);
  director.startSpeech(newClock, 4, 1);
  newClock.currentTime = 10.5;
  const output = director.update(0);
  assert.notEqual(output.state, 'speaking');
  assert.equal(output.phase, 'attending');
});

test('phase and target agree at the same physical times over 30, 60 and 144 fps', () => {
  const observations = [30, 60, 144].map(fps => {
    const director = waiting();
    let elapsed = 0, target = null;
    const frames = [];
    for (const at of [.2, .35, .5, 1.15, 1.4, 4, 8.35, 8.6, 9.15, 9.5]) {
      while (elapsed < at - 1e-9) {
        const dt = Math.min(1 / fps, at - elapsed);
        const output = director.update(dt);
        if (output.command) target = output.command;
        elapsed += dt;
      }
      frames.push({ phase: director.sample().phase, target: { ...target } });
    }
    return frames;
  });
  assert.deepEqual(observations[0], observations[1]);
  assert.deepEqual(observations[0], observations[2]);
});

test('large frame gaps evaluate the present target rather than replay missed away events', () => {
  const director = waiting();
  director.update(0);
  const output = director.update(3);
  assert.equal(output.phase, 'returned');
  assert.equal(output.command, null, 'waiting and returned share the viewer target');
});

test('three immutable styles expose bounded face and existing eye-head coordination tuning', () => {
  assert.deepEqual(DIALOGUE_GAZE_STYLES.map(style => style.id), ['restrained', 'natural', 'lively']);
  assert.deepEqual(DIALOGUE_GAZE_STYLES.map(style => style.maxGlances), [2, 3, 4]);
  assert.deepEqual(DIALOGUE_GAZE_STYLES.map(style => style.headRecruitScale), [1, .75, .65]);
  assert.ok(Object.isFrozen(DIALOGUE_GAZE_STYLES));
  for (const style of DIALOGUE_GAZE_STYLES) {
    assert.ok(Object.isFrozen(style));
    for (const key of ['faceScale', 'browScale', 'lidScale', 'gazeScale', 'headFollowScale', 'headDelayScale', 'headRecruitScale'])
      assert.ok(Number.isFinite(style[key]) && style[key] > 0 && style[key] <= 1.25);
    const director = new DialogueGazeDirector({ style: style.id });
    director.setState('listening');
    const output = director.update(0);
    assert.equal(output.style, style.id);
    assert.equal(output.command.headFollowScale, style.headFollowScale);
    assert.equal(output.command.headDelayScale, style.headDelayScale);
    assert.equal(output.command.headRecruitScale, style.headRecruitScale);
    director.stop();
    const release = director.update(0);
    assert.equal(release.command.headFollowScale, 1);
    assert.equal(release.command.headDelayScale, 1);
    assert.equal(release.command.headRecruitScale, 1);
  }
});

test('identical variation seeds create one common blueprint for a fair three-style comparison', () => {
  const plans = DIALOGUE_GAZE_STYLES.map(style => {
    const director = new DialogueGazeDirector({ style: style.id });
    assert.equal(director.resetVariation('lab-style-compare'), true);
    director.setState('thinking', { newTurn: true });
    const snapshot = director.sample();
    assert.equal(snapshot.plan.styleAtStart, style.id);
    assert.equal(snapshot.plan.budget, style.maxGlances);
    assert.equal(snapshot.plan.glances.length, 4);
    return snapshot.plan.glances;
  });
  assert.deepEqual(plans[0], plans[1]);
  assert.deepEqual(plans[0], plans[2]);
});

test('each style enforces a finite turn budget and keeps attending after it is exhausted', () => {
  for (const style of DIALOGUE_GAZE_STYLES) {
    const director = new DialogueGazeDirector({ style: style.id, seed: 'finite-budget' });
    director.setState('thinking', { newTurn: true });
    const { glances } = director.sample().plan;
    let elapsed = 0, target = null;
    for (const glance of glances) {
      const time = (glance.start + glance.end) / 2;
      const output = director.update(time - elapsed);
      if (output.command) target = output.command;
      elapsed = time;
      if (glance.index < style.maxGlances) {
        assert.equal(output.phase, 'averted');
        assert.equal(output.glance.index, glance.index);
      } else {
        assert.equal(output.phase, 'attending');
        assert.equal(output.glance, null);
      }
    }
    const last = glances[style.maxGlances - 1];
    assert.ok(last.end < 33, 'default long-wait performance ends within a bounded horizon');
    const after = director.update(100);
    if (after.command) target = after.command;
    assert.equal(after.phase, 'attending');
    assert.equal(after.glance, null);
    assert.equal(target.x, 0); assert.equal(target.y, 0);
    assert.equal(director.update(3600).command, null);
    assert.equal(director.sample().phase, 'attending');
  }
});

test('new turns vary timing and directions reproducibly, without reseeding on repeated messages', () => {
  const build = () => {
    const director = new DialogueGazeDirector({ seed: 'repeatable-session' });
    const plans = [];
    for (let turn = 0; turn < 5; turn++) {
      director.onEvent({ type: 'thinking', turn_id: `turn-${turn}` });
      plans.push(director.sample().plan);
      director.update(.4);
      director.onEvent({ type: 'thinking', turn_id: `turn-${turn}` });
      director.onEvent({ type: 'text_delta', delta: 'later tokens', turn_id: `turn-${turn}` });
      assert.deepEqual(director.sample().plan, plans.at(-1));
    }
    return plans;
  };
  const first = build(), replay = build();
  assert.deepEqual(first, replay);
  assert.equal(new Set(first.map(plan => plan.seed)).size, 5);
  assert.ok(new Set(first.map(plan => plan.glances[0].x)).size > 1);
  assert.ok(new Set(first.map(plan => plan.glances[0].end - plan.glances[0].start)).size > 1);
  assert.ok(new Set(first.map(plan => plan.glances[1].start - plan.glances[0].start)).size > 1);
});

test('the exported plan seed reproduces its exact schedule independently of prior turn history', () => {
  const director = waiting();
  director.setState('thinking', { newTurn: true });
  director.setState('thinking', { newTurn: true });
  const original = director.sample().plan;
  const replay = new DialogueGazeDirector({ seed: original.seed, style: original.styleAtStart });
  replay.setState('thinking');
  assert.deepEqual(replay.sample().plan.glances, original.glances);
});

test('sampling never draws randomness, changes the plan, or lets callers mutate a shared glance', () => {
  const director = waiting();
  const plan = director.sample().plan;
  const previousRandom = Math.random;
  Math.random = () => { throw new Error('per-frame randomness is not permitted'); };
  try {
    director.update(.4);
    for (let i = 0; i < 20; i++) assert.deepEqual(director.sample().plan, plan);
  } finally { Math.random = previousRandom; }
  const snapshot = director.sample();
  assert.ok(Object.isFrozen(snapshot.plan));
  assert.ok(Object.isFrozen(snapshot.plan.glances));
  assert.ok(Object.isFrozen(snapshot.plan.glances[0]));
  assert.ok(Object.isFrozen(snapshot.glance));
  assert.throws(() => { snapshot.plan.glances[0].x = 100; }, TypeError);
  assert.throws(() => { snapshot.glance.start = 0; }, TypeError);
  assert.deepEqual(director.sample().plan, plan);
});

test('style changes preserve scheduled and audible speech, waiting age, and the original blueprint', () => {
  const director = waiting(), clock = { currentTime: 0 };
  const plan = director.sample().plan;
  const ordinary = director.update(.4), age = director.age;
  director.startSpeech(clock, 1, 2);
  assert.equal(director.setStyle('restrained'), true);
  const quieter = director.update(0);
  close(quieter.age, age);
  assert.equal(quieter.phase, 'averted');
  close(quieter.command.x, ordinary.command.x * .72);
  assert.deepEqual(quieter.plan.glances, plan.glances);
  assert.equal(quieter.plan.styleAtStart, 'natural');
  assert.equal(quieter.plan.budget, 2);
  clock.currentTime = 1.2;
  assert.equal(director.update(0).state, 'speaking');
  director.setStyle('lively');
  assert.equal(director.update(0).state, 'speaking');
  director.setState('yielding');
  director.setStyle('natural');
  assert.equal(director.update(0).state, 'yielding');
  director.setState('speaking');
  assert.equal(director.update(0).state, 'speaking');
});

test('switching to a smaller budget returns a later glance without restarting its elapsed plan', () => {
  const director = new DialogueGazeDirector({ style: 'lively', seed: 23 });
  director.setState('thinking', { newTurn: true });
  const fourth = director.sample().plan.glances[3];
  director.update((fourth.start + fourth.end) / 2);
  const elapsed = director.age;
  assert.equal(director.sample().glance.index, 3);
  director.setStyle('restrained');
  const returned = director.update(0);
  assert.equal(returned.phase, 'attending'); viewer(returned);
  close(returned.age, elapsed);
  director.update(fourth.end - elapsed + .1);
  director.setStyle('lively');
  assert.equal(director.update(0).phase, 'attending', 'a finished glance cannot revive after switching back');
});

test('resetVariation changes only the next turn and never clears a currently audible or scheduled clip', () => {
  const director = waiting(), clock = { currentTime: 0 };
  director.update(.4);
  const plan = director.sample().plan, age = director.age;
  director.startSpeech(clock, 1, 2);
  assert.equal(director.resetVariation('next-turn'), true);
  assert.deepEqual(director.sample().plan, plan);
  close(director.age, age);
  clock.currentTime = 1;
  assert.equal(director.update(0).state, 'speaking');
  assert.equal(director.resetVariation(77), true);
  clock.currentTime = 1.3;
  assert.equal(director.update(0).state, 'speaking');
  director.setState('thinking', { newTurn: true });
  assert.equal(director.sample().plan.seed, 77);
});

test('fast real replies preempt the planned first glance in all styles', () => {
  for (const style of DIALOGUE_GAZE_STYLES) {
    const director = new DialogueGazeDirector({ style: style.id }), clock = { currentTime: 0 };
    director.setState('thinking', { newTurn: true });
    director.update(.1);
    director.startSpeech(clock, .2, 2);
    clock.currentTime = .2;
    const started = director.update(.1);
    assert.equal(started.state, 'speaking');
    assert.equal(started.glance, null); viewer(started);
    clock.currentTime = .6;
    assert.equal(director.update(.4).phase, 'speaking');
  }
});

test('phaseAge uses absolute glance time and audio time rather than the first observed frame', () => {
  const director = waiting(), clock = { currentTime: 0 };
  const first = director.sample().plan.glances[0];
  const away = director.update(first.start + .24);
  close(away.phaseAge, .24);
  director.setState('preparing');
  close(director.update(.05).phaseAge, .29);
  director.startSpeech(clock, 1, 2);
  clock.currentTime = 1.4;
  const speech = director.update(0);
  close(speech.phaseAge, .4);
  close(speech.age, 0);
  clock.state = 'suspended';
  close(director.update(3).phaseAge, .4);
  clock.state = 'running';
  clock.currentTime = 1.6;
  close(director.update(0).phaseAge, .6);
});

test('all generated plans stay finite and separate under extreme valid tuning', () => {
  for (const seed of [0, 1, 19, 4294967295]) {
    for (const tuning of [{ thinkingDelay: -1, aversionDuration: -1, repeatInterval: -1 },
      { thinkingDelay: 100, aversionDuration: 100, repeatInterval: 100, aversionX: 100, aversionY: -100 }]) {
      const director = new DialogueGazeDirector({ ...tuning, seed, style: 'lively' });
      director.setState('thinking', { newTurn: true });
      const glances = director.sample().plan.glances;
      assert.equal(glances.length, 4);
      for (const [index, glance] of glances.entries()) {
        for (const value of Object.values(glance)) assert.ok(Number.isFinite(value));
        assert.ok(glance.start >= .09 && glance.end <= 239.1);
        assert.ok(glance.end - glance.start >= .2 - 1e-8 && glance.end - glance.start <= 1.7 + 1e-8);
        assert.ok(Math.abs(glance.x) <= .5 && Math.abs(glance.y) <= .35);
        if (index) assert.ok(glance.start - glances[index - 1].end >= 1 - 1e-8);
      }
    }
  }
});

test('invalid styles and seeds leave the current performance intact', () => {
  const director = waiting();
  director.update(.4);
  const original = director.sample();
  for (const style of [null, [], ['natural'], 'unknown', 3, undefined]) assert.equal(director.setStyle(style), false);
  for (const seed of [null, [], NaN, Infinity, '', 'x'.repeat(129), undefined])
    assert.equal(director.resetVariation(seed), false);
  director.configure({ style: 'unknown', seed: Infinity });
  assert.deepEqual(director.sample(), original);
});

test('suspended plans exhaust normally and never replay their missed budget when ownership returns', () => {
  const director = waiting();
  director.update(.4);
  const hidden = director.update(60, { suspended: true });
  assert.equal(hidden.phase, 'attending');
  assert.equal(hidden.glance, null);
  assert.equal(hidden.command, null);
  const recovered = director.update(0);
  assert.equal(recovered.phase, 'attending'); viewer(recovered);
  assert.equal(director.update(100).command, null);
});

test('a variable plan matches at its exact phase boundaries across 30, 60 and 144 fps', () => {
  const traces = [30, 60, 144].map(fps => {
    const director = new DialogueGazeDirector({ seed: 'phase-boundaries' });
    director.setState('thinking', { newTurn: true });
    const glances = director.sample().plan.glances;
    const points = glances.flatMap(glance => [glance.start, glance.start + .2, glance.end]);
    let elapsed = 0, target = null;
    return points.map(at => {
      while (elapsed < at - 1e-9) {
        const dt = Math.min(1 / fps, at - elapsed), output = director.update(dt);
        if (output.command) target = output.command;
        elapsed += dt;
      }
      return { phase: director.sample().phase, glance: director.sample().glance, target };
    });
  });
  assert.deepEqual(traces[0], traces[1]);
  assert.deepEqual(traces[0], traces[2]);
});

test('explicit neutral intent retains the default seeded attention behavior in every style', () => {
  for (const { id: style } of DIALOGUE_GAZE_STYLES) {
    const ordinary = new DialogueGazeDirector({ style, seed: 'neutral-regression' });
    const explicit = new DialogueGazeDirector({ style, seed: 'neutral-regression', intent: 'neutral' });
    for (const director of [ordinary, explicit]) director.setState('thinking', { newTurn: true });
    for (const dt of [0, .2, .2, .5, 1, 8, 40]) {
      assert.deepEqual(explicit.update(dt), ordinary.update(dt));
      assert.equal(explicit.sample().intent, 'neutral');
    }
  }
});

test('intent timing freezes at waiting start while the shared blueprint remains reproducible', () => {
  const neutral = new DialogueGazeDirector({ seed: 'intent-blueprint', style: 'lively' });
  neutral.setState('thinking', { newTurn: true });
  const blueprint = neutral.sample().plan.glances;
  for (const [intent, profile] of Object.entries(DIALOGUE_INTENT_PROFILES)) {
    const director = new DialogueGazeDirector({ intent, style: 'lively', seed: 'intent-blueprint' });
    director.setState('thinking', { newTurn: true });
    assert.deepEqual(director.sample().plan.glances, blueprint);
    assert.equal(director.sample().plan.intentAtStart, intent);
    assert.equal(director.sample().plan.budget, profile.budgetMax);
    const start = blueprint[0].start * profile.delayScale;
    const end = start + (blueprint[0].end - blueprint[0].start) * profile.durationScale;
    assert.equal(director.update(start - .001).phase, 'waiting');
    const away = director.update(.001);
    assert.equal(away.phase, 'averted');
    close(away.phaseAge, 0);
    close(away.glance.start, start); close(away.glance.end, end);
    close(away.command.x, blueprint[0].x * 1.15 * profile.gazeScale);
    close(away.command.drift, DIALOGUE_GAZE_DEFAULTS.waitingDrift * 1.15 * profile.gazeScale * profile.driftScale);
    director.update(end - start);
    assert.equal(director.sample().glance, null);
    assert.equal(director.sample().phase, profile.budgetMax === 1 ? 'attending' : 'returned');
  }
});

test('live intent changes scale an ongoing glance without moving its frozen onset or end', () => {
  const director = new DialogueGazeDirector({ intent: 'hesitant', style: 'lively', seed: 85 });
  director.setState('thinking', { newTurn: true });
  const blueprint = director.sample().plan, first = blueprint.glances[0];
  const start = first.start * DIALOGUE_INTENT_PROFILES.hesitant.delayScale;
  const out = director.update(start + .15), elapsed = director.age;
  assert.equal(out.phase, 'averted');
  director.setIntent('serious');
  const serious = director.update(0);
  assert.equal(serious.phase, 'averted');
  close(serious.glance.start, out.glance.start); close(serious.glance.end, out.glance.end);
  close(serious.command.x / out.command.x, .65 / .9);
  close(serious.phaseAge, out.phaseAge); close(serious.age, elapsed);
  assert.equal(serious.plan.budget, 1);
  assert.equal(serious.plan.intentAtStart, 'hesitant');
  assert.deepEqual(serious.plan.glances, blueprint.glances);
  director.setState('preparing');
  const preparing = director.update(.1);
  assert.equal(preparing.plan.intentAtStart, 'hesitant');
  close(preparing.phaseAge, .25);
});

test('switching intent cannot postpone a finished glance and replay its expired phase', () => {
  const director = new DialogueGazeDirector({ seed: 'expired-intent' });
  director.setState('thinking', { newTurn: true });
  const blueprint = director.sample().plan;
  director.update(blueprint.glances[0].end + .02);
  assert.equal(director.sample().phase, 'returned');
  const elapsed = director.age;
  director.setIntent('hesitant');
  const switched = director.update(0);
  assert.equal(switched.phase, 'returned');
  assert.equal(switched.glance, null);
  close(switched.age, elapsed);
  assert.equal(switched.plan.intentAtStart, 'neutral');
  assert.deepEqual(switched.plan.glances, blueprint.glances);
  director.update(60);
  for (const intent of ['serious', 'neutral', 'teasing', 'hesitant']) {
    director.setIntent(intent);
    const current = director.update(0);
    assert.equal(current.phase, 'attending');
    assert.equal(current.glance, null);
  }
});

test('intent budget reductions release a later glance and expanding after its end never revives it', () => {
  const director = new DialogueGazeDirector({ seed: 23, style: 'lively' });
  director.setState('thinking', { newTurn: true });
  const fourth = director.sample().plan.glances[3];
  director.update((fourth.start + fourth.end) / 2);
  assert.equal(director.sample().glance.index, 3);
  director.setIntent('serious');
  assert.equal(director.update(0).phase, 'attending');
  director.update(fourth.end - director.age + .01);
  director.setIntent('neutral');
  assert.equal(director.update(0).phase, 'attending');
  assert.equal(director.sample().glance, null);
  assert.equal(director.sample().plan.budget, 4);
});

test('live intent changes preserve future and audible audio clips and use the actual speech clock', () => {
  const director = waiting(), clock = { currentTime: 0, state: 'running' };
  director.update(.4);
  const plan = director.sample().plan;
  director.startSpeech(clock, 1, 2);
  const clips = structuredClone(director._clips);
  director.setIntent('teasing');
  director.update(.01);
  assert.notEqual(director.sample().state, 'speaking');
  assert.deepEqual(director._clips, clips);
  assert.deepEqual(director.sample().plan.glances, plan.glances);
  clock.currentTime = 1.4;
  close(director.update(0).phaseAge, .4);
  director.setIntent('hesitant');
  const changed = director.update(0);
  assert.equal(changed.state, 'speaking');
  close(changed.phaseAge, .4);
  assert.deepEqual(director._clips, clips);
  close(changed.command.drift, DIALOGUE_GAZE_DEFAULTS.speakingDrift * .9 * .8);
});

test('invalid director intents leave active output and frozen planning untouched', () => {
  const director = waiting();
  director.update(.4);
  const before = director.sample();
  for (const intent of ['auto', 'unknown', '__proto__', 'constructor', ['serious'], {}, null, 3, undefined]) {
    assert.equal(director.setIntent(intent), false);
    assert.deepEqual(director.sample(), before);
  }
  director.configure({ intent: ['serious'] });
  assert.deepEqual(director.sample(), before);
});

test('changing intent after stop cannot restore old clips or replay a discarded waiting plan', () => {
  const director = new DialogueGazeDirector({ intent: 'hesitant' }), clock = { currentTime: 0 };
  director.setState('thinking', { newTurn: true });
  const old = director.sample().plan;
  director.startSpeech(clock, 3, 2);
  director.stop('interrupted');
  director.setIntent('teasing');
  clock.currentTime = 3.5;
  assert.equal(director.update(10).state, 'yielding');
  assert.equal(director.sample().plan, null);
  assert.equal(director._clips.length, 0);
  director.setState('thinking', { newTurn: true });
  assert.equal(director.sample().plan.intentAtStart, 'teasing');
  assert.notEqual(director.sample().plan.turn, old.turn);
});
