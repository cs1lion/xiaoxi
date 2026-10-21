# QA 验收标准：像素世界视觉升级（2026-10-20 立项）

> 维护人：QA Engineer。本文是本轮视觉升级的**独立验收依据**，与 `ART_STYLE_TARGET.md`（美术规格）、
> `UIUX_AUDIT.md`（病根诊断）、`PIXEL_WORLD_VISUAL_UPGRADE_PLAN.md`（实施计划）配套使用。
>
> 原则：不擅自替换验收目标；失败与未验证如实标注；不为凑通过降低标准；
> 实施者的自述与测试输出只作参考，QA 以自己实跑的结果为准。

---

## 1. 验收范围

**范围内**（视觉升级允许改动的面）：

- `pixel-world/src/render/**`（地形、建筑、装饰、角色、天气、特效、overlay 绘制）
- `pixel-world/src/scenes/**`（场景驱动）
- `pixel-world/src/ui/**` 与 `pixel-world/index.html`（DOM 面板、状态行、报纸）
- `pixel-world/src/world/prefabs.json`（仅 palette 槽位；蓝图结构不变）

**范围外**（本轮不验收、也不允许做）：

- Agent 集成（S5 之后的事）
- 昼夜循环、经营/惩罚机制（1.0 铁律）
- `pixel-world/src/world/**` 的逻辑改动（升级只改"怎么画"，不改"世界是什么"）
- 主仓库 `D:\project\neurosama`（master 线，任何人不得触碰）

**红线**：`src/world/` 不得出现 `import Phaser`（当前零 import，仅两处注释提及；升级若引入即 P1）。

---

## 2. 基线（QA 独立实测，2026-10-20，HEAD `bd2a8a7`，worktree 干净）

| 项 | 命令（cwd=`tmp/wt-pixel-world/pixel-world`） | 实测结果 |
|---|---|---|
| 类型检查 | `npm run check` | exit 0 |
| 纯逻辑单测 | `npm run test:inprocess` | **190/190**，0 fail |
| 源色审计 | `node scripts/audit-palette.mjs` | 世界 **16** / UI **8** / 共享 **1**（`#e3a13c`）/ 并集 **23** / 越界 0 |
| 严格模式 | `node scripts/audit-palette.mjs --strict` | exit 0 |
| 整门禁 | `python scripts/test_pixel_world.py`（worktree 根，用主仓 venv 的 python） | **PASS，exit 0** |
| 浏览器 smoke | `node scripts/smoke.mjs`（114 项）+ `LEDGER=1`（21 项） | **未验证**（ego-browser 就绪前，见 §7） |

> 基线数字由 QA 自己跑出，非引用。任何人复跑应得到同样结果；对不上先查环境再查改动。

---

## 3. 当前状态实测锚点（升级前的"病"有多大，用数字说话）

**取证图**：`pixel-world/eval/screens/smoke-ego-0{1,2,4}-*.png`（task-33 smoke-ego 取证，
World Builder 脚本 + Chief 代跑 ego-browser；**不是**本轮升级的正式前后对比基线，正式基线见 §5-D2）。

| 测量（QA 脚本法，可复跑） | 数值 |
|---|---|
| 全页截图尺寸 | 1126×765 |
| 全页唯一色数 | 2984 |
| 全页亮度 | 均值 51.2/255，std 60.4 |
| 画布区（960×640，按居中假设裁剪）唯一色 | 1338 |
| 画布区亮度 | 均值 45.1/255，std 50.4 |

**来源色关键对比度**（`audit-palette.mjs` 的 WCAG 比值，仅作初判依据）：

| 对比对 | 比值 | 读法 |
|---|---:|---|
| outline/grass | 1.59:1 | 轮廓与草地亮度差很小 |
| cloak/grass | 1.40:1 | 斗篷与草地亮度差很小 |
| **cloakShade/grass** | **1.00:1** | **斗篷暗部与草地亮度完全相同——按亮度她"融进"草地** |
| skin/grass | 6.23:1 | 皮肤可辨 |
| amber/grass | 5.31:1 | 焦点色可辨 |

**QA 判断**：`cloakShade/grass = 1.00:1` 是"她在哪"在 3 秒内答不出来的直接量化证据；
角色可辨性目前主要靠皮肤/围巾/琥珀几个亮点，整体剪影与背景拉不开。这正是本轮升级要解决的核心问题，
§4-A 的验收标准据此设定（靶值由美术定，QA 记录前后实测值）。

---

## 4. 验收标准

### A. 产品级：3 秒可读性（本轮核心，人工判定）

**A1 三问测试**

- 材料：① 开局截图（480×320 原生 1× 与 2× 显示各一）；② "有单在跑"状态截图（神谕派单后她进屋开工、状态行显示任务进度）。
- 方法：观察者（用户或 Chief 指定）每张限时 **3 秒**，回答三问——**她在哪？在干什么？我的事到哪一步？**
- 通过标准：三问在对应状态下全部答对 = 通过；答对 2 问 = 有条件通过（附缺陷单）；≤1 问 = **不通过（P1）**。
- QA 记录观察者的原始回答，不代答、不引导。

**A2 材质可辨（QA 独立看截图，不靠图例、不靠施工说明）**

- 草地 / 土地 / 石材 / 木材 / 屋顶 / 角色剪影，在 1×（480×320）下可互相区分。
- 判定为"仍像网格色块"（材质不可辨）= **P1**，不过。

**A3 轮廓与对比**

- 角色在草地、道路、建筑前三种背景下剪影均可分离。
- 量化辅助：升级后重跑 `audit-palette.mjs`，`outline/grass`、`cloak/grass`、`cloakShade/grass` 三个比值应相对基线（1.59 / 1.40 / 1.00）**显著提高**；具体靶值由 `ART_STYLE_TARGET.md` 定，QA 记录前后值。
- 注意：亮度对比只是初判，最终以截图人眼判定为准（色相差异在像素尺度可能救回来，也可能救不回来）。

**A4 图层与遮挡**

- 地面 → 建筑 → 角色 → 特效的绘制顺序正确；建筑多层深度不穿帮；smoke 已覆盖的断言（如"进屋后人不在门外"）保持通过。

### B. 约束合规（硬门禁，自动判定，全绿才算过）

| # | 检查 | 依据 |
|---|---|---|
| B1 | 源色预算不破：audit **16/8/1/23**、`--strict` exit 0；`palette-budget.test.ts` 全绿（含"render/scenes 无 canvas 颜色字面量""token 值等于提案 16 色"两条） | 色数铁律；**加色必须 ADR + 用户批准**，无 ADR 加色 = P1 |
| B2 | 中文不画进 canvas：`no-cjk-canvas.test.ts` 绿 | D5-A |
| B3 | 动画纪律：`anim-frames.test.ts` 绿（12fps 采样、姿态偏移整数像素、位置取整、采样是纯函数） | D1-A |
| B4 | overlay 投影：`labels.test.ts` 绿（100%/200%/400%、非整数 DPR 不抖动、对齐误差 ≤1 逻辑像素） | D5-A |
| B5 | 世界预算与不变量：`world.test.ts` 绿（30×20、三铺子各一活、门口可通行、越预算拒绝开工） | README 世界预算 |
| B6 | 存档不破：`save*.test.ts` 全绿（若动 prefabs palette，读档按当前 prefab 重解的路径已有测试覆盖） | D6-A |

### C. 回归（只增不减）

- C1 `npm run check` exit 0。
- C2 `npm run test:inprocess` **≥190/190**。新增视觉相关测试应使总数上升；总数下降或任何一条红 = 回归，定位到具体用例后才算修好。
- C3 整门禁 `python scripts/test_pixel_world.py` exit 0。
- C4 smoke **114 + 21**：ego-browser 就绪后由 Chief 代跑、QA 独立复验（静态核映射表条数是否被合并/删减 + 关键链路断言是否齐全；不复用施工脚本的输出）。
- C5 演示链路：`npm run dev` 起在 5188、页面加载无 JS 报错（smoke 覆盖项，随 C4 一起验）。

### D. 证据要求（每份施工交付必须齐）

- D1 六件套：目标、修改文件清单、运行命令、**实际测试结果**、截图或视觉证据、已知问题与下一步建议。缺一即退回。
- D2 截图规范：**同种子、同参数、同分辨率**的前后对比；用 `pixel-world/scripts/capture-palette.mjs`
  （晴/雨/夜 × 100%/canvas/native-480×320/400pct，同种子字节可复现）。
  **升级开工前先截一份正式基线**（task-33 的 smoke-ego 截图不能替代，见图注）。
- D3 失败/未验证如实标注；禁止用"看起来对""逻辑上应该没问题"替代实测。

---

## 5. 验收流程（每份交付走一遍）

1. 施工按 D1 交六件套 →
2. QA 跑 §4-B/C 全部自动门禁（自己跑，不看施工贴的输出） →
3. 浏览器验证：Chief 代跑 ego-browser，QA 写脚本或静态核（smoke 等价断言） →
4. 截图前后对比（D2 同种子） →
5. A1 三问测试（用户/观察者执行，QA 记录） →
6. QA 出缺陷清单（等级 / 复现步骤 / 预期 / 实际 / 验收依据） →
7. 修复后 QA **独立复验**：原缺陷是否真解决 + 重跑 B/C 全量查新回归。

## 6. 缺陷等级（视觉向口径）

| 等级 | 判据 |
|---|---|
| P0 | 跑不起来 / 崩溃 / 存档损坏 / 数据损坏 |
| P1 | 产品级目标未达成（三问 ≤1 答对、材质不可辨"仍像网格色块"）；关键链路断裂（smoke 红）；无 ADR 加色；约束门禁红 |
| P2 | 局部美术不一致（某材质与整体风格脱节）；一般回归（单测红但可定位修复）；对比度不足影响可读性 |
| P3 | 细节瑕疵（轻微锯齿、个别像素错位、易用性建议） |

## 7. 未验证规则

- ego-browser 就绪前，所有浏览器类验证（smoke、页面链路、截图取证）一律标**未验证**，不推测通过。
- A1 三问测试需人工观察者；未执行前标未验证，不用 QA 自答代替。
- 静态检查（代码走查、映射表核对、审计数字）可执行并出结论，但明确标注为**静态结论**，与实跑证据分开记录。

## 8. 复验与升级路径

- 修复后复验必须包含：原缺陷场景重测 + §4-B/C 全量重跑（防"修一处红三处"）。
- 疑似架构、安全或跨模块问题（如渲染层开始判断"能不能走"、世界层被塞进渲染依赖）：保留证据，升级 Architect，不在 QA 层私了。


---

## 9. P0-1 运行时取证：检测器口径修正与实测（2026-10-20 晚，QA 独立复算）

**取证方法（已生效，取代 §7 的「ego 就绪前」前提）**：ego 的 rAF 节流已由 Chief 用 CDP
`Page.addScriptToEvaluateOnNewDocument` 注入 MessageChannel 驱动的 rAF 替身解决（门控 60fps、
世界时间比 1.00）。截图取证走 `page.cdp('Page.captureScreenshot', { clip, scale })`——
浏览器合成器截图，**不依赖页面句柄、不走 getImageData、不受 WebGL preserveDrawingBuffer 影响**。
§7 写于 ego 就绪前，其「ego 就绪前一律未验证」条款自此 superseded（原文保留作历史记录）；
浏览器类验证已实际执行，结论见 §10。

**检测器 A（相位剖面）实测**——Chief 实现并实跑，QA 用 Pillow 独立复算，**数字完全一致**：

