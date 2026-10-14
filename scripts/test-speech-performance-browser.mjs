// Real app, PlaybackQueue, avatar and daily rig; controlled WS and synthetic WAV.
// The native AudioContext decodes/plays a tone. No LLM, TTS, mic or data writes.
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { chromium } from '@playwright/test';

const origin = process.env.AVATAR_TEST_ORIGIN || 'http://127.0.0.1:8000';
const out = 'eval/avatar/speech-performance';
const browser = await chromium.launch({ headless: true, args: ['--enable-unsafe-swiftshader', '--autoplay-policy=no-user-gesture-required'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 920 }, reducedMotion: 'no-preference' });
page.setDefaultTimeout(15000);
const checks = [], errors = [], forbiddenRequests = [];
page.on('pageerror', error => errors.push(error.message));
const pass = name => checks.push(name);
const wav = seconds => {
  const rate = 16000, length = Math.round(seconds * rate), bytes = Buffer.alloc(44 + length * 2);
  bytes.write('RIFF', 0); bytes.writeUInt32LE(bytes.length - 8, 4); bytes.write('WAVE', 8);
  bytes.write('fmt ', 12); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(rate, 24); bytes.writeUInt32LE(rate * 2, 28);
  bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34); bytes.write('data', 36);
  bytes.writeUInt32LE(length * 2, 40);
  for (let i = 0; i < length; i++) bytes.writeInt16LE(Math.round(Math.sin(i * 2 * Math.PI * 180 / rate) * 2000), 44 + i * 2);
  return bytes.toString('base64');
};
const cue = (text, anchor, intent, gesture, strength) => {
  const chars = Array.from(text), match = Array.from(anchor);
  const start = chars.findIndex((_, i) => chars.slice(i, i + match.length).join('') === anchor);
  assert(start >= 0, 'fixture anchor belongs to spoken text');
  return { intent, gesture, anchor, start_char: start, end_char: start + match.length, strength };
};
const plan = cues => ({ version: 1, source: 'llm', cues });
const emit = value => page.evaluate(message => window.speechHarness.ws.emit(message), value);
const submit = async text => {
  await page.locator('#textInput').fill(text); await page.locator('#sendBtn').click();
  return page.evaluate(() => window.speechHarness.sent.findLast(value => value.type === 'text').turn_id);
};
const begin = async text => {
  const turn_id = await submit('离线动作测试');
  await emit({ type: 'thinking', turn_id });
  await emit({ type: 'emotion', turn_id, emotion: 'base' });
  await emit({ type: 'text_delta', turn_id, delta: text });
  return turn_id;
};
const sourceCount = () => page.evaluate(() => window.speechHarness.starts.length);
const active = async name => {
  await page.waitForFunction(name => {
    const sample = window.__avatar.performance.sample();
    return sample.name === name && sample.weight > .8;
  }, name);
  return page.evaluate(() => window.speechHarness.snapshot());
};
const capturePose = async filename => {
  // Freeze the real audio clock while the renderer settles and captures. A
  // screenshot can otherwise outlast a short gesture on software WebGL.
  await page.evaluate(async () => {
    await window.speechHarness.ctx.suspend();
    await new Promise(resolve => {
      let frames = 0;
      const settle = () => ++frames >= 10 ? resolve() : requestAnimationFrame(settle);
      requestAnimationFrame(settle);
    });
  });
  const before = await page.evaluate(() => window.speechHarness.snapshot());
  await page.locator('#avatarStage').screenshot({ path: `${out}/${filename}.png` });
  const after = await page.evaluate(() => window.speechHarness.snapshot());
  assert.equal(after.at, before.at, 'suspended native audio clock remains authoritative during capture');
  assert.equal(after.performance.name, before.performance.name);
  assert.equal(after.performance.phase, before.performance.phase);
  await page.evaluate(() => window.speechHarness.ctx.resume());
};

