#!/usr/bin/env node
/**
 * Reproducible eye calibration evidence, separated from normal-loop performance.
 * Usage: node scripts/capture-eye-calibration.mjs [--manifest config/avatar-eye-calibration.json]
 *        [--url http://127.0.0.1:8000/static/avatar_lab.html?model=daily]
 *        [--perf-seconds 5] [--output eval/avatar/calibration] [--gpu-mode auto|software]
 *
 * No intermediate rasterization is skipped in the performance scenes. Reported
 * frame CPU wall time includes Avatar._frame + WebGL submission; it is not GPU time.
 */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, readFile, writeFile, copyFile, stat, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const runCommand = promisify(execFile);
const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sha256 = value => createHash('sha256').update(value).digest('hex');
const expressions = ['daily', 'happy', 'sleepy', 'curious', 'shy', 'tsundere', 'focused', 'surprised'];
const defaultCoreExpressions = ['daily', 'happy', 'sleepy', 'curious'];
const sourcePaths = [
  'config/avatar.yaml', 'web/avatar/calibration.js', 'web/avatar/performance-probe.js', 'web/avatar/index.js',
  'web/avatar/params.js', 'web/avatar/drivers/eye-expression.js',
  'web/avatar/render/eye-morph.js', 'web/avatar/render/vrm.js',
  'web/avatar/render/vrm-mapping.js', 'scripts/capture-eye-calibration.mjs',
  'web/avatar_lab.html', 'web/avatar_lab.js', 'package.json', 'package-lock.json',
  'scripts/build_seed_eye_expression.py', 'scripts/validate_seed_eye_expression.py',
  'web/models/seed-daily-eyes-source/build.json', 'web/models/SEED_DAILY_EYES_LICENSE.md',
  'scripts/build_seed_eye_lids.py', 'scripts/validate_seed_eye_lids.py',
  'web/models/seed-daily-lids-source/build.json', 'web/models/SEED_DAILY_LIDS_LICENSE.md',
];
const args = new Map();
for (let i = 2; i < process.argv.length; i++) {
  const flag = process.argv[i];
  if (flag === '--help' || flag === '-h') {
    console.log('Usage: node scripts/capture-eye-calibration.mjs [--manifest config/avatar-eye-calibration.json] [--url URL] [--perf-seconds 5] [--output eval/avatar/calibration] [--baseline JSON] [--gpu-mode auto|software]');
    process.exit(0);
  }
  if (!['--manifest', '--url', '--perf-seconds', '--output', '--baseline', '--gpu-mode'].includes(flag)) throw new Error(`Unknown argument: ${flag}`);
  const value = process.argv[++i];
  if (!value || value.startsWith('--')) throw new Error(`${flag} requires a value`);
  args.set(flag, value);
}

const manifestPath = path.resolve(workspace, args.get('--manifest') || 'config/avatar-eye-calibration.json');
const url = args.get('--url') || 'http://127.0.0.1:8000/static/avatar_lab.html?model=daily';
const durationSeconds = Number(args.get('--perf-seconds') || process.env.EYE_CALIBRATION_PERF_SECONDS || 5);
const gpuMode = args.get('--gpu-mode') || 'auto';
if (!['auto', 'software'].includes(gpuMode)) throw new Error('--gpu-mode must be auto or software');
// auto permits Chromium's normal renderer selection. software is an explicit,
// reproducible fallback; neither option is evidence of the selected hardware.
const launchArgs = gpuMode === 'software' ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
  : process.platform === 'win32' ? ['--use-angle=d3d11'] : [];
if (!Number.isFinite(durationSeconds) || durationSeconds < 0.5 || durationSeconds > 120) {
  throw new Error('Performance duration must be between 0.5 and 120 seconds; default is 5 seconds.');
}
const root = path.resolve(workspace, args.get('--output') || 'eval/avatar/calibration');
const runId = `${new Date().toISOString().replace(/[:.]/g, '-')}-${process.pid}`;
const output = path.join(root, runId);
const browserViewport = { width: 1400, height: 1000 };
const canvasSize = { width: 720, height: 720 };
const startedAt = new Date().toISOString();
const captures = [], checks = [], errors = [];
let browser, page, entered = false;

