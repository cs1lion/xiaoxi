// Real homepage integration with offline HTTP, AudioContext, avatar and WebSocket.
// No application server, models, real audio, microphone or personal data is used.
import { chromium } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [], requests = [], checks = [];
page.setDefaultTimeout(8000);
page.on('pageerror', error => errors.push(error.message));
const editorStub = `<!doctype html><meta charset="utf-8"><button id="play">听这条观点</button><script>
  window.events=[];window.point={request_id:'click-1',insight_id:'point-1',session_id:'editor-1',draft_revision:3};
  addEventListener('message',e=>{if(e.origin===location.origin&&e.source===parent&&e.data?.type==='aqi-editor-insight-playback')events.push(e.data);});
  document.querySelector('#play').onclick=()=>parent.postMessage({type:'aqi-editor-insight-play',...point},location.origin);
  window.cancelPoint=revision=>parent.postMessage({type:'aqi-editor-insight-cancel',session_id:'editor-1',draft_revision:revision},location.origin);
  </script>`;
await page.route('http://insight-voice.test/**', async route => {
  const url = new URL(route.request().url());
  if (url.pathname === '/static/avatar/index.js') return route.fulfill({ contentType: 'text/javascript', body: `
    const noop=()=>{};
    export const avatar=new Proxy({emotion:{set:noop},rendererKind:'svg',
      onMessage:m=>window.h.avatarEvents.push(m),stopSpeaking:()=>window.h.stopped++,
      speak:(text)=>window.h.spoken.push(text)}, {get:(o,k)=>o[k]||noop});
    export const boot=async()=>{};` });
  if (url.pathname === '/static/notes.html') return route.fulfill({ contentType: 'text/html', body: editorStub });
  if (/^\/api\/editor-insights\/[^/]+\/speak$/.test(url.pathname)) {
    return new Promise(resolve => requests.push({ route, request: route.request(), resolve }));
  }
  if (url.pathname.startsWith('/api/')) {
    const data = url.pathname === '/api/persona' ? {name:'测试',tagline:'离线测试',voices:['base'],default_voice:'base'} :
      url.pathname === '/api/activity' ? {selection:{},note:null,thread:null} : {count:0,items:[]};
    return route.fulfill({ json: data });
  }
  const relative = url.pathname === '/' ? 'web/index.html' : 'web/' + url.pathname.replace(/^\/static\//, '');
  const absolute = path.resolve(root, relative);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try {
    const body = await readFile(absolute);
    return route.fulfill({ body, contentType: absolute.endsWith('.js') ? 'text/javascript' :
      absolute.endsWith('.css') ? 'text/css' : 'text/html' });
  } catch { return route.fulfill({ status:404, body:'not found' }); }
});
await page.addInitScript(() => {
  const h = window.h = { sent:[],sources:[],spoken:[],avatarEvents:[],stopped:0,contexts:0,
    holdDecode:false,decodeWait:[],permissionWait:null,holdPermission:false,tracks:[] };
  class WS {
    static OPEN=1;
    constructor(){this.readyState=0;h.ws=this;setTimeout(()=>{this.readyState=1;this.onopen?.({});this.emit({type:'ready',session_id:'test'});},0);}
    send(data){h.sent.push(typeof data==='string'?JSON.parse(data):{binary:true});}
    emit(message){this.onmessage?.({data:JSON.stringify(message)});}
  }
  window.WebSocket=WS;
  const node=()=>({connect(){return this;},disconnect(){},gain:{value:1}});
  class AC {
    constructor(){h.contexts++;this.sampleRate=48000;this.state='running';this.destination=node();}
    get currentTime(){return performance.now()/1000;}
    resume(){return Promise.resolve();}close(){return Promise.resolve();}
    createGain(){return node();}
    createAnalyser(){return {...node(),fftSize:512,getFloatTimeDomainData:a=>a.fill(0)};}
    createMediaStreamSource(){return node();}
    createScriptProcessor(){return {...node(),onaudioprocess:null};}
    createBufferSource(){const s={...node(),onended:null,started:false,stopped:false,
      start(){this.started=true;h.sources.push(this);},stop(){this.stopped=true;this.onended?.();}};return s;}
    decodeAudioData(){return h.holdDecode?new Promise(resolve=>h.decodeWait.push(resolve)):Promise.resolve({duration:2});}
  }
  window.AudioContext=AC;
  const makeStream=()=>{const t={stopped:false,stop(){this.stopped=true;},getSettings(){return{};},addEventListener(){},removeEventListener(){}};
    h.tracks.push(t);return{getTracks:()=>[t],getAudioTracks:()=>[t]};};
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:()=>h.holdPermission?
    new Promise(resolve=>h.permissionWait=()=>resolve(makeStream())):Promise.resolve(makeStream())}});
});
let editor;
const result = (id, rev = 3) => ({ id, draft_revision:rev, text:'这里的结论缺少一个具体例子。', audio:'dGVzdA==', format:'wav' });
const waitRequest = count => page.waitForFunction(() => true).then(async () => {
  const deadline = Date.now()+8000;
  while(requests.length<count&&Date.now()<deadline) await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(requests.length,count);
});
async function release(index, id = `point-${index+1}`) {
  const request = requests[index];
  try { await request.route.fulfill({ json: result(id) }); } catch (_) { /* aborted HTTP */ }
  request.resolve();
}
const emit = data => page.evaluate(message=>h.ws.emit(message),data);
const setPoint = async (n, rev = 3) => editor.evaluate(([n,rev])=>{
  point={request_id:`click-${n}`,insight_id:`point-${n}`,session_id:'editor-1',draft_revision:rev};
},[n,rev]);
const clickPoint = () => editor.locator('#play').click();
const waitState = (request, state) => editor.waitForFunction(([request,state])=>events.some(e=>e.request_id===request&&e.state===state),[request,state]);
try {
  await page.goto('http://insight-voice.test/');
  await page.waitForFunction(()=>h.ws.readyState===1);
  await page.locator('iframe[title="个人笔记编辑器"]').waitFor({state:'attached'});
  await page.evaluate(()=>document.querySelector('#work-notes').hidden=false);
  editor = page.frames().find(frame=>frame.url().includes('/static/notes.html'));
  await editor.locator('#play').waitFor();

  // Start a normal server reply, then replace it with the cached point.
  await page.locator('#textInput').fill('普通对话'); await page.locator('#sendBtn').click();
  const normalId = await page.evaluate(()=>h.sent.findLast(m=>m.type==='text').turn_id);
  await emit({type:'audio',turn_id:normalId,data:'dGVzdA==',text:'原来在播的回复',emotion:'base'});
  await page.waitForFunction(()=>h.sources.length===1);
  const messagesBefore = await page.evaluate(()=>h.sent.length);
  const chatBefore = await page.locator('#chat .msg').count();
  await clickPoint(); await waitRequest(1); await waitState('click-1','preparing');
  assert.deepEqual(requests[0].request.postDataJSON(),{session_id:'editor-1',draft_revision:3});
  assert.equal(await page.evaluate(()=>h.sources[0].stopped),true);
  assert.deepEqual(await page.evaluate(start=>h.sent.slice(start),messagesBefore),[{type:'barge_in',turn_id:normalId}]);
  await emit({type:'turn_started',turn_id:'proactive-unrelated'});
  await emit({type:'thinking',turn_id:'proactive-unrelated'});
  await emit({type:'audio',turn_id:'proactive-unrelated',data:'dGVzdA==',text:'迟到回复'});
  await emit({type:'error',message:'不应该覆盖观点的迟到错误'});
  await release(0); await waitState('click-1','playing');
  assert.equal(await page.evaluate(()=>h.sources.length),2);
  assert.equal(await page.locator('#subtitle').textContent(),result('point-1').text);
  assert.equal(await page.locator('#chat .msg').count(),chatBefore);
  assert.equal(await page.evaluate(()=>h.spoken.at(-1)),result('point-1').text);
  await page.evaluate(()=>h.sources.at(-1).onended()); await waitState('click-1','done');
  checks.push('cached point uses shared playback/avatar and interrupts prior reply without submitting text or altering chat');
  assert.equal(await page.evaluate(()=>h.sent.some(m=>m.turn_id==='proactive-unrelated')),false);
  checks.push('unrelated late server callbacks cannot take over the local voice turn');

  // An iframe with the same origin but a different contentWindow has no authority.
  await page.evaluate(()=>{const f=document.createElement('iframe');f.title='other';f.src='/static/notes.html';document.body.append(f);});
  const sibling = page.frameLocator('iframe[title="other"]');
  await sibling.locator('#play').click();
  await page.waitForTimeout(50); assert.equal(requests.length,1);
  checks.push('same-origin sibling iframe cannot request audio');

  // Pending synthesis cancelled by a changed editor revision remains silent.
  await setPoint(2); await clickPoint(); await waitRequest(2);
  await editor.evaluate(()=>cancelPoint(4)); await waitState('click-2','stopped');
  await release(1); await page.waitForTimeout(50);
  assert.equal(await page.evaluate(()=>h.sources.length),2);
  assert.equal(await page.evaluate(()=>h.sent.filter(m=>m.type==='barge_in').length),1);
  checks.push('draft change aborts pending speech and local cancellation sends no server barge-in');

  // A normal submission wins over even a response that reaches native decoding.
  await setPoint(3); await page.evaluate(()=>h.holdDecode=true); await clickPoint(); await waitRequest(3);
  await release(2); await page.waitForFunction(()=>h.decodeWait.length===1);
  await page.locator('#textInput').fill('新的普通消息'); await page.locator('#sendBtn').click();
  await waitState('click-3','stopped');
  await page.evaluate(()=>{h.holdDecode=false;h.decodeWait.shift()({duration:2});});
  await page.waitForTimeout(50); assert.equal(await page.evaluate(()=>h.sources.length),2);
  assert.equal(await page.evaluate(()=>h.sent.filter(m=>m.type==='text').length),2);
  assert.equal(await page.evaluate(()=>h.sent.filter(m=>m.type==='barge_in').length),1);
  checks.push('normal text submission cancels late native decode without cancelling an unrelated server turn');

  // Stop cancels actual insight playback through the same queue and avatar.
  await setPoint(4); await clickPoint(); await waitRequest(4); await release(3); await waitState('click-4','playing');
  const bargeBeforeStop = await page.evaluate(()=>h.sent.filter(m=>m.type==='barge_in').length);
  await page.locator('#stopBtn').click(); await waitState('click-4','stopped');
  assert.equal(await page.evaluate(()=>h.sources.at(-1).stopped),true);
  assert.equal(await page.evaluate(()=>h.sent.filter(m=>m.type==='barge_in').length),bargeBeforeStop);
  checks.push('shared stop halts point speech and returns the avatar to idle');

  // Pressing the mic interrupts immediately, while permission is still pending.
  await setPoint(5); await clickPoint(); await waitRequest(5); await release(4); await waitState('click-5','playing');
  await page.evaluate(()=>h.holdPermission=true);
  await page.locator('#micBtn').dispatchEvent('pointerdown'); await waitState('click-5','stopped');
  assert.equal(await page.evaluate(()=>h.sources.at(-1).stopped),true);
  await page.locator('#stopBtn').click();
  await page.waitForFunction(()=>!!h.permissionWait); await page.evaluate(()=>h.permissionWait());
  await page.waitForFunction(()=>h.tracks.every(t=>t.stopped));
  checks.push('mic gesture interrupts before asynchronous permission; cancelled permission cannot start recording');

  await setPoint(6); await clickPoint(); await waitRequest(6);
  await page.evaluate(()=>window.dispatchEvent(new Event('pagehide'))); await waitState('click-6','stopped');
  await release(5); await page.waitForTimeout(50);
  assert.equal(await page.evaluate(()=>h.sources.length),4);
  checks.push('page close aborts pending speech and ignores its response');
  assert.deepEqual(errors,[]);
  console.log(`PASS ${checks.length} editor insight voice browser checks`);
  for (const check of checks) console.log(`  ${check}`);
} finally {
  for(const request of requests) request.resolve();
  await browser.close();
}
