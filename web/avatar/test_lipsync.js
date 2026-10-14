/* 共发音（coarticulation）的性质测试。
 *
 * 这里的判据都不是"看起来对不对"，而是能从模型定义直接推出来的性质。
 * 目型动画的 bug 特别难靠肉眼发现——嘴只要没崩就很难看出偏差，
 * 而偏差恰恰是"像不像"的主要来源。所以能写成性质的就写成判据。
 */

import { buildTimeline, sampleTimeline, COARTICULATION } from "./timeline.js";

let failures = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`); failures++; }
}

const trace = (tl, dur, n = 400) =>
  Array.from({ length: n + 1 }, (_, i) => sampleTimeline(tl, (i / n) * dur));

/* 只取真正的音素段，排除首尾补上的静止段。
 * buildTimeline 会 unshift/push 一个 rest 段来给共发音提供起点和终点，
 * 所以"segments[0]"已经不是第一个音素了——按位置取会拿到静止段，
 * 于是"元音张开度"、"闭塞音闭合度"这类断言全部失真。
 * 这个坑踩过一次，所以在这里封一个函数，别再散着写过滤条件。 */
const phonemes = (tl) => tl.segments.filter(s => !s.rest);

console.log("边界条件");
{
  const dur = 1.0;
  const t = buildTimeline("我要喝奶茶", dur);
  const a = sampleTimeline(t, 0);
  const b = sampleTimeline(t, dur);
  const c = sampleTimeline(t, dur + 5);
  check("t=0 有值（不是 null）", a !== null);
  check("t=dur 有值", b !== null);
  check("t 远超时长有值且不崩", c !== null && Number.isFinite(c.open));
  const e = buildTimeline("", 0.5);
  check("空文本也能采样", sampleTimeline(e, 0.1) !== null);
  const z = buildTimeline("   ", 0);
  check("全空白 + duration=0 不崩", sampleTimeline(z, 0.05) !== null);
}

console.log("输出恒在合法范围（不变量）");
{
  const texts = ["爸爸", "八八八", "我要喝奶茶", "嗯？", "好的好的好的", "A", "hello world",
                 "好好学习，天天向上！", "嗯嗯嗯", "b", "pppp"];
  let bad = null;
  for (const text of texts) {
    const dur = 0.6 + text.length * 0.12;
    const tl = buildTimeline(text, dur);
    for (let i = 0; i <= 300; i++) {
      const f = sampleTimeline(tl, (i / 300) * dur);
      if (!f) { bad = `${text}: 第 ${i} 点返回 null`; break; }
      for (const k of ["open", "wide", "round"]) {
        if (!Number.isFinite(f[k])) { bad = `${text}: ${k} = ${f[k]}`; break; }
      }
      if (f.open < -1e-9 || f.open > 1 + 1e-9) { bad = `${text}: open = ${f.open}`; break; }
      if (f.closureStrength !== undefined && (f.closureStrength < 0 || f.closureStrength > 1)) {
        bad = `${text}: closureStrength = ${f.closureStrength}`; break;
      }
    }
    if (bad) break;
  }
  check("所有文本/所有采样点：有限值且 open∈[0,1]", !bad, bad || "");
}

console.log("连续性：不能有断点");
{
  /* 这里要测的是"断点"，不是"够不够平缓"。
   *
   * 一开始这里写的是"单步变化 < 0.02"，结果稳定地测出一个 0.076 的"跳变"。
   * 追下去发现根本不是 bug：那是 b/p/m 的双唇闭合，
   * 而双唇闭合本来就要在 30~50ms 内完成，本来就是陡的。
   * 拿"平缓"当判据，会逼着代码去把真实的闭塞音抹平——测的是错的性质。
   *
   * 真正的断点有很鲜明的特征：孤零零一个大跳，旁边全是小步。
   * 所以用"相对中位数"的判据：既要有绝对下限（滤掉浮点噪声），
   * 又要比中位数大一个数量级（这样持续性的陡坡不会被误判）。 */
  let worst = null;
  for (const text of ["爸爸", "我要喝奶茶", "好的好的好的", "好好学习，天天向上", "八八八八"]) {
    const dur = 0.6 + text.length * 0.12;
    const tl = buildTimeline(text, dur);
    const n = 4000;
    const steps = [];
    let prev = sampleTimeline(tl, 0).open;
    for (let i = 1; i <= n; i++) {
      const cur = sampleTimeline(tl, (i / n) * dur).open;
      steps.push(Math.abs(cur - prev));
      prev = cur;
    }
    /* 断点的判据是"局部离群"：夹在两个安静邻居中间的一个大步。
     * 用全局统计量（比如中位数）不成立——口型在平台上时大部分步长是 0，
     * 中位数就是 0，于是"比中位数大 10 倍"退化成恒真，
     * 真正的闭塞音这种合法陡坡反而会被放过（或者反过来全被误报）。 */
    for (let i = 1; i < steps.length - 1; i++) {
      const s = steps[i];
      const nb = Math.max(steps[i - 1], steps[i + 1]);
      if (s > 0.05 && s > nb * 6) {
        worst = `${text} 第 ${i} 步跳变 ${s.toFixed(4)}，邻居 ${steps[i - 1].toFixed(4)}/${steps[i + 1].toFixed(4)}`;
        break;
      }
    }
    if (worst) break;
  }
  check("没有孤立的大跳变（断点）", !worst, worst || "");
}

console.log("双唇闭合的时长在生理范围内");
{
  /* 闭塞音要快，但不能快到读成"闪"。真人 b/p/m 的闭合约 30~50ms。
   * 慢于 150ms 就不像闭塞音了（像含在嘴里拖音），
   * 快于 12ms 则会看成帧间跳变。 */
  const dur = 1.0;
  const tl = buildTimeline("爸爸", dur);
  for (const s of phonemes(tl)) {
    if (!s.closure) continue;
    const mid = (s.t0 + s.t1) / 2;
    // 找出"闭合量达到 90% 所需的时间"
    const peakOpen = sampleTimeline(tl, s.t0 - 0.002).open;
    let t0 = null, t1 = null;
    const step = 0.0005;
    const floor = peakOpen * 0.1;
    for (let t = s.t0 - 0.06; t <= s.t1 + 0.06; t += step) {
      const o = sampleTimeline(tl, t).open;
      if (t0 === null && o <= floor) t0 = t;
      if (t0 !== null && o > floor) { t1 = t; break; }
    }
    if (t0 === null || t1 === null) continue;
    const closeMs = (t1 - t0) * 1000;
    check(`闭合段 [${s.t0.toFixed(2)},${s.t1.toFixed(2)}] 时长 ${closeMs.toFixed(0)}ms 合理`,
      closeMs > 12 && closeMs < 150, `${closeMs.toFixed(0)}ms`);
  }
}

console.log("提前共发音：嘴比声音早动");
{
  // 这是共发音最核心的可观察后果，也是硬切/线性插值永远做不到的。
  // 判据：在音节的 t0 之前，open 就已经离开静止值开始变化。
  //
  // 用例必须挑零声母的音节（爱/哦/啊）。挑"八"这类以 b/p/m 开头的，
  // 它自己的起始口型就是闭合，起点和终点都是 0，看不出提前与否——
  // 那种用例会让判据恒真，等于没测。
  const dur = 1.2;
  const tl = buildTimeline("爱", dur);
  const vowel = phonemes(tl)[0];
  check("用的是以元音起始的音节", vowel.open > 0.5,
    `首段 open=${vowel.open}（是闭合音素的话本条测试恒真，无意义）`);
  check("提前量窗口存在（a0 < t0）", vowel.a0 < vowel.t0,
    `a0=${vowel.a0.toFixed(4)} t0=${vowel.t0.toFixed(4)}`);

  const rest = sampleTimeline(tl, Math.max(0, vowel.a0 - 1e-3)).open;
  // 取窗口中点：此时 influence 应当已经明显上升，但还没到位
  const probe = (vowel.a0 + vowel.t0) / 2;
  const atProbe = sampleTimeline(tl, Math.max(0, probe)).open;
  const target = sampleTimeline(tl, (vowel.t0 + vowel.t1) / 2).open;

  check("音节开始前嘴已在成形", atProbe > rest && atProbe < target,
    `静止=${rest.toFixed(3)} 提前段=${atProbe.toFixed(3)} 目标=${target.toFixed(3)}`);

  // 提前量必须真的有量级：只有 1~2ms 的提前量等于没有
  const lead = vowel.t0 - Math.max(0, vowel.a0);
  check("提前量有实际量级（>20ms）", lead > 0.020, `lead=${(lead * 1000).toFixed(1)}ms`);
}

console.log("抗混合：双唇音必须真的闭合");
{
  // 加权平均在数学上到不了 0，唇上留一条缝的 b 不像 b。
  // 这是"只做 dominance 不做 resistance"被反复批评的那个问题。
  const dur = 1.0;
  const tl = buildTimeline("爸爸", dur);
  let minOpen = 1;
  for (const s of phonemes(tl)) {
    if (!s.closure) continue;
    const mid = (s.t0 + s.t1) / 2;
    const f = sampleTimeline(tl, mid);
    minOpen = Math.min(minOpen, f.open);
  }
  check("闭合段中部 open 基本为 0", minOpen < 0.02, `最小 open=${minOpen.toFixed(4)}`);
  check("所有 b/p/m 都标了 closure",
    tl.segments.filter(s => s.closure).length === 2, `实际 ${tl.segments.filter(s => s.closure).length}`);
}

console.log("抗混合不能过度：元音仍要张得开");
{
  // 连着好几个 b 的句子（爸爸爸爸）如果把闭合强度堆起来，
  // 中间的元音会被死死压住，整串像含在嘴里说话。
  // 这条和上一条互为约束：既不能合不拢，也不能张不开。
  const dur = 1.4;
  const tl = buildTimeline("爸爸", dur);
  let maxOpen = 0;
  for (const s of phonemes(tl)) {
    if (s.closure) continue;
    const f = sampleTimeline(tl, (s.t0 + s.t1) / 2);
    maxOpen = Math.max(maxOpen, f.open);
  }
  check("元音区 open 接近满值", maxOpen > 0.8, `最大 open=${maxOpen.toFixed(3)}`);

  const tl2 = buildTimeline("爸爸爸爸", dur);
  let minVowel = 1;
  for (const s of phonemes(tl2)) {
    if (s.closure) continue;
    minVowel = Math.min(minVowel, sampleTimeline(tl2, (s.t0 + s.t1) / 2).open);
  }
  check("连读时元音仍能张开", minVowel > 0.7, `最小元音 open=${minVowel.toFixed(3)}`);
}

console.log("提前量有绝对上限");
{
  // 长元音若按比例提前，会在前面短促的闭合音还没结束时就把嘴拉开，
  // 于是 ba 的双唇永远合不拢。绝对上限就是防这个的。
  let viol = null;
  for (const text of ["啊啊啊啊", "我要喝奶茶", "哈哈哈"]) {
    const dur = 0.5 + text.length * 0.2;
    const tl = buildTimeline(text, dur);
    for (const s of tl.segments) {
      const lead = s.t0 - s.a0;
      if (lead > COARTICULATION.lead_cap + 1e-6) {
        viol = `${text}: lead=${lead.toFixed(4)} > cap=${COARTICULATION.lead_cap}`;
      }
    }
  }
  check("没有任何段的提前量超过上限", !viol, viol || "");
}

console.log("时间轴长度守恒");
{
  // 极短段被并进上一段而不是丢弃。丢弃会让该段时间的权重凭空消失，
  // 后面所有音节的时间起点跟着前移，整句就对不上音频了。
  const dur = 0.4;
  const tl = buildTimeline("好好好好好", dur);
  const last = phonemes(tl)[phonemes(tl).length - 1];
  check("最后一段不超出时长", last.t1 <= dur + 1e-6, `t1=${last.t1.toFixed(4)} dur=${dur}`);
  check("段数不为 0", tl.segments.length > 0);
  let ordered = true;
  for (let i = 1; i < tl.segments.length; i++) {
    if (tl.segments[i].t0 < tl.segments[i - 1].t0 - 1e-9) ordered = false;
    if (tl.segments[i].t1 < tl.segments[i].t0) ordered = false;
  }
  check("段按时间有序且 t1>=t0", ordered);
}

console.log("说话时嘴不能一直是同一形状");
{
  // 静默的面孔和"在念同一句话"的面孔只差这一点。
  const dur = 1.5;
  const tl = buildTimeline("我要喝奶茶", dur);
  const fs = trace(tl, dur, 200);
  const opens = fs.map(f => f.open);
  const range = Math.max(...opens) - Math.min(...opens);
  const uniq = new Set(opens.map(v => Math.round(v * 20))).size;
  check("张口幅度有足够变化范围", range > 0.4, `range=${range.toFixed(3)}`);
  check("取值不是少数几档（说明在混合而非查表）", uniq > 6, `${uniq} 档`);
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
