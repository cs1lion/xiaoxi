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
import { StageCamera, resolveMorphWeights } from './stage.js';

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
    this.stageCamera = new StageCamera();
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
      state.ambient = new THREE.HemisphereLight(0xfff6ec, 0x9c9aa8, 1.62);
      state.scene.add(state.ambient);
      const key = new THREE.DirectionalLight(0xfff2e2, 2.45);
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
      state.lights = lights;
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
      const stageCfg = (options && options.stage && typeof options.stage === "object") ? options.stage : {};
      state.stage = stageCfg;
      this._captureFaceShading(state);
      this._captureStageMaterials(state);
      state.touch = (options && options.touch && typeof options.touch === "object") ? options.touch : {};
      this.stageCamera.configure(stageCfg);
      this.stageCamera.reset();
      this._captureStageMorphs(state);
      const bounds = new THREE.Box3().setFromObject(vrm.scene);
      const head = state.bones.head.node.getWorldPosition(new THREE.Vector3());
      state.headY = head.y;
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
      this._buildAtmosphere(state);
      this._watchInteraction(state);
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
      let width = framing.width;
      let height = framing.height * crop;
      let centerY = framing.center.y + (state.presentation && !state.eyeFocus ? framing.height * 0.035 : 0);
      let yaw = 0;
      if (state.stage && !state.eyeFocus && state.modelHeight) {
        // The one source of stage framing: idle medium shot vs speaking close-up.
        // eyeFocus keeps its calibration framing byte-for-byte.
        const sampled = this.stageCamera.sample(state.modelHeight,
          { width, height, centerY, headY: state.headY });
        width = sampled.width; height = sampled.height; centerY = sampled.centerY; yaw = sampled.yaw;
      }
      const distance = Math.max(height / 2, width / (2 * camera.aspect)) /
        Math.tan(halfFov) + framing.depth;
      const focus = new THREE.Vector3(framing.center.x, centerY, framing.center.z);
      const offset = new THREE.Vector3(Math.sin(yaw) * distance, 0, Math.cos(yaw) * distance);
      camera.position.copy(focus).add(offset);
      camera.near = Math.max(0.001, distance / 1000);
      camera.far = Math.max(100, distance * 10);
      camera.lookAt(focus);
      // 呼吸景深沿这条基准光轴推拉（见 draw）。
      state.dolly = { focus: focus.clone(), distance, offset };
    }
    camera.updateProjectionMatrix();
  }

  // Reversible presentation only; original model assets and material colors stay intact.
  setPresentation(enabled) {
    const s = this._state;
    if (!s?.framing) return;
    s.presentation = !!enabled;
    const lights = s.lights || {};
    const show = lights.presentation || {};
    if (enabled) {
      s.ambient.intensity = Number.isFinite(show.ambient) ? show.ambient : 1.35;
      s.key.intensity = Number.isFinite(show.key_intensity) ? show.key_intensity : 2.0;
      s.key.color.set(show.key_color || 0xffe5d1);
      const keyAt = show.key_position || [-2, 2, 3];
      s.key.position.set(keyAt[0] ?? -2, keyAt[1] ?? 2, keyAt[2] ?? 3);
      s.fill.intensity = Number.isFinite(show.fill_intensity) ? show.fill_intensity : 0.65;
      s.fill.color.set(show.fill_color || 0xbadce8);
      const fillAt = show.fill_position || [-2, 1, 2];
      s.fill.position.set(fillAt[0] ?? -2, fillAt[1] ?? 1, fillAt[2] ?? 2);
      if (s.rim) s.rim.intensity = s.rimBase * (Number.isFinite(show.rim_scale) ? show.rim_scale : 1.33);
    } else {
      s.ambient.intensity = 2;
      s.key.intensity = 2.5;
      s.key.color.set(0xffffff);
      s.key.position.set(1, 2, 3);
      s.fill.intensity = 0;
      s.fill.color.set(0xbadce8);
      s.fill.position.set(-2, 1, 2);
      if (s.rim) s.rim.intensity = s.rimBase;
    }
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

  // Resting expression morphs come from the asset's own dictionary. Unknown
  // names are dropped, and targets bound to a VRM expression keep their driven
  // value because this is written before expressionManager.update().
  _captureStageMorphs(state) {
    const requested = state.stage?.expression?.morphs || {};
    state.stageMorphs = [];
    const names = Object.keys(requested);
    if (!names.length || !state.vrm?.scene) return;
    const available = new Map();
    state.vrm.scene.traverse(node => {
      const dictionary = node.morphTargetDictionary, influences = node.morphTargetInfluences;
      if (!dictionary || !influences) return;
      for (const name of names) {
        const index = dictionary[name];
        if (!Number.isInteger(index) || index < 0 || index >= influences.length) continue;
        let targets = available.get(name);
        if (!targets) available.set(name, targets = []);
        if (!targets.some(target => target.influences === influences && target.index === index)) {
          targets.push({ influences, index });
        }
      }
    });
    state.stageMorphs = resolveMorphWeights(requested, available);
  }

  _applyStageMorphs(state) {
    for (const { weight, targets } of state.stageMorphs || []) {
      for (const { influences, index } of targets) influences[index] = weight;
    }
  }

  // The speaking close-up is driven by the lip clock, not by layout state.
  setSpeaking(speaking) {
    this.stageCamera.setSpeaking(!!speaking);
  }

  // Cool hair sheen and a softer skin toon boundary. Only the Daily asset is
  // tuned; an external VRM keeps its own material intent.
  _captureStageMaterials(state) {
    if (state.vrm?.meta?.name !== DAILY_GESTURE_MODEL_NAME) return;
    const look = state.stage?.look || {};
    const hairRim = look.hair_rim || {};
    const rimColor = new THREE.Color(hairRim.color || '#a9c9e8')
      .multiplyScalar(Number.isFinite(hairRim.strength) ? hairRim.strength : 1);
    // 衣料的轮廓光：暗窗里衣服是一整片米白，没有边缘就贴在背景上。
    // 只给布料，不动皮肤与五官——脸上打冷边光会立刻变成塑料。
    const bodyRim = look.body_rim || {};
    const clothRim = new THREE.Color(bodyRim.color || '#cfe2ff')
      .multiplyScalar(Number.isFinite(bodyRim.strength) ? bodyRim.strength : 0.5);
    const CLOTH = /jacket|sleeve|trouser|collar|neckline|knit|waistband|cuff|wear/i;
    const seen = new Set();
    state.vrm.scene.traverse(node => {
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (!material?.isMToonMaterial || seen.has(material)) continue;
        seen.add(material);
        const name = material.name || '';
        if (/hair/i.test(name)) {
          material.parametricRimColorFactor?.copy(rimColor);
          if (material.parametricRimFresnelPowerFactor !== undefined) {
            material.parametricRimFresnelPowerFactor = Number.isFinite(hairRim.fresnel) ? hairRim.fresnel : 3.4;
          }
          if (material.parametricRimLiftFactor !== undefined) {
            material.parametricRimLiftFactor = Number.isFinite(hairRim.lift) ? hairRim.lift : 0.05;
          }
          if (material.rimLightingMixFactor !== undefined) {
            material.rimLightingMixFactor = Number.isFinite(hairRim.mix) ? hairRim.mix : 0.7;
          }
          if (Number.isFinite(look.hair_outline)) material.outlineWidthFactor = look.hair_outline;
        } else if (CLOTH.test(name)) {
          material.parametricRimColorFactor?.copy(clothRim);
          if (material.parametricRimFresnelPowerFactor !== undefined) {
            material.parametricRimFresnelPowerFactor = Number.isFinite(bodyRim.fresnel) ? bodyRim.fresnel : 4.4;
          }
          if (material.parametricRimLiftFactor !== undefined) {
            material.parametricRimLiftFactor = Number.isFinite(bodyRim.lift) ? bodyRim.lift : 0.0;
          }
          if (material.rimLightingMixFactor !== undefined) {
            material.rimLightingMixFactor = Number.isFinite(bodyRim.mix) ? bodyRim.mix : 0.32;
          }
        } else if (name === 'body_bake' || name === 'body_bake (Outline)') {
          if (Number.isFinite(look.face_toony)) material.shadingToonyFactor = look.face_toony;
          if (Number.isFinite(look.face_outline)) material.outlineWidthFactor = look.face_outline;
        }
      }
    });
  }

  // Front/side hair spring bones are the only hair chains in the hair candidate.
  // Keep their loaded settings as originals and always recompute from them.
  _captureHairPhysics(state) {    state.hairPhysics = [];
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

  /* ---------- 舞台空气：尘埃、头顶光晕、状态补光 ----------
   * 三样都是新增的发光体，只读真实状态（#avatarStage[data-interaction] 由
   * app.js 每帧写入，另加 speak/listen 时的麦克风包络），不新增业务状态。
   * 刻意不改 ambient / key / fill：calibration 会冻结这三盏灯并在退出时还原，
   * 逐帧改写它们会破坏眼部标定与指纹比对。新灯在静默时强度为 0，所以
   * 默认画面与加这一层之前一致，只在状态变化时才有光。 */
  _buildAtmosphere(state) {
    if (!state?.scene || typeof document === 'undefined') return;
    const height = state.modelHeight || 1.6;
    const framing = state.framing;
    const center = (framing?.center ? framing.center : new THREE.Vector3(0, height * 0.78, 0)).clone();
    const visH = framing?.height || height * 0.5;

    // 一张 64px 的径向渐变贴图，光晕与尘埃共用：不额外请求图片。
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const brush = canvas.getContext('2d');
    if (!brush) return;
    const grad = brush.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.32, 'rgba(255,255,255,.6)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    brush.fillStyle = grad;
    brush.fillRect(0, 0, 64, 64);
    const sprite = new THREE.CanvasTexture(canvas);
    sprite.colorSpace = THREE.SRGBColorSpace;
    state.atmosTexture = sprite;

    // 头顶光晕：一块加色 sprite，摆在人物身后。必须开 depthTest——加色精灵
    // 属于透明队列，无论 renderOrder 多小都在不透明几何之后绘制；关掉深度
    // 测试就等于把整张脸盖上一层白光（第一次调就是这么白掉的）。
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({
      map: sprite, color: 0xffc47d, transparent: true, opacity: 0.46,
      depthWrite: false, depthTest: true, blending: THREE.AdditiveBlending,
    }));
    halo.scale.setScalar(visH * 2.1);
    halo.position.set(center.x, center.y + visH * 0.3, center.z - visH * 1.1);
    halo.renderOrder = -2;
    state.halo = halo;
    state.scene.add(halo);

    // 空气尘分两层：后景大而虚的光斑，和前景细而亮的浮尘。同一张三通道
    // 贴图，只靠尺寸与透明度拉开景深；全部在她身后，被身体正常遮挡。
    const narrow = (globalThis.innerWidth || 1440) < 760;
    const halfW = visH * 0.78;
    const halfH = visH * 0.72;
    const make = (count, size, opacity, zNear, zFar) => {
      const positions = new Float32Array(count * 3);
      const seeds = new Float32Array(count * 3);
      for (let i = 0; i < count; i++) {
        positions[i * 3] = center.x + (Math.random() - 0.5) * halfW * 2;
        positions[i * 3 + 1] = center.y + (Math.random() - 0.5) * halfH * 2;
        positions[i * 3 + 2] = center.z - visH * (zNear + Math.random() * (zFar - zNear));
        seeds[i * 3] = Math.random() * Math.PI * 2;
        seeds[i * 3 + 1] = 0.5 + Math.random();
        seeds[i * 3 + 2] = 0.35 + Math.random() * 0.65;
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
      const points = new THREE.Points(geometry, new THREE.PointsMaterial({
        map: sprite, color: 0xffd2a0, size, sizeAttenuation: true,
        transparent: true, opacity, depthWrite: false,
        blending: THREE.AdditiveBlending,
      }));
      points.frustumCulled = false;
      state.scene.add(points);
      return { points, seeds };
    };
    const motes = make(narrow ? 40 : 92, height * 0.026, 0.6, 0.05, 0.45);
    const bokeh = make(narrow ? 6 : 12, height * 0.075, 0.055, 0.3, 1.05);
    state.dust = [motes, bokeh];
    state.dustBox = { minY: center.y - halfH, maxY: center.y + halfH, halfW };
    state.dustFocus = new THREE.Vector3(center.x, center.y + visH * 0.28, center.z - visH * 0.4);
    state.dustSpeed = visH;

    // 状态补光：暖侧光（说话/倾听时抬起）与冷背光（思考时抬起）。
    const warm = new THREE.PointLight(0xffb877, 0, visH * 2.4);
    warm.position.set(center.x + visH * 0.7, center.y + visH * 0.35, center.z + visH * 0.55);
    state.moodWarm = warm;
    state.scene.add(warm);
    const cool = new THREE.PointLight(0x9cc4ff, 0, visH * 1.8);
    cool.position.set(center.x - visH * 0.8, center.y + visH * 0.2, center.z - visH * 0.5);
    state.moodCool = cool;
    state.scene.add(cool);

    state.atmosClock = 0;
    state.atmosWarm = 0.42;
    state.atmosCool = 0;
    state.atmosVisH = visH;
    state.reduceMotion = !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
  }

  /* 状态源只有一处：容器上是哪个 data-interaction，光就长什么样。 */
  _watchInteraction(state) {
    const host = state.container;
    const read = () => (state.interaction = host?.dataset?.interaction || 'idle');
    read();
    if (!host || typeof MutationObserver !== 'function') return;
    state.atmosObserver = new MutationObserver(read);
    state.atmosObserver.observe(host, { attributes: true, attributeFilter: ['data-interaction'] });
  }

  /* 麦克风包络：app.js 把真实 RMS 写进 #meterFill 的宽度，这里只读，不猜。 */
  _listenLevel() {
    const fill = document.getElementById?.('meterFill');
    const value = fill ? parseFloat(fill.style.width || '0') : 0;
    return Number.isFinite(value) ? Math.min(1, Math.max(0, value / 100)) : 0;
  }

  _updateAtmosphere(state, dt) {
    const halo = state.halo;
    if (!halo || !state.dust?.length) return;
    const seconds = Number.isFinite(dt) && dt > 0 ? Math.min(0.1, dt) : 0;
    state.atmosClock = (state.atmosClock || 0) + seconds;
    const clock = state.atmosClock;
    const mood = state.interaction || 'idle';
    const listening = mood === 'listening';
    const speaking = mood === 'speaking';
    const thinking = mood === 'thinking' || mood === 'preparing';
    const quiet = mood === 'unavailable' || mood === 'yielding';
    const level = listening || speaking ? this._listenLevel() : 0;

    // 权重指数趋近，避免状态切换时光硬切；静默时几乎不动。
    const ease = Math.min(1, seconds * 2.4);
    const targetWarm = quiet ? 0.08 : listening ? 1 : speaking ? 0.7 : 0.34;
    const targetCool = thinking ? 1 : 0;
    state.atmosWarm += (targetWarm - state.atmosWarm) * ease;
    state.atmosCool += (targetCool - state.atmosCool) * ease;
    const warm = state.atmosWarm;
    const cool = state.atmosCool;
    const breath = state.reduceMotion ? 0.5 : 0.5 + 0.5 * Math.sin(clock * 0.52);

    // 光晕：暖金呼吸 + 思考时转冷；说话时随音量再亮一点。
    halo.material.opacity = (0.2 + warm * 0.26 + breath * 0.12 + level * 0.12) * (quiet ? 0.45 : 1);
    halo.material.color.setRGB(
      1 - cool * 0.36,
      1 - cool * 0.2 + warm * 0.02,
      1 - warm * 0.42 + cool * 0.12,
    );
    const haloScale = (state.atmosVisH || 1) * (2.0 + breath * 0.12 + warm * 0.16);
    halo.scale.setScalar(haloScale);

    // 补光：暖侧光吃"倾听/说话"，冷背光只在思考时抬起来。
    // 点光源按平方衰减，这里离脸不到一米，强度必须压得很低——1 以上会把
    // 整张脸推到过曝（第一次调就是 1.1，人物直接白掉）。
    if (state.moodWarm) {
      state.moodWarm.intensity = ((listening ? 0.9 : speaking ? 0.72 : 0.42) * warm + level * 0.8) * 0.16;
      state.moodWarm.color.setHex(warm > 0.6 ? 0xffc487 : 0xff9f66);
    }
    if (state.moodCool) state.moodCool.intensity = cool * 0.3;

    // 尘埃：慢速上升 + 横向摆动；思考时向她聚拢，静默时几乎停住。
    const box = state.dustBox;
    const focus = state.dustFocus;
    const unit = state.atmosVisH || 1;
    const drift = state.reduceMotion ? 0.25 : quiet ? 0.5 : 1;
    const converge = thinking ? 0.55 : 0;
    for (const layer of state.dust) {
      const attr = layer.points.geometry.attributes.position;
      const seeds = layer.seeds;
      for (let i = 0, n = attr.count; i < n; i++) {
        const i3 = i * 3;
        const phase = seeds[i3];
        const speed = seeds[i3 + 1];
        let x = attr.array[i3];
        let y = attr.array[i3 + 1];
        y += (0.008 + speed * 0.008) * unit * seconds * drift;
        x += Math.sin(clock * 0.34 + phase) * 0.0014 * unit * seconds * drift;
        if (converge) {
          x += (focus.x - x) * converge * seconds * 0.5;
          y += (focus.y - y) * converge * seconds * 0.5;
        }
        if (y > box.maxY || Math.abs(x - focus.x) > box.halfW) {
          y = box.minY;
          x = focus.x + (Math.random() - 0.5) * box.halfW * 2;
        }
        attr.array[i3] = x;
        attr.array[i3 + 1] = y;
      }
      attr.needsUpdate = true;
      const base = layer.baseOpacity ?? layer.points.material.opacity;
      layer.baseOpacity = base;
      layer.points.material.opacity = base * (0.72 + warm * 0.5 + level * 0.4) * (quiet ? 0.55 : 1);
    }
  }

  _disposeAtmosphere(state) {
    state.atmosObserver?.disconnect();
    const objects = [state.halo, ...(state.dust || []).map(layer => layer.points)];
    for (const object of objects) {
      if (!object) continue;
      object.removeFromParent();
      object.material?.dispose?.();
      if (object.geometry) object.geometry.dispose();
    }
    for (const light of [state.moodWarm, state.moodCool]) light?.removeFromParent();
    state.atmosTexture?.dispose?.();
    state.halo = state.dust = state.moodWarm = state.moodCool = state.atmosTexture = null;
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
    this._applyStageMorphs(state);
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
    this.stageCamera.update(safeDelta(dt));
    state.stageBlend = this.stageCamera.eased;
    state.stageMode = this.stageCamera.mode;
    if (state.stage && !state.eyeFocus) this._frameCamera(state);
    const dof = state.stage?.dof;
    if (dof && dof.enabled !== false && state.dolly) {
      const amount = Number.isFinite(dof.amount) ? dof.amount : 0.012;
      const period = Math.max(1, Number.isFinite(dof.period) ? dof.period : 6.3);
      const scale = this.reduceGestures ? 0.25 : 1;
      this._dofPhase = (((this._dofPhase || 0) + safeDelta(dt) / period) % 1 + 1) % 1;
      const k = 1 + Math.sin(this._dofPhase * Math.PI * 2) * amount * scale;
      state.camera.position.set(
        state.dolly.focus.x + state.dolly.offset.x * k,
        state.dolly.focus.y,
        state.dolly.focus.z + state.dolly.offset.z * k);
      state.camera.lookAt(state.dolly.focus);
    }
    this._updateAtmosphere(state, safeDelta(dt));
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
    this._disposeAtmosphere(state);
    releaseScenes(state.roots);
    state.renderer?.dispose();
    state.renderer?.forceContextLoss();
    state.renderer?.domElement.remove(); // Never clear the host or other renderers.
  }
}
