/* Production Avatar + shipped V3 VRM regression. The laboratory is sampled
 * deterministically; the chat section keeps the real app/renderer and replaces
 * only WebSocket, microphone and audio device/decode boundaries. */
import assert from 'node:assert/strict';
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const output = `eval/avatar/dialogue-gaze/${new Date().toISOString().replace(/[:.]/g, '-')}`;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true,
  args: process.platform === 'win32' ? ['--use-angle=d3d11'] : [] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 } });
const errors = [], checks = [], evidence = {};
page.on('pageerror', error => errors.push(`lab: ${error.message}`));
const check = (condition, label) => { assert.ok(condition, label); checks.push(label); };
const near = (a, b, tolerance = 1e-6) => Number.isFinite(a) && Math.abs(a - b) <= tolerance;

async function ready(profile = 'seed-lids') {
  await page.waitForFunction(profile => window.__avatarReady &&
    window.__avatar?.svg.getEyeCapabilities?.().profile === profile,
  profile, { timeout: 90000 });
}
async function snapshot(target = page, avatarName = '__avatar') {
  return target.evaluate(name => {
    const a = window[name], s = a.svg._state, native = {};
    s?.vrm?.scene.traverse(mesh => {
      for (const [key, index] of Object.entries(mesh.morphTargetDictionary || {}))
        native[key.slice(key.lastIndexOf('.') + 1)] ??= mesh.morphTargetInfluences[index];
    });
    return { dialogue: a.dialogueFrame, director: a.dialogueGaze.sample(),
      params: { ...a.eyeFrame?.params }, target: { ...a.store.target }, native,
      gaze: { mode: a.gazeMode, targetName: a.idle.targetName,
        anchor: { ...a.idle.customAnchor }, context: { ...a.idle.gazeContext }, owned: a.idle.gazeOwned },
      gesture: a.gesture.active?.name || null, preview: a.eyeExpression.sample().active,
      blink: a.eyeFrame?.blink, lip: a.lip.speaking,
      blinkLeft: s?.vrm?.expressionManager.getValue('blinkLeft'),
      blinkRight: s?.vrm?.expressionManager.getValue('blinkRight'),
      vowels: ['aa', 'ih', 'ou', 'ee', 'oh'].map(key => s?.vrm?.expressionManager.getValue(key) || 0) };
  }, avatarName);
}
async function freeze() {
  await page.evaluate(() => {
    const a = window.__avatar;
    cancelAnimationFrame(a._raf); a._raf = 0;
    a.idle.cfg.energy.drain_per_sec = 0; a.idle.cfg.energy.drain_speaking = 0;
    a.idle.energy = 1; a.idle.nextBlink = 1e6;
    a.gesture.nextIdleLook = 1e6;
    window.__dialogueBaseTracks ??= { ...a.gesture.tracks };
    window.__dialogueBaseEmotions ??= { ...a.emotion.table };
  });
}
async function advance(seconds, { repeatState = null, audioAdvance = true } = {}) {
  return page.evaluate(({ seconds, repeatState, audioAdvance }) => {
    const a = window.__avatar, state = a.svg._state, render = state?.renderer.render;
    const trace = [];
    if (render) state.renderer.render = () => {};
    try {
      const count = Math.ceil(seconds * 60), dt = seconds / count;
      for (let i = 0; i < count; i++) {
        if (repeatState) a.setDialogueState(repeatState);
        if (audioAdvance && window.__dialogueAudio) window.__dialogueAudio.currentTime += dt;
        a.clock += dt; a._frame(dt);
        trace.push({ state: a.dialogueFrame.state, phase: a.dialogueFrame.phase,
          age: a.dialogueFrame.age, owner: a.dialogueFrame.owner,
          x: a.eyeFrame.params.ParamEyeBallX, y: a.eyeFrame.params.ParamEyeBallY,
          blink: a.eyeFrame.blink, mouth: a.eyeFrame.params.ParamMouthOpenY });
      }
    } finally {
      if (render) { state.renderer.render = render; render.call(state.renderer, state.scene, state.camera); }
    }
    return trace;
  }, { seconds, repeatState, audioAdvance });
}
async function reset() {
  await page.evaluate(() => {
    const a = window.__avatar;
    a.stopSpeaking(); a.gesture.stopAll(); a.face.setMimicryMode(false);
    a.gesture.setTracks(window.__dialogueBaseTracks);
    a.gesture.nextIdleLook = 1e6;
    a.emotion.setTable(window.__dialogueBaseEmotions); a.setEmotion('base');
    a.setEyeExpression(null); a.setReducedMotion(false);
    a.setDialogueGazeEnabled(true); a.dialogueGaze.stop('test-reset');
    a.setDialogueState('idle'); a.setGazeMode('natural');
    a.idle.setGaze('viewer', true); a.idle.claimGaze(false);
    a.idle.blinkPhase = -1; a.idle.blinkHold = 0; a.idle.eyeLevel = 1; a.idle.nextBlink = 1e6;
    window.__dialogueAudio = { currentTime: 100, state: 'running' };
    a.attachOutputAnalyser(null); a.setAudioContext(window.__dialogueAudio);
  });
  await advance(.8);
}
const state = id => page.evaluate(id => window.__avatar.setDialogueState(id), id);
const event = message => page.evaluate(message => window.__avatar.onMessage(message), message);
async function startClip(duration = 1, future = 0) {
  await page.evaluate(({ duration, future }) => {
    const a = window.__avatar, clock = window.__dialogueAudio;
    a.speak('我们可以继续看看。', clock, clock.currentTime + future, duration,
      { performance: { version: 1, source: 'llm', cues: [] } });
  }, { duration, future });
}

