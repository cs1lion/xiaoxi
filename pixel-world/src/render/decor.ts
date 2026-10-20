/** 世界的装饰：铺好的小径、信仰落地的摆件、施工脚手架。
 *
 * 这些都不参与碰撞，也不改快照；它们只是把快照里已有的事实画出来。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { ConstructionSnapshot, GridPoint, WorldProp } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { propDepth, propFootY } from './prop-depth.ts';

/** 场景把 ALTITUDE 传给装饰层，道具 depth 与角色用同一把尺子。 */
export const DECOR_ALTITUDE = 1000;

export interface DecorView {
  readonly roads: Phaser.GameObjects.Graphics;
  readonly scaffolds: Map<string, Phaser.GameObjects.Container>;
  readonly redrawRoads: (cells: readonly GridPoint[]) => void;
  readonly syncProps: (props: readonly WorldProp[]) => void;
  readonly syncScaffolds: (constructions: readonly ConstructionSnapshot[]) => void;
}

export function createDecorView(scene: Phaser.Scene, originX: number, originY: number): DecorView {
  const roads = scene.add.graphics();
  roads.setDepth(1);
  // 道具**不能**放进容器：Phaser 的 Container 按 list 顺序渲染子对象，容器内子对象的
  // depth 不参与全局排序（见 ContainerWebGLRenderer：直接遍历 container.list）。
  // 所以每个道具都是独立顶层对象，各自 setDepth，才能与角色做 Y-Sort。
  const props = new Map<string, Phaser.GameObjects.Graphics>();
  const scaffolds = new Map<string, Phaser.GameObjects.Container>();

  return {
    roads,
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
      // 按 id 增量对齐：摆件只增不删，但存档可用任意顺序重建，不能假设长度即内容。
      const alive = new Set(list.map((prop) => prop.id));
      for (const [id, node] of [...props.entries()]) {
        if (alive.has(id)) continue;
        node.destroy();
        props.delete(id);
      }
      for (const prop of list) {
        const existing = props.get(prop.id);
        if (existing !== undefined) {
          // 只在落地位置真的变了时改 depth，避免每帧无意义写入。
          const depth = propDepth(prop, originY, DECOR_ALTITUDE);
          if (existing.depth !== depth) existing.setDepth(depth);
          continue;
        }
        props.set(prop.id, drawProp(scene, prop, originX, originY));
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

/**
 * 画一个摆件，返回它自己（顶层对象，**不**挂容器）。
 *
 * 为什么必须是顶层对象：Phaser 的 Container 渲染时直接按 list 顺序遍历子对象，
 * 子对象的 depth 不参与全局深度排序。放进容器等于所有摆件共用一个 depth，
 * 角色永远压在它们前面（这就是 §1.5 说的 D5「道具没有深度」）。
 *
 * 落点 y 与 depth 都由 prop-depth.ts 算，保证与 drawProp 里的 setPosition 同源。
 */
function drawProp(
  scene: Phaser.Scene,
  prop: WorldProp,
  originX: number,
  originY: number,
): Phaser.GameObjects.Graphics {
  const px = originX + (prop.at.x + 0.5) * TILE_PX;
  const py = propFootY(prop, originY);
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
  // 与角色同口径的 Y-Sort：落点 y 越大（越靠下）越靠前，角色能走到树后面/前面。
  g.setDepth(propDepth(prop, originY, DECOR_ALTITUDE));
  // 摆件标签已随 D5-A 删除：图形自己说明是什么，中文不进 canvas。
  return g;
}
