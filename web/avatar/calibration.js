/* Development-only fixed-pose session. It uses the production renderer and
 * expression driver, pauses Avatar's RAF, and never creates another loop. */
import { ALL_PARAMS, DEFAULT_NEUTRAL, clampParam } from './params.js';
import { EyeExpressionDriver, combineEyeOpen } from './drivers/eye-expression.js';

const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const rounded = value => Math.round(number(value) * 1e7) / 1e7;
const array = value => value?.toArray ? value.toArray().map(rounded)
  : Array.isArray(value) ? value.map(rounded) : null;
const unit = (value, fallback = 0) => Math.max(0, Math.min(1, number(value, fallback)));
const signed = value => Math.max(-1, Math.min(1, number(value)));
const jsonCopy = value => value == null ? value : JSON.parse(JSON.stringify(value));
const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
const stateOf = rig => rig?._state || null;
const cameraOf = rig => stateOf(rig)?.camera || rig?.camera;
const canvasOf = rig => stateOf(rig)?.renderer?.domElement || rig?.renderer?.domElement || rig?.root;
const headOf = rig => stateOf(rig)?.bones?.head?.node || rig?.head;
const sceneOf = rig => stateOf(rig)?.vrm?.scene || rig?.scene;
const managerOf = rig => stateOf(rig)?.vrm?.expressionManager;

function viewport(rig) {
  const element = canvasOf(rig);
  const rect = element?.getBoundingClientRect?.() || {};
  return { width: rounded(rect.width || element?.clientWidth || 0),
    height: rounded(rect.height || element?.clientHeight || 0),
    backingWidth: number(element?.width), backingHeight: number(element?.height),
    pixelRatio: rounded(globalThis.devicePixelRatio || 1) };
}

export function serializeCalibrationCamera(rig) {
  const camera = cameraOf(rig);
  if (!camera) return { kind: 'svg', viewBox: rig?.root?.getAttribute?.('viewBox') || null };
  camera.updateMatrixWorld?.(true);
  return { kind: camera.type || 'camera', fov: rounded(camera.fov), aspect: rounded(camera.aspect),
    near: rounded(camera.near), far: rounded(camera.far), zoom: rounded(camera.zoom ?? 1),
    position: array(camera.position), quaternion: array(camera.quaternion), up: array(camera.up),
    matrixWorld: array(camera.matrixWorld), matrixWorldInverse: array(camera.matrixWorldInverse),
    projectionMatrix: array(camera.projectionMatrix) };
}

function headAnchor(rig) {
  const head = headOf(rig), camera = cameraOf(rig), view = viewport(rig);
  if (!head?.position?.clone || !camera) return { available: false, reason: 'No independent 3D head anchor' };
  const world = head.getWorldPosition(head.position.clone());
  const ndc = world.clone().project(camera);
  const x = (number(ndc.x) + 1) / 2, y = (1 - number(ndc.y)) / 2;
  return { available: true, bone: head.name || head.uuid || 'head', world: array(world),
    x: rounded(x), y: rounded(y), ndc: array(ndc),
    pixel: { x: rounded(x * view.width), y: rounded(y * view.height) },
    inside: x >= 0 && x <= 1 && y >= 0 && y <= 1 && number(ndc.z) >= -1 && number(ndc.z) <= 1 };
}

function appearance(rig) {
  const s = stateOf(rig), materials = new Map();
  sceneOf(rig)?.traverse?.(node => {
    for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
      if (material && !materials.has(material.uuid || material.name)) materials.set(material.uuid || material.name,
        { name: material.name || '', color: array(material.color), shadeColorFactor: array(material.shadeColorFactor), opacity: rounded(material.opacity ?? 1),
          map: material.map?.uuid || null, transparent: !!material.transparent,
          depthTest: material.depthTest !== false, depthWrite: material.depthWrite !== false });
    }
  });
  return { presentation: !!s?.presentation, eyeFocus: !!s?.eyeFocus, palette: s?.palette || 'original',
    faceShading: rig?.getFaceShading?.() || null,
    hairPhysics: rig?.getHairPhysics?.() || null,
    lights: ['ambient', 'key', 'fill'].map(name => ({ name, intensity: rounded(s?.[name]?.intensity),
      color: array(s?.[name]?.color), position: array(s?.[name]?.position) })),
    materials: [...materials.values()], svgLook: rig?.look ? jsonCopy(rig.look) : null };
}

