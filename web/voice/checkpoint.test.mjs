import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const load = async name => import(`data:text/javascript;base64,${Buffer.from(await readFile(new URL(name,import.meta.url))).toString('base64')}`);
const {PlaybackCheckpoint,VoiceMetrics}=await load('./checkpoint.js');
const {EnergyVad}=await load('./vad.js');
test('checkpoint replays only remaining current audio once and expires locally',()=>{
 let now=0; const c=new PlaybackCheckpoint({clock:()=>now});const buffer={duration:4};
 assert.ok(c.save({buffer,offset:1.5,text:'句子'}));assert.equal(c.take().offset,1.5);assert.equal(c.take(),null);
 assert.equal(c.save({buffer,offset:3.95}),false);
 c.save({buffer,offset:1});now=90000;assert.equal(c.available,false);assert.equal(c.take(),null);
 c.save({buffer,offset:-1});assert.equal(c.take().offset,0);
});
test('metrics retain bounded scalar samples, no audio or transcripts',()=>{
 const m=new VoiceMetrics(3);m.count('barge');for(const x of [10,20,30,40])m.timing('latency',x);
 m.timing('latency',NaN);m.timing('latency',-1);
 assert.deepEqual(m.snapshot(),{counts:{barge:1},timings:{latency:{n:3,p50:30,p95:40}}});
 m.reset();assert.deepEqual(m.snapshot(),{counts:{},timings:{}});
});
test('VAD adopts duplex pre-roll once, then endpoints normally',()=>{
 const v=new EnergyVad(); const frames=Array.from({length:34},()=>new Float32Array(320).fill(.1));
 v.adopt(frames,420);assert.equal(v.elapsed,680);assert.equal(v.speech,420);
 for(let i=0;i<44;i++)assert.equal(v.push(new Float32Array(320)).end,false);
 const end=v.push(new Float32Array(320));assert.equal(end.end,true);assert.equal(end.valid,true);
});
