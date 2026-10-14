import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { DAILY_GESTURE_MODEL_NAME, sampleGesturePose } from './render/gesture-poses.js';
import { VrmRig } from './render/vrm.js';
import { PerformanceDirector } from './drivers/performance.js';
import { PoseTransition } from './drivers/pose-transition.js';

const names = ['wave', 'deny', 'self', 'palm'];
const flat = pose => Object.values(pose).flat();
const close = (a, b, epsilon = 1e-12) => assert.ok(Math.abs(a - b) <= epsilon, `${a} != ${b}`);
const compare = (a, b) => {
  assert.deepEqual(Object.keys(a), Object.keys(b));
  for (const bone of Object.keys(a)) a[bone].forEach((value, i) => close(value, b[bone][i]));
};
const daily = (name, weight, strength = .4, phase = 0, options = {}) =>
  sampleGesturePose({ name, weight, strength, phase }, { profile: 'daily', ...options });

test('paths retain exact rest and all existing full-envelope targets', () => {
  for (const name of names) for (const strength of [0, .0001, .15, .4, .75, 1]) {
    for (const phase of [0, .25, .5, .75, 1]) {
      assert.ok(flat(daily(name, 0, strength, phase)).every(value => value === 0));
      compare(daily(name, 1, strength, phase), daily(name, 1, strength, phase, { paths: false }));
    }
  }
});

test('reference targets ignore paths and the live envelope at every strength and phase', () => {
  for (const name of names) for (const strength of [.0001, .15, .4, .75, 1]) {
    for (const phase of [0, .25, .75, 1]) {
      const expected = daily(name, 1, strength, phase, { paths: false });
      for (const weight of [0, .25, .5, .75, 1]) {
        compare(daily(name, weight, strength, phase, { reference: true }), expected);
        compare(daily(name, weight, strength, phase, { reference: true, paths: false }), expected);
      }
    }
  }
});

test('prepared paths change the intermediate shape without altering comparison or generic behavior', () => {
  for (const name of names) {
    const current = daily(name, .5);
    const comparison = daily(name, .5, .4, 0, { paths: false });
    assert.ok(flat(current).some((value, i) => Math.abs(value - flat(comparison)[i]) > .05), name);
    const target = daily(name, 1, .4, 0, { paths: false });
    for (const bone of Object.keys(target)) target[bone].forEach((value, i) => close(comparison[bone][i], value * .5));
    const frame = { name, weight: .43, strength: .6, phase: .25 };
    compare(sampleGesturePose(frame), sampleGesturePose(frame, { paths: false }));
    compare(sampleGesturePose(frame, { profile: 'unknown', paths: true }), sampleGesturePose(frame));
  }
});

test('each authored path segment stays inside its endpoint angles without overshoot', () => {
  for (const name of names) for (const strength of [.15, .4, .75, 1]) {
    const keys = [0, .5, 1].map(weight => flat(daily(name, weight, strength)));
    for (let segment = 0; segment < 2; ++segment) {
      for (let step = 0; step <= 100; ++step) {
        const values = flat(daily(name, (segment + step / 100) / 2, strength));
        values.forEach((value, i) => {
          assert.ok(value >= Math.min(keys[segment][i], keys[segment + 1][i]) - 1e-12);
          assert.ok(value <= Math.max(keys[segment][i], keys[segment + 1][i]) + 1e-12);
        });
      }
    }
  }
});

test('paths have matching middle velocities without a forced middle stop', () => {
  const h = 1e-5;
  for (const name of names) for (const phase of [0, .25]) {
    const before = flat(daily(name, .5 - h, .4, phase));
    const middle = flat(daily(name, .5, .4, phase));
    const after = flat(daily(name, .5 + h, .4, phase));
    let movingAxes = 0;
    middle.forEach((value, i) => {
      const left = (value - before[i]) / h, right = (after[i] - value) / h;
      close(left, right, .003);
      if (Math.abs(left) > .1) ++movingAxes;
    });
    assert.ok(movingAxes > 0, `${name} pauses at its preparation key`);
  }
});

test('director envelope keeps path velocity gentle at lift, arrival, and return', () => {
  const h = 1e-5;
  for (const name of names) {
    const clock = { currentTime: 0 }, director = new PerformanceDirector();
    director.preview(name, clock);
    const at = time => {
      clock.currentTime = time;
      // Isolate the authored path from the deliberately oscillating wrist.
      return flat(daily(name, director.sample().weight, .4));
    };
    for (const [from, to] of [[0, h], [.35 - h, .35], [2.4 - h, 2.4]]) {
      const a = at(from), b = at(to);
      a.forEach((value, i) => close((b[i] - value) / h, 0, .003));
    }
  }
});

test('wave and denial wrist sway waits until the hand has passed preparation', () => {
  for (const name of ['wave', 'deny']) {
    for (const weight of [.1, .25, .5]) compare(daily(name, weight, .4, 0), daily(name, weight, .4, .25));
    const before = daily(name, .75, .4, 0), swaying = daily(name, .75, .4, .25);
    assert.deepEqual(before.rightUpperArm, swaying.rightUpperArm);
    assert.deepEqual(before.rightLowerArm, swaying.rightLowerArm);
    assert.ok(swaying.rightHand[1] > before.rightHand[1]);
    close(swaying.rightHand[0], before.rightHand[0]);
    close(swaying.rightHand[2], before.rightHand[2]);
  }
});

