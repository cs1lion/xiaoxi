import { test } from 'node:test';
import assert from 'node:assert/strict';
import { forestNoise, shouldGrowTree } from '../src/world/wilderness.ts';
import { BIOME_RADII } from '../src/world/biomes.ts';

const CENTER = { x: 40, y: 30 };
const NO_ZONES: readonly { x: number; y: number; w: number; h: number }[] = [];

// ------------------------------------------------------------------ 确定性

test('噪声是确定性的：同一坐标永远同一个值', () => {
  for (const [x, y] of [[0, 0], [13, 27], [79, 59]] as const) {
    assert.equal(forestNoise(x, y), forestNoise(x, y));
  }
});

test('噪声落在 0..1，不会越界', () => {
  for (let x = 0; x < 80; x += 7) {
    for (let y = 0; y < 60; y += 5) {
      const value = forestNoise(x, y);
      assert.ok(value >= 0 && value <= 1, `(${x},${y}) = ${value}`);
    }
  }
});

test('噪声是连续的：相邻格不会从 0 跳到 1', () => {
  let maxJump = 0;
  for (let x = 0; x < 79; x += 1) {
    maxJump = Math.max(maxJump, Math.abs(forestNoise(x, 30) - forestNoise(x + 1, 30)));
  }
  assert.ok(maxJump < 0.35, `相邻格最大跳变 ${maxJump.toFixed(3)} 过大，会读成噪点`);
});

// ------------------------------------------------------------------ 不长树的地方

test('镇中心不长树：那是人待的地方', () => {
  for (let x = CENTER.x - 5; x <= CENTER.x + 5; x += 1) {
    assert.equal(shouldGrowTree({ x, y: CENTER.y }, CENTER, NO_ZONES), false);
  }
});

test('工坊环也不长树', () => {
  const point = { x: CENTER.x + BIOME_RADII.townCore + 3, y: CENTER.y };
  assert.equal(shouldGrowTree(point, CENTER, NO_ZONES), false);
});

test('可建区与它的一圈都不长树，否则造物主无处落位', () => {
  const zone = { x: 10, y: 10, w: 6, h: 6 };
  for (let x = zone.x - 1; x < zone.x + zone.w + 1; x += 1) {
    for (let y = zone.y - 1; y < zone.y + zone.h + 1; y += 1) {
      assert.equal(shouldGrowTree({ x, y }, CENTER, [zone]), false, `(${x},${y}) 不该长树`);
    }
  }
});

// ------------------------------------------------------------------ 郊野有树

test('郊野与边界会长出成片的树，不是零星点缀', () => {
  let trees = 0;
  let total = 0;
  for (let x = 0; x < 80; x += 1) {
    for (let y = 0; y < 60; y += 1) {
      const point = { x, y };
      // 只看郊野以外（离中心足够远）
      if (Math.hypot(x - CENTER.x, y - CENTER.y) < BIOME_RADII.craftRing + 2) continue;
      total += 1;
      if (shouldGrowTree(point, CENTER, NO_ZONES)) trees += 1;
    }
  }
  const share = trees / total;
  assert.ok(share > 0.2, `郊野树太稀疏：${(share * 100).toFixed(1)}%`);
  assert.ok(share < 0.7, `郊野树太密会挡路：${(share * 100).toFixed(1)}%`);
});

test('越往外越荒：同一片面积里，边界圈的树比郊野圈多', () => {
  /** 某个距离带内长树的占比。按面积归一，避免内外圈面积不同导致误判。 */
  const treeShareIn = (from: number, to: number): number => {
    let trees = 0;
    let cells = 0;
    for (let x = 0; x < 80; x += 1) {
      for (let y = 0; y < 60; y += 1) {
        const d = Math.hypot(x - CENTER.x, y - CENTER.y);
        if (d < from || d >= to) continue;
        cells += 1;
        if (shouldGrowTree({ x, y }, CENTER, NO_ZONES)) trees += 1;
      }
    }
    return cells === 0 ? 0 : trees / cells;
  };

  const near = treeShareIn(BIOME_RADII.craftRing + 2, BIOME_RADII.wilderness);
  const far = treeShareIn(BIOME_RADII.wilderness, 60);
  assert.ok(near > 0, '郊野圈必须有树');
  assert.ok(far > near, `边界圈应更荒：郊野 ${(near * 100).toFixed(1)}% vs 边界 ${(far * 100).toFixed(1)}%`);
});
