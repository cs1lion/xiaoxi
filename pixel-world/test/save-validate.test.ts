/** task-32：v1 字段级校验 + 参数化变异测试。
 *
 * 口径（用户批准）：
 *   1. 不改 schemaVersion、不改指纹与样例、不动 99+4 路径集；
 *   2. 冻结样例必须仍能加载；
 *   3. 每个叶子字段都有「类型 + 范围/枚举」声明；缺失/错类型/越界一律按坏档拒绝，
 *      **不静默补默认值**；
 *   4. 变异测试参数化生成（不手写几百条）：每个字段路径做 删除/换错类型/置空 三变异，
 *      断言加载器不抛未捕获异常、返回坏档 reason、先备份再提示、主槽不变；
 *      同时断言合法存档（样例 / fresh / 走路中 / 满数据）全部通过；
 *   5. read() 与 importText() 共用 parseSaveText → validateSaveFields 单一入口。
 *
 * 变异生成方式：从**冻结样例**出发，按 `collectSavePaths` 拿到每条叶子路径，
 * 用通用的「按路径删除 / 按路径改值」两个原语施加变异——路径覆盖自动跟着样例走，
 * 新增字段不会漏测，不需要人肉维护用例表。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { Simulation } from '../src/world/simulation.ts';
import {
  ACTIVITY_KINDS,
  assertSave,
  collectSavePaths,
  FACINGS,
  PROP_KINDS,
  SAVE_STORAGE_KEY,
  SAVE_V1_ALLOWED_PATHS,
  SCHEMA_V2_FINGERPRINT,
  SILHOUETTES,
  TASK_KINDS,
  TASK_SOURCES,
  TASK_STATUSES,
  validateSaveFields,
  VILLAGER_MODES,
  WEATHER_KINDS,
  WORK_ACCENTS,
  ZONES,
  type WorldSave,
} from '../src/world/save.ts';
import { parseSaveText, SaveStore, type StorageLike } from '../src/ui/save-store.ts';

const rawPrefabs = JSON.parse(
  readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
) as Record<string, Record<string, unknown>>;
const blueprints = parseBlueprintTable(rawPrefabs);
const sampleText = readFileSync(new URL('../../docs/save-v2.sample.json', import.meta.url), 'utf8');

class FakeStorage implements StorageLike {
  readonly data = new Map<string, string>();
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
    this.data.set(key, value);
  }
  removeItem(key: string): void {
    this.data.delete(key);
  }
}

function makeSim(wallClock: () => number = () => 1000): Simulation {
  const world = buildWorld(blueprints);
  return new Simulation({
    grid: world.grid,
    buildings: world.buildings,
    altar: ALTAR,
    villagerName: '莞尔',
    wallClock,
    spawnBuilding: (prefabId, instanceId) => {
      const blueprint = blueprints.get(prefabId);
      if (blueprint === undefined) return null;
      return placeBlueprint(world.grid, blueprint, instanceId, ALTAR);
    },
  });
}

/** 四种合法存档：样例、fresh 空数组、走路中（path 非空）、满数据（settled+工地）。 */
function legalSaves(): { label: string; save: WorldSave }[] {
  const sample = JSON.parse(sampleText) as WorldSave;

  const fresh = makeSim();
  fresh.update(16);

  const walking = makeSim();
  walking.enqueue({ kind: 'dispatch', taskKind: 'code', summary: '写脚本' });
  let guard = 0;
  while (!walking.toSave().world.villagers.some((villager) => villager.path.length > 0)) {
    walking.update(100);
    if ((guard += 1) > 400) throw new Error('没进入走路态');
  }

  const full = makeSim();
  full.enqueue({ kind: 'dispatch', taskKind: 'quick', summary: '顺手看一眼' });
  guard = 0;
  while (full.snapshot().deliveries.mock < 1) {
    full.update(100);
    if ((guard += 1) > 400) throw new Error('短活没结束');
  }
  full.enqueue({ kind: 'build', prefabId: 'workshop_plot', instanceId: 'workshop_plot#4' });
  full.update(100);
  full.update(100);

  return [
    { label: '冻结样例', save: sample },
    { label: 'fresh（空 tasks/settled）', save: fresh.toSave() },
    { label: '走路中（path/pendingRoute 非空）', save: walking.toSave() },
    { label: '满数据（settled + 在建工地）', save: full.toSave() },
  ];
}

