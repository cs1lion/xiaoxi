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

import { clamp, smoothstep, Spring } from "../params.js";

const NS = "http://www.w3.org/2000/svg";

/* 同一个页面可能挂多个 rig（预览页就是），defs 里的 id 必须唯一，
   否则所有眼睛会共用同一条裁剪路径。 */
let RIG_UID = 0;

/* 五官定位。按 anime 头部的构造规则定的，不是拍脑袋：
 *   眼线落在头高中线或略下方（中线在 y≈0.5）——眼睛放中线以上是初学者最常见的错，
 *   会让角色显老、显"西方卡通"；
 *   眼睛高度占发际线到下巴的 1/4~1/3，眼睛宽度约 0.24 头宽；
 *   鼻底约在眼线到下巴的中点，嘴约在鼻底到下巴的 1/3~1/2。
 * 头高 = -98（颅顶）到 99（下巴）= 197。 */
const EYE_X = 35;
const EYE_Y = 4;
const EYE_W = 19;
const EYE_TOP = -24;
const EYE_BOT = 26;
const NOSE_Y = 44;
const MOUTH_Y = 62;
/* 脖子：下巴到肩的过渡。缺了这段头会像浮在衣服上 */
const NECK_HW = 17;
const NECK_TOP = 64;
const NECK_BOT = 122;
const COLLAR_X = 23;
const COLLAR_TOP = 104;
const COLLAR_BOT = 126;

