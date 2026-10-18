import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable, validateCoverage, BlueprintError } from '../src/world/blueprints.ts';
import { buildWorld, assertWorldInvariants, WorldSetupError, ALTAR, GRID_WIDTH } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { parseOracle } from '../src/world/oracle.ts';


const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

function fixedRandom(value: number): () => number {
  return () => value;
}

test('生产蓝图表能解析，且每种活恰好一栋铺子', () => {
  assert.equal(blueprints.size, 3);
  assert.deepEqual(validateCoverage(blueprints), []);
});

test('蓝图 id 与键名不符时拒绝加载', () => {
  assert.throws(
    () => parseBlueprintTable({ a: { ...raw.code_forge, id: 'b' } }),
    BlueprintError,
  );
});

test('门口落在占地内时拒绝加载', () => {
  const bad = {
    ...raw.code_forge,
    door: { x: 1, y: 1 },
  };
  assert.throws(() => parseBlueprintTable({ code_forge: bad }), BlueprintError);
});

test('铺子与活对不上时拒绝加载', () => {
  const bad = {
    ...raw.code_forge,
    boundTask: 'research',
  };
  assert.throws(() => parseBlueprintTable({ code_forge: bad }), BlueprintError);
});

test('世界能把三栋铺子摆进各自分区，且都能从祭坛走到', () => {
  const world = buildWorld(blueprints);
  assert.equal(world.buildings.length, 3);
  assert.equal(new Set(world.buildings.map((b) => b.zone)).size, 3);
  assertWorldInvariants(world.grid, world.buildings);
});

test('世界越出预算时拒绝开工', () => {
  const world = buildWorld(blueprints);
  assert.throws(
    () => assertWorldInvariants(world.grid, world.buildings.slice(0, 2)),
    WorldSetupError,
  );
});

test('神谕按动词收敛，认不出的落 unknown', () => {
  assert.deepEqual(parseOracle('帮我写个爬虫'), { kind: 'dispatch', taskKind: 'code', summary: '帮我写个爬虫' });
  assert.deepEqual(parseOracle('查一下天气'), { kind: 'dispatch', taskKind: 'research', summary: '查一下天气' });
  assert.equal(parseOracle('整理一下便笺').kind, 'dispatch');
  assert.equal(parseOracle('交付').kind, 'complete');
  assert.equal(parseOracle('今天真好看').kind, 'unknown');
  assert.equal(parseOracle('   ').kind, 'unknown');
});

interface Rig {
  readonly sim: Simulation;
  readonly buildings: ReturnType<typeof buildWorld>['buildings'];
  tick(ms: number): void;
}

function makeRig(): Rig {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    random: fixedRandom(0.5),
  });
  return {
    sim,
    buildings: world.buildings,
    tick(ms: number) {
      sim.update(ms);
    },
  };
}

test('空闲时在祭坛附近走动，不会离开可走区', () => {
  const rig = makeRig();
  for (let i = 0; i < 200; i += 1) rig.tick(100);
  const snap = rig.sim.snapshot();
  assert.equal(snap.villager.mode, 'idle');
  assert.ok(snap.villager.pos.x > 0 && snap.villager.pos.x < GRID_WIDTH - 1);
});

test('神谕下达后：行礼 → 走进铺子 → 铺子开工', () => {
  const rig = makeRig();
  const forge = rig.buildings.find((b) => b.boundTask === 'code');
  assert.ok(forge);

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.equal(rig.sim.snapshot().villager.mode, 'saluting');

  rig.tick(800);
  assert.equal(rig.sim.snapshot().villager.mode, 'toWork');

  for (let i = 0; i < 100 && rig.sim.snapshot().villager.mode !== 'working'; i += 1) {
    rig.tick(100);
  }
  const working = rig.sim.snapshot();
  assert.equal(working.villager.mode, 'working');
  assert.equal(working.villager.visible, false, '进屋干活时人不该在门外');
  const activity = working.workstations.find((w) => w.buildingId === forge.instanceId)?.activity;
  assert.equal(activity?.kind, 'operating');
});

test('活干完之后铺子冒一阵冷却，人走到门口交付，再回到祭坛', () => {
  const rig = makeRig();
  const forge = rig.buildings.find((b) => b.boundTask === 'code');
  assert.ok(forge);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });

  for (let i = 0; i < 400 && rig.sim.snapshot().villager.mode !== 'delivering'; i += 1) {
    rig.tick(100);
  }
  const delivered = rig.sim.snapshot();
  assert.equal(delivered.villager.mode, 'delivering');
  assert.equal(delivered.villager.visible, true);
  assert.equal(delivered.tasks[0].status, 'done');
  const activity = delivered.workstations.find((w) => w.buildingId === forge.instanceId)?.activity;
  assert.equal(activity?.kind, 'cooling');

  for (let i = 0; i < 400 && rig.sim.snapshot().villager.mode !== 'idle'; i += 1) {
    rig.tick(100);
  }
  assert.equal(rig.sim.snapshot().villager.mode, 'idle');
});

test('手上有活时不接新神谕，世界状态不变', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  rig.sim.update(16);
  rig.tick(800);
  rig.tick(200);

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, false);
  assert.equal(rig.sim.snapshot().tasks.length, 1);
});

test('空手时交付被拒绝', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'complete' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, false);
});

test('每条命令只在 update 开头结算一次', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  assert.equal(rig.sim.snapshot().villager.mode, 'idle', '入队瞬间世界不动');
  rig.sim.update(16);
  assert.equal(rig.sim.snapshot().villager.mode, 'saluting');
});

test('同样的随机种子跑出同样的世界', () => {
  const a = makeRig();
  const b = makeRig();
  for (let i = 0; i < 100; i += 1) {
    a.tick(100);
    b.tick(100);
  }
  assert.deepEqual(a.sim.snapshot().villager.pos, b.sim.snapshot().villager.pos);
});

test('缺一栋铺子时神谕无法落地', () => {
  const world = buildWorld(blueprints);
  const stripped = world.buildings.filter((b) => b.boundTask !== 'research');
  const sim = new Simulation({
    grid: world.grid,
    buildings: stripped,
    altar: ALTAR,
    villagerName: '莞尔',
    random: fixedRandom(0.5),
  });
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查' });
  const result = sim.update(16);
  assert.equal(result[0].ok, false);
  assert.equal(sim.snapshot().villager.mode, 'idle');
});
