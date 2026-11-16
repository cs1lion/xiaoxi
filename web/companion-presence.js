const ACTIVE_TASK_STATES = new Set([
  "queued",
  "pending",
  "running",
  "executing",
  "in_progress",
  "started",
]);

const DONE_TASK_STATES = new Set([
  "done",
  "completed",
  "succeeded",
  "finished",
]);

export function summarizeV2Tasks(snapshot) {
  const rawTasks = snapshot?.tasks;
  const list = Array.isArray(rawTasks)
    ? rawTasks
    : rawTasks && typeof rawTasks === "object"
      ? Object.values(rawTasks)
      : [];

  let active = 0;
  let completed = 0;
  for (const item of list) {
    const status = String(item?.status ?? item?.state ?? "").toLowerCase();
    if (ACTIVE_TASK_STATES.has(status)) active += 1;
    else if (DONE_TASK_STATES.has(status)) completed += 1;
  }
  return { active, completed };
}

export function computePresenceCue({
  dialogueState = "idle",
  writing = false,
  activeTasks = 0,
  justCompletedTask = false,
} = {}) {
  if (
    dialogueState === "speaking" ||
    dialogueState === "listening" ||
    dialogueState === "thinking"
  ) {
    return {
      mode: dialogueState,
      override: false,
      resetGaze: false,
      eyeExpression: null,
      eyeDuration: 0,
      emotion: null,
      emotionDuration: 0,
      gesture: null,
      gaze: null,
      bubble: null,
    };
  }

  if (justCompletedTask) {
    return {
      mode: "celebrate",
      override: true,
      resetGaze: false,
      eyeExpression: "happy",
      eyeDuration: 2200,
      emotion: "开心",
      emotionDuration: 2200,
      gesture: "nod",
      gaze: null,
      bubble: "刚那件事处理好了～",
    };
  }

  if (writing) {
    return {
      mode: "writing",
      override: true,
      resetGaze: false,
      eyeExpression: "focused",
      eyeDuration: 0,
      emotion: "专注",
      emotionDuration: 0,
      gesture: null,
      gaze: { x: -0.25, y: 0.08 },
      bubble: null,
    };
  }

  if (activeTasks > 0) {
    return {
      mode: "working",
      override: true,
      resetGaze: false,
      eyeExpression: "focused",
      eyeDuration: 0,
      emotion: "认真",
      emotionDuration: 0,
      gesture: null,
      gaze: null,
      bubble: null,
    };
  }

  return {
    mode: "idle",
    override: false,
    resetGaze: true,
    eyeExpression: null,
    eyeDuration: 0,
    emotion: null,
    emotionDuration: 0,
    gesture: null,
    gaze: null,
    bubble: null,
  };
}

export function createCompanionPresence({ onCue } = {}) {
  let dialogueState = "idle";
  let writing = false;
  let activeTasks = 0;
  let completedTasks = 0;
  let lastMode = "idle";

  return {
    snapshot() {
      return { dialogueState, writing, activeTasks, completedTasks, mode: lastMode };
    },
    update(patch = {}) {
      if (typeof patch.dialogueState === "string") {
        dialogueState = patch.dialogueState;
      }
      if (typeof patch.writing === "boolean") {
        writing = patch.writing;
      }

      let justCompletedTask = false;
      if (patch.v2Snapshot !== undefined) {
        const next = summarizeV2Tasks(patch.v2Snapshot);
        if (next.completed > completedTasks && next.active === 0) {
          justCompletedTask = true;
        }
        activeTasks = next.active;
        completedTasks = next.completed;
      }

      const cue = computePresenceCue({
        dialogueState,
        writing,
        activeTasks,
        justCompletedTask,
      });

      if (cue.mode !== lastMode || justCompletedTask) {
        const prevMode = lastMode;
        lastMode = cue.mode;
        if (typeof onCue === "function") onCue(cue, prevMode);
      }
      return cue;
    },
  };
}
export function resolveGlimmerAction(sourceRefs = []) {
  const refs = Array.isArray(sourceRefs) ? sourceRefs : [];
  for (const raw of refs) {
    const ref = String(raw || "").trim().toLowerCase();
    if (
      ref === "memory" ||
      ref.startsWith("memory:") ||
      ref.startsWith("pref:") ||
      ref.startsWith("preference:") ||
      ref.startsWith("lesson:")
    ) {
      return { kind: "fold", foldId: "memoryFold" };
    }
    if (
      ref === "work" ||
      ref.startsWith("work:") ||
      ref.startsWith("task:") ||
      ref.startsWith("commission:") ||
      ref.startsWith("draft:") ||
      ref.startsWith("note:")
    ) {
      return { kind: "fold", foldId: "workFold" };
    }
  }
  return { kind: "chat", foldId: null };
}
