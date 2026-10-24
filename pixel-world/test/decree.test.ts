import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { awaitingDecree } from '../src/world/decree.ts';

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

/** 注入一条 blocked 的台账督办。 */
function blockOne(sim: Simulation, question = '要删掉 ./temp_build 吗？'): string {
  sim.enqueue({
    kind: 'sync',
    tasks: [
      {
        commissionId: 'c_block',
        kind: 'code',
        title: '清理临时目录',
        agentName: '甲',
        status: 'blocked',
        blockedQuestion: question,
      },
    ],
    finished: [],
    connected: true,
  });
  sim.update(16);
  return 'c_block';
}

// ------------------------------------------------------------------ 识别

test('没有 blocked 单时不产生急件', () => {
  const sim = makeSim();
  assert.equal(awaitingDecree(sim.snapshot()), null);
});

test('有 blocked 单时产生一张急件，带完整问题原文', () => {
  const sim = makeSim();
  const id = blockOne(sim, '要删掉 ./temp_build 吗？');
  const decree = awaitingDecree(sim.snapshot());
  assert.ok(decree !== null);
  assert.equal(decree.commissionId, id);
  assert.equal(decree.question, '要删掉 ./temp_build 吗？', '必须是完整原文，不能截断');
});

test('多个 blocked 单时只取最早的一个', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c1', kind: 'code', title: '甲单', agentName: '甲', status: 'blocked', blockedQuestion: '问题一' },
      { commissionId: 'c2', kind: 'research', title: '乙单', agentName: '乙', status: 'blocked', blockedQuestion: '问题二' },
    ],
    finished: [],
    connected: true,
  });
  sim.update(16);
  const decree = awaitingDecree(sim.snapshot());
  assert.equal(decree?.commissionId, 'c1', '一次只呈一份急件，先来先呈');
});

// ------------------------------------------------------------------ 状态变化

test('blocked 转 running 后急件消失', () => {
  const sim = makeSim();
  blockOne(sim);
  assert.ok(awaitingDecree(sim.snapshot()) !== null);

  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c_block', kind: 'code', title: '清理临时目录', agentName: '甲', status: 'running' },
    ],
    finished: [],
    connected: true,
  });
  sim.update(16);
  assert.equal(awaitingDecree(sim.snapshot()), null, '问题已答，急件收回');
});

test('台账离线时急件保留：那是已经发生的事，不会因为断线消失', () => {
  const sim = makeSim();
  blockOne(sim);
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: false });
  sim.update(16);
  assert.ok(awaitingDecree(sim.snapshot()) !== null);
});

// ------------------------------------------------------------------ 不能替人回答

test('世界不会自己回答：blocked 单在快照里仍是 blocked', () => {
  const sim = makeSim();
  const id = blockOne(sim);
  for (let i = 0; i < 50; i += 1) sim.update(100);

  const task = sim.snapshot().tasks.find((item) => item.id === id);
  assert.ok(task !== undefined);
  assert.notEqual(task.status, 'done', '世界不能替人做决定');
  assert.ok(task.blockedQuestion !== null, '问题必须一直在，直到台账改状态');
});

// ------------------------------------------------------------------ 具身化

test('有 blocked 单时，有人会跑去祭坛举急件', () => {
  const sim = makeSim();
  blockOne(sim);

  // 跑几拍让传令官出发并走到。
  let sawBearing = false;
  for (let i = 0; i < 200; i += 1) {
    sim.update(100);
    if (sim.snapshot().villagers.some((v) => v.mode === 'bearingDecree')) {
      sawBearing = true;
      break;
    }
  }
  assert.ok(sawBearing, '应当有人举着急件跑向祭坛');
});

test('走到祭坛后停下举牌，不自己回去', () => {
  const sim = makeSim();
  blockOne(sim);
  for (let i = 0; i < 200; i += 1) sim.update(100);

  // 判据是「有人站在祭坛旁」，不是 bubble：气泡 4 秒后就过期，那是正常的。
  const atAltar = (v: { pos: { x: number; y: number } }): boolean =>
    Math.abs(v.pos.x - ALTAR.x) < 1.5 && Math.abs(v.pos.y - ALTAR.y) < 1.5;
  const bearer = sim.snapshot().villagers.find(atAltar);
  assert.ok(bearer !== undefined, '应当有人已经走到祭坛');

  // 再跑一阵，仍站在那儿——问题没答就不收。
  for (let i = 0; i < 100; i += 1) sim.update(100);
  const still = sim.snapshot().villagers.find((v) => v.id === bearer.id);
  assert.ok(still !== undefined && atAltar(still), '问题没答之前传令官一直守在祭坛');
  assert.notEqual(still.mode, 'bearingDecree', '到了就不该继续"跑"的状态');
});

test('问题被回答后，传令官不再举牌', () => {
  const sim = makeSim();
  blockOne(sim);
  for (let i = 0; i < 200; i += 1) sim.update(100);

  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c_block', kind: 'code', title: '清理临时目录', agentName: '甲', status: 'running' },
    ],
    finished: [],
    connected: true,
  });
  for (let i = 0; i < 60; i += 1) sim.update(100);

  // 答案给了之后，不该再有人被派去送件。
  const atAltar = (v: { pos: { x: number; y: number } }): boolean =>
    Math.abs(v.pos.x - ALTAR.x) < 1.5 && Math.abs(v.pos.y - ALTAR.y) < 1.5;
  assert.equal(
    sim.snapshot().villagers.filter((v) => v.mode === 'bearingDecree').length,
    0,
    '答案已给，不该再有人跑去送件',
  );
  void atAltar;
});

test('没有人闲时不派传令官：不打断正在干的活', () => {
  const sim = makeSim();
  // 让两个角色都忙起来。
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查资料' });
  sim.update(16);
  blockOne(sim);
  sim.update(16);

  assert.equal(
    sim.snapshot().villagers.filter((v) => v.mode === 'bearingDecree').length,
    0,
    '两人都在干活时不该有人丢下活去送件',
  );
});
