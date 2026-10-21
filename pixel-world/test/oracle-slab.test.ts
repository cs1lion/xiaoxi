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

/** 说一句话并结算一拍。返回石板与回执。 */
function say(
  sim: Simulation,
  raw: string,
): { slab: ReturnType<Simulation['snapshot']>['oracle']; receipt: string } {
  sim.enqueue({ kind: 'oracle', raw });
  const results = sim.update(16);
  return { slab: sim.snapshot().oracle, receipt: results[0]?.message ?? '' };
}

// ------------------------------------------------------------------ 记什么

test('派活成功：石板记下原话与世界回执', () => {
  const sim = makeSim();
  const { slab, receipt } = say(sim, '帮我写一段爬虫');
  assert.equal(slab.length, 1);
  assert.equal(slab[0]!.raw, '帮我写一段爬虫');
  assert.ok(slab[0]!.receipt.length > 0, '回执不能为空');
  assert.equal(slab[0]!.receipt, receipt, '石板里的回执就是世界当拍的回执');
  assert.ok(slab[0]!.at >= 0, '记录逻辑时刻');
});

test('改天气成功：也进石板', () => {
  const sim = makeSim();
  const { slab } = say(sim, '下雨吧');
  assert.equal(slab.length, 1);
  assert.equal(slab[0]!.raw, '下雨吧');
  assert.equal(sim.snapshot().weather, 'rain');
});

test('认不出的话不进石板：世界没有动', () => {
  const sim = makeSim();
  const { slab, receipt } = say(sim, '今天天气不错');
  assert.equal(slab.length, 0, '认不出的输入不是世界事件');
  assert.match(receipt, /没听懂|没有动/);
});

test('扑空的命令不进石板：没排队的单可撤销时不该留痕', () => {
  const sim = makeSim();
  const { slab } = say(sim, '撤销');
  assert.equal(slab.length, 0, '世界没变就不记');
});

test('「天已经在下雨」这类无变化回执不重复记', () => {
  const sim = makeSim();
  say(sim, '下雨吧');
  const { slab } = say(sim, '下雨吧');
  assert.equal(slab.length, 1, '第二次下雨没改变世界，不该再记一条');
});

// ------------------------------------------------------------------ 封顶

test(`石板封顶 ${ORACLE_SLAB_LIMIT} 条，超出丢最旧`, () => {
  const sim = makeSim();
  for (let i = 0; i < ORACLE_SLAB_LIMIT + 5; i += 1) {
    // 每次先放晴再下雨，保证每条都真的改变了世界。
    if (i % 2 === 0) say(sim, `下雨吧 ${i}`);
    else say(sim, `放晴吧 ${i}`);
  }
  const slab = sim.snapshot().oracle;
  assert.equal(slab.length, ORACLE_SLAB_LIMIT, '不能超过上限');
  assert.match(slab[slab.length - 1]!.raw, new RegExp(` ${ORACLE_SLAB_LIMIT + 4}$`), '最新一条在末尾');
  assert.ok(
    !slab.some((item) => item.raw.endsWith(' 0')),
    '最旧的已经被挤掉',
  );
});

// ------------------------------------------------------------------ 存读档

test('石板能存能读，内容与顺序不变', () => {
  const sim = makeSim();
  say(sim, '帮我写一段爬虫');
  say(sim, '下雨吧');

  const save = sim.toSave();
  assert.equal(save.world.oracle.length, 2, '写档必须带上石板');

  const world = buildWorld(blueprints);
  const restored = Simulation.fromSave(
    JSON.parse(JSON.stringify(save)),
    {
      grid: world.grid,
      buildings: world.buildings,
      altar: ALTAR,
      villagerName: '莞尔',
      wallClock: () => 1000,
    },
  );
  const slab = restored.snapshot().oracle;
  assert.equal(slab.length, 2);
  assert.equal(slab[0]!.raw, '帮我写一段爬虫');
  assert.equal(slab[1]!.raw, '下雨吧');
  assert.deepEqual(
    slab.map((item) => item.receipt),
    sim.snapshot().oracle.map((item) => item.receipt),
  );
});

test('石板记录用逻辑时刻，不用墙钟', () => {
  const sim = makeSim();
  sim.update(500);
  sim.enqueue({ kind: 'oracle', raw: '下雨吧' });
  sim.update(16);
  const record = sim.snapshot().oracle[0];
  assert.ok(record !== undefined);
  assert.equal(record.at, 516, 'at 应等于结算那一刻的逻辑时钟');
});
