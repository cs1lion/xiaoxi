# ART_SPEC.md — 像素世界视觉规范 V0.2

> 状态：**已定稿**（用户 2026-10-19 选 A 批准，S3-4 收口产物）。
> 作者：Pixel Art Director（teammate `art-director`）
> 日期：2026-10-19 · 基线 HEAD `2a0e6ea`（定稿时 `0af8c27`）· 任务 S3-4 收口产物
> 结构：按 `pixel-agent-chief-starter/docs/ART_SPEC_TEMPLATE.md` 四节；结合 `docs/DEMO_ART_SPEC_TODO.md` B1–B12，其中 D1–D4 冲突已由用户裁决解决，本文按**现状**写，不照抄旧结论。
> 证据：`pixel-world/eval/screens/slice-{sunny,rain,night}-*`（task-30 切片，种子 20261019）；色数口径见 `docs/ART_PALETTE_AUDIT.md`。
> 规则：未实测的内容标「提案」；本文不写代码、不改源码、不动 `ART_PALETTE_AUDIT.md`。行号以「文件 + 符号名」为准（task-22 已让 `main.ts` 漂移）。
> 范围注：task-22 的存档导出/导入（`main.ts` save-io、`save-store.ts`）属功能/数据规范，**不属于视觉规范**，本文不涉及。

**裁决基线（用户 S3-4 裁决，2026-10-19）**：四栋建筑能分清、雨天可读 → S3 切片通过；1.0 保持**世界 16 色 + UI 8 色**；夜景 16 色并团**留到 1.1**，加第 17/18 色必须走 ADR + 用户批准。

---

## 1. 画面与像素规则

### 1.1 基础（现状即规范，已验证）

| 项 | 值 | 证据 |
|---|---|---|
| 投影 | 纯 2D 正交俯视（斜俯视仅视觉，无 3D） | 现状 |
| Tile | **16px**（`setup.ts` `TILE_PX`） | 已验证 |
| 网格 | 30×20 格（`setup.ts` `GRID_WIDTH`/`GRID_HEIGHT`） | 已验证 |
| 原生分辨率 | **480×320**（`main.ts` `WIDTH`/`HEIGHT` = 30×20×16） | 已验证 |
| 显示缩放 | **固定 2 倍**，`Scale.NONE + zoom:2`，`pixelArt/roundPixels:true`，CSS `image-rendering:pixelated`（`main.ts` `Phaser.Game` 配置）；不跟随窗口 | 已验证 |
| 相机 | 固定，`Scale.NONE`（`main.ts` `Phaser.Game` 配置 `scale.mode`），无滚动/跟随；截图 `scroll=0,0`（manifest） | 已验证 |
| 整数缩放 | 原生帧 480×320，2×/4× 走最近邻；**禁止非整数 CSS 缩放**（像素边缘渗色） | 已验证 |
| 截图口径 | 元素截图 **960×640** 整数 → 最近邻 ÷2 得原生 **480×320**；**400% = 原生 ×4（1920×1280，即 canvas ×2）**；`capture-palette.mjs` 硬断言避免 961×641/480×321（`100` 档是全页 1010×1400）；**禁用 `canvas.toDataURL()`**（WebGL 黑屏） | 已验证（B10） |

### 1.2 角色与帧（现状 + 12fps 采样已实现）

- **角色规格 12×20**（`villager.ts` 角色常量；脚底中心 pivot，`villager.ts` 定位），逻辑占 1 格；实际绘制 10×20，伞宽 14px。
- **帧率三层语义（现状）**：
  - 逻辑固定 **10Hz**（`SandboxScene.ts` `LOGIC_STEP_MS=100`，命令队列逐拍结算）；
  - 动画帧采样 **12fps**，`villager-frames.ts` `ANIM_FPS=12`，**帧号 = `floor(now/(1000/12)) % 帧数`，只由仿真时间 `WorldSnapshot.now` 决定**，与 rAF/掉帧/渲染时机无关（`villager-frames.ts` `animFrame`/`animStep`）；
  - 渲染跟屏幕 rAF，每帧重读快照（`SandboxScene.ts` `renderSnapshot`）。
- **idle 2 帧 / walk 4 帧**（现状已实现，`villager-frames.ts` `IDLE_FRAMES`/`WALK_FRAMES`）：
  - `idle`：2 帧，呼吸上下 1px（整数像素，无子像素晃动）；
  - `walk`：4 帧，左右脚交替前伸 ±1px + 1px 起伏；走路态 = `toWork`/`toAltar`/`sheltering`（`villager-frames.ts` `isWalkingMode`），其余模式一律按 idle 采样；
  - 行礼下蹲 2px、交付前倾 1px（整数偏移，`villager-frames.ts` `modeLift`）；
  - **只做姿态，不新增颜色、不 tint、不改仿真**（`villager-frames.ts` 文件头纪律注释）。
