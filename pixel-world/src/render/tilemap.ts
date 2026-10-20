/** 地形层：把网格账本画成一张静态贴图，只在开局画一次。
 *
 * 世界的空间事实全在 WorldGrid 里；这里不判断能不能走，只照着画。
 * 地形纹理不用“随机撒点”：16 色下随机 1px 噪点会糊成雪花，
 * 改用 2×2 有序抖动（Bayer）把基色与相邻一档调色板色压出中间调。
 */

import Phaser from 'phaser';
import type { WorldGrid } from '../world/grid.ts';
import { TILE_PX } from '../world/setup.ts';
import { TERRAIN_COLORS, TERRAIN_SPECK, WORLD } from '../world/palette-data.ts';

const DRESSING = ['tuft', 'pebble', 'flower', 'bare'] as const;
type Dressing = (typeof DRESSING)[number];

export function drawTerrain(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
): void {
  const g = scene.add.graphics();
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined) continue;
      const px = originX + x * TILE_PX;
      const py = originY + y * TILE_PX;
      g.fillStyle(TERRAIN_COLORS[cell.terrain], 1);
      g.fillRect(px, py, TILE_PX, TILE_PX);
      drawTexture(g, px, py, x, y, cell.terrain);
    }
  }
  drawDressing(scene, grid, originX, originY);
  // 网格线只是排查落位用的辅助层，默认不画：铺满全屏的细线会把地形纹理与轮廓压成
  // “表格纸”，也会让建筑/道路的边线失去对比。需要时用 ?debug-grid=1 打开。
  const params = new URLSearchParams(window.location.search);
  if (params.get('debug-grid') === '1') drawGridLines(g, grid, originX, originY);
}

/** 2×2 像素簇纹理：每格 2–3 个 2×2 实心小簇，位置由格坐标哈希稳定决定。
 *
 * 第一版铺满 2×2 网点，实测整片地面变成“织物”，远处看是脏噪点——比原来的随机
 * 1px 撒点更糟。改成稀疏、成簇：仍是确定性的 2×2 单元，但没有整块网点。
 */
function drawTexture(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
  terrain: keyof typeof TERRAIN_COLORS,
): void {
  const tone = TERRAIN_SPECK[terrain];
  if (tone === TERRAIN_COLORS[terrain]) return;
  const seed = Math.abs((x * 73856093) ^ (y * 19349663)) >>> 0;
  const clusters = 2 + (seed % 2); // 每格 2–3 簇
  g.fillStyle(tone, 1);
  for (let i = 0; i < clusters; i += 1) {
    const h = Math.imul(seed ^ ((i + 1) * 2654435761), 2246822519) >>> 0;
    const cx = px + 1 + (h % (TILE_PX - 3));
    const cy = py + 1 + ((h >>> 8) % (TILE_PX - 3));
    g.fillRect(cx, cy, 2, 2);
  }
}

/** 草地上的草簇与石子。纯装饰，不参与碰撞。 */
function drawDressing(
  scene: Phaser.Scene,
  grid: WorldGrid,
  originX: number,
  originY: number,
): void {
  const g = scene.add.graphics();
  for (let y = 0; y < grid.height; y += 1) {
    for (let x = 0; x < grid.width; x += 1) {
      const cell = grid.at({ x, y });
      if (cell === undefined || cell.terrain !== 'grass') continue;
      const hash = Math.abs((x * 2654435761) ^ (y * 40503));
      const kind: Dressing = DRESSING[hash % DRESSING.length];
      if (kind === 'bare') continue;
      const px = originX + x * TILE_PX;
      const py = originY + y * TILE_PX;
      const jx = px + 3 + (hash % 8);
      const jy = py + 4 + ((hash >> 3) % 8);
      if (kind === 'tuft') {
        g.fillStyle(WORLD.grassLit, 0.9);
        g.fillRect(jx, jy, 1, 3);
        g.fillRect(jx + 2, jy + 1, 1, 2);
      } else if (kind === 'pebble') {
        g.fillStyle(WORLD.plaza, 0.8);
        g.fillRect(jx, jy, 2, 1);
      } else {
        g.fillStyle(WORLD.amber, 0.9);
        g.fillRect(jx, jy, 1, 1);
      }
    }
  }
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
