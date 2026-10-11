/* 用户面部反应驱动：角色镜像用户的表情、视线、头部动作。
 *
 * 用 MediaPipe FaceLandmarker 检测用户的 52 个 ARKit blendshape 系数
 * 和 478 个面部标志点，映射到角色的参数上。
 *
 * 关键设计：
 *   - 所有值都做 EMA 平滑，避免摄像头噪声导致角色抽搐
 *   - 用叠加而不是覆盖，这样情绪层和面部反应层可以同时生效
 *   - 头部旋转从 facial transformation matrix 提取，比标志点稳定
 *   - 默认关闭（隐私），需要用户显式授权摄像头
 */

import { clamp, lerp } from "../params.js";

const BLENDSHAPE_MAP = {
  browInnerUp: "browInnerUp",
  browDownLeft: "browDownLeft",
  browDownRight: "browDownRight",
  browOuterUpLeft: "browOuterUpLeft",
  browOuterUpRight: "browOuterUpRight",
  cheekPuff: "cheekPuff",
  eyeBlinkLeft: "eyeBlinkLeft",
  eyeBlinkRight: "eyeBlinkRight",
  eyeSquintLeft: "eyeSquintLeft",
  eyeSquintRight: "eyeSquintRight",
  eyeWideLeft: "eyeWideLeft",
  eyeWideRight: "eyeWideRight",
  jawOpen: "jawOpen",
  mouthSmileLeft: "mouthSmileLeft",
  mouthSmileRight: "mouthSmileRight",
  mouthFrownLeft: "mouthFrownLeft",
  mouthFrownRight: "mouthFrownRight",
  mouthPucker: "mouthPucker",
  mouthFunnel: "mouthFunnel",
  mouthShrugUpper: "mouthShrugUpper",
  mouthShrugLower: "mouthShrugLower",
  mouthPressLeft: "mouthPressLeft",
  mouthPressRight: "mouthPressRight",
  mouthStretchLeft: "mouthStretchLeft",
  mouthStretchRight: "mouthStretchRight",
  mouthRollUpper: "mouthRollUpper",
  mouthRollLower: "mouthRollLower",
  mouthClose: "mouthClose",
  mouthUpperUpLeft: "mouthUpperUpLeft",
  mouthUpperUpRight: "mouthUpperUpRight",
  mouthLowerDownLeft: "mouthLowerDownLeft",
  mouthLowerDownRight: "mouthLowerDownRight",
  mouthDimpleLeft: "mouthDimpleLeft",
  mouthDimpleRight: "mouthDimpleRight",
  mouthFunneler: "mouthFunneler",
  mouthLeft: "mouthLeft",
  mouthRight: "mouthRight",
  mouthSmile: "mouthSmile",
  mouthFrown: "mouthFrown",
  tongueOut: "tongueOut",
};

export class FaceDriver {
  constructor() {
    this.landmarker = null;
    this.video = null;
    this.running = false;
    this.stream = null;
    this.lastVideoTime = -1;

    // 平滑后的输出
    this.smile = 0;
    this.frown = 0;
    this.browRaise = 0;
    this.browFurrow = 0;
    this.eyeBlinkL = 0;
    this.eyeBlinkR = 0;
    this.eyeWide = 0;
    this.jawOpen = 0;
    this.headYaw = 0;
    this.headPitch = 0;
    this.headRoll = 0;
    this.gazeX = 0;
    this.gazeY = 0;

    // 内部原始值（未平滑）
    this._raw = {};
  }

  async start(videoEl) {
    if (this.running) return true;
    const mod = await import("/static/vendor/mediapipe/vision_bundle.mjs");
    const fileset = await mod.FilesetResolver.forVisionTasks(
      "/static/vendor/mediapipe/wasm"
    );
    this.landmarker = await mod.FaceLandmarker.createFromOptions(fileset, {
      baseOptions: {
        modelAssetPath: "/static/vendor/mediapipe/face_landmarker.task",
        delegate: "GPU",
      },
      outputFaceBlendshapes: true,
      outputFacialTransformationMatrixes: true,
      runningMode: "VIDEO",
      numFaces: 1,
    });
    this.video = videoEl;
    this.stream = await navigator.mediaDevices.getUserMedia({
      video: { width: 320, height: 240, facingMode: "user" },
      audio: false,
    });
    videoEl.srcObject = this.stream;
    await videoEl.play();
    this.running = true;
    return true;
  }

  stop() {
    this.running = false;
    if (this.stream) {
      for (const t of this.stream.getTracks()) t.stop();
      this.stream = null;
    }
    if (this.video) this.video.srcObject = null;
    this.landmarker = null;
  }

