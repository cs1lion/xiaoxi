import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable, validateCoverage, BlueprintError } from '../src/world/blueprints.ts';
import {
  ALTAR,
  buildWorld,
  assertWorldInvariants,
  placeBlueprint,
  WorldSetupError,
  type BuiltWorld,
} from '../src/world/setup.ts';
import { Simulation, MAX_PENDING_DISPATCHES } from '../src/world/simulation.ts';
import { parseOracle } from '../src/world/oracle.ts';
import { TASK_PROFILES } from '../src/world/tasks.ts';
import { MILESTONE_POINTS, POINTS_PER_TASK } from '../src/world/faith.ts';
import { stepWeather, rainWeather, clearWeather, RAIN_DURATION_MS } from '../src/world/weather.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

const STEP = 100;

interface Rig {
  readonly sim: Simulation;
  readonly world: BuiltWorld;
  tick(ms: number): void;
  runUntil(predicate: () => boolean, limitMs?: number): void;
  state(): ReturnType<Simulation['snapshot']>;
}

function makeRig(): Rig {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
  const rig: Rig = {
    sim,
    world,
    tick(ms: number) {
      sim.update(ms);
    },
    runUntil(predicate, limitMs = 60_000) {
      let elapsed = 0;
      while (!predicate()) {
        if (elapsed >= limitMs) throw new Error(`超时：状态 ${JSON.stringify(sim.snapshot().villager)}`);
        sim.update(STEP);
        elapsed += STEP;
      }
    },
    state: () => sim.snapshot(),
  };
  return rig;
}

// ------------------------------------------------------------------ blueprints

test('生产蓝图表能解析，每种活 1–2 栋铺子，扩建样板与民居不占名额', () => {
  // 镇内三铺 + 郊野三铺 + 扩建样板一栋 + 镇内三屋 + 郊野两屋
  assert.equal(blueprints.size, 12);
  assert.deepEqual(validateCoverage(blueprints), []);
  assert.equal(blueprints.get('workshop_plot')?.boundTask, null);
  for (const kind of ['code', 'research', 'writing']) {
    const matches = [...blueprints.values()].filter((bp) => bp.boundTask === kind);
    assert.equal(matches.length, 2, `${kind} 应有镇内与郊野各一栋`);
  }
  // 民居的配额独立于铺子：加多少间屋子都不该改变「每种活两栋」这条预算。
  const homes = [...blueprints.values()].filter((bp) => bp.zone === 'living');
  assert.equal(homes.length, 5, '五间民居');
  for (const home of homes) {
    assert.equal(home.boundTask, null, `${home.id} 是民居，不该接活`);
  }
});

test('蓝图 id 与键名不符时拒绝加载', () => {
  assert.throws(
    () => parseBlueprintTable({ a: { ...raw.code_forge, id: 'b' } }),
    BlueprintError,
  );
});

test('门口落在占地内时拒绝加载', () => {
  assert.throws(
    () => parseBlueprintTable({ code_forge: { ...raw.code_forge, door: { x: 1, y: 1 } } }),
    BlueprintError,
  );
});

test('铺子与活对不上时拒绝加载', () => {
  assert.throws(
    () => parseBlueprintTable({ code_forge: { ...raw.code_forge, boundTask: 'research' } }),
    BlueprintError,
  );
});

test('接活的铺子开在住宅区时拒绝加载', () => {
  assert.throws(
    () => parseBlueprintTable({ code_forge: { ...raw.code_forge, zone: 'living' } }),
    BlueprintError,
  );
});

// ---------------------------------------------------------------------- world

