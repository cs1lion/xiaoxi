/* Avatar stage capture + metrics: idle vs speaking at 1440x1000 and 390x844.
 *
 * Run against a freshly started server (do not reuse a cached 8000):
 *   node scripts/capture-avatar-stage.mjs --origin http://127.0.0.1:8010 --tag before
 *
 * Face width is the projected head-mesh span at eye level, in CSS pixels.
 * Mouth contrast is measured from the WebGL canvas pixels captured immediately
 * after render: median luminance of the lip line region against the skin band
 * directly above it. Both are repeatable; screenshots are the visual evidence.
 */
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  origin: { type: 'string', default: process.env.AQI_ORIGIN || 'http://127.0.0.1:8010' },
  out: { type: 'string', default: 'eval/avatar-stage-20261017' },
  tag: { type: 'string', default: 'before' },
  model: { type: 'string', default: 'seed-daily-lids' },
  ab: { type: 'boolean', default: false },
} });
const out = values.out;
await mkdir(out, { recursive: true });

const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', e => errors.push(e.message));

async function mock() {
  await page.route('**/api/preferences/proactivity', r => r.fulfill({ json: { timezone: 'Asia/Shanghai', reminder_enabled: false } }));
  for (const endpoint of ['tasks', 'reminders', 'notifications']) {
    await page.route(`**/api/${endpoint}`, r => r.fulfill({ json: { items: [] } }));
  }
  await page.route('**/api/memories', r => r.fulfill({ json: { count: 0, items: [] } }));
  await page.route('**/api/executive/commissions', r => r.fulfill({ json: { items: [] } }));
  await page.route('**/api/executive/telemetry', r => r.fulfill({ json: { window_seconds: 86400, calls: 0, succeeded: 0, failed: 0, success_rate: null, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, avg_ttft_ms: 0, max_ttft_ms: 0, by_agent: [], token_ranking: [], spinning_alerts: [] } }));
  await page.route('**/api/executive/rubrics', r => r.fulfill({ json: { items: [] } }));
  await page.routeWebSocket('**/ws', socket => socket.onMessage(() => {}));
}

/* Installed once. Wraps the real render call and keeps the last frame's pixels
 * so measurements read the same buffer the user would have seen. */
async function installProbe() {
  await page.evaluate(async () => {
    // The page's own module instance: same resolved URL, so this is the singleton
    // the chat loop is already driving.
    if (!window.__avatar) window.__avatar = (await import('/static/avatar/index.js')).avatar;
    const a = window.__avatar, s = a?.svg?._state;
    if (!s?.renderer || window.__stageProbe) return;
    const gl = s.renderer.domElement;
    const copy = document.createElement('canvas');
    copy.width = gl.width; copy.height = gl.height;
    const ctx = copy.getContext('2d', { willReadFrequently: true });
    const render = s.renderer.render.bind(s.renderer);
    s.renderer.render = (scene, camera) => {
      render(scene, camera);
      if (copy.width !== gl.width || copy.height !== gl.height) { copy.width = gl.width; copy.height = gl.height; }
      ctx.clearRect(0, 0, copy.width, copy.height);
      ctx.drawImage(gl, 0, 0);
      window.__stageProbe = { data: ctx.getImageData(0, 0, copy.width, copy.height).data,
        width: copy.width, height: copy.height, frame: (window.__stageProbe?.frame || 0) + 1 };
    };
    s.renderer.render.__probe = true;
    /* The page loop keeps running; freeze idle noise so both captures are comparable. */
    a.idle.cfg.energy.drain_per_sec = 0; a.idle.cfg.energy.drain_speaking = 0;
    a.idle.energy = 1; a.idle.nextBlink = 1e9; a.gesture.nextIdleLook = 1e9;
    a.setGazeMode('forward');
  });
}

/* The page can restyle the stage asynchronously (the almanac plate). Two equal
 * reads mean the box has settled and the camera has a stable aspect. */
