import assert from 'node:assert/strict';
import test from 'node:test';
import { HAND_BONE_NAMES, HAND_POSE_NAMES, resolveHandProfile, sampleHandPose } from './render/hand-poses.js';
import { FINGER_BONE_NAMES, sampleFingerPose } from './render/finger-poses.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';

const gestures = ['wave', 'deny', 'self', 'palm'];
const thumbJoints = ['Metacarpal', 'Proximal', 'Distal'];
const daily = (frame = {}, options = {}) => sampleHandPose(frame, { profile: 'daily', ...options });
const close = (a, b, epsilon = 1e-12) => assert.ok(Math.abs(a - b) <= epsilon, `${a} != ${b}`);
const allClose = (a, b, epsilon = 1e-12) => {
  assert.deepEqual(Object.keys(a), Object.keys(b));
  for (const bone of HAND_BONE_NAMES) a[bone].forEach((value, i) => close(value, b[bone][i], epsilon));
};
const flat = pose => Object.values(pose).flat();

test('complete hand profile is confined to exact Daily and exposes 30 unique joint names', () => {
  assert.equal(resolveHandProfile(DAILY_GESTURE_MODEL_NAME), 'daily');
  assert.equal(resolveHandProfile(DAILY_GESTURE_MODEL_NAME, false), 'generic');
  for (const name of [undefined, 'Seed-san', 'seed-san daily study', 'Another VRM']) {
    assert.equal(resolveHandProfile(name), 'generic');
  }
  for (const profile of [undefined, 'generic', 'other']) {
    assert.deepEqual(sampleHandPose({ name: 'wave', weight: 1 }, { profile, preview: { name: 'open' } }), {});
  }
  assert.deepEqual(sampleHandPose(), {});
  assert.deepEqual(sampleHandPose(null, null), {});
  assert.equal(HAND_BONE_NAMES.length, 30);
  assert.equal(new Set(HAND_BONE_NAMES).size, 30);
  assert.ok(Object.isFrozen(HAND_BONE_NAMES));
  assert.deepEqual(HAND_BONE_NAMES.slice(0, 24), FINGER_BONE_NAMES);
  assert.deepEqual(Object.keys(daily()), HAND_BONE_NAMES);
  assert.deepEqual(HAND_POSE_NAMES, ['relaxed', 'open', 'softGrip']);
  assert.ok(Object.isFrozen(HAND_POSE_NAMES));
});

test('automatic regular fingers exactly retain the existing finger target contract', () => {
  for (const name of [undefined, 'unknown', 'toString', ...gestures]) {
    for (const weight of [0, .1, .5, 1]) for (const strength of [0, .1, .4, 1]) {
      for (const reduced of [false, true]) {
        const frame = { name, weight, strength };
        const pose = daily(frame, { reduced });
        const existing = sampleFingerPose(frame, { profile: 'daily', reduced });
        for (const bone of FINGER_BONE_NAMES) assert.deepEqual(pose[bone], existing[bone]);
      }
    }
  }
});

test('rest thumb targets mirror each measured curl axis and self retains conservative relaxation', () => {
  const rest = daily();
  for (const joint of thumbJoints) {
    const right = rest[`rightThumb${joint}`];
    const left = rest[`leftThumb${joint}`];
    assert.ok(right[0] > 0 && right[2] > 0);
    close(left[0], right[0]);
    close(left[1], -right[1]);
    close(left[2], -right[2]);
    assert.ok(Math.hypot(...right) < .11);
  }
  for (const weight of [0, .5, 1]) for (const strength of [0, .4, 1]) {
    const self = daily({ name: 'self', weight, strength });
    for (const side of ['left', 'right']) for (const joint of thumbJoints) {
      const bone = `${side}Thumb${joint}`;
      assert.deepEqual(self[bone], rest[bone]);
    }
  }
});

test('wave, deny and palm continuously open only the gesturing thumb with no phase oscillator', () => {
  const rest = daily();
  for (const name of ['wave', 'deny', 'palm']) {
    const full = daily({ name, weight: 1, strength: 1 });
    const open = daily({}, { preview: { name: 'open' } });
    for (const joint of thumbJoints) {
      const right = `rightThumb${joint}`;
      const left = `leftThumb${joint}`;
      full[right].forEach((value, i) => close(value, open[right][i]));
      assert.deepEqual(full[left], rest[left]);
      assert.ok(Math.hypot(...full[right]) < Math.hypot(...rest[right]));
    }
    for (const weight of [.1, .5, .9]) {
      const pose = daily({ name, weight, strength: 1 });
      for (const joint of thumbJoints) {
        const bone = `rightThumb${joint}`;
        pose[bone].forEach((value, i) => close(value, rest[bone][i] + (full[bone][i] - rest[bone][i]) * weight));
      }
      for (const phase of [0, .25, .5, 1]) assert.deepEqual(daily({ name, weight, strength: 1, phase }), pose);
    }
  }
});

test('unknown, cancelled, zero-envelope and zero-strength frames return rest', () => {
  const rest = daily();
  for (const frame of [null, {}, 'wave', 1, { name: 'unknown', weight: 1 }, { name: 'constructor', weight: 1 },
    { name: null, weight: 0, state: 'listening', transition: { reason: 'interrupted' } }]) {
    assert.deepEqual(daily(frame), rest);
  }
  for (const name of gestures) {
    assert.deepEqual(daily({ name, weight: 0, strength: 1 }), rest);
    assert.deepEqual(daily({ name, weight: 1, strength: 0 }), rest);
  }
});

