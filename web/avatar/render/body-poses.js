/* Small Daily shoulder/chest offsets in normalized humanoid local axes.
 * The existing director owns timing; the renderer owns actual-pose smoothing.
 * Spine, head, arm paths, and finger poses remain independent. */
import { finiteClamp } from './vrm-mapping.js';
import { DAILY_GESTURE_MODEL_NAME } from './gesture-poses.js';

export const BODY_BONE_NAMES = Object.freeze(['chest', 'rightShoulder']);

export function resolveBodyProfile(modelName, enabled = true) {
  return enabled && modelName === DAILY_GESTURE_MODEL_NAME ? 'daily' : 'generic';
}

// Daily faces +Z, with its right side at -X. The audited normalized axes use
// chest +X for a slight forward inclination and -Y to turn toward the hand;
// rightShoulder +Y moves the upper arm forward and -Z lifts it. No chest roll.
// Self stays smallest because that authored hand already approaches the chest.
const DAILY_BODY_POSES = Object.freeze({
  wave: { chest: [.010, -.006, 0], rightShoulder: [0, .010, -.030] },
  deny: { chest: [.005, -.008, 0], rightShoulder: [0, .006, -.020] },
  self: { chest: [.004, -.004, 0], rightShoulder: [0, .004, -.012] },
  palm: { chest: [.012, -.008, 0], rightShoulder: [0, .010, -.025] },
});

export function sampleBodyPose(frame = {}, { profile = 'generic', reduced = false } = {}) {
  if (profile !== 'daily') return {};
  const f = frame || {};
  const definition = Object.hasOwn(DAILY_BODY_POSES, f.name) ? DAILY_BODY_POSES[f.name] : null;
  const envelope = finiteClamp(f.weight ?? 0, 0, 1);
  const strength = finiteClamp(f.strength ?? 1, 0, 1, 1);
  // Follow the arm/finger protocol's continuous activation below strength .15.
  // Within its regular range, low-strength speech keeps smaller body support.
  const gain = Math.min(1, strength / .15) * (.65 + .35 * strength);
  const amplitude = envelope * gain * (reduced ? .4 : 1);
  return Object.fromEntries(BODY_BONE_NAMES.map(bone =>
    [bone, (definition?.[bone] || [0, 0, 0]).map(value => amplitude === 0 ? 0 : value * amplitude)]));
}
