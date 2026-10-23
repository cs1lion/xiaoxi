import { test } from 'node:test';
import assert from 'node:assert/strict';
import { litWindowCount } from '../src/world/bustle.ts';

const base = { litWindows: 0, ridge: 0, glow: 0, dim: 0, mist: 0, mistBand: 0 };

test('没有活的时候，窗灯只由时辰决定', () => {
  assert.equal(litWindowCount({ ...base, litWindows: 0 }, false), 0);
  assert.equal(litWindowCount({ ...base, litWindows: 2 }, false), 2);
});

test('有铺子在干活时，白天也亮一扇：镇子活着的样子', () => {
  assert.equal(litWindowCount({ ...base, litWindows: 0 }, true), 1);
});

test('夜里干活：不叠加，取最大值', () => {
  // 时辰已经亮 2 扇，干活不该变成 3 扇——窗只有两扇。
  assert.equal(litWindowCount({ ...base, litWindows: 2 }, true), 2);
});

test('结果封顶 2：只有两扇窗', () => {
  assert.ok(litWindowCount({ ...base, litWindows: 2 }, true) <= 2);
});

test('黄昏与夜（litWindows=2）时，干活与否都一样亮', () => {
  assert.equal(
    litWindowCount({ ...base, litWindows: 2 }, true),
    litWindowCount({ ...base, litWindows: 2 }, false),
  );
});
