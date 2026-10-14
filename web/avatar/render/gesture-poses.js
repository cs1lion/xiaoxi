/* Pure gesture targets in normalized humanoid local axes, in radians.
 * The renderer composes these offsets with the model's captured arm rest pose.
 * Envelope timing and quaternion transitions remain owned by their drivers. */
import { finiteClamp } from './vrm-mapping.js';

export const DAILY_GESTURE_MODEL_NAME = 'Seed-san Daily Study';

export function resolveGestureProfile(modelName, enabled = true) {
  return enabled && modelName === DAILY_GESTURE_MODEL_NAME ? 'daily' : 'generic';
}

const GENERIC_GESTURES = Object.freeze({
  wave: { rightUpperArm: [-.35, 0, -.6], rightLowerArm: [-1.35, 0, 0], rightHand: [0, 0, 0] },
  deny: { rightUpperArm: [-.25, 0, -.35], rightLowerArm: [-1.15, 0, 0], rightHand: [0, 0, 0] },
  self: { rightUpperArm: [-.4, 0, -.15], rightLowerArm: [-1.2, 0, .2] },
  palm: { rightUpperArm: [-.35, 0, -.4], rightLowerArm: [-.9, -.5, 0], rightHand: [0, -.45, 0] },
});

// Offline authored against this model's actual arm vectors and palm plane.
// Its forearms point along local -X: the old X-only "bend" mostly twisted
// the arm. Y/Z below supply the elbow bend; X controls forearm/palm rotation.
// These fixed targets require no runtime IK or model-asset modification.
const DAILY_GESTURES = Object.freeze({
  wave: {
    rightUpperArm: [.6168313576043354, .3161242103575086, -.7833419267920909],
    rightLowerArm: [.6272966565944245, .530673756685337, -1.939062298154004],
    rightHand: [-.02188952063872474, -.23243001081579912, -.08608876694276799],
  },
  deny: {
    rightUpperArm: [.21140900511810937, .20433051062808688, -.1987291702733595],
    rightLowerArm: [1.1055760244017847, .3901306074616103, -1.9773370309335057],
    rightHand: [-.018101603946284787, -.08720867104355029, -.29247164385694474],
  },
  self: {
    rightUpperArm: [.2088875559835433, .13386897667458758, -.5023085170452992],
    rightLowerArm: [-1.701210286349039, .2792022895133178, 2.3311254189859834],
    rightHand: [.3437585703311846, -.5053664392287563, .6709997724413393],
  },
  palm: {
    rightUpperArm: [.36376781238532807, .3010687592980823, -.35419611300237924],
    rightLowerArm: [-2.016998453246609, -.40436428103913474, 1.2823261891734081],
    rightHand: [.23243295889868673, .3267606052367209, -.585907456707616],
  },
});

// Soft keys retain the same intended hand direction while reducing reach.
// Uniformly shrinking every full-pose angle would undo elbow articulation and
// rotate an upward-facing palm downward at ordinary speech strengths.
const DAILY_SOFT_GESTURES = Object.freeze({
  wave: {
    rightUpperArm: [.5786052694987843, .41783671317331905, -.5033818056029318],
    rightLowerArm: [.7406280079648111, .34764603304013403, -2.084827906033003],
    rightHand: [-.017625635912195785, -.17264217777277524, -.1010246300042078],
  },
  deny: {
    rightUpperArm: [.2092905755917528, .2162157416161284, -.1453185355863549],
    rightLowerArm: [1.1428202941086858, .31979431329558605, -1.894520565347696],
    rightHand: [-.03596178157621869, -.11560862038873851, -.40000538332636826],
  },
  self: {
    rightUpperArm: [.24999547505043548, .22258016427259877, -.27250361361069425],
    rightLowerArm: [-1.7690614296478893, .2726302506982335, 2.0575067857264937],
    rightHand: [.6952337187034076, -.5744757423043417, 1.03071210927237],
  },
  palm: {
    rightUpperArm: [.24409064214319706, .26622924946451093, -.10505279907111409],
    rightLowerArm: [-1.9085270393313623, -.19801325948825277, 1.4846891230744272],
    rightHand: [.17250451947289341, .19982078156354405, -.6306620101502679],
  },
});

