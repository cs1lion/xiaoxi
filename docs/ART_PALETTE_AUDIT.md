# 调色板审计（24 色 ADR 后）

- 日期：2026-10-21
- 状态：用户批准 24 色 ADR（`docs/ADR_PALETTE_24.md`）。世界 **24** 唯一色 / 引用 **283**；UI **8** / **51**；共享 **1**（`#e3a13c`）；并集 **31**；越界 **0 / 0**；`--strict` exit **0**。
- 口径：D6-A。只统计**源码源色值**，不模拟运行时 alpha/光照叠加；不按截图唯一色数计（屏幕合成色必然多于源色）。
- 复跑：`cd pixel-world && node scripts/audit-palette.mjs [--json|--md|--strict]`
- 历史：task-26/27 的 16 色收敛快照保留在提交 `5214eb5`；2026-10-21 24 色 ADR 后本文件重新生成。

## 脚本输出

- 根目录：`pixel-world`
- 口径：只统计源码源色值；不模拟运行时 alpha/光照叠加；世界层与 UI 层分开。
- 预算：世界 24 色 / UI 8 色。

## 计数

| 层 | 唯一色 | 引用站点 | 预算 | 超出 |
|---|---:|---:|---:|---:|
| 世界层 | 24 | 283 | 24 | 0 |
| UI 层 | 8 | 51 | 8 | 0 |

- 共享色 1：#e3a13c
- 两层并集：31 色
- 越界收敛预估：世界层 近似等价 0 / 微调 0 / 重画 0；UI 层 近似等价 0 / 微调 0 / 重画 0

## 世界层 Top（按使用次数）

