import assert from 'node:assert/strict';
import test from 'node:test';
import { Avatar } from './index.js';
import { ALL_PARAMS } from './params.js';
import { softLimitStack } from './params.js';
import { IdleDriver } from './drivers/idle.js';
import { AvatarCalibration } from './calibration.js';

function fixture({ emotions = {}, gestures = {}, triggers = {}, profile = 'native', touch = TOUCH_FIXTURE } = {}) {
  const a = new Avatar();
  a.configure({response:Object.fromEntries(ALL_PARAMS.map(key=>[key,0])),
    emotions:{base:{shift:{}},...emotions},gestures,triggers,touch});
  a.svg = {draw(params) { this.params=params; }, getEyeCapabilities() { return {profile}; },
    setSecondaryMotion() {}};
  a.memoryInterval=Infinity;
  a.idle.nextBlink=Infinity;
  a.audioCtx={currentTime:0,state:'running'};
  return a;
}
function advance(a, duration) {
  for (let t=0;t<duration-1e-9;t+=1/60) { a.clock+=1/60; a._frame(1/60); }
}
const close=(value,wanted)=>assert.ok(Math.abs(value-wanted)<1e-7,`${value} != ${wanted}`);

/* A-4 触控热区的最小 DOM 桩：只需要 addEventListener / style / 矩形 / 指针捕获。 */
class FakeStage {
  constructor() {
    this.style = {}; this.listeners = new Map(); this.captured = [];
    this.rect = { left: 0, top: 0, width: 200, height: 200 };
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  removeEventListener(type, fn) {
    const list = this.listeners.get(type) || [];
    const index = list.indexOf(fn);
    if (index >= 0) list.splice(index, 1);
  }
  getBoundingClientRect() { return this.rect; }
  setPointerCapture(id) { this.captured.push(id); }
  releasePointerCapture(id) {
    const index = this.captured.indexOf(id);
    if (index >= 0) this.captured.splice(index, 1);
  }
  emit(type, event) {
    for (const fn of this.listeners.get(type) || []) fn({ pointerId: 1, timeStamp: 0, ...event });
  }
}

const TOUCH_FIXTURE = { svg: {
  head: [0.2, 0.0, 0.8, 0.4],
  face: [0.3, 0.1, 0.7, 0.5],
  hands: [[0.0, 0.5, 0.3, 1.0], [0.7, 0.5, 1.0, 1.0]],
} };

const sampleFrames = (a, seconds, visit) => {
  for (let t = 0; t < seconds - 1e-9; t += 1 / 60) { a.clock += 1 / 60; a._frame(1 / 60); visit?.(a); }
};

test('turning off chat pointer following restores viewer gaze while idle',()=>{
  const a=fixture();
  a.setMouseFollow(true);
  a.onUserInteraction('mousemove',.42,.1);
  assert.equal(a.idle.targetName,'custom');
  a.setMouseFollow(false); advance(a,.4);
  assert.equal(a.gazeMode,'natural');
  assert.equal(a.idle.mouseFollow,false);
  assert.equal(a.idle.targetName,'viewer');
  assert.equal(a.dialogueFrame.state,'idle');
});

test('waiting survives generation events and duplicate thinking until the actual audio boundary',()=>{
  const a=fixture();
  a.onMessage({type:'thinking',turn_id:'one'}); advance(a,.55);
  assert.equal(a.dialogueFrame.phase,'averted');
  const age=a.dialogueGaze.age;
  a.onMessage({type:'thinking',turn_id:'one'});
  close(a.dialogueGaze.age,age);
  a.onMessage({type:'emotion',emotion:'base',turn_id:'one'});
  a.onMessage({type:'text_delta',delta:'回答',turn_id:'one'});
  a.onMessage({type:'done',turn_id:'one'});
  advance(a,.05);
  assert.notEqual(a.dialogueFrame.state,'speaking');
  assert.equal(a.dialogueFrame.phase,'averted');
  a.speak('你好',a.audioCtx,1,2,{performance:{version:1,source:'llm',cues:[]}});
  advance(a,.05); assert.notEqual(a.dialogueFrame.state,'speaking');
  a.audioCtx.currentTime=1; advance(a,.3);
  assert.equal(a.dialogueFrame.state,'speaking');
  close(a.idle.customAnchor.x,0); close(a.idle.customAnchor.y,0);
});

test('a listening body/brow gesture does not freeze micro-saccades',()=>{
  const a=fixture({triggers:{on_user_speech:'listen'},gestures:{listen:{hold:true,keys:[
    {at:0,dur:.1,params:{ParamAngleY:.1,ParamBrowLY:.05}},
  ]}}});
  a.onMessage({type:'recording',state:true});
  advance(a,.4);
  assert.equal(a.dialogueFrame.owner,'dialogue');
  assert.equal(a.idle.gazeOwned,false);
  const observed=new Set();
  for (let i=0;i<240;i++) {a.clock+=1/60;a._frame(1/60);observed.add(a.store.target.ParamEyeBallX.toFixed(6));}
  assert.ok(observed.size>5,'eyes keep moving slightly around the attention target');
  assert.ok(Math.abs(a.idle.gazeToX)<=.035 && Math.abs(a.idle.gazeToY)<=.035);
});

test('default thinking keeps its brow/head track while the dialogue schedule owns gaze',()=>{
  const a=fixture({triggers:{on_thinking:'think'},gestures:{think:{hold:true,keys:[
    {at:0,dur:.1,params:{ParamEyeBallX:.8,ParamEyeBallY:-.7,ParamBrowLY:.2}},
  ]}}});
  a.onMessage({type:'thinking',turn_id:'one'}); advance(a,.7);
  assert.equal(a.dialogueFrame.owner,'dialogue');
  close(a.idle.customAnchor.x,a.dialogueFrame.glance.x);
  close(a.idle.customAnchor.y,a.dialogueFrame.glance.y);
  assert.ok(a.store.target.ParamBrowLY>.1);
  assert.ok(a.store.target.ParamEyeBallX<.5,'old eye track is not added again');
});

test('explicit gesture gaze, including zero, retains sole ownership',()=>{
  for (const x of [0,.7]) {
    const a=fixture({gestures:{look:{hold:true,keys:[{at:0,dur:.1,params:{ParamEyeBallX:x,ParamEyeBallY:.1}}]}}});
    a.setDialogueState('thinking'); advance(a,.5);
    a.gesture.play('look',a.clock); advance(a,.4);
    assert.equal(a.dialogueFrame.owner,'gesture'); assert.equal(a.dialogueFrame.suspended,true);
    close(a.store.target.ParamEyeBallX,x); close(a.store.target.ParamEyeBallY,.1);
  }
});

test('settled explicit emotion gaze is protected and manual forward overrides it',()=>{
  const a=fixture({emotions:{look:{owns_gaze:true,shift:{ParamEyeBallX:.5,ParamEyeBallY:.1}}}});
  a.setDialogueState('thinking'); a.setEmotion('look'); advance(a,6);
  assert.equal(a.dialogueFrame.owner,'emotion'); assert.equal(a.emotion.ownsGaze,false);
  close(a.store.target.ParamEyeBallX,a.emotion.shift().ParamEyeBallX);
  a.setGazeMode('forward'); advance(a,.1);
  close(a.svg.params.ParamEyeBallX,0); close(a.svg.params.ParamEyeBallY,0);
});

test('sentence gaps retain viewer attention and cancellation cannot revive an old scheduled clip',()=>{
  const a=fixture(); a.setDialogueState('thinking'); advance(a,.5);
  a.speak('你好',a.audioCtx,0,1); advance(a,.1);
  a.audioCtx.currentTime=1; a.stopSpeaking({turnPending:true}); advance(a,.5);
  assert.equal(a.dialogueFrame.state,'preparing'); assert.equal(a.dialogueFrame.phase,'attending');
  close(a.idle.customAnchor.x,0);
  a.speak('下一句',a.audioCtx,2,1); a.onBargeIn(); a.setDialogueState('idle');
  a.audioCtx.currentTime=2.5; advance(a,2);
  assert.equal(a.dialogueFrame.active,false); assert.equal(a.lip.speaking,false);
  assert.equal(a.idle.targetName,'viewer');
});

test('eye previews, full blink and speech retain their respective ownership',()=>{
  const a=fixture(); a.setDialogueState('thinking'); advance(a,.5);
  a.setEyeExpression('happy'); advance(a,.4);
  assert.equal(a.dialogueFrame.owner,'manual'); assert.equal(a.dialogueFrame.suspended,true);
  a.speak('你好',a.audioCtx,0,2); a.audioCtx.currentTime=.3;
  a.blinkEyes(); advance(a,.1);
  close(a.svg.params.ParamEyeLOpen,0); close(a.svg.params.ParamEyeROpen,0);
  assert.ok(a.svg.params.ParamMouthOpenY>0);
});

// ---- A-2 thinking pre-act ----

test('thinking pre-act adds micro tilt and one light blink without touching gaze',()=>{
  const a=fixture({triggers:{on_thinking:'think'},gestures:{think:{hold:true,keys:[
    {at:0,dur:.1,params:{ParamAngleZ:-0.26}},
  ]}}});
  a.onMessage({type:'thinking',turn_id:'one'});
  assert.equal(a.thinkingPreact.running,true);
  const frame=a.thinkingPreact.update(1/60,{reducedMotion:false});
  assert.ok(Math.abs(frame.values.ParamAngleZ)<=.05+1e-9,'tilt stays a micro motion');
  assert.ok(!('ParamEyeBallX' in frame.values)&&!('ParamEyeBallY' in frame.values),
    'gaze stays with the dialogue schedule; the pre-act never writes eye coordinates');
  let minOpen=1;
  sampleFrames(a,1.4,avatar=>{minOpen=Math.min(minOpen,avatar.svg.params.ParamEyeLOpen);});
  assert.equal(minOpen,0,'exactly one full blink happens during the wait');
  assert.equal(a.dialogueFrame.owner,'dialogue','the thinking gaze schedule keeps ownership');
  // 预演落在 idle 层：think 手势的歪头仍在，上面多了一层微动
  assert.ok(Math.abs(a.store.target.ParamAngleZ+0.26)>1e-4,'micro motion rides over the thinking pose');
});

test('thinking pre-act retires on first audio, done, interruption and a new turn',()=>{
  const retire=(event)=>{const a=fixture();a.onMessage({type:'thinking',turn_id:'one'});
    assert.equal(a.thinkingPreact.running,true);a.onMessage(event);
    assert.equal(a.thinkingPreact.running,false,`${event.type} must retire the pre-act`);};
  retire({type:'done',turn_id:'one',text:'x'});
  retire({type:'interrupted',turn_id:'one'});
  retire({type:'playback_stopped',turn_id:'one'});
  retire({type:'recording',state:true,turn_id:'two'});
  const a=fixture();
  a.onMessage({type:'thinking',turn_id:'one'});
  a.speak('你好',a.audioCtx,1,2);
  assert.equal(a.thinkingPreact.running,false,'first audio retires the pre-act');
});

test('reduced motion degrades the thinking pre-act to a static wait',()=>{
  const a=fixture();
  a.setReducedMotion(true);
  a.onMessage({type:'thinking',turn_id:'one'});
  sampleFrames(a,1.2);
  assert.equal(a.thinkingPreact.running,true);
  assert.equal(a.thinkingPreact.update(1/60,{reducedMotion:true}),null);
  assert.ok(a.svg.params.ParamEyeLOpen>.9,'no added blink under reduced motion');
});

// ---- A-1 breath point consumption ----

test('a breath chunk blinks once at the pause and keeps the mouth relaxed through it',()=>{
  const a=fixture();
  a.onMessage({type:'thinking',turn_id:'one'});
  a.onMessage({type:'emotion',emotion:'base',turn_id:'one'});
  a.speak('你好呀，',a.audioCtx,1,.5,{chunk:{seq:0,of:2,chars:4,pause_after_ms:260,breath:true}});
  close(a._breathBlinkAt,1.5,'the breath point is where the audio ends');
  a.audioCtx.currentTime=1.2;
  let minOpen=1;
  sampleFrames(a,.2,avatar=>{minOpen=Math.min(minOpen,avatar.svg.params.ParamEyeLOpen);});
  // 张大嘴时的共发音眯眼会把眼睛压窄，但永远不会闭合——只有眨眼会。
  assert.ok(minOpen>.5,'no blink before the breath point');
  a.audioCtx.currentTime=1.6;
  minOpen=1;
  sampleFrames(a,.2,avatar=>{minOpen=Math.min(minOpen,avatar.svg.params.ParamEyeLOpen);});
  assert.equal(minOpen,0,'the breath point triggers exactly one light blink');
  assert.equal(a._breathBlinkAt,null);
  a.audioCtx.currentTime=1.7;
  sampleFrames(a,.05);
  assert.ok(a.lip.speaking,'the mouth timeline spans the breath pause');
  assert.ok(a.svg.params.ParamMouthOpenY<=.08,'and the mouth holds its relaxed rest shape');
  a.stopSpeaking();
  assert.equal(a._breathBlinkAt,null,'stopping invalidates a pending breath blink');
});

// ---- A-3 idle-layer soft limiting ----

test('idle micro-motion stacking is soft-limited above the knee and identical below it',()=>{
  const opts={knee:.45,ceiling:.75};
  assert.equal(softLimitStack('ParamAngleY',.3,opts),.3,'below the knee: identity');
  assert.equal(softLimitStack('ParamAngleY',.45,opts),.45,'at the knee: identity');
  assert.equal(softLimitStack('ParamEyeLOpen',5,opts),5,'non-angle channels pass through');
  const high=softLimitStack('ParamAngleY',3,opts);
  assert.ok(high>.74&&high<.75,'above the knee: compressed toward the ceiling');
  assert.ok(softLimitStack('ParamAngleY',-3,opts)>-.75&&softLimitStack('ParamAngleY',-3,opts)<-.74,'symmetric');
  const slope=(softLimitStack('ParamAngleY',.46,opts)-softLimitStack('ParamAngleY',.45,opts));
  assert.ok(Math.abs(slope-.01)<.005,'C1 at the knee: no new velocity kink');

  const a=fixture();
  a.beat.pulse.kick(50);
  a.motion.setComponents([{target:'ParamAngleY',freq:1,amp:1,phase:0}]);
  let peak=0;
  sampleFrames(a,1.2,avatar=>{peak=Math.max(peak,Math.abs(avatar.store.target.ParamAngleY));});
  assert.ok(peak>.45,'the sources really did stack above the knee');
  assert.ok(peak<=.75+1e-9,'and the accumulated idle contribution stays under the ceiling');
});

// ---- A-4 touch zones ----

test('pointer taps trigger region feedback while drags gaze and pinches pass through',()=>{
  const a=fixture();
  const stage=new FakeStage();
  a._mountTouchZones(stage);
  assert.equal(stage.style.pointerEvents,'auto','the stage becomes hit-testable');
  assert.equal(stage.style.touchAction,'pinch-zoom','two fingers still zoom the page');
  stage.emit('pointerdown',{pointerId:1,clientX:100,clientY:10,timeStamp:0});
  stage.emit('pointerup',{pointerId:1,clientX:100,clientY:10,timeStamp:120});
  assert.equal(a.eyeExpression.name,'happy','a tap on the head is a pat');
  stage.emit('pointerdown',{pointerId:2,clientX:100,clientY:60,timeStamp:200});
  stage.emit('pointerup',{pointerId:2,clientX:100,clientY:60,timeStamp:320});
  assert.equal(a.eyeExpression.name,'surprised','a tap on the face is a poke');
  a.setGazeMode('follow');
  stage.emit('pointerdown',{pointerId:3,clientX:100,clientY:60,timeStamp:400});
  stage.emit('pointermove',{pointerId:3,clientX:160,clientY:120,timeStamp:460});
  stage.emit('pointerup',{pointerId:3,clientX:160,clientY:120,timeStamp:500});
  assert.equal(a.idle.targetName,'custom','a drag gazes instead of poking');
  assert.equal(a.eyeExpression.name,'surprised','a drag never retriggers a reaction');
  stage.emit('pointerdown',{pointerId:4,clientX:60,clientY:10,timeStamp:600});
  stage.emit('pointerdown',{pointerId:5,clientX:140,clientY:10,timeStamp:620});
  stage.emit('pointerup',{pointerId:4,clientX:60,clientY:10,timeStamp:660});
  stage.emit('pointerup',{pointerId:5,clientX:140,clientY:10,timeStamp:660});
  assert.equal(a.eyeExpression.name,'surprised','a two-finger pinch is not a tap');
  a._disposeTouchZones();
  assert.equal(stage.listeners.get('pointerdown').length,0,'dispose removes every listener');
});

test('touch feedback retires its own preview and pokes escalate to a pout',()=>{
  const a=fixture();
  a.onTouchRegion('hand',{x:.1,y:.8});
  assert.equal(a.performance.sample().name,'wave');
  assert.equal(a.performance.sample().source,'preview','a hand touch waves through the preview channel');
  a.onTouchRegion('head',{x:.5,y:.1});
  assert.equal(a._touchPreviewName,'happy');
  sampleFrames(a,2.4);
  assert.equal(a.eyeExpression.name,null,'the preview retires itself');
  a.onTouchRegion('face',{x:.5,y:.3});
  a.onTouchRegion('face',{x:.5,y:.3});
  assert.equal(a.eyeExpression.name,'surprised');
  a.onTouchRegion('face',{x:.5,y:.3});
  assert.equal(a.eyeExpression.name,'tsundere','a third consecutive poke turns into a pout');
  a.onBargeIn();
  assert.equal(a.eyeExpression.name,null,'interruption clears the touch preview');
});

test('attention micro context is bounded and reset restores the configured idle context',()=>{
  const idle=new IdleDriver();
  idle.setGazeContext({drift:9,intervalScale:30});
  assert.deepEqual(idle.gazeContext,{drift:.3,intervalScale:4,headFollowScale:1,headDelayScale:1,headRecruitScale:1});
  idle.setGazeContext(); assert.deepEqual(idle.gazeContext,{drift:null,intervalScale:1,headFollowScale:1,headDelayScale:1,headRecruitScale:1});
  idle.setGazeContext({drift:NaN,intervalScale:Infinity});
  assert.deepEqual(idle.gazeContext,{drift:null,intervalScale:1,headFollowScale:1,headDelayScale:1,headRecruitScale:1});
});

test('dialogue cues fill missing brow and V3 lid channels without touching speech or eye opening',()=>{
  const a=fixture({profile:'seed-lids'});
  a.onMessage({type:'recording',state:true}); advance(a,.7);
  assert.ok(a.dialogueExpressionFrame.appliedValues.ParamBrowLY>.05);
  assert.ok(a.store.target.ParamBrowLY>.05);
  a.speak('你好',a.audioCtx,0,2); a.audioCtx.currentTime=.7; advance(a,.7);
  assert.ok(a.dialogueExpressionFrame.appliedValues.ParamEyeLSmile>.03);
  assert.ok(a.svg.params.ParamEyeLLower>.04);
  assert.equal(a.dialogueFrame.state,'speaking');
  assert.ok(a.svg.params.ParamMouthOpenY>0);
  a.blinkEyes(); advance(a,.1);
  close(a.svg.params.ParamEyeLOpen,0); close(a.svg.params.ParamEyeROpen,0);
});

test('explicit emotion or gesture lid zero protects both shapes on its side',()=>{
  for (const intent of ['emotion','gesture']) {
    const a=fixture({profile:'seed-lids',
      emotions:{quiet:{shift:{ParamEyeLSmile:0}}},
      gestures:{quiet:{hold:true,keys:[{at:0,dur:.1,params:{ParamEyeLLower:0}}]}}});
    a.speak('你好',a.audioCtx,0,2); a.audioCtx.currentTime=.7;
    if (intent==='emotion') a.setEmotion('quiet'); else a.gesture.play('quiet',a.clock);
    advance(a,.7);
    assert.ok(a.dialogueExpressionFrame.protectedKeys.includes('ParamEyeLSmile'));
    assert.ok(a.dialogueExpressionFrame.protectedKeys.includes('ParamEyeLLower'));
    assert.equal(a.dialogueExpressionFrame.appliedValues.ParamEyeLSmile,undefined);
    assert.equal(a.dialogueExpressionFrame.appliedValues.ParamEyeLLower,undefined);
    close(a.svg.params.ParamEyeLSmile,0); close(a.svg.params.ParamEyeLLower,0);
  }
});

test('manual and preview ownership exclude dialogue face cues and interruption clears pending cue',()=>{
  const a=fixture({profile:'seed-lids'});
  a.onMessage({type:'recording',state:true}); advance(a,.7);
  a.setGazeMode('forward'); advance(a,.1);
  assert.deepEqual(a.dialogueExpressionFrame.appliedValues,{});
  a.setGazeMode('natural'); a.setEyeExpression('happy'); advance(a,.5);
  assert.deepEqual(a.dialogueExpressionFrame.appliedValues,{});
  a.setEyeExpression(null); advance(a,.4);
  a.speak('你好',a.audioCtx,0,2); a.audioCtx.currentTime=.1; advance(a,.05);
  a.onBargeIn(); a.audioCtx.currentTime=1; advance(a,.8);
  assert.equal(a.dialogueExpressionFrame.active,false);
  assert.ok(Object.values(a.dialogueExpressionFrame.appliedValues).every(value=>value===0));
});

test('changing dialogue style during actual speech preserves clips and changes face/context scale',()=>{
  const a=fixture({profile:'seed-lids'});
  a.speak('你好',a.audioCtx,0,3); a.audioCtx.currentTime=.8;
  a.setDialogueStyle('restrained'); advance(a,.6);
  const gentle=a.svg.params.ParamEyeLSmile;
  const clips=JSON.stringify(a.dialogueGaze._clips);
  a.setDialogueStyle('lively'); advance(a,.6);
  assert.equal(JSON.stringify(a.dialogueGaze._clips),clips);
  assert.equal(a.dialogueFrame.state,'speaking');
  assert.ok(a.svg.params.ParamEyeLSmile>gentle*1.5);
  assert.equal(a.idle.gazeContext.headFollowScale,.85);
});

test('head context keeps target allocation conserved and eyes lead the delayed head',()=>{
  const idle=new IdleDriver({gaze_coord:{recruit:.1,head_lag:.1}});
  for(let i=0;i<30;i++)idle.update(.01);
  idle.setGazeContext({drift:0,headFollowScale:.65,headDelayScale:1.5,headRecruitScale:.75});
  idle.setGazeTarget(.6,.2);
  close(idle.gazeToX+idle.headToX,.6); close(idle.gazeToY+idle.headToY,.2);
  const output=idle.update(.04);
  assert.ok(output.gazeX>0);
  close(output.headX,0); close(output.headY,0);
  idle.setGazeContext({headFollowScale:0,headDelayScale:99,headRecruitScale:-99});
  idle.setGazeTarget(.6,.2);
  close(idle.headToX,0); close(idle.gazeToX,.6);
  assert.equal(idle.gazeContext.headDelayScale,2);
  assert.equal(idle.gazeContext.headRecruitScale,.5);
});

test('the real RAF host skips stale waiting glances while body integration remains capped',()=>{
  const a=fixture(), pending=new Map(), bodySteps=[];
  const saved=Object.fromEntries(['performance','requestAnimationFrame','cancelAnimationFrame']
    .map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
  let now=0, sequence=0;
  const idleUpdate=a.idle.update.bind(a.idle);
  a.idle.update=(dt,options)=>{bodySteps.push(dt);return idleUpdate(dt,options);};
  const tick=milliseconds=>{
    now+=milliseconds;
    const next=pending.entries().next().value;
    assert.ok(next,'Avatar._start must schedule its next real loop callback');
    const [id,callback]=next;
    pending.delete(id);
    callback(now);
  };
  try {
    Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>now}});
    Object.defineProperty(globalThis,'requestAnimationFrame',{configurable:true,value:callback=>{
      const id=++sequence;pending.set(id,callback);return id;
    }});
    Object.defineProperty(globalThis,'cancelAnimationFrame',{configurable:true,value:id=>pending.delete(id)});
    a.onMessage({type:'thinking',turn_id:'raf-long-gap'});
    const first=a.dialogueGaze.sample().plan.glances[0];
    a._start();
    const firstObservedAt=(first.start+.2)*1000;
    while(now<firstObservedAt-1e-8)tick(Math.min(1000/60,firstObservedAt-now));
    assert.equal(a.dialogueFrame.phase,'averted');
    assert.equal(a.dialogueFrame.glance.index,0);
    assert.ok(Math.abs(a.idle.customAnchor.x)>.1);

    // Run the exact callback that a throttled/background RAF would deliver.
    // Passing a large dt directly to the director would miss this host bug.
    tick(2000);
    assert.ok(bodySteps.at(-1)<=.05,'body/idle movement keeps its safe integration step');
    assert.equal(a.dialogueFrame.phase,'returned','the elapsed wait has already left its first glance');
    assert.equal(a.dialogueFrame.glance,null);
    close(a.dialogueGaze.age,now/1000);
    close(a.idle.customAnchor.x,0);close(a.idle.customAnchor.y,0);
    assert.ok(a.dialogueFrame.phaseAge>1,'absolute phase age includes the RAF gap');
    for(let frame=0;frame<24;frame++){
      tick(1000/60);
      assert.equal(a.dialogueFrame.phase,'returned','normal RAF cannot replay the missed glance');
      assert.equal(a.dialogueFrame.glance,null);
    }

    tick(60000);
    assert.equal(a.dialogueFrame.phase,'attending','a longer host gap exhausts the finite waiting budget');
    for(let frame=0;frame<12;frame++){
      tick(1000/60);
      assert.equal(a.dialogueFrame.phase,'attending');
      assert.equal(a.dialogueFrame.glance,null);
    }
    assert.ok(bodySteps.every(dt=>Number.isFinite(dt)&&dt>=0&&dt<=.05));
    assert.ok(a.clock<2,'animation time is capped independently from dialogue elapsed time');
  } finally {
    pending.clear();a._raf=0;
    for(const [key,descriptor]of Object.entries(saved)){
      if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];
    }
  }
});