function relative(file) { return path.relative(output, file).split(path.sep).join('/'); }
async function saveJson(name, value) {
  await writeFile(path.join(output, name), JSON.stringify(value, null, 2) + '\n', 'utf8');
}
function check(value, label) {
  assert.ok(value, label);
  checks.push(label);
}
function finite(value) {
  if (typeof value === 'number') return Number.isFinite(value);
  if (value && typeof value === 'object') return Object.values(value).every(finite);
  return true;
}
async function revision() {
  const result = {};
  for (const [key, gitArgs] of [
    ['head', ['rev-parse', 'HEAD']], ['branch', ['branch', '--show-current']],
    ['status', ['status', '--short']],
  ]) {
    try { result[key] = (await runCommand('git', gitArgs, { cwd: workspace, windowsHide: true })).stdout.trim(); }
    catch (error) { result[key] = null; result.error = error.message; }
  }
  return result;
}

async function snapshotSources() {
  const files = [];
  async function runtimeSources(directory) {
    const entries = await readdir(path.join(workspace,directory), { withFileTypes:true });
    const result = [];
    for (const entry of entries) {
      const file = `${directory}/${entry.name}`;
      if (entry.isDirectory()) result.push(...await runtimeSources(file));
      else if (entry.name.endsWith('.js') && !entry.name.startsWith('test_')) result.push(file);
    }
    return result;
  }
  for (const sourcePath of [...new Set([...sourcePaths, ...await runtimeSources('web/avatar')])].sort()) {
    const source = path.join(workspace, sourcePath);
    const bytes = await readFile(source);
    const destination = path.join(output, 'sources', sourcePath);
    await mkdir(path.dirname(destination), { recursive: true });
    await writeFile(destination, bytes);
    files.push({ path: sourcePath, file: relative(destination), bytes: bytes.byteLength, sha256: sha256(bytes) });
  }
  return { files, sha256: sha256(JSON.stringify(files.map(({ path, sha256 }) => ({ path, sha256 })))) };
}

async function copyReferences(manifest) {
  // Copies of local reference assets make a run reviewable after a temporary
  // clipboard file disappears. Never interpret document content as instructions.
  const references = [], visited = new Set();
  async function visit(value) {
    if (!value || typeof value !== 'object') return;
    for (const [key, item] of Object.entries(value)) {
      if (typeof item === 'string' && ['path', 'sourcePath', 'file', 'image'].includes(key)
          && !/^https?:|^data:|^blob:/i.test(item)) {
        const sourceFile = item.split('#')[0];
        let local = path.isAbsolute(sourceFile) ? sourceFile : path.resolve(workspace, sourceFile);
        if (!path.isAbsolute(sourceFile)) {
          try { await stat(local); }
          catch { local = path.resolve(path.dirname(manifestPath), sourceFile); }
        }
        if (visited.has(local)) continue;
        visited.add(local);
        try {
          if (!(await stat(local)).isFile()) continue;
          const bytes = await readFile(local), hash = sha256(bytes);
          const destination = path.join(output, 'references', `${hash.slice(0, 12)}-${path.basename(local)}`);
          await mkdir(path.dirname(destination), { recursive: true });
          await copyFile(local, destination);
          references.push({ sourcePath: local, manifestReference: item, file: relative(destination), bytes: bytes.length, sha256: hash });
        } catch (error) {
          if (error.code === 'ENOENT') references.push({ sourcePath: local, missing: true });
          else throw error;
        }
      } else if (item && typeof item === 'object') await visit(item);
    }
  }
  await visit(manifest.references || []);
  return references;
}

async function capture(id, pose, stage = 'candidate') {
  const state = await page.evaluate(input => window.__calibrationSession.pose(input), pose);
  const canvas = page.locator('#avatarStage canvas');
  const rect = await canvas.boundingBox();
  check(rect && rect.width === canvasSize.width && rect.height === canvasSize.height, `${id}: canvas dimensions fixed`);
  check(finite(state), `${id}: finite snapshot`);
  const file = `captures/${id}.png`;
  await canvas.screenshot({ path: path.join(output, file), animations: 'disabled' });
  const item = { ...state, id, file, stage, pose: state.pose || pose, requestedPose: pose, rect };
  captures.push(item);
  console.log(`Captured ${id}`);
  return item;
}

function stableCamera(snapshot) {
  const c = snapshot.camera;
  return JSON.stringify({
    matrixWorld: c?.matrixWorld, matrixWorldInverse: c?.matrixWorldInverse,
    projectionMatrix: c?.projectionMatrix, position: c?.position, quaternion: c?.quaternion,
  });
}

