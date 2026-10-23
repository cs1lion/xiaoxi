/** 地形层：把网格账本画成一张静态贴图，只在开局画一次。
 *
 * 世界的事实全在 WorldGrid 里；这里不判断能不能走，只照着画。
 *
 * 2026-10-20 视觉语法重做（24 色内）：
 * - 先铺底色、再铺纹理、最后画拼接边。纹理可以跨格连续，不会被后画的底色盖住。
 * - 草地不再每格撒 2×2 噪点：用 4×4 格的低频草甸掩码，只在部分区域长草簇/花。
 * - 石/广场改成不规则板岩：每格 3–4 块，缝不必对齐 8px，避免读成砖墙。
 * - 农田保留垄线，但加作物幼苗与少量麦穗；空地仍然是安静的土。
 */

import Phaser from 'phaser';
import type { WorldGrid } from '../world/grid.ts';
import type { GridPoint, TerrainId } from '../world/types.ts';
import { TILE_PX } from '../world/setup.ts';
import { TERRAIN_COLORS, WORLD } from '../world/palette-data.ts';
import { biomeAt } from '../world/biomes.ts';
import { DRESSING } from '../world/dressing.ts';
import { drawTileEdge, type EdgeNeighbors } from './terrain-edges.ts';

type Terrain = keyof typeof TERRAIN_COLORS;

export function drawTerrain(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
  target?: Phaser.GameObjects.Graphics,
  altar?: GridPoint,
): void {
  // 给了 target 就画进它：烘焙到纹理时用同一个 Graphics，
  // 否则每次子绘制都会多创建一个对象。
  const g = target ?? scene.add.graphics();

  // pass 1：底色。先全部铺满，后面的纹理才不会被邻格底色覆盖。
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined) continue;
      g.fillStyle(TERRAIN_COLORS[cell.terrain], 1);
      g.fillRect(originX + x * TILE_PX, originY + y * TILE_PX, TILE_PX, TILE_PX);
    }
  }

  // pass 2：材质内部结构。
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined) continue;
      drawTexture(g, originX + x * TILE_PX, originY + y * TILE_PX, x, y, cell.terrain);
    }
  }

  // pass 3：材质拼接边（高度序由 terrain-edges 决定，边只画在高材质一侧）。
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined) continue;
      drawTileEdge(
        g,
        cell.terrain,
        neighborsOf(grid, x, y),
        originX + x * TILE_PX,
        originY + y * TILE_PX,
      );
    }
  }

  drawPlantingBeds(scene, grid, originX, originY, g);
  drawFarmFence(scene, grid, originX, originY, g);
  drawDressing(scene, grid, originX, originY, g, altar);

  // 网格线只是排查落位用的辅助层，默认不画：铺满全屏的细线会把地形纹理与轮廓压成
  // “表格纸”，也会让建筑/道路的边线失去对比。需要时用 ?debug-grid=1 打开。
  const params = new URLSearchParams(window.location.search);
  if (params.get('debug-grid') === '1') drawGridLines(g, grid, originX, originY);
}

/** 取一格周围 8 邻格的材质，供地形拼接边判定（越界记 null，世界边界不画边）。 */
function neighborsOf(grid: WorldGrid, x: number, y: number): EdgeNeighbors {
  const terrainAt = (dx: number, dy: number): TerrainId | null =>
    grid.at({ x: x + dx, y: y + dy })?.terrain ?? null;
  return {
    north: terrainAt(0, -1),
    northEast: terrainAt(1, -1),
    east: terrainAt(1, 0),
    southEast: terrainAt(1, 1),
    south: terrainAt(0, 1),
    southWest: terrainAt(-1, 1),
    west: terrainAt(-1, 0),
    northWest: terrainAt(-1, -1),
  };
}

/** 稳定的整数哈希：同坐标永远同结果，截图可复现。 */
function hash2(x: number, y: number): number {
  let h = Math.imul(x, 374761393) + Math.imul(y, 668265263);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return (h ^ (h >>> 16)) >>> 0;
}

