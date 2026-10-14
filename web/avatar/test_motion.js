/* 次级运动弹簧的数值自检。
 *
 * 这几条判据来自"怎么确认物理真的对了"的实践总结，比肉眼看曲线可靠：
 *
 *   1) 能量不能自己长。钉住目标、只让它自由衰减，记录每个峰的峰值，
 *      这些峰必须单调下降。任何一个峰升高就说明积分器写错了。
 *   2) 静止时必须真的静止。目标不动跑一分钟，末端位置的变化要收敛到
 *      浮点噪声。持续的小幅抖动通常意味着约束投影没有同步到速度上。
 *   3) 包络必须跨帧率一致。同一套主动画在 30/60/144fps 下跑，
 *      末端位置对时间的曲线要基本重合。任何分离都说明还有地方
 *      在直接消费 frame_dt。
 *
 * 顺带检查眨眼波形的不对称：闭眼段应该明显短于睁眼段。
 * 这是"像不像人在眨"最直接的量。
 */

import { Spring } from "./params.js";
import { IdleDriver } from "./drivers/idle.js";

let failures = 0;

function check(name, ok, detail = "") {
  if (ok) {
    console.log(`  ok   ${name}`);
  } else {
    console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`);
    failures++;
  }
}

/* 1) 自由衰减时包络必须单调收缩。
 *    注意要比较 |x| 的峰：欠阻尼振子过零点之后 x 会变号，
 *    只看 x 的局部极大只会量到下半周，得到"峰在上升"的假阳性。 */
function energyDecay(freq, zeta) {
  const s = new Spring(freq, zeta);
  s.x = 1;
  const peaks = [];
  let prevAbs = Math.abs(s.x), rising = true;
  for (let i = 0; i < 2400; i++) {
    s.step(0, 1 / 240);
    const a = Math.abs(s.x);
    if (rising && a <= prevAbs) { peaks.push(prevAbs); rising = false; }
    else if (!rising && a > prevAbs) rising = true;
    prevAbs = a;
  }
  // 欠阻尼才会来回摆；临界/过阻尼只该单调收敛下去，没有第二个包络峰可测。
  // 要求"至少两个峰"是在强行让一个本来正确的实现失败。
  if (zeta >= 1) return { ok: peaks.length <= 1, msg: `不该振荡，却量到 ${peaks.length} 个包络峰` };
  if (peaks.length < 2) return { ok: false, msg: `只量到 ${peaks.length} 个包络峰` };
  for (let i = 1; i < peaks.length; i++) {
    if (peaks[i] > peaks[i - 1] * (1 + 1e-9)) {
      return { ok: false, msg: `包络峰 ${i} = ${peaks[i].toExponential(3)} > 前一个 ${peaks[i - 1].toExponential(3)}` };
    }
  }
  return { ok: true };
}

/* 1b) 解析解的初值条件必须精确成立。
 *     这是最容易写错也最容易漏掉的地方：速度公式里少一项指数包络的
 *     微分，或者分母该用 omegaD 时用了 omega，x 在第一步就跳到错误的值，
 *     而曲线形状看起来"还挺像"。x(0)=x0, v(0)=v0 是免费的硬判据。 */
function initialConditions(freq, zeta) {
  const s = new Spring(freq, zeta);
  s.x = 0.37; s.v = -1.2;
  const x0 = s.x, v0 = s.v;
  s.step(1.0, 1e-9);          // 极小步长，约等于求导
  const ex = Math.abs(s.x - x0);
  const ev = Math.abs(s.v - v0);
  return { ok: ex < 1e-8 && ev < 1e-6, ex, ev };
}

/* 2) 静止时收敛到不动点 */
function restConverges(freq, zeta) {
  const s = new Spring(freq, zeta, 0);
  let x = 0, v = 0;
  for (let i = 0; i < 60 * 240; i++) {
    s.step(0, 1 / 240);
    if (i > 30 * 240) {          // 前 30 秒是收敛过程，不计入
      x = Math.max(x, Math.abs(s.x));
      v = Math.max(v, Math.abs(s.v));
    }
  }
  return { ok: x < 1e-9 && v < 1e-9, x, v };
}

/* 3) 跨帧率一致。
 *
 *    判据选「半群性质」：把一步 dt 拆成两步 dt/2，结果必须逐位相同。
 *    这正是跨帧率一致的充要条件——一帧无论被渲染成 1 步还是 2 步，
 *    状态都一样，那么 30/60/144fps 跑同一段动画自然重合。
 *    比"采样再互相对比"干净得多：后者要在浮点时间轴上做索引对齐，
 *    而循环里 t += dt 的累积误差会污染对齐本身，
 *    量出来的"帧率差异"其实是测试的误差而不是代码的。
 */
function semigroup(freq, zeta) {
  const mk = () => { const s = new Spring(freq, zeta); s.x = 0.2; s.v = -0.8; return s; };
  const a = mk(), b = mk(), c = mk(), d = mk();
  a.step(1, 0.016);
  b.step(1, 0.008); b.step(1, 0.008);                        // 2 步
  for (let i = 0; i < 4; i++) c.step(1, 0.004);              // 4 步
  for (let i = 0; i < 3; i++) d.step(1, 0.016 / 3);          // 3 步
  const err = (p, q) => Math.max(Math.abs(p.x - q.x), Math.abs(p.v - q.v));
  return { half: err(a, b), quarter: err(a, c), third: err(a, d) };
}

/* 4) 显式/半隐式的差别：把步长故意放大到接近稳定边界，
 *    确认实现没有在中途炸掉 */
function stabilityAtLargeStep() {
  const s = new Spring(19, 0.4);
  for (let i = 0; i < 200; i++) s.step(1, 1 / 60);
  return { ok: Number.isFinite(s.x) && Math.abs(s.x) < 1e3, x: s.x };
}

/* 5) 眨眼波形：睁眼段必须明显长于闭眼段。
 *
 *    量法上有两个坑，都踩过：
 *    - 不能读 blinkPhase 来判断"这一下眨完了没有"。最后一帧里
 *      blinkPhase 会被复位成 -1，而 eyeLevel 同时已经是 1，
 *      于是"完全睁开"那一刻在 blinkPhase 上根本读不到，量出来全是 0。
 *    - 睁眼段要从"眼睑开始离开闭合位"算起，不���从"压到底"算起，
 *      否则闭合保持段会被算进睁眼时间里，让不对称看起来比实际小。
 *    所以全程只跟踪 eyeLevel 的转折，不碰 blinkPhase。 */
function blinkAsymmetry() {
  const idle = new IdleDriver();
  const dt = 1 / 240;
  let t = 0, start = -1, bottom = -1, rising = -1;
  let wasOpen = true;
  for (let i = 0; i < 240 * 30; i++) {
    idle.update(dt, {});
    t += dt;
    const isOpen = idle.eyeLevel >= 1 - 1e-6;
    const atBottom = idle.eyeLevel <= 1e-6;
    if (wasOpen && !isOpen) { start = t; bottom = -1; rising = -1; }  // 开始眨
    if (atBottom && bottom < 0 && start > 0) bottom = t;             // 压到底
    if (!atBottom && !isOpen && bottom >= 0 && rising < 0) rising = t; // 眼睑离开闭合位
    wasOpen = isOpen;
    if (isOpen && start > 0 && bottom >= 0 && rising >= 0) {
      const closeDur = bottom - start;
      const openDur = t - rising;
      return { closeDur, openDur, ok: openDur > closeDur * 1.2 };
    }
  }
  return { closeDur: 0, openDur: 0, ok: false };
}

/* 6) 微扫视不能再让整个帧循环炸掉（targetName === "custom" 的老问题） */
function customGazeDriftIsSafe() {
  const idle = new IdleDriver();
  idle.setGazeTarget(0.5, -0.3);
  try {
    for (let i = 0; i < 2000; i++) idle.update(1 / 60, {});
    return { ok: true };
  } catch (e) {
    return { ok: false, msg: e.message };
  }
}

/* 7) 慢层：精神会掉，且互动会把它拉回来 */
function energyLayer() {
  const idle = new IdleDriver();
  const start = idle.energy;
  for (let i = 0; i < 60 * 30; i++) idle.update(1 / 60, {});   // 静置 30 秒
  const drained = idle.energy;
  idle.wake(1);
  const woken = idle.energy;
  const ok = drained < start - 0.3 && woken > drained + 0.5;
  return { ok, start, drained, woken, stage: idle.stage };
}

console.log("spring 能量单调衰减");
for (const [f, z] of [[1.5, 0.58], [1.1, 0.40], [4.0, 0.34], [2.4, 0.70], [1.2, 1.0], [1.0, 1.6]]) {
  const r = energyDecay(f, z);
  check(`f=${f} zeta=${z}`, r.ok, r.msg);
}

console.log("解析解初值条件 x(0)=x0, v(0)=v0");
for (const [f, z] of [[1.5, 0.58], [1.1, 0.40], [4.0, 0.34], [1.2, 1.0], [1.0, 1.6], [1.0, 0.0001], [1.0, 0.9999], [1.0, 1.0001]]) {
  const r = initialConditions(f, z);
  check(`f=${f} zeta=${z}`, r.ok, `dx=${r.ex.toExponential(2)} dv=${r.ev.toExponential(2)}`);
}

console.log("静止时收敛到不动点");
for (const [f, z] of [[1.5, 0.58], [1.1, 0.40], [1.2, 1.0]]) {
  const r = restConverges(f, z);
  check(`f=${f} zeta=${z}`, r.ok, `|x|=${r.x.toExponential(2)} |v|=${r.v.toExponential(2)}`);
}

console.log("跨帧率一致：一步 dt 必须等于 N 步 dt/N（误差 < 1e-12）");
for (const [f, z] of [[1.5, 0.58], [1.1, 0.40], [4.0, 0.34], [1.2, 1.0], [1.0, 1.6]]) {
  const r = semigroup(f, z);
  check(`f=${f} zeta=${z} 拆两步`, r.half < 1e-12, `${r.half.toExponential(2)}`);
  check(`f=${f} zeta=${z} 拆四步`, r.quarter < 1e-12, `${r.quarter.toExponential(2)}`);
  check(`f=${f} zeta=${z} 拆三步`, r.third < 1e-12, `${r.third.toExponential(2)}`);
}

console.log("过阻尼区不产生振荡");
{
  // 临界/过阻尼的弹簧应当单调回到平衡点，一次都不许过冲。
  // 出现振荡就是分支判断写错了（比如 zeta=1 掉进了简谐那一支）。
  for (const [f, z] of [[1.2, 1.0], [1.0, 1.6], [1.0, 3.0]]) {
    const s = new Spring(f, z);
    s.x = 1;
    let over = 0, maxAbs = 0;
    for (let i = 0; i < 240 * 10; i++) {
      s.step(0, 1 / 240);
      if (s.x < -1e-12) over = Math.max(over, -s.x);
      maxAbs = Math.max(maxAbs, Math.abs(s.x));
    }
    check(`f=${f} zeta=${z} 无过冲`, over < 1e-12 && maxAbs <= 1 + 1e-12,
      `overshoot=${over.toExponential(2)} peak=${maxAbs.toExponential(6)}`);
  }
}

console.log("大步长稳定性");
{
  const r = stabilityAtLargeStep();
  check("19Hz @ 60fps 不发散", r.ok, `x=${r.x}`);
}
{
  // 标签页切回来时 dt 可能有好几秒。数值积分在这里要么发散要么得封顶丢时间，
  // 闭式解两种代价都不付：直接一步算到平衡点，且不发散。
  const s = new Spring(1.5, 0.58);
  s.x = 1; s.v = 0;
  s.step(0, 5);
  const ok = Number.isFinite(s.x) && Math.abs(s.x) < 1e-9;
  check("dt=5s 一步到位且不发散", ok, `x=${s.x}`);
}

console.log("眨眼波形");
{
  const r = blinkAsymmetry();
  check("睁眼段明显长于闭眼段", r.ok,
    `close=${(r.closeDur * 1000).toFixed(0)}ms open=${(r.openDur * 1000).toFixed(0)}ms`);
}

console.log("注视点");
{
  const r = customGazeDriftIsSafe();
  check("custom 注视点下微扫视不抛异常", r.ok, r.msg);
}

console.log("慢层精神状态");
{
  const r = energyLayer();
  check("静置会犯困、互动会回神", r.ok,
    `start=${r.start} drained=${r.drained.toFixed(3)} woken=${r.woken.toFixed(3)} stage=${r.stage}`);
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
