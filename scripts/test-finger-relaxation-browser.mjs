// Actual normalized/raw Daily fingers and skinned mesh. Offline production
// renderer/director; no generated images, LLM/TTS/microphone or API mutation.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from '@playwright/test';

const root = 'eval/avatar/finger-relaxation';
if (process.argv.length === 2 && !existsSync(`${root}/before/report.json`)) {
  console.log('SKIP finger-relaxation: 缺少 before 夹具（本地证据未入 git；先跑一次 --before 捕获后恢复）');
  process.exit(0);
}
const before = process.argv.includes('--before'), focus = process.argv.includes('--focus');
const requested = process.argv.find(value => value.startsWith('--label='))?.slice(8);
if (requested) assert(/^[a-z0-9-]+$/.test(requested));
const label = before ? 'before' : requested || 'after', out = `${root}/${label}`;
const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const hash = async file => createHash('sha256').update(await readFile(file)).digest('hex');
const qAngle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, v, i) => sum + v * b[i], 0)) / (Math.hypot(...a) * Math.hypot(...b))));
const distance = (a, b) => Math.hypot(...a.map((v, i) => v - b[i]));
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1320, height: 960 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const errors = [], forbiddenRequests = [], checks = [], captures = [], movies = {}, dynamics = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await mkdir(out, { recursive: true });
  const files = [['vrm.js', 'renderer-source.js'], ['gesture-poses.js', 'gesture-poses.js']];
  if (!before) files.push(['finger-poses.js', 'finger-poses.js']);
  for (const [url, destination] of files) {
    const body = await readFile(before ? `${out}/${destination}` : `web/avatar/render/${url}`, 'utf8');
    if (!before) await writeFile(`${out}/${destination}`, body);
    await page.route(`**/static/avatar/render/${url}`, route => route.fulfill({ contentType: 'text/javascript', body }));
  }
  await page.route('**/api/**', route => {
    if (route.request().method() !== 'GET') { forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`); return route.abort(); }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue() : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    const h = window.fingerHarness = { microphoneRequests: 0 };
    const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
    window.AudioContext = class extends NativeAudioContext { constructor(...args) { super(...args); h.nativeCtx = this; } };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
      h.microphoneRequests++; return Promise.reject(new Error('No microphone in finger QA'));
    };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.evaluate(async () => {
    const THREE = await import('three'), { DEFAULT_NEUTRAL } = await import('/static/avatar/params.js');
    const a = window.__avatar, r = a.svg, s = r._state, h = window.fingerHarness;
    cancelAnimationFrame(a._raf); a._raf = 0; a.performance.stop(); a.lip.stop();
    r.setEyeFocus(false); r.setPresentation(false); r.setSecondaryMotion(0);
    h.neutral = { ...DEFAULT_NEUTRAL }; h.clock = { currentTime: 0 }; h.sequence = 0;
    const digits = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
    const names = ['left', 'right'].flatMap(side => digits.flatMap(digit =>
      (digit === 'Thumb' ? ['Metacarpal', 'Proximal', 'Distal'] : ['Proximal', 'Intermediate', 'Distal']).map(joint => side + digit + joint)));
    const normalized = name => s.vrm.humanoid.getNormalizedBoneNode(name), raw = name => s.vrm.humanoid.getRawBoneNode(name);
    h.bind = Object.fromEntries(names.map(name => [name, { node: normalized(name), raw: raw(name),
      quaternion: s.fingers?.[name]?.rest.clone() ?? normalized(name)?.quaternion.clone() }]).filter(([, b]) => b.node));
    for (const b of Object.values(h.bind)) b.node.quaternion.copy(b.quaternion);
    s.vrm.humanoid.update(); s.scene.updateMatrixWorld(true);
    const world = node => node.getWorldPosition(new THREE.Vector3());
    const handVertices = { left: [], right: [] }, tipVertices = {}, rawGroups = {}, palmBases = {}, closeCenters = {};
    h.handDescendants = {};
    h.meshes = []; s.vrm.scene.traverse(node => { if (node.isMesh) h.meshes.push(node); });
    h.refreshBounds = () => { for (const mesh of h.meshes) if (mesh.isSkinnedMesh) { mesh.computeBoundingSphere(); mesh.computeBoundingBox(); } };
    h.clothMeshes = h.meshes.filter(mesh => /right.*(sleeve|cuff)/i.test(mesh.name.replaceAll('_', ' ')));
    h.initialBounds = new Map(h.clothMeshes.map(mesh => [mesh, { sphere: mesh.boundingSphere?.clone() ?? null, box: mesh.boundingBox?.clone() ?? null }]));
    const vertexWorld = ({ mesh, index }) => {
      const point = new THREE.Vector3().fromBufferAttribute(mesh.geometry.getAttribute('position'), index);
      mesh.applyBoneTransform(index, point); return point.applyMatrix4(mesh.matrixWorld);
    };
    s.scene.updateMatrixWorld(true);
    for (const side of ['left', 'right']) {
      const hand = normalized(side + 'Hand'), inverse = hand.getWorldQuaternion(new THREE.Quaternion()).invert(), wrist = world(hand);
      const points = ['ThumbMetacarpal', 'IndexProximal', 'LittleProximal'].map(suffix => world(normalized(side + suffix)).sub(wrist).applyQuaternion(inverse));
      const normal = points[1].clone().sub(points[0]).cross(points[2].clone().sub(points[0])).normalize();
      // Orient the geometric plane toward the audited palmar flexion side:
      // regular normalized fingers curl toward local -Y on both hands.
      if (normal.y > 0) normal.negate();
      palmBases[side] = normal;
      closeCenters[side] = world(normalized(side + 'MiddleDistal')).sub(wrist).applyQuaternion(inverse).multiplyScalar(.5);
      const descendants = new Set(); raw(side + 'Hand')?.traverse(node => descendants.add(node));
      h.handDescendants[side] = descendants;
      for (const digit of digits) {
        const prefix = side + digit;
        rawGroups[prefix] = new Set(); raw(prefix + (digit === 'Thumb' ? 'Metacarpal' : 'Proximal'))?.traverse(node => rawGroups[prefix].add(node));
        tipVertices[prefix] = [];
      }
      s.vrm.scene.traverse(mesh => {
        if (!mesh.isSkinnedMesh) return;
        const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight'), positions = mesh.geometry.getAttribute('position');
        if (!indices || !weights || !positions) return;
        const accessor = ['getX', 'getY', 'getZ', 'getW'];
        for (let index = 0; index < positions.count; index++) {
          const influence = nodes => accessor.reduce((sum, get) => sum + (nodes.has(mesh.skeleton.bones[indices[get](index)]) ? weights[get](index) : 0), 0);
          if (influence(descendants) >= .5) handVertices[side].push({ mesh, index });
          for (const digit of digits) {
            const prefix = side + digit, distal = raw(prefix + 'Distal');
            const weight = accessor.reduce((sum, get) => sum + (mesh.skeleton.bones[indices[get](index)] === distal ? weights[get](index) : 0), 0);
            if (weight >= .35) tipVertices[prefix].push({ mesh, index });
          }
        }
      });
      for (const digit of digits) {
        const prefix = side + digit;
        const distal = world(normalized(prefix + 'Distal'));
        const previous = world(normalized(prefix + (digit === 'Thumb' ? 'Proximal' : 'Intermediate')));
        const direction = distal.clone().sub(previous).normalize();
        const scored = tipVertices[prefix].map(v => ({ ...v, score: vertexWorld(v).sub(distal).dot(direction) })).sort((x, y) => y.score - x.score);
        tipVertices[prefix] = scored.slice(0, Math.max(3, Math.ceil(scored.length * .12)));
      }
    }
    h.rig = { bones: Object.fromEntries(Object.entries(h.bind).map(([name, b]) => [name, {
      normalizedName: b.node.name, rawName: b.raw?.name, bindQuaternion: b.quaternion.toArray(), rawBindQuaternion: b.raw?.quaternion.toArray(),
      normalizedChildDirection: b.node.children[0] ? b.node.children[0].position.clone().normalize().toArray() : null,
    }])), handVertexCounts: Object.fromEntries(Object.entries(handVertices).map(([side, v]) => [side, v.length])),
      tipVertexCounts: Object.fromEntries(Object.entries(tipVertices).map(([digit, v]) => [digit, v.length])),
      palmLocalPlaneNormals: Object.fromEntries(Object.entries(palmBases).map(([side, v]) => [side, v.toArray()])),
      cloth: h.clothMeshes.map(mesh => {
        const indices = mesh.geometry.getAttribute('skinIndex'), weights = mesh.geometry.getAttribute('skinWeight'), influences = {};
        for (let i = 0; i < indices.count; i++) for (const get of ['getX', 'getY', 'getZ', 'getW']) {
          const weight = weights[get](i); if (!weight) continue;
          const name = mesh.skeleton.bones[indices[get](i)]?.name ?? 'missing'; influences[name] = (influences[name] || 0) + weight;
        }
        return { name: mesh.name, vertexCount: indices.count, aggregateSkinWeights: influences };
      }) };
    h.camera = { position: s.camera.position.clone(), quaternion: s.camera.quaternion.clone(), fov: s.camera.fov, near: s.camera.near, far: s.camera.far };
    h.restoreCamera = () => { s.camera.position.copy(h.camera.position); s.camera.quaternion.copy(h.camera.quaternion); s.camera.fov = h.camera.fov; s.camera.near = h.camera.near; s.camera.far = h.camera.far; s.camera.updateProjectionMatrix(); };
    h.measure = (frame, mesh = false) => {
      s.scene.updateMatrixWorld(true);
      const hands = {}, fingers = {};
      for (const side of ['left', 'right']) {
        const hand = normalized(side + 'Hand'), origin = world(hand), inverse = hand.getWorldQuaternion(new THREE.Quaternion()).invert();
        hands[side] = { world: origin.toArray(), quaternion: hand.quaternion.toArray(), worldQuaternion: hand.getWorldQuaternion(new THREE.Quaternion()).toArray(),
          rawQuaternion: raw(side + 'Hand')?.quaternion.toArray(), rawWorldQuaternion: raw(side + 'Hand')?.getWorldQuaternion(new THREE.Quaternion()).toArray() };
        for (const digit of digits) {
          const prefix = side + digit;
          const vertices = tipVertices[prefix];
          const tip = new THREE.Vector3(); for (const value of vertices) tip.add(vertexWorld(value)); if (vertices.length) tip.divideScalar(vertices.length);
          const tipLocal = tip.clone().sub(origin).applyQuaternion(inverse);
          fingers[prefix] = { tipWorld: tip.toArray(), tipHandLocal: tipLocal.toArray(), tipPlaneProjection: tipLocal.dot(palmBases[side]),
            tipVertexCount: vertices.length, joints: {} };
          for (const joint of digit === 'Thumb' ? ['Metacarpal', 'Proximal', 'Distal'] : ['Proximal', 'Intermediate', 'Distal']) {
            const name = prefix + joint, b = h.bind[name];
            if (!b) continue;
            fingers[prefix].joints[joint] = { normalized: b.node.quaternion.toArray(), raw: b.raw?.quaternion.toArray(),
              world: world(b.node).toArray(), rawWorld: b.raw ? world(b.raw).toArray() : null,
              restAngle: b.node.quaternion.angleTo(b.quaternion) };
          }
        }
        if (mesh) {
          const bounds = new THREE.Box3(), clip = { vertexCount: handVertices[side].length, outsideCount: 0, finite: true };
          for (const value of handVertices[side]) {
            const point = vertexWorld(value); bounds.expandByPoint(point);
            const ndc = point.clone().project(s.camera); clip.finite &&= ndc.toArray().every(Number.isFinite);
            if (Math.abs(ndc.x) > 1 || Math.abs(ndc.y) > 1 || ndc.z < -1 || ndc.z > 1) clip.outsideCount++;
          }
          hands[side].mesh = { ...clip, worldMin: bounds.min.toArray(), worldMax: bounds.max.toArray() };
        }
      }
      const armQuaternions = Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.toArray()]));
      const armRestAngles = Object.fromEntries(Object.entries(s.arms).map(([name, b]) => [name, b.node.quaternion.angleTo(b.rest)]));
      const rawArms = Object.fromEntries(Object.keys(s.arms).map(name => [name, { name: raw(name)?.name, quaternion: raw(name)?.quaternion.toArray(),
        worldQuaternion: raw(name)?.getWorldQuaternion(new THREE.Quaternion()).toArray(), worldPosition: raw(name) ? world(raw(name)).toArray() : null }]));
      const frustum = new THREE.Frustum().setFromProjectionMatrix(new THREE.Matrix4().multiplyMatrices(s.camera.projectionMatrix, s.camera.matrixWorldInverse));
      const cloth = mesh ? h.clothMeshes.map(value => ({ name: value.name, visible: value.visible, frustumCulled: value.frustumCulled,
        intersectsFrustum: frustum.intersectsObject(value), skinned: value.isSkinnedMesh,
        position: value.position.toArray(), quaternion: value.quaternion.toArray(), matrixWorld: value.matrixWorld.toArray(),
        boundingSphere: value.boundingSphere ? { center: value.boundingSphere.center.toArray(), radius: value.boundingSphere.radius } : null,
        materials: (Array.isArray(value.material) ? value.material : [value.material]).map(material => ({ name: material.name, visible: material.visible, opacity: material.opacity, transparent: material.transparent, side: material.side })),
        weightedBoneTransforms: Object.keys(h.rig.cloth.find(c => c.name === value.name).aggregateSkinWeights).map(name => {
          const bone = value.skeleton.bones.find(b => b.name === name);
          return { name, matrixWorld: bone.matrixWorld.toArray(), quaternion: bone.quaternion.toArray(), worldQuaternion: bone.getWorldQuaternion(new THREE.Quaternion()).toArray() };
        }),
        vertices: Array.from({ length: value.geometry.getAttribute('position').count }, (_, index) => vertexWorld({ mesh: value, index }).toArray()) })) : null;
      return { at: h.clock.currentTime, frame, hands, fingers, armQuaternions, rawArms, armRestAngles, cloth, capability: r.getFingerRelaxation?.() ?? null,
        camera: { position: s.camera.position.toArray(), quaternion: s.camera.quaternion.toArray(), fov: s.camera.fov, near: s.camera.near, far: s.camera.far },
        finite: [...Object.values(armQuaternions).flat(), ...Object.values(fingers).flatMap(v => [...v.tipWorld, ...v.tipHandLocal,
          ...Object.values(v.joints).flatMap(j => [...j.normalized, ...(j.raw || []), ...j.world, ...(j.rawWorld || [])])])].every(Number.isFinite) };
    };
    h.reset = (enabled = true) => {
      a.performance.stop(); a.performance.sample(); h.clock.currentTime = 0;
      for (const b of Object.values(s.arms)) b.node.quaternion.copy(b.rest);
      for (const b of Object.values(h.bind)) b.node.quaternion.copy(b.quaternion);
      for (const b of s.springBones) b.node.quaternion.copy(b.rest);
      r.poseTransition.reset(); r.setFingerRelaxation?.(enabled); h.restoreCamera();
    };
    h.closeCamera = (edge = false, dynamic = false, side = 'right') => {
      const hand = normalized(side + 'Hand'), q = hand.getWorldQuaternion(new THREE.Quaternion());
      const center = world(hand).add(closeCenters[side].clone().applyQuaternion(q));
      const normal = palmBases[side].clone().applyQuaternion(q);
      // Oblique palmar view makes the curl visible, matched by wrist pose in
      // before/after captures. No finger-dependent camera fit or auto orbit.
      const forward = world(normalized(side + 'MiddleProximal')).sub(world(hand)).normalize();
      const width = world(normalized(side + 'LittleProximal')).sub(world(normalized(side + 'IndexProximal'))).normalize();
      const name = r.performance?.name;
      const outside = dynamic || !name || name === 'self' || r.reduceGestures || (r.performance?.strength ?? 1) < .6;
      if (outside) {
        // Hanging/self-reference palms face the torso. Use an outside dorsal
        // view: a camera on their palmar normal would sit inside the clothing.
        const sign = side === 'right' ? -1 : 1;
        s.camera.position.copy(center).add(new THREE.Vector3(sign * (edge ? .42 : .30), .06, edge ? .18 : .36));
      } else {
        s.camera.position.copy(center).add(normal.multiplyScalar(edge ? .10 : .42)).add(forward.multiplyScalar(.08));
        if (edge) s.camera.position.add(width.multiplyScalar(.40));
      }
      s.camera.fov = 32; s.camera.near = .001; s.camera.far = 100;
      s.camera.up.set(0, 1, 0); s.camera.lookAt(center); s.camera.updateProjectionMatrix();
    };
    h.image = () => {
      const canvas = document.createElement('canvas'); canvas.width = 420; canvas.height = 360;
      const c = canvas.getContext('2d'); c.fillStyle = '#e7eaf0'; c.fillRect(0, 0, 420, 360); c.drawImage(s.renderer.domElement, 0, 0, 420, 360);
      return canvas.toDataURL('image/jpeg', .88);
    };
    h.visibility = (sample, side = 'right') => {
      h.refreshBounds();
      const result = { method: 'nearest actual mesh ray hit toward each distal tip centroid; triangle aggregate weight on raw right-hand descendants >=.35', fingers: {}, visibleCount: 0 };
      for (const digit of ['Thumb', 'Index', 'Middle', 'Ring', 'Little']) {
        const point = new THREE.Vector3(...sample.fingers[side + digit].tipWorld);
        const ray = new THREE.Raycaster(s.camera.position, point.clone().sub(s.camera.position).normalize());
        const hit = ray.intersectObjects(h.meshes, false)[0]; let handWeight = 0;
        if (hit?.object.isSkinnedMesh && hit.face) {
          const indices = hit.object.geometry.getAttribute('skinIndex'), weights = hit.object.geometry.getAttribute('skinWeight');
          if (indices && weights) for (const index of [hit.face.a, hit.face.b, hit.face.c]) for (const get of ['getX', 'getY', 'getZ', 'getW']) {
            if (h.handDescendants[side].has(hit.object.skeleton.bones[indices[get](index)])) handWeight += weights[get](index) / 3;
          }
        }
        const visible = handWeight >= .35;
        result.fingers[digit] = { visible, nearestMesh: hit?.object.name ?? null, handWeight, hitDistance: hit?.distance ?? null };
        if (visible) result.visibleCount++;
      }
      return result;
    };
    h.fixed = ({ name = null, strength = 1, reduced = false, enabled = true }) => {
      h.reset(enabled);
      const f = { name, strength, reduced, phase: .5, weight: name ? 1 : 0, transition: { id: ++h.sequence, reason: 'start', from: 'rest', to: name || 'rest' } };
      for (let i = 0; i < 75; i++) { r.setPerformance(f, reduced); r.draw(h.neutral, 1/60); }
      h.refreshBounds(); r.draw(h.neutral, 0);
      const full = h.measure(f, true); const fullImage = h.image();
      h.closeCamera(); h.refreshBounds(); r.draw(h.neutral, 0);
      const close = h.measure(f, true), closeImage = h.image(); close.visibility = h.visibility(close);
      h.closeCamera(true); h.refreshBounds(); r.draw(h.neutral, 0); const edge = h.measure(f, true), edgeImage = h.image(); edge.visibility = h.visibility(edge);
      return { input: { name, strength, reduced, enabled }, full, close, edge, fullImage, closeImage, edgeImage };
    };
    h.dynamic = ({ name, enabled = true }) => {
      h.reset(enabled);
      const relaxed = { name: null, strength: 1, phase: 0, weight: 0,
        transition: { id: ++h.sequence, reason: 'start', from: 'rest', to: 'rest' } };
      for (let i = 0; i < 75; i++) { r.setPerformance(relaxed, false); r.draw(h.neutral, 1/60); }
      r.poseTransition.reset(); a.performance.preview(name, h.clock);
      const frames = [], images = [];
      for (let i = 0; i <= 168; i++) {
        h.clock.currentTime = i/60; h.restoreCamera();
        const f = a.performance.sample(); r.setPerformance(f, false); r.draw(h.neutral, i ? 1/60 : 0);
        frames.push(h.measure(f, [0, 12, 72, 132, 168].includes(i)));
        if (i % 3 === 0) { h.closeCamera(false, true); h.refreshBounds(); r.draw(h.neutral, 0); images.push(h.image()); }
      }
      h.restoreCamera(); return { name, enabled, frames, images };
    };
    h.toggle = () => {
      h.fixed({ name: 'palm' }); const before = h.measure(r.performance);
      r.setFingerRelaxation(false); r.draw(h.neutral, 0); const disabled = h.measure(r.performance);
      r.setFingerRelaxation(true); r.draw(h.neutral, 0); const enabled = h.measure(r.performance);
      return { before, disabled, enabled };
    };
    h.cuffAudit = () => {
      h.fixed({ name: 'self', enabled: false }); h.closeCamera(true);
      for (const mesh of h.clothMeshes) { const bound = h.initialBounds.get(mesh); mesh.boundingSphere = bound.sphere?.clone() ?? null; mesh.boundingBox = bound.box?.clone() ?? null; }
      r.draw(h.neutral, 0); const stale = h.measure(r.performance, true), staleImage = h.image();
      for (const mesh of h.clothMeshes) mesh.frustumCulled = false;
      r.draw(h.neutral, 0); const uncull = h.measure(r.performance, true), uncullImage = h.image();
      for (const mesh of h.clothMeshes) { mesh.frustumCulled = true; mesh.computeBoundingSphere(); mesh.computeBoundingBox(); }
      r.draw(h.neutral, 0); const refreshed = h.measure(r.performance, true), refreshedImage = h.image();
      return { stale, uncull, refreshed, staleImage, uncullImage, refreshedImage };
    };
    h.leftRest = enabled => {
      h.fixed({ name: null, enabled }); h.closeCamera(false, false, 'left'); h.refreshBounds(); r.draw(h.neutral, 0);
      const close = h.measure(r.performance, true), closeImage = h.image(); close.visibility = h.visibility(close, 'left');
      h.closeCamera(true, false, 'left'); h.refreshBounds(); r.draw(h.neutral, 0);
      const edge = h.measure(r.performance, true), edgeImage = h.image(); edge.visibility = h.visibility(edge, 'left');
      return { enabled, close, edge, closeImage, edgeImage };
    };
  });
  const cuffAudit = await page.evaluate(() => window.fingerHarness.cuffAudit());
  for (const key of ['stale', 'uncull', 'refreshed']) await writeFile(`${out}/cuff-${key}.jpg`, Buffer.from(cuffAudit[key + 'Image'].split(',')[1], 'base64'));
  delete cuffAudit.staleImage; delete cuffAudit.uncullImage; delete cuffAudit.refreshedImage;
  await writeFile(`${out}/cuff-audit.json`, JSON.stringify(cuffAudit, null, 2));
  const configurations = [{ name: null }];
  for (const name of ['wave', 'deny', 'self', 'palm']) configurations.push({ name }, { name, strength: .4 }, { name, reduced: true });
  if (focus) configurations.splice(0, configurations.length, { name: null }, { name: 'self' }, { name: 'palm' }, { name: 'wave' });
  for (const config of configurations) {
    const value = await page.evaluate(config => window.fingerHarness.fixed(config), config);
    const stem = `${config.name || 'rest'}-strength${value.input.strength}${value.input.reduced ? '-reduced' : ''}`;
    await writeFile(`${out}/${stem}-full.jpg`, Buffer.from(value.fullImage.split(',')[1], 'base64'));
    await writeFile(`${out}/${stem}-hand.jpg`, Buffer.from(value.closeImage.split(',')[1], 'base64'));
    await writeFile(`${out}/${stem}-edge.jpg`, Buffer.from(value.edgeImage.split(',')[1], 'base64'));
    await writeFile(`${out}/${stem}-sample.json`, JSON.stringify({ input: value.input, full: value.full, close: value.close, edge: value.edge }, null, 2));
    assert(value.full.finite && value.close.finite, 'all normalized/raw joints, actual tips and arm transforms finite');
    assert.equal(value.close.hands.right.mesh.outsideCount, 0, `${config.name}: entire hand/finger influenced mesh fits closeup`);
    assert(value.close.visibility.visibleCount >= 3 && value.edge.visibility.visibleCount >= 3,
      `${config.name}: actual nearest mesh rays reach visible hand surface rather than torso/clothing`);
    assert(Object.values(value.close.fingers).every(v => v.tipVertexCount > 0), 'actual distal-influenced tip vertices present for all fingers');
    delete value.fullImage; delete value.closeImage; delete value.edgeImage;
    captures.push({ stem, ...value }); console.log(`${label}: ${stem} captured actual full body + hand closeup`);
  }
  for (const name of ['self', 'palm']) {
    const dynamic = await page.evaluate(name => window.fingerHarness.dynamic({ name }), name);
    assert(dynamic.frames.every(frame => frame.finite)); movies[name] = dynamic.images; delete dynamic.images; dynamics.push(dynamic);
  }
  await writeFile(`${out}/captures.json`, JSON.stringify({ label, captures, dynamics }, null, 2));
  await writeFile(`${out}/movies.json`, JSON.stringify(movies));
  checks.push(`${captures.length} fixed poses and two real director/renderer dynamic sequences with normalized/raw joints, influenced mesh tips, closeups and finite transforms`);
  let comparisons, disabled, toggle, graceful, nativeLab, leftRest;
  if (!before) {
    const baseline = JSON.parse(await readFile(`${root}/before/report.json`, 'utf8'));
    assert.equal(await hash('web/models/seed-daily.vrm'), baseline.assetHash);
    comparisons = [];
    for (const capture of captures) {
      const old = baseline.captures.find(c => c.stem === capture.stem); assert(old);
      for (const name of Object.keys(old.full.armQuaternions)) assert(qAngle(old.full.armQuaternions[name], capture.full.armQuaternions[name]) < 1e-6, 'static arm quaternion unchanged');
      for (const name of Object.keys(old.full.rawArms)) {
        assert(qAngle(old.full.rawArms[name].quaternion, capture.full.rawArms[name].quaternion) < 1e-6, 'raw arm/wrist quaternion unchanged');
        assert(distance(old.full.rawArms[name].worldPosition, capture.full.rawArms[name].worldPosition) < 1e-6, 'raw arm/wrist position unchanged');
      }
      for (const view of ['close', 'edge']) {
        assert(distance(old[view].camera.position, capture[view].camera.position) < 1e-6, 'matched hand camera position');
        assert(qAngle(old[view].camera.quaternion, capture[view].camera.quaternion) < 1e-6, 'matched hand camera direction');
      }
      const clothDifferences = [];
      for (const piece of capture.full.cloth) {
        const original = old.full.cloth.find(c => c.name === piece.name); assert(original);
        const largestDifference = Math.max(...piece.vertices.map((vertex, i) => distance(vertex, original.vertices[i])));
        assert(largestDifference < 1e-6, 'actual sleeve/cuff vertices unchanged');
        for (const bone of piece.weightedBoneTransforms) {
          const rawBone = original.weightedBoneTransforms.find(b => b.name === bone.name); assert(rawBone);
          assert(bone.matrixWorld.every((value, i) => Math.abs(value - rawBone.matrixWorld[i]) < 1e-6), 'weighted raw sleeve/cuff bones unchanged');
        }
        clothDifferences.push({ name: piece.name, largestVertexDifference: largestDifference });
      }
      const curls = {};
      for (const side of ['left', 'right']) for (const digit of ['Index', 'Middle', 'Ring', 'Little']) {
        const name = side + digit, delta = capture.full.fingers[name].tipPlaneProjection - old.full.fingers[name].tipPlaneProjection;
        curls[name] = delta;
        assert(delta > .00005, `${capture.stem}/${name}: actual skinned fingertip curls inward along palmar plane normal (${delta})`);
      }
      for (const side of ['left', 'right']) for (const joint of ['Metacarpal', 'Proximal', 'Distal']) {
        const oldJoint = old.full.fingers[side + 'Thumb'].joints[joint], newJoint = capture.full.fingers[side + 'Thumb'].joints[joint];
        assert(qAngle(oldJoint.normalized, newJoint.normalized) < 1e-6, 'normalized thumb unchanged');
        assert(qAngle(oldJoint.raw, newJoint.raw) < 1e-6, 'raw thumb unchanged');
      }
      comparisons.push({ stem: capture.stem, inwardTipPlaneDeltas: curls, clothDifferences });
    }
    for (const dynamic of dynamics) {
      const old = baseline.dynamics.find(d => d.name === dynamic.name);
      for (let i = 0; i < dynamic.frames.length; i++) for (const name of Object.keys(old.frames[i].armQuaternions))
        assert(qAngle(dynamic.frames[i].armQuaternions[name], old.frames[i].armQuaternions[name]) < 1e-6, `${dynamic.name}/${i}/${name}: arm sequence unchanged`);
      for (let i = 0; i < dynamic.frames.length; i++) for (const name of Object.keys(old.frames[i].rawArms))
        assert(qAngle(dynamic.frames[i].rawArms[name].quaternion, old.frames[i].rawArms[name].quaternion) < 1e-6, `${dynamic.name}/${i}/${name}: raw arm sequence unchanged`);
    }
    const rest = captures.find(c => !c.input.name);
    for (const side of ['left', 'right']) {
      const angles = ['Index', 'Middle', 'Ring', 'Little'].map(digit => Object.values(rest.full.fingers[side + digit].joints).reduce((sum, joint) => sum + joint.restAngle, 0));
      assert(angles.every((angle, i) => i === 0 || angle > angles[i-1]), 'ring/little resting curl gently exceeds index/middle');
    }
    checks.push('actual fingertip geometry bends inward; ring/little curl more than index; thumbs/model hash/static and dynamic normalized/raw arms unchanged; close cameras, cuff/sleeve raw bones and actual vertices match');
    leftRest = [];
    for (const enabled of [false, true]) {
      const sample = await page.evaluate(enabled => window.fingerHarness.leftRest(enabled), enabled);
      assert.equal(sample.close.hands.left.mesh.outsideCount, 0, 'left hand actual influenced mesh fits matched outside camera');
      assert(sample.close.visibility.visibleCount >= 3 && sample.edge.visibility.visibleCount >= 3, 'left hand is visible outside trousers');
      const stem = enabled ? 'left-rest-relaxed' : 'left-rest-original';
      await writeFile(`${out}/${stem}-hand.jpg`, Buffer.from(sample.closeImage.split(',')[1], 'base64'));
      await writeFile(`${out}/${stem}-edge.jpg`, Buffer.from(sample.edgeImage.split(',')[1], 'base64'));
      delete sample.closeImage; delete sample.edgeImage; leftRest.push(sample);
    }
    await writeFile(`${out}/left-rest.json`, JSON.stringify(leftRest, null, 2));
    checks.push('left resting hand original/relaxed closeups use matched mirrored outside cameras and visible actual skin surface');
    if (!focus) {
      disabled = [];
      for (const name of [null, 'wave', 'deny', 'self', 'palm']) {
        const sample = await page.evaluate(name => window.fingerHarness.fixed({ name, enabled: false }), name);
        const old = baseline.captures.find(c => c.input.name === name && c.input.strength === 1 && !c.input.reduced);
        for (const prefix of Object.keys(sample.full.fingers)) for (const joint of Object.keys(sample.full.fingers[prefix].joints)) {
          assert(qAngle(sample.full.fingers[prefix].joints[joint].normalized, old.full.fingers[prefix].joints[joint].normalized) < 1e-6);
          assert(distance(sample.full.fingers[prefix].tipWorld, old.full.fingers[prefix].tipWorld) < 1e-6, 'disabling restores actual bound mesh tips');
        }
        disabled.push({ name, full: sample.full });
      }
      toggle = await page.evaluate(() => window.fingerHarness.toggle());
      for (const prefix of Object.keys(toggle.before.fingers)) for (const joint of Object.keys(toggle.before.fingers[prefix].joints)) {
        assert.deepEqual(toggle.disabled.fingers[prefix].joints[joint].normalized, toggle.before.fingers[prefix].joints[joint].normalized);
        assert.deepEqual(toggle.enabled.fingers[prefix].joints[joint].normalized, toggle.disabled.fingers[prefix].joints[joint].normalized);
      }
      graceful = await page.evaluate(() => {
        const a = window.__avatar, h = window.fingerHarness, r = a.svg, s = r._state;
        const originalName = s.vrm.meta.name, fingers = s.fingers;
        s.vrm.meta.name = 'Unadapted model'; const genericBefore = h.measure(r.performance);
        r._applyFingers(s, r.performance, 1/60); const genericAfter = h.measure(r.performance), genericCapability = r.getFingerRelaxation();
        s.vrm.meta.name = originalName; s.fingers = {};
        r._applyFingers(s, r.performance, 1/60); const missingCapability = r.getFingerRelaxation(); s.fingers = fingers;
        return { genericBefore, genericAfter, genericCapability, missingCapability };
      });
      assert.equal(graceful.genericCapability.available, false); assert.equal(graceful.missingCapability.available, false);
      assert.deepEqual(graceful.genericAfter.fingers, graceful.genericBefore.fingers);
      checks.push('disabled fingers restore bound mesh/joints; dt0 mode toggle preserves actual pose; generic and missing-finger states safely decline adaptation');
      await page.evaluate(() => { const a = window.__avatar; a.onBargeIn(); a.svg.setFingerRelaxation(true); if (!a._raf) a._start(); });
      assert(await page.locator('#fingerRelaxation').isChecked()); assert.equal(await page.locator('#fingerRelaxation').isDisabled(), false);
      await page.locator('#gestureWave').click();
      await page.waitForFunction(() => { const f = window.__avatar.performance.sample(); return f.name === 'wave' && f.weight > .99; });
      const interruption = await page.evaluate(async () => {
        const a = window.__avatar, h = window.fingerHarness; await h.nativeCtx.suspend();
        const before = h.measure(a.performance.sample()), at = h.nativeCtx.currentTime;
        a.onBargeIn(); a.svg.setPerformance(a.performance.sample(), false); a.svg.draw(h.neutral, 0);
        return { before, atZero: h.measure(a.performance.sample()), at, queueLength: a.performance.queue.length };
      });
      assert.equal(interruption.atZero.frame.name, null); assert.equal(interruption.queueLength, 0);
      for (const prefix of Object.keys(interruption.before.fingers)) for (const joint of Object.keys(interruption.before.fingers[prefix].joints))
        assert.deepEqual(interruption.atZero.fingers[prefix].joints[joint].normalized, interruption.before.fingers[prefix].joints[joint].normalized);
      await page.waitForFunction(expected => {
        const h = window.fingerHarness, sample = h.measure(window.__avatar.performance.sample());
        const angle = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a.reduce((sum, v, i) => sum + v*b[i], 0))));
        return Object.values(sample.armRestAngles).every(v => v < .002) && Object.keys(sample.fingers).every(prefix =>
          Object.keys(sample.fingers[prefix].joints).every(joint => angle(sample.fingers[prefix].joints[joint].normalized, expected.fingers[prefix].joints[joint].normalized) < .002));
      }, rest.full);
      const recovery = await page.evaluate(() => ({ ...window.fingerHarness.measure(window.__avatar.performance.sample()), audioAt: window.fingerHarness.nativeCtx.currentTime }));
      assert.equal(recovery.audioAt, interruption.at);
      await page.locator('#fingerRelaxation').uncheck(); assert.equal(await page.evaluate(() => window.__avatar.svg.getFingerRelaxation().enabled), false);
      await page.locator('#fingerRelaxation').check(); assert.equal(await page.evaluate(() => window.__avatar.svg.getFingerRelaxation().enabled), true);
      await page.locator('#fingerRelaxationStatus').screenshot({ path: `${out}/lab-status.png` });
      nativeLab = { interruption, recovery };
      checks.push('actual lab native-clock interruption/toggle keeps dt0 fingers and returns to relaxed rest while audio is suspended');
    }
  }
  assert.deepEqual(errors, []); assert.deepEqual(forbiddenRequests, []); assert.equal(await page.evaluate(() => window.fingerHarness.microphoneRequests), 0);
  checks.push('no browser errors, microphone, speech/model calls or API mutation');
  const report = { label, focus, createdAt: new Date().toISOString(), assetHash: await hash('web/models/seed-daily.vrm'),
    rendererHash: await hash(`${out}/renderer-source.js`), gestureHash: await hash(`${out}/gesture-poses.js`),
    fingerHash: before ? null : await hash(`${out}/finger-poses.js`), rig: await page.evaluate(() => window.fingerHarness.rig),
    captures, dynamics, comparisons, disabled, toggle, graceful, nativeLab, leftRest, cuffAudit, checks, errors, forbiddenRequests,
    method: 'actual normalized and raw finger joints; tip centroid from furthest 12% of >=.35 distal-influenced mesh vertices at bind; actual skin transforms; no altered model; production director audio clock and 60Hz renderer; strong wave/deny/palm palmar closeup; rest/self/reduced/soft outside dorsal closeup; dynamic outside view follows wrist pose only; posed skinned bounding spheres/boxes consistently refreshed before every evidence screenshot for closeup QA only',
    limits: 'Checks establish inward geometry, preserved arm sequences and continuity, not skin/contact collision or human anatomical correctness. Thumb remains authored bind. Generic/missing tests decline adaptation on controlled state, not a second model.' };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(`Finger relaxation ${label}${focus ? ' focused' : ''}: ${checks.length} groups passed; ${out}/report.json`);
} finally { await browser.close(); }
