/* Import-map entries must resolve all three imports to mutually compatible builds.
 * Avatar owns the only animation loop. Await mount before selecting this renderer. */
import * as THREE from 'three';
import { createAccessories } from './accessories.js';
import { createEyeMorphAdapter } from './eye-morph.js';
import { DAILY_GESTURE_MODEL_NAME, resolveGestureProfile, sampleGesturePose } from './gesture-poses.js';
import { resolveFingerProfile } from './finger-poses.js';
import { HAND_BONE_NAMES, HAND_POSE_NAMES, sampleHandPose } from './hand-poses.js';
import { BODY_BONE_NAMES, resolveBodyProfile, sampleBodyPose } from './body-poses.js';
import { PoseTransition } from '../drivers/pose-transition.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { EXPRESSIONS, finiteClamp, mapParams, safeDelta, selectExpressions } from './vrm-mapping.js';

function releaseScenes(roots) {
  const geometries = new Set(), materials = new Set(), textures = new Set();
  const skeletons = new Set(), images = new Set();
  const texture = value => {
    if (!value?.isTexture || textures.has(value)) return;
    textures.add(value);
    const data = value.source?.data;
    for (const image of Array.isArray(data) ? data : [data]) {
      if (image && typeof image.close === 'function') images.add(image);
    }
  };
  for (const root of new Set(roots.filter(Boolean))) {
    root.traverse(node => {
      if (node.geometry) geometries.add(node.geometry);
      if (node.skeleton) skeletons.add(node.skeleton);
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (!material) continue;
        materials.add(material);
        for (const value of Object.values(material)) texture(value);
        for (const uniform of Object.values(material.uniforms || {})) texture(uniform?.value);
      }
    });
    root.removeFromParent();
  }
  for (const skeleton of skeletons) skeleton.dispose?.();
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
  for (const tex of textures) tex.dispose();
  for (const image of images) image.close();
}

export class VrmRig {
  constructor() {
    this.secondaryScale = 1;
    this.performanceTransitionsEnabled = true;
    this.gestureCalibrationEnabled = true;
    this.gesturePathsEnabled = true;
    this.fingerRelaxationEnabled = true;
    this.handPosePreview = null;
    this.bodyCoordinationEnabled = true;
    this.faceShadingEnabled = true;
    this.hairPhysicsEnabled = true;
    this.hairPhysicsStrength = 1;
    this.poseTransition = new PoseTransition();
    this._generation = 0;
    this._state = null;
  }

