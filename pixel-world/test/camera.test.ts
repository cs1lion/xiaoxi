import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DIRECTOR_DWELL_MS,
  DIRECTOR_IDLE_MS,
  ZOOM_STEPS,
  zoomAtStep,
  stepZoomIndex,
  clampScroll,
} from '../src/render/camera-math.ts';

// ------------------------------------------------------------------ 缩放档位

test('缩放档位全是整数或半整数，保证像素对齐', () => {
  for (const step of ZOOM_STEPS) {
    assert.ok(step > 0, '缩放必须为正');
    assert.equal(step * 2, Math.round(step * 2), `${step} 必须是 0.5 的整数倍`);
  }
});

test('档位单调递增，没有重复', () => {
  for (let i = 1; i < ZOOM_STEPS.length; i += 1) {
    assert.ok(ZOOM_STEPS[i]! > ZOOM_STEPS[i - 1]!, '档位必须严格递增');
  }
});

test('档位索引在两端夹住，不会越界', () => {
  assert.equal(stepZoomIndex(0, -1), 0, '已经最小就不能再缩');
  assert.equal(stepZoomIndex(ZOOM_STEPS.length - 1, 1), ZOOM_STEPS.length - 1);
});

test('放大缩小是可逆的：进一档再退一档回到原值', () => {
  for (let i = 0; i < ZOOM_STEPS.length; i += 1) {
    const up = stepZoomIndex(i, 1);
    const back = stepZoomIndex(up, -1);
    if (up === i) continue; // 已在顶
    assert.equal(back, i, `从 ${i} 进一档再退应回到 ${i}`);
  }
});

test('zoomAtStep 返回对应档位值', () => {
  assert.equal(zoomAtStep(0), ZOOM_STEPS[0]);
  assert.equal(zoomAtStep(ZOOM_STEPS.length - 1), ZOOM_STEPS[ZOOM_STEPS.length - 1]);
});

// ------------------------------------------------------------------ 边界夹取

test('镜头不会滚出世界左上角', () => {
  assert.deepEqual(clampScroll(-50, -30, 1280, 960, 480, 320, 2), { x: 0, y: 0 });
});

test('镜头不会滚出世界右下角', () => {
  const result = clampScroll(9999, 9999, 1280, 960, 480, 320, 2);
  // 视野 480×320 在 2 倍缩放下覆盖 240×160 世界像素。
  assert.equal(result.x, 1280 - 240);
  assert.equal(result.y, 960 - 160);
});

test('世界比视野还小时，镜头贴住左上，不会出现负偏移', () => {
  const result = clampScroll(100, 100, 200, 150, 480, 320, 2);
  assert.equal(result.x, 0);
  assert.equal(result.y, 0);
});

test('合法范围内的滚动值原样保留', () => {
  assert.deepEqual(clampScroll(300, 200, 1280, 960, 480, 320, 2), { x: 300, y: 200 });
});

// ------------------------------------------------------------------ 导播节奏

test('导播在闲置一段时间后才接管，且停留时间明确', () => {
  assert.ok(DIRECTOR_IDLE_MS >= 5000, '不该刚停手就切镜头');
  assert.ok(DIRECTOR_DWELL_MS >= 5000, '每个镜头至少停几秒才看得清');
});
