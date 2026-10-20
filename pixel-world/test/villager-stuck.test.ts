import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, placeBlueprint, type BuiltWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';

/** D-1 回归锁：读档后对账把「屋内正在干活的单」判成缺席时，角色不能卡死。
 *
 * 缺陷形态（QA 在 S5 e2e 发现）：
 *   角色 mode=working、visible=false、pos=工位（建筑内不可走格）。
 *   若该单在台账**完整快照**里缺席，reconcile 走 removeTask，
 *   而 removeTask 只重置 mode/taskId/path/... ，**没恢复 visible、也没移出建筑**。
 *   结果：不渲染（visible=false）+ 走不动（起点不可走，grid.path 返回空）。
 *   该状态还会入档，跨会话持久，只有清档能恢复。
 *
 * 对照：同一「单没了」语义的 live-sync 路径走 abandonActiveTask，
 *   那条有 visible = true。两条路径对同一语义处理不一致，reconcile 这条漏了。
 */

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);
const STEP = 100;

function makeSim(): { sim: Simulation; grid: BuiltWorld['grid'] } {
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
  // grid 是 private，测试通过世界对象判可走性（与 Simulation 用同一份账本）。
  return { sim, grid: world.grid };
}

/** 跑固定步数（不靠 predicate，避免超时掩盖问题）。 */
function tick(sim: Simulation, times: number): void {
  for (let i = 0; i < times; i += 1) sim.update(STEP);
}

/** 把一个台账单派给角色并让他走到工位（mode=working、visible=false）。 */
function dispatchAndWork(sim: Simulation, commissionId: string): void {
  sim.enqueue({
    kind: 'sync',
    tasks: [{ commissionId, kind: 'writing', title: '台账的活', agentName: '甲' }],
    finished: [],
    connected: true,
    order: [commissionId],
  });
  sim.update(STEP);
  // 走完 行礼 → 出门 → 进屋；最多跑 60 秒
  for (let i = 0; i < 600; i += 1) {
    const s = sim.snapshot();
    if (s.villager.mode === 'working') return;
    sim.update(STEP);
  }
  throw new Error('没能把角色送进工位：' + JSON.stringify(sim.snapshot().villager));
}

test('D-1：对账判缺席后，屋内干活的角色必须恢复可见并移到建筑外', () => {
  const { sim, grid } = makeSim();
  dispatchAndWork(sim, 'c_d1');

  const working = sim.snapshot();
  assert.equal(working.villager.mode, 'working', '前置：应在干活');
  assert.equal(working.villager.visible, false, '前置：干活时不可见');

  // 完整快照里这一单消失 → reconcile 按「撤单」处理
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true, complete: true });
  sim.update(STEP);

  const after = sim.snapshot();
  assert.equal(after.tasks.length, 0, '单已移除');
  assert.equal(after.villager.mode, 'idle', '状态机收回空闲');
  assert.equal(after.villager.visible, true, '必须恢复可见，否则人看不见也动不了');
  assert.equal(after.villager.taskId, null);

  // 位置必须在可走格：否则 grid.path 起点非法，寻路恒为空
  const pos = after.villager.pos;
  const cell = { x: Math.round(pos.x), y: Math.round(pos.y) };
  assert.ok(
    grid.isWalkable(cell),
    `位置必须在可走格，实际 (${cell.x},${cell.y}) 不可走`,
  );
});

test('D-1：恢复后角色能真正走动（寻路不再恒空）', () => {
  const { sim } = makeSim();
  dispatchAndWork(sim, 'c_d1b');
  sim.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true, complete: true });
  sim.update(STEP);

  const before = sim.snapshot().villager.pos;
  tick(sim, 120);
  const afterPos = sim.snapshot().villager.pos;
  const moved = Math.hypot(afterPos.x - before.x, afterPos.y - before.y);
  assert.ok(moved > 0.5, `恢复后应该能走动，实际位移 ${moved.toFixed(2)}`);
});