| 档 | G_col | G_row | 判定 |
|---|---|---|---|
| 默认 `?ledger=off` | 1.207 | 1.758 | ≤2 ✅ |
| `?debug-grid=1` | **5.317** | 2.794 | 列方向 **4.41×** ✅ |
| `?debug-grid=0` | 1.269 | 1.747 | ≤2 ✅ |
| `?debug-grid`（无值） | 1.221 | 1.792 | ≤2 ✅ |

**A3 判定口径修正：只取列方向。** 行方向实测只有 1.59×，达不到 3× 要求，且是**必现结果**
（任何人跑都一样），不改口径会稳定误判为不通过。实测机制（QA 量的，与初版解释不同）：
横线确实把行均值压暗了 **2.78**（不是 0——「行均值把横线抹平」的解释不准确），但列方向
信号是 5.317；同时行方向的默认噪声 1.758 明显高于列方向的 1.207（地图的水平分区带在行
均值上留下结构）。两者相乘使行方向比值只有 1.59×。**修正后 A3 通过条件：G_col ≥ 4 且
≥ 默认档 3 倍；G_row 记录存档，不作为通过条件。**

**三个与初版口径不同的实测事实**：
1. **同 URL 两次加载不是字节一致**：MOToff-1 vs MOToff-2 diff = **21,256 px**（0.68%），
   带外 12,557。角色/粒子每次加载初始化不同，全画布下不可字节复现（8× 局部裁剪避开角色
   区域时才字节一致——初版 8× 证据的 off/zero/empty 相同是**区域特性**，不是全局特性）。
2. **A4「严格相等」的成立范围**：全画布下 QAzero/QAempty 与 QAoff 字节不同（各差 16,191 /
   20,196 px，与加载间噪声同量级）；相位检测器三档等价（G 均 ≤2）✅。正确表述是「相位
   检测器等价 + 无网格线绘制」，字节级相同只在无角色区域成立。
3. **A6 是「噪声内通过」不是「零基线硬通过」**：grid 档带外变化 **11,453**（QA 实测，
   band ±9）与同 URL 加载间噪声 **12,557** 同量级 → 无系统性夹带 ✅。但基线不是 0，
   不能写成「低于 0 基线的硬通过」。

**检测器 B（同构建 diff）实测**（QA 独立复算，band ±9，阈值 sum>12）：

| 对比 | 总变化 | 带内 | 带外 |
|---|---|---|---|
| 默认 vs `?debug-grid=1` | 326,755 | 315,302 | 11,453 |
| 默认 vs `?debug-grid=0` | 16,191 | 8,658 | 7,533 |
| 默认 vs 无值 | 20,196 | 11,167 | 9,029 |
| MOToff-1 vs MOToff-2（同 URL 两次） | 21,256 | 8,699 | 12,557 |

A5（边界带内 ≥ 30000）：**315,302 ✅**。

---

## 10. 五步独立复验结论（2026-10-20 晚，HEAD `e513a11`）

批次号与缺陷等级是两个轴，区分见 `docs/QA_P0_BATCH_VS_DEFECT_LEVEL.md`（`13803a8`，
Chief 拍板）。以下每步同时标两个轴。

**门禁（QA 实跑，HEAD e513a11）**：`npm run check` exit 0；`test:inprocess` **206/206**
（190 → `c496e7d` +5 → `e513a11` +11）；`audit-palette` 世界 16（引用 107）/ UI 8（47）/
并集 23 / 越界 0，`--strict` exit 0；`python scripts/test_pixel_world.py` **PASS**。
smoke 116/116 为 Chief 代跑（六份日志），QA 只核过日志口径（3 项 MAN 单列记账、无静默跳过），
未独立复跑。

### P0-1 删通铺网格线（`260ad93`）· 缺陷等级 P2 · **通过**

- 静态：单文件 +5/−1，只有守卫 + 注释；`drawGridLines` 定义保留、仍用 `WORLD.soil`
  （零新色）；调用点 = 1 且在 `=== '1'` 同行
- 运行时：检测器 A/B 全过（§9）；A4 三态相位等价；A5 带内 315,302；A6 带外 11,453 在
  加载噪声 12,557 内
- 门禁全绿

### P0-2 地形拼接边（`899611d` + 修复 `e513a11`）· 缺陷等级 P2 · **有条件通过**

- **穷举 8,398,080 组合（5 材质 × 6^8）零违规**（QA 自己写脚本跑，修复前后各一遍）：
  掩码恒 [0,15]、每格原语 ≤4、每个边位 ⟺ 邻格严格更低（双向：不画低侧也不漏画）、
  每个角位 ⟺ 两直角边未画且斜角严格更低、fillRect 不越 16×16
- **真实地图（QA 自己光栅化）**：182 格有边（30.3%）、186 原语、面积和 2886 / 唯一像素
  2882（4 个双描点是两边线交叉的格角，同色或亮边胜出，无视觉影响）；**180 条材质交界
  每条恰好一条 1px 线，双画 0 漏画 0**；4 个 L 内角全部由斜角格补齐
- **隐形边 bug（QA 独立发现 → 施工独立发现并修）**：tree 底色就是 soil，暗边用 soil
  落差 0.0（96 个 tree 格中 48 个的边完全看不见，738 px）。`e513a11` 把 tree 暗边改
  ink（落差 14.0）。**QA 用新代码独立复跑：隐形像素 738 → 0，tree 可见像素 738 → 1476**，
  与施工声明一致
- **对比度（QA 自己算）**：修复后边色 vs 本格底色 WCAG 1.109–1.538（最弱 grass 亮边
  1.109）；Chief 报的六个数（1.11/1.27/1.39/1.53/5.31/8.38）QA 逐一复算吻合
- **11 条提交测试**（`terrain-edges.test.ts`）：质量高——直接读导出的 DARK/BRIGHT 表
  防副本脱钩，「暗边 ≠ 本格底色」正是这个 bug 的回归锁
- **有条件的原因**：1× 下边线对比度 1.11–1.53 WCAG 仍弱（8× 放大可见），这是规范
  mandated token 集（soil/plaza/grassLit）的物理限制，不是实现缺陷。需 Pixel Art
  Director + 用户裁决：接受 / 允许换高对比 token（amber 5.31、pale 8.38 都在 16 色内）/
  撤掉该特性

### P0-3 建筑三层深度（`7eade0a`）· 缺陷等级 P2 · **有条件通过**

- **深度算术三前提 QA 全部独立核实**：① `villager.ts:60` depth = `ALTITUDE + py + 8`、
  `py = (pos.y+1)×TILE_PX`（:57）；② prefabs.json 四栋蓝图 `door.y == footprint.h`
  全部成立（4/4、4/4、3/3、3/3）；③ `buildings.ts:149` `baseY = py + h`。推出：站门口
  角色 = baseY+24、再下一格 = baseY+40，front 取中点 baseY+32 ∈ (24,40) ✅ 压得住门口的
  人、压不到下一格的人
- 施工把规范的 +12 改成 +32 并写了推导——**这个偏离有据，QA 验过算术**
- 台阶归 body 不归 front 的理由（落在门口格上沿、会横穿角色身体）成立
- **有条件的原因**：① 这个提交**没有配套测试**（只动 buildings.ts，commit 里的「15 项
  断言」是临时跑的没入库），遮挡行为无回归保护——建议按 `prop-depth.test.ts` 的先例补
  一条 depth 断言（P3）；② 运行时遮挡证据是 Chief 代跑的截图（p0-3-villager-at-door-6x.png），
  QA 无法查看图像、未独立复验——静态算术已保证 depth 排序结果，但框架级确认还缺一次
  独立运行

### P0-4 地面材质结构（`7514246`）· 缺陷等级 P2 · **通过（代码级）；像素统计未独立光栅化**

- 代码级逐条核实：农田垄线 `for (ry=2; ry<16; ry+=4)` → y=[2,6,10,14] 精确命中声明；
  石板横缝 y=7 通栏、竖缝 x=7/15 按 (x+y) 奇偶错缝；草地簇 `1 + (seed%2)` → 平均 1.50
  簇/格与声明一致；贴边装饰逻辑（nearestNonGrassSide + 2px 带）与声明一致；小径三段 +
  逐边描边判定逻辑成立
- **未独立复现的**：施工的像素级统计（89% 装饰落贴边带等）——QA 尝试用假 scene 光栅化
  真实 drawTerrain，但 phaser 在 Node 里加载不了（缺可选依赖 phaser3spectorjs，模块图
  不完整），这条路堵死。像素统计标注为**施工自报、代码逻辑已核实、未独立光栅化**
- 门禁全绿；无新色（soil/plaza/stone/trim/grass/grassLit 全在 16 色内）

### 道具 Y-Sort + 接地线（`c496e7d`）· 缺陷等级 P2 · **通过**

- 算术独立核实：`propDepth = ALTITUDE + (at.y+0.8)×16 + 8`，与角色 depth 同口径（+8
  同源）；落点 0.8 与 drawProp 的 setPosition 同源——depth 锚在真实落点上
- 遮挡方向正确：角色在道具上一格 → 道具 depth 更大 → 被遮（走到树后）；同格/下一格 →
  角色在前
- **5 条提交测试质量高**：同口径、落点同源、两个方向的遮挡、depth 单调性——正是 P0-3
  缺的那类测试
- 接地线 8×1 ink α0.55 在 ART_SPEC 阴影档位内、不新增色；走路时容器级 alpha 0.28 同比
  变淡，相对深浅不变

### 总体

- **P0-1 通过；P0-2 有条件通过（可见性已知限制待用户/美术裁决）；P0-3 有条件通过（补
  depth 测试 + 运行时独立复验）；P0-4 通过（代码级）；c496e7d 通过**
- 全部门禁绿；色数预算零突破（16/8/23，越界 0）
- **共性未验项**：A1 三问测试（需人工观察者）五步都没跑；A2 材质可辨的人眼判定没跑；
  smoke 未独立复跑（Chief 代跑）
- **待办**：① L794 用 CDP `Runtime.enable` + `Runtime.exceptionThrown` 做真 pageerror
  等价（Chief 已采纳，收口前统一补）；② P0-3 补 depth 回归测试；③ P0-2 可见性裁决；
  ④ A1/A2 人工判定排期

---

## 11. P0-4b α 返工独立复验（`36f6a4c`，2026-10-20 晚，QA 独立复算 + 截图实测）

**基线（QA 实跑，HEAD `f84da49`）**：`git log` HEAD `f84da49`、工作区干净；
`test:inprocess` **206/206**；`audit-palette --strict` 世界 16（引用 **111**）/ UI 8（47）/
并集 23 / 越界 0，**exit 0**；`python scripts/test_pixel_world.py` **PASS**。

**ΔL* 独立复算（sRGB→linear→CIE L*，QA 自己算）——四个数全部吻合**：

| 项 | QA 复算 | 声明 | 判定 |
|---|---|---|---|
| 石板缝实心 soil α1 / stone | **14.08** | 14.1 | ✅ |
| `TERRAIN_SPECK.grass` → soil α1 / grass | **11.29** | 11.3 | ✅ |
| `BRIGHT.grass` → plaza α1 / grass | **9.51** | 9.5 | ✅ |
| 草簇 tuft 实心 soil α1 / grass | **11.29** | 11.3 | ✅ |

**返工前旧合成色独立核对（确认这些色确实产生过、且确实不可见）**：

