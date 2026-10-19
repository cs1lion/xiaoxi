# 像素世界调色板审计（ART_PALETTE_AUDIT）

> 作者：Pixel Art Director（teammate `art-director`）
> 日期：2026-10-19
> 任务：`task-26`（S3 第 1 步；只报告，不阻断）；**task-27 已按本文 §8.4 实施并复跑**
> 对象：`pixel-world`（Phaser 3.90 + Vite 8.3.4 + TS 5.9.3，当前含 S2-2 存档、S2-3 调试面板、**task-27 D5-A 中文迁 DOM**）
> 脚本：`pixel-world/scripts/audit-palette.mjs`（本次新增；可重复运行，默认退出码 0）
> **task-27 复跑（2026-10-19）**：5 个画布文字色与气泡底引用已删除，未引用的 `UI.paper` token 已删；**世界层 77→71（引用 85）、UI 层 17（引用 45）、共享 2（`#000000`/`#e3a13c`）、并集 86、世界越界 55**。下文数字均以本次复跑为准。
> 边界：task-26 只读源码 + 新增审计脚本与本文档；task-27 只改渲染层/DOM overlay 与审计脚本文案，未改调色板其他色值、未改仿真语义。

---

## 0. 摘要

1. **当前世界层 71 个唯一源色（85 个引用站点），UI 层 17 个（45 个引用站点）**（task-27 删 5 个画布文字色 + 气泡底引用 + 未用的 `UI.paper` token 后；删前为 77/86 与 17/41）；两层共享 2 色（`#000000`、`#e3a13c`），并集 **86 色**。预算是世界 16 + UI 8（按层计）；**世界超 55、UI 超 9**。提案收敛后仅 `#e3a13c` 共享，并集 23 色。
2. 55 个越界世界里，按 RGB/Lab 最近提案色粗分：**近似等价 4 个、需微调 27 个、需重画或改材质 24 个**；UI 越界 9 个（近似 1 / 微调 3 / 重画 5）。这说明 16 色不是“再删几个噪点”能到的，必须做一次**材质级重画 + 引用收敛**。
3. **Top 5 世界色**：`#000000`(4)、`#e3a13c`(4)、`#0a1420`(2)、`#111413`(2)、`#14110d`(2)。task-27 删掉中文文本色后，结构性暗色与琥珀成为主要复用色；亮部仍稀缺（16 色提案里 10 色亮度 <8%）。
4. **Top 5 UI 色**：`#2b322c`(8)、`#e8e2d2`(7)、`#8e968c`(6)、`#e3a13c`(5)、`#000000`(4)/`#171b18`(3)。task-27 把角色名/气泡迁到 DOM 后，`#e8e2d2` 只剩 UI 层（--ink/气泡底/纸面），世界与 UI 的共享色降为 2 个。
5. **提案 16+8 全部取自现有源色，不引入新色相**：16 色世界表见 §4.1，8 色 UI 表见 §4.2。这样可以把“改色数”和“改色相”解耦，先验证色数是否成立。
6. **初判（不是结论）**：当前 1.0 范围（晴/雨）**16 色足以做出可读的雨天**，但前提是先做值域重排、删掉随机 1px 噪点、并把角色亮部做成 ≥2px 的可读边；**“有层次的夜景”在 16 色下不可行**（地形分类亮度差只有 1.04–1.10:1，夜景 tint 会把它们并成一团），建议夜景不进 1.0，或单独批 18 色。完整依据见 §6。
7. 脚本已通过：`node --check`、从不同 cwd 运行结果一致、连续两次运行字节一致、手算抽查 2 个文件一致（§8.2）。默认不阻断构建；`--strict` 才在超预算时返回 1（**已启用**，提示文案已随 task-27 修正）。

**一句话**：task-27 已把 5 个画布中文色与气泡底清出世界层（77→71），接下来先把结构性暗色与 speck 合并、再重画蓝图材质，是成本最低的顺序。

---

## 1. 口径与方法

### 1.1 用户口径（原样执行）

- 只统计**源码里的源色值**；**排除运行时色调/透明度叠加**。不模拟“全局压暗后的颜色”“alpha 混合后的颜色”。
- **世界层与 UI 层分开统计**。
- 只报告，不改源码，不阻断构建。

### 1.2 扫描范围（含与任务清单的差异）

| 层 | 文件 | 说明 |
|---|---|---|
| 世界 | `src/main.ts` | 只有 `UI.sky` 背景色；任务清单写 `render/*.ts`，漏掉它会漏 `sky` 的引用 |
| 世界 | `src/render/*.ts`（8 个） | 主要 canvas 绘制；含 `palette.ts` 的 token 定义 |
| 世界 | `src/scenes/*.ts` | 祭坛/脚手架/施工标签的 7 个 `0x` + 2 个文本色；同属 canvas 源色 |
| 世界 | `src/world/prefabs.json` | 4 套蓝图 × 4 个 palette 槽位（十进制裸值） |
| UI | `index.html` 的 `<style>` | 5 个 CSS 变量 + 12 个字面量（含 `rgba`/`#000` 简写） |
| UI | `src/ui/*.ts` | 当前无颜色字面量；保留扫描口，防以后写进 TS |

> 额外纳入两类字面量：① Phaser 文本颜色以 `'#rrggbb'` 字符串出现（`villager.ts:35,42,43`、`buildings.ts:78`、`decor.ts:104`、`SandboxScene.ts:197,480`），只扫 `0x` 会漏掉 5 个画布文字色；② `rgba(...)` 只取 RGB 三分量归一为 `#rrggbb`，alpha 不另算颜色。这两点都写在脚本头注释里，和任务清单的差异是有意为之，便于 QA 复核。

### 1.3 计数定义

- **唯一色**：按 `#rrggbb` 去重；世界层包含 `palette.ts` 的 token 定义（即使当前未引用），因为它们是源码调色板的一部分。
- **使用次数（refs）**：应用/引用站点数 —— canvas 字面量、`UI.xxx` token 引用、`var(--x)` 引用、`prefab.palette` 槽位。
- **定义（defs）**：`palette.ts` 的 token 定义、CSS 变量的 `--x: ...` 定义。定义不计为使用，单独统计。
- **不统计**：运行时 `fillStyle(color, alpha)` 的 alpha 结果、`setAlpha`、天气叠加后的视觉色、浏览器合成色。所以“唯一色数”是**源码色数**，不是屏幕上出现的色数。

### 1.4 复跑

```bash
cd pixel-world
node scripts/audit-palette.mjs            # 人读报告
node scripts/audit-palette.mjs --json     # 机器可读
node scripts/audit-palette.mjs --md       # Markdown 表格
node scripts/audit-palette.mjs --strict   # 超预算时退出码 1（本轮未接入 CI）
```

