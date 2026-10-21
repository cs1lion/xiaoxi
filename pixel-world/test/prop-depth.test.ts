import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TILE_PX } from '../src/world/setup.ts';
import { ACTOR_DEPTH_OFFSET, propDepth, propFootY } from '../src/render/prop-depth.ts';
import type { WorldProp } from '../src/world/types.ts';

/** §1.5 D5：道具必须有 depth，且与角色同一把尺子，否则 Y-Sort 无效。 */

const ALT = 1000;
const prop = (y: number): WorldProp => ({
  id: 'p',
  kind: 'tree',
  at: { x: 3, y },
  label: '树',
  sourceTaskKind: null,
  sourceTaskId: null,
  sourceSummary: null,
});

/** 角色 depth：villager.ts 的 `altitude + py + 8`，py = (pos.y + 1) * TILE_PX。 */
const villagerDepth = (posY: number): number => ALT + (posY + 1) * TILE_PX + 8;

test('道具 depth 与角色同口径：偏移量就是角色的 +8', () => {
  assert.equal(ACTOR_DEPTH_OFFSET, 8, '角色 depth 偏移变了就要同步改这里');
  for (const y of [0, 5, 12, 19]) {
    assert.equal(propDepth(prop(y), 0, ALT), ALT + propFootY(prop(y), 0) + 8);
  }
});

test('道具落点用 (at.y + 0.8)：与 drawProp 的 setPosition 同源', () => {
  for (const y of [0, 3, 17]) {
    assert.equal(propFootY(prop(y), 0), (y + 0.8) * TILE_PX);
  }
});

test('角色走到道具上一格时被道具遮住（走到树后面）', () => {
  for (const atY of [2, 3, 5, 8, 12, 17]) {
    assert.ok(
      propDepth(prop(atY), 0, ALT) > villagerDepth(atY - 1),
      `at.y=${atY} 时道具应压在上一格的角色前面`,
    );
  }
});

test('角色站在道具同格或下一格时在道具前面（不在树里）', () => {
  for (const atY of [2, 3, 5, 8, 12, 17]) {
    assert.ok(propDepth(prop(atY), 0, ALT) < villagerDepth(atY), `同格：角色应在道具前`);
    assert.ok(propDepth(prop(atY), 0, ALT) < villagerDepth(atY + 1), `下一格：角色应在道具前`);
  }
});

test('depth 随 y 单调递增：不存在「越往下越靠后」的翻转', () => {
  let previous = Number.NEGATIVE_INFINITY;
  for (let y = 0; y < 20; y += 1) {
    const depth = propDepth(prop(y), 0, ALT);
    assert.ok(depth > previous, `y=${y} 的 depth 必须大于 y=${y - 1}`);
    previous = depth;
  }
});
