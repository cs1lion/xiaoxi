/* Blend from actual rendered arm rotations into approved pose targets.
 * Quaternions are duck-typed so this controller can be tested without WebGL.
 * Recovery advances with bounded render dt, including a paused audio clock. */
const boundedDelta = dt => Number.isFinite(dt) ? Math.max(0, Math.min(.05, dt)) : 0;
const smooth = value => value * value * (3 - 2 * value);

export class PoseTransition {
  constructor() { this.reset(); }

  reset() { this.signalId = null; this.state = null; this.origins = {}; }

  update(signal, current, targets, dt, referenceTargets = targets) {
    if (!signal || !Number.isInteger(signal.id) || signal.id < 1) { this.reset(); return null; }
    if (signal.id !== this.signalId) {
      this.signalId = signal.id;
      this.origins = {};
      let distance = 0;
      for (const [name, target] of Object.entries(targets)) {
        const actual = current[name];
        if (!actual) continue;
        this.origins[name] = actual.clone();
        distance = Math.max(distance, actual.angleTo(referenceTargets[name] || target));
      }
      // Reference targets describe the incoming approved pose at its requested
      // strength; interpolation still follows its live envelope/phase targets.
      // A small wrist adjustment settles faster than an elevated arm.
      const duration = Math.min(.35, .08 + .27 * Math.min(1, distance / (Math.PI / 2)));
      this.state = { id: signal.id, reason: signal.reason, from: signal.from, to: signal.to,
        progress: 0, duration, distance, elapsed: 0, active: true };
    }
    const state = this.state;
    if (!state.active) return null;
    state.elapsed = Math.min(state.duration, state.elapsed + boundedDelta(dt));
    state.progress = Math.min(1, state.elapsed / state.duration);
    state.active = state.progress < 1;
    const amount = smooth(state.progress), poses = {};
    for (const [name, target] of Object.entries(targets)) {
      poses[name] = this.origins[name]
        ? this.origins[name].clone().slerp(target, amount) : target.clone();
    }
    // The completion frame lands on the current target. Later frames use the
    // renderer's established exponential smoothing for ordinary gesture motion.
    return poses;
  }

  getDiagnostic() {
    if (!this.state) return null;
    const { id, reason, from, to, progress, duration, distance, active } = this.state;
    return { id, reason, from, to, progress, duration, distance, active };
  }
}
