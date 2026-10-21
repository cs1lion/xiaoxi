import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TILE_PX } from '../src/world/setup.ts';
import {
  ACTOR_DEPTH_OFFSET,
  BUILDING_DEPTH,
  FRONT_OFFSET_MAX_EXCLUSIVE,
  FRONT_OFFSET_MIN_EXCLUSIVE,
  actorDepthAt,
  buildingBaseY,
  layerDepth,
} from '../src/render/building-depth.ts';

/** P0-3 建筑三层深度的回归锁。
 *
 * QA 指出 `7eade0a` 当时那 15 项断言是临时的、没入库，等于**遮挡行为无回归保护**。
 * 这里把算术锁住：三层顺序、前檐与角色的相对关系、以及「只有站门口那格被压」。
 */

const ALT = 1000;

/** 三栋常驻铺子（prefabs.json 的 footprint.h；门口恒在占地底边下一行）。 */
const BUILDINGS = [
  { id: 'code_forge', footprintH: 4, originY: 5 },
  { id: 'star_archive', footprintH: 4, originY: 5 },
  { id: 'ink_house', footprintH: 3, originY: 13 },
];

test('三层偏移：back < body < front，且 back 在楼体之下', () => {
  assert.ok(BUILDING_DEPTH.back < BUILDING_DEPTH.body, 'back 必须压在 body 之下');
  assert.ok(BUILDING_DEPTH.body < BUILDING_DEPTH.front, 'front 必须在 body 之上');
  assert.equal(BUILDING_DEPTH.body, 0, 'body 沿用改造前口径，不能漂移');
});

test('角色 depth 偏移与 villager.ts 一致（+8）', () => {
  assert.equal(ACTOR_DEPTH_OFFSET, 8, 'villager.ts:55 是 ALTITUDE + py + 8，改一处要改两处');
  assert.equal(actorDepthAt(9, ALT), ALT + (9 + 1) * TILE_PX + 8);
});

test('落地线 baseY = originY + footprintH * TILE_PX', () => {
  for (const b of BUILDINGS) {
    // buildingBaseY 收的是**像素** originY，所以先把格坐标换算成像素再喂进去。
    assert.equal(
      buildingBaseY(b.originY * TILE_PX, b.footprintH),
      (b.originY + b.footprintH) * TILE_PX,
    );
  }
});

test('前檐压住站门口的人：front 落在 (baseY+24, baseY+40) 开区间内', () => {
  for (const b of BUILDINGS) {
    const baseY = buildingBaseY(b.originY * TILE_PX, b.footprintH);
    const doorY = b.originY + b.footprintH; // 门口格 = 占地底边下一行
    const front = layerDepth('front', baseY, ALT);
    const atDoor = actorDepthAt(doorY, ALT);
    const nextRow = actorDepthAt(doorY + 1, ALT);
    assert.ok(front > atDoor, `${b.id}: 前檐必须压住站门口的角色（front=${front} 须 > 角色=${atDoor}）`);
    assert.ok(front < nextRow, `${b.id}: 前檐不能压到门口下 1 格的角色（front=${front} 须 < 角色=${nextRow}）`);
    // layerDepth 返回 ALT + baseY + 偏移，所以相对 baseY 的偏移要减掉 ALT
    assert.equal(front - baseY - ALT, BUILDING_DEPTH.front, `${b.id}: front 偏移必须等于常量`);
  }
});

test('区间常量与实际取值自洽（改常量时这条会红）', () => {
  assert.ok(BUILDING_DEPTH.front > FRONT_OFFSET_MIN_EXCLUSIVE, 'front 必须大于站门口角色的偏移 24');
  assert.ok(BUILDING_DEPTH.front < FRONT_OFFSET_MAX_EXCLUSIVE, 'front 必须小于门口下 1 格角色的偏移 40');
  assert.equal(BUILDING_DEPTH.front, 32);
});

test('文档给的 +12 压不住人（锁住这个历史结论，防止被改回去）', () => {
  const b = BUILDINGS[0]!;
  const baseY = buildingBaseY(b.originY * TILE_PX, b.footprintH);
  const atDoor = actorDepthAt(b.originY + b.footprintH, ALT);
  const specValue = ALT + baseY + 12;
  assert.ok(specValue < atDoor, '+12 落在人后面，这正是当初改成 32 的理由');
});

test('楼体低于站门口的角色（人站在楼前）', () => {
  for (const b of BUILDINGS) {
    const baseY = buildingBaseY(b.originY * TILE_PX, b.footprintH);
    const doorY = b.originY + b.footprintH;
    assert.ok(
      layerDepth('body', baseY, ALT) < actorDepthAt(doorY, ALT),
      `${b.id}: 楼体必须低于站在门口的人`,
    );
    assert.ok(layerDepth('back', baseY, ALT) < layerDepth('body', baseY, ALT) - 2 + 2);
  }
});

test('地面层永远最低：back 低于楼体与所有可见站位', () => {
  for (const b of BUILDINGS) {
    const baseY = buildingBaseY(b.originY * TILE_PX, b.footprintH);
    const back = layerDepth('back', baseY, ALT);
    assert.ok(back < layerDepth('body', baseY, ALT), `${b.id}: 投影必须压在楼体之下`);
    // 可见站位从门口格起算：楼内（gy < door.y）角色 working 态 visible=false，不参与排序。
    for (let gy = b.originY + b.footprintH; gy <= b.originY + b.footprintH + 3; gy += 1) {
      assert.ok(back < actorDepthAt(gy, ALT), `${b.id}: 角色在 gy=${gy} 时应在投影之上`);
    }
  }
});
