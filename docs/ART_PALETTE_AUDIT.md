# 调色板审计（task-28 收敛后的当前状态）

- 日期：2026-10-19
- 状态：task-28 调色板收敛完成。世界 **16** 唯一色 / 引用 90 处；UI **8** / 45；共享 **1**（`#e3a13c`）；并集 **23**；越界 **0 / 0**；`--strict` exit **0**（默认也 0）。
- 口径：D6-A。只统计**源码源色值**，不模拟运行时 alpha/光照叠加；不按截图唯一色数计（屏幕合成色必然多于 16）。
- 复跑：`cd pixel-world && node scripts/audit-palette.mjs [--json|--md|--strict]`
- task-26/27 的时点快照（世界 71 / UI 17 / 共享 2 / 并集 86，越界 55/9）保留在提交 `5214eb5` 的本文件历史版本；本文只反映收敛后的当前事实。

> 下表由 `node scripts/audit-palette.mjs --md` 于 2026-10-19 task-28 收敛后生成，**逐字保留脚本输出**（含「提案」「越界」等脚本既有措辞；当前越界表为空）。

## 计数

| 层 | 唯一色 | 引用站点 | 预算 | 超出 |
|---|---:|---:|---:|---:|
| 世界层 | 16 | 90 | 16 | 0 |
| UI 层 | 8 | 45 | 8 | 0 |

- 共享色 1：#e3a13c
- 两层并集：23 色
- 越界收敛预估：世界层 近似等价 0 / 微调 0 / 重画 0；UI 层 近似等价 0 / 微调 0 / 重画 0

## 世界层 Top（按使用次数）

| # | 色值 | 使用次数 | token | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #e3a13c | 15 | amber | src/render/buildings.ts:102 (WORLD.amber)<br>src/render/buildings.ts:193 (WORLD.amber)<br>src/render/buildings.ts:196 (WORLD.amber)<br>src/render/buildings.ts:199 (WORLD.amber) |
| 2 | #14110d | 13 | ink | src/main.ts:32 (WORLD.ink)<br>src/render/buildings.ts:37 (WORLD.ink)<br>src/render/buildings.ts:55 (WORLD.ink)<br>src/render/buildings.ts:69 (WORLD.ink) |
| 3 | #c9a86a | 8 | trim | src/render/decor.ts:97 (WORLD.trim)<br>src/scenes/SandboxScene.ts:191 (WORLD.trim)<br>src/scenes/SandboxScene.ts:476 (WORLD.trim)<br>src/scenes/SandboxScene.ts:481 (WORLD.trim) |
| 4 | #1b211c | 6 | soil/TERRAIN_COLORS.tree/TERRAIN_SPECK.farm | src/render/buildings.ts:61 (WORLD.soil)<br>src/render/tilemap.ts:29 (TERRAIN_COLORS.tree)<br>src/render/tilemap.ts:51 (TERRAIN_SPECK.farm)<br>src/render/tilemap.ts:52 (TERRAIN_COLORS.tree) |
| 5 | #4a3f39 | 6 | timberDark | src/render/decor.ts:86 (WORLD.timberDark)<br>src/render/villager.ts:68 (WORLD.timberDark)<br>src/render/villager.ts:87 (WORLD.timberDark)<br>src/render/villager.ts:114 (WORLD.timberDark) |
| 6 | #4a4f42 | 6 | plaza/TERRAIN_COLORS.plaza/TERRAIN_SPECK.stone | src/render/decor.ts:38 (WORLD.plaza)<br>src/render/tilemap.ts:29 (TERRAIN_COLORS.plaza)<br>src/render/tilemap.ts:51 (TERRAIN_SPECK.stone)<br>src/render/tilemap.ts:52 (TERRAIN_COLORS.plaza) |
| 7 | #d8dad3 | 6 | pale | src/render/smoke.ts:22 (WORLD.pale)<br>src/render/smoke.ts:23 (WORLD.pale)<br>src/render/weather.ts:78 (WORLD.pale)<br>src/render/weather.ts:90 (WORLD.pale) |
| 8 | #2d3b2e | 5 | grass/TERRAIN_COLORS.grass/TERRAIN_SPECK.tree | src/render/decor.ts:88 (WORLD.grass)<br>src/render/tilemap.ts:29 (TERRAIN_COLORS.grass)<br>src/render/tilemap.ts:51 (TERRAIN_SPECK.tree)<br>src/render/tilemap.ts:52 (TERRAIN_COLORS.grass) |
| 9 | #8a5a44 | 5 | timber | src/render/villager.ts:64 (WORLD.timber)<br>src/world/prefabs.json:14 (code_forge.body=timber)<br>src/world/prefabs.json:28 (star_archive.body=timber)<br>src/world/prefabs.json:42 (ink_house.body=timber) |
| 10 | #223a4d | 4 | cloakDark | src/render/smoke.ts:24 (WORLD.cloakDark)<br>src/render/villager.ts:95 (WORLD.cloakDark)<br>src/render/weather.ts:45 (WORLD.cloakDark)<br>src/render/weather.ts:69 (WORLD.cloakDark) |
| 11 | #3a3f3b | 4 | stone/TERRAIN_COLORS.stone/TERRAIN_SPECK.plaza | src/render/decor.ts:36 (WORLD.stone)<br>src/render/tilemap.ts:29 (TERRAIN_COLORS.stone)<br>src/render/tilemap.ts:51 (TERRAIN_SPECK.plaza)<br>src/render/tilemap.ts:52 (TERRAIN_COLORS.stone) |
| 12 | #c06a3e | 4 | ember | src/render/buildings.ts:203 (WORLD.ember)<br>src/render/decor.ts:95 (WORLD.ember)<br>src/render/villager.ts:66 (WORLD.ember)<br>src/render/villager.ts:99 (WORLD.ember) |
| 13 | #334234 | 3 | grassLit/TERRAIN_SPECK.grass | src/render/decor.ts:90 (WORLD.grassLit)<br>src/render/tilemap.ts:51 (TERRAIN_SPECK.grass)<br>src/render/tilemap.ts:84 (WORLD.grassLit) |
| 14 | #30506b | 2 | cloak | src/render/villager.ts:93 (WORLD.cloak)<br>src/world/prefabs.json:56 (workshop_plot.roof=cloak) |
| 15 | #3b3423 | 2 | farm/TERRAIN_COLORS.farm | src/render/tilemap.ts:29 (TERRAIN_COLORS.farm)<br>src/render/tilemap.ts:52 (TERRAIN_COLORS.farm) |
| 16 | #e0b48c | 1 | skin | src/render/villager.ts:84 (WORLD.skin) |