test('世界能把三栋接活的铺子摆进各自分区，且都能从祭坛走到', () => {
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

test('造物主能落一栋新楼并铺一条从门口通往祭坛的小径', () => {
  const world = buildWorld(blueprints);
  const plot = blueprints.get('workshop_plot');
  assert.ok(plot);
  const spawned = placeBlueprint(world.grid, plot, 'plot#1', ALTAR);
  assert.ok(spawned);
  assert.ok(spawned.road.length > 0, '应该铺了路');
  assert.ok(spawned.road.every((cell) => world.grid.zoneOf(cell) !== 'altar'), '路不铺进祭坛');
});

test('空地用完时造物主返回 null，不留下半个建筑', () => {
  const world = buildWorld(blueprints);
  const plot = blueprints.get('workshop_plot');
  assert.ok(plot);

  let placed = 0;
  while (placeBlueprint(world.grid, plot, `plot#${placed + 1}`, ALTAR) !== null) {
    placed += 1;
    assert.ok(placed < 20, '分区容量应该有上限');
  }
  assert.ok(placed > 0, '至少能扩建一栋');

  const before = world.grid.countOccupied();
  assert.equal(placeBlueprint(world.grid, plot, 'overflow', ALTAR), null);
  assert.equal(world.grid.countOccupied(), before, '失败的落位不许改账本');
});

// --------------------------------------------------------------------- oracle

test('神谕按动词收敛，认不出的落 unknown', () => {
  assert.deepEqual(parseOracle('帮我写个爬虫'), {
    kind: 'dispatch',
    taskKind: 'code',
    summary: '帮我写个爬虫',
  });
  assert.equal(parseOracle('查一下天气').kind, 'dispatch');
  assert.equal(parseOracle('整理一下便笺').kind, 'dispatch');
  assert.equal(parseOracle('交付').kind, 'complete');
  assert.deepEqual(parseOracle('下雨了'), { kind: 'weather', weather: 'rain' });
  assert.deepEqual(parseOracle('放晴吧'), { kind: 'weather', weather: 'clear' });
  assert.equal(parseOracle('今天真好看').kind, 'unknown');
  assert.equal(parseOracle('   ').kind, 'unknown');
  assert.equal(parseOracle('撤销').kind, 'cancelPending');
  assert.equal(parseOracle('取消排队').kind, 'cancelPending');
});

test('「顺手」走原地短活，不抢长活的活', () => {
  const quick = parseOracle('顺手查一下今天几号');
  assert.equal(quick.kind, 'dispatch');
  assert.equal(quick.kind === 'dispatch' && quick.taskKind, 'quick');
  const long = parseOracle('查一下季度资料');
  assert.equal(long.kind === 'dispatch' && long.taskKind, 'research');
});

test('神谕里出现答复也只当普通文本，不触发任何回答', () => {
  assert.equal(parseOracle('先按短句还是完整句式？答复：短句').kind, 'unknown');
  assert.equal(parseOracle('回答：选 A').kind, 'unknown');
});

test('句中顺带提到天气：提问不改世界，查数据照旧派研究活', () => {
  assert.equal(parseOracle('明天下雨吗').kind, 'unknown');
  assert.equal(parseOracle('昨天放晴了吗').kind, 'unknown');
  const lookup = parseOracle('查一下下雨的历史数据');
  assert.equal(lookup.kind, 'dispatch');
  assert.equal(lookup.kind === 'dispatch' && lookup.taskKind, 'research');
});

test('句中顺带提到完成不收单，写报告照旧派写活', () => {
  assert.equal(parseOracle('把任务标记为完成').kind, 'unknown');
  const report = parseOracle('帮我写一份完成报告');
  assert.equal(report.kind, 'dispatch');
  assert.equal(report.kind === 'dispatch' && report.taskKind, 'writing');
  assert.equal(parseOracle('完成').kind, 'complete');
  assert.equal(parseOracle('交付').kind, 'complete');
});

test('短活必须打头：句中顺带提到「简单」抢不走长活', () => {
  const mid = parseOracle('查资料这事很简单');
  assert.equal(mid.kind, 'dispatch');
  assert.equal(mid.kind === 'dispatch' && mid.taskKind, 'research');
  const quick = parseOracle('顺手查一下今天几号');
  assert.equal(quick.kind === 'dispatch' && quick.taskKind, 'quick');
});

test('句中顺带提到撤销不动队列，动词打头才收', () => {
  assert.equal(parseOracle('取消排队').kind, 'cancelPending');
  assert.equal(parseOracle('先别取消排队').kind, 'unknown');
  assert.equal(parseOracle('撤销刚才那条').kind, 'cancelPending');
});

test('英文命中词同样只认打头，问句落 unknown', () => {
  assert.deepEqual(parseOracle('rain'), { kind: 'weather', weather: 'rain' });
  assert.equal(parseOracle('will it rain tomorrow').kind, 'unknown');
  assert.equal(parseOracle('DONE').kind, 'complete');
  const search = parseOracle('帮我 search 一下');
  assert.equal(search.kind, 'dispatch');
  assert.equal(search.kind === 'dispatch' && search.taskKind, 'research');
});

// ------------------------------------------------------------------- everyday

test('没人管的时候她也过日子：离开祭坛再回来', () => {
  const rig = makeRig();
  rig.runUntil(() => rig.state().villager.routine !== null, 5000);
  const first = rig.state().villager;
  assert.notEqual(first.routine, null);
  const start = first.pos;
  rig.tick(4000);
  const later = rig.state().villager.pos;
  assert.ok(
    Math.hypot(later.x - start.x, later.y - start.y) > 0.5 || rig.state().villager.routine !== null,
    '她应该换过地方',
  );
});

test('日常不会自己走进铺子，铺子只在接活时运转', () => {
  const rig = makeRig();
  for (let i = 0; i < 200; i += 1) rig.tick(STEP);
  const state = rig.state();
  assert.equal(state.villager.mode, 'idle');
  assert.ok(state.workstations.every((w) => w.activity.kind === 'idle'));
});

// ---------------------------------------------------------------------- work

test('神谕下达后：行礼 → 走进铺子 → 铺子开工', () => {
  const rig = makeRig();
  const forge = rig.world.buildings.find((b) => b.boundTask === 'code');
  assert.ok(forge);

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.equal(rig.state().villager.mode, 'saluting');

  rig.tick(800);
  assert.equal(rig.state().villager.mode, 'toWork');

  rig.runUntil(() => rig.state().villager.mode === 'working');
  const working = rig.state();
  assert.equal(working.villager.visible, false, '进屋干活时人不该在门外');
  const activity = working.workstations.find((w) => w.buildingId === forge.instanceId)?.activity;
  assert.equal(activity?.kind, 'operating');
});

test('活干完之后铺子冷却，人走到门口交付，再回到祭坛', () => {
  const rig = makeRig();
  const forge = rig.world.buildings.find((b) => b.boundTask === 'code');
  assert.ok(forge);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });

  rig.runUntil(() => rig.state().villager.mode === 'delivering');
  const delivered = rig.state();
  assert.equal(delivered.villager.visible, true);
  assert.equal(delivered.tasks[0].status, 'done');
  assert.equal(
    delivered.workstations.find((w) => w.buildingId === forge.instanceId)?.activity.kind,
    'cooling',
  );

  rig.runUntil(() => rig.state().villager.mode === 'idle');
  assert.equal(rig.state().villager.taskId, null);
});

