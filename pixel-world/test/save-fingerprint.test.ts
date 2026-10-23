import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  assertSave,
  collectSavePaths,
  fingerprintOfPaths,
  SAVE_SETTLED_KEYS,
  SAVE_TASK_KEYS,
  SAVE_V1_ALLOWED_PATHS,
  SAVE_V1_OPTIONAL_PATHS,
  SAVE_V1_PATHS,
  SAVE_V2_ADDED_PATHS,
  SCHEMA_V1_FINGERPRINT,
  SCHEMA_V2_FINGERPRINT,
  saveFingerprint,
  type SaveWorld,
  type SettledRecord,
  type WorldSaveV1,
  type WorldSaveV2,
} from '../src/world/save.ts';

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
  ? true
  : false;
type Assert<T extends true> = T;
// 编译期 Equal：当前版本的存档类型必须与 v2 冻结样例逐键一致。
// v1 的样例只用于「旧档仍能自证」，不再与运行期类型对齐（旧档没有 oracle 字段）。
import type sampleJson from '../../docs/save-v2.sample.json';
type _TopLevel = Assert<Equal<keyof typeof sampleJson, keyof WorldSaveV2>>;
type _World = Assert<Equal<keyof (typeof sampleJson)['world'], keyof SaveWorld>>;
type _Meta = Assert<Equal<keyof (typeof sampleJson)['meta'], keyof WorldSaveV2['meta']>>;
type _Settled = Assert<
  Equal<keyof (typeof sampleJson)['world']['settled'][number], keyof SettledRecord>
>;
export type _FingerprintAssertions = [_TopLevel, _World, _Meta, _Settled];

const sample = JSON.parse(
  readFileSync(new URL('../../docs/save-v2.sample.json', import.meta.url), 'utf8'),
) as WorldSaveV2;

const sampleV1 = JSON.parse(
  readFileSync(new URL('../../docs/save-v1.sample.json', import.meta.url), 'utf8'),
) as WorldSaveV1;

test('v1 冻结样例原样不动：99 条路径、旧指纹仍然成立', () => {
  const paths = [...new Set(collectSavePaths(sampleV1))].sort();
  assert.equal(paths.length, 99, 'v1 冻结路径条数');
  assert.equal(fingerprintOfPaths(paths), SCHEMA_V1_FINGERPRINT);
  assert.equal(sampleV1.meta.schemaFingerprint, SCHEMA_V1_FINGERPRINT);
  assert.equal(saveFingerprint(sampleV1), SCHEMA_V1_FINGERPRINT);
});

test('v2 样例：114 条叶子路径，指纹等于新常量', () => {
  const paths = [...new Set(collectSavePaths(sample))].sort();
  assert.equal(paths.length, 114, 'v2 路径条数');
  assert.equal(fingerprintOfPaths(paths), SCHEMA_V2_FINGERPRINT);
  assert.equal(sample.meta.schemaFingerprint, SCHEMA_V2_FINGERPRINT);
  assert.equal(saveFingerprint(sample), SCHEMA_V2_FINGERPRINT);
});

test('v2 路径全集 = v1 全集 + 新增十三条，没有漏加也没有重复', () => {
  const v2Only = [...new Set(collectSavePaths(sample))].filter(
    (path) => !SAVE_V1_ALLOWED_PATHS.includes(path),
  );
  assert.deepEqual([...new Set(v2Only)].sort(), [...SAVE_V2_ADDED_PATHS].sort());
});

test('样例能通过当前版本校验（字段与 v2 一致）', () => {
  assertSave(sample);
  assert.equal(sample.schemaVersion, 2);
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
  assert.notEqual(saveFingerprint(mutated), SCHEMA_V2_FINGERPRINT, '加字段必须改变指纹');

  const removed = JSON.parse(JSON.stringify(sample)) as Record<string, unknown> & {
    world: Record<string, unknown>;
  };
  delete removed.world.faith;
  assert.notEqual(saveFingerprint(removed), SCHEMA_V2_FINGERPRINT, '删字段必须改变指纹');

  // 加载时按 meta.schemaFingerprint 判坏档：值不符 → 拒绝。
  const wrongMeta = JSON.parse(JSON.stringify(sample)) as WorldSaveV2 & {
    meta: { schemaFingerprint: string };
  };
  wrongMeta.meta.schemaFingerprint = 'deadbeefdeadbeef';
  assert.throws(() => assertSave(wrongMeta));
});

