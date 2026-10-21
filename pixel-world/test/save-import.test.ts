/** task-22 手动导出/导入：v1 白名单、坏档备份、高版本拒绝、配额降级、导入后重建世界。 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, buildWorldFromSave } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import {
  assertSave,
  collectSavePaths,
  SAVE_STORAGE_KEY,
  SAVE_TASK_KEYS,
  SCHEMA_V2_FINGERPRINT,
  type WorldSave,
} from '../src/world/save.ts';
import {
  exportFileName,
  IMPORT_MAX_BYTES,
  parseSaveText,
  SaveStore,
  type StorageLike,
} from '../src/ui/save-store.ts';

const rawPrefabs = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(rawPrefabs);

const sampleText = readFileSync(new URL('../../docs/save-v2.sample.json', import.meta.url), 'utf8');

class FakeStorage implements StorageLike {
  readonly data = new Map<string, string>();
  failSet = false;

  get length(): number {
    return this.data.size;
  }

  key(index: number): string | null {
    return [...this.data.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    return this.data.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.failSet) {
      const error = new Error('QuotaExceededError');
      error.name = 'QuotaExceededError';
      throw error;
    }
    this.data.set(key, value);
  }

  removeItem(key: string): void {
    this.data.delete(key);
  }
}

function makeSave(wallClock: () => number = () => 1000): WorldSave {
  const world = buildWorld(blueprints);
  const sim = new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock,
  });
  sim.update(16);
  return sim.toSave();
}

/** 导出文本里唯一允许随时间变化的诊断字段。 */
function withoutWallClock(text: string): {
  parsed: { meta: Record<string, unknown> };
  clone: { meta: Record<string, unknown> };
} {
  const parsed = JSON.parse(text) as { meta: Record<string, unknown> };
  const clone = JSON.parse(text) as { meta: Record<string, unknown> };
  delete clone.meta.savedAtWallClock;
  return { parsed, clone };
}

test('导出内容就是当前版存档：通过校验、字段不超出冻结样例、无运行时字段', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  const save = makeSave();
  assert.equal(store.write(save).ok, true);

  const text = store.readRawText();
  assert.ok(text !== null, '写完后应能读到单槽原文');
  const parsed = JSON.parse(text) as WorldSave;
  assert.doesNotThrow(() => assertSave(parsed));
  assert.equal(parsed.meta.schemaFingerprint, SCHEMA_V2_FINGERPRINT);
  // 指纹是按“实际数据叶子路径”算的，空数组/空对象会让路径集合缩水，
  // 所以这里比的是「导出路径必须全部落在冻结样例的路径集合里」——多一个字段就红。
  const samplePaths = new Set(collectSavePaths(JSON.parse(sampleText)));
  for (const path of collectSavePaths(parsed)) {
    assert.ok(samplePaths.has(path), `导出多出冻结样例之外的字段路径：${path}`);
  }
  assert.equal('ledgerCompletedAt' in (parsed.world.tasks[0] ?? {}), false);
  for (const task of parsed.world.tasks) {
    assert.deepEqual(Object.keys(task).sort(), [...SAVE_TASK_KEYS].sort());
  }
});

test('导出可复现：同状态两次导出只差 meta.savedAtWallClock', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  let clock = 1000;
  const first = makeSave(() => (clock += 1));
  const second = makeSave(() => (clock += 1));
  assert.equal(store.write(first).ok, true);
  const firstText = store.readRawText();
  assert.equal(store.write(second).ok, true);
  const secondText = store.readRawText();
  assert.ok(firstText !== null && secondText !== null);
  assert.notEqual(
    JSON.parse(firstText).meta.savedAtWallClock,
    JSON.parse(secondText).meta.savedAtWallClock,
  );
  assert.deepEqual(withoutWallClock(firstText).clone, withoutWallClock(secondText).clone);
});

test('导出文件名带时间戳，且同秒同格式', () => {
  const name = exportFileName(new Date(2026, 9, 19, 20, 5, 7).getTime());
  assert.match(name, /^xiaoxi-pixel-world-save-20261019-200507\.json$/);
  assert.match(exportFileName(Date.now()), /^xiaoxi-pixel-world-save-\d{8}-\d{6}\.json$/);
});

test('导入合法档：写入单槽、可读回、能重建世界（刷新恢复路径）', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  const result = store.importText(sampleText);
  assert.equal(result.ok, true, result.message);
  const text = store.readRawText();
  assert.ok(text !== null);
  assert.doesNotThrow(() => assertSave(JSON.parse(text)), '导入后槽里必须是当前版合法档');

  const read = store.read();
  assert.equal(read.ok, true);
  if (!read.ok) return;
  const world = buildWorldFromSave(read.save.world.buildings, blueprints);
  assert.equal(world.buildings.length, read.save.world.buildings.length);
  assert.equal(read.save.meta.schemaFingerprint, SCHEMA_V2_FINGERPRINT);
});