## UI 层 Top（按使用次数）

| # | 色值 | 使用次数 | CSS 变量 | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #2b322c | 8 | --line | index.html:39 (--line)<br>index.html:75 (--line)<br>index.html:83 (--line)<br>index.html:91 (--line) |
| 2 | #8e968c | 7 | --dim | index.html:34 (--dim)<br>index.html:110 (--dim)<br>index.html:147<br>index.html:180 (--dim) |
| 3 | #e8e2d2 | 7 | --ink | index.html:29 (--ink)<br>index.html:54 (--ink)<br>index.html:65 (--ink)<br>index.html:84 (--ink) |
| 4 | #171b18 | 6 | --panel | index.html:74 (--panel)<br>index.html:82 (--panel)<br>index.html:90 (--panel)<br>index.html:98 (--panel) |
| 5 | #e3a13c | 6 | --amber | index.html:87 (--amber)<br>index.html:87 (--amber)<br>index.html:97 (--amber)<br>index.html:97 (--amber) |
| 6 | #0c0f0d | 5 | --base | index.html:28 (--base)<br>index.html:40<br>index.html:40 (--base)<br>index.html:55 (--base) |
| 7 | #24211b | 5 | --paper-ink | index.html:64 (--paper-ink)<br>index.html:119 (--paper-ink)<br>index.html:127<br>index.html:134 |
| 8 | #c8876a | 1 | --warn | index.html:113 (--warn) |

## 越界色：世界层（不在提案 16 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|

## 越界色：UI 层（不在提案 8 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|

