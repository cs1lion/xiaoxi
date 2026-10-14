import assert from 'node:assert/strict';
import test from 'node:test';
import * as THREE from 'three';
import { MToonMaterial, VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind } from '@pixiv/three-vrm';
import { VrmRig } from './render/vrm.js';
import { AvatarCalibration, captureCalibrationFingerprint, compareCalibrationFingerprints } from './calibration.js';
import { ParamStore } from './params.js';

// Real material, scene, expression and production draw objects; only GPU
// rasterization is stubbed. These checks exercise mode switching/restoration,
// rather than repeat the selected visual calibration constant.
function fixture({ model = 'Seed-san Daily Study', faces = true, enabled = true } = {}) {
  const rig = new VrmRig();
  rig.setFaceShading(enabled);
  const scene = new THREE.Scene(), root = new THREE.Group(); scene.add(root);
  const surface = new MToonMaterial(); surface.name = 'body_bake';
  surface.color.setRGB(.88, .73, .62); surface.shadeColorFactor.setRGB(.76, .42, .33);
  surface.shadingShiftFactor = -.2; surface.shadingToonyFactor = .8;
  surface.outlineWidthFactor = .0011;
  const texture = new THREE.DataTexture(new Uint8Array([255, 200, 180, 255]), 1, 1);
  surface.map = texture;
  const outline = surface.clone(); outline.name = 'body_bake (Outline)';
  outline.color.setRGB(.81, .64, .53); outline.shadeColorFactor.setRGB(.61, .36, .23);
  const hair = surface.clone(); hair.name = 'hair_daily';
  const similarName = surface.clone(); similarName.name = 'body_bake_extra';
  const plain = new THREE.MeshBasicMaterial({ color: 0x998877 }); plain.name = 'body_bake';
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, 0, 0, 1, 0, 0, 0, 1, 0], 3));
  geometry.setAttribute('normal', new THREE.Float32BufferAttribute([0, 0, 1, 0, 0, 1, 0, 0, 1], 3));
  const material = faces ? [surface, outline, hair, similarName, plain] : [hair, similarName, plain];
  const mesh = new THREE.Mesh(geometry, material); root.add(mesh);
  if (faces) root.add(new THREE.Mesh(geometry, surface)); // Same instance seen twice.
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
    bodyCoordination: {}, expressions, springBones: [],
    ambient: new THREE.HemisphereLight(0xffffff, 0xb6b2c9, 1.35),
    key: new THREE.DirectionalLight(0xffe5d1, 2), fill: new THREE.DirectionalLight(0xbadce8, .65) };
  scene.add(state.target); state.key.position.set(-2, 2, 3); state.fill.position.set(-2, 1, 2);
  rig._state = state;
  const originals = [surface, outline].map(material => ({ material,
    color: material.color.clone(), shade: material.shadeColorFactor.clone() }));
  rig._captureFaceShading(state);
  const avatar = { svg: rig, store: new ParamStore(), rendererKind: 'vrm', _rendererRequest: 4,
    _raf: 0, clock: 12.3, gazeMode: 'follow', eyeHighlight: .8 };
  scene.updateMatrixWorld(true);
  return { rig, state, root, avatar, surface, outline, hair, similarName, plain, mesh, geometry, texture, originals, head };
}

const channels = material => ({ color: material.color.toArray(), shade: material.shadeColorFactor?.toArray() });
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);

