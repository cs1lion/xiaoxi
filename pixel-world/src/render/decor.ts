/** 世界的装饰：铺好的小径、信仰落地的摆件、施工脚手架。
 *
 * 这些都不参与碰撞，也不改快照；它们只是把快照里已有的事实画出来。
 */

import Phaser from 'phaser';
import { TILE_PX } from '../world/setup.ts';
import type { ConstructionSnapshot, Critter, GridPoint, TaskKind, WorldProp } from '../world/types.ts';
import { WORLD } from '../world/palette-data.ts';
import { ACTOR_DEPTH_OFFSET, propDepth, propFootY } from './prop-depth.ts';

/** 场景把 ALTITUDE 传给装饰层，道具 depth 与角色用同一把尺子。 */
export const DECOR_ALTITUDE = 1000;

export interface DecorView {
  readonly roads: Phaser.GameObjects.Graphics;
  readonly scaffolds: Map<string, Phaser.GameObjects.Container>;
  readonly redrawRoads: (cells: readonly GridPoint[]) => void;
  /** 走出来的痕迹：一层比路更淡的踩踏。 */
  readonly syncTrails: (trails: readonly { readonly at: GridPoint; readonly strength: number }[]) => void;
  readonly syncProps: (props: readonly WorldProp[]) => void;
  /** 氛围实体（猫）。只做画面，不参与碰撞与结算。 */
  readonly syncCritters: (critters: readonly Critter[]) => void;
  readonly syncScaffolds: (constructions: readonly ConstructionSnapshot[]) => void;
}

