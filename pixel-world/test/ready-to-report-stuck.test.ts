import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { LedgerClient, type LedgerTimers } from '../src/world/ledger.ts';
import type { CommissionRow } from '../src/world/projection.ts';

/** ready_to_report 不触发交付，且不会把屋里的人卡死（6473b96 的回归锁）。
 *
 * 6473b96 把 FINISHED_STATUSES 从 {ready_to_report, done} 收成 {done}，
 * 因为「等老板验收」不是成功。本文件从**真实链路**验证修得完整：
 *
 *   ledger.ts:338        publish() 必经 project()
 *   projection.ts:97     ready_to_report 既不在 ACTIVE 也不在 FINISHED，被 continue 剔掉
 *   -> onChange 载荷里 tasks=[] 且 finished=[]
 *   simulation.ts:444    complete && !next.has(id) 成立 -> abandonActiveTask
 *   -> 角色 mode=idle、visible=true，正常收手
 *
 * 为什么必须走真实链路：上一版探针自己伪造 sync 命令，把 ready_to_report 单
 *   直接塞进 tasks 且给 complete=false。那条路径生产上不存在，因为 project()
 *   永远先把它剔掉。伪造载荷得出的「角色卡死」是假象，实测对比：
 *     伪造(tasks 含该单, complete=false) -> mode=working 卡死
 *     真实(tasks=[], complete=true)      -> mode=idle 正常
 *
 * 本测试从 LedgerClient.applyPush()（真实 WS 增量入口）与 client.start()
 * （真实 REST 全量入口）进，断言 simulation 收到**真实 onChange 载荷**后的行为。
 */

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);
const STEP = 100;

/** 手动放行的假定时器，避免 WS 重连与轮询干扰。 */
function makeTimers(): LedgerTimers {
  let handle = 1;
  return {
    setTimeout: () => handle++,
    clearTimeout: () => {},
    setInterval: () => handle++,
    clearInterval: () => {},
  };
}

/** 一单的台账行；status 是唯一会变的东西。 */
function row(status: string): CommissionRow {
  return {
    id: 'c_rr',
    title: '整理便笺',
    agent_id: 'scribe',
    status,
    step_idx: 0,
    blocked_question: '',
    completedAt: null,
  };
}

interface Harness {
  readonly sim: Simulation;
  readonly push: (status: string) => void;
  /** 最近一次真实 onChange 载荷，用于断言「世界到底收到了什么」。 */
  readonly lastPayload: () => { tasks: number; finished: number; complete: boolean };
}

/** 把 LedgerClient 的 onChange 接到真实 Simulation 上。 */
async function makeHarness(): Promise<Harness> {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });

  let last = { tasks: 0, finished: 0, complete: false };

  const fetchImpl = (async () =>
    ({
      ok: true,
      status: 200,
      json: async () => ({
        items: [],
        agents: [{ id: 'scribe', name: '抄书官', strengths: ['写作', '整理'] }],
      }),
    })) as unknown as typeof fetch;

  const client = new LedgerClient({
    onChange: (view) => {
      last = {
        tasks: view.tasks.length,
        finished: view.finished.length,
        complete: view.complete,
      };
      // 真实载荷：与 SandboxScene.connectLedger 同一条路径。
      sim.enqueue({ kind: 'sync', ...view });
    },
    timers: makeTimers(),
    fetchImpl,
    location: { protocol: 'http:', host: '127.0.0.1:5188' },
    socketFactory: () => ({
      readyState: 1,
      url: `ws://127.0.0.1:5188/ws`,
      addEventListener: () => {},
      close: () => {},
    }),
  });

  await client.start();

  return {
    sim,
    lastPayload: () => last,
    push: (status: string) => {
      // 真实 WS 增量入口：内部 applyPush -> publish -> project -> onChange。
      client.applyPush(row(status));
    },
  };
}

/** 推到 running 并等角色进屋干活。 */
function runUntilWorking(h: Harness): void {
  h.push('running');
  for (let i = 0; i < 900; i += 1) {
    if (h.sim.snapshot().villager.mode === 'working') return;
    h.sim.update(STEP);
  }
  throw new Error('没能把角色送进工位：' + JSON.stringify(h.sim.snapshot().villager));
}

test('ready_to_report 不触发交付，也不留下卡死的角色', async () => {
  const h = await makeHarness();
  runUntilWorking(h);

  const before = h.sim.snapshot();
  assert.equal(before.villager.mode, 'working', '前置：应在干活');
  assert.equal(before.villager.visible, false, '前置：干活时不可见');

  h.push('ready_to_report');

  // 先钉住「世界收到了什么」：project() 已把它从 tasks 剔掉。
  const payload = h.lastPayload();
  assert.equal(payload.tasks, 0, 'ready_to_report 不该出现在活动清单里');
  assert.equal(payload.finished, 0, 'ready_to_report 也不是收工');

  for (let i = 0; i < 1200; i += 1) h.sim.update(STEP);
  const after = h.sim.snapshot();

  assert.equal(after.faith.points, 0, 'ready_to_report 不是成功，不该加信仰点');
  assert.equal(after.deliveries.ledger, 0, 'ready_to_report 不该被算作台账交付');

  // 关键：角色被正常收手，不是卡在不可见的 working 状态。
  assert.equal(after.villager.mode, 'idle', '角色应收回空闲，而不是永久卡在 working');
  assert.equal(after.villager.visible, true, '角色应恢复可见');
});

test('ready_to_report 后角色能接新活（没有留下卡死的占位）', async () => {
  const h = await makeHarness();
  runUntilWorking(h);
  h.push('ready_to_report');
  for (let i = 0; i < 300; i += 1) h.sim.update(STEP);

  const s = h.sim.snapshot();
  const stuck = s.villagers.filter(
    (v) => v.mode === 'working' && v.visible === false,
  ).length;
  assert.equal(stuck, 0, '不该有角色永久不可见地卡在 working');
});
