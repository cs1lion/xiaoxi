import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind } from '@pixiv/three-vrm';
import { VrmRig } from './render/vrm.js';
import { AvatarCalibration, captureCalibrationFingerprint, compareCalibrationFingerprints } from './calibration.js';
import { ParamStore } from './params.js';

// Real spring bone joints and the production draw path; only rasterization is
// stubbed. These checks cover reversible toggling, not the visual amplitude.
function fixture({ model = 'Seed-san Daily Study', enabled = true, strength = 1 } = {}) {
  const rig = new VrmRig();
  const scene = new THREE.Scene(), root = new THREE.Group(); scene.add(root);
  const surface = new THREE.MeshBasicMaterial(); surface.name = 'body_bake';
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0,0,0, 1,0,0, 0,1,0], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0,0,1, 0,0,1, 0,0,1], 3));
  const mesh = new THREE.Mesh(geometry, surface); root.add(mesh);
  mesh.morphTargetDictionary = { blink_l: 0, blink_r: 1, aa: 2 };
  mesh.morphTargetInfluences = [0, 0, .23];
  const manager = new VRMExpressionManager(), expressions = {};
  for (const [name, index] of [['blinkLeft', 0], ['blinkRight', 1], ['aa', 2]]) {
    const expression = new VRMExpression(name);
    expression.addBind(new VRMExpressionMorphTargetBind({ primitives: [mesh], index, weight: 1 }));
    manager.registerExpression(expression); expressions[name] = expression;
  }
  manager.setValue('aa', .23); manager.update();
  const head = new THREE.Object3D(); head.name = 'Head'; head.position.y = 1.4; root.add(head);
  const camera = new THREE.PerspectiveCamera(30, 1.25, .01, 100);
  camera.position.set(0, 1.4, 4); camera.lookAt(0, 1.4, 0); camera.updateMatrixWorld(true);
  const rect = { width: 600, height: 480 };
  const renderer = { domElement: { getBoundingClientRect: () => rect, width: 600, height: 480 },
    renders: 0, render() { this.renders++; } };
  const vrm = { scene: root, meta: { name: model }, expressionManager: manager,
    humanoid: { update() {} }, update() { manager.update(); } };
  const state = { vrm, scene, camera, renderer, target: new THREE.Object3D(), modelHeight: 1.7,
    bones: { head: { node: head, rest: new THREE.Quaternion() } }, arms: {}, fingers: {},
    bodyCoordination: {}, expressions, springBones: [], eyeAdapter: null,
    ambient: new THREE.HemisphereLight(), key: new THREE.DirectionalLight(), fill: new THREE.DirectionalLight() };
  scene.add(state.target);
  rig._state = state;
  const springBones = [];
  const names = model === 'Seed-san' ? ['spine'] : ['hair_A', 'hair_B', 'hair_subA', 'spine'];
  for (const name of names) {
    const bone = new THREE.Object3D(); bone.name = name; head.add(bone);
    springBones.push({ bone, settings: { stiffness: 1.2, dragForce: 1, gravityPower: 0,
      gravityDir: new THREE.Vector3(0,-1,0), hitRadius: .01 }, child: null });
  }
  state.vrm.springBoneManager = { springBones, update() {} };
  rig._captureHairPhysics(state);
  rig.setHairPhysics(enabled, strength);
  const avatar = { svg: rig, store: new ParamStore(), rendererKind: 'vrm', _rendererRequest: 4,
    _raf: 0, clock: 12.3, gazeMode: 'follow', eyeHighlight: .8 };
  scene.updateMatrixWorld(true);
  return { rig, state, root, avatar, springBones, head, mesh };
}

const settingsOf = joints => joints.map(j => ({ stiffness: j.settings.stiffness,
  gravityPower: j.settings.gravityPower, dragForce: j.settings.dragForce, hitRadius: j.settings.hitRadius }));
const hairOf = sample => sample.springBones.filter(j => j.bone.name.includes('hair'));
const close = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} != ${b}`);

test('hair physics toggles from captured originals without accumulating, and only affects hair bones', () => {
  const sample = fixture();
  assert.deepEqual(sample.rig.getHairPhysics(), { available: true, enabled: true, strength: 1 });
  const hair = hairOf(sample), spine = sample.springBones.find(j => j.bone.name === 'spine');
  const selected = settingsOf(hair), spineBefore = { ...spine.settings };
  for (const joint of hair) { close(joint.settings.stiffness, 1.2); close(joint.settings.gravityPower, 0); }
  for (let i = 0; i < 20; i++) {
    sample.rig.setHairPhysics(false, 0);
    for (const joint of hair) {
      assert.ok(joint.settings.stiffness > 1e5, 'disabled is rigid');
      close(joint.settings.gravityPower, 0);
    }
    assert.deepEqual(spine.settings, spineBefore, 'non-hair bones untouched');
    sample.rig.setHairPhysics(true, 1);
    assert.deepEqual(settingsOf(hair), selected, 'repeated comparisons use the originals');
  }
  assert.equal(sample.rig._state.hairPhysics.length, 3);
});

test('strength scales linearly and is clamped, and unsupported models report unavailable', () => {
  const sample = fixture();
  sample.rig.setHairPhysics(true, 0.5);
  close(hairOf(sample)[0].settings.stiffness, 0.6);
  assert.deepEqual(sample.rig.getHairPhysics().strength, 0.5);
  sample.rig.setHairPhysics(true, 99);
  assert.deepEqual(sample.rig.getHairPhysics().strength, 2);
  sample.rig.setHairPhysics(false, 1);
  assert.deepEqual(sample.rig.getHairPhysics(), { available: true, enabled: false, strength: 0 });
  const other = fixture({ model: 'Seed-san' });
  assert.deepEqual(other.rig.getHairPhysics(), { available: false, enabled: false, strength: 0 });
});

test('fixed calibration restores selected hair physics mode, strength and exact settings', () => {
  for (const [enabled, strength] of [[false, 0], [true, 0.5]]) {
    const sample = fixture({ enabled, strength });
    const before = settingsOf(hairOf(sample));
    const session = new AvatarCalibration(sample.avatar); session.enter();
    sample.rig.setHairPhysics(!enabled, enabled ? 1 : 0.5);
    assert.equal(session.exit(), true);
    assert.deepEqual(sample.rig.getHairPhysics(), { available: true, enabled, strength });
    const after = settingsOf(hairOf(sample));
    if (JSON.stringify(after) !== JSON.stringify(before)) {
      console.log('MISMATCH', { enabled, strength, before, after,
        savedHair: session.saved.hairPhysics.map(h => h.settings.stiffness) });
    }
    assert.deepEqual(after, before, 'exact spring settings restored');
  }
});

test('changing hair physics settings invalidates fixed registration', () => {
  const sample = fixture();
  const before = settingsOf(hairOf(sample));
  const session = new AvatarCalibration(sample.avatar), snapshot = session.enter();
  sample.rig.setHairPhysics(false, 0);
  const comparison = compareCalibrationFingerprints(snapshot.fingerprint, captureCalibrationFingerprint(sample.avatar));
  assert.equal(comparison.compatible, false);
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.snapshot(), /scene conditions changed/);
  assert.equal(session.active, false);
  assert.deepEqual(settingsOf(hairOf(sample)), before);
});
