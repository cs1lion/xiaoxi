import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  project,
  parseCommissionRow,
  parseAgentRow,
  kindForAgent,
} from '../src/world/projection.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation, ledgerCompletedAtMs } from '../src/world/simulation.ts';
import type { SimulationDeps } from '../src/world/simulation.ts';
import { assertSave } from '../src/world/save.ts';
import {
  LedgerClient,
  reconnectDelay,
  type LedgerSocket,
  type LedgerTimers,
  type LedgerView,
} from '../src/world/ledger.ts';
import type { AgentRow, CommissionRow } from '../src/world/projection.ts';
import { BLOCKED_QUESTION_LIMIT, excerptBlockedQuestion } from '../src/world/types.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

function commission(over: Partial<CommissionRow> & { id: string }): CommissionRow {
  return {
    id: over.id,
    title: over.title ?? '某件事',
    agent_id: over.agent_id ?? '',
    status: over.status ?? 'running',
    step_idx: over.step_idx ?? 0,
    blocked_question: over.blocked_question ?? '',
    completedAt: over.completedAt ?? null,
  };
}

const AGENTS: AgentRow[] = [
  { id: 'claude', name: '真机 Claude', strengths: ['实现', '代码', '文件', '写入'] },
  { id: 'scout', name: '探马', strengths: ['检索', '查找', '搜索', '资料'] },
  { id: 'scribe', name: '笔录官', strengths: ['写作', '整理', '标题', '便笺'] },
];

function simDeps(): SimulationDeps {
  const world = buildWorld(blueprints);
  return {
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      return blueprint === undefined ? null : placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  };
}

function makeSim(): Simulation {
  return new Simulation(simDeps());
}

// ---------------------------------------------------------------- projection

test('按能力关键词把 Agent 归到三类活', () => {
  const code = kindForAgent(AGENTS[0], commission({ id: 'a' }));
  assert.equal(code, 'code');
  const research = kindForAgent(AGENTS[1], commission({ id: 'b' }));
  assert.equal(research, 'research');
  const writing = kindForAgent(AGENTS[2], commission({ id: 'c' }));
  assert.equal(writing, 'writing');
});

test('认不出的 Agent 落到写作，不冒充代码', () => {
  assert.equal(kindForAgent(undefined, commission({ id: 'x', title: '莫名其妙' })), 'writing');
});

test('只有 done 进 finished：ready_to_report 是待验收，不是成功', () => {
  const result = project({
    agents: AGENTS,
    commissions: [
      commission({ id: 'live', status: 'running', agent_id: 'claude' }),
      commission({ id: 'over', status: 'ready_to_report', agent_id: 'scribe' }),
      commission({ id: 'archived', status: 'done', agent_id: 'scribe' }),
    ],
  });
  // ready_to_report 既不驱动开工，也不算交付——世界要等老板点头。
  assert.deepEqual(result.tasks.map((t) => t.commissionId), ['live']);
  assert.deepEqual(result.finished.map((t) => t.commissionId), ['archived']);
  // 反例锁：曾经这里把 ready_to_report 也算 finished，导致验收前就加信仰点。
  assert.ok(
    !result.finished.some((t) => t.status === 'ready_to_report'),
    'ready_to_report 不得进 finished（ADR_AGENT_BRIDGE §4.3：待验收不是成功）',
  );
});

test('blocked 优先被挑出来，因为它要请示', () => {
  const result = project({
    agents: AGENTS,
    commissions: [
      commission({ id: 'a_running', status: 'running' }),
      commission({ id: 'b_blocked', status: 'blocked', blocked_question: '选哪个？' }),
    ],
  });
  assert.equal(result.active?.commissionId, 'b_blocked');
});

test('drafting 不进世界：还没派出去的单不该让铺子开工', () => {
  const result = project({
    agents: AGENTS,
    commissions: [commission({ id: 'draft', status: 'drafting' })],
  });
  assert.equal(result.tasks.length, 0);
  assert.equal(result.active, null);
});

test('同级按 id 稳定排序，不随输入顺序变', () => {
  const a = project({ agents: AGENTS, commissions: [commission({ id: 'b' }), commission({ id: 'a' })] });
  const b = project({ agents: AGENTS, commissions: [commission({ id: 'a' }), commission({ id: 'b' })] });
  assert.equal(a.active?.commissionId, b.active?.commissionId);
});

test('缺字段的督办被拒绝，不用默认值蒙混', () => {
  assert.throws(() => parseCommissionRow({ title: '没有 id' }));
  assert.throws(() => parseCommissionRow({ id: 'a', title: 5 }));
  const ok = parseCommissionRow({ id: 'a', title: 't', agent_id: '', status: 'running' });
  assert.equal(ok.step_idx, 0);
});