test('reduced motion keeps the .4 endpoint while limiting added preparation', () => {
  for (const name of names) for (const strength of [.15, .4, .75, 1]) {
    const target = daily(name, 1, strength, .25);
    const reducedTarget = daily(name, 1, strength, .25, { reduced: true });
    for (const bone of Object.keys(target)) target[bone].forEach((value, i) => close(reducedTarget[bone][i], value * .4));
    const prepared = daily(name, .5, strength);
    const oldHalf = daily(name, .5, strength, 0, { paths: false });
    const reduced = daily(name, .5, strength, 0, { reduced: true });
    const oldReducedHalf = daily(name, .5, strength, 0, { reduced: true, paths: false });
    for (const bone of Object.keys(prepared)) prepared[bone].forEach((value, i) => {
      close(reduced[bone][i] - oldReducedHalf[bone][i], (value - oldHalf[bone][i]) * .4 * .5);
    });
    assert.ok(flat(daily(name, 0, strength, 0, { reduced: true })).every(value => value === 0));
  }
});

test('reduced preparation bounds peak quaternion steps under production timing', () => {
  const bones = ['rightUpperArm', 'rightLowerArm', 'rightHand'];
  const peakStep = (name, strength, duration, paths) => {
    const clock = { currentTime: 0 }, director = new PerformanceDirector(), transition = new PoseTransition();
    director.preview(name, clock);
    director.action.strength = strength;
    director.action.duration = duration;
    const actual = Object.fromEntries(bones.map(bone => [bone, new THREE.Quaternion()]));
    const rotation = xyz => new THREE.Quaternion().setFromEuler(new THREE.Euler(...(xyz || [0, 0, 0])));
    let peak = 0;
    for (let step = 0; step <= Math.ceil((duration + 1) * 60); ++step) {
      clock.currentTime = step / 60;
      const frame = director.sample();
      const pose = sampleGesturePose(frame, { profile: 'daily', reduced: true, paths });
      const reference = sampleGesturePose(frame, { profile: 'daily', reduced: true, reference: true, paths });
      const targets = Object.fromEntries(bones.map(bone => [bone, rotation(pose[bone])]));
      const references = Object.fromEntries(bones.map(bone => [bone, rotation(reference[bone])]));
      const next = transition.update(frame.transition, actual, targets, 1 / 60, references);
      for (const bone of bones) {
        const before = actual[bone].clone();
        if (next?.[bone]) actual[bone].copy(next[bone]);
        else actual[bone].slerp(targets[bone], 1 - Math.exp(-14 / 60));
        peak = Math.max(peak, before.angleTo(actual[bone]));
      }
    }
    return peak;
  };
  for (const name of names) for (const strength of [.15, .4, .75, 1]) {
    for (const duration of [.3, .65, 1, 2.4]) {
      const oldPeak = peakStep(name, strength, duration, false);
      const newPeak = peakStep(name, strength, duration, true);
      assert.ok(newPeak <= oldPeak * 1.25, `${name} strength ${strength} duration ${duration}: ${newPeak} > ${oldPeak} × 1.25`);
    }
  }
});

test('malformed path samples remain finite and later samples are isolated', () => {
  for (const name of names) for (const value of [NaN, Infinity, -Infinity, -1, 2]) {
    assert.ok(flat(daily(name, value, value, value)).every(Number.isFinite));
  }
  assert.deepEqual(daily('other', .5), {});
  const pose = daily('self', .5), expected = [...pose.rightLowerArm];
  pose.rightLowerArm[0] = 99;
  assert.deepEqual(daily('self', .5).rightLowerArm, expected);
});

test('the path switch follows daily calibration and resets snapshots without moving actual arms', () => {
  const rig = new VrmRig();
  assert.deepEqual(rig.getGesturePaths(), { available: false, enabled: false });
  const arm = new THREE.Object3D();
  arm.quaternion.setFromEuler(new THREE.Euler(.1, -.2, .3));
  rig._state = { vrm: { meta: { name: DAILY_GESTURE_MODEL_NAME } }, arms: { rightUpperArm: { node: arm } } };
  assert.deepEqual(rig.getGesturePaths(), { available: true, enabled: true });
  const before = arm.quaternion.clone();
  rig.poseTransition.update({ id: 1, reason: 'start', from: 'rest', to: 'wave' },
    { rightUpperArm: arm.quaternion }, { rightUpperArm: new THREE.Quaternion() }, .01);
  rig.setGesturePaths(true);
  assert.ok(rig.poseTransition.getDiagnostic());
  rig.setGesturePaths(false);
  assert.equal(rig.poseTransition.getDiagnostic(), null);
  assert.ok(arm.quaternion.equals(before));
  assert.deepEqual(rig.getGesturePaths(), { available: true, enabled: false });
  rig.setGesturePaths(true);
  rig.setGestureCalibration(false);
  assert.deepEqual(rig.getGesturePaths(), { available: false, enabled: false });
  rig.setGestureCalibration(true);
  assert.deepEqual(rig.getGesturePaths(), { available: true, enabled: true });
  rig._state.vrm.meta.name = 'Another VRM';
  assert.deepEqual(rig.getGesturePaths(), { available: false, enabled: false });
});
