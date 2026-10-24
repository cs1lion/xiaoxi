/**
 * 地表细节按群落分化（`docs/LARGE_WORLD.md` 的「丰富地表层次」）。
 *
 * 这条规格容易被后来的改动悄悄抹平：把 `DRESSING` 表整个换成一个常量、
 * 或者 `drawDressing` 忘了往下传祭坛坐标，画面都不会报错，只会变回
 * 「一大片同密度的草地」——而那种退化在缩略图里几乎看不出来。
 *
 * 所以这里断言的是**配方本身分化了**，而不是某个像素画成了什么。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { BIOME_ORDER, biomeAt, type BiomeId } from '../src/world/biomes.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { DRESSING } from '../src/world/dressing.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

const world = buildLargeWorld(blueprints);

test('每个群落都有自己的一份配方，没有遗漏', () => {
  for (const biome of BIOME_ORDER) {
    assert.ok(DRESSING[biome] !== undefined, `${biome} 缺配方`);
  }
});

test('镇内与郊野的配方真的不一样：不是全图一个密度', () => {
  const town = DRESSING.townCore;
  const wild = DRESSING.wilderness;
  const same = (['edge', 'rock', 'bush', 'slab', 'flower'] as const).every(
    (key) => town[key] === wild[key],
  );
  assert.equal(same, false, '镇内与郊野配方完全一致，等于没有分化');
  // 郊野更荒：岩石更稀（数值更大）、灌木更密（数值更小）。
  assert.ok(wild.rock > town.rock, `郊野岩石应更稀：${wild.rock} vs ${town.rock}`);
  assert.ok(wild.bush < town.bush, `郊野灌木应更密：${wild.bush} vs ${town.bush}`);
});

test('野花只长在镇外：人踩的地方草都长不齐', () => {
  assert.equal(DRESSING.townCore.flower, 0, '镇中心地表不长花');
  assert.equal(DRESSING.craftRing.flower, 0, '工坊环地表不长花');
  assert.ok(DRESSING.wilderness.flower > 0, '郊野有野花');
  assert.ok(DRESSING.frontier.flower > 0, '未探索区有野花');
});

test('越往外越荒：未探索是「多石少花的荒原」，不是更密的灌木丛', () => {
  // 注意方向：值越小越密。郊野是没人管的茂盛，未探索是长不出东西的荒。
  // 两者不是同一条曲线上的两端——郊野的「野」是有生气的，未探索的「荒」
  // 是石头和枯枝。写成同向递增会让未探索变成一片更茂的灌木，读错了。
  assert.ok(
    DRESSING.frontier.rock < DRESSING.wilderness.rock,
    `未探索的碎石应更密：${DRESSING.frontier.rock} vs ${DRESSING.wilderness.rock}`,
  );
  assert.ok(
    DRESSING.frontier.bush > DRESSING.wilderness.bush,
    `未探索的灌木应更稀：${DRESSING.frontier.bush} vs ${DRESSING.wilderness.bush}`,
  );
  assert.ok(
    DRESSING.frontier.flower > DRESSING.wilderness.flower,
    `未探索的野花应更稀：${DRESSING.frontier.flower} vs ${DRESSING.wilderness.flower}`,
  );
  assert.ok(
    DRESSING.frontier.edge > DRESSING.wilderness.edge,
    '未探索的贴边草应比郊野更稀',
  );
});

test('石板碎屑只在镇内出现：那是铺装遗迹', () => {
  assert.ok(DRESSING.townCore.slab > 0 && DRESSING.craftRing.slab > 0);
  assert.equal(DRESSING.wilderness.slab, 0);
  assert.equal(DRESSING.frontier.slab, 0);
});

test('配方里的除数不能是 0：会变成除零或每格都画', () => {
  for (const biome of BIOME_ORDER) {
    const recipe = DRESSING[biome];
    for (const [key, value] of Object.entries(recipe)) {
      if (key === 'flower' || key === 'slab') {
        // 这两项允许为 0，表示「这个群落不长这个」。
        assert.ok(value >= 0, `${biome}.${key} 不能为负`);
        continue;
      }
      assert.ok(value > 0, `${biome}.${key} 必须为正，否则取模失去意义`);
    }
  }
});

test('群落判定与落位用的是同一套半径：镇内民居落在镇内配方里', () => {
  // 这条把 `biomeAt` 与地表配方连起来：民居的落点该吃到镇内配方。
  const homes = world.buildings.filter((building) => building.zone === 'living');
  const townHomes = homes.filter((home) => {
    const biome = biomeAt(home.origin, world.altar);
    return biome === 'townCore' || biome === 'craftRing';
  });
  assert.equal(townHomes.length, 3, '三间镇内民居应当落在镇内配方覆盖的范围内');
  for (const home of townHomes) {
    const biome = biomeAt(home.origin, world.altar) as BiomeId;
    assert.equal(DRESSING[biome].flower, 0, `${home.prefabId} 周围不该长野花`);
  }
});