test('花名册条目缺 id 时拒绝', () => {
  assert.throws(() => parseAgentRow({ name: '没有 id' }));
  const ok = parseAgentRow({ id: 'x', name: '甲', strengths: ['a', 5, 'b'] });
  assert.deepEqual(ok.strengths, ['a', 'b']);
});

// ---------------------------------------------------------------------- sync

test('台账来单：她闲着就派下去，走进对应铺子', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '真机 Claude' }],
    finished: [],
    connected: true,
  });
  sim.update(16);
  const state = sim.snapshot();
  assert.equal(state.ledger.connected, true);
  assert.equal(state.tasks.length, 1);
  assert.equal(state.tasks[0].id, 'c_1', '台账单用上游 id，不另编号');
  assert.equal(state.villager.mode, 'saluting', '派下去就进神谕态');

  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 200) throw new Error('没走到铺子');
  }
  const working = sim.snapshot();
  const operating = working.workstations.find((w) => w.activity.kind === 'operating');
  assert.ok(operating, '应该有铺子开工');
  assert.equal(operating.progress, null, '台账的活没有百分比，不许编');
});

test('台账说收工：她走正常交付，不凭空结束', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 200) throw new Error('没走到铺子');
  }
  // 不推进时间也能收尾：完成权在台账手里，不在计时器手里。
  sim.enqueue({ kind: 'sync', tasks: [], finished: ['c_1'], connected: true });
  sim.update(16);
  assert.equal(sim.snapshot().villager.mode, 'delivering');
  assert.equal(sim.snapshot().tasks.length, 0, '收工的单从活动清单里去掉');
});

test('台账撤单：她停手，铺子熄火', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 200) throw new Error('没走到铺子');
  }
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true });
  sim.update(16);
  const state = sim.snapshot();
  assert.equal(state.villager.mode, 'idle');
  assert.equal(state.villager.taskId, null);
  assert.ok(state.workstations.every((w) => w.activity.kind !== 'operating'), '铺子熄火');
});

test('离线时世界照常过日子，但状态行能读出离线', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: false });
  const result = sim.update(16);
  assert.equal(result[0].ok, false);
  assert.equal(sim.snapshot().ledger.connected, false);
  for (let i = 0; i < 50; i += 1) sim.update(100);
  assert.equal(sim.snapshot().villager.mode, 'idle', '她仍然过日子');
});

test('同一条督办重复同步不会复制成两条', () => {
  const sim = makeSim();
  const task = { commissionId: 'c_1', kind: 'code' as const, title: '写', agentName: '甲' };
  sim.enqueue({ kind: 'sync', tasks: [task], finished: [], connected: true });
  sim.update(16);
  sim.enqueue({ kind: 'sync', tasks: [task], finished: [], connected: true });
  sim.update(16);
  assert.equal(sim.snapshot().tasks.length, 1);
});

test('沙盒的活不受台账同步影响', () => {
  const sim = makeSim();
  sim.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  sim.update(16);
  assert.equal(sim.snapshot().villager.mode, 'working');
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true });
  sim.update(16);
  assert.equal(sim.snapshot().villager.mode, 'working', '台账同步不该打断沙盒的活');
});

// ------------------------------------------------- 台账连接：重连指数退避（S1-7）

interface FakeClock {
  readonly timers: LedgerTimers;
  readonly delays: number[];
  runNextTimeout(): boolean;
  /** 只放行指定周期的 interval（不传则全部跑一遍），返回触发次数。 */
  runIntervals(delayMs?: number): number;
  pendingTimeouts(): number;
  pendingIntervals(): number;
}

/** 假时钟：记下每次 setTimeout 的延迟，由测试手动放行；只实现 LedgerTimers 需要的最小面。 */
function makeClock(): FakeClock {
  let nextHandle = 1;
  const timeouts = new Map<number, () => void>();
  const intervals = new Map<number, { readonly delay: number; readonly run: () => void }>();
  const delays: number[] = [];
  const timers: LedgerTimers = {
    setTimeout(handler, ms) {
      const handle = nextHandle++;
      delays.push(ms);
      timeouts.set(handle, handler);
      return handle;
    },
    clearTimeout(handle) {
      if (typeof handle === 'number') timeouts.delete(handle);
    },
    setInterval(handler, ms) {
      const handle = nextHandle++;
      intervals.set(handle, { delay: ms, run: handler });
      return handle;
    },
    clearInterval(handle) {
      if (typeof handle === 'number') intervals.delete(handle);
    },
  };
  return {
    timers,
    delays,
    runNextTimeout() {
      const entry = [...timeouts.entries()][0] as [number, () => void] | undefined;
      if (entry === undefined) return false;
      timeouts.delete(entry[0]);
      entry[1]();
      return true;
    },
    runIntervals(delayMs) {
      let fired = 0;
      for (const interval of [...intervals.values()]) {
        if (delayMs !== undefined && interval.delay !== delayMs) continue;
        interval.run();
        fired += 1;
      }
      return fired;
    },
    pendingTimeouts: () => timeouts.size,
    pendingIntervals: () => intervals.size,
  };
}