默认 **不阻断**：即使超预算也返回 0。`--strict` 只是给下一阶段接 CI 留的口子。

---

## 2. 当前统计

### 2.1 总数

| 层 | 唯一色 | 引用站点 | 预算 | 超出 |
|---|---:|---:|---:|---:|
| 世界层 | **71** | 85 | 16 | **55** |
| UI 层 | **17** | 45 | 8 | **9** |
| 共享 | 2（`#000000`、`#e3a13c`） | — | — | — |
| 并集 | **86** | — | 23（16 + 8 − 1 共享） | **63** |

> 共享色的处理：世界层与 UI 层各算一次，并集只算一次。因此 71 + 17 − 2 = 86。
> task-27 前为 77 / 17 / 共享 3 / 并集 91；删掉 5 个画布文字色与气泡底引用、再删未引用的 `UI.paper` token 后得到本表。

### 2.2 每文件字面量统计（手算核对口径）

| 文件 | 层 | 口径 | 出现次数 | 唯一色 |
|---|---|---|---:|---:|
| `src/main.ts` | 世界 | canvas 字面量 | 0 | 0 |
| `src/render/buildings.ts` | 世界 | canvas 字面量 | 4 | 4 |
| `src/render/decor.ts` | 世界 | canvas 字面量 | 11 | 9 |
| `src/render/palette.ts` | 世界 | palette 定义 | 21 | 21 |
| `src/render/labels.ts` | 世界 | 投影/锚点纯函数（无颜色） | 0 | 0 |
| `src/render/smoke.ts` | 世界 | canvas 字面量 | 0 | 0 |
| `src/render/tilemap.ts` | 世界 | canvas 字面量 | 2 | 2 |
| `src/render/villager.ts` | 世界 | canvas 字面量 | 10 | 10 |
| `src/render/weather.ts` | 世界 | canvas 字面量 | 4 | 3 |
| `src/render/world-overlay.ts` | 世界 | DOM overlay（无 canvas 色） | 0 | 0 |
| `src/scenes/SandboxScene.ts` | 世界 | canvas 字面量 | 9 | 7 |
| `src/world/prefabs.json` | 世界 | prefab palette 槽位 | 16 | 15 |
| `index.html`（`<style>`） | UI | CSS 字面量/变量（含定义） | 24 | 17 |

> 说明：task-27 删掉了名字/气泡/招牌/摆件/祭坛/施工 6 处 canvas 文本（5 个文字色 + 气泡底），`palette.ts` 由 22 个定义降为 21（删未引用的 `UI.paper`）。`labels.ts` / `world-overlay.ts` 只做坐标与 DOM、无 canvas 颜色；UI 层 overlay 样式新增 `#24211b`（U2 纸墨）与 `#000` 阴影引用，唯一色仍 17。

### 2.3 结构性问题（比“色数超了”更值得先处理）

1. **命名误导**：`palette.ts` 的 `UI` 对象同时装了世界地形色（sky/soil/grass/…）与纸面 UI 色（paper/ink/amber）；“UI”这个命名会诱导后续继续把世界色写进去。建议拆 `WORLD` / `UI` 两组 token。
2. **token 未被引用 / 重复定义**：`UI.soil`、`UI.street` 仍未引用；`UI.paper` 已在 task-27 删除（其颜色 `#e8e2d2` 现在只属于 UI 层，名字/气泡迁 DOM 后不再进 canvas）。
3. **暗部重复**：`#000000`、`#111413`、`#14110d`、`#1a1a1a`、`#1b211c`、`#1d1a17`、`#1f2421`、`#241f19` 等 8 个近黑/深色，两两 ΔE 很小；它们分别被用在阴影、天空、轮廓、烟囱、台阶、网格、门窗上——**同一视觉角色被拆成多个色**，是 77 色的主要来源。
4. **speck 色与基色过近**：`TERRAIN_SPECK` 的 stone/plaza/farm/tree 四个色与对应基色的 ΔE 约 4–7，完全可以用“基色 + 有序抖动/alpha”表达；`tilemap.ts:36-53` 还是随机 1px 撒点，和设计文档“禁止单像素随机撒点、像素簇方向一致”（`source/01…:228`）冲突。
5. **蓝图色是十进制裸值**：`prefabs.json:14,28,42,56` 的 palette 是 `9067076` 这类数字，没有 token 名；改色必须同时动 JSON、蓝图解析测试和渲染层。
6. **UI 硬编码**：17 色里只有 `--ink`、`--dim`、`--panel`、`--line`、`--amber` 5 个变量；其余 12 个是 `background:#0f1310`、`button.primary`、纸面装饰等字面量。9 个越界色全部集中在这些字面量上。
7. **画布文字色已删除（task-27 完成）**：`#f3dca8`（角色名）、`#d9d4c5`（建筑招牌）、`#a9b49f`（摆件标签）、`#cfc7ae`（祭坛）、`#e0c98b`（施工）5 个画布中文色已随 DOM overlay/图标替换删除；气泡底 `#e8e2d2` 与未引用的 `UI.paper` token 也已离开世界层。世界层实跑 77 → **71**（差 55 到 16），其余 55 个越界色留给 task-28。

---

## 3. Top 色与用途

### 3.1 世界层 Top 10（按引用站点）

| # | 色值 | 次数 | 用途（证据） | 收敛处理 |
|---:|---|---:|---|---|
| 1 | `#000000` | 4 | `UI.shadow`：建筑阴影（`buildings.ts:37,69,97`）、角色阴影（`villager.ts:23`）；UI 还用作 box-shadow | 改 `#14110d` + alpha（不要新增黑色） |
| 2 | `#e3a13c` | 4 | `UI.amber`：招牌图标（`buildings.ts:102`）、进度条（`:193`）、祭坛符文（`SandboxScene.ts:201,203`） | **保留**（16 色里的焦点琥珀，与 UI 共享） |
| 3 | `#0a1420` | 2 | 雨幕压暗矩形（`weather.ts:18,42`） | 改 `#223a4d`（冷暗）+ alpha ≤0.28；不用最近色 `#14110d`（会把雨变绿） |
| 4 | `#111413` | 2 | 相机背景/天空（`main.ts:32`、`SandboxScene.ts:103`） | 合并到 `#14110d`（ΔE 3.0） |
| 5 | `#14110d` | 2 | `UI.ink`：眼睛（`villager.ts:93`）、进度条底（`buildings.ts:191`） | **保留**（16 色里的最暗色） |
| 6 | `#2b2b28` | 2 | 路灯杆/旗杆（`decor.ts:78,92`） | 合并到 `#14110d` 或 `#1b211c` |
| 7 | `#334234` | 2 | 草地亮部（`tilemap.ts:75`）+ 草簇 speck（`:46`） | **保留**（16 色里的草地亮部） |
| 8 | `#8f7a4c` | 2 | 脚手架压线/施工图标（`SandboxScene.ts:481,491`） | 改 `#4a3f39`（W09） |
| 9 | `#c9a86a` | 2 | 施工脚手架/图标（`SandboxScene.ts:476,488`） | **保留**（W10 暖色饰边） |
| 10 | `#f0c070` | 2 | 蓝图窗光（`prefabs.json:14,56`） | 改 `#e3a13c`（与焦点同色） |

