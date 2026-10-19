/** task-29 动画采样门禁：12fps、帧号由 simulation 时间决定、位置整数像素。
 *
 * 这些断言不加载 Phaser（`villager-frames.ts` 是纯函数），所以能在 node --test 里跑。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { ALTAR, buildWorld, placeBlueprint } from '../src/world/setup.ts';
import { parseBlueprintTable } from '../src/world/blueprints.ts';
import { Simulation } from '../src/world/simulation.ts';
import {
  ANIM_FPS,
  FRAME_MS,
  IDLE_FRAMES,
  WALK_FRAMES,
  animFrame,
  animStep,
  isWalkingMode,
  pixelAt,
  villagerPose,
} from '../src/render/villager-frames.ts';

test('采样率就是 12fps', () => {
  assert.equal(ANIM_FPS, 12);
  assert.ok(Math.abs(FRAME_MS - 1000 / 12) < 1e-9);
});

test('同一 simulation 时间 → 同一帧号（与调用时机无关）', () => {
  for (const now of [0, 1, 41, 83, 84, 167, 1667, 123_456]) {
    assert.equal(animFrame(now, IDLE_FRAMES), animFrame(now, IDLE_FRAMES));
    assert.equal(animFrame(now, WALK_FRAMES), animFrame(now, WALK_FRAMES));
  }
});

test('帧号每 1/12 秒进位一次，不掉帧也不跳帧', () => {
  const boundaries: number[] = [];
  let last = animStep(0);
  for (let t = 0; t <= 1000; t += 1) {
    const step = animStep(t);
    if (step !== last) {
      boundaries.push(t);
      last = step;
    }
  }
  // 0..1000ms 内应有 12 次进位（83,167,…,1000）
  assert.equal(boundaries.length, 12);
  assert.deepEqual(boundaries, [84, 167, 250, 334, 417, 500, 584, 667, 750, 834, 917, 1000]);
});

test('待机 2 帧 / 走路 4 帧，帧号始终在范围内且会回绕', () => {
  const idleSeen = new Set<number>();
  const walkSeen = new Set<number>();
  for (let t = 0; t < 2000; t += 17) {
    idleSeen.add(animFrame(t, IDLE_FRAMES));
    walkSeen.add(animFrame(t, WALK_FRAMES));
  }
  assert.deepEqual([...idleSeen].sort(), [0, 1]);
  assert.deepEqual([...walkSeen].sort(), [0, 1, 2, 3]);
});

test('只有走路态用 4 帧，其余模式用 2 帧待机', () => {
  assert.equal(isWalkingMode('toWork'), true);
  assert.equal(isWalkingMode('toAltar'), true);
  assert.equal(isWalkingMode('sheltering'), true);
  for (const mode of ['idle', 'saluting', 'working', 'delivering'] as const) {
    const pose = villagerPose(mode, 250);
    assert.equal(pose.frameCount, IDLE_FRAMES, `${mode} 应该走待机 2 帧`);
  }
  const walking = villagerPose('toWork', 250);
  assert.equal(walking.frameCount, WALK_FRAMES);
});

test('姿态偏移全是整数像素（没有逐帧浮点晃动）', () => {
  for (let t = 0; t < 5000; t += 7) {
    for (const mode of ['idle', 'toWork', 'saluting', 'delivering', 'working'] as const) {
      const pose = villagerPose(mode, t);
      assert.ok(Number.isInteger(pose.offsetY), `${mode}@${t} offsetY 非整数`);
      assert.ok(Number.isInteger(pose.footLeft), `${mode}@${t} footLeft 非整数`);
      assert.ok(Number.isInteger(pose.footRight), `${mode}@${t} footRight 非整数`);
      assert.ok(pose.frame >= 0 && pose.frame < pose.frameCount);
    }
  }
});

test('行礼/交付的额外偏移也是整数，且叠在帧偏移上', () => {
  const base = villagerPose('idle', 250);
  const salute = villagerPose('saluting', 250);
  const deliver = villagerPose('delivering', 250);
  assert.equal(salute.offsetY, base.offsetY - 2);
  assert.equal(deliver.offsetY, base.offsetY - 1);
});

test('位置取整：分数坐标一定落到整数像素，不出现 .5', () => {
  const cases = [0, 0.4999, 0.5, 0.51, 3.25, 12.5, -0.5, 7.999];
  for (const value of cases) {
    const px = pixelAt(value);
    assert.ok(Number.isInteger(px), `${value} → ${px} 不是整数`);
    assert.ok(Math.abs(px - value) <= 0.5, `${value} → ${px} 偏离超过半像素`);
  }
  // 世界格中心：整数格 → 整数像素
  const TILE_PX = 16;
  for (const cell of [0, 1, 7, 12]) {
    assert.ok(Number.isInteger(pixelAt((cell + 0.5) * TILE_PX)));
  }
});

test('像素取整不返回 -0（strictEqual 会红）', () => {
  // Math.round(-0.5) 是 -0；像素上等价，但断言里必须归一到 0。
  assert.strictEqual(pixelAt(-0.5), 0);
  assert.ok(Object.is(pixelAt(-0.5), 0), 'pixelAt(-0.5) 不能是 -0');
  assert.strictEqual(pixelAt(-0.49), 0);
  assert.strictEqual(pixelAt(0), 0);
  assert.ok(Object.is(pixelAt(-0), 0));
});

test('采样是纯函数：调用它不会改变输入，也不依赖调用次数', () => {
  const mode = 'toWork' as const;
  const now = 1234;
  const first = villagerPose(mode, now);
  for (let i = 0; i < 100; i += 1) villagerPose(mode, now + i * 13);
  const again = villagerPose(mode, now);
  assert.deepEqual(again, first);
});

test('动画采样不改变世界确定性回放（边跑边采样，结果与不采样一致）', () => {
  const raw = JSON.parse(
    readFileSync(new URL('../src/world/prefabs.json', import.meta.url), 'utf8'),
  ) as Record<string, Record<string, unknown>>;
  const blueprints = parseBlueprintTable(raw);
  const world = buildWorld(blueprints);
  const makeSim = (): Simulation =>
    new Simulation({
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

  const sampled = makeSim();
  const plain = makeSim();
  for (let i = 0; i < 300; i += 1) {
    plain.update(50);
    sampled.update(50);
    // 每步都按当前快照时间采样一次：纯函数，不许影响仿真。
    const snapshot = sampled.snapshot();
    for (const villager of snapshot.villagers) {
      const pose = villagerPose(villager.mode, snapshot.now);
      assert.ok(Number.isInteger(pose.offsetY));
    }
  }
  assert.deepEqual(sampled.snapshot(), plain.snapshot(), '采样改变了仿真回放结果');
});
