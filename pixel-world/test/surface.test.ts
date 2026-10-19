import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { composeReport, composeDiary } from '../src/world/report.ts';
import { syllablePlan } from '../src/audio/animalese.ts';
import { Bridge, toWorldCommand } from '../src/world/bridge.ts';
import type { WorldEvent } from '../src/world/types.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

function makeSim(): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      return blueprint === undefined
        ? null
        : placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
}

// -------------------------------------------------------------------- report

test('还没交付时，日报说实话，不用填充句', () => {
  const report = composeReport(makeSim().snapshot());
  assert.equal(report.headline, '世界刚醒来');
  assert.ok(report.lines[0].includes('还没有交付'));
  assert.ok(report.lines.length <= 3);
});

test('交付一单之后，日报写的是这单本身', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  let guard = 0;
  while (sim.snapshot().tasks[0]?.status !== 'done') {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('短活没结束');
  }
  const report = composeReport(sim.snapshot());
  assert.ok(report.lines.some((line) => line.includes('交付 1 单')));
  assert.ok(report.lines.some((line) => line.includes('顺手看一眼')));
  assert.equal(report.headline, '今日有交付', '没攒满里程碑就不能写"神恩"');
});

test('日报按角色与来源分账：Mock 与台账分开，不合并计数', () => {
  const sim = makeSim();
  // 台账单：完成权在台账，由 sync 收尾，归实际接单的角色。
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 200) throw new Error('没走到铺子');
  }
  sim.enqueue({ kind: 'sync', tasks: [], finished: ['c_1'], connected: true });
  sim.update(16);
  assert.equal(sim.snapshot().deliveries.ledger, 1);
  guard = 0;
  while (sim.snapshot().villager.mode !== 'idle') {
    sim.update(100);
    if ((guard += 1) > 200) throw new Error('台账单没走完交付');
  }

  // 沙盒单：短活自己结束，计入 Mock。
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  guard = 0;
  while (sim.snapshot().deliveries.mock !== 1) {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('短活没结束');
  }

  // 断线后 Mock 必须显式标成「离线 Mock」，日报据此分开写。
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: false });
  sim.update(16);
  const snapshot = sim.snapshot();
  assert.equal(snapshot.deliveries.ledger, 1);
  assert.equal(snapshot.deliveries.mock, 1);
  assert.deepEqual(
    snapshot.deliveriesByVillager.map((item) => [item.name, item.ledger, item.mock]),
    [
      ['莞尔', 1, 1],
      ['阿雾', 0, 0],
    ],
  );

  const report = composeReport(snapshot);
  const line = report.lines.find((item) => item.includes('交付')) ?? '';
  assert.ok(line.includes('莞尔交付 2 单'), line);
  assert.ok(line.includes('台账 1'), line);
  assert.ok(line.includes('离线 Mock 1'), line);
  assert.ok(!line.includes('交付 3 单'), '不能把两个人/两种来源合并成一个数');
  assert.equal(report.headline, '今日有交付');
});

test('日报写清每个角色交付了几单', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.update(16);
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查资料' });
  sim.update(16);
  let guard = 0;
  while (!sim.snapshot().villagers.every((item) => item.mode === 'working')) {
    sim.update(100);
    if ((guard += 1) > 300) throw new Error('两个人没同时开工');
  }
  sim.enqueue({ kind: 'complete', target: 'v0' });
  sim.update(16);
  assert.equal(sim.snapshot().villagers[1]?.mode, 'working', '指定交付不能串到另一个人');
  sim.enqueue({ kind: 'complete', target: '阿雾' });
  sim.update(16);

  const snapshot = sim.snapshot();
  assert.deepEqual(
    snapshot.deliveriesByVillager.map((item) => [item.name, item.ledger, item.mock]),
    [
      ['莞尔', 0, 1],
      ['阿雾', 0, 1],
    ],
  );
  const report = composeReport(snapshot);
  const line = report.lines.find((item) => item.includes('交付')) ?? '';
  assert.ok(line.includes('莞尔交付 1 单'), line);
  assert.ok(line.includes('阿雾交付 1 单'), line);
  assert.ok(line.includes('离线 Mock 1'), line);
  assert.equal(report.headline, '今日有交付');
});

test('台账单按实际接单的角色分账', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c_a', kind: 'code', title: '写爬虫', agentName: '甲' },
      { commissionId: 'c_b', kind: 'research', title: '查资料', agentName: '乙' },
    ],
    finished: [],
    connected: true,
    order: ['c_a', 'c_b'],
  });
  sim.update(16);
  let guard = 0;
  while (!sim.snapshot().villagers.every((item) => item.mode === 'working')) {
    sim.update(100);
    if ((guard += 1) > 300) throw new Error('两个人没各接一单');
  }
  sim.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_a', 'c_b'],
    connected: true,
  });
  sim.update(16);
  const snapshot = sim.snapshot();
  assert.equal(snapshot.deliveries.ledger, 2);
  assert.deepEqual(
    snapshot.deliveriesByVillager.map((item) => [item.name, item.ledger, item.mock]),
    [
      ['莞尔', 1, 0],
      ['阿雾', 1, 0],
    ],
  );
  const report = composeReport(snapshot);
  const line = report.lines.find((item) => item.includes('交付')) ?? '';
  assert.ok(line.includes('莞尔交付 1 单'), line);
  assert.ok(line.includes('阿雾交付 1 单'), line);
  assert.ok(line.includes('台账 1'), line);
});