export function createDecorView(scene: Phaser.Scene, originX: number, originY: number): DecorView {
  const roads = scene.add.graphics();
  roads.setDepth(1);
  // 道具**不能**放进容器：Phaser 的 Container 按 list 顺序渲染子对象，容器内子对象的
  // depth 不参与全局排序（见 ContainerWebGLRenderer：直接遍历 container.list）。
  // 所以每个道具都是独立顶层对象，各自 setDepth，才能与角色做 Y-Sort。
  const props = new Map<string, Phaser.GameObjects.Graphics>();
  const critters = new Map<string, Phaser.GameObjects.Graphics>();
  // 痕迹单独一层，压在路下面：踩出来的东西该比铺出来的更轻。
  const trails = scene.add.graphics();
  trails.setDepth(0);
  let lastTrailKey = '';
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
        // 小径：1px soil 软边 + plaza 面 + 不规则石板。描边按「这一侧有没有相邻的径」
        // 逐边判定，多格小径才会连成一条路，而不是一串各自带框的方块。
        roads.fillStyle(WORLD.plaza, 1);
        roads.fillRect(px + 1, py + 1, TILE_PX - 2, TILE_PX - 2);
        roads.fillStyle(WORLD.soil, 0.85);
        if (!isRoad(cell.x, cell.y - 1)) roads.fillRect(px + 1, py + 1, TILE_PX - 2, 1);
        if (!isRoad(cell.x, cell.y + 1)) roads.fillRect(px + 1, py + TILE_PX - 2, TILE_PX - 2, 1);
        if (!isRoad(cell.x - 1, cell.y)) roads.fillRect(px + 1, py + 1, 1, TILE_PX - 2);
        if (!isRoad(cell.x + 1, cell.y)) roads.fillRect(px + TILE_PX - 2, py + 1, 1, TILE_PX - 2);

        // 路面不再是 8px 通栏板缝：3 块大小不一的板岩，缝用 stone（对 plaza ΔL* 11.35）。
        // 每格 hash 稳定，同种子截图可复现；不跨格画，避免覆盖邻格。
        const h = Math.imul(cell.x + 17, 374761393) + Math.imul(cell.y + 31, 668265263);
        const seed = (h ^ (h >>> 13)) >>> 0;
        const slabs: readonly (readonly [number, number, number, number])[] = [
          [1, 1, 7, 6],
          [9, 1, 6, 7],
          [1, 9, 6, 6],
          [8, 9, 7, 6],
        ];
        for (let i = 0; i < slabs.length; i += 1) {
          if ((seed + i * 5) % 7 === 0) continue;
          const [sx, sy, sw, sh] = slabs[i]!;
          roads.fillStyle(WORLD.plaza, 1);
          roads.fillRect(px + sx, py + sy, sw, sh);
          roads.fillStyle(WORLD.stone, 1);
          roads.fillRect(px + sx, py + sy + sh - 1, sw, 1);
          roads.fillRect(px + sx + sw - 1, py + sy, 1, sh);
          if ((seed + i) % 4 === 0) {
            roads.fillStyle(WORLD.grass, 1);
            roads.fillRect(px + sx + 1, py + sy + sh - 2, 2, 1);
          }
        }
      }
    },
    syncTrails(list) {
      // 痕迹增长很慢，但每帧重画几百格也不贵。用 key 挡住无变化的帧。
      const key = list.map((t) => `${t.at.x},${t.at.y}:${t.strength}`).join('|');
      if (key === lastTrailKey) return;
      lastTrailKey = key;
      trails.clear();
      for (const trail of list) {
        const px = originX + trail.at.x * TILE_PX;
        const py = originY + trail.at.y * TILE_PX;
        // 强度 2：草被踩倒一条；强度 3：露出土色。
        // 用 soil（W02）而不是新增色——它是现成的"草下的土"。
        trails.fillStyle(WORLD.soil, trail.strength >= 3 ? 0.5 : 0.28);
        trails.fillRect(px + 3, py + 11, TILE_PX - 6, 2);
        if (trail.strength >= 3) {
          trails.fillStyle(WORLD.soil, 0.32);
          trails.fillRect(px + 5, py + 8, TILE_PX - 10, 2);
        }
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
    syncCritters(list) {
      const alive = new Set(list.map((critter) => critter.id));
      for (const [id, node] of [...critters.entries()]) {
        if (alive.has(id)) continue;
        node.destroy();
        critters.delete(id);
      }
      for (const critter of list) {
        if (critters.has(critter.id)) continue;
        const g = scene.add.graphics();
        drawCat(g);
        const px = originX + (critter.at.x + 0.5) * TILE_PX;
        const py = originY + (critter.at.y + 0.8) * TILE_PX;
        g.setPosition(px, py);
        // 与角色同一把尺子：猫也参与 Y-Sort，走到角色后面会被挡住。
        g.setDepth(DECOR_ALTITUDE + py + ACTOR_DEPTH_OFFSET);
        critters.set(critter.id, g);
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
  const seed = propHash(prop.id);
  // 外观由「换来它的那种活」决定，其次才是摆件大类：
  // 写代码的灯带齿轮，查资料的树带书，整理文稿的旗带砚台。
  // 老档的摆件没有来源，退回原来的三种画法。
  const accent = accentFor(prop.sourceTaskKind);
  if (prop.kind === 'lamp') {
    drawLamp(g, seed, accent);
  } else if (prop.kind === 'tree') {
    drawTree(g, seed, accent);
  } else {
    drawBanner(g, seed, accent);
  }
  g.setPosition(px, py);
  // 与角色同口径的 Y-Sort：落点 y 越大（越靠下）越靠前，角色能走到树后面/前面。
  g.setDepth(propDepth(prop, originY, DECOR_ALTITUDE));
  // 摆件标签已随 D5-A 删除：图形自己说明是什么，中文不进 canvas。
  return g;
}

/** 摆件上的来源记号。`null` 表示老档摆件，不加记号。 */
type PropAccent = 'gear' | 'book' | 'inkstone' | null;

function accentFor(kind: TaskKind | null): PropAccent {
  if (kind === 'code') return 'gear';
  if (kind === 'research') return 'book';
  if (kind === 'writing') return 'inkstone';
  return null;
}

/**
 * 猫：4×3 的小剪影，只求「有只猫」可读，不求细节。
 * 用 24 色里的 charcoal 做主体、amber 点眼睛，和摆件区分得开。
 */
function drawCat(g: Phaser.GameObjects.Graphics): void {
  // 9×11 的小剪影。早先的 6×7 在祭坛旁读不出来，所以放大一档并加黑轮廓：
  // 24 色里没有专门的"深色描边"，用 ink 就是它。
  g.fillStyle(WORLD.deepShade, 0.3);
  g.fillRect(-5, 0, 11, 2);

  // 轮廓层：先铺一层 ink，再把主体画进去，边缘就有了 1px 黑边。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-5, -8, 9, 8); // 身子（含轮廓）
  g.fillRect(3, -11, 5, 6); // 头

  g.fillStyle(WORLD.charcoal, 1);
  g.fillRect(-4, -7, 7, 6); // 身子内芯
  g.fillRect(4, -10, 3, 4); // 头内芯

  // 耳朵：两个清楚的三角尖。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(3, -12, 1, 2);
  g.fillRect(7, -12, 1, 2);
  g.fillStyle(WORLD.charcoal, 1);
  g.fillRect(4, -12, 1, 1);
  g.fillRect(6, -12, 1, 1);

  // 尾巴：向上翘两格，末端加深。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-6, -9, 2, 6);
  g.fillRect(-7, -11, 2, 3);
  g.fillStyle(WORLD.charcoal, 1);
  g.fillRect(-6, -8, 1, 4);
  g.fillStyle(WORLD.amber, 1);
  g.fillRect(-7, -11, 1, 1);

  // 眼睛：两只，比一只更好认成"脸"。
  g.fillStyle(WORLD.warmLight, 1);
  g.fillRect(4, -9, 1, 1);
  g.fillRect(6, -9, 1, 1);
}

/** 稳定的道具哈希：同 id 永远同外观，截图可复现。 */
function propHash(id: string): number {
  let h = 2166136261;
  for (let i = 0; i < id.length; i += 1) {
    h ^= id.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

/** 路灯：柱 + 灯笼 + 1px 硬光池。不用 fillCircle，避免矢量圆和像素块混用。 */
function drawLamp(g: Phaser.GameObjects.Graphics, seed: number, accent: PropAccent = null): void {
  // 接触阴影：两级实心块，读成“灯柱踩在地上”。
  g.fillStyle(WORLD.deepShade, 0.28);
  g.fillRect(-4, -1, 9, 2);
  g.fillStyle(WORLD.deepShade, 0.16);
  g.fillRect(-6, 1, 13, 1);

  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-1, -14, 2, 14);
  g.fillRect(-3, -1, 6, 1);
  g.fillStyle(WORLD.charcoal, 1);
  g.fillRect(0, -13, 1, 12);

  // 灯笼：黑框 + 暗木芯 + amber 光；pale 高光在左上。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-4, -21, 9, 8);
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(-3, -20, 7, 6);
  g.fillStyle(WORLD.amber, 0.95);
  g.fillRect(-2, -19, 5, 4);
  g.fillStyle(WORLD.warmLight, 1);
  g.fillRect(-1, -18, 3, 2);
  g.fillStyle(WORLD.pale, 1);
  g.fillRect(-2, -19, 1, 1);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(-5, -22, 11, 1);
  g.fillRect(-1, -23, 3, 1);

  if (accent === 'gear') {
    // 齿轮：灯罩下沿一枚 7×7 的记号。5×5 在 2 倍显示下只有 10px，读不出来。
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-4, -17, 9, 9);
    g.fillStyle(WORLD.trim, 1);
    g.fillRect(-3, -16, 7, 7);
    // 四个齿：四边各凸出一格。
    g.fillRect(-1, -18, 3, 1);
    g.fillRect(-1, -9, 3, 1);
    g.fillRect(-5, -14, 1, 3);
    g.fillRect(3, -14, 1, 3);
    // 轴心留空，读成轮子而不是方块。
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-1, -14, 3, 3);
    g.fillStyle(WORLD.timberDark, 1);
    g.fillRect(0, -13, 1, 1);
  }

  // 灯下光池：两层硬边矩形 + 抖动缺口，不用大 alpha 圆。
  const flicker = seed % 3;
  g.fillStyle(WORLD.amber, 0.1);
  g.fillRect(-9, -2, 19, 4);
  g.fillRect(-7, 2, 15, 2);
  g.fillStyle(WORLD.amber, 0.16);
  g.fillRect(-6, -1, 13, 2);
  g.fillStyle(WORLD.warmLight, 0.24);
  g.fillRect(-4 + flicker, -1, 8, 1);
}

/** 树：树干 + 3–5 个树冠团块 + 根部阴影。树冠用实心像素块，不用圆。 */
function drawTree(g: Phaser.GameObjects.Graphics, seed: number, accent: PropAccent = null): void {
  const size = seed % 3; // 小 / 中 / 大
  const trunkH = 8 + size * 2;
  const cw = 13 + size * 3;
  const ch = 11 + size * 2;

  // 地面阴影：向右下错开的硬边块。
  g.fillStyle(WORLD.deepShade, 0.24);
  g.fillRect(-5, -1, 12, 2);
  g.fillRect(-3, 1, 8, 1);

  // 树干与根。
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-1, -trunkH, 3, trunkH);
  g.fillStyle(WORLD.timberDark, 1);
  g.fillRect(0, -trunkH + 1, 2, trunkH - 1);
  g.fillStyle(WORLD.timber, 1);
  g.fillRect(1, -trunkH + 2, 1, Math.max(2, trunkH - 3));
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-2, -1, 3, 1);
  g.fillRect(1, -1, 3, 1);

  // 树冠：先黑轮廓，再暗底，再两档亮部；团块错开，避免圆球感。
  const top = -trunkH - ch;
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-Math.floor(cw / 2) - 1, top - 1, cw + 2, ch + 2);
  g.fillRect(-Math.floor(cw / 2) + 2, top - 4, cw - 3, 4);
  g.fillStyle(WORLD.soil, 1);
  g.fillRect(-Math.floor(cw / 2), top, cw, ch);
  g.fillStyle(WORLD.grass, 1);
  g.fillRect(-Math.floor(cw / 2) + 1, top + 1, cw - 2, ch - 2);
  g.fillStyle(WORLD.moss, 1);
  g.fillRect(-Math.floor(cw / 2) + 2, top + 1, Math.floor(cw / 2), 2);
  g.fillRect(-Math.floor(cw / 2) + 2, top + 3, 2, ch - 4);
  g.fillRect(-Math.floor(cw / 2) + 2, top - 2, Math.floor(cw / 2), 2);

  // 树冠孔隙：2–3 个 1×2 的土壤色缺口。
  g.fillStyle(WORLD.soil, 1);
  g.fillRect(-2 + (seed % 3), top + 4, 1, 2);
  g.fillRect(3 - (seed % 2), top + 7, 2, 1);
  if (size > 0) g.fillRect(-Math.floor(cw / 2) + 3, top + 8, 1, 2);

  if (accent === 'book') {
    // 书放在树干**左侧**：右侧是角色站立与走动的热点，放那边会被小人挡掉。
    // 8×6：一块斜靠的石板 + 摊开的书页，读成「这里查过资料」。
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-13, -8, 10, 8);
    g.fillStyle(WORLD.stone, 1);
    g.fillRect(-12, -7, 8, 6);
    g.fillStyle(WORLD.plaster, 1);
    g.fillRect(-11, -6, 3, 4);
    g.fillRect(-7, -6, 2, 4);
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-8, -6, 1, 4);
    g.fillRect(-12, -1, 8, 1);
  }
}

