import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, buildWorldFromSave, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { assertSaveV1, MIGRATIONS, SAVE_SETTLED_KEYS, SAVE_TASK_KEYS, type WorldSaveV1 } from '../src/world/save.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

function makeSim(wallClock: () => number = () => 1000): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock,
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
}

function depsFromSave(save: WorldSaveV1, wallClock: () => number = () => 1000) {
  const world = buildWorldFromSave(save.world.buildings);
  return {
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: save.world.villagers[0]?.name ?? '莞尔',
    wallClock,
  };
}

function fromSave(save: WorldSaveV1): Simulation {
  const clone = JSON.parse(JSON.stringify(save)) as WorldSaveV1;
  return Simulation.fromSave(clone, depsFromSave(clone));
}

test('续跑等价：toSave → fromSave → 再跑 50 拍，快照一致', () => {
  let wall = 5000;
  const a = makeSim(() => (wall += 1));
  a.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  a.update(16);
  for (let index = 0; index < 30; index += 1) a.update(100);
  const save = a.toSave();

  const b = fromSave(save);
  for (let index = 0; index < 50; index += 1) {
    a.update(100);
    b.update(100);
  }
  const pick = (sim: Simulation): string => {
    const snapshot = sim.snapshot();
    return JSON.stringify({
      now: snapshot.now,
      villagers: snapshot.villagers,
      tasks: snapshot.tasks,
      pending: snapshot.pending,
      props: snapshot.props,
      roads: snapshot.roads,
      faith: snapshot.faith,
      deliveries: snapshot.deliveries,
      deliveriesByVillager: snapshot.deliveriesByVillager,
      weather: snapshot.weather,
      intensity: sim.weatherIntensity(),
      constructions: snapshot.constructions,
      workstations: snapshot.workstations,
    });
  };
  assert.equal(pick(b), pick(a));
});

test('settled 时钟不混用：mock=local/wall clock，ledger=ledger/null', () => {
  let wall = 1000;
  const mockSim = makeSim(() => (wall += 1));
  mockSim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  let guard = 0;
  while (mockSim.snapshot().deliveries.mock !== 1) {
    mockSim.update(100);
    if ((guard += 1) > 400) throw new Error('短活没结束');
  }
  const mockSettled = mockSim.toSave().world.settled[0];
  assert.equal(mockSettled?.source, 'mock');
  assert.equal(mockSettled?.completedAtClock, 'local');
  assert.equal(typeof mockSettled?.completedAt, 'number');

  const ledgerSim = makeSim();
  ledgerSim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  guard = 0;
  while (ledgerSim.snapshot().villager.mode !== 'working') {
    ledgerSim.update(100);
    if ((guard += 1) > 200) throw new Error('没走到铺子');
  }
  ledgerSim.enqueue({ kind: 'sync', tasks: [], finished: ['c_1'], connected: true });
  ledgerSim.update(16);
  const ledgerSettled = ledgerSim.toSave().world.settled.find((item) => item.id === 'c_1');
  assert.equal(ledgerSettled?.source, 'ledger');
  assert.equal(ledgerSettled?.completedAtClock, 'ledger');
  assert.equal(ledgerSettled?.completedAt, null, '台账时间缺失记 null，不拿本地时间顶替');
});