| 旧实现 | 合成色（QA 复算） | 声明 | ΔL*（QA） |
|---|---|---|---|
| 石板缝 soil α0.25 / stone | **#323833** | #323833 | 3.26 |
| 广场高光 stone α0.35 / plaza | **#444940** | #444940 | 2.48 |
| 草簇 grassLit α0.9 / grass | **#324133** | #324133 | 2.57 |
| 旧网格线 soil α0.25 / grass | **#29352a** | #29352a | 2.51 |

**真实渲染截图实测**（`p04b-canvas.png`，2160×1440 = canvas 960×640 CSS × DPR 2.25，
格周期 72 设备像素，QA 用 Pillow 自己数）：
- **`#323833` 像素数 = 0** ✅（声明归零，实测归零）
- **`pctInWorld16` = 98.06%**（QA 实测，与 Chief 报的 98.06% 一致；UIUX 文档记的 98.19%
  差 0.13pp，属采样差异，文档自己也记了这笔）
- **grass 基色精确像素 = 37.61%**（声明 37.15%，截图口径吻合；QA 按格数算是 44.00%——
  两个口径差异来自「格数」含被道路/建筑/边线覆盖的部分，「截图精确基色」只数未被覆盖的，
  不是矛盾）
- 剩余非 16 色像素 top：`#644a3d` 0.33%、`#232b21` 0.17%、`#524737` 0.15%、
  `#d5963d` 0.13%——均为角色/建筑/小径/阴影的 α 合成与非整数缩放的抗锯齿，非地形细节

**返工后边色对比度全表（QA 复算，ΔL* vs 本格底色）**：

| 材质 | 亮边 | ΔL* | 暗边 | ΔL* |
|---|---|---|---|---|
| tree | grassLit | 14.31 | ink | 6.76 |
| grass | **plaza**（原 grassLit） | **9.51**（原 3.02） | soil | 11.29 |
| farm | plaza | 10.82 | soil | 9.98 |
| stone | plaza | 6.72 | soil | 14.08 |
| plaza | （永不画边，死分支值 trim/stone） | — | — | — |

**全部边 ΔL* ≥ 6.72，隐形边 0**——P0-2 原「有条件通过」的条件（1× 可见性弱）至此解决。
WCAG 比值在暗端仍只有 1.39–1.53，但 ΔL* 才是 1px 像素细节可辨性的正确度量（WCAG 为文本
对比设计），两个度量不冲突。

**两处小差异（记录，不影响判定）**：
1. **grass 亮边数**：QA 数 **20 条**（12 北 + 8 西，无格同时有两者），commit 写 18 条。
   差 2 条，修复覆盖全部亮边，不影响有效性
2. **色相**：QA 用 HSV 算 soil 130.0° / grass 124.3°，声明写 148.5 / 145.1（应为 OkLCH
   口径）。两种空间下结论一致：同色相族

**`grassLit/stone = 1.008:1` 发现的佐证价值**：QA 在 P0-2 初轮复验时独立算出这个数
（亮边画在石材上几乎不可见），与 `36f6a4c` 修的 `BRIGHT.grass` token 选错是**同一根因的
两个度量视角**（QA 的 WCAG 比值 vs 美术的 ΔL*）。这条发现已由返工本身证实。

**P0-2 结论更新**：由「有条件通过」升级为 **通过**——原条件（边线 1× 可见性弱）已被
`36f6a4c` 解决（全部边 ΔL* ≥ 6.72、隐形边 0、`#323833` 归零）。剩余已知限制：小径上缘
trim α0.55 高光未改（ΔL* 21.31，本就可辨；保留原因是 16 色内没有「比 plaza 亮又不读成
白线」的替代色，待美术裁决），不影响地形边判定。

**P0-4 结论**：**通过**——α 合成色四个声明数全部独立复算吻合，`#323833` 截图实测归零，
`pctInWorld16` 98.06% 实测吻合。

> **基线注记**：本节全部测量基于 HEAD `f84da49`。其后施工正在改 `decor.ts` 的小径 α
> （工作区未提交），小径相关数值（trim 高光、板缝、描边的 ΔL* 与像素占比）会随之变化；
> 重测小径相关项时须以新 HEAD 为准，本节数字仅对 `f84da49` 成立。

### 11.1 草簇换 soil 的连通性实测（施工补，2026-10-21，HEAD `b8e40d2`）

QA 在 §11 末尾记了「P0-4 的像素级统计为代码级核实，Node 光栅化被 phaser 在 Node 下缺可选
依赖 `phaser3spectorjs` 阻塞，未独立复现」。**这条阻塞可以绕开**：把 `tilemap.ts` 的绘制
函数源码抽取成独立 TS 文件、用 `import type Phaser`（类型会被 Node 的类型擦除丢弃）+
一个只实现 `fillStyle/fillRect` 的假 Graphics 对象，就能在 Node 里跑**真实实现**而不需要
Phaser 运行时。本节数据即用此法得到，不是手抄重写。

**为什么需要单独验**：`TERRAIN_SPECK.grass` 从 `grassLit` 改为 `soil` 后，soil 在画布上的
占比从 18.01% 涨到 22.31%（+4.3pp）。涨的这 4.3pp 会不会让草地读成「脏」，取决于那些
soil 像像素是**散点**还是**连片**——占比本身说明不了。

**测量口径（关键，容易做错两处）**：
1. 只渲染「底色 + 纹理」，**不画边**。grass 的暗边也是 `soil`，画进来会被 4 邻接连成大片，
   把「纹理散点」误判成「连片脏斑」。第一版正是踩了这个坑。
2. 只统计 **grass 格内**且颜色等于 `TERRAIN_SPECK.grass` 的像素，排除树篱底色（tree 的
   底色也是 soil）与其它材质的暗边。

**实测结果（30×20 全图，264 个 grass 格 = 67584 px）**：

| 指标 | 值 |
|---|---|
| 纹理像素 | **1580 px = 2.34% 面积** |
| 连通分量数 | **389 个** |
| 分量大小中位数 | **4 px** |
| 分量大小平均 | 4.06 px |
| 最大分量 | **8 px** |
| ≥16px 的块 | **0 个** |

**分量大小分布**：

| 大小 | 个数 | 占比 |
|---|---:|---:|
| 4px（单簇 2×2） | 382 | **98.2%** |
| 5–7px | 4 | 1.0% |
| 8px（两簇相邻） | 3 | 0.8% |
| 9–15px | 0 | 0% |
| 16px+ | **0** | **0%** |

**判读**：单簇 = 2×2 = 4px。98.2% 的分量正好是 4px，**16px 以上一个都没有**——没有连片，
是标准散点。每格簇数分布也符合 §1.4 的「1–2 簇/格」设计：1 簇 ×136 格、2 簇 ×128 格。

**结论**：soil 占比 +4.3pp 分散在 389 个独立小块里，**不构成「地面变脏」**。美术担心的
「单格局部对比从 +3.0 翻成 −11.3」是真的（暗斑比亮簇更显眼），但它是散点、不是色块，
视觉读作「草地上的暗斑」。**P0-4b 的草簇换色在连通性维度上成立。**
`pctInWorld16` 98.06% 实测吻合。
`pctInWorld16` 98.06% 实测吻合。P0-4 的像素级统计（农田垄线位置、石板缝、簇数、贴边
装饰）为代码级核实（Node 光栅化被 phaser 在 Node 下缺可选依赖 phaser3spectorjs 阻塞，
未独立复现，标注为施工自报）。

---

## 12. L794 pageerror 等价：实现规格（待收口前统一补）

**背景**：原 `scripts/smoke.mjs` 的 L780/L794 用 Playwright 的 `pageerror` 事件流核验
「页面加载无 JS 报错 / 全程无 JS 报错」。ego-browser 不提供该事件流，当前以 3 项 MAN
单列记账（间接覆盖：Oracle 可调用 + 套件跑完 + 清档/reload 不崩）。该覆盖抓不住**非致命**
未捕获异常（rAF 回调抛错被 Phaser 吞掉之类）。

**方案**：ego 的 CDP 通道已通（rAF 替身就是走 `Page.addScriptToEvaluateOnNewDocument`
注入的），同一 session 上补 `Runtime` 域即可拿到真正的 pageerror 等价——且比 Playwright 的
pageerror 更全（覆盖未捕获异常含 Promise rejection）。

**实现规格**（落在 `smoke-ego.mjs` 或抽出的共用 helper）：

1. CDP session attach 后（与 rAF 注入同一 session），调用 `Runtime.enable`；
2. 监听 `Runtime.exceptionThrown`，把每条事件的 `{ timestamp, exceptionDetails.text,
   url, lineNumber, columnNumber }` 收集进数组 `pageErrors`；
3. （可选加强）同时监听 `Runtime.consoleAPICalled`，筛 `type === "error"` 的条目进
   `consoleErrors`——console.error 不是 pageerror，但作为附加信号单独记账、不混入断言；
4. **断言**：套件结束时 `assert.equal(pageErrors.length, 0)`——这就是 L794 的等价；
   L780 保留现有 S-L780 等价（Oracle 可调用 + canvas/overlay 挂载），exceptionThrown
   额外覆盖加载窗口期；
5. **通过条件**：全程 0 条未捕获异常。consoleErrors 只记账不断言（避免把既有 warning
   级输出误判为失败）；
6. **自验（变异探针，必须做）**：实现后临时在页面脚本里插一句 `throw new Error('qa-probe')`，
   确认 `pageErrors` 收到 1 条、断言变红；拔掉探针恢复绿。没有这一步就不能证明监听真的通；
7. **边界**：`Runtime.enable` 须在 goto 之前或之后立刻调用（事件不回溯）；若 smoke 中途
   reload 页面（S-L767 清档后 reload），需确认 session 保持、事件继续收集。

**验收**：实现 + 自验通过后，3 项 MAN 里的 L780/L794 两条从「不可自动化」升级为「已自动化」，
L548（元素级截图）那条保留（ego 无 locator.screenshot，已降级整页截图且断言 L544 仍执行）。

---

## 13. P0-3 depth 回归测试的复验口径（`building-depth.test.ts` 落地后执行）

**背景**：`7eade0a`（建筑三层深度）没有配套提交测试，遮挡行为无回归保护——这是 QA 在
P0-3 复验时提的条件。施工已在补 `building-depth.ts` + `building-depth.test.ts`（工作区
WIP，未提交）。本节是落地后的独立复验口径，**现在写好，提交后直接执行**。

**测试应断言的不变量（强形式，不是锁常量）**：

1. 深度序：`back(−2) < body(0) < 站门口角色(+24) < front(+32) < 门口下一格角色(+40)`；
2. 角色深度公式与 `villager.ts:60` **同一把尺子**：`ALTITUDE + py + 8`，
   `py = (pos.y+1)×TILE_PX`——测试里若复写公式，必须注释链回源码行号（防副本漂移）；
3. `baseY = py + h = door.y×16` 的推导前提（prefabs 四栋 `door.y == footprint.h`）；
4. 台阶归 body 层（不归 front）的遮挡方向。

**弱形式识别**：只写 `assert.equal(BUILDING_DEPTH.front, 32)` 锁常量值的测试**不算**——
它锁的是数字不是不变量，改公式时不会红。见到这种形式记为 P3 并要求改不变量形式。

**变异探针协议（落地后由 QA 执行，逐个改→跑→还原）**：