## 提案 16 色世界

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #14110d | 最暗/轮廓/阴影/眼睛（合并 ink、shadow、sky） | 13 | 0.6% |  |
| #1b211c | 深土/树冠暗部/街巷暗（当前 soil 定义未引用，保留槽） | 6 | 1.4% |  |
| #2d3b2e | 草地基色 | 5 | 3.9% |  |
| #334234 | 草地亮部/树冠亮部 | 3 | 4.8% |  |
| #3a3f3b | 石/灰泥基色 | 4 | 4.8% |  |
| #4a4f42 | 广场/道路/石材亮部 | 6 | 7.4% |  |
| #3b3423 | 农田土 | 2 | 3.5% |  |
| #8a5a44 | 木/砖墙基色（建筑主体） | 5 | 13.1% |  |
| #4a3f39 | 木/屋顶暗部（屋顶、树干、伞杆） | 6 | 5.3% |  |
| #c9a86a | 暖色饰边/脚手架/旗帜/浅木 | 8 | 41.5% |  |
| #e3a13c | 焦点琥珀（窗光、进度、路灯、祭坛符号） | 15 | 42.1% | 与 UI 共享 |
| #e0b48c | 皮肤 | 1 | 50.4% |  |
| #30506b | 斗篷基色 | 2 | 7.4% |  |
| #223a4d | 斗篷暗部 | 4 | 3.9% |  |
| #c06a3e | 围巾/强调橙（同时给故障与暖色点缀） | 4 | 21.9% |  |
| #d8dad3 | 烟/纸片/雨/高光浅色 | 6 | 69.4% |  |

## 提案 8 色 UI

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #e8e2d2 | 纸面/深底亮文字 | 7 | 76.2% |  |
| #24211b | 纸上墨字 | 5 | 1.5% |  |
| #8e968c | 次要文字（深底） | 7 | 29.5% |  |
| #171b18 | 面板底 | 6 | 1.0% |  |
| #2b322c | 分隔线/边框 | 8 | 3.0% |  |
| #e3a13c | 强调/焦点（与琥珀同值） | 6 | 42.1% | 与世界共享 |
| #0c0f0d | 页面底/投影 | 5 | 0.4% |  |
| #c8876a | 警告/错误 | 1 | 30.6% |  |

## 每文件字面量统计（手算核对口径）

| 文件 | 层 | 口径 | 出现次数 | 唯一色 |
|---|---|---|---:|---:|
| src/main.ts | world | canvas 字面量 | 0 | 0 |
| src/render/buildings.ts | world | canvas 字面量 | 0 | 0 |
| src/render/decor.ts | world | canvas 字面量 | 0 | 0 |
| src/render/labels.ts | world | canvas 字面量 | 0 | 0 |
| src/render/smoke.ts | world | canvas 字面量 | 0 | 0 |
| src/render/tilemap.ts | world | canvas 字面量 | 0 | 0 |
| src/render/villager.ts | world | canvas 字面量 | 0 | 0 |
| src/render/weather.ts | world | canvas 字面量 | 0 | 0 |
| src/render/world-overlay.ts | world | canvas 字面量 | 0 | 0 |
| src/scenes/SandboxScene.ts | world | canvas 字面量 | 0 | 0 |
| src/world/prefabs.json | world | prefab palette 槽位 | 16 | 7 |
| index.html (style) | ui | CSS 字面量/变量（含 var 定义） | 14 | 8 |

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

---

## 收敛记录（task-28，2026-10-19）

- 55 个世界越界色 + 9 个 UI 越界色已收敛到 16 + 8；逐色映射、三处人工修正与 workshop 屋顶裁决见 `docs/ART_SLICE_REVIEW_CHECKLIST.md`。
- `src/render/palette.ts` 已删除，世界 16 色数据移到 `src/world/palette-data.ts`（纯数据、零渲染依赖，`blueprints.ts` 不再反向 import render）；prefabs 16 槽全部改用语义 token（`timber` 等），解析器兼容旧整数与 `"#rrggbb"` 并告警。
- workshop 屋顶 `#450f43` → `cloak`（W13 `#30506b`），不加第 17 色（Chief 裁决）。
- 对抗：世界/UI 各注入一色或改回旧 hex → `--strict` exit 1；还原后 SHA256 与改前一致（QA 变异实测）。
- gap A：读档按 `prefabId` 从当前 prefab 表重解 palette（`setup.ts` `rehydratePalettes`），旧存档不会停留在旧色；存档 v1 schema、样例与指纹未动。

## 审计边界

- 没有做屏幕截图逐像素取色；本文色数是**源码源色数**，不是屏幕色数（运行时 alpha/光照叠加会让屏幕出现更多色）。
- 未评估色盲可读性（Protanopia/Deuteranopia）；未评估 400% 放大下的色阶 banding——色带用抖动解决，不加色。
- 未修改/重画任何素材；重画清单是历史建议，不是已实施。
- 门禁：`--strict` 目前只在本地/人工调用；接入本地 gate 与 CI 的报告口径另开卡（不在 task-28 范围）。
