/** 小人层：16×32 的色块精灵，动作由状态机给，渲染层不猜。 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { VillagerSnapshot } from '../world/types.ts';
import { UI } from './palette.ts';

export const VILLAGER_W = 12;
export const VILLAGER_H = 20;

export interface VillagerView {
  readonly container: Phaser.GameObjects.Container;
  readonly body: Phaser.GameObjects.Graphics;
  readonly shadow: Phaser.GameObjects.Graphics;
  readonly bubble: Phaser.GameObjects.Text;
  readonly name: Phaser.GameObjects.Text;
}

export function createVillager(
  scene: Phaser.Scene,
  name: string,
  originX: number,
  originY: number,
): VillagerView {
  const shadow = scene.add.graphics();
  shadow.fillStyle(UI.shadow, 0.35);
  shadow.fillEllipse(0, 0, 14, 5);

  const body = scene.add.graphics();
  drawBody(body, 'down', 0);

  const nameText = scene.add.text(0, -30, name, {
    fontFamily: 'ui-monospace, monospace',
    fontSize: '10px',
    color: '#f3dca8',
  });
  nameText.setOrigin(0.5, 1);

  const bubble = scene.add.text(0, -36, '', {
    fontFamily: 'ui-monospace, monospace',
    fontSize: '11px',
    color: '#14110d',
    backgroundColor: '#e8e2d2',
    padding: { x: 4, y: 2 },
  });
  bubble.setOrigin(0.5, 1);
  bubble.setVisible(false);

  const container = scene.add.container(originX, originY, [shadow, body, nameText, bubble]);
  return { container, body, shadow, bubble, name: nameText };
}

/** 每帧把快照画出来；坐标换算只在这里做一次。 */
export function updateVillager(
  view: VillagerView,
  villager: VillagerSnapshot,
  originX: number,
  originY: number,
  altitude: number,
): void {
  const px = originX + (villager.pos.x + 0.5) * TILE_PX;
  const py = originY + (villager.pos.y + 1) * TILE_PX;
  view.container.setPosition(px, py);
  view.container.setVisible(villager.visible);
  view.container.setDepth(altitude + py + 8);

  view.body.clear();
  const bob = villager.mode === 'saluting' ? -2 : 0;
  const hop = villager.mode === 'delivering' ? -1 : 0;
  drawBody(view.body, villager.facing, bob + hop);

  view.bubble.setVisible(villager.bubble !== null);
  if (villager.bubble !== null) view.bubble.setText(villager.bubble);
  // 气泡出现时让出位置，名字压在建筑招牌上会读不清。
  view.name.setVisible(villager.bubble === null);

  const walking = villager.mode === 'toWork' || villager.mode === 'toAltar';
  view.shadow.setAlpha(walking ? 0.28 : 0.35);
}

function drawBody(
  g: Phaser.GameObjects.Graphics,
  facing: VillagerSnapshot['facing'],
  offsetY: number,
): void {
  const y = -VILLAGER_H + offsetY;
  // 斗篷
  g.fillStyle(0x30506b, 1);
  g.fillRect(-5, y + 8, 10, 10);
  g.fillStyle(0x3f6584, 1);
  g.fillRect(-5, y + 8, 10, 2);
  // 底边
  g.fillStyle(0x223a4d, 1);
  g.fillRect(-5, y + 16, 10, 2);
  // 头
  g.fillStyle(0xe0b48c, 1);
  g.fillRect(-4, y + 1, 8, 8);
  g.fillStyle(0x2b2320, 1);
  g.fillRect(-4, y, 8, 3);
  g.fillRect(-6, y + 2, 2, 5);
  g.fillRect(4, y + 2, 2, 5);
  // 围巾
  g.fillStyle(0xc06a3e, 1);
  g.fillRect(-5, y + 8, 10, 2);
  // 眼
  g.fillStyle(UI.ink, 1);
  if (facing === 'down') {
    g.fillRect(-2, y + 5, 1, 1);
    g.fillRect(2, y + 5, 1, 1);
  } else if (facing === 'left') {
    g.fillRect(-3, y + 5, 1, 1);
  } else if (facing === 'right') {
    g.fillRect(2, y + 5, 1, 1);
  }
  // 脚
  g.fillStyle(0x1d1a17, 1);
  g.fillRect(-4, y + 18, 3, 2);
  g.fillRect(1, y + 18, 3, 2);
}
