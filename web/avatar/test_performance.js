import assert from 'node:assert/strict';
import {PerformanceDirector} from './drivers/performance.js';
const d=new PerformanceDirector(),clock={currentTime:0};
d.setEmotion('调侃');d.speak('真的做完了吗？',clock,1,4);
assert.equal(d.sample().weight,0);
clock.currentTime=1.6;assert.equal(d.sample().name,'palm');assert.equal(d.sample().weight,1);
d.onEvent({type:'interrupted'});assert.equal(d.sample().weight,0);assert.equal(d.state,'listening');
d.speak('你好',clock,2,3);assert.equal(d.action,null,'cooldown suppresses repeated gesture');
clock.currentTime=7;d.speak('你好',clock,7,3);clock.currentTime=7.7;assert.equal(d.sample().name,'wave');
clock.currentTime=11;assert.equal(d.sample().name,null);assert.equal(d.state,'idle');
d.preview('self',clock);clock.currentTime+=.5;assert.equal(d.sample().name,'self');
d.onEvent({type:'recording',state:true});assert.equal(d.action,null);assert.equal(d.state,'listening');
d.onEvent({type:'thinking'});assert.equal(d.state,'thinking');

const planned = (cues, wordTimings) => ({ performance: { version: 1, source: 'llm', cues }, wordTimings });
const selfCue = { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 5, end_char: 8, strength: .35 };

// Audio can be decoded/scheduled well before playback. Neither tokens nor the
// arrival of metadata should animate the later phrase early.
const semantic = new PerformanceDirector(), audio = { currentTime: 0 };
semantic.speak('先等一下，我觉得可以。', audio, 2, 5, planned([selfCue], [
  { text: '先等一下', start: .1, end: 1.5 },
  { text: '我觉得', start: 2.4, end: 3.2 },
  { text: '可以', start: 3.3, end: 4.1 },
]));
assert.equal(semantic.sample().name, null);
audio.currentTime = 3; assert.equal(semantic.sample().name, null);
audio.currentTime = 4.5;
let frame = semantic.sample();
assert.equal(frame.name, 'self');assert.equal(frame.alignment, 'provider');
assert.equal(frame.strength, .35);assert.equal(frame.anchor, '我觉得');
assert.ok(frame.weight > .9);
audio.currentTime = 5.8; assert.equal(semantic.sample().name, null);

// Intentional neutral speech suppresses misleading greeting/negation keywords.
const neutral = new PerformanceDirector();
neutral.setEmotion('调侃');neutral.speak('你好不是暗号？', audio, 8, 3, planned([]));
audio.currentTime = 8.7;assert.equal(neutral.sample().weight, 0);

