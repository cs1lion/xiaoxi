import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

/** 大世界里的世界层，不接台账。 */
function makeLargeSim(): Simulation {
  const world = buildLargeWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
  });
}

const WILD_IDS = new Set(['crawler_mine#1', 'ink_mill#1', 'gear_tower#1']);

test('神谕派的研究活去郊野铺子，不去镇内书斋', () => {
  const sim = makeLargeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下向量库' });
  sim.update(16);

  const task = sim.snapshot().tasks.find((item) => item.summary.includes('向量库'));
  assert.ok(task !== undefined, '任务应当被创建');
  assert.ok(task.buildingId !== null, '长活应当进铺子');
  assert.ok(
    WILD_IDS.has(task.buildingId),
    `神谕活应去郊野，实际去了 ${task.buildingId}`,
  );
});

test('神谕派的写码活也去郊野', () => {
  const sim = makeLargeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写个爬虫' });
  sim.update(16);

  const task = sim.snapshot().tasks.find((item) => item.summary.includes('爬虫'));
  assert.ok(task !== undefined);
  assert.ok(WILD_IDS.has(String(task.buildingId)), `实际去了 ${task.buildingId}`);
});

test('郊野铺子忙时，神谕活退回镇内', () => {
  const sim = makeLargeSim();
  // 让两个角色分别占用郊野的研究铺子与另一栋。
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '第一单' });
  sim.update(16);
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '第二单' });
  sim.update(16);

  const tasks = sim.snapshot().tasks.filter((item) => item.summary.includes('单'));
  const buildings = new Set(tasks.map((item) => item.buildingId));
  assert.ok(
    buildings.has('star_archive#1'),
    '第二单应退回镇内书斋，实际只用了 ' + [...buildings].join(','),
  );
});

test('小世界没有郊野铺子：神谕活仍然去镇内，行为与以前一致', () => {
  const world = buildLargeWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings.filter((b) => !WILD_IDS.has(b.instanceId)),
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
  });
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查资料' });
  sim.update(16);
  const task = sim.snapshot().tasks[0];
  assert.equal(task?.buildingId, 'star_archive#1');
});