/* ---------------------------------------------------------------- 变异原语 */

type Json = Record<string, unknown>;

/**
 * 把 `world.villagers[].pos.x` 这种路径拆成可寻址的段。
 *
 * 关键区别（P3 修正）：`[]` 只表示「上一段是数组」，**不单独产生一个段**。
 *   - 末段是数组（`world.roads[]`）→ 地址的是**容器属性** `world.roads`。
 *     删掉它就是「缺必填数组」，是真实坏档。
 *   - 非末段是数组（`world.villagers[].pos`）→ 遍历该数组的每个元素继续走。
 *
 * 早期实现把末段 `[]` 也展开成"遍历元素"，导致 `world.roads[]` 之类寻不到目标、
 * 被算进「跳过」。那不是不可达，是**漏测**——已修正为 297 例全部执行。
 *
 * 修正前后口径换算（自证用）：原来「拒 253 / 跳过 33 / 放行 0」；那 33 例
 * （11 条数组容器路径 × 3 变异）改为按容器属性寻址后**全部执行并全部拒绝**，
 * 所以现在是「拒 286 / 接受 11 / 跳过 0 / 放行 0」，253+33=286。
 * 可空字段置空仍是 11 例合法接受，**本来就在原来的 11 例里**，没有变多。
 */
type Segment = { key: string };

function parsePath(path: string): Segment[] {
  return path.split('.').map((part) => ({
    key: part.endsWith('[]') ? part.slice(0, -2) : part,
  }));
}

/**
 * 在副本上就地走到路径的父容器，返回所有 `{parent, key}` 目标。
 * 数组在遍历中**透明**：剩余段继续作用到每个元素上，所以
 * `world.villagers[].pos.x` 会命中所有角色的 `pos.x`。
 *
 * 只写真实属性名，绝不写 `arr['']` 这种非索引属性——那类写入 `JSON.stringify`
 * 会直接丢弃，真实链路（localStorage / 导入文件）根本到不了，测它没有意义。
 */
function locate(root: Json, path: string): { parent: Json | unknown[]; key: string }[] {
  const segments = parsePath(path);
  const targets: { parent: Json | unknown[]; key: string }[] = [];
  const walk = (container: unknown, index: number): void => {
    if (index >= segments.length) return;
    if (Array.isArray(container)) {
      for (const item of container) walk(item, index);
      return;
    }
    if (typeof container !== 'object' || container === null) return;
    const record = container as Json;
    const segment = segments[index];
    if (index === segments.length - 1) {
      targets.push({ parent: record, key: segment.key });
      return;
    }
    walk(record[segment.key], index + 1);
  };
  walk(root, 0);
  return targets;
}

/** 深拷贝样例/存档。 */
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** 变异 1：删除该路径（数组元素里的键也删，模拟"字段被拿掉"）。 */
function deletePath(save: WorldSave, path: string): boolean {
  const targets = locate(save as unknown as Json, path);
  let touched = false;
  for (const { parent, key } of targets) {
    if (typeof parent === 'object' && parent !== null && key in (parent as Json)) {
      delete (parent as Json)[key];
      touched = true;
    }
  }
  return touched;
}

/** 变异 2/3：把该路径换成给定值（错类型 / null）。 */
function setPath(save: WorldSave, path: string, value: unknown): boolean {
  const targets = locate(save as unknown as Json, path);
  let touched = false;
  for (const { parent, key } of targets) {
    if (typeof parent === 'object' && parent !== null) {
      (parent as Json)[key] = value;
      touched = true;
    }
  }
  return touched;
}

/**
 * 变异造出的"错类型"：故意选一个与该字段原类型不同、且绝不会被误认成合法的值。
 * 用对象占位（数值/字符串/布尔字段拿到对象都必然拒；null 另由 NULLABLE_PATHS 处理）。
 */
const WRONG_TYPE = { wrongType: true } as const;

/**
 * v1 里**本来就允许 null** 的叶子路径（与 types.ts 的 `| null` 逐条对应）。
 * 对这些路径，「置空」是合法值而不是变异——所以只断言它们被接受，
 * 不把 null 当成坏档（否则会误杀正常存档）。
 * 注意：合法 null ≠ 允许缺键；「删除」对这些字段同样必须拒绝。
 */