> 读法：排名靠前的是“被多处复用的结构性暗色”，不是“最重要的材质色”。真正决定画面辨识度的是 `#e0b48c`（皮肤）、`#c06a3e`（围巾）、`#e3a13c`（焦点）、`#d8dad3`（烟/雨）这四个亮色，但它们各只有 1 次引用——**亮部稀缺**才是当前画面发闷的原因。

### 3.2 UI 层 Top 6

| # | 色值 | 次数 | 用途 |
|---:|---|---:|---|
| 1 | `#2b322c` | 8 | `--line`：画布边框、面板/输入/按钮/纸面/调试面板的分隔线 |
| 2 | `#e8e2d2` | 7 | `--ink`：深底亮文字；角色名/气泡（新 overlay）与纸面底色 |
| 3 | `#8e968c` | 6 | `--dim`：副标题、回执、调试面板次要文字 |
| 4 | `#e3a13c` | 5 | `--amber`：焦点描边、hover、调试标题 |
| 5 | `#000000` | 4 | `#app`/调试面板 box-shadow（含 rgba）与 overlay 名字阴影 |
| 6 | `#171b18` | 3 | `--panel`：神谕面板/状态行/调试面板底色 |

> UI 层 17 色的 Top 6 已覆盖 33/45 次引用；剩下 11 色各 1 次，基本是“按钮底色/纸面装饰”的一次性硬编码，收敛风险低。

---

## 4. 16 + 8 提案

### 4.1 世界层 16 色（全部取自现有源色）

| # | 色值 | 语义角色 | 当前引用 | L | 备注 |
|---:|---|---|---:|---:|---|
| W01 | `#14110d` | 最暗/轮廓/阴影/眼睛（合并 ink、shadow、sky） | 2 | 0.6% | 核心暗色 |
| W02 | `#1b211c` | 深土/树冠暗部/街巷暗 | 0 | 1.4% | 当前 `UI.soil` 未引用，保留槽；用于树/街/暗部 |
| W03 | `#2d3b2e` | 草地基色 | 1 | 3.9% | |
| W04 | `#334234` | 草地亮部/树冠亮部 | 2 | 4.8% | |
| W05 | `#3a3f3b` | 石/灰泥基色 | 1 | 4.8% | 蓝图 sage 主体也可落到这里 |
| W06 | `#4a4f42` | 广场/道路/石材亮部 | 1 | 7.4% | |
| W07 | `#3b3423` | 农田土 | 1 | 3.5% | |
| W08 | `#8a5a44` | 木/砖墙基色（建筑主体） | 1 | 13.1% | 保留 code_forge body |
| W09 | `#4a3f39` | 木/屋顶暗部（屋顶、树干、伞杆） | 1 | 5.3% | 保留 ink_house roof |
| W10 | `#c9a86a` | 暖色饰边/脚手架/旗帜/浅木 | 2 | 41.5% | |
| W11 | `#e3a13c` | 焦点琥珀（窗光、进度、路灯、祭坛符号） | 4 | 42.1% | 与 UI 共享 |
| W12 | `#e0b48c` | 皮肤 | 1 | 50.4% | |
| W13 | `#30506b` | 斗篷基色 | 1 | 7.4% | |
| W14 | `#223a4d` | 斗篷暗部 | 1 | 3.9% | 兼作雨天压暗 tint |
| W15 | `#c06a3e` | 围巾/强调橙（故障、暖色点缀） | 1 | 21.9% | |
| W16 | `#d8dad3` | 烟/纸片/雨/高光浅色 | 1 | 69.4% | |

> 取舍：16 色里没有独立的冷色亮部、没有第二套建筑材质、没有独立的紫色/鼠尾草色。三栋建筑的区分退化为**剪影（gable/tower/flat）+ 饰边/窗光颜色**；如果用户要求“靠材料区分三栋”，需要 18 色或接受更明显的结构差异（这是需要裁决的项，见 §7）。

### 4.2 UI 层 8 色（全部取自现有源色）

| # | 色值 | 语义角色 | 当前引用 | L | 备注 |
|---:|---|---|---:|---:|---|
| U1 | `#e8e2d2` | 纸面/深底亮文字 | 7 | 76.2% | 任务-27 后只属 UI（名字/气泡 overlay 与纸面） |
| U2 | `#24211b` | 纸上墨字 | 2 | 1.5% | 气泡文字色 |
| U3 | `#8e968c` | 次要文字（深底） | 6 | 29.5% | |
| U4 | `#171b18` | 面板底 | 3 | 1.0% | |
| U5 | `#2b322c` | 分隔线/边框 | 8 | 3.0% | |
| U6 | `#e3a13c` | 强调/焦点 | 5 | 42.1% | 与世界共享（收敛后唯一共享色） |
| U7 | `#0c0f0d` | 页面底/投影 | 1 | 0.4% | |
| U8 | `#c8876a` | 警告/错误 | 1 | 30.6% | `#reply.bad` |

> UI 8 色全部是现有值；9 个越界色可以通过“换 var / 加 alpha”收敛，不需要改视觉语言（暖纸 + 苔绿 + 琥珀的调性不变）。

---

## 5. 重画清单（收敛到 16 + 8 要动什么）

### 5.1 按模块的映射（世界层 55 个越界色）

> “动作”分三类：**合并引用**（色值接近，换引用即可）、**改值**（保留角色、调色值）、**重画/删**（材质或图层要重做）。文件:行号来自脚本完整表（附录 A）。