test('both independent face materials switch from captured originals, including shared references, without accumulating', () => {
  const { rig, surface, outline, originals } = fixture();
  assert.deepEqual(rig.getFaceShading(), { available: true, enabled: true });
  const selected = [surface, outline].map(channels);
  for (const { material, color, shade } of originals) {
    const factor = material.color.r / color.r;
    assert.ok(factor > 0 && factor < 1, 'selected mode reduces face brightness');
    color.toArray().forEach((value, i) => close(material.color.toArray()[i] / value, factor));
    shade.toArray().forEach((value, i) => close(material.shadeColorFactor.toArray()[i] / value, factor));
  }
  const identities = [surface, outline].map(m => [m.color, m.shadeColorFactor]);
  for (let i = 0; i < 20; i++) {
    rig.setFaceShading(true);
    assert.deepEqual([surface, outline].map(channels), selected, 'already-enabled mode is idempotent');
    rig.setFaceShading(false);
    for (const { material, color, shade } of originals) {
      assert.ok(material.color.equals(color)); assert.ok(material.shadeColorFactor.equals(shade));
    }
    rig.setFaceShading(true);
    assert.deepEqual([surface, outline].map(channels), selected, 'repeated comparisons use the originals');
  }
  [surface, outline].forEach((m, i) => {
    assert.equal(m.color, identities[i][0]); assert.equal(m.shadeColorFactor, identities[i][1]);
  });
});

test('exact model and MToon name guards preserve unsupported materials and preload mode choice', () => {
  assert.deepEqual(new VrmRig().getFaceShading(), { available: false, enabled: false });
  const selected = fixture({ enabled: false });
  assert.deepEqual(selected.rig.getFaceShading(), { available: true, enabled: false });
  for (const { material, color, shade } of selected.originals) {
    assert.ok(material.color.equals(color)); assert.ok(material.shadeColorFactor.equals(shade));
  }
  for (const options of [{ model: 'Seed-san' }, { model: 'Seed-san Daily Study Extra' }, { faces: false }]) {
    const sample = fixture(options), before = [sample.surface, sample.outline, sample.hair, sample.similarName, sample.plain].map(channels);
    assert.deepEqual(sample.rig.getFaceShading(), { available: false, enabled: false });
    sample.rig.setFaceShading(false); sample.rig.setFaceShading(true);
    assert.deepEqual([sample.surface, sample.outline, sample.hair, sample.similarName, sample.plain].map(channels), before);
  }
  const unaffected = [selected.hair, selected.similarName, selected.plain].map(channels);
  selected.rig.setFaceShading(true);
  assert.deepEqual([selected.hair, selected.similarName, selected.plain].map(channels), unaffected);
});

test('material comparison leaves lights, camera, geometry, textures and active motion state intact', () => {
  const { rig, state, surface, outline, hair, similarName, plain, mesh, geometry, texture, head } = fixture();
  const selected = rig.handPosePreview = Object.freeze({ name: 'softGrip', side: 'right' });
  const performance = rig.performance = { name: 'palm', source: 'preview', weight: .7 };
  const signal = { id: 8, reason: 'start', from: 'self', to: 'palm' };
  rig.poseTransition.update(signal, { arm: new THREE.Quaternion() },
    { arm: new THREE.Quaternion().setFromEuler(new THREE.Euler(.2, .3, .1)) }, .01);
  const controller = rig.poseTransition, diagnostic = controller.getDiagnostic();
  const before = {
    lights: ['ambient', 'key', 'fill'].map(name => [state[name].intensity, state[name].color.toArray(), state[name].position.toArray()]),
    camera: state.camera.toJSON(), positions: [...geometry.attributes.position.array], normals: [...geometry.attributes.normal.array],
    morphs: [...mesh.morphTargetInfluences], head: head.quaternion.clone(),
    others: [hair, similarName, plain].map(channels),
    settings: [surface, outline].map(m => [m.shadingShiftFactor, m.shadingToonyFactor, m.outlineWidthFactor, m.version]),
  };
  rig.setFaceShading(false); rig.setFaceShading(true);
  assert.deepEqual(['ambient', 'key', 'fill'].map(name => [state[name].intensity, state[name].color.toArray(), state[name].position.toArray()]), before.lights);
  assert.deepEqual(state.camera.toJSON(), before.camera);
  assert.deepEqual([...geometry.attributes.position.array], before.positions);
  assert.deepEqual([...geometry.attributes.normal.array], before.normals);
  assert.deepEqual(mesh.morphTargetInfluences, before.morphs); assert.ok(head.quaternion.equals(before.head));
  assert.deepEqual([hair, similarName, plain].map(channels), before.others);
  assert.deepEqual([surface, outline].map(m => [m.shadingShiftFactor, m.shadingToonyFactor, m.outlineWidthFactor, m.version]), before.settings);
  assert.equal(mesh.geometry, geometry); assert.equal(surface.map, texture); assert.equal(outline.map, texture);
  assert.equal(rig.handPosePreview, selected); assert.equal(rig.performance, performance);
  assert.equal(rig.poseTransition, controller); assert.deepEqual(controller.getDiagnostic(), diagnostic);
});

