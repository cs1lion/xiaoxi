// Offline DOM/export regression for the independent trial panel. Synthetic
// metadata only; no microphone, ASR/TTS/model provider, or application server.
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

const projectRoot = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage(); const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://baseline.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<!doctype html><html lang="zh"><body><main id="baseline"></main><script type="module">
      import { mountVoiceBaseline } from './voice/baseline-ui.js';
      window.baselineHarness = { context: {mode:'speaker',captureKind:'none',activity:'off',endpointWaitMs:900,referenceAvailable:true} };
      baselineHarness.collector = mountVoiceBaseline({root:document.querySelector('#baseline'),getContext:()=>baselineHarness.context});
      baselineHarness.tokens=[];
    </script></body></html>` });
    const name = pathname === '/voice/baseline.js' ? 'baseline.js' : pathname === '/voice/baseline-ui.js' ? 'baseline-ui.js' : null;
    if (!name) return route.abort();
    return route.fulfill({ contentType: 'text/javascript', body: await readFile(path.join(projectRoot, 'web', 'voice', name)) });
  });
  await page.goto('http://baseline.test/');
  await page.locator('[data-voice-baseline="panel"]').waitFor();
  await page.locator('summary').click();
  const control = name => page.locator(`[data-baseline="${name}"]`);
  assert.equal(await control('status').textContent(), '采集关闭 · 尚无试验');
  await control('start').click();
  assert.match(await control('status').textContent(), /请先开启/);
  assert.equal(await page.evaluate(() => baselineHarness.collector.exportData().trials.length), 0);
  console.log('PASS: default-off panel requires ready capture and records no pre-trial metadata');

  await page.evaluate(() => { baselineHarness.context.captureKind = 'worklet'; });
  await control('scenario').selectOption('speaker_interrupt_short'); await control('start').click();
  assert.equal(await control('scenario').isDisabled(), true); assert.equal(await control('device').isDisabled(), true);
  await page.evaluate(() => {
    const c=baselineHarness.collector, token=c.captureToken(); baselineHarness.tokens.push(token);
    const at=performance.now(), turn_id='raw-sensitive-turn';
    c.record({type:'turn_begin',turn_id,at,meta:{input:'voice_auto',text:'sensitive-transcript'}},{token});
    c.record({type:'speech_last_energy',turn_id,at:at+100},{token});
    c.record({type:'audio_submit',turn_id,at:at+1000,meta:{input:'voice_auto'}},{token});
    c.record({type:'asr_received',turn_id,at:at+1100,meta:{empty:false}},{token});
    c.record({type:'playback_start',turn_id,at:at+1500,meta:{durationMs:1000}},{token});
  });
  await control('interruption').selectOption('missed'); await control('recognition').selectOption('needs_correction');
  await control('seam').selectOption('gap'); await control('finish').click();
  assert.match(await control('results').textContent(), /漏检 1 次/);
  assert.match(await control('timings').textContent(), /末次能量→播放调度：p50 1400ms \/ p95 1400ms（1 个代理样本）/);
  assert.equal(await control('start').isDisabled(), false);
  console.log('PASS: trial locks environment, collects scoped proxies and preserves manual outcomes');

  await control('start').click();
  await page.evaluate(() => {
    const c=baselineHarness.collector;
    if(c.record({type:'decode_end',turn_id:'old',at:performance.now()},{token:baselineHarness.tokens[0]})) throw Error('late callback entered newer trial');
    c.setContext({mode:'immediate'});
  });
  assert.match(await control('status').textContent(), /无效.*策略变化/);
  assert.equal(await control('start').isDisabled(), false);
  console.log('PASS: late callbacks are isolated and changed policy invalidates a fixed-context trial');

  const downloadPromise = page.waitForEvent('download'); await control('export').click();
  const download = await downloadPromise;
  const exportedText = await readFile(await download.path(), 'utf8');
  const exported = JSON.parse(exportedText);
  assert.equal(download.suggestedFilename(), 'voice-baseline-metadata.json');
  assert.equal(exported.summary.trials.complete, 1); assert.equal(exported.summary.trials.invalid, 1);
  assert.equal(exported.physicalAcousticMetrics.status, 'not_measured');
  for (const forbidden of ['raw-sensitive-turn','sensitive-transcript','turn_id','startedAt','trialToken']) assert.equal(exportedText.includes(forbidden), false);
  assert.equal(exported.trials[0].setup.echoCancellation, null);
  assert.equal(exported.trials[0].outcomes.recognition, 'needs_correction');
  console.log('PASS: download contains whitelisted metadata and marks physical metrics unmeasured');

  await control('clear').click();
  assert.equal(await control('export').isDisabled(), true);
  assert.equal(await page.evaluate(() => baselineHarness.collector.exportData().trials.length), 0);
  assert.deepEqual(errors, []);
  console.log('PASS: clear erases trial metadata and DOM panel has no browser exceptions');
} finally {
  await browser.close();
}