async function installChatHarness(chat) {
  await chat.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/avatar') return route.continue();
    const data = pathname === '/api/persona'
      ? { name: '测试', tagline: '对话眼神回归', voices: ['base', '认真'], default_voice: 'base' }
      : { count: 0, items: [], tasks: [], notes: [] };
    return route.fulfill({ json: data });
  });
  await chat.addInitScript(() => {
    const h = window.dialogueHarness = { sent: [], sources: [], processors: [], tracks: [],
      decodeWait: [], holdDecode: false, now: 1, started: 0, stopped: 0 };
    class WS {
      static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
      constructor() { this.readyState = 0; h.ws = this; setTimeout(() => {
        this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000,
          session_id: 'dialogue-gaze-test', persona: '测试' });
      }, 0); }
      send(data) { h.sent.push(typeof data === 'string' ? JSON.parse(data) : { binary: true }); }
      emit(message) { return this.onmessage?.({ data: JSON.stringify(message) }); }
      close() { this.readyState = 3; this.onclose?.({}); }
    }
    window.WebSocket = WS;
    const node = () => ({ connect() { return this; }, disconnect() {}, gain: { value: 1 } });
    class AC {
      constructor() { this.sampleRate = 48000; this.state = 'running'; this.destination = node(); }
      get currentTime() { return h.now; }
      resume() { this.state = 'running'; return Promise.resolve(); }
      close() { this.state = 'closed'; return Promise.resolve(); }
      createGain() { return node(); }
      createAnalyser() { return { ...node(), fftSize: 512,
        get frequencyBinCount() { return this.fftSize / 2; },
        getFloatTimeDomainData(a) { a.fill(0); }, getByteFrequencyData(a) { a.fill(0); } }; }
      createMediaStreamSource() { return node(); }
      createScriptProcessor(size) { const p = { ...node(), size, context: this,
        onaudioprocess: null }; h.processors.push(p); return p; }
      createBuffer(channels, length, rate) { const samples = new Float32Array(length);
        return { length, numberOfChannels: channels, duration: length / rate,
          getChannelData() { return samples; } }; }
      createBufferSource() { const s = { ...node(), onended: null,
        start(at) { this.startedAt = at; h.sources.push(this); h.started++; },
        stop() { h.stopped++; h.sources = h.sources.filter(item => item !== this); this.onended?.(); } };
        return s; }
      decodeAudioData() { const buffer = { duration: 2, length: 96000, numberOfChannels: 1 };
        if (h.holdDecode) return new Promise(resolve => h.decodeWait.push(() => resolve(buffer)));
        return Promise.resolve(buffer); }
    }
    window.AudioContext = AC;
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      addEventListener() {}, getUserMedia: async () => {
        const track = { stopped: false, stop() { this.stopped = true; },
          getSettings() { return { echoCancellation: true, noiseSuppression: false }; },
          addEventListener() {}, removeEventListener() {} };
        h.tracks.push(track); return { getTracks: () => [track], getAudioTracks: () => [track] };
      } } });
    h.finish = () => { h.now += 2.01; for (const source of h.sources.splice(0)) source.onended?.(); };
  });
}