function fingerprintId(conditions) {
  const text = JSON.stringify(conditions);
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return (hash >>> 0).toString(16).padStart(8, '0');
}

export function captureCalibrationFingerprint(avatar) {
  const rig = avatar.svg, s = stateOf(rig), meta = s?.vrm?.meta || {};
  const conditions = { asset: { renderer: avatar.rendererKind || 'svg', request: avatar._rendererRequest || 0,
    generation: rig?._generation || 0, scene: sceneOf(rig)?.uuid || rig?.root?.id || null,
    name: meta.name || meta.title || null, version: meta.version || meta.metaVersion || null },
    camera: serializeCalibrationCamera(rig), viewport: viewport(rig),
    head: headAnchor(rig).world || null, appearance: appearance(rig) };
  return { schema: 1, id: fingerprintId(conditions), conditions };
}

export function compareCalibrationFingerprints(before, after) {
  if (!before?.conditions || !after?.conditions) return { compatible: false, reasons: ['missing-fingerprint'] };
  const reasons = [];
  if (before.schema !== after.schema) reasons.push('schema');
  for (const key of new Set([...Object.keys(before.conditions), ...Object.keys(after.conditions)])) {
    if (JSON.stringify(before.conditions[key]) !== JSON.stringify(after.conditions[key])) reasons.push(key);
  }
  return { compatible: reasons.length === 0, reasons };
}

function nativeSnapshot(rig) {
  const nativeMorphs = {}, morphTargets = [];
  sceneOf(rig)?.traverse?.(mesh => {
    for (const [name, index] of Object.entries(mesh.morphTargetDictionary || {})) {
      const short = name.slice(name.lastIndexOf('.') + 1), weight = rounded(mesh.morphTargetInfluences?.[index]);
      nativeMorphs[short] ??= weight;
      morphTargets.push({ mesh: mesh.name || mesh.uuid || '', name, index, weight });
    }
  });
  const manager = managerOf(rig), expressionWeights = {};
  for (const name of Object.keys(manager?.expressionMap || {})) expressionWeights[name] = rounded(manager.getValue(name));
  return { nativeMorphs, morphTargets, expressionWeights };
}

function components(rig, capabilities) {
  const head = headOf(rig), records = [];
  const bindings = { eyes: /eye|EYE/, brows: /brow|BRW/, mouth: /lip|MTH|mouth/ };
  for (const [id, pattern] of Object.entries(bindings)) {
    const meshes = [], channels = [], materials = [];
    sceneOf(rig)?.traverse?.(mesh => {
      const names = Object.keys(mesh.morphTargetDictionary || {}).filter(name => pattern.test(name));
      if (!names.length) return;
      meshes.push(mesh.name || mesh.uuid || ''); channels.push(...names);
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        if (material) materials.push({ name: material.name || '', transparent: !!material.transparent,
          depthTest: material.depthTest !== false, depthWrite: material.depthWrite !== false,
          alphaTest: rounded(material.alphaTest) });
      }
    });
    records.push({ id, parent: head?.name || head?.uuid || 'svg-features', meshes: [...new Set(meshes)],
      pivot: null, pivotMode: meshes.length ? 'mesh-local morph deformation; no independent pivot' : 'procedural SVG',
      channels: [...new Set(channels)], occlusion: { mode: meshes.length ? 'material/depth' : 'SVG paint order and clipping',
        validated: false, materials },
      missing: id === 'eyes' ? [!capabilities.channels?.lowerLid?.available ? 'independent lower eyelid controls are not established' : null,
        'independent upper eyelid control is not established'].filter(Boolean)
        : id === 'brows' ? Object.entries(capabilities.channels || {}).filter(([key, c]) => key.startsWith('brow') && !c.available).map(([key]) => key) : [] });
  }
  if (rig?.nodes?.eyes) for (const eye of rig.nodes.eyes) records.push({ id: eye.side < 0 ? 'svg-eye-left' : 'svg-eye-right',
    parent: 'svg-features', pivot: [eye.side * 35, 4], pivotMode: 'SVG local transform',
    channels: ['opening', 'smile', 'iris', 'pupil', 'gaze'],
    occlusion: { mode: 'clipPath', clipPath: eye.clipped?.getAttribute?.('clip-path') || null, validated: false }, missing: [] });
  return records;
}