async function stableStage() {
  let last = null;
  for (let i = 0; i < 40; i++) {
    const box = await page.evaluate(() => {
      const r = document.querySelector('#avatarStage')?.getBoundingClientRect();
      return r ? [Math.round(r.width), Math.round(r.height)] : null;
    });
    if (last && box && box[0] === last[0] && box[1] === last[1]) return box;
    last = box;
    await page.waitForTimeout(250);
  }
  return last;
}

async function measure() {  return page.evaluate(() => {
    const a = window.__avatar, s = a?.svg?._state;
    if (!s?.camera) return null;
    const V3 = s.camera.position.constructor;
    const canvas = s.renderer.domElement;
    const rect = canvas.getBoundingClientRect();
    const probe = window.__stageProbe;
    const scale = probe ? probe.width / rect.width : 1;
    const project = (x, y, z, matrix) => {
      const v = new V3(x, y, z).applyMatrix4(matrix).project(s.camera);
      return { x: (v.x + 1) / 2 * rect.width, y: (1 - v.y) / 2 * rect.height };
    };
    let head = null;
    s.vrm.scene.traverse(node => {
      if (!head && node.geometry && node.morphTargetDictionary?.lip_a !== undefined) head = node;
    });
    if (!head) return { error: 'head mesh with lip_a not found' };
    head.updateWorldMatrix(true, false);
    const pos = head.geometry.attributes.position;
    let ymin = Infinity, ymax = -Infinity;
    for (let i = 0; i < pos.count; i++) { const y = pos.getY(i); ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); }
    const span = ymax - ymin;
    const band = (lo, hi, { front = false, halfWidth = null } = {}) => {
      const points = [];
      for (let i = 0; i < pos.count; i++) {
        const t = (pos.getY(i) - ymin) / span;
        if (t < lo || t > hi) continue;
        if (front && pos.getZ(i) < 0.06) continue;
        if (halfWidth !== null && Math.abs(pos.getX(i)) > halfWidth) continue;
        points.push(project(pos.getX(i), pos.getY(i), pos.getZ(i), head.matrixWorld));
      }
      return points;
    };
    const eye = band(0.50, 0.58);
    const xs = eye.map(p => p.x);
    const faceWidth = Math.max(...xs) - Math.min(...xs);

    const lipIndex = head.morphTargetDictionary.lip_a;
    const targets = head.geometry.morphAttributes.position?.[lipIndex];
    let mouth = null;
    if (targets) {
      let mMinX = Infinity, mMaxX = -Infinity, mMinY = Infinity, mMaxY = -Infinity;
      for (let i = 0; i < targets.count; i++) {
        const d = Math.hypot(targets.getX(i), targets.getY(i), targets.getZ(i));
        if (d < 1e-6) continue;
        const p = project(pos.getX(i), pos.getY(i), pos.getZ(i), head.matrixWorld);
        mMinX = Math.min(mMinX, p.x); mMaxX = Math.max(mMaxX, p.x);
        mMinY = Math.min(mMinY, p.y); mMaxY = Math.max(mMaxY, p.y);
      }
      mouth = { x: mMinX, y: mMinY, width: mMaxX - mMinX, height: mMaxY - mMinY };
    }

    const luminance = (data, i) => {
      const lin = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * lin(data[i]) + 0.7152 * lin(data[i + 1]) + 0.0722 * lin(data[i + 2]);
    };
    const stats = (region, cut = null) => {
      if (!probe || !region) return null;
      const values = [];
      const x0 = Math.max(0, Math.floor(region.x * scale)), x1 = Math.min(probe.width, Math.ceil((region.x + region.width) * scale));
      const y0 = Math.max(0, Math.floor(region.y * scale)), y1 = Math.min(probe.height, Math.ceil((region.y + region.height) * scale));
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) {
        const i = (y * probe.width + x) * 4;
        if (probe.data[i + 3] < 128) continue;
        values.push(luminance(probe.data, i));
      }
      values.sort((p, q) => p - q);
      return {
        median: values.length ? values[Math.floor(values.length / 2)] : null,
        min: values.length ? values[0] : null,
        p10: values.length ? values[Math.floor(values.length * 0.1)] : null,
        pixels: values.length,
        dark: cut == null ? null : values.filter(v => v < cut).length,
      };
    };
    const skin = mouth ? stats({ x: mouth.x, y: mouth.y - mouth.height * 1.6, width: mouth.width, height: mouth.height * 1.2 }) : null;
    const lips = mouth ? stats(mouth, skin?.median ? skin.median * 0.82 : null) : null;
    const forehead = stats(regionFrom(band(0.66, 0.73, { front: true, halfWidth: 0.06 })));
    const cheek = stats(regionFrom(band(0.45, 0.52, { front: true, halfWidth: 0.06 })));
    function regionFrom(points) {
      if (!points?.length) return null;
      const px = points.map(p => p.x), py = points.map(p => p.y);
      const x = Math.min(...px), y = Math.min(...py);
      return { x, y, width: Math.max(...px) - x, height: Math.max(...py) - y };
    }
    const fg = s.vrm.scene.getObjectByProperty?.('isMesh', true);
    return {
      probeFrame: probe?.frame ?? null,
      faceWidth: +faceWidth.toFixed(1),
      headHeight: +span.toFixed(4),
      mouth: mouth && { width: +mouth.width.toFixed(1), height: +mouth.height.toFixed(1) },
      mouthMedian: lips?.median == null ? null : +lips.median.toFixed(4),
      mouthP10: lips?.p10 == null ? null : +lips.p10.toFixed(4),
      mouthPixels: lips?.pixels ?? null,
      skinMedian: skin?.median == null ? null : +skin.median.toFixed(4),
      mouthContrast: lips?.median && skin?.median ? +(skin.median / lips.median).toFixed(3) : null,
      darkLipPixels: lips?.dark ?? null,
      foreheadMedian: forehead?.median == null ? null : +forehead.median.toFixed(4),
      cheekMedian: cheek?.median == null ? null : +cheek.median.toFixed(4),
      foreheadRatio: forehead?.median && cheek?.median ? +(forehead.median / cheek.median).toFixed(3) : null,
      camera: {
        position: s.camera.position.toArray().map(v => +v.toFixed(3)),
        distance: +s.camera.position.distanceTo(s.camera.position.clone().set(s.dolly?.focus.x ?? 0, s.dolly?.focus.y ?? 0, s.dolly?.focus.z ?? 0)).toFixed(3),
        stageBlend: Number.isFinite(s.stageBlend) ? +s.stageBlend.toFixed(3) : null,
        stageMode: s.stageMode || null,
      },
      stage: (() => { const r = document.querySelector('#avatarStage').getBoundingClientRect();
        return { width: Math.round(r.width), height: Math.round(r.height), x: Math.round(r.x), y: Math.round(r.y) }; })(),
      fg: !!fg,
    };
  });
}

