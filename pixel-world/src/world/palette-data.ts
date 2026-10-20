/** 世界 16 色调色板：纯数据，零渲染依赖。
 *
 * 为什么单独一个文件：世界逻辑（蓝图解析、读档）也要知道 token 名 → 颜色，
 * 但 `src/world/` 不能反向 import `src/render/`。渲染层直接从这里取，不再转一手。
 *
 * 依据 docs/ART_PALETTE_AUDIT.md §4.1（D6-A）。这 16 个值是 canvas 上唯一允许出现的
 * 世界源色；UI 8 色在 index.html 的 CSS 变量里。加色之前先改审计报告，`--strict` 会挡住。
 *
 * 亮度分布：暗部 10（W01–W09、W13、W14）、中间调 2（W08/W15）、亮部 4（W10–W12、W16）。
 * 画面能不能读，取决于亮部这 4 个色和 1px 轮廓，不取决于色数。
 */

export const WORLD = {
  /** W01 最暗 / 轮廓 / 阴影 / 眼睛（合并旧 ink、shadow、sky） */
  ink: 0x14110d,
  /** W02 深土 / 树冠暗部 / 街巷暗（旧 soil，兼作树的底色） */
  soil: 0x1b211c,
  /** W03 草地基色 */
  grass: 0x2d3b2e,
  /** W04 草地亮部 / 树冠亮部 */
  grassLit: 0x334234,
  /** W05 石 / 灰泥基色 */
  stone: 0x3a3f3b,
  /** W06 广场 / 道路 / 石材亮部 */
  plaza: 0x4a4f42,
  /** W07 农田土 */
  farm: 0x3b3423,
  /** W08 木 / 砖墙基色（建筑主体） */
  timber: 0x8a5a44,
  /** W09 木 / 屋顶暗部（屋顶、树干、靴子、发色） */
  timberDark: 0x4a3f39,
  /** W10 暖色饰边 / 脚手架 / 旗帜 / 浅木 */
  trim: 0xc9a86a,
  /** W11 焦点琥珀：窗光、进度、路灯、祭坛符号 */
  amber: 0xe3a13c,
  /** W12 皮肤 */
  skin: 0xe0b48c,
  /** W13 斗篷基色（兼作 workshop 屋顶的冷色识别） */
  cloak: 0x30506b,
  /** W14 斗篷暗部，兼作雨天压暗的冷暗 tint */
  cloakDark: 0x223a4d,
  /** W15 围巾 / 故障强调橙 */
  ember: 0xc06a3e,
  /** W16 烟 / 纸片 / 雨 / 高光浅色 */
  pale: 0xd8dad3,
} as const;

export type WorldColor = keyof typeof WORLD;

export const TERRAIN_COLORS = {
  grass: WORLD.grass,
  stone: WORLD.stone,
  plaza: WORLD.plaza,
  farm: WORLD.farm,
  /** 树冠底色用深土色，比草地暗一档才压得住树影。 */
  tree: WORLD.soil,
} as const;

/** 每种地形的抖动色：与基色是调色板里相邻的一档，2×2 有序网点把两者之间调出来。
 *
 * 不再给每种地形单开一个“近色 speck”——那些色与基色 ΔE 只有 4–7，
 * 用基色 + 有序抖动就能表达，还省掉 5 个源色（ART_PALETTE_AUDIT §5.1）。
 */
export const TERRAIN_SPECK = {
  // grass 用 soil 而不是 grassLit：grassLit 对 grass 的 ΔL* 只有 3.0（不可见），
  // 而 grass 占画布 37.15% —— 这是「一片同亮度」的第一来源。soil 对 grass 是 11.3，
  // 且两者同色相族（hue 148.5 vs 145.1），读成「草的暗部」而不是脏点。
  grass: WORLD.soil,
  stone: WORLD.plaza,
  plaza: WORLD.stone,
  farm: WORLD.soil,
  tree: WORLD.grass,
} as const;