test('短活原地办，不进铺子', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  rig.sim.update(16);
  const starting = rig.state();
  assert.equal(starting.villager.mode, 'working');
  assert.ok(starting.workstations.every((w) => w.activity.kind === 'idle'), '没有铺子开工');

  rig.runUntil(() => rig.state().villager.mode === 'idle', TASK_PROFILES.quick.estimateMs + 2000);
  assert.equal(rig.state().tasks[0].status, 'done');
});

// 两个角色都忙时，新神谕才进等待队列。
function makeBothBusy(rig: Rig): void {
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  rig.sim.update(16);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  rig.sim.update(16);
  rig.runUntil(() => rig.state().villagers.every((item) => item.mode === 'working'), 20_000);
}

test('两个角色各有一条独立作息，派活只叫一个', () => {
  const rig = makeRig();
  rig.tick(3400);
  const idle = rig.state();
  assert.equal(idle.villagers.length, 2, '起步两个角色');
  assert.deepEqual(idle.villagers.map((item) => item.name), ['莞尔', '阿雾']);
  assert.deepEqual(idle.villagers.map((item) => item.id), ['v0', 'v1']);
  assert.ok(idle.villagers.every((item) => item.routine !== null), '两个人各自在过日子');

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  rig.sim.update(16);
  const state = rig.state();
  assert.equal(state.tasks.length, 1, '一次派活只开一单');
  assert.equal(state.villagers.filter((item) => item.mode === 'saluting').length, 1, '只叫一个人');
  const worker = state.villagers.find((item) => item.taskId !== null);
  assert.equal(state.tasks[0]?.workerId, worker?.id, '交付归属记在实际干活的角色上');
});

