import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  assertSaveV1,
  collectSavePaths,
  fingerprintOfPaths,
  SAVE_SETTLED_KEYS,
  SAVE_TASK_KEYS,
  SCHEMA_V1_FINGERPRINT,
  saveFingerprint,
  type SaveWorld,
  type SettledRecord,
  type WorldSaveV1,
} from '../src/world/save.ts';

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true
  : false;
type Assert<T extends true> = T;
// 编译期 Equal：WorldSaveV1 的顶层/世界/meta/settled 字段必须与冻结样例一致。
import type sampleJson from '../../docs/save-v1.sample.json';
type _TopLevel = Assert<Equal<keyof typeof sampleJson, keyof WorldSaveV1>>;
type _World = Assert<Equal<keyof (typeof sampleJson)['world'], keyof SaveWorld>>;
type _Meta = Assert<Equal<keyof (typeof sampleJson)['meta'], keyof WorldSaveV1['meta']>>;
type _Settled = Assert<
  Equal<keyof (typeof sampleJson)['world']['settled'][number], keyof SettledRecord>
>;
export type _FingerprintAssertions = [_TopLevel, _World, _Meta, _Settled];

const sample = JSON.parse(
  readFileSync(new URL('../../docs/save-v1.sample.json', import.meta.url), 'utf8'),
) as WorldSaveV1;

test('冻结样例：99 条叶子路径，指纹等于常量', () => {
  const paths = [...new Set(collectSavePaths(sample))].sort();
  assert.equal(paths.length, 99, '冻结路径条数');
  assert.equal(fingerprintOfPaths(paths), SCHEMA_V1_FINGERPRINT);
  assert.equal(sample.meta.schemaFingerprint, SCHEMA_V1_FINGERPRINT);
  assert.equal(saveFingerprint(sample), SCHEMA_V1_FINGERPRINT);
});

test('样例能通过 assertSaveV1（字段与 §1.2 一致）', () => {
  assertSaveV1(sample);
  assert.equal(sample.schemaVersion, 1);
  assert.equal(sample.world.villagers.length, 2);
  assert.equal(sample.world.settled.length, 2);
});

test('嵌套键集合冻结：样例 tasks[] / settled[] 与常量逐键一致', () => {
  for (const task of sample.world.tasks) {
    assert.deepEqual(
      Object.keys(task).sort(),
      [...SAVE_TASK_KEYS].sort(),
      'world.tasks[] 键集合必须与冻结常量一致（多一个字段就是漂移）',
    );
  }
  assert.ok(sample.world.tasks.length > 0, '样例必须有 task 可供嵌套校验');
  for (const record of sample.world.settled) {
    assert.deepEqual(
      Object.keys(record).sort(),
      [...SAVE_SETTLED_KEYS].sort(),
      'world.settled[] 键集合必须与冻结常量一致',
    );
  }
});

test('变异探针：字段变了但版本没升 → 指纹变化、测试必须红', () => {
  const mutated = JSON.parse(JSON.stringify(sample)) as Record<string, unknown> & {
    world: Record<string, unknown>;
  };
  mutated.world.unexpectedField = 1;
  assert.notEqual(saveFingerprint(mutated), SCHEMA_V1_FINGERPRINT, '加字段必须改变指纹');

  const removed = JSON.parse(JSON.stringify(sample)) as Record<string, unknown> & {
    world: Record<string, unknown>;
  };
  delete removed.world.faith;
  assert.notEqual(saveFingerprint(removed), SCHEMA_V1_FINGERPRINT, '删字段必须改变指纹');

  // 加载时按 meta.schemaFingerprint 判坏档：值不符 → 拒绝。
  const wrongMeta = JSON.parse(JSON.stringify(sample)) as WorldSaveV1 & {
    meta: { schemaFingerprint: string };
  };
  wrongMeta.meta.schemaFingerprint = 'deadbeefdeadbeef';
  assert.throws(() => assertSaveV1(wrongMeta));
});
