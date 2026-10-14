import assert from 'node:assert/strict';
import { buildSpeechPlan, validatePerformancePlan } from './drivers/speech-plan.js';

let count = 0;
function test(name, fn) { fn(); count++; console.log(`ok ${count} - ${name}`); }
const cue = (intent, gesture, anchor, start_char, end_char, strength = .45) =>
  ({ intent, gesture, anchor, start_char, end_char, strength });
const options = cues => ({ performance: { version: 1, source: 'llm', cues } });

test('only supported LLM envelopes suppress the legacy fallback', () => {
  for (const value of [undefined, null, {}, { version: 2, source: 'llm', cues: [] },
    { version: 1, source: 'fallback', cues: [] }, { version: 1, source: 'llm', cues: 'bad' }]) {
    assert.equal(validatePerformancePlan('你好', value), null);
  }
  assert.deepEqual(buildSpeechPlan('你好？', 2, options([])).cues, []);
});

test('anchors use codepoints and their exact occurrence', () => {
  const text = '🙂我觉得，可以，我觉得不错。';
  const plan = buildSpeechPlan(text, 5, options([cue('self_reference', 'self', '我觉得', 8, 11)]));
  assert.equal(plan.cues.length, 1);
  assert.equal(plan.cues[0].anchor, '我觉得');
  assert.ok(plan.cues[0].anchorStart > 2);
  assert.equal(plan.alignment, 'estimated');
  const wrong = buildSpeechPlan(text, 5, options([cue('self_reference', 'self', '我觉得', 9, 12)]));
  assert.equal(wrong.cues.length, 0, 'UTF-16 offsets do not accidentally match');
});

test('intent cannot select arbitrary gestures or bones', () => {
  const plan = buildSpeechPlan('你好，我觉得可以。', 5, options([
    cue('greeting', 'rightUpperArm', '你好', 0, 2),
    cue('unknown', 'wave', '你好', 0, 2),
    cue('self_reference', 'palm', '我觉得', 3, 6),
  ]));
  assert.equal(plan.cues.length, 0);
});

test('strength is finite, bounded and never copied as a rotation', () => {
  const text = '你好，我觉得不错。';
  const plan = buildSpeechPlan(text, 5, options([
    cue('greeting', 'wave', '你好', 0, 2, 99),
    cue('self_reference', 'self', '我觉得', 3, 6, -.5),
  ]));
  assert.deepEqual(plan.cues.map(c => c.strength), [.75, .15]);
  const defaultCue = cue('greeting', 'wave', '你好', 0, 2);
  delete defaultCue.strength;
  assert.equal(buildSpeechPlan('你好', 2, options([defaultCue])).cues[0].strength, .4);
  for (const strength of [NaN, Infinity, '1', null]) {
    assert.equal(buildSpeechPlan('你好', 2, options([cue('greeting', 'wave', '你好', 0, 2, strength)])).cues.length, 0);
  }
});

test('up to two ordered, nonoverlapping anchors survive', () => {
  const plan = buildSpeechPlan('你好，我觉得好，你呢？', 6, options([
    cue('question', 'palm', '你呢', 8, 10),
    cue('self_reference', 'self', '我觉得', 3, 6),
    cue('greeting', 'wave', '你好', 0, 2),
    cue('greeting', 'wave', '好', 1, 2),
  ]));
  assert.deepEqual(plan.cues.map(c => c.gesture), ['wave', 'self']);
  assert.deepEqual(plan.cues.map(c => c.start_char), [0, 3]);
});

test('complete provider timestamps map the later repeated phrase', () => {
  const text = '我觉得，可以，我觉得不错。';
  const plan = buildSpeechPlan(text, 5, { ...options([cue('self_reference', 'self', '我觉得', 7, 10)]),
    wordTimings: [
      { text: '我觉得', start: .2, end: 1 }, { text: '可以', start: 1.2, end: 1.8 },
      { text: '我觉得', start: 2.7, end: 3.4 }, { text: '不错', start: 3.5, end: 4.2 },
    ] });
  assert.equal(plan.alignment, 'provider');
  assert.equal(plan.cues[0].anchorStart, 2.7);
  assert.equal(plan.cues[0].anchorEnd, 3.4);
});

test('provider punctuation and whitespace do not shift anchor offsets', () => {
  const plan = buildSpeechPlan('你好，Neuro！', 4, { ...options([cue('greeting', 'wave', '你好', 0, 2)]),
    wordTimings: [{ text: '你好，', start: .1, end: 1 }, { text: ' Neuro！', start: 1.2, end: 3.1 }] });
  assert.equal(plan.alignment, 'provider');
  assert.equal(plan.cues[0].anchorStart, .1);
});

test('partial, mismatched or malformed provider timings fall back as a whole', () => {
  const text = '你好，我来。';
  const good = [{ text: '你好', start: .1, end: 1 }, { text: '我来', start: 1.2, end: 2.3 }];
  for (const wordTimings of [[], good.slice(0, 1), good.toReversed(),
    [{ ...good[0], text: '你坏' }, good[1]], [{ ...good[0], start: -1 }, good[1]],
    [good[0], { ...good[1], end: 5 }], [good[0], { ...good[1], start: .8 }],
    [good[0], { ...good[1], end: NaN }], [good[0], { ...good[1], end: '2.3' }]]) {
    assert.equal(buildSpeechPlan(text, 3, { ...options([cue('self_reference', 'self', '我来', 3, 5)]), wordTimings }).alignment, 'estimated');
  }
});

test('empty and invalid audio duration are handled safely', () => {
  for (const duration of [0, -1, NaN, Infinity, undefined]) {
    assert.equal(buildSpeechPlan('你好', duration, options([])), null);
  }
  assert.equal(buildSpeechPlan('', 1, options([])).cues.length, 0);
});

console.log(`${count} speech plan tests passed`);