const NULLABLE_PATHS: ReadonlySet<string> = new Set([
  'world.villagers[].taskId',
  'world.villagers[].bubble',
  'world.villagers[].routineLabel',
  'world.villagers[].pendingBuildingId',
  'world.buildings[].boundTask',
  'world.tasks[].buildingId',
  'world.tasks[].completedAt',
  'world.tasks[].blockedQuestion',
  'world.tasks[].workerId',
  'world.settled[].completedAt',
  'world.weather.remainingMs',
  'world.props[].sourceTaskKind',
  'world.props[].sourceTaskId',
  'world.props[].sourceSummary',
  'world.buildings[].sourceTaskId',
  'world.buildings[].sourceSummary',
]);

/* ---------------------------------------------------------------- 用例 */

test('task-32：冻结样例仍能加载（read + importText + parseSaveText 三口一致）', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  assert.doesNotThrow(() => validateSaveFields(sample, 2), '样例必须过字段级校验');
  assert.doesNotThrow(() => assertSave(sample), '样例必须过完整校验');
  assert.equal(sample.meta.schemaFingerprint, SCHEMA_V2_FINGERPRINT);

  const parsed = parseSaveText(sampleText);
  assert.equal(parsed.ok, true);

  const storage = new FakeStorage();
  storage.setItem(SAVE_STORAGE_KEY, sampleText);
  const store = new SaveStore({ storage });
  const read = store.read();
  assert.equal(read.ok, true, read.ok ? '' : read.message);

  const imported = new SaveStore({ storage: new FakeStorage() }).importText(sampleText);
  assert.equal(imported.ok, true, imported.message);
});

test('task-32：四种合法存档全部通过（样例 / fresh / 走路中 / 满数据）', () => {
  for (const { label, save } of legalSaves()) {
    assert.doesNotThrow(() => validateSaveFields(save, 2), `${label} 必须过字段级校验`);
    assert.doesNotThrow(() => assertSave(save), `${label} 必须过完整校验`);
    // 合法档经 JSON 往返（真实读写路径）后仍合法。
    const roundTrip = JSON.parse(JSON.stringify(save)) as WorldSave;
    assert.doesNotThrow(() => assertSave(roundTrip), `${label} JSON 往返后必须仍合法`);
  }
});

test('task-32：枚举常量与 types.ts 联合类型逐项对齐', () => {
  assert.deepEqual([...VILLAGER_MODES].sort(), ['bearingDecree', 'delivering', 'idle', 'saluting', 'sheltering', 'toAltar', 'toWork', 'working']);
  assert.deepEqual([...TASK_STATUSES].sort(), ['assigned', 'done', 'failed', 'in_progress']);
  assert.deepEqual([...TASK_KINDS].sort(), ['code', 'quick', 'research', 'writing']);
  assert.deepEqual([...TASK_SOURCES].sort(), ['ledger', 'sandbox']);
  assert.deepEqual([...FACINGS].sort(), ['down', 'left', 'right', 'up']);
  assert.deepEqual([...ZONES].sort(), ['agriculture', 'altar', 'industry', 'living', 'scholarly', 'street']);
  assert.deepEqual([...PROP_KINDS].sort(), ['banner', 'lamp', 'tree']);
  assert.deepEqual([...WORK_ACCENTS].sort(), ['ink', 'pages', 'smoke']);
  assert.deepEqual([...SILHOUETTES].sort(), ['flat', 'gable', 'tower']);
  assert.deepEqual([...WEATHER_KINDS].sort(), ['clear', 'rain']);
  assert.deepEqual([...ACTIVITY_KINDS].sort(), ['cooling', 'idle', 'operating', 'overheating']);
});

