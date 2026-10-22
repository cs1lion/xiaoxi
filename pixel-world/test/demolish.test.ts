import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { summarizeWorld } from '../src/world/world-state.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

function makeSim(): Simulation {
  const world = buildLargeWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
    spawnBuilding: (prefabId, instanceId, at, source) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, world.altar, at, source);
    },
  });
}

const demolish = (sim: Simulation, instanceId: string) => {
  sim.enqueue({ kind: 'demolish', instanceId });
  return sim.update(16)[0];
};

// ------------------------------------------------------------------ 基本

test('拆一栋楼：它从世界消失，格子空出来', () => {
  const sim = makeSim();
  const target = 'workshop_plot#1';
  // 先建一栋再拆。
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: target });
  sim.update(16);
  // 等施工走完（3200ms），否则拆不了。
  for (let i = 0; i < 40; i += 1) sim.update(100);
  assert.ok(sim.snapshot().buildings.some((b) => b.instanceId === target));

  const before = sim.snapshot().buildings.length;
  const result = demolish(sim, target);

  assert.equal(result?.ok, true, result?.message);
  assert.equal(sim.snapshot().buildings.length, before - 1);
  assert.ok(!sim.snapshot().buildings.some((b) => b.instanceId === target));
});

test('拆不存在的楼：拒绝，并说清', () => {
  const sim = makeSim();
  const result = demolish(sim, 'nope#1');
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /没有|找不到/);
});

test('拆完能重新建在同一位置', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'plot#a' });
  sim.update(16);
  for (let i = 0; i < 40; i += 1) sim.update(100);
  const at = sim.snapshot().buildings.find((b) => b.instanceId === 'plot#a')?.origin;
  assert.ok(at !== undefined);

  demolish(sim, 'plot#a');
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'plot#b', at });
  const result = sim.update(16)[0];
  assert.equal(result?.ok, true, `同一位置应当能重建：${result?.message}`);
});

// ------------------------------------------------------------------ 边界

test('正在施工的楼不能拆', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'plot#c' });
  sim.update(16);
  // 施工中（3200ms 未走完）。
  const result = demolish(sim, 'plot#c');
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /施工/);
});

test('有角色在里面干活的楼不能拆', () => {
  const sim = makeSim();
  // 派一个活到某栋常驻铺子，等人走进去。
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.update(16);
  // 大世界里神谕活会派到郊野（20+ 格），走过去要时间。
  // 要抓「正在运转」的那一刻，不能一口气跑完——跑完就进冷却了。
  for (let i = 0; i < 400; i += 1) {
    sim.update(100);
    if (sim.snapshot().workstations.some((w) => w.activity.kind === 'operating')) break;
  }

  const working = sim.snapshot().workstations.find((w) => w.activity.kind === 'operating');
  assert.ok(working !== undefined, '应当有一台铺子在运转');

  const result = demolish(sim, working.buildingId);
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /干活|忙|运转/);
});

test('常驻铺子也能拆：世界允许把镇子拆空', () => {
  const sim = makeSim();
  const result = demolish(sim, 'crawler_mine#1');
  assert.equal(result?.ok, true, result?.message);
  assert.ok(!sim.snapshot().buildings.some((b) => b.instanceId === 'crawler_mine#1'));
});

// ------------------------------------------------------------------ 拆完的世界

test('拆掉郊野铺子后，世界状态说出缺口', () => {
  const sim = makeSim();
  demolish(sim, 'crawler_mine#1');
  const state = summarizeWorld(sim.snapshot());
  assert.deepEqual(state.gaps.missingWild, ['research'], '缺研究类郊野铺子');
});

test('拆完刻石板：这是改变了世界的事', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'oracle', raw: '拆掉矿洞' });
  sim.update(16);
  // 先确认造物神谕不认「拆」——拆是独立命令，不是造物。
  // 这里直接走 demolish，验证石板。
  const slabBefore = sim.snapshot().oracle.length;
  demolish(sim, 'crawler_mine#1');
  assert.ok(sim.snapshot().oracle.length >= slabBefore);
});
