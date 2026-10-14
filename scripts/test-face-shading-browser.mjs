// Fixed production VRM face lighting evidence. No microphone, live speech, or API writes.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { chromium } from '@playwright/test';

const base = 'eval/avatar/face-shading';
const label = process.argv.find(x => x.startsWith('--label='))?.slice(8) || 'before';
assert(/^[a-z0-9-]+$/.test(label));
const frozen = process.argv.includes('--frozen') || label === 'before';
const probe = process.argv.includes('--probe');
const dynamic = process.argv.includes('--dynamic');
const verify = process.argv.includes('--verify');
const reuse = process.argv.includes('--reuse');
const out = `${base}/${label}`;
if (process.argv.length === 2 && !existsSync(`${out}/vrm.js`)) {
  console.log(`SKIP face-shading: 缺少 ${label} 夹具（本地证据未入 git；用 --label=<name> 捕获后恢复）`);
  process.exit(0);
}
const settingsFile = process.argv.find(x => x.startsWith('--settings='))?.slice(11);
const settings = settingsFile ? JSON.parse(await readFile(settingsFile, 'utf8')) : null;
const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
// Persisted JSON has no signed zero. Canonicalize live structured-clone numbers
// to that same representation before comparing saved and live fingerprints.
const same = (actual, expected, message) => assert.deepEqual(JSON.parse(JSON.stringify(actual)), JSON.parse(JSON.stringify(expected)), message);
let maximumRestoredNumericDelta = 0;
function nearly(actual, expected, path = 'restored') {
  if(typeof actual==='number' && typeof expected==='number') {
    const delta=Math.abs(actual-expected); maximumRestoredNumericDelta=Math.max(maximumRestoredNumericDelta,delta);
    assert(Number.isFinite(delta)&&delta<1e-10,`${path}: numeric delta ${delta}`);return;
  }
  if(actual && expected && typeof actual==='object' && typeof expected==='object') {
    same(Object.keys(actual),Object.keys(expected),`${path}: keys`);
    for(const key of Object.keys(actual))nearly(actual[key],expected[key],`${path}.${key}`);return;
  }
  assert.equal(actual,expected,path);
}
const sources = { 'vrm.js': 'web/avatar/render/vrm.js', 'eye-morph.js': 'web/avatar/render/eye-morph.js',
  'calibration.js': 'web/avatar/calibration.js', 'avatar_lab.js': 'web/avatar_lab.js', 'avatar_lab.html': 'web/avatar_lab.html' };
