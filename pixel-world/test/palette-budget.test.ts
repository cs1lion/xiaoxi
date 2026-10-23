/** D6-A 调色板预算的回归门禁。
 *
 * 目标（ART_PALETTE_AUDIT §5.3 第 6 步）：世界 24 色、UI 8 色、共享 1、并集 31。
 * 2026-10-21 用户批准 24 色 ADR（docs/ADR_PALETTE_24.md），新增 W17–W24。
 * 这里用纯逻辑断言锁住，不依赖 `npm test` 的默认子进程隔离（沙箱里会 spawn EPERM）。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { WORLD } from '../src/world/palette-data.ts';
import { parseBlueprint, parseBlueprintTable, BlueprintError } from '../src/world/blueprints.ts';
import { buildWorld, buildWorldFromSave } from '../src/world/setup.ts';

const WORLD_BUDGET = 24;
const UI_BUDGET = 8;
/** 世界 24 + UI 8 − 共享 1（#e3a13c）= 31。 */
const UNION_BUDGET = 31;

const readText = (rel: string): string => readFileSync(new URL(rel, import.meta.url), 'utf8');

const hex = (value: number): string => `#${value.toString(16).padStart(6, '0')}`;

const prefabRaw = (): Record<string, Record<string, unknown>> =>
  JSON.parse(readText('../src/world/prefabs.json'));

const blueprints = () => parseBlueprintTable(prefabRaw());