test('automatic intent follows the actual matching target and manual selection wins over emotion',()=>{
  const emotions=Object.fromEntries(['调侃','得意','认真','收敛','轻微不服','变小气'].map(name=>[name,{shift:{}}]));
  const a=fixture({emotions});
  a.onMessage({type:'recording',state:true});
  const expected={base:'neutral',调侃:'teasing',得意:'neutral',认真:'serious',收敛:'neutral',轻微不服:'neutral',变小气:'neutral'};
  assert.equal(a.emotion.matchingMode,true);
  for(const [emotion,intent]of Object.entries(expected)){
    a.emotion.setMatchingTarget(emotion);advance(a,.05);
    assert.equal(a.emotion.current,'base','matching mode need not change its old current label');
    assert.equal(a.dialogueFrame.intent,intent);
    assert.equal(a.dialogueExpressionFrame.intent,intent);
  }
  a.emotion.setMatchingTarget('调侃');
  assert.equal(a.setDialogueIntent('hesitant'),true);advance(a,.05);
  assert.equal(a.dialogueFrame.intent,'hesitant');
  assert.equal(a.dialogueExpressionFrame.intent,'hesitant');
  a.setDialogueIntent('auto');advance(a,.05);
  assert.equal(a.dialogueFrame.intent,'teasing');
  a.emotion.setMatchingMode(false);advance(a,.05);
  assert.equal(a.dialogueFrame.intent,'neutral','ordinary mode follows current rather than retired matchingTarget');
});

