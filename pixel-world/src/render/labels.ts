/** 世界坐标 → DOM overlay 的纯投影/锚点数学（D5-A：按 canvas 实际位置统一换算）。
 *
 * 只做数学、不碰 DOM/Phaser，方便单测 100%–400%、滚动与相机缩放。
 * DOM 侧见 `world-overlay.ts`；本文件负责“同一个换算公式只写一次”。
 */

import { TILE_PX } from '../world/setup.ts';
import type { VillagerSnapshot } from '../world/types.ts';

export interface OverlayRect {
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

/** Phaser 相机与画布在某一帧的实际口径。 */
export interface OverlayViewport {
  readonly rect: OverlayRect;
  readonly gameWidth: number;
  readonly gameHeight: number;
  readonly cameraWorldX: number;
  readonly cameraWorldY: number;
  readonly cameraZoom: number;
}

export interface OverlayPoint {
  readonly x: number;
  readonly y: number;
}

/** 世界像素 → overlay 内 CSS 像素（相对 overlay 左上角）。 */
export function projectWorldToOverlay(
  worldX: number,
  worldY: number,
  viewport: OverlayViewport,
): OverlayPoint {
  const internalX = (worldX - viewport.cameraWorldX) * viewport.cameraZoom;
  const internalY = (worldY - viewport.cameraWorldY) * viewport.cameraZoom;
  return {
    x: internalX * (viewport.rect.width / viewport.gameWidth),
    y: internalY * (viewport.rect.height / viewport.gameHeight),
  };
}

/** 取整到设备像素，避免非整数 DPR 下 1px 抖动。 */
export function snapToDevicePixel(cssValue: number, dpr: number): number {
  if (!Number.isFinite(dpr) || dpr <= 0) return Math.round(cssValue);
  return Math.round(cssValue * dpr) / dpr;
}

export interface LabelAnchors {
  readonly name: OverlayPoint | null;
  readonly bubble: OverlayPoint | null;
}

/**
 * 角色名字/气泡的锚点：与旧 canvas 文本相对容器的偏移一致
 * （名字 -30、气泡 -36，单位世界像素）；隐藏规则也沿用旧实现。
 */
export function villagerLabelAnchors(
  villager: VillagerSnapshot,
  viewport: OverlayViewport,
): LabelAnchors {
  if (!villager.visible) return { name: null, bubble: null };
  const worldX = (villager.pos.x + 0.5) * TILE_PX;
  const worldY = (villager.pos.y + 1) * TILE_PX;
  const bubble = projectWorldToOverlay(worldX, worldY - 36, viewport);
  if (villager.bubble !== null) {
    return { name: null, bubble };
  }
  return { name: projectWorldToOverlay(worldX, worldY - 30, viewport), bubble: null };
}

/** 屏幕像素下的对齐误差（逻辑像素），供 smoke/QA 断言用。 */
export function alignmentError(
  actual: OverlayPoint,
  expected: OverlayPoint,
  cssPerLogical = 1,
): number {
  const dx = actual.x - expected.x;
  const dy = actual.y - expected.y;
  return Math.hypot(dx, dy) / cssPerLogical;
}