  /* 每帧调用。返回要叠加到角色参数上的增量。 */
  sample() {
    if (!this.running || !this.landmarker || !this.video) return null;
    if (this.video.readyState < 2) return null;
    if (this.video.currentTime === this.lastVideoTime) return this._output();
    this.lastVideoTime = this.video.currentTime;

    let result;
    try {
      result = this.landmarker.detectForVideo(this.video, performance.now());
    } catch {
      return this._output();
    }

    const faces = result.faceBlendshapes;
    if (!faces || !faces.length) return this._output();

    const shapes = {};
    for (const cat of faces[0].categories) {
      shapes[cat.categoryName] = cat.score;
    }
    this._raw = shapes;

    // 头部旋转从变换矩阵提取（比标志点稳定）
    const matrices = result.facialTransformationMatrixes;
    if (matrices && matrices.length) {
      const m = matrices[0].data;
      // 旋转矩阵 → 欧拉角（弧度）
      this.headPitch = Math.asin(clamp(-m[6], -1, 1));
      this.headYaw = Math.atan2(m[2], m[10]);
      this.headRoll = Math.atan2(m[4], m[5]);
    }

    // 视线方向从眼睛标志点估算
    const landmarks = result.faceLandmarks;
    if (landmarks && landmarks.length) {
      const pts = landmarks[0];
      // 左眼中心 (33) 和右眼中心 (263)，以及鼻梁 (1)
      const lx = (pts[33].x + pts[133].x) / 2;
      const ly = (pts[33].y + pts[133].y) / 2;
      const rx = (pts[263].x + pts[362].x) / 2;
      const ry = (pts[263].y + pts[362].y) / 2;
      const nx = pts[1].x;
      const ny = pts[1].y;
      // 眼睛相对于鼻梁的偏移 → 视线方向
      const eyeMidX = (lx + rx) / 2;
      const eyeMidY = (ly + ry) / 2;
      this.gazeX = clamp((eyeMidX - nx) * 8, -1, 1);
      this.gazeY = clamp((eyeMidY - ny) * 8, -1, 1);
    }

    // EMA 平滑
    const k = 0.35;
    this.smile = lerp(this.smile, (shapes.mouthSmileLeft || 0) + (shapes.mouthSmileRight || 0), k);
    this.frown = lerp(this.frown, (shapes.mouthFrownLeft || 0) + (shapes.mouthFrownRight || 0), k);
    this.browRaise = lerp(
      this.browRaise,
      (shapes.browInnerUp || 0) + (shapes.browOuterUpLeft || 0) + (shapes.browOuterUpRight || 0),
      k
    );
    this.browFurrow = lerp(
      this.browFurrow,
      (shapes.browDownLeft || 0) + (shapes.browDownRight || 0),
      k
    );
    this.eyeBlinkL = lerp(this.eyeBlinkL, shapes.eyeBlinkLeft || 0, k);
    this.eyeBlinkR = lerp(this.eyeBlinkR, shapes.eyeBlinkRight || 0, k);
    this.eyeWide = lerp(
      this.eyeWide,
      (shapes.eyeWideLeft || 0) + (shapes.eyeWideRight || 0),
      k
    );
    this.jawOpen = lerp(this.jawOpen, shapes.jawOpen || 0, k);

    return this._output();
  }

  _output() {
    return {
      smile: this.smile,
      frown: this.frown,
      browRaise: this.browRaise,
      browFurrow: this.browFurrow,
      eyeBlinkL: this.eyeBlinkL,
      eyeBlinkR: this.eyeBlinkR,
      eyeWide: this.eyeWide,
      jawOpen: this.jawOpen,
      headYaw: this.headYaw,
      headPitch: this.headPitch,
      headRoll: this.headRoll,
      gazeX: this.gazeX,
      gazeY: this.gazeY,
    };
  }

  /* 把面部反应叠加到 rig 上。face 层优先级最低（5），
   * 这样情绪/手势/口型都能覆盖它。 */
  apply(rig) {
    const f = this.sample();
    if (!f) return;
    // 微笑/皱眉 → 嘴形
    rig.add("face", "ParamMouthForm", f.smile * 0.6 - f.frown * 0.4);
    // 挑眉/皱眉 → 眉毛
    rig.add("face", "ParamBrowLY", f.browRaise * 0.5 - f.browFurrow * 0.3);
    rig.add("face", "ParamBrowRY", f.browRaise * 0.5 - f.browFurrow * 0.3);
    // 眨眼 → 眼睛（叠加到情绪的睁眼度上）
    rig.add("face", "ParamEyeLOpen", -f.eyeBlinkL * 0.8);
    rig.add("face", "ParamEyeROpen", -f.eyeBlinkR * 0.8);
    // 张嘴 → 口型（只在用户张嘴时叠加，不覆盖说话时的口型）
    if (f.jawOpen > 0.15) {
      rig.add("face", "ParamMouthOpenY", f.jawOpen * 0.4);
    }
    // 头部旋转 → 叠加到身体/头部
    rig.add("face", "ParamAngleX", f.headYaw * 0.35);
    rig.add("face", "ParamAngleY", f.headPitch * 0.3);
    rig.add("face", "ParamAngleZ", f.headRoll * 0.25);
    // 视线 → 眼珠
    rig.add("face", "ParamEyeBallX", f.gazeX * 0.5);
    rig.add("face", "ParamEyeBallY", f.gazeY * 0.4);
  }
}