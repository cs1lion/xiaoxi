import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  describePropSource,
  MILESTONE_POINTS,
  nextProp,
  POINTS_PER_TASK,
  propKindFor,
  propLabelFor,
} from '../src/world/faith.ts';
import type { GridPoint, TaskKind, WorldProp } from '../src/world/types.ts';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { composeReport } from '../src/world/report.ts';

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

/** 跑固定步长直到条件成立；超时就抛错，避免测试挂死。 */
function runUntil(sim: Simulation, predicate: () => boolean, limitMs: number): void {
  let elapsed = 0;
  while (!predicate()) {
    sim.update(100);
    elapsed += 100;
    if (elapsed > limitMs) throw new Error(`等了 ${limitMs}ms 条件仍未成立`);
  }
}

const NO_SOURCE = null;
const everywhere = (): boolean => true;
const anchor: GridPoint = { x: 15, y: 9 };

const prop = (over: Partial<WorldProp> = {}): WorldProp => ({
  id: 'prop-0',
  kind: 'lamp',
  at: { x: 13, y: 9 },
  label: '一盏路灯',
  sourceTaskKind: null,
  sourceTaskId: null,
  sourceSummary: null,
  ...over,
});

// ------------------------------------------------------------------ 选型

test('摆件按活类型选型：代码→灯，资料→树，文稿→旗', () => {
  assert.equal(propKindFor('code'), 'lamp');
  assert.equal(propKindFor('research'), 'tree');
  assert.equal(propKindFor('writing'), 'banner');
});

test('选型覆盖全部四种活，没有漏项', () => {
  const kinds: TaskKind[] = ['code', 'research', 'writing', 'quick'];
  for (const kind of kinds) {
    assert.ok(propKindFor(kind).length > 0, `${kind} 必须有摆件类型`);
    assert.ok(propLabelFor(kind).length > 0, `${kind} 必须有摆件名`);
  }
});

test('不同活类型给出不同外观，不是一律路灯', () => {
  const kinds: TaskKind[] = ['code', 'research', 'writing'];
  const shapes = new Set(kinds.map((kind) => propKindFor(kind)));
  assert.equal(shapes.size, 3, '三种长活应给出三种不同外形');
});

// ------------------------------------------------------------------ 落位

test('落位把来源抄进摆件：类型、单号、摘要三样都在', () => {
  const made = nextProp(0, anchor, everywhere, {
    kind: 'research',
    taskId: 'c_abc',
    summary: '查一下向量数据库的选型',
  });
  assert.ok(made !== null);
  assert.equal(made.sourceTaskKind, 'research');
  assert.equal(made.sourceTaskId, 'c_abc');
  assert.equal(made.sourceSummary, '查一下向量数据库的选型');
  assert.equal(made.kind, 'tree', '查资料落树');
});

test('没有来源时三个字段都是 null，不编造', () => {
  const made = nextProp(0, anchor, everywhere, NO_SOURCE);
  assert.ok(made !== null);
  assert.equal(made.sourceTaskKind, null);
  assert.equal(made.sourceTaskId, null);
  assert.equal(made.sourceSummary, null);
});

test('周围没有空地时返回 null，由调用方保留点数', () => {
  const made = nextProp(0, anchor, () => false, null);
  assert.equal(made, null);
});

test('摆在已有摆件旁边，不叠在同一个格子上', () => {
  const first = nextProp(0, anchor, everywhere, null);
  assert.ok(first !== null);
  // 真实调用会把已落下的位置传进来，这里照做。
  const second = nextProp(1, anchor, everywhere, null, [first.at]);
  assert.ok(second !== null);
  assert.equal(first.id, 'prop-0');
  assert.equal(second.id, 'prop-1');
  assert.notDeepEqual(second.at, first.at, '第二件不能落在第一件头上');
});

test('当前环被占满时往外扩，而不是放弃落件', () => {
  const ring = 2;
  const occupied: GridPoint[] = [];
  for (let dx = -ring; dx <= ring; dx += 1) {
    occupied.push({ x: anchor.x + dx, y: anchor.y - ring });
    occupied.push({ x: anchor.x + dx, y: anchor.y + ring });
  }
  for (let dy = -ring + 1; dy <= ring - 1; dy += 1) {
    occupied.push({ x: anchor.x - ring, y: anchor.y + dy });
    occupied.push({ x: anchor.x + ring, y: anchor.y + dy });
  }
  const placed = nextProp(0, anchor, everywhere, null, occupied);
  assert.ok(placed !== null, '外环还有空位，必须落下');
  assert.ok(
    !occupied.some((p) => p.x === placed.at.x && p.y === placed.at.y),
    '落点必须在已占位置之外',
  );
});

