/** 世界调色板。像素风的风格来自"少而固定"的颜色，不来自每栋楼随手取色。 */

export const UI = {
  sky: 0x111413,
  soil: 0x1b211c,
  grid: 0x1f2421,
  grass: 0x2d3b2e,
  grassAlt: 0x334234,
  stone: 0x3a3f3b,
  plaza: 0x4a4f42,
  farm: 0x3b3423,
  tree: 0x1a2a1d,
  street: 0x2f3634,
  ink: 0x14110d,
  amber: 0xe3a13c,
  ember: 0xf2c14e,
  smoke: 0xd8dad3,
  pages: 0xf0e7c8,
  inkDrop: 0x6b7c9c,
  shadow: 0x000000,
} as const;

export const TERRAIN_COLORS = {
  grass: UI.grass,
  stone: UI.stone,
  plaza: UI.plaza,
  farm: UI.farm,
  tree: UI.tree,
} as const;

/** 16×16 图块上的一层细碎噪点，避免大片纯色看起来像占位符。 */
export const TERRAIN_SPECK = {
  grass: UI.grassAlt,
  stone: 0x434845,
  plaza: 0x565b4c,
  farm: 0x4a412c,
  tree: 0x243826,
} as const;
