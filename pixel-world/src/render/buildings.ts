/** 建筑层：每栋铺子一个容器，状态变化只改部件颜色与可见性。 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { PlacedBuilding, WorkstationSnapshot } from '../world/types.ts';
import { UI } from './palette.ts';

export interface BuildingView {
  readonly container: Phaser.GameObjects.Container;
  readonly chimney: Phaser.GameObjects.Graphics;
  readonly window: Phaser.GameObjects.Graphics;
  readonly progress: Phaser.GameObjects.Graphics;
  readonly nameLabel: Phaser.GameObjects.Text;
  /** 烟囱口的场景坐标，粒子发射器挂在这里。 */
  readonly chimneyWorld: Phaser.Math.Vector2;
  /** 屋顶上沿，Y-Sort 用。 */
  readonly baseY: number;
}

export function createBuilding(
  scene: Phaser.Scene,
  building: PlacedBuilding,
  originX: number,
  originY: number,
): BuildingView {
  const px = originX + building.origin.x * TILE_PX;
  const py = originY + building.origin.y * TILE_PX;
  const w = building.footprint.w * TILE_PX;
  const h = building.footprint.h * TILE_PX;
  const container = scene.add.container(px, py);

  const g = scene.add.graphics();
  const { body, roof, trim, window: windowColor } = building.palette;
  const roofH = Math.max(8, Math.round(h * 0.34));

  g.fillStyle(UI.shadow, 0.32);
  g.fillRect(2, h - 2, w - 2, 4);
  g.fillStyle(body, 1);
  g.fillRect(0, roofH, w, h - roofH);
  g.fillStyle(trim, 1);
  g.fillRect(0, roofH, w, 2);
  g.fillRect(0, h - 4, w, 4);

  drawRoof(g, building.silhouette, w, roofH, roof, trim);

  for (let x = 4; x < w - 4; x += 7) {
    g.fillStyle(roof, 0.6);
    g.fillRect(x, roofH + 4, 1, h - roofH - 8);
  }

  const chimney = scene.add.graphics();
  chimney.fillStyle(trim, 1);
  chimney.fillRect(w - 14, -6, 7, 10);
  chimney.fillStyle(0x1a1a1a, 1);
  chimney.fillRect(w - 13, -6, 5, 2);

  const windowG = scene.add.graphics();
  const doorX = building.door.x * TILE_PX - px;
  const doorY = building.door.y * TILE_PX - py;
  windowG.fillStyle(0x241f19, 1);
  windowG.fillRect(doorX - 4, doorY - 14, 10, 14);
  windowG.fillStyle(trim, 1);
  windowG.fillRect(doorX - 4, doorY - 14, 1, 14);
  windowG.fillRect(doorX + 5, doorY - 14, 1, 14);
  windowG.fillStyle(windowColor, 0.85);
  windowG.fillRect(6, roofH + 8, 8, 6);
  windowG.fillRect(w - 18, roofH + 8, 8, 6);
  windowG.fillStyle(UI.shadow, 0.4);
  windowG.fillRect(10, roofH + 8, 1, 6);
  windowG.fillRect(w - 14, roofH + 8, 1, 6);

  const progress = scene.add.graphics();
  progress.setVisible(false);

  const nameLabel = scene.add.text(w / 2, -18, building.displayName, {
    fontFamily: 'ui-monospace, monospace',
    fontSize: '11px',
    color: '#d9d4c5',
  });
  nameLabel.setOrigin(0.5, 1);

  container.add([g, chimney, windowG, progress, nameLabel]);

  return {
    container,
    chimney,
    window: windowG,
    progress,
    nameLabel,
    chimneyWorld: new Phaser.Math.Vector2(px + w - 10, py - 2),
    baseY: py + h,
  };
}

/** 三种剪影：尖顶、塔楼、平顶。远看就能分出是哪一栋。 */
function drawRoof(
  g: Phaser.GameObjects.Graphics,
  silhouette: PlacedBuilding['silhouette'],
  w: number,
  roofH: number,
  roof: number,
  trim: number,
): void {
  if (silhouette === 'gable') {
    g.fillStyle(roof, 1);
    // 一阶一阶往上收，尖顶落在顶边中点。
    const rows = Math.max(3, Math.round(roofH / 3));
    for (let row = 0; row < rows; row += 1) {
      const inset = Math.round(((rows - 1 - row) / rows) * (w / 2));
      g.fillRect(-3 + inset, row * (roofH / rows), w + 6 - inset * 2, roofH / rows + 1);
    }
    g.fillStyle(trim, 1);
    g.fillRect(w / 2 - 1, 0, 2, roofH);
  } else if (silhouette === 'tower') {
    g.fillStyle(roof, 1);
    g.fillRect(-3, roofH * 0.45, w + 6, roofH * 0.55);
    g.fillRect(w * 0.3, 0, w * 0.4, roofH);
    g.fillStyle(trim, 1);
    g.fillRect(w * 0.3 - 1, roofH * 0.45, w * 0.4 + 2, 2);
    g.fillRect(w * 0.5 - 1, -4, 2, 5);
  } else {
    g.fillStyle(roof, 1);
    g.fillRect(-3, 0, w + 6, roofH);
    g.fillStyle(trim, 1);
    for (let x = 1; x < w + 4; x += 4) g.fillRect(x - 3, 2, 2, roofH - 4);
    g.fillRect(-3, roofH - 2, w + 6, 2);
  }
}

/** 干活时窗户亮起、门口挂上脉动的进度条；冷却时灯仍亮一小会儿。 */
export function updateBuilding(
  scene: Phaser.Scene,
  view: BuildingView,
  building: PlacedBuilding,
  workstation: WorkstationSnapshot | undefined,
  altitude: number,
): void {
  const activity = workstation?.activity ?? { kind: 'idle' as const };
  const working = activity.kind === 'operating';
  const cooling = activity.kind === 'cooling';

  view.container.setDepth(altitude + view.baseY);
  view.nameLabel.setAlpha(working ? 1 : 0.72);
  view.chimney.setAlpha(working ? 1 : 0);

  view.progress.clear();
  view.progress.setVisible(working || cooling);
  const width = building.footprint.w * TILE_PX - 8;
  if (working) {
    const filled = Math.round((width - 2) * (workstation?.progress ?? 0));
    view.progress.fillStyle(UI.ink, 0.8);
    view.progress.fillRect(4, -8, width, 3);
    view.progress.fillStyle(UI.amber, 0.9);
    view.progress.fillRect(5, -7, Math.max(1, filled), 1);
    // 进度线在走之外再加一层脉动，远处也能看出这台机器还活着。
    view.progress.fillStyle(UI.ember, 0.35 + 0.35 * Math.sin(scene.time.now / 240));
    view.progress.fillRect(5, -7, Math.max(1, filled), 1);
  } else if (cooling) {
    view.progress.fillStyle(UI.ember, 0.8);
    view.progress.fillRect(4, -8, width, 3);
  }
}
