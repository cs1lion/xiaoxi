// Offline integration: real app DOM/modules, mocked devices/WebSocket/avatar.
// No application server, credentials, user memories, or real microphone needed.
import { chromium } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';

const root = path.resolve(import.meta.dirname, '..');
const browser = await chromium.launch({ headless: true });
const errors = [];
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
let releasePersona;
const personaGate = new Promise(resolve => { releasePersona = resolve; });
let markPersonaRequested;
const personaRequested = new Promise(resolve => { markPersonaRequested = resolve; });
let memoryRecords = [];
const memoryRequests = [];
page.setDefaultTimeout(8000);
page.on('pageerror', error => errors.push(error.message));
await page.route('http://voice.test/**', async route => {
  const url = new URL(route.request().url());
  if (url.pathname === '/static/avatar/index.js') {
    return route.fulfill({ contentType: 'text/javascript', body: `
      const noop = () => {};
      export const avatar = new Proxy({ emotion: {set: noop}, rendererKind:'svg',
        onMessage: m => window.voiceHarness.avatarEvents.push(m),
        stopSpeaking: () => window.voiceHarness.stopped++,
      }, { get: (o,k) => o[k] || noop });
      export const boot = async () => {};
    ` });
  }
  if (url.pathname.startsWith('/api/')) {
    if (url.pathname === '/api/memories' || url.pathname.startsWith('/api/memories/')) {
      const request = route.request();
      const method = request.method();
      memoryRequests.push({ method, path: url.pathname, query: url.search, body: request.postDataJSON() });
      if (url.pathname === '/api/memories' && method === 'GET') {
        const items = memoryRecords.filter(m => url.searchParams.get('include_history') === 'true' || m.status === 'active');
        return route.fulfill({ json: { count: memoryRecords.filter(m => m.status === 'active').length, items } });
      }
      const record = memoryRecords.find(m => String(m.id) === url.pathname.split('/').at(-1));
      if (!record) return route.fulfill({ status: 404, json: { error: 'memory not found' } });
      if (method === 'PATCH') {
        Object.assign(record, request.postDataJSON(), { evidence_state: 'manual', evidence_message_id: null,
          evidence_quote: '', preference_target: '', preference_level: '', preference_scope: '', preference_as_of: null });
        return route.fulfill({ json: record });
      }
      if (method === 'DELETE') {
        memoryRecords = memoryRecords.filter(m => m.id !== record.id);
        return route.fulfill({ json: { ok: true } });
      }
    }
    if (url.pathname === '/api/persona') {
      markPersonaRequested();
      await personaGate;
    }
    const data = url.pathname === '/api/persona' ? {name:'测试',tagline:'离线测试',voices:['base','认真'],default_voice:'base'} : {count:0,items:[]};
    return route.fulfill({json:data});
  }
  const relative = url.pathname === '/' ? 'web/index.html' : 'web/' + url.pathname.replace(/^\/static\//, '');
  const absolute = path.resolve(root, relative);
  if (!absolute.startsWith(root + path.sep)) return route.abort();
  try {
    const body = await readFile(absolute);
    const contentType = absolute.endsWith('.js') || absolute.endsWith('.mjs') ? 'text/javascript' : absolute.endsWith('.css') ? 'text/css' : 'text/html';
    return route.fulfill({body,contentType});
  } catch { return route.fulfill({status:404,body:'not found'}); }
});
await page.addInitScript(() => {
  const h = window.voiceHarness = {sent:[],avatarEvents:[],processors:[],sources:[],gains:[],tracks:[],stopped:0,permission:'allow',pendingPermission:null,decodeWait:[],holdDecode:false};
  class WS {
    static OPEN = 1; static CLOSED = 3; static CONNECTING = 0;
    constructor() { this.readyState=0; h.ws=this; setTimeout(() => {this.readyState=1;this.onopen?.({});this.emit({type:'ready',sample_rate:16000,session_id:'test',persona:'测试'});},0); }
    send(data) { if(this.readyState !== 1) throw Error('socket closed'); h.sent.push(typeof data==='string'?JSON.parse(data):{binary:true,bytes:data.byteLength}); }
    emit(data) { this.onmessage?.({data:JSON.stringify(data)}); }
    close() { this.readyState=3;this.onclose?.({}); }
  }
  window.WebSocket=WS;
  const node = () => ({connect(){return this;},disconnect(){},gain:{value:1}});
  class AC {
    constructor() {this.sampleRate=48000;this.clockOrigin=performance.now();this.state='running';this.destination=node();}
    get currentTime(){return (performance.now()-this.clockOrigin)/1000;}
    resume(){this.state='running';return Promise.resolve();}
    close(){this.state='closed';return Promise.resolve();}
    createGain(){const g=node();h.gains.push(g);return g;}
    createAnalyser(){return {...node(),fftSize:1024,getFloatTimeDomainData(a){a.fill(0);}};}
    createMediaStreamSource(){return node();}
    createScriptProcessor(size){const p={...node(),size,context:this,onaudioprocess:null};h.processors.push(p);return p;}
    createBuffer(channels,length,rate){const a=new Float32Array(length);return {length,duration:length/rate,getChannelData(){return a;}};}
    createBufferSource(){const s={...node(),onended:null,start(){h.sources.push(this);},stop(){this.onended?.();}};return s;}
    decodeAudioData(){if(h.holdDecode)return new Promise(resolve=>h.decodeWait.push(resolve));return Promise.resolve({duration:2});}
  }
  window.AudioContext=AC;
  const makeStream=()=>{const t={stopped:false,stop(){this.stopped=true;},getSettings(){return {echoCancellation:true,noiseSuppression:false};},addEventListener(){},removeEventListener(){}};h.tracks.push(t);return {getTracks:()=>[t],getAudioTracks:()=>[t]};};
  Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{getUserMedia:async()=>{
    if(h.permission==='deny')throw new DOMException('denied','NotAllowedError');
    if(h.permission==='wait')return new Promise(resolve=>h.pendingPermission=()=>resolve(makeStream()));
    return makeStream();
  }}});
  h.feed=(seconds,amplitude=0)=>{
    const p=[...h.processors].reverse().find(p=>p.onaudioprocess);
    if(!p)throw Error('no microphone processor');
    const count=Math.ceil(seconds*48000/p.size);
    for(let i=0;i<count;i++){
      const a=new Float32Array(p.size);
      for(let j=0;j<a.length;j++)a[j]=amplitude*Math.sin(j*.17);
      p.onaudioprocess({playbackTime:p.context.currentTime+1,inputBuffer:{length:p.size,getChannelData:()=>a},outputBuffer:{getChannelData:()=>new Float32Array(p.size)}});
    }
  };
  h.finishPlayback=()=>{for(const s of h.sources.splice(0))s.onended?.();};
});
const sent = () => page.evaluate(() => voiceHarness.sent.filter(x=>!x.binary));
const feed = (seconds, amplitude) => page.evaluate(([s,a])=>voiceHarness.feed(s,a),[seconds,amplitude]);
const emit = message => page.evaluate(m=>voiceHarness.ws.emit(m),message);
const ackExperience = message => page.evaluate(m => voiceHarness.ws.emit({ ...m,
  experience_request_id: voiceHarness.sent.findLast(e => e.type === 'config' && e.experience_request_id)?.experience_request_id,
}), message);
const checks=[];
async function exportedBaseline() {
  const pending = page.waitForEvent('download');
  await page.locator('[data-baseline="export"]').click();
  return JSON.parse(await readFile(await (await pending).path(), 'utf8'));
}
try {
  await page.goto('http://voice.test/');
  await page.waitForFunction(()=>window.voiceHarness?.ws?.readyState===1);
  await personaRequested;
  await page.locator('#presenceCapsule').click();
  assert.equal(Object.hasOwn((await sent()).filter(e=>e.type==='config').at(-1), 'emotion'), false,
    'startup controls must not send an unconfirmed speech default');
  await emit({type:'config_ack',emotion:'认真'});
  releasePersona();
  await page.locator('#emotionSelect option[value="base"]').waitFor({state:'attached'});
  assert.equal(await page.locator('#emotionSelect').inputValue(), '认真',
    'late persona defaults cannot overwrite an acknowledged speech preference');
  await page.locator('#presenceCapsule').click();
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, '认真',
    'late persona defaults cannot silently change subsequent configs');
  checks.push('slow persona response preserves acknowledged speech and startup configs omit unknown preference');
  await page.reload();
  await page.waitForFunction(()=>window.voiceHarness?.ws?.readyState===1);
  await page.locator('#emotionSelect option[value="base"]').waitFor({state:'attached'});
  assert.equal(await page.locator('#emotionSelect').inputValue(), 'base', 'daily speech starts with natural');
  assert.equal(await page.locator('#emotionSelect option[value="base"]').textContent(), '自然');
  await page.locator('details.preferences > summary').click();
  await page.locator('#emotionSelect').selectOption('认真');
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, '认真');
  await emit({type:'config_ack',emotion:'认真'});
  await page.locator('#presenceCapsule').click();
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, '认真', 'other controls retain selected speech');
  await page.locator('#presenceCapsule').click();
  await page.locator('#emotionSelect').selectOption('');
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, '', 'automatic speech remains explicit');
  await emit({type:'config_ack',emotion:'认真'});
  assert.equal(await page.locator('#emotionSelect').inputValue(), '', 'older success ack cannot overwrite newer selection');
  await page.locator('#presenceCapsule').click();
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, '',
    'other controls must retain the newer automatic selection after an older ack');
  await page.locator('#presenceCapsule').click();
  checks.push('older successful speech acknowledgement cannot revert a newer local selection');
  await emit({type:'config_ack',emotion:'base',emotion_error:'无效的语气设置，未应用。'});
  assert.equal(await page.locator('#emotionSelect').inputValue(), 'base', 'ack restores actual speech preference');
  await page.locator('#presenceCapsule').click();
  assert.equal((await sent()).filter(e=>e.type==='config').at(-1).emotion, 'base', 'ack synchronizes subsequent configs');
  await page.locator('#presenceCapsule').click();
  await page.locator('details.preferences > summary').click();
  checks.push('natural default, speech choices, retained preference and server acknowledgement');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(()=>voiceHarness.processors.some(p=>p.onaudioprocess));
  await feed(2,0);
  assert.equal((await sent()).filter(e=>e.type==='start').length,0,'silence must stay local');
  checks.push('silent monitoring sends no audio turn');
  await feed(.7,.12);
  await feed(1.3,0);
  const first=(await sent()).find(e=>e.type==='start');
  assert.ok(first?.turn_id,'speech starts a tagged turn');
  assert.ok((await sent()).some(e=>e.type==='audio_end'&&e.turn_id===first.turn_id),'pause automatically submits');
  checks.push('speech onset + pre-roll + automatic endpoint');
  await emit({type:'thinking',turn_id:first.turn_id});
  await emit({type:'text_delta',turn_id:first.turn_id,delta:'第一轮'});
  await page.evaluate(()=>{voiceHarness.holdDecode=true;});
  await emit({type:'audio',turn_id:first.turn_id,data:'AA==',text:'第一轮',emotion:'base'});
  await page.waitForFunction(()=>voiceHarness.decodeWait.length===1);
  assert.notEqual(await page.locator('#avatarStage').getAttribute('data-interaction'),'speaking','pending decode cannot mark avatar speaking');
  assert.equal(await page.locator('#interactionState').textContent(),await page.locator('#status').textContent(),'stage status mirrors topbar during decode');
  await page.locator('#textInput').fill('换个问题');
  await page.locator('#sendBtn').click();
  const second=(await sent()).filter(e=>e.type==='text').at(-1);
  assert.notEqual(second.turn_id,first.turn_id);
  const before=await page.evaluate(()=>voiceHarness.avatarEvents.length);
  await emit({type:'text_delta',turn_id:first.turn_id,delta:'过期内容'});
  await emit({type:'done',turn_id:first.turn_id,text:'过期内容'});
  assert.equal(await page.evaluate(()=>voiceHarness.avatarEvents.length),before,'stale events never reach avatar');
  await page.evaluate(()=>{voiceHarness.holdDecode=false;voiceHarness.decodeWait.shift()({duration:.1});});
  await page.evaluate(()=>Promise.resolve());
  assert.equal(await page.evaluate(()=>voiceHarness.sources.length),0,'late decode never starts old audio');
  checks.push('new turn rejects late websocket events and pending decode');
  await emit({type:'thinking',turn_id:second.turn_id});
  await emit({type:'audio',turn_id:second.turn_id,data:'AA==',text:'新回复',emotion:'base'});
  await page.waitForFunction(()=>voiceHarness.sources.length===1);
  assert.equal(await page.locator('#subtitle').textContent(),'新回复','subtitle follows audible sentence');
  await emit({type:'text_delta',turn_id:second.turn_id,delta:'尚未播出的后文'});
  assert.equal(await page.locator('#subtitle').textContent(),'新回复','later streaming text cannot replace playing subtitle');
  assert.equal(await page.locator('#avatarStage').getAttribute('data-interaction'),'speaking','actual source playback marks speaking');
  assert.equal(await page.locator('#interactionState').textContent(),await page.locator('#status').textContent(),'playing status is mirrored');
  await emit({type:'done',turn_id:second.turn_id,text:'新回复'});
  await page.evaluate(()=>voiceHarness.finishPlayback());
  await feed(.7,.12);
  await feed(1.3,0);
  const starts=(await sent()).filter(e=>e.type==='start');
  assert.equal(starts.length,2,'listening continues after actual playback');
  checks.push('real playback completion permits next hands-free utterance');
  const third = starts.at(-1);
  await emit({type:'thinking',turn_id:third.turn_id});
  await emit({type:'audio',turn_id:third.turn_id,data:'AA==',text:'请打断我',emotion:'base'});
  await page.waitForFunction(()=>voiceHarness.sources.length===1);
  const beforeShort=(await sent()).filter(e=>e.type==='start').length;
  await feed(.16,.15);
  assert.ok(await page.evaluate(()=>voiceHarness.gains.some(g=>g.gain.value===.22)),'candidate ducks without cancelling');
  await feed(.22,0);
  assert.equal((await sent()).filter(e=>e.type==='start').length,beforeShort,'short response does not steal turn');
  assert.ok(await page.evaluate(()=>voiceHarness.gains.every(g=>g.gain.value!==.22)),'short response restores volume');
  checks.push('short vocal response ducks then restores without cancelling');
  await feed(.8,.15);
  const fourth = (await sent()).filter(e=>e.type==='start').at(-1);
  await page.locator('details.preferences > summary').click();
  await page.selectOption('#duplexMode','speaker');
  assert.notEqual(fourth.turn_id,third.turn_id,'speech during playback starts a new turn');
  assert.ok((await sent()).some(e=>e.type==='barge_in'&&e.turn_id===third.turn_id));
  const eventsBefore=await page.evaluate(()=>voiceHarness.avatarEvents.length);
  await emit({type:'audio',turn_id:third.turn_id,data:'AA==',text:'旧话不能复活'});
  assert.equal(await page.evaluate(()=>voiceHarness.avatarEvents.length),eventsBefore);
  await feed(1.3,0);
  assert.ok((await sent()).some(e=>e.type==='audio_end'&&e.turn_id===fourth.turn_id),'mode change must not reset active utterance endpoint');
  await emit({type:'done',turn_id:fourth.turn_id,text:''});
  assert.match(await page.locator('#status').textContent(),/本地监听/,'silent TTS returns to local listening');
  assert.equal(await page.locator('#avatarStage').getAttribute('data-interaction'),'monitoring','stopped turn returns avatar status to local monitoring');
  checks.push('spoken barge-in stops old turn and silent result returns to listening');
  checks.push('avatar and visible state follow decode/playback/interruption facts');
  const messagesBeforeResume=(await sent()).length;
  await page.locator('#resumeSpeechBtn').click();
  assert.equal((await sent()).length,messagesBeforeResume,'resume is audio only, no tool or generation replay');
  await page.selectOption('#duplexMode','manual');
  const beforeManualOnly=(await sent()).filter(e=>e.type==='start').length;
  await feed(1.2,.15);
  assert.equal((await sent()).filter(e=>e.type==='start').length,beforeManualOnly,'manual-only ignores acoustic barge-in');
  await page.locator('#micBtn').focus(); await page.keyboard.down('Space');
  await page.waitForFunction(()=>document.querySelector('#micBtn').classList.contains('recording'));
  const explicit=(await sent()).filter(e=>e.type==='start').at(-1);
  assert.equal((await sent()).filter(e=>e.type==='start').length,beforeManualOnly+1,'explicit key still interrupts');
  await feed(.3,.1); await page.keyboard.up('Space');
  await emit({type:'done',turn_id:explicit.turn_id,text:''});
  await page.evaluate(()=>voiceHarness.finishPlayback());
  await page.selectOption('#duplexMode','headset');
  checks.push('manual-only fallback ignores noise but permits explicit keyboard interruption');
  checks.push('interrupted sentence continuation never sends a new task');
  await page.locator('#stopBtn').click();
  await page.locator('#bargeToggle').click();
  assert.ok(await page.evaluate(()=>voiceHarness.tracks.every(t=>t.stopped)),'disabling releases microphone');
  checks.push('stop control and disable release');
  // Permission requested while holding, then pointer released before grant.
  await page.evaluate(()=>{voiceHarness.permission='wait';});
  await page.locator('#micBtn').dispatchEvent('pointerdown',{pointerId:7,button:0});
  await page.waitForFunction(()=>!!voiceHarness.pendingPermission);
  await page.locator('#micBtn').dispatchEvent('pointerup',{pointerId:7,button:0,bubbles:true});
  const countBefore=(await sent()).filter(e=>e.type==='start').length;
  await page.evaluate(()=>voiceHarness.pendingPermission());
  await page.waitForFunction(()=>voiceHarness.tracks.every(t=>t.stopped));
  assert.equal((await sent()).filter(e=>e.type==='start').length,countBefore,'released permission request cannot begin recording');
  checks.push('permission popup pointer-release race');
  await page.evaluate(()=>{voiceHarness.permission='deny';});
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(()=>document.querySelector('#bargeToggle').textContent.includes('关'));
  checks.push('permission rejection leaves continuous mode off');
  await page.evaluate(()=>{voiceHarness.permission='allow';});
  await page.locator('#micBtn').focus();
  await page.keyboard.down('Space');
  await page.waitForFunction(()=>document.querySelector('#micBtn').classList.contains('recording'));
  const manual=(await sent()).filter(e=>e.type==='start').at(-1);
  await feed(.4,.1);
  await page.keyboard.up('Space');
  assert.ok((await sent()).some(e=>e.type==='audio_end'&&e.turn_id===manual.turn_id));
  assert.ok(await page.evaluate(()=>voiceHarness.tracks.every(t=>t.stopped)));
  await emit({type:'done',turn_id:manual.turn_id,text:''});
  checks.push('keyboard hold-to-talk submits and releases tracks');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(()=>voiceHarness.tracks.some(t=>!t.stopped));
  await feed(.6,.12);
  const cancelled=(await sent()).filter(e=>e.type==='start').at(-1);
  await page.locator('#bargeToggle').click();
  assert.ok(!(await sent()).some(e=>e.type==='audio_end'&&e.turn_id===cancelled.turn_id),'disable must not submit unfinished speech');
  assert.ok(await page.evaluate(()=>voiceHarness.tracks.every(t=>t.stopped)));
  checks.push('disable cancels unfinished speech without submitting');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(()=>voiceHarness.tracks.some(t=>!t.stopped));
  await feed(31,.12);
  const capped=(await sent()).filter(e=>e.type==='start').at(-1);
  assert.ok((await sent()).some(e=>e.type==='audio_end'&&e.turn_id===capped.turn_id),'long speech is capped');
  const cappedStarts=(await sent()).filter(e=>e.type==='start').length;
  await feed(1,.12);
  assert.equal((await sent()).filter(e=>e.type==='start').length,cappedStarts,'cap waits for quiet, not infinite new turns');
  await feed(1.3,0);
  checks.push('continuous utterance cap waits for quiet before rearming');
  let promptReply;
  page.on('dialog', dialog=>dialog.type() === 'prompt' ? dialog.accept(promptReply) : dialog.accept());
  await page.selectOption('#practiceMode','free');
  await page.locator('#practiceApply').click();
  assert.ok((await sent()).some(e=>e.type==='config'&&e.practice?.mode==='free'),'practice config explicit');
  await feed(.5,.12);
  const practiceTurn=(await sent()).filter(e=>e.type==='start').at(-1);
  await feed(1.3,0);
  assert.ok(!(await sent()).some(e=>e.type==='audio_end'&&e.turn_id===practiceTurn.turn_id),'practice tolerates thinking pause');
  await feed(1.2,0);
  assert.ok((await sent()).some(e=>e.type==='audio_end'&&e.turn_id===practiceTurn.turn_id),'practice eventually endpoints');
  await page.locator('#practiceReview').click();
  assert.ok((await sent()).some(e=>e.type==='practice_review'&&e.turn_id),'review uses cancellable tagged turn');
  await page.selectOption('#practiceMode','off'); await page.locator('#practiceApply').click();
  assert.ok(await page.locator('#practiceReview').isDisabled());
  checks.push('English practice settings, longer pause, tagged review and exit');
  await page.locator('#experienceMount summary').first().click();
  await page.selectOption('#experienceMode','theatre');
  await page.locator('#experienceApply').click();
  assert.ok((await sent()).some(e=>e.type==='config'&&e.experience?.mode==='theatre'),'experience real app config');
  await ackExperience({type:'config_ack',experience:{mode:'theatre',scene:'rain-radio',source:{text:''}},practice:{mode:'off',level:'B1',correction:'after'}});
  await page.locator('#experienceKickoff').click();
  assert.ok((await sent()).some(e=>e.type==='text'&&e.turn_id&&e.text.includes('虚构小剧场')),'experience kickoff reuses cancellable real voice turn');
  await page.selectOption('#experienceMode','off');
  await page.locator('#experienceApply').click();
  await ackExperience({type:'config_ack',experience:{mode:'off',scene:'',source:{text:''}}});
  assert.ok(await page.locator('#experienceKickoff').isDisabled());
  checks.push('experience config acknowledgements, fictional kickoff and exit reuse real voice controls');
  const appliedSource = { text: 'However, it was still useful.', translation: '不过，它仍然有用。',
    label: '选中字幕', source_id: 'subtitle:2', context: [
      { text: 'The old map was inaccurate.', translation: '旧地图不准确。', label: '前一条字幕', source_id: 'subtitle:1' },
    ] };
  await page.evaluate(value => sessionStorage.setItem('neurosama:selected-source', JSON.stringify({ ...value, timestamp: Date.now() })), appliedSource);
  await page.locator('#experienceImport').click();
  await page.selectOption('#experienceMode', 'listen');
  await page.locator('#experienceApply').click();
  assert.ok(await page.locator('#experienceRelations').isDisabled(), 'relationship question waits for ack');
  await ackExperience({ type: 'config_ack', experience: { mode: 'listen', scene: '', source: appliedSource } });
  assert.match(await page.locator('#experienceActiveSource').innerText(), /However, it was still useful/);
  await page.locator('#experienceRelations').click();
  assert.ok((await sent()).some(e => e.type === 'text' && e.turn_id && e.text.includes('解释选中原句与前后内容的关系')), 'relationship question uses the real tagged text turn');
  const sourceTexts = (await sent()).filter(e => e.type === 'text').length;
  await page.locator('#experienceSource').fill('A revised source.');
  assert.ok(await page.locator('#experienceExplain').isDisabled());
  assert.ok(await page.locator('#experienceRelations').isDisabled());
  assert.match(await page.locator('#experienceActiveSource').innerText(), /However, it was still useful/);
  assert.equal((await sent()).filter(e => e.type === 'text').length, sourceTexts, 'editing sends nothing');
  await page.locator('#experienceApply').click();
  const revisedSource = (await sent()).filter(e => e.experience_source).at(-1).experience_source;
  await emit({ type: 'config_ack', experience: { mode: 'listen', scene: '', source: appliedSource } });
  assert.ok(await page.locator('#experienceApply').isDisabled(), 'ordinary settings ack must not complete source application');
  await ackExperience({ type: 'config_ack', experience: { mode: 'listen', scene: '', source: revisedSource } });
  assert.match(await page.locator('#experienceActiveSource').innerText(), /A revised source/);
  assert.ok(await page.locator('#experienceExplain').isEnabled());
  assert.ok(await page.locator('#experienceRelations').isDisabled(), 'manual edit removed associated neighbors');
  checks.push('applied source preview, draft gating and explicit relationship question reuse the real voice turn');
  await page.evaluate(()=>{
    Object.defineProperty(document,'hidden',{configurable:true,value:true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  assert.ok(await page.evaluate(()=>voiceHarness.tracks.every(t=>t.stopped)));
  assert.equal(await page.locator('#bargeToggle').getAttribute('aria-pressed'),'false');
  await page.evaluate(()=>{Object.defineProperty(document,'hidden',{configurable:true,value:false});});
  checks.push('hidden page releases microphone and disables listening');
  const evidenceQuote = '  我常喝绿茶。<img src="x" onerror="window.memoryEvidenceExecuted=true">\n<script>window.memoryEvidenceExecuted=true</script>  ';
  memoryRecords = [
    { id: 81, kind: 'fact', source: 'stated', status: 'active', key: '饮品', value: '常喝绿茶', evidence_state: 'linked', evidence_quote: evidenceQuote,
      preference_target: '绿茶', preference_level: 'favorite', preference_scope: 'long_term', preference_as_of: 1704067200 },
    { id: 82, kind: 'fact', source: 'stated', status: 'active', key: '饮品', value: '最近也喜欢红茶', evidence_state: 'linked', evidence_quote: '我最近也喜欢红茶',
      preference_target: '<img src="x" onerror="window.memoryPreferenceExecuted=true">红茶', preference_level: 'like', preference_scope: 'recent', preference_as_of: 1717200000 },
    { id: 203, kind: 'fact', source: 'stated', status: 'active', key: '日常', value: '偏好晚间散步', evidence_state: 'manual' },
    { id: 901, kind: 'fact', source: 'stated', status: 'active', key: '日常', value: '旧版保存的记忆', evidence_state: 'legacy' },
    { id: 55, kind: 'fact', source: 'stated', status: 'superseded', key: '饮品', value: '以前常喝咖啡', evidence_state: 'linked', evidence_quote: '我以前常喝咖啡。' },
    { id: 602, kind: 'fact', source: 'inferred', status: 'conflict', key: '饮品', value: '可能改喝红茶', evidence_state: 'linked', evidence_quote: '红茶也不错。' },
  ];
  await page.locator('#memRefresh').click();
  await page.locator('#memList .mem').filter({ hasText: '常喝绿茶' }).waitFor();
  const linkedMemory = page.locator('#memList .mem').filter({ hasText: '常喝绿茶' });
  assert.match(await linkedMemory.innerText(), /有原话证据/);
  assert.match(await linkedMemory.innerText(), /最喜欢：绿茶/);
  assert.match(await linkedMemory.innerText(), /长期自述/);
  assert.match(await linkedMemory.innerText(), /原话日期 2024-01-01（UTC）/);
  await linkedMemory.locator('summary').click();
  assert.equal(await linkedMemory.locator('blockquote').textContent(), evidenceQuote, 'evidence keeps exact whitespace and HTML-looking text');
  assert.equal(await page.locator('#memList img, #memList script').count(), 0, 'evidence text never creates HTML nodes');
  assert.equal(await page.evaluate(() => window.memoryEvidenceExecuted), undefined, 'evidence text cannot execute script or event handlers');
  checks.push('linked memory reveals exact evidence as safe text, including whitespace and HTML-looking content');
  const recentMemory = page.locator('#memList .mem').filter({ hasText: '最近也喜欢红茶' });
  assert.match(await recentMemory.innerText(), /喜欢：.*红茶/);
  assert.match(await recentMemory.innerText(), /最近自述/);
  assert.match(await recentMemory.innerText(), /原话日期 2024-06-01（UTC）/);
  assert.match(await recentMemory.innerText(), /仅原话当时自述，未确认延续至今；未设定过期日/);
  assert.equal(await recentMemory.locator('.meta img, .meta script').count(), 0);
  assert.equal(await page.evaluate(() => window.memoryPreferenceExecuted), undefined);
  checks.push('favorite and additional recent like render degree and original date as safe text without claiming present validity');
  const manualMemory = page.locator('#memList .mem').filter({ hasText: '偏好晚间散步' });
  const legacyMemory = page.locator('#memList .mem').filter({ hasText: '旧版保存的记忆' });
  assert.match(await manualMemory.innerText(), /手动保存/);
  assert.match(await legacyMemory.innerText(), /旧记忆，未关联原话/);
  assert.equal(await manualMemory.locator('details').count(), 0);
  assert.equal(await legacyMemory.locator('details').count(), 0);
  assert.doesNotMatch(await manualMemory.innerText(), /你说过|有原话证据/);
  assert.doesNotMatch(await legacyMemory.innerText(), /你说过|有原话证据/);
  assert.doesNotMatch(await manualMemory.innerText(), /当时自述|未确认延续至今|原话日期/);
  assert.equal(await page.locator('#memList .mem').count(), 4, 'default list excludes history and conflicts');
  assert.equal(await page.locator('#memCount').textContent(), '4 条当前记忆');
  checks.push('manual and legacy memories use honest labels without invented original quotes');
  await page.locator('#textInput').fill('检查记忆提示');
  await page.locator('#sendBtn').click();
  const memoryTurn = (await sent()).filter(e => e.type === 'text').at(-1);
  const subtitleBeforeConflict = await page.locator('#subtitle').textContent();
  const requestsBeforeNotice = memoryRequests.length;
  const conflictRefresh = page.waitForResponse(response => response.url() === 'http://voice.test/api/memories');
  await emit({ type: 'memory_notice', turn_id: memoryTurn.turn_id, items: [{ id: 602, status: 'conflict', value: '可能改喝红茶' }] });
  await page.waitForFunction(() => document.querySelector('#memoryFeedback').textContent.includes('待核对'));
  await conflictRefresh;
  assert.ok(memoryRequests.length > requestsBeforeNotice, 'conflict notice refreshes the actual memory API');
  assert.equal(await page.locator('#subtitle').textContent(), subtitleBeforeConflict, 'conflict notice cannot claim an active successful save');
  assert.match(await page.locator('#memoryFeedback').textContent(), /有新说法待核对，原记忆仍保留/);
  assert.equal(await page.locator('#memList .mem').filter({ hasText: '可能改喝红茶' }).count(), 0);
  await page.locator('#memHistory').check();
  await page.locator('#memList .mem').filter({ hasText: '可能改喝红茶' }).waitFor();
  assert.equal(memoryRequests.filter(r => r.method === 'GET').at(-1).query, '?include_history=true');
  assert.match(await page.locator('#memList .mem').filter({ hasText: '以前常喝咖啡' }).innerText(), /历史/);
  assert.match(await page.locator('#memList .mem').filter({ hasText: '可能改喝红茶' }).innerText(), /待核对，不自动使用/);
  assert.equal(await page.locator('#memList .mem').count(), 6);
  assert.equal(await page.locator('#memCount').textContent(), '4 条当前记忆', 'history does not inflate the active count');
  checks.push('history toggle fetches superseded and conflict records, while conflict notice retains the active memory honestly');
  promptReply = '改为上午散步';
  await page.locator('#memList .mem').filter({ hasText: '偏好晚间散步' }).locator('.val').click();
  await page.locator('#memList .mem').filter({ hasText: '改为上午散步' }).waitFor();
  const patch = memoryRequests.findLast(r => r.method === 'PATCH');
  assert.deepEqual(patch, { method: 'PATCH', path: '/api/memories/203', query: '', body: { value: '改为上午散步' } }, 'editing uses the original memory ID');
  assert.equal(memoryRequests.at(-1).method, 'GET', 'successful edit refreshes the list');
  assert.equal(await page.locator('#memoryFeedback').textContent(), '记忆已更新。');
  assert.equal(await page.locator('#memList .mem').filter({ hasText: '偏好晚间散步' }).count(), 0);
  const correctedMemory = page.locator('#memList .mem').filter({ hasText: '改为上午散步' });
  assert.match(await correctedMemory.innerText(), /手动保存/);
  assert.doesNotMatch(await correctedMemory.locator('.meta').innerText(), /喜欢：|当时自述|原话日期|未确认延续至今/);
  promptReply = '绿茶只偶尔喝，未说明偏好程度';
  await linkedMemory.locator('.val').click();
  const correctedPreference = page.locator('#memList .mem').filter({ hasText: '绿茶只偶尔喝，未说明偏好程度' });
  await correctedPreference.waitFor();
  assert.match(await correctedPreference.innerText(), /手动保存/);
  assert.doesNotMatch(await correctedPreference.locator('.meta').innerText(), /最喜欢：|长期自述|原话日期/);
  assert.equal(await correctedPreference.locator('details').count(), 0);
  assert.match(await recentMemory.innerText(), /最近自述/);
  checks.push('manual correction refresh clears derived preference/time labels while retaining the other preference member');
  await page.locator('#memList .mem').filter({ hasText: '旧版保存的记忆' }).getByRole('button', { name: '忘掉' }).click();
  await page.waitForFunction(() => document.querySelector('#memList').textContent.includes('旧版保存的记忆') === false);
  const forget = memoryRequests.findLast(r => r.method === 'DELETE');
  assert.equal(forget.path, '/api/memories/901', 'forgetting uses the original memory ID');
  assert.equal(memoryRequests.at(-1).method, 'GET', 'successful forget refreshes the list');
  assert.equal(await page.locator('#memoryFeedback').textContent(), '已忘掉这条记忆。');
  assert.equal(await page.locator('#memCount').textContent(), '3 条当前记忆');
  checks.push('edit and forget preserve original IDs and display the refreshed saved result');
  await page.locator('#stopBtn').click();
  const retiredListenRequest = (await sent()).findLast(e => e.experience_request_id)?.experience_request_id;
  await page.selectOption('#experienceMode', 'off');
  await page.locator('#experienceApply').click();
  await ackExperience({type:'config_ack',experience:{mode:'off',scene:'',source:{text:''}},practice:{mode:'off',level:'B1',correction:'after'}});
  await page.locator('details.preferences').evaluate(node => { node.open = true; });
  await page.locator('[data-voice-baseline="panel"]').evaluate(node => { node.open = true; });
  for (const width of [1440, 900]) {
    await page.setViewportSize({width,height:1000});
    assert.ok(await page.locator('[data-voice-baseline="panel"]').evaluate(panel => {
      const bounds = panel.getBoundingClientRect();
      return [...panel.querySelectorAll('select,button')].every(node => {
        const box = node.getBoundingClientRect();
        return box.left >= bounds.left - 1 && box.right <= bounds.right + 1;
      });
    }), 'baseline controls fit both wide and narrow desktop sidebars');
  }
  await page.setViewportSize({width:1440,height:1000});
  checks.push('baseline scenario, judgment and export controls fit wide and narrow desktop sidebars');
  const baselineControl = name => page.locator(`[data-baseline="${name}"]`);
  const tracksBeforeBaseline = await page.evaluate(() => voiceHarness.tracks.length);
  const configsBeforeBaseline = (await sent()).filter(e => e.type === 'config').length;
  assert.equal(await baselineControl('device').inputValue(), 'speaker');
  await baselineControl('start').click();
  assert.match(await baselineControl('status').textContent(), /请先开启/);
  assert.equal(await page.evaluate(() => voiceHarness.tracks.length), tracksBeforeBaseline, 'trial start never requests microphone permission');
  assert.equal((await sent()).filter(e => e.type === 'config').length, configsBeforeBaseline, 'collector never changes server settings');
  assert.equal(await page.locator('#duplexMode').inputValue(), 'headset', 'speaker trial default never silently changes duplex strategy');
  checks.push('baseline is opt-in, defaults to speaker environment and starts neither microphone nor server config');
  await page.locator('#bargeToggle').click();
  await page.waitForFunction(()=>voiceHarness.tracks.some(t=>!t.stopped));
  await page.selectOption('#duplexMode', 'speaker');
  await baselineControl('start').click();
  assert.ok(await baselineControl('scenario').isDisabled());
  assert.ok(await baselineControl('device').isDisabled());
  await emit({type:'config_ack',experience_request_id:retiredListenRequest,experience:{mode:'listen',source:appliedSource},practice:{mode:'free',level:'B1',correction:'after'}});
  assert.match(await baselineControl('status').textContent(), /记录中/);
  assert.ok(await page.locator('#experienceActiveSource').isHidden());
  checks.push('rejected late experience acknowledgement changes neither active trial context nor endpoint wait');
  // The mock callback covers its previous 21ms; begin fresh synthetic samples
  // after the trial boundary instead of assigning pre-trial samples to it.
  await page.waitForTimeout(35);
  await feed(.08, 0); // Drain the mock capture's partial chunk across the trial boundary.
  await feed(.7, .12); await feed(1.3, 0);
  const measuredVoice = (await sent()).filter(e => e.type === 'start').at(-1);
  await emit({type:'asr',turn_id:measuredVoice.turn_id,text:'private transcript must not export'});
  await emit({type:'audio',turn_id:measuredVoice.turn_id,data:'AA==',text:'private reply must not export',emotion:'base'});
  await page.waitForFunction(() => voiceHarness.sources.length > 0);
  await emit({type:'done',turn_id:measuredVoice.turn_id,text:'private reply must not export',delivery:{text_status:'complete',audio_status:'synthesized'}});
  await page.evaluate(() => voiceHarness.finishPlayback());
  await baselineControl('recognition').selectOption('correct');
  await baselineControl('interruption').selectOption('not_attempted');
  await baselineControl('seam').selectOption('smooth');
  await baselineControl('finish').click();
  let evidence = await exportedBaseline();
  const measured = evidence.trials[0];
  assert.equal(measured.status, 'complete');
  assert.equal(measured.setup.echoCancellation, true);
  assert.equal(measured.setup.noiseSuppression, false);
  assert.equal(measured.setup.autoGainControl, null, 'unknown effective settings are not invented');
  assert.equal(measured.setup.timingSource, 'script_callback_estimate', 'fallback ignores future output playbackTime and labels its input estimate');
  assert.equal(measured.setup.activity, 'off');
  assert.equal(measured.setup.endpointWaitMs, 900);
  for (const type of ['turn_begin','speech_first_energy','speech_last_energy','audio_submit','asr_received','audio_received','decode_start','decode_end','playback_start','playback_end','turn_done']) {
    assert.ok(measured.events.some(e => e.type === type), `real app seam records ${type}`);
  }
  const measuredGroup = evidence.summary.groups.find(g => g.input === 'voice_auto');
  assert.equal(measuredGroup.metrics.audioSubmitToPlaybackScheduledMs.n, 1);
  assert.equal(measuredGroup.metrics.speechLastEnergyToPlaybackScheduledMs.n, 1);
  assert.equal(evidence.physicalAcousticMetrics.status, 'not_measured');
  assert.doesNotMatch(JSON.stringify(evidence), /private transcript|private reply|client-|AA==|session_id|turn_id|deviceId/);
  checks.push('real app voice seams export detector and scheduling metadata, actual browser settings and no conversation content');
  await baselineControl('scenario').selectOption('speaker_interrupt_long');
  await baselineControl('start').click();
  await page.locator('#textInput').fill('baseline playback prompt'); await page.locator('#sendBtn').click();
  const interruptTarget = (await sent()).filter(e => e.type === 'text').at(-1);
  await emit({type:'audio',turn_id:interruptTarget.turn_id,data:'AA==',text:'interrupt playback'});
  await page.waitForFunction(() => voiceHarness.sources.length > 0);
  await page.waitForTimeout(35);
  await feed(.08, 0);
  await feed(.8, .15); await feed(1.3, 0);
  const interruptionVoice = (await sent()).filter(e => e.type === 'start').at(-1);
  await emit({type:'done',turn_id:interruptionVoice.turn_id,text:''});
  await baselineControl('interruption').selectOption('correct');
  await baselineControl('finish').click();
  evidence = await exportedBaseline();
  const interruptedTrial = evidence.trials[1];
  for (const type of ['gate_onset','gate_candidate','gate_commit','playback_stop_requested']) assert.ok(interruptedTrial.events.some(e => e.type === type), `real interruption records ${type}`);
  const textGroup = evidence.summary.groups.find(g => g.scenario === 'speaker_interrupt_long' && g.input === 'text');
  assert.equal(textGroup.metrics.gateOnsetToPlaybackStopRequestedMs.n, 1);
  assert.equal(textGroup.metrics.audioSubmitToPlaybackScheduledMs.n, 0, 'text input cannot become speech latency evidence');
  checks.push('spoken interruption records detector onset through actual stop request, keeping text outside speech latency');
  await baselineControl('scenario').selectOption('long_explanation');
  await baselineControl('start').click();
  await page.locator('#resumeSpeechBtn').click();
  await page.waitForFunction(() => voiceHarness.sources.length > 0);
  await page.waitForTimeout(35); await feed(.08, 0);
  await feed(.8, .15); await feed(1.3, 0);
  const resumedInterruption = (await sent()).filter(e => e.type === 'start').at(-1);
  await emit({type:'done',turn_id:resumedInterruption.turn_id,text:''});
  await baselineControl('interruption').selectOption('correct');
  await baselineControl('finish').click();
  evidence = await exportedBaseline();
  const resumeGroup = evidence.summary.groups.find(g => g.scenario === 'long_explanation' && g.input === 'resume');
  assert.equal(resumeGroup.metrics.gateOnsetToPlaybackStopRequestedMs.n, 1, 'local resume has a metadata turn even without a server turn');
  assert.equal(resumeGroup.metrics.decodeStartToEndMs.n, 0, 'cached resume does not masquerade as network audio decoding');
  assert.equal(resumeGroup.metrics.audioSubmitToPlaybackScheduledMs.n, 0);
  checks.push('interrupting audio-only continuation records its own gate timing without new generation or decode latency');
  await page.locator('#stopBtn').click();
  await feed(1.3, 0);
  await baselineControl('start').click();
  await page.locator('#textInput').fill('pending decode trial'); await page.locator('#sendBtn').click();
  const delayedTrialTurn = (await sent()).filter(e => e.type === 'text').at(-1);
  await page.evaluate(() => { voiceHarness.holdDecode = true; });
  await emit({type:'audio',turn_id:delayedTrialTurn.turn_id,data:'AA==',text:'late audio'});
  await page.waitForFunction(() => voiceHarness.decodeWait.length === 1);
  await page.selectOption('#duplexMode', 'headset');
  assert.match(await baselineControl('status').textContent(), /无效.*策略变化/);
  await baselineControl('start').click();
  await page.evaluate(() => { voiceHarness.holdDecode = false; voiceHarness.decodeWait.shift()({duration:.1}); });
  await page.waitForFunction(() => voiceHarness.sources.length > 0);
  await page.evaluate(() => voiceHarness.finishPlayback());
  await baselineControl('finish').click();
  evidence = await exportedBaseline();
  assert.equal(evidence.trials.at(-2).status, 'invalid');
  assert.equal(evidence.trials.at(-2).invalidReason, 'mode_changed');
  assert.equal(evidence.trials.at(-1).events.length, 0, 'late decode and playback from an older trial never enter the next trial');
  assert.equal(evidence.summary.trials.invalid, 1);
  checks.push('strategy changes invalidate trials and delayed audio callbacks retain their original trial token');
  await baselineControl('start').click();
  await page.evaluate(()=>voiceHarness.ws.close());
  evidence = await exportedBaseline();
  assert.equal(evidence.trials.at(-1).invalidReason, 'disconnected');
  checks.push('disconnect invalidates active baseline while retaining exportable local failure evidence');
  assert.ok(await page.evaluate(()=>voiceHarness.tracks.every(t=>t.stopped)),'disconnect releases tracks');
  assert.ok(await page.locator('#experienceActiveSource').isHidden(), 'disconnect invalidates applied source');
  assert.ok(await page.locator('#experienceExplain').isDisabled());
  assert.ok(await page.locator('#experienceApply').isDisabled());
  assert.equal(await page.locator('#experienceSource').inputValue(), revisedSource.text, 'disconnect preserves local draft');
  await emit({ type: 'config_ack', experience: { mode: 'listen', source: revisedSource } });
  assert.ok(await page.locator('#experienceActiveSource').isHidden(), 'late ack cannot revive closed session');
  checks.push('disconnect releases microphone and invalidates experience while preserving draft');
  assert.deepEqual(errors,[],'no uncaught browser errors');
  console.log(JSON.stringify({ok:true,checks},null,2));
} finally {await browser.close();}
