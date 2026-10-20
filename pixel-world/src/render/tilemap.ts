/** 地形层：把网格账本画成一张静态贴图，只在开局画一次。
 *
 * 世界的空间事实全在 WorldGrid 里；这里不判断能不能走，只照着画。
 * 地形纹理不用“随机撒点”：16 色下随机 1px 噪点会糊成雪花，
 * 改用 2×2 有序抖动（Bayer）把基色与相邻一档调色板色压出中间调。
 */

import Phaser from 'phaser';
import type { WorldGrid } from '../world/grid.ts';
import type { TerrainId } from '../world/types.ts';
import { TILE_PX } from '../world/setup.ts';
import { TERRAIN_COLORS, TERRAIN_SPECK, WORLD } from '../world/palette-data.ts';
import { drawTileEdge, type EdgeNeighbors } from './terrain-edges.ts';

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
      drawTileEdge(g, cell.terrain, neighborsOf(grid, x, y), px, py);
    }
  }
  drawDressing(scene, grid, originX, originY);
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

/** 单格材质内部结构（ART_STYLE_TARGET §1.4）：靠**内部结构**区分材质，不靠跨材质对比。
 *
 * 三种材质各有自己的结构语言，读法不同就不会互相混淆：
 *   农田    横向垄线（每 4px 一条 1px 深土），读成「犁过的地」
 *   石/广场 石板接缝（8px 一块，横缝通栏、竖缝错缝），读成「铺过的石板」
 *   草地    1–2 个 2×2 簇，且优先长在材质边界附近，读成「地界上的草」而不是满地噪点
 *
 * 为什么不能都用随机 2×2 簇：三种材质用同一种噪声语言，远看就只剩「一层纹理」，
 * 分不出哪块是田、哪块是石板。结构语言不同，16 色也够用。
 */
function drawTexture(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  x: number,
  y: number,
  terrain: keyof typeof TERRAIN_COLORS,
): void {
  if (terrain === 'farm') {
    drawFarmRows(g, px, py);
    return;
  }
  if (terrain === 'stone' || terrain === 'plaza') {
    drawSlabSeams(g, px, py, terrain, x, y);
    return;
  }
  drawGrassClusters(g, px, py, x, y, terrain);
}

/** 农田：横向垄线。每 4px 一条 1px 深土，首条压在格内 y=2，避免与上格的边线叠成 2px。 */
function drawFarmRows(g: Phaser.GameObjects.Graphics, px: number, py: number): void {
  g.fillStyle(TERRAIN_SPECK.farm, 1);
  for (let ry = 2; ry < TILE_PX; ry += 4) {
    g.fillRect(px, py + ry, TILE_PX, 1);
  }
}

/** 石板接缝：8px 一块。横缝通栏，竖缝按行错开半块（错缝砌法），全部 1px 深土 α0.25。 */
function drawSlabSeams(
  g: Phaser.GameObjects.Graphics,
  px: number,
  py: number,
  terrain: 'stone' | 'plaza',
  x: number,
  y: number,
): void {
  // 缝线必须**实心**：soil α0.25 叠在 stone 上合成 #323833，对底色 ΔL* 只有 3.3，
  // 与 P0-1 删掉的通铺网格线（#29352a，ΔL* 2.5）是同一个手法，画了等于没画。
  // 实心 soil 的 ΔL* 是 14.1，与已证明可读的角色墨线（18.1）同量级。
  g.fillStyle(WORLD.soil, 1);
  const slab = TILE_PX / 2; // 16px 格 → 8px 石板，一格 2×2 块
  // 横缝：格内 y = 8 处通栏（y=0 那条由地形拼接边/上一格负责，不重复画）
  g.fillRect(px, py + slab - 1, TILE_PX, 1);
  // 竖缝：错缝。上排缝在 x=0，下排缝在 x=8；用格坐标奇偶决定哪一排是「整块」。
  const offset = (x + y) % 2 === 0 ? 0 : slab;
  g.fillRect(px + ((offset + slab - 1) % TILE_PX), py, 1, slab - 1);
  g.fillRect(px + ((offset + slab * 2 - 1) % TILE_PX), py + slab, 1, slab);
  // 广场板面高光已删除：原实现是 stone α0.35 叠在 plaza 上，合成 #444940，
  // 对底色 ΔL* 只有 2.5 —— 同样不可见。删掉比留一条看不见的线好。
  void terrain;
}

/** 草地：1–2 个 2×2 簇（原为 2–3），位置由格坐标哈希稳定决定。
 *
 * 簇数从 2–3 降到 1–2 是 §1.4 的要求：满地都是簇时，整片草地读成「脏噪点」；
 * 稀疏之后单簇才有意义。贴边的草由 drawDressing 单独负责。
 */
function drawGrassClusters(
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
  const clusters = 1 + (seed % 2); // 每格 1–2 簇（§1.4）
  g.fillStyle(tone, 1);
  for (let i = 0; i < clusters; i += 1) {
    const h = Math.imul(seed ^ ((i + 1) * 2654435761), 2246822519) >>> 0;
    const cx = px + 1 + (h % (TILE_PX - 3));
    const cy = py + 1 + ((h >>> 8) % (TILE_PX - 3));
    g.fillRect(cx, cy, 2, 2);
  }
}

/** 草地上的草簇与石子。纯装饰，不参与碰撞。
 *
 * §1.4：草簇**优先长在贴路边 2px 内**，而不是纯随机撒。
 * 理由：草地上原本满地随机草簇，等于给整块地面加噪声；把草挪到材质边界附近之后，
 * 边界「长出草」这件事本身就成了地形的分界线索，草地内部反而安静下来。
 * 判据：只要 4 邻里有任一个不是草地，这格就是「边界格」；边界格把装饰压在
 * 朝向那个邻格的一侧 2px 内。
 */
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

      // 找第一个「非草地」的邻格方向；没有就是草地内部。
      const inner = nearestNonGrassSide(grid, x, y);
      let jx: number;
      let jy: number;
      if (inner === null) {
        // 草地内部：落在格中央偏上一带，避开边线。
        jx = px + 5 + (hash % 6);
        jy = py + 5 + ((hash >> 3) % 6);
      } else {
        // 边界格：压到朝那一侧的 2px 带里，读成「地界上长的草」。
        const inset = 1 + (hash % 2); // 1 或 2px
        jx = px + 5 + (hash % 6);
        jy = py + 6 + ((hash >> 3) % 4);
        if (inner === 'west') jx = px + inset;
        else if (inner === 'east') jx = px + TILE_PX - 2 - inset;
        else if (inner === 'north') jy = py + inset;
        else jy = py + TILE_PX - 2 - inset;
      }

      if (kind === 'tuft') {
        // 草簇用实心 soil（ΔL* 11.3）；原 grassLit α0.9 合成 #324133，对草地只有 2.6，不可见。
        g.fillStyle(WORLD.soil, 1);
        // 贴边时草簇朝外竖，指向邻格方向，强化边界线。
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
