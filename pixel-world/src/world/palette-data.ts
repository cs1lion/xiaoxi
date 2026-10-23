/** 世界 24 色调色板：纯数据，零渲染依赖。
 *
 * 为什么单独一个文件：世界逻辑（蓝图解析、读档）也要知道 token 名 → 颜色，
 * 但 `src/world/` 不能反向 import `src/render/`。渲染层直接从这里取，不再转一手。
 *
 * 2026-10-21 用户批准 24 色 ADR：在 16 色材料表上新增 8 个材料/光色（W17–W24），
 * 用于灰泥墙、陶瓦、铜绿屋顶、水洼、苔、冷影、暖高光与炭黑。颜色仍是 canvas 上
 * 唯一允许出现的世界源色；UI 仍 8 色（共享 amber），并集 31。加第 25 色必须再走 ADR。
 * 依据 docs/ADR_PALETTE_24.md 与 ART_PALETTE_AUDIT §5.3。
 *
 * 亮度分布：暗部 11（W01–W09、W13、W14、W22）、中间调 7（W08、W15、W17–W21、W24）、
 * 亮部 6（W10–W12、W16、W23）。画面能不能读，取决于亮部与 1px 轮廓，不取决于色数。
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
  /** 2026-10-20 明度重排（用户批准方案 A）：原 #3a3f3b luma 61.65 → 65.65。
   *  为什么：世界 16 色里 6 个挤在亮度带 48–63，其中 4 个是地形基色，覆盖屏幕 65% 面积；
   *  grass 55.08 与 stone 61.65 只差 6.57，是「石路和草地糊成一片」的直接原因。
   *  stone 占屏 20.22%，把它推过带边界 64 即让 1/5 像素整块搬出拥挤带。
   *  实测（真实渲染 A/B，同机同时刻双 server）：众数带 67.01% → 45.79%，IQR 9.39 → 13.39。
   *  色相 0 度变化（等比明度缩放），不破地形高度序，UI 共用色 amber 未动。 */
  stone: 0x3e433f,
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
  /** W17 灰泥墙 / 浅色纸面（书阁主体；比 trim 更中性） */
  plaster: 0xc2b39a,
  /** W18 陶瓦 / 砖（工坊屋顶；比 timber 更红、更暗一档） */
  terracotta: 0x9c4f3c,
  /** W19 铜绿屋顶（书阁塔楼；与草地/木色都拉开） */
  copper: 0x5f8a74,
  /** W20 水（水洼、雾、冷反光） */
  water: 0x3d6b7a,
  /** W21 苔（石缝、树冠、绿化带；比 grassLit 亮且更黄） */
  moss: 0x4e6241,
  /** W22 冷影（檐下、底部、雾的冷暗层；比 ink 更蓝） */
  deepShade: 0x18242b,
  /** W23 暖高光（窗芯、灯芯、祭坛符文；比 amber 更亮） */
  warmLight: 0xf2c879,
  /** W24 炭黑（炭化木、暗瓦、铁器；暖灰而非纯黑） */
  charcoal: 0x5a463e,
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

/**
 * 色值 → token 名。找不到时抛错而不是编一个。
 *
 * 用途：把已有的色值（比如模板自带的配色）反查回 token 名，
 * 好喂给造物主 schema——它只接受 token 名，不接受色值。
 */
export function tokenNameOf(color: number): string {
  for (const [name, value] of Object.entries(WORLD)) {
    if (value === color) return name;
  }
  throw new Error(`色值 ${color} 不在 24 色表里，无法反查 token 名`);
}