  async mount(container, look = {}, options = {}) {
    this.dispose();
    if (!container?.appendChild) throw new TypeError('VrmRig requires a DOM container');
    const url = options.url;
    if (typeof url !== 'string' || !url.trim()) throw new TypeError('VrmRig.mount requires {url}');
    const generation = this._generation;
    const state = { container, roots: [], bones: {}, expressions: {}, springBones: [] };
    this._state = state;
    try {
      state.renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      state.renderer.setClearColor(0x000000, 0);
      state.renderer.outputColorSpace = THREE.SRGBColorSpace;
      const canvas = state.renderer.domElement;
      canvas.className = 'avatar-vrm';
      canvas.setAttribute('aria-label', '3D avatar');
      canvas.style.cssText = 'display:block;width:100%;height:100%;pointer-events:none;';
      container.appendChild(canvas);
      state.scene = new THREE.Scene();
      state.camera = new THREE.PerspectiveCamera(30, 1, 0.01, 100);
      state.ambient = new THREE.HemisphereLight(0xffffff, 0xb6b2c9, 2);
      state.scene.add(state.ambient);
      const key = new THREE.DirectionalLight(0xffffff, 2.5);
      key.position.set(1, 2, 3);
      state.key = key;
      state.scene.add(key);
      state.fill = new THREE.DirectionalLight(0xbadce8, 0);
      state.fill.position.set(-2, 1, 2);
      state.scene.add(state.fill);
      // A-5 柔和边缘光：冷色轮廓灯从后侧上方扫过，把肩线和发丝从背景里
      // 剥出来。强度低、角度偏，只勾边，不改整体明暗关系。参数来自
      // config/avatar.yaml 的 stage.lights，缺省即这里的常量。
      const lights = (options && options.stage && options.stage.lights) || {};
      const rimAt = lights.rim_position || [-1.6, 2.4, -2.2];
      state.rim = new THREE.DirectionalLight(
        new THREE.Color(lights.rim_color || "#bfd8ff"),
        Number.isFinite(lights.rim_intensity) ? lights.rim_intensity : 0.9);
      state.rim.position.set(rimAt[0] ?? -1.6, rimAt[1] ?? 2.4, rimAt[2] ?? -2.2);
      state.rimBase = state.rim.intensity;
      state.scene.add(state.rim);
      state.target = new THREE.Object3D();
      state.scene.add(state.target);
      const resize = () => {
        if (this._state !== state) return;
        const rect = container.getBoundingClientRect();
        const width = Math.max(1, rect.width || container.clientWidth || 1);
        const height = Math.max(1, rect.height || container.clientHeight || 1);
        state.renderer.setPixelRatio(finiteClamp(globalThis.devicePixelRatio || 1, 1, 2, 1));
        state.renderer.setSize(width, height, false);
        state.camera.aspect = width / height;
        this._frameCamera(state);
      };
      if (typeof ResizeObserver !== 'undefined') {
        state.observer = new ResizeObserver(resize);
        state.observer.observe(container);
      } else {
        globalThis.addEventListener('resize', resize);
        state.removeResize = () => globalThis.removeEventListener('resize', resize);
      }
      resize();
      const loader = new GLTFLoader();
      loader.register(parser => new VRMLoaderPlugin(parser));
      const gltf = await loader.loadAsync(url);
      const roots = gltf.scenes?.length ? gltf.scenes : [gltf.scene];
      // GLTFLoader cannot reliably abort external textures. Late loads are released
      // here and can never attach to a newer mount's scene or remove its canvas.
      if (generation !== this._generation || this._state !== state) {
        releaseScenes(roots);
        throw new DOMException('VRM mount superseded or disposed', 'AbortError');
      }
      state.roots = roots;
      const vrm = gltf.userData?.vrm;
      if (!vrm?.humanoid || !vrm.scene) throw new Error('The loaded asset is not a humanoid VRM');
      state.vrm = vrm;
      VRMUtils.rotateVRM0(vrm); // Both VRM 0.x and 1.x now face +Z.
      state.scene.add(vrm.scene);
      const humanoid = vrm.humanoid;
      if (typeof humanoid.getNormalizedBoneNode !== 'function') {
        throw new Error('This three-vrm build lacks normalized humanoid bones');
      }
      for (const name of ['head', 'neck', 'spine']) {
        const node = humanoid.getNormalizedBoneNode(name);
        if (node) state.bones[name] = { node, rest: node.quaternion.clone() };
      }
      if (!state.bones.head) throw new Error('VRM has no normalized head bone');
      // Lower arms geometrically, not by assuming an exporter's left/right axes.
      vrm.scene.updateMatrixWorld(true);
      for (const side of ['left', 'right']) {
        const upper = humanoid.getNormalizedBoneNode(`${side}UpperArm`);
        const lower = humanoid.getNormalizedBoneNode(`${side}LowerArm`);
        if (!upper || !lower || !upper.parent) continue;
        const from = lower.getWorldPosition(new THREE.Vector3())
          .sub(upper.getWorldPosition(new THREE.Vector3())).normalize();
        const to = new THREE.Vector3(Math.sign(from.x) * 0.23, -0.97, 0.06).normalize();
        const inverse = upper.parent.getWorldQuaternion(new THREE.Quaternion()).invert();
        from.applyQuaternion(inverse);
        to.applyQuaternion(inverse);
        upper.quaternion.premultiply(new THREE.Quaternion().setFromUnitVectors(from, to));
      }
      state.arms = {};
      for (const name of ['leftUpperArm', 'leftLowerArm', 'leftHand', 'rightUpperArm', 'rightLowerArm', 'rightHand']) {
        const node = humanoid.getNormalizedBoneNode(name);
        if (node) state.arms[name] = { node, rest: node.quaternion.clone() };
      }
      this._captureFingers(state);
      this._captureBody(state);
      humanoid.update?.();
      vrm.scene.updateMatrixWorld(true);
      for (const name of EXPRESSIONS) {
        const expression = vrm.expressionManager?.getExpression?.(name);
        if (expression) state.expressions[name] = expression;
      }
      state.eyeAdapter = createEyeMorphAdapter(vrm);
      if (vrm.lookAt) {
        vrm.lookAt.target = state.target;
        vrm.lookAt.autoUpdate = true;
      }
      // Daily's face brightness is reversible; retain its textures, normals,
      // toon shadow thresholds and all other materials.
      this._captureFaceShading(state);
      const stageCfg = (options && options.stage && typeof options.stage === "object") ? options.stage : {};
      state.stage = stageCfg;
      state.touch = (options && options.touch && typeof options.touch === "object") ? options.touch : {};
      const bounds = new THREE.Box3().setFromObject(vrm.scene);
      const head = state.bones.head.node.getWorldPosition(new THREE.Vector3());
      const size = bounds.getSize(new THREE.Vector3());
      const height = Number.isFinite(size.y) && size.y > 0 ? size.y : 1.7;
      // A-5 舞台镜头构图：头顶留白与构图下沿都可配（stage.framing），
      // 缺省值与原构图逐字节一致——默认造型换头发也不改变取景。
      const framing = stageCfg.framing || {};
      const headroom = Number.isFinite(framing.headroom) ? framing.headroom : 0.12;
      const chest = Number.isFinite(framing.chest) ? framing.chest : 0.43;
      const zoom = Number.isFinite(framing.zoom) ? framing.zoom : 1.08;
      const top = Math.max(head.y + height * headroom, bounds.max.y);
      const bottom = head.y - height * chest;
      state.framing = {
        center: new THREE.Vector3(head.x, (top + bottom) / 2, head.z),
        height: Math.max(height * 0.35, top - bottom) * zoom,
        width: Math.max(height * 0.40, Math.min(size.x, height * 0.85)),
        depth: Math.max(height * 0.15, size.z || 0),
      };
      state.modelHeight = height;
      state.eyeFraming = {
        // Humanoid head pivots sit below the eyes on these models. Raise the
        // portrait center to the upper face to keep the crown above the eyes.
        center: new THREE.Vector3(head.x, head.y + height * (Number.isFinite(framing.eye_raise) ? framing.eye_raise : 0.05), head.z),
        height: height * (Number.isFinite(framing.eye_height) ? framing.eye_height : 0.23),
        width: height * (Number.isFinite(framing.eye_width) ? framing.eye_width : 0.22),
        // Whole-model depth can include equipment extending behind the torso.
        // The face closeup needs only a small allowance for nose/hair depth.
        depth: height * (Number.isFinite(framing.eye_depth) ? framing.eye_depth : 0.04),
      };
      const joints = vrm.springBoneManager?.joints;
      if (joints && typeof joints[Symbol.iterator] === 'function') {
        const seen = new Set();
        for (const joint of joints) {
          if (!joint.bone?.quaternion || seen.has(joint.bone)) continue;
          seen.add(joint.bone);
          state.springBones.push({ node: joint.bone, rest: joint.bone.quaternion.clone() });
        }
      }
      this._captureHairPhysics(state);
      resize();
      this.draw({}, 0);
      return this;
    } catch (error) {
      if (this._state === state) this.dispose();
      throw error;
    }
  }

