/** 小人层：16×32 的色块精灵，动作由状态机给，渲染层不猜。
 *
 * 动画采样在 `villager-frames.ts`（纯函数、可单测）：帧号由 simulation 时间决定，
 * 这里只按帧号画，并且所有坐标取整到整数像素。
 *
 * 16 色收敛后不再有“斗篷亮部”这种单独色：亮部靠 1px 暗轮廓 + 围巾/皮肤的面积撑起来。
 * 审计 §6.2：轮廓/草地只有 1.59:1，所以先补轮廓，再谈换色。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { VillagerSnapshot } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { isWalkingMode, pixelAt, villagerPose, type VillagerPose } from './villager-frames.ts';

export const VILLAGER_W = 12;
export const VILLAGER_H = 20;

export interface VillagerView {
  readonly container: Phaser.GameObjects.Container;
  readonly body: Phaser.GameObjects.Graphics;
  readonly shadow: Phaser.GameObjects.Graphics;
}

export function createVillager(
  scene: Phaser.Scene,
  originX: number,
  originY: number,
): VillagerView {
  // D6：接地不能只有一枚椭圆。椭圆说明「这里有团暗」，但脚和地面没有接触点，
  // 走起来会像浮着。这里用阶梯像素块代替矢量椭圆，再加一条 1px 接触线压在脚底。
  // alpha 纪律（ART_SPEC §2.4）：阴影只允许 ink，且不超过既有档位（0.55 是现值上限）。
  const shadow = scene.add.graphics();
  shadow.fillStyle(WORLD.ink, 0.28);
  shadow.fillRect(-6, 0, 12, 2);
  shadow.fillRect(-4, 2, 8, 1);
  shadow.fillStyle(WORLD.ink, 0.55);
  shadow.fillRect(-4, 0, 8, 1);

  const body = scene.add.graphics();
  drawBody(body, 'down', villagerPose('idle', 0), 'idle', false);

  // 名字/气泡已迁 DOM overlay（D5-A）：canvas 里不再画中文。
  const container = scene.add.container(originX, originY, [shadow, body]);
  return { container, body, shadow };
}

/** 每帧把快照画出来；坐标换算只在这里做一次，且结果取整到整数像素。 */
export function updateVillager(
  view: VillagerView,
  villager: VillagerSnapshot,
  nowMs: number,
  originX: number,
  originY: number,
  altitude: number,
): void {
  const px = pixelAt(originX + (villager.pos.x + 0.5) * TILE_PX);
  const py = pixelAt(originY + (villager.pos.y + 1) * TILE_PX);
  view.container.setPosition(px, py);
  view.container.setVisible(villager.visible);
  view.container.setDepth(altitude + py + 8);

  const pose = villagerPose(villager.mode, nowMs);
  view.body.clear();
  drawBody(
    view.body,
    villager.facing,
    pose,
    villager.mode,
    isWalkingMode(villager.mode),
  );
  if (villager.umbrella) drawUmbrella(view.body, pose.offsetY);

  // 走路时整条阴影（椭圆 + 接触线）一起变淡：抬脚时接触减弱，落地时才压深。
  // 用容器级 alpha 而不是改每个 fillStyle，保证椭圆与接触线的相对深浅不变。
  const walking = villager.mode === 'toWork' || villager.mode === 'toAltar';
  view.shadow.setAlpha(walking ? 0.28 : 0.35);
}

/** 伞只在下雨且她在户外时出现；世界给的事实，渲染层不自己判断天气。 */
function drawUmbrella(g: Phaser.GameObjects.Graphics, offsetY: number): void {
  const y = offsetY;
  // 伞面：中间高、两侧低，右缘留一个缺口；伞骨用暗木色。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-7, -31 + y, 14, 3);
  g.fillStyle(WORLD.ember, 1);
  g.fillRect(-6, -31 + y, 12, 2);
  g.fillRect(-4, -32 + y, 8, 1);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(-5, -32 + y, 4, 1);
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-5, -30 + y, 1, 2);
  g.fillRect(0, -30 + y, 1, 2);
  g.fillRect(4, -30 + y, 1, 2);
  g.fillRect(0, -28 + y, 1, 9);
  g.fillStyle(WORLD.plaza, 1);
  g.fillRect(-1, -20 + y, 3, 1);
}

