import assert from "node:assert/strict";
import { createCompanionPresence, resolveGlimmerAction, summarizeDiaryNotice } from "./companion-presence.js";

const events = [];
const presence = createCompanionPresence({
  onCue: (cue, prevMode) => events.push({ mode: cue.mode, prevMode, cue }),
});

// 1. Entering writing mode triggers focused writing cue
const writingCue = presence.update({ writing: true });
assert.equal(writingCue.mode, "writing");
assert.equal(writingCue.override, true);
assert.deepEqual(writingCue.gaze, { x: -0.25, y: 0.08 });
assert.equal(events.length, 1);

// 2. Duplicate update does not re-fire onCue
presence.update({ writing: true });
assert.equal(events.length, 1);

// 3. Active speech takes priority without overriding avatar speech
const speakCue = presence.update({ dialogueState: "speaking" });
assert.equal(speakCue.mode, "speaking");
assert.equal(speakCue.override, false);
assert.equal(events.length, 2);

// 4. Returning to idle dialogue restores writing mode
const backToWriting = presence.update({ dialogueState: "idle" });
assert.equal(backToWriting.mode, "writing");
assert.equal(events.length, 3);

// 5. Exiting writing mode resets gaze to idle
const idleCue = presence.update({ writing: false });
assert.equal(idleCue.mode, "idle");
assert.equal(idleCue.resetGaze, true);
assert.equal(events.length, 4);

// 6. V2 task running -> completed triggers celebrate cue
presence.update({ v2Snapshot: { tasks: { t1: { status: "running" } } } });
assert.equal(events.at(-1).mode, "working");

const doneCue = presence.update({
  v2Snapshot: { tasks: { t1: { status: "completed" } } },
});
assert.equal(doneCue.mode, "celebrate");
assert.equal(doneCue.gesture, "nod");
assert.equal(doneCue.eyeExpression, "happy");
assert.ok(doneCue.bubble);
assert.deepEqual(resolveGlimmerAction(["memory:123"]), { kind: "fold", foldId: "memoryFold" });
assert.deepEqual(resolveGlimmerAction(["work:draft"]), { kind: "fold", foldId: "workFold" });
assert.deepEqual(resolveGlimmerAction([]), { kind: "chat", foldId: null });
// 7. summarizeDiaryNotice & notifyDiary with bubbleCooldownMs
assert.equal(summarizeDiaryNotice(null), "");
assert.equal(summarizeDiaryNotice("  hello   world  "), "hello world");
assert.equal(summarizeDiaryNotice({ summary: "Diary summary" }), "Diary summary");
assert.equal(summarizeDiaryNotice({ text: "Diary text" }), "Diary text");
assert.equal(summarizeDiaryNotice({ content: "Diary content" }), "Diary content");
assert.equal(summarizeDiaryNotice({ title: "Diary title" }), "Diary title");
assert.equal(summarizeDiaryNotice("a".repeat(37)), "a".repeat(35) + "\u2026");
let nowMs = 100000;
const diaryCues = [];
const cooldownPresence = createCompanionPresence({ bubbleCooldownMs: 30000, now: () => nowMs, onCue: (cue) => diaryCues.push(cue) });
assert.equal(cooldownPresence.notifyDiary(""), null);
const firstDiaryCue = cooldownPresence.notifyDiary({ summary: "First diary note" });
assert.equal(firstDiaryCue.bubble, "First diary note");
assert.equal(firstDiaryCue.gesture, "nod");
assert.equal(firstDiaryCue.eyeExpression, "happy");
assert.equal(diaryCues.length, 1);
nowMs += 10000;
assert.equal(cooldownPresence.notifyDiary({ summary: "Within cooldown" }), null);
cooldownPresence.update({ v2Snapshot: { tasks: { t1: { status: "running" } } } });
const suppressedDoneCue = cooldownPresence.update({ v2Snapshot: { tasks: { t1: { status: "completed" } } } });
assert.equal(suppressedDoneCue.bubble, null);
nowMs += 20000;
cooldownPresence.update({ dialogueState: "speaking" });
const secondDiaryCue = cooldownPresence.notifyDiary({ title: "After cooldown" });
assert.equal(secondDiaryCue.bubble, "After cooldown");
assert.equal(secondDiaryCue.eyeExpression, "warm");

console.log("companion-presence tests passed");