| # | 变异 | 预期 | 验什么 |
|---|---|---|---|
| M1 | `BUILDING_DEPTH.front` 32 → 12（旧规范值） | **必须红** | 挡住「檐压不住任何人」的回退 |
| M2 | `BUILDING_DEPTH.front` 32 → 45 | **必须红** | 挡住「檐压到门口下一格的人」 |
| M3 | `BUILDING_DEPTH.back` −2 → +5（高于 body） | **必须红** | 挡住地面层顺序颠倒 |
| M4 | 角色公式偏移 +8 → +0（若测试复写了公式） | **必须红** | 挡住「两把尺子」漂移 |
| M5 | 台阶从 body 移到 front（若可参数化） | **必须红** | 挡住台阶横穿角色身体 |

每个变异：改 → `npm run test:inprocess` 确认目标测试红（记下红的用例名）→ 还原 → 确认
全绿。M1/M2 是核心（这两个不红就说明测试没锁住遮挡语义）；M3–M5 视测试实现可达性。

**通过条件**：M1、M2 至少两条探针能让目标测试变红，且还原后 206+ 条全绿。若测试只锁
常量、探针不改数字就无法触发，判 P3 并要求补不变量断言。

---

## 14. 待办总账（2026-10-20 晚）

1. **L794**：按 §12 规格实现 + 自验，收口前统一补（Chief 已采纳）；
2. **P0-3 depth 测试**：施工 WIP 中（`building-depth.ts` + `building-depth.test.ts`），
   落地后按 §13 执行变异探针复验；
3. **P0-3 运行时遮挡**：Chief 补采「交付态站门口」截图（当前 p0-3 那张角色在屋里
   `visible=false`，看不到檐压人），落地后 QA 做像素级判定（檐色是否压在角色头顶）；
4. **A1 三问 + A2 人眼判定**：五步都没跑，需人工观察者排期；
5. **S5 世界端 e2e**：服务端已落地（71 测试 + 12/12 live probe 绿），缺「真实派单 →
   像素世界角色去干活 → 台账回执」一环，合同标「待 QA」——QA 出验证协议，Chief 代跑
   浏览器部分；
6. **小径 trim 高光**：ΔL* 21.31 本就可辨，保留待美术裁决（16 色内无「比 plaza 亮又不
   读成白线」的替代色）；
7. **源码冻结**：施工正在改 `buildings.ts`/`decor.ts`/`smoke-ego.mjs`，冻结前 QA 不跑
   浏览器类验证（HMR 重载会冲掉 `window.Oracle`）。

---

## 15. P0-3 depth 回归测试变异探针 + 小径 α 返工终检（`14e7847`，2026-10-20 晚）

**新基线（QA 实跑）**：HEAD `14e7847` + `9598b05`、工作区干净；`test:inprocess`
**214/214**（206 + 新增 8 条 depth 测试）；`audit --strict` 世界 16（引用 **110**）/ UI 8 /
并集 23 / 越界 0，exit 0；`python scripts/test_pixel_world.py` **PASS**。

### 15.1 depth 测试质量评估（先于探针）

`building-depth.test.ts` 8 条断言的是**不变量**不是锁常量：三层序、front 落在
`(baseY+24, baseY+40)` 开区间（`front > 站门口角色 && front < 下一格角色`）、+12 历史结论、
楼体低于站门口的人、地面层永远最低。算术抽成 `building-depth.ts` 纯函数、`buildings.ts`
改为调用——测试与实现共用同一套算式，无平行公式漂移风险。**这是 §13 要的强形式。**

### 15.2 变异探针结果（QA 自己执行，逐个改→跑→`git checkout` 还原）

| 探针 | 变异 | 结果 | 变红的用例 |
|---|---|---|---|
| M1 | `front` 32→12（旧规范值） | **2 条红** ✅ | 前檐压住站门口的人（开区间）· 区间常量自洽 |
| M2 | `front` 32→45（压到下一格） | **2 条红** ✅ | 同上两条 |
| M3 | `back` −2→+5（地面层顺序颠倒） | **3 条红** ✅ | 三层偏移 · 楼体低于站门口的人 · 地面层永远最低 |
| M4 | `ACTOR_DEPTH_OFFSET` 8→0（尺子漂移） | **2 条红** ✅ | 角色偏移与 villager.ts 一致 · 前檐开区间 |

还原后 **214/214 全绿**，`building-depth.ts` 与探针前**逐字节一致**（`git checkout` 还原，
已比对）。M1/M2 证明测试锁住了遮挡语义的两个方向；M4 证明「同一把尺子」锁有效——改角色
偏移会同时打断开区间断言，即测试用的是真公式不是副本。**P0-3 的「无回归保护」条件关闭。**

### 15.3 小径 α 返工七个数终检（QA 独立复算）

| 项 | QA 复算 | 声明 | 判定 |
|---|---|---|---|
| 上缘高光 trim α0.55 / plaza（已删） | #908058，ΔL* **21.31** | #908058，21.3 | ✅ |
| 实心 plaza / plaza（假想改法） | ΔL* **0.00** | 0.0 | ✅ |
| 板缝 stone α0.6 / plaza（旧） | #40453e，ΔL* **4.16** | #40453e，4.2 | ✅ |
| 板缝实心 stone / plaza（新） | ΔL* **6.72** | 6.7 | ✅ |
| 描边 soil α0.85 / plaza（保持） | ΔL* **17.45** | 17.4 | ✅ |
| 描边实心 soil / plaza（假想） | ΔL* **20.80** | 20.8 | ✅ |
| 墨线基准 ink / grass | ΔL* **18.05** | 18.1 | ✅ |

返工决策链独立核实：trim 高光 21.31 是墨线基准（18.05）的 1.18 倍 → 读成白线；实心 plaza
对 plaza 是 0.0 → 隐形线；16 色无中间档 → 删除（与广场板面高光同处理）。**QA 初轮判断方向正确（trim 21.31
过亮会读成白线）但结论写错——实际落地是「删除」而非「改成 plaza」，理由是 plaza 画在 plaza 上
等于隐形（ΔL* 0.0），两种改法都不对，删掉比留一条看不见的线好。**
对 plaza 是 0.0 → 隐形线；16 色内无中间档 → 删除（与广场板面高光同处理）。板缝 4.16 →
6.72 落入可读区间。描边 17.45 已达标、实心会到 20.80 超基准 → 保持。**逻辑自洽。**

### 15.4 P0-3 结论更新

原「有条件通过」的两个条件：① 无提交测试——**已关闭**（`14e7847` 补了 8 条不变量测试，
QA 四条变异探针全部按预期变红）；② 运行时遮挡未独立复验——**仍开**（Chief 补采「交付态
站门口」截图后 QA 做像素级判定：檐色是否压在角色头顶；当前 p0-3 那张角色在屋里
`visible=false`，看不到檐压人）。静态算术三前提（villager 公式、prefabs door.y==h、
baseY 定义）此前已全部独立核实。

---

## 16. 当前总账（HEAD `14e7847`）

| 批次 | 提交 | 缺陷等级 | 结论 |
|---|---|---|---|
| P0-1 删网格线 | `260ad93` | P2 | **通过** |
| P0-2 地形拼接边 | `899611d`+`e513a11`+`36f6a4c` | P2 | **通过**（原有条件通过，条件已被 36f6a4c 解决） |
| P0-3 建筑三层深度 | `7eade0a`+`14e7847` | P2 | **有条件通过**（depth 测试已补并验有效；运行时遮挡待 Chief 补采截图） |
| P0-4 地面材质结构 | `7514246`+`36f6a4c`+`14e7847` | P2 | **通过**（α 合成色全部复算吻合、#323833 归零、小径返工七数终检通过） |
| P1-1 道具 Y-Sort | `c496e7d` | P2 | **通过** |

门禁：check 0 / 214 单测 / audit 16-8-23 越界 0 / strict exit 0 / 整门禁 PASS。
共性未验项：A1 三问 + A2 人眼判定（需人工观察者）、smoke 独立复跑（Chief 代跑）、
S5 世界端 e2e（服务端已绿，世界端待 QA 出协议 + Chief 代跑）。

---

## 17. S5 世界端真帧 e2e 验证协议（QA 独立复验，浏览器部分由 Chief 代跑）

**背景**：`docs/PIXEL_WORLD_S5_CONTRACT.md`（master，`fdacd41`）状态为「服务端已落地；
世界端真帧 e2e 待 QA」，且明确「不接受文档说通了」。服务端已由 71 项 bridge 测试 +
`probe_world_live.py` 12/12 覆盖：派单句选中 `world.dispatch` 并停在确认门、批准前台账
无新单、批准后恰好一单、普通请求不误选。**唯一没验的是像素世界这一端**：台账落单之后，
世界里的角色是否真的去干活、干完台账是否收到回执。服务端回执里 `world_visible="unknown"`
——服务端无法确认浏览器世界在线，这个洞只能由 e2e 补。

**范围（合同 e2e 行原文）**：`?ledger=on`：派单可见 → blocked 可见 → 完成/撤单 → 重连；
证据 = 录屏 + 日志 + 断言。

### 17.1 环境

1. **隔离服务端实例**：独立端口 + 临时 data 目录（沿用 S4 live 遍的隔离模式，如 8001 +
   临时库），**不碰用户 8000**；
2. **像素世界**：dev server 起好，页面**不带 `?ledger=off`**（`LedgerClient` 连隔离实例）；
   台账地址指向隔离实例（沿用 S4 live 遍的接线方式）；
3. **真实模型**：隔离实例需 `LLM_API_KEY`（与 12/12 live 探针同一前提——那次已用真实
   模型跑通，说明取钥路径存在）。若隔离实例取不到 Key，退路是用户批准对 8000 实机跑
   （会写用户自己的台账）——**这是用户/Chief 的决策，不是 QA 的**；
4. **取证工具链**：rAF 注入（MessageChannel 替身，门控 60fps）+ 截图前
   `Browser.setWindowBounds` 恢复最大化 + CDP `Page.captureScreenshot({clip})` 精确裁 canvas。

### 17.2 要验的链路（端到端）

1. 真实模型收到派单句 → 选中 `world.dispatch` → 停在 `waiting_confirmation`（服务端侧，
   12/10 探针已覆盖，e2e 复现一遍留证）；
2. 老板批准 → 单落台账（服务端侧已覆盖）；
3. **世界收到**：WS 推 `commission_update` → 仿真入队 → 快照出现该单
   （状态行文案、角色 mode → toWork、taskId）；
4. **角色真的动**：走向对应铺子（位置收敛到工位；「进屋后人不在门外」）；铺子开工
   （恰好一台在转、且是匹配任务种类的那台）；
5. **干完交付**：角色重新出现在门口 → 回祭坛；台账 REST 显示 done；世界的 finished
   清单含该单；
6. `world.status(commission_id)` 回公开字段且与台账一致；
7. **负例**：普通提醒请求 → 零状态变化（快照 diff 除对话回复外为 0，无角色移动、无铺子开工）；
8. **重连**：任务中途杀服务端 → 世界照常过日子（状态行标离线、不崩）；重启 → 重连并对账
   （不重复派单）。客户端侧重连逻辑已有单测，e2e 验的是真实链路下的表现。

### 17.3 断言清单（世界端部分——只有 e2e 能证明的）

| # | 断言 | 取证方式 |
|---|---|---|
| A | 批准后 N 秒内世界快照出现该单（mode=toWork + taskId + 状态行） | JSONL 快照时间线，记实际延迟 |
| B | 角色位置收敛到目标铺子工位；「进屋后人不在门外」 | 快照坐标序列 + 截图 |
 | C | 恰好一台铺子运转，且是匹配任务种类的那台 | 快照 buildings + 截图 |