test('fixed calibration restores the selected mode and exact live colors without replacing materials or captured originals', () => {
  for (const enabled of [false, true]) {
    const { rig, avatar, surface, outline, originals } = fixture({ enabled });
    // The exit contract restores the actual state, even when it differs from
    // the preset originals. Rounded fingerprints are not restoration data.
    surface.color.r = .712345678901234; outline.shadeColorFactor.g = .321234567890123;
    const before = [surface, outline].map(channels);
    const identities = [surface, outline].map(m => [m, m.color, m.shadeColorFactor]);
    const session = new AvatarCalibration(avatar); session.enter();
    rig.setFaceShading(!enabled);
    surface.color.b = .123456789012345; outline.shadeColorFactor.r = .198765432109876;
    assert.equal(session.exit(), true);
    assert.deepEqual(rig.getFaceShading(), { available: true, enabled });
    assert.deepEqual([surface, outline].map(channels), before, 'full precision live material state is restored');
    [surface, outline].forEach((m, i) => {
      assert.equal(m, identities[i][0]); assert.equal(m.color, identities[i][1]); assert.equal(m.shadeColorFactor, identities[i][2]);
    });
    rig.setFaceShading(false);
    for (const { material, color, shade } of originals) {
      assert.ok(material.color.equals(color)); assert.ok(material.shadeColorFactor.equals(shade));
    }
  }
});

test('shade-only edits invalidate fixed registration and a stale calibration safely restores its prior appearance', () => {
  const { rig, avatar, outline, surface } = fixture();
  const before = [surface, outline].map(channels);
  const session = new AvatarCalibration(avatar), snapshot = session.enter();
  outline.shadeColorFactor.r += .05;
  const comparison = compareCalibrationFingerprints(snapshot.fingerprint, captureCalibrationFingerprint(avatar));
  assert.equal(comparison.compatible, false); assert.ok(comparison.reasons.includes('appearance'));
  assert.equal(session.isCurrent(), false);
  assert.throws(() => session.snapshot(), /scene conditions changed/);
  assert.equal(session.active, false);
  assert.deepEqual([surface, outline].map(channels), before);
  assert.deepEqual(rig.getFaceShading(), { available: true, enabled: true });
});

test('production turning, blinking and mouth movement agree across material comparison modes', () => {
  const calibrated = fixture(), original = fixture({ enabled: false });
  for (const params of [
    { ParamAngleX: .3, ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamMouthOpenY: 0 },
    { ParamAngleY: -.2, ParamEyeLOpen: 0, ParamEyeROpen: .4, ParamMouthOpenY: .6 },
    { ParamAngleX: -.4, ParamEyeLOpen: .8, ParamEyeROpen: .8, ParamMouthOpenY: .3 },
  ]) {
    for (const sample of [calibrated, original]) sample.rig.draw(params, 1 / 60);
    assert.ok(calibrated.head.quaternion.equals(original.head.quaternion));
    assert.deepEqual(calibrated.mesh.morphTargetInfluences, original.mesh.morphTargetInfluences);
    assert.ok(calibrated.state.target.position.equals(original.state.target.position));
  }
  assert.equal(calibrated.mesh.morphTargetInfluences[2], .3);
  assert.notDeepEqual(channels(calibrated.surface), channels(original.surface));
});
