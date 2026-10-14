import assert from 'node:assert/strict';
import test from 'node:test';
import { BODY_BONE_NAMES, resolveBodyProfile, sampleBodyPose } from './render/body-poses.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';
import { PerformanceDirector } from './drivers/performance.js';

const names = ['wave', 'deny', 'self', 'palm'];
const daily = (frame = {}, options = {}) => sampleBodyPose(frame, { profile: 'daily', ...options });
const flat = pose => Object.values(pose).flat();
const close = (a, b, epsilon = 1e-12) => assert.ok(Math.abs(a - b) <= epsilon, `${a} != ${b}`);
const rest = { chest: [0, 0, 0], rightShoulder: [0, 0, 0] };

test('body support is opt-in for the exact Daily profile and leaves other rigs alone', () => {
  assert.equal(resolveBodyProfile(DAILY_GESTURE_MODEL_NAME), 'daily');
  assert.equal(resolveBodyProfile(DAILY_GESTURE_MODEL_NAME, false), 'generic');
  for (const name of [undefined, 'Seed-san', 'seed-san daily study', 'Another VRM']) {
    assert.equal(resolveBodyProfile(name), 'generic');
  }
  for (const profile of [undefined, 'generic', 'other']) {
    assert.deepEqual(sampleBodyPose({ name: 'wave', weight: 1 }, { profile }), {});
  }
  assert.deepEqual(sampleBodyPose(), {});
});

test('rest, cancelled, unknown and zero-strength frames contain no body offset', () => {
  assert.deepEqual(BODY_BONE_NAMES, ['chest', 'rightShoulder']);
  assert.ok(Object.isFrozen(BODY_BONE_NAMES));
  for (const frame of [null, {}, { name: 'unknown', weight: 1 }, { name: 'toString', weight: 1 },
    { name: 'constructor', weight: 1 }, { name: null, weight: 0, state: 'listening', transition: { reason: 'interrupted' } }]) {
    assert.deepEqual(daily(frame), rest);
  }
  for (const name of names) {
    assert.deepEqual(daily({ name, weight: 0, strength: 1 }), rest);
    assert.deepEqual(daily({ name, weight: 1, strength: 0 }), rest);
    assert.deepEqual(daily({ name, weight: 0, strength: 1 }, { reduced: true }), rest);
  }
});

test('audited directions stay modest and do not add chest roll or other joint offsets', () => {
  for (const name of names) for (const strength of [.001, .15, .4, .75, 1]) {
    const pose = daily({ name, weight: 1, strength });
    assert.deepEqual(Object.keys(pose), BODY_BONE_NAMES);
    assert.ok(pose.chest[0] > 0 && pose.chest[1] < 0);
    assert.equal(pose.chest[2], 0);
    assert.equal(pose.rightShoulder[0], 0);
    assert.ok(pose.rightShoulder[1] > 0 && pose.rightShoulder[2] < 0);
    assert.ok(Math.hypot(...pose.chest) <= .018);
    assert.ok(Math.hypot(...pose.rightShoulder) <= .035);
  }
  const self = daily({ name: 'self', weight: 1 });
  for (const name of names.filter(name => name !== 'self')) {
    const pose = daily({ name, weight: 1 });
    assert.ok(Math.hypot(...self.chest) < Math.hypot(...pose.chest));
    assert.ok(Math.hypot(...self.rightShoulder) < Math.hypot(...pose.rightShoulder));
  }
});

test('the existing envelope scales support without introducing phase-driven motion', () => {
  for (const name of names) {
    const full = flat(daily({ name, weight: 1, strength: .4 }));
    for (const weight of [.1, .5, .9]) {
      const pose = daily({ name, weight, strength: .4 });
      flat(pose).forEach((value, i) => close(value, full[i] * weight));
      for (const phase of [0, .25, .5, .75, 1]) assert.deepEqual(daily({ name, weight, strength: .4, phase }), pose);
    }
  }
});

test('strength activates continuously and monotonically through the protocol minimum', () => {
  for (const name of names) {
    let previous = 0;
    for (const strength of [0, .00001, .05, .14999, .15, .15001, .4, .75, 1]) {
      const value = Math.hypot(...daily({ name, weight: 1, strength }).rightShoulder);
      assert.ok(value >= previous);
      previous = value;
    }
    const before = flat(daily({ name, weight: 1, strength: .15 - 1e-7 }));
    const after = flat(daily({ name, weight: 1, strength: .15 + 1e-7 }));
    before.forEach((value, i) => close(value, after[i], 1e-7));
    assert.ok(Math.hypot(...daily({ name, weight: 1, strength: 1e-9 }).rightShoulder) < 1e-8);
  }
});

test('reduced motion preserves exact rest and limits all support offsets to forty percent', () => {
  assert.deepEqual(daily({}, { reduced: true }), rest);
  for (const name of names) for (const weight of [.1, .5, 1]) for (const strength of [.05, .15, .4, 1]) {
    const normal = flat(daily({ name, weight, strength }));
    const reduced = flat(daily({ name, weight, strength }, { reduced: true }));
    normal.forEach((value, i) => close(reduced[i], value * .4));
  }
});

test('production timing reaches rest, full support and retraction without endpoint jumps', () => {
  const h = 1e-5;
  for (const name of names) {
    const at = time => {
      const clock = { currentTime: 0 }, director = new PerformanceDirector();
      director.preview(name, clock);
      clock.currentTime = time;
      return flat(daily(director.sample()));
    };
    for (const [from, to] of [[0, h], [.35 - h, .35], [2.4 - h, 2.4]]) {
      const a = at(from), b = at(to);
      a.forEach((value, i) => close((b[i] - value) / h, 0, 1e-4));
    }
    assert.deepEqual(at(0), flat(rest));
    assert.deepEqual(at(.35), flat(daily({ name, weight: 1 })));
    assert.deepEqual(at(2.4), flat(rest));
    const clock = { currentTime: 0 }, director = new PerformanceDirector();
    director.preview(name, clock);
    clock.currentTime = .2;
    assert.ok(flat(daily(director.sample())).some(value => value !== 0));
    director.stop('interrupted');
    assert.deepEqual(daily(director.sample()), rest);
  }
});

test('malformed numeric input remains finite within the audited amplitude caps', () => {
  const numeric = [NaN, Infinity, -Infinity, -1, 2, 'bad', '0.4', null];
  for (const name of names) for (const weight of numeric) for (const strength of numeric) {
    const pose = daily({ name, weight, strength });
    assert.ok(flat(pose).every(Number.isFinite));
    assert.ok(Math.hypot(...pose.chest) <= .018);
    assert.ok(Math.hypot(...pose.rightShoulder) <= .035);
  }
});

test('sampling neither mutates semantic frames nor shares target arrays with callers', () => {
  const frame = Object.freeze({ name: 'palm', weight: .5, strength: .4, phase: .3,
    transition: Object.freeze({ reason: 'start' }), anchor: 'why', source: 'llm' });
  const before = structuredClone(frame);
  const pose = daily(frame), expected = structuredClone(pose);
  pose.chest[0] = 99;
  delete pose.rightShoulder;
  assert.deepEqual(frame, before);
  assert.deepEqual(daily(frame), expected);
});
