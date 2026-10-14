/* Pure finger offsets for the authored Daily model's normalized humanoid.
 * The renderer captures finger rest rotations and owns physical smoothing.
 * Arm poses, gesture timing, and thumbs remain separate. */
import { finiteClamp } from './vrm-mapping.js';
import { DAILY_GESTURE_MODEL_NAME } from './gesture-poses.js';

const SIDES = Object.freeze(['left', 'right']);
const JOINTS = Object.freeze(['Proximal', 'Intermediate', 'Distal']);

// Radians, kept small enough for a relaxed hand rather than a closing fist.
// Daily normalized regular fingers point ±X; the audited inward curl is
// approximately right +Z / left -Z. Distal directions inherit their segment.
const REST_CURLS = Object.freeze({
  Index: [.08, .10, .05],
  Middle: [.12, .16, .08],
  Ring: [.16, .20, .10],
  Little: [.20, .24, .12],
});

export const FINGER_BONE_NAMES = Object.freeze(SIDES.flatMap(side =>
  Object.keys(REST_CURLS).flatMap(digit => JOINTS.map(joint => `${side}${digit}${joint}`))));

export function resolveFingerProfile(modelName, enabled = true) {
  return enabled && modelName === DAILY_GESTURE_MODEL_NAME ? 'daily' : 'generic';
}

// Fraction of resting curl kept at full strength; weaker cues stay a little
// more relaxed. Every expression opens the hand modestly and retains curl.
const EXPRESSIONS = Object.freeze({
  wave: [.20, .15],
  deny: [.20, .15],
  palm: [.55, .10],
  self: [.85, .05],
});

export function sampleFingerPose(frame = {}, { profile = 'generic', reduced = false } = {}) {
  if (profile !== 'daily') return {};
  const f = frame || {};
  const envelope = finiteClamp(f.weight ?? 0, 0, 1);
  const strength = finiteClamp(f.strength ?? 1, 0, 1, 1);
  const activation = Math.min(1, strength / .15);
  let rightScale = 1;
  if (Object.hasOwn(EXPRESSIONS, f.name)) {
    const [fullFraction, softExtra] = EXPRESSIONS[f.name];
    const fraction = fullFraction + (1 - strength) * softExtra;
    rightScale -= (1 - fraction) * envelope * activation * (reduced ? .4 : 1);
  }
  const pose = {};
  for (const side of SIDES) {
    const sign = side === 'right' ? 1 : -1;
    const scale = side === 'right' ? rightScale : 1;
    for (const [digit, curls] of Object.entries(REST_CURLS)) {
      for (let joint = 0; joint < JOINTS.length; ++joint) {
        pose[`${side}${digit}${JOINTS[joint]}`] = [0, 0, sign * curls[joint] * scale];
      }
    }
  }
  return pose;
}