const browser = await chromium.launch({ headless: true, args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
const page = await browser.newPage({ viewport: { width: 1320, height: 960 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(20000);
const errors = [], forbiddenRequests = [], captures = [], sourceHashes = {}, dynamics = [], checks = [];
page.on('pageerror', error => errors.push(error.message));
try {
  await mkdir(out, { recursive: true });
  for (const [name, file] of Object.entries(sources)) {
    const bytes = await readFile(frozen ? `${base}/before/${name}` : file);
    if (!frozen) await writeFile(`${out}/${name}`, bytes);
    sourceHashes[file] = hash(bytes);
    const staticPath = file.replace(/^web\//, '/static/');
    await page.route(`**${staticPath}`, route => route.fulfill({ body: bytes, contentType: name.endsWith('.html') ? 'text/html' : 'text/javascript' }));
  }
  await page.route('**/api/**', route => {
    if (route.request().method() !== 'GET') { forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`); return route.abort(); }
    return new URL(route.request().url()).pathname === '/api/avatar' ? route.continue() : route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    const h = window.faceHarness = { microphoneRequests: 0 };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => { h.microphoneRequests++; return Promise.reject(new Error('No microphone in face lighting QA')); };
  });
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  const setup = await page.evaluate(async settings => {
    const THREE = await import('three');
    const { AvatarCalibration, captureCalibrationFingerprint } = await import('/static/avatar/calibration.js');
    const a = window.__avatar, r = a.svg, s = r._state, h = window.faceHarness;
    if (a._raf) cancelAnimationFrame(a._raf); a._raf = 0; a.performance.stop(); a.lip.stop();
    s.observer?.disconnect(); r.setPresentation(true); r.setEyeFocus(true); r.setSecondaryMotion(0);
    s.renderer.setPixelRatio(1); s.renderer.setSize(560, 680, false); s.camera.aspect = 560/680; r._frameCamera(s);
    h.center = s.eyeFraming.center.clone();
    s.camera.position.sub(h.center).multiplyScalar(1.12).add(h.center); s.camera.lookAt(h.center); s.camera.updateProjectionMatrix();
    h.camera = { position: s.camera.position.clone(), quaternion: s.camera.quaternion.clone(), fov: s.camera.fov, aspect: s.camera.aspect, near: s.camera.near, far: s.camera.far };
    h.lights = Object.fromEntries(['ambient', 'key', 'fill'].map(key => [key, { intensity: s[key].intensity, color: s[key].color.clone(), position: s[key].position.clone() }]));
    h.materials = new Map(); h.meshes = [];
    s.vrm.scene.traverse(mesh => {
      if (mesh.isMesh) h.meshes.push(mesh);
      for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) if (material && !h.materials.has(material)) h.materials.set(material, {
        color: material.color?.clone(),
        shadeColorFactor: material.shadeColorFactor?.clone(), shadingShiftFactor: material.shadingShiftFactor, shadingToonyFactor: material.shadingToonyFactor,
        outlineWidthFactor: material.outlineWidthFactor, outlineColorFactor: material.outlineColorFactor?.clone(), rimLightingMixFactor: material.rimLightingMixFactor,
        parametricRimColorFactor: material.parametricRimColorFactor?.clone(), parametricRimFresnelPowerFactor: material.parametricRimFresnelPowerFactor,
        parametricRimLiftFactor: material.parametricRimLiftFactor,
      });
    });
    const arr = value => value?.toArray ? value.toArray() : value ?? null;
    const bytesHash = async array => {
      const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
      return [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map(x => x.toString(16).padStart(2, '0')).join('');
    };
    h.geometry = [];
    for (const mesh of h.meshes) {
      const attrs = {};
      for (const [name, attr] of Object.entries(mesh.geometry.attributes)) attrs[name] = { count: attr.count, itemSize: attr.itemSize, type: attr.array.constructor.name, sha256: await bytesHash(attr.array) };
      const morphs = {};
      for (const [name, items] of Object.entries(mesh.geometry.morphAttributes)) morphs[name] = await Promise.all(items.map(async attr => ({ count: attr.count, itemSize: attr.itemSize, sha256: await bytesHash(attr.array) })));
      h.geometry.push({ name: mesh.name, attrs, index: mesh.geometry.index ? { count: mesh.geometry.index.count, sha256: await bytesHash(mesh.geometry.index.array) } : null, morphs });
    }
    h.measure = () => {
      s.vrm.scene.updateMatrixWorld(true);
      const transforms = [], joints = {}, materials = [];
      s.vrm.scene.traverse(node => transforms.push({ name: node.name, type: node.type, p: node.position.toArray(), q: node.quaternion.toArray(), scale: node.scale.toArray(), matrixWorld: node.matrixWorld.toArray(), morphs: node.morphTargetInfluences ? [...node.morphTargetInfluences] : null }));
      for (const name of Object.keys(s.vrm.humanoid.normalizedHumanBones)) {
        const n = s.vrm.humanoid.getNormalizedBoneNode(name), raw = s.vrm.humanoid.getRawBoneNode(name);
        joints[name] = { normalized: n ? { p: n.position.toArray(), q: n.quaternion.toArray(), world: n.matrixWorld.toArray() } : null, raw: raw ? { p: raw.position.toArray(), q: raw.quaternion.toArray(), world: raw.matrixWorld.toArray() } : null };
      }
      for (const m of h.materials.keys()) materials.push({ name: m.name, type: m.type, color: arr(m.color), shadeColorFactor: arr(m.shadeColorFactor),
        shadingShiftFactor: m.shadingShiftFactor, shadingToonyFactor: m.shadingToonyFactor, shadingShiftTextureScale: m.shadingShiftTextureScale,
        outlineWidthFactor: m.outlineWidthFactor, outlineWidthMode: m.outlineWidthMode, outlineColorFactor: arr(m.outlineColorFactor), outlineLightingMixFactor: m.outlineLightingMixFactor,
        rimLightingMixFactor: m.rimLightingMixFactor, parametricRimColorFactor: arr(m.parametricRimColorFactor), parametricRimFresnelPowerFactor: m.parametricRimFresnelPowerFactor,
        parametricRimLiftFactor: m.parametricRimLiftFactor, matcapFactor: arr(m.matcapFactor), emissive: arr(m.emissive), emissiveIntensity: m.emissiveIntensity,
        opacity: m.opacity, transparent: m.transparent, alphaTest: m.alphaTest, depthTest: m.depthTest, depthWrite: m.depthWrite,
        textures: Object.fromEntries(['map', 'shadeMultiplyTexture', 'shadingShiftTexture', 'outlineWidthMultiplyTexture', 'rimMultiplyTexture', 'matcapTexture', 'emissiveMap', 'normalMap'].map(key => [key, m[key] ? { name: m[key].name, colorSpace: m[key].colorSpace, width: m[key].image?.width, height: m[key].image?.height, src: m[key].image?.src || null } : null])) });
      return { camera: { p: s.camera.position.toArray(), q: s.camera.quaternion.toArray(), fov: s.camera.fov, aspect: s.camera.aspect, near: s.camera.near, far: s.camera.far },
        lights: Object.fromEntries(['ambient', 'key', 'fill'].map(key => [key, { intensity: s[key].intensity, color: s[key].color.toArray(), position: s[key].position.toArray() }])),
        renderer: { toneMapping: s.renderer.toneMapping, toneMappingExposure: s.renderer.toneMappingExposure, outputColorSpace: s.renderer.outputColorSpace },
        materials, transforms, joints, geometry: h.geometry,
      };
    };
    h.applySettings = (light) => {
      for (const [m, rest] of h.materials) for (const [key, value] of Object.entries(rest)) {
        if (value === undefined) continue;
        if (m[key]?.copy && value?.clone) m[key].copy(value); else m[key] = value;
      }
      if (!settings) return;
      for (const [key, item] of Object.entries(light === 'normal' || settings.allLights ? settings.lights || {} : {})) {
        if (item.intensity != null) s[key].intensity = item.intensity;
        if (item.color != null) s[key].color.set(item.color);
        if (item.position != null) s[key].position.fromArray(item.position);
      }
      for (const [m] of h.materials) for (const item of settings.materials || []) if (new RegExp(item.match).test(m.name)) {
        for (const [key, value] of Object.entries(item.values || {})) {
          if (m[key]?.isColor) m[key].set(value); else m[key] = value;
        }
        for (const [key, factor] of Object.entries(item.multiplyColor || {})) m[key].multiplyScalar(factor);
        m.needsUpdate = true;
      }
    };
    h.fixed = (expression, view, light) => {
      h.cal?.exit();
      for (const [name, original] of Object.entries(h.lights)) {
        s[name].intensity = original.intensity; s[name].color.copy(original.color); s[name].position.copy(original.position);
      }
      h.applySettings(light);
      if (light !== 'normal') s.key.position.set(light === 'left' ? -3 : 3, 1.6, .8);
      for (const key of ['fov', 'aspect', 'near', 'far']) s.camera[key] = h.camera[key];
      s.camera.position.copy(h.camera.position).sub(h.center).applyAxisAngle(new THREE.Vector3(0, 1, 0), view * Math.PI / 180).add(h.center);
      s.camera.lookAt(h.center); s.camera.updateProjectionMatrix();
      h.cal = new AvatarCalibration(a); h.cal.enter();
      const snapshot = h.cal.pose({ expression, intensity: 1, blink: 0, gaze: { x: 0, y: 0 }, mouthOpen: 0 });
      for (const mesh of h.meshes) if (mesh.isSkinnedMesh) { mesh.computeBoundingSphere(); mesh.computeBoundingBox(); }
      r.draw(h.cal.params, 0);
      const canvas = document.createElement('canvas'); canvas.width = 560; canvas.height = 680;
      const c = canvas.getContext('2d'); c.fillStyle = '#e7eaf0'; c.fillRect(0, 0, 560, 680); c.drawImage(s.renderer.domElement, 0, 0);
      return { image: canvas.toDataURL('image/png'), input: { expression, view, light, pose: snapshot.pose, params: snapshot.params }, actual: h.measure() };
    };
    h.dynamic = light => {
      h.fixed('daily', 0, light);
      const frames = [], images = [], base = { ...h.cal.params };
      for (let i = 0; i <= 180; i++) {
        const t = i/60, phase = Math.sin(t*Math.PI*2/3);
        const blink = Math.max(0, 1-Math.abs(t-.75)/.1);
        const opening = t > 1 && t < 2.5 ? .62*Math.sin((t-1)*Math.PI/1.5)**2 : 0;
        const params = { ...base, ParamAngleX: .82*phase, ParamAngleY: .12*Math.sin(t*Math.PI*2/3),
          ParamEyeLOpen: base.ParamEyeLOpen*(1-blink), ParamEyeROpen: base.ParamEyeROpen*(1-blink),
          ParamEyeBallX: .45*Math.sin(t*Math.PI*2/3+.3), ParamEyeBallY: .10*Math.sin(t*Math.PI*2),
          ParamMouthOpenY: opening, ParamMouthForm: .18, ParamMouthRound: .25 };
        for (const mesh of h.meshes) if (mesh.isSkinnedMesh) { mesh.computeBoundingSphere(); mesh.computeBoundingBox(); }
        r.draw(params, 1/60);
        const sample = h.measure();
        frames.push({ i, t, params, joints: sample.joints, expressions: Object.fromEntries(Object.keys(s.vrm.expressionManager.expressionMap).map(name => [name, s.vrm.expressionManager.getValue(name)])),
          meshMorphs: h.meshes.map(mesh => ({ name: mesh.name, weights: mesh.morphTargetInfluences ? [...mesh.morphTargetInfluences] : null })) });
        if (i%6 === 0) {
          const canvas=document.createElement('canvas'); canvas.width=560;canvas.height=680;
          const c=canvas.getContext('2d');c.fillStyle='#e7eaf0';c.fillRect(0,0,560,680);c.drawImage(s.renderer.domElement,0,0);
          images.push({ i, t, image: canvas.toDataURL('image/png') });
        }
      }
      return { light, frames, images };
    };
    const gl = s.renderer.getContext(), ext = gl.getExtension('WEBGL_debug_renderer_info');
    return { model: s.vrm.meta.name, modelHeight: s.modelHeight, size: { width: 560, height: 680 }, geometry: h.geometry,
      gpu: { renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null, vendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null },
      calibration: captureCalibrationFingerprint(a), materials: h.measure().materials };
  }, settings);
  if(reuse) {
    const saved=JSON.parse(await readFile(`${out}/captures.json`,'utf8'));
    same(saved.sourceHashes,sourceHashes,'reused evidence source hashes must match current runtime');
    assert.equal(saved.modelSha256,hash(await readFile('web/models/seed-daily.vrm')));
    captures.push(...saved.captures);dynamics.push(...saved.dynamics);
    console.log(`Reusing ${captures.length} captures/${dynamics.length} dynamic scenes with matching source and model hashes`);
  }
  else for (const expression of (probe ? ['daily'] : ['daily', 'happy'])) for (const light of (probe ? ['normal', 'left'] : ['normal', 'left', 'right'])) for (const view of (probe ? [0, -25] : [0, -25, 25])) {
    const name = `${expression}-${light}-${view === 0 ? 'front' : view < 0 ? 'left25' : 'right25'}`;
    const result = await page.evaluate(input => window.faceHarness.fixed(input.expression, input.view, input.light), { expression, view, light });
    await writeFile(`${out}/${name}.png`, Buffer.from(result.image.split(',')[1], 'base64'));
    delete result.image;
    const bytes = Buffer.from(JSON.stringify(result.actual));
    result.actualFingerprint = hash(bytes);
    captures.push({ name, ...result });
    console.log(`Captured ${name}`);
  }
  if (dynamic && !reuse) for (const light of ['normal', 'left', 'right']) {
    const result = await page.evaluate(light => window.faceHarness.dynamic(light), light);
    await mkdir(`${out}/dynamic-${light}`, { recursive: true });
    for (const item of result.images) {
      await writeFile(`${out}/dynamic-${light}/${String(item.i).padStart(3,'0')}.png`, Buffer.from(item.image.split(',')[1], 'base64'));
      delete item.image;
    }
    dynamics.push(result); console.log(`Captured dynamic ${light} (${result.frames.length} 60Hz steps, ${result.images.length} raster captures)`);
  }
  let verification = null;
  await writeFile(`${out}/captures.json`, JSON.stringify({ schema:1,label,sourceHashes,setup,captures,dynamics,modelSha256:hash(await readFile('web/models/seed-daily.vrm')),status:'captured; verification pending' }));
  if (verify) {
    const prior = JSON.parse(await readFile(`${base}/before/report.json`, 'utf8'));
    for (const capture of captures) {
      const old = prior.captures.find(x => x.name === capture.name); assert(old);
      same(capture.input, old.input, `${capture.name} same params`);
      for (const key of ['camera','lights','renderer','geometry','transforms','joints']) same(capture.actual[key], old.actual[key], `${capture.name} unchanged ${key}`);
      for (let i=0;i<capture.actual.materials.length;i++) {
        const actual=capture.actual.materials[i], original=old.actual.materials[i], factor=/^body_bake(?:$|\b)/.test(actual.name) ? .9 : 1;
        for(const key of ['color','shadeColorFactor']) {
          if(!original[key]) { assert.equal(actual[key], original[key]); continue; }
          actual[key].forEach((value,k) => assert(Math.abs(value-original[key][k]*factor)<1e-12,`${capture.name}/${actual.name}/${key}[${k}] factor`));
        }
        const other = material => Object.fromEntries(Object.entries(material).filter(([key])=>!['color','shadeColorFactor'].includes(key)));
        same(other(actual),other(original),`${capture.name}/${actual.name} material other properties unchanged`);
      }
    }
    checks.push('18 matched fixed scenes: camera/light/geometry/raw-normalized transforms/morph params unchanged; only face color and shade linear x.90');
    if(dynamic) {
      for(const result of dynamics) {
        const old=prior.dynamics.find(x=>x.light===result.light); assert(old);
        same(result.frames,old.frames,`${result.light} actual dynamic joints/expressions/morphs/input preserved`);
      }
      checks.push('3 actual dynamic scenes: 181 60Hz steps each have identical input, raw/normalized joints, eye/mouth expressions and morph weights');
    }
    verification = await page.evaluate(async originalMaterials => {
      const { AvatarCalibration } = await import('/static/avatar/calibration.js');
      const a=window.__avatar,r=a.svg,h=window.faceHarness,s=r._state;
      h.cal?.exit();h.cal=null;
      const measured=()=>({mode:r.getFaceShading(),actual:h.measure()});
      r.setFaceShading(false);const off1=measured();r.setFaceShading(true);const on1=measured();
      r.setFaceShading(true);const on2=measured();r.setFaceShading(false);const off2=measured();
      const calibration=[];
      for(const enabled of [false,true]) {
        r.setFaceShading(enabled);const before=measured();
        const cal=new AvatarCalibration(a);cal.enter();cal.pose({expression:'happy',mouthOpen:.4,blink:.3});
        r.setFaceShading(!enabled);const rejectsChangedAppearance=!cal.isCurrent();cal.exit();
        calibration.push({enabled,before,after:measured(),rejectsChangedAppearance});
      }
      r.setFaceShading(true);const faceMaterialSnapshot=()=>h.measure().materials.filter(x=>/^body_bake(?:$|\b)/.test(x.name)).map(x=>({name:x.name,color:x.color,shadeColorFactor:x.shadeColorFactor}));
      const composition=[];
      for(const presentation of [false,true,false,true]) {r.setPresentation(presentation);composition.push({presentation,mode:r.getFaceShading(),face:faceMaterialSnapshot(),lights:h.measure().lights});}
      return {off1,on1,on2,off2,calibration,composition};
    }, prior.setup.materials);
    await writeFile(`${out}/verification.json`,JSON.stringify(verification));
    assert(verification.off1.mode.available && !verification.off1.mode.enabled);
    assert(verification.on1.mode.available && verification.on1.mode.enabled);
    same(verification.off1.actual.materials,verification.off2.actual.materials);same(verification.on1.actual.materials,verification.on2.actual.materials);
    nearly(verification.off1,verification.off2);nearly(verification.on1,verification.on2);
    for(let i=0;i<verification.off1.actual.materials.length;i++) {
      for(const key of ['color','shadeColorFactor']) assert.deepEqual(verification.off1.actual.materials[i][key],prior.setup.materials[i][key],`off original ${i}/${key}`);
    }
    const nonAppearance=actual=>Object.fromEntries(Object.entries(actual).filter(([key])=>key!=='materials'));
    nearly(nonAppearance(verification.off1.actual),nonAppearance(verification.on1.actual));
    checks.push('on/off/on/off immediately restores exact original colors; repeated enable has no multiplication drift; bones/morphs/camera/lights unchanged');
    const calibrationRelevant = measured => ({...measured,actual:{...measured.actual,transforms:measured.actual.transforms.map(({matrixWorld,...local})=>local)}});
    for(const item of verification.calibration) {
      assert(item.rejectsChangedAppearance);same(item.before.actual.materials,item.after.actual.materials);
      nearly(calibrationRelevant(item.before),calibrationRelevant(item.after));
      item.legacyWorldCacheChanges=item.before.actual.transforms.map((node,i)=>({name:node.name,maxDelta:Math.max(...node.matrixWorld.map((value,k)=>Math.abs(value-item.after.actual.transforms[i].matrixWorld[k])))})).filter(x=>x.maxDelta>1e-10);
    }
    checks.push('calibration restores either initial face mode, exact material colors, local transforms, mapped joint worlds and morphs, and detects changed appearance');
    for(const item of verification.composition) {assert(item.mode.enabled);assert.deepEqual(item.face,verification.composition[0].face);}
    assert.deepEqual(verification.composition[0].lights,verification.composition[2].lights);assert.deepEqual(verification.composition[1].lights,verification.composition[3].lights);
    checks.push('face preset composes with repeated presentation toggles without color drift; original light presets restore');
    await page.locator('#face-shading').uncheck();
    assert.equal((await page.evaluate(()=>window.__avatar.svg.getFaceShading())).enabled,false);
    await page.locator('#face-shading').check();
    assert.equal((await page.evaluate(()=>window.__avatar.svg.getFaceShading())).enabled,true);
    checks.push('actual lab checkbox switches preset off and on');
  }
  const microphoneRequests = await page.evaluate(() => window.faceHarness.microphoneRequests);
  assert.equal(captures.length, probe ? 4 : 18); assert.deepEqual(errors, []); assert.deepEqual(forbiddenRequests, []); assert.equal(microphoneRequests, 0);
  const report = { schema: 1, label, sourceMode: frozen ? 'frozen-before' : 'current', settings, sourceHashes,
    modelSha256: hash(await readFile('web/models/seed-daily.vrm')), setup, captures, dynamics, verification, checks, maximumRestoredNumericDelta, errors, forbiddenRequests, microphoneRequests,
    method: 'Production AvatarCalibration fixed poses; same 560x680 camera distance/crop across all views and runs; camera yaw 0/+/-25 around fixed upper-face center; only key direction changes in side-light scenes; all geometry and transforms recorded; no individually fitted frame.' };
  await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
  console.log(`PASS ${captures.length} fixed face captures -> ${out}`);
} finally { await browser.close(); }