test('角色数量可配置：传 3 个名字就是 3 个人', () => {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '甲',
    villagerNames: ['甲', '乙', '丙'],
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      return blueprint === undefined
        ? null
        : placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
  sim.update(16);
  const state = sim.snapshot();
  assert.equal(state.villagers.length, 3);
  assert.deepEqual(state.villagers.map((item) => item.id), ['v0', 'v1', 'v2']);
  assert.equal(state.villager.name, '甲', 'villager 兼容字段仍指向主角色');
});

test('一人在工坊、一人在祭坛；第二单交给另一个空闲角色', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  rig.runUntil(() => rig.state().villager.mode === 'working', 20_000);
  let state = rig.state();
  assert.equal(state.villagers[0]?.mode, 'working');
  assert.equal(state.villagers[1]?.mode, 'idle', '另一个人还在祭坛');
  assert.equal(state.villagers[1]?.taskId, null);

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  rig.sim.update(16);
  rig.runUntil(
    () => rig.state().villagers.every((item) => item.mode === 'working'),
    20_000,
  );
  state = rig.state();
  const workers = state.villagers.map((item) => item.taskId);
  assert.notEqual(workers[0], workers[1], '两个人各干各的，不串任务');
  const buildings = state.tasks
    .filter((item) => item.status === 'in_progress')
    .map((item) => item.buildingId);
  assert.equal(new Set(buildings).size, 2, '两单在不同铺子');
});

test('两个角色都忙时新神谕进队列，交付后自动开工', () => {
  const rig = makeRig();
  makeBothBusy(rig);
  assert.equal(rig.state().tasks.length, 2);

  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'writing', summary: '整理便笺' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true, '都忙时不拒单');
  assert.ok(result[0].message.includes('已排队（第 1 位）'), result[0].message);
  assert.equal(rig.state().pending.length, 1, '排队单在快照里可见');
  assert.equal(rig.state().tasks.length, 2, '排队时还不建 TaskRecord');

  // 交付主角色那单；队列在它空下来后自动派发。
  rig.sim.enqueue({ kind: 'complete' });
  rig.sim.update(16);
  rig.runUntil(
    () => rig.state().tasks[2]?.status === 'in_progress',
    20_000,
  );
  const state = rig.state();
  assert.equal(state.pending.length, 0, '出队后队列清空');
  assert.equal(state.tasks[2]?.kind, 'writing', '按原任务类型开工');
  assert.equal(state.tasks[2]?.status, 'in_progress');
});

test('等待队列按角色分：每人 3 条，全部占满才拒单', () => {
  const rig = makeRig();
  makeBothBusy(rig);

  for (let i = 1; i <= MAX_PENDING_DISPATCHES * 2; i += 1) {
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: `排队 ${i}` });
  }
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '超出的那条' });
  const results = rig.sim.update(16);
  assert.ok(
    results.slice(0, MAX_PENDING_DISPATCHES * 2).every((item) => item.ok),
    '两个角色各 3 条都排得下',
  );
  assert.equal(results[MAX_PENDING_DISPATCHES * 2]?.ok, false, '全满才拒');
  assert.ok(results[MAX_PENDING_DISPATCHES * 2]?.message.includes('队列也满了'));
  const pending = rig.state().pending;
  assert.equal(pending.length, MAX_PENDING_DISPATCHES * 2);
  assert.deepEqual(
    [...new Set(pending.map((item) => item.villagerId))].sort(),
    ['v0', 'v1'],
    '队列按角色分',
  );
  assert.equal(rig.state().tasks.length, 2, '被拒的单没有进台账');
});