/** 假 WebSocket：只登记监听器，open/close/message 由测试手动触发。 */
class FakeSocket implements LedgerSocket {
  readyState = 1;
  readonly url: string;
  private readonly listeners = new Map<string, ((event: unknown) => void)[]>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const list = this.listeners.get(type);
    if (list === undefined) this.listeners.set(type, [listener]);
    else list.push(listener);
  }

  close(): void {
    this.readyState = 3;
  }

  emit(type: string, event: unknown = undefined): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** 按 URL 路由的假 fetch：忽略 querystring 匹配、未登记的地址返回 404，used 记录原始地址。 */
function routedFetch(
  routes: Record<string, () => Response | Promise<Response>>,
): { fetchImpl: typeof fetch; used: string[] } {
  const used: string[] = [];
  const fetchImpl = (async (input: RequestInfo | URL) => {
    const url = String(input);
    used.push(url);
    const handler = routes[url.split('?')[0] ?? url];
    if (handler === undefined) return jsonResponse({}, 404);
    return handler();
  }) as unknown as typeof fetch;
  return { fetchImpl, used };
}

function makeLedgerClient(clock: FakeClock, fetchOverride?: typeof fetch): {
  client: LedgerClient;
  sockets: FakeSocket[];
  views: LedgerView[];
} {
  const sockets: FakeSocket[] = [];
  const views: LedgerView[] = [];
  const fetchImpl = fetchOverride ?? ((async () =>
    jsonResponse({ items: [], agents: [] })) as unknown as typeof fetch);
  const client = new LedgerClient({
    onChange: (view) => views.push(view),
    fetchImpl,
    location: { protocol: 'http:', host: '127.0.0.1:5188' },
    timers: clock.timers,
    socketFactory: (url) => {
      const socket = new FakeSocket(url);
      sockets.push(socket);
      return socket;
    },
  });
  return { client, sockets, views };
}

test('重连退避序列：500/1000/2000/4000/5000/5000', () => {
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5, 60].map((step) => reconnectDelay(step)),
    [500, 1000, 2000, 4000, 5000, 5000, 5000],
  );
  assert.equal(reconnectDelay(-1), 500, '负数按第一次算');
  assert.equal(reconnectDelay(Number.NaN), 500, '非有限的失败次数按第一次算');
});

test('断线按退避序列重连，连上后重置回 500ms', async () => {
  const clock = makeClock();
  const { client, sockets, views } = makeLedgerClient(clock);
  await client.start();
  assert.equal(sockets.length, 1, 'start 起一条连接');
  assert.equal(clock.pendingIntervals(), 2, '4s 兜底与 30s 对账两个定时器都挂着');

  const expected = [500, 1000, 2000, 4000, 5000, 5000];
  for (const delay of expected) {
    sockets[sockets.length - 1]?.emit('close');
    assert.equal(clock.delays.at(-1), delay, `断线后等待 ${delay}ms`);
    assert.equal(clock.runNextTimeout(), true);
  }
  assert.equal(sockets.length, expected.length + 1, '每次等待结束都建一条新连接');
  assert.equal(views.at(-1)?.connected, false, '断线后世界看到的是离线');

  // 重连后用一次 REST 对账把状态拉回在线（增量不补发，协议 §19）。
  sockets[sockets.length - 1]?.emit('open');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(views.at(-1)?.connected, true, '重连后状态回到在线');

  // 连上就清零：下一次断线重新从 500ms 起跳。
  sockets[sockets.length - 1]?.emit('close');
  assert.equal(clock.delays.at(-1), 500);
  client.stop();
});

test('stop 之后不再重连，也不留定时器', async () => {
  const clock = makeClock();
  const { client, sockets } = makeLedgerClient(clock);
  await client.start();
  sockets[0]?.emit('close');
  assert.equal(clock.pendingTimeouts(), 1, '断线排了一次重连');
  client.stop();
  assert.equal(clock.pendingTimeouts(), 0, 'stop 清掉重连定时器');
  assert.equal(clock.pendingIntervals(), 0, 'stop 清掉对账定时器');
  sockets[0]?.emit('close');
  assert.equal(clock.pendingTimeouts(), 0, 'dispose 后的事件不再排重连');
});