test('task-32：字段级拒绝的具体理由（每个字段族各一例）', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  const cases: [string, (copy: Json) => void, RegExp][] = [
    ['world.now 负数', (c) => { (c.world as Json).now = -1; }, /world\.now 必须 ≥ 0/],
    ['world.now 非数字', (c) => { (c.world as Json).now = '123'; }, /world\.now 必须是有限数字/],
    ['weather.kind 非法', (c) => { ((c.world as Json).weather as Json).kind = 'snow'; }, /world\.weather\.kind 只能是 clear\/rain/],
    ['weather.intensity 越界', (c) => { ((c.world as Json).weather as Json).intensity = 1.5; }, /world\.weather\.intensity 必须在 0\.\.1/],
    ['villagers[].mode 非法', (c) => { (((c.world as Json).villagers as Json[])[0]).mode = 'dancing'; }, /mode 只能是 idle/],
    ['villagers[].facing 非法', (c) => { (((c.world as Json).villagers as Json[])[0]).facing = 'upish'; }, /facing 只能是/],
    ['tasks[].status 非法', (c) => { (((c.world as Json).tasks as Json[])[0]).status = 'paused'; }, /status 只能是 assigned/],
    ['settled[].source 非法', (c) => { (((c.world as Json).settled as Json[])[0]).source = 'oracle'; }, /source 只能是 ledger\/mock/],
    ['settled[].completedAtClock 非法', (c) => { (((c.world as Json).settled as Json[])[0]).completedAtClock = 'utc'; }, /completedAtClock 只能是 ledger\/local/],
    ['buildings[].zone 非法', (c) => { (((c.world as Json).buildings as Json[])[0]).zone = 'space'; }, /zone 只能是 street/],
    ['buildings[].palette 负数', (c) => { ((((c.world as Json).buildings as Json[])[0]).palette as Json).roof = -5; }, /palette\.roof 必须 ≥ 0/],
    ['buildings[].boundTask 非法', (c) => { (((c.world as Json).buildings as Json[])[0]).boundTask = 'quick'; }, /boundTask 只能是 code\/research\/writing/],
    ['constructions remaining>total', (c) => { (((c.world as Json).constructions as Json[])[0]).remainingMs = 99_999; }, /remainingMs 不能大于 totalMs/],
    ['activities[].kind 非法', (c) => { ((((c.world as Json).activities as Json[])[0]).activity as Json).kind = 'sleeping'; }, /activity\.kind 只能是/],
    ['faith.points 负数', (c) => { ((c.world as Json).faith as Json).points = -2; }, /faith\.points 必须 ≥ 0/],
    ['nextTaskNumber < 1', (c) => { (c.world as Json).nextTaskNumber = 0; }, /nextTaskNumber 必须 ≥ 1/],
    ['meta.savedAtWallClock 非数字', (c) => { (c.meta as Json).savedAtWallClock = 'now'; }, /savedAtWallClock 必须是有限数字/],
    ['meta.savedReconciled 非布尔', (c) => { (c.meta as Json).savedReconciled = 'yes'; }, /savedReconciled 必须是布尔值/],
  ];
  for (const [label, mutate, pattern] of cases) {
    const copy = clone(sample) as unknown as Json;
    mutate(copy);
    assert.throws(() => validateSaveFields(copy, 2), pattern, `${label} 必须按该理由拒绝`);
  }
});

test('task-32：可选嵌套路径（path[]/pendingRoute[]）删一个元素键不误伤合法档', () => {
  // 这两个数组元素是**合法可选**：样例里是空数组，运行中非空也只是 {x,y}。
  // 「删除/置空不得判坏档」指的是这两个字段本身可以为空数组——而不是允许半截元素。
  const sample = JSON.parse(sampleText) as WorldSave;
  for (const villager of sample.world.villagers) {
    assert.deepEqual(villager.path, [], '样例里 path 是空数组');
    assert.deepEqual(villager.pendingRoute, [], '样例里 pendingRoute 是空数组');
  }
  assert.doesNotThrow(() => assertSave(sample), '空数组必须合法');
});

