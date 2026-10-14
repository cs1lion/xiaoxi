import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { DAILY_GESTURE_MODEL_NAME, resolveGestureProfile, sampleGesturePose } from './render/gesture-poses.js';
import { VrmRig } from './render/vrm.js';

const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} != ${b}`);
const compare = (a, b) => {
  assert.deepEqual(Object.keys(a), Object.keys(b));
  for (const bone of Object.keys(a)) a[bone].forEach((value, i) => close(value, b[bone][i]));
};

test('generic poses preserve the four existing authored targets', () => {
  compare(sampleGesturePose({ name: 'wave', weight: 1, phase: 0 }), {
    rightUpperArm: [-.35, 0, -.6], rightLowerArm: [-1.35, 0, 0], rightHand: [0, 0, 0] });
  compare(sampleGesturePose({ name: 'deny', weight: 1, phase: 0 }), {
    rightUpperArm: [-.25, 0, -.35], rightLowerArm: [-1.15, 0, 0], rightHand: [0, 0, 0] });
  compare(sampleGesturePose({ name: 'self', weight: 1 }), {
    rightUpperArm: [-.4, 0, -.15], rightLowerArm: [-1.2, 0, .2] });
  compare(sampleGesturePose({ name: 'palm', weight: 1 }), {
    rightUpperArm: [-.35, 0, -.4], rightLowerArm: [-.9, -.5, 0], rightHand: [0, -.45, 0] });
});

test('generic strength and reduced motion keep the existing multiplicative behavior', () => {
  const full = sampleGesturePose({ name: 'palm', weight: 1 });
  const scaled = sampleGesturePose({ name: 'palm', weight: .6, strength: .75 }, { reduced: true });
  for (const bone of Object.keys(full)) full[bone].forEach((value, i) => close(scaled[bone][i], value * .6 * .75 * .4));
});

test('reference samples use requested strength independently of the live envelope', () => {
  const resting = sampleGesturePose({ name: 'self', weight: 0, strength: .4 });
  assert.ok(Object.values(resting).flat().every(value => value === 0));
  compare(sampleGesturePose({ name: 'self', weight: 0, strength: .4 }, { reference: true }),
    sampleGesturePose({ name: 'self', weight: 1, strength: .4 }));
});

test('wave and denial retain their authored wrist phase behavior', () => {
  close(sampleGesturePose({ name: 'wave', weight: .8, strength: .5, phase: 1/12 }).rightHand[2], .12);
  close(sampleGesturePose({ name: 'deny', weight: .8, strength: .5, phase: 1/8 }).rightHand[1], .12);
});

test('unknown or missing gestures return no arm offsets and invalid inputs stay finite', () => {
  assert.deepEqual(sampleGesturePose(), {});
  assert.deepEqual(sampleGesturePose(null), {});
  assert.deepEqual(sampleGesturePose({ name: 'other', weight: 1 }), {});
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    for (const value of [NaN, Infinity, -Infinity, -1, 2]) {
      const sample = sampleGesturePose({ name, weight: value, strength: value, phase: value });
      assert.ok(Object.values(sample).flat().every(Number.isFinite));
    }
    const zero = sampleGesturePose({ name, weight: 1, strength: 0 });
    assert.ok(Object.values(zero).flat().every(value => value === 0));
  }
});

test('sampled arrays cannot mutate the next gesture target', () => {
  const one = sampleGesturePose({ name: 'self', weight: 1 });
  one.rightUpperArm[0] = 100;
  close(sampleGesturePose({ name: 'self', weight: 1 }).rightUpperArm[0], -.4);
});

test('the calibrated profile is limited to the exact authored Daily model', () => {
  assert.equal(resolveGestureProfile(DAILY_GESTURE_MODEL_NAME), 'daily');
  assert.equal(resolveGestureProfile(DAILY_GESTURE_MODEL_NAME, false), 'generic');
  for (const name of [undefined, 'Seed-san', 'seed-san daily study', 'Another VRM']) {
    assert.equal(resolveGestureProfile(name), 'generic');
  }
  compare(sampleGesturePose({ name: 'palm', weight: .8 }, { profile: 'unknown' }),
    sampleGesturePose({ name: 'palm', weight: .8 }));
});

test('Daily poses bend the local-X forearm instead of merely twisting it at normal speech strengths', () => {
  const restingAxis = new THREE.Vector3(-1, 0, 0);
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    for (const strength of [.4, .75]) {
      const angles = sampleGesturePose({ name, weight: 1, strength }, { profile: 'daily' }).rightLowerArm;
      const rotation = new THREE.Quaternion().setFromEuler(new THREE.Euler(...angles));
      const articulatedAxis = restingAxis.clone().applyQuaternion(rotation);
      assert.ok(restingAxis.angleTo(articulatedAxis) > .85, `${name} at ${strength} has no clear elbow bend`);
    }
  }
});

test('Daily strength smoothly reaches rest and honors reduced motion', () => {
  const normal = sampleGesturePose({ name: 'palm', weight: 1, strength: .4 }, { profile: 'daily' });
  const reduced = sampleGesturePose({ name: 'palm', weight: 1, strength: .4 }, { profile: 'daily', reduced: true });
  for (const bone of Object.keys(normal)) normal[bone].forEach((value, i) => close(reduced[bone][i], value * .4));
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    for (const zero of [{ weight: 0, strength: .75 }, { weight: 1, strength: 0 }]) {
      assert.ok(Object.values(sampleGesturePose({ name, ...zero }, { profile: 'daily' })).flat().every(value => value === 0));
    }
    const tiny = sampleGesturePose({ name, weight: 1, strength: .0001 }, { profile: 'daily' });
    assert.ok(Object.values(tiny).flat().every(value => Math.abs(value) < .002));
  }
});

test('Daily wave and denial add small wrist sway without changing the upper or lower arm target', () => {
  for (const name of ['wave', 'deny']) {
    const zero = sampleGesturePose({ name, weight: 1, strength: 1, phase: 0 }, { profile: 'daily' });
    const quarter = sampleGesturePose({ name, weight: 1, strength: 1, phase: .25 }, { profile: 'daily' });
    assert.deepEqual(quarter.rightUpperArm, zero.rightUpperArm);
    assert.deepEqual(quarter.rightLowerArm, zero.rightLowerArm);
    close(quarter.rightHand[1] - zero.rightHand[1], name === 'wave' ? .12 : .10);
    close(quarter.rightHand[0], zero.rightHand[0]);
    close(quarter.rightHand[2], zero.rightHand[2]);
    compare(sampleGesturePose({ name, weight: 0, strength: .4, phase: .25 }, { profile: 'daily', reference: true }),
      sampleGesturePose({ name, weight: 1, strength: .4, phase: .25 }, { profile: 'daily' }));
  }
});

test('Daily invalid input stays finite, and later samples are isolated', () => {
  for (const name of ['wave', 'deny', 'self', 'palm']) {
    for (const value of [NaN, Infinity, -Infinity, -1, 2]) {
      assert.ok(Object.values(sampleGesturePose({ name, weight: value, strength: value, phase: value },
        { profile: 'daily' })).flat().every(Number.isFinite));
    }
  }
  assert.deepEqual(sampleGesturePose({ name: 'other', weight: 1 }, { profile: 'daily' }), {});
  const one = sampleGesturePose({ name: 'self', weight: 1 }, { profile: 'daily' });
  const expected = [...one.rightHand];
  one.rightHand[0] = 100;
  assert.deepEqual(sampleGesturePose({ name: 'self', weight: 1 }, { profile: 'daily' }).rightHand, expected);
});

test('renderer calibration switch resets transition snapshots without moving the actual arm', () => {
  const rig = new VrmRig();
  assert.deepEqual(rig.getGestureCalibration(), { available: false, enabled: false, profile: 'generic' });
  const arm = new THREE.Object3D();
  arm.quaternion.setFromEuler(new THREE.Euler(.1, -.2, .3));
  rig._state = { vrm: { meta: { name: DAILY_GESTURE_MODEL_NAME } }, arms: { rightUpperArm: { node: arm } } };
  assert.deepEqual(rig.getGestureCalibration(), { available: true, enabled: true, profile: 'daily' });
  const before = arm.quaternion.clone();
  rig.poseTransition.update({ id: 1, reason: 'start', from: 'rest', to: 'wave' },
    { rightUpperArm: arm.quaternion }, { rightUpperArm: new THREE.Quaternion() }, .01);
  rig.setGestureCalibration(true);
  assert.ok(rig.poseTransition.getDiagnostic(), 'a no-op switch should preserve active transition');
  rig.setGestureCalibration(false);
  assert.equal(rig.poseTransition.getDiagnostic(), null);
  assert.ok(arm.quaternion.equals(before));
  assert.deepEqual(rig.getGestureCalibration(), { available: true, enabled: false, profile: 'generic' });
  rig.setGestureCalibration(true);
  assert.deepEqual(rig.getGestureCalibration(), { available: true, enabled: true, profile: 'daily' });
  rig._state.vrm.meta.name = 'Another VRM';
  assert.deepEqual(rig.getGestureCalibration(), { available: false, enabled: false, profile: 'generic' });
});