// Two anchors in one chunk use distinct, nonoverlapping action windows.
const multi = new PerformanceDirector();audio.currentTime = 0;
multi.speak('我觉得，可以这样试试。', audio, 1, 5, planned([
  { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
  { intent: 'explanation', gesture: 'palm', anchor: '这样试试', start_char: 6, end_char: 10, strength: .55 },
], [
  { text: '我觉得', start: .3, end: .9 }, { text: '可以', start: 1.1, end: 1.6 },
  { text: '这样试试', start: 2.8, end: 4.3 },
]));
assert.equal(multi.queue.length, 2);
assert.ok(multi.queue[0].start + multi.queue[0].duration < multi.queue[1].start);
audio.currentTime = 1.6;assert.equal(multi.sample().name, 'self');
audio.currentTime = 4.1;assert.equal(multi.sample().name, 'palm');
assert.equal(multi.sample().strength, .55);

// Forward audio fragments do not throw away a previously scheduled later cue.
const fragments = new PerformanceDirector();audio.currentTime = 0;
fragments.speak('先等一下，我觉得可以。', audio, 1, 5, planned([selfCue]));
const first = fragments.queue[0];
fragments.speak('那再见啦。', audio, 6, 3, planned([
  { intent: 'farewell', gesture: 'wave', anchor: '再见', start_char: 1, end_char: 3, strength: .3 },
]));
assert.equal(fragments.queue.length, 2);
audio.currentTime = first.start + .4;assert.equal(fragments.sample().name, 'self');
audio.currentTime = 7;assert.equal(fragments.sample().name, 'wave');

// A later legacy/fallback fragment must wait behind an earlier semantic cue.
// Booking the fallback also retains its cooldown when the older cue activates.
const mixed = new PerformanceDirector();audio.currentTime = 0;
mixed.speak('先等一下，我觉得可以。', audio, 1, 5, planned([selfCue], [
  { text: '先等一下', start: .1, end: 1.5 }, { text: '我觉得', start: 2.4, end: 3.2 },
  { text: '可以', start: 3.3, end: 4.1 },
]));
mixed.speak('你好', audio, 6, 3);
assert.equal(mixed.queue.length, 2);
audio.currentTime = 3.7;assert.equal(mixed.sample().name, 'self');
audio.currentTime = 6.7;assert.equal(mixed.sample().name, 'wave');
audio.currentTime = 9;mixed.speak('再见', audio, 9, 3);
assert.equal(mixed.action, null,'mixed fragments retain the four-second fallback cooldown');
assert.equal(mixed.queue.length, 0);

// Rapid overlapping fragments do not stack poses, and interruption clears all
// future cues. An unperformed future cue cannot impose cooldown on a new turn.
const rapid = new PerformanceDirector();audio.currentTime = 0;
rapid.speak('先等一下，我觉得可以。', audio, 1, 5, planned([selfCue]));
rapid.speak('我觉得', audio, 1.5, 2, planned([
  { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
]));
assert.equal(rapid.queue.length, 1);
rapid.onEvent({ type: 'interrupted' });assert.equal(rapid.queue.length, 0);
rapid.speak('我觉得', audio, 2, 2, planned([
  { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
]));
assert.equal(rapid.queue.length, 1);
rapid.onEvent({ type: 'recording', state: true });assert.equal(rapid.queue.length, 0);
rapid.speak('我觉得', audio, 5, 2, planned([
  { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
]));
rapid.onEvent({ type: 'thinking' });assert.equal(rapid.queue.length, 0);
audio.currentTime = 20;assert.equal(rapid.sample().name, null);

// Malformed/incomplete provider data is estimated instead of advertised precise.
const estimated = new PerformanceDirector();audio.currentTime = 0;
estimated.speak('先等一下，我觉得可以。', audio, 1, 5, planned([selfCue], [
  { text: '我觉得', start: 1, end: 2 },
]));
assert.equal(estimated.queue[0].alignment, 'estimated');
audio.currentTime = estimated.queue[0].start + .4;
assert.equal(estimated.sample().alignment, 'estimated');

// Missing/source-fallback plans and manual preview keep the approved strength.
const compatibility = new PerformanceDirector();audio.currentTime = 30;
compatibility.speak('你好', audio, 30, 3, { performance: { version: 1, source: 'fallback', cues: [] } });
audio.currentTime = 30.7;assert.equal(compatibility.sample().name, 'wave');
assert.equal(compatibility.sample().strength, 1);
compatibility.preview('deny', audio);audio.currentTime += .5;
assert.equal(compatibility.sample().strength, 1);assert.equal(compatibility.sample().source, 'preview');
assert.equal(compatibility.queue.length, 0);

// A clock jump never replays already expired actions; changing AudioContext
// cannot import cooldown or a schedule from the previous clock domain.
const expired = new PerformanceDirector(), newClock = { currentTime: 0 };audio.currentTime = 40;
expired.speak('你好', audio, 2, 2, planned([{ intent: 'greeting', gesture: 'wave', anchor: '你好', start_char: 0, end_char: 2, strength: .3 }]));
assert.equal(expired.queue.length, 0);assert.equal(expired.sample().name, null);
expired.speak('你好', newClock, 0, 3);newClock.currentTime = .7;assert.equal(expired.sample().name, 'wave');
console.log('Performance director: legacy + semantic audio scheduling assertions passed');