| # | 色值 | 使用次数 | token | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #14110d | 39 | ink | src/main.ts:32 (WORLD.ink)<br>src/render/buildings.ts:76 (WORLD.ink)<br>src/render/buildings.ts:139 (WORLD.ink)<br>src/render/buildings.ts:208 (WORLD.ink) |
| 2 | #c9a86a | 33 | trim | src/render/buildings.ts:74 (WORLD.trim)<br>src/render/buildings.ts:237 (WORLD.trim)<br>src/render/buildings.ts:249 (WORLD.trim)<br>src/render/buildings.ts:287 (WORLD.trim) |
| 3 | #4a3f39 | 24 | timberDark | src/render/buildings.ts:178 (WORLD.timberDark)<br>src/render/buildings.ts:182 (WORLD.timberDark)<br>src/render/buildings.ts:247 (WORLD.timberDark)<br>src/render/buildings.ts:264 (WORLD.timberDark) |
| 4 | #1b211c | 23 | soil/TERRAIN_COLORS.tree/TERRAIN_SPECK.grass/TERRAIN_SPECK.farm | src/render/buildings.ts:72 (WORLD.soil)<br>src/render/buildings.ts:174 (WORLD.soil)<br>src/render/buildings.ts:317 (WORLD.soil)<br>src/render/buildings.ts:383 (WORLD.soil) |
| 5 | #d8dad3 | 21 | pale | src/render/buildings.ts:249 (WORLD.pale)<br>src/render/buildings.ts:262 (WORLD.pale)<br>src/render/buildings.ts:273 (WORLD.pale)<br>src/render/decor.ts:180 (WORLD.pale) |
| 6 | #e3a13c | 20 | amber | src/render/buildings.ts:149 (WORLD.amber)<br>src/render/buildings.ts:151 (WORLD.amber)<br>src/render/buildings.ts:214 (WORLD.amber)<br>src/render/buildings.ts:372 (WORLD.amber) |
| 7 | #4a4f42 | 18 | plaza/TERRAIN_COLORS.plaza/TERRAIN_SPECK.stone | src/render/buildings.ts:172 (WORLD.plaza)<br>src/render/buildings.ts:226 (WORLD.plaza)<br>src/render/buildings.ts:529 (WORLD.plaza)<br>src/render/decor.ts:44 (WORLD.plaza) |
| 8 | #f2c879 | 18 | warmLight | src/render/buildings.ts:80 (WORLD.warmLight)<br>src/render/buildings.ts:189 (WORLD.warmLight)<br>src/render/buildings.ts:216 (WORLD.warmLight)<br>src/render/buildings.ts:327 (WORLD.warmLight) |
| 9 | #3e433f | 12 | stone/TERRAIN_COLORS.stone/TERRAIN_SPECK.plaza | src/render/buildings.ts:170 (WORLD.stone)<br>src/render/buildings.ts:531 (WORLD.stone)<br>src/render/decor.ts:67 (WORLD.stone)<br>src/render/terrain-edges.ts:77 (WORLD.stone) |
| 10 | #18242b | 11 | deepShade | src/render/buildings.ts:141 (WORLD.deepShade)<br>src/render/buildings.ts:143 (WORLD.deepShade)<br>src/render/buildings.ts:348 (WORLD.deepShade)<br>src/render/buildings.ts:386 (WORLD.deepShade) |
| 11 | #4e6241 | 10 | moss | src/render/buildings.ts:320 (WORLD.moss)<br>src/render/buildings.ts:355 (WORLD.moss)<br>src/render/buildings.ts:389 (WORLD.moss)<br>src/render/decor.ts:229 (WORLD.moss) |
| 12 | #5a463e | 10 | charcoal | src/render/buildings.ts:70 (WORLD.charcoal)<br>src/render/buildings.ts:210 (WORLD.charcoal)<br>src/render/buildings.ts:224 (WORLD.charcoal)<br>src/render/buildings.ts:233 (WORLD.charcoal) |
| 13 | #c06a3e | 10 | ember | src/render/buildings.ts:78 (WORLD.ember)<br>src/render/buildings.ts:212 (WORLD.ember)<br>src/render/buildings.ts:451 (WORLD.ember)<br>src/render/buildings.ts:623 (WORLD.ember) |
| 14 | #2d3b2e | 6 | grass/TERRAIN_COLORS.grass/TERRAIN_SPECK.tree | src/render/decor.ts:71 (WORLD.grass)<br>src/render/decor.ts:227 (WORLD.grass)<br>src/render/tilemap.ts:34 (TERRAIN_COLORS.grass)<br>src/render/tilemap.ts:102 (WORLD.grass) |
| 15 | #8a5a44 | 6 | timber | src/render/buildings.ts:284 (WORLD.timber)<br>src/render/decor.ts:214 (WORLD.timber)<br>src/render/villager.ts:42 (WORLD.timber)<br>src/world/prefabs.json:14 (code_forge.body=timber) |
| 16 | #223a4d | 4 | cloakDark | src/render/smoke.ts:24 (WORLD.cloakDark)<br>src/render/villager.ts:32 (WORLD.cloakDark)<br>src/render/weather.ts:81 (WORLD.cloakDark)<br>src/render/weather.ts:108 (WORLD.cloakDark) |
| 17 | #9c4f3c | 4 | terracotta | src/render/buildings.ts:278 (WORLD.terracotta)<br>src/render/tilemap.ts:455 (WORLD.terracotta)<br>src/render/villager.ts:46 (WORLD.terracotta)<br>src/world/prefabs.json:14 (code_forge.roof=terracotta) |
| 18 | #e0b48c | 3 | skin | src/render/villager.ts:156 (WORLD.skin)<br>src/render/villager.ts:214 (WORLD.skin)<br>src/render/villager.ts:277 (WORLD.skin) |
| 19 | #30506b | 2 | cloak | src/render/villager.ts:31 (WORLD.cloak)<br>src/world/prefabs.json:56 (workshop_plot.roof=cloak) |
| 20 | #334234 | 2 | grassLit | src/render/terrain-edges.ts:45 (WORLD.grassLit)<br>src/render/tilemap.ts:322 (WORLD.grassLit) |
| 21 | #3d6b7a | 2 | water | src/render/weather.ts:73 (WORLD.water)<br>src/render/weather.ts:149 (WORLD.water) |
| 22 | #5f8a74 | 2 | copper | src/render/buildings.ts:235 (WORLD.copper)<br>src/world/prefabs.json:28 (star_archive.roof=copper) |
| 23 | #c2b39a | 2 | plaster | src/render/buildings.ts:249 (WORLD.plaster)<br>src/world/prefabs.json:28 (star_archive.body=plaster) |
| 24 | #3b3423 | 1 | farm/TERRAIN_COLORS.farm | src/render/tilemap.ts:34 (TERRAIN_COLORS.farm) |

