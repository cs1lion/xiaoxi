/** 世界的装饰：铺好的小径、信仰落地的摆件、施工脚手架。
 *
 * 这些都不参与碰撞，也不改快照；它们只是把快照里已有的事实画出来。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { ConstructionSnapshot, GridPoint, WorldProp } from '../world/types.ts';

export interface DecorView {
  readonly roads: Phaser.GameObjects.Graphics;
  readonly props: Phaser.GameObjects.Container;
  readonly scaffolds: Map<string, Phaser.GameObjects.Container>;
  readonly redrawRoads: (cells: readonly GridPoint[]) => void;
  readonly syncProps: (props: readonly WorldProp[]) => void;
  readonly syncScaffolds: (constructions: readonly ConstructionSnapshot[]) => void;
}

export function createDecorView(scene: Phaser.Scene, originX: number, originY: number): DecorView {
  const roads = scene.add.graphics();
  roads.setDepth(1);
  const props = scene.add.container(0, 0);
  const scaffolds = new Map<string, Phaser.GameObjects.Container>();
  let propCount = 0;

  return {
    roads,
    props,
    scaffolds,
    redrawRoads(cells) {
      roads.clear();
      for (const cell of cells) {
        const px = originX + cell.x * TILE_PX;
        const py = originY + cell.y * TILE_PX;
        roads.fillStyle(0x4b4f48, 0.85);
        roads.fillRect(px + 2, py + 2, TILE_PX - 4, TILE_PX - 4);
        roads.fillStyle(0x5c6158, 0.55);
        roads.fillRect(px + 3, py + 3, TILE_PX - 6, 2);
      }
    },
    syncProps(list) {
      if (list.length === propCount) return;
      propCount = list.length;
      props.removeAll(true);
      for (const prop of list) {
        drawProp(scene, props, prop, originX, originY);
      }
    },
    syncScaffolds(constructions) {
      const active = new Set(constructions.map((item) => item.buildingId));
      for (const [id, container] of [...scaffolds.entries()]) {
        if (!active.has(id)) {
          container.destroy(true);
          scaffolds.delete(id);
        }
      }
      for (const construction of constructions) {
        if (scaffolds.has(construction.buildingId)) continue;
        const container = scene.add.container(0, 0);
        container.setDepth(6500);
        scaffolds.set(construction.buildingId, container);
      }
    },
  };
}

function drawProp(
  scene: Phaser.Scene,
  parent: Phaser.GameObjects.Container,
  prop: WorldProp,
  originX: number,
  originY: number,
): void {
  const px = originX + (prop.at.x + 0.5) * TILE_PX;
  const py = originY + (prop.at.y + 0.8) * TILE_PX;
  const g = scene.add.graphics();
  if (prop.kind === 'lamp') {
    g.fillStyle(0x2b2b28, 1);
    g.fillRect(-1, -12, 2, 12);
    g.fillStyle(0xf0cf7a, 0.95);
    g.fillRect(-3, -16, 6, 4);
    g.fillStyle(0xf0cf7a, 0.18);
    g.fillCircle(0, -14, 9);
  } else if (prop.kind === 'tree') {
    g.fillStyle(0x4a3a28, 1);
    g.fillRect(-1, -8, 2, 8);
    g.fillStyle(0x3c5f3c, 1);
    g.fillCircle(0, -11, 6);
    g.fillStyle(0x4c744a, 1);
    g.fillCircle(-2, -13, 4);
  } else {
    g.fillStyle(0x2b2b28, 1);
    g.fillRect(-4, -16, 1, 16);
    g.fillStyle(0xb4553f, 0.95);
    g.fillRect(-3, -15, 8, 6);
    g.fillStyle(0xd9c9a6, 0.8);
    g.fillRect(-3, -15, 8, 1);
  }
  g.setPosition(px, py);
  parent.add(g);
  // 摆件标签已随 D5-A 删除：图形自己说明是什么，中文不进 canvas。
}