test('注入连接上的 commission_update 会进世界视图', async () => {
  const clock = makeClock();
  const { client, sockets, views } = makeLedgerClient(clock);
  await client.start();
  sockets[0]?.emit('message', {
    data: JSON.stringify({
      type: 'commission_update',
      commission: {
        id: 'c_ws',
        title: '把便笺整理成三版标题',
        agent_id: 'scribe',
        status: 'running',
        step_idx: 1,
        blocked_question: '',
      },
    }),
  });
  const last = views.at(-1);
  assert.equal(last?.connected, true);
  assert.deepEqual(last?.tasks.map((task) => task.commissionId), ['c_ws']);
  client.stop();
});

// ------------------------------------------- 花名册来源：/roster 优先，/adapters 回退（D4-A）

test('parseAgentRow 兼容新 /roster（id+strengths）与旧 /adapters（agent_id，无 strengths）', () => {
  const roster = parseAgentRow({
    id: 'claude', name: '真机 Claude', strengths: ['实现', '代码', '脚本'],
    adapter: 'cli_subproc',
  });
  assert.deepEqual(roster, { id: 'claude', name: '真机 Claude', strengths: ['实现', '代码', '脚本'] });

  const legacy = parseAgentRow({
    agent_id: 'scribe', name: '笔录官', adapter: 'manual',
    config_keys: ['adapter', 'id', 'name', 'strengths'],
  });
  assert.deepEqual(legacy, { id: 'scribe', name: '笔录官', strengths: [] },
    '旧端点只有 agent_id、没有 strengths 值：能解析且留空，不抛');

  assert.throws(() => parseAgentRow({ name: '既无 id 也无 agent_id' }), '两个 id 都缺才拒绝');
});

test('/roster 优先：strengths 决定分类，且不再请求旧 /adapters', async () => {
  const clock = makeClock();
  const { fetchImpl, used } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({
      items: [{
        id: 'c_1', title: '把这件事办了', agent_id: 'claude',
        status: 'running', step_idx: 2, blocked_question: '',
      }],
    }),
    '/api/executive/roster': () => jsonResponse({
      items: [{ id: 'claude', name: '真机 Claude', strengths: ['实现', '代码'], adapter: 'cli_subproc' }],
    }),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  assert.ok(used.includes('/api/executive/roster'), '要请求新的 roster 端点');
  assert.ok(!used.includes('/api/executive/adapters'), 'roster 成功就不该再请求旧端点');
  const task = views.at(-1)?.tasks[0];
  assert.equal(task?.agentName, '真机 Claude');
  assert.equal(task?.kind, 'code', '标题无关键词时靠 strengths 命中代码');
  client.stop();
});

test('/roster 404 时回退 /adapters，旧服务照常可用', async () => {
  const clock = makeClock();
  const { fetchImpl, used } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({
      items: [{
        id: 'c_1', title: '把便笺整理成三版标题', agent_id: 'scribe',
        status: 'running', step_idx: 1, blocked_question: '',
      }],
    }),
    // 旧服务没有 /roster：路由表不登记 → 404。
    '/api/executive/adapters': () => jsonResponse({
      types: ['manual'],
      agents: [{
        agent_id: 'scribe', name: '笔录官', adapter: 'manual',
        config_keys: ['adapter', 'id', 'name', 'strengths'],
      }],
    }),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  assert.ok(used.includes('/api/executive/roster'));
  assert.ok(used.includes('/api/executive/adapters'), 'roster 404 要回退旧端点');
  const task = views.at(-1)?.tasks[0];
  assert.equal(task?.agentName, '笔录官', '旧端点的 agent_id 也能解析出名字');
  assert.equal(task?.kind, 'writing', '没有 strengths 值就按标题关键词兜底');
  client.stop();
});

test('花名册两个来源都失败时只降级分类，不把台账判离线', async () => {
  const clock = makeClock();
  const { fetchImpl, used } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({ items: [] }),
    '/api/executive/roster': () => jsonResponse({}, 500),
    '/api/executive/adapters': () => jsonResponse({}, 500),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  assert.ok(used.includes('/api/executive/roster') && used.includes('/api/executive/adapters'));
  assert.equal(views.at(-1)?.connected, true, '花名册拉不到不影响台账在线');
  client.stop();
});

// ------------------------------- 周期对账 / 分页截断 / 请求超时（S1-5）

