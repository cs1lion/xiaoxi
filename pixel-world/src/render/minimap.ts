/** 雷达小地图：一块俯瞰全图的缩略图 + 视野白框 + 点击跳转。
 *
 * 实现选择：**独立 Canvas 2D，不用 Phaser 副相机**。
 *
 * 副相机在 Phaser 里要处理 ignore 列表——而现在的渲染对象是零散的 Graphics，
 * 没有分组，维护 ignore 很容易漏掉新加的东西，漏了就会让小地图里出现气泡、烟雾。
 * 独立画布从零开始画，只画我们想画的四样：地形轮廓、建筑、角色、视野框。
 * 代价是它得自己跟着世界状态走，但那本来就只有几十个点。
 */

import type { WorldSnapshot } from '../world/types.ts';
import type { WorldGrid } from '../world/grid.ts';
import { TERRAIN_COLORS, WORLD } from '../world/palette-data.ts';

export interface MinimapPoint {
  readonly x: number;
  readonly y: number;
}

export interface MinimapView {
  /** 重画地形底图。世界地形不变，只在开局与扩建后调一次。 */
  paintTerrain(grid: WorldGrid): void;
  /** 每帧更新：角色、建筑、视野框。 */
  update(snapshot: WorldSnapshot, viewport: { x: number; y: number; width: number; height: number }): void;
  /** 把画布坐标换成世界格坐标；点在小地图外时返回 null。 */
  toWorld(clientX: number, clientY: number): MinimapPoint | null;
  readonly canvas: HTMLCanvasElement;
}

export function createMinimapView(
  host: HTMLElement,
  worldWidth: number,
  worldHeight: number,
): MinimapView {
  const canvas = document.createElement('canvas');
  // 小地图自己定一个合适的尺寸：宽 160，按世界长宽比算高。
  const width = 160;
  const height = Math.round((width * worldHeight) / worldWidth);
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  canvas.style.imageRendering = 'pixelated';
  canvas.setAttribute('aria-label', '世界小地图');
  host.replaceChildren(canvas);

  const ctx = canvas.getContext('2d');
  if (ctx === null) throw new Error('小地图拿不到 2D 上下文');

  /** 地形底图先画到离屏画布，之后每帧只贴一次，不重画每格。 */
  const terrain = document.createElement('canvas');
  terrain.width = width;
  terrain.height = height;
  const terrainCtx = terrain.getContext('2d');

  const scaleX = width / worldWidth;
  const scaleY = height / worldHeight;

  return {
    canvas,

    paintTerrain(grid) {
      if (terrainCtx === null) return;
      terrainCtx.fillStyle = hex(WORLD.ink);
      terrainCtx.fillRect(0, 0, width, height);
      // 一格在缩略图上可能不到 1px；按格填会糊成一片，所以按「每格至少 1px」放大画。
      const cellW = Math.max(1, width / grid.width);
      const cellH = Math.max(1, height / grid.height);
      for (let y = 0; y < grid.height; y += 1) {
        for (let x = 0; x < grid.width; x += 1) {
          const cell = grid.at({ x, y });
          if (cell === undefined) continue;
          terrainCtx.fillStyle = hex(TERRAIN_COLORS[cell.terrain]);
          terrainCtx.fillRect(x * cellW, y * cellH, Math.ceil(cellW), Math.ceil(cellH));
        }
      }
    },

    update(snapshot, viewport) {
      ctx.drawImage(terrain, 0, 0);

      // 建筑：亮蓝块，一眼能看出镇子在哪。
      ctx.fillStyle = hex(WORLD.water);
      for (const building of snapshot.buildings) {
        ctx.fillRect(
          building.origin.x * scaleX,
          building.origin.y * scaleY,
          Math.max(2, building.footprint.w * scaleX),
          Math.max(2, building.footprint.h * scaleY),
        );
      }

      // 摆件：暖色小点，读成「这里有点什么」。
      ctx.fillStyle = hex(WORLD.amber);
      for (const prop of snapshot.props) {
        ctx.fillRect(prop.at.x * scaleX, prop.at.y * scaleY, 2, 2);
      }

      // 角色：亮黄点，比摆件更亮更实。
      ctx.fillStyle = hex(WORLD.warmLight);
      for (const villager of snapshot.villagers) {
        ctx.fillRect(villager.pos.x * scaleX - 1, villager.pos.y * scaleY - 1, 3, 3);
      }

      // 视野白框：让玩家知道主画面在看哪。
      ctx.strokeStyle = hex(WORLD.pale);
      ctx.lineWidth = 1;
      ctx.strokeRect(
        viewport.x * scaleX + 0.5,
        viewport.y * scaleY + 0.5,
        Math.max(2, viewport.width * scaleX),
        Math.max(2, viewport.height * scaleY),
      );
    },

    toWorld(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const localX = clientX - rect.left;
      const localY = clientY - rect.top;
      if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) return null;
      // 用 CSS 尺寸换算，而不是 canvas 的像素尺寸：两者可能因缩放不同。
      return {
        x: (localX / rect.width) * worldWidth,
        y: (localY / rect.height) * worldHeight,
      };
    },
  };
}

function hex(color: number): string {
  return `#${color.toString(16).padStart(6, '0')}`;
}
