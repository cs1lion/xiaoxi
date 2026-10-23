import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Simulation } from '../src/world/simulation.ts';
import type { SimulationDeps } from '../src/world/simulation.ts';
import type { IntentModel } from '../src/world/intent-router.ts';
import { buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { readFileSync } from 'node:fs';

const templates = parseBlueprintTable(
  JSON.parse(readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8')),
);

function makeSim(model?: IntentModel): Simulation {
  const world = buildWorld(templates);
  const deps: SimulationDeps = {
    grid: world.grid,
    buildings: world.buildings,
    altar: world.altar,
    villagerName: '阿禾',
    blueprints: templates,
    intentModel: model,
    // 给世界一个造物主，模型判出的 build 才有地方落。
    spawnBuilding: (prefabId, instanceId, at, source) => {
      const blueprint = templates.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, world.altar, at, source);
    },
  };
  return new Simulation(deps);
}

/**
 * 推几拍，让命令队列结算。
 *
 * 模型是 async 的：先让微任务队列排空，再推拍子——否则模型还没回来，
 * 队列里就只有原来那一条。
 */
async function settle(sim: Simulation, ticks = 3): Promise<void> {
  for (let i = 0; i < ticks; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
    sim.update(16);
  }
}

const say = (sim: Simulation, text: string) => sim.enqueue({ kind: 'oracle', raw: text });

/** 推一拍，返回本拍结算出的所有回执。 */
function tick(sim: Simulation): readonly { ok: boolean; message: string }[] {
  return sim.update(16);
}

// ---------------------------------------------------------------- 快环不阻塞

test('接了模型之后，投递仍然同步返回，不等网络', () => {
  let released: (() => void) | undefined;
  const model: IntentModel = {
    classify: () => new Promise((resolve) => { released = () => resolve({ kind: 'complete' }); }),
  };
  const sim = makeSim(model);

  // 关键：模型永远不回来，投递与结算也照常发生。
  say(sim, '把那份报告弄一下');
  const started = Date.now();
  tick(sim);
  assert.ok(Date.now() - started < 100, '一拍不得等网络');
  assert.ok(released !== undefined, '模型已经在后台被问了一次');
});

test('规则认得出的句子根本不问模型', async () => {
  const calls: string[] = [];
  const model: IntentModel = {
    classify: async (text) => { calls.push(text); return { kind: 'complete' }; },
  };
  const sim = makeSim(model);
  say(sim, '帮我写个爬虫');
  await settle(sim);
  assert.deepEqual(calls, [], '规则命中时一次都不问');
});

// ---------------------------------------------------------------- 模型结果回队

test('模型的意图回到命令队列并生效', async () => {
  const model: IntentModel = {
    classify: async () => ({ kind: 'setWeather', weather: 'rain' }),
  };
  const sim = makeSim(model);
  assert.equal(sim.snapshot().weather, 'clear');

  say(sim, '把那份报告弄一下');
  await settle(sim);
  assert.equal(sim.snapshot().weather, 'rain', '模型的意图真的改变了世界');
});

test('模型判出的造物，落的还是同一套校验', async () => {
  const model: IntentModel = {
    classify: async () => ({ kind: 'build', templateId: 'workshop_plot' }),
  };
  const sim = makeSim(model);
  const before = sim.snapshot().buildings.length;
  say(sim, '把那份报告弄一下');
  await settle(sim);
  assert.equal(sim.snapshot().buildings.length, before + 1);
});

// ---------------------------------------------------------------- 失败必须不动世界

test('模型抛错 → 世界不动，也不猜', async () => {
  const model: IntentModel = {
    classify: async () => { throw new Error('timeout'); },
  };
  const sim = makeSim(model);
  const before = sim.snapshot();
  say(sim, '把那份报告弄一下');
  await settle(sim);
  const after = sim.snapshot();
  assert.equal(after.weather, before.weather);
  assert.equal(after.buildings.length, before.buildings.length);
  assert.equal(after.tasks.length, before.tasks.length);
});

test('模型输出非法 → 世界不动', async () => {
  const model: IntentModel = { classify: async () => ({ kind: 'teleport' }) };
  const sim = makeSim(model);
  const before = sim.snapshot();
  say(sim, '瞬移吧');
  await settle(sim);
  assert.equal(sim.snapshot().buildings.length, before.buildings.length);
  assert.equal(sim.snapshot().weather, before.weather);
});

test('模型说 unknown → 世界不动', async () => {
  const model: IntentModel = {
    classify: async () => ({ kind: 'unknown', reason: '在征求意见' }),
  };
  const sim = makeSim(model);
  const before = sim.snapshot();
  say(sim, '你觉得该怎么办');
  await settle(sim);
  assert.equal(sim.snapshot().buildings.length, before.buildings.length);
});

// ---------------------------------------------------------------- 离线降级

test('没有模型时，认不出就是认不出', () => {
  const sim = makeSim();
  say(sim, '把那份报告弄一下');
  const result = tick(sim)[0];
  assert.ok(result !== undefined, '应当有一条回执');
  assert.equal(result.ok, false);
  assert.match(result.message, /没听懂/);
});

test('同一句话连着说三遍，只问模型一次', async () => {
  let calls = 0;
  const model: IntentModel = {
    classify: async () => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      return { kind: 'unknown', reason: '闲聊' };
    },
  };
  const sim = makeSim(model);
  say(sim, '今天好累啊');
  tick(sim);
  say(sim, '今天好累啊');
  tick(sim);
  say(sim, '今天好累啊');
  await settle(sim, 6);
  assert.equal(calls, 1, '去重是按原话，不是按时间窗');
});
