import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  FOG_INITIAL_RADIUS,
  fogAt,
  revealedRadiusFor,
  type FogState,
} from '../src/world/fog.ts';

const CENTER = { x: 40, y: 30 };
const state = (radius: number): FogState => ({ center: CENTER, revealedRadius: radius });

// ------------------------------------------------------------------ 判定

test('初始只点亮镇中心一圈，外围是迷雾', () => {
  assert.ok(FOG_INITIAL_RADIUS >= 10, '至少盖住整个核心镇');
  const s = state(FOG_INITIAL_RADIUS);
  assert.equal(fogAt(CENTER, s), false, '中心不该有雾');
  assert.equal(fogAt({ x: CENTER.x + FOG_INITIAL_RADIUS - 1, y: CENTER.y }, s), false);
  assert.equal(fogAt({ x: CENTER.x + FOG_INITIAL_RADIUS + 1, y: CENTER.y }, s), true);
});

test('迷雾判定是纯函数：同输入同输出', () => {
  const s = state(20);
  const p = { x: 55, y: 30 };
  assert.equal(fogAt(p, s), fogAt(p, s));
});

test('半径扩大后，原本有雾的地方被点亮', () => {
  const p = { x: CENTER.x + 25, y: CENTER.y };
  assert.equal(fogAt(p, state(20)), true);
  assert.equal(fogAt(p, state(30)), false);
});

test('揭示是单向的：交付数再少也不会小于初始半径', () => {
  // 参数是**交付单数**，不是半径。
  assert.equal(revealedRadiusFor(0), FOG_INITIAL_RADIUS);
  assert.equal(revealedRadiusFor(1), FOG_INITIAL_RADIUS);
  assert.equal(revealedRadiusFor(Number.NaN), FOG_INITIAL_RADIUS, '坏数字退回初始值');
  assert.ok(revealedRadiusFor(30) > FOG_INITIAL_RADIUS, '交付多了会变大');
});

// ------------------------------------------------------------------ 与交付挂钩

test('交付越多，揭示越大，但有上限', () => {
  const r1 = revealedRadiusFor(0);
  const r2 = revealedRadiusFor(10);
  const r3 = revealedRadiusFor(1000);
  assert.ok(r2 > r1, '交付 10 单应比 0 单揭得多');
  assert.ok(r3 > r2);
  assert.ok(r3 <= 60, `半径不该超过地图半宽，实际 ${r3}`);
});

test('揭示半径增长是阶梯的，不是每单都变', () => {
  // 同一档内的交付数给出同一个半径。
  assert.equal(revealedRadiusFor(4), revealedRadiusFor(7), '4 与 7 应落在同一档');
  // 跨档才变。
  assert.ok(revealedRadiusFor(8) > revealedRadiusFor(7), '跨档应当变大');
});
