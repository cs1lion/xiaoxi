import { test } from 'node:test';
import assert from 'node:assert/strict';
import { routeIntent } from '../src/world/intent-router.ts';
import type { IntentModel } from '../src/world/intent-router.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { readFileSync } from 'node:fs';

const templates = parseBlueprintTable(
  JSON.parse(readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8')),
);
const WORLD = { width: 96, height: 72 };

/** 一个记账用的假模型：记下被问过几次、问的是什么。 */
function fakeModel(answer: unknown | (() => Promise<unknown>)): IntentModel & {
  readonly calls: string[];
} {
  const calls: string[] = [];
  return {
    calls,
    async classify(utterance: string) {
      calls.push(utterance);
      if (typeof answer === 'function') return (answer as () => Promise<unknown>)();
      return answer;
    },
  };
}

const route = (text: string, model?: IntentModel) =>
  routeIntent(text, { templates, worldSize: WORLD, model });

// ---------------------------------------------------------------- 规则优先

test('规则认得出的就不问模型', async () => {
  const model = fakeModel({ kind: 'unknown', reason: '不该被问到' });
  const cases: [string, string][] = [
    ['帮我写个爬虫', 'dispatch'],
    ['建个工坊', 'build'],
    ['点灯', 'miracle'],
    ['下雨吧', 'setWeather'],
    ['交付', 'complete'],
  ];
  for (const [text, kind] of cases) {
    const result = await route(text, model);
    assert.equal(result.intent.kind, kind, `「${text}」应当由规则认出`);
    assert.equal(result.source, 'rule');
  }
  assert.deepEqual(model.calls, [], '规则命中时一次都不该问模型');
});

test('规则的结果与 speak() 的既有行为一致', async () => {
  // 接模型不该改变任何已能被规则稳定识别的行为。
  assert.deepEqual((await route('建个工坊')).intent, {
    kind: 'build',
    templateId: 'workshop_plot',
    at: null,
  });
  assert.deepEqual((await route('在 (40,45) 种树')).intent, {
    kind: 'miracle',
    miracle: 'tree',
    at: { x: 40, y: 45 },
  });
  assert.deepEqual((await route('拆掉抄书斋')).intent, {
    kind: 'demolish',
    templateId: 'ink_house',
  });
  assert.deepEqual((await route('放晴吧')).intent, { kind: 'setWeather', weather: 'clear' });
});

test('拆的解析器优先于建：同一句「拆掉抄书斋」不会被认成建', async () => {
  assert.equal((await route('拆掉抄书斋')).intent.kind, 'demolish');
});

// ---------------------------------------------------------------- 模型兜底

test('同义改写正是模型该接的那类：规则词表认不出「种棵树」', async () => {
  // 规则词表是「种树 / 种一棵树 / 栽树」，没有「种棵树」。
  // 这不是规则的 bug，是规则的边界——补词表会无限膨胀，这类活该由模型接。
  assert.equal((await route('在 (40,45) 种棵树')).intent.kind, 'unknown', '离线时认不出');

  const model = fakeModel({ kind: 'miracle', miracle: 'tree', at: { x: 40, y: 45 } });
  const result = await route('在 (40,45) 种棵树', model);
  assert.equal(result.source, 'model');
  assert.deepEqual(result.intent, { kind: 'miracle', miracle: 'tree', at: { x: 40, y: 45 } });
});

test('规则认不出时问模型，模型的结果被采纳', async () => {
  const model = fakeModel({ kind: 'dispatch', taskKind: 'writing', summary: '把季度报告弄一下' });
  const result = await route('把那份季度报告弄一下', model);
  assert.equal(result.source, 'model');
  assert.deepEqual(result.intent, {
    kind: 'dispatch',
    taskKind: 'writing',
    summary: '把季度报告弄一下',
  });
  assert.deepEqual(model.calls, ['把那份季度报告弄一下'], '问的是原话，不改写');
});

test('模型说 unknown，路由就认 unknown', async () => {
  const model = fakeModel({ kind: 'unknown', reason: '在征求意见' });
  const result = await route('你觉得该怎么办', model);
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'model', 'unknown 也是模型的合法输出，不是失败');
  assert.equal(result.modelError, null);
});

// ---------------------------------------------------------------- 失败必须落到 unknown

test('模型抛错 → unknown，不猜', async () => {
  const model = fakeModel(async () => {
    throw new Error('network down');
  });
  const result = await route('把那份报告弄一下', model);
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'none');
  assert.match(result.modelError ?? '', /network down/);
});

test('模型返回非法 JSON 形状 → unknown，并说清哪里不合法', async () => {
  const model = fakeModel({ kind: 'teleport', to: 'moon' });
  const result = await route('瞬移吧', model);
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'none');
  assert.match(result.modelError ?? '', /未知意图 "teleport"/);
});

test('模型发明建筑 → unknown，不是"建个最像的"', async () => {
  const model = fakeModel({ kind: 'build', templateId: 'dragon_tower' });
  const result = await route('建个龙塔', model);
  assert.equal(result.intent.kind, 'unknown');
  assert.match(result.modelError ?? '', /未知模板/);
});

// ---------------------------------------------------------------- 离线与边界

test('没有模型时，规则认不出就是 unknown', async () => {
  const result = await route('把那份季度报告弄一下');
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'none');
  assert.equal(result.modelError, null, '离线不是错误');
});

test('空句子不问模型', async () => {
  const model = fakeModel({ kind: 'complete' });
  for (const text of ['', '   ']) {
    const result = await route(text, model);
    assert.equal(result.intent.kind, 'unknown');
    assert.equal(result.source, 'none');
  }
  assert.deepEqual(model.calls, []);
});

test('闲聊不会被模型硬翻成命令：模型自己也该答 unknown', async () => {
  // 这条测的是契约：模型若答 unknown，路由原样透传，世界不动。
  const model = fakeModel({ kind: 'unknown', reason: '闲聊' });
  const result = await route('今天天气不错', model);
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'model');
});
