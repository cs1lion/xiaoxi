import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WorldGrid, type GridSpec } from '../src/world/grid.ts';

const SPEC: GridSpec = {
  width: 12,
  height: 10,
  border: 1,
  zones: [{ id: 'industry', x: 2, y: 2, w: 4, h: 4 }],
};

test('边界一圈不可走，内部可走', () => {
  const grid = new WorldGrid(SPEC);
  assert.equal(grid.isWalkable({ x: 0, y: 0 }), false);
  assert.equal(grid.isWalkable({ x: 6, y: 5 }), true);
  assert.equal(grid.isWalkable({ x: 12, y: 5 }), false);
});

test('落位把占地设为不可走，门口保持可走', () => {
  const grid = new WorldGrid(SPEC);
  const ok = grid.place({
    instanceId: 'forge',
    origin: { x: 2, y: 2 },
    w: 3,
    h: 3,
    door: { x: 3, y: 5 },
  });
  assert.equal(ok, true);
  assert.equal(grid.isWalkable({ x: 2, y: 2 }), false);
  assert.equal(grid.isWalkable({ x: 3, y: 5 }), true);
});

test('门口在占地内时拒绝落位，并保持账本不变', () => {
  const grid = new WorldGrid(SPEC);
  const before = grid.countOccupied();
  const ok = grid.place({
    instanceId: 'bad',
    origin: { x: 2, y: 2 },
    w: 3,
    h: 3,
    door: { x: 3, y: 3 },
  });
  assert.equal(ok, false);
  assert.equal(grid.countOccupied(), before);
});

test('留白会把相邻建筑挡在外面', () => {
  const grid = new WorldGrid(SPEC);
  grid.place({ instanceId: 'a', origin: { x: 2, y: 2 }, w: 2, h: 2, door: { x: 2, y: 4 } });
  assert.equal(grid.canPlace({ x: 4, y: 2 }, 2, 2, 1), false);
  assert.equal(grid.canPlace({ x: 2, y: 5 }, 2, 2, 0), true);
});

test('A* 绕过建筑，且不把起点算进路线', () => {
  const grid = new WorldGrid({ width: 9, height: 7, border: 1, zones: [] });
  grid.place({ instanceId: 'wall', origin: { x: 4, y: 1 }, w: 1, h: 4, door: { x: 4, y: 5 } });
  const path = grid.path({ x: 2, y: 3 }, { x: 6, y: 3 });
  assert.ok(path.length > 0, '应该有路');
  const first = path[0];
  assert.equal(Math.abs(first.x - 2) + Math.abs(first.y - 3), 1, '第一步必须与起点相邻');
  assert.deepEqual(path[path.length - 1], { x: 6, y: 3 });
  assert.ok(!path.some((p) => p.x === 4 && p.y >= 1 && p.y <= 4), '不能穿墙');
});

test('目标被占时退到最近的可走格', () => {
  const grid = new WorldGrid({ width: 9, height: 7, border: 1, zones: [] });
  grid.place({ instanceId: 'house', origin: { x: 4, y: 2 }, w: 2, h: 2, door: { x: 4, y: 4 } });
  const path = grid.path({ x: 1, y: 1 }, { x: 5, y: 3 });
  assert.ok(path.length > 0);
  const last = path[path.length - 1];
  assert.equal(grid.isWalkable(last), true);
});

test('两栋建筑拼成整面墙时，两侧互相不可达', () => {
  const grid = new WorldGrid({ width: 9, height: 7, border: 1, zones: [] });
  assert.equal(
    grid.place({ instanceId: 'wall', origin: { x: 4, y: 1 }, w: 1, h: 4, door: { x: 4, y: 5 } }),
    true,
  );
  assert.equal(
    grid.place({ instanceId: 'plug', origin: { x: 4, y: 5 }, w: 1, h: 1, door: { x: 3, y: 5 } }),
    true,
  );
  assert.deepEqual(grid.path({ x: 2, y: 3 }, { x: 6, y: 3 }), []);
  assert.ok(grid.path({ x: 2, y: 3 }, { x: 3, y: 5 }).length > 0, '左侧仍可走到门口');
});
