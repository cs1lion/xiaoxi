/**
 * 民居：让镇子看起来有人住，但不碰任何一条派活规则。
 *
 * 这组断言的防线意义大于验收意义。"加几间房子"听起来是纯美术改动，
 * 实际上民居和铺子共用 `PlacedBuilding`、共用 `buildings` 数组、共用落位逻辑。
 * 一旦哪处把 `zone === 'living'` 当成铺子，最先坏掉的是**郊野常明灯**
 * 和**每类活 1–2 栋**这两条，而它们都不会抛错，只会静默错算。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld, buildWorld, WILD_PREFABS } from '../src/world/setup.ts';
import { summarizeWorld } from '../src/world/world-state.ts';
import { litWildLanterns } from '../src/world/wilderness-signal.ts';
import { biomeAt } from '../src/world/biomes.ts';
import type { PlacedBuilding } from '../src/world/types.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

const homesOf = (buildings: readonly PlacedBuilding[]): readonly PlacedBuilding[] =>
  buildings.filter((building) => building.zone === 'living');

test('民居在开局落位：镇内三间、郊野两间', () => {
  const world = buildLargeWorld(blueprints);
  const homes = homesOf(world.buildings);
  assert.equal(homes.length, 5, '五间民居都该落进世界');
  const town = homes.filter((home) => {
    const biome = biomeAt(home.origin, world.altar);
    return biome === 'townCore' || biome === 'craftRing';
  });
  assert.equal(town.length, 3, '镇内三间');
  assert.equal(homes.length - town.length, 2, '郊野两间');
});

test('小世界不落民居：旧规格的既有数字全部照旧', () => {
  const small = buildWorld(blueprints);
  assert.equal(homesOf(small.buildings).length, 0, '30×20 是测试夹具，不该凭空多出房子');
  assert.equal(small.buildings.length, 3);
});

test('民居不接活，也不进铺子预算', () => {
  const world = buildLargeWorld(blueprints);
  for (const home of homesOf(world.buildings)) {
    assert.equal(home.boundTask, null, `${home.prefabId} 是住家，不该承接任何活`);
  }
  // 每类活仍是镇内一栋 + 郊野一栋：加房子没有改变这条预算。
  const state = summarizeWorld({
    buildings: world.buildings,
    villagers: [],
    workstations: [],
  } as never);
  assert.equal(state.buildings.town, 3, '镇内铺子还是三栋');
  assert.equal(state.buildings.wild, 3, '郊野铺子还是三栋');
  for (const entry of state.taskKinds) {
    assert.equal(entry.town, 1, `${entry.kind} 镇内一栋`);
    assert.equal(entry.wild, 1, `${entry.kind} 郊野一栋`);
  }
});

test('民居不亮郊野常明灯：那盏灯的意思是「有人在野外干活」', () => {
  const world = buildLargeWorld(blueprints);
  const lit = litWildLanterns({
    buildings: world.buildings,
    villagers: [],
    workstations: [],
  } as never);
  assert.equal(lit.length, 3, '只有三栋郊野铺子亮灯');
  assert.equal(
    lit.filter((building) => building.zone === 'living').length,
    0,
    '住家整晚亮着守夜的灯是错的',
  );
});

test('民居不进 WILD_PREFABS：派活分流按它判断去不去郊野', () => {
  const world = buildLargeWorld(blueprints);
  const leaked = homesOf(world.buildings).filter((home) => WILD_PREFABS.has(home.prefabId));
  assert.deepEqual(leaked.map((home) => home.prefabId), [], '民居混进派活集合会让神谕活派给住家');
});

test('同分区的民居不会叠在一起', () => {
  const world = buildLargeWorld(blueprints);
  const seen = new Set<string>();
  for (const home of homesOf(world.buildings)) {
    const key = `${home.origin.x},${home.origin.y}`;
    assert.ok(!seen.has(key), `${home.prefabId} 与另一间民居叠在同一格 ${key}`);
    seen.add(key);
  }
  // 叠在一起的话，占用的格数会少于各自占地之和。
  const cells = homesOf(world.buildings).reduce(
    (sum, home) => sum + home.footprint.w * home.footprint.h,
    0,
  );
  assert.equal(seen.size, 5, '五间各有各的位置');
  assert.ok(cells >= 5 * 9, `总占地 ${cells} 格，三间 3×3 起算不该少于 45`);
});