## UI 层 Top（按使用次数）

| # | 色值 | 使用次数 | CSS 变量 | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #2b322c | 10 | --line | index.html:47 (--line)<br>index.html:84 (--line)<br>index.html:99 (--line)<br>index.html:120 (--line) |
| 2 | #8e968c | 8 | --dim | index.html:42 (--dim)<br>index.html:155 (--dim)<br>index.html:161 (--dim)<br>index.html:199 |
| 3 | #e8e2d2 | 8 | --ink | index.html:37 (--ink)<br>index.html:69 (--ink)<br>index.html:80 (--ink)<br>index.html:111 (--ink) |
| 4 | #0c0f0d | 6 | --base | index.html:36 (--base)<br>index.html:54 (--base)<br>index.html:55 (--base)<br>index.html:70 (--base) |
| 5 | #171b18 | 6 | --panel | index.html:119 (--panel)<br>index.html:127 (--panel)<br>index.html:135 (--panel)<br>index.html:143 (--panel) |
| 6 | #e3a13c | 6 | --amber | index.html:132 (--amber)<br>index.html:132 (--amber)<br>index.html:142 (--amber)<br>index.html:142 (--amber) |
| 7 | #24211b | 5 | --paper-ink | index.html:79 (--paper-ink)<br>index.html:170 (--paper-ink)<br>index.html:178<br>index.html:186 |
| 8 | #c8876a | 2 | --warn | index.html:158 (--warn)<br>index.html:164 (--warn) |

## 越界色：世界层（不在提案 24 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|

## 越界色：UI 层（不在提案 8 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|

## 提案 24 色世界

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #14110d | 最暗/轮廓/阴影/眼睛（合并 ink、shadow、sky） | 39 | 0.6% |  |
| #1b211c | 深土/树冠暗部/街巷暗（当前 soil 定义未引用，保留槽） | 23 | 1.4% |  |
| #2d3b2e | 草地基色 | 6 | 3.9% |  |
| #334234 | 草地亮部/树冠亮部 | 2 | 4.8% |  |
| #3e433f | 石/灰泥基色 | 12 | 5.4% |  |
| #4a4f42 | 广场/道路/石材亮部 | 18 | 7.4% |  |
| #3b3423 | 农田土 | 1 | 3.5% |  |
| #8a5a44 | 木/砖墙基色（建筑主体） | 6 | 13.1% |  |
| #4a3f39 | 木/屋顶暗部（屋顶、树干、伞杆） | 24 | 5.3% |  |
| #c9a86a | 暖色饰边/脚手架/旗帜/浅木 | 33 | 41.5% |  |
| #e3a13c | 焦点琥珀（窗光、进度、路灯、祭坛符号） | 20 | 42.1% | 与 UI 共享 |
| #e0b48c | 皮肤 | 3 | 50.4% |  |
| #30506b | 斗篷基色 | 2 | 7.4% |  |
| #223a4d | 斗篷暗部 | 4 | 3.9% |  |
| #c06a3e | 围巾/强调橙（同时给故障与暖色点缀） | 10 | 21.9% |  |
| #d8dad3 | 烟/纸片/雨/高光浅色 | 21 | 69.4% |  |
| #c2b39a | 灰泥墙/浅色纸面（书阁主体） | 2 | 46.0% |  |
| #9c4f3c | 陶瓦/砖（工坊屋顶、干草垛） | 4 | 13.0% |  |
| #5f8a74 | 铜绿屋顶（书阁塔楼） | 2 | 21.9% |  |
| #3d6b7a | 水（水洼、雾、冷反光） | 2 | 12.9% |  |
| #4e6241 | 苔（石缝、树冠、绿化带） | 10 | 10.7% |  |
| #18242b | 冷影（檐下、底部、雾的冷暗层） | 11 | 1.6% |  |
| #f2c879 | 暖高光（窗芯、灯芯、祭坛符文） | 18 | 61.6% |  |
| #5a463e | 炭黑（暗瓦、烟囱、铁器） | 10 | 6.9% |  |

