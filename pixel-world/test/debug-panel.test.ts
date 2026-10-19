import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import type { WorldEvent, WorldSnapshot } from '../src/world/types.ts';
import {
  buildDebugModel,
  DEBUG_EVENT_LIMIT,
  ledgerSummary,
  saveStatusLine,
  selectEvents,
  type SaveStatusView,
} from '../src/ui/debug-panel.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

function makeSim(): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => 1_700_000_000_000,
  });
}

function twoBusy(sim: Simulation): void {
  sim.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  sim.update(16);
  sim.enqueue({ kind: 'dispatch', taskKind: 'research', summary: '查资料' });
  sim.update(16);
}

test('调试模型：实体数量与快照一致，任务带归属/来源/请示', () => {
  const sim = makeSim();
  twoBusy(sim);
  sim.enqueue({ kind: 'dispatch', taskKind: 'writing', summary: '整理便笺' });
  sim.update(16);
  const snapshot = sim.snapshot();
  const model = buildDebugModel(snapshot, sim.recentEvents(DEBUG_EVENT_LIMIT), null, {
    tone: 'all',
    limit: DEBUG_EVENT_LIMIT,
  });

  assert.equal(model.counts.villagers, snapshot.villagers.length);
  assert.equal(model.counts.buildings, snapshot.workstations.length);
  assert.equal(model.counts.tasks, snapshot.tasks.length);
  assert.equal(model.counts.pending, snapshot.pending.length);
  assert.equal(model.counts.events, snapshot.events.length <= DEBUG_EVENT_LIMIT
    ? sim.recentEvents(DEBUG_EVENT_LIMIT).length
    : DEBUG_EVENT_LIMIT);

  assert.equal(model.villagers[0]?.id, 'v0');
  assert.equal(model.villagers[0]?.taskId, 't1');
  assert.deepEqual(
    model.pending.map((queue) => queue.villagerId),
    snapshot.villagers.map((villager) => villager.id),
  );
  const queued = model.pending.flatMap((queue) => queue.items);
  assert.equal(queued.length, snapshot.pending.length);
  assert.equal(model.tasks.every((task) => typeof task.workerId === 'string'), true);
  assert.equal(model.tasks.every((task) => typeof task.source === 'string'), true);
  assert.equal(model.tasks.every((task) => typeof task.blockedQuestion === 'string'), true);
});

test('事件流：最近 50 条上限 + tone 过滤', () => {
  const events: WorldEvent[] = Array.from({ length: 80 }, (_, index) => ({
    at: index,
    tone: index % 2 === 0 ? 'info' : 'warn',
    text: `event-${index}`,
  }));
  const all = selectEvents(events, { tone: 'all', limit: DEBUG_EVENT_LIMIT });
  assert.equal(all.length, DEBUG_EVENT_LIMIT);
  assert.equal(all[0]?.text, 'event-30', '取最后 50 条');
  const warn = selectEvents(events, { tone: 'warn', limit: DEBUG_EVENT_LIMIT });
  assert.equal(warn.length, 40);
  assert.ok(warn.every((event) => event.tone === 'warn'));
  const none = selectEvents(events, { tone: 'oracle', limit: DEBUG_EVENT_LIMIT });
  assert.equal(none.length, 0);
});

test('连接与存档摘要可读，不依赖 DOM', () => {
  const snapshot = makeSim().snapshot();
  assert.ok(ledgerSummary(snapshot.ledger).includes('connected='));
  assert.ok(ledgerSummary(snapshot.ledger).includes('reconciled='));
  const save: SaveStatusView = {
    schemaVersion: 1,
    savedAtWallClock: 1_700_000_000_000,
    autoSavePaused: false,
  };
  const line = saveStatusLine(save);
  assert.ok(line.includes('v1'));
  assert.ok(line.includes('自动存档开启'));
  assert.equal(saveStatusLine(null), '存档：未写入');
  assert.ok(
    saveStatusLine({ ...save, autoSavePaused: true }).includes('已暂停'),
  );
});

test('调试模型只读：构建后快照不被改写', () => {
  const sim = makeSim();
  twoBusy(sim);
  const snapshot: WorldSnapshot = sim.snapshot();
  const before = JSON.stringify(snapshot);
  buildDebugModel(snapshot, sim.recentEvents(50), null, { tone: 'all', limit: 50 });
  assert.equal(JSON.stringify(snapshot), before);
});
