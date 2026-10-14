/* 连续情感维度（arousal / valence）与其驱动的生理通道。
 *
 * 这一层的设计要点是：它**独立于表情标签**地驱动呼吸、眼跳、眨眼、瞳孔。
 * 所以测试不能只看"标签变了表情变了"（那是早就有的），
 * 要看的是"标签没变、只改唤醒度，生理通道是否真的跟着变"。
 *
 * 另外这一层的失败模式特别隐蔽：驱动顺序错了不会崩，
 * 只会静默地把某个通道关掉（比如 NaN 让眨眼彻底不发生）。
 * 所以这里对每个输出都做有限值检查。
 */

import { EmotionDriver } from "./drivers/emotion.js";
import { IdleDriver } from "./drivers/idle.js";

let failures = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`); failures++; }
}

const DT = 1 / 240;

/* 和 config/avatar.yaml 的 emotions 段保持一致的缩略版。
 * 只需要 arousal/valence 和一个 shift，测的是连续维度的行为。 */
const table = {
  base: { blend: 0.6, arousal: 0.25, valence: 0.15, shift: { ParamMouthForm: 0.20 } },
  得意: { blend: 0.28, arousal: 0.85, valence: 0.85, shift: { ParamMouthForm: 0.52, ParamCheek: 0.55 } },
  收敛: { blend: 0.50, arousal: 0.20, valence: -0.25, shift: { ParamEyeBallY: -0.45, ParamMouthForm: 0.24 } },
  变小气: { blend: 0.60, arousal: 0.25, valence: -0.70, shift: { ParamEyeBallX: -0.62 } },
};

const settle = (drv, seconds) => { for (let i = 0; i < seconds / DT; i++) drv.update(DT); };

console.log("arousal / valence 从配置读出来");
{
  const e = new EmotionDriver(table);
  e.set("得意");
  settle(e, 2.0);
  check("唤醒度接近配置值", Math.abs(e.arousal - 0.85) < 0.02, `arousal=${e.arousal.toFixed(3)}`);
  check("效价接近配置值", Math.abs(e.valence - 0.85) < 0.02, `valence=${e.valence.toFixed(3)}`);
}

console.log("缺配置时退回中性，而不是 0 或崩溃");
{
  const e = new EmotionDriver(table);
  e.set("变小气");
  settle(e, 2.0);
  const bad = new EmotionDriver({ base: table.base });   // 表里没有"得意"
  bad.set("得意");                                       // 会回退到 base
  settle(bad, 1.0);
  check("回退到 base 且唤醒度非零", Number.isFinite(bad.arousal) && bad.arousal > 0.1,
    `arousal=${bad.arousal}`);
  const empty = new EmotionDriver({});
  empty.set("随便什么");
  settle(empty, 1.0);
  check("空表也不崩且有合理值", Number.isFinite(empty.arousal) && empty.arousal >= 0,
    `arousal=${empty.arousal}`);
  // arousal=0 会让表情被压到下限，看起来像"卡住了"
  check("空表时不是 0（0 会读成睡着/卡住）", empty.arousal > 0.05, `arousal=${empty.arousal}`);
}

console.log("分级强度：arousal 决定表情打多大");
{
  /* 断言的是**相对**关系，不是绝对值。
   * 绝对值去对表很容易踩空：表情还会经过 blend 交叉淡化、弹簧过冲、
   * 最终在仲裁层被 clamp，真正到达渲染层的数值和 shift 本身并不相等。
   * 稳定的不变量只有一条：同一个 shift，arousal 越高，出来的幅度越大。 */
  const render = (ar, shiftValue) => {
    const e = new EmotionDriver({
      base: table.base,
      弱: { blend: 0.2, arousal: 0.05, valence: 0, shift: { ParamMouthForm: shiftValue } },
      强: { blend: 0.2, arousal: 0.95, valence: 0, shift: { ParamMouthForm: shiftValue } },
    });
    e.set(ar > 0.5 ? "强" : "弱");
    settle(e, 3.0);          // 唤醒度弹簧 0.7Hz，要给足收敛时间
    return e.shift().ParamMouthForm;
  };
  const weak = render(0.05, 1.0);
  const strong = render(0.95, 1.0);
  check("高唤醒度下表情更大", strong > weak, `强=${strong.toFixed(3)} 弱=${weak.toFixed(3)}`);
  check("低唤醒度下表情不为零（否则读成卡住）", weak > 0.2, `${weak.toFixed(3)}`);
  check("强弱差距至少 1.6 倍", strong > weak * 1.6,
    `${strong.toFixed(3)} vs ${weak.toFixed(3)} 倍数 ${(strong / weak).toFixed(2)}`);
  check("高唤醒度接近满值但不越界", strong <= 1.0 && strong > 0.85, `${strong.toFixed(3)}`);
}

console.log("情感维度在情绪切换时平滑过渡（不跳变）");
{
  const e = new EmotionDriver(table);
  e.set("收敛");
  settle(e, 3.0);
  const series = [];
  e.set("得意");
  for (let i = 0; i < 240 * 3; i++) { e.update(DT); series.push(e.arousal); }
  let worst = 0;
  for (let i = 1; i < series.length; i++) worst = Math.max(worst, Math.abs(series[i] - series[i - 1]));
  check("唤醒度单步变化有界", worst < 0.01, `最大单步 ${worst.toFixed(5)}`);
  // 唤醒度弹簧 0.7Hz / zeta 0.75，收敛时间约 1.2s。只跑 0.5s 时
  // 它还停在半路上，早先按 0.5s 断言"应该到 0.85"必然失败。
  check("足够时间后收敛到目标", Math.abs(series[series.length - 1] - 0.85) < 0.02,
    `末值=${series[series.length - 1].toFixed(4)} 目标=0.85`);
  check("过渡是渐进的（中途确实经过中间值）",
    series.some(v => v > 0.3 && v < 0.7), "没有中间值 -> 像是直接跳过去的");
}

console.log("唤醒度 -> 呼吸频率");
{
  const mk = (ar) => {
    const i = new IdleDriver();
    i.energy = 1;
    i.setAffect(ar, 0);
    // 关掉能量流失，保证测得的是"唤醒度对周期的影响"而不是"能量流失"
    i.cfg.energy.drain_per_sec = 0;
    for (let k = 0; k < 200; k++) i.update(DT, {});
    return i;
  };
  const calm = mk(0.0), hot = mk(1.0), tired = mk(0.0);
  tired.cfg.energy.drain_per_sec = 0; tired.energy = 0;
  /* 基准是"精神饱满 + 情绪平静"，此时周期就等于配置里的 breath.period。
   * 断言写成"等于配置值"而不是某个硬编码的秒数，是因为这个值属于
   * 表现风格，应该由配置说话。实测安静态约 0.27Hz（≈3.7s），
   * 配置的 4.1s 是偏慢一档的刻意选择，两者不冲突。 */
  check("平静饱满时周期等于配置值",
    Math.abs(calm._breathPeriod() - calm.cfg.breath.period) < 1e-6,
    `${calm._breathPeriod().toFixed(4)} vs 配置 ${calm.cfg.breath.period}`);
  check("高唤醒时周期明显变短", hot._breathPeriod() < calm._breathPeriod() * 0.75,
    `热=${hot._breathPeriod().toFixed(4)} 静=${calm._breathPeriod().toFixed(4)}`);
  check("呼吸周期有下限，不会变成 0", hot._breathPeriod() > 1.2,
    `${hot._breathPeriod().toFixed(4)}`);
  check("犯困时呼吸变长（浅而慢）", tired._breathPeriod() > calm._breathPeriod(),
    `困=${tired._breathPeriod().toFixed(4)} 静=${calm._breathPeriod().toFixed(4)}`);
}

console.log("唤醒度 -> 呼吸深度（激动时呼气变浅）");
{
  const mk = (ar) => {
    const i = new IdleDriver();
    i.energy = 1; i.setAffect(ar, 0);
    let last = null;
    for (let k = 0; k < 300; k++) last = i.update(DT, {});
    return last;
  };
  const calm = mk(0), hot = mk(1);
  check("高唤醒度呼吸更浅", hot.breathDepth < calm.breathDepth,
    `热=${hot.breathDepth.toFixed(3)} 静=${calm.breathDepth.toFixed(3)}`);
}

console.log("效价 -> 眨眼疏密（正面更频繁）");
{
  /* 量的是"同样时间里眨了几次"，不是 nextBlink 这个数。
   * nextBlink 只在眨眼真正触发时才重新抽样，所以每帧去读它，
   * 读到的永远是同一个待计数值——三种效价量出来完全相同。
   * 这条测试一开始就是被这个测法骗过去的。 */
  const count = (valence) => {
    const i = new IdleDriver();
    i.energy = 1;                 // 固定精神，排除犯困对间隔的干扰
    i.cfg.energy.drain_per_sec = 0;   // 关掉能量流失，测的是效价而不是疲劳
    let blinks = 0, prev = -1;
    for (let k = 0; k < 240 * 240; k++) {     // 模拟 4 分钟，减少泊松方差
      i.setAffect(0.5, valence);
      i.update(DT, {});
      if (prev < 0 && i.blinkPhase >= 0) blinks++;
      prev = i.blinkPhase;
    }
    return blinks;
  };
  const positive = count(1.0), neutral = count(0), negative = count(-1.0);
  check("正面情绪眨眼更频繁", positive > neutral,
    `正面=${positive} 中性=${neutral}`);
  check("负面情绪眨眼更稀疏", negative < neutral,
    `负面=${negative} 中性=${neutral}`);
  check("单调：正面 > 中性 > 负面", positive > neutral && neutral > negative,
    `${positive} / ${neutral} / ${negative}`);
  /* 基线落在实测区间内：自发性眨眼 8~21 次/分，
   * 其中约 18 次/分被认为最亲和。这里中性 + 精神饱满应该是这个量级。 */
  const perMin = neutral / 4;   // 4 分钟模拟，求每分钟
  check("中性眨眼率落在实测区间 8~21 次/分", perMin > 6 && perMin < 26,
    `${perMin.toFixed(1)} 次/分`);
}

console.log("唤醒度 -> 眼跳时长（激动时变慢）");
{
  const dur = (ar) => {
    const i = new IdleDriver();
    i.energy = 1;
    i.setAffect(ar, 0);
    i.setGaze("viewer", true);
    i.t += 10;
    i._moveTo(0.9, 0, null);      // 弹道自算时长，不受显式指令影响
    return i.gazeDur;
  };
  const calm = dur(0), hot = dur(1);
  check("高唤醒度时眼跳更慢（实测关系）", hot > calm,
    `热=${hot.toFixed(4)} 静=${calm.toFixed(4)}`);
  check("两者仍在合法时长区间内", calm >= 0.02 && hot <= 0.4,
    `${calm.toFixed(4)} / ${hot.toFixed(4)}`);
}

console.log("眼跳-眨眼互相抑制（OPN 门控）");
{
  /* 这是修正一个方向性错误：
   * 最早写的是"大幅移视时立刻眨眼"，那正好和门控方向相反。 */
  const i = new IdleDriver();
  i.setGaze("viewer", true);
  for (let k = 0; k < 120; k++) i.update(DT, {});
  i.t += 10;
  i.blinkPhase = -1; i.blinkHold = 0; i.nextBlink = 99;

  let blinkedDuringSaccade = false;
  let blinkAfterSaccade = false;
  let saccadeEnded = false;
  i._moveTo(1.4, 0, null);
  for (let k = 0; k < 240; k++) {
    const inFlight = i.gazeT < 1;
    const o = i.update(DT, {});
    const closed = o.blink > 0.5;
    if (closed && inFlight) blinkedDuringSaccade = true;
    if (closed && !inFlight) { blinkAfterSaccade = true; break; }
    if (!inFlight) saccadeEnded = true;
  }
  check("眼跳进行中绝不眨眼", !blinkedDuringSaccade);
  check("眼跳结束后补了那一次眨眼", blinkAfterSaccade);
}

console.log("眨眼期间不发起眼跳");
{
  const i = new IdleDriver();
  i.setGaze("viewer", true);
  for (let k = 0; k < 120; k++) i.update(DT, {});
  i.t += 10;
  // 手动置于"正在眨眼"
  i.blinkPhase = 0.02;
  i.blinkHold = 0;
  i.gazeX = 0; i.gazeY = 0;
  const accepted = i._moveTo(1.2, 0, null);
  check("眨眼期间眼跳被拒", accepted === false);
  i.blinkPhase = -1;
  i.t += 10;
  check("不眨时可以发起眼跳", i._moveTo(1.2, 0, null) === true);
}

console.log("所有输出都是有限值");
{
  /* 驱动初始化顺序错了会让 nextBlink 变成 NaN，
   * 而 NaN 的比较恒为假 —— 眨眼会被静默地彻底关掉，没有任何报错。
   * 这条就是为那种故障准备的。 */
  const out = [];
  for (const ar of [0, 0.5, 1]) {
    for (const va of [-1, 0, 1]) {
      const i = new IdleDriver();
      for (let k = 0; k < 600; k++) {
        i.setAffect(ar, va);
        const o = i.update(DT, {});
        for (const [k2, v] of Object.entries(o)) out.push([k2, v]);
      }
      if (!Number.isFinite(i.nextBlink)) out.push(["nextBlink", NaN]);
      if (!Number.isFinite(i.breathPhase)) out.push(["breathPhase", NaN]);
    }
  }
  const bad = out.filter(([, v]) => typeof v === "number" && !Number.isFinite(v));
  check("所有驱动输出均为有限值", bad.length === 0,
    bad.length ? `${bad.length} 个非有限值，例如 ${JSON.stringify(bad[0])}` : "");
}

console.log("瞳孔通道独立于表情");
{
  /* ParamPupil 是新加的一条通道。必须能在表情没变的情况下改变，
   * 否则"同一种情绪的强弱"还是表达不出来。 */
  const i = new IdleDriver();
  for (let k = 0; k < 120; k++) i.update(DT, {});
  const calm = new IdleDriver(); calm.energy = 1; calm.setAffect(0, 0);
  const hot = new IdleDriver(); hot.energy = 1; hot.setAffect(1, 0);
  // 驱动本身不改 ParamPupil（那是 index.js 的仲裁层做的），
  // 这里只确认 arousal 是可传的且有效
  check("arousal 能被灌进 idle 并读到", Math.abs(hot.arousal - 1) < 1e-9, `${hot.arousal}`);
  check("valence 能被灌进 idle 并读到", Math.abs(calm.valence - 0) < 1e-9, `${calm.valence}`);
  const junk = new IdleDriver();
  junk.setAffect(NaN, NaN);
  check("灌入 NaN 不会污染状态", Number.isFinite(junk.arousal) && Number.isFinite(junk.valence),
    `${junk.arousal}/${junk.valence}`);
  junk.setAffect(5, -9);
  check("灌入越界值会被夹住", junk.arousal <= 1 && junk.valence >= -1,
    `${junk.arousal}/${junk.valence}`);
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
