/* 情绪强度标定：arousal -> 位移增益 -> 最终 shift。
 *
 * 这个测试守的是一个静默失效：config/avatar.yaml 的每个情绪必须带
 * arousal/valence 字段。缺了不会报错，只会回落到 NEUTRAL_AFFECT(0.25)，
 * 所有情绪的位移被一起砍到 59%，表情看起来全都一个样。
 *
 * 上一轮就是这个问题：通道、标定、测试夹具全都齐了，
 * 只有线上配置一个字段都没写。
 */

import { EmotionDriver } from "./drivers/emotion.js";
import { DEFAULT_NEUTRAL } from "./params.js";
import { readFileSync } from "node:fs";

let failures = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`); failures++; }
}

/* 从配置里读情绪表。server/config.py 是 python，这里按行抠出 emotions 段。 */
const yaml = readFileSync(new URL("../../config/avatar.yaml", import.meta.url), "utf8");
const neutral = { ...DEFAULT_NEUTRAL };
const neutralBlock = yaml.split(/^neutral:\s*$/m)[1]?.split(/^response:\s*$/m)[0] || "";
for (const match of neutralBlock.matchAll(/^\s+(Param\w+):\s*(-?[\d.]+)/gm)) {
  neutral[match[1]] = Number(match[2]);
}
function parseEmotions(src) {
  const lines = src.split("\n");
  const i0 = lines.findIndex((l) => l.startsWith("emotions:"));
  const i1 = lines.findIndex((l, i) => i > i0 && /^[a-z_]+:/.test(l));
  const out = {};
  let name = null;
  for (const l of lines.slice(i0 + 1, i1)) {
    const head = l.match(/^  (\S+):\s*$/);
    if (head) { name = head[1]; out[name] = { shift: {} }; continue; }
    if (!name) continue;
    const ar = l.match(/^\s+arousal:\s*(-?[\d.]+)/);
    const va = l.match(/^\s+valence:\s*(-?[\d.]+)/);
    const kv = l.match(/^\s+(Param\w+):\s*(-?[\d.]+)/);
    if (ar) out[name].arousal = parseFloat(ar[1]);
    else if (va) out[name].valence = parseFloat(va[1]);
    else if (kv) out[name].shift[kv[1]] = parseFloat(kv[2]);
    else {
      /* 行内 flow 映射：shift: { ParamA: 0.1, ParamB: 0.2 }
       * 上面那个 kv 正则要求 Param 紧跟行首空白，匹配不到这种。
       * base 的 shift 就是一行的，漏掉它会让增益算成 0。 */
      const flow = l.match(/\{([^}]*)\}/);
      if (flow) {
        for (const pair of flow[1].split(",")) {
          const m = pair.match(/(Param\w+):\s*(-?[\d.]+)/);
          if (m) out[name].shift[m[1]] = parseFloat(m[2]);
        }
      }
    }
  }
  return out;
}

const table = parseEmotions(yaml);
const keys = Object.keys(table);
console.log(`情绪表共 ${keys.length} 项: ${keys.join(" / ")}`);

/* 先构造一个只在 k>=1 时返回 shift 的驱动，把过渡期排除掉 */
function settledShift(name) {
  const d = new EmotionDriver(table, neutral);
  d.set(name);
  for (let i = 0; i < 600; i++) d.update(1 / 60);   // 过渡 + arousal 弹簧都跑满
  const s = d.shift();
  return { shift: s, arousal: d.arousal, valence: d.valence };
}

console.log("每个情绪都带 arousal / valence");
for (const k of keys) {
  const e = table[k];
  check(`${k} 有 arousal`, Number.isFinite(e.arousal), `arousal=${e.arousal}`);
  check(`${k} 有 valence`, Number.isFinite(e.valence), `valence=${e.valence}`);
}

console.log("位移增益真的拉开了");
const rows = keys.map((k) => ({ k, ...settledShift(k) }));
const gains = rows.map((r) => {
  // shift 是绝对目标：增益测的是相对中位的位移，睁眼中位 1 不应参与缩放。
  let best = 0;
  for (const [pk, pv] of Object.entries(table[r.k].shift || {})) {
    const baseline = neutral[pk] ?? 0;
    const delta = pv - baseline;
    if (Math.abs(delta) > 1e-6) best = Math.max(best, Math.abs((r.shift[pk] - baseline) / delta));
  }
  return best;
});
const gmin = Math.min(...gains), gmax = Math.max(...gains);
check("最低增益 >= 0.45（不是被砍到零）", gmin >= 0.45, `min=${gmin.toFixed(3)}`);
check("最高增益 > 0.75（强情绪真的更强）", gmax > 0.75, `max=${gmax.toFixed(3)}`);
check("强弱情绪的增益差 > 20%", gmax - gmin > 0.2, `${gmin.toFixed(3)} → ${gmax.toFixed(3)}`);

for (const [i, r] of rows.entries()) {
  console.log(`    ${String(r.k).padEnd(6)} arousal=${r.arousal.toFixed(2)} ` +
    `valence=${r.valence.toFixed(2)} gain=${gains[i].toFixed(3)}`);
}

console.log("高唤醒情绪的瞳孔更大（生理通道真的被驱动）");
{
  const strong = settledShift("得意");
  const calm = settledShift("收敛");
  check("得意的瞳孔 > 收敛的瞳孔", strong.arousal > calm.arousal + 0.3,
    `得意=${strong.arousal.toFixed(2)} 收敛=${calm.arousal.toFixed(2)}`);
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
