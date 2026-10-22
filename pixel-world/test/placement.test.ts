import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, checkPlacement, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { composeReport } from '../src/world/report.ts';
import { WorldGrid } from '../src/world/grid.ts';
import type { PrefabBlueprint } from '../src/world/types.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

const plot = blueprints.get('workshop_plot');
if (plot === undefined) throw new Error('测试依赖 workshop_plot 蓝图');

// ------------------------------------------------------------------ 合法位置

test('合法位置：扩建样板能落在它自己的分区里', () => {
  const world = buildWorld(blueprints);
  const slot = world.grid.findBestSlot(plot, ALTAR);
  assert.ok(slot !== null, '世界初始状态应当有空位');

  const result = checkPlacement(world.grid, plot, slot);
  assert.equal(result.ok, true, result.ok ? '' : result.reason);
});

// ------------------------------------------------------------------ 五种拒绝理由

test('越界：原点在世界外 → edge', () => {
  const world = buildWorld(blueprints);
  const result = checkPlacement(world.grid, plot, { x: -2, y: 3 });
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'edge');
});

test('越界：占地伸出世界右边界 → edge', () => {
  const world = buildWorld(blueprints);
  const result = checkPlacement(world.grid, plot, { x: world.grid.width - 1, y: 3 });
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'edge');
});

test('分区不符：把工业区的样板放到书斋区 → outOfZone', () => {
  const world = buildWorld(blueprints);
  // 找一个明确属于 scholarly 分区的格子。
  let target: { x: number; y: number } | null = null;
  for (let y = 0; y < world.grid.height && target === null; y += 1) {
    for (let x = 0; x < world.grid.width; x += 1) {
      if (world.grid.zoneOf({ x, y }) === 'scholarly') {
        target = { x, y };
        break;
      }
    }
  }
  assert.ok(target !== null, '世界应当有 scholarly 分区');
  const result = checkPlacement(world.grid, plot, target);
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'outOfZone');
});

test('被占用：落在已建楼的位置上 → occupied', () => {
  const world = buildWorld(blueprints);
  const existing = world.buildings[0];
  assert.ok(existing !== undefined);
  const result = checkPlacement(world.grid, plot, existing.origin);
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'occupied');
});

/**
 * 合成网格：一整块 industry 分区，没有真实世界的既有建筑干扰。
 * 用来精确构造「只有这一条规则不满足」的场景。
 */
function syntheticGrid(width = 16, height = 12): WorldGrid {
  return new WorldGrid({
    width,
    height,
    border: 1,
    zones: [{ id: 'industry', x: 0, y: 0, w: width, h: height }],
  });
}

const syntheticPlot: PrefabBlueprint = {
  ...plot,
  footprint: { w: 4, h: 3 },
  // 关门放在占地正下方一格：留白为 0 时它才不会被 margin 检查覆盖。
  clearMargin: 0,
  door: { x: 1, y: 3 },
};

test('门口被堵：占地合法但门口被占 → doorBlocked', () => {
  const grid = syntheticGrid();
  // 在门口的落点先放一栋楼。
  grid.place({ instanceId: 'blocker', origin: { x: 5, y: 6 }, w: 3, h: 2, door: { x: 6, y: 5 } });

  const result = checkPlacement(grid, syntheticPlot, { x: 4, y: 3 });
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'doorBlocked');
});

test('留白不足：占地空着，但紧贴已有建筑 → margin', () => {
  const grid = syntheticGrid();
  // 邻居放在 (4,4)，与待建占地之间只隔 0 格；margin=1 的蓝图会拒绝。
  grid.place({ instanceId: 'neighbor', origin: { x: 4, y: 4 }, w: 4, h: 3, door: { x: 5, y: 3 } });

  const withMargin: PrefabBlueprint = { ...syntheticPlot, clearMargin: 1, door: { x: 1, y: 3 } };
  const result = checkPlacement(grid, withMargin, { x: 4, y: 7 });
  assert.equal(result.ok, false);
  assert.equal(result.ok === false && result.reason, 'margin');
});

// ------------------------------------------------------------------ 与自动落位一致

test('校验与自动落位用同一套规则：findBestSlot 给出的位置必然通过', () => {
  const world = buildWorld(blueprints);
  for (const blueprint of blueprints.values()) {
    const slot = world.grid.findBestSlot(blueprint, ALTAR);
    if (slot === null) continue;
    const result = checkPlacement(world.grid, blueprint, slot);
    assert.equal(
      result.ok,
      true,
      `${blueprint.id} 的自动落点未通过校验：${result.ok === false ? result.reason : ''}`,
    );
  }
});

// ------------------------------------------------------------------ 指定位置建楼

test('指定位置建楼：落点就是玩家给的那个', () => {
  const world = buildWorld(blueprints);
  const slot = world.grid.findBestSlot(plot, ALTAR);
  assert.ok(slot !== null);

  const placed = placeBlueprint(world.grid, plot, 'plot#custom', ALTAR, slot);
  assert.ok(placed !== null);
  assert.deepEqual(placed.building.origin, slot, '必须落在指定位置，不能自己挑');
});

test('不指定位置时行为不变：仍然走 findBestSlot', () => {
  const world = buildWorld(blueprints);
  const expected = world.grid.findBestSlot(plot, ALTAR);
  const placed = placeBlueprint(world.grid, plot, 'plot#auto', ALTAR);
  assert.ok(placed !== null);
  assert.deepEqual(placed.building.origin, expected);
});

test('指定非法位置时返回 null，不建半截楼', () => {
  const world = buildWorld(blueprints);
  const before = world.grid.countOccupied();
  const placed = placeBlueprint(world.grid, plot, 'plot#bad', ALTAR, { x: -3, y: -3 });
  assert.equal(placed, null);
  assert.equal(world.grid.countOccupied(), before, '被拒时不能占用任何格子');
});

// ------------------------------------------------------------------ 建筑来源

test('造物模式建出的楼记着自己是从哪来的', () => {
  const world = buildWorld(blueprints);
  const slot = world.grid.findBestSlot(plot, ALTAR);
  assert.ok(slot !== null);

  const placed = placeBlueprint(world.grid, plot, 'plot#src', ALTAR, slot, {
    taskId: 't_42',
    summary: '开一间新工坊',
  });
  assert.ok(placed !== null);
  assert.equal(placed.building.sourceTaskId, 't_42');
  assert.equal(placed.building.sourceSummary, '开一间新工坊');
});

test('开局常驻的楼没有来源：它们是世界自带的，不是谁建的', () => {
  const world = buildWorld(blueprints);
  for (const building of world.buildings) {
    assert.equal(building.sourceTaskId, null, `${building.instanceId} 不该有来源`);
    assert.equal(building.sourceSummary, null);
  }
});

test('有来由的楼在日报里写清来由', () => {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1000,
    spawnBuilding: (prefabId, instanceId, at, source) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR, at, source);
    },
  });
  sim.enqueue({
    kind: 'build',
    prefabId: 'workshop_plot',
    instanceId: 'plot#report',
    source: { taskId: 'creation-1', summary: '在 (2,2) 建了新工坊' },
  });
  sim.update(16);

  const report = composeReport(sim.snapshot());
  const line = report.lines.find((item) => item.includes('造物主新起'));
  assert.ok(line !== undefined, '日报应当提到新建的楼');
  assert.match(line, /在 \(2,2\) 建了新工坊/, '日报要写清这栋楼是怎么来的');
});