// ------------------------------- P2：路径集合包含校验（只防新增路径，不重算双常量）

test('v1 冻结路径全集：99 条、与 v1 样例逐条一致、指纹等于常量', () => {
  const fromSample = [...new Set(collectSavePaths(sampleV1))].sort();
  assert.equal(SAVE_V1_PATHS.length, 99);
  assert.deepEqual([...SAVE_V1_PATHS].sort(), fromSample, '常量必须与 docs 样例路径逐条一致');
  assert.equal(fingerprintOfPaths(SAVE_V1_PATHS), SCHEMA_V1_FINGERPRINT);
  // 允许集合 = 样例 99 条 + 正在走路才会出现的可选嵌套路径。
  assert.deepEqual(
    new Set(SAVE_V1_ALLOWED_PATHS),
    new Set([...SAVE_V1_PATHS, ...SAVE_V1_OPTIONAL_PATHS]),
  );
});

test('P2：顶层 / world / meta / 任意数组元素加字段 → 拒绝，且报错指名越界路径', () => {
  const mutate = (
    apply: (copy: Record<string, unknown> & { world: Record<string, unknown>; meta: Record<string, unknown> }) => void,
  ): unknown => {
    const copy = JSON.parse(JSON.stringify(sample)) as Record<string, unknown> & {
      world: Record<string, unknown>;
      meta: Record<string, unknown>;
    };
    apply(copy);
    return copy;
  };
  const cases: [string, unknown, string][] = [
    ['顶层', mutate((copy) => { copy.unexpectedTopLevel = 1; }), 'unexpectedTopLevel'],
    ['world', mutate((copy) => { copy.world.unexpectedWorldField = 1; }), 'world.unexpectedWorldField'],
    ['meta', mutate((copy) => { copy.meta.unexpectedMetaField = 1; }), 'meta.unexpectedMetaField'],
    [
      'villagers 元素',
      mutate((copy) => {
        const villagers = copy.world.villagers as Record<string, unknown>[];
        villagers[0].unexpectedVillagerField = 1;
      }),
      'world.villagers[].unexpectedVillagerField',
    ],
    [
      'buildings 元素',
      mutate((copy) => {
        const buildings = copy.world.buildings as Record<string, unknown>[];
        buildings[0].unexpectedBuildingField = 1;
      }),
      'world.buildings[].unexpectedBuildingField',
    ],
  ];
  for (const [label, mutated, path] of cases) {
    assert.throws(
      () => assertSave(mutated),
      (error: unknown) => {
        assert.ok(error instanceof Error);
        assert.match(error.message, /字段漂移/);
        assert.ok(error.message.includes(path), `${label}报错必须指名越界路径 ${path}：${error.message}`);
        return true;
      },
      `${label}加字段必须被 assertSave 拒绝`,
    );
  }
});

test('P2：meta.schemaFingerprint 缺失或不是字符串 → 拒绝', () => {
  const missing = JSON.parse(JSON.stringify(sample)) as {
    meta: Record<string, unknown>;
  };
  delete missing.meta.schemaFingerprint;
  assert.throws(() => assertSave(missing), /缺 meta\.schemaFingerprint/);

  const notString = JSON.parse(JSON.stringify(sample)) as { meta: Record<string, unknown> };
  notString.meta.schemaFingerprint = 12345;
  assert.throws(() => assertSave(notString), /缺 meta\.schemaFingerprint/);

  const nulled = JSON.parse(JSON.stringify(sample)) as { meta: Record<string, unknown> };
  nulled.meta.schemaFingerprint = null;
  assert.throws(() => assertSave(nulled), /缺 meta\.schemaFingerprint/);
});

test('P2：伪造 meta 字段骗不过校验（改值 / 加字段都拒）', () => {
  const wrongValue = JSON.parse(JSON.stringify(sample)) as { meta: Record<string, unknown> };
  wrongValue.meta.schemaFingerprint = 'deadbeefdeadbeef';
  assert.throws(() => assertSave(wrongValue), /指纹不符/);

  const spoofed = JSON.parse(JSON.stringify(sample)) as Record<string, unknown> & {
    world: Record<string, unknown>;
    meta: Record<string, unknown>;
  };
  // 加字段 + meta 里仍写着"看起来合法"的常量：指纹层放行，包含校验必须拒绝。
  spoofed.world.smuggledField = 1;
  spoofed.meta.schemaFingerprint = SCHEMA_V2_FINGERPRINT;
  assert.throws(() => assertSave(spoofed), /字段漂移/);
});