function el(name, attrs, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function clamp01(v) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/* 眼睛：上眼睑下降模拟眨眼，下眼睑基本不动。open=0 时收成一道弯。
 *
 * lid 是眼睑随眼球上下动的偏移量（lid saccade）。真实眼球转上转下时上眼睑
 * 会跟着压下来/抬起来，两者高度相关。少了这条，眼球转动时虹膜会顶着上眼睑
 * 边滑过去，看着像"贴纸眼睛"——这是 2D 角色最容易被看穿的破绽之一。
 * 只跟随约 70%：眼睑跟满 100% 会读成"眼珠被眼皮拖着走"，反而假。 */
function eyePath(open, w, top, bot, tilt = 0, lid = 0) {
  const o = clamp01(open);
  const cy = (top + bot) / 2;
  if (o < 0.16) {
    // 闭眼：一道平缓的弧。弧度要小，弯太多会看成哭脸
    const y = cy - (0.16 - o) * 34;
    const bow = 6 - (0.16 - o) * 12;
    return `M ${-w} ${y} Q ${-w * 0.5} ${y - bow} 0 ${y - bow * 0.7} ` +
           `Q ${w * 0.5} ${y - bow} ${w} ${y}`;
  }
  // 上睑最低不能压过下睑，否则倒视（眼睛在下面）会画成一个倒扣的口袋
  const yTop = Math.max(top - 9, Math.min(bot - 5, top + (bot - top) * (1 - o) + lid));
  const t = tilt;
  return `M ${-w} ${cy} Q ${-w * 0.58} ${yTop + t} 0 ${yTop + t} ` +
         `Q ${w * 0.58} ${yTop + t} ${w} ${cy} ` +
         `Q ${w * 0.62} ${bot} 0 ${bot} Q ${-w * 0.58} ${bot} ${-w} ${cy} Z`;
}

/* 眼睑线：只在半闭时画粗，不然眼睛会像贴纸 */
function lidPath(open, w, top, bot, lid = 0) {
  const o = clamp01(open);
  const cy = (top + bot) / 2;
  const yTop = Math.max(top - 9, Math.min(bot - 5, top + (bot - top) * (1 - o) + lid));
  return `M ${-w} ${cy} Q ${-w * 0.58} ${yTop} 0 ${yTop} Q ${w * 0.58} ${yTop} ${w} ${cy}`;
}

/* 呼吸波形。ParamBreath 输入是 -1..1 的正弦，这里重新整形：
 * 吸气快（约 42% 周期，且越接近顶点越慢）、顶上短暂停留、呼气慢。
 * 生理解剖上潮气量就是这样，正弦反而像机械摆动。 */
function breathShape(v) {
  let p = (clamp(v) + 1) * 0.5;
  if (p < 0) p = 0; else if (p > 1) p = 1;
  const IN = 0.42;
  return p < IN
    ? smoothstep(p / IN)
    : -smoothstep((p - IN) / (1 - IN));
}
/* 笑眼。Live2D 标准的 ParamEyeSmile：眼睛不是闭上，而是收成一条
 * 向上弯的弧（^ 形）。这是"真的在笑"和"眼睛半闭"的分界线 ——
 * 眯眼只是幅度，笑眼是形状。只靠眯眼做不出笑意。 */
function smilePath(k, w, top, bot) {
  const t = clamp01(k);
  const cy = (top + bot) / 2;
  // 满值时是一条饱满的向上弧；不满时从睁眼形状往弧上长
  const y = cy + (1 - t) * (bot - cy) * 0.55 - t * 5;
  const bow = 9 * t;
  return `M ${-w} ${y + t * 4} Q ${-w * 0.5} ${y - bow} 0 ${y - bow * 0.8} ` +
         `Q ${w * 0.5} ${y - bow} ${w} ${y + t * 4}`;
}

/* 眉毛。
 * ParamBrow*Form 的语义（本项目约定，与 Live2D 的方向保持一致）：
 *   > 0 皱眉 —— 内侧下压并向中间靠
 *   < 0 挑眉 —— 内侧上抬，整条眉弓起来
 * ParamBrow*Angle 控外端单独的高低（惊讶/怀疑）。
 * y0/y1 要留在眼睑上方：眉毛贴着上眼睑会挤成一团，
 * 往上抬又会钻进刘海里，两头都不对。 */
function browPath(y, angle, form, mirror) {
  const s = mirror ? -1 : 1;
  const innerX = -23 * s * (1 - form * 0.2);
  const outerX = 23 * s;
  const press = -form * 11;
  const tilt = angle * 13;
  const x0 = innerX;
  const y0 = -36 + y * 12 + press;
  const x1 = outerX;
  const y1 = -45 + y * 12 - tilt * 0.5;
  return `M ${x0} ${y0} Q ${(x0 + x1) / 2} ${(y0 + y1) / 2 - 8 - tilt} ${x1} ${y1}`;
}

function mouthPath(open, form, round, y) {
  const form01 = clamp(form, -1, 1);
  const round01 = clamp(round, -1, 1);
  // 嘴宽要跟着脸宽走：脸半宽 80，嘴半宽 25 才是自然的比例
  const w = 25 * (1 + 0.40 * form01) * (1 - 0.24 * Math.max(0, round01));
  const top = MOUTH_Y + y * 9;

  if (open < 0.07) {
    // 闭嘴：一道弧。form 为正=嘴角上扬，round 为正=嘟嘴更圆
    const curve = 13 * form01 + 4 * round01;
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
    /* 次级运动的弹簧。参数是「频率 f(Hz) / 阻尼比 zeta」，见 params.js 的说明。
     * 关键的取法：zeta 决定"甩几下"，f 决定"多快"。头发要的是
     * 明显过冲（低 zeta），眉毛几乎不过冲（高 zeta）。
     * 头发链上从根部到尖端 f 递减、zeta 也递减 —— 末梢又轻又慢，
     * 才会像链条一样飘；整条同频就成了整块一起晃的塑料板。 */
    this.spring = {
      hairX: new Spring(1.5, 0.58),
      hairY: new Spring(1.4, 0.57),
      ahoge: new Spring(1.2, 0.40),
      brow: new Spring(2.1, 0.77),
      /* 头部姿态弹簧：比头发快一点（头比头发重），但仍然留一点滞后和过冲 */
      headYaw: new Spring(1.6, 0.60),
      headPitch: new Spring(1.5, 0.58),
      headRoll: new Spring(1.4, 0.56),
      bodyTilt: new Spring(1.3, 0.54),
      bodyLean: new Spring(1.2, 0.52),
      /* 眨眼弹簧：快。zeta 0.7 略欠阻尼，闭眼到位时有一点点"到位感"，
       * 但绝不能低到会来回抖两下 —— 眼睛抖是恐怖谷，不是生动。 */
      blinkL: new Spring(2.4, 0.70, 1),
      blinkR: new Spring(2.4, 0.70, 1),
      /* 呼吸时肩膀起伏：比身体快，肩膀比身体动得急 */
      shoulder: new Spring(1.2, 0.52),
      hairSeg1X: new Spring(1.7, 0.64),
      hairSeg1Y: new Spring(1.7, 0.62),
      hairSeg2X: new Spring(1.4, 0.56),
      hairSeg2Y: new Spring(1.4, 0.52),
      hairSeg3X: new Spring(1.1, 0.50),
      hairSeg3Y: new Spring(1.1, 0.45),
      /* 眼睑跟随眼球上下动的弹簧。眼睛上下看时上眼睑必须跟着压下来，
       * 这是真实感的一大来源（lid saccade），见 draw() 里的注释。 */
      lidY: new Spring(3.0, 0.85, 0),
    };
    /* 系统"减少动态效果"时把次级运动压下去，见 config/avatar.yaml */
    this.secondaryScale = 1;
  }

  setSecondaryMotion(scale) {
    this.secondaryScale = clamp(scale, 0, 1);
  }

  mount(container, look = {}) {
    const uid = `r${++RIG_UID}`;
    const L = {
      skin: "#f6ddd2", shade: "#e8c3b4", hair: "#4a4256", hairHi: "#6b6079",
      // 眉毛比头发淡一档：和发色一样黑会把这张柔和的脸压得很凶
      brow: "#584d64",
      blush: "#f0908a", lip: "#c96a63", mouthIn: "#8e3b3b",
      cloth: "#5b6bb5", clothDark: "#46528c", clothHi: "#c9cee7",
      eye: "#3b3348", glow: "#5aa7c9",
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
    /* 腮红用径向渐变：实心椭圆看着像两块贴在脸上的胶布 */
    const blush = el("radialGradient", { id: "av-blush" }, defs);
    el("stop", { offset: "0%", "stop-color": L.blush, "stop-opacity": "0.85" }, blush);
    el("stop", { offset: "65%", "stop-color": L.blush, "stop-opacity": "0.32" }, blush);
    el("stop", { offset: "100%", "stop-color": L.blush, "stop-opacity": "0" }, blush);
    /* 腮红高光（A-5）：腮红上方一小块柔光。没有它，红晕只是一块哑色，
     * 像贴纸；加一点高光才读得出"皮肤下面的血晕"。 */
    const blushHi = el("radialGradient", { id: "av-blush-hi" }, defs);
    el("stop", { offset: "0%", "stop-color": "#fffafa", "stop-opacity": "0.55" }, blushHi);
    el("stop", { offset: "100%", "stop-color": "#fffafa", "stop-opacity": "0" }, blushHi);
    /* 上眼睑在眼白上的投影。没有它眼睛就是贴纸。 */
    const lidShade = el("linearGradient", { id: "av-lidshade", x1: "0", y1: "0", x2: "0", y2: "1" }, defs);
    el("stop", { offset: "0%", "stop-color": "#6b4f45", "stop-opacity": "0.34" }, lidShade);
    el("stop", { offset: "55%", "stop-color": "#6b4f45", "stop-opacity": "0.05" }, lidShade);
    el("stop", { offset: "100%", "stop-color": "#6b4f45", "stop-opacity": "0" }, lidShade);

    const n = this.nodes;
    el("ellipse", { cx: 210, cy: 210, rx: 190, ry: 186, fill: "url(#av-glow)" }, svg);
    n.bgGlow = svg.querySelector('ellipse[fill="url(#av-glow)"]');

    const stage = el("g", { transform: "translate(210 212)" }, svg);
    n.stage = stage;

    /* --- 身体 --- */
    const body = el("g", {}, stage);
    n.body = body;

    /* 脖子：画在衣服前面、脸后面。头动的时候脖子不动，
       头才会像长在身体上而不是浮在上面。
       顶端要藏进下巴底下，脖子太宽会变成一根柱子。 */
    el("path", {
      d: `M ${-NECK_HW} ${NECK_TOP} C ${-NECK_HW - 1} ${NECK_TOP + 26} ` +
         `${-NECK_HW - 3} ${COLLAR_TOP - 14} ${-NECK_HW - 5} ${NECK_BOT} ` +
         `L ${NECK_HW + 5} ${NECK_BOT} ` +
         `C ${NECK_HW + 3} ${COLLAR_TOP - 14} ${NECK_HW + 1} ${NECK_TOP + 26} ` +
         `${NECK_HW} ${NECK_TOP} Z`,
      fill: "url(#av-skin)",
    }, body);
    /* 下巴在脖子上的投影。压太重脖子会变成一个洞。 */
    el("path", {
      d: `M ${-NECK_HW - 1} ${NECK_TOP + 4} C ${-8} ${NECK_TOP + 26} ` +
         `${8} ${NECK_TOP + 26} ${NECK_HW + 1} ${NECK_TOP + 4} ` +
         `C ${10} ${NECK_TOP + 15} ${-10} ${NECK_TOP + 15} ${-NECK_HW - 1} ${NECK_TOP + 4} Z`,
      fill: L.shade, opacity: "0.30",
    }, body);

    /* 衣服：肩线从领口两侧斜下去。领口挖成一个浅勺，
       露出的脖子刚好是一小截，多一点就成了大船领。 */
    el("path", {
      d: `M -134 210 C -126 146 -82 ${COLLAR_TOP + 8} ${-COLLAR_X} ${COLLAR_TOP} ` +
         `C -15 ${COLLAR_BOT} 15 ${COLLAR_BOT} ${COLLAR_X} ${COLLAR_TOP} ` +
         `C 82 ${COLLAR_TOP + 8} 126 146 134 210 Z`,
      fill: L.cloth,
    }, body);
    /* 领口滚边 */
    el("path", {
      d: `M ${-COLLAR_X} ${COLLAR_TOP} C -15 ${COLLAR_BOT} 15 ${COLLAR_BOT} ${COLLAR_X} ${COLLAR_TOP} ` +
         `C 12 ${COLLAR_TOP + 7} -12 ${COLLAR_TOP + 7} ${-COLLAR_X} ${COLLAR_TOP} Z`,
      fill: L.shade,
    }, body);
    /* 肩部的暗面，给衣服一点厚度 */
    el("path", {
      d: `M -134 210 C -126 146 -82 ${COLLAR_TOP + 8} ${-COLLAR_X} ${COLLAR_TOP} ` +
         `C -30 ${COLLAR_TOP + 14} -60 ${COLLAR_TOP + 30} -70 ${COLLAR_BOT + 40} ` +
         `L -80 210 Z`,
      fill: L.clothDark, opacity: "0.35",
    }, body);

    /* 领口亮区。
     * 深色衣服上必须有一块亮色，否则脸和肩膀在灰阶里糊成一团 ——
     * VTuber 角色设计的固定检查项：把画面转成灰度，头发/衣服/皮肤
     * 三块的边界要还认得出。对比度不够时的正确做法是拉明明度差
     * （不是加饱和度，加饱和在暗背景上反而更糊）。
     * 数据见 look.hair / look.cloth / look.cloth_hi 三者的 WCAG 对比度。 */
    el("path", {
      d: `M ${-COLLAR_X + 3} ${COLLAR_TOP + 2} ` +
         `C ${-COLLAR_X + 14} ${COLLAR_TOP + 16} ${COLLAR_X - 14} ${COLLAR_TOP + 16} ` +
         `${COLLAR_X - 3} ${COLLAR_TOP + 2} ` +
         `C ${COLLAR_X - 14} ${COLLAR_TOP - 8} ${-COLLAR_X + 14} ${COLLAR_TOP - 8} ` +
         `${-COLLAR_X + 3} ${COLLAR_TOP + 2} Z`,
      fill: L.clothHi, opacity: "0.5",
    }, body);

    /* --- 头 --- */
    const head = el("g", {}, stage);
    n.head = head;
    const roll = el("g", {}, head);
    n.roll = roll;

    /* 后发：从头顶顺着两侧垂到肩。分成 3 段串联弹簧，
       否则头一转头发是刚性跟上的，立刻出戏。
       段1（靠近头）刚度大，段3（末端）刚度小，
       这样头发会像链条一样飘动，而不是整体平移。 */
    const hairSegs = [];
    for (let i = 0; i < 3; i++) {
      const seg = el("g", {}, roll);
      hairSegs.push(seg);
      n[`hairSeg${i}`] = seg;
    }
    // 段1：靠近头，短而粗
    el("path", {
      d: "M -86 -52 C -100 -10 -104 30 -100 60 " +
         "C -96 70 -88 76 -82 72 C -84 50 -86 20 -84 -10 Z",
      fill: L.hair,
    }, hairSegs[0]);
    el("path", {
      d: "M 86 -52 C 100 -10 104 30 100 60 " +
         "C 96 70 88 76 82 72 C 84 50 86 20 84 -10 Z",
      fill: L.hair,
    }, hairSegs[0]);
    // 段2：中段，逐渐变细
    el("path", {
      d: "M -100 60 C -108 90 -106 120 -98 140 " +
         "C -92 148 -84 152 -78 148 C -80 130 -82 100 -82 72 Z",
      fill: L.hair,
    }, hairSegs[1]);
    el("path", {
      d: "M 100 60 C 108 90 106 120 98 140 " +
         "C 92 148 84 152 78 148 C 80 130 82 100 82 72 Z",
      fill: L.hair,
    }, hairSegs[1]);
    // 段3：末端，收尖
    el("path", {
      d: "M -98 140 C -102 155 -98 168 -88 176 " +
         "C -82 180 -76 178 -72 174 C -74 162 -76 150 -78 148 Z",
      fill: L.hair,
    }, hairSegs[2]);
    el("path", {
      d: "M 98 140 C 102 155 98 168 88 176 " +
         "C 82 180 -76 178 -72 174 C 74 162 -76 150 -78 148 Z",
      fill: L.hair,
    }, hairSegs[2]);
    /* 外缘高光：一条就够，两条就假了 */
    el("path", {
      d: "M -94 -30 C -106 20 -102 88 -92 132",
      fill: "none", stroke: L.hairHi, "stroke-width": "5",
      "stroke-linecap": "round", opacity: "0.38",
    }, hairSegs[0]);
    el("path", {
      d: "M 94 -30 C 106 20 -102 88 -92 132",
      fill: "none", stroke: L.hairHi, "stroke-width": "5",
      "stroke-linecap": "round", opacity: "0.38",
    }, hairSegs[0]);

    /* 脸：最宽处落在颧骨（约 y=-10，正好在眼线附近），下颌内收、下巴收尖。
     颅顶到下巴约 197，最宽处约在 45% 处，和眼线一起构成 anime 的比例关系。 */
    el("path", {
      d: "M 0 -98 C 47 -98 79 -60 80 -10 " +
         "C 81 26 69 56 51 73 C 38 86 19 95 0 100 " +
         "C -19 95 -38 86 -51 73 C -69 56 -81 26 -80 -10 " +
         "C -79 -60 -47 -98 0 -98 Z",
      fill: "url(#av-skin)", stroke: L.shade, "stroke-width": "2",
    }, roll);
    /* 颧骨高光 */
    el("ellipse", { cx: -40, cy: 26, rx: 26, ry: 20, fill: "#ffffff",
                    opacity: "0.10" }, roll);
    el("ellipse", { cx: 42, cy: 24, rx: 24, ry: 18, fill: "#ffffff",
                    opacity: "0.08" }, roll);

    /* 耳朵：顶对齐眉线、底对齐鼻底，取中点。这条约束是通用的，
       耳朵往上飘是最常见的五官脱位之一。 */
    el("ellipse", { cx: -77, cy: 12, rx: 8, ry: 13, fill: L.skin,
                    stroke: L.shade, "stroke-width": "1.5" }, roll);
    el("ellipse", { cx: 77, cy: 12, rx: 8, ry: 13, fill: L.skin,
                    stroke: L.shade, "stroke-width": "1.5" }, roll);

    /* 五官容器：偏航/俯仰的位移作用在这一层 */
    const features = el("g", {}, roll);
    n.features = features;

    n.blush = [];
    for (const side of [-1, 1]) {
      n.blush.push(el("ellipse", {
        cx: side * 48, cy: 34, rx: 24, ry: 14,
        fill: "url(#av-blush)", opacity: "0",
      }, features));
    }
    n.blushHi = [];
    for (const side of [-1, 1]) {
      n.blushHi.push(el("ellipse", {
        cx: side * 46, cy: 27, rx: 13, ry: 7,
        fill: "url(#av-blush-hi)", opacity: "0",
      }, features));
    }

    n.brow = [
      el("path", { fill: "none", stroke: L.brow, "stroke-width": "5",
                   "stroke-linecap": "round" }, features),
      el("path", { fill: "none", stroke: L.brow, "stroke-width": "5",
                   "stroke-linecap": "round" }, features),
    ];
    n.browG = [];
    /* 眉毛单独一层：低头/抬头时眉毛要比脸慢半拍才自然 */
    const browLag = el("g", {}, features);
    n.browLag = browLag;
    for (const side of [-1, 1]) {
      n.browG.push(el("g", { transform: `translate(${side * EYE_X} ${EYE_Y})` }, browLag));
    }
    n.browG[0].appendChild(n.brow[0]);
    n.browG[1].appendChild(n.brow[1]);

    /* 眼睛。anime 的眼睛不是"白底上贴一个黑圆"，而是：
     * 一块竖椭圆虹膜填满眼裂，再被上下眼睑裁掉多余的部分。
     * 所以虹膜一定要 clip 到眼裂形状里，否则眼白会压倒虹膜，
     * 眼睛立刻变成斗鸡眼。整个组 clip 在下面这个 path 里。 */
    const eyeClip = el("clipPath", { id: `av-eyeclip-${uid}` }, defs);
    const clipPath = el("path", {}, eyeClip);

    n.eyes = [];
    for (const side of [-1, 1]) {
      const g = el("g", { transform: `translate(${side * EYE_X} ${EYE_Y})` }, features);
      const clipped = el("g", { "clip-path": `url(#av-eyeclip-${uid})` }, g);
      const sclera = el("path", { fill: "#fdfbfa" }, clipped);
      /* 上眼睑在眼白上的投影。没有它眼睛就是贴纸。 */
      const shade = el("path", { fill: "url(#av-lidshade)" }, clipped);
      const iris = el("g", {}, clipped);
      /* 竖椭圆，中心略低于眼裂中心 —— 上方多留一点眼白是经典的 anime 眼 */
      el("ellipse", { cx: 0, cy: 4, rx: 13.5, ry: 21, fill: L.eye }, iris);
      el("ellipse", { cx: 0, cy: -1, rx: 11, ry: 17, fill: "#1b1626" }, iris);
      el("ellipse", { cx: 0, cy: 11, rx: 7, ry: 7.5, fill: L.eye, opacity: "0.8" }, iris);
      /* 瞳孔单独成组，才能独立于虹膜缩放。
       *
       * 生理上放大的是瞳孔，不是虹膜——虹膜（棕绿色那圈）大小基本不变，
       * 变的是中间的瞳孔口径。把整只眼睛一起放大是常见的画错：
       * 那样"瞳孔放大"会读成"眼睛瞪大了"，而瞪眼和瞳孔放大
       * 表达的是完全不同的东西（一个是肌肉收缩，一个是虹膜括约肌）。
       * 这条通道必须能独立变化，否则情绪的唤醒度就没地方体现。
       *
       * 缩放中心取瞳孔自己的圆心 (0,3) 而不是原点，
       * 否则放大时瞳孔会连着位置一起跑，像眼球在往下滑。 */
      const pupilG = el("g", {}, iris);
      el("ellipse", { cx: 0, cy: 3, rx: 6.5, ry: 10.5, fill: "#141018" }, pupilG);
      /* 主高光跟着瞳孔走：它反的是瞳孔表面的光，
       * 瞳孔涨大它就跟着盖更大的面积，这正是"眼睛亮起来"的来源。 */
      el("ellipse", { cx: -4.6, cy: -7, rx: 4.2, ry: 5.4, fill: "#ffffff" }, pupilG);
      /* 副高光是角膜上的反光，不属于瞳孔，所以留在组外 */
      el("circle", { cx: 4.8, cy: 10, r: 2.2, fill: "#ffffff", opacity: "0.6" }, iris);
      const rim = el("ellipse", { cx: 0, cy: 4, rx: 13.5, ry: 21, fill: "none",
                                  stroke: "#241d2e", "stroke-width": "2",
                                  opacity: "0.9" }, iris);
      /* 眼线：上睑线重、下睑线轻，这是 anime 眼型的定式 */
      const lid = el("path", { fill: "none", stroke: L.eye,
                               "stroke-width": "4.4", "stroke-linecap": "round",
                               opacity: "0" }, g);
      const lower = el("path", {
        fill: "none", stroke: L.shade, "stroke-width": "2",
        "stroke-linecap": "round", opacity: "0.5",
      }, g);
      /* 笑眼：一条向上弯的弧，叠在眼球之上 */
      const smileLine = el("path", { fill: "none", stroke: L.eye,
                                      "stroke-width": "4.6",
                                      "stroke-linecap": "round",
                                      opacity: "0" }, g);
      n.eyes.push({ g, clipped, sclera, shade, iris, pupilG, rim, lid, lower,
                    smileLine, clipPath, side });
    }

    /* 鼻尖。anime 的鼻子是"减法"：一个小记号就够，
       画全了反而把风格毁掉。这里只留一道短弧。 */
    n.nose = el("path", {
      d: `M 4 ${NOSE_Y - 10} Q 10 ${NOSE_Y - 2} 0 ${NOSE_Y}`,
      fill: "none", stroke: L.shade,
      "stroke-width": "2.4", "stroke-linecap": "round", opacity: "0.75",
    }, features);

    n.mouth = el("path", { fill: L.mouthIn, stroke: L.lip, "stroke-width": "3.2",
                           "stroke-linecap": "round", "stroke-linejoin": "round" }, features);
    n.tongue = el("ellipse", { cx: 0, cy: MOUTH_Y + 11, rx: 13, ry: 6,
                               fill: "#d9736f", opacity: "0" }, features);

    /* 刘海：三七分、三个长短不一的发尖。
       等高的齐刘海是一整块硬边，会把额头压死也把眉毛挤到边上。
       发尖最低要停在眉毛上方 12px 以上，不然挑眉时眉毛会钻进头发里。 */
    el("path", {
      d: "M -78 -26 C -78 -80 -38 -106 2 -104 C 44 -102 78 -76 80 -28 " +
         "C 78 -46 70 -60 58 -66 " +
         "C 60 -56 56 -50 48 -52 " +
         "C 42 -62 26 -68 10 -70 " +
         "C 12 -60 6 -54 -2 -56 " +
         "C -18 -70 -44 -70 -62 -58 " +
         "C -72 -54 -77 -40 -78 -26 Z",
      fill: L.hair,
    }, roll);
    /* 一缕横过额头的碎发，给刘海分层，不然还是一整块 */
    el("path", {
      d: "M -66 -70 C -40 -88 6 -92 40 -78 C 30 -74 12 -76 -6 -78 " +
         "C -30 -81 -52 -78 -66 -70 Z",
      fill: L.hairHi, opacity: "0.30",
    }, roll);
    /* 刘海高光：顺着发流方向，不是横着一条 */
    el("path", {
      d: "M -50 -80 C -30 -96 4 -100 30 -90",
      fill: "none", stroke: L.hairHi, "stroke-width": "4.5",
      "stroke-linecap": "round", opacity: "0.45",
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

  /* p: ParamStore.current，dt: 秒 */
  draw(p, dt = 1 / 60) {
    const n = this.nodes;
    if (!n.stage) return;

    /* --- 动态背景 ---
     * 背景随情绪变化：激动时变亮，收敛时变暗。
     * 用弹簧平滑过渡，避免突变。 */
    const emotion = p.ParamCheek || 0;
    const bgTarget = 0.30 + emotion * 0.25;
    this.bgLevel = (this.bgLevel || 0.30) + (bgTarget - (this.bgLevel || 0.30)) * 0.05;
    if (n.bgGlow) {
      n.bgGlow.setAttribute("opacity", this.bgLevel.toFixed(3));
    }

    /* 头部姿态用弹簧平滑：刚度比头发高（头比头发重），
       但比直接设置低，这样头部转动会有轻微滞后和过冲。 */
    const rawYaw = clamp(p.ParamAngleX);
    const rawPitch = clamp(p.ParamAngleY);
    const rawRoll = clamp(p.ParamAngleZ);
    const yaw = this.spring.headYaw.step(rawYaw, dt);
    const pitch = this.spring.headPitch.step(rawPitch, dt);
    const rollA = this.spring.headRoll.step(rawRoll, dt);
    const bob = clamp(p.ParamBodyAngleX) * 6;

    /* --- 次级运动 ---
     * 弹簧追的是"头的姿态"，但头发挂在滞后量上：
     * 头在转 -> 弹簧落后 -> 头发被"拖"在后面；
     * 头急停   -> 弹簧冲过头 -> 头发甩过头再荡回来。
     * 直接用弹簧位置做位移是不够的，必须用"头 - 弹簧"这个差值。 */
    const hx = this.spring.hairX.step(yaw, dt);
    const hy = this.spring.hairY.step(pitch, dt);
    const s = this.secondaryScale;
    const lagX = (yaw - hx) * s;
    const lagY = (pitch - hy) * s;
    /* 头发自己的慢周期。周期刻意和呼吸（4.1s）、点头（≈6s）都错开：
     * 次级运动只有跑在比主运动更长的周期上，才最便宜地把循环点糊掉 ——
     * 等周期的话，头发和呼吸会同时到极限，循环点就是个"标志帧"，
     * 观众看五秒就能指出它在循环。 */
    this.hairT = (this.hairT || 0) + dt;
    /* 发丝次级摆动（A-5）：原来只有两个慢周期，摇头停下之后头发会显出
     * 一个安静的"来回摇"。补一组更短、带相位偏移的谐波，三个周期互不可约，
     * 每段头发又有自己的增益（末端最大），才读得出"发丝自己在飘"。
     * 幅度仍受 secondaryScale 控制，reduced-motion 下整体收敛。 */
    const drift = Math.sin((this.hairT / 7.3) * Math.PI * 2) * s;
    const drift2 = Math.sin((this.hairT / 5.1) * Math.PI * 2 + 1.7) * s;
    const drift3 = Math.sin((this.hairT / 3.7) * Math.PI * 2 + 0.6) * s;

    /* --- 弹簧链头发物理 ---
     * 3 段串联：段1 跟随头，段2 跟随段1，段3 跟随段2。
     * 刚度递减（120 → 80 → 50），末端甩得最明显。
     * 每段的旋转中心在段的顶部（连接点），这样链条才能弯。 */
    const seg1X = this.spring.hairSeg1X.step(yaw, dt);
    const seg1Y = this.spring.hairSeg1Y.step(pitch, dt);
    const seg2X = this.spring.hairSeg2X.step(seg1X, dt);
    const seg2Y = this.spring.hairSeg2Y.step(seg1Y, dt);
    const seg3X = this.spring.hairSeg3X.step(seg2X, dt);
    const seg3Y = this.spring.hairSeg3Y.step(seg2Y, dt);

    const s1 = 1 - this.secondaryScale;
    const s2 = 1 - this.secondaryScale * 0.7;
    const s3 = 1 - this.secondaryScale * 0.4;

    /* 高次谐波逐段叠加：根段最小、末段最大，末端因此甩得最活。
     * 每个分量的平移/旋转增益按段递增，和弹簧链的滞后量叠在一起。 */
    const segBase = [
      { tx: -(yaw - seg1X) * 5, ty: -(pitch - seg1Y) * 3, rot: (yaw - seg1X) * 2.2, pivot: 0 },
      { tx: -(seg1X - seg2X) * 4, ty: -(seg1Y - seg2Y) * 2.5, rot: (seg1X - seg2X) * 2.0, pivot: 60 },
      { tx: -(seg2X - seg3X) * 3, ty: -(seg2Y - seg3Y) * 2, rot: (seg2X - seg3X) * 1.8, pivot: 140 },
    ];
    const segDrift = [drift * 1.2, drift * 1.0, drift * 0.8];
    const segDrift2 = [drift2 * 0.6, drift2 * 0.5, drift2 * 0.4];
    const segDrift3 = [drift3 * 0.4, drift3 * 0.7, drift3 * 1.0];
    for (const [index, node] of [n.hairSeg0, n.hairSeg1, n.hairSeg2].entries()) {
      const base = segBase[index];
      node.setAttribute("transform",
        `translate(${(base.tx + segDrift[index]).toFixed(2)} ` +
        `${(base.ty + segDrift2[index] + segDrift3[index] * 0.5).toFixed(2)}) ` +
        `rotate(${(base.rot + segDrift[index] * 0.35 + segDrift3[index] * 0.3).toFixed(2)} 0 ${base.pivot})`);
    }

    const bh = this.spring.brow.step(pitch, dt);
    n.browLag.setAttribute("transform", `translate(0 ${(-(pitch - bh) * 9 * s).toFixed(2)})`);

    /* 呆毛跟着翻滚和点头甩，但左右转头时也该被带一下，
       只跟 roll 的话呆毛在日常动作里几乎不会动。 */
    const ah = this.spring.ahoge.step(rollA * 22 + bob + yaw * 11, dt);
    n.ahoge.setAttribute("transform",
      `rotate(${(ah * 1.25 * s + drift * 2.2).toFixed(2)} 4 -102)`);

    /* --- 呼吸 ---
     * 两条都是硬要求：
     *   1) 必须动肩膀，不能整块均匀缩放。静止画面只认轮廓，
     *      均匀缩放只是明暗变化，两帧对比看不出差别 —— 那是"死待机"的判据。
     *      所以这里绕 body 的底边（y=210）横向张开，肩膀是轮廓上的点。
     *   2) 波形不是正弦。真人吸气快、顶上停一拍、呼气慢，
     *      正弦会让呼吸看起来像机械摆动。 */
    const b = breathShape(p.ParamBreath);
    // 肩膀弹簧：刚度比身体高，肩膀比身体动得快
    const sh = this.spring.shoulder.step(b, dt);
    n.stage.setAttribute("transform", "translate(210 212)");

    // 身体跟头，但幅度更小、更慢
    const bodyRot = clamp(p.ParamBodyAngleX + yaw * 0.28);
    // 身体倾斜用弹簧平滑：刚度比头低，身体比头慢
    const bodyTilt = this.spring.bodyTilt.step(clamp(p.ParamBodyAngleZ + rollA * 0.3), dt);
    const lean = this.spring.bodyLean.step(clamp(p.ParamBodyLeanX), dt) * 16;
    n.body.setAttribute(
      "transform",
      `translate(${lean.toFixed(2)} ${(-sh * 1.2).toFixed(2)}) ` +
      `rotate(${(bodyTilt * 0.6).toFixed(2)}) ` +
      // 底边固定：SVG 的 scale() 只收 1~2 个参数，没有缩放原点这一说，
      // 所以要用 translate 前后包。这样张开只发生在肩膀一侧，
      // 而衣服下摆钉在原地不动。
      `translate(0 210) ` +
      `scale(${(1 - Math.abs(bodyRot) * 0.05 + sh * 0.035).toFixed(4)} ` +
      `${(1 - sh * 0.016).toFixed(4)}) translate(0 -210)`
    );

    const sx = 1 - Math.abs(yaw) * 0.13;
    const sy = 1 - Math.abs(pitch) * 0.08;
    // 头跟着胸腔一起抬，落点只有 1px 上下，但少了这个呼吸就只剩衣服在动
    n.roll.setAttribute(
      "transform",
      `translate(0 ${(-b * 0.85).toFixed(2)}) ` +
      `rotate(${(rollA * 7).toFixed(2)}) scale(${sx.toFixed(4)} ${sy.toFixed(4)})`
    );

    // 五官偏移：偏航横向 + 俯仰纵向，转头时五官要"跟过去"。
    // 视线移动时五官也跟着走一点（伪深度视差：五官比后发更"近"）。
    const fx = yaw * 9;
    const fy = pitch * 6;
    n.features.setAttribute("transform", `translate(${fx.toFixed(2)} ${fy.toFixed(2)})`);

    /* --- 眼睛 --- */
    const gx = clamp(p.ParamEyeBallX);
    const gy = clamp(p.ParamEyeBallY);
    /* 眼睑跟随眼球上下动。用一根偏临界阻尼的弹簧：眼睑要紧跟（否则
     * 眼球已经看下面了、眼皮还没落下来，像是眼球在眼窝里滑），
     * 但阻尼要高，不能有过冲回摆——眼皮抖两下比不跟还假。
     * 跟随比例 0.72：跟满 100% 读成"眼皮拖着眼球"，反而不自然。 */
    const lidOffset = this.spring.lidY.step(gy * 4.2 * 0.72, dt);
    // 眨眼用弹簧平滑：刚度很高，眨眼是快速动作
    const openL = this.spring.blinkL.step(clamp01(p.ParamEyeLOpen), dt);
    const openR = this.spring.blinkR.step(clamp01(p.ParamEyeROpen), dt);
    const smileL = clamp01(p.ParamEyeLSmile);
    const smileR = clamp01(p.ParamEyeRSmile);
    /* 虹膜缩放：Live2D 标准参数 ParamEyeBallForm。
     * -1 收小、+1 放大。Animaze 的扩展集里它专门用来让虹膜配合
     * 笑/皱眉改变眼睛轮廓 —— 只靠 ParamEyeLOpen 做不出"瞳孔缩起来"的
     * 心虚感或"瞪大眼"的专注感，那是虹膜大小的事，不是开度的事。 */
    const ballForm = clamp(p.ParamEyeBallForm);
    const irisScale = 1 + ballForm * 0.34;
    const irisShiftY = ballForm * -1.2;
    for (const eye of n.eyes) {
      const open = eye.side < 0 ? openL : openR;
      const smile = eye.side < 0 ? smileL : smileR;
      const d = eyePath(open, EYE_W, EYE_TOP, EYE_BOT, 0, lidOffset);
      eye.sclera.setAttribute("d", d);
      eye.shade.setAttribute("d", d);
      // 裁剪路径跟着眼裂走：闭眼时虹膜自然被裁没，不需要额外开关
      eye.clipPath.setAttribute("d", d);
      // 转头时远侧眼睛窄一点
      const depth = 1 - Math.max(0, yaw * eye.side) * 0.22;
      eye.g.setAttribute("transform",
        `translate(${(eye.side * EYE_X).toFixed(2)} ${EYE_Y}) scale(${depth.toFixed(3)} 1)`);
      eye.iris.setAttribute("transform",
        `translate(${(gx * 5.6).toFixed(2)} ${(gy * 4.2 + irisShiftY).toFixed(2)}) ` +
        `scale(${irisScale.toFixed(3)} ${irisScale.toFixed(3)})`);
      /* 瞳孔大小：唤醒度越高瞳孔越大。
       *
       * 这是一条独立于表情的生理通道。七个离散情绪标签最多只能表达七种状态，
       * 而瞳孔大小是一条连续量——同一种情绪从轻微到强烈，
       * 瞳孔是连续变化的，所以观感上"情绪有强弱"这件事才立得住。
       *
       * 真实瞳孔在放松时约 3~4mm，强光下收到 1.5mm 左右，
       * 黑暗中放到 6~7mm。这里 ParamPupil 的中位是 1.0，
       * 映射到 0.62~1.45 这个区间——比生理范围保守，
       * 因为动漫眼睛的虹膜本来就画得比真人浅，
       * 瞳孔再放大会显得像在瞪眼而不是在专注。 */
      const pupil = Math.max(0.55, Math.min(1.5, p.ParamPupil ?? 1));
      eye.pupilG.setAttribute("transform",
        `translate(0 3) scale(${pupil.toFixed(3)} ${pupil.toFixed(3)}) translate(0 -3)`);
      eye.lid.setAttribute("d", lidPath(open, EYE_W, EYE_TOP, EYE_BOT, lidOffset));
      eye.lid.setAttribute("opacity", open < 0.86 && smile < 0.12 ? "1" : "0");
      /* 笑眼：眼球淡出，换成一条向上弯的弧。
       两条曲线必须在同一条过渡带里互补，否则中间会出现
       "眼球半透明 + 弧线半透明"同时存在的糊状帧。
       过渡带刻意收得很窄（0.06~0.20）：宽了就会在脸上停很久。
       注意 smoothstep() 只 clamp 到 -1..1，负值平方后仍是正数，
       所以过渡带的输入必须先自己按 0 截断，否则 smile=0 时
       k 已经是 0.7，每只眼睛都会带着一条笑眼弧。 */
      const band = (smile - 0.06) / 0.14;
      const k = band <= 0 ? 0 : band >= 1 ? 1 : smoothstep(band);
      eye.clipped.setAttribute("opacity", (1 - k).toFixed(3));
      eye.smileLine.setAttribute("d", smilePath(smile, EYE_W, EYE_TOP, EYE_BOT));
      eye.smileLine.setAttribute("opacity", k.toFixed(3));
      // 下睑线跟着上眼睑走：闭眼时往上收，睁眼时落在眼白下缘
      const lift = (1 - clamp01(open)) * 9;
      eye.lower.setAttribute("d",
        `M ${-EYE_W * 0.86} ${EYE_BOT - lift - 2} ` +
        `Q 0 ${EYE_BOT + 5 - lift} ${EYE_W * 0.86} ${EYE_BOT - lift - 2}`);
      eye.lower.setAttribute("opacity", open < 0.1 || smile > 0.08 ? "0" : "0.5");
    }

    /* --- 伪深度视差 ---
     * 视线移动时，五官（近）跟着走，后发（远）反向移动较少。
     * 这样头部转动时会产生层次感和空间深度。 */
    const parallaxX = -gx * 0.3;
    const parallaxY = -gy * 0.25;
    n.features.setAttribute("transform",
      `translate(${(fx + gx * 3).toFixed(2)} ${(fy + gy * 2).toFixed(2)})`);
    if (n.hairSeg0) {
      const cur = n.hairSeg0.getAttribute("transform") || "";
      const m = cur.match(/translate\((-?[\d.]+) (-?[\d.]+)\)/);
      if (m) {
        const cx = parseFloat(m[1]) + parallaxX * 0.3;
        const cy = parseFloat(m[2]) + parallaxY * 0.2;
        n.hairSeg0.setAttribute("transform",
          // 替换串必须自带右括号：少了它，后面整个 rotate(...) 都会被吞进
          // 一个不闭合的 translate(...)，浏览器当场丢掉这条 transform。
          cur.replace(/translate\([^)]+\)/, `translate(${cx.toFixed(2)} ${cy.toFixed(2)})`));
      }
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

    /* --- 腮红 --- */
    const cheek = clamp01(p.ParamCheek);
    for (const b of n.blush) b.setAttribute("opacity", (cheek * 0.85).toFixed(3));
    for (const h of n.blushHi) h.setAttribute("opacity", (cheek * 0.5).toFixed(3));
  }
}
