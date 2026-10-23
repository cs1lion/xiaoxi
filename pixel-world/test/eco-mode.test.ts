import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  BACKGROUND_FPS,
  FOREGROUND_FPS,
  targetFpsFor,
  shouldAnimateAt,
} from '../src/render/eco-mode.ts';

// ------------------------------------------------------------------ 帧率档位

test('聚焦时跑满帧，失焦时明显降频', () => {
  assert.equal(targetFpsFor(true), FOREGROUND_FPS);
  assert.equal(targetFpsFor(false), BACKGROUND_FPS);
  assert.ok(FOREGROUND_FPS >= 60, '前台应跑满');
  assert.ok(BACKGROUND_FPS <= 15, `后台应降到 15 以下，实际 ${BACKGROUND_FPS}`);
});

test('后台帧率不是 0：完全停掉会让人以为挂了', () => {
  assert.ok(BACKGROUND_FPS > 0);
});

test('降频幅度足够大：后台不超过前台的 1/4', () => {
  assert.ok(
    BACKGROUND_FPS <= FOREGROUND_FPS / 4,
    `后台 ${BACKGROUND_FPS} 相对前台 ${FOREGROUND_FPS} 降得不够`,
  );
});

// ------------------------------------------------------------------ 视野外跳过

test('视野内的对象每帧都动', () => {
  const view = { x: 0, y: 0, width: 480, height: 320 };
  assert.equal(shouldAnimateAt({ x: 100, y: 100 }, view), true);
});

test('视野外远处的对象跳过动画', () => {
  const view = { x: 0, y: 0, width: 480, height: 320 };
  assert.equal(shouldAnimateAt({ x: 1200, y: 900 }, view), false);
});

test('视野边缘留一点余量：刚出界的不该立刻停', () => {
  const view = { x: 0, y: 0, width: 480, height: 320 };
  // 稍微超出边界一点，仍应动画（避免边缘一像素就闪断）。
  assert.equal(shouldAnimateAt({ x: 490, y: 100 }, view), true);
  assert.equal(shouldAnimateAt({ x: 520, y: 100 }, view), false);
});

test('视野移动后判定跟着变', () => {
  const point = { x: 600, y: 100 };
  assert.equal(shouldAnimateAt(point, { x: 0, y: 0, width: 480, height: 320 }), false);
  assert.equal(shouldAnimateAt(point, { x: 400, y: 0, width: 480, height: 320 }), true);
});
