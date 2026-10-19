/** 世界内动态中文的 DOM overlay（D5-A）：名字与气泡跟随 canvas 实际位置。
 *
 * 只在渲染层读快照：不改变仿真、不写存档/台账。静态文字已改图标，不在这里。
 * 投影数学在 labels.ts（纯函数，可单测）；本文件只负责创建/摆放 DOM。
 */

import type Phaser from 'phaser';
import type { WorldSnapshot } from '../world/types.ts';
import {
  projectWorldToOverlay,
  snapToDevicePixel,
  villagerLabelAnchors,
  type OverlayViewport,
} from './labels.ts';

interface OverlayEntry {
  readonly name: HTMLDivElement;
  readonly bubble: HTMLDivElement;
}

export interface WorldOverlay {
  readonly sync: (snapshot: WorldSnapshot) => void;
  readonly dispose: () => void;
}

export function createWorldOverlay(scene: Phaser.Scene, root: HTMLElement): WorldOverlay {
  const entries = new Map<string, OverlayEntry>();
  let lastSnapshot: WorldSnapshot | null = null;

  const ensure = (id: string): OverlayEntry => {
    const existing = entries.get(id);
    if (existing !== undefined) return existing;
    const name = document.createElement('div');
    name.className = 'overlay-name';
    const bubble = document.createElement('div');
    bubble.className = 'overlay-bubble';
    root.append(name, bubble);
    const entry = { name, bubble };
    entries.set(id, entry);
    return entry;
  };

  const place = (
    node: HTMLElement,
    point: { x: number; y: number } | null,
    rootScale: number,
    dpr: number,
  ): void => {
    if (point === null) {
      node.style.display = 'none';
      return;
    }
    // overlay 被同步成与 canvas 同尺寸（可能处在被 CSS transform 缩放的 stage 里），
    // 所以先把“缩放后的 CSS 像素”折回 overlay 本地坐标。
    const left = snapToDevicePixel(point.x, dpr) / rootScale;
    const top = snapToDevicePixel(point.y, dpr) / rootScale;
    node.style.display = 'block';
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  };

  const sync = (snapshot: WorldSnapshot): void => {
    lastSnapshot = snapshot;
    const canvas = scene.game.canvas as HTMLCanvasElement;
    const canvasRect = canvas.getBoundingClientRect();
    const stage = root.parentElement ?? root;
    const stageRect = stage.getBoundingClientRect();
    const rootScale = stage.offsetWidth > 0 ? stageRect.width / stage.offsetWidth : 1;
    // overlay 与 canvas 完全同框：大小/位置每帧按真实 rect 同步，避免边框/缩放误差。
    root.style.left = `${(canvasRect.left - stageRect.left) / rootScale}px`;
    root.style.top = `${(canvasRect.top - stageRect.top) / rootScale}px`;
    root.style.width = `${canvasRect.width / rootScale}px`;
    root.style.height = `${canvasRect.height / rootScale}px`;
    const camera = scene.cameras.main;
    const viewport: OverlayViewport = {
      rect: { left: 0, top: 0, width: canvasRect.width, height: canvasRect.height },
      gameWidth: camera.width,
      gameHeight: camera.height,
      cameraWorldX: camera.worldView.x,
      cameraWorldY: camera.worldView.y,
      cameraZoom: camera.zoom,
    };
    const dpr = window.devicePixelRatio || 1;
    const seen = new Set<string>();
    for (const villager of snapshot.villagers) {
      seen.add(villager.id);
      const entry = ensure(villager.id);
      entry.name.textContent = villager.name;
      entry.bubble.textContent = villager.bubble ?? '';
      const anchors = villagerLabelAnchors(villager, viewport);
      place(entry.name, anchors.name, rootScale, dpr);
      place(entry.bubble, anchors.bubble, rootScale, dpr);
    }
    for (const [id, entry] of entries) {
      if (seen.has(id)) continue;
      entry.name.remove();
      entry.bubble.remove();
      entries.delete(id);
    }
  };

  const onResize = (): void => {
    if (lastSnapshot !== null) sync(lastSnapshot);
  };
  window.addEventListener('resize', onResize);

  return {
    sync,
    dispose: () => {
      window.removeEventListener('resize', onResize);
      root.replaceChildren();
      entries.clear();
    },
  };
}

export { projectWorldToOverlay };
