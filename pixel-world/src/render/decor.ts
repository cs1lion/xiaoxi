/** 世界的装饰：铺好的小径、信仰落地的摆件、施工脚手架。
 *
 * 这些都不参与碰撞，也不改快照；它们只是把快照里已有的事实画出来。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { ConstructionSnapshot, GridPoint, WorldProp } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';

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
      const at = new Set(cells.map((cell) => cell.x + ',' + cell.y));
      const isRoad = (x: number, y: number): boolean => at.has(x + ',' + y);
      for (const cell of cells) {
        const px = originX + cell.x * TILE_PX;
        const py = originY + cell.y * TILE_PX;
        // §1.4：小径由「内缩矩形」改三段 —— 1px soil 描边 + plaza 面 + 上缘 1px trim 高光。
        // 描边按「这一侧有没有相邻的径」逐边判定：相邻那一侧不描边，多格小径才会连成一条
        // 路，而不是一串各自带框的方块。
        roads.fillStyle(WORLD.plaza, 1);
        roads.fillRect(px + 1, py + 1, TILE_PX - 2, TILE_PX - 2);
        roads.fillStyle(WORLD.soil, 0.85);
        if (!isRoad(cell.x, cell.y - 1)) roads.fillRect(px + 1, py + 1, TILE_PX - 2, 1);
        if (!isRoad(cell.x, cell.y + 1)) roads.fillRect(px + 1, py + TILE_PX - 2, TILE_PX - 2, 1);
        if (!isRoad(cell.x - 1, cell.y)) roads.fillRect(px + 1, py + 1, 1, TILE_PX - 2);
        if (!isRoad(cell.x + 1, cell.y)) roads.fillRect(px + TILE_PX - 2, py + 1, 1, TILE_PX - 2);
        // 上缘高光：光从左上打，受光面在顶边。用 trim 让路面「亮起来」，与草地分开。
        roads.fillStyle(WORLD.trim, 0.55);
        if (!isRoad(cell.x, cell.y - 1)) roads.fillRect(px + 2, py + 2, TILE_PX - 4, 1);
        // 石材板缝：路面内部 8px 一道，只用 stone，读成「铺过的路」而不是一块毡。
        roads.fillStyle(WORLD.stone, 0.6);
        roads.fillRect(px + 1, py + TILE_PX / 2, TILE_PX - 2, 1);
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
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-1, -12, 2, 12);
    g.fillStyle(WORLD.amber, 0.95);
    g.fillRect(-3, -16, 6, 4);
    g.fillStyle(WORLD.amber, 0.18);
    g.fillCircle(0, -14, 9);
  } else if (prop.kind === 'tree') {
    g.fillStyle(WORLD.timberDark, 1);
    g.fillRect(-1, -8, 2, 8);
    g.fillStyle(WORLD.grass, 1);
    g.fillCircle(0, -11, 6);
    g.fillStyle(WORLD.grassLit, 1);
    g.fillCircle(-2, -13, 4);
  } else {
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-4, -16, 1, 16);
    g.fillStyle(WORLD.ember, 0.95);
    g.fillRect(-3, -15, 8, 6);
    g.fillStyle(WORLD.trim, 0.8);
    g.fillRect(-3, -15, 8, 1);
  }
  g.setPosition(px, py);
  parent.add(g);
  // 摆件标签已随 D5-A 删除：图形自己说明是什么，中文不进 canvas。
}
