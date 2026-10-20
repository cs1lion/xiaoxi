import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TERRAIN_COLORS, WORLD } from '../src/world/palette-data.ts';
import {
  BRIGHT,
  CORNER_BIT,
  DARK,
  SIDE_BIT,
  TERRAIN_EDGE_VARIANTS,
  TERRAIN_HEIGHT,
  cornerMask,
  primitiveCount,
  sideMask,
  type EdgeNeighbors,
} from '../src/render/terrain-edges.ts';
import type { TerrainId } from '../src/world/types.ts';

/** §1.2 地形拼接边：20 变体（16 邻边 + 4 内凹角）的纯函数锁。 */

const all = (v: TerrainId | null): EdgeNeighbors => ({
  north: v, northEast: v, east: v, southEast: v, south: v, southWest: v, west: v, northWest: v,
});
const around = (
  n: TerrainId | null, ne: TerrainId | null, e: TerrainId | null, se: TerrainId | null,
  s: TerrainId | null, sw: TerrainId | null, w: TerrainId | null, nw: TerrainId | null,
): EdgeNeighbors => ({ north: n, northEast: ne, east: e, southEast: se, south: s, southWest: sw, west: w, northWest: nw });

const TERRAINS: readonly TerrainId[] = ['tree', 'grass', 'farm', 'stone', 'plaza'];

test('变体总数 = 16 邻边 + 4 内凹角 = 20', () => {
  assert.equal(TERRAIN_EDGE_VARIANTS, 20);
});

test('材质高度序 tree > grass > farm > stone > plaza', () => {
  const order: readonly TerrainId[] = ['tree', 'grass', 'farm', 'stone', 'plaza'];
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(
      TERRAIN_HEIGHT[order[i - 1]!] > TERRAIN_HEIGHT[order[i]!],
      `${order[i - 1]} 必须高于 ${order[i]}`,
    );
  }
});

test('全同材质不画边', () => {
  for (const self of TERRAINS) {
    assert.equal(sideMask(self, all(self)), 0, `${self} 四周同材质时不该有边`);
    assert.equal(cornerMask(self, all(self)), 0);
    assert.equal(primitiveCount(self, all(self)), 0);
  }
});

test('边只画在高度更高的一侧', () => {
  for (const self of TERRAINS) {
    for (const other of TERRAINS) {
      const mask = sideMask(self, all(other));
      const shouldDraw = TERRAIN_HEIGHT[other] < TERRAIN_HEIGHT[self];
      assert.equal(
        mask !== 0,
        shouldDraw,
        `${self} 邻 ${other}：${shouldDraw ? '应画' : '不应画'}`,
      );
    }
  }
});

test('越界邻格（世界边界）不画边', () => {
  for (const self of TERRAINS) assert.equal(sideMask(self, all(null)), 0);
});

test('四面都更低时四条边全亮（mask=15）', () => {
  const low = around('plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza');
  assert.equal(sideMask('tree', low), 15);
});

test('内凹角：直角边都没画、斜角更低时才补角', () => {
  // 只有西北斜角更低，两条直角邻边（N/W）同高
  const onlyDiag = around('grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'plaza');
  assert.equal(sideMask('grass', onlyDiag), 0);
  assert.equal(cornerMask('grass', onlyDiag), CORNER_BIT.northWest);

  // N 也画了线 → 交点已被那条线覆盖，不再补角
  const northLow = around('plaza', 'grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'plaza');
  assert.equal(sideMask('grass', northLow), SIDE_BIT.north);
  assert.equal(cornerMask('grass', northLow), 0);
});

test('每格最多 4 条边 + 4 个角', () => {
  const values: readonly (TerrainId | null)[] = [null, ...TERRAINS];
  for (const self of TERRAINS) {
    for (const v of values) {
      for (const diag of values) {
        // 四条边全画、四个斜角全更低但也可能补角，上界是 8
        const a = around(v, diag, v, diag, v, diag, v, diag);
        assert.ok(primitiveCount(self, a) <= 8);
      }
    }
  }
});

/**
 * 回归：tree 的底色就是 soil，暗边若也用 soil 会与底色完全相同、亮度落差 0.0，
 * 等于白画（实测 96 个 tree 格里有 48 个的边完全看不见，占总写入像素 25.6%）。
 * 修法是 tree 的暗边改用 ink。这条测试锁住「暗边不能等于本格底色」这个不变量。
 */
test('暗边不能与本格底色相同（tree 用 ink，其余用 soil）', () => {
  // 直接读源码导出的 DARK 表，不在测试里另建副本——副本会和实现脱钩，
  // 导致把实现改回 bug 时测试仍然绿（同义反复）。
  for (const self of TERRAINS) {
    assert.notEqual(
      DARK[self],
      TERRAIN_COLORS[self],
      `${self} 的暗边色与底色相同，画出来会隐形`,
    );
  }
  // tree 具体取值：必须比底色 soil 更深（亮度更低）
  assert.equal(DARK.tree, WORLD.ink, 'tree 暗边必须用 ink：它的底色就是 soil，用 soil 落差 0.0');
  const lum = (hex: number): number =>
    0.2126 * ((hex >> 16) & 255) + 0.7152 * ((hex >> 8) & 255) + 0.0722 * (hex & 255);
  assert.ok(
    lum(WORLD.ink) < lum(WORLD.soil),
    'ink 必须比 soil 更深，否则 tree 暗边虽可见但不是「更暗」',
  );
});

test('亮边不能与本格底色相同', () => {
  for (const self of TERRAINS) {
    assert.notEqual(BRIGHT[self], TERRAIN_COLORS[self], `${self} 的亮边色与底色相同`);
  }
});

/**
 * plaza 是最低材质（高度 0），邻格不可能比它更低，所以它**永远不画边**。
 * 实测：30×20 世界里 16 个 plaza 格，画了边的 0 个。
 * 这条不是「不用管」，而是说明 BRIGHT.plaza / DARK.plaza 当前是取不到的死分支——
 * 但仍要求它们与自身底色不同，否则将来改动高度序时会静默画出一条隐形边。
 */
test('plaza 是最低材质，任何邻格都不触发它的边', () => {
  for (const other of TERRAINS) {
    assert.equal(sideMask('plaza', all(other)), 0, `plaza 邻 ${other} 不该画边`);
    assert.equal(cornerMask('plaza', all(other)), 0);
  }
  // 反向确认：其他材质邻 plaza 时会画边（plaza 更低）
  for (const self of ['tree', 'grass', 'farm', 'stone'] as const) {
    assert.notEqual(sideMask(self, all('plaza')), 0, `${self} 邻 plaza 应画边`);
  }
});