/** 矮灌木：阶梯轮廓 + 顶部受光，不画成齐整黑框。 */
function drawBush(g: Phaser.GameObjects.Graphics, px: number, py: number): void {
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(px + 4, py + 9, 9, 2);
  g.fillRect(px + 5, py + 7, 7, 2);
  g.fillRect(px + 3, py + 9, 1, 1);
  g.fillStyle(WORLD.grass, 1);
  g.fillRect(px + 5, py + 8, 7, 2);
  g.fillRect(px + 6, py + 7, 5, 1);
  g.fillStyle(WORLD.moss, 1);
  g.fillRect(px + 5, py + 7, 4, 1);
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(px + 6, py + 6, 1, 1);
  g.fillRect(px + 10, py + 8, 1, 1);
}

/** 岩石：4–7px 的阶梯块，左上 plaza 受光，右下 ink 阴影。 */
function drawRock(g: Phaser.GameObjects.Graphics, px: number, py: number): void {
  g.fillStyle(WORLD.ink, 1);
  g.fillRect(px + 4, py + 10, 7, 1);
  g.fillRect(px + 5, py + 8, 5, 3);
  g.fillRect(px + 9, py + 9, 1, 2);
  g.fillStyle(WORLD.stone, 1);
  g.fillRect(px + 5, py + 8, 5, 2);
  g.fillRect(px + 4, py + 9, 1, 1);
  g.fillStyle(WORLD.plaza, 1);
  g.fillRect(px + 5, py + 8, 3, 1);
}

/** 单格材质内部结构：靠结构语言区分材质，不靠跨材质加色。 */
function drawTexture(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
  terrain: Terrain,
): void {
  if (terrain === 'farm') {
    drawFarmRows(g, px, py, x, y);
    return;
  }
  if (terrain === 'stone' || terrain === 'plaza') {
    drawFlagstones(g, px, py, x, y, terrain);
    return;
  }
  if (terrain === 'tree') {
    drawCanopy(g, px, py, x, y);
    return;
  }
  drawMeadow(g, px, py, x, y);
}

/**
 * 石/广场：每格 3–4 块不规则板岩。
 *
 * 旧实现是 8px 一块、横缝通栏 + 竖缝错缝，整条路读成砖墙。这里把石块尺寸打散到
 * 5–10px，缝宽 1px，且缝在格内断开；石材的“铺装”读法来自石块轮廓，不是网格。
 */
function drawFlagstones(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
  terrain: 'stone' | 'plaza',
): void {
  const face = terrain === 'stone' ? WORLD.stone : WORLD.plaza;
  const seam = terrain === 'stone' ? WORLD.soil : WORLD.stone;
  g.fillStyle(seam, 1);
  g.fillRect(px, py, TILE_PX, TILE_PX);

  const layouts: readonly (readonly [number, number, number, number])[][] = [
    [
      [0, 0, 7, 7],
      [8, 0, 8, 6],
      [0, 8, 5, 8],
      [6, 8, 10, 8],
    ],
    [
      [0, 0, 6, 9],
      [7, 0, 9, 6],
      [0, 10, 9, 6],
      [10, 7, 6, 9],
    ],
    [
      [0, 0, 8, 5],
      [9, 0, 7, 9],
      [0, 6, 8, 10],
      [9, 10, 7, 6],
    ],
  ];
  const h = hash2(x, y);
  const stones = layouts[h % layouts.length] ?? layouts[0]!;

  for (let i = 0; i < stones.length; i += 1) {
    const stone = stones[i]!;
    const [sx, sy, sw, sh] = stone;
    g.fillStyle(face, 1);
    g.fillRect(px + sx, py + sy, sw, sh);

    // 光从左上打：石块上/左各 1px 受光。只有 stone 有更亮一档的 plaza 可用；
    // plaza 自身已是最亮石材，不再加高光，避免出现白线。
    if (terrain === 'stone') {
      g.fillStyle(WORLD.plaza, 1);
      g.fillRect(px + sx, py + sy, sw, 1);
      g.fillRect(px + sx, py + sy, 1, sh);
    }

    // 缺角：每块石头最多一个 1px 缺口，读成磨损而不是图案。
    if ((h + i * 11) % 3 === 0) {
      g.fillStyle(seam, 1);
      g.fillRect(px + sx + sw - 2, py + sy + 1, 1, 1);
    }

    // 苔：缝里长一点，不铺满。用 grass 而不是 grassLit，避免和石材亮度打架。
    if ((h + i * 7) % 5 === 0) {
      g.fillStyle(WORLD.moss, 1);
      g.fillRect(px + sx + 1, py + sy + sh - 2, 2, 1);
    }
  }

  // 石板裂纹：一条 3–5px 的折线，只在少数石格出现。
  if (h % 4 === 0) {
    const cx = px + 3 + (h % 8);
    const cy = py + 2 + ((h >>> 8) % 9);
    g.fillStyle(seam, 1);
    g.fillRect(cx, cy, 1, 2);
    g.fillRect(cx + 1, cy + 2, 1, 1);
    g.fillRect(cx + 2, cy + 3, 1, 1);
  }
}

