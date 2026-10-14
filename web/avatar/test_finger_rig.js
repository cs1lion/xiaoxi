import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VrmRig } from './render/vrm.js';
import { DAILY_GESTURE_MODEL_NAME } from './render/gesture-poses.js';
import { sampleFingerPose } from './render/finger-poses.js';

function fixture(model = DAILY_GESTURE_MODEL_NAME) {
  const rig = new VrmRig();
  const nodes = Object.fromEntries(['rightIndexProximal', 'leftLittleDistal'].map(name => {
    const node = new THREE.Object3D();
    node.quaternion.setFromEuler(new THREE.Euler(.1, -.15, .03));
    return [name, node];
  }));
  const state = { vrm: { meta: { name: model }, humanoid: { getNormalizedBoneNode: name => nodes[name] } } };
  rig._state = state;
  rig._captureFingers(state);
  return { rig, state, nodes };
}

const settle = (rig, state, frame) => {
  for (let i = 0; i < 120; i++) rig._applyFingers(state, frame, 1 / 60);
};
const target = (bone, offsets) => bone.rest.clone().multiply(new THREE.Quaternion()
  .setFromEuler(new THREE.Euler(...offsets)));

test('only adapted normalized joints are captured; partial mappings retain their bind rotations', () => {
  const { rig, state, nodes } = fixture();
  assert.equal(Object.keys(state.fingers).length, 2);
  assert.deepEqual(rig.getFingerRelaxation(), { available: true, enabled: true });
  for (const [name, bone] of Object.entries(state.fingers)) {
    assert.equal(bone.node, nodes[name]);
    assert.notEqual(bone.rest, nodes[name].quaternion);
    assert.ok(bone.rest.equals(nodes[name].quaternion));
  }
  const other = fixture('Other VRM');
  assert.deepEqual(other.state.fingers, {});
  assert.deepEqual(other.rig.getFingerRelaxation(), { available: false, enabled: false });
  other.rig._applyFingers(other.state, { name: 'wave', weight: 1 }, 1 / 60);
  assert.ok(other.nodes.rightIndexProximal.quaternion.equals(nodes.rightIndexProximal.quaternion));
  assert.deepEqual(new VrmRig().getFingerRelaxation(), { available: false, enabled: false });
});

test('relaxation composes with bind pose; zero delta and mode switches keep actual joint pose', () => {
  const { rig, state } = fixture();
  const finger = state.fingers.rightIndexProximal, bind = finger.rest.clone();
  rig._applyFingers(state, { weight: 0 }, 0);
  assert.ok(finger.node.quaternion.equals(bind));
  settle(rig, state, { weight: 0 });
  const restPose = sampleFingerPose({}, { profile: 'daily' });
  assert.ok(finger.node.quaternion.angleTo(target(finger, restPose.rightIndexProximal)) < 1e-6);
  assert.ok(finger.rest.equals(bind), 'the captured bind pose remains unchanged');
  const before = finger.node.quaternion.clone();
  rig.setFingerRelaxation(false);
  rig._applyFingers(state, { weight: 0 }, 0);
  assert.ok(finger.node.quaternion.equals(before));
  settle(rig, state, { weight: 0 });
  assert.ok(finger.node.quaternion.angleTo(bind) < 1e-6);
  assert.deepEqual(rig.getFingerRelaxation(), { available: true, enabled: false });
});

test('cancelled expression returns to relaxed rest without advancing its audio clock or arm transition', () => {
  const { rig, state } = fixture();
  const finger = state.fingers.rightIndexProximal;
  const controller = rig.poseTransition;
  const signal = { id: 4, reason: 'start', from: 'rest', to: 'wave' };
  controller.update(signal, { arm: new THREE.Quaternion() },
    { arm: new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), .3) }, .01);
  const diagnostic = controller.getDiagnostic();
  settle(rig, state, { name: 'wave', weight: 1, strength: 1 });
  const opened = finger.node.quaternion.clone();
  rig._applyFingers(state, { name: null, weight: 0 }, 0);
  assert.ok(finger.node.quaternion.equals(opened));
  settle(rig, state, { name: null, weight: 0 });
  const rest = sampleFingerPose({}, { profile: 'daily' });
  assert.ok(finger.node.quaternion.angleTo(target(finger, rest.rightIndexProximal)) < 1e-6);
  rig.setFingerRelaxation(false);
  assert.deepEqual(controller.getDiagnostic(), diagnostic, 'finger controls do not restart arm transitions');
});

test('suspended-tab deltas are bounded and reduced motion preserves relaxed rest', () => {
  const a = fixture(), b = fixture();
  const frame = { name: 'palm', weight: .75, strength: .4 };
  a.rig._applyFingers(a.state, frame, 10);
  b.rig._applyFingers(b.state, frame, .05);
  assert.ok(a.nodes.rightIndexProximal.quaternion.angleTo(b.nodes.rightIndexProximal.quaternion) < 1e-7);
  a.rig.reduceGestures = true;
  settle(a.rig, a.state, { name: null, weight: 0 });
  const rest = sampleFingerPose({}, { profile: 'daily' });
  assert.ok(a.nodes.rightIndexProximal.quaternion.angleTo(target(a.state.fingers.rightIndexProximal, rest.rightIndexProximal)) < 1e-6);
});
