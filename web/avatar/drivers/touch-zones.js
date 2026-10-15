/* 触控热区（打磨轮 A-4）：形象自己的 Pointer Events，自包含在 avatar 层。
 *
 * 为什么挂在 Avatar 而不是页面：app.js / index.html 归别的角色，而这个互动
 * 只关心"点到身体的哪个部位"。容器上只落两个内联样式（pointer-events 打开、
 * touch-action 放行双指缩放），不碰任何共享 CSS，也不新增页面级监听。
 *
 * 手势语义（汇报里同步给 Role 3）：
 *   - 轻点（按下抬起、位移小于 slop、时长小于 tapMs）= 区域互动
 *   - 单指/单鼠标拖动 = 凝视，与页面级 mousemove 跟随同一个目标
 *   - 双指 = 不拦截：touch-action 只允许 pinch-zoom，页面缩放照常；
 *     第二根手指落下时取消在途的轻点判定
 *   - pointercancel / lostpointercapture = 放弃这一手，不触发反应
 *
 * 命中判定交给注入的 region()：SVG 备选造型用视图盒归一化矩形，VRM 用骨骼
 * 投影（render/vrm.js 的 hitRegion），屏幕空间才跟得上"手真的在那"。 */

const DEFAULTS = { slop: 10, tapMs: 700 };

export class TouchZones {
  constructor(container, { region, onRegion, onDrag, slop = DEFAULTS.slop, tapMs = DEFAULTS.tapMs } = {}) {
    if (!container?.addEventListener) throw new TypeError('TouchZones requires a DOM container');
    this.container = container;
    this.region = typeof region === 'function' ? region : () => null;
    this.onRegion = typeof onRegion === 'function' ? onRegion : () => {};
    this.onDrag = typeof onDrag === 'function' ? onDrag : () => {};
    this.slop = Math.max(0, Number(slop) || DEFAULTS.slop);
    this.tapMs = Math.max(120, Number(tapMs) || DEFAULTS.tapMs);
    this.pointers = new Map();
    // .avatar-stage 默认 pointer-events:none，事件根本到不了容器；这里只改
    // 自己容器的内联样式。touch-action 留 pinch-zoom：单指拖拽交给凝视，
    // 双指缩放绝不拦截。
    container.style.pointerEvents = 'auto';
    container.style.touchAction = 'pinch-zoom';
    this._onDown = event => this._down(event);
    this._onMove = event => this._move(event);
    this._onUp = event => this._up(event);
    this._onCancel = event => this._cancel(event);
    container.addEventListener('pointerdown', this._onDown);
    container.addEventListener('pointermove', this._onMove);
    container.addEventListener('pointerup', this._onUp);
    container.addEventListener('pointercancel', this._onCancel);
    container.addEventListener('lostpointercapture', this._onCancel);
  }

  _normalize(event) {
    const rect = this.container.getBoundingClientRect?.();
    if (!rect || !rect.width || !rect.height) return { x: 0, y: 0 };
    return { x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height };
  }

  _down(event) {
    if (!Number.isFinite(event.clientX)) return;
    // 第二根手指：这是页面缩放的手势，不是在戳她。
    if (this.pointers.size >= 1) {
      this.pointers.clear();
      try { this.container.releasePointerCapture?.(event.pointerId); } catch (_) {}
      return;
    }
    const point = this._normalize(event);
    this.pointers.set(event.pointerId, {
      x0: event.clientX, y0: event.clientY, at: event.timeStamp || 0,
      dragging: false, region: this.region(event.clientX, event.clientY, point.x, point.y),
    });
    // 捕获之后拖到舞台外面也能继续凝视；轻点判定不受影响。
    try { this.container.setPointerCapture?.(event.pointerId); } catch (_) {}
  }

  _move(event) {
    const state = this.pointers.get(event.pointerId);
    if (!state || !Number.isFinite(event.clientX)) return;
    const moved = Math.hypot(event.clientX - state.x0, event.clientY - state.y0);
    if (!state.dragging && moved > this.slop) state.dragging = true;
    if (state.dragging) {
      const point = this._normalize(event);
      this.onDrag(point.x, point.y);
    }
  }

  _up(event) {
    const state = this.pointers.get(event.pointerId);
    this.pointers.delete(event.pointerId);
    if (!state || state.dragging) return;
    const elapsed = (event.timeStamp || 0) - state.at;
    if (elapsed > this.tapMs) return;               // 长按不是轻点
    if (!state.region) return;                      // 点在空白处：不演
    const point = this._normalize(event);
    this.onRegion(state.region, point);
  }

  _cancel(event) {
    this.pointers.delete(event.pointerId);
  }

  dispose() {
    this.pointers.clear();
    this.container.removeEventListener('pointerdown', this._onDown);
    this.container.removeEventListener('pointermove', this._onMove);
    this.container.removeEventListener('pointerup', this._onUp);
    this.container.removeEventListener('pointercancel', this._onCancel);
    this.container.removeEventListener('lostpointercapture', this._onCancel);
  }
}

/* SVG 备选造型没有手部骨骼，热区按视图盒归一化矩形定义（0..1，y 向下）。
 * face 必须排在 head 前面：戳脸和摸头是两种反馈，重叠时脸赢。 */
export function svgRegionHit(rects, x, y) {
  if (!rects || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  const inside = rect => Array.isArray(rect) && x >= rect[0] && x <= rect[2] && y >= rect[1] && y <= rect[3];
  if (inside(rects.face)) return 'face';
  if (inside(rects.head)) return 'head';
  for (const rect of rects.hands || []) if (inside(rect)) return 'hand';
  return null;
}