| D | 完成时角色重新出现→回祭坛；台账 REST done；finished 含该单 | REST before/after + 快照 |
| E | `world.status(commission_id)` 公开字段与台账一致 | 工具回执 vs REST 对照 |
| F | 负例：普通请求零状态变化 | 前后快照 diff |
| G | 杀/重启服务端：不崩、状态行离线、重连对账不重复 | 快照 + 日志 |
| H | 并发：两单在飞时第三单派不出去（busy 回执），世界侧只显示两单活跃 | 回执 + 快照 |

### 17.4 证据与通过条件

**证据**：世界快照 JSONL 时间线（mode/位置/状态行，带时间戳）、台账 REST before/after、
WS 帧日志（收到的 `commission_update`）、关键节点截图（派单后走路中 / 屋内开工 / 门口
交付 / 完成后回祭坛）、各阶段实际耗时。

**通过条件**：A–G 全部成立且有留证；H 若跑了并发场景也要成立。任一失败 → 按 §6 出缺陷单
（等级/复现步骤/预期/实际/验收依据）。**QA 独立判定，不接受「文档说通了」。**

### 17.5 已知限制（跑之前就要写进报告）

- 服务端回执 `world_visible="unknown"`：服务端无法确认浏览器世界在线——e2e 就是补这个洞；
- 需要真实模型（有 API 费用），与 12/10 live 探针同一前提；
- 隔离实例取不到 Key 时的退路（用户批准对 8000 实机跑）是用户/Chief 的决策；
- 合同 §2.2：`world.status` 需要 `commission_id`，S5 不提供全量列表——查询类话语由模型
  基于上下文回答，e2e 不验「全量列表」这种不存在的面。

---

---

## 18. stone 明度重排（`b56d312`）独立复验 + 边可见性回归锁评估（2026-10-20 晚）

**基线（QA 实跑，HEAD `b56d312`）**：`npm run check` exit 0；`audit --strict` 世界 16
（引用 **110**）/ UI 8（引用 **51**）/ 并集 23 / 越界 0，exit 0。

**门禁状态要分清两层**：
- **提交态**（`b56d312` 本身）：214/214 绿（commit 自述；217 − 3 条未提交测试 = 214，算术一致）；
- **含工作区未提交改动**：217 条，**1 条红**——`not ok 173 - 亮边色对本格底色 ΔL* ≥ 6
  （边可见性回归锁）`，报 `stone 亮边 ΔL*=4.95`。该测试是 Chief 未提交的工作区改动
  （`git status` 显示 `M test/terrain-edges.test.ts`，git log 无此提交），共 3 条：亮边锁、
  暗边锁、历史值变异探针。**红的是这把新锁在准确报 stone 改色的副作用，不是既有回归。**

### 18.1 边色矩阵按新 stone 独立复算

新 stone `#3e433f` luma **65.65**（原 `#3a3f3b` = 61.65），QA 独立复算与 Chief 的数一致。
**但 Chief 表里有两行标错，实际受影响的面比表里窄**：边只画在**高材质一侧**，stone
（高度 1）唯一的低邻格是 plaza（0），所以 stone 画边的位置**只有 stone/plaza 交界**；
grass/farm/tree 的边都画在**它们自己的底色**上，与 stone 值无关。改色只影响 stone 格
自己的两条边：

| 实际受影响的边 | 边色 | 改前 ΔL* | 改后 ΔL* |
|---|---|---:|---:|
| stone 亮边（plaza 画在 stone 上，仅 stone 邻 plaza 时出现） | plaza | 6.72 | **4.95** |
| stone 暗边（soil 画在 stone 上） | soil | 14.08 | **15.85** |

不受影响的边（底色没变，QA 复算确认）：tree 亮 14.31 / 暗 6.76；grass 亮 9.51 / 暗 11.29；
farm 亮 10.82 / 暗 9.98。Chief 表中「grass>stone 亮边」「farm>stone 亮边」两行的 4.95 是
**标错**——那两条边画在 grass/farm 底色上（plaza/grass 9.51、plaza/farm 10.82），与 stone
改色无关。

**底色差（luma）**：grass–stone 6.56 → **10.56**（主目标达成）；stone–plaza 15.35 →
**11.35**（降了，仍清楚）；stone–farm 9.39 → **13.39**。

### 18.2 渲染实测：stone 亮边结构上不可见（改色前后都是）

QA 用改色后的 `stoneA-canvas.png`（2160×1440 canvas clip）实测：

- 世界网格（Node 复算）：祭坛周围 stone 格共画 **6 条亮边**（(17,9)/(17,10) 西、(13-16,11)
  北）、6 条暗边；
- 全画布 stone/plaza 水平交界扫描 **269 个采样点，stone 侧贴边 4px 内 luma 抬升 >2 的
  **0 个**；
- 直接测量 (13,11) 北边缘：行 788-796 为 plaza（77.0），行 797+ 直接是 stone（65.6）——
  **亮边线与上方 plaza 同色，整条线融进 plaza 区**，视觉上只是 plaza 区厚了 1px 然后直接
  过渡到 stone；(17,9)/(17,10) 两条西亮边所在格被道路 plaza 面覆盖（该格是径格）；
- **结论：stone 亮边在改色前后都不可见**。原因是结构性的：stone 唯一的低邻格是 plaza，
  而 `BRIGHT.stone` 就是 plaza——线必然与相邻 plaza 同色相融。ΔL* 4.95 量的是「线 vs
  stone 底色」，但这条线在渲染里从不单独对着 stone 底色出现（它永远夹在同色的 plaza 与
  stone 之间）。改色真正改变的是 stone/plaza **底色差** 15.35 → 11.35（仍清楚），不是
  亮边的可见性。

**施工补测（2026-10-21，HEAD `b8e40d2`）：QA 上面的结论成立，这里把「同色相融」从定性
变成三个精确像素值。** 方法同上节：抽取真实 `drawTexture`/`drawTileEdge` 源码在 Node 里
光栅化，**画完边之后**采样（第一版在画边前采样读到的是底色，做错过一次）。

以 stone 格 (13,11) 北邻 plaza 为例，沿列取 y−1 / y / y+1 三个像素：

| 位置 | 实际像素 | 说明 |
|---|---|---|
| y−1（plaza 格底色） | `#4a4f42` | 邻格 |
| **y（亮边）** | **`#4a4f42`** | **与邻格底色逐字节相同** |
| y+1（stone 格底色） | `#3e433f` | 本格 |

算成 ΔL*：

- **亮边 vs 上邻（plaza 侧）= 0.00** —— 外侧完全看不出边界，不是「弱」而是「零」；
- 亮边 vs 本格底（stone 侧）= **4.95** —— 只有内侧有一道微亮。

也就是说 QA 说的「整条线融进 plaza 区」在像素上可以精确表述为：**这条亮边占用 1px，
但它和外侧邻格是同一个值，唯一的作用是把 stone 的内缘提亮 4.95**。它从来不是材质边界，
只是高材质格内缘的装饰性提亮。

顺带解释为什么 §18.3 的选项 D（plaza 上调 #4e5345）不解决问题：改 plaza 会让亮边线跟着
plaza 一起变亮，线仍然等于邻格颜色，**外侧 ΔL* 仍然 0.00**，4.95→6.63 变的只是那圈内侧
提亮的强度。而 plaza 是全局共享 token，会连带改动 `BRIGHT.grass`（9.51→11.19）、
`BRIGHT.farm`（10.82→12.50）、祭坛地面、小径路面、石地抖动色（各 +1.68 L*）——**是全局
视觉变化，不是修一条边**。这与 QA 的「选项 D：指标过关但感知不变」结论一致，此处提供
量化依据。

**因此 §18.3 的选项 B（结构豁免 + 文档化原因）是唯一不引入新问题的选择。**

### 18.3 边可见性回归锁评估

Chief 未提交的 3 条测试质量高：ΔL* 口径（附「为什么不用 WCAG」的实测依据）、阈值 6 有
经验基础（角色墨线 18.1 为强轮廓参照，6 以下实测不可辨）、plaza 豁免、自带历史值变异
探针、直接读导出的表防副本脱钩。**这正是 QA 指出的缺口，值得保留。**

**但对 stone 亮边这一条，指标与感知错位**（见 18.2）：线融进 plaza，4.95 这个数不对应
感知退化（改前 6.72 时同样不可见）。四个选项：

| 选项 | 做法 | QA 评估 |
|---|---|---|
| A | 阈值降到 4 | ✗ 削弱对 grass/farm/tree 的保护（那三条边的线 vs 底色是真实可见面） |
| B | stone 亮边豁免（文档化结构原因） | ✓ **推荐**：线面对 plaza 必融，边界由 11.35 底色差 + 15.85 暗边承担 |
| C | 换 `BRIGHT.stone` 为非 plaza 亮 token | △ 需美术定：timber ΔL* 15.12 但色相是木、trim 42.66 有白线风险（小径高光的教训） |
| D | plaza 上调到 #4e5345（亮边回 6.63） | ✗ **指标过关但感知不变**：线仍与 plaza 同色相融，且改 plaza 是全局调色板变动、要用户批准 |

**QA 推荐 B**：保留 ≥6 阈值（对 grass/farm/tree 亮边与全部暗边有效），stone 亮边加结构化
豁免并写明原因。若美术认为 stone 需要可见亮边，走 C 且须过用户批准（换 token = 调色板
变动）。

### 18.4 P0-2 结论

**维持通过**。改色解决了主问题（grass/stone 不再糊：底色差 6.56 → 10.56；众数带
67.01% → 45.46%、IQR 9.39 → 13.39 为 Chief CDP 实测，QA 未独立复跑像素统计——CDP clip
是 Chief 的通道）。stone 亮边 4.95 不构成感知退化（18.2 已证结构性不可见，改前同样）。
剩余动作：边可见性锁的 stone 豁免（选项 B）由 Chief/施工落；落前门禁带着这条红不影响
提交态（提交态 214/214 绿）。

---

---

## 19. 边可见性锁（`185d7cf`）独立复验 + P0-3 升级（2026-10-20 晚）

**新基线（QA 实跑，HEAD `185d7cf`）**：`npm run check` exit 0；`test:inprocess`
**218/218**；`audit --strict` 世界 16（引用 110）/ UI 8（引用 51）/ 并集 23 / 越界 0，
exit 0；`python scripts/test_pixel_world.py` **PASS**。

### 19.1 边可见性锁复验（§18 推荐的选项 B 已落地）

`185d7cf` 把 §18 推荐的**选项 B** 落地了：亮边 ≥6 断言覆盖 tree/grass/farm，stone 与
plaza 豁免；暗边 ≥6 覆盖 tree/grass/farm/stone；豁免理由引用了 QA 的 269 采样实测。
Chief 额外加的「豁免的结构前提」测试（三条事实合取：stone 低邻格只有 plaza /
`BRIGHT.stone` 等于 plaza 底色 / `TERRAIN_COLORS.plaza` 等于 `WORLD.plaza`）正是防豁免
默默失效的那层——**QA 评估：覆盖了 §18 指出的缺口，实现与推荐一致。**

**QA 对结构前提测试做 4 条变异探针（逐个改→跑→`git checkout` 还原）**：

| 探针 | 变异 | 结果 | 变红的用例 |
|---|---|---|---|
| P1 | `BRIGHT.stone` plaza→trim（换 token） | **1 条红** ✅ | stone 亮边豁免的结构前提 |
| P2 | `TERRAIN_COLORS.plaza`→stone（改 plaza 值） | **2 条红** ✅ | 结构前提 + 暗边不能与本格底色相同 |
| P3 | `TERRAIN_HEIGHT.plaza` 0→2（调高度序） | **3 条红** ✅ | 结构前提 + 材质高度序 + plaza 永不画边 |
| P4 | `BRIGHT.grass` plaza→grassLit（历史退化） | **1 条红** ✅ | 亮边色对本格底色 ΔL* ≥ 6 |