| 模块 | 现有色 → 目标 | 动作 | 影响 |
|---|---|---|---|
| 天空/网格/街道 | `#111413`→W01；`#1f2421`→W02（alpha 0.25）；`#2f3634`→W05；`#1a2a1d`→W02 | 合并 | `main.ts:32`、`SandboxScene.ts:103`、`tilemap.ts:95`、`palette.ts:13` |
| 地形 speck（4 个） | `#243826`→W03；`#434845`→W05；`#565b4c`→W06；`#4a412c`→W07 | 合并 + 改绘制 | `tilemap.ts:36-53`：随机 1px 改为 2×2 有序抖动 |
| 地面小物 | `#59615a`→W06；`#c9b46a`→W11（花用琥珀，或 W10） | 改值 | `tilemap.ts:79,82` |
| 建筑内联 | `#000000`→W01+alpha；`#1a1a1a`→W01；`#241f19`→W02；`#f2c14e`→W11+alpha；`#8f3b2f`/`#d9583f`→W15/W11 | 合并/改值 | `buildings.ts:36,54,60,68,158,161,165,167` |
| 蓝图 palette（13 个） | body：`#6b7a6a`/`#7a6252`/`#6b5c2f`→W08（code 的 `#8a5a44` 保留）；roof：`#3f4a4a`/`#2f3b33`/`#450f43`→W09（ink 的 `#4a3f39` 保留）；trim：`#c98b3f`/`#c7a06a`→W10，`#8fa08a`/`#c0a2c6`→W16；window：`#f0c070`/`#f2e3b0`/`#f6d9a0`→W11 | **重画** | `prefabs.json:14,28,42,56`；三栋材料趋同，靠剪影区分 |
| 角色 | `#1d1a17`→W01；`#2b2320`→W09（发色）；`#3a2d26`→W09；`#8c4a3a`→W08；`#a85c46`→W15；`#3f6584`→W13（失去斗篷亮部，需 1px 高光或删） | 改值/重画 | `villager.ts:84,86,88,101,104,109,127` |
| 装饰 | `#2b2b28`→W01；`#f0cf7a`→W11；`#4a3a28`→W09；`#3c5f3c`/`#4c744a`→W03/W04；`#4b4f48`/`#5c6158`→W06；`#b4553f`→W15；`#d9c9a6`→W10 | 改值/重画 | `decor.ts:35,37,78,80,82,85,87,89,92,94,96` |
| 祭坛/脚手架 | `#5a5f4c`→W06；`#8d8f74`→W10；`#2c302a`→W02；`#d9c27a`→W11；`#8f7a4c`→W09；`#2a241c`→W01+alpha | 改值/重画 | `SandboxScene.ts:183,185,187,189,467,472,474` |
| 天气 | `#0a1420`→W14（alpha ≤0.28）；`#b9cbd6`→W16（alpha）；`#c9dae3`→W16（alpha） | 改值（人工指定，不用最近色） | `weather.ts:18,42,50,62` |
| 工作粒子 | `#f0e7c8`→W16；`#6b7c9c`→W01（墨点=暗色） | 合并 | `smoke.ts:23,24` |
| 画布文字（5 个） | `#f3dca8`/`#d9d4c5`/`#a9b49f`/`#cfc7ae`/`#e0c98b` | **已删除**（task-27 迁 DOM overlay + 静态图标） | 已从世界层移除，不再参与 16 色预算 |
| 气泡底色（1 个） | `#e8e2d2` | **已删除**（气泡迁 DOM；`UI.paper` token 一并删除） | 世界层不再有该色，UI 层保留 |

### 5.2 UI 9 个越界色的收敛

| 现有色 | 目标 | 方式 |
|---|---|---|
| `#000000`（box-shadow ×3） | U7 `#0c0f0d` + alpha | 换值，阴影仍成立 |
| `#0f1310`（input 底） | U4 `#171b18` 或 U7 | 换值（ΔE 1.7） |
| `#1e2620`（button 底） | U4 `#171b18` | 换值 + 保留 border |
| `#2d2618`（primary 底） | U4 + U6 alpha | 换值 |
| `#5a4a26`（primary 边） | U6 `#e3a13c` | 换值 |
| `#b8ae94`（报头分隔线） | U2 `#24211b` alpha 0.25 | 换值 |
| `#6b6252`（纸面日期） | U2 alpha 0.65 | 换值 |
| `#9a8f74`（li marker） | U2 alpha 0.45 | 换值 |
| `#5d675e`（页脚） | U3 `#8e968c` alpha 0.8 | 换值 |

> alpha 派生不增加源色数，但会让“8 色”看起来像更多层；建议按 `UI_VISUAL_AUDIT.md` 的方法重新测对比度，确保正文 ≥4.5:1。

### 5.3 重画顺序（成本/收益）

1. ✅ **task-27 已删 5 个文字色 + 气泡底与未用 `UI.paper` token**（0 成本，世界 77→71；已复跑）。
2. **合并结构性暗色 + speck**（约 15 个色，改引用 + 2×2 抖动，1–2 天）。
3. **改蓝图 13 色 / 重画材质**（成本最高，2–4 天；先做一张三栋同框对照再动手）。
4. **角色 8 色**（影响 3 秒可读性，建议与 2 一起做；重点不是换色而是把亮部/轮廓做厚）。
5. **天气 3 色 + UI 9 色**（低风险，0.5–1 天）。
6. 全部完成后复跑本脚本，目标：世界 ≤16、UI ≤8、共享 ≤1（只剩 `#e3a13c`）。

---

## 6. 16 色能否做出可读雨天 / 夜景（初判）

### 6.1 依据 A：16 色亮度分布

把 16 色按 WCAG 相对亮度排序：**10 色 <8%**（W01–W09 加 W13/W14），**2 色 13–22%**，**4 色 41.5–69.4%**。也就是说，16 色里 **62.5% 是暗部**，画面能不能读，取决于那 4 个亮色（皮肤、琥珀、浅木、烟白）和 1px 轮廓。这与当前 77 色“暗色冗余、亮部稀缺”的病根相同——**只删色数不重排值域，画面不会变清楚**。

### 6.2 依据 B：关键对比度（WCAG 比值，仅作相邻色块的明度差参考）

| 对比对 | 比值 | 含义 |
|---|---:|---|
| 轮廓 / 草地（`#14110d` / `#2d3b2e`） | **1.59:1** | 角色轮廓在草地上非常弱 |
| 斗篷 / 草地（`#30506b` / `#2d3b2e`） | **1.40:1** | 斗篷与草地几乎同明度 |
| 斗篷暗部 / 草地（`#223a4d` / `#2d3b2e`） | **1.00:1** | 完全同亮度，只剩色相差异 |
| 皮肤 / 草地 | 6.23:1 | 唯一强对比 |
| 围巾 / 草地 | 3.02:1 | 中等 |
| 琥珀 / 草地 | 5.31:1 | 强 |
| 烟白 / 最暗 | 13.34:1 | 雨/烟粒子最清楚 |
| 纸 / 墨 | 12.41:1 | UI 没问题 |