- 整数像素：所有偏移 `Math.round`，`-0` 归一为 `0`（`villager-frames.ts` `pixelAt`），禁止逐帧浮点晃动。

### 1.3 建筑 4 套剪影（现状即规范）

| 建筑 | id | footprint | silhouette | 占位分区 |
|---|---|---|---|---|
| 炼码工坊 | `code_forge` | 5×4 | **gable**（尖顶） | industry |
| 星图书阁 | `star_archive` | 4×4 | **tower**（高塔） | scholarly |
| 抄书斋 | `ink_house` | 4×3 | **flat**（平顶） | agriculture |
| 新工坊 | `workshop_plot` | 4×3 | **gable**（扩建样板，蓝顶 W13） | industry（扩建） |

- 屋顶高 = `max(8, round(h*0.34))`（`buildings.ts` `createBuilding`）；门/窗/烟囱/工位全数据化（`prefabs.json`）。
- 剪影绘制：gable 逐阶收尖、tower 中塔+檐、flat 平顶+竖饰条（`buildings.ts` `drawRoof`）。
- 深度 Y-Sort：建筑 `depth = ALTITUDE + baseY`（`buildings.ts` `updateBuilding`），天气层固定 8000–8002（`weather.ts` `createWeatherView`）。

---

## 2. 颜色与材质

> 调色板单一来源：`src/world/palette-data.ts`（语义 token，零渲染依赖）。加色前先改 `ART_PALETTE_AUDIT.md`，`--strict` 会挡住（`palette-data.ts:5-7`）。

### 2.1 世界 16 色（`WORLD`，`palette-data.ts:13-46`）

| 编号 | token | 色值 | 用途 |
|---|---|---|---|
| W01 | `ink` | `#14110d` | 最暗 / 轮廓 / 阴影 / 眼睛 / 背景 |
| W02 | `soil` | `#1b211c` | 深土 / 树冠暗部 / 街巷暗 / 树底色 |
| W03 | `grass` | `#2d3b2e` | 草地基色 |
| W04 | `grassLit` | `#334234` | 草地亮部 / 树冠亮部 / 草簇 speck |
| W05 | `stone` | `#3a3f3b` | 石 / 灰泥基色 / 广场 speck |
| W06 | `plaza` | `#4a4f42` | 广场 / 道路 / 石材亮部 / 石子 speck |
| W07 | `farm` | `#3b3423` | 农田土 |
| W08 | `timber` | `#8a5a44` | 木 / 砖墙基色（建筑主体） |
| W09 | `timberDark` | `#4a3f39` | 木 / 屋顶暗部 / 树干 / 发色 / 靴子 |
| W10 | `trim` | `#c9a86a` | 暖色饰边 / 脚手架 / 旗帜 / 浅木 / 门框 |
| W11 | `amber` | `#e3a13c` | 焦点琥珀：窗光 / 进度 / 路灯 / 祭坛符文（与 UI 共享） |
| W12 | `skin` | `#e0b48c` | 皮肤 |
| W13 | `cloak` | `#30506b` | 斗篷基色；**兼作 workshop 蓝屋顶冷色识别** |
| W14 | `cloakDark` | `#223a4d` | 斗篷暗部；**兼作雨天压暗冷 tint** |
| W15 | `ember` | `#c06a3e` | 围巾 / 故障强调橙 |
| W16 | `pale` | `#d8dad3` | 烟 / 纸片 / 雨滴 / 高光浅色 |

亮度分布：暗部 10（W01–W07、W09、W13、W14）/ 中间调 2（W08、W15）/ 亮部 4（W10–W12、W16）（`palette-data.ts:9-10`）。**画面可读性取决于 4 个亮色 + 1px 轮廓，不取决于色数。**

### 2.2 UI 8 色（`index.html` CSS 变量）

`#e8e2d2`（纸面/深底亮字）、`#24211b`（纸上墨字/气泡字）、`#8e968c`（次要文字）、`#171b18`（面板底）、`#2b322c`（分隔线/边框）、`#e3a13c`（强调，与世界共享）、`#0c0f0d`（页面底/投影）、`#c8876a`（警告/错误）。收敛后**世界与 UI 仅 `#e3a13c` 共享 1 色**。

### 2.3 材质色阶与像素簇方向（模板 §2 要求）

