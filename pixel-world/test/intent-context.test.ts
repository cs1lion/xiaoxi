/**
 * 意图层把世界状态喂给模型（`docs/WORLD_INTENT.md` §八 原先的已知边界）。
 *
 * 三条要锁住的性质：
 *
 *   1. **上下文只描述现状，不接管判定。** 已经有某栋楼时模型仍该输出 build，
 *      让世界给出准确拒绝；把这一点写进提示词并被测试盯住，是因为它最容易
 *      在"接上状态"之后被顺手改成"有楼就答 unknown"。
 *   2. **省略上下文时整段不出现。** 写「世界状态未知」会让模型以为该去猜。
 *   3. **不发可推断的细节。** 只有建筑名与产能计数，没有坐标、占地、任务 id。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { buildLargeWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { summarizeForIntent, renderIntentContext } from '../src/world/intent-context.ts';
import { buildIntentPrompt } from '../src/world/intent-prompt.ts';
import { createIntentClient } from '../src/world/intent-client.ts';
import { routeIntent } from '../src/world/intent-router.ts';

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
    blueprints,
  });
}

// ------------------------------------------------------------ 上下文归纳

test('上下文按镇内 / 郊野分组列出已有建筑', () => {
  const context = summarizeForIntent(makeSim().snapshot());
  const townIds = context.built.town.map((entry) => entry.templateId);
  const wildIds = context.built.wild.map((entry) => entry.templateId);
  assert.ok(townIds.includes('code_forge'), '镇内应当有炼码工坊');
  assert.ok(wildIds.includes('crawler_mine'), '郊野应当有爬虫矿洞');
  // 民居不接活，但它确实在世界里——按郊野/镇内分组，不按接不接活分。
  assert.ok(
    [...townIds, ...wildIds].some((id) => id.startsWith('cottage_')),
    '镇内民居应当出现在镇内那一组',
  );
});

test('产能按活类型数，静息的也算', () => {
  const context = summarizeForIntent(makeSim().snapshot());
  const byKind = new Map(context.capacity.map((entry) => [entry.taskKind, entry.count]));
  for (const kind of ['code', 'research', 'writing'] as const) {
    assert.equal(byKind.get(kind), 2, `${kind} 应当有镇内 + 郊野各一栋`);
  }
});

test('开始没人干活，派活之后有人忙', () => {
  const sim = makeSim();
  const before = summarizeForIntent(sim.snapshot());
  assert.equal(before.busy.length, 0, '开局没人忙');
  assert.equal(before.queued, 0, '开局没有排队');

  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写个爬虫' });
  sim.update(16);
  // 走几拍让她真的接单上路。
  for (let i = 0; i < 20; i += 1) sim.update(100);

  const after = summarizeForIntent(sim.snapshot());
  assert.ok(after.busy.length > 0, '派活之后应当有人忙着');
  assert.equal(after.busy[0]?.taskKind, 'code');
});

test('已建模板列在 exhausted 里：与世界的拒绝一致', () => {
  const context = summarizeForIntent(makeSim().snapshot());
  assert.ok(context.exhausted.includes('code_forge'));
  assert.ok(context.exhausted.includes('workshop_plot') === false, '还没建的模板不算耗尽');
});

test('不出现坐标：模型拿不到可推断的实现细节', () => {
  const context = summarizeForIntent(makeSim().snapshot());
  const text = renderIntentContext(context);
  assert.doesNotMatch(text, /\d+\s*,\s*\d+/, '渲染结果里不该有坐标对');
  // 结构层面也确认：条目只有 templateId 与 name 两个键。
  for (const entry of [...context.built.town, ...context.built.wild]) {
    assert.deepEqual(Object.keys(entry).sort(), ['name', 'templateId']);
  }
});

// ------------------------------------------------------------ 提示词

test('提示词包含世界现状，并说明它不改变判断规则', () => {
  const context = summarizeForIntent(makeSim().snapshot());
  const prompt = buildIntentPrompt(blueprints, context);
  assert.match(prompt, /世界现在长什么样/);
  assert.match(prompt, /炼码工坊/);
  // 这四条是实测逼出来的：接上现状后模型一度开始替世界判可行性，
  // 「给我整个新铺子」从 build 退成 unknown（理由："没有指定铺子类型"）。
  assert.match(prompt, /现状不改变你输出的命令/, '少了这句，模型会拿现状当判据');
  assert.match(prompt, /仍然照常输出 build/, '已有建筑时仍要输出 build，让世界拒绝');
  assert.match(prompt, /只有用户在问现状时才答 unknown/, '要分清「打听」与「要求」');
  assert.match(
    prompt,
    /看用户.*要你做什么.*不看世界/s,
    '判断口诀要写明：看要求，不看现状',
  );
});

test('不给上下文时整段不出现，也不写「未知」', () => {
  const prompt = buildIntentPrompt(blueprints);
  assert.doesNotMatch(prompt, /世界现在长什么样/);
  assert.doesNotMatch(prompt, /世界状态未知/);
});

// ------------------------------------------------------------ 请求

test('客户端把上下文放进请求体', async () => {
  const calls: { body: string }[] = [];
  const impl = (async (_url: string | URL | Request, init?: RequestInit) => {
    calls.push({ body: String(init?.body) });
    return new Response(JSON.stringify({ intent: { kind: 'complete' }, model: 'm', source: 'model' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;

  const context = summarizeForIntent(makeSim().snapshot());
  const client = createIntentClient(() => blueprints, {
    fetchImpl: impl,
    context: () => context,
  });
  await client.classify('建个工坊');

  const body = JSON.parse(calls[0]!.body) as { context?: { exhausted: string[] } };
  assert.ok(body.context !== undefined, '应当带上 context');
  assert.ok(body.context.exhausted.includes('code_forge'));
});

test('不提供 context 时请求体里没有这个字段', async () => {
  const calls: { body: string }[] = [];
  const impl = (async (_url: string | URL | Request, init?: RequestInit) => {
    calls.push({ body: String(init?.body) });
    return new Response(JSON.stringify({ intent: { kind: 'complete' }, model: 'm', source: 'model' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  }) as typeof fetch;

  const client = createIntentClient(() => blueprints, { fetchImpl: impl });
  await client.classify('收工');

  const body = JSON.parse(calls[0]!.body) as Record<string, unknown>;
  assert.equal('context' in body, false, '省略而不是发 null');
});

// ------------------------------------------------------------ 路由

test('上下文送到模型手上，但不改变校验', async () => {
  const context = summarizeForIntent(makeSim().snapshot());
  let received: unknown = null;

  // 「把那间新工坊张罗起来」——规则三连都认不出（没有建/盖/造开头，
  // 也没有派活动词），才会落到模型那一路。
  const result = await routeIntent('把那间新工坊张罗起来', {
    templates: blueprints,
    worldSize: { width: 96, height: 72 },
    context,
    model: {
      async classify(_utterance, passed) {
        received = passed ?? null;
        // 模型照常输出 build，即使 workshop_plot 已经在 exhausted 里。
        return { kind: 'build', templateId: 'workshop_plot' };
      },
    },
  });

  assert.deepEqual(received, context, '模型应当收到上下文');
  assert.equal(result.intent.kind, 'build', '校验不看上下文：仍是一份合法的 build');
  assert.equal(result.source, 'model');
});

test('规则认得出时根本不问模型，上下文也就不参与', async () => {
  let asked = 0;
  const result = await routeIntent('下雨吧', {
    templates: blueprints,
    worldSize: { width: 96, height: 72 },
    context: summarizeForIntent(makeSim().snapshot()),
    model: {
      async classify() {
        asked += 1;
        return { kind: 'unknown', reason: '不该被问到' };
      },
    },
  });
  assert.equal(asked, 0, '规则命中就不该调模型');
  assert.equal(result.source, 'rule');
  assert.equal(result.intent.kind, 'setWeather');
});