// Copy only driver data. Browser/media handles, configuration and the renderer's
// Three.js objects retain their identities; rest-bone objects are never copied.
function cloneDriverData(value, key = '', seen = new Map()) {
  if (value === null || typeof value !== 'object' || ['cfg', 'clock', 'audioCtx', '_audioClock', 'landmarker', 'video', 'stream'].includes(key)) return value;
  if (seen.has(value)) return seen.get(value);
  const proto = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && proto !== Object.prototype && proto !== null && !['Spring', 'DelayLine'].includes(value.constructor?.name)) return value;
  const result = Array.isArray(value) ? [] : Object.create(proto);
  seen.set(value, result);
  for (const [name, item] of Object.entries(value)) result[name] = cloneDriverData(item, name, seen);
  return result;
}

function saveFields(object) {
  if (!object) return null;
  return Object.fromEntries(Object.entries(object).map(([key, value]) => [key, cloneDriverData(value, key)]));
}
function restoreFields(object, saved) {
  if (!object || !saved) return;
  for (const key of Object.keys(object)) if (!own(saved, key)) delete object[key];
  for (const [key, value] of Object.entries(saved)) object[key] = cloneDriverData(value, key);
}

export class AvatarCalibration {
  constructor(avatar) {
    if (!avatar?.svg || !avatar?.store) throw new TypeError('AvatarCalibration requires a configured Avatar');
    this.avatar = avatar;
    this.active = false;
    this.params = null;
    this.currentPose = null;
  }