test('撤销排队：省略 id 撤最后一条，可按 id 撤，空了再撤被拒', () => {
  const rig = makeRig();
  makeBothBusy(rig);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '第一条' });
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '第二条' });
  rig.sim.update(16);
  const queued = rig.state().pending;
  assert.deepEqual(queued.map((item) => item.summary), ['第一条', '第二条']);
  assert.deepEqual(queued.map((item) => item.villagerId), ['v0', 'v1']);
  assert.equal(queued[0]?.source, 'oracle');

  // 撤销最后一条（省略 id）：全局最后一条在 v1 队列里。
  rig.sim.enqueue({ kind: 'cancelPending' });
  assert.equal(rig.sim.update(16)[0].ok, true);
  assert.deepEqual(rig.state().pending.map((item) => item.summary), ['第一条']);

  // 按 id 撤销剩下那条。
  rig.sim.enqueue({ kind: 'cancelPending', id: queued[0]?.id });
  assert.equal(rig.sim.update(16)[0].ok, true);
  assert.equal(rig.state().pending.length, 0);

  // 队列空了再撤：拒绝，但世界不动。
  rig.sim.enqueue({ kind: 'cancelPending' });
  assert.equal(rig.sim.update(16)[0].ok, false);
  assert.equal(rig.state().tasks.length, 2, '撤销不碰正在干的活');
});

test('交付归属到实际干活的角色，交付统计不串', () => {
  const rig = makeRig();
  makeBothBusy(rig);
  assert.equal(rig.state().deliveries.mock, 0);

  rig.sim.enqueue({ kind: 'complete' });
  rig.sim.update(16);
  let state = rig.state();
  assert.equal(state.villagers[0]?.mode, 'delivering', '主角色那单先交付');
  assert.equal(state.villagers[1]?.mode, 'working', '另一人还在工坊');
  assert.equal(state.deliveries.mock, 1);

  rig.sim.enqueue({ kind: 'complete' });
  rig.sim.update(16);
  state = rig.state();
  assert.equal(state.villagers[1]?.mode, 'delivering', '第二单由它自己的角色交付');
  assert.equal(state.deliveries.mock, 2);
});

test('固定步长回放：同样的命令与步长，快照完全一致', () => {
  const script = (rig: Rig): void => {
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
    rig.sim.update(16);
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
    rig.sim.update(16);
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'writing', summary: '整理便笺' });
    rig.sim.update(100);
    rig.tick(800);
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
    rig.sim.update(100);
    rig.sim.enqueue({ kind: 'cancelPending' });
    rig.sim.update(100);
  };
  const a = makeRig();
  const b = makeRig();
  script(a);
  script(b);
  assert.deepEqual(a.state(), b.state(), '同命令同步长必须逐字段一致');
});

test('台账优先：空闲时先派台账单，oracle 排队单继续等', () => {
  const rig = makeRig();
  makeBothBusy(rig);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '排队的 oracle 单' });
  rig.sim.update(16);
  assert.equal(rig.state().pending.length, 1);

  // 主角色那单交付后：台账有单在册，先派台账的。
  rig.sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_ledger', kind: 'writing', title: '台账的活', agentName: '甲' }],
    finished: [],
    connected: true,
    order: ['c_ledger'],
  });
  rig.sim.enqueue({ kind: 'complete' });
  rig.sim.update(16);
  rig.runUntil(
    () =>
      rig.state().villagers[0]?.taskId === 'c_ledger' &&
      (rig.state().villagers[0]?.mode === 'saluting' || rig.state().villagers[0]?.mode === 'toWork'),
    20_000,
  );
  const state = rig.state();
  assert.equal(state.villagers[0]?.taskId, 'c_ledger', '台账单先派给空出来的人');
  assert.equal(state.pending.length, 1, 'oracle 排队单还在等');
});

test('行礼还没出发时台账收工：取消行礼、清路线，不残留旧目标', () => {
  const rig = makeRig();
  rig.sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'writing', title: '台账的活', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  rig.sim.update(16);
  assert.equal(rig.state().villager.mode, 'saluting');

  // 还在行礼（未出发）时台账收工：直接转交付，不能继续行礼/走旧路线。
  rig.sim.enqueue({ kind: 'sync', tasks: [], finished: ['c_1'], connected: true });
  rig.sim.update(16);
  const state = rig.state();
  assert.equal(state.villager.mode, 'delivering', '行礼被取消，直接交付');
  assert.equal(state.villager.bubble, '任务交付！');

  // 交付完回祭坛；再派一单走新路线，不残留上一单的 pendingRoute。
  rig.runUntil(() => rig.state().villager.mode === 'idle', 20_000);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '新活' });
  rig.sim.update(16);
  rig.runUntil(() => rig.state().villager.mode === 'working', 20_000);
  assert.equal(rig.state().tasks.at(-1)?.summary, '新活');
});

