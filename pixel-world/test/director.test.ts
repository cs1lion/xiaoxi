import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  DIRECTOR_DWELL_MS,
  DIRECTOR_IDLE_MS,
  createDirectorClock,
} from '../src/render/camera-math.ts';

/** 导播的计时逻辑单独抽出来，才能脱离浏览器验。 */
const clock = () => createDirectorClock(DIRECTOR_IDLE_MS, DIRECTOR_DWELL_MS);

test('刚进来不接管：导播要等用户闲置', () => {
  const c = clock();
  c.enable();
  c.tick(1000);
  assert.equal(c.mode(), 'free', '1 秒不该接管');
});

test('闲置够久才接管', () => {
  const c = clock();
  c.enable();
  c.tick(DIRECTOR_IDLE_MS - 100);
  assert.equal(c.mode(), 'free');
  c.tick(200);
  assert.equal(c.mode(), 'director');
});

test('用户一操作就退出导播', () => {
  const c = clock();
  c.enable();
  c.tick(DIRECTOR_IDLE_MS + 100);
  assert.equal(c.mode(), 'director');
  c.notifyInput();
  assert.equal(c.mode(), 'free');
});

test('退出后重新计时：不会立刻又接管', () => {
  const c = clock();
  c.enable();
  c.tick(DIRECTOR_IDLE_MS + 100);
  c.notifyInput();
  c.tick(1000);
  assert.equal(c.mode(), 'free', '重新计时后才接管');
});

test('导播每停留一段时间换一次目标', () => {
  const c = clock();
  c.enable();
  c.tick(DIRECTOR_IDLE_MS + 100);
  assert.equal(c.mode(), 'director');
  const before = c.targetIndex();
  c.tick(DIRECTOR_DWELL_MS);
  assert.notEqual(c.targetIndex(), before, '停留到点应换目标');
});

test('没登记目标时永远不接管', () => {
  const c = clock();
  c.tick(DIRECTOR_IDLE_MS * 3);
  assert.equal(c.mode(), 'free', '没有候选就不该自己动');
});

test('关掉导播后不再接管', () => {
  const c = clock();
  c.enable();
  c.disable();
  c.tick(DIRECTOR_IDLE_MS * 3);
  assert.equal(c.mode(), 'free');
});