  enter(options = {}) {
    if (this.active) this.exit();
    const a = this.avatar, rig = a.svg, s = stateOf(rig);
    if (rig._state !== undefined && !s?.vrm) throw new Error('Wait for the model to finish loading before calibration');
    a.dialogueReview?.stop('calibration');
    const transforms = [];
    sceneOf(rig)?.traverse?.(node => {
      if (node.position?.clone && node.quaternion?.clone && node.scale?.clone) transforms.push({ node,
        position: node.position.clone(), quaternion: node.quaternion.clone(), scale: node.scale.clone(),
        morphs: node.morphTargetInfluences ? [...node.morphTargetInfluences] : null });
    });
    const drivers = {};
    for (const key of ['idle', 'lip', 'emotion', 'eyeExpression', 'dialogueGaze', 'dialogueExpression', 'gesture', 'face', 'beat', 'motion', 'discourseGesture', 'performance'])
      if (a[key]) drivers[key] = { object: a[key], fields: saveFields(a[key]) };
    const dom = rig.root?.querySelectorAll ? [rig.root, ...rig.root.querySelectorAll('*')].map(node => ({ node,
      attributes: [...node.attributes].map(attribute => [attribute.name, attribute.value]) })) : [];
    const camera = cameraOf(rig);
    this.saved = { rig, state: s, request: a._rendererRequest, generation: rig._generation,
      running: !!a._raf, eyeFrame: a.eyeFrame, storeObject: a.store, store: saveFields(a.store), drivers,
      avatar: Object.fromEntries(['clock', 'gazeMode', 'eyeHighlight', '_eyeBlinkTime', '_lookAroundTime', '_lookAroundIndex',
        'reducedMotion', 'gestureScale', 'dialogueIntentMode', 'dialogueFrame', 'dialogueExpressionFrame'].map(key => [key, a[key]])),
      renderer: Object.fromEntries(['performance', 'reduceGestures', 'performanceTransitionsEnabled', 'gestureCalibrationEnabled', 'gesturePathsEnabled', 'fingerRelaxationEnabled', 'handPosePreview', 'bodyCoordinationEnabled', 'faceShadingEnabled', 'hairPhysicsEnabled', 'hairPhysicsStrength', 'eyeBrowMode', 'eyeLidMode', 'secondaryScale', 'secondary', 'bgLevel', 'hairT'].map(key => [key,
        { present: own(rig, key), value: rig[key] }])),
      poseTransition: rig.poseTransition ? { object: rig.poseTransition, fields: {
        ...saveFields(rig.poseTransition),
        // These are transition origins, independent from the rig's rest bones.
        origins: Object.fromEntries(Object.entries(rig.poseTransition.origins || {}).map(([key, origin]) =>
          [key, origin?.clone ? origin.clone() : cloneDriverData(origin)])),
      } } : null,
      spring: rig.spring ? Object.fromEntries(Object.entries(rig.spring).map(([key, spring]) => [key, saveFields(spring)])) : null,
      transforms, dom, viewport: viewport(rig), camera: camera ? {
        ...Object.fromEntries(['fov', 'aspect', 'near', 'far', 'zoom'].map(key => [key, camera[key]])),
        position: camera.position.clone(), quaternion: camera.quaternion.clone(), up: camera.up.clone(),
      } : null,
      expressions: nativeSnapshot(rig).expressionWeights,
      faceMaterials: (s?.faceShading || []).map(({ material }) => ({ material,
        color: material.color.clone(), shadeColorFactor: material.shadeColorFactor.clone() })),
      hairPhysics: (s?.hairPhysics || []).map(({ joint }) => ({ joint, settings: {
        stiffness: joint.settings.stiffness, gravityPower: joint.settings.gravityPower,
        dragForce: joint.settings.dragForce, hitRadius: joint.settings.hitRadius } })),
      presentation: s?.presentation, eyeFocus: s?.eyeFocus,
      lights: ['ambient', 'key', 'fill'].map(key => ({ key, object: s?.[key], intensity: s?.[key]?.intensity,
        color: s?.[key]?.color?.clone(), position: s?.[key]?.position?.clone() })) };
    if (a._raf) globalThis.cancelAnimationFrame?.(a._raf);
    a._raf = 0;
    this.active = true;
    this.fingerprint = null;
    if (own(options, 'presentation')) rig.setPresentation?.(!!options.presentation);
    if (own(options, 'eyeFocus')) rig.setEyeFocus?.(!!options.eyeFocus);
    this.pose({ expression: null });
    this.fingerprint = captureCalibrationFingerprint(a);
    return this.snapshot();
  }

  isCurrent() {
    const a = this.avatar, saved = this.saved;
    const sameAsset = !!this.active && a.store === saved.storeObject && a.svg === saved.rig && stateOf(a.svg) === saved.state &&
      a._rendererRequest === saved.request && a.svg._generation === saved.generation;
    return sameAsset && (!this.fingerprint ||
      compareCalibrationFingerprints(this.fingerprint, captureCalibrationFingerprint(a)).compatible);
  }

  _assertCurrent() {
    if (this.isCurrent()) return;
    if (this.active) this.exit();
    throw new Error('Calibration session is inactive, the model changed, or fixed scene conditions changed; enter a new session');
  }