还原后 **218/218 全绿**，两个源文件与探针前**逐字节一致**。三条豁免前提各自被对应改动
精准触发（换 token 触发②、改 plaza 值触发③、调高度序触发①），历史退化值也被亮边锁
抓住——**这把锁真能挡住「改色/换 token/调高度序」三类静默退化。**

### 19.2 P0-3 深度算术的一处绝对值更正

Chief 的深度验算结论（`front ∈ (站门口角色, 下一格角色)` 开区间）**正确且与 ALTITUDE
无关**（ALT 在比较中抵消）。但引用的绝对值用了 ALT=400，**实际 `ALTITUDE = 1000`**
（`SandboxScene.ts:41`，`updateBuilding`/`updateVillager` 都传它）。正确绝对值：

```
baseY = 5*16 + 64 = 144
front            = 1000 + 144 + 32 = 1176
站门口角色      = 1000 + 160 + 8  = 1168   （交付态实测 pos.y=9 正是这档）
门口下 1 格角色 = 1000 + 176 + 8  = 1184
1176 > 1168（压住）且 1176 < 1184（不误压）✓
```

区间关系与遮挡判定不受影响；记录更正以备后引。

### 19.3 P0-3 结论升级：有条件通过 → **通过**

四层证据链齐全：
1. **静态**：深度算术三前提 QA 独立核实（villager 公式、prefabs 四栋 door.y==h、
   baseY 定义）；
2. **测试**：`14e7847` 的 8 条不变量回归测试；
3. **变异探针**：QA 跑的 4 条（front→12/45、back→+5、actor 偏移→0）全部按预期变红；
4. **运行时**：Chief 补采的交付态截图（`docs/screens-p0/p0-3-交付态站门口被檐压住.png`），
   带 mode 状态序列（saluting→toWork→working→delivering，轮询 `Oracle.view()` 在
   `working→delivering` 切换瞬间 CDP clip），图中前檐压住角色头顶；depth 值验算见 19.2。

### 19.4 总账更新（HEAD `185d7cf`）

| 批次 | 结论 |
|---|---|
| P0-1 删网格线 | **通过** |
| P0-2 地形拼接边 | **通过** |
| P0-3 建筑三层深度 | **通过**（四层证据链齐全） |
| P0-4 地面材质结构 | **通过** |
| P1-1 道具 Y-Sort | **通过** |

**视觉五步全部通过。** 门禁：check 0 / 218 单测 / audit 16-8-23 越界 0 / strict 0 /
整门禁 PASS。共性未验项不变：A1 三问 + A2 人眼判定（需人工观察者）、smoke 独立复跑
（Chief 代跑）、S5 世界端 e2e（§17 协议已就绪，卡在执行环境：需 `LLM_API_KEY` 或用户
批准对 8010 实机跑——用户的决策）。

---

---

## 20. S5 世界端真帧 e2e 独立复验（2026-10-20 晚，QA 读证据文件独立判定）

**证据来源**：`pixel-world/eval/screens/` 下 `s5-e2e-evidence.json`（round-04 主链路：
600 帧世界快照时间线 + WS 帧 + 台账 REST before/after）、`s5-e2e-evidence-fg.json`
（round-05 负例 F + 杀重启 G）、`s5-e2e-r3/r4/r5.out.txt`、`s5-e2e-env.json`、
`s5-walking.png` / `s5-working.png`。**QA 逐条读证据独立判定，不采信 Chief 总结。**

**环境独立核实**：8001 活着且 health `offline=false`（真实 step-3.7-flash）；
页面 `?ledger=on`；隔离临时根 + 5189 代理（8000/8010 未碰）。

### 20.1 逐条判定

| 断言 | 判定 | QA 独立证据 |
|---|---|---|
| A 批准后世界快照出现该单 | **通过** | 时间线 t+28.1s 首现任务（status=assigned），final.tasks[0]：id=c_e6f7400b6e8f、source=**ledger**、kind=code、buildingId=code_forge#1、workerId=v1；ledger.connected=true / mock=**false** / reconciled=true——走的是真台账路不是 Mock 路 |
| B 角色位置收敛到工位 | **通过（附缺陷 D-1）** | round-03：villagers[0] 走 toWork→working（sawWork=true，walking/working 两张截图在案）；round-04：该单派给 v1 而驱动只采样 villagers[0]（采样口径所限），间接证据为 code_forge#1 转 operating 且带该 taskId。**但 round-04 暴露出 D-1（见 20.2）** |
| C 恰好一台铺子运转且种类匹配 | **通过** | 时间线铺子状态迁移：t+29.6s code_forge#1 idle→operating（taskId=c_e6f7400b6e8f），star_archive#1 与 ink_house#1 全程 idle；boundTask=code 与任务 kind=code 匹配 |
| blocked 可见 | **通过** | final.tasks[0].blockedQuestion 与台账 blocked_question **逐字一致**（simulation.ts:414-425 的透传设计）；世界 task.status=in_progress 是世界自己的状态机口径，问题本身已可见——满足 §17「blocked 可见」 |
| D 完成/交付 | **未达成（根因在世界之外，已验证）** | 台账 status=blocked、deliveries.ledger=0。根因独立核实：**127.0.0.1:15722（网关）OPEN、127.0.0.1:15721（上游）ECONNREFUSED**——网关健康但上游关闭，claude worker 因此 exit 1、单落 blocked。属用户主产品线，非像素世界缺陷 |
| E world.status | 未跑 | 需模型再选一次 world.status，可另起轮 |
| F 负例 | **通过** | r5 输出：「提醒我明天下午三点开会」选中 **reminder.create** 而非 world.dispatch；zeroChange=true（worldTasks 1→1、台账 1→1、mode idle→idle、pos (10,7) 不变、workstations 不变） |
| G 杀/重启重连 | **通过** | r5 输出：杀 8001 后 offlineSeen=true（connected=false 且世界照常过日子不崩）；同临时根重启后 reconnected=true、任务 id 前后均为 c_e6f7400b6e8f、台账仍 1 条、noDuplicate=true |
| H 并发 | 未跑 | — |

### 20.2 缺陷 D-1（P2）：reconcile 撤单不恢复干活角色的可见性

**现象**（e2e round-04 实测）：villagers[0] 全程 600 帧（314s）停在 (10,7)（code_forge
占地内）、mode=idle、**visible=false**、bubble=null、routine 标签在跑但位置零变化。

**根因（代码级确认）**：读档恢复了一个「在屋内干活」的角色（mode=working、
visible=false、pos=工位）；首次 sync 走 reconcile，该单在完整快照里缺席（轮次间 DB
被重置）→ `simulation.ts:1439-1440` 直接调 `removeTask` → `removeTask`（1482-1489）
只重置 mode/taskId/path，**不恢复 `visible`、不把角色移出建筑**。同一「单没了」场景
的 live-sync 路径走 `abandonActiveTask`（546-564），那条**有** `visible = true` 和气泡——
**两条路径对同一语义处理不一致，reconcile 这条漏了可见性恢复**。角色随后既不可见
（不渲染）又动不了（起点在不可走格内，寻路失败），且该状态入档跨会话持久（只有清档
能恢复）。

**触发条件（正常使用可达，非仅 e2e）**：页面关闭时角色在屋内干活 + 关闭期间该单从
台账消失（用户撤单 / 台账库更换）→ 重开读档 + 首次 sync 对账 → 卡死。

**预期**：按撤单语义恢复 `visible = true` 并把角色移出建筑（对齐 abandonActiveTask
的做法）。**实际**：mode=idle 但 visible=false、pos 留在屋内。

**等级 P2**（局部功能缺陷）：角色永久消失且状态持久，但主链路（派单→干活→交付）
不受影响（round-03 完整走通），触发条件窄。修复建议：reconcile 的缺席分支改走
abandonActiveTask 语义，或 removeTask 内对 working 态角色补 `visible = true` + 移到
门口。建议施工补一条「读档恢复干活角色 + 对账撤单」的回归测试。

### 20.3 两处口径判定（Chief 提请）

1. **round-04 sawWork=false**：B 应以全部角色为准。判定：**B 成立**——round-03 有直接
证据（villagers[0] 走 toWork→working + 截图），round-04 有间接证据（铺子带该单
operating）；采样只取 villagers[0] 是**测试工具的口径限制**（P3，建议驱动改采样
全部角色），不是世界缺陷。
2. **task.status=in_progress vs 台账 blocked**：判定**满足「blocked 可见」**——世界
透传展示 blockedQuestion（逐字一致），status 是世界自己的状态机字段，§17 要的是
「blocked 可见」而非「status 字面等于 blocked」。

### 20.4 e2e 总结论

**有条件通过**：A/B/C/F/G 与 blocked 可见全部独立验通过；D 未达成但根因在世界之外
（网关上游 15721 拒绝连接，已独立端口实测）；E/H 未跑。**附带产出 1 条 P2 缺陷
（D-1，reconcile 撤单不恢复可见性）+ 1 条 P3 工具建议（驱动采样全部角色）**。
已知限制：world_visible=unknown 的洞已由 A/B/C 补上；本轮有真实 API 费用。

---

---

## 21. S5 e2e 补跑 E/H 独立判定（2026-10-20 晚，round-06）

**证据**：`s5-e2e-evidence-eh.json` + `s5-e2e-r6.out.txt`（QA 逐字段独立核对，
不采信 Chief 判定）。

### 21.1 E（world.status）：**通过**

- 查询句「像素世界里的 c_e6f7400b6e8f 现在什么情况？」→ 模型选中 `world.status`
  （selectedWorldStatus=true）
- **回执与合同 §2.2 逐字段一致**：found=true / id=c_e6f7400b6e8f / status=blocked /
  agent_name=真机 Claude / blocked_question（与台账逐字一致）/ delivery_summary="" /
  updated_at=1792508340.4977021 / context_is_untrusted_data=true /
  grants_authorization=false——合同示例的九个字段全中，无多余字段
- **noPacketLeak 独立复核**：QA 自己 grep 证据原文——`packet` 0 次、`memory_block`
  0 次、`memory` 0 次（合同要求「不返回整行、不含 packet.memory_block」）
- **小缺口（P3）**：合同还要求「查无此单返回 `{found:false}` 不抛 KeyError」——
  found=false 分支本轮未跑（目标单存在）。该分支由服务端单测覆盖，e2e 未 exercised，
  记为已知小缺口，不影响 E 的通过判定。

### 21.2 H（并发 busy）：**环境阻塞未验**

**现象**：三路并行 WS 同时派单（各自独立连接、各自过确认门），三单全部 succeeded
（status=running、agent=笔录官），busyCount=0，台账 1→4 条。

**根因（QA 代码级独立确认，与 Chief 判断一致）**：
- `service.py:286-288`：`active = sum(1 for task in self._world_tasks if not task.done())`；
  `if active >= WORLD_DRIVE_CONCURRENCY(2): return busy`——**busy 判定数的是「未完成的
  drive 异步任务数」，不是单数**
- `agents_roster.yaml`：scribe 的 `adapter: manual`（注释明说「只接单、组密件，没有
  执行体」）→ drive 无事可做立即返回