test('对账：已结算不重复、不复活；缺席且完整不复活；不完整则保留', () => {
  const sample = JSON.parse(
    readFileSync(new URL('../../docs/save-v1.sample.json', import.meta.url), 'utf8'),
  ) as WorldSaveV1;
  assertSaveV1(sample);
  const demo = 'c_demo000001';
  const settledLedger = 'c_000000000001';
  // 样例里有一个已超过工时的 operating 单：先跑一拍把计时器落定，再作为计数基准。
  const fresh = (): Simulation => {
    const sim = fromSave(sample);
    sim.update(16);
    return sim;
  };
  const baseline = fresh().snapshot().deliveries;

  // 1) finished 里是已结算 id：不重复交付/计数。
  const finished = fresh();
  const before = finished.snapshot().deliveries;
  assert.deepEqual(before, baseline);
  finished.enqueue({
    kind: 'sync',
    tasks: [],
    finished: [settledLedger],
    connected: true,
    complete: true,
    order: [],
  });
  finished.update(16);
  assert.deepEqual(finished.snapshot().deliveries, before);

  // 2) 已结算 id 以活动单回来：不复活。
  const revive = fresh();
  revive.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: settledLedger, kind: 'code', title: '不该复活', agentName: '甲' }],
    finished: [],
    connected: true,
    complete: true,
    order: [settledLedger],
  });
  revive.update(16);
  assert.equal(revive.snapshot().tasks.some((task) => task.id === settledLedger), false);
  assert.deepEqual(revive.snapshot().deliveries, before);

  // 3) 未结算单缺席且完整快照：丢弃，不复活。
  const dropped = fresh();
  assert.ok(dropped.snapshot().tasks.some((task) => task.id === demo));
  dropped.enqueue({
    kind: 'sync',
    tasks: [],
    finished: [],
    connected: true,
    complete: true,
    order: [],
  });
  dropped.update(16);
  assert.equal(dropped.snapshot().tasks.some((task) => task.id === demo), false);
  assert.deepEqual(dropped.snapshot().deliveries, before);

  // 4) 快照不完整：保留未对账单，不撤单。
  const kept = fresh();
  kept.enqueue({
    kind: 'sync',
    tasks: [],
    finished: [],
    connected: true,
    complete: false,
    order: [],
  });
  kept.update(16);
  assert.ok(kept.snapshot().tasks.some((task) => task.id === demo));

  // 5) 台账覆盖本地标题，对账完成后才恢复驱动。
  const overwritten = fresh();
  overwritten.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: demo, kind: 'writing', title: '对账后的新标题', agentName: '笔录官' }],
    finished: [],
    connected: true,
    complete: true,
    order: [demo],
  });
  overwritten.update(16);
  const task = overwritten.snapshot().tasks.find((item) => item.id === demo);
  assert.equal(task?.summary, '对账后的新标题');
  assert.equal(overwritten.snapshot().ledger.reconciled, true);
});

test('v1→v2 迁移样例：补 completedAtClock，不重复不丢', () => {
  const migrated = MIGRATIONS[1]?.({
    schemaVersion: 1,
    world: {
      settled: [{ id: 't0', source: 'mock', workerId: 'v1', completedAt: 123 }],
    },
  }) as { schemaVersion: number; world: { settled: { id: string; completedAtClock: string }[] } };
  assert.equal(migrated.schemaVersion, 2);
  assert.equal(migrated.world.settled.length, 1);
  assert.equal(migrated.world.settled[0]?.id, 't0');
  assert.equal(migrated.world.settled[0]?.completedAtClock, 'local');
});

// ---------------------------------------- v1 存档字段漂移防线（task-24）

test('toSave 剥离运行时字段：tasks[] 键集合=样例、done⊆settled、settled 键集合固定', () => {
  const sim = makeSim();
  // 台账单跑到 working 再按时收工：此时运行时的 TaskRecord 带 ledgerCompletedAt。
  sim.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_led', kind: 'code', title: '写爬虫', agentName: '甲',
      status: 'running', completedAt: 1_792_377_700,
    }],
    finished: [],
    connected: true,
  });
  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('没走到铺子');
  }
  // 关键点：先在中途存档——运行时任务正带着 ledgerCompletedAt，落盘必须剥离。
  const midSave = sim.toSave();
  const midTask = midSave.world.tasks.find((task) => task.id === 'c_led');
  assert.ok(midTask !== undefined, '中途存档里应有这条台账任务');
  assert.deepEqual(Object.keys(midTask).sort(), [...SAVE_TASK_KEYS].sort(),
    'tasks[] 键集合必须与 v1 冻结样例一致');
  assert.equal('ledgerCompletedAt' in midTask, false, '运行时字段 ledgerCompletedAt 不得落盘');
  assert.doesNotThrow(() => assertSaveV1(midSave), '中途存档必须通过 v1 嵌套校验');

  sim.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_led'],
    finishedAt: [{ commissionId: 'c_led', completedAt: 1_792_377_786 }],
    connected: true,
  });
  sim.update(16);
  while (sim.snapshot().villager.mode !== 'idle') {
    sim.update(100);
    if ((guard += 1) > 800) throw new Error('没回到 idle');
  }
  // 再交一单沙盒活，保证存档里既有 done 任务又有对应 settled。
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  while (sim.snapshot().deliveries.mock < 1) {
    sim.update(100);
    if ((guard += 1) > 1200) throw new Error('短活没结束');
  }

  const save = sim.toSave();
  assert.ok(save.world.settled.length >= 2, '台账与 Mock 各有一条 settled');
  for (const task of save.world.tasks) {
    assert.deepEqual(
      Object.keys(task).sort(),
      [...SAVE_TASK_KEYS].sort(),
      'tasks[] 键集合必须与 v1 冻结样例一致',
    );
    assert.equal('ledgerCompletedAt' in task, false, '运行时字段 ledgerCompletedAt 不得落盘');
  }
  const settledIds = new Set(save.world.settled.map((record) => record.id));
  for (const task of save.world.tasks.filter((item) => item.status === 'done')) {
    assert.ok(settledIds.has(task.id), `done 任务 ${task.id} 必须能在 settled 找到`);
  }
  for (const record of save.world.settled) {
    assert.deepEqual(Object.keys(record).sort(), [...SAVE_SETTLED_KEYS].sort());
  }
  assert.doesNotThrow(() => assertSaveV1(save), '运行时存档必须通过 v1 嵌套校验');
});

