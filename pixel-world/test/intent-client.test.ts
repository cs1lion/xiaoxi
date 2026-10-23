import { test } from 'node:test';
import assert from 'node:assert/strict';
import { IntentClientError, createIntentClient } from '../src/world/intent-client.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { routeIntent } from '../src/world/intent-router.ts';
import { readFileSync } from 'node:fs';

const templates = parseBlueprintTable(
  JSON.parse(readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8')),
);
const WORLD = { width: 96, height: 72 };

/** 造一个假 fetch：记下请求，返回给定响应。 */
function fakeFetch(handler: (init: RequestInit | undefined) => Response | Promise<Response>) {
  const calls: { url: string; init: RequestInit | undefined }[] = [];
  const impl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return handler(init);
  }) as typeof fetch;
  return { impl, calls };
}

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

// ---------------------------------------------------------------- 请求形状

test('请求带上原话与模板表', async () => {
  const { impl, calls } = fakeFetch(() =>
    jsonResponse({ intent: { kind: 'complete' }, model: 'x', source: 'model' }),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  await client.classify('把那份报告弄一下');

  assert.equal(calls.length, 1);
  assert.equal(calls[0]?.url, '/api/world/intent');
  const body = JSON.parse(String(calls[0]?.init?.body)) as {
    utterance: string;
    templates: { id: string; displayName: string; boundTask: string | null }[];
  };
  assert.equal(body.utterance, '把那份报告弄一下', '原话不改写');
  assert.equal(body.templates.length, templates.size, '模板表全发过去');
  const mine = body.templates.find((t) => t.id === 'crawler_mine');
  assert.equal(mine?.displayName, '爬虫矿洞');
  assert.equal(mine?.boundTask, 'research');
});

test('模板表是调用时取的，不是构造时快照', async () => {
  const later = new Map(templates);
  later.set('new_build', {
    ...templates.get('workshop_plot')!,
    id: 'new_build',
    displayName: '新楼',
  });
  let current = templates;
  const { impl, calls } = fakeFetch(() => jsonResponse({ intent: { kind: 'complete' } }));
  const client = createIntentClient(() => current, { fetchImpl: impl });

  await client.classify('建个东西');
  current = later;
  await client.classify('建个东西');

  const second = JSON.parse(String(calls[1]?.init?.body)) as { templates: { id: string }[] };
  assert.ok(second.templates.some((t) => t.id === 'new_build'), '新模板下一次就问得到了');
});

// ---------------------------------------------------------------- 信封

test('从信封里取出 intent，不是整份响应', async () => {
  const { impl } = fakeFetch(() =>
    jsonResponse({ intent: { kind: 'setWeather', weather: 'rain' }, model: 'm', source: 'model' }),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  assert.deepEqual(await client.classify('下雨吧'), { kind: 'setWeather', weather: 'rain' });
});

test('响应缺 intent 字段 → 失败，不让 undefined 混进校验器', async () => {
  const { impl } = fakeFetch(() => jsonResponse({ model: 'm' }));
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  await assert.rejects(() => client.classify('下雨吧'), /缺少 intent 字段/);
});

// ---------------------------------------------------------------- 失败原因

test('503 的 detail 被带回来（说清是缺 Key）', async () => {
  const { impl } = fakeFetch(() =>
    jsonResponse({ detail: '未配置 LLM_API_KEY，意图模型不可用' }, 503),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  await assert.rejects(
    () => client.classify('下雨吧'),
    (error: unknown) => {
      assert.ok(error instanceof IntentClientError);
      assert.equal(error.status, 503);
      assert.match(error.message, /LLM_API_KEY/);
      return true;
    },
  );
});

test('网络失败带原因回来', async () => {
  const { impl } = fakeFetch(() => {
    throw new TypeError('Failed to fetch');
  });
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  await assert.rejects(() => client.classify('下雨吧'), /意图服务不可达：Failed to fetch/);
});

test('超时被当成不可达，不是永远挂着', async () => {
  const { impl } = fakeFetch(
    (init) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
      }),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl, timeoutMs: 20 });
  await assert.rejects(() => client.classify('下雨吧'), /超时 20ms/);
});

test('响应不是 JSON → 失败', async () => {
  const { impl } = fakeFetch(() => new Response('<html>oops</html>', { status: 200 }));
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  await assert.rejects(() => client.classify('下雨吧'), /不是 JSON/);
});

// ---------------------------------------------------------------- 与路由合起来的端到端

test('客户端 + 路由：模型判出的意图真的生效', async () => {
  const { impl } = fakeFetch(() =>
    jsonResponse({
      intent: { kind: 'build', templateId: 'workshop_plot', at: { x: 30, y: 20 } },
      model: 'm',
      source: 'model',
    }),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  const result = await routeIntent('把那份报告弄一下', {
    templates,
    worldSize: WORLD,
    model: client,
  });
  assert.equal(result.source, 'model');
  assert.deepEqual(result.intent, {
    kind: 'build',
    templateId: 'workshop_plot',
    at: { x: 30, y: 20 },
  });
});

test('客户端 + 路由：服务端 503 时世界不动', async () => {
  const { impl } = fakeFetch(() => jsonResponse({ detail: '未配置 LLM_API_KEY' }, 503));
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  const result = await routeIntent('把那份报告弄一下', {
    templates,
    worldSize: WORLD,
    model: client,
  });
  assert.equal(result.intent.kind, 'unknown');
  assert.equal(result.source, 'none');
  assert.match(result.modelError ?? '', /LLM_API_KEY/);
});

test('客户端 + 路由：模型编造模板时世界不动', async () => {
  const { impl } = fakeFetch(() =>
    jsonResponse({ intent: { kind: 'build', templateId: 'dragon_tower' } }),
  );
  const client = createIntentClient(() => templates, { fetchImpl: impl });
  const result = await routeIntent('建个龙塔', { templates, worldSize: WORLD, model: client });
  assert.equal(result.intent.kind, 'unknown');
  assert.match(result.modelError ?? '', /未知模板/);
});
