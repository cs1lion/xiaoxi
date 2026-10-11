/* 程序化 SVG 渲染器。
 *
 * 没有美术素材：脸、头发、眼睛、嘴都是按参数现算出来的路径。
 * 参数名和驱动层解耦，换 Live2D/VRM 时只重写这一层。
 *
 * 三维感的实现是 2D 惯用手法：
 *   偏航 AngleX -> 横向压扁 + 五官横向偏移 + 远眼变窄
 *   俯仰 AngleY -> 纵向压扁 + 五官纵向偏移
 *   翻滚 AngleZ -> 直接旋转
 */

import { clamp } from "../params.js";

const NS = "http://www.w3.org/2000/svg";
const DEG = 180 / Math.PI;

/* 脸型基准（局部坐标，头心在 0,0） */
const FACE_TOP = -96;
const FACE_BOTTOM = 96;
const EYE_X = 33;
const EYE_Y = -8;
const EYE_W = 17;
const EYE_TOP = -19;
const EYE_BOT = 17;
const IRIS_R = 8.4;
const MOUTH_Y = 44;

function el(name, attrs, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/* 眼睛：上眼睑下降模拟眨眼，下眼睑基本不动。open=0 时收成一道弯。 */
function eyePath(open, w, top, bot, tilt = 0) {
  const o = clamp01(open);
  const cy = (top + bot) / 2;
  if (o < 0.16) {
    // 闭眼：一道平缓的弧。弧度要小，弯太多会看成哭脸
    const y = cy - (0.16 - o) * 34;
    const bow = 6 - (0.16 - o) * 12;
    return `M ${-w} ${y} Q ${-w * 0.5} ${y - bow} 0 ${y - bow * 0.7} ` +
           `Q ${w * 0.5} ${y - bow} ${w} ${y}`;
  }
  const yTop = top + (bot - top) * (1 - o);
  const t = tilt;
  return `M ${-w} ${cy} Q ${-w * 0.58} ${yTop + t} 0 ${yTop + t} ` +
         `Q ${w * 0.58} ${yTop + t} ${w} ${cy} ` +
         `Q ${w * 0.62} ${bot} 0 ${bot} Q ${-w * 0.58} ${bot} ${-w} ${cy} Z`;
}

/* 眼睑线：只在半闭时画粗，不然眼睛会像贴纸 */
function lidPath(open, w, top, bot) {
  const o = clamp01(open);
  const cy = (top + bot) / 2;
  const yTop = top + (bot - top) * (1 - o);
  return `M ${-w} ${cy} Q ${-w * 0.58} ${yTop} 0 ${yTop} Q ${w * 0.58} ${yTop} ${w} ${cy}`;
}

/* 眉毛。
 * ParamBrow*Form 的语义（本项目约定，与 Live2D 的方向保持一致）：
 *   > 0 皱眉 —— 内侧下压并向中间靠
 *   < 0 挑眉 —— 内侧上抬，整条眉弓起来
 * ParamBrow*Angle 控外端单独的高低（惊讶/怀疑）。 */
function browPath(y, angle, form, mirror) {
  const s = mirror ? -1 : 1;
  const innerX = -21 * s * (1 - form * 0.2);
  const outerX = 21 * s;
  const press = -form * 9;
  const tilt = angle * 11;
  const x0 = innerX;
  const y0 = -20 + y * 11 + press;
  const x1 = outerX;
  const y1 = -28 + y * 11 - tilt * 0.5;
  return `M ${x0} ${y0} Q ${(x0 + x1) / 2} ${(y0 + y1) / 2 - 7 - tilt} ${x1} ${y1}`;
}

function mouthPath(open, form, round, y) {
  const form01 = clamp(form, -1, 1);
  const round01 = clamp(round, -1, 1);
  const w = 21 * (1 + 0.44 * form01) * (1 - 0.26 * Math.max(0, round01));
  const top = MOUTH_Y + y * 9;

  if (open < 0.07) {
    // 闭嘴：一道弧。form 为正=嘴角上扬，round 为正=嘟嘴更圆
    const curve = 9 * form01 + 4 * round01;
    return {
      fill: null,
      d: `M ${-w} ${top} Q 0 ${top + curve} ${w} ${top}`,
    };
  }
  const h = 2.5 + 25 * clamp01(open);
  const bulge = 1 - 0.42 * Math.max(0, round01);
  return {
    fill: true,
    d: `M ${-w} ${top} Q ${-w * 0.5} ${top - h * 0.86 * bulge} 0 ${top - h * bulge} ` +
       `Q ${w * 0.5} ${top - h * 0.86 * bulge} ${w} ${top} ` +
       `Q ${w * 0.55} ${top + h * 0.74 * bulge} 0 ${top + h * 0.74 * bulge} ` +
       `Q ${-w * 0.55} ${top + h * 0.74 * bulge} ${-w} ${top} Z`,
  };
}

export class SvgRig {
  constructor() {
    this.root = null;
    this.nodes = {};
  }

  mount(container, look = {}) {
    const L = {
      skin: "#f6ddd2", shade: "#e8c3b4", hair: "#4a4256", hairHi: "#6b6079",
      blush: "#f0908a", lip: "#c96a63", mouthIn: "#8e3b3b", cloth: "#5b6bb5",
      clothDark: "#46528c", eye: "#3b3348", glow: "#5aa7c9",
      ...look,
    };

    const svg = el("svg", {
      viewBox: "0 0 420 420",
      class: "avatar-svg",
      "aria-hidden": "true",
    });
    const defs = el("defs", {}, svg);

    const glow = el("radialGradient", { id: "av-glow" }, defs);
    el("stop", { offset: "0%", "stop-color": L.glow, "stop-opacity": "0.30" }, glow);
    el("stop", { offset: "70%", "stop-color": L.glow, "stop-opacity": "0.05" }, glow);
    const skin = el("radialGradient", { id: "av-skin" }, defs);
    el("stop", { offset: "0%", "stop-color": "#fff6f0" }, skin);
    el("stop", { offset: "100%", "stop-color": L.skin }, skin);

    const n = this.nodes;
    el("ellipse", { cx: 210, cy: 210, rx: 190, ry: 186, fill: "url(#av-glow)" }, svg);

    const stage = el("g", { transform: "translate(210 212)" }, svg);
    n.stage = stage;

    /* --- 身体 --- */
    const body = el("g", {}, stage);
    n.body = body;
    el("path", {
      d: "M -128 210 C -120 126 -62 96 0 96 C 62 96 120 126 128 210 Z",
      fill: L.cloth,
    }, body);
    el("path", {
      d: "M -26 98 C -20 128 -14 168 -10 210 L 10 210 C 14 168 20 128 26 98 Z",
      fill: L.clothDark, opacity: "0.55",
    }, body);
    el("path", {
      d: "M -34 100 C -22 124 22 124 34 100 C 16 92 -16 92 -34 100 Z",
      fill: L.shade,
    }, body);

    /* --- 头 --- */
    const head = el("g", {}, stage);
    n.head = head;
    const roll = el("g", {}, head);
    n.roll = roll;

    /* 后发：从头顶顺着两侧垂到肩，带一点外扩的弧度 */
    el("path", {
      d: "M -100 -44 C -124 24 -112 128 -84 200 L -50 200 " +
         "C -76 126 -92 48 -84 -46 Z",
      fill: L.hair,
    }, roll);
    el("path", {
      d: "M 100 -44 C 124 24 112 128 84 200 L 50 200 " +
         "C 76 126 92 48 84 -46 Z",
      fill: L.hair,
    }, roll);

    /* 脸 */
    el("path", {
      d: "M 0 -98 C 48 -98 82 -58 82 -6 C 82 46 48 92 0 98 " +
         "C -48 92 -82 46 -82 -6 C -82 -58 -48 -98 0 -98 Z",
      fill: "url(#av-skin)", stroke: L.shade, "stroke-width": "2",
    }, roll);
    el("ellipse", { cx: 0, cy: 12, rx: 74, ry: 84, fill: "none",
                    stroke: L.shade, "stroke-width": "1", opacity: "0.35" }, roll);

    /* 耳朵：贴着脸，别支出去 */
    el("ellipse", { cx: -79, cy: 8, rx: 8, ry: 13, fill: L.skin,
                    stroke: L.shade, "stroke-width": "1.5" }, roll);
    el("ellipse", { cx: 79, cy: 8, rx: 8, ry: 13, fill: L.skin,
                    stroke: L.shade, "stroke-width": "1.5" }, roll);

    /* 五官容器：偏航/俯仰的位移作用在这一层 */
    const features = el("g", {}, roll);
    n.features = features;

    n.blush = [];
    for (const side of [-1, 1]) {
      n.blush.push(el("ellipse", {
        cx: side * 52, cy: 26, rx: 19, ry: 11,
        fill: L.blush, opacity: "0",
      }, features));
    }

    n.brow = [
      el("path", { fill: "none", stroke: L.hair, "stroke-width": "7",
                   "stroke-linecap": "round" }, features),
      el("path", { fill: "none", stroke: L.hair, "stroke-width": "7",
                   "stroke-linecap": "round" }, features),
    ];
    n.browG = [];
    for (const side of [-1, 1]) {
      n.browG.push(el("g", { transform: `translate(${side * EYE_X} ${EYE_Y})` }, features));
    }
    n.browG[0].appendChild(n.brow[0]);
    n.browG[1].appendChild(n.brow[1]);

    n.eyes = [];
    for (const side of [-1, 1]) {
      const g = el("g", { transform: `translate(${side * EYE_X} ${EYE_Y})` }, features);
      const sclera = el("path", { fill: "#fdfbfa" }, g);
      const iris = el("g", {}, g);
      const ball = el("circle", { cx: 0, cy: 0, r: IRIS_R, fill: L.eye }, iris);
      const inner = el("circle", { cx: 0, cy: 1.5, r: IRIS_R * 0.52, fill: "#141018" }, iris);
      const shine = el("circle", { cx: -3.4, cy: -4.2, r: 3.0, fill: "#ffffff" }, iris);
      const lid = el("path", { fill: "none", stroke: L.eye,
                               "stroke-width": "3.4", "stroke-linecap": "round",
                               opacity: "0" }, g);
      n.eyes.push({ g, sclera, iris, ball, lid, side });
    }

    /* 鼻尖 */
    n.nose = el("path", {
      d: "M 3 14 Q 8 22 0 24", fill: "none", stroke: L.shade,
      "stroke-width": "2.4", "stroke-linecap": "round", opacity: "0.8",
    }, features);

    n.mouth = el("path", { fill: L.mouthIn, stroke: L.lip, "stroke-width": "2.6",
                           "stroke-linecap": "round", "stroke-linejoin": "round" }, features);
    n.tongue = el("ellipse", { cx: 0, cy: MOUTH_Y + 12, rx: 12, ry: 6,
                               fill: "#d9736f", opacity: "0" }, features);

    /* 刘海：齐刘海压在额头上沿，不能盖到眉毛（眉毛在 y≈-20..-28） */
    el("path", {
      d: "M -84 -34 C -80 -86 -38 -106 2 -104 C 44 -102 80 -78 84 -32 " +
         "C 74 -54 58 -66 40 -68 C 46 -56 46 -52 42 -48 " +
         "C 26 -62 4 -68 -18 -64 C -40 -60 -62 -52 -72 -40 " +
         "C -78 -36 -82 -34 -84 -34 Z",
      fill: L.hair,
    }, roll);
    el("path", {
      d: "M -58 -72 C -42 -88 -10 -96 20 -92", fill: "none",
      stroke: L.hairHi, "stroke-width": "4", "stroke-linecap": "round", opacity: "0.5",
    }, roll);
    /* 呆毛 */
    n.ahoge = el("path", {
      d: "M 4 -102 C 12 -126 34 -130 40 -116 C 30 -120 16 -114 12 -100",
      fill: "none", stroke: L.hair, "stroke-width": "6", "stroke-linecap": "round",
    }, roll);

    container.appendChild(svg);
    this.root = svg;
    this.look = L;
    return svg;
  }

  /* p: ParamStore.current */
  draw(p) {
    const n = this.nodes;
    if (!n.stage) return;

    const yaw = clamp(p.ParamAngleX);
    const pitch = clamp(p.ParamAngleY);
    const rollA = clamp(p.ParamAngleZ);

    // 呼吸：整块轻微缩放，比单独动胸腔更像在呼吸
    const breath = p.ParamBreath * 0.010 + 0.998;
    n.stage.setAttribute(
      "transform",
      `translate(210 212) scale(${breath.toFixed(4)})`
    );

    // 身体跟头，但幅度更小、更慢
    const bodyRot = clamp(p.ParamBodyAngleX + yaw * 0.28);
    const bodyTilt = clamp(p.ParamBodyAngleZ + rollA * 0.3);
    const lean = clamp(p.ParamBodyLeanX) * 16;
    n.body.setAttribute(
      "transform",
      `translate(${lean.toFixed(2)} 0) rotate(${(bodyTilt * 0.6).toFixed(2)}) ` +
      `scale(${(1 - Math.abs(bodyRot) * 0.05).toFixed(4)} 1)`
    );

    const sx = 1 - Math.abs(yaw) * 0.13;
    const sy = 1 - Math.abs(pitch) * 0.08;
    n.roll.setAttribute(
      "transform",
      `rotate(${(rollA * 7).toFixed(2)}) scale(${sx.toFixed(4)} ${sy.toFixed(4)})`
    );

    // 五官偏移：偏航横向 + 俯仰纵向，转头时五官要"跟过去"
    const fx = yaw * 9;
    const fy = pitch * 6;
    n.features.setAttribute("transform", `translate(${fx.toFixed(2)} ${fy.toFixed(2)})`);

    /* --- 眼睛 --- */
    const gx = clamp(p.ParamEyeBallX);
    const gy = clamp(p.ParamEyeBallY);
    const openL = clamp01(p.ParamEyeLOpen);
    const openR = clamp01(p.ParamEyeROpen);
    for (const eye of n.eyes) {
      const open = eye.side < 0 ? openL : openR;
      const d = eyePath(open, EYE_W, EYE_TOP, EYE_BOT);
      eye.sclera.setAttribute("d", d);
      // 转头时远侧眼睛窄一点
      const depth = 1 - Math.max(0, yaw * eye.side) * 0.22;
      eye.g.setAttribute("transform",
        `translate(${(eye.side * EYE_X).toFixed(2)} ${EYE_Y}) scale(${depth.toFixed(3)} 1)`);
      eye.iris.setAttribute("transform",
        `translate(${(gx * 5.2).toFixed(2)} ${(gy * 3.8).toFixed(2)})`);
      eye.iris.setAttribute("opacity", open < 0.18 ? "0" : "1");
      eye.lid.setAttribute("d", lidPath(open, EYE_W, EYE_TOP, EYE_BOT));
      eye.lid.setAttribute("opacity", open < 0.86 ? "1" : "0");
    }

    /* --- 眉毛 --- */
    n.brow[0].setAttribute("d",
      browPath(clamp(p.ParamBrowLY), clamp(p.ParamBrowLAngle), clamp(p.ParamBrowLForm), false));
    n.brow[1].setAttribute("d",
      browPath(clamp(p.ParamBrowRY), clamp(p.ParamBrowRAngle), clamp(p.ParamBrowRForm), true));

    /* --- 嘴 --- */
    const m = mouthPath(
      clamp01(p.ParamMouthOpenY),
      clamp(p.ParamMouthForm),
      clamp(p.ParamMouthRound),
      clamp(p.ParamMouthY)
    );
    n.mouth.setAttribute("d", m.d);
    n.mouth.setAttribute("fill", m.fill ? this.look.mouthIn : "none");
    n.tongue.setAttribute("opacity", p.ParamMouthOpenY > 0.28 ? "0.85" : "0");

    /* --- 腮红 / 呆毛 --- */
    const cheek = clamp01(p.ParamCheek);
    for (const b of n.blush) b.setAttribute("opacity", (cheek * 0.55).toFixed(3));

    const bob = clamp(p.ParamBodyAngleX) * 6;
    n.ahoge.setAttribute("transform",
      `rotate(${(bob + rollA * 22).toFixed(2)} ${4} -102)`);
  }
}