- 每种主要材质 3 级色阶：基色 / 暗部（同 token 降 1 档或 alpha）/ 亮部（相邻亮 token）。木 = W08 基 + W09 暗；石/地 = W05 基 + W06 亮 + W02 暗；草 = W03 基 + W04 亮。
- **像素簇方向**：草地 speck 朝草簇（竖 2×3），石/广场 speck 朝石板接缝（横 2×1），农田 speck 朝垄（竖），统一 2×2 簇形状，方向随材质，不随机（见 §3.4）。
- 限制 W11（amber）与 W15（ember）使用：只给角色围巾、窗光、进度、路灯、祭坛符文、故障线；**不得铺大面积**，让角色/路径/工坊状态优先可读（§1 亮部稀缺原则）。

### 2.4 alpha 纪律（硬性）

- **alpha 只用于三类：阴影、压暗、脉冲**。禁止用 alpha 做材质色阶（会看着像 40 色且难维护）。
  - 阴影：建筑底 `WORLD.ink alpha 0.32`（`buildings.ts` `createBuilding`）、角色阴影 `alpha 0.35`（`villager.ts` 阴影）；
  - 压暗：雨天 `WORLD.cloakDark`，`intensity*0.28`，**alpha ≤ 0.28**（`weather.ts` `createWeatherView`）；
  - 脉冲：进度线 `amber alpha 0.35+0.35·sin(t/240)`、故障线 `amber alpha 0.5+0.5·sin(t/120)`（`buildings.ts` `updateBuilding`）。
- 材质色阶一律用**源色值**（16 色表内），不用 alpha 渐变。

### 2.5 雨天用色（现状即规范）

- 雨幕压暗 = `WORLD.cloakDark`（W14），`alpha = intensity × 0.28`（`weather.ts` `createWeatherView`）；**明令禁止用 W01 `ink` 压雨**（雨幕会发绿）。
- 雨滴/水花 = `WORLD.pale`（W16）：雨滴 `alpha 0.35+0.35·intensity`、水花 `alpha 0.22·intensity`（`weather.ts` 雨滴/水花绘制）。
- 主角雨天可读靠 W11 `amber` + W12 `skin`（亮部 4 色中的 2 个）+ 围巾 W15。
- 雨滴 140 滴（其中 1/3 画斜线）、水花 18 个，深度 8000–8002 盖在所有世界标签之上（`weather.ts` `createWeatherView`）；确定性种子 `window.__RAIN_SEED__` 供截图复现（`weather.ts` `setRainSeed`/`rainRandom`）。

---

## 3. 形状与环境细节

### 3.1 三栋生产建筑 + 抄书斋的识别规则（16 色下只能靠剪影 + 识别色）

| 建筑 | 剪影 | 识别色（`prefabs.json` palette） | 判定依据 |
|---|---|---|---|
| `code_forge` 炼码工坊 | gable 尖顶（5×4 最高） | 窗光 **W11 `amber`**、主体 W08 | 尖顶高差 + 琥珀窗光 |
| `star_archive` 星图书阁 | tower 高塔（4×4） | 饰边 **W16 `pale`**、屋顶 W03 `grass`、窗光 W11 | 塔高差 + 浅色饰边 |
| `ink_house` 抄书斋 | flat 平顶（4×3） | 饰边 **W10 `trim`**、屋顶 W09、窗光 W11 | 平顶 + 暖饰边 |
| `workshop_plot` 新工坊（扩建） | gable（4×3） | 屋顶 **W13 `cloak` 蓝顶**、饰边 W16、窗光 W11 | 蓝冷屋顶（与三栋暖身区分） |

- **识别色像素分布（task-30 实测，`slice-sunny`，native→400% 比例稳定）**：
  - W11 amber 400% ≈ 4,320 px（0.21%）· W10 trim ≈ 21,632 px（1.11%）· W13 cloak ≈ 13,888 px（0.72%）· W15 ember ≈ 768 px（0.04%）。
  - 四栋**剪影高差 ≥2px 在 400% 可辨**；识别色中 amber/trim/cloak 可辨，ember 占比 <0.05% 偏弱（400% 肉眼几乎不可见，记为风险项，非本轮阻断）。
- **workshop 蓝屋顶 W13 裁决**（用户 S3-4）：不加第 17 色，`workshop_plot` 屋顶用 W13 `cloak`（`prefabs.json` `workshop_plot.palette.roof`）；若切片评审证明 W13 屋顶与 W09 屋顶 400% 不可区分、三栋指认失败，再带截图走 ADR 向用户申请加色。
- 招牌静态文字已改**像素图标**（`<>` 代码 / 书 研究 / 笔 写作 / 锤 空工坊，`buildings.ts` `drawSignIcon`），不进 canvas 中文（D5-A）。