test('invalid public intent values cannot mutate a manual mode, active plan, or queued speech',()=>{
  const a=fixture();
  a.setDialogueIntent('hesitant');a.onMessage({type:'thinking',turn_id:'public-mode'});advance(a,.7);
  a.speak('后续音频',a.audioCtx,2,1);
  const snapshot=a.dialogueGaze.sample(), clips=structuredClone(a.dialogueGaze._clips);
  for(const mode of [null,undefined,3,{},['serious'],new String('serious'),'unknown','__proto__','constructor']){
    assert.equal(a.setDialogueIntent(mode),false);
    assert.equal(a.dialogueIntentMode,'hesitant');
    assert.deepEqual(a.dialogueGaze.sample(),snapshot);
    assert.deepEqual(a.dialogueGaze._clips,clips);
  }
});

test('changing live intent preserves waiting timing and audio while updating permitted gaze amplitude',()=>{
  const a=fixture({emotions:{调侃:{shift:{}}}});
  a.setDialogueIntent('hesitant');a.onMessage({type:'thinking',turn_id:'frozen-timing'});advance(a,.2);
  const plan=a.dialogueFrame.plan, start=plan.glances[0].start*1.6;
  a.setDialogueIntent('neutral');advance(a,.1);
  assert.equal(a.dialogueFrame.phase,'waiting','manual change cannot advance the frozen hesitant onset');
  advance(a,start+.1-a.dialogueGaze.age);
  assert.equal(a.dialogueFrame.phase,'averted');
  const glance=a.dialogueFrame.glance, elapsed=a.dialogueGaze.age;
  a.speak('排队回答',a.audioCtx,3,2);
  const clips=structuredClone(a.dialogueGaze._clips);
  a.setEmotion('调侃');a.setDialogueIntent('auto');a._frame(0);
  assert.equal(a.dialogueFrame.intent,'teasing');
  assert.equal(a.dialogueFrame.plan.intentAtStart,'hesitant');
  assert.deepEqual(a.dialogueFrame.plan.glances,plan.glances);
  close(a.dialogueFrame.glance.start,glance.start);close(a.dialogueFrame.glance.end,glance.end);
  close(a.dialogueFrame.glance.x,glance.x*.85);close(a.dialogueGaze.age,elapsed);
  assert.deepEqual(a.dialogueGaze._clips,clips);
  a.audioCtx.currentTime=3.5;advance(a,.6);
  assert.equal(a.dialogueFrame.state,'speaking');
  assert.equal(a.dialogueExpressionFrame.intent,'teasing');
  assert.ok(a.dialogueExpressionFrame.values.ParamBrowRY>0);
});