/** 把 index.html 的 <style> 拆成“源色集合”：十六进制字面量与 rgba() 的 RGB 三分量。 */
function uiSourceColors(): string[] {
  const html = readText('../index.html');
  const style = /<style[^>]*>([\s\S]*?)<\/style>/.exec(html)?.[1] ?? '';
  const bare = style.replace(/\/\*[\s\S]*?\*\//g, ' ');
  const found: string[] = [];
  for (const match of bare.matchAll(/#([0-9a-fA-F]{6})\b/g)) found.push(`#${match[1].toLowerCase()}`);
  for (const match of bare.matchAll(/rgba?\(([^)]*)\)/g)) {
    const channels = match[1]
      .split(/[,/\s]+/)
      .map((part) => part.trim())
      .filter(Boolean)
      .slice(0, 3)
      .map(Number);
    if (channels.length !== 3 || channels.some((value) => !Number.isFinite(value))) continue;
    found.push(`#${channels.map((value) => Math.round(value).toString(16).padStart(2, '0')).join('')}`);
  }
  return [...new Set(found)];
}

test('世界层正好 24 个源色，且没有重复值', () => {
  const values = Object.values(WORLD);
  assert.equal(values.length, WORLD_BUDGET, `世界 24 色，实际 ${values.length} 个 token`);
  assert.equal(new Set(values).size, WORLD_BUDGET, '世界 24 色里有重复值');
});

test('UI 层正好 8 个源色', () => {
  const colors = uiSourceColors();
  assert.equal(colors.length, UI_BUDGET, `UI 预算 8 色，实际 ${colors.join(', ')}`);
});

/** 提案表（ART_PALETTE_AUDIT §4.1 / §4.2）逐字抄在这里：色数对但换值也必须变红。
 *  P3-1：`--strict` 只判色数，`WORLD.ink` 换成 `#000000` 时它仍是 0——这条断言补上“值”的门禁。
 */
const PROPOSED_WORLD: Record<string, string> = {
  ink: '#14110d',
  soil: '#1b211c',
  grass: '#2d3b2e',
  grassLit: '#334234',
  stone: '#3e433f', // 2026-10-20 明度重排方案 A（用户批准）：原 #3a3f3b
  plaza: '#4a4f42',
  farm: '#3b3423',
  timber: '#8a5a44',
  timberDark: '#4a3f39',
  trim: '#c9a86a',
  amber: '#e3a13c',
  skin: '#e0b48c',
  cloak: '#30506b',
  cloakDark: '#223a4d',
  ember: '#c06a3e',
  pale: '#d8dad3',
  plaster: '#c2b39a',
  terracotta: '#9c4f3c',
  copper: '#5f8a74',
  water: '#3d6b7a',
  moss: '#4e6241',
  deepShade: '#18242b',
  warmLight: '#f2c879',
  charcoal: '#5a463e',
};
const PROPOSED_UI = [
  '#e8e2d2',
  '#24211b',
  '#8e968c',
  '#171b18',
  '#2b322c',
  '#e3a13c',
  '#0c0f0d',
  '#c8876a',
];

test('WORLD 的每个 token 值都等于提案 24 色（换值必须变红）', () => {
  const actual = Object.fromEntries(Object.entries(WORLD).map(([name, value]) => [name, hex(value)]));
  assert.deepEqual(actual, PROPOSED_WORLD, 'WORLD 的 token 名或色值偏离了提案 24 色');
});

test('UI 的源色集合等于提案 8 色（换值必须变红）', () => {
  assert.deepEqual([...uiSourceColors()].sort(), [...PROPOSED_UI].sort(), 'UI 色值偏离了提案 8 色');
});

test('世界与 UI 只共享 #e3a13c，并集 31', () => {
  const world = new Set(Object.values(WORLD).map(hex));
  const ui = new Set(uiSourceColors());
  const shared = [...ui].filter((color) => world.has(color));
  assert.deepEqual(shared, [hex(WORLD.amber)], `共享色只能是琥珀，实际 ${shared.join(', ')}`);
  assert.equal(new Set([...world, ...ui]).size, UNION_BUDGET, '并集应为 24 + 8 − 1 = 31');
});

test('render/scenes 里不再有 canvas 颜色字面量（只许引用 WORLD）', () => {
  const files = [
    '../src/main.ts',
    '../src/render/buildings.ts',
    '../src/render/decor.ts',
    '../src/render/smoke.ts',
    '../src/render/tilemap.ts',
    '../src/render/villager.ts',
    '../src/render/weather.ts',
    '../src/scenes/SandboxScene.ts',
  ];
  for (const file of files) {
    const text = readText(file).replace(/\/\*[\s\S]*?\*\//g, ' ');
    const literals = [...text.matchAll(/0x[0-9a-fA-F]{6}\b/g)].map((match) => match[0]);
    assert.deepEqual(literals, [], `${file} 还有裸色值：${literals.join(', ')}`);
  }
});

test('全部蓝图 × 4 槽都指向 WORLD token', () => {
  const raw = prefabRaw() as Record<string, { palette: Record<string, unknown> }>;
  const names = new Set(Object.keys(WORLD));
  let slots = 0;
  for (const [id, blueprint] of Object.entries(raw)) {
    for (const [slot, value] of Object.entries(blueprint.palette)) {
      slots += 1;
      assert.equal(typeof value, 'string', `${id}.palette.${slot} 应该是 token 名`);
      assert.ok(names.has(value as string), `${id}.palette.${slot}="${String(value)}" 不在 WORLD 里`);
    }
  }
  // 蓝图套数随世界扩张增加（镇内三 + 郊野三 + 扩建样板），断言从"写死 16"
  // 改为"槽数必须是套数的 4 倍"，避免每加一栋楼就要改这个数字。
  const expected = Object.keys(raw).length * 4;
  assert.equal(slots, expected, `${Object.keys(raw).length} 套 × 4 槽 = ${expected}，实际 ${slots}`);
});

test('解析器兼容旧值：整数与 "#rrggbb" 都解析回同一个数', () => {
  const raw = prefabRaw();
  const fromTokens = parseBlueprint('code_forge', raw.code_forge).palette;

  const numeric = structuredClone(raw.code_forge) as { palette: Record<string, unknown> };
  numeric.palette = { body: WORLD.timber, roof: WORLD.terracotta, trim: WORLD.trim, window: WORLD.amber };
  assert.deepEqual(parseBlueprint('code_forge', numeric).palette, fromTokens);

  const asHex = structuredClone(raw.code_forge) as { palette: Record<string, unknown> };
  asHex.palette = { body: '#8a5a44', roof: '#9c4f3c', trim: '#c9a86a', window: '#e3a13c' };
  assert.deepEqual(parseBlueprint('code_forge', asHex).palette, fromTokens);
});

test('未知颜色名的报错能看懂：指出槽位、原值、可用清单', () => {
  const raw = prefabRaw();
  const broken = structuredClone(raw.code_forge) as { palette: Record<string, unknown> };
  broken.palette = { body: 'sage', roof: 'timberDark', trim: 'trim', window: 'amber' };
  assert.throws(
    () => parseBlueprint('code_forge', broken),
    (error: unknown) => {
      assert.ok(error instanceof BlueprintError);
      assert.match(error.message, /调色板槽 body/);
      assert.match(error.message, /"sage"/);
      assert.match(error.message, /ink, soil, grass/);
      return true;
    },
  );
});

test('读档后按当前 prefab 重解 palette：旧档不会停在旧色（D6-A P1）', () => {
  const table = blueprints();
  const built = buildWorld(table);
  // 模拟旧存档：存档里留着旧色表的颜色（不是现在的 token 色）。
  const stale = built.buildings.map((building) => ({
    ...building,
    palette: { body: 0x6b7a6a, roof: 0x3f4a4a, trim: 0x8fa08a, window: 0xf0c070 },
  }));
  const loaded = buildWorldFromSave(stale, table);
  for (const building of loaded.buildings) {
    const blueprint = table.get(building.prefabId);
    assert.ok(blueprint !== undefined, `${building.prefabId} 应该在蓝图表里`);
    assert.deepEqual(
      building.palette,
      blueprint.palette,
      `${building.prefabId} 读档后应取当前 prefab 的 palette`,
    );
    assert.notEqual(building.palette.body, 0x6b7a6a, '旧色不该残留在读档结果里');
  }
});

test('读档遇到不存在的 prefab：保留存档色并告警，不崩', () => {
  const table = blueprints();
  const built = buildWorld(table);
  const orphan = built.buildings.map((building, index) =>
    index === 0 ? { ...building, prefabId: 'ghost_forge' } : building,
  );
  const loaded = buildWorldFromSave(orphan, table);
  assert.equal(loaded.buildings[0]?.prefabId, 'ghost_forge');
  assert.deepEqual(loaded.buildings[0]?.palette, orphan[0]?.palette);
});