test('WS 在线时也每 30s 拉一次 REST 对账，4s 兜底不重复刷', async () => {
  const clock = makeClock();
  const { fetchImpl, used } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({ items: [] }),
    '/api/executive/roster': () => jsonResponse({ items: [] }),
  });
  const { client, sockets, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  sockets[0]?.emit('open');
  const commissionsRequests = (): number =>
    used.filter((url) => url.startsWith('/api/executive/commissions')).length;

  assert.equal(commissionsRequests(), 1, 'start 先拉一次全量');
  assert.equal(clock.runIntervals(4000), 1, '4s 兜底定时器触发');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(commissionsRequests(), 1, 'WS 在线时 4s 兜底不刷新');

  assert.equal(clock.runIntervals(30_000), 1, '30s 对账定时器触发');
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(commissionsRequests(), 2, 'WS 在线也要 REST 对账');
  assert.equal(views.at(-1)?.complete, true, '条数没取满就是完整快照');
  assert.equal(clock.pendingTimeouts(), 0, '正常返回后超时定时器要清掉');
  client.stop();
});

test('REST 取满 limit 时标记「可能未取全」', async () => {
  const clock = makeClock();
  const items = Array.from({ length: 100 }, (_, index) => ({
    id: `c_${index}`,
    title: '整理便笺',
    agent_id: 'scribe',
    status: 'done',
    step_idx: 0,
    blocked_question: '',
  }));
  const { fetchImpl } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({ items }),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  const view = views.at(-1);
  assert.equal(view?.complete, false, '返回条数==limit 必须标不完整');
  assert.equal(view?.finished.length, 100);
  client.stop();
});

test('快照截断时「不在快照里」不当作撤单，完整快照才撤', () => {
  const sim = makeSim();
  const task = { commissionId: 'c_1', kind: 'code' as const, title: '写爬虫', agentName: '甲' };
  sim.enqueue({ kind: 'sync', tasks: [task], finished: [], connected: true });
  sim.update(16);
  assert.equal(sim.snapshot().tasks.length, 1);

  // 截断快照：这一单没出现在 items 里，但不能判撤，她继续干活。
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true, complete: false });
  sim.update(16);
  assert.equal(sim.snapshot().tasks.length, 1, '截断快照不撤单');
  assert.equal(sim.snapshot().ledger.complete, false, '快照不完整要透给状态行');
  assert.notEqual(sim.snapshot().villager.mode, 'idle', '没被叫停，仍在处理这单');

  // 完整快照里确实消失，才按撤单处理。
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true, complete: true });
  sim.update(16);
  assert.equal(sim.snapshot().tasks.length, 0, '完整快照才判定撤单');
  assert.equal(sim.snapshot().villager.taskId, null);
  assert.equal(sim.snapshot().ledger.complete, true);
});

test('fetch 5s 超时按离线降级，不崩', async () => {
  const clock = makeClock();
  const views: LedgerView[] = [];
  const hangingFetch = ((_input: RequestInfo | URL, init?: RequestInit) =>
    new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () =>
        reject(new DOMException('aborted', 'AbortError')));
    })) as unknown as typeof fetch;
  const client = new LedgerClient({
    onChange: (view) => views.push(view),
    fetchImpl: hangingFetch,
    location: { protocol: 'http:', host: '127.0.0.1:5188' },
    timers: clock.timers,
  });
  const pending = client.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(clock.pendingTimeouts(), 1, '挂起的请求有一个 5s 超时');
  assert.equal(clock.runNextTimeout(), true);
  await pending;
  assert.equal(views.at(-1)?.connected, false, '超时按离线处理');
  assert.equal(clock.pendingTimeouts(), 0, '超时后清掉定时器');
  client.stop();
});

// ------------------------------- pickActive 优先级接线（S1-4）

test('活动清单按 blocked→running→auditing→其它 排序，同级 id 稳定', () => {
  const result = project({
    agents: AGENTS,
    commissions: [
      commission({ id: 'c_brief', status: 'briefing' }),
      commission({ id: 'c_audit', status: 'auditing' }),
      commission({ id: 'c_run_b', status: 'running' }),
      commission({ id: 'c_run_a', status: 'running' }),
      commission({ id: 'c_blocked', status: 'blocked', blocked_question: '选哪个？' }),
      commission({ id: 'c_done', status: 'done' }),
    ],
  });
  assert.deepEqual(
    result.ordered.map((task) => task.commissionId),
    ['c_blocked', 'c_run_a', 'c_run_b', 'c_audit', 'c_brief'],
    'blocked 最先；同级 running 按 id 稳定',
  );
  assert.equal(result.active?.commissionId, 'c_blocked', 'active 就是排序后的第一条');
});

