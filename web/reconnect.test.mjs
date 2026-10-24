/* web/reconnect.js 的单测：node --test web/reconnect.test.mjs
 * （package.json 冻结期间单独跑；纳入 npm test 需要 Role 0 改 scripts.test。）
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createReconnectScheduler } from './reconnect.js';

test('指数退避：1s 起步、翻倍、15s 封顶', () => {
  const s = createReconnectScheduler();
  assert.equal(s.next(), 1000);
  assert.equal(s.next(), 2000);
  assert.equal(s.next(), 4000);
  assert.equal(s.next(), 8000);
  assert.equal(s.next(), 15000);   // 16000 被封顶
  assert.equal(s.next(), 15000);
});

test('reset 后从 baseMs 重新起退避', () => {
  const s = createReconnectScheduler();
  s.next(); s.next(); s.next();    // 走到 4s
  s.reset();
  assert.equal(s.attempt, 0);
  assert.equal(s.next(), 1000);
});

test('自定义 base/max 生效，长时间断线不会溢出', () => {
  const s = createReconnectScheduler({ baseMs: 100, maxMs: 300 });
  assert.equal(s.next(), 100);
  assert.equal(s.next(), 200);
  assert.equal(s.next(), 300);
  for (let i = 0; i < 60; i += 1) s.next();
  assert.equal(s.next(), 300);     // attempt 已夹住，指数不爆 Infinity
  assert.ok(Number.isFinite(s.next()));
});
