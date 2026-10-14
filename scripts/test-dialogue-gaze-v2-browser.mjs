/* V2 integration evidence: shipped V3, production Avatar and profile/cue
 * arbitration. Devices and transport are substituted only in the chat section.
 * Historical V1 test and evidence directories remain untouched. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const output = `eval/avatar/dialogue-gaze-v2/${new Date().toISOString().replace(/[:.]/g, '-')}`;
const styles = ['restrained', 'natural', 'lively'];
const faceKeys = ['ParamBrowLY', 'ParamBrowRY', 'ParamEyeLSmile', 'ParamEyeRSmile', 'ParamEyeLLower', 'ParamEyeRLower'];
const sourceFiles = ['web/avatar/index.js', 'web/avatar/params.js', 'web/avatar/calibration.js',
  'web/avatar/drivers/dialogue-gaze.js', 'web/avatar/drivers/dialogue-expression.js',
  'web/avatar/drivers/idle.js', 'web/avatar/drivers/emotion.js', 'web/avatar/drivers/gesture.js',
  'web/avatar/drivers/face.js', 'web/avatar/drivers/discourseGesture.js',
  'web/avatar/render/eye-morph.js', 'web/avatar/render/vrm.js',
  'web/avatar_lab.js', 'web/avatar_lab.html', 'web/app.js', 'config/avatar.yaml',
  'web/models/seed-daily-lids.vrm', 'scripts/test-dialogue-gaze-v2-browser.mjs'];
await mkdir(output, { recursive: true });
const sourceHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async path => {
  const bytes = await readFile(path); return [path, { bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex') }];
})));
await writeFile(output + '/source-hashes.json', JSON.stringify(sourceHashes, null, 2));
const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const checks = [], errors = [], evidence = { sourceHashes, styles: {}, cases: [] };
page.on('pageerror', error => errors.push(`lab: ${error.message}`));
const check = (condition, label) => { assert.ok(condition, label); checks.push(label); };
const finiteValues = values => Object.values(values || {}).every(Number.isFinite);
const near = (a, b, tolerance = 1e-6) => Number.isFinite(a) && Math.abs(a - b) <= tolerance;

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
      head: { x: a.idle.headX, y: a.idle.headY, duration: a.idle.headDur,
        context: { ...a.idle.gazeContext }, quaternion: s?.bones?.head?.node.quaternion.toArray() },
      gaze: { mode: a.gazeMode, name: a.idle.targetName, anchor: { ...a.idle.customAnchor } },
      blinkLeft: s?.vrm?.expressionManager.getValue('blinkLeft'),
      blinkRight: s?.vrm?.expressionManager.getValue('blinkRight'),
      vowels: ['aa', 'ih', 'ou', 'ee', 'oh'].map(key => s?.vrm?.expressionManager.getValue(key) || 0),
      clips: a.dialogueGaze._clips?.map(clip => ({ ...clip })), lip: a.lip.speaking,
      preview: a.eyeExpression.sample().active, gesture: a.gesture.active?.name || null };
  }, name);
}
async function freeze() {
  await page.evaluate(() => {
    const a = window.__avatar;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.idle.cfg.energy.drain_per_sec = 0; a.idle.cfg.energy.drain_speaking = 0;
    a.idle.energy = 1; a.idle.nextBlink = 1e6; a.gesture.nextIdleLook = 1e6;
    window.__v2Tracks = { ...a.gesture.tracks }; window.__v2Emotions = { ...a.emotion.table };
    window.__v2FaceApply = a.face.apply; window.__v2DiscourseUpdate = a.discourseGesture.update;
  });
}
async function advance(seconds, { audio = true } = {}) {
  return page.evaluate(({ seconds, audio }) => {
    const a = window.__avatar, s = a.svg._state, render = s?.renderer.render, trace = [];
    if (render) s.renderer.render = () => {};
    try {
      const count = Math.ceil(seconds * 60), dt = seconds / count;
      for (let i = 0; i < count; i++) {
        if (audio && window.__v2Audio) window.__v2Audio.currentTime += dt;
        a.clock += dt; a._frame(dt);
        trace.push({ state: a.dialogueFrame.state, phase: a.dialogueFrame.phase,
          style: a.dialogueFrame.style, age: a.dialogueFrame.age,
          command: a.dialogueFrame.command, glance: a.dialogueFrame.glance,
          expression: a.dialogueExpressionFrame, headX: a.idle.headX, headY: a.idle.headY,
          x: a.eyeFrame.params.ParamEyeBallX, y: a.eyeFrame.params.ParamEyeBallY });
      }
    } finally {
      if (render) { s.renderer.render = render; render.call(s.renderer, s.scene, s.camera); }
    }
    return trace;
  }, { seconds, audio });
}
async function reset(style = 'natural') {
  await page.evaluate(style => {
    const a = window.__avatar;
    a.onBargeIn(); a.gesture.setTracks(window.__v2Tracks); a.gesture.nextIdleLook = 1e6;
    a.face.apply = window.__v2FaceApply; a.face.setMimicryMode(false);
    a.discourseGesture.update = window.__v2DiscourseUpdate;
    a.emotion.setTable(window.__v2Emotions); a.setEmotion('base'); a.setEyeExpression(null);
    a.setReducedMotion(false); a.setDialogueGazeEnabled(true); a.setDialogueStyle(style);
    a.dialogueGaze.stop('test-reset'); a.setDialogueState('idle'); a.setGazeMode('natural');
    a.idle.setGaze('viewer', true); a.idle.claimGaze(false);
    a.idle.blinkPhase = -1; a.idle.blinkHold = 0; a.idle.eyeLevel = 1; a.idle.nextBlink = 1e6;
    window.__v2Audio = { currentTime: 100, state: 'running' };
    a.attachOutputAnalyser(null); a.setAudioContext(window.__v2Audio);
  }, style);
  await advance(.8);
}
const event = message => page.evaluate(message => window.__avatar.onMessage(message), message);
async function speak(duration = 3) {
  await page.evaluate(duration => { const a = window.__avatar, ctx = window.__v2Audio;
    a.speak('我们可以慢慢看，再决定怎么做。', ctx, ctx.currentTime, duration,
      { performance: { version: 1, source: 'llm', cues: [] } }); }, duration);
}
async function record(name) { const value = await snap(); evidence.cases.push({ name, ...value }); return value; }
async function installChatHarness(chat) {
  await chat.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/avatar') return route.continue();
    return route.fulfill({ json: pathname === '/api/persona'
      ? { name: '测试', tagline: '对话 V2', voices: ['base'], default_voice: 'base' }
      : { count: 0, items: [], tasks: [], notes: [] } });
  });
  await chat.addInitScript(() => {
    const h = window.v2Harness = { sent: [], sources: [], now: 1 };
    class WS {
      static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
      constructor() { h.ws = this; this.readyState = 0; setTimeout(() => {
        this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'v2' });
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
      decodeAudioData() { return Promise.resolve({ duration: 3, length: 144000, numberOfChannels: 1 }); }
      createBufferSource() { const s = { ...node(), onended: null,
        start() { h.sources.push(this); }, stop() { h.sources = h.sources.filter(item => item !== this); this.onended?.(); } };
        return s; }
    }
    window.AudioContext = AC;
    h.finish = () => { h.now += 3.01; for (const source of h.sources.splice(0)) source.onended?.(); };
  });
}

let chat;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1'); await ready();
  const exported = await page.evaluate(async () => {
    const module = await import('/static/avatar/drivers/dialogue-gaze.js');
    return module.DIALOGUE_GAZE_STYLES.map(style => style.id);
  });
  check(JSON.stringify(exported) === JSON.stringify(styles), 'exactly three documented attention styles are exported');
  check(JSON.stringify(await page.locator('#dialogueStyle option').evaluateAll(options => options.map(option => option.value)))
    === JSON.stringify(styles), 'laboratory exposes the same three profile choices');
  check(await page.locator('#dialogueStyle').inputValue() === 'natural', 'the laboratory starts with natural style');
  await freeze();
  for (const style of styles) {
    await reset(style);
    await page.evaluate(() => window.__avatar.dialogueGaze.resetVariation('v2-profile-compare'));
    await event({ type: 'recording', state: true, turn_id: `profile-${style}` });
    const listeningTrace = await advance(.7), listening = await record(`${style}-listening`);
    check(listening.director.style === style && listening.dialogue.state === 'listening', `${style}: selected style reaches the actual runtime`);
    check(finiteValues(listening.expression.values) && finiteValues(listening.expression.appliedValues)
      && Object.keys(listening.expression.values || {}).every(key => faceKeys.includes(key)),
    `${style}: lightweight cue writes only the six supported brow/lid/smile channels`);
    check(Object.values(listening.expression.values || {}).every(value => Math.abs(value) <= .15),
      `${style}: facial cues remain a small bounded addition`);
    check(Object.values(listening.expression.appliedValues || {}).some(value => Math.abs(value) > 1e-5),
      `${style}: listening cue produces an actual unprotected face delta`);
    await page.locator('#viewport').screenshot({ path: `${output}/${style}-listening.png` });
    await event({ type: 'thinking', turn_id: `profile-${style}` });
    const plan = (await snap()).director.plan;
    check(plan && Number.isFinite(plan.seed) && Array.isArray(plan.glances) && plan.glances.length === 4
      && plan.budget === ({ restrained: 2, natural: 3, lively: 4 })[style],
    `${style}: a finite four-glance blueprint has the correct per-style budget`);
    check(plan.styleAtStart === style && plan.glances.every(glance => Number.isFinite(glance.start)
      && Number.isFinite(glance.end) && glance.start >= 0 && glance.end > glance.start
      && Math.abs(glance.x) <= .5 && Math.abs(glance.y) <= .35),
    `${style}: glance timing and direction remain bounded`);
    const glance = plan.glances[0];
    await advance(glance.start + Math.min(.25, (glance.end - glance.start) * .5));
    const thinking = await record(`${style}-thinking`);
    check(thinking.dialogue.phase === 'averted', `${style}: the real update chain enters the first planned glance`);
    await page.locator('#viewport').screenshot({ path: `${output}/${style}-thinking.png` });
    const originalPlan = JSON.stringify(thinking.director.plan), originalAge = thinking.director.age;
    await event({ type: 'thinking', turn_id: `profile-${style}` }); await advance(.05);
    const repeated = await snap();
    check(JSON.stringify(repeated.director.plan) === originalPlan && repeated.director.age > originalAge,
      `${style}: a repeated same-turn event neither rerolls nor restarts the plan`);
    await speak(); const responseTrace = await advance(.4), response = await record(`${style}-response`);
    check(response.dialogue.state === 'speaking' && response.vowels.some(value => value > 0),
      `${style}: response cues coexist with real VRM speech vowels`);
    check(response.native.neuro_lower_lid_raise_L > 0 && response.native.neuro_lower_lid_raise_R > 0,
      `${style}: response cue deltas reach both actual V3 lower-lid targets`);
    await page.locator('#viewport').screenshot({ path: `${output}/${style}-response.png` });
    evidence.styles[style] = { listeningTrace, listening, plan, thinking, responseTrace, response };
  }
  const commands = styles.map(style => evidence.styles[style].listeningTrace.find(frame => frame.command)?.command);
  check(commands.every(command => Number.isFinite(command?.headFollowScale) && Number.isFinite(command?.headDelayScale)),
    'all styles supply explicit head participation and timing scales');
  check(new Set(commands.map(command => command.headFollowScale)).size === 3
    && new Set(commands.map(command => command.headDelayScale)).size === 3,
  'each style has a distinct head movement and timing profile');
  check(new Set(styles.map(style => JSON.stringify(evidence.styles[style].listening.expression.appliedValues))).size === 3,
    'the three styles produce distinct actual facial cue deltas');
  check(new Set(styles.map(style => JSON.stringify(evidence.styles[style].thinking.native))).size > 1,
    'profile differences reach actual VRM morph targets');
  check(new Set(styles.map(style => JSON.stringify(evidence.styles[style].plan.glances))).size === 1,
    'profile comparison reuses the same seeded blueprint instead of comparing unrelated random turns');
  check(Math.hypot(evidence.styles.lively.thinking.head.x, evidence.styles.lively.thinking.head.y)
    > Math.hypot(evidence.styles.restrained.thinking.head.x, evidence.styles.restrained.thinking.head.y) + .001,
  'profile head participation changes the actual IdleDriver head movement');

  const priorVariation = await snap();
  check(await page.evaluate(() => window.__avatar.dialogueGaze.resetVariation('future-seed')),
    'a legal deterministic variation seed is accepted');
  const nextVariation = await snap();
  check(JSON.stringify(nextVariation.director.plan) === JSON.stringify(priorVariation.director.plan)
    && nextVariation.director.age === priorVariation.director.age
    && JSON.stringify(nextVariation.clips) === JSON.stringify(priorVariation.clips),
  'variation reset affects the next plan without changing the current plan, clock or audio gate');
  await page.evaluate(() => window.__avatar.setDialogueStyle('restrained'));
  const changedStyle = await snap();
  check(changedStyle.director.style === 'restrained'
    && changedStyle.director.plan.styleAtStart === priorVariation.director.plan.styleAtStart
    && JSON.stringify(changedStyle.director.plan.glances) === JSON.stringify(priorVariation.director.plan.glances)
    && changedStyle.director.age === priorVariation.director.age
    && JSON.stringify(changedStyle.clips) === JSON.stringify(priorVariation.clips),
  'style change updates the profile and budget without rerolling the active plan or cancelling speech');

  await reset(); await event({ type: 'thinking', turn_id: 'long-wait' });
  const boundedPlan = (await snap()).director.plan; const longWait = await advance(80);
  check(JSON.stringify((await snap()).director.plan) === JSON.stringify(boundedPlan), 'a long wait does not grow an unbounded plan');
  check(longWait.every(frame => finiteValues({ x: frame.x, y: frame.y, headX: frame.headX, headY: frame.headY })),
    'long waiting keeps real eye and head output finite');
  check(longWait.slice(-60).every(frame => frame.phase !== 'averted'), 'an exhausted glance budget returns to the viewer');

  // Explicit zeros are intent, not an invitation to generate a cue default.
  await reset();
  await page.evaluate(keys => {
    const a = window.__avatar; a.emotion.setTable({ ...a.emotion.table,
      v2_zero: { arousal: 1, valence: 0, blend: .1, shift: Object.fromEntries(keys.map(key => [key, 0])) } });
    a.setEmotion('v2_zero');
  }, faceKeys);
  await event({ type: 'recording', state: true, turn_id: 'explicit-zero' }); await advance(.7);
  const emotionZero = await record('emotion-zero');
  check(faceKeys.every(key => emotionZero.expression.protectedKeys.includes(key)
    && !(Math.abs(emotionZero.expression.appliedValues?.[key] || 0) > 1e-9)),
  'every explicit zero emotion channel blocks the matching dialogue delta');
  await reset(); await event({ type: 'recording', state: true, turn_id: 'gesture-zero' });
  await page.evaluate(keys => { const a = window.__avatar;
    a.gesture.setTracks({ ...a.gesture.tracks, v2_zero: { priority: 100, cooldown: 0, hold: true,
      keys: [{ at: 0, dur: .1, params: Object.fromEntries(keys.map(key => [key, 0])) }] } });
    a.gesture.play('v2_zero', a.clock); }, faceKeys);
  await advance(.5); const gestureZero = await record('gesture-zero');
  check(faceKeys.every(key => gestureZero.expression.protectedKeys.includes(key)
    && !(Math.abs(gestureZero.expression.appliedValues?.[key] || 0) > 1e-9)),
  'actual gesture pose keys protect zeros independently of the cue');
  await reset(); await event({ type: 'recording', state: true, turn_id: 'other-layer-zero' });
  await page.evaluate(() => { const a = window.__avatar;
    a.face.apply = rig => rig.set('face', 'ParamBrowLY', 0);
    a.discourseGesture.update = () => ({ ParamEyeLLower: 0 }); });
  await advance(.5); const otherZero = await record('face-discourse-zero');
  check(['ParamBrowLY', 'ParamEyeLLower'].every(key => otherZero.expression.protectedKeys.includes(key)
    && !(Math.abs(otherZero.expression.appliedValues?.[key] || 0) > 1e-9)),
  'mimicry and discourse layers retain explicit channel ownership including zero');
  check(otherZero.expression.values.ParamBrowLY > 0, 'the mimicry zero blocks an otherwise active listening brow cue');
  await speak(); await advance(.7); const discourseSpeech = await record('discourse-zero-during-speech');
  check(discourseSpeech.expression.values.ParamEyeLLower > 0
    && !(Math.abs(discourseSpeech.expression.appliedValues?.ParamEyeLLower || 0) > 1e-9)
    && discourseSpeech.expression.appliedValues.ParamEyeRLower > 0,
  'discourse zero blocks its active lower-lid cue while the other side remains expressive');

  await reset(); await event({ type: 'recording', state: true, turn_id: 'preview' });
  await page.evaluate(() => window.__avatar.setEyeExpression('happy')); await advance(.7);
  const preview = await record('preview');
  check(preview.preview && Object.values(preview.expression.appliedValues || {}).every(value => Math.abs(value) < 1e-9),
    'manual expression preview owns its six facial channels');
  await page.evaluate(() => window.__avatar.setGazeMode('follow')); await advance(.2);
  check((await snap()).dialogue.suspended, 'manual gaze mode suspends the semantic target schedule');

  await reset(); await page.evaluate(() => window.__avatar.setEmotion('得意'));
  await speak(3); await advance(.8); const smile = await record('configured-smile');
  check(['ParamEyeLLower', 'ParamEyeRLower', 'ParamEyeLSmile', 'ParamEyeRSmile'].every(key =>
    smile.expression.protectedKeys.includes(key) && !(Math.abs(smile.expression.appliedValues?.[key] || 0) > 1e-9)),
  'generated V3 emotion lid defaults remain authoritative over dialogue cues');
  check(smile.native.neuro_lower_lid_raise_L > 0 && smile.native.neuro_lower_lid_raise_R > 0,
    'ordinary V3 expression still drives both actual lower lids');
  await page.evaluate(() => window.__avatar.blinkEyes()); await advance(.075); const closed = await record('blink-mouth');
  check(closed.blink >= .999 && closed.blinkLeft === 1 && closed.blinkRight === 1,
    'profile cues permit a complete ordinary blink');
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R']
    .every(key => closed.native[key] === 0) && closed.vowels.some(value => value > 0),
  'complete blink clears new eye shapes while speech remains animated');

  await reset('lively'); await event({ type: 'thinking', turn_id: 'calibration-v2' }); await advance(.6);
  const beforeCalibration = await snap();
  await page.evaluate(async () => { const { AvatarCalibration } = await import('/static/avatar/calibration.js');
    window.__v2Calibration = new AvatarCalibration(window.__avatar);
    window.__v2Calibration.enter({ presentation: true, eyeFocus: true }); });
  await page.waitForTimeout(200); await page.evaluate(() => window.__v2Calibration.exit());
  const afterCalibration = await snap();
  check(JSON.stringify(beforeCalibration.director) === JSON.stringify(afterCalibration.director)
    && JSON.stringify(beforeCalibration.expression) === JSON.stringify(afterCalibration.expression),
  'calibration restores style, turn plan, glance, command and facial cue state exactly');
  evidence.calibration = { beforeCalibration, afterCalibration };
  await page.evaluate(() => window.__avatar._start());
  await page.locator('#dailyEyes').click(); await ready('seed-eyes');
  check(await page.evaluate(() => window.__avatar.dialogueGaze.sample().style === 'lively'),
    'switching to the V2 eye asset preserves the chosen attention style');
  check(await page.evaluate(() => window.__avatar.dialogueExpression.sample().values.ParamEyeLLower === 0
    && window.__avatar.dialogueExpression.sample().values.ParamEyeRLower === 0),
  'the older model retires unsupported lower-lid cue deltas');
  await page.locator('#dailyLids').click(); await ready();
  await page.setViewportSize({ width: 430, height: 900 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'style and comparison controls fit mobile width');
  await page.locator('#dialoguePanel').screenshot({ path: output + '/mobile-controls.png' });
  await page.setViewportSize({ width: 1280, height: 920 });

  // Resume cancellation is checked before any demo/compare timer is created.
  await page.locator('#speak').click(); await page.waitForFunction(() => window.__avatar.audioCtx instanceof AudioContext);
  await page.locator('#dialogueStop').click();
  await page.evaluate(() => { const a = window.__avatar, ctx = a.audioCtx;
    window.__v2RealCtx = ctx; window.__v2Resume = ctx.resume;
    ctx.resume = () => new Promise(resolve => { window.__v2ResolveResume = resolve; }); });
  for (const button of ['dialogueDemo', 'dialogueCompare']) {
    await page.evaluate(() => { window.__v2ResolveResume = null; });
    await page.locator('#' + button).click();
    await page.waitForFunction(() => typeof window.__v2ResolveResume === 'function');
    await page.locator('#dialogueStop').click(); await page.evaluate(() => window.__v2ResolveResume());
    await page.waitForTimeout(150);
    check(await page.evaluate(() => !window.__avatar.lip.timeline
      && window.__avatar.dialogueGaze.sample().state === 'idle' && !window.__dialogueComparison?.active),
    `${button}: stopping a pending resume cannot recreate a comparison or dialogue plan`);
  }
  await page.selectOption('#dialogueStyle', 'natural');
  await page.evaluate(() => { window.__v2ResolveResume = null; });
  await page.locator('#dialogueCompare').click();
  await page.waitForFunction(() => typeof window.__v2ResolveResume === 'function');
  await page.selectOption('#dialogueStyle', 'lively');
  const pendingStyleState = await page.evaluate(() => window.__avatar.dialogueGaze.sample().state);
  await page.evaluate(() => window.__v2ResolveResume());
  await page.waitForTimeout(150);
  check(await page.evaluate(state => document.querySelector('#dialogueStyle').value === 'lively'
    && window.__avatar.dialogueGaze.sample().style === 'lively'
    && window.__avatar.dialogueGaze.sample().state === state
    && window.__avatar.dialogueGaze.sample().plan === null
    && window.__avatar.dialogueGaze._clips.length === 0
    && !window.__dialogueComparison?.active && !window.__avatar.lip.timeline, pendingStyleState),
  'changing style during pending comparison resume invalidates the old automatic comparison');
  await page.evaluate(() => { window.__v2RealCtx.resume = window.__v2Resume; });
  await page.selectOption('#dialogueStyle', 'natural');
  await page.locator('#dialogueCompare').click();
  await page.waitForFunction(() => window.__dialogueComparison?.active);
  await page.waitForTimeout(500); await page.locator('#dialogueStop').click();
  const stoppedStyle = await page.locator('#dialogueStyle').inputValue();
  await page.waitForTimeout(1200);
  check(await page.evaluate(() => !window.__dialogueComparison?.active && !window.__avatar.lip.speaking)
    && await page.locator('#dialogueStyle').inputValue() === stoppedStyle,
  'stopping style comparison clears its pending stage changes and speech');
  await page.evaluate(() => {
    window.__v2Compared = {};
    window.__v2CompareObserver = setInterval(() => {
      const comparison = window.__dialogueComparison, frame = window.__avatar.dialogueGaze.sample();
      if (comparison?.active && frame.plan && !window.__v2Compared[comparison.style])
        window.__v2Compared[comparison.style] = JSON.parse(JSON.stringify({ comparison,
          director: frame, expression: window.__avatar.dialogueExpressionFrame }));
    }, 25);
  });
  await page.locator('#dialogueCompare').click();
  await page.waitForFunction(() => window.__dialogueComparison?.active);
  await page.waitForFunction(() => window.__dialogueComparison?.active === false, {}, { timeout: 40000 });
  const compared = await page.evaluate(() => { clearInterval(window.__v2CompareObserver); return window.__v2Compared; });
  check(JSON.stringify(Object.keys(compared)) === JSON.stringify(styles),
    'the complete comparison runs the same dialogue sequence through all three styles in order');
  check(new Set(Object.values(compared).map(value => JSON.stringify(value.director.plan.glances))).size === 1,
    'the complete comparison uses an identical seeded glance blueprint for every style');
  evidence.comparison = compared;
  await page.screenshot({ path: output + '/laboratory.png', fullPage: true });

  chat = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  chat.on('pageerror', error => errors.push(`chat: ${error.message}`));
  await installChatHarness(chat); await chat.goto(origin + '/?avatar=seed-daily-lids');
  await chat.waitForFunction(() => document.querySelector('#avatarRenderer')?.value === 'seed-daily-lids'
    && document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'),
    {}, { timeout: 90000 });
  await chat.evaluate(async () => { window.__chatV2Avatar = (await import('/static/avatar/index.js')).avatar; });
  check((await snap(chat, '__chatV2Avatar')).director.style === 'natural', 'ordinary chat uses natural style by default');
  await chat.locator('#textInput').fill('测试新版注意力'); await chat.locator('#sendBtn').click();
  const turn = await chat.evaluate(() => v2Harness.sent.filter(message => message.type === 'text').at(-1));
  const emit = message => chat.evaluate(message => v2Harness.ws.emit(message), message);
  await emit({ type: 'thinking', turn_id: turn.turn_id });
  await emit({ type: 'audio', data: 'AA==', text: '我们慢慢看看。', emotion: 'base', turn_id: turn.turn_id });
  await chat.waitForFunction(() => window.__chatV2Avatar.dialogueFrame?.state === 'speaking');
  await chat.evaluate(() => { v2Harness.now += .7; }); await chat.waitForTimeout(100);
  check((await snap(chat, '__chatV2Avatar')).native.neuro_lower_lid_raise_L > 0,
    'ordinary chat response reaches an actual lower-lid cue after its audio onset');
  const originalClips = (await snap(chat, '__chatV2Avatar')).clips;
  for (const style of styles) {
    await chat.evaluate(style => window.__chatV2Avatar.setDialogueStyle(style), style);
    await chat.waitForTimeout(100); const value = await snap(chat, '__chatV2Avatar');
    check(value.director.style === style && value.dialogue.state === 'speaking' && value.lip
      && JSON.stringify(value.clips) === JSON.stringify(originalClips),
    `${style}: changing style during actual chat speech retains the audio clip gate`);
    evidence.cases.push({ name: `chat-speaking-${style}`, ...value });
  }
  await chat.evaluate(() => { const a = window.__chatV2Avatar;
    a.emotion.setTable({ ...a.emotion.table, v2_zero: { arousal: 1, valence: 0, blend: .1,
      shift: { ParamBrowLY: 0, ParamBrowRY: 0, ParamEyeLLower: 0, ParamEyeRLower: 0 } } });
    a.setEmotion('v2_zero'); });
  await chat.waitForTimeout(200); const chatZero = await snap(chat, '__chatV2Avatar');
  check(['ParamBrowLY', 'ParamBrowRY', 'ParamEyeLLower', 'ParamEyeRLower'].every(key =>
    chatZero.expression.protectedKeys.includes(key) && !(Math.abs(chatZero.expression.appliedValues?.[key] || 0) > 1e-9)),
  'ordinary chat applies explicit emotion overrides without dialogue cue defaults');
  evidence.cases.push({ name: 'chat-explicit-zero', ...chatZero });
  await emit({ type: 'done', text: '我们慢慢看看。', turn_id: turn.turn_id });
  await chat.evaluate(() => v2Harness.finish());
  await chat.waitForFunction(() => window.__chatV2Avatar.dialogueFrame?.state === 'idle');
  check(!(await snap(chat, '__chatV2Avatar')).lip, 'V2 style selection leaves ordinary chat playback completion intact');
  await chat.screenshot({ path: output + '/chat.png', fullPage: true });
  check(errors.length === 0, `no browser exceptions: ${errors.join('; ')}`);
  const afterHashes = Object.fromEntries(await Promise.all(sourceFiles.map(async path => [path,
    createHash('sha256').update(await readFile(path)).digest('hex')])));
  check(sourceFiles.every(path => sourceHashes[path].sha256 === afterHashes[path]), 'runtime sources remain unchanged throughout this evidence run');
  await writeFile(output + '/results.json', JSON.stringify({ ok: true, checks, errors, evidence }, null, 2));
  console.log(`${checks.length} V2 dialogue gaze browser checks passed; ${output}`);
} catch (error) {
  await page.screenshot({ path: output + '/failure.png', fullPage: true }).catch(() => {});
  evidence.failureSnapshot = await snap().catch(() => null);
  if (chat && !chat.isClosed()) evidence.chatFailure = await snap(chat, '__chatV2Avatar').catch(() => null);
  await writeFile(output + '/results.json', JSON.stringify({ ok: false, checks, errors,
    failure: error.message, stack: error.stack, evidence }, null, 2));
  throw error;
} finally { await browser.close(); }
