import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { parseOracleMiracle } from '../src/world/oracle-miracle.ts';

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

const miracle = (sim: Simulation, kind: 'light' | 'tree', at?: { x: number; y: number }) => {
  sim.enqueue(at === undefined ? { kind: 'miracle', miracle: kind } : { kind: 'miracle', miracle: kind, at });
  return sim.update(16)[0];
};

// ------------------------------------------------------------------ 旧行为不变

test('不给位置时仍落在祭坛周围', () => {
  const sim = makeSim();
  const altar = sim.snapshot().altar;
  miracle(sim, 'light');

  const prop = sim.snapshot().props[0];
  assert.ok(prop !== undefined);
  const d = Math.hypot(prop.at.x - altar.x, prop.at.y - altar.y);
  assert.ok(d < 8, `应当落在祭坛附近，实际 ${d.toFixed(1)} 格`);
});

// ------------------------------------------------------------------ 指定位置

test('给位置时落在指定处', () => {
  const sim = makeSim();
  const at = { x: 30, y: 40 };
  const result = miracle(sim, 'light', at);

  assert.equal(result?.ok, true, result?.message);
  assert.deepEqual(sim.snapshot().props[0]?.at, at);
});

test('指定的位置被占时拒绝，不硬塞', () => {
  const sim = makeSim();
  const building = sim.snapshot().buildings[0];
  assert.ok(building !== undefined);

  const result = miracle(sim, 'tree', building.origin);
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /占|建|不行/);
});

test('指定的位置在迷雾里时拒绝', () => {
  const sim = makeSim();
  const altar = sim.snapshot().altar;
  const fog = sim.snapshot().fog;
  // 从中心往外找，取第一块**界内但在迷雾里**的格子。
  // 不能简单加半径：altar.y + radius + 10 会出界（地图只有 72 高）。
  let far: { x: number; y: number } | null = null;
  for (let y = 0; y < 72 && far === null; y += 1) {
    for (let x = 0; x < 96; x += 1) {
      if (Math.hypot(x - altar.x, y - altar.y) > fog.revealedRadius + 4) {
        far = { x, y };
        break;
      }
    }
  }
  assert.ok(far !== null, '应当找得到界内的迷雾格');
  const result = miracle(sim, 'tree', far);
  assert.equal(result?.ok, false);
  assert.match(result?.message ?? '', /长出来|雾|未/);
});

test('两次指定不同位置：两件都落对地方', () => {
  const sim = makeSim();
  const a = { x: 30, y: 40 };
  const b = { x: 34, y: 40 };
  miracle(sim, 'light', a);
  miracle(sim, 'tree', b);

  const props = sim.snapshot().props;
  assert.equal(props.length, 2);
  assert.deepEqual(props[0]?.at, a);
  assert.deepEqual(props[1]?.at, b);
});

test('猫也能指定位置', () => {
  const sim = makeSim();
  const at = { x: 33, y: 41 };
  sim.enqueue({ kind: 'miracle', miracle: 'cat', at });
  sim.update(16);

  const critter = sim.snapshot().critters[0];
  assert.ok(critter !== undefined);
  assert.equal(critter.at.x, at.x + 0.5, '猫落在格子中央');
  assert.equal(critter.at.y, at.y + 0.5);
});

// ------------------------------------------------------------------ 从神谕里说

test('「点灯」「种树」「召唤猫」认得出', () => {
  const cases: [string, string][] = [
    ['点灯', 'light'],
    ['点一盏灯', 'light'],
    ['种树', 'tree'],
    ['种一棵树', 'tree'],
    ['召唤猫', 'cat'],
    ['来只猫', 'cat'],
  ];
  for (const [text, expected] of cases) {
    const intent = parseOracleMiracle(text);
    assert.ok(intent !== null, `「${text}」应当认出来`);
    assert.equal(intent.miracle, expected, `「${text}」应指向 ${expected}`);
  }
});

test('带坐标的神迹用坐标', () => {
  const intent = parseOracleMiracle('在 (40,45) 种一棵树');
  assert.ok(intent !== null);
  assert.equal(intent.miracle, 'tree');
  assert.deepEqual(intent.at, { x: 40, y: 45 });
});

test('不带坐标时 at 为 null：由世界挑地方', () => {
  const intent = parseOracleMiracle('种树');
  assert.ok(intent !== null);
  assert.equal(intent.at, null);
});

test('叙述句不认：「这棵树真好看」', () => {
  assert.equal(parseOracleMiracle('这棵树真好看'), null);
  assert.equal(parseOracleMiracle('我想点灯'), null);
});

test('「点灯」与「建工坊」不冲突：前者是神迹，后者是造物', () => {
  assert.equal(parseOracleMiracle('点灯')?.miracle, 'light');
  // 造物解析器认「建 / 盖」，神迹认「点 / 种 / 召唤」，两张动词表不重叠。
});

// ------------------------------------------------------------------ 接进世界

test('说「在 (30,40) 种一棵树」，树真的长在那儿', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'oracle', raw: '在 (30,40) 种一棵树' });
  const result = sim.update(16)[0];

  assert.equal(result?.ok, true, result?.message);
  const prop = sim.snapshot().props[0];
  assert.ok(prop !== undefined);
  assert.equal(prop.kind, 'tree');
  assert.deepEqual(prop.at, { x: 30, y: 40 });
  assert.equal(sim.snapshot().oracle.length, 1, '神迹也刻石板');
});

test('说「召唤猫」，猫出现在世界里', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'oracle', raw: '召唤猫' });
  const result = sim.update(16)[0];
  assert.equal(result?.ok, true, result?.message);
  assert.equal(sim.snapshot().critters.length, 1);
});