/**
 * 草地：低频草甸掩码。
 *
 * 4×4 格一块，约 40% 的块是“草甸”，其余是安静草地。草簇只长在草甸里；贴边草由
 * drawDressing 负责。这样整片草地有呼吸，不会每格都像雪花屏。
 */
function drawMeadow(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
): void {
  const patch = hash2(x >> 2, y >> 2);
  const h = hash2(x, y);
  const dense = patch % 7 < 2;
  const clusters = dense ? 1 + (h % 2) : h % 7 === 0 ? 1 : 0;

  for (let i = 0; i < clusters; i += 1) {
    const ch = hash2(x + i * 17, y + i * 31);
    const cx = px + 2 + (ch % 12);
    const cy = py + 3 + ((ch >>> 8) % 10);
    g.fillStyle(WORLD.soil, 1);
    g.fillRect(cx, cy, 1, 3);
    g.fillRect(cx + 2, cy + 1, 1, 2);
    g.fillRect(cx + 1, cy + 2, 2, 1);
    if (ch % 3 === 0) {
      g.fillStyle(WORLD.moss, 1);
      g.fillRect(cx + 2, cy, 1, 1);
    }
  }

  // 花只在少数草甸块里成簇出现；trim 已经够亮，不再用 pale 抢窗光。
  if (dense && patch % 29 === 0 && h % 5 === 0) {
    const fx = px + 4 + (h % 6);
    const fy = py + 4 + ((h >>> 5) % 6);
    g.fillStyle(WORLD.soil, 1);
    g.fillRect(fx, fy + 1, 1, 3);
    g.fillRect(fx + 2, fy + 2, 1, 2);
    g.fillStyle(WORLD.trim, 1);
    g.fillRect(fx, fy, 1, 1);
    g.fillRect(fx + 2, fy + 1, 1, 1);
  }
}

/** 树冠/树篱地形：底土 + 大小不一的草团，不做重复的“脸”形。 */
function drawCanopy(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
): void {
  g.fillStyle(WORLD.soil, 1);
  g.fillRect(px, py, TILE_PX, TILE_PX);
  for (let i = 0; i < 3; i += 1) {
    const h = hash2(x * 3 + i, y * 5 + i);
    const w = 4 + (h % 4);
    const hh = 3 + ((h >>> 4) % 4);
    const bx = px + 1 + ((h >>> 8) % (TILE_PX - w - 1));
    const by = py + 2 + ((h >>> 16) % (TILE_PX - hh - 2));
    g.fillStyle(WORLD.grass, 1);
    g.fillRect(bx, by, w, hh);
    g.fillStyle(WORLD.moss, 1);
    g.fillRect(bx, by, Math.max(2, w - 2), 1);
  }
  // 树篱的暗缝：只在部分格出现，避免每格同形。
  const h = hash2(x, y);
  if (h % 3 === 0) {
    g.fillStyle(WORLD.deepShade, 1);
    g.fillRect(px + 2 + (h % 10), py + 3 + ((h >>> 8) % 9), 1, 2);
  }
}

