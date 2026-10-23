import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WorldGrid } from '../src/world/grid.ts';
import { WALK_COST, walkCostOf } from '../src/world/terrain-cost.ts';
import type { TerrainId } from '../src/world/types.ts';

/** 一整块可走的合成网格，按回调改地形。 */
function gridWith(width: number, height: number, paint: (x: number, y: number) => TerrainId): WorldGrid {
  const grid = new WorldGrid({
    width,
    height,
    border: 1,
    zones: [{ id: 'industry', x: 0, y: 0, w: width, h: height }],
  });
  // 直接改格子的地形：测试要精确控制，不走分区推导。
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined) continue;
      cell.terrain = paint(x, y);
      cell.walkable = true;
    }
  }
  return grid;
}

// ------------------------------------------------------------------ 代价表

test('通行代价有明确的高低序，且都为正', () => {
  assert.ok(WALK_COST.plaza < WALK_COST.stone);
  assert.ok(WALK_COST.stone < WALK_COST.grass);
  assert.ok(WALK_COST.grass < WALK_COST.farm);
  assert.ok(WALK_COST.farm < WALK_COST.tree);
  for (const value of Object.values(WALK_COST)) assert.ok(value > 0);
});

test('未知地形按草地算，不会变成免费通行', () => {
  assert.equal(walkCostOf('grass'), WALK_COST.grass);
  assert.equal(walkCostOf('nonsense' as TerrainId), WALK_COST.grass);
});

// ------------------------------------------------------------------ 涌现的路线

test('有石板路时，路线会先拐上大路而不是直穿草地', () => {
  // 一条横向的石板大道在中段，起点终点都在草地上。
  const grid = gridWith(21, 11, (_x, y) => (y === 5 ? 'stone' : 'grass'));
  const path = grid.path({ x: 2, y: 2 }, { x: 18, y: 2 });

  assert.ok(path.length > 0);
  const onRoad = path.filter((p) => p.y === 5).length;
  assert.ok(
    onRoad > path.length * 0.4,
    `路线应当大部分走在大路上，实际 ${onRoad}/${path.length}`,
  );
});

test('没有大路时退化为近似直线，不会绕远', () => {
  const grid = gridWith(21, 11, () => 'grass');
  const path = grid.path({ x: 2, y: 2 }, { x: 18, y: 2 });
  assert.ok(path.length > 0);
  // 全草地时最优解就是曼哈顿距离。
  assert.equal(path.length, 16, `应为 16 步，实际 ${path.length}`);
});

test('密林够宽时直穿更划算：A* 算的是总代价，不是「一律避开」', () => {
  // 密林横跨 y 2..8（7 行）。从 y=5 绕到 y=1 要走 4 步出去 + 4 步回来，
  // 多花 8 格草地；而直穿只有 5 格密林。算下来直穿 17.5 < 绕行 18，直穿才对。
  // 这条用例防的是「为了避开密林而无条件绕远」——那同样是错的行为。
  const grid = gridWith(21, 11, (x, y) => (x >= 8 && x <= 12 && y >= 2 && y <= 8 ? 'tree' : 'grass'));
  const path = grid.path({ x: 5, y: 5 }, { x: 15, y: 5 });
  assert.ok(path.length > 0);
  const total = path.reduce((sum, p) => {
    const cell = grid.at(p);
    return sum + (cell === undefined ? 1 : walkCostOf(cell.terrain));
  }, 0);
  assert.ok(total <= 17.5 + 1e-9, `总代价 ${total.toFixed(2)} 不应高于直穿的 17.5`);
});

test('路线代价低于直线穿越：用总代价验证，而不是只看步数', () => {
  const grid = gridWith(21, 11, (_x, y) => (y === 5 ? 'stone' : 'grass'));
  const path = grid.path({ x: 2, y: 2 }, { x: 18, y: 2 });
  const total = path.reduce((sum, p) => {
    const cell = grid.at(p);
    return sum + (cell === undefined ? 1 : walkCostOf(cell.terrain));
  }, 0);

  // 直穿草地：16 步 × 1.0 = 16。绕上石板路应当更便宜。
  assert.ok(total < 16, `路线总代价 ${total.toFixed(2)} 应低于直穿草地的 16`);
});

test('寻路是确定性的：同起点终点重复调用结果一致', () => {
  const grid = gridWith(21, 11, (_x, y) => (y === 5 ? 'stone' : 'grass'));
  const first = JSON.stringify(grid.path({ x: 2, y: 2 }, { x: 18, y: 2 }));
  for (let i = 0; i < 5; i += 1) {
    assert.equal(JSON.stringify(grid.path({ x: 2, y: 2 }, { x: 18, y: 2 })), first);
  }
});
