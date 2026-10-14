/* Dialogue intent + opt-in turn review evidence. The VRM, Avatar, application
 * and review UI are production code. Only chat transport/audio devices are
 * substituted, so no external service or TTS request is needed. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const output = `eval/avatar/dialogue-intent-review/${new Date().toISOString().replace(/[:.]/g, '-')}`;
const faceKeys = ['ParamBrowLY', 'ParamBrowRY', 'ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower'];
const eyes = ['ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower'];
const sourceFiles = ['web/avatar/index.js', 'web/avatar/calibration.js',
  'web/avatar/drivers/dialogue-gaze.js', 'web/avatar/drivers/dialogue-expression.js',
  'web/avatar/drivers/dialogue-intent.js', 'web/avatar/drivers/idle.js',
  'web/avatar/dialogue-review.js', 'web/avatar/dialogue-review-ui.js', 'web/avatar/dialogue-review.css',
  'web/avatar/drivers/emotion.js', 'web/avatar/drivers/gesture.js',
  'web/avatar/render/vrm.js', 'web/avatar/render/eye-morph.js',
  'web/avatar_lab.js', 'web/avatar_lab.html', 'web/app.js', 'web/index.html',
  'web/models/seed-daily-lids.vrm', 'scripts/test-dialogue-intent-review-browser.mjs'];
await mkdir(output, { recursive: true });
const sourceHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async path => {
  const bytes = await readFile(path); return [path, { bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex') }];
})));
await writeFile(output + '/source-hashes.json', JSON.stringify(sourceHashes, null, 2));
const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const checks = [], errors = [], evidence = { sourceHashes, intents: {}, cases: [] };
page.on('pageerror', error => errors.push(`lab: ${error.message}`));
const check = (condition, label) => { assert.ok(condition, label); checks.push(label); };
const zero = values => Object.values(values || {}).every(value => Math.abs(value) < 1e-8);

async function ready(profile = 'seed-lids') {
  await page.waitForFunction(profile => window.__avatarReady &&
    window.__avatar?.svg.getEyeCapabilities?.().profile === profile,
  profile, { timeout: 90000 });
}
async function snap(target = page, name = '__avatar') {
  return target.evaluate(name => {
    const a = window[name], s = a.svg._state, native = {};
    s?.vrm?.scene.traverse(mesh => {
      for (const [key, index] of Object.entries(mesh.morphTargetDictionary || {}))
        native[key.slice(key.lastIndexOf('.') + 1)] ??= mesh.morphTargetInfluences[index];
    });
    return { director: a.dialogueGaze.sample(), dialogue: a.dialogueFrame,
      expression: a.dialogueExpressionFrame, target: { ...a.store.target },
      params: { ...a.eyeFrame?.params }, native, blink: a.eyeFrame?.blink,
      head: { x: a.idle.headX, y: a.idle.headY, context: { ...a.idle.gazeContext } },
      gaze: { mode: a.gazeMode, name: a.idle.targetName, anchor: { ...a.idle.customAnchor } },
      vowels: ['aa', 'ih', 'ou', 'ee', 'oh'].map(key => s?.vrm?.expressionManager.getValue(key) || 0),
      clips: a.dialogueGaze._clips?.map(clip => ({ ...clip })), lip: a.lip.speaking,
      preview: a.eyeExpression.sample().active, gesture: a.gesture.active?.name || null };
  }, name);
}
async function freeze() {
  await page.evaluate(() => {
    const a = window.__avatar; cancelAnimationFrame(a._raf); a._raf = 0;
    a.idle.cfg.energy.drain_per_sec = 0; a.idle.cfg.energy.drain_speaking = 0;
    a.idle.energy = 1; a.idle.nextBlink = 1e6; a.gesture.nextIdleLook = 1e6;
    window.__intentTracks = { ...a.gesture.tracks }; window.__intentEmotions = { ...a.emotion.table };
    window.__intentFaceApply = a.face.apply; window.__intentDiscourseUpdate = a.discourseGesture.update;
  });
}
async function advance(seconds, { audio = true } = {}) {
  return page.evaluate(({ seconds, audio }) => {
    const a = window.__avatar, s = a.svg._state, render = s?.renderer.render, trace = [];
    if (render) s.renderer.render = () => {};
    try {
      const count = Math.ceil(seconds * 60), dt = seconds / count;
      for (let i = 0; i < count; i++) {
        if (audio && window.__intentAudio) window.__intentAudio.currentTime += dt;
        if (Number.isFinite(window.__intentReviewClock)) window.__intentReviewClock += dt * 1000;
        a.clock += dt; a._frame(dt);
        trace.push({ state: a.dialogueFrame.state, phase: a.dialogueFrame.phase,
          age: a.dialogueFrame.age, intent: a.dialogueFrame.intent, glance: a.dialogueFrame.glance,
          expression: a.dialogueExpressionFrame, headX: a.idle.headX, headY: a.idle.headY,
          x: a.eyeFrame.params.ParamEyeBallX, y: a.eyeFrame.params.ParamEyeBallY });
      }
    } finally {
      if (render) { s.renderer.render = render; render.call(s.renderer, s.scene, s.camera); }
    }
    return trace;
  }, { seconds, audio });
}
const event = message => page.evaluate(message => window.__avatar.onMessage(message), message);
async function record(name) { const value = await snap(); evidence.cases.push({ name, ...value }); return value; }
async function reset(intent = 'auto') {
  await page.evaluate(intent => {
    const a = window.__avatar;
    window.__dialogueReview.stop('test-reset');
    a.onBargeIn(); a.gesture.setTracks(window.__intentTracks); a.gesture.nextIdleLook = 1e6;
    a.face.apply = window.__intentFaceApply; a.face.setMimicryMode(false);
    a.discourseGesture.update = window.__intentDiscourseUpdate;
    a.emotion.setTable(window.__intentEmotions); a.setEmotion('base'); a.setEyeExpression(null);
    a.setReducedMotion(false); a.setDialogueGazeEnabled(true); a.setDialogueStyle('natural');
    a.setDialogueIntent(intent); a.dialogueGaze.resetVariation('intent-browser-same-seed');
    a.dialogueGaze.stop('test-reset'); a.setDialogueState('idle'); a.setGazeMode('natural');
    a.idle.setGaze('viewer', true); a.idle.claimGaze(false);
    a.idle.blinkPhase = -1; a.idle.blinkHold = 0; a.idle.eyeLevel = 1; a.idle.nextBlink = 1e6;
    window.__intentAudio = { currentTime: 100, state: 'running' };
    a.attachOutputAnalyser(null); a.setAudioContext(window.__intentAudio);
  }, intent);
  await advance(.8);
}
async function speak(duration = 3, offset = 0) {
  await page.evaluate(({ duration, offset }) => {
    const a = window.__avatar, ctx = window.__intentAudio;
    a.speak('我们可以慢慢看，再决定怎么做。', ctx, ctx.currentTime + offset, duration,
      { performance: { version: 1, source: 'llm', cues: [] } });
  }, { duration, offset });
}
async function review(target = page) { return target.evaluate(() => window.__dialogueReview.snapshot()); }

async function installChatHarness(chat) {
  await chat.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/avatar') return route.continue();
    return route.fulfill({ json: pathname === '/api/persona'
      ? { name: '测试', tagline: '对话意图复盘', voices: ['base', '认真', '调侃'], default_voice: 'base' }
      : { count: 0, items: [], tasks: [], notes: [] } });
  });
  await chat.addInitScript(() => {
    const h = window.intentHarness = { sent: [], sources: [], now: 1, decodePending: false, decodes: [], duration: 3 };
    class WS {
      static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
      constructor() { h.ws = this; this.readyState = 0; setTimeout(() => {
        this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'intent' });
      }, 0); }
      send(data) { h.sent.push(typeof data === 'string' ? JSON.parse(data) : { binary: true }); }
      emit(message) { return this.onmessage?.({ data: JSON.stringify(message) }); }
      close() { this.readyState = 3; this.onclose?.({}); }
    }
    window.WebSocket = WS;
    const node = () => ({ connect() { return this; }, disconnect() {}, gain: { value: 1 } });
    class AC {
      constructor() { this.state = 'running'; this.sampleRate = 48000; this.destination = node(); }
      get currentTime() { return h.now; }
      resume() { this.state = 'running'; return Promise.resolve(); }
      close() { this.state = 'closed'; return Promise.resolve(); }
      createGain() { return node(); }
      createAnalyser() { return { ...node(), fftSize: 512,
        get frequencyBinCount() { return this.fftSize / 2; },
        getFloatTimeDomainData(a) { a.fill(0); }, getByteFrequencyData(a) { a.fill(0); } }; }
      decodeAudioData() { const buffer = { duration: h.duration, length: Math.ceil(h.duration * 48000), numberOfChannels: 1 };
        return h.decodePending ? new Promise(resolve => h.decodes.push(() => resolve(buffer))) : Promise.resolve(buffer); }
      createBufferSource() { const s = { ...node(), onended: null,
        start() { h.sources.push(this); }, stop() { h.sources = h.sources.filter(item => item !== this); this.onended?.(); } };
        return s; }
    }
    window.AudioContext = AC;
    h.resolveDecode = () => { h.decodePending = false; for (const resolve of h.decodes.splice(0)) resolve(); };
    h.finish = () => { h.now += h.duration + .01; const source = h.sources.shift(); source?.onended?.(); };
  });
}

let chat;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1'); await ready();
  await page.waitForFunction(() => window.__dialogueReview && document.querySelector('#dialogueIntent'));
  check(await page.locator('#dialogueIntent').inputValue() === 'auto'
    && (await snap()).director.style === 'natural', 'laboratory defaults retain automatic neutral intent and natural style');
  check(!(await review()).recording && (await review()).current === null && (await review()).turns.length === 0,
    'fresh laboratory never records a dialogue until the user enables review');
  await freeze();
  for (const intent of ['neutral', 'serious', 'hesitant', 'teasing']) {
    await reset(intent); await event({ type: 'recording', state: true, turn_id: `intent-${intent}` });
    await advance(.7); const listening = await record(`${intent}-listening`);
    await event({ type: 'thinking', turn_id: `intent-${intent}` });
    const plan = (await snap()).director.plan;
    const trace = await advance(1.12), thinking = await record(`${intent}-thinking`);
    check(thinking.dialogue.intent === intent && thinking.expression.intent === intent,
      `${intent}: Avatar resolves and supplies the same intent to gaze and face drivers`);
    check(trace.some(frame => frame.phase === 'averted'), `${intent}: its bounded waiting plan reaches a genuine glance`);
    check(thinking.native.neuro_brow_raise_L > 0 || thinking.native.neuro_brow_raise_R > 0,
      `${intent}: a real calibrated VRM brow morph receives the waiting cue`);
    await page.locator('#viewport').screenshot({ path: `${output}/${intent}-thinking.png` });
    await speak(); const responseTrace = await advance(.85), response = await record(`${intent}-response`);
    check(response.dialogue.state === 'speaking' && response.vowels.some(value => value > 0),
      `${intent}: intent cues preserve actual rendered speech vowels`);
    if (intent === 'serious' || intent === 'hesitant') check(eyes.every(key => response.expression.values[key] === 0),
      `${intent}: automatic smile and lower-lid cues are absent during speech`);
    else check(response.native.neuro_lower_lid_raise_L > 0 && response.native.neuro_lower_lid_raise_R > 0,
      `${intent}: speech eye cue reaches both actual VRM lower-lid targets`);
    if (intent === 'teasing') {
      check(response.expression.values.ParamBrowRY > response.expression.values.ParamBrowLY
        && response.expression.values.ParamEyeRSmile > response.expression.values.ParamEyeLSmile,
      'teasing response retains its modest asymmetric brow and smile');
      check(responseTrace.slice(0, 10).every(frame => eyes.every(key => frame.expression.values[key] < .001)),
        'teasing smile waits after the director requests viewer focus at actual audio onset');
    }
    await page.locator('#viewport').screenshot({ path: `${output}/${intent}-response.png` });
    evidence.intents[intent] = { plan, listening, trace, thinking, responseTrace, response };
  }
  check(evidence.intents.hesitant.trace.find(frame => frame.phase === 'averted').age
    > evidence.intents.neutral.trace.find(frame => frame.phase === 'averted').age + .1,
  'hesitant gaze starts later than the same-seed neutral waiting plan');
  check(evidence.intents.serious.listening.expression.values.ParamBrowLY
    < evidence.intents.neutral.listening.expression.values.ParamBrowLY / 2,
  'serious listening produces substantially smaller automatic brow motion');
  check(evidence.intents.serious.plan.budget === 1 && evidence.intents.hesitant.plan.budget === 2,
    'serious and hesitant waiting budgets are finite and different');

  await reset(); await event({ type: 'thinking', turn_id: 'generated-before-audio' });
  await event({ type: 'text_delta', turn_id: 'generated-before-audio', delta: '调侃？犹豫？认真？' });
  await event({ type: 'done', turn_id: 'generated-before-audio', text: '调侃？犹豫？认真？' });
  await advance(.2); const generated = await record('text-does-not-infer-intent');
  check(generated.dialogue.intent === 'neutral' && generated.dialogue.state !== 'speaking',
    'text contents and generated completion neither infer intent nor start speech');
  await page.evaluate(() => window.__avatar.setEmotion('认真')); await advance(.1);
  check((await snap()).dialogue.intent === 'serious', 'auto maps the existing structured serious emotion');
  await page.evaluate(() => window.__avatar.setEmotion('调侃')); await advance(.1);
  check((await snap()).dialogue.intent === 'teasing', 'auto maps the existing structured teasing emotion');
  await page.evaluate(() => window.__avatar.setEmotion('收敛')); await advance(.1);
  check((await snap()).dialogue.intent === 'neutral', 'other structured emotions retain neutral presentation intent');

  await reset('teasing'); await event({ type: 'thinking', turn_id: 'delayed-audio' });
  await speak(2, 1); await advance(.65);
  const delayed = await record('scheduled-audio-not-playing');
  check(delayed.dialogue.state !== 'speaking' && eyes.every(key => delayed.expression.values[key] === 0),
    'a scheduled but inaudible clip cannot trigger the teasing smile');
  await advance(.5); const onset = await record('actual-audio-onset');
  check(onset.dialogue.state === 'speaking' && eyes.every(key => onset.expression.values[key] < .001),
    'actual delayed audio switches to speaking before its later smile cue');
  await advance(.65); const started = await record('actual-audio-teasing-cue');
  check(started.expression.values.ParamEyeRSmile > 0, 'the delayed smile activates only after actual audio time advances');
  const livePlan = started.director.plan, clips = JSON.stringify(started.clips);
  await page.evaluate(() => window.__avatar.setDialogueIntent('serious')); await advance(.1);
  const changed = await record('live-intent-change');
  check(changed.dialogue.intent === 'serious' && zero(Object.fromEntries(eyes.map(key => [key, changed.expression.values[key]])))
    && JSON.stringify(changed.clips) === clips && JSON.stringify(changed.director.plan.glances) === JSON.stringify(livePlan.glances),
  'live intent selection changes current cues without cancelling speech or rerolling plan timing');

  await reset('teasing'); await event({ type: 'thinking', turn_id: 'interrupt-cue' }); await speak(2, .5);
  await advance(.4); await page.evaluate(() => window.__avatar.onBargeIn()); await advance(2);
  const interrupted = await record('cancel-pending-cue');
  check(interrupted.clips.length === 0 && !interrupted.lip && zero(interrupted.expression.appliedValues),
    'barge-in invalidates delayed intent cue and audio gates');

  for (const owner of ['emotion', 'gesture']) {
    await reset('teasing'); await event({ type: 'recording', state: true, turn_id: `zero-${owner}` });
    await page.evaluate(({ owner, keys }) => {
      const a = window.__avatar, values = Object.fromEntries(keys.map(key => [key, 0]));
      if (owner === 'emotion') { a.emotion.setTable({ ...a.emotion.table,
        intent_zero: { arousal: 1, valence: 0, blend: .1, shift: values } }); a.setEmotion('intent_zero'); }
      else { a.gesture.setTracks({ ...a.gesture.tracks,
        intent_zero: { priority: 100, cooldown: 0, hold: true, keys: [{ at: 0, dur: .1, params: values }] } });
        a.gesture.play('intent_zero', a.clock); }
    }, { owner, keys: faceKeys });
    await speak(); await advance(.85); const value = await record(`explicit-zero-${owner}`);
    check(faceKeys.every(key => value.expression.protectedKeys.includes(key)
      && Math.abs(value.expression.appliedValues[key] || 0) < 1e-9),
    `${owner}: explicit zero still protects every intent cue channel`);
  }
  await reset('teasing'); await event({ type: 'recording', state: true, turn_id: 'preview-owner' });
  await page.evaluate(() => window.__avatar.setEyeExpression('happy')); await speak(); await advance(.9);
  check((await snap()).preview && zero((await snap()).expression.appliedValues),
    'manual eye preview protects intent channels while actual speech continues');
  await reset('hesitant'); await event({ type: 'thinking', turn_id: 'gaze-owner' });
  await page.evaluate(() => window.__avatar.setGazeMode('follow')); await advance(.9);
  check((await snap()).dialogue.suspended && zero((await snap()).expression.appliedValues),
    'manual gaze ownership suspends intent motion and face offsets');

  const bounds = await page.evaluate(async () => {
    const { DialogueReviewRecorder } = await import('/static/avatar/dialogue-review.js');
    let at = 0;
    const r = new DialogueReviewRecorder({ now: () => at });
    const before = r.snapshot(), a = window.__avatar;
    r.start({ source: 'browser-bounds', token: 'must-not-copy' });
    for (let i = 0; i < 15; i++) { r.beginTurn({ id: `bounded-${i}`, input: '真实文字' });
      r.recordEvent({ type: 'text_delta', delta: '正文。'.repeat(9000), data: 'audio-sentinel', token: 'token-sentinel' });
      r.recordEvent({ type: 'playback-start', text: '实际发声', buffer: { audio: 'buffer-sentinel' } });
      r.endTurn(`bounded-${i}`); at += 1; }
    const retained = r.snapshot();
    r.beginTurn({ id: 'duration-bound' }); at += 120000; r.recordFrame(a);
    const timed = r.snapshot();
    const samples = new DialogueReviewRecorder({ now: () => at, sampleIntervalMs: 1 });
    samples.start(); samples.beginTurn({ id: 'sample-bound' });
    for (let i = 0; i < 1250; i++) { at += 1; samples.recordFrame(a); }
    const sampled = samples.snapshot();
    samples.recordEvent({ type: 'done', text: '服务端先完成' }); const beforeEnd = samples.snapshot();
    samples.stop(); const stopped = samples.snapshot();
    samples.clear(); const cleared = samples.snapshot();
    return { before, retained, timed, sampled, beforeEnd, stopped, cleared };
  });
  check(bounds.before.limits.maxTurns === 12 && bounds.before.limits.maxDurationMs === 120000
    && bounds.before.limits.maxSamples === 1200, 'default review bounds remain 12 turns, 120 seconds per turn and 1200 samples per turn');
  check(bounds.retained.turns.length === 12 && bounds.retained.turns[0].id === 'bounded-3',
    'completed review turns evict oldest evidence beyond the 12-turn limit');
  check(bounds.retained.turns.every(turn => turn.generatedText.length <= 16384 && turn.truncated.generatedText),
    'generated review text is bounded and explicitly marked when truncated');
  check(!JSON.stringify(bounds.retained).includes('audio-sentinel')
    && !JSON.stringify(bounds.retained).includes('buffer-sentinel')
    && !JSON.stringify(bounds.retained).includes('token-sentinel')
    && !JSON.stringify(bounds.retained).includes('must-not-copy'),
  'review snapshots exclude audio payloads, buffers and unrelated credential fields');
  check(bounds.timed.current === null && bounds.timed.turns.at(-1).reason === 'duration-limit'
    && bounds.timed.turns.at(-1).durationMs === 120000, '120-second capture closes its turn at the exact duration cap');
  check(bounds.sampled.current.frames.length === 1200 && bounds.sampled.current.truncated.frames,
    'sample evidence cannot exceed 1200 frames even at a faster test sampling interval');
  check(bounds.beforeEnd.current.generatedText === '服务端先完成' && bounds.beforeEnd.current.spokenText === ''
    && bounds.beforeEnd.current.endedAt === null, 'generated done remains distinct from actual playback and cannot close a pending turn');
  check(!bounds.stopped.recording && bounds.stopped.current === null && bounds.cleared.turns.length === 0,
    'stop and clear independently end capture and remove retained evidence');
  evidence.bounds = { limits: bounds.before.limits,
    retainedTurnIds: bounds.retained.turns.map(turn => turn.id),
    responseLengths: bounds.retained.turns.map(turn => turn.generatedText.length),
    duration: { reason: bounds.timed.turns.at(-1).reason, durationMs: bounds.timed.turns.at(-1).durationMs },
    sampleCount: bounds.sampled.current.frames.length, sampleTruncated: bounds.sampled.current.truncated.frames,
    generatedBeforeEnd: bounds.beforeEnd.current.generatedText,
    spokenBeforeEnd: bounds.beforeEnd.current.spokenText,
    recordingAfterStop: bounds.stopped.recording, turnsAfterClear: bounds.cleared.turns.length };

  await reset('teasing');
  await page.selectOption('#dialogueIntent', 'teasing');
  await page.selectOption('#dialogueReviewScenario', 'multi');
  await page.evaluate(() => {
    window.__intentReviewClock = performance.now();
    window.__intentReviewNow = window.__dialogueReview._now;
    window.__dialogueReview._now = () => window.__intentReviewClock;
  });
  await page.locator('#dialogueReviewClear').click();
  await page.locator('#dialogueReviewToggle').click();
  check((await review()).recording, 'laboratory record control explicitly enables in-memory capture');
  await page.evaluate(() => window.__dialogueReview.beginTurn({ id: 'lab-review', input: '实际用户输入', source: 'lab', scenario: 'multi' }));
  await event({ type: 'thinking', turn_id: 'lab-review' }); await advance(.8);
  await page.evaluate(() => {
    const r = window.__dialogueReview;
    r.recordEvent({ type: 'text_delta', turn_id: 'lab-review', delta: '先生成的正文。后续还没说。' });
    r.recordEvent({ type: 'done', turn_id: 'lab-review', text: '先生成的正文。后续还没说。' });
  });
  await speak(); await page.evaluate(() => window.__dialogueReview.recordEvent({ type: 'playback-start', turn_id: 'lab-review', text: '先生成的正文。' }));
  await advance(.9); const captured = await review();
  check(captured.current.frames.length >= 10 && captured.current.frames.some(frame => frame.state === 'speaking'
    && frame.intent === 'teasing' && frame.capabilities.profile === 'seed-lids'),
  'Avatar render chain automatically samples actual intent, eye capabilities and playing state');
  check(captured.current.generatedText === '先生成的正文。后续还没说。' && captured.current.spokenText === '先生成的正文。',
    'generated and actually played text remain separate in the same laboratory turn');
  await page.locator('#dialogueReviewToggle').click();
  const stoppedCapture = await review();
  check(!stoppedCapture.recording && stoppedCapture.current === null && stoppedCapture.turns.length === 1,
    'stopping review preserves the completed turn for scrubbing');
  const stoppedFrames = stoppedCapture.turns[0].frames.length;
  await advance(.5); check((await review()).turns[0].frames.length === stoppedFrames,
    'disabled review no longer appends animation samples');
  await page.selectOption('#dialogueReviewTurns', 'lab-review');
  await page.locator('#dialogueReviewScrub').evaluate(node => { node.value = '600'; node.dispatchEvent(new Event('input', { bubbles: true })); });
  const beforeScrub = await snap();
  await page.locator('#dialogueReviewScrub').evaluate(node => { node.value = '1200'; node.dispatchEvent(new Event('input', { bubbles: true })); });
  const afterScrub = await snap();
  check(JSON.stringify(beforeScrub.clips) === JSON.stringify(afterScrub.clips)
    && beforeScrub.director.age === afterScrub.director.age && beforeScrub.lip === afterScrub.lip,
  'scrubbing evidence reads a recorded frame without seeking or restarting real playback');
  check((await page.locator('#dialogueReviewGenerated').textContent()).includes('后续还没说')
    && !(await page.locator('#dialogueReviewSpoken').textContent()).includes('后续还没说'),
  'review UI displays generated and actually spoken text in distinct panes');
  const downloadPromise = page.waitForEvent('download'); await page.locator('#dialogueReviewExport').click();
  const download = await downloadPromise; await download.saveAs(output + '/review-export.json');
  const exportedReview = JSON.parse(await readFile(output + '/review-export.json', 'utf8'));
  check(JSON.stringify(exportedReview).includes('lab-review') && JSON.stringify(exportedReview).includes('seed-lids'),
    'review export contains the bounded captured turn and model capability evidence');
  await page.locator('#dialogueReviewClear').click();
  check((await review()).turns.length === 0 && (await review()).current === null,
    'clear control removes all completed and active review data');

  const injection = '<img src=x onerror="window.__reviewInjected=true"><script>window.__reviewInjected=true</script>';
  await page.evaluate(text => {
    const r = window.__dialogueReview; r.start();
    r.beginTurn({ id: 'unsafe-text', input: text, source: 'lab' });
    r.recordEvent({ type: 'done', text }); r.recordEvent({ type: 'playback-start', text });
    r.endTurn('unsafe-text'); r.stop();
  }, injection);
  await page.waitForTimeout(350); await page.selectOption('#dialogueReviewTurns', 'unsafe-text');
  check(await page.evaluate(() => !window.__reviewInjected &&
    !document.querySelector('#dialogueReviewMount img, #dialogueReviewMount script')),
  'untrusted captured text renders as text and cannot create executable review DOM');
  check((await page.locator('#dialogueReviewGenerated').textContent()).includes('<img')
    && (await page.locator('#dialogueReviewSpoken').textContent()).includes('<script>'),
  'review panes retain literal untrusted markup through textContent');
  await page.locator('#dialogueReviewClear').click();
  await page.evaluate(() => { window.__dialogueReview._now = window.__intentReviewNow; delete window.__intentReviewClock; });

  await page.selectOption('#dialogueIntent', 'hesitant');
  await page.evaluate(() => { const r = window.__dialogueReview; r.start();
    r.beginTurn({ id: 'calibration-review', source: 'lab' }); window.__avatar._start(); });
  await page.locator('#calibrationEnter').click();
  check(!(await review()).recording && (await review()).turns.at(-1).reason === 'calibration',
    'entering real calibration closes and disables capture before the fixed pose');
  await page.locator('#calibrationExit').click();
  check(await page.evaluate(() => window.__avatar.dialogueIntentMode === 'hesitant') && !(await review()).recording,
    'calibration restores chosen presentation intent without silently resuming review');
  await page.evaluate(() => { const r = window.__dialogueReview; r.start();
    r.beginTurn({ id: 'switch-review', source: 'lab' }); });
  await page.locator('#dailyEyes').click(); await ready('seed-eyes');
  check(!(await review()).recording && (await review()).turns.at(-1).reason === 'model-switch',
    'switching the real model cancels capture with an explicit model-switch reason');
  check(await page.evaluate(() => window.__avatar.dialogueIntentMode === 'hesitant'),
    'model switching retains the manual presentation intent');
  await page.locator('#dailyLids').click(); await ready();
  await page.setViewportSize({ width: 430, height: 900 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'intent and review controls fit a mobile laboratory viewport');
  await page.locator('#dialogueReviewMount').screenshot({ path: output + '/mobile-review-controls.png' });
  await page.setViewportSize({ width: 1280, height: 920 });
  await page.screenshot({ path: output + '/laboratory.png', fullPage: true });

  chat = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  chat.on('pageerror', error => errors.push(`chat: ${error.message}`));
  await installChatHarness(chat); await chat.goto(origin + '/?avatar=seed-daily-lids');
  await chat.waitForFunction(() => document.querySelector('#avatarRenderer')?.value === 'seed-daily-lids'
    && document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用') && window.__dialogueReview,
  {}, { timeout: 90000 });
  await chat.evaluate(async () => { window.__chatIntentAvatar = (await import('/static/avatar/index.js')).avatar; });
  await chat.locator('.preferences > summary').click();
  const emit = message => chat.evaluate(message => intentHarness.ws.emit(message), message);
  const submit = async text => { await chat.locator('#textInput').fill(text); await chat.locator('#sendBtn').click();
    return chat.evaluate(() => intentHarness.sent.filter(message => message.type === 'text').at(-1)); };
  const defaultTurn = await submit('默认不记录的对话');
  await emit({ type: 'thinking', turn_id: defaultTurn.turn_id });
  await emit({ type: 'done', turn_id: defaultTurn.turn_id, text: '文字回复。' });
  check(!(await review(chat)).recording && (await review(chat)).turns.length === 0 && (await review(chat)).current === null,
    'ordinary chat events remain unrecorded by default');
  check((await snap(chat, '__chatIntentAvatar')).director.style === 'natural'
    && await chat.locator('#dialogueIntent').inputValue() === 'auto',
  'ordinary chat keeps the established natural style with automatic intent');

  await chat.selectOption('#dialogueReviewScenario', 'multi');
  await chat.locator('#dialogueReviewToggle').click();
  const turn = await submit('分句真聊输入');
  await emit({ type: 'thinking', turn_id: turn.turn_id });
  await emit({ type: 'emotion', turn_id: turn.turn_id, emotion: '认真' });
  await emit({ type: 'text_delta', turn_id: turn.turn_id, delta: '第一句认真。第二句调侃。还有未发声的后文。' });
  await chat.evaluate(() => { intentHarness.decodePending = true; });
  await emit({ type: 'audio', turn_id: turn.turn_id, data: 'AA==', text: '第一句认真。', emotion: '认真' });
  await chat.waitForFunction(() => intentHarness.decodes.length === 1); await chat.waitForTimeout(150);
  const decodeWait = await snap(chat, '__chatIntentAvatar'), waitingReview = await review(chat);
  check(decodeWait.dialogue.state !== 'speaking' && waitingReview.current.spokenText === ''
    && waitingReview.current.generatedText.includes('后文'),
  'production decode delay records generated text while withholding actually spoken text and speech cues');
  await chat.evaluate(() => intentHarness.resolveDecode());
  await chat.waitForFunction(() => window.__chatIntentAvatar.dialogueFrame?.state === 'speaking');
  await chat.evaluate(() => { intentHarness.now += .75; }); await chat.waitForTimeout(150);
  const firstSpeech = await snap(chat, '__chatIntentAvatar');
  check(firstSpeech.dialogue.intent === 'serious' && eyes.every(key => firstSpeech.expression.values[key] === 0),
    'first production audio sentence uses its structured serious intent');
  await emit({ type: 'audio', turn_id: turn.turn_id, data: 'AA==', text: '第二句调侃。', emotion: '调侃' });
  await emit({ type: 'done', turn_id: turn.turn_id, text: '第一句认真。第二句调侃。还有未发声的后文。' });
  const doneWhilePlaying = await review(chat);
  check(doneWhilePlaying.current?.id === turn.turn_id && doneWhilePlaying.current.spokenText === '第一句认真。',
    'server done retains the active recorded turn until queued audible sentences finish');
  await chat.evaluate(() => intentHarness.finish());
  await chat.waitForFunction(() => intentHarness.sources.length === 1 && window.__chatIntentAvatar.dialogueFrame?.intent === 'teasing');
  await chat.evaluate(() => { intentHarness.now += .8; }); await chat.waitForTimeout(150);
  const secondSpeech = await snap(chat, '__chatIntentAvatar'), sentenceReview = await review(chat);
  check(secondSpeech.dialogue.state === 'speaking' && secondSpeech.dialogue.intent === 'teasing'
    && sentenceReview.current.spokenText === '第一句认真。\n第二句调侃。',
  'second production sentence updates intent at playback and appends only its actually spoken text');
  const liveClips = JSON.stringify(secondSpeech.clips);
  await chat.selectOption('#dialogueIntent', 'hesitant'); await chat.waitForTimeout(150);
  const liveSelected = await snap(chat, '__chatIntentAvatar');
  check(liveSelected.dialogue.intent === 'hesitant' && liveSelected.lip
    && JSON.stringify(liveSelected.clips) === liveClips,
  'changing real chat presentation during speech preserves its audio gate');
  await chat.evaluate(() => intentHarness.finish());
  await chat.waitForFunction(() => !window.__dialogueReview.snapshot().current);
  const complete = await review(chat);
  check(complete.turns.at(-1).id === turn.turn_id && complete.turns.at(-1).reason === 'complete'
    && complete.turns.at(-1).generatedText.includes('未发声') && !complete.turns.at(-1).spokenText.includes('未发声'),
  'production playback completion closes the same turn with distinct generated and spoken evidence');
  evidence.chatCompleted = complete.turns.at(-1);

  await chat.selectOption('#dialogueIntent', 'teasing');
  const stoppedTurn = await submit('打断未完成音频');
  await emit({ type: 'thinking', turn_id: stoppedTurn.turn_id });
  await chat.evaluate(() => { intentHarness.decodePending = true; });
  await emit({ type: 'audio', turn_id: stoppedTurn.turn_id, data: 'AA==', text: '不应发声。', emotion: '调侃' });
  await chat.waitForFunction(() => intentHarness.decodes.length === 1);
  await chat.locator('#stopBtn').click(); await chat.evaluate(() => intentHarness.resolveDecode());
  await chat.waitForTimeout(250); const cancelled = await snap(chat, '__chatIntentAvatar'), cancelledReview = await review(chat);
  check(!cancelled.lip && cancelled.clips.length === 0 && await chat.evaluate(() => intentHarness.sources.length) === 0
    && cancelledReview.current === null && cancelledReview.turns.at(-1).id === stoppedTurn.turn_id
    && cancelledReview.turns.at(-1).reason !== 'complete' && cancelledReview.turns.at(-1).spokenText === '',
  'production stop retires pending decode and turn evidence without reviving speech');
  const lateBefore = JSON.stringify(await review(chat));
  await emit({ type: 'text_delta', turn_id: stoppedTurn.turn_id, delta: '退休回合不应混入' });
  await emit({ type: 'audio', turn_id: stoppedTurn.turn_id, data: 'AA==', text: '迟到音频', emotion: '调侃' });
  check(JSON.stringify(await review(chat)) === lateBefore,
    'TurnGate rejects retired-turn text and audio before they reach review');

  const resumeOriginal = await submit('续播只记录新片段边界');
  await emit({ type: 'thinking', turn_id: resumeOriginal.turn_id });
  await emit({ type: 'audio', turn_id: resumeOriginal.turn_id, data: 'AA==', text: '这一整句只会播放剩余的音频部分。', emotion: 'base' });
  await chat.waitForFunction(() => intentHarness.sources.length === 1);
  await chat.evaluate(() => { intentHarness.now += 1.2; }); await chat.waitForTimeout(150);
  await chat.locator('#stopBtn').click();
  await chat.waitForFunction(() => !document.querySelector('#resumeSpeechBtn').hidden && !document.querySelector('#resumeSpeechBtn').disabled);
  await chat.locator('#resumeSpeechBtn').click();
  await chat.waitForFunction(() => intentHarness.sources.length === 1);
  const resumed = await review(chat);
  check(resumed.current?.id.startsWith('baseline-resume-') && resumed.current.input.includes('无剩余文字对齐')
    && resumed.current.generatedText === '' && resumed.current.spokenText === ''
    && resumed.current.events.some(event => event.type === 'playback-start' && event.meta?.source === 'resume'),
  'audio-only continuation records a separate turn and marks unavailable remaining-text alignment');
  await chat.evaluate(() => intentHarness.finish());
  await chat.waitForFunction(() => !window.__dialogueReview.snapshot().current);
  check((await review(chat)).turns.at(-1).reason === 'complete',
    'audio-only continuation completes its own recorded turn after playback');

  await chat.locator('#dialogueReviewToggle').click();
  check(!(await review(chat)).recording, 'production review stop remains independent from actual conversation');
  const partialTurn = await submit('中途开始记录');
  await emit({ type: 'thinking', turn_id: partialTurn.turn_id });
  await emit({ type: 'text_delta', turn_id: partialTurn.turn_id, delta: '不应追溯的前文。' });
  await chat.locator('#dialogueReviewToggle').click();
  await emit({ type: 'text_delta', turn_id: partialTurn.turn_id, delta: '开启后的后文。' });
  await emit({ type: 'done', turn_id: partialTurn.turn_id, text: '不应追溯的前文。开启后的后文。' });
  const partial = (await review(chat)).turns.at(-1);
  check(partial.input === '' && partial.generatedText === '开启后的后文。'
    && partial.events.some(event => event.meta?.reason === 'partial-recording'),
  'enabling review mid-turn records only subsequent text and marks missing earlier context');
  const restartedTurn = await submit('停录前的旧输入');
  await emit({ type: 'thinking', turn_id: restartedTurn.turn_id });
  await emit({ type: 'text_delta', turn_id: restartedTurn.turn_id, delta: '停录前的旧文。' });
  await chat.locator('#dialogueReviewToggle').click();
  check(await chat.evaluate(() => !Object.hasOwn(window.__dialogueReviewUI.activeTurn || {}, 'input')),
    'stopping capture does not retain user input in the UI active-turn handle');
  await emit({ type: 'text_delta', turn_id: restartedTurn.turn_id, delta: '停录间的文本。' });
  await chat.locator('#dialogueReviewToggle').click();
  await emit({ type: 'text_delta', turn_id: restartedTurn.turn_id, delta: '重新开启后的文。' });
  await emit({ type: 'done', turn_id: restartedTurn.turn_id, text: '停录前的旧文。停录间的文本。重新开启后的文。' });
  const restartEvidence = await review(chat), restarted = restartEvidence.turns.at(-1), earlier = restartEvidence.turns.at(-2);
  check(earlier.id === restartedTurn.turn_id && restarted.id !== earlier.id
    && earlier.generatedText === '停录前的旧文。' && restarted.generatedText === '重新开启后的文。' && restarted.input === '',
  'stopping and restarting one transport turn creates independent evidence segments without backfilling skipped text');
  check(!JSON.stringify(restartEvidence).includes('停录间的文本'), 'text arriving while capture is disabled is excluded from later export evidence');

  const clearedTurn = await submit('清空时应消失的输入');
  await emit({ type: 'thinking', turn_id: clearedTurn.turn_id });
  await emit({ type: 'text_delta', turn_id: clearedTurn.turn_id, delta: '清空时应消失的正文。' });
  await chat.locator('#dialogueReviewClear').click();
  const afterClear = await review(chat);
  check(afterClear.turns.length === 0 && afterClear.current?.id !== clearedTurn.turn_id
    && !JSON.stringify(afterClear).includes('清空时应消失')
    && await chat.evaluate(() => !Object.hasOwn(window.__dialogueReviewUI.activeTurn || {}, 'input')),
  'clearing an active turn removes prior input and text while beginning a marked empty segment');
  await chat.evaluate(() => {
    window.__dialogueReviewUI.event({ type: 'text_delta', turn_id: 'foreign-turn', delta: '不应进入复盘的其他回合' });
    window.__dialogueReviewUI.endTurn('foreign-turn', 'complete');
  });
  check((await review(chat)).current?.id === afterClear.current.id && (await review(chat)).current.generatedText === '',
    'wrong transport IDs cannot mutate or close the active review segment');
  await emit({ type: 'text_delta', turn_id: clearedTurn.turn_id, delta: '清空后的新正文。' });
  await emit({ type: 'done', turn_id: clearedTurn.turn_id, text: '清空时应消失的正文。清空后的新正文。' });
  const clearEvidence = await review(chat);
  check(clearEvidence.current === null && clearEvidence.turns.length === 1
    && clearEvidence.turns[0].generatedText === '清空后的新正文。' && clearEvidence.turns[0].input === '',
  'original transport completion closes the cleared segment without restoring earlier text');
  await chat.locator('#dialogueReviewToggle').click();
  await chat.setViewportSize({ width: 430, height: 900 });
  await chat.locator('#sidebarToggle').click();
  check(await chat.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'intent and review controls fit mobile chat preferences');
  await chat.locator('#dialogueReviewMount').screenshot({ path: output + '/mobile-chat-review.png' });
  await chat.setViewportSize({ width: 1440, height: 1000 });
  await chat.screenshot({ path: output + '/chat.png', fullPage: true });
  check(errors.length === 0, `no browser exceptions: ${errors.join('; ')}`);
  const afterHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async path => [path,
    createHash('sha256').update(await readFile(path)).digest('hex')])));
  check(sourceFiles.every(path => sourceHashes[path].sha256 === afterHashes[path]),
    'runtime sources remain unchanged throughout the intent and review evidence run');
  await writeFile(output + '/results.json', JSON.stringify({ ok: true, checks, errors, evidence }, null, 2));
  console.log(`${checks.length} dialogue intent and review browser checks passed; ${output}`);
} catch (error) {
  await page.screenshot({ path: output + '/failure.png', fullPage: true }).catch(() => {});
  evidence.failureSnapshot = await snap().catch(() => null);
  if (chat && !chat.isClosed()) evidence.chatFailure = await snap(chat, '__chatIntentAvatar').catch(() => null);
  await writeFile(output + '/results.json', JSON.stringify({ ok: false, checks, errors,
    failure: error.message, stack: error.stack, evidence }, null, 2));
  throw error;
} finally { await browser.close(); }
