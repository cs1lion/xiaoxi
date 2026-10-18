/** 地形层：把网格账本画成一张静态贴图，只在开局画一次。
 *
 * 世界的空间事实全在 WorldGrid 里；这里不判断能不能走，只照着画。
 */

import Phaser from 'phaser';
import type { WorldGrid } from '../world/grid.ts';
import { TILE_PX } from '../world/setup.ts';
import { TERRAIN_COLORS, TERRAIN_SPECK, UI } from './palette.ts';

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
      drawSpeck(g, px, py, x, y, cell.terrain);
    }
  }
  drawDressing(scene, grid, originX, originY);
  drawGridLines(g, grid, originX, originY);
}

function drawSpeck(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
  terrain: keyof typeof TERRAIN_COLORS,
): void {
  const seed = (x * 73856093) ^ (y * 19349663);
  const count = 3 + (Math.abs(seed) % 3);
  g.fillStyle(TERRAIN_SPECK[terrain], 0.55);
  for (let i = 0; i < count; i += 1) {
    const h = Math.abs(seed * (i + 1) * 2654435761);
    const sx = px + 2 + (h % 12);
    const sy = py + 2 + ((h >> 4) % 12);
    g.fillRect(sx, sy, 1, 1);
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
        g.fillStyle(UI.grassAlt, 0.9);
        g.fillRect(jx, jy, 1, 3);
        g.fillRect(jx + 2, jy + 1, 1, 2);
      } else if (kind === 'pebble') {
        g.fillStyle(0x59615a, 0.8);
        g.fillRect(jx, jy, 2, 1);
      } else {
        g.fillStyle(0xc9b46a, 0.9);
        g.fillRect(jx, jy, 1, 1);
      }
    }
  }
}

function drawGridLines(
  g: Phaser.GameObjects.Graphics,
  grid: WorldGrid,
  originX: number,
  originY: number,
): void {
  g.lineStyle(1, UI.grid, 0.25);
  for (let x = 0; x <= grid.width; x += 1) {
    g.lineBetween(originX + x * TILE_PX, originY, originX + x * TILE_PX, originY + grid.height * TILE_PX);
  }
  for (let y = 0; y <= grid.height; y += 1) {
    g.lineBetween(originX, originY + y * TILE_PX, originX + grid.width * TILE_PX, originY + y * TILE_PX);
  }
}
