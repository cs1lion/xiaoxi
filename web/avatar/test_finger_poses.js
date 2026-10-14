import assert from 'node:assert/strict';
import test from 'node:test';
import { FINGER_BONE_NAMES, resolveFingerProfile, sampleFingerPose } from './render/finger-poses.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';

const names = ['wave', 'deny', 'self', 'palm'];
const daily = (frame = {}, options = {}) => sampleFingerPose(frame, { profile: 'daily', ...options });
const close = (a, b, epsilon = 1e-12) => assert.ok(Math.abs(a - b) <= epsilon, `${a} != ${b}`);
const flat = pose => Object.values(pose).flat();

test('the profile is limited to the exact Daily model and generic samples leave fingers alone', () => {
  assert.equal(resolveFingerProfile(DAILY_GESTURE_MODEL_NAME), 'daily');
  assert.equal(resolveFingerProfile(DAILY_GESTURE_MODEL_NAME, false), 'generic');
  for (const name of [undefined, 'Seed-san', 'seed-san daily study', 'Another VRM']) {
    assert.equal(resolveFingerProfile(name), 'generic');
  }
  for (const profile of [undefined, 'generic', 'other']) {
    assert.deepEqual(sampleFingerPose({ name: 'wave', weight: 1 }, { profile }), {});
  }
  assert.deepEqual(sampleFingerPose(), {});
});

test('relaxed rest covers 24 regular joints with mirrored curl and distinct fingers', () => {
  const pose = daily();
  assert.equal(FINGER_BONE_NAMES.length, 24);
  assert.equal(new Set(FINGER_BONE_NAMES).size, 24);
  assert.ok(Object.isFrozen(FINGER_BONE_NAMES));
  assert.deepEqual(Object.keys(pose), FINGER_BONE_NAMES);
  const expected = { Index: [.08, .10, .05], Middle: [.12, .16, .08], Ring: [.16, .20, .10], Little: [.20, .24, .12] };
  for (const [digit, curls] of Object.entries(expected)) {
    ['Proximal', 'Intermediate', 'Distal'].forEach((joint, i) => {
      assert.deepEqual(pose[`right${digit}${joint}`], [0, 0, curls[i]]);
      assert.deepEqual(pose[`left${digit}${joint}`], [0, 0, -curls[i]]);
    });
  }
  assert.ok(FINGER_BONE_NAMES.every(bone => !/Thumb|Hand|Arm/.test(bone)));
});

test('only the gesturing right hand opens and every expression retains slight curl', () => {
  const rest = daily();
  for (const name of names) {
    const pose = daily({ name, weight: 1, strength: .4 });
    for (const bone of FINGER_BONE_NAMES) {
      if (bone.startsWith('left')) assert.deepEqual(pose[bone], rest[bone]);
      else {
        assert.ok(pose[bone][2] > 0 && pose[bone][2] < rest[bone][2]);
        assert.equal(pose[bone][0], 0);
        assert.equal(pose[bone][1], 0);
      }
    }
  }
  for (const bone of FINGER_BONE_NAMES.filter(bone => bone.startsWith('right'))) {
    const wave = daily({ name: 'wave', weight: 1, strength: .4 })[bone][2];
    const deny = daily({ name: 'deny', weight: 1, strength: .4 })[bone][2];
    const palm = daily({ name: 'palm', weight: 1, strength: .4 })[bone][2];
    const self = daily({ name: 'self', weight: 1, strength: .4 })[bone][2];
    close(wave, deny);
    assert.ok(wave < palm && palm < self && self < rest[bone][2]);
  }
});

test('empty, unknown, canceled, and zero-strength frames return relaxed rest', () => {
  const rest = daily();
  for (const frame of [null, {}, { name: 'unknown', weight: 1 }, { name: 'toString', weight: 1 },
    { name: null, weight: 0, state: 'listening', transition: { reason: 'interrupted' } }]) {
    assert.deepEqual(daily(frame), rest);
  }
  for (const name of names) {
    assert.deepEqual(daily({ name, weight: 0, strength: 1 }), rest);
    assert.deepEqual(daily({ name, weight: 1, strength: 0 }), rest);
  }
});

test('envelope gates opening continuously without phase-dependent motion', () => {
  const rest = daily();
  for (const name of names) {
    const full = daily({ name, weight: 1, strength: .4 });
    for (const weight of [.1, .5, .9]) {
      const pose = daily({ name, weight, strength: .4 });
      for (const bone of FINGER_BONE_NAMES) {
        pose[bone].forEach((value, i) => close(value, rest[bone][i] + (full[bone][i] - rest[bone][i]) * weight));
      }
      for (const phase of [0, .25, .5, 1]) assert.deepEqual(daily({ name, weight, strength: .4, phase }), pose);
    }
  }
});

test('strength opens gently, with continuous activation through the protocol minimum', () => {
  const rest = daily();
  for (const name of names) {
    let previous = rest.rightLittleIntermediate[2];
    for (const strength of [0, .00001, .05, .14999, .15, .15001, .4, .75, 1]) {
      const value = daily({ name, weight: 1, strength }).rightLittleIntermediate[2];
      assert.ok(value <= previous && value > 0);
      previous = value;
    }
    const before = daily({ name, weight: 1, strength: .15 - 1e-7 });
    const after = daily({ name, weight: 1, strength: .15 + 1e-7 });
    flat(before).forEach((value, i) => close(value, flat(after)[i], 1e-6));
  }
});

test('reduced motion keeps relaxed rest while reducing expressive changes', () => {
  const rest = daily();
  assert.deepEqual(daily({}, { reduced: true }), rest);
  for (const name of names) for (const weight of [.1, .5, 1]) {
    const normal = daily({ name, weight, strength: .4 });
    const reduced = daily({ name, weight, strength: .4 }, { reduced: true });
    for (const bone of FINGER_BONE_NAMES) normal[bone].forEach((value, i) => {
      close(reduced[bone][i] - rest[bone][i], (value - rest[bone][i]) * .4);
    });
  }
});

test('malformed numeric frames stay finite and cannot exceed relaxed curl bounds', () => {
  const rest = daily();
  for (const name of names) for (const value of [NaN, Infinity, -Infinity, -1, 2]) {
    const pose = daily({ name, weight: value, strength: value, phase: value });
    assert.ok(flat(pose).every(Number.isFinite));
    for (const bone of FINGER_BONE_NAMES) {
      assert.ok(Math.abs(pose[bone][2]) <= Math.abs(rest[bone][2]));
      assert.equal(pose[bone][0], 0);
      assert.equal(pose[bone][1], 0);
    }
  }
});

test('returned samples cannot mutate later finger targets', () => {
  const pose = daily({ name: 'wave', weight: .5 });
  const expected = [...pose.rightIndexProximal];
  pose.rightIndexProximal[2] = 99;
  delete pose.leftLittleDistal;
  assert.deepEqual(daily({ name: 'wave', weight: .5 }).rightIndexProximal, expected);
  assert.ok(daily().leftLittleDistal);
});
