/** 三模相机：自由平移、锁定跟随、自动导播。
 *
 * 为什么需要三种而不是一种：这是挂在桌面上陪你干活的窗口，不是游戏。
 * 你可能正在看工坊，也可能只是在打字、完全不看它。相机必须自己知道该看哪。
 *
 * 两条硬约束：
 *   1. **缩放只能取阶梯值**。连续浮点缩放会让像素块的边缘出现摩尔纹——
 *      16px 的图块被 1.13 倍缩放后，每条边落在半个像素上，看起来像糊了。
 *   2. **必须锁在世界边界内**。否则镜头会飘到世界外，露出空白背景。
 */

import Phaser from 'phaser';
import {
  DIRECTOR_DWELL_MS,
  DIRECTOR_IDLE_MS,
  ZOOM_STEPS,
  clampScroll,
  createDirectorClock,
  stepZoomIndex,
} from './camera-math.ts';

export type CameraMode = 'free' | 'follow' | 'director';

export interface CameraController {
  readonly mode: () => CameraMode;
  /** 拖拽平移：按屏幕像素位移移动镜头。 */
  panBy(dx: number, dy: number): void;
  /** 平滑移到世界坐标。 */
  panTo(x: number, y: number, durationMs?: number): void;
  /** 阶梯缩放：direction 为正放大，为负缩小。 */
  zoomStep(direction: 1 | -1): number;
  readonly zoom: () => number;
  /** 跟随某个对象（通常是角色）。传 null 回到自由模式。 */
  follow(target: Phaser.GameObjects.GameObject | null): void;
  /** 打开自动导播；`candidates` 返回当前值得看的世界坐标。 */
  enableDirector(candidates: () => readonly { x: number; y: number }[]): void;
  disableDirector(): void;
  /** 每帧调用：处理闲置计时与导播切换。 */
  update(deltaMs: number): void;
  /** 用户有任何输入时调用：重置闲置计时并退出导播。 */
  notifyInput(): void;
  /** 当前视野在世界里的矩形，给小地图画白框。 */
  viewportRect(): { x: number; y: number; width: number; height: number };
}

export function createCameraController(
  scene: Phaser.Scene,
  worldWidth: number,
  worldHeight: number,
): CameraController {
  const cam = scene.cameras.main;
  cam.setBounds(0, 0, worldWidth, worldHeight);

  // 计时逻辑交给纯函数时钟：规则只写一份，测试也只验一份。
  const clock = createDirectorClock(DIRECTOR_IDLE_MS, DIRECTOR_DWELL_MS);
  /** 跟随模式是相机自己的事，时钟只管"自由 / 导播"。 */
  let following = false;
  let candidates: (() => readonly { x: number; y: number }[]) | null = null;
  let nextCandidate = 0;

  const mode = (): CameraMode => (following ? 'follow' : clock.mode());

  let zoomIndex = Math.max(0, ZOOM_STEPS.indexOf(2));

  return {
    mode,

    panBy(dx, dy) {
      this.notifyInput();
      // 位移要除以缩放：屏幕上拖 10px，缩放到 2 倍时世界只该移动 5px。
      cam.scrollX -= dx / cam.zoom;
      cam.scrollY -= dy / cam.zoom;
    },

    panTo(x, y, durationMs = 300) {
      this.notifyInput();
      if (durationMs <= 0) {
        // 0 毫秒时 `cam.pan` 不会在这一帧生效，worldView 仍是旧值——
        // overlay 会在开局错位一整个屏幕。这里直接设中心点。
        cam.centerOn(x, y);
        return;
      }
      cam.pan(x, y, durationMs, 'Sine.easeInOut', true);
    },

    zoomStep(direction) {
      this.notifyInput();
      zoomIndex = stepZoomIndex(zoomIndex, direction);
      const next = ZOOM_STEPS[zoomIndex]!;
      cam.setZoom(next);
      // 缩放后可能露出世界外，立刻夹回来。
      const clamped = clampScroll(cam.scrollX, cam.scrollY, worldWidth, worldHeight, cam.width, cam.height, next);
      cam.scrollX = clamped.x;
      cam.scrollY = clamped.y;
      return next;
    },

    zoom: () => cam.zoom,

    follow(target) {
      this.notifyInput();
      if (target === null) {
        cam.stopFollow();
        following = false;
        return;
      }
      following = true;
      cam.startFollow(target, true, 0.08, 0.08);
    },

    enableDirector(nextCandidates) {
      // 只登记目标，不立刻接管：导播要等闲置计时到点。
      // 直接切进 director 会和用户抢镜头——他一进来就发现镜头自己在动。
      candidates = nextCandidates;
      clock.enable();
    },

    disableDirector() {
      candidates = null;
      clock.disable();
    },

    update(deltaMs) {
      const before = clock.targetIndex();
      clock.tick(deltaMs);
      if (clock.mode() !== 'director') return;
      if (clock.targetIndex() === before && nextCandidate > 0) return;

      const list = candidates?.() ?? [];
      if (list.length === 0) return;
      const point = list[nextCandidate % list.length]!;
      nextCandidate += 1;
      cam.pan(point.x, point.y, 900, 'Sine.easeInOut', true);
    },

    notifyInput() {
      following = false;
      clock.notifyInput();
    },

    viewportRect() {
      const zoom = cam.zoom;
      return {
        x: cam.scrollX,
        y: cam.scrollY,
        width: cam.width / zoom,
        height: cam.height / zoom,
      };
    },
  };
}
