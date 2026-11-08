/* 记忆面板纯函数单测（issue #28 何时说过 / 停用）：node web/memory-panel.test.mjs
 *
 * 覆盖 memory-panel.js 的每个导出：停用标签、更新时间（只在真的改过时出字）、
 * 原话消息引用、停用/启用操作形状。零 DOM、零请求——直接喂 Memory.to_dict()
 * 形状的字面量。期望串逐字锁定渲染文案，全角括号一起锁住。 */
import test from "node:test";
import assert from "node:assert/strict";

import {
  DISABLED_TAG,
  memoryDisabledTag,
  memoryUpdatedNote,
  memoryEvidenceNote,
  memoryDisableControl,
  memoryForgetControl,
  memoryForgetNote,
  memoryForgetConfirm,
  memoryForgetKeyControl,
  memoryForgetKeyConfirm,
  memoryHistoryRows,
  DELETE_IRREVERSIBLE,
} from "./memory-panel.js";

const CREATED = Date.UTC(2026, 9, 1) / 1000;
const UPDATED = Date.UTC(2026, 9, 24) / 1000;

test("停用标签：只有显式 disabled === true 才出字", () => {
  assert.equal(memoryDisabledTag({ disabled: true }), DISABLED_TAG);
  assert.equal(memoryDisabledTag({ disabled: false }), "");
  assert.equal(memoryDisabledTag({}), "");
  assert.equal(memoryDisabledTag(null), "");
});

test("更新时间：updated_at 晚于 created_at 才出「更新于」", () => {
  assert.equal(memoryUpdatedNote({ created_at: CREATED, updated_at: UPDATED }),
    "更新于 2026-10-24（UTC）");
});

test("更新时间：未编辑过（相等）不出字，避免与「记于」重复", () => {
  assert.equal(memoryUpdatedNote({ created_at: UPDATED, updated_at: UPDATED }), "");
});

test("更新时间：倒退或缺失不出字，不编造日期", () => {
  assert.equal(memoryUpdatedNote({ created_at: UPDATED, updated_at: CREATED }), "");
  assert.equal(memoryUpdatedNote({ created_at: CREATED }), "");
  assert.equal(memoryUpdatedNote({ created_at: "x", updated_at: "y" }), "");
  assert.equal(memoryUpdatedNote(null), "");
});

test("原话引用：linked 且有消息 id 才出「原话出自消息 #N」", () => {
  assert.equal(memoryEvidenceNote(
    { evidence_state: "linked", evidence_message_id: 42 }), "原话出自消息 #42");
  assert.equal(memoryEvidenceNote({ evidence_state: "linked", evidence_message_id: 0 }), "");
  assert.equal(memoryEvidenceNote({ evidence_state: "linked" }), "");
  assert.equal(memoryEvidenceNote({ evidence_state: "manual", evidence_message_id: 42 }), "");
  assert.equal(memoryEvidenceNote(null), "");
});

test("停用操作：active 未停用 → 停用（disabled:true）", () => {
  const control = memoryDisableControl({ status: "active", disabled: false });
  assert.deepEqual(control, {
    label: "停用",
    body: { disabled: true },
    done: "已停用，检索不再使用这条记忆。",
  });
});

test("停用操作：active 已停用 → 启用（disabled:false），缺字段按未停用处理", () => {
  assert.deepEqual(memoryDisableControl({ status: "active", disabled: true }), {
    label: "启用",
    body: { disabled: false },
    done: "已恢复，检索会重新考虑这条记忆。",
  });
  assert.equal(memoryDisableControl({ status: "active" }).label, "停用");
});

test("停用操作：非 active 状态不提供开关——它们本来就不进装配", () => {
  assert.equal(memoryDisableControl({ status: "superseded", disabled: false }), null);
  assert.equal(memoryDisableControl({ status: "conflict", disabled: false }), null);
  assert.equal(memoryDisableControl({ status: "done", disabled: false }), null);
  assert.equal(memoryDisableControl(null), null);
});

test("忘掉操作：只有 active 行提供——DELETE 删整条身份链，含当前生效行", () => {
  assert.deepEqual(memoryForgetControl({ status: "active", id: 7, value: "用户在乙公司工作" }), {
    label: "忘掉",
    done: "已忘掉这条记忆。",
  });
});

test("忘掉操作：非 active 行不提供按钮——在待核对/历史行上点它会误删生效记忆", () => {
  for (const status of ["superseded", "conflict", "done"]) {
    assert.equal(memoryForgetControl({ status, id: 7 }), null, status);
  }
  assert.equal(memoryForgetControl({ id: 7 }), null, "缺 status 视为非 active");
  assert.equal(memoryForgetControl(null), null);
});

test("忘掉说明：非 active 行给一句解释，别让按钮消失像 bug", () => {
  for (const status of ["superseded", "conflict", "done"]) {
    assert.ok(memoryForgetNote({ status }).length > 0, status);
    assert.match(memoryForgetNote({ status }), /不影响当前生效的记忆/);
  }
  assert.equal(memoryForgetNote({ status: "active" }), "", "active 行不出解释");
  assert.equal(memoryForgetNote(null), "");
});

test("硬删除确认文案：必须写明删除后不可恢复，且带上是哪条记忆", () => {
  assert.match(DELETE_IRREVERSIBLE, /不可恢复/);
  assert.equal(memoryForgetConfirm({ value: "用户在乙公司工作" }),
    "删除「用户在乙公司工作」的全部版本？删除后不可恢复。");
  assert.match(memoryForgetConfirm({ value: "  " }), /这条记忆.*不可恢复/);
  assert.match(memoryForgetConfirm(null), /不可恢复/);
});

test("忘掉整条事实：非 active 且有 key 才给入口；active 已有忘掉不重复", () => {
  assert.deepEqual(
    memoryForgetKeyControl({ status: "superseded", key: "employment:user" }),
    {
      label: "忘掉整条事实",
      key: "employment:user",
      done: "这条事实的全部版本已删除，且不可恢复。",
    });
  assert.equal(memoryForgetKeyControl({ status: "active", key: "employment:user" }), null);
  assert.equal(memoryForgetKeyControl({ status: "conflict", key: "  " }), null, "空 key 不入口");
  assert.equal(memoryForgetKeyControl({ status: "conflict" }), null);
  assert.equal(memoryForgetKeyControl(null), null);
});

test("忘掉整条事实确认文案：说清会连带当前生效行，且不可恢复", () => {
  assert.equal(memoryForgetKeyConfirm({ key: "employment:user" }),
    "删除「employment:user」的所有版本（含当前生效的一行）？删除后不可恢复。");
  assert.match(memoryForgetKeyConfirm({}), /这条事实.*不可恢复/);
});

test("历史时间线：按 key 返回的 items 只留状态/值/日期，缺日期不编", () => {
  assert.deepEqual(memoryHistoryRows([
    { status: "active", value: "用户在乙公司工作", created_at: CREATED, id: 3, kind: "fact" },
    { status: "superseded", value: "用户在甲公司工作", created_at: CREATED },
    { status: "conflict", value: "用户待业" },
    { status: null, value: 42 },
  ]), [
    { status: "active", value: "用户在乙公司工作", created: "2026-10-01" },
    { status: "superseded", value: "用户在甲公司工作", created: "2026-10-01" },
    { status: "conflict", value: "用户待业", created: "" },
    { status: "", value: "", created: "" },
  ]);
  assert.deepEqual(memoryHistoryRows(null), []);
  assert.deepEqual(memoryHistoryRows({ items: [] }), []);
});
