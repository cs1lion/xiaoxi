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
import { Simulation } from '../src/world/simulation.ts';
import type { AgentRow, CommissionRow } from '../src/world/projection.ts';

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
  };
}

const AGENTS: AgentRow[] = [
  { id: 'claude', name: '真机 Claude', strengths: ['实现', '代码', '文件', '写入'] },
  { id: 'scout', name: '探马', strengths: ['检索', '查找', '搜索', '资料'] },
  { id: 'scribe', name: '笔录官', strengths: ['写作', '整理', '标题', '便笺'] },
];

function makeSim(): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      return blueprint === undefined ? null : placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
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

test('收工的单不进活动清单，进 finished', () => {
  const result = project({
    agents: AGENTS,
    commissions: [
      commission({ id: 'live', status: 'running', agent_id: 'claude' }),
      commission({ id: 'over', status: 'ready_to_report', agent_id: 'scribe' }),
      commission({ id: 'archived', status: 'done', agent_id: 'scribe' }),
    ],
  });
  assert.deepEqual(result.tasks.map((t) => t.commissionId), ['live']);
  assert.deepEqual(result.finished.map((t) => t.commissionId).sort(), ['archived', 'over']);
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