test('automatic reduced motion scales every expressive delta by .4 and leaves rest unchanged', () => {
  const rest = daily();
  assert.deepEqual(daily({}, { reduced: true }), rest);
  for (const name of gestures) for (const weight of [.1, .5, 1]) {
    const normal = daily({ name, weight, strength: .4 });
    const reduced = daily({ name, weight, strength: .4 }, { reduced: true });
    for (const bone of HAND_BONE_NAMES) normal[bone].forEach((value, i) => {
      close(reduced[bone][i] - rest[bone][i], (value - rest[bone][i]) * .4);
    });
  }
});

test('three complete inspection poses have distinct bounded curl and preserve the other automatic hand', () => {
  const frame = { name: 'wave', weight: 1, strength: .4 };
  const auto = daily(frame);
  for (const side of ['left', 'right']) {
    const poses = Object.fromEntries(HAND_POSE_NAMES.map(name => [name, daily(frame, { preview: { name, side } })]));
    for (const bone of HAND_BONE_NAMES) {
      if (!bone.startsWith(side)) for (const pose of Object.values(poses)) assert.deepEqual(pose[bone], auto[bone]);
      else {
        const magnitude = name => Math.hypot(...poses[name][bone]);
        assert.ok(magnitude('open') < magnitude('relaxed'));
        assert.ok(magnitude('relaxed') < magnitude('softGrip'));
        assert.ok(magnitude('softGrip') < .5);
      }
    }
    const relaxed = daily({}, { preview: { name: 'relaxed', side } });
    for (const bone of HAND_BONE_NAMES.filter(bone => bone.startsWith(side))) {
      assert.deepEqual(poses.relaxed[bone], relaxed[bone]);
    }
  }
});

test('inspection replaces the full selected hand independent of gesture strength, timing and reduced mode', () => {
  for (const name of HAND_POSE_NAMES) {
    const expected = daily({}, { preview: { name } });
    const explicitRight = daily({}, { preview: { name, side: 'right' } });
    assert.deepEqual(expected, explicitRight);
    for (const frame of [{ name: 'wave', weight: 1, strength: 1 }, { name: 'palm', weight: .5, strength: .2 },
      { name: null, weight: 0, strength: 0 }]) {
      for (const reduced of [false, true]) {
        const pose = daily(frame, { reduced, preview: { name } });
        for (const bone of HAND_BONE_NAMES.filter(bone => bone.startsWith('right'))) {
          assert.deepEqual(pose[bone], expected[bone]);
        }
      }
    }
  }
});

test('left and right inspection targets are mirrored without accumulating automatic targets', () => {
  for (const name of HAND_POSE_NAMES) {
    const right = daily({ name: 'palm', weight: 1 }, { preview: { name, side: 'right' } });
    const left = daily({ name: 'wave', weight: 1 }, { preview: { name, side: 'left' } });
    for (const bone of HAND_BONE_NAMES.filter(bone => bone.startsWith('right'))) {
      const opposite = bone.replace('right', 'left');
      close(left[opposite][0], right[bone][0]);
      close(left[opposite][1], -right[bone][1]);
      close(left[opposite][2], -right[bone][2]);
    }
  }
});

test('invalid inspection input is ignored and does not partially alter a hand', () => {
  const frame = { name: 'deny', weight: .5, strength: .4 };
  const expected = daily(frame);
  for (const preview of [undefined, null, false, 'open', [], {}, { name: 'constructor' }, { name: Symbol('open') },
    { name: 'open', side: 'both' }, { name: 'open', side: null }, { name: 'open', side: 'Right' },
    { name: 'softGrip', side: 1 }, { name: 1, side: 'right' }]) {
    assert.deepEqual(daily(frame, { preview }), expected);
  }
});

test('malformed numeric fields are finite and bounded, including conversion failures', () => {
  const rest = daily();
  for (const name of gestures) for (const value of [NaN, Infinity, -Infinity, -1, 2, Symbol('bad'),
    { valueOf() { throw new Error('invalid'); } }]) {
    const pose = daily({ name, weight: value, strength: value });
    assert.ok(flat(pose).every(Number.isFinite));
    for (const bone of HAND_BONE_NAMES) assert.ok(Math.hypot(...pose[bone]) <= Math.hypot(...rest[bone]) + 1e-12);
  }
});

test('thumb activation is continuous at zero, the .15 threshold and envelope boundaries', () => {
  for (const name of ['wave', 'deny', 'palm']) {
    for (const strength of [0, .15, 1]) {
      const before = daily({ name, weight: 1, strength: strength - 1e-7 });
      const after = daily({ name, weight: 1, strength: strength + 1e-7 });
      allClose(before, after, 1e-6);
    }
    for (const weight of [0, 1]) {
      allClose(daily({ name, weight: weight - 1e-7 }), daily({ name, weight: weight + 1e-7 }), 1e-6);
    }
  }
});

test('returned targets and preview inputs cannot mutate future targets or each other', () => {
  const preview = { name: 'softGrip', side: 'right' };
  const first = daily({ name: 'wave', weight: .5 }, { preview });
  const expected = daily({ name: 'wave', weight: .5 }, { preview });
  first.rightThumbProximal[0] = 99;
  first.leftIndexProximal[2] = 99;
  delete first.rightLittleDistal;
  assert.deepEqual(daily({ name: 'wave', weight: .5 }, { preview }), expected);
  assert.deepEqual(preview, { name: 'softGrip', side: 'right' });
  const automatic = daily({ name: 'wave', weight: .5 });
  const other = daily({ name: 'wave', weight: .5 });
  for (const bone of HAND_BONE_NAMES) assert.notEqual(automatic[bone], other[bone]);
});
