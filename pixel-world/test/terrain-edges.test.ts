import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TERRAIN_COLORS, WORLD } from '../src/world/palette-data.ts';
import {
  BRIGHT,
  CORNER_BIT,
  DARK,
  SIDE_BIT,
  TERRAIN_EDGE_VARIANTS,
  TERRAIN_HEIGHT,
  cornerMask,
  primitiveCount,
  sideMask,
  type EdgeNeighbors,
} from '../src/render/terrain-edges.ts';
import type { TerrainId } from '../src/world/types.ts';

/** §1.2 地形拼接边：20 变体（16 邻边 + 4 内凹角）的纯函数锁。 */

const all = (v: TerrainId | null): EdgeNeighbors => ({
  north: v, northEast: v, east: v, southEast: v, south: v, southWest: v, west: v, northWest: v,
});
const around = (
  n: TerrainId | null, ne: TerrainId | null, e: TerrainId | null, se: TerrainId | null,
  s: TerrainId | null, sw: TerrainId | null, w: TerrainId | null, nw: TerrainId | null,
): EdgeNeighbors => ({ north: n, northEast: ne, east: e, southEast: se, south: s, southWest: sw, west: w, northWest: nw });

const TERRAINS: readonly TerrainId[] = ['tree', 'grass', 'farm', 'stone', 'plaza'];

test('变体总数 = 16 邻边 + 4 内凹角 = 20', () => {
  assert.equal(TERRAIN_EDGE_VARIANTS, 20);
});

test('材质高度序 tree > grass > farm > stone > plaza', () => {
  const order: readonly TerrainId[] = ['tree', 'grass', 'farm', 'stone', 'plaza'];
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(
      TERRAIN_HEIGHT[order[i - 1]!] > TERRAIN_HEIGHT[order[i]!],
      `${order[i - 1]} 必须高于 ${order[i]}`,
    );
  }
});

test('全同材质不画边', () => {
  for (const self of TERRAINS) {
    assert.equal(sideMask(self, all(self)), 0, `${self} 四周同材质时不该有边`);
    assert.equal(cornerMask(self, all(self)), 0);
    assert.equal(primitiveCount(self, all(self)), 0);
  }
});

test('边只画在高度更高的一侧', () => {
  for (const self of TERRAINS) {
    for (const other of TERRAINS) {
      const mask = sideMask(self, all(other));
      const shouldDraw = TERRAIN_HEIGHT[other] < TERRAIN_HEIGHT[self];
      assert.equal(
        mask !== 0,
        shouldDraw,
        `${self} 邻 ${other}：${shouldDraw ? '应画' : '不应画'}`,
      );
    }
  }
});

test('越界邻格（世界边界）不画边', () => {
  for (const self of TERRAINS) assert.equal(sideMask(self, all(null)), 0);
});

test('四面都更低时四条边全亮（mask=15）', () => {
  const low = around('plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza', 'plaza');
  assert.equal(sideMask('tree', low), 15);
});

