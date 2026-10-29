/* showCompanionResult 让位契约单测（零 DOM）。
 * 带确认门(confirmation)或回执(receipts)的助手结果必须由 showAssistantResult 渲染——
 * 「确认执行/不执行」按钮只在那里。这里若把这类结果吞掉，用户永远看不到确认按钮，
 * 有副作用的动作就无法被批准。钉住这条不变量，防止重排/删掉早退时静默回归。 */
import test from "node:test";
import assert from "node:assert/strict";
import { defersToAssistantGate, showCompanionResult } from "./companion.js";

const confirmResult = (over = {}) => ({
  text: "要执行这个吗？", run_id: "r1",
  confirmation: { id: "c1", tool: "add_task", arguments: { title: "买菜" } }, ...over });

test("defersToAssistantGate 只认确认门与回执", () => {
  assert.equal(defersToAssistantGate(confirmResult()), true);
  assert.equal(defersToAssistantGate({ receipts: [{ text: "{}" }] }), true);
  assert.equal(defersToAssistantGate({ receipts: [] }), false, "空回执不是回执");
  assert.equal(defersToAssistantGate({ companion: { thread: { id: "t1" } } }), false, "共同事项结果不让位");
  assert.equal(defersToAssistantGate({ proposal: { id: "p1" } }), false, "候选预览不让位");
  assert.equal(defersToAssistantGate(null), false, "畸形输入不抛也不误让位");
  assert.equal(defersToAssistantGate(undefined), false);
  assert.equal(defersToAssistantGate({}), false);
});

test("带确认门的结果必须让位给助手确认门", () => {
  // 安全关键路径：吞掉它用户就看不到「确认执行/不执行」。
  const events = [];
  globalThis.window = { dispatchEvent: (event) => events.push(event) };
  assert.equal(showCompanionResult(confirmResult()), false);
  assert.equal(events.length, 0, "纯确认门结果不该触发共同事项面板刷新");
  assert.equal(showCompanionResult(confirmResult({ companion: { thread: { id: "t1" } } })), false);
  assert.equal(events.length, 1, "带 thread 的确认门结果要通知面板");
  assert.equal(events[0].type, "companion-updated");
});

test("带回执的结果同样让位，并通知共同事项面板", () => {
  const events = [];
  globalThis.window = { dispatchEvent: (event) => events.push(event) };
  assert.equal(showCompanionResult({ text: "做完了", receipts: [{ text: '{"ok":true}' }] }), false);
  // 钉住让位分支里「回执要通知面板」的那一半：否则回执结果不会刷新共同事项视图。
  assert.equal(events.length, 1, "回执结果要通知共同事项面板");
  assert.equal(events[0].type, "companion-updated");
});