  _frameCamera(state) {
    const { camera } = state;
    const framing = state.eyeFocus ? state.eyeFraming : state.framing;
    if (framing) {
      const halfFov = THREE.MathUtils.degToRad(camera.fov / 2);
      const crop = state.presentation && !state.eyeFocus ? 0.86 : 1;
      const distance = Math.max(framing.height * crop / 2, framing.width / (2 * camera.aspect)) /
        Math.tan(halfFov) + framing.depth;
      const focus = framing.center.clone();
      if (state.presentation && !state.eyeFocus) focus.y += framing.height * 0.035;
      camera.position.copy(focus).add(new THREE.Vector3(0, 0, distance));
      camera.near = Math.max(0.001, distance / 1000);
      camera.far = Math.max(100, distance * 10);
      camera.lookAt(focus);
      // 呼吸景深在这条基准光轴上做极缓慢的推拉（见 draw）。
      state.dolly = { focus: focus.clone(), distance };
    }
    camera.updateProjectionMatrix();
  }

  // Reversible presentation only; original model assets and material colors stay intact.
  setPresentation(enabled) {
    const s = this._state;
    if (!s?.framing) return;
    s.presentation = !!enabled;
    s.ambient.intensity = enabled ? 1.35 : 2;
    s.key.intensity = enabled ? 2.0 : 2.5;
    s.key.color.set(enabled ? 0xffe5d1 : 0xffffff);
    s.key.position.set(enabled ? -2 : 1, 2, 3);
    s.fill.intensity = enabled ? 0.65 : 0;
    // 边缘光在演出模式下加强一档（舞台打光），其余模式回到配置基准。
    if (s.rim) s.rim.intensity = enabled ? s.rimBase * 1.33 : s.rimBase;
    this._frameCamera(s);
  }

