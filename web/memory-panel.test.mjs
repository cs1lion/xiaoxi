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