- 唯一有执行体的真机 Claude 是 cli_subproc，但网关上游 15721 关闭（QA 端口实测
  ECONNREFUSED）→ worker 立刻失败 → drive 也立即结束
- 故每次判定时 `active` 都是 0，并发窗口打不开——**busy 路径一次都没触发**

**QA 台账现状独立核实**（直连隔离 8001 REST）：4 条全 blocked——
c_a3e490a89188 / c_82e1b24726aa / c_82323d258a8b（scribe）+ c_e6f7400b6e8f（claude），
与 Chief 所述一致，全在隔离临时库，未碰 8010。

**判定口径**：记**环境阻塞未验**，不记「有条件通过」——「有条件通过」意味着断言在某
条件下被验过，而 busy 路径本轮**完全没被触发**（busyCount=0 是「没走到」，不是
「走到且通过」）。诚实记录：busy 逻辑经代码读验正确（并发 2、数未完成 drive 任务），
但 e2e 这层缺「真实在飞」的前提。附带弱正面证据：三路并发派单无损坏（id 唯一、
台账 4 条无重复、无竞态错乱）。要真验 H 需把 15721 拉起来（一个能跑几分钟的
worker）——与 D 同一根因。

### 21.3 e2e 总状态更新

| 断言 | 状态 |
|---|---|
| A/B/C/F/G + blocked 可见 | **通过**（§20） |
| D 完成/交付 | 环境阻塞未验（根因在世界之外：15721 拒绝连接） |
| E world.status | **通过**（§21.1；found=false 分支小缺口 P3） |
| H 并发 busy | 环境阻塞未验（busy 路径未触发；根因同 D） |

**e2e 总结论维持「有条件通过」**：主链路（派单→世界动→状态查询→负例→重连）
全部独立验通过；D/H 两条卡在同一环境前提（网关上游 15721 关闭），非世界侧缺陷；
已产出 1 条 P2（D-1 reconcile 可见性）+ 2 条 P3（驱动采样全部角色、E 的 found=false
分支未跑）。

---

---

## 22. S5 e2e 网关补链后 D/H 重跑独立判定（2026-10-21 凌晨，round-07/08）

**背景**：Chief 在隔离环境自建网关（15725 直指 StepFun），把隔离临时副本的
`config/agents_roster.yaml` 的 `AQI_GATEWAY_URL` 从 15722 改到 15725，只改临时副本，
主仓库与用户 15722 未动。QA 独立读证据判定。

### 22.1 H（并发 busy）：**通过**（推翻「环境阻塞」判定）

**证据**：`s5-e2e-evidence-h2.json` + `s5-e2e-r8b.out.txt`。QA 逐项独立核对：

- 派单前台账 4 条，其中 **2 条 running**（c_f3fadee17928/claude、c_086bf315bade/dsh）
  —— drive 真实在飞（网关通了，worker 真在跑）
- 三路并行派单**全部被拒**：busyCount=3、okCount=0，回执逐字段匹配合同 §2.1 的 busy
  规格（status=busy / commission_id="" / agent_name="" / world_visible="unknown" /
  retryable=true / message「世界正在收尾两单，这单先不派，等一会儿再试。」）
- **台账 4→4 未增长**——busy 不建单
- 世界侧一致：code_forge#1:operating + ink_house#1:operating、v0:working + v1:working，
  恰好两个活跃单

**判定：通过**。busy 路径被真实触发 3 次且全部正确拒绝，并发限制（≥2 在飞即拒）
按设计执行。小注：互补情形（一个 drive 结束后窗口重开）本轮未 exercised，核心断言
已验。

### 22.2 D（完成/交付）：**未达成（断言未满足），根因已移出基础设施**

**证据**：`s5-e2e-evidence-d.json` + `s5-e2e-r7b.out.txt` + 隔离临时库
`aqi-s5-e2e-8B1hgf/data/commissions/c_b385572769e1-*`（3 个 attempt 目录，对应
retry=3）+ QA 直连 8001 拉取的完整单记录。

**世界侧链路在真实 worker 下完整走通（新证据，QA 逐帧核）**：
- t=20.3s 任务出现（status=assigned、source=ledger、kind=code），v1 转 saluting
- t=30.4s v1 转 working，code_forge#1 转 operating（代码活匹配代码工坊）
- 之后 408s 稳定：双角色 working、双工坊 operating

**worker 真干了活**：最新 attempt 的 `out/count_py_lines.py`（9056B）+ `out/README.md`
（2251B）+ 会话记录（jsonl 397KB）——不是空跑。

**打回原因（QA 从完整单记录独立取证）**：
- status=blocked、retry_count=3；blocked_question 与 audit.findings 逐字一致：
  「[major] prompt_token_bloat：单次调用 Prompt Tokens **421413** 超出上限 **120000**」
- audit.usage：calls=3、prompt_tokens=**1017073**、max_prompt_tokens_per_call=421413、
  total_budget_exceeded=**true**——单次超 3.5 倍、总量超 2.5 倍
- packet.rubrics 里 prompt_token_bloat 是该单的 major 级 l1 规则——**审计按预算规则
  正确打回，是产品行为按设计工作**

**判定口径**：记**「未达成（断言未满足）；根因 = 审计按 token 预算正确打回，属主产品
线的上下文控制（提示词装配/裁剪），非像素世界缺陷」**。世界侧部分已完整验证
（这本身是 D 的强证据：真实 worker 在飞时角色真的在干活）；交付腿要通需 worker 在
120K/次预算内完成——与基础设施无关，是 worker 侧的事。

### 22.3 隔离 roster 改 AQI_GATEWAY_URL：**认可为合法测试环境替换**

判定依据：
1. 与「DB_PATH 指向临时库」同一类——**环境端点替换**，只改隔离临时副本；
2. 主仓库与用户 15722 零改动（Chief 自述 + QA 未发现任何主仓改动）；
3. 世界侧断言不受影响：世界只消费台账 WS/REST 投影，网关替换改的是 drive 路径，
   不改世界看到的东西；H 验的 busy 逻辑是同一份代码路径；
4. e2e 的目的是世界端集成，不是用户网关链的健康——用户 15722→15721 当前上游关闭
   是用户侧基础设施状态，用可用的隔离网关替换正好把世界端行为与用户 infra 问题解耦。

**唯一要记的环境注记**：本轮 drive 路径走的是替换后的隔离网关（15725→StepFun），
不是用户的 15722——证据里已自带 tempRoot 与 roster 改写记录，可复现。

### 22.4 e2e 总状态（最终）

| 断言 | 状态 |
|---|---|
| A/B/C 派单→世界动→铺子运转 | **通过** |
| blocked 可见 | **通过** |
| D 完成/交付 | **未达成**（断言未满足；根因=审计按 token 预算正确打回，主产品线） |
| E world.status | **通过** |
| F 负例 | **通过** |
| G 杀/重启重连 | **通过** |
| H 并发 busy | **通过**（网关补链后真实触发 3 次） |

**e2e 总结论：有条件通过**——世界端集成七项全过；D 的交付腿卡在 worker token 预算
（已验证根因、非世界侧缺陷）。已产出缺陷：1 条 P2（D-1 reconcile 撤单不恢复可见性）
+ 2 条 P3（驱动采样全部角色、E 的 found=false 分支未跑）。隔离台账现有 4 条
（QA 直连 8001 核实：1 running retry=1 claude、1 blocked retry=0 dsh、1 blocked
retry=3 claude、1 blocked retry=0 scribe），全在隔离临时库。

---

---

## 23. §20.2 根因更正：三条撤单/缺席路径 + D-1 关闭（2026-10-21）

**本节更正 §20.2 的根因定位**（原文保留作历史，以本节为准）。Chief 在 b3bc7b8 上
逐行追出三条路径，QA 在当前 HEAD `f4200e5` 上独立核实并补状态。

### 23.1 三条路径全貌（QA 逐行核实）

| # | 路径 | 触发条件 | 缺陷形态 | 状态 |
|---|---|---|---|---|
| 1 | live-sync 撤单 → `abandonActiveTask`（:436-437 → :546） | 会话内 sync，`ledgerReconciled=true` | 缺**位置**（`visible=true` 本来就有，:557） | **已修**（b8e40d2） |
| 2 | reconcile 的 finished 分支 → `finishActiveTask` → `removeTask`（:1460-1463） | 读档后首次 sync，单在 finished 里 | **无缺陷**——`finishActiveTask` 先跑（:1140 挪到 door、:1143 恢复 visible），removeTask 的恢复是幂等的 | 安全（设计如此） |
| 3 | reconcile 的「完整快照里缺席」分支 → `removeTask`（:1467） | 读档后首次 sync，单在完整快照里缺席 | 缺 **visible + 位置**（removeTask 两样都没做） | **已修**（f4200e5） |

**路径 2 安全性（QA 独立核实）**：`finishActiveTask`（:1125-1151）在 :1140 把 pos 挪到
`building.door`、:1143 置 `visible=true`，然后才 removeTask——先恢复再删，安全。

**e2e round-04 走的是路径 3**（判定依据：600 帧 `visible=false`——`abandonActiveTask`
必然置 true，它没跑过；读档后首次 sync 按 :404 必进 reconcile）。施工 f4200e5 的复现
（toSave→fromSave 造「关页时在屋内干活」的档 + 首次 sync 缺席）独立印证了这条路径，
并实测「首次对账后 visible 仍 false、12 秒位移 0」。

**为什么 b8e40d2 的测试没覆盖路径 3**：新 Simulation 的 `ledgerReconciled` 默认 true
（:191），sync 只在 `!ledgerReconciled` 时才进 reconcile（:404）——测试走的全是 live
路径。这是测试覆盖缺口，f4200e5 补上了读档场景。

### 23.2 触发条件更正

§20.2 的触发链表述基本正确但可更准：**读档只是把角色放进「在屋内干活」这个前置
状态；触发者是撤单/缺席本身**，至于是哪条路径处理取决于当时是否已对账。e2e 实例：
读档（前置）+ 轮次间 DB 重置导致首次对账判缺席（触发）→ 路径 3。

### 23.3 修复与独立复验（D-1 关闭）

**修法**：b8e40d2 先在 `abandonActiveTask` 内补「不可走就挪 door」；f4200e5 把
「恢复可见 + 移出建筑」抽成共用 `restoreOutOfBuilding(villager, building)`，两条路径
都调，另加 `buildingOfId(taskId)` 在删单前记下挂靠的楼。落点选 `building.door`
（assertWorldInvariants 强校验门口可通行）；本就可走（户外撤单）时不动。

**QA 独立复验（当前 HEAD f4200e5）**：
1. **门禁实跑**：check 0 / `test:inprocess` **221/221** / audit --strict 世界 16
   （引用 110）UI 8（51）并集 23 越界 0 / 整门禁 **PASS**；
2. **变异探针（QA 自己跑）**：停用 `restoreOutOfBuilding` 方法体 → **3 条全红**
   （两条 live + 一条读档：对账判缺席恢复可见 / 恢复后能真正走动 / 读档+首次对账
   判缺席）→ 还原 → **221/221 全绿**、文件与原始**逐字节一致**；
3. 测试形态：3 条断言覆盖「恢复可见 + 位置在可走格 + 再跑 12 秒位移 > 0.5」，读档
   场景用 toSave→fromSave 造前置态——不变量形式，不是锁常量。

**时间线备注**：Chief 的三路径追查在 b3bc7b8 上完成（当时路径 3 未修、施工认领明早
修）；f4200e5 于 01:10 落地把路径 3 也修了。**当前 HEAD 两条缺陷路径均已修复。**