async function capture(view, state) {
  const stage = page.locator('#avatarStage');
  // Viewport-size evidence: the page layout can be taller than the viewport and
  // a full-page stitch says nothing about the 1440x1000 composition.
  await page.screenshot({ path: `${out}/${values.tag}-${view}-${state}.png` });
  await stage.screenshot({ path: `${out}/${values.tag}-${view}-stage-${state}.png` });
  return measure();
}

/* Paired camera comparison on the same DOM, lighting and expression: the old
 * fixed framing (state.stage removed) versus the two-state stage camera. */
async function abCamera() {
  await page.evaluate(() => {
    const a = window.__avatar;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.stopSpeaking();
    window.__abStage = a.svg._state.stage;
    window.__abCamera = { enter: a.svg.stageCamera.cfg, blend: 0 };
  });
  const results = {};
  for (const arm of ['before-idle', 'before-speaking', 'after-idle', 'after-speaking']) {
    const before = arm.startsWith('before'), speaking = arm.endsWith('speaking');
    await page.evaluate(({ before, speaking }) => {
      const a = window.__avatar, s = a.svg._state;
      s.stage = before ? null : window.__abStage;
      const cam = a.svg.stageCamera;
      cam.target = speaking ? 1 : 0;
      cam.blend = speaking ? 1 : 0;
      for (let i = 0; i < 60; i++) {
        if (!before) { cam.target = speaking ? 1 : 0; cam.blend = speaking ? 1 : 0; }
        a._frame(1 / 60);
        if (before) a.svg._frameCamera(s);
      }
    }, { before, speaking });
    await page.waitForTimeout(200);
    await page.locator('#avatarStage').screenshot({ path: `${out}/${values.tag}-camera-${arm}-stage.png` });
    results[arm] = await measure();
  }
  return results;
}