// Offline preparation keys bend the elbow and lift the hand toward the lower
// chest before finishing the palm turn. Wave/deny forearms use an equivalent
// XYZ branch close to their final keys, avoiding a needless full-axis twist.
const DAILY_PREPARED_GESTURES = Object.freeze({
  wave: {
    rightUpperArm: [.3753135627496249, .3228553186111569, -.3230108441392388],
    rightLowerArm: [.7452969235446685, 1.8783604923404356, -1.396729231457795],
    rightHand: [-.00036762898227147564, -.18694867165635978, .015862139711719674],
  },
  deny: {
    rightUpperArm: [.22630222901283922, .24645635058217, -.10466728933811066],
    rightLowerArm: [1.1095178814174478, 1.7504959103363473, -1.526825286519535],
    rightHand: [-.00036762898227168044, -.1869486716563595, .01586213971171987],
  },
  self: {
    rightUpperArm: [.17833283775805206, .19999955651312212, -.0763152669371964],
    rightLowerArm: [-2.313275951357026, 1.3744847871210268, 1.9215372508551687],
    rightHand: [-.0003676289822715648, -.18694867165635998, .015862139711719885],
  },
  palm: {
    rightUpperArm: [.2167329427398558, .23182880988897428, -.11830123720684921],
    rightLowerArm: [-1.2372333178909711, 1.320808160378086, .8891663170048985],
    rightHand: [-.00036762898227187457, -.18694867165635903, .015862139711719632],
  },
});

const DAILY_PREPARED_SOFT_GESTURES = Object.freeze({
  wave: {
    rightUpperArm: [.3496330939325782, .34454905563233196, -.201719385521747],
    rightLowerArm: [.9170858270487421, 1.762883100215485, -1.4417415860714413],
    rightHand: [-.0003676289822715116, -.1869486716563596, .015862139711719885],
  },
  deny: {
    rightUpperArm: [.18565017468005768, .2208369158301842, -.030475970827956885],
    rightLowerArm: [1.0572755438871422, 1.700079686071498, -1.3964110019302745],
    rightHand: [-.00036762898227204967, -.18694867165635998, .01586213971172006],
  },
  self: {
    rightUpperArm: [.15939533415576415, .19496613143656621, -.007812203977662178],
    rightLowerArm: [-2.226370979879122, 1.4444566752941976, 1.91577160427157],
    rightHand: [-.0003676289822714787, -.18694867165635976, .015862139711720225],
  },
  palm: {
    rightUpperArm: [.1583045322936867, .19398879437655314, -.006375674933960961],
    rightLowerArm: [-1.4891068261720022, 1.4474585721416668, 1.2110314582042154],
    rightHand: [-.0003676289822716954, -.18694867165635906, .01586213971171976],
  },
});

// Keep preparation modest within the existing short audio-clock ramp. The
// authored key contributes this fraction beyond the old half-target posture.
const DAILY_PREPARATION_AMOUNT = .6;
// Reduced mode keeps the .4 final amplitude and uses half the extra detour.
const DAILY_REDUCED_PREPARATION_AMOUNT = .3;

function cloneGesture(definition) {
  return definition ? Object.fromEntries(Object.entries(definition).map(([bone, xyz]) => [bone, [...xyz]])) : {};
}

function genericGesture(name, phase) {
  const definition = GENERIC_GESTURES[name];
  if (!definition) return {};
  const angles = cloneGesture(definition);
  if (name === 'wave') angles.rightHand[2] = Math.sin(phase * Math.PI * 6) * .3;
  if (name === 'deny') angles.rightHand[1] = Math.sin(phase * Math.PI * 4) * .3;
  return angles;
}

function strengthKey(full, soft, strength) {
  if (!full || !soft) return {};
  const mix = finiteClamp((strength - .15) / .85, 0, 1);
  return Object.fromEntries(Object.entries(full).map(([bone, xyz]) =>
    [bone, xyz.map((value, i) => soft[bone][i] + (value - soft[bone][i]) * mix)]));
}