// ------------------------------------------------------------------ 纸面文案

test('有来源时纸面写得出「那单」的摘要', () => {
  const line = describePropSource(
    prop({ sourceTaskKind: 'code', sourceTaskId: 't1', sourceSummary: '写一个爬虫' }),
  );
  assert.match(line, /写一个爬虫/);
  assert.match(line, /路灯/);
});

test('神迹落下的东西说「神迹」，不冒充任务来源，也不算来路不明', () => {
  const line = describePropSource(
    prop({ sourceTaskKind: null, sourceTaskId: null, sourceSummary: '神迹：种树', label: '一棵神迹种的树' }),
  );
  assert.match(line, /神迹/);
  assert.ok(!line.includes('来路不明'), `神迹不该被报成来路不明：${line}`);
});

test('老摆件如实说「来路不明」，不假装有来源', () => {
  const line = describePropSource(prop());
  assert.match(line, /来路不明/);
});

test('只有类型没有摘要时退到类型名，不写空括号', () => {
  const line = describePropSource(
    prop({ sourceTaskKind: 'writing', sourceTaskId: 't2', sourceSummary: null }),
  );
  assert.match(line, /旗/);
  assert.ok(!line.includes('「」'), `不能出现空引号：${line}`);
});

test('长摘要截断到 24 码点并加省略号', () => {
  const long = '这是一个非常长的任务摘要'.repeat(4);
  const line = describePropSource(
    prop({ sourceTaskKind: 'research', sourceTaskId: 't3', sourceSummary: long }),
  );
  assert.match(line, /…/);
  const inside = line.slice(line.indexOf('「') + 1, line.indexOf('…'));
  assert.equal(Array.from(inside).length, 24, '省略号前应恰好 24 个码点');
});

test('空白摘要按没有摘要处理', () => {
  const line = describePropSource(
    prop({ sourceTaskKind: 'code', sourceTaskId: 't4', sourceSummary: '   ' }),
  );
  assert.ok(!line.includes('「'), `空白摘要不该生成引号：${line}`);
});

// ------------------------------------------------------------------ 走真实世界

test('真实交付攒满三点：摆件带上那一单的来源，日报写得出摘要', () => {
  const sim = makeSim();
  const rounds = Math.ceil(MILESTONE_POINTS / POINTS_PER_TASK);

  for (let i = 0; i < rounds; i += 1) {
    sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: `顺手记一笔 ${i}` });
    runUntil(sim, () => sim.snapshot().villager.mode === 'working', 5000);
    runUntil(sim, () => sim.snapshot().villager.mode === 'idle', 10_000);
  }

  const snapshot = sim.snapshot();
  assert.equal(snapshot.props.length, 1, '三点应落一件');
  const placed = snapshot.props[0]!;
  assert.equal(placed.sourceTaskKind, 'quick', '来源类型取自最后一单');
  assert.ok(placed.sourceTaskId !== null, '来源单号不能为空');
  assert.match(String(placed.sourceSummary), /顺手记一笔/);

  const report = composeReport(snapshot);
  const propLine = report.lines.find((line) => line.includes('祭坛边'));
  assert.ok(propLine !== undefined, '日报必须提到落下的摆件');
  assert.match(propLine, /顺手记一笔/, '日报要写清是哪一单换的');
});

test('三种活落三种不同外形', () => {
  const shapes = new Set(
    (['code', 'research', 'writing'] as TaskKind[]).map((kind) => {
      const made = nextProp(0, anchor, everywhere, { kind, taskId: 't', summary: 'x' });
      assert.ok(made !== null);
      return made.kind;
    }),
  );
  assert.equal(shapes.size, 3, '写码/查资料/整理应给出三种外形');
});

// ------------------------------------------------------------------ 常量

test('攒点规则没变：每单一点，三点一件', () => {
  assert.equal(POINTS_PER_TASK, 1);
  assert.equal(MILESTONE_POINTS, 3);
});
