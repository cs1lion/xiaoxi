/* Stage composition model: a two-state camera and resting face morphs.
 *
 * Pure data and easing only. No three.js, no DOM, so the state transitions can
 * be asserted without a browser. The renderer reads sample() once per frame and
 * blends between the idle medium shot and the speaking close-up. */

export const STAGE_DEFAULTS = Object.freeze({
  camera: Object.freeze({
    // Enter is quicker than exit: she leans in as she starts talking, then
    // settles back without snapping.
    enter: 0.32,
    exit: 0.72,
    idle: Object.freeze({ yaw: 6, centerY: 0 }),
    // Width/height are fractions of the model height. The speaking width is the
    // term that governs distance: 0.22 keeps the face above 90 CSS px in the
    // 240 px stage at 1440.
    speaking: Object.freeze({ width: 0.22, height: 0.30, centerY: 0.05, yaw: 9 }),
  }),
  expression: Object.freeze({ morphs: Object.freeze({}) }),
});

const finite = (value, fallback) => Number.isFinite(value) ? value : fallback;
const positive = (value, fallback) => Number.isFinite(value) && value > 0 ? value : fallback;
const smoothstep = t => t * t * (3 - 2 * t);

export function resolveStageConfig(stage = {}) {
  const camera = stage?.camera || {};
  const idle = camera.idle || {};
  const speaking = camera.speaking || {};
  const expression = stage?.expression || {};
  const morphs = {};
  for (const [name, value] of Object.entries(expression.morphs || {})) {
    const weight = Number(value);
    if (Number.isFinite(weight) && weight > 0) morphs[name] = Math.min(1, weight);
  }
  return {
    camera: {
      enter: Math.max(0.02, positive(camera.enter, STAGE_DEFAULTS.camera.enter)),
      exit: Math.max(0.02, positive(camera.exit, STAGE_DEFAULTS.camera.exit)),
      idle: { yaw: finite(idle.yaw, STAGE_DEFAULTS.camera.idle.yaw),
        centerY: finite(idle.centerY, STAGE_DEFAULTS.camera.idle.centerY) },
      speaking: {
        width: positive(speaking.width, STAGE_DEFAULTS.camera.speaking.width),
        height: positive(speaking.height, STAGE_DEFAULTS.camera.speaking.height),
        centerY: finite(speaking.centerY, STAGE_DEFAULTS.camera.speaking.centerY),
        yaw: finite(speaking.yaw, STAGE_DEFAULTS.camera.speaking.yaw),
      },
    },
    expression: { morphs },
  };
}

export class StageCamera {
  constructor(stage = {}) {
    this.configure(stage);
    this.reset();
  }

  configure(stage = {}) {
    this.cfg = resolveStageConfig(stage);
  }

  reset() {
    this.blend = 0;
    this.target = 0;
    this.mode = 'idle';
  }

  setSpeaking(speaking) {
    this.target = speaking ? 1 : 0;
  }

  /* Exponential approach with separate enter/exit time constants. dt is in
   * seconds; a suspended tab passes 0 and changes nothing. */
  update(dt) {
    const seconds = Number.isFinite(dt) && dt > 0 ? dt : 0;
    if (seconds > 0) {
      const tau = this.target ? this.cfg.camera.enter : this.cfg.camera.exit;
      this.blend += (this.target - this.blend) * (1 - Math.exp(-seconds / tau));
      if (this.target === 1 && this.blend > 0.9999) this.blend = 1;
      if (this.target === 0 && this.blend < 0.0001) this.blend = 0;
    }
    this.mode = this.blend >= 0.5 ? 'speaking' : 'idle';
    return this.blend;
  }

  get eased() { return smoothstep(this.blend); }

  /* base: the existing idle framing in world units
   *   { width, height, centerY }
   * Returns the interpolated framing plus the camera yaw in radians. */
  sample(modelHeight, base) {
    const e = this.eased;
    const speaking = this.cfg.camera.speaking;
    const targetWidth = speaking.width * modelHeight;
    const targetHeight = speaking.height * modelHeight;
    const targetCenterY = (base.headY ?? base.centerY) + speaking.centerY * modelHeight;
    return {
      width: base.width + (targetWidth - base.width) * e,
      height: base.height + (targetHeight - base.height) * e,
      centerY: base.centerY + (targetCenterY - base.centerY) * e,
      yaw: (this.cfg.camera.idle.yaw + (speaking.yaw - this.cfg.camera.idle.yaw) * e) * Math.PI / 180,
      blend: e,
      mode: this.mode,
    };
  }
}

/* Keep only morphs the asset actually exposes. Available targets come from the
 * loaded dictionaries, never from a guessed name. */
export function resolveMorphWeights(requested = {}, available = new Map()) {
  const out = [];
  for (const [name, weight] of Object.entries(requested)) {
    const targets = typeof available.get === 'function' ? available.get(name) : available[name];
    if (!targets?.length || !Number.isFinite(weight) || weight <= 0) continue;
    out.push({ name, weight: Math.min(1, weight), targets });
  }
  return out;
}
