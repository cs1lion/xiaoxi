import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { ORACLE_SLAB_LIMIT } from '../src/world/types.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

function makeSim(): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1000,
  });
}

const miracle = (sim: Simulation, kind: 'light' | 'tree' | 'cat') => {
  sim.enqueue({ kind: 'miracle', miracle: kind });
  return sim.update(16)[0];
};

// ------------------------------------------------------------------ 点灯

test('点灯：世界里多一盏亮着的灯，带神迹来源', () => {
  const sim = makeSim();
  const result = miracle(sim, 'light');
  assert.equal(result?.ok, true, result?.message);

  const props = sim.snapshot().props;
  assert.equal(props.length, 1);
  assert.equal(props[0]!.kind, 'lamp');
  assert.equal(props[0]!.sourceTaskKind, null, '神迹不是任务换来的');
  assert.match(String(props[0]!.sourceSummary), /点灯|神迹/, '要能说清它是神迹');
});

// ------------------------------------------------------------------ 种树

test('种树：世界里多一棵树，落在可走的空地上', () => {
  const sim = makeSim();
  const result = miracle(sim, 'tree');
  assert.equal(result?.ok, true, result?.message);

  const props = sim.snapshot().props;
  assert.equal(props.length, 1);
  assert.equal(props[0]!.kind, 'tree');
  // 落点必须在世界内，且不能落在祭坛正中（那是角色站立与行礼的地方）。
  const at = props[0]!.at;
  assert.ok(at.x >= 0 && at.y >= 0, `落点越界：${at.x},${at.y}`);
  assert.notDeepEqual(at, ALTAR, '不能落在祭坛格上');
});

// ------------------------------------------------------------------ 召唤猫

test('召唤猫：猫只在氛围层，不参与任务、不改变交付统计', () => {
  const sim = makeSim();
  const before = sim.snapshot();
  const result = miracle(sim, 'cat');
  assert.equal(result?.ok, true, result?.message);

  const after = sim.snapshot();
  assert.equal(after.tasks.length, before.tasks.length, '猫不产生任务');
  assert.equal(after.deliveries.mock, before.deliveries.mock, '猫不算交付');
  assert.ok(after.critters.length > 0, '猫应当出现在快照的氛围实体里');
});

test('猫会自己走开：过一段时间后离开世界，不永久占位', () => {
  const sim = makeSim();
  miracle(sim, 'cat');
  assert.ok(sim.snapshot().critters.length > 0);

  // 猫待一会儿就走；跑满一分钟必须已经离开。
  for (let i = 0; i < 60; i += 1) sim.update(1000);
  assert.equal(sim.snapshot().critters.length, 0, '猫不该永久留在世界里');
});

// ------------------------------------------------------------------ 与摆件去重

test('种树与信仰摆件不抢同一格', () => {
  const sim = makeSim();
  miracle(sim, 'tree');
  const firstAt = sim.snapshot().props[0]!.at;

  miracle(sim, 'tree');
  const props = sim.snapshot().props;
  assert.equal(props.length, 2);
  const seen = new Set(props.map((p) => `${p.at.x},${p.at.y}`));
  assert.equal(seen.size, props.length, '两棵树不能落在同一格');
  assert.notDeepEqual(props[1]!.at, firstAt);
});

// ------------------------------------------------------------------ 存读档

test('神迹落下的东西能存读档', () => {
  const sim = makeSim();
  miracle(sim, 'light');
  miracle(sim, 'tree');
  miracle(sim, 'cat');

  const save = sim.toSave();
  const world = buildWorld(blueprints);
  const restored = Simulation.fromSave(JSON.parse(JSON.stringify(save)), {
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1000,
  });

  const after = restored.snapshot();
  assert.equal(after.props.length, 2, '灯与树都要恢复');
  assert.equal(after.critters.length, 1, '猫也要恢复');
});

// ------------------------------------------------------------------ 不刷屏

test('神迹不写石板：它不是你的话', () => {
  const sim = makeSim();
  miracle(sim, 'light');
  miracle(sim, 'tree');
  miracle(sim, 'cat');
  assert.equal(sim.snapshot().oracle.length, 0, '神迹没有原话，不该刻石板');
});

test('神迹不受石板上限影响，也不会把石板挤掉', () => {
  const sim = makeSim();
  for (let i = 0; i < ORACLE_SLAB_LIMIT + 3; i += 1) {
    if (i % 2 === 0) {
      sim.enqueue({ kind: 'oracle', raw: `下雨吧 ${i}` });
    } else {
      sim.enqueue({ kind: 'oracle', raw: `放晴吧 ${i}` });
    }
    sim.update(16);
  }
  const slabBefore = sim.snapshot().oracle.length;
  miracle(sim, 'light');
  assert.equal(sim.snapshot().oracle.length, slabBefore, '神迹不占石板位置');
});