### 3.2 角色亮部最低规则（现状 + 验收）

- 围巾 W15 与皮肤 W12 亮边**原生 ≥2px**（400% 下视觉 ≥8px 块）；`villager.ts` 皮肤 8×8、围巾 10×2，400% 下成块可读。
- 眼睛 1px 可接受，但**必须贴 ≥2px 皮肤块**（`villager.ts` 眼睛贴脸 8×8 皮肤），不孤立悬浮。
- 斗篷 W13 `cloak` 对草地 1.40:1，**并入 16 色后不得只剩 1px 孤点**：补 1px 高光或删该亮部层（§1.4 角色亮部风险）。
- 多角色同框时角色间亮部**不粘连**（间隔 ≥2px 暗部或留白）。

### 3.3 建筑与装饰规格

- 门窗/烟囱尺寸（现状）：门 10×14、窗 8×6、烟囱 7×10（`buildings.ts` 门窗/烟囱绘制）；屋顶出檐 2px（`buildings.ts` `createBuilding`）。
- 装饰 3 类：lamp / tree / banner（`decor.ts`），带 1px 接触阴影；每区可交互物 ≤8（架构预算）。
- 天气/装饰不得淹没角色轮廓：雨幕深度 8000+ 在上，但**角色 1px 轮廓（W01）必须保留**，雨天压暗后主角亮部 ≥2 个相邻像素块可辨（§2.5）。

### 3.4 地面 speck（现状：稀疏 2×2 确定性簇）

- **不再单开近色 speck**：`TERRAIN_SPECK` 取基色的相邻 1 档（草→`grassLit`、石→`plaza`、广场→`stone`、农田→`soil`、树→`grass`，`palette-data.ts:64-69`），省 5 个源色。
- 绘制 = **每 tile 2–3 个 2×2 实心像素簇，位置由格坐标哈希确定性决定**（`tilemap.ts` `drawTexture`），**非随机、非满铺**（满铺 Bayer 实测像织物，已改稀疏成簇）。
- 约束：簇形状统一 2×2；方向随材质（§2.3）；**不引入第 17/18 色**；密度偏差（满铺→稀疏）是受控偏差，需保留注释说明。
- 已知问题：**祭坛遮挡**——角色名/祭坛符文在 400% 下叠字风险，静态文字已改图标（`SandboxScene.ts` `drawAltar` 祭坛符文），但角色 DOM overlay 名字压在祭坛区仍需用户评审（B9 残留项）。

### 3.5 植物

- 草簇/石子/花 3 类装饰（`tilemap.ts` `drawDressing`），`tuft` 竖 1×3、`pebble` 横 2×1、`flower` 单点 amber，**确定性哈希、非随机散点**；`bare` 留空。

---

## 4. 像素与构图验收

### 4.1 截图口径（E-16，硬性）

1. **清档**（清 localStorage，避免旧存档 `PlacedBuilding.palette` 带旧色进画面）；
2. **固定种子**（`window.__RAIN_SEED__`，雨滴确定性，`weather.ts` `setRainSeed`）；
3. **冻帧**（`sim now ≥ 6000` 后停 rAF，manifest 口径）；
4. 取景 = **原生 480×320（由 canvas 960×640 最近邻 ÷2）+ 400%（1920×1280，由原生最近邻 ×4）**；元素截图整数像素，**禁用 `toDataURL()`**（WebGL 黑屏）；
5. 相机固定 `Scale.NONE + zoom 2`、`scroll=0,0`（manifest）；
6. 晴/雨/夜三组**同一构图**。

### 4.2 客观项（可脚本测，pass/fail + 数字）

