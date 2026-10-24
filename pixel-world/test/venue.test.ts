import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickVenue, type VenueCandidate } from '../src/world/venue.ts';

const town = (kind: 'code' | 'research' | 'writing'): VenueCandidate => ({
  instanceId: `town-${kind}`,
  boundTask: kind,
  isWild: false,
  busy: false,
});

const wild = (kind: 'code' | 'research' | 'writing'): VenueCandidate => ({
  instanceId: `wild-${kind}`,
  boundTask: kind,
  isWild: true,
  busy: false,
});

// ------------------------------------------------------------------ 台账活去镇内

test('台账的活优先去镇内铺子：那是确定性的本地工作', () => {
  const picked = pickVenue([wild('code'), town('code')], 'code', 'ledger');
  assert.equal(picked?.instanceId, 'town-code');
});

test('镇内铺子忙时，台账活宁可排队也不去郊野', () => {
  const busyTown: VenueCandidate = { ...town('code'), busy: true };
  const picked = pickVenue([busyTown, wild('code')], 'code', 'ledger');
  assert.equal(picked, null, '台账活不该被派到郊野');
});

// ------------------------------------------------------------------ 神谕活去郊野

test('神谕派的长活优先去郊野：那是探索性的外部工作', () => {
  const picked = pickVenue([town('research'), wild('research')], 'research', 'oracle');
  assert.equal(picked?.instanceId, 'wild-research');
});

test('郊野铺子忙时，神谕活退回镇内', () => {
  const busyWild: VenueCandidate = { ...wild('research'), busy: true };
  const picked = pickVenue([town('research'), busyWild], 'research', 'oracle');
  assert.equal(picked?.instanceId, 'town-research');
});

test('只有镇内铺子时，神谕活照样能派', () => {
  const picked = pickVenue([town('writing')], 'writing', 'oracle');
  assert.equal(picked?.instanceId, 'town-writing');
});

test('只有郊野铺子时，台账活不去（宁可不动，也不假装在本地做）', () => {
  const picked = pickVenue([wild('code')], 'code', 'ledger');
  assert.equal(picked, null);
});

// ------------------------------------------------------------------ 边界

test('活类型对不上时返回 null，不会乱派', () => {
  assert.equal(pickVenue([town('code')], 'research', 'ledger'), null);
  assert.equal(pickVenue([], 'code', 'oracle'), null);
});

test('同类多个候选时取第一个可用的，顺序稳定', () => {
  const a = town('code');
  const b: VenueCandidate = { ...town('code'), instanceId: 'town-code-2' };
  assert.equal(pickVenue([a, b], 'code', 'ledger')?.instanceId, 'town-code');
  const aBusy: VenueCandidate = { ...a, busy: true };
  assert.equal(pickVenue([aBusy, b], 'code', 'ledger')?.instanceId, 'town-code-2');
});
