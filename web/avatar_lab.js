import { boot } from './avatar/index.js';
import { EYE_EXPRESSION_PRESETS } from './avatar/drivers/eye-expression.js';
import { AvatarCalibration } from './avatar/calibration.js';
import { AvatarPerformanceProbe } from './avatar/performance-probe.js';
import { DIALOGUE_GAZE_STYLES } from './avatar/drivers/dialogue-gaze.js';
import { mountDialogueReview } from './avatar/dialogue-review-ui.js';
const $ = (id) => document.getElementById(id);
const status = $('status');
let avatar, modelUrl = '/static/models/sample.vrm', objectUrl, ctx, source, transitionDemo;
let calibration, calibrationManifest, calibrationCaption, calibrationDisabled = new Map();
const calibrationManifests = new Map();
let performanceProbe, performanceResult;
let dialogueDemo = null;
let dialogueDemoEpoch = 0;
let dialogueReview, labReviewTurn = null, labReviewClip = null, labReviewSequence = 0;
function labMessage(message) { avatar.onMessage(message); dialogueReview?.event({ ...message, turn_id: labReviewTurn }); }
function labPlaybackStart(text, at, duration) {
  labReviewClip = { text, duration };
  dialogueReview?.event({ type: 'playback-start', turn_id: labReviewTurn, text,
    meta: { source: 'silent-lab-demo', audioTime: at, durationMs: duration * 1000 } });
  avatar.speak(text, ctx, at, duration, { performance: { version: 1, source: 'llm', cues: [] } });
}
function labPlaybackEnd(reason = 'natural') {
  if (!labReviewClip) return;
  dialogueReview?.event({ type: 'playback-end', turn_id: labReviewTurn,
    meta: { reason, source: 'silent-lab-demo', audioTime: ctx?.currentTime } }); labReviewClip = null;
}
function endLabReview(reason) {
  labPlaybackEnd(reason);
  if (labReviewTurn) dialogueReview?.endTurn(labReviewTurn, reason); labReviewTurn = null;
}
window.__dialogueComparison = { active: false, index: 0, total: 3, style: 'natural', elapsed: 0 };
let stopLabPlayback = () => stopAudio();
const isCalibrationModel = () => avatar?.rendererKind === 'vrm' && modelUrl === calibrationManifest?.modelUrl;
const showCalibrationRegions = visible => $('calibrationRegions').toggleAttribute('hidden', !visible);
const controls = [...document.querySelectorAll('button,input,select')];
controls.forEach(el => el.disabled = true);
function stopAudio() { endLabReview('stopped'); dialogueDemoEpoch++; window.__dialogueComparison = { ...window.__dialogueComparison, active: false }; if (dialogueDemo) { dialogueDemo = null; $('dialogueStatus').textContent = '流程已停止'; } if (transitionDemo) { transitionDemo = null; $('transitionStatus').textContent = '流程已停止 · 后续切换已取消'; } if (source) { source.onended = null; try { source.stop(); } catch {} source.disconnect(); source = null; } avatar?.stopSpeaking(); }
function eyeCapabilities() {
  const cap = avatar.svg.getEyeCapabilities?.();
  const native = cap?.available;
  $('eyeFocus').disabled = typeof avatar.svg.setEyeFocus !== 'function';
  $('eyeHighlight').disabled = !native || !cap.channels.highlight.available;
  if (!native) {
    $('eyeCapabilities').textContent = avatar.rendererKind === 'svg' ? 'SVG 可预览眉眼与注视；高光控制需要支持的 VRM。' : '当前模型使用基础眨眼与注视，尚无已适配的独立眉眼通道。';
    return;
  }
  const labels = { smile:'笑眼', lowerLid:'下睑局部抬升', eyeWide:'睁大', browHeight:'眉形', highlight:'高光', cheek:'脸红' };
  const supported = Object.entries(labels).filter(([key]) => cap.channels[key]?.available).map(([, label]) => label);
  const limits = [];
  if (cap.channels.smile.mode === 'bilateral') limits.push('笑眼左右联动');
  if (cap.channels.browHeight.mode === 'bilateral') limits.push('眉形左右联动');
  if (cap.channels.browHeight.approximate && cap.channels.browHeight.description) limits.push(cap.channels.browHeight.description);
  if (!cap.channels.pupil.available) limits.push('无独立瞳孔大小通道');
  $('eyeCapabilities').textContent = `可用：${supported.join('、')}。${limits.join('；')}。`;
}
function selectGazeMode(mode) {
  avatar.setGazeMode(mode);
  $('follow').checked = mode === 'follow';
  document.querySelectorAll('[data-gaze]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.gaze === mode)));
}
function selectEyeExpression(name) {
  avatar.setEyeExpression(name);
  document.querySelectorAll('[data-eye-expression]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.eyeExpression === name)));
  $('eyeInherit').setAttribute('aria-pressed', String(name === null));
  const label = EYE_EXPRESSION_PRESETS.find(p => p.name === name)?.label;
  $('caption').textContent = label ? `${label} · 眉眼与视线独立合成，眨眼与口型继续工作` : '跟随聊天表情 · 保留眼神、眨眼和语音口型';
}
function faceShadingStatus() {
  const value = avatar.svg.getFaceShading?.();
  $('face-shading').disabled = !value?.available;
  $('face-shading-status').textContent = !value?.available ? '当前模型尚未适配脸部亮部校准。'
    : value.enabled ? '适度降低脸部亮度，保留暖光；可关闭查看原版鼻嘴细节。'
    : '原版脸部亮度 · 开启后可对照鼻子和嘴部细节。';
}
function gestureCalibrationStatus() {
  const value = avatar.svg.getGestureCalibration?.();
  $('gestureCalibration').disabled = !value?.available;
  $('gestureCalibrationStatus').textContent = !value?.available ? '当前模型使用通用手势。'
    : value.enabled ? '日常角色：招呼在脸侧，否定在胸前，自我强调朝向自己，摊掌朝上。'
    : '使用原有通用姿势 · 可重复同一手势作对照。';
}
function gesturePathStatus() {
  const value = avatar.svg.getGesturePaths?.();
  $('gesturePaths').disabled = !value?.available;
  $('gesturePathStatus').textContent = !value?.available ? '动作路线需要启用日常手势校准。'
    : value.enabled ? '先屈肘抬手，再转掌表达；收回沿原路返回。'
    : '使用原来的同步抬手与转掌 · 可重复同一手势作对照。';
}
function fingerRelaxationStatus() {
  const value = avatar.svg.getFingerRelaxation?.();
  $('fingerRelaxation').disabled = !value?.available;
  $('fingerRelaxationStatus').textContent = !value?.available ? '当前模型尚未适配手指松弛。'
    : value.enabled ? '休息时手指轻弯，拇指随招呼和摊掌舒展，收回后放松。'
    : '保留原来的平直手指 · 可重复同一手势作对照。';
  handPoseStatus();
}
let lastHandPoseUI = null;
function handPoseStatus() {
  if (calibration) return;
  const value = avatar?.svg.getHandPosePreview?.();
  const key = JSON.stringify(value || null);
  if (key === lastHandPoseUI) return;
  lastHandPoseUI = key;
  const poses = [['handPoseRelaxed', 'relaxed'], ['handPoseOpen', 'open'], ['handPoseSoftGrip', 'softGrip'], ['handPoseAuto', null]];
  for (const [id, name] of poses) {
    $(id).disabled = !value?.enabled;
    $(id).setAttribute('aria-pressed', String((value?.name ?? null) === name));
  }
  const labels = { relaxed: '放松', open: '摊掌', softGrip: '轻握' };
  $('handPoseStatus').textContent = !value?.available ? '当前模型尚未适配基础手型。'
    : !value.enabled ? '开启手指自然松弛后可检查手型。'
    : value.name ? `右手${labels[value.name]}检查 · 播放后自动收回，可切换手型或退出。`
    : '手型随动作变化 · 可单独查看放松、摊掌和轻握。';
}
function bodyCoordinationStatus() {
  const value = avatar.svg.getBodyCoordination?.();
  $('bodyCoordination').disabled = !value?.available;
  $('bodyCoordinationStatus').textContent = !value?.available ? '当前模型尚未适配上身配合。'
    : value.enabled ? '抬手时肩部和胸部轻微配合，收回后回到休息姿态。'
    : '使用原来的上身姿态 · 可重复同一手势作对照。';
}
async function switchRenderer() {
  dialogueReview?.stop('model-switch');
  await performanceProbe?.stop('model-switch');
  stopLabPlayback();
  exitCalibration();
  const select = $('renderer');
  select.disabled = true;
  $('calibrationEnter').disabled = true;
  window.__avatarReady = false;
  status.textContent = '正在加载…现有形象会保留到新模型就绪。';
  try {
    await avatar.setRenderer(select.value, modelUrl);
    avatar.svg.setPresentation?.($('presentation').checked);
    avatar.svg.setFaceShading?.($('face-shading').checked);
    avatar.svg.setEyeFocus?.($('eyeFocus').checked);
    avatar.svg.setPerformanceTransitions?.($('poseTransitions').checked);
    avatar.svg.setGestureCalibration?.($('gestureCalibration').checked);
    avatar.svg.setGesturePaths?.($('gesturePaths').checked);
    avatar.svg.setFingerRelaxation?.($('fingerRelaxation').checked);
    avatar.svg.setBodyCoordination?.($('bodyCoordination').checked);
    faceShadingStatus();
    gestureCalibrationStatus();
    gesturePathStatus();
    fingerRelaxationStatus();
    bodyCoordinationStatus();
    $('poseTransitions').disabled = typeof avatar.svg.setPerformanceTransitions !== 'function';
    $('transitionDemo').disabled = $('poseTransitions').disabled;
    $('transitionStop').disabled = $('poseTransitions').disabled;
    eyeCapabilities();
    selectCalibrationManifest();
    $('calibrationEnter').disabled = !calibrationManifest || avatar.rendererKind !== 'vrm';
    const supportsPalette = avatar.svg.setPalette?.($('palette').value);
    $('palette').disabled = !supportsPalette;
    window.__avatarReady = true;
    const isDaily = avatar.rendererKind === 'vrm' && ['/static/models/seed-daily.vrm', '/static/models/seed-daily-eyes.vrm', '/static/models/seed-daily-lids.vrm', '/static/models/seed-daily-hair.vrm'].includes(modelUrl);
    status.textContent = isDaily ? '及肩短发与日常外套已就绪 · Seed-san 衍生造型 · 原模型 © VirtualCast, Inc. · VRM Public License 1.0' : avatar.rendererKind === 'original' ? '原创程序化 3D 原型 · 无外部角色资产 · 非 VRM 成品' : avatar.rendererKind === 'vrm' ? '3 渲 2 已就绪 · 可测试表情与口型' : 'SVG 对照模式';
    $('caption').textContent = isDaily ? '保留原脸部、骨架与动作驱动 · 及肩短发＋针织内搭＋日常短外套' : '保留现有情绪、口型、眼神驱动 · 模型仅用于技术验证';
    document.querySelector('.badge').textContent = avatar.rendererKind === 'original' ? 'AQI / ORIGINAL 3D STUDY 01' : 'AVATAR LAB / ' + avatar.rendererKind.toUpperCase();
  } catch (error) {
    select.value = avatar.rendererKind;
    status.textContent = `加载失败，保留当前形象：${error.message}`;
  } finally {
    select.disabled = false;
    $('calibrationEnter').disabled = !calibrationManifest || avatar.rendererKind !== 'vrm';
    window.__avatarReady = true;
  }
}
function selectCalibrationManifest() {
  calibrationManifest = calibrationManifests.get(modelUrl) || calibrationManifests.get('/static/models/seed-daily.vrm');
  $('calibrationRegions').replaceChildren();
  if (!calibrationManifest) return;
  const ns = 'http://www.w3.org/2000/svg';
  const labels = { iris_left: '左眼', iris_right: '右眼', brow_left: '左眉', brow_right: '右眉',
    upper_lid_left:'左上睑', upper_lid_right:'右上睑', lower_lid_left:'左下睑', lower_lid_right:'右下睑' };
  for (const [name, annotation] of Object.entries(calibrationManifest.rois)) {
    const [x, y, w, h] = Array.isArray(annotation) ? annotation : annotation.rect;
    const rect = document.createElementNS(ns, 'rect');
    for (const [key, value] of Object.entries({ x:x*1000, y:y*1000, width:w*1000, height:h*1000 })) rect.setAttribute(key, value);
    const label = document.createElementNS(ns, 'text'); label.setAttribute('x',x*1000); label.setAttribute('y',y*1000-6);
    label.textContent = labels[name] || name; $('calibrationRegions').append(rect,label);
    for (const points of annotation.excludePolygonsNormalized || []) {
      const polygon = document.createElementNS(ns,'polygon'); polygon.setAttribute('points',points.map(([x,y])=>`${x*1000},${y*1000}`).join(' '));
      polygon.setAttribute('fill','#cb744733'); polygon.setAttribute('stroke','#eeaa83'); $('calibrationRegions').append(polygon);
    }
  }
}
function downloadCalibration(blob, extension) {
  const link = document.createElement('a');
  const url = URL.createObjectURL(blob);
  const kind = $('calibrationBaseline').checked ? 'baseline' : 'candidate';
  link.href = url;
  link.download = `eye-${kind}-${$('calibrationExpression').value}-${Date.now()}.${extension}`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exitCalibration(message = '自然动作已恢复。') {
  calibration?.exit(); calibration = null;
  $('viewport').classList.remove('calibrating');
  if (calibrationCaption !== undefined) $('caption').textContent = calibrationCaption;
  calibrationCaption = undefined;
  window.__avatarCalibration = null;
  for (const [control, disabled] of calibrationDisabled) control.disabled = disabled;
  calibrationDisabled.clear();
  $('calibrationControls').hidden = true;
  showCalibrationRegions(false);
  $('calibrationExit').disabled = true;
  $('calibrationEnter').disabled = !calibrationManifest || avatar?.rendererKind !== 'vrm';
  $('calibrationStatus').textContent = message;
}
function drawCalibrationPose() {
  if (!calibration) return;
  try {
    const name = $('calibrationExpression').value;
    const hasReference = isCalibrationModel();
    $('calibrationBaseline').disabled = !hasReference || !Object.hasOwn(calibrationManifest.baselineOverrides, name);
    if ($('calibrationBaseline').disabled) $('calibrationBaseline').checked = false;
    $('calibrationShowRegions').disabled = !hasReference;
    showCalibrationRegions(hasReference && $('calibrationShowRegions').checked);
    const gazes = { front: [0, 0], left: [-0.8, 0], right: [0.8, 0], up: [0, -0.55], down: [0, 0.55] };
    const [x, y] = gazes[$('calibrationGaze').value];
    const overrides = $('calibrationBaseline').checked ? Object.fromEntries(
      Object.entries(calibrationManifest.baselineOverrides[name] || {}).map(([key, value]) => {
        const neutral = avatar.store.neutral[key];
        return [key, neutral + (value-neutral)*Number($('calibrationIntensity').value)/100];
      })) : {};
    const manual = $('calibrationManualBrow').checked;
    $('calibrationBrowLeft').disabled = $('calibrationBrowRight').disabled = !manual;
    for (const side of ['Left','Right']) $('calibrationBrow'+side+'Value').value = $('calibrationBrow'+side).value+'%';
    // UI/ROI sides are the viewer's; VRM parameter sides are the character's.
    if (manual) { overrides.ParamBrowRY = Number($('calibrationBrowLeft').value)/100; overrides.ParamBrowLY = Number($('calibrationBrowRight').value)/100; }
    const hasLids = !!avatar.svg.getEyeCapabilities?.().channels.lowerLid?.available;
    $('calibrationLidControls').hidden = !hasLids;
    const manualLids = hasLids && $('calibrationManualLids').checked;
    for (const kind of ['Lower', 'Smile']) for (const side of ['Left', 'Right']) {
      const control = $('calibration' + kind + side);
      control.disabled = !manualLids;
      $('calibration' + kind + side + 'Value').value = control.value + '%';
    }
    if (manualLids) {
      for (const [viewer, character] of [['Left', 'R'], ['Right', 'L']]) {
        overrides[`ParamEye${character}Lower`] = Number($('calibrationLower' + viewer).value) / 100;
        overrides[`ParamEye${character}Smile`] = Number($('calibrationSmile' + viewer).value) / 100;
      }
    }
    const browMode = hasLids ? calibrationManifest.baselineBrowModes?.[name] || 'calibrated'
      : $('calibrationBaseline').checked && (manual || calibrationManifest.baselineBrowModes?.[name])
      ? calibrationManifest.baselineBrowModes?.[name] || 'native' : 'calibrated';
    const lidMode = hasLids && $('calibrationBaseline').checked ? calibrationManifest.baselineLidModes?.[name] || 'native' : 'calibrated';
    $('calibrationBaselineLabel').textContent = hasLids ? '对照上一版眼睑与笑眼'
      : calibrationManifest.schemaVersion >= 2 && (manual || name === 'curious') ? '对照原生眉形' : '对照修改前参数';
    const snapshot = calibration.pose({ expression: name,
      intensity: Number($('calibrationIntensity').value) / 100,
      blink: Number($('calibrationBlink').value) / 100,
      gaze: { x, y }, mouthOpen: Number($('calibrationMouth').value) / 100,
      overrides, browMode, lidMode,
    });
    window.__calibrationSnapshot = snapshot;
    for (const key of ['Intensity', 'Blink', 'Mouth']) $('calibration' + key + 'Value').value = $('calibration' + key).value + '%';
    $('calibrationData').textContent = JSON.stringify({ pose: snapshot.pose, params: snapshot.params,
      nativeMorphs: snapshot.nativeMorphs, headAnchor: snapshot.headAnchor, fingerprint: snapshot.fingerprint }, null, 2);
    const goal = calibrationManifest.goals[name];
    $('calibrationGoal').textContent = !hasReference ? '此模型可检查固定姿态；本轮参数基线和眼区检查框适用于日常模型。'
      : goal ? goal.intent + '（本轮试调目标）' : '保留原预设，检查组合眨眼与注视。';
    const mode = $('calibrationBaseline').checked ? '修改前参数' : '本轮参数';
    $('caption').textContent = `${EYE_EXPRESSION_PRESETS.find(p => p.name === name)?.label} · ${mode} · 闭合 ${$('calibrationBlink').value}%`;
    $('calibrationStatus').textContent = '相机、灯光和头部已固定；用相同条件检查前后变化。';
  } catch (error) { exitCalibration(`画面条件已变化，请重新开始校准：${error.message}`); }
}
async function initializeCalibration() {
  $('calibrationExit').disabled = true;
  $('calibrationEnter').disabled = true;
  for (const preset of EYE_EXPRESSION_PRESETS) {
    const option = document.createElement('option'); option.value = preset.name; option.textContent = preset.label;
    $('calibrationExpression').append(option);
  }
  try {
    for (const suffix of ['', '-v2', '-v3']) {
      const response = await fetch(`/static/avatar/eye-calibration${suffix}.json`);
      if (!response.ok) throw new Error('校准清单无法读取');
      const manifest = await response.json(); calibrationManifests.set(manifest.modelUrl, manifest);
    }
    selectCalibrationManifest();
    $('calibrationStatus').textContent = '本轮参考与目标已登记，切换到日常模型后可固定画面。';
  } catch (error) { $('calibrationStatus').textContent = error.message; }
  $('calibrationEnter').onclick = async () => {
    dialogueReview?.stop('calibration');
    await performanceProbe?.stop('enter-calibration');
    $('calibrationEnter').disabled = true;
    stopLabPlayback();
    calibrationCaption = $('caption').textContent;
    $('viewport').classList.add('calibrating');
    // Let ResizeObserver establish the square comparison viewport first.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    calibration = new AvatarCalibration(avatar);
    try {
      calibration.enter({ presentation: true, eyeFocus: true });
      for (const control of document.querySelectorAll('button,input,select')) {
        if (control.closest('#calibrationPanel')) continue;
        calibrationDisabled.set(control, control.disabled); control.disabled = true;
      }
      $('calibrationControls').hidden = false; $('calibrationExit').disabled = false; $('calibrationEnter').disabled = true;
      showCalibrationRegions(isCalibrationModel() && $('calibrationShowRegions').checked);
      window.__avatarCalibration = calibration;
      drawCalibrationPose();
    } catch (error) { exitCalibration(`无法进入校准：${error.message}`); }
  };
  $('calibrationExit').onclick = () => exitCalibration();
  for (const id of ['calibrationExpression', 'calibrationBaseline', 'calibrationIntensity', 'calibrationBlink', 'calibrationGaze', 'calibrationMouth', 'calibrationManualBrow', 'calibrationBrowLeft', 'calibrationBrowRight',
    'calibrationManualLids', 'calibrationLowerLeft', 'calibrationLowerRight', 'calibrationSmileLeft', 'calibrationSmileRight']) {
    $(id).addEventListener('input', drawCalibrationPose);
  }
  $('calibrationShowRegions').onchange = () => showCalibrationRegions(isCalibrationModel() && $('calibrationShowRegions').checked);
  $('calibrationExport').onclick = () => {
    drawCalibrationPose();
    if (calibration) downloadCalibration(new Blob([JSON.stringify({ manifest: calibrationManifest, snapshot: window.__calibrationSnapshot }, null, 2)], { type: 'application/json' }), 'json');
  };
  $('calibrationImage').onclick = () => {
    drawCalibrationPose();
    if (!calibration) return;
    const canvas = avatar.svg._state?.renderer?.domElement;
    canvas?.toBlob(blob => { if (blob) downloadCalibration(blob, 'png'); });
  };
  window.addEventListener('resize', () => {
    if (calibration && !calibration.isCurrent()) exitCalibration('窗口尺寸变化，重新固定画面后再对照。');
  });
  window.addEventListener('pagehide', () => calibration?.exit(), { once: true });
}
try {
  avatar = await boot();
  dialogueReview = mountDialogueReview({ mount: $('dialogueReviewMount'), avatar, source: 'lab',
    metadata: () => ({ model: modelUrl, page: 'avatar-lab' }), onIntentChange: () => {
      dialogueDemoEpoch++;
      if (dialogueDemo?.comparison) { stopLabPlayback(); avatar.onBargeIn(); avatar.setDialogueState('idle'); }
    } });
  if (new URLSearchParams(location.search).get('avatar') === 'original') $('renderer').value = 'original';
  window.__avatar = avatar;
  controls.forEach(el => el.disabled = false);
  await initializeCalibration();
  for (const emotion of Object.keys(avatar.cfg.emotions || {})) {
    const button = document.createElement('button');
    button.textContent = emotion === 'base' ? '自然' : emotion;
    button.onclick = () => { selectEyeExpression(null); avatar.setEmotion(emotion); $('caption').textContent = `当前情绪：${button.textContent} · 共用现有驱动，模型表情为近似映射`; };
    $('emotions').append(button);
  }
  $('renderer').onchange = switchRenderer;
  $('palette').onchange = () => avatar.svg.setPalette?.($('palette').value);
  const requestedPalette = new URLSearchParams(location.search).get('palette');
  if (['ocean','warm'].includes(requestedPalette)) { $('palette').value = requestedPalette; $('presentation').checked = true; }
  $('seed').onclick = async () => { modelUrl = '/static/models/seed-san.vrm'; $('renderer').value = 'vrm'; await switchRenderer(); };
  $('daily').onclick = async () => { modelUrl = '/static/models/seed-daily.vrm'; $('renderer').value = 'vrm'; $('presentation').checked = true; await switchRenderer(); };
  $('dailyHair').onclick = async () => { modelUrl = '/static/models/seed-daily-hair.vrm'; $('renderer').value = 'vrm'; $('presentation').checked = true; await switchRenderer(); };
  $('dailyEyes').onclick = async () => { modelUrl = '/static/models/seed-daily-eyes.vrm'; $('renderer').value = 'vrm'; $('presentation').checked = true; await switchRenderer(); };
  $('dailyLids').onclick = async () => { modelUrl = '/static/models/seed-daily-lids.vrm'; $('renderer').value = 'vrm'; $('presentation').checked = true; await switchRenderer(); };
  if (new URLSearchParams(location.search).get('model') === 'seed') modelUrl = '/static/models/seed-san.vrm';
  if (new URLSearchParams(location.search).get('model') === 'daily') { modelUrl = '/static/models/seed-daily.vrm'; $('presentation').checked = true; }
  if (new URLSearchParams(location.search).get('model') === 'daily-eyes') { modelUrl = '/static/models/seed-daily-eyes.vrm'; $('presentation').checked = true; }
  if (new URLSearchParams(location.search).get('model') === 'daily-lids') { modelUrl = '/static/models/seed-daily-lids.vrm'; $('presentation').checked = true; }
  $('modelFile').onchange = async () => {
    const file = $('modelFile').files[0];
    if (!file) return;
    if (file.size > 100 * 1024 * 1024) { status.textContent = '模型超过 100 MB，请先优化资源。'; return; }
    const previous = objectUrl;
    objectUrl = URL.createObjectURL(file); modelUrl = objectUrl;
    $('renderer').value = 'vrm';
    await switchRenderer();
    if (previous) URL.revokeObjectURL(previous);
  };
  $('sample').onclick = async () => { modelUrl = '/static/models/sample.vrm'; $('renderer').value = 'vrm'; await switchRenderer(); if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null; $('modelFile').value = ''; };
  $('background').onclick = () => $('viewport').classList.toggle('light');
  $('nod').onclick = () => avatar.gesture.play('nod', avatar.clock);
  $('think').onclick = () => avatar.onMessage({ type: 'thinking' });
  $('interrupt').onclick = () => { stopAudio(); avatar.onBargeIn(); };
  for (const preset of EYE_EXPRESSION_PRESETS) {
    const button = document.createElement('button');
    button.textContent = preset.label;
    button.dataset.eyeExpression = preset.name;
    button.setAttribute('aria-pressed', 'false');
    button.onclick = () => selectEyeExpression(preset.name);
    $('eyeExpressions').append(button);
  }
  document.querySelectorAll('[data-gaze]').forEach(button => button.onclick = () => selectGazeMode(button.dataset.gaze));
  $('eyeInherit').onclick = () => selectEyeExpression(null);
  $('eyeBlink').onclick = () => avatar.blinkEyes();
  $('eyeFocus').onchange = () => avatar.svg.setEyeFocus?.($('eyeFocus').checked);
  $('eyeIntensity').oninput = () => {
    $('eyeIntensityValue').value = $('eyeIntensity').value + '%';
    avatar.setEyeExpressionIntensity(Number($('eyeIntensity').value) / 100);
  };
  $('eyeHighlight').oninput = () => {
    $('eyeHighlightValue').value = $('eyeHighlight').value + '%';
    avatar.setEyeHighlight(Number($('eyeHighlight').value) / 100);
  };
  $('follow').onchange = () => selectGazeMode($('follow').checked ? 'follow' : 'auto');
  $('dialogueGaze').checked = avatar.dialogueGaze.enabled;
  $('dialogueGaze').onchange = () => avatar.setDialogueGazeEnabled($('dialogueGaze').checked);
  const requestedStyle = new URLSearchParams(location.search).get('style');
  if (requestedStyle) avatar.setDialogueStyle(requestedStyle);
  $('dialogueStyle').value = avatar.dialogueGaze.style;
  $('dialogueStyle').onchange = () => {
    // A selection also cancels a startup still awaiting AudioContext.resume().
    // An ordinary running demo keeps its audio clips and changes style live.
    dialogueDemoEpoch++;
    if (dialogueDemo?.comparison) {
      stopLabPlayback(); avatar.onBargeIn(); avatar.setDialogueState('idle');
      $('dialogueStatus').textContent = '已停止自动对比，可单独体验当前风格';
    }
    avatar.setDialogueStyle($('dialogueStyle').value);
    dialogueReview?.event({ type: 'style', turn_id: labReviewTurn, meta: { style: avatar.dialogueGaze.style } });
  };
  $('dialogueStop').onclick = () => {
    stopLabPlayback();
    avatar.onMessage({type:'recording',state:false});
    avatar.onBargeIn();
    avatar.setDialogueState('idle');
    $('dialogueStatus').textContent = '流程已停止，恢复自然播放';
  };
  async function playDialogueDemo(comparison = false) {
    stopLabPlayback();
    const epoch = dialogueDemoEpoch;
    await audioContext();
    if (epoch !== dialogueDemoEpoch || calibration || !window.__avatarReady) return;
    selectEyeExpression(null); selectGazeMode('natural');
    avatar.setEmotion('base'); avatar.setDialogueState('idle');
    avatar.attachOutputAnalyser(null);
    if (comparison) { avatar.setDialogueGazeEnabled(true); $('dialogueGaze').checked = true; }
    const kind = comparison ? 'normal' : $('dialogueExample').value;
    const styles = comparison ? DIALOGUE_GAZE_STYLES.map(item=>item.id) : [$('dialogueStyle').value];
    const steps = [], startedAt = ctx.currentTime;
    let offset = 0;
    styles.forEach((style,index) => {
      const at = offset, speakingAt = kind === 'delay' ? 8 : kind === 'short' ? 3.6 : 5.4;
      const end = kind === 'interrupt' ? speakingAt+3.2 : speakingAt+4.8;
      const input = kind === 'short' ? '这个办法可以吗？' : kind === 'multi' ? '先说结论，再解释一下。' : '你怎么看这个办法？';
      const text = kind === 'short' ? '可以，我们试试。' : '我想了一下，这个办法可以试试。';
      const scenario = kind === 'delay' ? 'long' : kind === 'multi' ? 'multi' : kind === 'interrupt' ? 'interrupt' : 'short';
      const thinkingAt = kind === 'short' ? 1.5 : 2.4;
      const generatedAt = kind === 'short' ? 2.6 : 4;
      steps.push({at, action:()=>{
        endLabReview('complete');
        labReviewTurn = `lab-dialogue-${++labReviewSequence}`;
        dialogueReview?.beginTurn({ id: labReviewTurn, input, scenario });
        avatar.onBargeIn(); avatar.setDialogueState('idle'); avatar.setDialogueStyle(style);
        avatar.dialogueGaze.resetVariation('lab-style-compare');
        $('dialogueStyle').value = style;
        window.__dialogueComparison = { active: comparison, index, total: styles.length, style, elapsed: 0 };
        labMessage({type:'recording',state:true});
      }},
      {at:at+thinkingAt, action:()=>{labMessage({type:'recording',state:false});labMessage({type:'thinking'});}},
      // Generated emotion/text deliberately lead the actual audio boundary.
      {at:at+generatedAt-.4, action:()=>labMessage({type:'emotion',emotion:'base'})},
      {at:at+generatedAt, action:()=>labMessage({type:'text_delta',delta:text})},
      {at:at+generatedAt+.2, action:()=>dialogueReview?.event({type:'done',turn_id:labReviewTurn,text})},
      {at:at+speakingAt, action:()=>labPlaybackStart(kind === 'multi' ? '我想了一下。' : text,startedAt+at+speakingAt,kind === 'multi' ? 1.4 : 2.8)});
      if (kind === 'interrupt') {
        steps.push({at:at+speakingAt+1.2, action:()=>{
          dialogueReview?.event({type:'interrupted',turn_id:labReviewTurn});labPlaybackEnd('interrupted');avatar.onBargeIn();labMessage({type:'recording',state:true});
        }});
      } else if (kind === 'multi') {
        steps.push({at:at+speakingAt+1.4, action:()=>{labPlaybackEnd();avatar.stopSpeaking({turnPending:true});}},
          {at:at+speakingAt+2.2, action:()=>labPlaybackStart('这个办法可以试试。',startedAt+at+speakingAt+2.2,1.5)},
          {at:at+speakingAt+3.7, action:()=>{labPlaybackEnd();avatar.stopSpeaking();labMessage({type:'recording',state:true});}});
      } else {
        steps.push({at:at+speakingAt+2.8, action:()=>{labPlaybackEnd();avatar.stopSpeaking();labMessage({type:'recording',state:true});}});
      }
      steps.push({at:at+end, action:()=>{labMessage({type:'recording',state:false});avatar.setDialogueState('idle');endLabReview(kind === 'interrupt' ? 'interrupted' : 'complete');}});
      offset += end;
    });
    dialogueDemo = {startedAt,steps,step:0,kind,end:offset,comparison};
    $('dialogueStatus').textContent = comparison ? '开始三档对比 · 静音演示' : '流程开始 · 静音演示';
  }
  $('dialogueDemo').onclick = () => playDialogueDemo();
  $('dialogueCompare').onclick = () => playDialogueDemo(true);
  $('reduced').checked = avatar.reducedMotion;
  $('reduced').onchange = () => avatar.setReducedMotion($('reduced').checked);
  selectGazeMode(new URLSearchParams(location.search).get('dialogue') === '1' ? 'natural' : 'follow');
  $('performanceProbeStop').disabled = $('performanceProbeExport').disabled = true;
  $('performanceProbeStart').onclick = async () => {
    if (performanceProbe?.active) return;
    if (calibration) exitCalibration('自然动作已恢复，开始测量实际播放。');
    const probe = new AvatarPerformanceProbe(avatar); performanceProbe = probe;
    window.__avatarPerformanceProbe = probe;
    $('performanceProbeStart').disabled = true; $('performanceProbeStop').disabled = false;
    $('performanceProbeStatus').textContent = '正在采集正常播放，请保持窗口可见；表情与口型仍可操作。';
    try {
      probe.start({ durationMs: Number($('performanceProbeDuration').value)*1000, name:'interactive', metadata:{modelUrl} });
      performanceResult = await probe.complete(); window.__avatarPerformanceResult = performanceResult;
      const s = performanceResult.summary, ms = value => Number.isFinite(value) ? value.toFixed(2)+' ms' : '不可用';
      const kind = performanceResult.environment.renderingClass === 'software' ? '软件渲染'
        : performanceResult.environment.renderingClass === 'unknown' ? '未识别渲染环境' : '浏览器报告非软件渲染';
      const label = performanceResult.stopReason === 'duration-complete' ? '采样完成' : '采样提前结束';
      $('performanceProbeStatus').textContent = `${label} · 实际播放 ${Number.isFinite(s.observedAvatarFramesPerSecond) ? s.observedAvatarFramesPerSecond.toFixed(1) : '无有效'} FPS · 帧间隔 P95 ${ms(s.rafCadence.p95Ms)} · CPU P95 ${ms(s.avatarFrameCpuWall.p95Ms)} · GPU P95 ${ms(s.gpuRenderCommands?.p95Ms)}。${kind}；${performanceResult.measurement.gpu.reason || 'GPU计时已采样'}。`;
      $('performanceProbeExport').disabled = false;
    } catch (error) { $('performanceProbeStatus').textContent = `采样未完成：${error.message}`; }
    finally { if (performanceProbe === probe) { performanceProbe = null; $('performanceProbeStart').disabled = false; $('performanceProbeStop').disabled = true; } }
  };
  $('performanceProbeStop').onclick = () => performanceProbe?.stop('user-stopped');
  $('performanceProbeExport').onclick = () => {
    if (!performanceResult) return;
    const link = document.createElement('a'), url = URL.createObjectURL(new Blob([JSON.stringify(performanceResult,null,2)],{type:'application/json'}));
    link.href = url; link.download = `avatar-performance-${Date.now()}.json`; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  window.addEventListener('pagehide', () => performanceProbe?.stop('pagehide'), {once:true});
  $('viewport').onpointermove = (e) => { if (calibration || !$('follow').checked) return; const r = e.currentTarget.getBoundingClientRect(); avatar.onUserInteraction('mousemove', (e.clientX-r.left)/r.width*2-1, (e.clientY-r.top)/r.height*2-1); };
  $('viewport').onpointerleave = () => { if (!calibration && avatar.gazeMode === 'follow') avatar.resetGaze(); };
  async function audioContext() { ctx ||= new AudioContext(); await ctx.resume(); return ctx; }
  $('presentation').onchange = () => avatar.svg.setPresentation?.($('presentation').checked);
  $('face-shading').onchange = () => { avatar.svg.setFaceShading?.($('face-shading').checked); faceShadingStatus(); };
  for (const [id,name] of [['gestureWave','wave'],['gesturePalm','palm'],['gestureDeny','deny'],['gestureSelf','self']]) {
    $(id).onclick = async () => { if (transitionDemo) stopScenario(); avatar.svg.setHandPosePreview?.(null); await audioContext(); $('eyeFocus').checked = false; avatar.svg.setEyeFocus?.(false); avatar.performance.preview(name,ctx); };
  }
  let scenarioTimers=[];
  const stopScenario=()=>{scenarioTimers.forEach(clearTimeout);scenarioTimers=[];stopAudio();avatar.onBargeIn();$('scenarioStatus').textContent='流程已停止，回到倾听';};
  stopLabPlayback = stopScenario;
  $('stopScenario').onclick=stopScenario;
  $('scenario').onclick=async()=>{
    stopScenario();await audioContext();selectEyeExpression(null);selectGazeMode('natural');
    avatar.setEmotion('base');$('scenarioStatus').textContent='1 / 安静待机';
    const at=(ms,fn)=>scenarioTimers.push(setTimeout(fn,ms));
    at(1000,()=>{avatar.onMessage({type:'recording',state:true});$('scenarioStatus').textContent='2 / 专注倾听';});
    at(3500,()=>{avatar.onMessage({type:'recording',state:false});avatar.onMessage({type:'thinking'});$('scenarioStatus').textContent='3 / 思考';});
    at(5500,()=>{avatar.onMessage({type:'emotion',emotion:'调侃'});avatar.speak('你这计划，真的能今天做完吗？',ctx,ctx.currentTime,4);$('scenarioStatus').textContent='4 / 调侃回答与手势（静音口型，不是 TTS）';});
    at(7600,()=>{avatar.onBargeIn();avatar.onMessage({type:'recording',state:true});$('scenarioStatus').textContent='5 / 用户打断，停止口型并收回手势';});
    at(10000,()=>{avatar.onMessage({type:'recording',state:false});avatar.setDialogueState('idle');$('scenarioStatus').textContent='流程结束 · 可重复播放';});
  };
  $('interrupt').onclick=stopScenario;
  for (const [id, name] of [['handPoseRelaxed', 'relaxed'], ['handPoseOpen', 'open'], ['handPoseSoftGrip', 'softGrip']]) {
    $(id).onclick = async () => {
      stopScenario();
      const epoch = dialogueDemoEpoch, rig = avatar.svg;
      await audioContext();
      if (epoch !== dialogueDemoEpoch || calibration || !window.__avatarReady ||
        avatar.svg !== rig || !rig.getHandPosePreview?.().enabled) return;
      $('eyeFocus').checked = false; avatar.svg.setEyeFocus?.(false);
      avatar.performance.preview('palm', ctx);
      avatar.svg.setHandPosePreview?.(name, 'right');
      handPoseStatus();
    };
  }
  $('handPoseAuto').onclick = () => { stopScenario(); avatar.svg.setHandPosePreview?.(null); handPoseStatus(); };
  window.addEventListener('pagehide',()=>scenarioTimers.forEach(clearTimeout),{once:true});
  window.addEventListener('pagehide',()=>{endLabReview('pagehide');dialogueReview?.stop('pagehide');},{once:true});
  $('speak').onclick = async () => { stopAudio(); await audioContext(); avatar.attachOutputAnalyser(null); avatar.speak($('speechText').value, ctx, ctx.currentTime, 5); $('caption').textContent = '静音口型演示 · 5 秒时间轴（不是合成语音）'; };
  const performanceExamples = {
    suggest: { text: '我觉得，这个办法可以试试。', cues: [
      { intent: 'self_reference', gesture: 'self', anchor: '我觉得', start_char: 0, end_char: 3, strength: .4 },
      { intent: 'explanation', gesture: 'palm', anchor: '这个办法', start_char: 4, end_char: 8, strength: .4 },
    ] },
    greeting: { text: '你好，很高兴又见到你。', cues: [
      { intent: 'greeting', gesture: 'wave', anchor: '你好', start_char: 0, end_char: 2, strength: .45 },
    ] },
    quiet: { text: '我不是不喜欢这个办法，我们可以慢慢看看。', cues: [] },
  };
  let performanceDemoEnds = null;
  $('performanceDemo').onclick = async () => {
    stopScenario(); await audioContext(); selectEyeExpression(null); selectGazeMode('forward');
    $('eyeFocus').checked = false; avatar.svg.setEyeFocus?.(false);
    avatar.setEmotion('base'); avatar.attachOutputAnalyser(null);
    const example = performanceExamples[$('performanceExample').value];
    $('speechText').value = example.text;
    performanceDemoEnds = ctx.currentTime + 6;
    avatar.speak(example.text, ctx, ctx.currentTime, 6, {
      performance: { version: 1, source: 'llm', cues: example.cues },
    });
    $('caption').textContent = example.text + ' · 预设动作意图演示（静音）';
  };
  $('poseTransitions').onchange = () => avatar.svg.setPerformanceTransitions?.($('poseTransitions').checked);
  $('gestureCalibration').onchange = () => { avatar.svg.setGestureCalibration?.($('gestureCalibration').checked); gestureCalibrationStatus(); gesturePathStatus(); };
  $('gesturePaths').onchange = () => { avatar.svg.setGesturePaths?.($('gesturePaths').checked); gesturePathStatus(); };
  $('fingerRelaxation').onchange = () => { avatar.svg.setFingerRelaxation?.($('fingerRelaxation').checked); fingerRelaxationStatus(); };
  $('bodyCoordination').onchange = () => { avatar.svg.setBodyCoordination?.($('bodyCoordination').checked); bodyCoordinationStatus(); };
  $('transitionStop').onclick = stopScenario;
  $('transitionDemo').onclick = async () => {
    stopScenario(); await audioContext(); selectEyeExpression(null); selectGazeMode('forward');
    $('eyeFocus').checked = false; avatar.svg.setEyeFocus?.(false); avatar.setEmotion('base');
    const wave = $('transitionExample').value === 'wave-stop';
    transitionDemo = { startedAt: ctx.currentTime, step: 0,
      steps: wave ? [{ at: .95, action: null }] : [{ at: .8, action: 'palm' }, { at: 1.75, action: null }],
      endsAt: ctx.currentTime + 3 };
    avatar.performance.preview(wave ? 'wave' : 'self', ctx);
    $('caption').textContent = wave ? '招呼 → 中途打断 · 固定流程对照（静音）' : '自我强调 → 摊掌 → 收回 · 固定流程对照（静音）';
  };
  $('audioFile').onchange = async () => {
    const file = $('audioFile').files[0]; if (!file) return;
    try {
      stopAudio(); await audioContext();
      const buffer = await ctx.decodeAudioData(await file.arrayBuffer());
      const analyser = ctx.createAnalyser(); analyser.fftSize = 1024;
      source = ctx.createBufferSource(); source.buffer = buffer;
      source.connect(analyser); analyser.connect(ctx.destination);
      avatar.attachOutputAnalyser(analyser);
      const startedAt = ctx.currentTime + 0.05;
      source.start(startedAt); avatar.speak($('speechText').value, ctx, startedAt, buffer.duration);
      source.onended = () => { avatar.stopSpeaking(); analyser.disconnect(); };
      $('caption').textContent = '本地音频播放 · 请确保文本与音频一致';
    } catch (e) { status.textContent = `音频加载失败：${e.message}`; }
  };
  let frames = 0, since = performance.now();
  function measure(now) {
    frames++;
    handPoseStatus();
    if (dialogueDemo && ctx && !calibration) {
      const demo = dialogueDemo;
      while (demo.step < demo.steps.length && ctx.currentTime - demo.startedAt >= demo.steps[demo.step].at)
        demo.steps[demo.step++].action();
      const frame = avatar.dialogueFrame;
      const labels = {idle:'自然播放',monitoring:'等待你说话',listening:'认真听',thinking:'思考',preparing:'准备回应',speaking:'开始回应',yielding:'回看并让出话轮'};
      const phases = {waiting:'保持关注',averted:'短暂移视',returned:'回看',attending:'保持关注',speaking:'跟随声音',yielding:'回看'};
      const styleLabel = DIALOGUE_GAZE_STYLES.find(item=>item.id === avatar.dialogueGaze.style)?.label || '自然';
      const progress = demo.comparison ? `对比 ${window.__dialogueComparison.index+1}/3 · ` : '';
      window.__dialogueComparison = { ...window.__dialogueComparison, elapsed: ctx.currentTime-demo.startedAt };
      $('dialogueStatus').textContent = `${progress}${styleLabel} · ${labels[frame?.state] || '准备中'} · ${phases[frame?.phase] || ''} · 静音演示`;
      if (demo.step === demo.steps.length) {
        dialogueDemo = null; window.__dialogueComparison = { ...window.__dialogueComparison, active: false };
        $('dialogueStatus').textContent = demo.comparison ? '三档对比完成，可选择喜欢的风格单独体验' : '流程完成，可切换流程或关闭交流眼神作对照';
      }
    }
    if (transitionDemo && ctx) {
      const demo = transitionDemo;
      while (demo.step < demo.steps.length && ctx.currentTime - demo.startedAt >= demo.steps[demo.step].at) {
        const action = demo.steps[demo.step++].action;
        if (action) avatar.performance.preview(action, ctx); else avatar.onBargeIn();
      }
      const value = avatar.svg.getPerformanceTransition?.();
      const labels = { self: '自我强调', palm: '摊掌', wave: '招呼', deny: '否定', rest: '自然姿态' };
      $('transitionStatus').textContent = value
        ? `${labels[value.from]} → ${labels[value.to]} · ${Math.round(value.duration * 1000)} 毫秒 · ${value.active ? '衔接中' : '已到达'}`
        : '使用原有逐帧平滑 · 固定流程对照';
      if (ctx.currentTime >= demo.endsAt) { transitionDemo = null; $('transitionStatus').textContent += ' · 流程结束'; }
    }
    if (performanceDemoEnds !== null) {
      const frame = avatar.performance.sample();
      const actionNames = { self: '自我强调', palm: '摊掌', wave: '招呼', deny: '否定' };
      if (!avatar.lip.speaking || !ctx || ctx.currentTime >= performanceDemoEnds) {
        $('performanceStatus').textContent = ctx?.currentTime >= performanceDemoEnds ? '演示结束 · 可重复播放' : '演示已停止 · 后续动作已取消';
        performanceDemoEnds = null;
      } else {
        $('performanceStatus').textContent = frame.name
          ? `${actionNames[frame.name]} · 对应“${frame.anchor}” · ${frame.alignment === 'provider' ? '语音时间戳' : '估算对齐'}`
          : '跟随台词播放 · 估算对齐 · 保持自然';
      }
    }
    if (now-since > 1000) {
      $('metrics').textContent = calibration ? '校准画面已固定 · 自然动画暂停' : `页面刷新约 ${Math.round(frames*1000/(now-since))} FPS · ${avatar.rendererKind.toUpperCase()}（非 GPU 耗时）`;
      frames=0; since=now;
    }
    requestAnimationFrame(measure);
  }
  requestAnimationFrame(measure);
  await switchRenderer();
  window.addEventListener('pagehide', () => { stopAudio(); avatar.dispose(); ctx?.close(); if(objectUrl) URL.revokeObjectURL(objectUrl); }, { once:true });
} catch (error) { status.textContent = `启动失败：${error.message}`; console.error(error); }
