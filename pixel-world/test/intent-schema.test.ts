import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IntentSchemaError, parseIntent } from '../src/world/intent-schema.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { readFileSync } from 'node:fs';

const templates = parseBlueprintTable(
  JSON.parse(readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8')),
);
const WORLD = { width: 96, height: 72 };

const ok = (raw: unknown) => parseIntent(raw, templates, WORLD);
const bad = (raw: unknown): string => {
  try {
    ok(raw);
  } catch (error) {
    if (error instanceof IntentSchemaError) return error.message;
    throw error;
  }
  throw new Error('期望被拒绝，但通过了');
};

// ---------------------------------------------------------------- 合法意图

test('派活：任务类型与摘要都要合法', () => {
  assert.deepEqual(ok({ kind: 'dispatch', taskKind: 'code', summary: '写个爬虫' }), {
    kind: 'dispatch',
    taskKind: 'code',
    summary: '写个爬虫',
  });
});

test('交付与撤销没有载荷', () => {
  assert.deepEqual(ok({ kind: 'complete' }), { kind: 'complete' });
  assert.deepEqual(ok({ kind: 'cancelPending' }), { kind: 'cancelPending' });
});

test('天气只有晴与雨', () => {
  assert.deepEqual(ok({ kind: 'setWeather', weather: 'rain' }), {
    kind: 'setWeather',
    weather: 'rain',
  });
});

test('造物：模板必须是注册表里有的', () => {
  assert.deepEqual(ok({ kind: 'build', templateId: 'code_forge' }), {
    kind: 'build',
    templateId: 'code_forge',
    at: null,
  });
});

test('造物：位置可省略，也可给整数格', () => {
  assert.deepEqual(ok({ kind: 'build', templateId: 'code_forge', at: { x: 32, y: 14 } }), {
    kind: 'build',
    templateId: 'code_forge',
    at: { x: 32, y: 14 },
  });
});

test('拆：只要模板 id', () => {
  assert.deepEqual(ok({ kind: 'demolish', templateId: 'ink_house' }), {
    kind: 'demolish',
    templateId: 'ink_house',
  });
});

test('神迹：三件，可带坐标', () => {
  assert.deepEqual(ok({ kind: 'miracle', miracle: 'tree', at: { x: 40, y: 45 } }), {
    kind: 'miracle',
    miracle: 'tree',
    at: { x: 40, y: 45 },
  });
});

test('unknown 是一等公民：听不出就该说听不出', () => {
  assert.deepEqual(ok({ kind: 'unknown', reason: '这句话没有要求世界做任何事' }), {
    kind: 'unknown',
    reason: '这句话没有要求世界做任何事',
  });
});

// ---------------------------------------------------------------- 拒绝

test('未知意图被拒，并列出可选值', () => {
  const why = bad({ kind: 'teleport' });
  assert.match(why, /未知意图 "teleport"/);
  assert.match(why, /dispatch/);
});

test('未知字段被拒，不静默忽略', () => {
  assert.match(
    bad({ kind: 'complete', confidence: 0.9 }),
    /不接受字段 "confidence"/,
    '多写字段说明模型对形状的理解已经偏了',
  );
  assert.match(bad({ kind: 'unknown', reason: '不懂', at: { x: 1, y: 1 } }), /不接受字段 "at"/);
});

test('模型不能发明建筑', () => {
  assert.match(bad({ kind: 'build', templateId: 'dragon_tower' }), /未知模板 "dragon_tower"/);
});

test('越界的坐标被拒，且说清范围', () => {
  const why = bad({ kind: 'build', templateId: 'code_forge', at: { x: 96, y: 0 } });
  assert.match(why, /在世界之外/);
  assert.match(why, /0\.\.95/);
});

test('非整数坐标被拒', () => {
  assert.match(
    bad({ kind: 'build', templateId: 'code_forge', at: { x: 1.5, y: 2 } }),
    /at\.x 必须是整数/,
  );
});

test('at 里多一个字段也被拒', () => {
  assert.match(
    bad({ kind: 'build', templateId: 'code_forge', at: { x: 1, y: 2, z: 3 } }),
    /at 不接受字段 "z"/,
  );
});

test('枚举外取值被拒，并列出取值域', () => {
  assert.match(bad({ kind: 'setWeather', weather: 'snow' }), /snow.*clear \/ rain/);
  assert.match(bad({ kind: 'miracle', miracle: 'dragon' }), /dragon.*light \/ tree \/ cat/);
  assert.match(bad({ kind: 'dispatch', taskKind: 'cook', summary: '做饭' }), /cook/);
});

test('空摘要与空理由被拒', () => {
  assert.match(bad({ kind: 'dispatch', taskKind: 'code', summary: '  ' }), /summary 不能为空/);
  assert.match(bad({ kind: 'unknown', reason: '   ' }), /reason 不能为空/);
});

test('整份 JSON 不是对象时被拒', () => {
  for (const raw of [null, 'hello', 42, [1, 2]]) {
    assert.match(bad(raw), /意图必须是对象/);
  }
});

// ---------------------------------------------------------------- 边界

test('quick 是合法任务类型：短活也走派活', () => {
  assert.equal(ok({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' }).kind, 'dispatch');
});

test('at 为 null 与省略等价', () => {
  assert.deepEqual(
    ok({ kind: 'miracle', miracle: 'cat', at: null }),
    ok({ kind: 'miracle', miracle: 'cat' }),
  );
});

test('未知模板时把可选模板全列出来，模型才有机会改对', () => {
  const why = bad({ kind: 'build', templateId: 'nope' });
  for (const id of templates.keys()) assert.ok(why.includes(id), `提示里缺 ${id}`);
});
