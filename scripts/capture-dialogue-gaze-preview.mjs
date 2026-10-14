/* Visual evidence for the public laboratory workflows. Keep ordinary RAF,
 * AudioContext and VRM rendering running; no frozen poses or synthetic clock. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const videoOnly = process.argv.includes('--video-only');
const output = `eval/avatar/dialogue-gaze-preview/${new Date().toISOString().replace(/[:.]/g, '-')}`;
const sourceFiles = ['scripts/capture-dialogue-gaze-preview.mjs', 'web/avatar_lab.js',
  'web/avatar_lab.html', 'web/avatar/index.js', 'web/app.js', 'web/avatar/calibration.js',
  'web/avatar/drivers/dialogue-gaze.js', 'web/avatar/drivers/idle.js', 'config/avatar.yaml'];
const hash = buffer => createHash('sha256').update(buffer).digest('hex');
await mkdir(output + '/sources', {recursive:true});
const sources = [];
for (const file of sourceFiles) {
  const buffer = await readFile(file), destination = output + '/sources/' + file;
  await mkdir(destination.slice(0, destination.lastIndexOf('/')), {recursive:true});
  await writeFile(destination, buffer);
  sources.push({path:file, sha256:hash(buffer)});
}
const model = await readFile('web/models/seed-daily-lids.vrm');
const browser = await chromium.launch({headless:true,
  args:process.platform === 'win32' ? ['--use-angle=d3d11'] : []});
const context = await browser.newContext({viewport:{width:1280,height:920},
  recordVideo:{dir:output + '/video', size:{width:1280,height:920}}});
const page = await context.newPage(), errors = [], frames = [], traces = [];
page.on('pageerror', error => errors.push(error.message));
const sample = () => page.evaluate(() => {
  const a = window.__avatar, frame = a.eyeFrame;
  return {now:performance.now(), audioTime:a.audioCtx?.currentTime,
    dialogue:{...a.dialogueFrame}, gazeMode:a.gazeMode,
    params:{...frame?.params}, blink:frame?.blink, lip:a.lip.speaking,
    status:document.querySelector('#dialogueStatus').textContent};
});
let metadata;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1');
  await page.waitForFunction(() => window.__avatarReady &&
    window.__avatar.svg.getEyeCapabilities?.().profile === 'seed-lids', {}, {timeout:90000});
  await page.mouse.move(1200, 10);
  metadata = await page.evaluate(() => {
    const a = window.__avatar, gl = a.svg._state.renderer.getContext();
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    return {userAgent:navigator.userAgent, viewport:{width:innerWidth,height:innerHeight},
      renderer:debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      cfg:{...a.dialogueGaze.cfg}, reducedMotion:a.reducedMotion};
  });
  await page.evaluate(() => {
    window.__dialogueCaptureTrace = [];
    let previous = -Infinity;
    const observe = now => {
      const a = window.__avatar;
      if (now - previous >= 80) {
        window.__dialogueCaptureTrace.push({now,audioTime:a.audioCtx?.currentTime,
          state:a.dialogueFrame?.state,phase:a.dialogueFrame?.phase,
          owner:a.dialogueFrame?.owner, x:a.eyeFrame?.params.ParamEyeBallX,
          y:a.eyeFrame?.params.ParamEyeBallY, mouth:a.eyeFrame?.params.ParamMouthOpenY,
          headX:a.eyeFrame?.params.ParamAngleX,headY:a.eyeFrame?.params.ParamAngleY,
          blink:a.eyeFrame?.blink});
        previous = now;
      }
      window.__dialogueCaptureRAF = requestAnimationFrame(observe);
    };
    window.__dialogueCaptureRAF = requestAnimationFrame(observe);
  });
  const workflows = [
    {kind:'normal', times:[.8,2.58,3.02,3.65,4.6,5.9,8.65,10.6]},
    {kind:'delay', times:[.8,3.02,4.6,7.1,8.5,11.3,13.1]},
    {kind:'interrupt', times:[.8,3.02,4.6,5.9,6.45,6.95,9.0]},
  ];
  for (const {kind,times} of workflows) {
    await page.selectOption('#dialogueExample',kind);
    await page.click('#dialogueDemo');
    await page.waitForFunction(() => window.__avatar.dialogueFrame?.state === 'listening');
    const started = await page.evaluate(() => performance.now());
    for (let index=0;index<times.length;index++) {
      const desired = started + times[index]*1000;
      await page.waitForFunction(desired => performance.now() >= desired, desired);
      const before = await sample(), filename = videoOnly ? null : `${kind}-${String(index).padStart(2,'0')}.png`;
      if (!videoOnly) await page.locator('#viewport').screenshot({path:output+'/'+filename});
      const after = await sample();
      frames.push({kind,index,filename,requestedSeconds:times[index],
        measuredSeconds:(before.now-started)/1000,before,after});
    }
    traces.push({kind,startedAt:started,finishedAt:await page.evaluate(() => performance.now())});
    await page.click('#dialogueStop');
  }
  const trace = await page.evaluate(() => {
    cancelAnimationFrame(window.__dialogueCaptureRAF);
    return window.__dialogueCaptureTrace;
  });
  assert.equal(errors.length,0,errors.join('\n'));
  for (const source of sources)
    assert.equal(hash(await readFile(source.path)),source.sha256,source.path+' changed during capture');
  assert.equal(hash(await readFile('web/models/seed-daily-lids.vrm')),hash(model));
  await writeFile(output+'/results.json',JSON.stringify({capturedAt:new Date().toISOString(),
    purpose:'Ordinary continuous rendering of public silent-mouth demos; visual QA, not an FPS benchmark.',
    captureMode:videoOnly ? 'video-without-concurrent-screenshots' : 'video-with-timed-screenshots',
    browserVersion:browser.version(),url:page.url(),metadata,sources,
    model:{path:'web/models/seed-daily-lids.vrm',bytes:model.length,sha256:hash(model)},
    video:'continuous.webm',frames,workflows:traces,trace,errors},null,2));
} catch (error) {
  await writeFile(output+'/failure.json',JSON.stringify({error:error.stack,errors,frames},null,2));
  throw error;
} finally {
  const video = page.video();
  await page.close();
  if (video) await video.saveAs(output+'/continuous.webm');
  await context.close(); await browser.close();
}
console.log(JSON.stringify({output,frames:frames.length,errors},null,2));
