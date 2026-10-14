/* Pure complete hand targets for the authored Daily normalized humanoid.
 * Existing automatic four-finger relaxation is reused without a second layer.
 * The renderer captures bind rotations and smooths these absolute offsets. */
import { finiteClamp } from './vrm-mapping.js';
import { DAILY_GESTURE_MODEL_NAME } from './gesture-poses.js';
import { FINGER_BONE_NAMES, sampleFingerPose } from './finger-poses.js';

const SIDES = Object.freeze(['left', 'right']);
const THUMB_JOINTS = Object.freeze(['Metacarpal', 'Proximal', 'Distal']);
const FINGER_JOINTS = Object.freeze(['Proximal', 'Intermediate', 'Distal']);

export const HAND_BONE_NAMES = Object.freeze([
  ...FINGER_BONE_NAMES,
  ...SIDES.flatMap(side => THUMB_JOINTS.map(joint => `${side}Thumb${joint}`)),
]);
export const HAND_POSE_NAMES = Object.freeze(['relaxed', 'open', 'softGrip']);

export function resolveHandProfile(modelName, enabled = true) {
  return enabled && modelName === DAILY_GESTURE_MODEL_NAME ? 'daily' : 'generic';
}

// Audited on actual active indexed Daily skin vertices. Positive angles curl
// toward the palm. Left axes mirror right X unchanged and Y/Z negated.
// No thumb opposition/contact or closed-fist pose is inferred from these axes.
const RIGHT_THUMB_AXES = Object.freeze([
  [.67385385, .005909337, .738841031],
  [.617273303, .012458761, .786650144],
  [.853783003, -.019185456, .520275410],
]);
const THUMB_ANGLES = Object.freeze({
  relaxed: [.08, .10, .06],
  open: [.025, .03, .015],
  softGrip: [.16, .22, .13],
});
const SOFT_GRIP_CURLS = Object.freeze({
  Index: [.28, .34, .18],
  Middle: [.32, .40, .20],
  Ring: [.36, .44, .22],
  Little: [.40, .48, .24],
});

// Convert the measured axis-angle to the renderer's XYZ Euler offset contract.
// Pose angles stay far from the XYZ singularity; no Three.js dependency needed.
function thumbEuler(axis, angle) {
  const scale = Math.sin(angle / 2) / Math.hypot(...axis);
  const [x, y, z] = axis.map(value => value * scale);
  const w = Math.cos(angle / 2);
  return [
    Math.atan2(2 * (x * w - y * z), 1 - 2 * (x * x + y * y)),
    Math.asin(finiteClamp(2 * (x * z + y * w), -1, 1)),
    Math.atan2(2 * (z * w - x * y), 1 - 2 * (y * y + z * z)),
  ];
}

const THUMB_POSES = Object.fromEntries(HAND_POSE_NAMES.map(name => [name,
  Object.fromEntries(SIDES.flatMap(side => THUMB_JOINTS.map((joint, i) => {
    const axis = RIGHT_THUMB_AXES[i].map((value, component) =>
      side === 'left' && component > 0 ? -value : value);
    return [`${side}Thumb${joint}`, thumbEuler(axis, THUMB_ANGLES[name][i])];
  }))),
]));

function safeUnit(value, fallback) {
  try { return finiteClamp(value, 0, 1, fallback); }
  catch { return fallback; }
}

// Invalid preview input is ignored. A valid preview replaces one whole hand;
// it does not multiply/add an automatic finger target or affect the other hand.
function validPreview(preview) {
  if (!preview || typeof preview !== 'object' || Array.isArray(preview)) return null;
  const side = preview.side === undefined ? 'right' : preview.side;
  return HAND_POSE_NAMES.includes(preview.name) && SIDES.includes(side)
    ? { name: preview.name, side } : null;
}

export function sampleHandPose(frame = {}, options = {}) {
  const { profile = 'generic', reduced = false, preview = null } = options || {};
  if (profile !== 'daily') return {};
  const f = frame && typeof frame === 'object' ? frame : {};
  const weight = safeUnit(f.weight ?? 0, 0);
  const strength = safeUnit(f.strength ?? 1, 1);
  const name = typeof f.name === 'string' ? f.name : null;
  const pose = sampleFingerPose({ name, weight, strength }, { profile: 'daily', reduced });
  const opensThumb = name === 'wave' || name === 'deny' || name === 'palm';
  const amplitude = opensThumb ? weight * Math.min(1, strength / .15)
    * (.65 + .35 * strength) * (reduced ? .4 : 1) : 0;
  for (const side of SIDES) for (const joint of THUMB_JOINTS) {
    const bone = `${side}Thumb${joint}`;
    const rest = THUMB_POSES.relaxed[bone];
    pose[bone] = side === 'right'
      ? rest.map((value, i) => value + (THUMB_POSES.open[bone][i] - value) * amplitude)
      : [...rest];
  }

  const manual = validPreview(preview);
  if (manual) {
    const { side, name: poseName } = manual;
    const relaxed = sampleFingerPose({}, { profile: 'daily' });
    for (const bone of FINGER_BONE_NAMES.filter(bone => bone.startsWith(side))) {
      if (poseName === 'softGrip') {
        const digit = Object.keys(SOFT_GRIP_CURLS).find(digit => bone.startsWith(`${side}${digit}`));
        const joint = FINGER_JOINTS.findIndex(joint => bone.endsWith(joint));
        pose[bone] = [0, 0, (side === 'right' ? 1 : -1) * SOFT_GRIP_CURLS[digit][joint]];
      } else {
        const fraction = poseName === 'open' ? .2 : 1;
        pose[bone] = relaxed[bone].map(value => value * fraction);
      }
    }
    for (const joint of THUMB_JOINTS) {
      const bone = `${side}Thumb${joint}`;
      pose[bone] = [...THUMB_POSES[poseName][bone]];
    }
  }
  return pose;
}
