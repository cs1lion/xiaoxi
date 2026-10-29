/* task-card 纯函数单测（协议 §25 任务计划）：node web/task-card.test.mjs
 *
 * 只测抽取出来的判定逻辑，零 DOM、零请求——renderTaskCard 本身依赖
 * document/crypto，其可测的决策面（状态标签降级、关联提醒筛选、保存失败文案）
 * 抽成导出函数后在这里逐字锁定。 */
import test from "node:test";
import assert from "node:assert/strict";

import { taskStatusLabel, linkedTaskReminders, taskSaveError } from "./task-card.js";

test("状态标签：五个已知状态有中文，未知状态原样降级", () => {
  assert.equal(taskStatusLabel("open"), "未开始");
  assert.equal(taskStatusLabel("in_progress"), "进行中");
  assert.equal(taskStatusLabel("waiting"), "等待中");
  assert.equal(taskStatusLabel("done"), "已完成");
  assert.equal(taskStatusLabel("cancelled"), "已取消");
  // 未知/缺省不猜不吞：原样显示，让人能看出服务端来了没见过的状态。
  assert.equal(taskStatusLabel("weird_future_state"), "weird_future_state");
  assert.equal(taskStatusLabel(undefined), undefined);
});

test("关联提醒：只取同 task_id 且在生命周期内的三个状态", () => {
  const reminders = [
    { id: 1, task_id: "t1", status: "scheduled" },
    { id: 2, task_id: "t1", status: "queued" },
    { id: 3, task_id: "t1", status: "notified" },
    { id: 4, task_id: "t1", status: "cancelled" },   // 不在白名单
    { id: 5, task_id: "t2", status: "scheduled" },   // 别的任务
    { id: 6, task_id: "t1", status: "delivered" },   // 白名单外
  ];
  assert.deepEqual(linkedTaskReminders(reminders, "t1").map(r => r.id), [1, 2, 3]);
  assert.deepEqual(linkedTaskReminders(reminders, "t9"), []);
  assert.deepEqual(linkedTaskReminders([], "t1"), []);
  assert.deepEqual(linkedTaskReminders(null, "t1"), []);
});

test("保存失败：version conflict 走专属文案（服务端 store.py:237 的活契约）", () => {
  assert.equal(
    taskSaveError("task version conflict"),
    "任务已在其他窗口更新。请刷新后重试，当前输入仍保留。");
  assert.equal(taskSaveError("boom"), "未保存：boom");
  // 非字符串不炸，走通用分支。
  assert.equal(taskSaveError(undefined), "未保存：undefined");
});
