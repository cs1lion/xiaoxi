import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  TRAIL_LIMIT,
  addTrail,
  trailStrengthAt,
  type TrailMap,
} from '../src/world/trails.ts';

const empty = (): TrailMap => new Map();

// ------------------------------------------------------------------ 累积

test('第一次走过：记 1 次', () => {
  const trails = empty();
  addTrail(trails, { x: 5, y: 5 });
  assert.equal(trailStrengthAt(trails, { x: 5, y: 5 }), 1);
});

test('再走一次：累加，不是覆盖', () => {
  const trails = empty();
  addTrail(trails, { x: 5, y: 5 });
  addTrail(trails, { x: 5, y: 5 });
  assert.equal(trailStrengthAt(trails, { x: 5, y: 5 }), 2);
});

test('没走过的地方强度为 0', () => {
  const trails = empty();
  assert.equal(trailStrengthAt(trails, { x: 9, y: 9 }), 0);
});

test('不同格子互不影响', () => {
  const trails = empty();
  addTrail(trails, { x: 1, y: 1 });
  addTrail(trails, { x: 1, y: 1 });
  addTrail(trails, { x: 2, y: 1 });
  assert.equal(trailStrengthAt(trails, { x: 1, y: 1 }), 2);
  assert.equal(trailStrengthAt(trails, { x: 2, y: 1 }), 1);
});

// ------------------------------------------------------------------ 上限

test('痕迹有上限：走再多次也不会无限涨', () => {
  const trails = empty();
  for (let i = 0; i < 100; i += 1) addTrail(trails, { x: 3, y: 3 });
  assert.ok(trailStrengthAt(trails, { x: 3, y: 3 }) <= 3, '强度封顶 3');
});

test('痕迹格子数有上限：超出丢最弱/最旧，不会拖垮存档', () => {
  const trails = empty();
  for (let i = 0; i < TRAIL_LIMIT + 50; i += 1) {
    addTrail(trails, { x: i % 90, y: Math.floor(i / 90) });
  }
  assert.ok(trails.size <= TRAIL_LIMIT, `痕迹格数 ${trails.size} 超过上限 ${TRAIL_LIMIT}`);
});

// ------------------------------------------------------------------ 只记"走过"

test('强度 1 的痕迹不画：走一次不算路', () => {
  const trails = empty();
  addTrail(trails, { x: 7, y: 7 });
  // 强度 1 太轻，画出来会让整张图布满噪点。
  assert.equal(trailStrengthAt(trails, { x: 7, y: 7 }), 1);
});

test('强度 2 及以上才值得画', () => {
  const trails = empty();
  addTrail(trails, { x: 7, y: 7 });
  addTrail(trails, { x: 7, y: 7 });
  assert.ok(trailStrengthAt(trails, { x: 7, y: 7 }) >= 2);
});
