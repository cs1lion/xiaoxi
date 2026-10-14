// Dedicated offline browser test: no server, cloud, real devices, or credentials.
import { chromium } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
const errors = [];
const page = await browser.newPage();
page.setDefaultTimeout(8000);
page.on('pageerror', e => errors.push(e.message));
await page.route('http://localhost/**', async route => {
  const pathname = new URL(route.request().url()).pathname;
  if (pathname === '/') return route.fulfill({contentType:'text/html',body:'<!doctype html><title>handoff target</title>'});
  if (pathname === '/static/voice/capture.js') return route.fulfill({ contentType: 'text/javascript', body: `
    export async function createCapture(options) {
      const h = window.h; h.captureCalls++; h.chunk = options.onChunk; h.captureError = options.onError;
      const capture = {stop(){ h.captureStops++; }};
      if (h.holdCapture) await new Promise(resolve => h.releaseCapture = resolve);
      return capture;
    }` });
  const relative = pathname === '/interpret' ? 'interpret.html' : pathname.replace(/^\/static\//, '');
  if (!['interpret.html','interpret.js','interpret.css','voice/pcm.js'].includes(relative)) return route.abort();
  const body = await readFile(path.join(root, 'web', relative));
  return route.fulfill({ body, contentType: relative.endsWith('.js') ? 'text/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html' });
});
await page.addInitScript(() => {
  const h = window.h = { sent: [], tracks: [], contexts: [], captureCalls: 0, captureStops: 0 };
  class WS {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; h.ws = this; setTimeout(() => { this.readyState = 1; this.onopen?.(); }, 0); }
    send(data) { if (this.readyState !== 1) throw Error('closed'); h.sent.push(typeof data === 'string' ? JSON.parse(data) : { binary: true, bytes: data.byteLength, allZero: new Uint8Array(data).every(x => x === 0) }); }
    emit(m) { this.onmessage?.({ data: JSON.stringify({ session_id: h.sent.findLast(x => x.type === 'start')?.session_id, ...m }) }); }
    close() { this.readyState = 3; this.onclose?.(); }
  }
  window.WebSocket = WS;
  window.AudioContext = class {
    constructor() { h.contexts.push(this); }
    resume() { return Promise.resolve(); }
    close() { this.closed = true; return Promise.resolve(); }
  };
  const stream = display => {
    const track = kind => { const t = { kind, stopped: false, stop() { this.stopped = true; }, addEventListener(type, fn) { if (type === 'ended') this.ended = fn; } }; h.tracks.push(t); return t; };
    const tracks = [...(h.noAudio ? [] : [track('audio')]), ...(display ? [track('video')] : [])];
    return { getTracks: () => tracks, getAudioTracks: () => tracks.filter(t => t.kind === 'audio') };
  };
  const permission = display => {
    h.permissionCalls = (h.permissionCalls || 0) + 1;
    if (h.holdPermission) return new Promise(resolve => h.releasePermission = () => resolve(stream(display)));
    return Promise.resolve(stream(display));
  };
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
    getUserMedia: options => { h.options = options; return permission(false); },
    getDisplayMedia: options => { h.options = options; return permission(true); },
  } });
  h.feed = (seconds, amplitude = .1) => {
    for (let i = 0; i < Math.round(seconds * 50); i++) h.chunk?.({ microphone: new Float32Array(960).fill(amplitude), sampleRate: 48000 });
  };
});
const click = id => page.locator(`#${id}`).click();
const emit = m => page.evaluate(m => h.ws.emit(m), m);
const feed = (seconds, amplitude = .1) => page.evaluate(([s,a]) => h.feed(s,a), [seconds, amplitude]);
const sent = () => page.evaluate(() => h.sent);
const fresh = async () => { await page.goto('http://localhost/interpret'); await page.locator('#start').waitFor(); };
const startReady = async () => { await click('start'); await page.waitForFunction(() => h.sent.some(x => x.type === 'start')); await emit({ type: 'ready' }); await page.waitForFunction(() => h.captureCalls === 1); };
const checks = [];
try {
  await fresh();
  await click('start');
  await page.waitForFunction(() => h.sent.some(x => x.type === 'start'));
  assert.equal(await page.evaluate(() => h.captureCalls), 0);
  assert.match((await sent())[0].session_id, /^[0-9a-f-]{36}$/);
  assert.equal(await page.evaluate(() => new URL(h.ws.url).pathname), '/ws/interpret');
  await emit({ type: 'ready', session_id: 'wrong-session' });
  assert.equal(await page.evaluate(() => h.captureCalls), 0);
  await emit({ type: 'ready' }); await page.waitForFunction(() => h.captureCalls === 1);
  checks.push('ready handshake gates capture; isolated session and endpoint');
  await feed(5, 0); assert.equal((await sent()).filter(x => x.binary).length, 0);
  await feed(.5); await feed(.48, 0); assert.equal((await sent()).filter(x => x.binary).length, 0);
  await feed(.02, 0);
  let messages = await sent(), segments = messages.filter(x => x.type === 'segment');
  assert.equal(segments.length, 1); assert.equal(segments[0].end_ms - segments[0].start_ms, 1000);
  assert.equal(segments[0].start_ms, 5000);
  const idx = messages.findIndex(x => x.type === 'segment'); assert.equal(messages[idx-1].bytes, 32000);
  await emit({ type: 'source', segment_id: 1, text: '<img src=x onerror="window.pwned=1"> Hello' });
  assert.match(await page.locator('.source').innerText(), /<img/);
  assert.equal(await page.locator('#rows img').count(), 0);
  assert.equal(await page.locator('.segment-state').innerText(), '已识别片段，待译');
  await emit({ type: 'translation', segment_id: 1, text: '你好 <script>bad()</script>', latency_ms: 123 });
  assert.match(await page.locator('.translation').innerText(), /你好/);
  assert.match(await page.locator('#latency').innerText(), /123 ms/);
  await emit({ type: 'segment_done', segment_id: 1 });
  await feed(12);
  segments = (await sent()).filter(x => x.type === 'segment');
  assert.equal(segments.length, 4);
  for (const s of segments) assert.ok(s.end_ms - s.start_ms >= 1000 && s.end_ms - s.start_ms <= 4000);
  for (let i=1;i<segments.length;i++) assert.ok(segments[i].start_ms >= segments[i-1].end_ms);
  await feed(4); assert.equal((await sent()).filter(x => x.type === 'segment').length, 4);
  assert.match(await page.locator('.gap').innerText(), /本地缺口/);
  await emit({ type: 'gap', segment_id: 2, message: '服务暂时繁忙' });
  await feed(1); await feed(.5, 0);
  assert.equal((await sent()).filter(x => x.type === 'segment').length, 5);
  await emit({ type: 'error', segment_id: 3, code: 'asr_failed', message: '<b>retry next segment</b>' });
  assert.equal(await page.locator('#stop').isEnabled(), true);
  assert.equal(await page.locator('#rows b').count(), 0);
  await emit({ type: 'segment_done', segment_id: 3 });
  await page.evaluate(() => { h.ws.bufferedAmount = 256 * 1024; });
  await feed(4); assert.equal((await sent()).filter(x => x.type === 'segment').length, 5);
  checks.push('silence/minimum/maximum boundaries, PCM binary pairing, no overlaps, source/translation, XSS, pending and network bounds');
  await page.evaluate(() => { h.lateMessage = h.ws.onmessage; h.oldId = h.sent[0].session_id; });
  await click('stop');
  const before = await page.locator('#rows').innerText();
  await page.evaluate(() => { h.feed(8); h.lateMessage({ data: JSON.stringify({ type: 'translation', session_id: h.oldId, segment_id: 4, text: 'LATE' }) }); });
  assert.equal(await page.locator('#rows').innerText(), before);
  assert.equal(await page.evaluate(() => h.tracks.every(t => t.stopped) && h.contexts.every(c => c.closed) && h.ws.readyState === 3), true);
  assert.equal(await page.evaluate(() => h.captureStops), 1);
  checks.push('stop cancels capture and releases devices/context/socket; late callbacks ignored');
  await fresh(); await page.evaluate(() => { h.holdPermission = true; }); await click('start'); await click('stop');
  await page.evaluate(() => h.releasePermission());
  await page.waitForFunction(() => h.tracks.length && h.tracks.every(t => t.stopped));
  assert.equal(await page.evaluate(() => h.captureCalls), 0);
  checks.push('stop while permission pending never starts capture');
  await fresh(); await page.evaluate(() => { h.holdCapture = true; }); await click('start');
  await page.waitForFunction(() => h.sent.length); await emit({ type:'ready' });
  await page.waitForFunction(() => h.releaseCapture); await click('stop'); await page.evaluate(() => h.releaseCapture());
  await page.waitForFunction(() => h.captureStops === 1);
  checks.push('stop during asynchronous capture/worklet setup cancels returned capture');
  await fresh(); await page.locator('#source').selectOption('display'); await page.evaluate(() => { h.noAudio = true; }); await click('start');
  await page.waitForFunction(() => document.getElementById('status').textContent.includes('未共享音频'));
  assert.equal(await page.evaluate(() => h.tracks.length === 1 && h.tracks.every(t => t.stopped)), true);
  assert.deepEqual(await page.evaluate(() => h.options), {video:true,audio:true});
  await fresh(); await page.locator('#source').selectOption('display'); await startReady();
  await page.evaluate(() => h.tracks.find(t => t.kind === 'video').ended());
  assert.equal(await page.evaluate(() => h.tracks.every(t => t.stopped) && h.contexts.every(c => c.closed)), true);
  checks.push('display options, missing shared audio and video-track ended release all tracks');
  await fresh(); await page.evaluate(() => { navigator.mediaDevices.getUserMedia = undefined; }); await click('start');
  assert.match(await page.locator('#status').innerText(), /不支持/); assert.equal(await page.evaluate(() => h.contexts.length), 0);
  await fresh(); await startReady(); await feed(4);
  await emit({type:'error',code:'busy',message:'<img src=x onerror=alert(1)>'});
  assert.equal(await page.locator('#status img').count(),0);
  assert.equal(await page.evaluate(() => h.tracks.every(t => t.stopped)),true);
  await fresh(); await startReady(); await page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  assert.equal(await page.evaluate(() => h.tracks.every(t => t.stopped) && h.ws.readyState === 3), true);
  checks.push('unsupported API, safe server errors, pagehide cleanup');
  await fresh(); await startReady();
  await page.evaluate(() => {
    for(let i=1;i<=105;i++) { h.feed(1,.1); h.feed(.5,0); h.ws.emit({type:'segment_done',segment_id:i,empty:true}); }
  });
  assert.equal(await page.locator('.segment').count(),100);
  await feed(4); const lastId = (await sent()).filter(x=>x.type==='segment').at(-1).segment_id;
  await click('clear'); await emit({type:'translation',segment_id:lastId,text:'must stay cleared'});
  assert.equal(await page.locator('.segment').count(),0);
  await click('stop');
  checks.push('100-row cap and Clear do not resurrect pending rows');
  // Independent streaming group: mocked transport/audio only; no paid API.
  const streamReady = async () => { await fresh(); await page.locator('#backend').selectOption('streaming'); await startReady(); };
  await streamReady();
  assert.equal(await page.evaluate(() => new URL(h.ws.url).pathname), '/ws/interpret-stream');
  assert.equal(await page.locator('#backend').isDisabled(), true);
  assert.match(await page.locator('#status').innerText(), /流式原文，确认后翻译/);
  await feed(.08, 0); assert.equal((await sent()).filter(x => x.binary).length, 0);
  await feed(.02, 0); await feed(.9, 0);
  assert.equal((await sent()).filter(x => x.binary).length, 10);
  assert.ok((await sent()).filter(x => x.binary).every(x => x.bytes === 3200 && x.allZero));
  await feed(1);
  assert.equal((await sent()).filter(x => x.binary).length, 20);
  assert.equal((await sent()).filter(x => x.type === 'segment').length, 0);
  await emit({type:'source_partial',item_id:'remote-A',text:'I scream',stash:'uncertain suffix'});
  assert.equal(await page.locator('.source').innerText(), 'I scream');
  assert.match(await page.locator('.source-stash').innerText(), /不确定暂存：uncertain suffix/);
  assert.equal(await page.locator('.translation').innerText(), '');
  await emit({type:'source_partial',item_id:'remote-A',text:'Ice cream',stash:'<b>maybe</b>'});
  assert.equal(await page.locator('.source').innerText(), 'Ice cream');
  assert.equal(await page.locator('#rows b').count(), 0);
  await emit({type:'source_final',item_id:'remote-A',text:'Ice cream!'});
  await emit({type:'source_partial',item_id:'remote-A',text:'LATE',stash:'LATE'});
  await emit({type:'source_final',item_id:'remote-A',text:'LATE FINAL'});
  assert.equal(await page.locator('.source').innerText(), 'Ice cream!');
  assert.equal(await page.locator('.source-stash').isHidden(), true);
  await emit({type:'translation',item_id:'remote-A',text:'冰淇淋！',latency_ms:42});
  await emit({type:'translation',item_id:'remote-A',text:'LATE TRANSLATION'});
  assert.equal(await page.locator('.translation').innerText(), '冰淇淋！');
  assert.match(await page.locator('#latency').innerText(), /42 ms/);
  await emit({type:'error',item_id:'remote-B',code:'asr_failed',message:'item failure'});
  assert.equal(await page.locator('#stop').isEnabled(), true);
  await emit({type:'gap',item_id:'remote-C',message:'missing item'});
  await emit({type:'source_partial',item_id:'remote-D',text:'still pending'});
  await page.evaluate(() => { h.lateMessage = h.ws.onmessage; h.oldId = h.sent[0].session_id; });
  await click('stop');
  assert.match(await page.locator('.segment-state').last().innerText(), /已取消/);
  assert.equal(await page.locator('#backend').isEnabled(), true);
  const streamBefore = await page.locator('#rows').innerText();
  await page.evaluate(() => h.lateMessage({data:JSON.stringify({type:'source_final',session_id:h.oldId,item_id:'remote-D',text:'late'})}));
  assert.equal(await page.locator('#rows').innerText(), streamBefore);
  checks.push('streaming: endpoint, disabled selector, continuous silent 100ms PCM without segment metadata, replacing partials, separate stash, final/translation guards, item errors and late stop events');
  await streamReady();
  await page.evaluate(() => {
    for (let i=1;i<=105;i++) h.ws.emit({type:'source_partial',item_id:`id-${i}`,text:`text ${i}`});
  });
  assert.equal(await page.locator('.segment').count(), 100);
  await emit({type:'source_final',item_id:'id-1',text:'no resurrection'});
  assert.equal(await page.locator('.segment').count(), 100);
  await click('clear');
  await emit({type:'source_final',item_id:'id-105',text:'remain cleared'});
  await emit({type:'translation',item_id:'id-105',text:'remain cleared'});
  assert.equal(await page.locator('.segment').count(), 0);
  await page.evaluate(() => {
    for (let i=106;i<=256;i++) h.ws.emit({type:'source_final',item_id:`id-${i}`,text:`text ${i}`});
  });
  assert.equal(await page.locator('#stop').isEnabled(), true);
  await emit({type:'source_partial',item_id:'id-257',text:'overflow'});
  assert.match(await page.locator('#status').innerText(), /256/);
  assert.equal(await page.locator('#stop').isDisabled(), true);
  await streamReady();
  await page.evaluate(() => { h.ws.bufferedAmount = 256 * 1024; }); await feed(.1,0);
  assert.equal((await sent()).filter(x => x.binary).length, 0);
  assert.match(await page.locator('#status').innerText(), /缓冲已满/);
  assert.equal(await page.evaluate(() => h.tracks.every(t => t.stopped) && h.contexts.every(c => c.closed)), true);
  await streamReady();
  await page.evaluate(() => h.chunk({microphone:new Float32Array(48001),sampleRate:48000}));
  assert.match(await page.locator('#status').innerText(), /超过 1 秒/);
  await streamReady();
  await emit({type:'error',code:'stream_not_configured',message:'separate ASR access required'});
  assert.match(await page.locator('#status').innerText(), /stream_not_configured/);
  assert.equal(await page.locator('#stop').isDisabled(), true);
  assert.equal(await page.evaluate(() => new URL(h.ws.url).pathname), '/ws/interpret-stream');
  checks.push('streaming: 100-row history/Clear tombstones, 256-item session bound, overflow stops instead of dropping PCM, one-second local bound, fatal configuration error without fallback');
  await streamReady();
  await emit({type:'source_partial',item_id:'handoff',text:'I might go.',stash:'tomorrow'});
  assert.equal(await page.getByRole('button',{name:'带这句去解释 / 练习'}).isDisabled(),true);
  await emit({type:'source_final',item_id:'handoff',text:'I might go tomorrow.'});
  assert.equal(await page.getByRole('button',{name:'带这句去解释 / 练习'}).isEnabled(),true);
  await emit({type:'translation',item_id:'handoff',text:'我可能明天去。'});
  await emit({type:'source_final',item_id:'neighbor',text:'The weather might improve.'});
  await emit({type:'translation',item_id:'neighbor',text:'天气可能好转。'});
  await emit({type:'source_partial',item_id:'unstable-neighbor',text:'Do not import me.',stash:'pending'});
  page.once('dialog',dialog=>dialog.accept());
  await page.getByRole('button',{name:'带这句去解释 / 练习'}).first().click();
  await page.waitForTimeout(100);
  const selected = await page.evaluate(()=>JSON.parse(sessionStorage.getItem('neurosama:selected-source')));
  assert.equal(selected.text,'I might go tomorrow.');
  assert.match(selected.label,/识别原文/);
  assert.ok(selected.timestamp>0);
  assert.equal(selected.context.length,1);
  assert.equal(selected.context[0].text,'The weather might improve.');
  assert.equal(selected.context[0].translation,'天气可能好转。');
  assert.ok(!JSON.stringify(selected).includes('Do not import me'));
  checks.push('selected original plus bounded same-session confirmed neighbors handoff; unstable partials excluded');
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({ok:true,checks},null,2));
} finally { await browser.close(); }