| 项 | 通过条件 | 实测（task-30） |
|---|---|---|
| 色数（源码） | 世界 16 / UI 8 / 越界 0 | 源色 16/8；**截图唯一色 138–162 是 alpha/滤镜合成色，不作验收口径**（§0 口径） |
| 越界色 | `--strict` 不超预算 | 收敛后 0（task-28 后） |
| 色带 | 400% 同一色块无 ≥3px 灰阶条带 | **PASS**（候选均为材质/光源边界，非连续条带） |
| 建筑 vs 草地对比 | **不设统一硬阈值**，记录实测，400% 轮廓可辨为准；剪影高差 ≥2px | WCAG 实测（2026-10-19 复核）：timber `#8a5a44` vs grass **2.04:1**、timber vs plaza 1.46:1、plaza vs grass 1.40:1、cloak 屋顶 vs grass 1.40:1；stone/farm vs grass 1.04–1.10 属已知低对比区，记入报告不阻断 |
| 路径 vs 草地 | 记录对比度 | 雨天 on-screen 1.26:1 < 1.4（**路径弱，标 flag**） |
| 雨滴 W16 / 烟 W16 | 雨滴对最暗可读 | `pale vs ink` **13.34:1**（`ART_PALETTE_AUDIT.md` 对比度表，源色口径）；400% 截图实测约 3.2:1 是合成/滤镜后的屏幕值，不作源色口径 |
| 夜景明度差 | 路径/广场/农田 ≥1.1:1 才可辨 | **并团**：grass vs farm 1.03、plaza vs stone 1.17、plaza vs grass 1.26 → **FAIL** |

### 4.3 人眼项（用户 3 秒指认，一律「待用户评审」）

- 原生 480×320 静帧，未看过世界的人 **3 秒内指出：主角 / 主路径 / 当前神谕状态**；
- 三栋建筑 3 秒指认（forge/archive/workshop）；
- **不得由 Bot/AI 冒充人类样本**；报告只写「待用户评审」，本仓库唯一人类评审者是用户；
- 夜景**不做**于 1.0（用户裁决，留 1.1）；16 色并团已实测，加 17/18 色必须 **ADR + 用户批准**。

### 4.4 加色红线

- **不新增颜色**；任何加色（第 17/18 色）必须：截图证据 + ADR + 用户批准；`--strict` 会挡住未改预算的加色（`palette-data.ts:5-7`）。
- 1.0 保持**世界 16 + UI 8**；夜景 16 色并团 → 记「16 色不支持有层次夜景，需 18 色」，留 1.1 裁决。

### 4.5 未决与风险（带入 1.1 / 需 ADR）

| 项 | 状态 | 去向 |
|---|---|---|
| workshop W13 蓝顶识别度（400% 与 W09 不可辨则三栋指认失败） | 风险 | ADR 裁量（加 18 色或改剪影/值域） |
| 夜景 16 色并团（grass/farm 1.03） | **FAIL，留 1.1** | 18 色 ADR + 用户批准 |
| 雨天路径对比 1.26 < 1.4（弱可辨） | flag | 改值域或接受（用户裁决） |
| 角色斗篷 W13 亮部并入后仅 1px 孤点 | 风险 | 补 1px 高光或删亮部层 |
| 角色 DOM overlay 名字压祭坛（3.4 已知遮挡） | 待用户评审 | B9 残留 |
| 390px 视口画布 960 溢出到 677 | 既有 P3 | 移动端断点策略（B1/B7） |

---

### 与模板 / TODO 的差异点（S3-4 收口说明）

1. **模板 §1「Tile/角色/分辨率待定」→ 本文已写死**（16px / 12×20 / 480×320 / 固定 2×，D1/D2 已裁决，非提案）。
2. **模板 §2「每材质 3–4 级色阶」→ 收敛为 16 色表内 3 级**（基/暗/亮），并新增 **alpha 纪律 + 雨天 W14≤0.28 / 禁 W01 压雨** 的硬性用色规则（§2.4/2.5，现状已实现）。
3. **TODO D1–D4 旧冲突不照抄**：16px（非 32px）、480×320（非 480×300/396）、**世界 16+UI 8**（非 24/32）、**中文不进 canvas（DOM overlay + 图标）**；本文按现状写。
4. **TODO B2「72 色失控」→ 已收敛**：task-28 后世界 16 / UI 8 / 越界 0（`ART_PALETTE_AUDIT.md` task-27/28 复跑口径），本文 §2.1/2.2 即最终表。
5. **TODO B4/B8「帧动画/12fps 缺失」→ 已实现**：idle 2 帧 / walk 4 帧 / 12fps 纯函数采样 / 逻辑 10Hz 三层（§1.2，`villager-frames.ts`），非提案。
6. **新增「workshop 蓝屋顶 W13 不加色」裁决**（§3.1，用户 S3-4），TODO 旧稿未含。
7. **夜景从「做」改「留 1.1」**（§4.3/4.4，用户裁决 1.0 不做昼夜，并团已实测），与 TODO B6「伪光照/昼夜进 1.0 之后」一致但更硬。
8. **证据锚定 task-30 切片**（§4.1/4.2，`slice-*` 实测数字），替换 TODO §1.2 的过期 `04/05/08` 截图口径。