test('LedgerView 携带按优先级排好的 order', async () => {
  const clock = makeClock();
  const { fetchImpl } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({
      items: [
        { id: 'c_run', title: '跑着的', agent_id: 'scribe', status: 'running', step_idx: 1, blocked_question: '' },
        { id: 'c_blocked', title: '等请示', agent_id: 'scribe', status: 'blocked', step_idx: 2, blocked_question: '选哪个？' },
        { id: 'c_done', title: '完了', agent_id: 'scribe', status: 'done', step_idx: 0, blocked_question: '' },
      ],
    }),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  const view = views.at(-1);
  assert.deepEqual(view?.order, ['c_blocked', 'c_run'], 'blocked 排在 running 前');
  assert.deepEqual(view?.finished, ['c_done'], '收工的不进 order');
  client.stop();
});

test('台账快照带 order 时按优先级派活', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c_run', kind: 'writing', title: '普通过', agentName: '甲' },
      { commissionId: 'c_blocked', kind: 'writing', title: '等请示', agentName: '乙' },
    ],
    finished: [],
    connected: true,
    order: ['c_blocked', 'c_run'],
  });
  sim.update(16);
  assert.equal(sim.snapshot().villager.taskId, 'c_blocked', 'blocked 优先被派下去');
  assert.equal(sim.snapshot().villager.mode, 'saluting');
});

test('没有 order 时保持插入序（旧服务/旧快照兼容）', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [
      { commissionId: 'c_a', kind: 'writing', title: '甲单', agentName: '甲' },
      { commissionId: 'c_b', kind: 'writing', title: '乙单', agentName: '乙' },
    ],
    finished: [],
    connected: true,
  });
  sim.update(16);
  assert.equal(sim.snapshot().villager.taskId, 'c_a', '无 order 时退回插入序');
});

test('撤单后按新 order 重排，自动接下一单', () => {
  const sim = makeSim();
  const a = { commissionId: 'c_a', kind: 'writing' as const, title: '甲单', agentName: '甲' };
  const b = { commissionId: 'c_b', kind: 'writing' as const, title: '乙单', agentName: '乙' };
  sim.enqueue({ kind: 'sync', tasks: [a, b], finished: [], connected: true, order: ['c_a', 'c_b'] });
  sim.update(16);
  assert.equal(sim.snapshot().villager.taskId, 'c_a');

  // c_a 被上游撤掉，新顺序只剩 b：她应当停手并接 b。
  sim.enqueue({ kind: 'sync', tasks: [b], finished: [], connected: true, order: ['c_b'] });
  sim.update(16);
  assert.equal(sim.snapshot().villager.taskId, 'c_b', '撤单后按最新 order 重排');
  assert.equal(sim.snapshot().villager.mode, 'saluting');
  assert.equal(sim.snapshot().tasks.length, 1, '被撤的单已出清单');
});

// ------------------------------------------ blocked_question：只透传展示，不回答（S1-3）

test('blocked_question 从台账透传到 LedgerView，且不请求任何回答端点', async () => {
  const clock = makeClock();
  const calls: { method: string; url: string }[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ method: init?.method ?? 'GET', url });
    const path = url.split('?')[0] ?? url;
    if (path === '/api/executive/commissions') {
      return jsonResponse({
        items: [{
          id: 'c_blocked',
          title: '把便笺整理成三版标题',
          agent_id: 'scribe',
          status: 'blocked',
          step_idx: 2,
          blocked_question: '先按短句还是完整句式？',
        }],
      });
    }
    if (path === '/api/executive/roster') {
      return jsonResponse({ items: [{ id: 'scribe', name: '笔录官', strengths: ['写作'] }] });
    }
    return jsonResponse({}, 404);
  }) as unknown as typeof fetch;
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  const task = views.at(-1)?.tasks[0];
  assert.equal(task?.status, 'blocked');
  assert.equal(task?.blockedQuestion, '先按短句还是完整句式？');
  assert.ok(calls.every((call) => call.method === 'GET'), '全程只读，不写台账');
  assert.ok(
    calls.every((call) => !/answer|reply|respond/i.test(call.url)),
    '不调用任何回答端点',
  );
  client.stop();
});