let chat;
try {
  await page.goto(origin + '/static/avatar_lab.html?model=daily-lids&dialogue=1');
  await ready();
  check(await page.locator('#dialoguePanel').isVisible(), 'dialogue URL exposes the experiment panel');
  check(await page.locator('#dialogueGaze').isChecked(), 'dialogue attention is enabled by default');
  check(await page.locator('[data-gaze="natural"]').getAttribute('aria-pressed') === 'true',
    'dialogue entry selects natural gaze');
  await freeze(); await reset();
  check((await snapshot()).dialogue.state === 'idle', 'idle has no dialogue target ownership');

  await event({ type: 'recording', state: true, turn_id: 'lab-1' });
  const listen = await advance(3.5, { repeatState: 'listening' });
  evidence.listening = { trace: listen, final: await snapshot() };
  check(listen.every(frame => frame.state === 'listening' && frame.owner === 'dialogue'),
    'head/brow-only listening gesture leaves eye motion to dialogue');
  check(Math.max(...listen.map(frame => frame.age)) > 3.4,
    'repeated listening status updates do not restart the state clock');
  check(Math.max(...listen.map(frame => frame.x)) - Math.min(...listen.map(frame => frame.x)) > .005,
    'listening retains visible bounded micro eye motion');
  check(listen.every(frame => Math.hypot(frame.x, frame.y) < .12),
    'listening stays close to the viewer rather than wandering');
  await page.locator('#viewport').screenshot({ path: output + '/listening.png' });

  await event({ type: 'thinking', turn_id: 'lab-1' });
  await advance(.45); const thinking = await snapshot();
  check(thinking.dialogue.phase === 'averted' && thinking.dialogue.owner === 'dialogue',
    'thinking performs a short semantic glance while retaining the legacy head/brow track');
  await page.locator('#viewport').screenshot({ path: output + '/thinking-averted.png' });
  await event({ type: 'emotion', emotion: 'base', turn_id: 'lab-1' });
  await event({ type: 'text_delta', delta: '准备回应', turn_id: 'lab-1' });
  await event({ type: 'done', text: '准备回应', turn_id: 'lab-1' });
  await state('preparing');
  const waiting = await advance(.9, { repeatState: 'preparing' });
  check(waiting.every(frame => frame.state !== 'speaking'),
    'early emotion, tokens and generation done do not claim audible speech');
  check((await snapshot()).dialogue.phase === 'returned', 'a short thought glance returns while audio is still pending');
  evidence.waiting = { thinking, trace: waiting, final: await snapshot() };
  await page.locator('#viewport').screenshot({ path: output + '/thinking-returned.png' });

  await reset(); await event({ type: 'thinking', turn_id: 'lab-repeat' });
  await advance(.5);
  const beforeRepeatedEvent = await snapshot();
  await event({ type: 'thinking', turn_id: 'lab-repeat' });
  await advance(.1); const afterRepeatedEvent = await snapshot();
  check(afterRepeatedEvent.director.age > beforeRepeatedEvent.director.age
    && afterRepeatedEvent.dialogue.phase === 'averted',
  'a repeated thinking event for the same turn does not restart the existing glance');

  await startClip(1, .8); await state('speaking');
  const scheduled = await advance(.5);
  check(scheduled.every(frame => frame.state !== 'speaking' && frame.mouth === 0),
    'future scheduled audio stays in preparation and keeps the mouth closed');
  await advance(.4); const spoken = await snapshot();
  check(spoken.dialogue.state === 'speaking' && spoken.dialogue.phase === 'speaking',
    'the supplied audio clock reaching the clip starts response gaze');
  check(spoken.params.ParamMouthOpenY > 0 && spoken.vowels.some(value => value > 0),
    'response gaze coexists with actual vowel animation on the V3 asset');
  await advance(1); const gap = await snapshot();
  check(gap.dialogue.state === 'preparing' && gap.dialogue.phase === 'attending'
    && near(gap.gaze.anchor.x, 0) && near(gap.gaze.anchor.y, 0),
  'sentence gaps attend to the viewer without replaying the thinking glance');
  await startClip(.5); await advance(.1);
  check((await snapshot()).dialogue.state === 'speaking', 'a later sentence resumes response gaze');
  await page.evaluate(() => window.__avatar.stopSpeaking({ turnPending: true }));
  const between = await advance(1.5);
  check(between.every(frame => frame.phase === 'attending'), 'an explicitly pending sentence gap does not restart thinking');
  evidence.audioGate = { scheduled, spoken, gap, between };

  await reset(); await state('listening');
  await page.evaluate(() => {
    const a = window.__avatar;
    a.gesture.setTracks({ ...a.gesture.tracks, dialogue_test_gaze: { priority: 90,
      hold: true, cooldown: 0, keys: [{ at: 0, dur: .1,
        params: { ParamEyeBallX: -.58, ParamEyeBallY: .2 } }] } });
    a.gesture.play('dialogue_test_gaze', a.clock);
  });
  await advance(.4); const gesture = await snapshot();
  check(gesture.dialogue.owner === 'gesture' && gesture.dialogue.suspended
    && near(gesture.target.ParamEyeBallX, -.58) && near(gesture.target.ParamEyeBallY, .2),
  'an explicit gaze gesture owns its exact eye target without dialogue stacking');
  await reset(); await state('listening');
  await page.evaluate(() => {
    const a = window.__avatar;
    a.emotion.setTable({ ...a.emotion.table, dialogue_test_emotion: {
      arousal: 1, valence: 0, blend: .1, owns_gaze: true,
      shift: { ParamEyeBallX: .46, ParamEyeBallY: -.18 } } });
    a.setEmotion('dialogue_test_emotion');
  });
  await advance(2); const emotion = await snapshot();
  check(emotion.dialogue.owner === 'emotion' && emotion.dialogue.suspended,
    'explicit emotional gaze stays authoritative after its transition finishes');
  check(near(emotion.target.ParamEyeBallX, .46, .03) && near(emotion.target.ParamEyeBallY, -.18, .03),
    'dialogue contributes no extra eye displacement to a stable emotional target');
  evidence.ownership = { gesture, emotion };

  await reset(); await state('thinking');
  await page.evaluate(() => { const a = window.__avatar; a.setGazeMode('follow');
    a.onUserInteraction('mousemove', -.42, .1); });
  await advance(.5); const follow = await snapshot();
  check(follow.dialogue.owner === 'manual' && follow.dialogue.suspended && follow.gaze.anchor.x === -.42,
    'pointer following takes priority over a thinking plan');
  await page.evaluate(() => window.__avatar.setGazeMode('forward')); await advance(.5);
  const forward = await snapshot();
  check(forward.dialogue.suspended && forward.params.ParamEyeBallX === 0 && forward.params.ParamEyeBallY === 0,
    'forward mode retains exact straight-ahead eyes');
  await page.evaluate(() => { const a = window.__avatar; a.setGazeMode('natural'); a.setEyeExpression('shy'); });
  await advance(.8); const preview = await snapshot();
  check(preview.preview && preview.dialogue.owner === 'manual' && preview.dialogue.suspended,
    'manual expression preview retains its gaze offset and suspends dialogue commands');
  evidence.manual = { follow, forward, preview };

  await reset();
  await page.evaluate(() => { const a = window.__avatar; a.setMouseFollow(true);
    a.onUserInteraction('mousemove', -.36, .13); });
  await advance(.3);
  await page.evaluate(() => window.__avatar.setMouseFollow(false));
  await advance(.4); const followReleased = await snapshot();
  check(followReleased.gaze.mode === 'natural' && followReleased.gaze.targetName === 'viewer'
    && Math.hypot(followReleased.params.ParamEyeBallX, followReleased.params.ParamEyeBallY) < .08,
  'disabling idle pointer follow returns to the viewer instead of retaining the old pointer');
  evidence.manual.followReleased = followReleased;

  await reset(); await state('thinking'); await advance(.45);
  const normal = await snapshot();
  await page.evaluate(() => window.__avatar.setReducedMotion(true)); await advance(.2);
  const reduced = await snapshot();
  evidence.reducedMotion = { normal, reduced };
  check(Math.abs(reduced.gaze.anchor.x) < Math.abs(normal.gaze.anchor.x)
    && Math.abs(reduced.gaze.anchor.y) < Math.abs(normal.gaze.anchor.y),
  'reduced motion scales semantic aversion instead of disabling natural blinking');
  await reset(); await state('speaking'); await page.evaluate(() => window.__avatar.setEmotion('得意'));
  await startClip(3); await advance(.8); const smile = await snapshot();
  check(smile.native.neuro_lower_lid_raise_L > .01 && smile.native.neuro_lower_lid_raise_R > .01,
    'ordinary dialogue response preserves independent lower lids and smile intent');
  await page.evaluate(() => window.__avatar.blinkEyes()); await advance(.075); const closed = await snapshot();
  check(closed.blink >= .999 && closed.blinkLeft === 1 && closed.blinkRight === 1,
    'dialogue response allows complete bilateral blink');
  check(['neuro_lower_lid_raise_L', 'neuro_lower_lid_raise_R', 'neuro_eye_smile_L', 'neuro_eye_smile_R']
    .every(key => closed.native[key] === 0) && closed.vowels.some(value => value > 0),
  'full blink retires added eye shapes while speech vowels remain active');
  await page.locator('#viewport').screenshot({ path: output + '/response-blink-mouth.png' });
  await advance(.3); const reopened = await snapshot();
  check(reopened.native.neuro_lower_lid_raise_L > 0 && reopened.dialogue.state === 'speaking',
    'blink reopening restores the lower-lid intent during the same response');
  evidence.composition = { normal, reduced, smile, closed, reopened };

  await page.evaluate(() => { const a = window.__avatar; a.onBargeIn(); });
  await advance(5); const stopped = await snapshot();
  check(stopped.dialogue.state !== 'speaking' && !stopped.lip && stopped.gesture === null,
    'interruption cancels speech and every old gesture/attention clip');
  check(near(stopped.gaze.anchor.x, 0) && near(stopped.gaze.anchor.y, 0),
    'interruption returns attention to the viewer without resurrecting the old glance');
  await reset(); await state('thinking'); await advance(.45);
  await page.evaluate(() => window.__avatar.setDialogueGazeEnabled(false)); await advance(1.5);
  check(!(await snapshot()).dialogue.active, 'the dialogue toggle releases semantic target ownership');

  await reset(); await state('thinking'); await advance(.45);
  const beforeCalibration = await snapshot();
  await page.evaluate(async () => { const { AvatarCalibration } = await import('/static/avatar/calibration.js');
    window.__dialogueCalibration = new AvatarCalibration(window.__avatar);
    window.__dialogueCalibration.enter({ presentation: true, eyeFocus: true }); });
  await page.waitForTimeout(300);
  check(await page.evaluate(age => window.__avatar.dialogueGaze.age === age,
    beforeCalibration.director.age), 'a frozen calibration session does not advance the dialogue plan');
  await page.evaluate(() => window.__dialogueCalibration.exit());
  const afterCalibration = await snapshot();
  check(JSON.stringify(afterCalibration.director) === JSON.stringify(beforeCalibration.director)
    && JSON.stringify(afterCalibration.dialogue) === JSON.stringify(beforeCalibration.dialogue),
  'calibration restores exact dialogue phase, age, command and prior ownership');
  evidence.calibration = { beforeCalibration, afterCalibration };
  await page.evaluate(() => window.__avatar._start());
  await page.locator('#dailyEyes').click(); await ready('seed-eyes');
  check(await page.evaluate(() => typeof window.__avatar.dialogueGaze.sample === 'function'),
    'dialogue intent survives switching to an older asset');
  await page.locator('#dailyLids').click(); await ready();
  await page.setViewportSize({ width: 430, height: 900 });
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    'dialogue controls fit a mobile viewport');
  await page.locator('#dialoguePanel').screenshot({ path: output + '/mobile-controls.png' });
  await page.setViewportSize({ width: 1280, height: 920 });
  await page.locator('#dialogueGaze').check();
  for (const example of ['normal', 'delay', 'interrupt']) {
    await page.selectOption('#dialogueExample', example);
    await page.locator('#dialogueDemo').click();
    await page.waitForFunction(() => window.__avatar.dialogueFrame?.state !== 'idle');
    await page.locator('#dialogueStop').click();
    await page.waitForTimeout(150);
    check(!(await snapshot()).lip, `${example} UI demo stop releases mouth playback`);
  }
  // The delayed demo's audio boundary is 8 seconds after start. Observe beyond
  // that boundary so a stale scheduled step cannot pass a short stop check.
  await page.waitForTimeout(8500);
  check(!(await snapshot()).lip && (await snapshot()).dialogue.state !== 'speaking',
    'stopped demo timers cannot revive scheduled audio');
  // Exercise the asynchronous boundary itself, not just timers that were
  // already created: stop while AudioContext.resume() has not resolved.
  await page.locator('#speak').click();
  await page.waitForFunction(() => window.__avatar.audioCtx instanceof AudioContext);
  await page.locator('#dialogueStop').click();
  await page.evaluate(() => {
    const a = window.__avatar, ctx = a.audioCtx;
    window.__dialogueResumeContext = ctx;
    window.__dialogueOriginalResume = ctx.resume;
    window.__dialogueOriginalOnMessage = a.onMessage;
    window.__dialogueResumeEvents = [];
    ctx.resume = () => new Promise(resolve => { window.__dialogueResumeResolve = resolve; });
    a.onMessage = function (message) {
      window.__dialogueResumeEvents.push(message);
      return window.__dialogueOriginalOnMessage.call(this, message);
    };
  });
  await page.locator('#dialogueDemo').click();
  await page.waitForFunction(() => typeof window.__dialogueResumeResolve === 'function');
  await page.locator('#dialogueStop').click();
  await page.evaluate(() => window.__dialogueResumeResolve());
  await page.waitForTimeout(200);
  check(await page.evaluate(() => !window.__avatar.lip.timeline &&
    !window.__dialogueResumeEvents.some(message => message.type === 'recording' && message.state)),
  'stop during pending audio resume prevents later demo initialization or recording events');
  check((await snapshot()).dialogue.state === 'idle', 'a stopped pending resume cannot recreate an attention plan');
  await page.evaluate(() => {
    window.__dialogueResumeContext.resume = window.__dialogueOriginalResume;
    window.__avatar.onMessage = window.__dialogueOriginalOnMessage;
  });
  await page.screenshot({ path: output + '/laboratory.png', fullPage: true });

  chat = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  chat.on('pageerror', error => errors.push(`chat: ${error.message}`));
  await installChatHarness(chat);
  await chat.goto(origin + '/?avatar=seed-daily-lids');
  await chat.waitForFunction(() => document.querySelector('#avatarRenderer')?.value === 'seed-daily-lids'
    && document.querySelector('#avatarRendererStatus')?.textContent.includes('已启用'), {}, { timeout: 90000 });
  await chat.evaluate(async () => { window.__chatAvatar = (await import('/static/avatar/index.js')).avatar; });
  check((await snapshot(chat, '__chatAvatar')).native.neuro_lower_lid_raise_L !== undefined,
    'mock transport chat mounts the real shipped V3 asset and real Avatar');
  const emit = message => chat.evaluate(message => dialogueHarness.ws.emit(message), message);
  const sendText = async text => { await chat.locator('#textInput').fill(text); await chat.locator('#sendBtn').click();
    return chat.evaluate(() => dialogueHarness.sent.filter(message => message.type === 'text').at(-1)); };
  const first = await sendText('测试倾听和思考');
  await emit({ type: 'recording', state: true, turn_id: first.turn_id });
  await chat.waitForFunction(() => window.__chatAvatar.dialogueFrame?.state === 'listening');
  check((await snapshot(chat, '__chatAvatar')).dialogue.state === 'listening', 'accepted real app recording events enter listening');
  await emit({ type: 'asr', text: '测试倾听和思考', turn_id: first.turn_id });
  await emit({ type: 'thinking', turn_id: first.turn_id });
  await chat.waitForFunction(() => window.__chatAvatar.dialogueFrame?.state === 'thinking');
  await emit({ type: 'emotion', emotion: 'base', turn_id: first.turn_id });
  await emit({ type: 'text_delta', delta: '准备回应。', turn_id: first.turn_id });
  await chat.evaluate(() => { dialogueHarness.holdDecode = true; });
  await emit({ type: 'audio', data: 'AA==', text: '准备回应。', emotion: 'base', turn_id: first.turn_id });
  await chat.waitForFunction(() => dialogueHarness.decodeWait.length === 1);
  await emit({ type: 'done', text: '准备回应。', turn_id: first.turn_id });
  await chat.waitForTimeout(450); const decoding = await snapshot(chat, '__chatAvatar');
  check(decoding.dialogue.state !== 'speaking' && !decoding.lip
    && await chat.evaluate(() => dialogueHarness.started === 0),
  'real app emotion/tokens/done and held decode cannot manufacture audible response');
  await chat.evaluate(() => { dialogueHarness.holdDecode = false; dialogueHarness.decodeWait.shift()(); });
  await chat.waitForFunction(() => dialogueHarness.started === 1 && window.__chatAvatar.dialogueFrame?.state === 'speaking');
  const actualPlaying = await snapshot(chat, '__chatAvatar');
  check(actualPlaying.lip && actualPlaying.dialogue.state === 'speaking',
    'real PlaybackQueue decode completion and source start activate response gaze');
  const initialClipCount = await chat.evaluate(() => window.__chatAvatar.dialogueGaze._clips.length);
  await emit({ type: 'config_ack', emotion: 'base', emotion_error: '测试：语气设置未应用' });
  await chat.waitForTimeout(100);
  check(await chat.evaluate(count => window.__chatAvatar.dialogueGaze._clips.length === count
    && window.__chatAvatar.lip.speaking && window.__chatAvatar.dialogueFrame.state === 'speaking'
    && dialogueHarness.sources.length === 1, initialClipCount),
  'an emotion preference error preserves the currently audible clip and response gaze');
  await emit({ type: 'error', recoverable: true, message: '测试：部分交付失败', turn_id: first.turn_id });
  await chat.waitForTimeout(100);
  check(await chat.evaluate(count => window.__chatAvatar.dialogueGaze._clips.length === count
    && window.__chatAvatar.lip.speaking && window.__chatAvatar.dialogueFrame.state === 'speaking'
    && dialogueHarness.sources.length === 1, initialClipCount),
  'a recoverable server error preserves the currently audible clip and response gaze');
  // An intentionally invalid, tiny payload exercises app voiceError without
  // large allocations or replacing the valid source that is already playing.
  await emit({ type: 'audio', data: { invalid: true }, text: '拒绝的后续片段', turn_id: first.turn_id });
  await chat.waitForTimeout(100);
  check(await chat.evaluate(count => window.__chatAvatar.dialogueGaze._clips.length === count
    && window.__chatAvatar.lip.speaking && window.__chatAvatar.dialogueFrame.state === 'speaking'
    && dialogueHarness.sources.length === 1 && dialogueHarness.started === 1, initialClipCount),
  'a rejected later audio payload reports a voice error without cancelling valid playback');
  await chat.evaluate(() => dialogueHarness.finish());
  await chat.waitForFunction(() => !window.__chatAvatar.lip.speaking && window.__chatAvatar.dialogueFrame?.state === 'idle');
  check((await snapshot(chat, '__chatAvatar')).dialogue.state === 'idle',
    'generation done releases attention only after the real queue finishes playback');

  const second = await sendText('等待旧音频解码');
  await emit({ type: 'thinking', turn_id: second.turn_id });
  await chat.evaluate(() => { dialogueHarness.holdDecode = true; });
  await emit({ type: 'audio', data: 'AA==', text: '旧音频', emotion: 'base', turn_id: second.turn_id });
  await chat.waitForFunction(() => dialogueHarness.decodeWait.length === 1);
  const third = await sendText('现在换问题');
  await emit({ type: 'thinking', turn_id: third.turn_id });
  await chat.evaluate(() => { dialogueHarness.holdDecode = false; dialogueHarness.decodeWait.shift()(); });
  await emit({ type: 'done', text: '过期结果', turn_id: second.turn_id });
  await chat.waitForTimeout(200);
  check(await chat.evaluate(() => dialogueHarness.started === 1), 'superseded pending decode never starts old audio');
  check((await snapshot(chat, '__chatAvatar')).dialogue.state === 'thinking',
    'retired turn events cannot release or restart the current turn attention');
  await emit({ type: 'audio', data: 'AA==', text: '请打断我', emotion: 'base', turn_id: third.turn_id });
  await chat.waitForFunction(() => dialogueHarness.started === 2 && window.__chatAvatar.dialogueFrame?.state === 'speaking');
  await emit({ type: 'interrupted', turn_id: third.turn_id });
  await chat.waitForFunction(() => !window.__chatAvatar.lip.speaking && window.__chatAvatar.dialogueFrame?.state !== 'speaking');
  check(await chat.evaluate(() => dialogueHarness.sources.length === 0), 'accepted interruption stops the real app audio source');
  const interrupt = await snapshot(chat, '__chatAvatar');
  check(!interrupt.lip && interrupt.dialogue.state !== 'speaking', 'accepted app interruption clears the dialogue clip gate');
  evidence.chat = { decoding, actualPlaying, interrupt };
  await chat.screenshot({ path: output + '/chat.png', fullPage: true });
  check(errors.length === 0, `no browser exceptions: ${errors.join('; ')}`);
  await writeFile(output + '/results.json', JSON.stringify({ ok: true, checks, errors, evidence }, null, 2));
  console.log(`${checks.length} dialogue gaze browser checks passed; ${output}`);
} catch (error) {
  if (!page.isClosed()) await page.screenshot({ path: output + '/failure.png', fullPage: true }).catch(() => {});
  if (chat && !chat.isClosed()) {
    await chat.screenshot({ path: output + '/chat-failure.png', fullPage: true }).catch(() => {});
    evidence.chatFailure = await snapshot(chat, '__chatAvatar').catch(() => null);
  }
  await writeFile(output + '/results.json', JSON.stringify({ ok: false, checks, errors, failure: error.message,
    stack: error.stack, evidence }, null, 2));
  throw error;
} finally { await browser.close(); }
