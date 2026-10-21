/**
 * §4.1:218 源色口径闸门：边可见性（`ART_STYLE_TARGET.md:218`）。
 *
 * 判据原文：「`BRIGHT.<X>` 对本格底色 `dL*` >=8，**且** 对紧邻那一格底色 `dL*` >=8」。
 * 本文件逐对实现它：X 是高材质格，Y 是任意一个比 X 低的邻格，一共 10 对。
 *
 * 口径：源色，不依赖渲染、不依赖浏览器，`npm run test:inprocess` 可跑。
 * 度量为 CIE `L*`（从 sRGB 换算）。阈值 8 是 §4.1 的验收线（`terrain-edges.test.ts`
 * 里的 6 是「不退化」下限，两者不同，不要混用）。
 *
 * 为什么「对邻格」也要管：亮边画在高格北/西内缘，外侧紧邻的就是低邻格。
 * 边色若等于邻格底色，那条交界上的边**必然**融进外侧、只让高格少 1px，是 no-op。
 *
 * 实测结论（2026-10-21，24 色）：**§4.1:218 的完整判据当前 1/10 达标**。
 *
 * **剩下 9 对不是缺陷，但也不是达标 —— 它们靠暗边或底色差兜底可用。**
 * 其中 `stone->plaza` 是真缺陷：兜底只剩暗边 20.80 一条线索（底色差 4.95 撑不住）。
 * 见 §1.4c。
 *
 * 这句话有两个不同的意思，不能混：
 *   - **文档写的标准达不到** —— 10 对里 9 对满足不了「对邻格 >=8」
 *   - **不等于这些边现在合格** —— 它们只是「靠兜底可用」，不是「靠 §4.1:218 达标」
 *
 * 所以 `KNOWN_FAILING` 里那 9 条的性质是**已知不达标 + 有替代线索**，不是「已验收」。
 * 测试记录差距，文档记录目标，两者不一致正是这个差距的表达 —— 不要把 `:218` 降成
 * 达到现状的宽松版，那会把这个差距抹掉。
 *
 * 达不到的原因不是实现 bug：`BRIGHT.grass/farm/stone` 都取 `plaza`，而 `plaza` 本身就是
 * 它们的低邻格之一，于是这些交界上边色与邻格同色（0.00）。一个 token 无法同时远离 4 个
 * 低邻格底色 —— 色带只有 20 L* 宽。要真达标只能改 `BRIGHT` 表，那属调色板决策，
 * 需 ADR + 用户批准。
 *
 * 防线是两条叠加的，缺一不可：
 *   1. 逐对测试 —— 达标的对真断言；不达标的按 `KNOWN_FAILING` skip
 *   2. 守卫测试 —— 不达标集合必须与清单完全一致，且登记值与实测一致（容差 0.05）
 *   单靠第 1 条不够：被 skip 的对没有断言在挡它们继续变差。单靠第 2 条也不够：
 *   它只查集合与登记值，不查每条边自己的断言。
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TERRAIN_COLORS } from '../src/world/palette-data.ts';
import { BRIGHT, TERRAIN_HEIGHT } from '../src/render/terrain-edges.ts';
import type { TerrainId } from '../src/world/types.ts';

/** §4.1:218 的验收线。 */
const MIN_DL = 8;

/** sRGB 十六进制 → CIE L*。 */
function lstar(hex: number): number {
  const channel = (v: number): number => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  const r = channel((hex >> 16) & 0xff);
  const g = channel((hex >> 8) & 0xff);
  const b = channel(hex & 0xff);
  const y = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return y > 0.008856 ? 116 * y ** (1 / 3) - 16 : 903.3 * y;
}

const dl = (a: number, b: number): number => Math.abs(lstar(a) - lstar(b));

const TERRAINS: readonly TerrainId[] = ['tree', 'grass', 'farm', 'stone', 'plaza'];

/** 一对「高格 X / 低邻格 Y」上亮边的实测结果。 */
interface PairResult {
  readonly key: string;
  readonly self: TerrainId;
  readonly neighbor: TerrainId;
  readonly token: number;
  /** 边色 vs 本格底色。 */
  readonly own: number;
  /** 边色 vs 邻格底色。 */
  readonly against: number;
  /** 按 §4.1:218 是否达标（两个条件都要满足）。 */
  readonly passes: boolean;
}

/** 枚举所有 (高格, 更低邻格) 对，逐对算实测值。 */
function measurePairs(): readonly PairResult[] {
  const out: PairResult[] = [];
  for (const self of TERRAINS) {
    for (const neighbor of TERRAINS) {
      if (TERRAIN_HEIGHT[neighbor] >= TERRAIN_HEIGHT[self]) continue;
      const token = BRIGHT[self];
      const own = dl(token, TERRAIN_COLORS[self]);
      const against = dl(token, TERRAIN_COLORS[neighbor]);
      out.push({
        key: `${self}->${neighbor}`,
        self,
        neighbor,
        token,
        own,
        against,
        passes: own >= MIN_DL && against >= MIN_DL,
      });
    }
  }
  return out;
}

const PAIRS = measurePairs();

/**
 * 已知不达标的对，附实测值与它靠什么兜底。
 *
 * 这张表是**已知限制清单**，不是豁免名单：
 *   - 新出现的不达标对 -> 清单外的 key -> 下面那条测试会红
 *   - 清单里的对修好了 -> 值变了 -> 也会红，提示把该行删掉
 * 所以它不会悄悄过期。
 */