test('blocked_question 进世界快照；转 running / 收工后清除，不残留', () => {
  const sim = makeSim();
  const base = {
    commissionId: 'c_1',
    kind: 'writing' as const,
    title: '把便笺整理成三版标题',
    agentName: '笔录官',
  };
  sim.enqueue({
    kind: 'sync',
    tasks: [{ ...base, status: 'blocked', blockedQuestion: '先按短句还是完整句式？' }],
    finished: [],
    connected: true,
    complete: true,
    order: ['c_1'],
  });
  const result = sim.update(16);
  assert.ok(
    result[0].message.includes('在等你的答复：先按短句还是完整句式？'),
    result[0].message,
  );
  assert.equal(sim.snapshot().tasks[0]?.blockedQuestion, '先按短句还是完整句式？');

  // 上游回答了 → 转 running：问题立刻清空，不残留。
  sim.enqueue({
    kind: 'sync',
    tasks: [{ ...base, status: 'running', blockedQuestion: '' }],
    finished: [],
    connected: true,
    complete: true,
    order: ['c_1'],
  });
  sim.update(16);
  assert.equal(sim.snapshot().tasks[0]?.blockedQuestion, null);

  // 再 blocked 一次，然后收工：问题跟任务一起离开活动清单。
  sim.enqueue({
    kind: 'sync',
    tasks: [{ ...base, status: 'blocked', blockedQuestion: '选 A 还是 B？' }],
    finished: [],
    connected: true,
    complete: true,
    order: ['c_1'],
  });
  sim.update(16);
  assert.equal(sim.snapshot().tasks[0]?.blockedQuestion, '选 A 还是 B？');
  sim.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_1'],
    connected: true,
    complete: true,
    order: [],
  });
  sim.update(16);
  assert.equal(sim.snapshot().tasks.some((task) => task.id === 'c_1'), false);
  assert.ok(sim.snapshot().tasks.every((task) => task.blockedQuestion === null));
});

test('blocked 问题按 60 字符截断展示，快照保留完整原文；空值不显示', () => {
  const long = '问'.repeat(80);
  assert.equal(BLOCKED_QUESTION_LIMIT, 60);
  assert.equal(excerptBlockedQuestion(long), `${'问'.repeat(60)}…`);
  assert.equal(excerptBlockedQuestion('短问题'), '短问题');
  assert.equal(excerptBlockedQuestion(null), '');
  assert.equal(excerptBlockedQuestion('   '), '');

  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_long',
      kind: 'writing',
      title: '长问题',
      agentName: '甲',
      status: 'blocked',
      blockedQuestion: long,
    }],
    finished: [],
    connected: true,
    complete: true,
    order: ['c_long'],
  });
  sim.update(16);
  assert.equal(sim.snapshot().tasks[0]?.blockedQuestion, long, '快照保留完整值');
  assert.equal(
    excerptBlockedQuestion(sim.snapshot().tasks[0]?.blockedQuestion ?? null),
    `${'问'.repeat(60)}…`,
  );

  // 空串 / 缺字段：即便状态是 blocked 也不挂问题。
  const empty = makeSim();
  empty.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_empty',
      kind: 'writing',
      title: '空问题',
      agentName: '甲',
      status: 'blocked',
      blockedQuestion: '   ',
    }],
    finished: [],
    connected: true,
    complete: true,
  });
  empty.update(16);
  assert.equal(empty.snapshot().tasks[0]?.blockedQuestion, null);

  const legacy = makeSim();
  legacy.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_old', kind: 'writing', title: '旧调用', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  legacy.update(16);
  assert.equal(legacy.snapshot().tasks[0]?.blockedQuestion, null, '旧调用方省略字段按没有处理');
});

// ------------------------------- 台账时间戳 → settled.completedAt（task-23）

/** 走到铺子里开工；超时抛错，避免死循环。 */
function runUntilWorking(sim: Simulation): void {
  let guard = 0;
  while (sim.snapshot().villager.mode !== 'working') {
    sim.update(100);
    if ((guard += 1) > 400) throw new Error('没走到铺子');
  }
}

test('台账时间戳解析：closed_at 优先、退 updated_at、都缺→null', () => {
  const base = { title: 't', agent_id: '', status: 'running', step_idx: 0, blocked_question: '' };
  const closed = parseCommissionRow({
    ...base, id: 'a', closed_at: 1792377786.5, updated_at: 1792377700,
  });
  assert.equal(closed.completedAt, 1792377786.5, 'closed_at 优先');
  const updated = parseCommissionRow({
    ...base, id: 'b', closed_at: null, updated_at: 1792377700,
  });
  assert.equal(updated.completedAt, 1792377700, 'closed_at 为 null 退 updated_at');
  const missing = parseCommissionRow({ ...base, id: 'c' });
  assert.equal(missing.completedAt, null, '两个字段都缺→null');
});