function drawBody(
  g: Phaser.GameObjects.Graphics,
  facing: VillagerSnapshot['facing'],
  pose: VillagerPose,
  mode: VillagerSnapshot['mode'],
  walking: boolean,
): void {
  const y = -VILLAGER_H + pose.offsetY;
  const scarfLift = walking ? pose.frame % 2 : 0;

  // 轮廓：头/肩/斗篷各一圈 1px 最暗色。斗篷下摆比肩宽 2px，形成 A 字剪影。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-5, y, 10, 10);
  g.fillRect(-6, y + 7, 12, 11);

  // 头
  g.fillStyle(WORLD.skin, 1);
  g.fillRect(-4, y + 3, 8, 6);

  // 发：顶 2px + 两侧，另加马尾/后发。发色与靴子同源，保持 16 色。
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-4, y + 1, 8, 2);
  g.fillRect(-5, y + 1, 1, 6);
  g.fillRect(4, y + 1, 1, 6);
  if (facing === 'left') {
    g.fillRect(5, y + 4, 1, 4);
  } else if (facing === 'right') {
    g.fillRect(-6, y + 4, 1, 4);
  } else {
    g.fillRect(4, y + 7, 2, 4);
  }

  // 发丝高光：只给 2px，避免头变成亮块。
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(-3, y + 1, 3, 1);
  g.fillStyle(WORLD.pale, 1);
  g.fillRect(-4, y, 1, 1);

  // 眼
  g.fillStyle(WORLD.ink, 1);
  if (facing === 'down') {
    g.fillRect(-2, y + 5, 1, 1);
    g.fillRect(2, y + 5, 1, 1);
  } else if (facing === 'left') {
    g.fillRect(-3, y + 5, 1, 1);
  } else if (facing === 'right') {
    g.fillRect(2, y + 5, 1, 1);
  }
  // 嘴：1px，朝下时可见。
  if (facing === 'down') {
    g.fillStyle(WORLD.ember, 1);
    g.fillRect(0, y + 7, 1, 1);
  }

  // 斗篷：肩 8px、下摆 12px；右侧暗部，底边再压一档。
  g.fillStyle(WORLD.cloak, 1);
  g.fillRect(-4, y + 8, 8, 4);
  g.fillRect(-5, y + 12, 10, 4);
  g.fillRect(-6, y + 16, 12, 2);
  g.fillStyle(WORLD.cloakDark, 1);
  g.fillRect(3, y + 8, 1, 10);
  g.fillRect(-6, y + 16, 12, 2);
  g.fillRect(0, y + 9, 1, 7);

  // 围巾：2px 横贯 + 左侧飘尾；走路时尾端 1px 上下。
  g.fillStyle(WORLD.ember, 1);
  g.fillRect(-6, y + 7, 12, 2);
  g.fillRect(-8, y + 8 + scarfLift, 3, 1);
  g.fillRect(-7, y + 9 + scarfLift, 2, 1);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(-5, y + 7, 4, 1);

  // 侧包：打破矩形，给“出门办事”的读法。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(4, y + 12, 4, 5);
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(4, y + 13, 3, 3);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(4, y + 13, 3, 1);

  // 靴子：走路的 4 帧就是这两只脚的前伸组合（整数像素）。
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-4 + pose.footLeft, y + 18, 3, 2);
  g.fillRect(1 + pose.footRight, y + 18, 3, 2);
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-4 + pose.footLeft, y + 19, 3, 1);
  g.fillRect(1 + pose.footRight, y + 19, 3, 1);

  // 轮廓光：固定左上，1px trim/ pale；不是每一侧都描亮。
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(-6, y + 8, 1, 5);
  g.fillRect(-5, y + 2, 1, 3);
  g.fillStyle(WORLD.pale, 1);
  g.fillRect(-4, y + 1, 2, 1);

  drawHeldItem(g, y, facing, mode);
}

/** 手里拿的东西：工作=锤，交付=纸，行礼=抬手。都只占 2–4px。 */
function drawHeldItem(
  g: Phaser.GameObjects.Graphics,
  y: number,
  facing: VillagerSnapshot['facing'],
  mode: VillagerSnapshot['mode'],
): void {
  const side = facing === 'left' ? -1 : 1;
  if (mode === 'working') {
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(side * 5 - 1, y + 11, 2, 6);
    g.fillStyle(WORLD.plaza, 1);
    g.fillRect(side * 5 - 2, y + 10, 4, 2);
    g.fillStyle(WORLD.pale, 1);
    g.fillRect(side * 5 - 2, y + 10, 4, 1);
  } else if (mode === 'delivering') {
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(3 * side - (side < 0 ? 3 : 0), y + 12, 5, 5);
    g.fillStyle(WORLD.pale, 1);
    g.fillRect(4 * side - (side < 0 ? 3 : 0), y + 12, 3, 4);
    g.fillStyle(WORLD.soil, 1);
    g.fillRect(4 * side - (side < 0 ? 3 : 0), y + 14, 3, 1);
  } else if (mode === 'saluting') {
    g.fillStyle(WORLD.skin, 1);
    g.fillRect(4 * side - (side < 0 ? 2 : 0), y + 3, 2, 4);
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(5 * side - (side < 0 ? 2 : 0), y + 2, 1, 1);
  }
}