async function measureScene(name) {
  await page.evaluate(async name => {
    const avatar = window.__avatar;
    avatar.stopSpeaking(); avatar.gesture.stopAll();
    avatar.setReducedMotion(false);
    avatar.setEyeExpression(name === 'speech' ? 'happy' : 'daily');
    avatar.setEyeExpressionIntensity(1); avatar.setEyeHighlight(1);
    avatar.setGazeMode(name === 'auto' ? 'auto' : 'forward');
    if (!avatar._raf) avatar._start();
    if (name === 'speech') {
      const ctx = new AudioContext(); await ctx.resume();
      window.__calibrationAudio = ctx;
      avatar.attachOutputAnalyser(null);
      avatar.speak('你好，今天我们一起检查眼睛和表情，看看注视、眨眼与说话能不能自然配合。', ctx, ctx.currentTime, 150);
    }
  }, name);
  // Warm-up remains real rendering and is excluded from recorded measurements.
  await page.waitForTimeout(500);
  const startState = await page.evaluate(async () => {
    const a = window.__avatar;
    const { serializeCalibrationCamera } = await import('/static/avatar/calibration.js');
    return { gazeMode: a.gazeMode, preview: a.eyeFrame?.preview, params: a.eyeFrame?.params,
      camera: serializeCalibrationCamera(a.svg), presentation: !!a.svg._state.presentation,
      eyeFocus: !!a.svg._state.eyeFocus, reducedMotion: a.reducedMotion,
      silentSpeechTimeline: !!window.__calibrationAudio };
  });
  const raw = await page.evaluate(async ({ name, durationMs }) => {
    const { AvatarPerformanceProbe } = await import('/static/avatar/performance-probe.js');
    const probe = new AvatarPerformanceProbe(window.__avatar);
    window.__calibrationPerformanceProbe = probe;
    try { return await probe.start({ name, durationMs }).complete(); }
    finally { await probe.stop(); delete window.__calibrationPerformanceProbe; }
  }, { name, durationMs: durationSeconds * 1000 });
  await page.evaluate(async () => {
    window.__avatar.stopSpeaking();
    if (window.__calibrationAudio) {
      await window.__calibrationAudio.close(); delete window.__calibrationAudio;
    }
  });
  check(raw.avatarFrames.length > 0 && raw.rafIntervalsMs.length > 0, `${name}: normal rendering produced measured frames`);
  check(raw.avatarFrames.some(frame => frame.render.calls > 0 && frame.render.triangles > 0), `${name}: raster submission was active`);
  check(finite(raw), `${name}: performance samples are finite`);
  const result = { ...raw, startState };
  console.log(`Measured ${name}: ${raw.avatarFrames.length} actual frames over ${(raw.elapsedMs / 1000).toFixed(2)} s`);
  return result;
}