test('ledgerCompletedAtMs：秒×1000、毫秒原样、缺失 null', () => {
  assert.equal(ledgerCompletedAtMs(1792377786), 1_792_377_786_000);
  assert.equal(ledgerCompletedAtMs(1_792_377_786_000), 1_792_377_786_000);
  assert.equal(ledgerCompletedAtMs(null), null);
  assert.equal(ledgerCompletedAtMs(undefined), null);
  assert.equal(ledgerCompletedAtMs(Number.NaN), null);
});

test('LedgerView 携带 completedAt 与 finishedAt（settled 数据源）', async () => {
  const clock = makeClock();
  const { fetchImpl } = routedFetch({
    '/api/executive/commissions': () => jsonResponse({
      items: [
        {
          id: 'c_run', title: '跑着的', agent_id: 'scribe', status: 'running',
          step_idx: 1, blocked_question: '', updated_at: 1792377700, closed_at: null,
        },
        {
          id: 'c_done', title: '完了', agent_id: 'scribe', status: 'done',
          step_idx: 0, blocked_question: '', updated_at: 1792377700, closed_at: 1792377786.5,
        },
      ],
    }),
  });
  const { client, views } = makeLedgerClient(clock, fetchImpl);
  await client.start();
  const view = views.at(-1);
  assert.equal(
    view?.tasks.find((task) => task.commissionId === 'c_run')?.completedAt,
    1792377700,
    '活动单带 updated_at',
  );
  assert.deepEqual(view?.finishedAt, [{ commissionId: 'c_done', completedAt: 1792377786.5 }]);
  client.stop();
});

test('ledger 交付的 settled.completedAt 取台账时间 ×1000，clock=ledger', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_1', kind: 'code', title: '写爬虫', agentName: '甲',
      status: 'running', completedAt: 1792377700,
    }],
    finished: [],
    connected: true,
  });
  runUntilWorking(sim);
  sim.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_1'],
    finishedAt: [{ commissionId: 'c_1', completedAt: 1792377786 }],
    connected: true,
  });
  sim.update(16);
  const settled = sim.toSave().world.settled.find((record) => record.id === 'c_1');
  assert.equal(settled?.source, 'ledger');
  assert.equal(settled?.completedAt, 1_792_377_786_000, 'closed_at(秒) ×1000');
  assert.equal(settled?.completedAtClock, 'ledger');
  assert.doesNotThrow(() => assertSave(sim.toSave()), '格式层接受非空 ledger 时间');
});

test('finishedAt 缺失时退回已见到的 updated_at，仍不用本地时间', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_2', kind: 'code', title: '写爬虫', agentName: '甲',
      status: 'running', completedAt: 1792377700,
    }],
    finished: [],
    connected: true,
  });
  runUntilWorking(sim);
  // 旧客户端只给 finished id、不给时间：用上一拍记下的 updated_at。
  sim.enqueue({ kind: 'sync', tasks: [], finished: ['c_2'], connected: true });
  sim.update(16);
  const settled = sim.toSave().world.settled.find((record) => record.id === 'c_2');
  assert.equal(settled?.completedAt, 1_792_377_700_000);
  assert.equal(settled?.completedAtClock, 'ledger');
});

test('台账时间缺失 → completedAt null + clock=ledger（绝不本地时间顶替）', () => {
  const sim = makeSim();
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId: 'c_3', kind: 'code', title: '写爬虫', agentName: '甲' }],
    finished: [],
    connected: true,
  });
  runUntilWorking(sim);
  sim.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_3'],
    finishedAt: [{ commissionId: 'c_3', completedAt: null }],
    connected: true,
  });
  sim.update(16);
  const settled = sim.toSave().world.settled.find((record) => record.id === 'c_3');
  assert.equal(settled?.completedAt, null);
  assert.equal(settled?.completedAtClock, 'ledger');
});

test('读档对账也把台账时间写进 settled（reconcile 路径）', () => {
  const sim1 = makeSim();
  sim1.enqueue({
    kind: 'sync',
    tasks: [{
      commissionId: 'c_4', kind: 'code', title: '写爬虫', agentName: '甲',
      status: 'running', completedAt: 1792377700,
    }],
    finished: [],
    connected: true,
  });
  runUntilWorking(sim1);
  const sim2 = Simulation.fromSave(sim1.toSave(), simDeps());
  sim2.enqueue({
    kind: 'sync',
    tasks: [],
    finished: ['c_4'],
    finishedAt: [{ commissionId: 'c_4', completedAt: 1792377786 }],
    connected: true,
    complete: true,
    order: [],
  });
  sim2.update(16);
  const settled = sim2.toSave().world.settled.find((record) => record.id === 'c_4');
  assert.equal(settled?.completedAt, 1_792_377_786_000, '对账结算也用台账时间');
  assert.equal(settled?.completedAtClock, 'ledger');
});