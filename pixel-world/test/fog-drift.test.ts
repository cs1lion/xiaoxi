import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DRIFT_PUFFS, driftPuffs } from '../src/world/fog-drift.ts';

const CENTER = { x: 40, y: 30 };
const RADIUS = 16;

// ------------------------------------------------------------------ 确定性

test('同一时刻永远得到同一组雾团', () => {
  const a = driftPuffs(CENTER, RADIUS, 12345);
  const b = driftPuffs(CENTER, RADIUS, 12345);
  assert.deepEqual(a, b, '雾团位置必须可复现，否则截图对不上');
});

test('雾团数量固定，不随时间变化', () => {
  for (const t of [0, 5000, 60000]) {
    assert.equal(driftPuffs(CENTER, RADIUS, t).length, DRIFT_PUFFS);
  }
});

// ------------------------------------------------------------------ 真的在动

test('过一段时间后位置变了：雾确实在漂', () => {
  const a = driftPuffs(CENTER, RADIUS, 0);
  const b = driftPuffs(CENTER, RADIUS, 5000);
  const moved = a.some(
    (puff, i) =>
      Math.hypot(puff.at.x - b[i]!.at.x, puff.at.y - b[i]!.at.y) > 0.3,
  );
  assert.ok(moved, '5 秒后应有明显位移');
});

test('移动是缓慢的：1 秒内位移不超过一格', () => {
  const a = driftPuffs(CENTER, RADIUS, 0);
  const b = driftPuffs(CENTER, RADIUS, 1000);
  for (let i = 0; i < a.length; i += 1) {
    const d = Math.hypot(a[i]!.at.x - b[i]!.at.x, a[i]!.at.y - b[i]!.at.y);
    assert.ok(d <= 1, `第 ${i} 团 1 秒位移 ${d.toFixed(2)} 格，太快会抢注意力`);
  }
});

test('浓度在呼吸：不是恒定的', () => {
  const a = driftPuffs(CENTER, RADIUS, 0);
  const b = driftPuffs(CENTER, RADIUS, 3000);
  assert.ok(
    a.some((puff, i) => Math.abs(puff.alpha - b[i]!.alpha) > 0.05),
    '浓度应随时间变化',
  );
});

test('浓度落在 0..1，不会变成负的或全实心', () => {
  for (const t of [0, 2000, 7000, 20000]) {
    for (const puff of driftPuffs(CENTER, RADIUS, t)) {
      assert.ok(puff.alpha > 0 && puff.alpha < 1, `alpha ${puff.alpha}`);
    }
  }
});

// ------------------------------------------------------------------ 只贴着边界

test('雾团贴着揭示边界，不跑到世界中心', () => {
  for (const puff of driftPuffs(CENTER, RADIUS, 1000)) {
    const distance = Math.hypot(puff.at.x - CENTER.x, puff.at.y - CENTER.y);
    assert.ok(
      distance > RADIUS - 8,
      `雾团离中心 ${distance.toFixed(1)} 格，跑进镇子里了`,
    );
  }
});

test('雾团不会飘到世界外面太远', () => {
  for (const puff of driftPuffs(CENTER, RADIUS, 1000)) {
    const distance = Math.hypot(puff.at.x - CENTER.x, puff.at.y - CENTER.y);
    assert.ok(distance < RADIUS + 12, `雾团离中心 ${distance.toFixed(1)} 格，太远`);
  }
});

test('半径变化时雾团跟着边界走', () => {
  const near = driftPuffs(CENTER, 10, 1000);
  const far = driftPuffs(CENTER, 30, 1000);
  const nearAvg =
    near.reduce((s, p) => s + Math.hypot(p.at.x - CENTER.x, p.at.y - CENTER.y), 0) /
    near.length;
  const farAvg =
    far.reduce((s, p) => s + Math.hypot(p.at.x - CENTER.x, p.at.y - CENTER.y), 0) /
    far.length;
  assert.ok(farAvg > nearAvg + 10, '揭示半径变大时雾团应外移');
});
