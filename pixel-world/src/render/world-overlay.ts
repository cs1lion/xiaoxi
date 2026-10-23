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

/** 从 stage 的计算样式取**真实**缩放（transform 矩阵的 x 缩放分量）。 */
function readStageScale(stage: HTMLElement): number {
  const transform = getComputedStyle(stage).transform;
  if (transform === '' || transform === 'none') return 1;
  const matched = /matrix\(([^)]+)\)/.exec(transform);
  if (matched === null) return 1;
  const scale = Number.parseFloat(matched[1].split(',')[0] ?? '');
  return Number.isFinite(scale) && scale > 0 ? scale : 1;
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
    // 缩放必须取 transform 矩阵的真实值，**不能**用 stageRect.width / stage.offsetWidth 反推：
    // offsetWidth 是整数取整值（实测 962），而 stageRect.width 是小数（961.7778），
    // 两者相除得到 0.99977 这种假缩放，把 overlay 宽度写成 960.215px 而不是 960px。
    // 亚像素偏差会随 stage 缩放放大（100% 0.215px → 200% 0.431px → 400% 0.861px），
    // 到 400% 就超出 overlay 贴合 canvas 的 0.5px 容差（S-L544 实测 contained=false）。
    // 矩阵在 stage 未被缩放时是 none，此时取 1，正好也是正确值。
    const rootScale = readStageScale(stage);
    // overlay 与 canvas 完全同框：大小/位置每帧按真实 rect 同步，避免边框/缩放误差。
    root.style.left = `${(canvasRect.left - stageRect.left) / rootScale}px`;
    root.style.top = `${(canvasRect.top - stageRect.top) / rootScale}px`;
    root.style.width = `${canvasRect.width / rootScale}px`;
    root.style.height = `${canvasRect.height / rootScale}px`;
    const camera = scene.cameras.main;
    const viewport: OverlayViewport = {
      rect: { left: 0, top: 0, width: canvasRect.width, height: canvasRect.height },
      // gameWidth 必须是**画布的逻辑像素**（480×320），不是显示像素（960×640）。
      // 传错的话 rect/gameWidth 恒为 1，overlay 会按世界像素直接摆放，
      // 在 2 倍显示下正好偏一半——实测偏 486px。
      gameWidth: canvas.width,
      gameHeight: canvas.height,
      // 用 scrollX/Y 而不是 worldView：worldView 由相机的 preRender 更新，
      // 而场景 update 在 preRender 之前跑，拿到的会是上一帧的值——开局第一帧
      // 就是 (0,0)，overlay 会整整错开一个屏幕。scroll 是即时值。
      cameraWorldX: camera.scrollX,
      cameraWorldY: camera.scrollY,
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