test('内凹角：直角边都没画、斜角更低时才补角', () => {
  // 只有西北斜角更低，两条直角邻边（N/W）同高
  const onlyDiag = around('grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'plaza');
  assert.equal(sideMask('grass', onlyDiag), 0);
  assert.equal(cornerMask('grass', onlyDiag), CORNER_BIT.northWest);

  // N 也画了线 → 交点已被那条线覆盖，不再补角
  const northLow = around('plaza', 'grass', 'grass', 'grass', 'grass', 'grass', 'grass', 'plaza');
  assert.equal(sideMask('grass', northLow), SIDE_BIT.north);
  assert.equal(cornerMask('grass', northLow), 0);
});

test('每格最多 4 条边 + 4 个角', () => {
  const values: readonly (TerrainId | null)[] = [null, ...TERRAINS];
  for (const self of TERRAINS) {
    for (const v of values) {
      for (const diag of values) {
        // 四条边全画、四个斜角全更低但也可能补角，上界是 8
        const a = around(v, diag, v, diag, v, diag, v, diag);
        assert.ok(primitiveCount(self, a) <= 8);
      }
    }
  }
});

/**
 * 回归：tree 的底色就是 soil，暗边若也用 soil 会与底色完全相同、亮度落差 0.0，
 * 等于白画（实测 96 个 tree 格里有 48 个的边完全看不见，占总写入像素 25.6%）。
 * 修法是 tree 的暗边改用 ink。这条测试锁住「暗边不能等于本格底色」这个不变量。
 */
test('暗边不能与本格底色相同（tree 用 ink，其余用 soil）', () => {
  // 直接读源码导出的 DARK 表，不在测试里另建副本——副本会和实现脱钩，
  // 导致把实现改回 bug 时测试仍然绿（同义反复）。
  for (const self of TERRAINS) {
    assert.notEqual(
      DARK[self],
      TERRAIN_COLORS[self],
      `${self} 的暗边色与底色相同，画出来会隐形`,
    );
  }
  // tree 具体取值：必须比底色 soil 更深（亮度更低）
  assert.equal(DARK.tree, WORLD.ink, 'tree 暗边必须用 ink：它的底色就是 soil，用 soil 落差 0.0');
  const lum = (hex: number): number =>
    0.2126 * ((hex >> 16) & 255) + 0.7152 * ((hex >> 8) & 255) + 0.0722 * (hex & 255);
  assert.ok(
    lum(WORLD.ink) < lum(WORLD.soil),
    'ink 必须比 soil 更深，否则 tree 暗边虽可见但不是「更暗」',
  );
});

test('亮边不能与本格底色相同', () => {
  for (const self of TERRAINS) {
    assert.notEqual(BRIGHT[self], TERRAIN_COLORS[self], `${self} 的亮边色与底色相同`);
  }
});

/**
 * plaza 是最低材质（高度 0），邻格不可能比它更低，所以它**永远不画边**。
 * 实测：30×20 世界里 16 个 plaza 格，画了边的 0 个。
 * 这条不是「不用管」，而是说明 BRIGHT.plaza / DARK.plaza 当前是取不到的死分支——
 * 但仍要求它们与自身底色不同，否则将来改动高度序时会静默画出一条隐形边。
 */
test('plaza 是最低材质，任何邻格都不触发它的边', () => {
  for (const other of TERRAINS) {
    assert.equal(sideMask('plaza', all(other)), 0, `plaza 邻 ${other} 不该画边`);
    assert.equal(cornerMask('plaza', all(other)), 0);
  }
  // 反向确认：其他材质邻 plaza 时会画边（plaza 更低）
  for (const self of ['tree', 'grass', 'farm', 'stone'] as const) {
    assert.notEqual(sideMask(self, all('plaza')), 0, `${self} 邻 plaza 应画边`);
  }
});

/* ------------------------------------------------------------------ 边可见性 */

/**
 * 1px 边的可辨性用 **ΔL\***（CIE L*，从 sRGB 换算）度量，不是 WCAG 比值。
 *
 * 为什么不用 WCAG：它的 +0.05 底数是给「中等亮度背景上的文字」校准的，而这套调色板
 * 本调色板里有 11 色在 L* 33 以下。同一个 ink vs grass，WCAG 只给 1.59，ΔL* 是 18.1——
 * 用 WCAG 会得出「所有边都一样弱」的错误结论（实测暗边其实全部合格）。
 *
 * 阈值 6 的依据：画面里已证明可读的 1px 线是角色墨线（ink vs grass ΔL* 18.1）。
 * 但那是强轮廓，地形边只需弱一档；实测 6 以下（如原 grass 亮边 3.0、tree 暗边 0.0）
 * 肉眼不可辨。取 6 作为下限，留出与强轮廓的区分空间。
 *
 * 这条测试锁的是「改色/换 token 时边可见性不会静默退化」——2026-10-20 的 stone 明度
 * 重排（#3a3f3b → #3e433f）就把 stone 亮边从 ΔL* 6.72 压到 4.95，当时没有任何测试会红。
 */
const EDGE_VISIBLE_MIN_DL = 6;

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

/**
 * 亮边：对**本格底色**必须可辨。
 *
 * 两类豁免，各有硬理由（不是为了让测试变绿）：
 *
 * 1. **plaza** —— 最低材质永不画边（上面那条测试已锁），BRIGHT.plaza 是防将来改
 *    高度序的占位值，不参与实际绘制。
 *
 * 2. **stone 的亮边** —— 结构性不可见，与改色无关。stone 高度 1，唯一的低邻格是
 *    plaza（高度 0），而 `BRIGHT.stone = plaza`。于是这条亮边画出来**必然与它相邻的
 *    plaza 格同色**，融进邻格。QA 实测印证：269 个 stone/plaza 交界采样点里，stone
 *    侧贴边 4px 内 luma 抬升 >2 的 0 个；直接量 (13,11) 北边缘，亮线行与上方 plaza
 *    完全同色。**ΔL* 4.95 是「线 vs stone 底色」，但这条线在渲染里从不单独对着 stone
 *    底色出现**——它永远夹在同色的 plaza 与 stone 之间。
 *
 *    所以 stone/plaza 的边界由**底色差 11.35**（改色后）与**暗边 15.85** 承担，不靠
 *    这条亮边。若将来要让 stone 有可见亮边，得换 `BRIGHT.stone` 为非 plaza 的 token
 *    （timber 是木色、trim 有白线风险），那是调色板决策，不是测试能推的。
 */
test('亮边色对本格底色 ΔL* ≥ 6（边可见性回归锁）', () => {
  const failing: string[] = [];
  for (const self of ['tree', 'grass', 'farm'] as const) {
    const value = dl(BRIGHT[self], TERRAIN_COLORS[self]);
    if (value < EDGE_VISIBLE_MIN_DL) {
      failing.push(`${self} 亮边 ΔL*=${value.toFixed(2)}`);
    }
  }
  assert.deepEqual(failing, [], `以下亮边不可辨（阈值 ${EDGE_VISIBLE_MIN_DL}）：${failing.join('、')}`);
});

/** 暗边：同上，plaza 豁免。 */
test('暗边色对本格底色 ΔL* ≥ 6（边可见性回归锁）', () => {
  const failing: string[] = [];
  for (const self of ['tree', 'grass', 'farm', 'stone'] as const) {
    const value = dl(DARK[self], TERRAIN_COLORS[self]);
    if (value < EDGE_VISIBLE_MIN_DL) {
      failing.push(`${self} 暗边 ΔL*=${value.toFixed(2)}`);
    }
  }
  assert.deepEqual(failing, [], `以下暗边不可辨（阈值 ${EDGE_VISIBLE_MIN_DL}）：${failing.join('、')}`);
});

/**
 * 锁住 stone 亮边豁免的**结构前提**。
 *
 * 上面那条测试把 stone 从亮边断言里豁免了，理由是「stone 唯一的低邻格是 plaza，
 * 而 BRIGHT.stone 也是 plaza，所以线必然融进邻格」。这是三个事实的合取，缺一不成立：
 *
 *   ① stone 的邻格里只有 plaza 比它低（否则会出现 stone 邻其他材质画边的情况）
 *   ② BRIGHT.stone 恰好等于 plaza 的底色（否则线不会融）
 *   ③ 低邻格的底色就是 plaza（同义反复，但如果将来 plaza 挪了色这条会提醒）
 *
 * 任何一条被改动（例如调高度序、给 BRIGHT.stone 换 token、改 plaza 的值），这条测试
 * 就会红——那时必须重新评估「stone 亮边是否还该豁免」，而不是让豁免默默失效。
 */
test('stone 亮边豁免的结构前提（改这三条任一都必须重估豁免）', () => {
  // ① stone 是高度 1，比它低的只有 plaza（高度 0）
  const lowerNeighbors = TERRAINS.filter((t) => TERRAIN_HEIGHT[t] < TERRAIN_HEIGHT.stone);
  assert.deepEqual(lowerNeighbors, ['plaza'], 'stone 的低邻格必须只有 plaza');

  // ② BRIGHT.stone 用的就是 plaza 的底色
  assert.equal(BRIGHT.stone, WORLD.plaza, 'BRIGHT.stone 必须等于 plaza 底色，豁免才成立');

  // ③ 低邻格的底色 = plaza（TERRAIN_COLORS.plaza）
  assert.equal(TERRAIN_COLORS.plaza, WORLD.plaza, 'plaza 的底色必须是 WORLD.plaza');
});

/**
 * 变异探针：这几条断言必须真能挡住退化。
 *
 * 用历史值验证——原 grass 亮边用 grassLit 是 ΔL* 3.02（不过），改 plaza 后 9.51（过）；
 * 原 tree 暗边用 soil 是 0.0（完全隐形），改 ink 后 6.76（过）。
 * 如果断言对这些历史退化值都给「过」，说明阈值或算式写错了。
 */
test('变异探针：历史退化值必须被判为不可辨', () => {
  // grass 底色下 grassLit 亮边 3.02 —— 不过（P0-2 修复前的状态）
  assert.ok(dl(WORLD.grassLit, WORLD.grass) < EDGE_VISIBLE_MIN_DL, 'grassLit 亮边应被判不可辨');
  // tree 底色下 soil 暗边 0.0 —— 完全隐形（P0-2 修复前的状态）
  assert.ok(dl(WORLD.soil, TERRAIN_COLORS.tree) < EDGE_VISIBLE_MIN_DL, 'soil 暗边在 tree 上应被判不可辨');
  // 反向：修复后的值必须过
  assert.ok(dl(WORLD.plaza, WORLD.grass) >= EDGE_VISIBLE_MIN_DL, 'grass 亮边改 plaza 后应通过');
  assert.ok(dl(WORLD.ink, TERRAIN_COLORS.tree) >= EDGE_VISIBLE_MIN_DL, 'tree 暗边改 ink 后应通过');
});
