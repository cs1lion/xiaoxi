import assert from "node:assert/strict";
import { createCompanionPresence } from "./companion-presence.js";

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

console.log("companion-presence tests passed");