const KNOWN_FAILING: Readonly<Record<string, { own: number; against: number; fallback: string }>> = {
  // tree 亮边 grassLit 只比 4 个低邻格的底色亮 1.54-6.49，但 tree 底色 soil 极深，
  // 与各邻格底色差 11.99 以上，边界靠底色差就够。这 4 条不是缺陷。
  'tree->grass': { own: 14.31, against: 3.02, fallback: '底色差 tree/grass' },
  'tree->farm': { own: 14.31, against: 4.33, fallback: '底色差 tree/farm' },
  'tree->stone': { own: 14.31, against: 1.54, fallback: '底色差 tree/stone' },
  'tree->plaza': { own: 14.31, against: 6.49, fallback: '底色差 tree/plaza' },
  // grass/farm 亮边都是 plaza，邻 stone 时只有 4.95；靠暗边 soil 对 stone 15.85 兜底。
  'grass->stone': { own: 9.51, against: 4.95, fallback: '暗边 soil 对 stone' },
  'farm->stone': { own: 10.82, against: 4.95, fallback: '暗边 soil 对 stone' },
  // grass/farm 邻 plaza 时边色与邻格同色（0.00）。grass 靠底色差 9.51，farm 靠 10.82。
  'grass->plaza': { own: 9.51, against: 0, fallback: '底色差 grass/plaza' },
  'farm->plaza': { own: 10.82, against: 0, fallback: '底色差 farm/plaza' },
  // stone 唯一低邻格就是 plaza，边色与它同色（0.00）；对本格也只有 4.95。
  // 底色差 stone/plaza 只有 4.95 撑不住，只剩暗边 20.80 一条线索 —— 真缺陷，见 §1.4c。
  'stone->plaza': { own: 4.95, against: 0, fallback: '仅暗边 soil 对 plaza 20.80（底色差 4.95 不足）' },
};

/* ---------------------------------------------------------- 逐对断言（§4.1:218） */

for (const pair of PAIRS) {
  const known = KNOWN_FAILING[pair.key];
  // 达标的对真跑断言；不达标的按已知限制跳过，skip 原因里带实测值，
  // 免得三个月后有人以为这条边是好的。
  const skipReason = pair.passes
    ? false
    : known === undefined
      ? false // 未登记的不达标对：不跳过，让它直接红
      : `§4.1:218 未达标（本格 ${pair.own.toFixed(2)} / 邻格 ${pair.against.toFixed(2)}），`
        + `靠 ${known.fallback} 兜底。改 BRIGHT 表属调色板决策，需 ADR + 用户批准`;

  test(`§4.1:218 ${pair.key}：边色对本格与对邻格 dL* 均 >= ${MIN_DL}`, { skip: skipReason }, () => {
    assert.ok(
      pair.own >= MIN_DL,
      `${pair.key} 对本格底色只有 ${pair.own.toFixed(2)}`, 
    );
    assert.ok(
      pair.against >= MIN_DL,
      `${pair.key} 对邻格底色只有 ${pair.against.toFixed(2)}`,
    );
  });
}

/* ---------------------------------------------------------- 清单守卫 */

/**
 * 上面的逐对测试对已知不达标的对是 skip，所以它们永远不会红。
 * 这条负责真正的闸门作用：**不达标集合必须与 KNOWN_FAILING 完全一致**。
 *
 * 两种偏差都会红：
 *   1. 出现清单外的不达标对（新的退化）
 *   2. 清单内的对已达标（清单过期，该删行）
 */
test('不达标集合必须与 KNOWN_FAILING 完全一致（防新退化与清单过期）', () => {
  const failingKeys = PAIRS.filter((p) => !p.passes).map((p) => p.key).sort();
  const registeredKeys = Object.keys(KNOWN_FAILING).sort();

  const appeared = failingKeys.filter((k) => !(k in KNOWN_FAILING));
  const fixed = registeredKeys.filter((k) => !failingKeys.includes(k));

  assert.deepEqual(
    appeared,
    [],
    `出现清单外的不达标边，属新退化：${appeared.join('、')}。`
      + ' 要么修 BRIGHT 表，要么登记进 KNOWN_FAILING 并写清兜底。',
  );
  assert.deepEqual(
    fixed,
    [],
    `这些边已达标，KNOWN_FAILING 里的记录过期了，删掉对应行：${fixed.join('、')}`,
  );
});

/** 登记的实测值必须仍然成立，否则清单在说假话。 */
test('KNOWN_FAILING 的实测值必须与当前一致（容差 0.05）', () => {
  const drift: string[] = [];
  for (const pair of PAIRS) {
    const known = KNOWN_FAILING[pair.key];
    if (known === undefined) continue;
    if (Math.abs(known.own - pair.own) > 0.05 || Math.abs(known.against - pair.against) > 0.05) {
      drift.push(
        `${pair.key} 记为 本格 ${known.own} / 邻格 ${known.against}，`
          + `实为 ${pair.own.toFixed(2)} / ${pair.against.toFixed(2)}`,
      );
    }
  }
  assert.deepEqual(drift, [], `以下登记已失真：${drift.join('；')}`);
});

/**
 * 当前达标率必须与文档一致。
 *
 * §4.1:218 写的是「均 >=8」，而实测 10 对只有 1 对达标。这条把那个比例锁住，
 * 避免有人以为 §4.1:218 已经全线生效。修好若干对之后这个数字会变，届时同步改文档。
 */
test('达标对数与文档记录一致（当前 1/10）', () => {
  const passing = PAIRS.filter((p) => p.passes).map((p) => p.key);
  assert.equal(
    PAIRS.length,
    10,
    '材质对数量变了，说明高度序或材质集合被改过，需重看 §4.1:218 的覆盖面',
  );
  assert.deepEqual(
    passing,
    ['grass->farm'],
    `当前只有 grass->farm 达标（实测），其余靠 KNOWN_FAILING 里登记的兜底。`
      + ` 若这里变了，§4.1:218 与 §1.4c 的结论要一起更新。实为：${passing.join('、') || '无'}`,
  );
});
