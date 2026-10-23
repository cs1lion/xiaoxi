import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import { SAVE_STORAGE_KEY, SETTLED_WARN_THRESHOLD, type WorldSave } from '../src/world/save.ts';
import { SaveStore, type StorageLike } from '../src/ui/save-store.ts';

const raw = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(raw);

class FakeStorage implements StorageLike {
  readonly data = new Map<string, string>();
  failSet: 'none' | 'quota' | 'security' = 'none';
  failGet = false;

  get length(): number {
    return this.data.size;
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    if (this.failGet) {
      if (this.failSet === 'security') throw new Error('SecurityError');
      throw new Error('blocked');
    }
    return this.data.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failSet === 'quota') {
      const error = new Error('QuotaExceededError');
      error.name = 'QuotaExceededError';
      throw error;
    }
    if (this.failSet === 'security') throw new Error('SecurityError');
    this.data.set(key, value);
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }
}

function makeSave(): WorldSave {
  const world = buildWorld(blueprints);
  let clock = 1000;
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock: () => (clock += 1),
  });
  sim.update(16);
  return sim.toSave();
}

test('setItem 抛 QuotaExceeded：停自动存档、只提示一次、不抛异常', () => {
  const storage = new FakeStorage();
  storage.failSet = 'quota';
  const warnings: string[] = [];
  const store = new SaveStore({ storage, onWarning: (message) => warnings.push(message) });
  const save = makeSave();

  const first = store.write(save);
  assert.equal(first.ok, false);
  assert.equal(first.paused, true);
  assert.equal(store.isAutoSaveEnabled(), false);
  assert.equal(warnings.length, 1);
  assert.ok(warnings[0]?.includes('存储已满'));

  const second = store.write(save);
  assert.equal(second.ok, false);
  assert.equal(warnings.length, 1, '每会话只提示一次');

  // 世界本身照常推进：配额异常没有抛进 update。
  const world = buildWorld(blueprints);
  const sim = new Simulation({ grid: world.grid, buildings: world.buildings, altar: ALTAR, villagerName: '莞尔' });
  sim.update(100);
  assert.ok(sim.snapshot().now >= 100);
});

test('高版本只读：拒绝加载、暂停自动存档、原槽字节不变', () => {
  const storage = new FakeStorage();
  const text = JSON.stringify({ ...makeSave(), schemaVersion: 99 });
  storage.setItem(SAVE_STORAGE_KEY, text);
  const warnings: string[] = [];
  const store = new SaveStore({ storage, onWarning: (message) => warnings.push(message) });

  const result = store.read();
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'version');
  assert.equal(result.paused, true);
  assert.equal(store.isAutoSaveEnabled(), false);
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), text, '原槽字节保留');
  assert.ok(warnings[0]?.includes('只读'));
});

test('坏档：带时间戳备份，只保留最近 3 份', () => {
  const storage = new FakeStorage();
  let clock = 1_700_000_000_000;
  const store = new SaveStore({ storage, wallClock: () => (clock += 1) });
  for (let index = 0; index < 4; index += 1) {
    storage.setItem(SAVE_STORAGE_KEY, '{ bad json');
    const result = store.read();
    assert.equal(result.ok, false);
    assert.equal(result.reason, 'corrupt');
  }
  const backups = store.listBackups();
  assert.equal(backups.length, 3, '保留最近 3 份');
  assert.ok(backups.every((key) => key.includes('.corrupt.')));
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), '{ bad json', '不静默清空原槽');
});

test('读不到存档：提示可能是来源(origin/端口)变了', () => {
  const storage = new FakeStorage();
  storage.failGet = true;
  storage.failSet = 'security';
  const store = new SaveStore({ storage });
  const result = store.read();
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'storage');
  assert.ok(result.message.includes('来源(origin/端口)'));
  assert.equal(store.isAutoSaveEnabled(), false);
});

test('已结算超过阈值只告警一次，不截断不删除', () => {
  const storage = new FakeStorage();
  const warnings: string[] = [];
  const store = new SaveStore({ storage, onWarning: (message) => warnings.push(message) });
  const save = makeSave();
  const settled = Array.from({ length: SETTLED_WARN_THRESHOLD + 1 }, (_, index) => ({
    id: `c_${index}`,
    source: 'mock' as const,
    workerId: 'v0',
    completedAt: index,
    completedAtClock: 'local' as const,
  }));
  const big = { ...save, world: { ...save.world, settled } };
  assert.equal(store.write(big).ok, true);
  assert.equal(store.write(big).ok, true);
  assert.equal(warnings.length, 1);
  assert.ok(warnings[0]?.includes(String(SETTLED_WARN_THRESHOLD)));
});