  pose(input = {}) {
    this._assertCurrent();
    const a = this.avatar, rig = a.svg, s = stateOf(rig);
    const browMode = input.browMode ?? 'calibrated';
    if (!['calibrated', 'native'].includes(browMode)) throw new RangeError('Unknown eyebrow deformation mode');
    const lidMode = input.lidMode ?? 'calibrated';
    if (!['calibrated', 'native'].includes(lidMode)) throw new RangeError('Unknown eyelid deformation mode');
    const pose = { expression: input.expression ?? null, intensity: unit(input.intensity, 1), blink: unit(input.blink), browMode, lidMode,
      gaze: { x: signed(input.gaze?.x), y: signed(input.gaze?.y) }, mouthOpen: unit(input.mouthOpen), overrides: {} };
    const neutral = { ...DEFAULT_NEUTRAL, ...a.store.neutral };
    const profile = lidMode === 'calibrated' ? rig.getEyeCapabilities?.().profile : 'native';
    const driver = new EyeExpressionDriver({ neutral, profile });
    driver.set(pose.expression); driver.setIntensity(pose.intensity);
    const sample = driver.update(10), params = { ...neutral, ...sample.values };
    for (const [key, value] of Object.entries(input.overrides || {})) {
      if (ALL_PARAMS.includes(key)) params[key] = pose.overrides[key] = clampParam(key, value);
    }
    // Registration fixes the head/body independently of the target expression.
    for (const key of ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBodyAngleX', 'ParamBodyAngleZ', 'ParamBodyLeanX', 'ParamBreath']) params[key] = 0;
    params.ParamEyeBallX = pose.gaze.x; params.ParamEyeBallY = pose.gaze.y;
    params.ParamMouthOpenY = pose.mouthOpen;
    params.ParamEyeLOpen = combineEyeOpen(params.ParamEyeLOpen, pose.blink);
    params.ParamEyeROpen = combineEyeOpen(params.ParamEyeROpen, pose.blink);
    for (const key of ALL_PARAMS) params[key] = clampParam(key, params[key]);
    rig.setPerformance?.({ name: null, weight: 0, phase: 0, strength: 0 }, true);
    rig.setEyeBrowMode?.(browMode);
    rig.setEyeLidMode?.(lidMode);
    rig.setSecondaryMotion?.(0);
    for (const { node, rest } of [...Object.values(s?.arms || {}), ...Object.values(s?.fingers || {}), ...Object.values(s?.bodyCoordination || {}),
      ...(s?.springBones || [])]) node.quaternion.copy(rest);
    // SVG springs also need an exact fixed pose; dt=0 alone preserves old state.
    for (const [key, spring] of Object.entries(rig.spring || {})) {
      spring.x = key === 'blinkL' ? Math.min(1, params.ParamEyeLOpen) : key === 'blinkR' ? Math.min(1, params.ParamEyeROpen)
        : key === 'lidY' ? pose.gaze.y * 4.2 * 0.72 : 0;
      spring.v = 0;
    }
    if (rig.spring) { rig.hairT = 0; rig.bgLevel = 0.30 + params.ParamCheek * 0.25; }
    rig.draw(params, 0);
    this.params = params; this.currentPose = pose;
    a.eyeFrame = { blink: pose.blink, preview: pose.expression, params: { ...params }, calibration: true };
    return this.snapshot();
  }

  reset() { return this.pose({ expression: null, intensity: 1, blink: 0, gaze: { x: 0, y: 0 }, mouthOpen: 0 }); }

  snapshot() {
    this._assertCurrent();
    const a = this.avatar, rig = a.svg, s = stateOf(rig), native = nativeSnapshot(rig);
    const capabilities = jsonCopy(rig.getEyeCapabilities?.() || { available: false, profile: a.rendererKind, channels: {}, degraded: [] });
    const anchor = headAnchor(rig), fingerprint = captureCalibrationFingerprint(a);
    return { schema: 1, pose: jsonCopy(this.currentPose), params: { ...this.params }, ...native, native: native.nativeMorphs,
      capabilities, model: { renderer: a.rendererKind, meta: jsonCopy(s?.vrm?.meta || {}), generation: rig._generation ?? 0 },
      camera: serializeCalibrationCamera(rig), viewport: viewport(rig), headAnchor: anchor, anchors: { head: anchor },
      components: components(rig, capabilities), fingerprint,
      registration: this.fingerprint ? compareCalibrationFingerprints(this.fingerprint, fingerprint) : { compatible: true, reasons: [] } };
  }

  exit() {
    if (!this.active) return false;
    const a = this.avatar, saved = this.saved, rig = saved.rig;
    this.active = false;
    if (a._raf) globalThis.cancelAnimationFrame?.(a._raf);
    a._raf = 0;
    if (a.store === saved.storeObject) restoreFields(a.store, saved.store);
    for (const [key, driver] of Object.entries(saved.drivers)) if (a[key] === driver.object) restoreFields(a[key], driver.fields);
    Object.assign(a, saved.avatar);
    a.eyeFrame = saved.eyeFrame;
    // The old rig may already have been disposed during an async renderer swap.
    if (a.svg === rig && stateOf(rig) === saved.state && rig._generation === saved.generation) {
      const currentViewport = viewport(rig);
      const viewportChanged = JSON.stringify(saved.viewport) !== JSON.stringify(currentViewport);
      for (const [key, record] of Object.entries(saved.renderer)) {
        if (record.present) rig[key] = record.value; else delete rig[key];
      }
      if (saved.poseTransition) {
        rig.poseTransition = saved.poseTransition.object;
        restoreFields(rig.poseTransition, saved.poseTransition.fields);
      }
      for (const [key, fields] of Object.entries(saved.spring || {})) restoreFields(rig.spring[key], fields);
      if (saved.state) { saved.state.presentation = saved.presentation; saved.state.eyeFocus = saved.eyeFocus; }
      for (const light of saved.lights) if (light.object) {
        light.object.intensity = light.intensity;
        if (light.color) light.object.color.copy(light.color);
        if (light.position) light.object.position.copy(light.position);
      }
      for (const { material, color, shadeColorFactor } of saved.faceMaterials) {
        material.color.copy(color); material.shadeColorFactor.copy(shadeColorFactor);
      }
      for (const { joint, settings } of saved.hairPhysics) {
        joint.settings.stiffness = settings.stiffness;
        joint.settings.gravityPower = settings.gravityPower;
        joint.settings.dragForce = settings.dragForce;
        joint.settings.hitRadius = settings.hitRadius;
      }
      const camera = cameraOf(rig);
      if (camera && saved.camera) {
        for (const key of ['fov', 'aspect', 'near', 'far', 'zoom']) camera[key] = saved.camera[key];
        for (const key of ['position', 'quaternion', 'up']) camera[key].copy(saved.camera[key]);
        camera.updateProjectionMatrix?.(); camera.updateMatrixWorld?.(true);
        // ResizeObserver may already have updated the renderer during the
        // paused session. Restoring its old aspect would undo that resize.
        if (viewportChanged && currentViewport.width > 0 && currentViewport.height > 0) {
          camera.aspect = currentViewport.width / currentViewport.height;
          if (saved.state && typeof rig._frameCamera === 'function') rig._frameCamera(saved.state);
          else if (typeof rig.resize === 'function') rig.resize();
          else camera.updateProjectionMatrix?.();
          const renderer = saved.state?.renderer || rig.renderer;
          renderer?.setPixelRatio?.(Math.max(1, Math.min(2, globalThis.devicePixelRatio || 1)));
          renderer?.setSize?.(currentViewport.width, currentViewport.height, false);
        }
      }
      const manager = managerOf(rig);
      for (const [name, weight] of Object.entries(saved.expressions)) manager?.setValue(name, weight);
      for (const item of saved.transforms) {
        item.node.position.copy(item.position); item.node.quaternion.copy(item.quaternion); item.node.scale.copy(item.scale);
        if (item.morphs) item.node.morphTargetInfluences.splice(0, item.morphs.length, ...item.morphs);
      }
      for (const item of saved.dom) {
        for (const attribute of [...item.node.attributes]) item.node.removeAttribute(attribute.name);
        for (const [name, value] of item.attributes) item.node.setAttribute(name, value);
      }
      const scene = saved.state?.scene || rig.scene, renderer = saved.state?.renderer || rig.renderer;
      scene?.updateMatrixWorld?.(true);
      if (renderer && scene && camera) renderer.render(scene, camera);
    }
    const rendererAlive = a.svg !== rig || stateOf(rig) === saved.state && rig._generation === saved.generation;
    if (saved.running && rendererAlive && typeof a._start === 'function') a._start();
    return true;
  }
}