/** 旗帜：木杆 + 布面 + 顶部亮边；布面有一个像素缺口，读成被风吹起。 */
function drawBanner(g: Phaser.GameObjects.Graphics, seed: number, accent: PropAccent = null): void {
  g.fillStyle(WORLD.ink, 0.2);
  g.fillRect(-3, -1, 8, 2);
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(-1, -19, 2, 19);
  g.fillRect(-2, -1, 4, 1);
  g.fillStyle(WORLD.charcoal, 1);
  g.fillRect(0, -18, 1, 17);
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(1, -18, 9, 8);
  g.fillStyle(WORLD.ember, 1);
  g.fillRect(2, -17, 7, 6);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(2, -17, 7, 1);
  // 风吹缺口：固定在右侧，不逐帧抖动。
  g.fillStyle(WORLD.soil, 1);
  g.fillRect(8, -15 + (seed % 2), 1, 2);

  if (accent === 'inkstone') {
    // 砚台：旗杆底一方浅石台，中央一点墨。放大到 11×5 才看得出来。
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-6, -5, 11, 5);
    g.fillStyle(WORLD.plaster, 1);
    g.fillRect(-5, -4, 9, 3);
    g.fillStyle(WORLD.stone, 1);
    g.fillRect(-5, -1, 9, 1);
    g.fillStyle(WORLD.ink, 1);
    g.fillRect(-2, -4, 4, 2);
    g.fillStyle(WORLD.pale, 1);
    g.fillRect(-1, -4, 1, 1);
  }
}