test('日报收尾写清谁在忙、谁在祭坛', () => {
  // 两个人都在祭坛：措辞要能读。
  const idle = composeReport(makeSim().snapshot());
  assert.ok(
    idle.lines.some((line) => line.includes('莞尔、阿雾都在祭坛边坐着')),
    idle.lines.join('|'),
  );

  // 一个人接活：另一个人写名字。
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.update(16);
  const mixed = composeReport(sim.snapshot());
  assert.ok(
    mixed.lines.some(
      (line) => line.includes('莞尔手上是「写脚本」') && line.includes('阿雾在祭坛边坐着'),
    ),
    mixed.lines.join('|'),
  );

  // 两个人都忙：两个名字与两单都写出来。
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查资料' });
  sim.update(16);
  const busy = composeReport(sim.snapshot());
  assert.ok(
    busy.lines.some(
      (line) => line.includes('莞尔手上是「写脚本」') && line.includes('阿雾手上是「查资料」'),
    ),
    busy.lines.join('|'),
  );
});

test('攒满里程碑后，标题写的是已经落地的那次神恩', () => {
  const sim = makeSim();
  for (let i = 0; i < 3; i += 1) {
    sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: `小事 ${i}` });
    let guard = 0;
    while (sim.snapshot().tasks[i]?.status !== 'done') {
      sim.update(100);
      if ((guard += 1) > 400) throw new Error('短活没结束');
    }
  }
  const report = composeReport(sim.snapshot());
  assert.equal(report.headline, '第 1 次神恩已落地');
});

test('日报会写上新起的楼与落地的东西', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'plot#1' });
  sim.update(16);
  const report = composeReport(sim.snapshot());
  assert.ok(report.lines.some((line) => line.includes('造物主新起 1 栋楼')));
});

test('日记只挑有信息量的事件，不编心情', () => {
  const events: WorldEvent[] = [
    { at: 0, tone: 'info', text: '世界醒来。' },
    { at: 1, tone: 'oracle', text: '神谕：写个脚本 → 炼码工坊' },
    { at: 2, tone: 'done', text: '写个脚本 已交付。' },
  ];
  const diary = composeDiary(events);
  assert.equal(diary.length, 2);
  assert.ok(diary[0].startsWith('今天，'));
  assert.ok(diary.every((line) => !line.includes('世界醒来')));
});

test('空事件时日记不硬凑', () => {
  assert.deepEqual(composeDiary([]), ['今天没什么事。']);
});

// ------------------------------------------------------------------ animalese

test('同一句话永远生成同一串音高', () => {
  assert.deepEqual(syllablePlan('吾神降旨'), syllablePlan('吾神降旨'));
});

test('标点不发声，但会占一拍', () => {
  const plan = syllablePlan('好！');
  assert.deepEqual(plan, [plan[0], 0]);
});

test('空白不产生音节', () => {
  assert.deepEqual(syllablePlan('   '), []);
});

test('音高偏移落在 0.85..1.25 之间', () => {
  for (const value of syllablePlan('把便笺整理成三版标题')) {
    if (value === 0) continue;
    assert.ok(value >= 0.85 && value <= 1.25, `越界：${value}`);
  }
});

// --------------------------------------------------------------------- bridge

test('外部任务消息翻成世界命令', () => {
  assert.deepEqual(
    toWorldCommand({ type: 'task.assigned', taskId: 'a', taskKind: 'code', summary: '写' }),
    { kind: 'dispatch', taskKind: 'code', summary: '写' },
  );
  assert.deepEqual(toWorldCommand({ type: 'task.completed', taskId: 'a' }), { kind: 'complete' });
  assert.deepEqual(toWorldCommand({ type: 'world.weather', weather: 'rain' }), {
    kind: 'setWeather',
    weather: 'rain',
  });
});

test('桥接的收件箱取一次就空，不重复投递', () => {
  const bridge = new Bridge();
  bridge.feed({ type: 'world.weather', weather: 'rain' });
  assert.equal(bridge.takeInbox().length, 1);
  assert.equal(bridge.takeInbox().length, 0);
});

test('桥接上行只取出一次，不重复上报', () => {
  const bridge = new Bridge();
  bridge.emit({ type: 'task.delivered', taskId: 't1', summary: '写完' });
  assert.equal(bridge.drain().length, 1);
  assert.equal(bridge.drain().length, 0);
});