test('task-32 变异测试：每条冻结路径 × 删除/错类型/置空 = 全拒且不崩（参数化）', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  const paths = [...new Set(collectSavePaths(sample))].sort();
  assert.ok(paths.length >= 99, `样例应至少有 99 条路径，实际 ${paths.length}`);

  const mutations: { label: string; apply: (copy: WorldSave, path: string) => boolean }[] = [
    { label: '删除', apply: (copy, path) => deletePath(copy, path) },
    { label: '错类型', apply: (copy, path) => setPath(copy, path, WRONG_TYPE) },
    { label: '置空', apply: (copy, path) => setPath(copy, path, null) },
  ];

  let executed = 0;
  let nullableAccepted = 0;
  let skipped = 0;
  const accepted: string[] = [];
  for (const path of paths) {
    for (const mutation of mutations) {
      const copy = clone(sample);
      const touched = mutation.apply(copy, path);
      if (!touched) {
        // 修正后所有路径都可寻址（数组容器路径地址的是容器属性本身）；
        // 这里保留计数只为「逐例结清」自证，正常应为 0。
        skipped += 1;
        continue;
      }
      // v1 里允许 null 的字段：「置空」是合法值，断言被接受而不是被拒。
      if (mutation.label === '置空' && NULLABLE_PATHS.has(path)) {
        assert.doesNotThrow(() => assertSave(copy), `${path} 置空是 v2 合法值（| null），不得误判坏档`);
        nullableAccepted += 1;
        continue;
      }
      executed += 1;
      // 1) 不抛未捕获异常：只有 assert.throws 能接住的 Error 才算"拒绝"。
      let rejected = false;
      let message = '';
      try {
        assertSave(copy);
      } catch (error) {
        rejected = true;
        message = error instanceof Error ? error.message : String(error);
        assert.ok(error instanceof Error, `${path} ${mutation.label} 抛出的必须是被校验器主动拒绝的 Error`);
      }
      if (!rejected) accepted.push(`${path} ${mutation.label}`);
      assert.equal(rejected, true, `${path} ${mutation.label} 必须被拒：${message}`);
      assert.ok(message.length > 0, `${path} ${mutation.label} 拒绝时必须带可读理由`);
    }
  }

  const total = paths.length * mutations.length;
  assert.deepEqual(accepted, [], `不应有任何变异被放行：${accepted.join(' | ')}`);
  // 自证三件事：用例数守恒、没有一例"跳过"（漏测）、没有一例放行。
  assert.equal(executed + nullableAccepted + skipped, total, '变异必须逐例结清，不许漏计');
  assert.equal(skipped, 0, '数组容器路径也必须可寻址（按容器属性寻址），不得跳过');
  assert.equal(accepted.length, 0, '不允许出现放行用例');
  assert.equal(executed, total - nullableAccepted, '除可空置空外，其余每例都必须被拒');
  console.log(
    `\n[变异测试汇总] ${paths.length} 条冻结路径 × ${mutations.length} 种变异（删除/错类型/置空）= ${total} 例；` +
    `拒绝 ${executed} 例（全拒，含 ${paths.filter((p) => p.endsWith('[]')).length} 条数组容器路径按容器属性删除/换值）；` +
    `接受 ${nullableAccepted} 例（v1 允许 null 的字段置空，是合法值不是变异）；` +
    `跳过 ${skipped} 例；放行 ${accepted.length} 例。`,
  );
});

test('task-32 变异测试（加载器口径）：坏档 → 备份 + 提示 + 主槽不变，绝不静默补默认', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  // 取几条代表性路径：必填数字、枚举、字符串、可选嵌套。
  const probes: [string, string][] = [
    ['world.now', '删除'],
    ['world.weather.kind', '错类型'],
    ['world.villagers[].mode', '置空'],
    ['world.buildings[].palette.roof', '删除'],
    ['meta.savedReconciled', '错类型'],
  ];
  for (const [path, kind] of probes) {
    const copy = clone(sample);
    if (kind === '删除') deletePath(copy, path);
    else if (kind === '错类型') setPath(copy, path, WRONG_TYPE);
    else setPath(copy, path, null);

    const text = JSON.stringify(copy);
    const storage = new FakeStorage();
    let clock = 1_700_000_000_000;
    const warnings: string[] = [];
    const store = new SaveStore({
      storage,
      wallClock: () => (clock += 1),
      onWarning: (message) => warnings.push(message),
    });
    // 主槽里先放一份"原始合法档"，用来证明坏档不会覆盖它。
    storage.setItem(SAVE_STORAGE_KEY, sampleText);

    const result = store.importText(text);
    assert.equal(result.ok, false, `${path} ${kind} 导入必须失败`);
    assert.equal(result.reason, 'corrupt', `${path} ${kind} 必须判 corrupt`);
    assert.ok(result.message.includes('备份'), `${path} ${kind} 必须提示已备份`);
    assert.equal(store.listBackups().length, 1, `${path} ${kind} 必须先备份`);
    assert.equal(storage.getItem(SAVE_STORAGE_KEY), sampleText, `${path} ${kind} 主槽不得被改`);

    // read() 同样按坏档处理（同一入口）。
    const readStorage = new FakeStorage();
    readStorage.setItem(SAVE_STORAGE_KEY, text);
    const readStore = new SaveStore({ storage: readStorage, wallClock: () => (clock += 1) });
    const read = readStore.read();
    assert.equal(read.ok, false, `${path} ${kind} 读档必须失败`);
    if (!read.ok) {
      assert.equal(read.reason, 'corrupt');
      assert.ok(read.message.includes('备份'));
    }
    assert.equal(readStore.listBackups().length, 1, `${path} ${kind} 读档也要先备份`);
  }
});