辅助：**道路 / 草地 1.40:1、石面 / 草地 1.10:1、农田 / 草地 1.04:1**——三种地形在明度上几乎不可区分，目前靠色相与格子线分辨。

### 6.3 雨天初判

- 现有实现：雨幕 140 滴 + 18 splash + 压暗 `alpha≤0.28`；16 色下 drop/splash 统一用 W16（13.34:1），压暗用 W14（冷暗）而不是 W01（避免雨变绿）。
- **可读性风险**：雨天压暗会进一步压低已经只有 1.4–1.6:1 的角色/地面差；但雨滴本身和角色皮肤/围巾/琥珀仍保留 3–13:1。**判断：16 色可以做出“能看出主角与雨”的雨天，但必须重画角色亮部（≥2px 的围巾/皮肤边），并删掉 1px 噪点；否则会退化成“暗底 + 亮点”。**
- 反例证据：task-27 前 77 色下 `04-rain.png` 里主角已经接近“撑伞的小色块”，轮廓靠伞和皮肤读出来——16 色不会自动改善，只会更依赖这两个亮色；task-27 后世界 71 色，本质问题不变。

### 6.4 夜景初判

- 当前版本**没有昼夜**（`pixel-world/README.md:103`；`PROJECT_BRIEF.md:36` 也把完整昼夜列为暂不包含）。若未来加夜景：
  - 夜景 = 同一 16 色 + 暗 tint + 窗光/路灯（W11）；16 色里没有独立的冷色亮部，也没有“月光下的石板/路径”中间调。
  - 在 0.4–0.5 的暗 tint 下，地形 1.04–1.40:1 的差异会进一步压缩，**路径/农田/石面会并成一团**；主角还能靠皮肤/琥珀读出，但“3 秒指出路径”会失败。
- **初判**：16 色可以支撑“夜里有主角和灯”的画面，**不足以支撑“有层次的夜景”**。建议：夜景不进 1.0；若用户坚持，单独批 18 色（+冷色天空/石板中间调）并做 E-16 实验，而不是从 16 色里硬挤。
- 这不是硬结论：需要 §6.5 的实验验证。

### 6.5 可执行实验 E-16（建议下一阶段做）

1. 用提案 16 色重画一帧晴/雨/夜三态（同一构图、原生 480×320、400% 最近邻检查；**不要**用 `canvas.toDataURL()`——WebGL 黑屏，需元素截图）。
2. 3 秒可读性测试：给未看过的人看原生分辨率静帧，问“主角 / 主路径 / 当前神谕状态”，记录错误项；至少 3 人。
3. 通过条件：三态都能在 3 秒内指出主角与路径；雨天主角不被雨幕淹没；夜景若做，路径可辨。
4. 若失败，改动优先级：**值域（亮度）> 色相 > 增加色数**；不要先加颜色再调值域。

---

## 7. 风险与未决

| 项 | 风险 | 建议 |
|---|---|---|
| 色数上限未裁决（D4） | 16 vs 24/32 会决定重画规模；本文按 16+8 出清单 | 用户先拍板；若放宽到 24，本清单里“需重画 28 个”可降到约 12 个 |
| 三栋建筑材料区分 | 16 色下 sage/紫/棕三套材质被压成共享木/石/暖边，只剩剪影区分 | 接受剪影 + 饰边区分；或批 18 色保留 2 个材质强调色 |
| task-27 改变色表 | 中文迁 DOM 后 5 个文字色 + 气泡底色从世界层消失，色数 77→71 | ✅ 已完成并复跑；世界 16 色表不需要给文字色留位 |
| 脚本最近色是启发式 | 只按 Lab 距离给建议，不能理解“雨要冷色”“故障要红”等语义 | 天气/故障/阴影按 §5.1 的人工指定处理，不用脚本最近色 |
| prefab 十进制裸值 | 改色要同步 JSON、解析器、测试 | 下一阶段把 palette 改成 token 名/索引，再批量替换 |
| alpha 滥用 | 16 色 + 大量 alpha 渐变会看起来像 40 色，且难维护 | 规定：alpha 只用于阴影/压暗/脉冲，不用于材质色阶 |
| 夜景范围 | 16 色不足；18 色则要重开预算 | 先裁决夜景是否进 1.0，再决定是否扩色 |

---

## 8. 验收与 QA 复验

### 8.1 脚本验收

- `node --check scripts/audit-palette.mjs` 通过。
- 从 `pixel-world/` 与从其他 cwd 运行结果一致（脚本以自身位置解析根目录）。
- 连续两次运行 `--md` 输出**字节一致**（无时间戳）。
- 默认退出码 0；`--strict` 在超预算时退出码 1（已验证；提示文案已随 task-27 修正为“已启用”）。
- 不修改源码：task-26 只新增 `scripts/audit-palette.mjs` 与本报告；task-27 只改 `src/render|scenes|ui` 的渲染/DOM 与脚本提示文案，未改其他颜色与仿真逻辑。

### 8.2 手算抽查（至少 2 个文件）

| 文件 | 脚本输出 | 手算 | 结论 |
|---|---|---|---|
| `src/render/villager.ts` | 出现 10 / 唯一 10 | task-27 删名字/气泡 text 后，`0x` 10 个（84,86,88,99,101,104,107,109,114,127）；不再有 `#` 文本色 | ✅ 一致 |
| `index.html`（`<style>`） | 出现 24 / 唯一 17 | task-26 的 20 次 + overlay 样式（`.overlay-name`/`.overlay-bubble` 新增 4 次）→ 24；唯一色仍 17 | ✅ 一致 |
| `src/render/decor.ts`（附加） | 12 / 10 → **11 / 9** | 删摆件标签 text 后为 11 次，`#2b2b28` 与 `#f0cf7a` 各重复 1 次 → 9 唯一 | ✅ 一致 |
| `prefabs.json`（附加） | 16 / 15 | 4 套 × 4 槽 = 16；`#f0c070` 重复 1 次 → 15 唯一 | ✅ 一致 |

### 8.3 QA 复核命令

```bash
cd pixel-world
node scripts/audit-palette.mjs | head -12
# 预期（task-27 后）：世界层 71 / UI 17 / 共享 2 / 并集 86 / 世界超出 55 / UI 超出 9
node scripts/audit-palette.mjs --json | node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(JSON.parse(s).counts))"
node scripts/audit-palette.mjs --strict; echo $?   # 预期 1（已启用，仅验证门可接）
node scripts/audit-palette.mjs; echo $?            # 预期 0（默认不阻断）
```

### 8.4 task-27 交接（已实施，2026-10-19）

