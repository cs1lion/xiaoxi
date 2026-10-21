import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import {
  LARGE_WORLD_SPEC,
  SMALL_WORLD_SPEC,
  buildWorld,
  buildLargeWorld,
  assertWorldInvariants,
  TILE_PX,
} from '../src/world/setup.ts';
import { WorldGrid } from '../src/world/grid.ts';
import { BIOME_LABEL, BIOME_ORDER, biomeAt } from '../src/world/biomes.ts';
import { WALK_COST } from '../src/world/terrain-cost.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

// ------------------------------------------------------------------ 规格

test('大世界规格：80×60，图块仍是 16px', () => {
  assert.equal(LARGE_WORLD_SPEC.width, 80);
  assert.equal(LARGE_WORLD_SPEC.height, 60);
  assert.equal(TILE_PX, 16, '放大图块与扩大世界互相抵消，图块尺寸不动');
});

test('旧规格保留为测试夹具，尺寸不变', () => {
  assert.equal(SMALL_WORLD_SPEC.width, 30);
  assert.equal(SMALL_WORLD_SPEC.height, 20);
});

test('大世界总像素：1280×960，是旧世界的约 8 倍面积', () => {
  const large = LARGE_WORLD_SPEC.width * LARGE_WORLD_SPEC.height;
  const small = SMALL_WORLD_SPEC.width * SMALL_WORLD_SPEC.height;
  assert.equal(LARGE_WORLD_SPEC.width * TILE_PX, 1280);
  assert.equal(LARGE_WORLD_SPEC.height * TILE_PX, 960);
  assert.ok(large >= small * 7.5, `面积比 ${(large / small).toFixed(2)} 应约 8 倍`);
});

// ------------------------------------------------------------------ 群落

test('群落顺序由内向外：核心 → 工坊农田 → 郊野 → 迷雾边界', () => {
  assert.deepEqual([...BIOME_ORDER], ['townCore', 'craftRing', 'wilderness', 'frontier']);
});

test('群落按离中心距离划分：越靠近祭坛越是核心', () => {
  const center = { x: 40, y: 30 };
  assert.equal(biomeAt(center, center), 'townCore');
  assert.equal(biomeAt({ x: center.x + 6, y: center.y }, center), 'townCore');
  assert.equal(biomeAt({ x: center.x + 12, y: center.y }, center), 'craftRing');
  assert.equal(biomeAt({ x: center.x + 24, y: center.y }, center), 'wilderness');
  assert.equal(biomeAt({ x: center.x + 36, y: center.y }, center), 'frontier');
});

test('群落判定是纯函数：同输入同输出', () => {
  const center = { x: 40, y: 30 };
  const p = { x: 55, y: 30 };
  assert.equal(biomeAt(p, center), biomeAt(p, center));
});

test('每个群落都有纸面可用的名字', () => {
  for (const biome of BIOME_ORDER) {
    assert.ok(BIOME_LABEL[biome].length > 0, `${biome} 必须有名字`);
  }
});

// ------------------------------------------------------------------ 通行代价

test('通行代价：石板路最快，森林最慢，顺序固定', () => {
  assert.ok(WALK_COST.plaza < WALK_COST.stone, '石板路比广场砖快');
  assert.ok(WALK_COST.stone < WALK_COST.grass, '铺装路面比草地快');
  assert.ok(WALK_COST.grass < WALK_COST.farm, '草地比农田快');
  assert.ok(WALK_COST.farm < WALK_COST.tree, '农田比密林快');
  for (const cost of Object.values(WALK_COST)) {
    assert.ok(cost > 0, '代价必须为正，否则 A* 会退化成免费穿越');
  }
});

test('大世界可寻路：镇中心到郊野锚点能走通', () => {
  const world = buildLargeWorld(blueprints);
  const far = { x: world.grid.width - 3, y: world.grid.height - 3 };
  const path = world.grid.path(world.altar, far);
  assert.ok(path.length > 0, '镇中心应当能走到地图另一角');
});

test('大世界寻路不慢：单次跨图寻路在 20ms 内', () => {
  const world = buildLargeWorld(blueprints);
  const t0 = performance.now();
  for (let i = 0; i < 10; i += 1) {
    world.grid.path(world.altar, { x: 2, y: 2 });
  }
  const per = (performance.now() - t0) / 10;
  assert.ok(per < 20, `单次寻路 ${per.toFixed(2)}ms 超过 20ms`);
});

// ------------------------------------------------------------------ 世界不变量

test('大世界开局满足既有不变量：三栋铺子、分区不重叠、都能走到祭坛', () => {
  const world = buildLargeWorld(blueprints);
  assert.doesNotThrow(() => assertWorldInvariants(world.grid, world.buildings));
  const workshops = world.buildings.filter((b) => b.boundTask !== null);
  assert.equal(workshops.length, 3);
});

test('大世界把郊野铺子放在核心圈之外', () => {
  const world = buildLargeWorld(blueprints);
  // 郊野至少有一栋铺子不在核心圈里，这是「辽阔」的最小证据。
  const coreRadius = 10;
  const outside = world.buildings.filter((b) => {
    const dx = b.origin.x - world.altar.x;
    const dy = b.origin.y - world.altar.y;
    return Math.hypot(dx, dy) > coreRadius;
  });
  assert.ok(outside.length > 0, '郊野应当有铺子，而不是全挤在中心');
});

// ------------------------------------------------------------------ 旧规格不受影响

test('默认 buildWorld 仍用旧规格：20 处既有调用点行为不变', () => {
  const world = buildWorld(blueprints);
  assert.equal(world.grid.width, 30);
  assert.equal(world.grid.height, 20);
});

test('大世界与小世界可以并存：同一个蓝图表能建两种规格', () => {
  const small = buildWorld(blueprints);
  const large = buildLargeWorld(blueprints);
  assert.equal(small.grid.width, 30);
  assert.equal(large.grid.width, 80);
  assert.equal(small.buildings.length, large.buildings.length, '常驻铺子数量不随规格变化');
});

test('空网格：大世界规格能独立构造，不依赖 buildLargeWorld', () => {
  const grid = new WorldGrid(LARGE_WORLD_SPEC);
  assert.equal(grid.width, 80);
  assert.equal(grid.height, 60);
  assert.equal(grid.at({ x: 79, y: 59 }) !== undefined, true);
  assert.equal(grid.at({ x: 80, y: 59 }), undefined);
});