/** 农田：横向垄线 + 幼苗/麦穗。垄线仍是结构，但不再只有线。 */
function drawFarmRows(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
): void {
  g.fillStyle(WORLD.soil, 1);
  for (let ry = 3; ry < TILE_PX; ry += 4) {
    g.fillRect(px, py + ry, TILE_PX, 1);
  }

  const h = hash2(x, y);
  for (const rowY of [3, 7, 11, 15] as const) {
    for (let sx = 1; sx < TILE_PX; sx += 3) {
      if ((hash2(x * 16 + sx, y * 16 + rowY) & 3) !== 0) continue;
      g.fillStyle(WORLD.moss, 1);
      g.fillRect(px + sx, py + rowY - 1, 1, 2);
      g.fillStyle(WORLD.grassLit, 1);
      g.fillRect(px + sx + 1, py + rowY, 1, 1);
    }
  }

  // 少量成熟麦穗：每 3 格最多 1 个 1px 点，不用 amber。
  if (h % 3 === 0) {
    g.fillStyle(WORLD.trim, 1);
    g.fillRect(px + 3 + (h % 10), py + 5 + ((h >>> 8) % 8), 1, 1);
  }
}

/**
 * 种植带：把 4 格宽的石街视觉收成 2 格路径，两侧长草。
 *
 * 网格里 street 仍是 stone、仍可走；这里只是在石面上铺低矮草带与石缘，属于
 * 渲染层的构图处理——世界逻辑、碰撞、路径都不改。没有这层，30×20 的画面里
 * 石带会占掉近一半屏幕，读成“停车场”而不是小镇。
 */
function drawPlantingBeds(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
  target?: Phaser.GameObjects.Graphics,
): void {
  const g = target ?? scene.add.graphics();
  const streetX = Math.floor(grid.width / 2) - 2;
  const streetW = 4;
  let plazaTop = grid.height;
  let plazaBottom = -1;
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      if (grid.at({ x, y })?.terrain !== 'plaza') continue;
      plazaTop = Math.min(plazaTop, y);
      plazaBottom = Math.max(plazaBottom, y);
    }
  }
  if (plazaBottom < 0) return;

  type Rect = readonly [number, number, number, number];
  const beds: Rect[] = [
    // 竖向街道的两侧：留下 x = streetX+1 / streetX+2 两格作路面。
    [streetX, 1, 1, plazaTop - 1],
    [streetX + streetW - 1, 1, 1, plazaTop - 1],
    [streetX, plazaBottom + 1, 1, grid.height - 2 - (plazaBottom + 1) + 1],
    [streetX + streetW - 1, plazaBottom + 1, 1, grid.height - 2 - (plazaBottom + 1) + 1],
    // 横向石带的下两行改成草带：保留 y = plazaBottom-1 / plazaBottom 作东西向路面。
    [1, plazaBottom + 1, streetX - 1, 2],
    [streetX + streetW, plazaBottom + 1, grid.width - 2 - (streetX + streetW) + 1, 2],
  ];

  for (const [rx, ry, rw, rh] of beds) {
    for (let y = ry; y < ry + rh; y += 1) {
      for (let x = rx; x < rx + rw; x += 1) {
        if (grid.at({ x, y })?.terrain !== 'stone') continue;
        const px = originX + x * TILE_PX;
        const py = originY + y * TILE_PX;
        const h = hash2(x + 71, y + 29);
        g.fillStyle(WORLD.grass, 1);
        g.fillRect(px, py, TILE_PX, TILE_PX);
        // 石缘：只画在整块草带的外边界，内部草格之间不画线。
        g.fillStyle(WORLD.stone, 1);
        if (x === rx) g.fillRect(px, py, 1, TILE_PX);
        if (x === rx + rw - 1) g.fillRect(px + TILE_PX - 1, py, 1, TILE_PX);
        if (y === ry) g.fillRect(px, py, TILE_PX, 1);
        if (y === ry + rh - 1) g.fillRect(px, py + TILE_PX - 1, TILE_PX, 1);

        // 草带内部：草簇、花与矮灌木分层出现；每块只长少量，保持安静。
        if (h % 3 === 0) {
          const cx = px + 2 + (h % 11);
          const cy = py + 3 + ((h >>> 8) % 9);
          g.fillStyle(WORLD.soil, 1);
          g.fillRect(cx, cy, 1, 3);
          g.fillRect(cx + 2, cy + 1, 1, 2);
        }
        if (h % 5 === 0) {
          const fx = px + 3 + (h % 9);
          const fy = py + 4 + ((h >>> 6) % 7);
          g.fillStyle(WORLD.soil, 1);
          g.fillRect(fx, fy + 1, 1, 2);
          g.fillRect(fx + 3, fy + 2, 1, 1);
          g.fillStyle(WORLD.trim, 1);
          g.fillRect(fx, fy, 1, 1);
          g.fillRect(fx + 3, fy + 1, 1, 1);
        }
        // 矮灌木：只在大草带里偶尔出现，读成绿化而不是树。
        if ((rw > 1 && h % 7 === 0) || (rw === 1 && h % 11 === 0)) {
          drawBush(g, px, py);
        }
      }
    }
  }
}

