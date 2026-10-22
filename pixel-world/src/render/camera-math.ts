/** 相机的纯数学：缩放档位与边界夹取。不 import Phaser，可离线测。
 *
 * 抽出来的理由：这些是"会不会出现像素撕裂""镜头会不会飘出世界"的判定，
 * 必须能脱离浏览器反复验算；塞进场景里就只能靠肉眼。
 */

/** 允许的缩放档位。全是整数或半整数，保证像素对齐。 */
export const ZOOM_STEPS: readonly number[] = [1, 1.5, 2, 3];

/** 自动导播：多久没碰鼠标就接管。 */
export const DIRECTOR_IDLE_MS = 15_000;
/** 导播每次停留多久再切下一个目标。 */
export const DIRECTOR_DWELL_MS = 12_000;

/** 把档位索引夹在合法范围内。 */
export function stepZoomIndex(index: number, direction: 1 | -1): number {
  return Math.max(0, Math.min(ZOOM_STEPS.length - 1, index + direction));
}

export function zoomAtStep(index: number): number {
  return ZOOM_STEPS[Math.max(0, Math.min(ZOOM_STEPS.length - 1, index))]!;
}

/**
 * 夹取镜头滚动值。
 *
 * `viewportWidth/Height` 是**画布**尺寸（逻辑像素），缩放后覆盖的世界范围是
 * 它除以 zoom。世界比视野小时贴左上，不出现负偏移——否则会看到世界外的空白。
 */
export function clampScroll(
  x: number,
  y: number,
  worldWidth: number,
  worldHeight: number,
  viewportWidth: number,
  viewportHeight: number,
  zoom: number,
): { x: number; y: number } {
  const spanX = viewportWidth / zoom;
  const spanY = viewportHeight / zoom;
  const maxX = Math.max(0, worldWidth - spanX);
  const maxY = Math.max(0, worldHeight - spanY);
  return {
    x: Math.max(0, Math.min(maxX, x)),
    y: Math.max(0, Math.min(maxY, y)),
  };
}

export type DirectorMode = 'free' | 'director';

/**
 * 导播计时：闲置多久接管、接管后多久换一个目标。
 *
 * 抽成纯逻辑是因为这些规则最容易写错，而它们又必须脱离浏览器反复验：
 * 「刚进来就接管」和「用户一动还抢镜头」都是很烦人的行为。
 */
export function createDirectorClock(idleMs: number, dwellMs: number): {
  mode(): DirectorMode;
  enable(): void;
  disable(): void;
  notifyInput(): void;
  tick(deltaMs: number): void;
  targetIndex(): number;
} {
  let registered = false;
  let mode: DirectorMode = 'free';
  let idle = 0;
  let dwell = 0;
  let index = 0;

  return {
    mode: () => mode,
    enable() {
      registered = true;
      idle = 0;
      if (mode === 'director') dwell = dwellMs;
    },
    disable() {
      registered = false;
      if (mode === 'director') mode = 'free';
    },
    notifyInput() {
      idle = 0;
      if (mode === 'director') mode = 'free';
    },
    tick(deltaMs) {
      if (mode === 'director') {
        dwell += deltaMs;
        if (dwell >= dwellMs) {
          dwell = 0;
          index += 1;
        }
        return;
      }
      if (!registered) return;
      idle += deltaMs;
      if (idle >= idleMs) {
        mode = 'director';
        dwell = dwellMs; // 进来立刻切一次
        idle = 0;
      }
    },
    targetIndex: () => index,
  };
}
