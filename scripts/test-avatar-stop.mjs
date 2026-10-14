import assert from 'node:assert/strict';
import {Avatar} from '../web/avatar/index.js';
const early=new Avatar();early._pending.push({text:'stale'});
assert.doesNotThrow(()=>early.stopSpeaking());assert.equal(early._pending.length,0);
assert.doesNotThrow(()=>early.onBargeIn());
const calls=[];const ready=new Avatar();ready.performance={stop:()=>calls.push('performance')};ready.lip={stop:()=>calls.push('lip')};ready.gesture={stopAll:()=>calls.push('gesture')};ready.emotion={set:value=>calls.push(value)};
ready._pending.push({text:'old'});ready.onBargeIn();assert.deepEqual(calls,['performance','lip','gesture','base']);assert.equal(ready._pending.length,0);
console.log('PASS: stop/interruption safe before init; deferred speech cleared; initialized mouth/action/emotion stop together');

const phrase='我觉得，这个办法可以试试。';
const semantic={performance:{version:1,source:'llm',cues:[
 {intent:'self_reference',gesture:'self',anchor:'我觉得',start_char:0,end_char:3,strength:.4},
 {intent:'explanation',gesture:'palm',anchor:'这个办法',start_char:4,end_char:8,strength:.4},
]}};
const clock={currentTime:0};
const delayed=new Avatar();
assert.doesNotThrow(()=>delayed.speak(phrase,clock,1,6,semantic),'speech before configuration keeps metadata');
assert.equal(delayed._pending[0].options,semantic);
delayed.configure({});
assert.equal(delayed._pending.length,0);
assert.equal(delayed.performance.clock,clock);
assert.deepEqual(delayed.performance.queue.map(cue=>cue.anchor),['我觉得','这个办法']);
assert.equal(delayed.performance.sample().name,null,'pending replay cannot start before audio');
let textMatches=0,plays=0;
delayed.gesture.matchText=()=>{textMatches++;return ['nod'];};
delayed.gesture.play=()=>plays++;
delayed.onMessage({type:'text_delta',delta:'[gesture:nod]你好，我觉得这个办法不错。'});
delayed.onMessage({type:'done',text:phrase,ttsDuration:6});
assert.equal(textMatches,0,'generation events do not schedule speech text gestures');
assert.equal(plays,0,'legacy embedded tags cannot run before audible speech');
delayed.speak(phrase,clock,7,6,semantic);
assert.equal(textMatches,0,'semantic plan owns gestures instead of text fallback');
delayed.speak('你好',clock,14,3);
assert.equal(textMatches,1,'older servers keep text gestures at actual speak boundary');
assert.equal(plays,1);
delayed.onBargeIn();
assert.equal(delayed.performance.queue.length,0,'interrupt clears all planned phrases');
const cancelled=new Avatar();
cancelled.speak(phrase,clock,1,6,semantic);cancelled.onBargeIn();cancelled.configure({});
assert.equal(cancelled.performance.queue.length,0,'stop before configuration discards deferred metadata too');
for (const event of [{type:'interrupted'}, {type:'playback_stopped'},
  {type:'recording',state:true}, {type:'thinking'}]) {
  const loading=new Avatar();
  loading.speak(phrase,clock,1,6,semantic);
  loading.onMessage(event);
  assert.equal(loading._pending.length,0,`${event.type} cancels speech while configuration loads`);
  loading.configure({});
  assert.equal(loading.performance.queue.length,0,`${event.type} cannot revive canceled semantic cues`);
  assert.equal(loading.lip.speaking,false,`${event.type} cannot revive canceled mouth animation`);
}
console.log('PASS: deferred semantic plans, playback-only speech gestures and queued cancellation');