## 提案 8 色 UI

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #e8e2d2 | 纸面/深底亮文字 | 8 | 76.2% |  |
| #24211b | 纸上墨字 | 5 | 1.5% |  |
| #8e968c | 次要文字（深底） | 8 | 29.5% |  |
| #171b18 | 面板底 | 6 | 1.0% |  |
| #2b322c | 分隔线/边框 | 10 | 3.0% |  |
| #e3a13c | 强调/焦点（与琥珀同值） | 6 | 42.1% | 与世界共享 |
| #0c0f0d | 页面底/投影 | 6 | 0.4% |  |
| #c8876a | 警告/错误 | 2 | 30.6% |  |

## 每文件字面量统计（手算核对口径）

| 文件 | 层 | 口径 | 出现次数 | 唯一色 |
|---|---|---|---:|---:|
| src/main.ts | world | canvas 字面量 | 0 | 0 |
| src/render/building-depth.ts | world | canvas 字面量 | 0 | 0 |
| src/render/buildings.ts | world | canvas 字面量 | 0 | 0 |
| src/render/decor.ts | world | canvas 字面量 | 0 | 0 |
| src/render/labels.ts | world | canvas 字面量 | 0 | 0 |
| src/render/prop-depth.ts | world | canvas 字面量 | 0 | 0 |
| src/render/smoke.ts | world | canvas 字面量 | 0 | 0 |
| src/render/terrain-edges.ts | world | canvas 字面量 | 0 | 0 |
| src/render/tilemap.ts | world | canvas 字面量 | 0 | 0 |
| src/render/villager-frames.ts | world | canvas 字面量 | 0 | 0 |
| src/render/villager.ts | world | canvas 字面量 | 0 | 0 |
| src/render/weather.ts | world | canvas 字面量 | 0 | 0 |
| src/render/world-overlay.ts | world | canvas 字面量 | 0 | 0 |
| src/scenes/SandboxScene.ts | world | canvas 字面量 | 0 | 0 |
| src/world/prefabs.json | world | prefab palette 槽位 | 16 | 9 |
| index.html (style) | ui | CSS 字面量/变量（含 var 定义） | 12 | 8 |

## 提案关键对比度（WCAG 比值）

| 对比对 | 比值 |
|---|---:|
| outline/grass | 1.59:1 |
| cloak/grass | 1.40:1 |
| cloakShade/grass | 1.00:1 |
| skin/grass | 6.23:1 |
| scarf/grass | 3.02:1 |
| amber/grass | 5.31:1 |
| smoke/night | 13.34:1 |
| paper/ink | 12.41:1 |
| amber/panel | 7.82:1 |
| dim/panel | 5.71:1 |
| paper/panel | 13.46:1 |

## 复跑

```bash
cd pixel-world
node scripts/audit-palette.mjs          # 人读
node scripts/audit-palette.mjs --json   # 机器可读
node scripts/audit-palette.mjs --md     # 本 Markdown
node scripts/audit-palette.mjs --strict # 超预算时退出码 1（已启用；默认不阻断）
```
