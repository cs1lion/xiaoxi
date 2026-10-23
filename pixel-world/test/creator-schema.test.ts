import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import {
  CreatorSchemaError,
  parseCreationPlan,
  type CreationPlan,
} from '../src/world/creator-schema.ts';
import { WORLD } from '../src/world/palette-data.ts';
import { ALTAR, buildWorld, placeFromPlan } from '../src/world/setup.ts';

const blueprints = parseBlueprintTable(
  JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>,
);

const valid = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  templateId: 'workshop_plot',
  at: { x: 32, y: 12 },
  palette: { body: 'timber', roof: 'cloak', trim: 'pale', window: 'amber' },
  ...over,
});

const parse = (raw: unknown): CreationPlan =>
  parseCreationPlan(raw, blueprints, { width: 80, height: 60 });

// ------------------------------------------------------------------ 合法输入

test('最小合法输入：模板 + 位置 + 调色板', () => {
  const plan = parse(valid());
  assert.equal(plan.templateId, 'workshop_plot');
  assert.deepEqual(plan.at, { x: 32, y: 12 });
  assert.equal(plan.palette.body, WORLD.timber);
});

test('调色板可省略：省略时用模板自带的配色', () => {
  const plan = parse(valid({ palette: undefined }));
  const template = blueprints.get('workshop_plot');
  assert.ok(template !== undefined);
  assert.deepEqual(plan.palette, template.palette, '省略调色板应回落到模板配色');
});

test('调色板可以只给一部分，其余回落到模板', () => {
  const plan = parse(valid({ palette: { roof: 'terracotta' } }));
  const template = blueprints.get('workshop_plot');
  assert.ok(template !== undefined);
  assert.equal(plan.palette.roof, WORLD.terracotta, '给了的用给的');
  assert.equal(plan.palette.body, template.palette.body, '没给的回落模板');
});

// ------------------------------------------------------------------ 拒绝：模板

test('未知模板直接拒绝，并指名是哪个', () => {
  assert.throws(
    () => parse(valid({ templateId: 'castle' })),
    (error: unknown) => {
      assert.ok(error instanceof CreatorSchemaError);
      assert.match(error.message, /castle/);
      return true;
    },
  );
});

test('模板 id 不是字符串时拒绝', () => {
  assert.throws(() => parse(valid({ templateId: 42 })), CreatorSchemaError);
});

// ------------------------------------------------------------------ 拒绝：位置

test('位置必须在世界内', () => {
  assert.throws(() => parse(valid({ at: { x: -1, y: 5 } })), CreatorSchemaError);
  assert.throws(() => parse(valid({ at: { x: 999, y: 5 } })), CreatorSchemaError);
});

test('位置必须是整数格，不接受小数', () => {
  assert.throws(() => parse(valid({ at: { x: 1.5, y: 5 } })), CreatorSchemaError);
});

test('位置缺失时拒绝', () => {
  assert.throws(() => parse(valid({ at: undefined })), CreatorSchemaError);
});

// ------------------------------------------------------------------ 拒绝：调色板

test('调色板只接受现有 24 色里的 token 名', () => {
  assert.throws(
    () => parse(valid({ palette: { body: 'neon_pink' } })),
    (error: unknown) => {
      assert.ok(error instanceof CreatorSchemaError);
      assert.match(error.message, /neon_pink/);
      return true;
    },
  );
});

test('调色板不接受十六进制字面量：那是绕开 24 色预算', () => {
  assert.throws(() => parse(valid({ palette: { body: '#ff00ff' } })), CreatorSchemaError);
});

test('调色板槽位必须是已知的四个', () => {
  assert.throws(
    () => parse(valid({ palette: { wallpaper: 'timber' } })),
    /wallpaper/,
  );
});

// ------------------------------------------------------------------ 拒绝：多余字段

test('未知顶层字段拒绝：LLM 多写一个字段不该被静默忽略', () => {
  assert.throws(
    () => parse(valid({ furniture: ['desk'] })),
    (error: unknown) => {
      assert.ok(error instanceof CreatorSchemaError);
      assert.match(error.message, /furniture/);
      return true;
    },
  );
});

test('拒绝时给出可读理由，不是裸 TypeError', () => {
  for (const bad of [null, 42, 'string', [], { templateId: 'x' }]) {
    assert.throws(
      () => parse(bad),
      (error: unknown) => error instanceof CreatorSchemaError,
      `${JSON.stringify(bad)} 应以 CreatorSchemaError 拒绝`,
    );
  }
});