/**
 * 农庄边界：只沿世界最底/最右的农田边缘画矮篱，避免挡住从铺子出来的路。
 * 再加少量干草垛，让大片农田有“收过一季”的读法。
 */
function drawFarmFence(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
  target?: Phaser.GameObjects.Graphics,
): void {
  const g = target ?? scene.add.graphics();
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      if (grid.at({ x, y })?.terrain !== 'farm') continue;
      const px = originX + x * TILE_PX;
      const py = originY + y * TILE_PX;
      const h = hash2(x + 13, y + 41);
      if (y === grid.height - 2) {
        g.fillStyle(WORLD.timberDark, 1);
        g.fillRect(px + 2, py + 12, 2, 4);
        g.fillRect(px + 11, py + 12, 2, 4);
        g.fillStyle(WORLD.trim, 1);
        g.fillRect(px, py + 13, TILE_PX, 1);
        g.fillRect(px, py + 15, TILE_PX, 1);
      }
      if (x === grid.width - 2) {
        g.fillStyle(WORLD.timberDark, 1);
        g.fillRect(px + 12, py + 2, 2, 4);
        g.fillRect(px + 12, py + 11, 2, 4);
        g.fillStyle(WORLD.trim, 1);
        g.fillRect(px + 13, py, 1, TILE_PX);
        g.fillRect(px + 15, py, 1, TILE_PX);
      }
      // 干草垛：每 97 格出现一次，1 个 5×3 小块。
      if (h % 97 === 0) {
        g.fillStyle(WORLD.ink, 1);
        g.fillRect(px + 4, py + 5, 7, 5);
        g.fillStyle(WORLD.trim, 1);
        g.fillRect(px + 5, py + 6, 5, 3);
        g.fillStyle(WORLD.terracotta, 1);
        g.fillRect(px + 5, py + 7, 5, 1);
      }
    }
  }
}

/** 草地/农田的贴边草与地表细节。纯装饰，不参与碰撞。 */
function drawDressing(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
  target?: Phaser.GameObjects.Graphics,
  altar?: GridPoint,
): void {
  const g = target ?? scene.add.graphics();
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined || cell.terrain !== 'grass') continue;
      const px = originX + x * TILE_PX;
      const py = originY + y * TILE_PX;
      const h = hash2(x, y);
      // 没有祭坛坐标就退回工坊环的配方：调用方漏传时行为最接近旧版，
      // 而不是让整张图突然变成郊野。
      const recipe = altar === undefined
        ? DRESSING.craftRing
        : DRESSING[biomeAt({ x, y }, altar)];

      const edge = nearestNonGrassSide(grid, x, y);
      if (edge !== null && h % recipe.edge < 3) {
        const inset = 1 + (h % 2);
        let jx = px + 4 + (h % 7);
        let jy = py + 6 + ((h >>> 3) % 5);
        if (edge === 'west') jx = px + inset;
        else if (edge === 'east') jx = px + TILE_PX - 2 - inset;
        else if (edge === 'north') jy = py + inset;
        else jy = py + TILE_PX - 3 - inset;
        g.fillStyle(WORLD.soil, 1);
        g.fillRect(jx, jy, 1, 3);
        g.fillRect(jx + 2, jy + 1, 1, 2);
        continue;
      }

      // 四类细节互斥，按固定顺序判定：一格只加一样东西，
      // 否则镇内会出现「岩石上开着花」这种拼贴感。
      if (h % recipe.rock === 0) {
        drawRock(g, px, py);
      } else if (h % recipe.bush === 0) {
        drawBush(g, px, py);
      } else if (recipe.flower !== 0 && h % recipe.flower === 0) {
        drawWildflower(g, px, py, h);
      } else if (recipe.slab !== 0 && h % recipe.slab === 0) {
        g.fillStyle(WORLD.plaza, 0.8);
        g.fillRect(px + 4 + (h % 8), py + 5 + ((h >>> 8) % 6), 2, 1);
      }
    }
  }
}