- ✅ 5 个画布文字色（`#f3dca8` 角色名、`#d9d4c5` 招牌、`#a9b49f` 摆件、`#cfc7ae` 祭坛、`#e0c98b` 施工）与气泡底 `#e8e2d2` 已从世界层删除；`UI.paper` token 一并删除。
- ✅ 动态中文走 DOM overlay：`src/render/labels.ts`（纯投影/锚点）+ `src/render/world-overlay.ts`（DOM 名字/气泡），按 `canvas.getBoundingClientRect()` 与相机 `worldView/zoom` 统一换算，100%–400% 贴合；静态文字改图标（招牌 glyph/祭坛符文/施工图标/摆件去标签）。
- ✅ 复跑实况：世界 71 / UI 17 / 共享 2（`#000000`、`#e3a13c`）/ 并集 86；世界越界 55。文字颜色改从 UI 8 色取（U1 亮字 / U3 次要），未引入新的画布文字色。
- ⏭ 剩余：按 §5.3 第 2–6 步收敛其余 55 个越界色（task-28）。

---

## 附录 A：脚本 `--md` 输出的 Top 与完整越界表

> 本附录由 `node scripts/audit-palette.mjs --md` 于 2026-10-19 task-27 复跑后重生成；正文口径：世界 71 / UI 17 / 共享 2 / 并集 86 / 世界越界 55。

## 世界层 Top（按使用次数）

| # | 色值 | 使用次数 | token | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #000000 | 4 | UI.shadow | src/render/buildings.ts:37 (UI.shadow)<br>src/render/buildings.ts:69 (UI.shadow)<br>src/render/buildings.ts:97 (UI.shadow)<br>src/render/villager.ts:23 (UI.shadow) |
| 2 | #e3a13c | 4 | UI.amber | src/render/buildings.ts:102 (UI.amber)<br>src/render/buildings.ts:193 (UI.amber)<br>src/scenes/SandboxScene.ts:201 (UI.amber)<br>src/scenes/SandboxScene.ts:203 (UI.amber) |
| 3 | #0a1420 | 2 |  | src/render/weather.ts:18<br>src/render/weather.ts:42 |
| 4 | #111413 | 2 | UI.sky | src/main.ts:32 (UI.sky)<br>src/scenes/SandboxScene.ts:105 (UI.sky) |
| 5 | #14110d | 2 | UI.ink | src/render/buildings.ts:191 (UI.ink)<br>src/render/villager.ts:93 (UI.ink) |
| 6 | #2b2b28 | 2 |  | src/render/decor.ts:78<br>src/render/decor.ts:92 |
| 7 | #334234 | 2 | UI.grassAlt/TERRAIN_SPECK.grass | src/render/tilemap.ts:46 (TERRAIN_SPECK.grass)<br>src/render/tilemap.ts:75 (UI.grassAlt) |
| 8 | #8f7a4c | 2 |  | src/scenes/SandboxScene.ts:481<br>src/scenes/SandboxScene.ts:491 |
| 9 | #c9a86a | 2 |  | src/scenes/SandboxScene.ts:476<br>src/scenes/SandboxScene.ts:488 |
| 10 | #f0c070 | 2 |  | src/world/prefabs.json:14 (code_forge.window)<br>src/world/prefabs.json:56 (workshop_plot.window) |
| 11 | #f0cf7a | 2 |  | src/render/decor.ts:80<br>src/render/decor.ts:82 |
| 12 | #f2c14e | 2 | UI.ember | src/render/buildings.ts:196 (UI.ember)<br>src/render/buildings.ts:199 (UI.ember) |
| 13 | #1a1a1a | 1 |  | src/render/buildings.ts:55 |
| 14 | #1a2a1d | 1 | UI.tree/TERRAIN_COLORS.tree | src/render/tilemap.ts:27 (TERRAIN_COLORS.tree) |
| 15 | #1d1a17 | 1 |  | src/render/villager.ts:103 |
| 16 | #1f2421 | 1 | UI.grid | src/render/tilemap.ts:95 (UI.grid) |

## UI 层 Top（按使用次数）

| # | 色值 | 使用次数 | CSS 变量 | 站点（前 4） |
|---:|---|---:|---|---|
| 1 | #2b322c | 8 | --line | index.html:36 (--line)<br>index.html:72 (--line)<br>index.html:80 (--line)<br>index.html:88 (--line) |
| 2 | #e8e2d2 | 7 | --ink | index.html:26 (--ink)<br>index.html:51<br>index.html:62<br>index.html:81 (--ink) |
| 3 | #8e968c | 6 | --dim | index.html:31 (--dim)<br>index.html:107 (--dim)<br>index.html:177 (--dim)<br>index.html:178 (--dim) |
| 4 | #e3a13c | 5 | --amber | index.html:84 (--amber)<br>index.html:84 (--amber)<br>index.html:94 (--amber)<br>index.html:94 (--amber) |
| 5 | #000000 | 4 |  | index.html:37<br>index.html:37<br>index.html:52<br>index.html:160 |
| 6 | #171b18 | 3 | --panel | index.html:71 (--panel)<br>index.html:100 (--panel)<br>index.html:158 (--panel) |
| 7 | #24211b | 2 |  | index.html:61<br>index.html:116 |
| 8 | #0c0f0d | 1 |  | index.html:25 |
| 9 | #0f1310 | 1 |  | index.html:79 |
| 10 | #1e2620 | 1 |  | index.html:87 |