function dailyGesture(name, phase, strength) {
  const angles = strengthKey(DAILY_GESTURES[name], DAILY_SOFT_GESTURES[name], strength);
  if (name === 'wave' || name === 'deny') {
    angles.rightHand[1] += Math.sin(phase * Math.PI * 2) * (name === 'wave' ? .12 : .10) * strength;
  }
  return angles;
}

// Two monotone cubic segments share a harmonic midpoint slope. Endpoint
// tangents follow their secants; the driver's envelope already eases in/out.
// Opposite secants make only that axis pause at the key.
// This keeps each axis inside its segment's authored angles while preserving
// continuous velocity through preparation when its direction stays the same.
function pathAngle(prepared, final, envelope) {
  const left = prepared * 2, right = (final - prepared) * 2;
  const middleSlope = left * right > 0 ? 2 * left * right / (left + right) : 0;
  const second = envelope >= .5;
  const t = second ? (envelope - .5) * 2 : envelope * 2;
  const t2 = t * t, t3 = t2 * t;
  const from = second ? prepared : 0, to = second ? final : prepared;
  const fromSlope = second ? middleSlope : left, toSlope = second ? right : middleSlope;
  return (2 * t3 - 3 * t2 + 1) * from + (t3 - 2 * t2 + t) * .5 * fromSlope +
    (-2 * t3 + 3 * t2) * to + (t3 - t2) * .5 * toSlope;
}

function dailyPath(name, phase, strength, envelope, preparationAmount) {
  const final = strengthKey(DAILY_GESTURES[name], DAILY_SOFT_GESTURES[name], strength);
  const prepared = strengthKey(DAILY_PREPARED_GESTURES[name], DAILY_PREPARED_SOFT_GESTURES[name], strength);
  const angles = Object.fromEntries(Object.entries(final).map(([bone, xyz]) =>
    [bone, xyz.map((value, i) => {
      const halfTarget = value * .5;
      const preparation = halfTarget + (prepared[bone][i] - halfTarget) * preparationAmount;
      return pathAngle(preparation, value, envelope);
    })]));
  if (name === 'wave' || name === 'deny') {
    const stage = finiteClamp((envelope - .5) * 2, 0, 1);
    const swayEnvelope = stage * stage * (3 - 2 * stage);
    angles.rightHand[1] += Math.sin(phase * Math.PI * 2) * (name === 'wave' ? .12 : .10) * strength * swayEnvelope;
  }
  return angles;
}

export function sampleGesturePose(frame = {}, { profile = 'generic', reduced = false, reference = false, paths = true } = {}) {
  const f = frame || {};
  const phase = finiteClamp(f.phase ?? 0, 0, 1);
  const strength = finiteClamp(f.strength ?? 1, 0, 1, 1);
  const envelope = reference ? 1 : finiteClamp(f.weight ?? 0, 0, 1);
  const daily = profile === 'daily';
  // Semantic strength selects between two authored posture keys. Below the
  // protocol minimum .15, a continuous activation ramp still reaches rest.
  const gain = daily ? Math.min(1, strength / .15) : strength;
  if (daily && paths && !reference && envelope < 1) {
    const path = dailyPath(f.name, phase, strength, envelope,
      reduced ? DAILY_REDUCED_PREPARATION_AMOUNT : DAILY_PREPARATION_AMOUNT);
    const amplitude = gain * (reduced ? .4 : 1);
    return Object.fromEntries(Object.entries(path).map(([bone, xyz]) =>
      [bone, xyz.map(value => value * amplitude)]));
  }
  const amplitude = envelope * gain * (reduced ? .4 : 1);
  const angles = daily ? dailyGesture(f.name, phase, strength) : genericGesture(f.name, phase);
  return Object.fromEntries(Object.entries(angles).map(([bone, xyz]) =>
    [bone, xyz.map(value => value * amplitude)]));
}