test('configured teasing brow and smile ownership protects existing expression while the free right brow animates',()=>{
  const a=fixture({profile:'seed-lids',emotions:{调侃:{blend:.1,shift:{ParamBrowLY:.42,ParamEyeLSmile:0}}}});
  a.setEmotion('调侃');a.speak('调侃回应',a.audioCtx,0,3);a.audioCtx.currentTime=1;advance(a,.8);
  assert.equal(a.dialogueFrame.intent,'teasing');
  assert.equal(a.dialogueExpressionFrame.appliedValues.ParamBrowLY,undefined);
  assert.ok(a.dialogueExpressionFrame.appliedValues.ParamBrowRY>.03);
  assert.ok(a.svg.params.ParamBrowLY>.15,'configured emotion retains its stronger left brow');
  assert.equal(a.dialogueExpressionFrame.appliedValues.ParamEyeLSmile,undefined);
  assert.equal(a.dialogueExpressionFrame.appliedValues.ParamEyeLLower,undefined);
  assert.equal(a.dialogueExpressionFrame.appliedValues.ParamEyeRSmile,undefined,'seed-lids emotion owns its generated right eye intent');
  close(a.svg.params.ParamEyeLSmile,0);
  assert.ok(a.dialogueExpressionFrame.values.ParamEyeRSmile>a.dialogueExpressionFrame.values.ParamEyeLSmile);
});