## 越界色：世界层（不在提案 16 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|
| #000000 | 4 | 0.0% | #14110d | 5.8 | 需微调并重验轮廓 | src/render/buildings.ts:37 (UI.shadow)<br>src/render/buildings.ts:69 (UI.shadow)<br>src/render/buildings.ts:97 (UI.shadow)<br>src/render/villager.ts:23 (UI.shadow) |
| #0a1420 | 2 | 0.7% | #14110d | 12.0 | 需微调并重验轮廓 | src/render/weather.ts:18<br>src/render/weather.ts:42 |
| #111413 | 2 | 0.7% | #14110d | 3.0 | 近似等价：改引用/换色值即可 | src/main.ts:32 (UI.sky)<br>src/scenes/SandboxScene.ts:105 (UI.sky) |
| #2b2b28 | 2 | 2.4% | #1b211c | 6.4 | 需微调并重验轮廓 | src/render/decor.ts:78<br>src/render/decor.ts:92 |
| #8f7a4c | 2 | 20.3% | #8a5a44 | 19.5 | 需重画或改材质 | src/scenes/SandboxScene.ts:481<br>src/scenes/SandboxScene.ts:491 |
| #f0c070 | 2 | 57.4% | #c9a86a | 14.6 | 需重画或改材质 | src/world/prefabs.json:14 (code_forge.window)<br>src/world/prefabs.json:56 (workshop_plot.window) |
| #f0cf7a | 2 | 64.6% | #c9a86a | 17.2 | 需重画或改材质 | src/render/decor.ts:80<br>src/render/decor.ts:82 |
| #f2c14e | 2 | 57.6% | #e3a13c | 13.8 | 需重画或改材质 | src/render/buildings.ts:196 (UI.ember)<br>src/render/buildings.ts:199 (UI.ember) |
| #1a1a1a | 1 | 1.0% | #14110d | 4.7 | 需微调并重验轮廓 | src/render/buildings.ts:55 |
| #1a2a1d | 1 | 2.0% | #1b211c | 8.0 | 需微调并重验轮廓 | src/render/tilemap.ts:27 (TERRAIN_COLORS.tree) |
| #1d1a17 | 1 | 1.1% | #14110d | 4.3 | 需微调并重验轮廓 | src/render/villager.ts:103 |
| #1f2421 | 1 | 1.7% | #1b211c | 2.3 | 近似等价：改引用/换色值即可 | src/render/tilemap.ts:95 (UI.grid) |
| #241f19 | 1 | 1.4% | #1b211c | 5.7 | 需微调并重验轮廓 | src/render/buildings.ts:61 |
| #243826 | 1 | 3.3% | #2d3b2e | 4.9 | 需微调并重验轮廓 | src/render/tilemap.ts:46 (TERRAIN_SPECK.tree) |
| #2a241c | 1 | 1.8% | #1b211c | 7.1 | 需微调并重验轮廓 | src/scenes/SandboxScene.ts:483 |
| #2b2320 | 1 | 1.8% | #1b211c | 7.7 | 需微调并重验轮廓 | src/render/villager.ts:85 |
| #2c302a | 1 | 2.8% | #3a3f3b | 6.9 | 需微调并重验轮廓 | src/scenes/SandboxScene.ts:193 |
| #2f3b33 | 1 | 4.0% | #2d3b2e | 3.5 | 近似等价：改引用/换色值即可 | src/world/prefabs.json:28 (star_archive.roof) |
| #3a2d26 | 1 | 2.9% | #3b3423 | 7.3 | 需微调并重验轮廓 | src/render/villager.ts:64 |
| #3c5f3c | 1 | 9.5% | #334234 | 18.2 | 需重画或改材质 | src/render/decor.ts:87 |
| #3f4a4a | 1 | 6.4% | #3a3f3b | 5.7 | 需微调并重验轮廓 | src/world/prefabs.json:14 (code_forge.roof) |
| #3f6584 | 1 | 12.0% | #30506b | 8.8 | 需微调并重验轮廓 | src/render/villager.ts:77 |
| #434845 | 1 | 6.3% | #3a3f3b | 4.0 | 需微调并重验轮廓 | src/render/tilemap.ts:46 (TERRAIN_SPECK.stone) |
| #450f43 | 1 | 2.0% | #223a4d | 36.8 | 需重画或改材质 | src/world/prefabs.json:56 (workshop_plot.roof) |
| #4a3a28 | 1 | 4.6% | #3b3423 | 5.9 | 需微调并重验轮廓 | src/render/decor.ts:85 |
| #4a412c | 1 | 5.4% | #3b3423 | 6.4 | 需微调并重验轮廓 | src/render/tilemap.ts:46 (TERRAIN_SPECK.farm) |
| #4b4f48 | 1 | 7.6% | #4a4f42 | 3.8 | 近似等价：改引用/换色值即可 | src/render/decor.ts:35 |
| #4c744a | 1 | 14.5% | #4a4f42 | 25.1 | 需重画或改材质 | src/render/decor.ts:89 |
| #565b4c | 1 | 10.0% | #4a4f42 | 5.1 | 需微调并重验轮廓 | src/render/tilemap.ts:46 (TERRAIN_SPECK.plaza) |
| #59615a | 1 | 11.4% | #4a4f42 | 8.5 | 需微调并重验轮廓 | src/render/tilemap.ts:79 |
| #5a5f4c | 1 | 10.9% | #4a4f42 | 7.4 | 需微调并重验轮廓 | src/scenes/SandboxScene.ts:189 |
| #5c6158 | 1 | 11.5% | #4a4f42 | 8.1 | 需微调并重验轮廓 | src/render/decor.ts:37 |
| #6b5c2f | 1 | 11.0% | #8a5a44 | 19.1 | 需重画或改材质 | src/world/prefabs.json:56 (workshop_plot.body) |
| #6b7a6a | 1 | 18.1% | #4a4f42 | 17.3 | 需重画或改材质 | src/world/prefabs.json:28 (star_archive.body) |
| #6b7c9c | 1 | 19.9% | #30506b | 19.6 | 需重画或改材质 | src/render/smoke.ts:24 (UI.inkDrop) |
| #7a6252 | 1 | 13.5% | #8a5a44 | 12.8 | 需重画或改材质 | src/world/prefabs.json:42 (ink_house.body) |
| #8c4a3a | 1 | 10.8% | #8a5a44 | 10.0 | 需微调并重验轮廓 | src/render/villager.ts:60 |
| #8d8f74 | 1 | 26.6% | #4a4f42 | 26.8 | 需重画或改材质 | src/scenes/SandboxScene.ts:191 |
| #8f3b2f | 1 | 9.2% | #8a5a44 | 19.3 | 需重画或改材质 | src/render/buildings.ts:203 |
| #8fa08a | 1 | 32.8% | #d8dad3 | 25.0 | 需重画或改材质 | src/world/prefabs.json:28 (star_archive.trim) |
| #a85c46 | 1 | 16.4% | #8a5a44 | 13.7 | 需重画或改材质 | src/render/villager.ts:62 |
| #b4553f | 1 | 16.6% | #c06a3e | 12.1 | 需重画或改材质 | src/render/decor.ts:94 |
| #b9cbd6 | 1 | 57.9% | #d8dad3 | 12.5 | 需重画或改材质 | src/render/weather.ts:50 |
| #c0a2c6 | 1 | 41.1% | #d8dad3 | 30.9 | 需重画或改材质 | src/world/prefabs.json:56 (workshop_plot.trim) |
| #c7a06a | 1 | 38.3% | #c9a86a | 5.1 | 需微调并重验轮廓 | src/world/prefabs.json:42 (ink_house.trim) |
| #c98b3f | 1 | 31.2% | #e3a13c | 13.6 | 需重画或改材质 | src/world/prefabs.json:14 (code_forge.trim) |
| #c9b46a | 1 | 46.1% | #c9a86a | 8.1 | 需微调并重验轮廓 | src/render/tilemap.ts:82 |
| #c9dae3 | 1 | 68.1% | #d8dad3 | 9.8 | 需微调并重验轮廓 | src/render/weather.ts:62 |
| #d9583f | 1 | 22.1% | #c06a3e | 18.7 | 需重画或改材质 | src/render/buildings.ts:205 |
| #d9c27a | 1 | 54.7% | #c9a86a | 10.4 | 需微调并重验轮廓 | src/scenes/SandboxScene.ts:195 |
| #d9c9a6 | 1 | 59.3% | #e0b48c | 13.5 | 需重画或改材质 | src/render/decor.ts:96 |
| #f0e7c8 | 1 | 79.8% | #d8dad3 | 13.9 | 需重画或改材质 | src/render/smoke.ts:23 (UI.pages) |
| #f2e3b0 | 1 | 77.0% | #e0b48c | 19.2 | 需重画或改材质 | src/world/prefabs.json:28 (star_archive.window) |
| #f6d9a0 | 1 | 71.8% | #e0b48c | 15.3 | 需重画或改材质 | src/world/prefabs.json:42 (ink_house.window) |
| #2f3634 | 0 | 3.5% | #3a3f3b | 4.4 | 需微调并重验轮廓 | src/render/palette.ts:13 (UI.street) |

