/* Run: node web/avatar/test_eye_morph.js
 * Uses actual VRMExpressionManager update/reset behavior and native names read
 * from the shipped assets, without WebGL or loading textures in Node. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';
import { VRMExpression, VRMExpressionManager, VRMExpressionMorphTargetBind, VRMLookAt } from '@pixiv/three-vrm';
import { createEyeMorphAdapter } from './render/eye-morph.js';
import { VrmRig } from './render/vrm.js';

let count = 0;
const test = (name, fn) => { fn(); console.log(`ok ${++count} - ${name}`); };
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-10, `${actual} != ${expected}`);

function fixture(asset) {
  const bytes = readFileSync(new URL(`../models/${asset}.vrm`, import.meta.url));
  const gltf = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString());
  const scene = new THREE.Scene();
  const nodes = new Map();
  for (let nodeIndex = 0; nodeIndex < gltf.nodes.length; nodeIndex++) {
    const node = gltf.nodes[nodeIndex];
    const mesh = gltf.meshes[node.mesh];
    if (!mesh?.extras?.targetNames?.length) continue;
    const primitive = new THREE.Object3D();
    primitive.morphTargetDictionary = Object.fromEntries(mesh.extras.targetNames.map((name, index) => [name, index]));
    primitive.morphTargetInfluences = mesh.extras.targetNames.map(() => 0);
    scene.add(primitive);
    nodes.set(nodeIndex, primitive);
  }
  const expressionManager = new VRMExpressionManager();
  for (const [name, source] of Object.entries(gltf.extensions.VRMC_vrm.expressions.preset)) {
    const expression = new VRMExpression(name);
    for (const property of ['isBinary', 'overrideBlink', 'overrideMouth', 'overrideLookAt']) {
      if (source[property] != null) expression[property] = source[property];
    }
    for (const bind of source.morphTargetBinds || []) {
      expression.addBind(new VRMExpressionMorphTargetBind({ primitives: [nodes.get(bind.node)], index: bind.index, weight: bind.weight }));
    }
    expressionManager.registerExpression(expression);
  }
  const native = (name, value) => {
    for (const mesh of nodes.values()) {
      for (const [rawName, index] of Object.entries(mesh.morphTargetDictionary)) {
        if (rawName.slice(rawName.lastIndexOf('.') + 1) === name) {
          if (value != null) mesh.morphTargetInfluences[index] = value;
          return mesh.morphTargetInfluences[index];
        }
      }
    }
    return undefined;
  };
  return { scene, expressionManager, native, nodes };
}

test('shipped model signatures report their actual independent and degraded channels', () => {
  const sample = createEyeMorphAdapter(fixture('sample'));
  const seed = createEyeMorphAdapter(fixture('seed-san'));
  const daily = createEyeMorphAdapter(fixture('seed-daily'));
  assert.equal(sample.capabilities.profile, 'sample');
  assert.equal(sample.capabilities.channels.smile.mode, 'independent');
  assert.equal(sample.capabilities.channels.browHeight.mode, 'bilateral');
  assert.equal(sample.capabilities.channels.browHeight.approximate, true);
  assert.equal(sample.capabilities.channels.irisSize.available, false);
  assert.equal(sample.capabilities.channels.mouthForm.available, false);
  assert.equal(seed.capabilities.profile, 'seed');
  assert.equal(seed.capabilities.channels.smile.mode, 'bilateral');
  assert.equal(seed.capabilities.channels.browHeight.mode, 'independent');
  assert.equal(seed.capabilities.channels.browHeight.approximate, true);
  assert.ok(seed.capabilities.channels.browHeight.description.includes('不是纯垂直抬眉'));
  assert.ok(seed.capabilities.degraded.includes('browHeight:approximate'));
  assert.equal(seed.capabilities.channels.browAngle.available, false);
  assert.equal(seed.capabilities.channels.browForm.available, false);
  assert.equal(seed.capabilities.channels.irisSize.available, true);
  assert.equal(seed.capabilities.channels.pupil.available, false);
  assert.equal(seed.capabilities.channels.mouthForm.available, true);
  assert.equal(daily.capabilities.profile, 'seed');
  assert.equal(daily.capabilities.channels.browHeight.approximate, true);
});

test('shipped Seed brow morph geometry confirms mixed shape displacement instead of a pure upward translation', () => {
  for (const asset of ['seed-san', 'seed-daily']) {
    const bytes = readFileSync(new URL(`../models/${asset}.vrm`, import.meta.url));
    const jsonLength = bytes.readUInt32LE(12), binaryStart = 28 + jsonLength;
    const gltf = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString());
    const displacement = name => {
      const y = [];
      for (const mesh of gltf.meshes) {
        const index = mesh.extras?.targetNames?.indexOf(name) ?? -1;
        if (index < 0) continue;
        for (const primitive of mesh.primitives) {
          const accessor = gltf.accessors[primitive.targets[index].POSITION];
          const view = gltf.bufferViews[accessor.bufferView];
          assert.equal(accessor.componentType, 5126);
          const values = new Float32Array(accessor.count * 3);
          if (view) {
            const offset = binaryStart + (view.byteOffset || 0) + (accessor.byteOffset || 0), stride = view.byteStride || 12;
            for (let i = 0; i < accessor.count; i++) for (let c = 0; c < 3; c++)
              values[i * 3 + c] = bytes.readFloatLE(offset + i * stride + c * 4);
          }
          // Seed stores many morph vectors as sparse glTF accessors with an
          // implicit zero base. Read their real sparse indices and values.
          if (accessor.sparse) {
            const sparse = accessor.sparse, iv = gltf.bufferViews[sparse.indices.bufferView], vv = gltf.bufferViews[sparse.values.bufferView];
            const io = binaryStart + (iv.byteOffset || 0) + (sparse.indices.byteOffset || 0);
            const vo = binaryStart + (vv.byteOffset || 0) + (sparse.values.byteOffset || 0);
            const indexBytes = { 5121: 1, 5123: 2, 5125: 4 }[sparse.indices.componentType];
            const read = { 1: 'readUInt8', 2: 'readUInt16LE', 4: 'readUInt32LE' }[indexBytes];
            assert.ok(read);
            for (let i = 0; i < sparse.count; i++) {
              const vertex = bytes[read](io + i * indexBytes);
              for (let c = 0; c < 3; c++) values[vertex * 3 + c] = bytes.readFloatLE(vo + i * 12 + c * 4);
            }
          }
          for (let i = 0; i < accessor.count; i++) {
            const dx = values[i * 3], dy = values[i * 3 + 1], dz = values[i * 3 + 2];
            if (dx * dx + dy * dy + dz * dz > 1e-14) y.push(dy);
          }
        }
      }
      assert.ok(y.length > 0);
      return { mean: y.reduce((sum, v) => sum + v, 0) / y.length, min: Math.min(...y), max: Math.max(...y) };
    };
    for (const side of ['L', 'R']) {
      const up = displacement(`eye_brow_up_${side}`), down = displacement(`eye_brow_down_${side}`);
      assert.ok(up.min < 0 && up.max > 0, `${asset}: up is a mixed brow shape`);
      assert.ok(up.mean < 0, `${asset}: exporter up does not mean an upward centroid`);
      assert.ok(down.mean < up.mean, `${asset}: swapping down/up would not create a raise`);
    }
  }
});

test('full blink retires smile/widening while speech and highlight survive the real manager update', () => {
  const vrm = fixture('sample');
  const adapter = createEyeMorphAdapter(vrm);
  vrm.expressionManager.setValue('blinkLeft', 1);
  vrm.expressionManager.setValue('blinkRight', 1);
  vrm.expressionManager.setValue('aa', 0.6);
  adapter.write({ ParamEyeLOpen: 0, ParamEyeROpen: 0, ParamEyeLSmile: 1, ParamEyeRSmile: 1,
    ParamEyeHighlight: 0.3, ParamBrowLY: 0.2, ParamBrowRY: 0.2 });
  // A raw value is deliberately overwritten by the manager: output comes from
  // registered expressions, rather than a second write after VRM update.
  vrm.native('Fcl_EYE_Joy_L', 0.9);
  vrm.expressionManager.update();
  close(vrm.native('Fcl_EYE_Close_L'), 1);
  close(vrm.native('Fcl_EYE_Close_R'), 1);
  close(vrm.native('Fcl_EYE_Joy_L'), 0);
  close(vrm.native('Fcl_EYE_Joy_R'), 0);
  close(vrm.native('Fcl_EYE_Surprised'), 0);
  close(vrm.native('Fcl_EYE_HighlightHide'), 0.7);
  close(vrm.native('Fcl_MTH_A'), 0.6);
  close(vrm.native('Fcl_BRW_Surprised'), 0.2);
  const procedural = vrm.expressionManager._calculateWeightMultipliers();
  assert.deepEqual(procedural, { blink: 1, lookAt: 1, mouth: 1 });
});

test('Seed expression gaze, vowels and independent native brow shape survive bilateral smile blending', () => {
  const vrm = fixture('seed-san');
  const adapter = createEyeMorphAdapter(vrm);
  const originalLookNames = vrm.expressionManager.lookAtExpressionNames.slice();
  const originalLookBind = vrm.expressionManager.getExpression('lookLeft').binds[0];
  vrm.expressionManager.setValue('lookLeft', 0.45);
  vrm.expressionManager.setValue('aa', 0.7);
  vrm.expressionManager.setValue('blinkLeft', 0.6);
  vrm.expressionManager.setValue('blinkRight', 0.2);
  adapter.write({ ParamEyeLOpen: 0.4, ParamEyeROpen: 0.8, ParamEyeLSmile: 0.6, ParamEyeRSmile: 0.2,
    ParamBrowLY: 0.5, ParamBrowRY: -0.3, ParamEyeBallForm: -0.4, ParamPupil: 1.5,
    ParamCheek: 0.2, ParamMouthForm: 0.5, ParamMouthOpenY: 0.7 });
  vrm.expressionManager.update();
  close(vrm.native('eye_smile'), 0.16);
  close(vrm.native('blink_L'), 0.6);
  close(vrm.native('blink_R'), 0.2);
  close(vrm.native('look_left'), 0.45);
  close(vrm.native('lip_a'), 0.7);
  close(vrm.native('lip_up'), 0.5 * 0.6 * (1 - 0.7 * 0.65));
  close(vrm.native('eye_brow_up_L'), 0.5);
  close(vrm.native('eye_brow_down_R'), 0.3);
  close(vrm.native('eye_black_small'), 0.4);
  close(vrm.native('eye_black_big'), 0);
  close(vrm.native('cheek'), 0.2);
  assert.deepEqual(vrm.expressionManager.lookAtExpressionNames, originalLookNames);
  assert.equal(vrm.expressionManager.getExpression('lookLeft').binds[0], originalLookBind);
  adapter.write({ ParamEyeLOpen: 0, ParamEyeROpen: 1, ParamEyeLSmile: 1, ParamEyeRSmile: 1 });
  vrm.expressionManager.setValue('blinkLeft', 1);
  vrm.expressionManager.setValue('blinkRight', 0);
  vrm.expressionManager.update();
  close(vrm.native('eye_smile'), 0);
  close(vrm.native('blink_L'), 1);
  close(vrm.native('look_left'), 0.45);
});

test('shared influence arrays bind once while distinct primitives keep their own output', () => {
  const scene = new THREE.Scene(), manager = new VRMExpressionManager();
  const shared = [0, 0], independent = [0, 0];
  for (const influences of [shared, shared, independent]) {
    const mesh = new THREE.Object3D();
    mesh.morphTargetDictionary = { 'Face_Blendshape.Fcl_EYE_Joy_L': 0, 'Face_Blendshape.Fcl_EYE_Joy_R': 1 };
    mesh.morphTargetInfluences = influences;
    scene.add(mesh);
  }
  let traversals = 0;
  const traverse = scene.traverse.bind(scene);
  scene.traverse = fn => { traversals++; traverse(fn); };
  const adapter = createEyeMorphAdapter({ scene, expressionManager: manager });
  for (let frame = 0; frame < 3; frame++) {
    adapter.write({ ParamEyeLSmile: 0.4, ParamEyeRSmile: 0.2 });
    manager.update();
    close(shared[0], 0.4); close(independent[0], 0.4);
    close(shared[1], 0.2); close(independent[1], 0.2);
  }
  assert.equal(traversals, 1, 'resolver is cached at mount');
  assert.equal(manager.getExpression('neuro_eye_Fcl_EYE_Joy_L').binds.length, 2);
});

test('missing or invalid native names never bind guessed numeric indexes', () => {
  const scene = new THREE.Scene(), manager = new VRMExpressionManager();
  const mesh = new THREE.Object3D();
  mesh.morphTargetDictionary = { unrelated: 0, Fcl_EYE_Joy_L: 100 };
  mesh.morphTargetInfluences = [0.37];
  scene.add(mesh);
  const adapter = createEyeMorphAdapter({ scene, expressionManager: manager });
  assert.equal(adapter.available, false);
  assert.equal(adapter.capabilities.profile, 'unsupported');
  adapter.write({ ParamEyeLSmile: 1, ParamPupil: 1.5, ParamEyeHighlight: 0 });
  manager.update();
  assert.equal(manager.expressions.length, 0);
  close(mesh.morphTargetInfluences[0], 0.37);
});

test('reset and return to neutral leave no stale native shape, disposal preserves standard expressions', () => {
  const vrm = fixture('seed-san');
  const originalCount = vrm.expressionManager.expressions.length;
  const adapter = createEyeMorphAdapter(vrm);
  adapter.write({ ParamEyeLSmile: 0.8, ParamEyeRSmile: 0.8, ParamEyeHighlight: 0, ParamCheek: 0.7 });
  vrm.expressionManager.update();
  vrm.expressionManager.resetValues();
  vrm.expressionManager.update();
  close(vrm.native('eye_smile'), 0); close(vrm.native('eye_no_hilight'), 0); close(vrm.native('cheek'), 0);
  adapter.write();
  vrm.expressionManager.update();
  close(vrm.native('eye_smile'), 0); close(vrm.native('eye_no_hilight'), 0);
  adapter.dispose();
  assert.equal(vrm.expressionManager.expressions.length, originalCount);
  assert.ok(vrm.expressionManager.getExpression('blinkLeft'));
  assert.ok(vrm.expressionManager.getExpression('lookLeft'));
});

test('renderer writes native shape and standard blink/vowel before update, without legacy emotion stacking', () => {
  const vrm = fixture('sample');
  const head = new THREE.Object3D(); vrm.scene.add(head);
  const manager = vrm.expressionManager;
  let renders = 0, updates = 0;
  vrm.update = () => { updates++; manager.update(); };
  const rig = new VrmRig();
  const eyeAdapter = createEyeMorphAdapter(vrm);
  rig._state = {
    vrm, eyeAdapter, scene: vrm.scene, camera: new THREE.PerspectiveCamera(),
    bones: { head: { node: head, rest: head.quaternion.clone() } },
    target: new THREE.Object3D(), modelHeight: 1.7, springBones: [],
    expressions: Object.fromEntries(['blink', 'blinkLeft', 'blinkRight', 'aa', 'ih', 'ou', 'ee', 'oh', 'happy', 'sad', 'angry']
      .map(name => [name, manager.getExpression(name)])),
    renderer: { render: () => { renders++; } },
  };
  rig.draw({ ParamEyeLOpen: 0, ParamEyeROpen: 1, ParamEyeLSmile: 1, ParamEyeRSmile: 0.4,
    ParamBrowLForm: 1, ParamBrowRForm: 1, ParamMouthOpenY: 0.5 }, 0.016);
  assert.equal(updates, 1); assert.equal(renders, 1);
  close(manager.getValue('angry'), 0);
  close(vrm.native('Fcl_ALL_Angry'), 0);
  close(vrm.native('Fcl_EYE_Close_L'), 1);
  close(vrm.native('Fcl_EYE_Joy_L'), 0);
  close(vrm.native('Fcl_EYE_Joy_R'), 0.4);
  close(vrm.native('Fcl_MTH_A'), 0.5);
  assert.equal(rig.getEyeCapabilities().profile, 'sample');
});

test('eye framing is reversible and survives resize aspect/presentation changes', () => {
  const rig = new VrmRig();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
  rig._state = { camera, framing: { center: new THREE.Vector3(0, 1, 0), height: 1, width: 0.6, depth: 0.2 },
    eyeFraming: { center: new THREE.Vector3(0, 1.4, 0), height: 0.32, width: 0.29, depth: 0.13 } };
  rig._frameCamera(rig._state);
  const original = camera.position.clone();
  rig.setEyeFocus(true);
  assert.ok(camera.position.z < original.z);
  close(camera.position.y, 1.4);
  camera.aspect = 0.6;
  rig._frameCamera(rig._state);
  assert.ok(Number.isFinite(camera.position.z));
  camera.aspect = 1;
  rig._state.presentation = true;
  rig._frameCamera(rig._state);
  close(camera.position.y, 1.4);
  rig._state.presentation = false;
  rig.setEyeFocus(false);
  assert.ok(camera.position.distanceTo(original) < 1e-10);
});

test('ocular residual keeps its direction through positive/negative head motion for both native profiles', () => {
  for (const asset of ['sample', 'seed-san']) {
    const vrm = fixture(asset);
    const head = new THREE.Object3D();
    head.position.set(0, 1.4, 0);
    const rawHead = new THREE.Object3D(); rawHead.position.copy(head.position);
    vrm.scene.add(head, rawHead);
    vrm.scene.updateMatrixWorld(true);
    // Use the real three-vrm lookAt inverse-head calculation, with a no-op eye
    // applier. Like the real humanoid, this updates raw quaternions without
    // refreshing their matrices: the renderer must synchronize before LookAt.
    vrm.humanoid = { getRawBoneNode: () => rawHead,
      update() { rawHead.quaternion.copy(head.quaternion); } };
    const lookAt = new VRMLookAt(vrm.humanoid, { applyYawPitch() {} });
    const target = new THREE.Object3D(); vrm.scene.add(target); lookAt.target = target;
    vrm.update = () => { vrm.humanoid.update(); lookAt.update(0.016); vrm.expressionManager.update(); };
    const rig = new VrmRig();
    rig._state = { vrm, scene: vrm.scene, camera: new THREE.PerspectiveCamera(),
      eyeAdapter: createEyeMorphAdapter(vrm), bones: { head: { node: head, rest: head.quaternion.clone() } },
      target, modelHeight: 1.7, expressions: {}, springBones: [], renderer: { render() {} } };
    for (const gaze of [-0.4, 0, 0.4]) {
      let neutralYaw, neutralPitch;
      for (const headAngle of [0, -0.7, 0.7]) {
        rig.draw({ ParamAngleX: headAngle, ParamAngleY: -headAngle,
          ParamEyeBallX: gaze, ParamEyeBallY: gaze }, 0.016);
        if (headAngle === 0) { neutralYaw = lookAt.yaw; neutralPitch = lookAt.pitch; }
        close(lookAt.yaw, neutralYaw);
        close(lookAt.pitch, neutralPitch);
        if (gaze) assert.equal(Math.sign(lookAt.yaw), Math.sign(gaze), `${asset}: yaw follows ocular residual`);
      }
    }
  }
});

test('enhanced brow targets remain independent, retire native shapes, and switch back without stale weights', () => {
  const vrm = fixture('seed-daily-eyes'), adapter = createEyeMorphAdapter(vrm);
  assert.equal(adapter.capabilities.profile, 'seed-eyes');
  assert.equal(adapter.capabilities.channels.browHeight.approximate, false);
  assert.equal(adapter.capabilities.channels.browHeight.pureTranslation, true);
  adapter.write({ParamBrowLY:.5,ParamBrowRY:-.25,ParamEyeLOpen:0,ParamEyeROpen:0,ParamMouthOpenY:.6});
  vrm.expressionManager.update();
  close(vrm.native('neuro_brow_raise_L'),.5); close(vrm.native('neuro_brow_lower_R'),.25);
  close(vrm.native('neuro_brow_raise_R'),0); close(vrm.native('neuro_brow_lower_L'),0);
  close(vrm.native('eye_brow_up_L'),0); close(vrm.native('eye_brow_down_R'),0);
  adapter.write({ParamBrowLY:.5,ParamBrowRY:-.25},{browMode:'native'}); vrm.expressionManager.update();
  close(vrm.native('eye_brow_up_L'),.5); close(vrm.native('eye_brow_down_R'),.25);
  close(vrm.native('neuro_brow_raise_L'),0); close(vrm.native('neuro_brow_lower_R'),0);
  adapter.write({ParamBrowLY:0,ParamBrowRY:0}); vrm.expressionManager.update();
  close(vrm.native('eye_brow_up_L'),0); close(vrm.native('neuro_brow_raise_L'),0);
});

console.log(`${count} native eye morph tests passed`);