try {
  // mkdir without recursive on the run directory refuses to overwrite history.
  await mkdir(root, { recursive: true });
  await mkdir(output);
  await mkdir(path.join(output, 'captures'));
  const manifestBytes = await readFile(manifestPath);
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  await writeFile(path.join(output, 'reference-manifest.json'), manifestBytes);
  let baselineOverrides = manifest.baselineOverrides || {};
  if (args.has('--baseline')) {
    const baselinePath = path.resolve(workspace, args.get('--baseline'));
    const baselineBytes = await readFile(baselinePath), baseline = JSON.parse(baselineBytes.toString('utf8'));
    await writeFile(path.join(output, 'baseline-manifest.json'), baselineBytes);
    baselineOverrides = baseline.baselineOverrides || baseline.presets?.reduce((result, preset) => {
      result[preset.name] = preset.values || preset.overrides; return result;
    }, {}) || baseline;
  }
  const source = await snapshotSources();
  const git = await revision();
  const references = await copyReferences(manifest);
  browser = await chromium.launch({ headless: true, args: launchArgs });
  page = await browser.newPage({ viewport: browserViewport, deviceScaleFactor: 1 });
  page.on('pageerror', error => errors.push(error.message));
  try {
    const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    if (!response?.ok()) throw new Error(`HTTP ${response?.status() ?? 'no response'}`);
  }
  catch (error) { throw new Error(`Avatar service unavailable at ${url}: ${error.message}`); }
  await page.waitForFunction(() => window.__avatar?.rendererKind === 'vrm'
    && window.__avatar.svg._state?.vrm && !document.getElementById('renderer').disabled,
  {}, { timeout: 90000 });
  await page.addStyleTag({ content: `
    main { max-width:1380px; grid-template-columns:722px 340px; }
    #viewport { position:relative; top:0; width:722px; height:722px; min-height:722px; }
    #avatarStage { width:720px; height:720px; }
    #viewport .badge, #viewport .caption { visibility:hidden; }
  ` });
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const environment = await page.evaluate(() => {
    const state = window.__avatar.svg._state, gl = state.renderer.getContext();
    const extension = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      userAgent: navigator.userAgent, devicePixelRatio, hardwareConcurrency: navigator.hardwareConcurrency,
      webglRenderer: extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      webglVendor: extension ? gl.getParameter(extension.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      canvas: { css: { width: 720, height: 720 }, pixel: { width: state.renderer.domElement.width, height: state.renderer.domElement.height } },
      renderer: { outputColorSpace: state.renderer.outputColorSpace, toneMapping: state.renderer.toneMapping, toneMappingExposure: state.renderer.toneMappingExposure },
      config: window.__avatar.cfg,
    };
  });
  const modelUrl = await page.evaluate(() => window.__avatar.svg._state.modelUrl || null);
  const requestedModel = new URL(url).searchParams.get('model');
  const actualModelUrl = modelUrl || (requestedModel === manifest.model ? manifest.modelUrl : null)
    || `/static/models/${requestedModel === 'seed' ? 'seed-san' : requestedModel === 'daily' ? 'seed-daily' : 'sample'}.vrm`;
  const absoluteModelUrl = new URL(actualModelUrl, url).href;
  const modelResponse = await page.request.get(absoluteModelUrl);
  if (!modelResponse.ok()) throw new Error(`Could not hash loaded model ${absoluteModelUrl}: HTTP ${modelResponse.status()}`);
  const modelBytes = await modelResponse.body();
  const modelInput = { url: absoluteModelUrl, bytes: modelBytes.length, sha256: sha256(modelBytes), contentType: modelResponse.headers()['content-type'] || null };
  await saveJson('input-config.json', environment.config);
  delete environment.config;
  await page.evaluate(async () => {
    const { AvatarCalibration } = await import('/static/avatar/calibration.js');
    window.__calibrationSession = new AvatarCalibration(window.__avatar);
    window.__calibrationSession.enter({ presentation: true, eyeFocus: true });
  });
  entered = true;
  const initial = await page.evaluate(() => window.__calibrationSession.snapshot());
  const session = {
    version: 1, runId, startedAt, status: 'capturing', url,
    manifest: { sourcePath: manifestPath, file: 'reference-manifest.json', sha256: sha256(manifestBytes) },
    baseline: { kind: 'previous preset parameters rerendered in this frozen scene',
      source: args.has('--baseline') ? 'baseline-manifest.json' : 'reference-manifest.json#baselineOverrides',
      overrides: baselineOverrides, note: 'A parameter baseline, not an approved character-design target or historical screenshot.' },
    references, source, revision: git, inputConfig: 'input-config.json', model: modelInput,
    browser: { headless: true, version: browser.version(), requestedGpuMode: gpuMode, launchArgs, viewport: browserViewport },
    environment, canvasSize, presentation: true, eyeFocus: true,
    fingerprint: initial.fingerprint, camera: initial.camera, headAnchor: initial.headAnchor,
    capabilities: initial.capabilities, captures: 'captures.json', performance: 'performance.json',
    requestedPerformanceDurationSeconds: durationSeconds,
    visualJudgement: 'Evidence only. No automatic claim that a character expression looks correct.',
  };
  await saveJson('session.json', session);

  for (const expression of manifest.coreExpressions || defaultCoreExpressions) {
    if (!baselineOverrides[expression]) continue;
    await capture(`baseline-${expression}`, { expression, intensity: 1, blink: 0, gaze: { x: 0, y: 0 }, mouthOpen: 0,
      browMode: manifest.baselineBrowModes?.[expression] || 'calibrated',
      lidMode: manifest.baselineLidModes?.[expression] || 'calibrated', overrides: baselineOverrides[expression] }, 'baseline');
  }
  for (const expression of expressions) {
    await capture(`candidate-${expression}`, { expression, intensity: 1, blink: 0, gaze: { x: 0, y: 0 }, mouthOpen: 0 });
  }
  for (const expression of manifest.blinkExpressions || ['daily', 'happy', 'sleepy']) {
    for (const blink of [0.25, 0.5, 0.75, 1, 0]) {
      const label = blink === 0 ? 'recovered' : String(Math.round(blink * 100)).padStart(3, '0');
      const captureItem = await capture(`${expression}-blink-${label}`, { expression, intensity: 1, blink, gaze: { x: 0, y: 0 }, mouthOpen: 0 });
      if (blink === 1) {
        check(captureItem.params.ParamEyeLOpen === 0 && captureItem.params.ParamEyeROpen === 0, `${expression}: full blink reaches zero openness`);
        const native = captureItem.nativeMorphs || captureItem.native || {};
        check(finite(native), `${expression}: closed native morphs are finite`);
        const standard = captureItem.expressionWeights;
        if (standard?.blinkLeft !== undefined) check(standard.blinkLeft === 1 && standard.blinkRight === 1, `${expression}: VRM blink expression fully closes`);
        for (const name of ['eye_close_L', 'eye_close_R']) {
          if (name in native) check(native[name] === 1, `${expression}: native ${name} fully closes`);
        }
        if ('eye_smile' in native) check(native.eye_smile === 0, `${expression}: native smile retires during full closure`);
        for (const name of ['neuro_eye_smile_L', 'neuro_eye_smile_R', 'neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R']) {
          if (name in native) check(native[name] === 0, `${expression}: ${name} retires during full closure`);
        }
      }
      if (blink === 0) {
        const neutral = captures.find(item => item.id === `candidate-${expression}`);
        check(Math.abs(captureItem.params.ParamEyeLOpen - neutral.params.ParamEyeLOpen) < 1e-8
          && Math.abs(captureItem.params.ParamEyeROpen - neutral.params.ParamEyeROpen) < 1e-8, `${expression}: recovered openness matches source pose`);
      }
    }
  }
  for (const [direction, gaze] of [
    ['left', { x: -0.8, y: 0 }], ['right', { x: 0.8, y: 0 }],
    ['up', { x: 0, y: -0.6 }], ['down', { x: 0, y: 0.6 }],
  ]) await capture(`daily-gaze-${direction}`, { expression: 'daily', intensity: 1, blink: 0, gaze, mouthOpen: 0 });
  await capture('happy-mouth-open', { expression: 'happy', intensity: 1, blink: 0, gaze: { x: 0, y: 0 }, mouthOpen: 0.7 });
  for (const scene of manifest.extraScenes || []) {
    if (!/^[a-z0-9-]+$/.test(scene.id) || captures.some(item => item.id === scene.id)) throw new Error(`Invalid or duplicate scene id: ${scene.id}`);
    await capture(scene.id, scene.pose, scene.stage || 'extra');
  }
  for (const captureItem of captures) {
    check(stableCamera(captureItem) === stableCamera(initial), `${captureItem.id}: frozen camera matrices match`);
    check(JSON.stringify(captureItem.fingerprint) === JSON.stringify(initial.fingerprint), `${captureItem.id}: frozen scene fingerprint matches`);
    check(Math.abs((captureItem.params.ParamAngleX || 0)) < 1e-10
      && Math.abs((captureItem.params.ParamAngleY || 0)) < 1e-10
      && Math.abs((captureItem.params.ParamAngleZ || 0)) < 1e-10, `${captureItem.id}: head pose is neutral`);
  }
  await saveJson('captures.json', captures);
  await page.evaluate(() => window.__calibrationSession.exit());
  entered = false;
  const performanceScenes = [];
  for (const name of ['forward', 'auto', 'speech']) performanceScenes.push(await measureScene(name));
  await saveJson('performance.json', {
    version: 2, environment, browser: session.browser,
    note: 'Normal Avatar rAF with actual rendering. CPU wall samples are not GPU timings. Actual GL renderer and optional GPU query availability are recorded per scene; headless numbers describe this run only.',
    scenes: performanceScenes,
  });
  check(errors.length === 0, `no browser page errors: ${errors.join('; ')}`);
  session.status = 'complete'; session.completedAt = new Date().toISOString();
  session.captureCount = captures.length; session.checks = checks; session.pageErrors = errors;
  await saveJson('session.json', session);
  console.log(`Calibration evidence saved: ${output}`);
  console.log(`${captures.length} screenshots, ${checks.length} structural checks; visual judgement remains manual.`);
} catch (error) {
  if (captures.length) await saveJson('captures.json', captures);
  try { await saveJson('failure.json', { failedAt: new Date().toISOString(), message: error.message, stack: error.stack, checks, pageErrors: errors }); } catch {}
  console.error(error.stack || error.message);
  process.exitCode = 1;
} finally {
  if (entered && page && !page.isClosed()) {
    try { await page.evaluate(() => window.__calibrationSession?.exit()); } catch {}
  }
  await browser?.close();
}
