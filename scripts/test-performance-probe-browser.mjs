import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const gpuMode = process.argv[2] || 'auto';
assert.ok(['auto', 'software', 'd3d11'].includes(gpuMode), 'mode must be auto, software or d3d11');
const args = gpuMode === 'software' ? ['--use-angle=swiftshader', '--enable-unsafe-swiftshader']
  : gpuMode === 'd3d11' ? ['--use-angle=d3d11', '--disable-software-rasterizer'] : [];
const output = `eval/avatar/performance-probe/${new Date().toISOString().replace(/[:.]/g, '-')}-${process.pid}-${gpuMode}`;
await mkdir(output, { recursive: true });
const checks = [], errors = [];
const browser = await chromium.launch({ headless: true, args });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
page.on('pageerror', error => errors.push(error.message));
const check = (ok, label) => { assert.ok(ok, label); checks.push(label); };
try {
  await page.goto('http://127.0.0.1:8000/static/avatar_lab.html?model=daily');
  await page.waitForFunction(() => window.__avatarReady && window.__avatar?.rendererKind === 'vrm', {}, { timeout: 90000 });
  const data = await page.evaluate(async () => {
    const { AvatarPerformanceProbe } = await import('/static/avatar/performance-probe.js');
    const a = window.__avatar, renderer = a.svg._state.renderer, originalFrame = a._frame, originalRender = renderer.render;
    const actualRafTimestamps = [];
    const monitor = function (...args) { actualRafTimestamps.push(this._last); return originalFrame.apply(this, args); };
    a._frame = monitor;
    const probe = new AvatarPerformanceProbe(a).start({ durationMs: 1200, name: 'normal-daily' });
    const result = await probe.complete();
    const restored = a._frame === monitor && renderer.render === originalRender;
    a._frame = originalFrame;
    return { result, restored, actualRafTimestamps, started: probe.started, clock: a.clock };
  });
  check(data.result.avatarFrames.length > 2 && data.result.rafIntervalsMs.length === data.result.avatarFrames.length - 1, 'actual normal loop generated interval and frame samples');
  check(data.result.avatarFrames.every(frame => frame.render.calls > 0 && frame.render.triangles > 0), 'sampled frames submitted character geometry');
  check(data.result.avatarFrames.every((frame, i) => Math.abs(frame.rafTimestampMs + data.started - data.actualRafTimestamps[i]) < 1e-5), 'interval source matches production Avatar._last rAF timestamps');
  check(data.result.avatarFrames.every(frame => frame.cpuWallMs >= 0 && frame.renderSubmissions.length === 1), 'CPU samples wrap actual single renderer.render submission');
  check(data.restored, 'automatic completion restores original frame and renderer methods');
  check(data.result.environment.hardwareVerified === false, 'renderer identity is reported without asserting hardware verification');
  check(data.result.measurement.gpuTimeMeasured ? data.result.gpuFrames.length > 0 && data.result.measurement.gpu.reason === null
    : !!data.result.measurement.gpu.reason, 'GPU results have valid samples or explicit unavailable reason');
  await page.waitForTimeout(250);
  check(await page.evaluate(clock => window.__avatar.clock > clock, data.clock), 'normal animation continues after completion');
  await page.evaluate(async () => {
    const { AvatarPerformanceProbe } = await import('/static/avatar/performance-probe.js');
    const a = window.__avatar;
    window.__probeOriginalFrame = a._frame; window.__probeOriginalRender = a.svg._state.renderer.render;
    window.__probe = new AvatarPerformanceProbe(a).start({ durationMs: 10000, name: 'early-stop' });
  });
  await page.waitForTimeout(250);
  const stopped = await page.evaluate(async () => {
    const result = await window.__probe.stop('browser-test-stop');
    return { result, restored: window.__avatar._frame === window.__probeOriginalFrame
      && window.__avatar.svg._state.renderer.render === window.__probeOriginalRender };
  });
  check(stopped.result.stopReason === 'browser-test-stop' && stopped.result.elapsedMs < 2000 && stopped.restored, 'manual stop restores methods before requested duration');
  const changed = await page.evaluate(async () => {
    const { AvatarPerformanceProbe } = await import('/static/avatar/performance-probe.js');
    const a = window.__avatar, oldRenderer = a.svg._state.renderer, originalFrame = a._frame, originalRender = oldRenderer.render;
    const probe = new AvatarPerformanceProbe(a).start({ durationMs: 10000, name: 'renderer-switch' });
    await a.setRenderer('svg');
    const result = await probe.complete();
    return { result, restored: a._frame === originalFrame && oldRenderer.render === originalRender };
  });
  check(changed.result.stopReason === 'renderer-changed' && changed.restored, 'renderer swap stops sample and restores old renderer method');
  check(errors.length === 0, 'no browser page errors');
  await writeFile(`${output}/results.json`, JSON.stringify({ ok: true, checks, errors, browser: { version: browser.version(), headless: true, requestedGpuMode: gpuMode, launchArgs: args }, normal: data.result, stopped: stopped.result, changed: changed.result }, null, 2));
  console.log(`${checks.length} browser checks passed; ${output}`);
  console.log(JSON.stringify({ environment: data.result.environment, gpu: data.result.measurement.gpu, summary: data.result.summary }, null, 2));
} catch (error) {
  await writeFile(`${output}/failure.json`, JSON.stringify({ message: error.message, checks, errors, requestedGpuMode: gpuMode, launchArgs: args }, null, 2));
  throw error;
} finally { await browser.close(); }