try {
  await mkdir(out, { recursive: true });
  await page.route('**/api/**', async route => {
    if (route.request().method() !== 'GET') {
      forbiddenRequests.push(`${route.request().method()} ${route.request().url()}`);
      return route.abort();
    }
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/avatar') return route.continue();
    if (pathname === '/api/persona') return route.fulfill({ json: { name: '动作测试', tagline: '受控消息', voices: ['base'], default_voice: 'base' } });
    if (pathname === '/api/preferences/proactivity') return route.fulfill({ json: { timezone: 'Asia/Shanghai', reminder_enabled: false } });
    return route.fulfill({ json: { count: 0, items: [], context: '' } });
  });
  await page.addInitScript(() => {
    const h = window.speechHarness = { sent: [], starts: [], ends: [], decodes: [], holdDecode: false, speaks: [], events: [], samples: [], microphoneRequests: 0 };
    if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = () => {
      h.microphoneRequests++;
      return Promise.reject(new Error('Microphone access forbidden in offline motion test'));
    };
    class WS {
      static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
      constructor() {
        h.ws = this; this.readyState = 0;
        setTimeout(() => { this.readyState = 1; this.onopen?.({}); this.emit({ type: 'ready', sample_rate: 16000, session_id: 'speech-test', persona: '动作测试' }); }, 0);
      }
      send(data) { if (this.readyState !== 1) throw Error('closed fixture socket'); h.sent.push(JSON.parse(data)); }
      emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
      close() { this.readyState = 3; this.onclose?.({}); }
    }
    window.WebSocket = WS;
    const NativeAudioContext = window.AudioContext || window.webkitAudioContext;
    window.AudioContext = class extends NativeAudioContext {
      constructor(...args) { super(...args); h.ctx = this; }
      createBufferSource() {
        const source = super.createBufferSource(), start = source.start.bind(source);
        source.start = (...args) => { h.starts.push({ at: this.currentTime, args, duration: source.buffer.duration }); start(...args); };
        source.addEventListener('ended', () => h.ends.push({ at: this.currentTime }));
        return source;
      }
      decodeAudioData(bytes) {
        return super.decodeAudioData(bytes).then(buffer => h.holdDecode
          ? new Promise(resolve => h.decodes.push({ duration: buffer.duration, release: () => resolve(buffer) })) : buffer);
      }
    };
    h.release = () => { h.holdDecode = false; for (const value of h.decodes.splice(0)) value.release(); };
  });
  await page.goto(`${origin}/?avatar=seed-daily`);
  await page.waitForFunction(() => document.querySelector('#avatarRendererStatus')?.textContent.includes('及肩短发与日常外套已启用'), {}, { timeout: 90000 });
  await page.evaluate(async () => {
    const a = window.__avatar = (await import('/static/avatar/index.js')).avatar, h = window.speechHarness;
    const originalSpeak = a.speak.bind(a), originalEvent = a.onMessage.bind(a);
    a.speak = (text, ctx, startedAt, duration, options) => {
      h.speaks.push({ text, startedAt, duration, options, sourceCount: h.starts.length });
      return originalSpeak(text, ctx, startedAt, duration, options);
    };
    a.onMessage = message => { h.events.push(message); return originalEvent(message); };
    h.snapshot = () => {
      const state = a.svg._state;
      const arms = Object.fromEntries(Object.entries(state.arms).map(([name, bone]) => [name, bone.node.quaternion.angleTo(bone.rest)]));
      let finite = Object.values(arms).every(Number.isFinite);
      state.vrm.scene.traverse(node => { finite &&= node.matrixWorld.elements.every(Number.isFinite); });
      const latest = h.speaks.at(-1);
      return { performance: a.performance.sample(), arms, speaking: a.lip.speaking, finite,
        at: h.ctx?.currentTime || 0, elapsed: latest ? h.ctx.currentTime - latest.startedAt : 0 };
    };
    let last = -Infinity;
    const watch = () => {
      const value = h.snapshot();
      if (value.at - last >= .035) { h.samples.push(value); last = value.at; }
      requestAnimationFrame(watch);
    };
    watch();
  });
  assert.equal(await page.locator('#avatarStage canvas').count(), 1);
  pass('real daily VRM loaded with real application and controlled transport');

  const text = '🙂先看这个方案，我觉得可以试试；这个办法可以慢慢展开。';
  const selfCue = cue(text, '我觉得', 'self_reference', 'self', .55);
  const palmCue = cue(text, '这个办法', 'explanation', 'palm', .35);
  const word_timings = [
    { text: '🙂先看这个方案，', start: 0, end: 1.1 }, { text: '我觉得', start: 1.1, end: 1.4 },
    { text: '可以试试；', start: 1.4, end: 2.2 }, { text: '这个办法', start: 2.5, end: 2.9 },
    { text: '可以慢慢展开。', start: 2.9, end: 4.1 },
  ];
  const turn = await begin(text);
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null, 'text_delta never starts speech cue');
  assert.equal(await sourceCount(), 0, 'text_delta never starts audio');
  await page.evaluate(() => { window.speechHarness.holdDecode = true; });
  await emit({ type: 'audio', turn_id: turn, index: 0, text, emotion: 'base', data: wav(4.8), performance: plan([selfCue, palmCue]), word_timings });
  await page.waitForFunction(() => window.speechHarness.decodes.length === 1);
  await emit({ type: 'done', turn_id: turn, text });
  assert.equal(await sourceCount(), 0);
  assert.equal(await page.evaluate(() => window.speechHarness.speaks.length), 0, 'queued metadata does not call Avatar.speak while decode is held');
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null, 'done does not execute queued speech cues');
  await page.locator('#avatarStage').screenshot({ path: `${out}/before-playback.png` });
  pass('text, audio receipt, pending native decode and done all precede motion playback');

  await page.evaluate(() => window.speechHarness.release());
  await page.waitForFunction(() => window.speechHarness.starts.length === 1);
  const actual = await page.evaluate(() => window.speechHarness.speaks[0]);
  assert.deepEqual(actual.options.performance, plan([selfCue, palmCue]));
  assert.deepEqual(actual.options.wordTimings, word_timings);
  assert.equal(actual.sourceCount, 1, 'native source.start is called before Avatar.speak');
  assert(Math.abs(actual.duration - 4.8) < .01, 'native WAV decode supplies actual duration');
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null, 'late anchor does not animate at sentence start');
  pass('PlaybackQueue passes intact Unicode cue offsets and word timings only at actual source.start');

  const self = await active('self');
  assert.equal(self.performance.intent, 'self_reference');
  assert.equal(self.performance.anchor, '我觉得');
  assert.equal(self.performance.alignment, 'provider');
  assert.equal(self.performance.strength, .55);
  assert(self.elapsed >= .8 && self.elapsed < 2, 'self cue appears around the provider anchor');
  assert(self.finite && self.arms.rightUpperArm > .05, 'cue reaches finite real arm bones');
  await capturePose('self-provider');
  const palm = await active('palm');
  assert.equal(palm.performance.intent, 'explanation');
  assert.equal(palm.performance.anchor, '这个办法');
  assert.equal(palm.performance.alignment, 'provider');
  assert.equal(palm.performance.strength, .35);
  assert(palm.elapsed >= 2.1 && palm.elapsed < 3.5, 'palm follows the second anchor');
  assert(palm.finite && palm.arms.rightUpperArm > .05);
  await capturePose('palm-provider');
  await page.waitForFunction(() => window.speechHarness.ends.length >= 1 && !window.__avatar.lip.speaking);
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null);
  pass('two distinct intent gestures track native audio clock, phrase anchors and strengths');

  const neutralText = '你好，这是保持自然姿态的一句话。';
  const neutral = await begin(neutralText), beforeNeutral = await sourceCount();
  await emit({ type: 'audio', turn_id: neutral, index: 0, text: neutralText, emotion: 'base', data: wav(1.6), performance: plan([]) });
  await emit({ type: 'done', turn_id: neutral, text: neutralText });
  await page.waitForFunction(count => window.speechHarness.starts.length === count + 1, beforeNeutral);
  await page.waitForFunction(() => window.speechHarness.ends.length >= 2 && !window.__avatar.lip.speaking);
  const neutralSamples = await page.evaluate(() => {
    const call = window.speechHarness.speaks[1];
    return window.speechHarness.samples.filter(value => value.at >= call.startedAt && value.at < call.startedAt + call.duration);
  });
  assert(neutralSamples.length > 5 && neutralSamples.every(value => value.performance.name === null), 'explicit neutral suppresses greeting regex throughout playback');
  pass('explicit neutral is authoritative even when spoken text contains a greeting keyword');

  const estimatedText = '我们先把前面的想法讲清楚，后面我觉得可以试试。';
  const estimatedTurn = await begin(estimatedText), beforeEstimated = await sourceCount();
  const estimatedCue = cue(estimatedText, '我觉得', 'self_reference', 'self', .4);
  await emit({ type: 'audio', turn_id: estimatedTurn, index: 0, text: estimatedText, emotion: 'base', data: wav(3.4), performance: plan([estimatedCue]) });
  await page.waitForFunction(count => window.speechHarness.starts.length === count + 1, beforeEstimated);
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null, 'estimated late cue waits for its phrase');
  const estimated = await active('self');
  assert.equal(estimated.performance.alignment, 'estimated');
  assert(estimated.elapsed > 1, 'missing word timestamps does not pin a late phrase to the beginning');
  await capturePose('self-estimated');
  pass('missing provider word timestamps uses visibly declared estimated phrase timing');

  // Hold prefetch for a second audio chunk, then interrupt the active chunk.
  await page.evaluate(() => { window.speechHarness.holdDecode = true; });
  const queuedText = '再见，这段排队声音不应该复活。';
  await emit({ type: 'audio', turn_id: estimatedTurn, index: 1, text: queuedText, emotion: 'base', data: wav(1.8), performance: plan([cue(queuedText, '再见', 'farewell', 'wave', .5)]) });
  await page.waitForFunction(() => window.speechHarness.decodes.length === 1);
  const next = await submit('换下一轮'), counts = await page.evaluate(() => ({ starts: window.speechHarness.starts.length, speaks: window.speechHarness.speaks.length, events: window.speechHarness.events.length }));
  assert.notEqual(next, estimatedTurn);
  await emit({ type: 'audio', turn_id: estimatedTurn, index: 2, text: queuedText, emotion: 'base', data: wav(1.8), performance: plan([cue(queuedText, '再见', 'farewell', 'wave', .5)]) });
  await emit({ type: 'text_delta', turn_id: estimatedTurn, delta: '迟到正文' });
  await emit({ type: 'done', turn_id: estimatedTurn, text: '迟到正文' });
  assert.equal(await page.evaluate(() => window.speechHarness.events.length), counts.events, 'retired turn events do not reach avatar');
  await page.evaluate(() => window.speechHarness.release());
  await page.waitForFunction(() => {
    const value = window.speechHarness.snapshot();
    return !value.speaking && value.performance.name === null && Object.values(value.arms).every(angle => angle < .025);
  });
  assert.equal(await sourceCount(), counts.starts, 'late prefetched decode never starts canceled audio');
  assert.equal(await page.evaluate(() => window.speechHarness.speaks.length), counts.speaks, 'canceled queued cue never reaches speak');
  await page.locator('#avatarStage').screenshot({ path: `${out}/interrupted-rest.png` });
  await emit({ type: 'done', turn_id: next, text: '' });
  pass('interruption clears active and queued cues, rejects stale turn events and late native decode');

  assert.equal(await page.evaluate(() => window.speechHarness.microphoneRequests), 0, 'test never asks for microphone');
  await page.goto(`${origin}/static/avatar_lab.html?model=daily`);
  await page.waitForFunction(() => window.__avatarReady && window.__avatar.svg._state?.vrm?.meta.name === 'Seed-san Daily Study', {}, { timeout: 90000 });
  await page.selectOption('#performanceExample', 'suggest');
  await page.locator('#performanceDemo').click();
  assert.equal(await page.locator('#eyeFocus').isChecked(), false, 'semantic demo switches from eye closeup to visible body');
  assert.equal(await page.evaluate(() => window.__avatar.svg._state.eyeFocus), false, 'camera follows semantic demo framing');
  await page.waitForFunction(() => {
    const frame = window.__avatar.performance.sample();
    return frame.name === 'self' && frame.weight > .8 && frame.alignment === 'estimated';
  });
  assert.match(await page.locator('#performanceStatus').textContent(), /我觉得.*估算对齐/);
  await page.evaluate(() => window.speechHarness.ctx.suspend());
  await page.locator('#viewport').screenshot({ path: `${out}/lab-semantic-self.png` });
  await page.evaluate(() => window.speechHarness.ctx.resume());
  await page.waitForFunction(() => window.__avatar.performance.sample().name === 'palm');
  assert.match(await page.locator('#performanceStatus').textContent(), /这个办法.*估算对齐/);
  await page.selectOption('#performanceExample', 'quiet');
  await page.locator('#performanceDemo').click();
  assert.equal(await page.evaluate(() => window.__avatar.performance.sample().name), null);
  assert.equal(await page.evaluate(() => window.__avatar.performance.queue.length), 0);
  await page.locator('#interrupt').click();
  assert.equal(await page.evaluate(() => window.__avatar.lip.speaking), false);
  pass('daily laboratory preset exposes both phrase cues, declared estimated alignment, neutral and interruption');

  assert.deepEqual(errors, [], 'no browser page errors');
  assert.deepEqual(forbiddenRequests, [], 'no attempted API mutation');
  pass('no browser page errors, microphone access, paid requests or API writes');
  await writeFile(`${out}/results.json`, JSON.stringify({ ok: true, checks, errors, forbiddenRequests, self, palm, estimated,
    evidence: 'Real app, Avatar, PlaybackQueue and daily VRM; controlled WebSocket, native AudioContext and generated 16 kHz WAV tone. Provider timestamps are test fixtures, not StepAudio timestamps. Headless software rendering is not physical device or real voice quality validation.',
    visualQuality: 'Captures show actual pose and model. Passing finite-bone checks alone does not prove absence of clothing intersections or professional motion quality.',
  }, null, 2));
  console.log(`Speech performance browser regression passed: ${checks.length} check groups. Evidence: ${out}/results.json`);
} finally { await browser.close(); }
