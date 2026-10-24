/**
 * 世界的日常动静：白天的鸟、黄昏的蝙蝠。
 *
 * 这层是纯函数（`src/render/ambience.ts` 不 import Phaser），所以能离线测。
 * 断言的三个性质，缺一条这类东西就会坏：
 *
 *   1. **确定性**：同一 now 永远同一组位置。否则截图不复现、回放不一致。
 *   2. **真的在动**：不同 now 的位置必须不同。否则只是一堆静态贴图。
 *   3. **跟时刻走**：白天有鸟没蝙蝠，夜里反过来。否则"氛围"没有时间感。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  AMBIENCE_BY_PHASE,
  ambienceAt,
  describeAmbience,
  lanePointAt,
} from '../src/render/ambience.ts';
import { PHASE_ORDER } from '../src/render/day-phase.ts';

test('同一时刻永远同一组位置：确定性', () => {
  const a = ambienceAt(123_456, 'noon');
  const b = ambienceAt(123_456, 'noon');
  assert.deepEqual(a, b);
});

test('不同时刻位置不同：它们真的在动', () => {
  const a = ambienceAt(10_000, 'noon');
  const b = ambienceAt(10_500, 'noon');
  assert.equal(a.length, b.length);
  const moved = a.some((actor, i) => {
    const other = b[i]!;
    return actor.at.x !== other.at.x || actor.at.y !== other.at.y;
  });
  assert.ok(moved, '半秒之后位置应当变了');
});

test('鸟在白天飞，夜里没有', () => {
  for (const phase of ['dawn', 'noon'] as const) {
    const birds = ambienceAt(0, phase).filter((actor) => actor.kind === 'bird');
    assert.ok(birds.length > 0, `${phase} 应当有鸟`);
  }
  assert.equal(
    ambienceAt(0, 'night').filter((actor) => actor.kind === 'bird').length,
    0,
    '夜里不该有鸟',
  );
});

test('蝙蝠只在黄昏与夜里出现', () => {
  assert.equal(ambienceAt(0, 'noon').filter((a) => a.kind === 'bat').length, 0, '正午没有蝙蝠');
  assert.ok(ambienceAt(0, 'dusk').filter((a) => a.kind === 'bat').length > 0, '黄昏有蝙蝠');
  assert.ok(ambienceAt(0, 'night').filter((a) => a.kind === 'bat').length > 0, '夜里有蝙蝠');
});

test('每个时刻都有配方，没有遗漏', () => {
  for (const phase of PHASE_ORDER) {
    assert.ok(AMBIENCE_BY_PHASE[phase] !== undefined, `${phase} 缺配方`);
  }
});

test('数量不会超过轨道数：多配了会绕回复用同一条轨道', () => {
  for (const phase of PHASE_ORDER) {
    const recipe = AMBIENCE_BY_PHASE[phase];
    assert.ok(recipe.birds >= 0 && recipe.bats >= 0, `${phase} 数量不能为负`);
    // 4 条鸟道、2 条蝙蝠道是上限：再多就会有两只在同一条道上重叠。
    assert.ok(recipe.birds <= 4, `${phase} 的鸟最多 4 只（4 条道）`);
    assert.ok(recipe.bats <= 2, `${phase} 的蝙蝠最多 2 只（2 条道）`);
  }
});

test('朝向在端点翻转：来回飞而不是单程飞出去', () => {
  const first = lanePointAt('bird', 0, 0);
  const later = lanePointAt('bird', 0, 9000 * 0.7);
  assert.notEqual(
    first.facing,
    later.facing,
    '一个半周期之后应当朝反方向',
  );
});

test('活动范围有限：不会飞到世界之外', () => {
  // 世界 96×72 格。轨道最远 66 格出手，纵坐标都在 28–44。
  for (let t = 0; t < 60_000; t += 250) {
    for (const actor of ambienceAt(t, 'noon')) {
      assert.ok(actor.at.x > 0 && actor.at.x < 96, `x=${actor.at.x} 越界`);
      assert.ok(actor.at.y > 0 && actor.at.y < 72, `y=${actor.at.y} 越界`);
    }
  }
});

test('拍翅相位在 0..1，且会变化', () => {
  for (let t = 0; t < 2000; t += 70) {
    for (const actor of ambienceAt(t, 'noon')) {
      assert.ok(actor.flap >= 0 && actor.flap < 1, `拍翅相位 ${actor.flap} 越界`);
    }
  }
  const a = ambienceAt(0, 'noon')[0]!;
  const b = ambienceAt(200, 'noon')[0]!;
  assert.notEqual(a.flap, b.flap, '拍翅应当推进');
});

test('一行摘要说得清天上有几只', () => {
  assert.match(describeAmbience(0, 'noon'), /只鸟/);
  assert.match(describeAmbience(0, 'night'), /蝙蝠/);
});