test('导入坏档：拒绝、原文件按现有机制备份、不写主槽', () => {
  const storage = new FakeStorage();
  let clock = 1_700_000_000_000;
  const store = new SaveStore({ storage, wallClock: () => (clock += 1) });
  const result = store.importText('{ not json');
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'corrupt');
  assert.ok(result.message.includes('备份'));
  assert.equal(store.listBackups().length, 1, '坏档应生成带时间戳备份');
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), null, '坏档不得写进主槽');

  // 形状不对（JSON 合法但不是存档）同样按坏档处理。
  const shape = store.importText(JSON.stringify({ schemaVersion: 1, world: {} }));
  assert.equal(shape.ok, false);
  assert.equal(shape.reason, 'corrupt');
  assert.equal(store.listBackups().length, 2);
});

test('导入高版本：拒绝、不迁移不降级、不写槽、不备份', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  const high = JSON.stringify({ ...makeSave(), schemaVersion: 99 });
  const result = store.importText(high);
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'version');
  assert.ok(result.message.includes('v99'));
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), null);
  assert.equal(store.listBackups().length, 0);
});

test('导入超过 2MB：直接拒绝，不解析不备份', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  const huge = `{"pad":"${'x'.repeat(IMPORT_MAX_BYTES + 1)}"}`;
  const result = store.importText(huge);
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'too-large');
  assert.equal(store.listBackups().length, 0);
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), null);
});

test('导入时配额异常：降级停自动存档、提示一次、不抛', () => {
  const storage = new FakeStorage();
  storage.failSet = true;
  const warnings: string[] = [];
  const store = new SaveStore({ storage, onWarning: (message) => warnings.push(message) });
  const result = store.importText(sampleText);
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'storage');
  assert.equal(store.isAutoSaveEnabled(), false);
  assert.equal(warnings.length, 1);
  assert.ok(warnings[0]?.includes('存储已满'));
});

test('导入后自动存档重新打开（导入是显式用户动作）', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  store.disableAutoSave();
  assert.equal(store.importText(sampleText).ok, true);
  assert.equal(store.isAutoSaveEnabled(), true);
});

test('P2：导入带多余字段的存档 → 按坏档拒绝并备份，不写主槽', () => {
  const storage = new FakeStorage();
  const store = new SaveStore({ storage });
  const tampered = JSON.parse(sampleText) as Record<string, unknown> & {
    world: Record<string, unknown>;
  };
  tampered.world.smuggledField = 'x';
  const result = store.importText(JSON.stringify(tampered));
  assert.equal(result.ok, false);
  assert.equal(result.reason, 'corrupt');
  assert.ok(result.message.includes('校验不通过'));
  assert.equal(store.listBackups().length, 1);
  assert.equal(storage.getItem(SAVE_STORAGE_KEY), null);
});

test('P3：parseSaveText 是 read/import 共用的那条链（三类输入结果一致）', () => {  const ok = parseSaveText(sampleText);
  assert.equal(ok.ok, true);

  const version = parseSaveText(JSON.stringify({ ...JSON.parse(sampleText), schemaVersion: 99 }));
  assert.equal(version.ok, false);
  if (!version.ok) assert.equal(version.reason, 'version');

  const corrupt = parseSaveText('{ not json');
  assert.equal(corrupt.ok, false);
  if (!corrupt.ok) {
    assert.equal(corrupt.reason, 'corrupt');
    assert.equal(corrupt.jsonInvalid, true);
  }

  const schemaBad = parseSaveText(JSON.stringify({ ...JSON.parse(sampleText), world: { ...JSON.parse(sampleText).world, extra: 1 } }));
  assert.equal(schemaBad.ok, false);
  if (!schemaBad.ok) assert.equal(schemaBad.reason, 'corrupt');

  // read() 与 importText() 对同一段文本给出同样的判定。
  const storage = new FakeStorage();
  storage.setItem(SAVE_STORAGE_KEY, '{ not json');
  const store = new SaveStore({ storage });
  assert.equal(store.read().ok, false);
  assert.equal(store.importText('{ not json').ok, false);
});

test('P3：纯空白文本在 read 与 importText 里都是 empty（同一个标签）', () => {
  const blank = ' \n\t ';
  const parsed = parseSaveText(blank);
  assert.equal(parsed.ok, false);
  if (!parsed.ok) assert.equal(parsed.reason, 'empty', '空白文本必须判 empty，不是 corrupt');

  const storage = new FakeStorage();
  storage.setItem(SAVE_STORAGE_KEY, blank);
  const store = new SaveStore({ storage });
  const read = store.read();
  assert.equal(read.ok, false);
  if (!read.ok) assert.equal(read.reason, 'empty');
  assert.equal(store.listBackups().length, 0, '空白档不按坏档备份');

  const imported = store.importText(blank);
  assert.equal(imported.ok, false);
  assert.equal(imported.reason, 'empty');
});
