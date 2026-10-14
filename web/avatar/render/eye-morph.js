/* Model-native face channels are registered with the VRM expression manager.
 * The manager remains the sole writer of morphTargetInfluences. Resolve names
 * once at mount; numeric indexes are only read from the loaded dictionaries. */
import { VRMExpression, VRMExpressionMorphTargetBind } from '@pixiv/three-vrm';
import { finiteClamp } from './vrm-mapping.js';

const unit = (value, fallback = 0) => finiteClamp(value ?? fallback, 0, 1, fallback);
const signed = value => finiteClamp(value);
const mean = (left, right) => (left + right) / 2;

const PROFILES = {
  seed: {
    smile: 'eye_smile', wide: 'eye_open',
    smileCalL: 'neuro_eye_smile_L', smileCalR: 'neuro_eye_smile_R',
    lowerL: 'neuro_lower_lid_raise_L', lowerR: 'neuro_lower_lid_raise_R',
    browUp: 'eye_brow_up', browDown: 'eye_brow_down',
    browUpL: 'eye_brow_up_L', browUpR: 'eye_brow_up_R',
    browDownL: 'eye_brow_down_L', browDownR: 'eye_brow_down_R',
    browRaiseL: 'neuro_brow_raise_L', browRaiseR: 'neuro_brow_raise_R',
    browLowerL: 'neuro_brow_lower_L', browLowerR: 'neuro_brow_lower_R',
    irisBig: 'eye_black_big', irisSmall: 'eye_black_small',
    highlightHide: 'eye_no_hilight', cheek: 'cheek',
    lipUp: 'lip_up', lipDown: 'lip_down',
  },
  sample: {
    smileL: 'Fcl_EYE_Joy_L', smileR: 'Fcl_EYE_Joy_R', smile: 'Fcl_EYE_Joy',
    wide: 'Fcl_EYE_Surprised',
    browUp: 'Fcl_BRW_Surprised', browDown: 'Fcl_BRW_Angry',
    browAngry: 'Fcl_BRW_Angry', browSorrow: 'Fcl_BRW_Sorrow',
    browJoy: 'Fcl_BRW_Joy', browFun: 'Fcl_BRW_Fun',
    highlightHide: 'Fcl_EYE_HighlightHide',
  },
};

function collectMorphs(scene) {
  const names = new Map();
  scene?.traverse?.(node => {
    const influences = node.morphTargetInfluences;
    if (!influences || !node.morphTargetDictionary) return;
    for (const [rawName, index] of Object.entries(node.morphTargetDictionary)) {
      if (!Number.isInteger(index) || index < 0 || index >= influences.length) continue;
      // VRoid prefixes its original shape names with "Face_Blendshape.".
      const name = rawName.slice(rawName.lastIndexOf('.') + 1);
      let targets = names.get(name);
      if (!targets) names.set(name, targets = []);
      // GLTF primitives can share their influence array. Binding both would add
      // the same value twice even though they are different Mesh objects.
      if (!targets.some(target => target.influences === influences && target.index === index)) {
        targets.push({ mesh: node, influences, index });
      }
    }
  });
  return names;
}

function capability(available, mode = 'unavailable', extra = {}) {
  return Object.freeze({ available: !!available, mode: available ? mode : 'unavailable', ...extra });
}