test('行礼还没出发时台账撤单：回到空闲，旧行礼计时器不再推进', () => {
  const rig = makeRig();
  rig.sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'writing', title: '台账的活', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  rig.sim.update(16);
  assert.equal(rig.state().villager.mode, 'saluting');

  rig.sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true });
  rig.sim.update(16);
  assert.equal(rig.state().villager.mode, 'idle');
  assert.equal(rig.state().tasks.length, 0);

  // 已经过了旧的 saluteUntil：不能再把她推进 toWork。
  rig.tick(1000);
  assert.equal(rig.state().villager.mode, 'idle', '旧行礼计时器已取消');
});

test('空手时交付被拒绝', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'complete' });
  assert.equal(rig.sim.update(16)[0].ok, false);
});

// ------------------------------------------------------- 人工交付与离线 Mock（D3）

test('台账单不能人工完成：完成权在台账，状态不变', () => {
  const rig = makeRig();
  rig.sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  rig.runUntil(() => rig.state().villager.mode === 'working');
  assert.equal(rig.state().ledger.mock, false, '台账在线');

  rig.sim.enqueue({ kind: 'complete' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, false);
  assert.ok(result[0].message.includes('完成权在台账'), result[0].message);

  const state = rig.state();
  assert.equal(state.villager.mode, 'working', '被拒后台账单继续跑');
  assert.equal(state.tasks[0].status, 'in_progress');
  assert.equal(state.deliveries.ledger, 0);
  assert.equal(state.deliveries.mock, 0);
});

test('沙盒单可以人工收尾，回执标明离线 Mock 且不计入台账', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写个脚本' });
  rig.runUntil(() => rig.state().villager.mode === 'working');

  rig.sim.enqueue({ kind: 'complete' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.ok(result[0].message.includes('离线 Mock'), result[0].message);
  assert.ok(result[0].message.includes('不计入台账'), result[0].message);

  const state = rig.state();
  assert.equal(state.villager.mode, 'delivering');
  assert.equal(state.deliveries.mock, 1, '沙盒交付计入 Mock');
  assert.equal(state.deliveries.ledger, 0, '不冒充台账交付');
});

test('人工交付可指定角色 id 或名字，不串任务与统计', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写个脚本' });
  rig.sim.update(16);
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  rig.sim.update(16);
  rig.runUntil(() => rig.state().villagers.every((item) => item.mode === 'working'));
  assert.equal(rig.state().villagers[0]?.taskId, 't1');
  assert.equal(rig.state().villagers[1]?.taskId, 't2');

  // 指定 v1：只收助手那单，主角色继续干。
  rig.sim.enqueue({ kind: 'complete', target: 'v1' });
  let result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.ok(result[0].message.includes('阿雾'), result[0].message);
  let state = rig.state();
  assert.equal(state.villagers[1]?.mode, 'delivering');
  assert.equal(state.villagers[0]?.mode, 'working', '不串到另一个人');
  assert.equal(state.tasks[1]?.status, 'done');
  assert.equal(state.tasks[0]?.status, 'in_progress');
  assert.deepEqual(
    state.deliveriesByVillager.map((item) => [item.name, item.mock]),
    [
      ['莞尔', 0],
      ['阿雾', 1],
    ],
  );

  // 用名字指定 v0。
  rig.sim.enqueue({ kind: 'complete', target: '莞尔' });
  result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.ok(result[0].message.includes('莞尔'), result[0].message);
  state = rig.state();
  assert.equal(state.villagers[0]?.mode, 'delivering');
  assert.equal(state.tasks[0]?.status, 'done');
  assert.deepEqual(
    state.deliveriesByVillager.map((item) => [item.name, item.mock]),
    [
      ['莞尔', 1],
      ['阿雾', 1],
    ],
  );
});

test('人工交付指定未知角色：明确拒绝，世界不动', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写个脚本' });
  rig.runUntil(() => rig.state().villager.mode === 'working');
  const before = rig.state();
  for (const target of ['v9', '不存在的人']) {
    rig.sim.enqueue({ kind: 'complete', target });
    const result = rig.sim.update(16);
    assert.equal(result[0].ok, false);
    assert.ok(result[0].message.includes('没有叫'), result[0].message);
  }
  const after = rig.state();
  assert.equal(after.villager.mode, 'working');
  assert.equal(after.tasks[0]?.status, 'in_progress');
  assert.deepEqual(after.deliveries, before.deliveries);
});