  _captureFaceShading(state) {
    state.faceShading = [];
    if (state.vrm?.meta?.name !== DAILY_GESTURE_MODEL_NAME) return;
    const seen = new Set();
    state.vrm.scene.traverse(node => {
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (!material?.isMToonMaterial || !['body_bake', 'body_bake (Outline)'].includes(material.name) ||
          !material.color?.isColor || !material.shadeColorFactor?.isColor || seen.has(material)) continue;
        seen.add(material);
        // The outline is a separate loader clone. Keep both originals and
        // always apply from them, so toggling never compounds the correction.
        state.faceShading.push({ material, color: material.color.clone(), shadeColorFactor: material.shadeColorFactor.clone() });
      }
    });
    this._applyFaceShading(state);
  }

  _applyFaceShading(state) {
    const scale = this.faceShadingEnabled ? 0.90 : 1;
    for (const { material, color, shadeColorFactor } of state?.faceShading || []) {
      material.color.copy(color).multiplyScalar(scale);
      material.shadeColorFactor.copy(shadeColorFactor).multiplyScalar(scale);
    }
  }

  setFaceShading(enabled) {
    this.faceShadingEnabled = !!enabled;
    this._applyFaceShading(this._state);
  }

  getFaceShading() {
    const available = !!this._state?.faceShading?.length;
    return { available, enabled: available && this.faceShadingEnabled };
  }

  // Front/side hair spring bones are the only hair chains in the hair candidate.
  // Keep their loaded settings as originals and always recompute from them.
  _captureHairPhysics(state) {
    state.hairPhysics = [];
    for (const joint of state.vrm?.springBoneManager?.springBones || []) {
      if (!joint?.bone?.name?.includes('hair') || !joint.settings) continue;
      state.hairPhysics.push({ joint, settings: {
        stiffness: joint.settings.stiffness,
        gravityPower: joint.settings.gravityPower,
        dragForce: joint.settings.dragForce,
        hitRadius: joint.settings.hitRadius,
      } });
    }
    this._applyHairPhysics(state);
  }

  _applyHairPhysics(state) {
    const strength = this.hairPhysicsEnabled ? this.hairPhysicsStrength : 0;
    for (const { joint, settings } of state?.hairPhysics || []) {
      // Disabled means rigid: a very large stiffness pins the bone to its rest
      // axis and zero gravity removes the droop, matching the pre-physics look.
      joint.settings.stiffness = strength > 0 ? settings.stiffness * strength : 1e6;
      joint.settings.gravityPower = settings.gravityPower * strength;
      joint.settings.dragForce = settings.dragForce;
      joint.settings.hitRadius = settings.hitRadius;
    }
  }

  setHairPhysics(enabled, strength) {
    this.hairPhysicsEnabled = !!enabled;
    if (Number.isFinite(strength)) this.hairPhysicsStrength = Math.max(0, Math.min(2, strength));
    this._applyHairPhysics(this._state);
  }

  getHairPhysics() {
    const available = !!this._state?.hairPhysics?.length;
    return { available, enabled: available && this.hairPhysicsEnabled,
      strength: available && this.hairPhysicsEnabled ? this.hairPhysicsStrength : 0 };
  }

  // Multiplicative tint, not replacement textures; only the licensed Seed-san asset.
  setPalette(name = 'original') {
    const s = this._state;
    if (!s?.vrm || s.vrm.meta?.name !== 'Seed-san') return false;
    if (!['original', 'ocean', 'warm'].includes(name)) return false;
    const palettes = {
      ocean: { hair: '#819eb4', huku_bake: '#bed3df', eye: '#a4dbc9' },
      warm: { hair: '#d7b99e', huku_bake: '#ead4b4', eye: '#dfc3a6' },
      original: {},
    };
    s.paletteOriginals ||= new Map();
    s.vrm.scene.traverse(node => {
      for (const m of Array.isArray(node.material) ? node.material : [node.material]) {
        if (!m?.color || !/^(hair|huku_bake|eye)(?:\b|_)/.test(m.name)) continue;
        if (!s.paletteOriginals.has(m)) s.paletteOriginals.set(m, m.color.clone());
        m.color.copy(s.paletteOriginals.get(m));
        const key = Object.keys(palettes[name]).find(k => m.name === k || m.name.startsWith(k + '_'));
        if (key) m.color.multiply(new THREE.Color(palettes[name][key]));
      }
    });
    if (name === 'warm' && !s.accessories) s.accessories = createAccessories(s.vrm, s.modelHeight);
    s.accessories?.setVisible(name === 'warm');
    s.palette = name;
    return true;
  }

  setPerformance(frame, reduced = false) {
    this.performance = frame; this.reduceGestures = reduced;
    // A manual hand inspection belongs to the lab's palm preview only. Speech,
    // ordinary recovery and interruption all return to the automatic hand.
    if (this.handPosePreview && (frame?.source !== 'preview' || frame?.name !== 'palm' ||
      ['stop', 'interrupted', 'playback_stopped', 'recording', 'thinking', 'complete'].includes(frame?.transition?.reason))) {
      this.handPosePreview = null;
    }
  }

  setEyeBrowMode(mode) {
    if (!['calibrated', 'native'].includes(mode)) throw new RangeError('Unknown eyebrow deformation mode');
    this.eyeBrowMode = mode;
  }

  setEyeLidMode(mode) {
    if (!['calibrated', 'native'].includes(mode)) throw new RangeError('Unknown eyelid deformation mode');
    this.eyeLidMode = mode;
  }

  setPerformanceTransitions(enabled) {
    const next = !!enabled;
    if (next !== this.performanceTransitionsEnabled) this.poseTransition.reset();
    this.performanceTransitionsEnabled = next;
  }

  getPerformanceTransition() {
    return this.performanceTransitionsEnabled && this._state?.vrm
      ? this.poseTransition.getDiagnostic() : null;
  }

  setGestureCalibration(enabled) {
    const next = !!enabled;
    if (next !== this.gestureCalibrationEnabled) this.poseTransition.reset();
    this.gestureCalibrationEnabled = next;
  }

  getGestureCalibration() {
    const available = this._state?.vrm?.meta?.name === DAILY_GESTURE_MODEL_NAME;
    const enabled = available && this.gestureCalibrationEnabled;
    return { available, enabled, profile: enabled ? 'daily' : 'generic' };
  }

  setGesturePaths(enabled) {
    const next = !!enabled;
    if (next !== this.gesturePathsEnabled) this.poseTransition.reset();
    this.gesturePathsEnabled = next;
  }

  getGesturePaths() {
    const available = this.getGestureCalibration().enabled;
    return { available, enabled: available && this.gesturePathsEnabled };
  }

  _captureFingers(state) {
    state.fingers = {};
    if (resolveFingerProfile(state.vrm?.meta?.name) !== 'daily') return;
    for (const name of HAND_BONE_NAMES) {
      const node = state.vrm.humanoid.getNormalizedBoneNode(name);
      if (node) state.fingers[name] = { node, rest: node.quaternion.clone() };
    }
  }

  setFingerRelaxation(enabled) {
    this.fingerRelaxationEnabled = !!enabled;
    if (!this.fingerRelaxationEnabled) this.handPosePreview = null;
  }

  setHandPosePreview(name = null, side = 'right') {
    if (name !== null && (!HAND_POSE_NAMES.includes(name) || !['left', 'right'].includes(side))) {
      throw new RangeError('Unknown hand pose or side');
    }
    if (name === null) { this.handPosePreview = null; return true; }
    if (!this.getFingerRelaxation().enabled || !this._hasCompleteHand(side)) return false;
    this.handPosePreview = Object.freeze({ name, side });
    return true;
  }

  getHandPosePreview() {
    const side = this.handPosePreview?.side ?? 'right';
    const available = this._hasCompleteHand(side);
    return { available, enabled: available && this.fingerRelaxationEnabled,
      name: this.handPosePreview?.name ?? null, side };
  }

  _hasCompleteHand(side) {
    const state = this._state;
    return resolveFingerProfile(state?.vrm?.meta?.name) === 'daily' &&
      HAND_BONE_NAMES.filter(name => name.startsWith(side)).every(name => state?.fingers?.[name]);
  }

  getFingerRelaxation() {
    const state = this._state;
    const available = resolveFingerProfile(state?.vrm?.meta?.name) === 'daily' &&
      Object.keys(state?.fingers || {}).length > 0;
    return { available, enabled: available && this.fingerRelaxationEnabled };
  }

  _applyFingers(state, frame, dt) {
    if (resolveFingerProfile(state.vrm?.meta?.name) !== 'daily') return;
    const blend = 1 - Math.exp(-safeDelta(dt) * 18);
    if (!blend) return;
    const pose = sampleHandPose(frame, {
      profile: resolveFingerProfile(state.vrm.meta.name, this.fingerRelaxationEnabled),
      reduced: !!this.reduceGestures,
      preview: this.handPosePreview,
    });
    for (const [name, { node, rest }] of Object.entries(state.fingers || {})) {
      const target = rest.clone().multiply(new THREE.Quaternion()
        .setFromEuler(new THREE.Euler(...(pose[name] || [0, 0, 0]), 'XYZ')));
      node.quaternion.slerp(target, blend);
    }
  }

  _captureBody(state) {
    state.bodyCoordination = {};
    if (resolveBodyProfile(state.vrm?.meta?.name) !== 'daily') return;
    for (const name of BODY_BONE_NAMES) {
      const node = state.vrm.humanoid.getNormalizedBoneNode(name);
      if (node) state.bodyCoordination[name] = { node, rest: node.quaternion.clone() };
    }
  }

  setBodyCoordination(enabled) { this.bodyCoordinationEnabled = !!enabled; }

  getBodyCoordination() {
    const state = this._state;
    const available = resolveBodyProfile(state?.vrm?.meta?.name) === 'daily' &&
      Object.keys(state?.bodyCoordination || {}).length > 0;
    return { available, enabled: available && this.bodyCoordinationEnabled };
  }

  _applyBody(state, frame, dt) {
    if (resolveBodyProfile(state.vrm?.meta?.name) !== 'daily') return;
    const blend = 1 - Math.exp(-safeDelta(dt) * 10);
    if (!blend) return;
    const pose = sampleBodyPose(frame, {
      profile: resolveBodyProfile(state.vrm.meta.name, this.bodyCoordinationEnabled),
      reduced: !!this.reduceGestures,
    });
    // These joints have no mapped idle target of their own; their parent spine
    // continues to supply the existing body motion. Interpolate actual local
    // rotations without resetting the arm transition or accumulating offsets.
    for (const [name, { node, rest }] of Object.entries(state.bodyCoordination || {})) {
      const target = rest.clone().multiply(new THREE.Quaternion()
        .setFromEuler(new THREE.Euler(...(pose[name] || [0, 0, 0]), 'XYZ')));
      node.quaternion.slerp(target, blend);
    }
  }

  getEyeCapabilities() {
    return this._state?.eyeAdapter?.capabilities || { available: false, profile: 'unmounted', channels: {}, degraded: [] };
  }

  /* A-4 触控热区：把头部与双手的骨骼位置投影到画布坐标做命中判定。
   * 屏幕空间才跟得上"手真的在那"——模型转身或镜头推拉之后，视图盒里的
   * 固定矩形就不准了。没有对应骨骼时返回 null（调用方退回其它热区）。 */
  hitRegion(clientX, clientY) {
    const s = this._state;
    const canvas = s?.renderer?.domElement;
    if (!s?.camera || !canvas?.getBoundingClientRect) return null;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    const x = (clientX - rect.left) / rect.width;
    const y = (clientY - rect.top) / rect.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return null;
    const project = node => {
      const p = node.getWorldPosition(new THREE.Vector3()).project(s.camera);
      return { x: (p.x + 1) / 2, y: (1 - p.y) / 2 };
    };
    const near = (point, rx, ry) => Math.abs(point.x - x) <= rx && Math.abs(point.y - y) <= ry;
    // 命中半径来自 config/avatar.yaml 的 touch.vrm，缺省用下面的常量。
    const radii = s.touch?.vrm || {};
    const pick = (key, fallback) => {
      const value = Array.isArray(radii[key]) ? radii[key] : [];
      return [Number.isFinite(value[0]) ? value[0] : fallback[0],
              Number.isFinite(value[1]) ? value[1] : fallback[1]];
    };
    const handR = pick("hand", [0.1, 0.13]);
    const faceR = pick("face", [0.075, 0.07]);
    const headR = pick("head", [0.13, 0.15]);
    for (const name of ['leftHand', 'rightHand']) {
      const node = s.arms?.[name]?.node;
      if (node && near(project(node), handR[0], handR[1])) return 'hand';
    }
    const head = s.bones?.head?.node;
    if (head) {
      const point = project(head);
      // 脸是头内偏上的区域，其余头部算"摸头"。
      if (near({ x: point.x, y: point.y - 0.02 }, faceR[0], faceR[1])) return 'face';
      if (near(point, headR[0], headR[1])) return 'head';
    }
    return null;
  }

  /* 模型信息与许可提示位（A-5）：设置面板据此展示"当前模型 / 作者 / 许可"。 */
  getModelInfo() {
    const meta = this._state?.vrm?.meta;
    if (!meta) return null;
    return {
      name: meta.name || null,
      version: meta.version || null,
      authors: Array.isArray(meta.authors) ? meta.authors.slice(0, 8) : [],
      licenseUrl: meta.licenseUrl || null,
      avatarPermission: meta.avatarPermission || null,
      commercialUsage: meta.commercialUsage || null,
      allowRedistribution: meta.allowRedistribution || null,
      modification: meta.modification || null,
      creditNotation: meta.creditNotation || null,
      otherLicenseUrl: meta.otherLicenseUrl || null,
    };
  }

  setEyeFocus(enabled) {
    const state = this._state;
    if (!state) return;
    state.eyeFocus = !!enabled;
    this._frameCamera(state);
  }

  setSecondaryMotion(scale) {
    this.secondaryScale = finiteClamp(scale, 0, 1);
  }

  draw(params, dt) {
    const state = this._state;
    if (!state?.vrm) return;
    const mapped = mapParams(params);
    for (const [name, { node, rest }] of Object.entries(state.bones)) {
      node.quaternion.copy(rest).multiply(new THREE.Quaternion()
        .setFromEuler(new THREE.Euler(...mapped[name], 'XYZ')));
    }
    for (const [name, value] of Object.entries(selectExpressions(mapped, state.expressions))) {
      const nativeFace = state.eyeAdapter?.available && ['happy', 'sad', 'angry'].includes(name);
      state.vrm.expressionManager.setValue(name, nativeFace ? 0 : value);
    }
    state.eyeAdapter?.write(params, { browMode: this.eyeBrowMode || 'calibrated', lidMode: this.eyeLidMode || 'calibrated' });
    const f = this.performance || { weight: 0 };
    const profile = resolveGestureProfile(state.vrm.meta?.name, this.gestureCalibrationEnabled);
    const poseOptions = { profile, reduced: !!this.reduceGestures, paths: this.gesturePathsEnabled };
    const pose = sampleGesturePose(f, poseOptions);
    const referencePose = sampleGesturePose(f, { ...poseOptions, reference: true });
    const targets = {}, referenceTargets = {}, current = {};
    for (const [name, bone] of Object.entries(state.arms || {})) {
      targets[name] = bone.rest.clone().multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...(pose[name] || [0, 0, 0]))));
      referenceTargets[name] = bone.rest.clone().multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(...(referencePose[name] || [0, 0, 0]))));
      current[name] = bone.node.quaternion;
    }
    const transitionPose = this.performanceTransitionsEnabled
      ? this.poseTransition.update(f.transition, current, targets, safeDelta(dt), referenceTargets) : null;
    for (const [name, bone] of Object.entries(state.arms || {})) {
      if (transitionPose?.[name]) bone.node.quaternion.copy(transitionPose[name]);
      else bone.node.quaternion.slerp(targets[name], 1-Math.exp(-safeDelta(dt)*14));
    }
    this._applyFingers(state, f, dt);
    this._applyBody(state, f, dt);
    // LookAt reads raw head matrixWorld. Synchronize the normalized pose into
    // raw bones before refreshing matrices so gaze does not use last-frame head
    // rotation. vrm.update still owns the normal component update/render cycle.
    state.vrm.humanoid?.update?.();
    state.scene.updateMatrixWorld(true);
    const head = state.bones.head.node.getWorldPosition(new THREE.Vector3());
    // Eye parameters are the ocular residual after the idle driver has moved the
    // head. Put that direction into the animated head frame before LookAt turns
    // it back into a head-relative yaw/pitch; world axes would subtract head
    // rotation a second time and can reverse pointer gaze.
    const headRotation = state.bones.head.node.getWorldQuaternion(new THREE.Quaternion());
    state.target.position.copy(head).add(new THREE.Vector3(...mapped.gaze)
      .applyQuaternion(headRotation).multiplyScalar(state.modelHeight * 1.5));
    state.target.updateMatrixWorld(true);
    state.vrm.update(safeDelta(dt));
    if (this.secondaryScale < 1) {
      for (const { node, rest } of state.springBones) {
        node.quaternion.slerpQuaternions(rest, node.quaternion.clone(), this.secondaryScale);
      }
    }
    /* 呼吸景深（A-5）：镜头沿基准光轴做极缓慢的推拉（默认 ±1.2% 距离），
     * 像持机拍摄时人的呼吸。不是真的散景——那需要后处理管线，而持续视差
     * 已经能读成"镜头是活的"。周期刻意避开呼吸(4.1s)和头发慢漂(7.3s)的
     * 整数倍，防止出现循环点；reduced-motion 下压到四分之一。 */
    const dof = state.stage?.dof;
    if (dof && dof.enabled !== false && state.dolly) {
      const amount = Number.isFinite(dof.amount) ? dof.amount : 0.012;
      const period = Math.max(1, Number.isFinite(dof.period) ? dof.period : 6.3);
      const scale = this.reduceGestures ? 0.25 : 1;
      this._dofPhase = (((this._dofPhase || 0) + safeDelta(dt) / period) % 1 + 1) % 1;
      const k = 1 + Math.sin(this._dofPhase * Math.PI * 2) * amount * scale;
      state.camera.position.set(state.dolly.focus.x, state.dolly.focus.y,
        state.dolly.focus.z + state.dolly.distance * k);
      state.camera.lookAt(state.dolly.focus);
    }
    state.renderer.render(state.scene, state.camera);
  }

  dispose() {
    ++this._generation;
    this.handPosePreview = null;
    this.poseTransition.reset();
    const state = this._state;
    this._state = null;
    if (!state) return;
    state.observer?.disconnect();
    state.removeResize?.();
    if (state.vrm?.lookAt?.target === state.target) state.vrm.lookAt.target = null;
    state.eyeAdapter?.dispose();
    state.accessories?.dispose();
    releaseScenes(state.roots);
    state.renderer?.dispose();
    state.renderer?.forceContextLoss();
    state.renderer?.domElement.remove(); // Never clear the host or other renderers.
  }
}