export function createEyeMorphAdapter(vrm) {
  const manager = vrm?.expressionManager;
  const morphs = collectMorphs(vrm?.scene);
  const profile = morphs.has('eye_smile') || morphs.has('eye_brow_up_L') ? 'seed'
    : morphs.has('Fcl_EYE_Joy_L') || morphs.has('Fcl_BRW_Surprised') ? 'sample' : 'unsupported';
  const names = PROFILES[profile] || {};
  const has = key => !!manager?.registerExpression && !!morphs.get(names[key])?.length;
  const pair = (left, right) => has(left) && has(right);
  const independentSmile = pair('smileL', 'smileR');
  const calibratedLids = pair('smileCalL', 'smileCalR') && pair('lowerL', 'lowerR');
  const independentBrow = pair('browUpL', 'browUpR') && pair('browDownL', 'browDownR');
  const calibratedBrow = pair('browRaiseL', 'browRaiseR') && pair('browLowerL', 'browLowerR');
  const bilateralBrow = has('browUp') || has('browDown');
  const available = independentSmile || has('smile') || calibratedBrow || independentBrow || bilateralBrow;
  const channels = Object.freeze({
    smile: capability(calibratedLids || independentSmile || has('smile'), calibratedLids || independentSmile ? 'independent' : 'bilateral',
      { supportedModes: calibratedLids ? ['calibrated', 'native'] : ['native'] }),
    lowerLid: capability(calibratedLids, 'independent', {
      description: calibratedLids ? '左右独立下睑局部抬升；与原有完整眨眼合成。' : '模型没有独立下睑抬升通道。',
      approximate: calibratedLids, pureTranslation: false,
      supportedModes: calibratedLids ? ['calibrated', 'native'] : ['native'],
    }),
    eyeWide: capability(has('wide'), 'bilateral', { approximate: profile === 'sample' }),
    // EyeBallForm changes iris/black-eye size, not eyelid shape or a separate pupil.
    eyeForm: capability(has('irisBig') || has('irisSmall'), 'bilateral'),
    irisSize: capability(has('irisBig') || has('irisSmall'), 'bilateral'),
    pupil: capability(false),
    browHeight: capability(calibratedBrow || independentBrow || bilateralBrow, calibratedBrow || independentBrow ? 'independent' : 'bilateral',
      { approximate: !calibratedBrow && (profile === 'sample' || profile === 'seed'),
        pureTranslation: calibratedBrow, supportedModes: calibratedBrow ? ['calibrated', 'native'] : ['native'],
        description: calibratedBrow ? '新增左右独立垂直眉高；原生眉形保留用于对照。'
          : profile === 'seed' ? '原生眉形混合位置与形状变化，不是纯垂直抬眉。'
          : profile === 'sample' ? '双侧语义眉形近似，不是独立眉高位移。' : '没有已适配的眉形通道。' }),
    browAngle: capability(has('browAngry') || has('browSorrow'), 'bilateral', { approximate: true }),
    browForm: capability(has('browJoy') || has('browFun'), 'bilateral', { approximate: true }),
    highlight: capability(has('highlightHide'), 'bilateral'),
    cheek: capability(has('cheek'), 'bilateral'),
    mouthForm: capability(has('lipUp') || has('lipDown'), 'bilateral'),
  });
  const degraded = [];
  for (const [key, channel] of Object.entries(channels)) {
    if (!channel.available) degraded.push(`${key}:unavailable`);
    else if (channel.mode === 'bilateral' && ['smile', 'browHeight', 'browAngle', 'browForm', 'eyeWide'].includes(key)) {
      degraded.push(`${key}:bilateral`);
    }
    if (channel.available && channel.approximate) degraded.push(`${key}:approximate`);
  }
  const capabilities = Object.freeze({ available, profile: calibratedLids ? 'seed-lids' : calibratedBrow ? 'seed-eyes' : profile, channels, degraded: Object.freeze(degraded) });
  const expressions = new Map();
  if (available) {
    for (const nativeName of new Set(Object.values(names))) {
      const targets = morphs.get(nativeName);
      if (!targets?.length) continue;
      let expressionName = `neuro_eye_${nativeName}`;
      while (manager.getExpression(expressionName)) expressionName += '_';
      const expression = new VRMExpression(expressionName);
      // Defaults are continuous and have no procedural overrides. Native blink,
      // speech vowels and expression/bone LookAt keep their own existing binds.
      for (const { mesh, index } of targets) {
        expression.addBind(new VRMExpressionMorphTargetBind({ primitives: [mesh], index, weight: 1 }));
      }
      manager.registerExpression(expression);
      vrm.scene.add(expression);
      expressions.set(nativeName, expression);
    }
  }

  return {
    available,
    capabilities,
    write(params = {}, options = {}) {
      if (!available) return;
      const weights = new Map();
      const add = (key, value) => {
        const name = names[key];
        if (expressions.has(name)) weights.set(name, unit((weights.get(name) || 0) + value));
      };
      const openL = finiteClamp(params.ParamEyeLOpen ?? 1, 0, 1.2, 1);
      const openR = finiteClamp(params.ParamEyeROpen ?? 1, 0, 1.2, 1);
      const gateL = unit(openL), gateR = unit(openR);
      const gateBoth = Math.min(gateL, gateR);
      const smileL = unit(params.ParamEyeLSmile), smileR = unit(params.ParamEyeRSmile);
      if (calibratedLids && options.lidMode !== 'native') {
        add('smileCalL', smileL * gateL);
        add('smileCalR', smileR * gateR);
        add('lowerL', unit(params.ParamEyeLLower) * gateL);
        add('lowerR', unit(params.ParamEyeRLower) * gateR);
      } else if (independentSmile) {
        add('smileL', smileL * gateL);
        add('smileR', smileR * gateR);
      } else {
        // Average intent for a bilateral-only asset; fade against the more closed
        // eye so a wink/full blink cannot stack a second closing shape.
        add('smile', mean(smileL, smileR) * gateBoth);
      }
      add('wide', Math.max(0, mean(openL, openR) - 1) / 0.2 * gateBoth);

      const browL = signed(params.ParamBrowLY), browR = signed(params.ParamBrowRY);
      // Exporter names are routing aliases, not proof of vertical displacement.
      // Seed's "up" includes mixed positive/negative vertex Y and a downward
      // visible centroid; report browHeight as an approximation above.
      if (calibratedBrow && options.browMode !== 'native') {
        add('browRaiseL', Math.max(0, browL)); add('browLowerL', Math.max(0, -browL));
        add('browRaiseR', Math.max(0, browR)); add('browLowerR', Math.max(0, -browR));
      } else if (independentBrow) {
        add('browUpL', Math.max(0, browL)); add('browDownL', Math.max(0, -browL));
        add('browUpR', Math.max(0, browR)); add('browDownR', Math.max(0, -browR));
      } else {
        const height = mean(browL, browR);
        add('browUp', Math.max(0, height)); add('browDown', Math.max(0, -height));
      }
      // VRoid exposes semantic bilateral shapes rather than independent eyebrow
      // rotations/curvature. The capability report labels this approximation.
      const angle = mean(signed(params.ParamBrowLAngle), signed(params.ParamBrowRAngle));
      const form = mean(signed(params.ParamBrowLForm), signed(params.ParamBrowRForm));
      add('browAngry', Math.max(0, -angle) * 0.55);
      add('browSorrow', Math.max(0, angle) * 0.55 + Math.max(0, -form) * 0.45);
      add('browJoy', Math.max(0, form) * 0.65);
      add('browFun', Math.max(0, form) * 0.20);

      const iris = signed(params.ParamEyeBallForm);
      add('irisBig', Math.max(0, iris)); add('irisSmall', Math.max(0, -iris));
      add('highlightHide', 1 - unit(params.ParamEyeHighlight, 1));
      add('cheek', unit(params.ParamCheek));
      const mouth = signed(params.ParamMouthForm);
      const mouthGate = 1 - unit(params.ParamMouthOpenY) * 0.65;
      add('lipUp', Math.max(0, mouth) * 0.6 * mouthGate);
      add('lipDown', Math.max(0, -mouth) * 0.6 * mouthGate);
      for (const [name, expression] of expressions) manager.setValue(expression.expressionName, weights.get(name) || 0);
    },
    dispose() {
      for (const expression of expressions.values()) {
        manager.unregisterExpression(expression);
        expression.removeFromParent();
      }
      expressions.clear();
    },
  };
}
