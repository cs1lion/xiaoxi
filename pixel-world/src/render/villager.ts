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
import { pixelAt, villagerPose, type VillagerPose } from './villager-frames.ts';

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
  const shadow = scene.add.graphics();
  shadow.fillStyle(WORLD.ink, 0.35);
  shadow.fillEllipse(0, 0, 14, 5);

  const body = scene.add.graphics();
  drawBody(body, 'down', villagerPose('idle', 0));

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
  drawBody(view.body, villager.facing, pose);
  if (villager.umbrella) drawUmbrella(view.body, pose.offsetY);

  const walking = villager.mode === 'toWork' || villager.mode === 'toAltar';
  view.shadow.setAlpha(walking ? 0.28 : 0.35);
}

/** 伞只在下雨且她在户外时出现；世界给的事实，渲染层不自己判断天气。 */
function drawUmbrella(g: Phaser.GameObjects.Graphics, offsetY: number): void {
  const y = offsetY;
  g.fillStyle(WORLD.timber, 1);
  g.fillRect(-7, -30 + y, 14, 3);
  g.fillStyle(WORLD.ember, 1);
  g.fillRect(-5, -32 + y, 10, 2);
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(0, -27 + y, 1, 8);
}

function drawBody(
  g: Phaser.GameObjects.Graphics,
  facing: VillagerSnapshot['facing'],
  pose: VillagerPose,
): void {
  const y = -VILLAGER_H + pose.offsetY;
  // 轮廓：头和斗篷各一圈 1px 最暗色，草地/雨天压暗时仍能读出剪影。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-5, y, 10, 10);
  g.fillRect(-6, y + 7, 12, 11);

  // 头
  g.fillStyle(WORLD.skin, 1);
  g.fillRect(-4, y + 3, 8, 6);
  // 发
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-4, y + 1, 8, 2);
  g.fillRect(-5, y + 1, 1, 6);
  g.fillRect(4, y + 1, 1, 6);

  // 斗篷：基色 + 下摆暗部（替代原来的斗篷亮部，亮部靠轮廓与围巾区分）
  g.fillStyle(WORLD.cloak, 1);
  g.fillRect(-5, y + 8, 10, 10);
  g.fillStyle(WORLD.cloakDark, 1);
  g.fillRect(-5, y + 16, 10, 2);

  // 围巾：2px 横贯到轮廓外，远景靠这条暖色认出她。
  g.fillStyle(WORLD.ember, 1);
  g.fillRect(-6, y + 8, 12, 2);

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

  // 靴子：走路的 4 帧就是这两只脚的前伸组合（整数像素）。
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-4 + pose.footLeft, y + 18, 3, 2);
  g.fillRect(1 + pose.footRight, y + 18, 3, 2);
}
