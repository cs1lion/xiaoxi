import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { parseOracleBuild } from '../src/world/oracle-build.ts';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

const parse = (text: string) => parseOracleBuild(text, blueprints);

// ------------------------------------------------------------------ 认出模板

test('「建一间书阁」认成星图书阁', () => {
  const plan = parse('建一间书阁');
  assert.ok(plan !== null, '应当认出来');
  assert.equal(plan.templateId, 'star_archive');
});

test('模板的别名与显示名都能认', () => {
  const cases: [string, string][] = [
    ['盖个工坊', 'workshop_plot'],
    ['造一座抄书斋', 'ink_house'],
    ['建一间书阁', 'star_archive'],
    ['开一个矿洞', 'crawler_mine'],
    ['修一座齿轮塔', 'gear_tower'],
  ];
  for (const [text, expected] of cases) {
    const plan = parse(text);
    assert.ok(plan !== null, `「${text}」应当认出来`);
    assert.equal(plan.templateId, expected, `「${text}」应指向 ${expected}`);
  }
});

// ------------------------------------------------------------------ 位置

test('不指定位置时 at 为 null：由世界挑地方', () => {
  const plan = parse('建一间书阁');
  assert.ok(plan !== null);
  assert.equal(plan.at, null, '没指定位置就不该编一个');
});

test('句子里带坐标时用坐标', () => {
  const plan = parse('在 (32,14) 建一间书阁');
  assert.ok(plan !== null);
  assert.deepEqual(plan.at, { x: 32, y: 14 });
});

test('坐标写成「32,14」也能认', () => {
  const plan = parse('建一间书阁 32,14');
  assert.ok(plan !== null);
  assert.deepEqual(plan.at, { x: 32, y: 14 });
});

// ------------------------------------------------------------------ 不认的情况

test('没有建造动词时不认：这不是造物', () => {
  assert.equal(parse('书阁真好看'), null);
  assert.equal(parse('我想建一间书阁'), null, '叙述不是命令');
  assert.equal(parse('建不建书阁？'), null, '疑问不是命令');
});

test('有建造动词但没有可认的模板时不认', () => {
  assert.equal(parse('建一座城堡'), null, '城堡不在注册表里');
});

test('空句子不认', () => {
  assert.equal(parse(''), null);
  assert.equal(parse('   '), null);
});

// ------------------------------------------------------------------ 与 schema 的关系

test('认出的结果能直接喂给 schema：形状一致', () => {
  const plan = parse('在 (32,14) 建一间书阁');
  assert.ok(plan !== null);
  // 形状是 { templateId, at, palette? }，与 CreationPlan 的输入一致。
  assert.equal(typeof plan.templateId, 'string');
  assert.ok(plan.at === null || typeof plan.at.x === 'number');
});

// ------------------------------------------------------------------ 接进世界

test('一句「建个工坊」真的建出一栋楼，并刻上石板', () => {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
    spawnBuilding: (prefabId, instanceId, at, source) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR, at, source);
    },
  });

  const before = sim.snapshot().buildings.length;
  sim.enqueue({ kind: 'oracle', raw: '建个工坊' });
  const result = sim.update(16)[0];

  assert.equal(result?.ok, true, result?.message);
  assert.equal(sim.snapshot().buildings.length, before + 1, '应当多一栋楼');
  const built = sim.snapshot().buildings.find((b) => b.prefabId === 'workshop_plot');
  assert.equal(built?.sourceSummary, '按神谕建了新工坊', '来源要记清楚');
  assert.equal(sim.snapshot().oracle.length, 1, '造物神谕应当刻石板');
});

test('已经有书阁时说「建一间书阁」，认得出但不建，并说明原因', () => {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1000,
    blueprints,
  });

  sim.enqueue({ kind: 'oracle', raw: '建一间书阁' });
  const result = sim.update(16)[0];
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /已经有星图书阁/);
  assert.equal(sim.snapshot().oracle.length, 0, '没改变世界就不刻石板');
});