**验证边界（CH-1 补，Chief 与施工对齐，2026-10-21）**：

D-1 两条缺陷路径（live-sync 撤单走 `abandonActiveTask`、读档后首次对账缺席走
`removeTask`）各有单测覆盖 + 共用方法的变异探针；**smoke 只覆盖 live 路径**，
结构上测不到读档对账——smoke 是新建 world 跑的，`ledgerReconciled` 默认为 `true`，
而 `sync` 只在 `!ledgerReconciled` 时才进 `reconcile`（simulation.ts:191 / :404）。
已重跑 smoke 确认无回归（116/116），但那只能证明「没把别的东西弄坏」，不能证明读档
分支被覆盖。

读档分支的直接证据来自单测本身：`toSave → fromSave → 首次 sync 缺席` 精确构造了
任务卡要求的场景，断言不变量（mode / taskId / visible / 位置可走 / 再跑 12 秒位移），
比 smoke 的间接覆盖更强。若将来需要 e2e 级证据，需另造「带存档启动 + 首次 sync」用例，
当前不在 D-1 范围内。

**D-1 判定：已关闭**（P2 → 两路径修复 + 3 条回归测试 + QA 变异探针 + 门禁全绿）。
e2e 缺陷清单：P2 清零；剩 2 条 P3（驱动采样全部角色、E 的 found=false 分支未跑）。

---

---

## 24. 合并收尾后的独立复验准备（2026-10-21，合并待用户批准）

**背景**：`feat/pixel-world-oracle-world` 领先 origin 88 提交；master 有 11 个我们没有的
提交（含 S5 桥接 fdacd41、durable C1–C4 c60c374、预算分离 c9ef30a、outbox 2c83cd4、
**world.dispatch 幂等去重 a643cf7**）。merge-base `df36bed`，双方都改过的 6 个文件：
`docs/PROTOCOL.md`、`scripts/check_protocol.py`、`scripts/test_executive_v3.py`、
`server/app.py`、`server/assistant/executive_api.py`、`server/interaction/threads.py`。

### 24.1 合并后复验协议（两层 + 一条专项）

**第一层 · 主产品门禁**（合并后 master 侧不能红）：

| 命令 | 口径 |
|---|---|
| `python scripts/check_protocol.py` | 必须用 `.venv\Scripts\python.exe`（系统 anaconda 会 numpy ImportError） |
| `python scripts/test_core.py` | 同上，离线无需 Key |
| `npm test` | 主产品前端测试 |
| `python scripts/run_gates.py` | 总门禁 |

**第二层 · 像素世界门禁**（我们这条不能红，基线见 24.3）：

| 命令 | 基线（合并前 HEAD） |
|---|---|
| `npm run check` | exit 0 |
| `npm run test:inprocess` | **221/221** |
| `node scripts/audit-palette.mjs --strict` | 世界 16（引用 110）/ UI 8（51）/ 并集 23 / 越界 0 |
| smoke-ego | 116/116（Chief 代跑浏览器，QA 核映射与断言） |

**第三层 · 专项**：a643cf7 幂等去重对 e2e 断言的影响（QA 已预分析，结论：现有断言
不受影响，见 24.2）。

### 24.2 去重语义预分析（QA 读 a643cf7 独立完成）

**机制**：`world.dispatch` 注册 `idempotency_key='request_id'`；loop 在 mutation
调用时注入 `operation_id = runtime.operation_id(run_id, action_seq)`（loop.py:495-498）
——**由轮次 id + 轮内变异序号派生，与参数无关**。`_world_dispatch` 先按 request_id
反查台账：有非 drafting 的既有单 → 直接返回既有回执（不建新单、**不走 busy**）；
busy 检查加了 `existing is None` 守卫；drafting 重放允许通过（补完崩溃的
open→dispatch 窗口）。台账层还有第二层：同 request_id 返同一单、同 request_id
换参数被拒（test_world_bridge.py 新增断言覆盖）。

**对 e2e 各断言的影响判定**：

| 断言 | 合并后是否仍有效 | 理由 |
|---|---|---|
| A/B/C/F/G | **有效** | 全新派单（新 run_id → 新 operation_id → 无既有单）行为不变 |
| H 并发 busy | **有效** | 三条并行派单是三个独立轮次 → 三个新 operation_id → `existing is None` → busy 检查照常生效 |
| D 完成/交付 | 有效（根因未变） | worker token 预算问题与去重无关 |
| E world.status | 有效 | 只读，不受影响 |

**去重路径本身**：只挡**同一 (run_id, action_seq) 的重放**（崩溃/重试场景），e2e
自然流程不会触发（每条 WS 文本都是新轮次）。该路径由服务端单测
（test_world_bridge.py 的同 request_id 返同一单、换参数被拒）覆盖，e2e 层记为
「单测覆盖、e2e 不自然触发」。

**合并后若重跑 e2e 的一条注意**：H 的三条派单必须用**不同的文本/轮次**（本来
就是）；同轮内重发同一动作会因 action_seq 不同仍算新单，但若复现的是同一 run 的
重放则会被去重挡回既有单。

### 24.3 合并前基线（QA 实跑，HEAD 8acac73）

像素世界：check 0 / **221/221** / audit 16-8-23 越界 0 / strict exit 0 / 整门禁 PASS。
主产品侧基线合并时建立（不在 master 上预跑，避免碰用户线）。

**复验执行时机**：合并落地、Chief 通知后；两门禁层 QA 自己跑，smoke 浏览器部分
Chief 代跑。冲突 6 文件若合并后有语义损失（不是文本冲突而是功能被覆盖），按 §6
记 P1（关键集成链路断裂）。

---

---

## 25. 合并落地后独立复验结果（2026-10-21，HEAD `20db387`）

### 25.1 两层门禁（QA 独立实跑，非采信 Chief 数字）

**第一层 · 主产品**（cwd=worktree 根，python 用 master 的 `.venv\Scripts\python.exe`，
Python 3.12.4 / numpy 2.5.4；worktree 无自有 venv）：

| 命令 | QA 实测 |
|---|---|
| `python scripts/check_protocol.py` | **exit 0**（PASS） |
| `python scripts/test_core.py` | **64 passed, 0 failed** |
| `python scripts/test_world_bridge.py` | **75 passed, 0 failed**（合并前 71，master 的幂等测试 +4） |
| `python scripts/test_executive_v3.py` | **231 passed, 0 failed** |

**第二层 · 像素世界**（cwd=pixel-world）：

| 命令 | QA 实测 |
|---|---|
| `npm run check` | **exit 0** |
| `npm run test:inprocess` | **221/221**（fail 0） |
| `node scripts/audit-palette.mjs --strict` | **exit 0**；世界 16（引用 **267**）/ UI 8（51）/ 并集 23 / 越界 0 |
| `python scripts/test_pixel_world.py` | **PASS** |

注：audit 引用数 110 → 267 来自合并前的视觉工作提交（ca0bca3 世界美术重建 +
8acac73 像素字体与双角色），非合并引入；唯一色数仍 16、越界 0，调色板纪律守住。
smoke-ego 116/116 为 Chief 代跑（浏览器部分），QA 未独立复跑——ego 沙箱限制，
记为「Chief 代跑、QA 核口径」。

### 25.2 冲突解法逐处核查（4 处，3 处正确、1 处断裂）

| # | 冲突点 | 解法 | QA 核查结果 |
|---|---|---|---|
| 1 | `extra_env` | 取 master | **正确**：`:1437` `extra_env = _durable_extra_env(cfg)` → `:1458` 传入；`:1412` 含 `XIAOXI_APPROVAL_TOOLS`，未回退成空 |
| 2 | `bind_durable` 参数 | 取 master | **正确**：`:1469` `workspace_root` + `:1471` `apply_to=_durable_switch_agents(cfg)` 都在 |
| 3 | shutdown 全局 | 两边合并 | **正确**：`:1553` 四个 global；`:1555-1558` 订阅解绑 + `_loop=None`；`:1560-1566` `_reconcile_task.cancel()` + 置 None——两件事都在，注册/解绑、启动/取消均成对 |
| 4 | `durable_lookup` | 取 master | **断裂（P1，见 25.3）** |

### 25.3 P1：合并后 `_durable_lookup` 双定义遮蔽，durable 重启收口断裂

**现象**：合并后的 `server/app.py` 有**两个模块级 `_durable_lookup` 定义**——
`:1339`（master 版，直连查询函数 `(commission_id, attempt)`）与 `:1498`（我们版，
**工厂** `()` 返回闭包）。Python 后定义遮蔽前定义，**运行时绑定的是工厂**。
而两个调用点都是 master 的不带括号风格：`:1375` `reconcile_durable, _durable_lookup`、
`:1542` `recover_interrupted(durable_lookup=_durable_lookup)`——传出去的是工厂。

**实证（QA 自己跑，非推理）**：把两个定义原样提取 exec，运行时
`_durable_lookup.__code__.co_argcount == 0`（工厂）；模拟 master `_adopt_durable`
的调用模式（`lookup(id, attempt)` → TypeError → 回退 `lookup(id)`）→
**抛出未捕获的 `TypeError: _durable_lookup() takes 0 positional arguments
but 1 was given`**。

**影响**：
1. **P1 · 启动即崩**：`recover_interrupted`（executive.py:366）对 `_adopt_durable`
   没有 try/except 包裹 → TypeError 穿透到 `startup()` → **服务端在存在在飞单
   （running/briefing）时重启起不来**——恰是 durable 重启收口要用的场景。
   （auditing 态因 `_adopt_durable` 前置状态检查提前 return False 不触发；
   e2e 的 G 重启时单都是 blocked，所以没暴露。）
2. **P2 · 周期对账静默失效**：`:1375` 的 `reconcile_durable` 同样拿到工厂 →
   TypeError 被 `_reconcile_loop` 的 `except Exception: continue` 吞掉 → 周期对账
   永远不跑，无任何报错。

**为什么门禁没抓到**：test_core / test_world_bridge / test_executive_v3 都不走
app.py 的 startup 路径 + 在飞单组合；e2e 的 G 重启发生在单已 blocked 之后。

**根因**：冲突解法意图是「durable_lookup 取 master」，但两个定义位于不同区段，
git 自动合并把**两边都留下**，我们的工厂遮蔽了 master 的直连版——意图未达成。

**修复建议（施工执行，QA 复验）**：删掉遮蔽的工厂定义（`:1498-1528`）——
master 的直连版同时服务两个调用点，且重启路径的失败语义不变
（`_adopt_durable` 的 `except Exception: return False` 与工厂返回 None 等价，
都走「转 BLOCKED」）。备选：把工厂改名并恢复带括号调用，但删工厂是最小正确修。

### 25.4 主仓库未跟踪项判断

- **`pixel-world/`（134 文件）不提交进 master**：与 Chief 判断一致——像素世界属
  特性分支，提交进 master 会造成双份真相与漂移风险；master 里那份是独立本地副本，
  保持未跟踪即可。
- **两个评审 HTML**：主产品线的评审产物，是否入库由主产品侧定；QA 不干预。
  若它们被其他文档引用则应入库保迹，否则保持未跟踪亦可。

### 25.5 合并复验总结论

**不通过（1 条 P1）**：两层门禁全绿、4 处冲突解法 3 处正确，但 `durable_lookup`
双定义遮蔽导致 durable 重启收口断裂（启动即崩 + 周期对账静默失效），实证确认。
修复 + QA 复验通过后，合并结论可改「通过」。

---
