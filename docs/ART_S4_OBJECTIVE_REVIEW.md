# ART_S4_OBJECTIVE_REVIEW — S4 演示截图客观像素统计复核（2026-10-19）

> 作者：像素美术 II（Pixel Art Director II）· 状态：S4 验收客观项留档 · 规则：只报实测数字，人类指认一律「待用户评审」
> 证据：`docs/DEMO_RUN_EVIDENCE.md` + `pixel-world/eval/screens/demo-{mock,live}-*-{100.png,400pct.png}`（34 张）
> 方法：bundled python（PIL 12.3.0 / numpy 2.3.5）+ 脚本 `pixel-world/eval/_art2_temp/s4_recheck.py`（`pixel-world/.gitignore:3` 忽略 `eval/`，未入 git）。nearest-token 归因 = 每像素对 16 个世界色 token 取 RGB 欧氏最近；精确色 = 源值全等；雨天定位用「token×0.72 + cloakDark×0.28」合成色。

## 1. 尺寸口径（E-16）
- 400pct 全部 **1920×1280** = native 480×320 **×4** ✓（同 `docs/ART_SPEC.md` §1.1/§4.1）
- 100% 整页 995×1359；canvas 在页内 (27,91) 占 960×640 → **canvas 像素块 = 1.99 dev px / native px**（实测草格 bbox 956×636，扣世界土壤边）

## 2. 四栋剪影与识别色（mock/live 0-initial 400pct，两模式逐项一致）
- demo 世界实放 **3 栋**（`demo-run-mock.jsonl:4` `buildings:3`、`:25`「铺子 3」）：code_forge 5×4 gable / star_archive 4×4 tower / ink_house 4×3 flat；**workshop_plot 未在 demo 放置**（扩建样坂，识别证据在 task-30 切片）
- 剪影高（prefab 几何 + 像素验证，tower 塔尖 +4）：**forge 64 / archive 68 / ink 48 native px，两两差 4 / 16 / 20，均 ≥2 ✓**（400% 下 256/272/192 dev px；塔尖 pale 10 native px² 实测在 py−4）
- 识别色分布（nearest-token，整幅 canvas）：**W11 amber 0.262% / W10 trim 0.981% / W13 cloak 0.039% / W15 ember 0.048%**（W16 pale 0.270%）。与 task-30（0.21/1.11/0.72/0.04）同量级；W13 偏低因 demo 无 workshop 蓝顶、仅剩斗篷
- 截图唯一精确色 75（晴天 alpha 合成色；源码口径世界 16 / UI 8 不变）

## 3. 雨天（mock/live 3-rain 400pct）
- 色带：3600/3668 段地形 run 扫描，**≥3px 灰阶阶跃 0 条**（max_chain=2）✓
- 雨幕压暗：实测 grass `#2d3b2e` → `#2a3b37` = 0.72·grass + 0.28·cloakDark，**α=0.28 与 `weather.ts` 口径一致** ✓
- 主角（压暗后合成色定位）：mock 帧两村民同在书阁屋檐下——skin 39/14 npx²、ember 24/10、cloak 60/24，**亮部块均 ≥2px ✓**；两人间隔 **9 native px** 不粘连 ✓；live 帧主角 skin 39 npx² + 围巾 + 斗篷 ✓
- 夜景：demo 无夜景段（1.0 不做昼夜，用户裁决）→ 未涉及

## 4. overlay 文字尺度（100% 整页，canvas 块 1.99 dev px）
- **名字标签渲染 ✓**：10px 字号、字形带 7–10 dev px；mock-0-initial 实测「阿雾」双字形（native 242–251, 124–128，各 9×9 dev px）；seg2/4/5 实测「莞尔」双字形（native 468–489, 194）
- **气泡渲染 ✓**：11px，纸底矩形 62–86 × 17–28 dev px 含墨字（live-0-initial 74×21、mock-1-salute 74×18、mock-1-delivered 62×17、雨帧 84×17）
- 其他 DOM：status 12px（带 10–12 dev px）、按钮 13px（字形 11–12）、日报标题 17px（带 15）、日报正文 13px（带 12）、debug 11px（字形 5–8）
- 判读口径（Chief 2026-10-19 裁决）：**按 CSS px 判读**，name 10 / bubble 11 / status 12 均 ≥8px ✓；原生等效 3.5–7.5 属 DOM 层固有属性（D5-A），不按 canvas 像素判（`docs/ART_SLICE_REVIEW_CHECKLIST.md` §2.2 第 4 行已同步）

## 5. 结论
> 范围声明：本文 live 部分仅为**画面证据**（400pct 像素统计 + 100% overlay 实测），不构成「live 台账单驱动世界」的结论；该链路另有独立验收，结论以 QA 的 S4 章节与 Chief 汇总为准（当前状态：待施工证据/可能降级为已知限制）。

- **客观项：通过**——尺寸口径、四栋剪影高差、识别色分布、雨幕无色带与 α=0.28、主角亮部块 ≥2px 且两人不粘连、overlay 名字/气泡渲染与 canvas 咬合，全部达标
- P3 信息项：① demo 世界 3 栋（workshop_plot 未放置）；② mock-0-initial 莞尔走路中被建筑遮挡（canvas 不可见，DOM 名字标签正常渲染）
- **人类项（3 秒指认主角 / 三栋 / overlay 观感）：全部「待用户评审」**；位置稳定性引 QA task-27 实测 0.00–0.40px ≤1（`docs/QA_VERIFICATION_S1.md:35`）