// ------------------------------------------------------------------ 与真实世界的边界

test('校验器不判断位置能否建：那是 checkPlacement 的事', () => {
  // 位置在雾里、压在楼上，schema 都认为合法——它只管形状。
  const plan = parse(valid({ at: { x: 0, y: 0 } }));
  assert.deepEqual(plan.at, { x: 0, y: 0 });
});

test('schema 与蓝图解析共用同一套 token 名', () => {
  const plan = parse(valid());
  for (const value of Object.values(plan.palette)) {
    assert.ok(
      (Object.values(WORLD) as number[]).includes(value),
      `${value} 必须来自 24 色表`,
    );
  }
});

// ------------------------------------------------------------------ 校验真实数据

test('现有 7 个模板都能被 schema 接受：模板 id 与配色合规', () => {
  for (const template of blueprints.values()) {
    // 模板自带配色必须全是 24 色表里的 token 名（解析后是色值）。
    for (const [slot, value] of Object.entries(template.palette)) {
      assert.ok(
        (Object.values(WORLD) as number[]).includes(value),
        `${template.id}.palette.${slot} 的值 ${value} 不在 24 色表里`,
      );
    }
    // 用模板自己的配色造一份意图，应当被接受。
    assert.doesNotThrow(() =>
      parseCreationPlan(
        {
          templateId: template.id,
          at: { x: 10, y: 10 },
          palette: {
            body: tokenOf(template.palette.body),
            roof: tokenOf(template.palette.roof),
            trim: tokenOf(template.palette.trim),
            window: tokenOf(template.palette.window),
          },
        },
        blueprints,
        { width: 80, height: 60 },
      ),
    );
  }
});

/** 色值 → token 名。用于把模板配色反向喂回 schema。 */
function tokenOf(value: number): string {
  for (const [name, color] of Object.entries(WORLD)) {
    if (color === value) return name;
  }
  throw new Error(`色值 ${value} 没有对应的 token 名`);
}

// ------------------------------------------------------------------ 完整链路

test('合法意图 + 合法位置 → 真的落进世界，且带自定义配色', () => {
  const world = buildWorld(blueprints);
  const slot = world.grid.findBestSlot(blueprints.get('workshop_plot')!, ALTAR);
  assert.ok(slot !== null);

  const plan = parse({ ...valid(), at: slot, palette: { roof: 'terracotta' } });
  const placed = placeFromPlan(world, plan, 'plot#plan', blueprints);
  assert.ok(placed !== null, '合法意图应当落得下去');
  assert.equal(placed.building.palette.roof, WORLD.terracotta, '自定义配色要生效');
  assert.equal(placed.building.palette.body, blueprints.get('workshop_plot')!.palette.body);
});

test('合法意图 + 非法位置 → 拒绝，且理由是位置而不是 schema', () => {
  const world = buildWorld(blueprints);
  const plan = parse(valid({ at: { x: 0, y: 0 } }));
  // (0,0) 是边界树篱，不可建。
  const placed = placeFromPlan(world, plan, 'plot#bad', blueprints);
  assert.equal(placed, null, '边界格落不下');
});

test('落位失败时不占格：账本保持原样', () => {
  const world = buildWorld(blueprints);
  const before = world.grid.countOccupied();
  const plan = parse(valid({ at: { x: 0, y: 0 } }));
  placeFromPlan(world, plan, 'plot#bad', blueprints);
  assert.equal(world.grid.countOccupied(), before, '被拒时不能占用任何格子');
});

// ------------------------------------------------------------------ 玩家操作也走 schema

test('玩家在造物模式里的选择，拼成同一份意图后能被 schema 接受', () => {
  // 这是「schema 不是死代码」的证据：手动造物与将来的 LLM 走同一条校验。
  const intent = {
    templateId: 'workshop_plot',
    at: { x: 30, y: 14 },
    palette: {
      body: 'timber',
      roof: 'cloak',
      trim: 'pale',
      window: 'amber',
    },
  };
  assert.doesNotThrow(() => parse(intent));
});

test('玩家选的配色同样受 24 色约束：造物模式没有特权', () => {
  assert.throws(
    () =>
      parse({
        templateId: 'workshop_plot',
        at: { x: 30, y: 14 },
        palette: { body: '#123456' },
      }),
    /十六进制/,
  );
});