## 越界色：UI 层（不在提案 8 色内）

| 色值 | 使用 | L | 最近提案色 | ΔE76 | 建议 | 文件:行号 |
|---|---:|---:|---|---:|---|---|
| #000000 | 4 | 0.0% | #0c0f0d | 4.3 | 需微调并重验轮廓 | index.html:37<br>index.html:37<br>index.html:52<br>index.html:160 |
| #0f1310 | 1 | 0.6% | #0c0f0d | 1.7 | 近似等价：改引用/换色值即可 | index.html:79 |
| #1e2620 | 1 | 1.8% | #24211b | 5.7 | 需微调并重验轮廓 | index.html:87 |
| #2d2618 | 1 | 2.0% | #24211b | 6.5 | 需微调并重验轮廓 | index.html:95 |
| #5a4a26 | 1 | 7.2% | #2b322c | 25.1 | 需重画或改材质 | index.html:95 |
| #5d675e | 1 | 12.8% | #8e968c | 18.7 | 需重画或改材质 | index.html:144 |
| #6b6252 | 1 | 12.5% | #8e968c | 20.9 | 需重画或改材质 | index.html:131 |
| #9a8f74 | 1 | 27.8% | #8e968c | 12.5 | 需重画或改材质 | index.html:143 |
| #b8ae94 | 1 | 42.6% | #8e968c | 15.0 | 需重画或改材质 | index.html:124 |

## 提案 16 色世界

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #14110d | 最暗/轮廓/阴影/眼睛（合并 ink、shadow、sky） | 2 | 0.6% |  |
| #1b211c | 深土/树冠暗部/街巷暗（当前 soil 定义未引用，保留槽） | 0 | 1.4% | 当前未引用/保留槽 |
| #2d3b2e | 草地基色 | 1 | 3.9% |  |
| #334234 | 草地亮部/树冠亮部 | 2 | 4.8% |  |
| #3a3f3b | 石/灰泥基色 | 1 | 4.8% |  |
| #4a4f42 | 广场/道路/石材亮部 | 1 | 7.4% |  |
| #3b3423 | 农田土 | 1 | 3.5% |  |
| #8a5a44 | 木/砖墙基色（建筑主体） | 1 | 13.1% |  |
| #4a3f39 | 木/屋顶暗部（屋顶、树干、伞杆） | 1 | 5.3% |  |
| #c9a86a | 暖色饰边/脚手架/旗帜/浅木 | 2 | 41.5% |  |
| #e3a13c | 焦点琥珀（窗光、进度、路灯、祭坛符号） | 4 | 42.1% | 与 UI 共享 |
| #e0b48c | 皮肤 | 1 | 50.4% |  |
| #30506b | 斗篷基色 | 1 | 7.4% |  |
| #223a4d | 斗篷暗部 | 1 | 3.9% |  |
| #c06a3e | 围巾/强调橙（同时给故障与暖色点缀） | 1 | 21.9% |  |
| #d8dad3 | 烟/纸片/雨/高光浅色 | 1 | 69.4% |  |

## 提案 8 色 UI

| 色值 | 语义 | 当前使用 | L | 备注 |
|---|---|---:|---:|---|
| #e8e2d2 | 纸面/深底亮文字 | 7 | 76.2% |  |
| #24211b | 纸上墨字 | 2 | 1.5% |  |
| #8e968c | 次要文字（深底） | 6 | 29.5% |  |
| #171b18 | 面板底 | 3 | 1.0% |  |
| #2b322c | 分隔线/边框 | 8 | 3.0% |  |
| #e3a13c | 强调/焦点（与琥珀同值） | 5 | 42.1% | 与世界共享 |
| #0c0f0d | 页面底/投影 | 1 | 0.4% |  |
| #c8876a | 警告/错误 | 1 | 30.6% |  |


## 附录 B：脚本输出字段

| 字段 | 含义 |
|---|---|
| `counts.world` / `counts.ui` | 世界/UI 唯一源色数（含定义） |
| `counts.worldRefs` / `counts.uiRefs` | 应用/引用站点数（不含定义） |
| `counts.union` / `counts.shared` | 两层并集 / 共享色 |
| `outWorld` / `outUi` | 越界色数组：`hex, refs, defs, luminance, nearest{hex,role,distance}, suggestion, sites[]` |
| `proposedWorld` / `proposedUi` | 提案色：`hex, role, refs, defs, shared, luminance, reserved` |
| `outSummary` | 越界色的“近似/微调/重画”计数 |
| `files[]` | 每文件字面量出现次数/唯一色（手算核对口径） |
| `contrast` | 提案关键对比度（WCAG） |
| `unusedTokens` | `palette.ts` 中未被引用的 token |

## 附录 C：审计边界（未覆盖）

- 没有做屏幕截图逐像素取色；本文的“当前色数”是**源码源色数**，不是屏幕色数（运行时 alpha/叠加会让屏幕出现更多色）。
- 没有评估色盲可读性（Protanopia/Deuteranopia 模拟），建议下一阶段用 16 色表做一次模拟检查。
- 没有评估 400% 放大下的色阶 banding；16 色下大面积渐变（雨幕、阴影）可能出现色带，需要用抖动而不是新增颜色解决。
- 没有修改/重画任何素材；重画清单是建议，不是已实施。
- 未跑 `npm test` / `npm run check`：本次只新增脚本与文档，不改源码；QA 可自行复跑作为对照。