test('嵌套漂移防线：tasks[] 加字段 / done 缺 settled → assertSaveV1 必红，还原后恢复', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  sim.update(16);
  const save = sim.toSave();
  assertSaveV1(save);

  type MutableSave = WorldSaveV1 & { world: { tasks: Record<string, unknown>[] } };
  const withExtraKey = JSON.parse(JSON.stringify(save)) as MutableSave;
  withExtraKey.world.tasks[0].runtimeOnly = 1;
  assert.throws(() => assertSaveV1(withExtraKey), /键集合与 v1 样例不一致/);
  delete withExtraKey.world.tasks[0].runtimeOnly;
  assertSaveV1(withExtraKey);

  // done 必须能在 settled 找到：伪造一个「完成了但没结算」的任务。
  const missingSettled = JSON.parse(JSON.stringify(save)) as MutableSave & {
    world: { settled: unknown[] };
  };
  missingSettled.world.tasks[0].status = 'done';
  missingSettled.world.settled = [];
  assert.throws(() => assertSaveV1(missingSettled), /缺少 settled 记录/);
});

// P2：指纹重算不能与「空数组」冲突——还没发生过 tasks/settled 的正常存档必须继续通过。
test('P2 契约：fresh toSave（空数组）与运行中 toSave 都通过 assertSaveV1', () => {
  const fresh = makeSim();
  fresh.update(16);
  const save = fresh.toSave();
  assert.equal(save.world.settled.length, 0);
  assert.doesNotThrow(() => assertSaveV1(save), '空 tasks/settled 的正常存档不能判成坏档');

  const running = makeSim();
  running.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  running.update(16);
  assert.doesNotThrow(() => assertSaveV1(running.toSave()));
});

test('P2 契约：小人走路中（villagers[].path 非空）的存档仍通过 assertSaveV1', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  let guard = 0;
  while (!sim.toSave().world.villagers.some((villager) => villager.path.length > 0)) {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('没进入走路态');
  }
  const save = sim.toSave();
  assert.ok(save.world.villagers.some((villager) => villager.path.length > 0), '样例里没有的 path[].x/y 必须被允许');
  assert.doesNotThrow(() => assertSaveV1(save));
});

test('P2 契约：满数据存档（settled / constructions / 队列非空）通过 assertSaveV1', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  let guard = 0;
  while (sim.snapshot().deliveries.mock < 1) {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('短活没结束');
  }
  // 再开一栋，制造非空 constructions（施工中存档）。
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'workshop_plot#4' });
  sim.update(100);
  sim.update(100);
  const save = sim.toSave();
  assert.ok(save.world.settled.length > 0, '满数据档应有 settled');
  assert.ok(save.world.constructions.length > 0, '满数据档应有 constructions');
  assert.doesNotThrow(() => assertSaveV1(save));
});