test('serious and hesitant replies never add smile and preserve mouth playback and full blink',()=>{
  for(const intent of ['serious','hesitant']){
    const a=fixture({profile:'seed-lids'});a.setDialogueIntent(intent);
    a.speak('认真回答',a.audioCtx,0,3);a.audioCtx.currentTime=1;advance(a,.8);
    assert.equal(a.dialogueFrame.state,'speaking');
    assert.equal(a.dialogueExpressionFrame.intent,intent);
    assert.equal(a.dialogueExpressionFrame.values.ParamEyeLSmile,0);
    assert.equal(a.dialogueExpressionFrame.values.ParamEyeRLower,0);
    assert.ok(a.svg.params.ParamMouthOpenY>0);
    a.blinkEyes();advance(a,.1);
    close(a.svg.params.ParamEyeLOpen,0);close(a.svg.params.ParamEyeROpen,0);
  }
});

test('calibration exit restores manual intent, frozen plan, expression and live queued audio clock',()=>{
  const a=fixture({profile:'seed-lids'});a.setDialogueIntent('hesitant');
  a.onMessage({type:'thinking',turn_id:'calibration-intent'});advance(a,.95);
  a.speak('恢复之后',a.audioCtx,3,2);
  const before={gaze:a.dialogueGaze.sample(),expression:a.dialogueExpression.sample(),
    frame:a.dialogueFrame,expressionFrame:a.dialogueExpressionFrame,context:structuredClone(a.idle.gazeContext)};
  const clock=a.audioCtx, session=new AvatarCalibration(a);
  session.enter();
  a.setDialogueIntent('teasing');a.dialogueGaze.stop();a.dialogueExpression.stop({immediate:true});
  a.dialogueFrame={intent:'teasing'};a.dialogueExpressionFrame={intent:'teasing'};
  a.idle.setGazeContext({drift:0});
  assert.equal(session.exit(),true);
  assert.equal(a.dialogueIntentMode,'hesitant');
  assert.deepEqual(a.dialogueGaze.sample(),before.gaze);
  assert.deepEqual(a.dialogueExpression.sample(),before.expression);
  assert.equal(a.dialogueFrame,before.frame);
  assert.equal(a.dialogueExpressionFrame,before.expressionFrame);
  assert.deepEqual(a.idle.gazeContext,before.context);
  assert.equal(a.dialogueGaze._audioClock,clock,'restoration must retain the actual media clock handle');
  clock.currentTime=3.5;advance(a,.1);
  assert.equal(a.dialogueFrame.state,'speaking');
  close(a.dialogueFrame.phaseAge,.5);
  assert.equal(a.dialogueExpressionFrame.intent,'hesitant');
});

test('interruption cancels queued audio and delayed intent cues even after changing presentation',()=>{
  const a=fixture({profile:'seed-lids'});a.setDialogueIntent('teasing');
  a.onMessage({type:'thinking',turn_id:'cancel-intent'});advance(a,.1);
  a.speak('不会播放',a.audioCtx,2,2);a.onBargeIn();
  a.setDialogueIntent('hesitant');a.audioCtx.currentTime=2.8;advance(a,2);
  assert.equal(a.dialogueFrame.state,'yielding');
  assert.equal(a.dialogueFrame.plan,null);
  assert.equal(a.dialogueGaze._clips.length,0);
  assert.equal(a.lip.speaking,false);
  assert.equal(a.dialogueExpressionFrame.active,false);
  assert.ok(Object.values(a.dialogueExpressionFrame.values).every(value=>value===0));
});