async function speak() {
  await page.evaluate(async () => {
    const a = window.__avatar;
    a.stopSpeaking();
    const clock = { currentTime: 100, state: 'running' };
    a.audioCtx = clock;
    a.speak('今天想和你一起，把这件事慢慢做完。', clock, 100, 8);
    const { sampleTimeline } = await import('/static/avatar/timeline.js');
    // Hold the clock at the widest vowel so the lip line is measurable.
    let best = 0, at = 1;
    for (let t = 0.05; t < 7.9; t += 0.02) {
      const open = sampleTimeline(a.lip.timeline, t)?.open || 0;
      if (open > best) { best = open; at = t; }
    }
    clock.currentTime = 100 + at;
    window.__stageClock = clock;
    window.__stageVowel = { at: +at.toFixed(2), open: +best.toFixed(2) };
  });
  await page.waitForFunction(() => window.__stageVowel?.open > 0.2, {}, { timeout: 5000 });
  await page.waitForTimeout(1800);
}
async function quiet() {
  await page.evaluate(() => { window.__avatar.stopSpeaking(); });
  await page.waitForTimeout(1600);
}

/* Same frozen close-up frame, only the resting expression differs. Isolates
 * the mouth change from camera and lighting, which are shared by both arms. */
async function abExpression() {
  await page.evaluate(() => {
    const a = window.__avatar;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.stopSpeaking();
    a.svg.stageCamera.blend = 1; a.svg.stageCamera.target = 1; a.svg.stageCamera.mode = 'speaking';
    window.__abAfter = { shift: { ...a.emotion.table.base.shift }, morphs: (a.svg._state.stageMorphs || []).slice() };
  });
  const results = {};
  for (const variant of ['before', 'after']) {
    await page.evaluate(variant => {
      const a = window.__avatar, s = a.svg._state;
      const after = variant === 'after';
      a.emotion.table.base.shift = after ? { ...window.__abAfter.shift } : { ParamMouthForm: 0.18 };
      a.emotion.k = 1; a.emotion.springValue = 1; a.emotion.spring.reset(0);
      s.stageMorphs = after ? window.__abAfter.morphs : [];
      // Converge the parameter store without letting the stage camera ease out.
      for (let i = 0; i < 90; i++) {
        a.svg.stageCamera.blend = 1; a.svg.stageCamera.target = 1;
        a._frame(1 / 60);
      }
    }, variant);
    await page.waitForTimeout(250);
    await page.locator('#avatarStage').screenshot({ path: `${out}/${values.tag}-ab-${variant}-stage.png` });
    results[variant] = await measure();
  }
  return results;
}

try {
  await mock();
  await page.goto(`${values.origin}/?avatar=${values.model}`);
  await page.waitForSelector('#avatarStage canvas', { timeout: 120000 });
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'), {}, { timeout: 120000 });
  await page.waitForTimeout(3000);
  await installProbe();
  await quiet();
  await stableStage();

  const metrics = {};
  metrics['1440-idle'] = await capture('1440', 'idle');
  await speak();
  metrics['1440-speaking'] = await capture('1440', 'speaking');
  await quiet();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(1500);
  await stableStage();
  metrics['390-idle'] = await capture('390', 'idle');
  await speak();
  metrics['390-speaking'] = await capture('390', 'speaking');
  await quiet();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.waitForTimeout(800);
  await stableStage();
  metrics.abCamera = await abCamera();
  if (values.ab) metrics.abExpression = await abExpression();

  const payload = { capturedAt: new Date().toISOString(), origin: values.origin, tag: values.tag, model: values.model, metrics, errors };
  await writeFile(`${out}/metrics-${values.tag}.json`, JSON.stringify(payload, null, 2));
  console.log(JSON.stringify(payload, null, 2));
} finally {
  await browser.close();
}
