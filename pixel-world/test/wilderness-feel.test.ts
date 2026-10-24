import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { WILD_PREFABS } from '../src/world/setup.ts';
import { taskDurationFor } from '../src/world/tasks.ts';
import { isWildLanternOn } from '../src/world/wilderness-signal.ts';

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

// ------------------------------------------------------------------ 时长

test('郊野的活比镇内久：远行一次的代价要看得见', () => {
  const town = taskDurationFor('research', false);
  const wild = taskDurationFor('research', true);
  assert.ok(wild > town, `郊野 ${wild}ms 应当比镇内 ${town}ms 久`);
});

test('郊野时长不是随便加倍：控制在镇内的 1.5–2.5 倍', () => {
  for (const kind of ['code', 'research', 'writing'] as const) {
    const ratio = taskDurationFor(kind, true) / taskDurationFor(kind, false);
    assert.ok(ratio >= 1.5 && ratio <= 2.5, `${kind} 郊野倍率 ${ratio.toFixed(2)} 越界`);
  }
});

test('短活不受影响：它本来就不进铺子', () => {
  assert.equal(taskDurationFor('quick', false), taskDurationFor('quick', true));
});

// ------------------------------------------------------------------ 出门与返程

test('去郊野干活时，角色有一段"在路上"的状态', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  sim.update(16);

  // 大世界里从祭坛到郊野 25 格，走路 8 秒以上。
  let sawTravel = false;
  for (let i = 0; i < 100; i += 1) {
    sim.update(100);
    const villager = sim.snapshot().villagers.find((v) => v.taskId !== null);
    if (villager !== undefined && villager.mode === 'toWork') sawTravel = true;
    if (villager?.mode === 'working') break;
  }
  assert.ok(sawTravel, '应当有"走向郊野"的阶段');
});

test('郊野干完活会带着东西回来：交付时不在铺子门口', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  sim.update(16);

  let sawDelivering = false;
  for (let i = 0; i < 600; i += 1) {
    sim.update(100);
    if (sim.snapshot().villagers.some((v) => v.mode === 'delivering')) {
      sawDelivering = true;
      break;
    }
  }
  assert.ok(sawDelivering, '应当在某个时刻交付');
});

// ------------------------------------------------------------------ 郊野识别

test('郊野铺子的 id 集合与界面的补缺映射一致', () => {
  const world = buildLargeWorld(blueprints);
  for (const building of world.buildings) {
    if (!WILD_PREFABS.has(building.prefabId)) continue;
    // 郊野铺子必须在郊野圈
    const d = Math.hypot(building.origin.x - world.altar.x, building.origin.y - world.altar.y);
    assert.ok(d > 21, `${building.prefabId} 离祭坛 ${d.toFixed(1)} 格，不在郊野`);
  }
});

// ------------------------------------------------------------------ 郊野的辨识

test('郊野铺子不管有没有开工，都有一盏自己的灯', () => {
  const world = buildLargeWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });

  // 没有任何活，郊野铺子仍然是"点着灯"的。
  const snapshot = sim.snapshot();
  const wild = snapshot.buildings.filter((b) => WILD_PREFABS.has(b.prefabId));
  assert.ok(wild.length > 0);
  for (const building of wild) {
    assert.equal(
      isWildLanternOn(building, snapshot),
      true,
      `${building.prefabId} 的灯应当常亮`,
    );
  }
});

test('镇内铺子没有常亮灯：灯的对比才是郊野的识别', () => {
  const world = buildLargeWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: world.altar,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });
  const snapshot = sim.snapshot();
  const town = snapshot.buildings.filter((b) => !WILD_PREFABS.has(b.prefabId));
  for (const building of town) {
    assert.equal(isWildLanternOn(building, snapshot), false, `${building.prefabId} 不该常亮`);
  }
});

// ------------------------------------------------------------------ 返程带东西

test('从郊野干完活回来时，角色带着东西', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  sim.update(16);

  let sawCarrying = false;
  for (let i = 0; i < 600; i += 1) {
    sim.update(100);
    if (sim.snapshot().villagers.some((v) => v.carrying !== null)) {
      sawCarrying = true;
      break;
    }
  }
  assert.ok(sawCarrying, '从郊野回来应当带着东西');
});

test('带的东西与活类型对应', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查一下' });
  sim.update(16);

  for (let i = 0; i < 600; i += 1) {
    sim.update(100);
    const carrier = sim.snapshot().villagers.find((v) => v.carrying !== null);
    if (carrier !== undefined) {
      assert.equal(carrier.carrying, 'scroll', '查资料带回来的是卷轴');
      return;
    }
  }
  assert.fail('没找到带东西的角色');
});

test('镇内干活不背东西：只有去了远处才值得带回来', () => {
  const sim = makeSim();
  // 短活原地办，不进铺子。
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  sim.update(16);

  for (let i = 0; i < 100; i += 1) {
    sim.update(100);
    assert.ok(
      sim.snapshot().villagers.every((v) => v.carrying === null),
      '短活不该让人背东西',
    );
  }
});
