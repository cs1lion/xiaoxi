import assert from 'node:assert/strict';
import test from 'node:test';
import { DIALOGUE_INTENTS, DIALOGUE_INTENT_PROFILES, resolveDialogueIntent } from './drivers/dialogue-intent.js';

test('auto follows the two supported presentation labels and never guesses hesitation', () => {
  const expected = { base: 'neutral', 调侃: 'teasing', 得意: 'neutral', 认真: 'serious',
    收敛: 'neutral', 轻微不服: 'neutral', 变小气: 'neutral' };
  for (const [emotion, intent] of Object.entries(expected)) assert.equal(resolveDialogueIntent({ emotion }), intent);
  for (const emotion of ['犹豫', '好像不太确定', '无语', '生气', 'unknown', '', null, {}, ['认真']]) {
    assert.equal(resolveDialogueIntent({ emotion }), 'neutral');
  }
  assert.equal(resolveDialogueIntent({ emotion: '认真', text: '我犹豫了' }), 'serious');
});

test('manual presentation intent takes precedence over emotion without inferring from text', () => {
  for (const { id } of DIALOGUE_INTENTS) {
    for (const emotion of ['认真', '调侃', '收敛', 'unknown']) {
      assert.equal(resolveDialogueIntent({ intent: id, emotion }), id);
    }
  }
  assert.equal(resolveDialogueIntent({ intent: 'auto', emotion: '调侃' }), 'teasing');
  assert.equal(resolveDialogueIntent({ intent: 'hesitant', emotion: 'base' }), 'hesitant');
});

test('invalid manual values safely resolve neutral and object prototype keys are not profiles', () => {
  for (const intent of ['constructor', '__proto__', 'toString', '未知', '', null, {}, [], 1, NaN]) {
    assert.equal(resolveDialogueIntent({ intent, emotion: '认真' }), 'neutral');
  }
  for (const options of [null, undefined, false, 1, '认真']) assert.equal(resolveDialogueIntent(options), 'neutral');
});

test('shared profiles are immutable, bounded and cap rather than expand glance budgets', () => {
  assert.ok(Object.isFrozen(DIALOGUE_INTENTS));
  assert.ok(Object.isFrozen(DIALOGUE_INTENT_PROFILES));
  assert.deepEqual(DIALOGUE_INTENTS.map(item => item.id), ['neutral', 'serious', 'hesitant', 'teasing']);
  assert.deepEqual(DIALOGUE_INTENTS.map(item => item.label), ['自然', '认真', '犹豫', '调侃']);
  for (const { id } of DIALOGUE_INTENTS) {
    const profile = DIALOGUE_INTENT_PROFILES[id];
    assert.ok(Object.isFrozen(profile));
    assert.ok(Object.values(profile).every(Number.isFinite));
    assert.ok(profile.gazeScale > 0 && profile.gazeScale <= 1);
    assert.ok(profile.driftScale > 0 && profile.driftScale <= 1);
    assert.ok(profile.delayScale >= 1 && profile.delayScale <= 2);
    assert.ok(profile.durationScale > 0 && profile.durationScale <= 1.5);
    assert.ok(Number.isInteger(profile.budgetMax) && profile.budgetMax >= 1 && profile.budgetMax <= 4);
    assert.throws(() => { profile.budgetMax = 42; }, TypeError);
  }
  assert.equal(DIALOGUE_INTENT_PROFILES.serious.budgetMax, 1);
  assert.ok(DIALOGUE_INTENT_PROFILES.hesitant.delayScale > DIALOGUE_INTENT_PROFILES.neutral.delayScale);
});
