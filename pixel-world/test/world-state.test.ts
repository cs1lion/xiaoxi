import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { describeWorldState, summarizeWorld } from '../src/world/world-state.ts';

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
  });
}

// ------------------------------------------------------------------ 形状

test('世界状态是一份可读的摘要，不是原始快照', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());

  assert.ok(state.buildings.total > 0, '要能说出现在有几栋');
  assert.equal(typeof state.buildings.town, 'number');
  assert.equal(typeof state.buildings.wild, 'number');
  assert.ok(Array.isArray(state.taskKinds), '要能说出每类活的铺子数');
});

test('摘要里的数字与快照一致：不是另算一套', () => {
  const sim = makeSim();
  const snapshot = sim.snapshot();
  const state = summarizeWorld(snapshot);
  assert.equal(state.buildings.total, snapshot.buildings.length);
});

// ------------------------------------------------------------------ 每类活的覆盖

test('每类活都说得出「镇内有几栋、郊野有几栋」', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());

  assert.equal(state.taskKinds.length, 3, '三类长活');
  for (const entry of state.taskKinds) {
    assert.ok(entry.kind === 'code' || entry.kind === 'research' || entry.kind === 'writing');
    assert.equal(entry.town + entry.wild, entry.total, '镇内 + 郊野 = 总数');
  }
});

test('开局时每类活都是镇内一栋、郊野一栋', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());
  for (const entry of state.taskKinds) {
    assert.equal(entry.town, 1, `${entry.kind} 应当有一栋镇内铺子`);
    assert.equal(entry.wild, 1, `${entry.kind} 应当有一栋郊野铺子`);
  }
});

// ------------------------------------------------------------------ 空间占用

test('说得出郊野占了多少、还剩多少可建', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());
  assert.ok(state.space.wildBuilt >= 0);
  assert.ok(state.space.wildFree >= 0, '要能说出郊野还剩多少空地');
});

test('新建一栋后，郊野占用增加', () => {
  const sim = makeSim();
  const before = summarizeWorld(sim.snapshot()).space.wildBuilt;

  sim.enqueue({
    kind: 'build',
    prefabId: 'workshop_plot',
    instanceId: 'plot#state',
  });
  sim.update(16);

  const after = summarizeWorld(sim.snapshot()).space.wildBuilt;
  assert.ok(after >= before, '建楼不会让占用变少');
});

// ------------------------------------------------------------------ 活动

test('说得出现在有几个人在忙、几个闲', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.update(16);

  const state = summarizeWorld(sim.snapshot());
  assert.equal(state.activity.busy + state.activity.idle, state.activity.total);
  assert.ok(state.activity.busy >= 1, '派了活之后至少有一个在忙');
});

// ------------------------------------------------------------------ 给造物主用的判断

test('说得出「哪类活还没有郊野铺子」——造物主据此做选择', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());
  // 开局每类都有，所以是空数组。
  assert.deepEqual(state.gaps.missingWild, []);
});

test('少一栋郊野铺子时，缺口说得出是哪一类', () => {
  const world = buildLargeWorld(blueprints);
  // 去掉郊野的研究铺子。
  const without = world.buildings.filter((b) => b.instanceId !== 'crawler_mine#1');
  const sim = new Simulation({
    grid: world.grid,
    buildings: without,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });

  const state = summarizeWorld(sim.snapshot());
  assert.deepEqual(state.gaps.missingWild, ['research'], '缺的是研究类郊野铺子');
});

// ------------------------------------------------------------------ 接进造物

test('已有书阁时，回应里指出世界缺什么', () => {
  const world = buildLargeWorld(blueprints);
  // 去掉郊野研究铺子，制造一个明确缺口。
  const without = world.buildings.filter((b) => b.instanceId !== 'crawler_mine#1');
  const sim = new Simulation({
    grid: world.grid,
    buildings: without,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });

  sim.enqueue({ kind: 'oracle', raw: '建一间书阁' });
  const result = sim.update(16)[0];

  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /已经有星图书阁/);
  // 纸面用「查资料」而不是内部枚举名 research。
  assert.match(result?.message ?? '', /查资料/, '应当指出缺的是查资料的郊野铺子');
});

test('没有缺口时，回应退回「想扩建就说建个工坊」', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'oracle', raw: '建一间书阁' });
  const result = sim.update(16)[0];
  assert.match(result?.message ?? '', /建个工坊/);
});

// ------------------------------------------------------------------ 给界面用的摘要

test('界面摘要：一行说清镇内郊野、缺口、可建模板', () => {
  const sim = makeSim();
  const state = summarizeWorld(sim.snapshot());
  const line = describeWorldState(state);

  assert.match(line, /镇内 3/);
  assert.match(line, /郊野 3/);
  // 没有缺口时不该出现「缺」字。
  assert.ok(!line.includes('缺'), `没有缺口时不该提：${line}`);
});

test('有缺口时，摘要点名缺哪一类（用纸面说法）', () => {
  const world = buildLargeWorld(blueprints);
  const without = world.buildings.filter((b) => b.instanceId !== 'crawler_mine#1');
  const sim = new Simulation({
    grid: world.grid,
    buildings: without,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });

  const line = describeWorldState(summarizeWorld(sim.snapshot()));
  assert.match(line, /缺/);
  assert.match(line, /查资料/, '要说「查资料」，不是 research');
});

test('摘要里说得出郊野还剩多少空地', () => {
  const sim = makeSim();
  const line = describeWorldState(summarizeWorld(sim.snapshot()));
  assert.match(line, /空地/);
});