test('指定角色也不能绕过台账完成权', () => {
  const rig = makeRig();
  rig.sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  rig.runUntil(() => rig.state().villager.mode === 'working');
  rig.sim.enqueue({ kind: 'complete', target: 'v0' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, false);
  assert.ok(result[0].message.includes('完成权在台账'), result[0].message);
  assert.equal(rig.state().tasks[0]?.status, 'in_progress');
  assert.equal(rig.state().deliveries.ledger, 0);
});

test('快照的离线 Mock 标记随台账连接变化', () => {
  const rig = makeRig();
  assert.equal(rig.state().ledger.mock, true, '没接台账就是离线 Mock');

  rig.sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true });
  rig.sim.update(16);
  assert.equal(rig.state().ledger.mock, false, '连上后不再是 Mock');

  rig.sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: false });
  rig.sim.update(16);
  assert.equal(rig.state().ledger.mock, true, '断线后标回离线 Mock');
});

test('每条命令只在 update 开头结算一次', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写一个脚本' });
  assert.equal(rig.state().villager.mode, 'idle', '入队瞬间世界不动');
  rig.sim.update(16);
  assert.equal(rig.state().villager.mode, 'saluting');
});

// ------------------------------------------------------------------- weather

test('下雨：户外的人当场改主意去躲雨，天晴后恢复日常', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'setWeather', weather: 'rain' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.equal(rig.state().weather, 'rain');
  assert.equal(rig.state().villager.mode, 'sheltering');
  assert.equal(rig.state().villager.umbrella, true);

  rig.runUntil(() => rig.state().villager.mode === 'idle', 20_000);
  assert.equal(rig.state().villager.routine, '在屋檐下看雨');

  rig.sim.enqueue({ kind: 'setWeather', weather: 'clear' });
  rig.sim.update(16);
  assert.equal(rig.state().weather, 'clear');
});

test('雨会自己停，不需要第二条神谕', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'setWeather', weather: 'rain' });
  rig.sim.update(16);
  rig.runUntil(() => rig.state().weather === 'clear', RAIN_DURATION_MS + 5000);
  assert.equal(rig.state().weather, 'clear');
});

test('天气浓淡是渐变的，不会一帧切完', () => {
  let state = rainWeather(0);
  state = stepWeather(state, 100);
  assert.ok(state.intensity > 0 && state.intensity < 1, '刚下雨时还没拉满');
  assert.equal(stepWeather(clearWeather(), 100).intensity, 0);
});

// ----------------------------------------------------------------------- faith

test('交付攒够里程碑，世界里多一件东西，且不重复', () => {
  const rig = makeRig();
  const tasks = Math.ceil(MILESTONE_POINTS / POINTS_PER_TASK);
  for (let i = 0; i < tasks; i += 1) {
    rig.sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: `小事 ${i}` });
    rig.runUntil(() => rig.state().villager.mode === 'working', 5000);
    rig.runUntil(() => rig.state().villager.mode === 'idle', 10_000);
  }
  const state = rig.state();
  assert.equal(state.faith.milestones, 1);
  assert.equal(state.props.length, 1);
  assert.ok(state.props[0].id.startsWith('prop-'));
});

// ------------------------------------------------------------------ creator

test('造物主能扩建一栋新楼，施工期间它不接活', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'plot#1' });
  const result = rig.sim.update(16);
  assert.equal(result[0].ok, true);
  assert.equal(rig.state().buildings.length, 4);
  assert.equal(rig.state().constructions.length, 1);
  assert.ok(rig.state().roads.length > 0, '新楼带出一条路');

  rig.runUntil(() => rig.state().constructions.length === 0, 10_000);
  assert.equal(rig.state().constructions.length, 0);
});

test('同一种铺子不能建第二栋', () => {
  const rig = makeRig();
  rig.sim.enqueue({ kind: 'build', prefabId: 'code_forge', instanceId: 'forge#2' });
  assert.equal(rig.sim.update(16)[0].ok, false);
  assert.equal(rig.state().buildings.length, 3);
});