/**
 * 野花：一小丛，三朵高低错开。
 *
 * 第一版只有 1px 花头 + 1px 茎，3x 缩放下看起来像两个孤零零的亮点，
 * 读不出「一丛花」。改成每个花头 2×2（带 1px 花心）、三朵错开高度，
 * 才在像素密度上立得住。
 *
 * 镇内的花在窗台花箱里（`drawFacade`），地表不长花——人踩的地方草都长不齐。
 * 郊野才有一片一片的野花，那也是「没人来过」的证据。
 */
function drawWildflower(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  h: number,
): void {
  const ox = 2 + (h % 9);
  const oy = 5 + ((h >>> 7) % 7);
  // 茎：三根，高度错开。
  g.fillStyle(WORLD.soil, 1);
  g.fillRect(px + ox, py + oy - 2, 1, 3);
  g.fillRect(px + ox + 3, py + oy - 3, 1, 4);
  g.fillRect(px + ox + 6, py + oy - 1, 1, 2);
  // 花冠：2×2 的 trim 加一格暖高光，读成花心。
  g.fillStyle(WORLD.trim, 1);
  g.fillRect(px + ox - 1, py + oy - 4, 2, 2);
  g.fillRect(px + ox + 2, py + oy - 5, 2, 2);
  g.fillRect(px + ox + 5, py + oy - 3, 2, 2);
  g.fillStyle(WORLD.warmLight, 1);
  g.fillRect(px + ox + 2, py + oy - 5, 1, 1);
  // 叶：只在部分丛出现，避免每丛都一样。
  if (h % 3 === 0) {
    g.fillStyle(WORLD.moss, 1);
    g.fillRect(px + ox + 1, py + oy, 2, 1);
    g.fillRect(px + ox + 5, py + oy + 1, 2, 1);
  }
}

/** 草地格最先找到的非草地邻边方向；四面都是草地时返回 null。 */
function nearestNonGrassSide(
  grid: WorldGrid,
  x: number,
  y: number,
): 'north' | 'east' | 'south' | 'west' | null {
  const isGrass = (dx: number, dy: number): boolean =>
    grid.at({ x: x + dx, y: y + dy })?.terrain === 'grass';
  if (!isGrass(0, -1)) return 'north';
  if (!isGrass(1, 0)) return 'east';
  if (!isGrass(0, 1)) return 'south';
  if (!isGrass(-1, 0)) return 'west';
  return null;
}

/** 调试用的网格线：只在 ?debug-grid=1 时由 drawTerrain 调用，不参与正式画面。 */
function drawGridLines(
  g: Phaser.GameObjects.Graphics,
  grid: WorldGrid,
  originX: number,
  originY: number,
): void {
  g.lineStyle(1, WORLD.soil, 0.25);
  for (let x = 0; x <= grid.width; x += 1) {
    g.lineBetween(originX + x * TILE_PX, originY, originX + x * TILE_PX, originY + grid.height * TILE_PX);
  }
  for (let y = 0; y <= grid.height; y += 1) {
    g.lineBetween(originX, originY + y * TILE_PX, originX + grid.width * TILE_PX, originY + y * TILE_PX);
  }
}