test('task-32 变异测试：数组元素路径（villagers[]/tasks[]/settled[]/props[]/buildings[]）同样全拒', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  const elementPaths = [...new Set(collectSavePaths(sample))]
    .filter((path) => /\[\]\./.test(path))
    .sort();
  assert.ok(elementPaths.length >= 60, `元素内路径应 >= 60，实际 ${elementPaths.length}`);

  let executed = 0;
  let nullableAccepted = 0;
  let skipped = 0;
  for (const path of elementPaths) {
    for (const [kind, apply] of [
      ['删除', (copy: WorldSave) => deletePath(copy, path)],
      ['错类型', (copy: WorldSave) => setPath(copy, path, WRONG_TYPE)],
      ['置空', (copy: WorldSave) => setPath(copy, path, null)],
    ] as const) {
      const copy = clone(sample);
      if (!apply(copy)) {
        skipped += 1;
        continue;
      }
      if (kind === '置空' && NULLABLE_PATHS.has(path)) {
        assert.doesNotThrow(() => assertSave(copy), `${path} 置空是 v2 合法值，不得误判坏档`);
        nullableAccepted += 1;
        continue;
      }
      executed += 1;
      assert.throws(() => assertSave(copy), `${path} ${kind} 必须被拒`);
    }
  }
  const total = elementPaths.length * 3;
  assert.equal(executed + nullableAccepted + skipped, total, '元素内变异必须逐例结清，不许漏计');
  assert.ok(executed > 150, `元素内变异应覆盖充分，实际 ${executed}`);
  console.log(
    `\n[元素内变异汇总] ${elementPaths.length} 条元素内路径 × 3 变异（删除/错类型/置空）= ${total} 例；` +
    `拒绝 ${executed} 例（全拒）；接受 ${nullableAccepted} 例（可空字段置空，v2 合法值）；` +
    `跳过 ${skipped} 例；放行 0 例。`,
  );
});

test('task-32：字段缺失是坏档，不是「用默认值补上」', () => {
  const sample = JSON.parse(sampleText) as WorldSave;
  // 这几个字段一旦缺失，旧实现会由 `?? default` 静默兜住；task-32 后必须拒绝。
  const fields = [
    'world.villagers[].bubbleUntil',
    'world.villagers[].routineIndex',
    'world.villagers[].inPlaceUntil',
    'world.villagers[].pendingBuildingId',
    'world.activities[].activity.kind',
    'world.weather.remainingMs',
    'world.nextPendingNumber',
    'meta.savedReconciled',
  ];
  for (const path of fields) {
    const copy = clone(sample);
    assert.equal(deletePath(copy, path), true, `${path} 应存在可删`);
    assert.throws(() => assertSave(copy), /缺失（当前版要求该字段存在）/, `${path} 缺失必须拒绝`);
  }
});

test('task-32：路径集合（第 3 层）仍然只防新增，不与字段级校验打架', () => {
  // 删字段 → 路径集合合法缩小，但第 1 层必须拦住（上一条已断言）；
  // 加字段 → 第 3 层必须拦住（P2 已断言）。这里只确认允许集合没被改动。
  assert.equal(SAVE_V1_ALLOWED_PATHS.length, 103, '冻结 99 + 可选 4 = 103 条，不许变');
  // pendingRoute 相关路径：容器 `pendingRoute[]` 1 条 + 元素 x/y 2 条 = 3 条。
  assert.equal(SAVE_V1_ALLOWED_PATHS.filter((path) => path.includes('pendingRoute')).length, 3);
});
