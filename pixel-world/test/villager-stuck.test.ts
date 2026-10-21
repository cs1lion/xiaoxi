import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import {
  ALTAR,
  buildWorld,
  buildWorldFromSave,
  placeBlueprint,
  type BuiltWorld,
} from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';

/** D-1 回归锁：屋内正在干活的单被判「没了」时，角色不能卡死。
 *
 * 缺陷形态（QA 在 S5 e2e 发现）：角色 mode=working、visible=false、pos=工位（占地内
 * 不可走格）。该单从台账消失后，状态机把 mode 收回 idle，但：
 *   - 读档对账路径（reconcile → removeTask）：visible 没恢复、位置没移出 →
 *     不渲染 + 走不动，且入档跨会话持久，只有清档能恢复；
 *   - live-sync 路径（abandonActiveTask）：visible 恢复了，但位置仍留在工位 →
 *     看得见却钉在屋里不动。
 * 两条路径都缺「位置移出建筑」，修法统一到 restoreOutOfBuilding()。
 *
 * 本文件覆盖三条路径：
 *   ① live-sync 撤单（abandonActiveTask）
 *   ② 读档 + 首次 sync 对账（reconcile → removeTask）  ← 任务卡要求的原始场景
 *   ③ 两条路径修复后都能真正走动
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
/**
 * 任务卡要求的原始场景：**读档 + 首次 sync 对账**。
 *
 * 为什么单独写一条：本文件前两条用的是新建 Simulation，此时 ledgerReconciled 默认 true
 * （simulation.ts:191），sync 走 live 路径（:404 只在 !ledgerReconciled 时才进 reconcile）。
 * 也就是说前两条测的是 abandonActiveTask，**没覆盖 reconcile**。
 * 这里用 toSave → fromSave 造出「关页时他在屋内干活」的存档，再发首次 sync 走对账分支。
 */
test('D-1：读档 + 首次对账判缺席，角色必须恢复可见且不在建筑内', () => {
  const { sim, grid } = makeSim();
  dispatchAndWork(sim, 'c_load');

  const working = sim.snapshot();
  assert.equal(working.villager.mode, 'working', '前置：应在干活');
  assert.equal(working.villager.visible, false, '前置：干活时不可见');

  // 存档 → 读档（复现「关页时他在屋内干活」）
  const save = sim.toSave();
  assert.equal(save.world.villagers[0]?.mode, 'working', '存档里应保留干活态');

  const rebuilt = buildWorldFromSave(save.world.buildings, blueprints);
  const reloaded = Simulation.fromSave(save, {
    grid: rebuilt.grid,
    buildings: rebuilt.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    spawnBuilding: (prefabId: string, instanceId: string) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(rebuilt.grid, blueprint, instanceId, ALTAR);
    },
  });

  // 首次 sync：完整快照里这单缺席 → 走 reconcile
  reloaded.enqueue({ kind: 'sync', tasks: [], finished: [], connected: true, complete: true });
  reloaded.update(STEP);

  const after = reloaded.snapshot();
  assert.equal(after.villager.mode, 'idle', '状态机收回空闲');
  assert.equal(after.villager.taskId, null, '手上不再挂单');
  assert.equal(after.villager.visible, true, 'must 恢复可见，否则整轮不渲染');
  const cell = { x: Math.round(after.villager.pos.x), y: Math.round(after.villager.pos.y) };
  assert.ok(rebuilt.grid.isWalkable(cell), `位置必须在可走格，实际 (${cell.x},${cell.y}) 不可走`);

  // 且真能走动
  const beforePos = { ...after.villager.pos };
  for (let i = 0; i < 120; i += 1) reloaded.update(STEP);
  const finalPos = reloaded.snapshot().villager.pos;
  const moved = Math.hypot(finalPos.x - beforePos.x, finalPos.y - beforePos.y);
  assert.ok(moved > 0.5, `读档对账后应能走动，实际位移 ${moved.toFixed(2)}`);
  void grid;
});
