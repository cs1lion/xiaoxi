# ART_S4_OBJECTIVE_REVIEW_R2 — S4 占比数据 24 色重建后重测（2026-10-27）

> 作者：像素美术 II · 状态：#15 交付件 · 触发：24 色 ADR（`d56d64b`，2026-10-21 用户批准）后，
> 原 `docs/ART_S4_OBJECTIVE_REVIEW.md` 的 nearest-token 占比口径（16 色）失效，本文重测。
> 证据：`pixel-world/eval/screens/r2-24col-{0-initial,3-rain}-400pct.png`（2026-10-27 重截，
> `pixel-world/.gitignore:3` 忽略 `eval/`，未入 git）。
> 方法：bundled python（anaconda3，PIL 12.3.0 / numpy 2.5.1）+ 脚本
> `pixel-world/eval/_art2_temp/s4_recheck.py`（`TOKENS` 表已重建为 24 色，RGB 逐值取自
> `pixel-world/src/world/palette-data.ts`，2026-10-27 修订）。截图由
> `pixel-world/scripts/r2-shot.mjs` 驱动本地 vite 5188 产出，口径复用 demo-run.mjs 的
> shot()（隐 DOM、canvas 1920×1280 native 480×320）。

## 1. 与 R1 的口径差异

R1（2026-10-19）的 nearest-token 归因基于 16 个世界色 token。24 色重建新增 W17–W24
（plaster / terracotta / copper / water / moss / deepShade / warmLight / charcoal，
palette-data.ts:54-67）。R2 的 `TOKENS` 表已含全部 24 色，R1 数字与 R2 数字
**不可直接对比**——同一像素在新表下会归因到不同的最近 token（例如书阁主体的
plaster 在 R1 下被最近的 W10 trim 吸走）。

## 2. 0-initial 帧（400pct canvas，nearest-token 整幅归因）

| token | nearest | exact |
|---|---:|---:|
| W01_ink | 0 (0.000%) | 0 |
| W02_soil | 910318 (37.012%) | 317200 |
| W03_grass | 1210850 (49.231%) | 433824 |
| W04_grassLit | 21396 (0.870%) | 0 |
| W05_stone | 193884 (7.883%) | 12084 |
| W06_plaza | 11920 (0.485%) | 4608 |
| W07_farm | 57384 (2.333%) | 20328 |
| W08_timber | 2208 (0.090%) | 0 |
| W09_timberDark | 2544 (0.103%) | 0 |
| W10_trim | 28 (0.001%) | 0 |
| W11_amber | 0 (0.000%) | 0 |
| W12_skin | 0 (0.000%) | 0 |
| W13_cloak | 1416 (0.058%) | 0 |
| W14_cloakDark | 132 (0.005%) | 0 |
| W15_ember | 0 (0.000%) | 0 |
| W16_pale | 8448 (0.343%) | 8448 |
| **W17_plaster** | **12852 (0.523%)** | 0 |
| W18_terracotta | 0 (0.000%) | 0 |
| **W19_copper** | **11840 (0.481%)** | 0 |
| **W20_water** | **1044 (0.042%)** | 264 |
| **W21_moss** | **808 (0.033%)** | 0 |
| **W22_deepShade** | **11296 (0.459%)** | 0 |
| **W23_warmLight** | **1152 (0.047%)** | 756 |
| W24_charcoal | 0 (0.000%) | 0 |

精确色总数 333。R1 记录的唯一精确色 75（晴天 alpha 合成色）。

## 3. 3-rain 帧（400pct canvas）

雨天归因与 0-initial 逐 token 一致（差异在第四位小数内）：grass 49.217% / soil
36.993% / stone 7.926%；W17 plaster 0.523%、W19 copper 0.467%、W22 deepShade
0.492%（较晴天升，符合檐下冷影层的设计意图）、W20 water 0.058%（晴天 0.042%，
雨天水洼反光成立）。R1 的雨天 α=0.28 压暗口径（grass → 0.72·grass +
0.28·cloakDark）与 R2 帧的 top 色带（#2d3b2e 系仍居首）不冲突，该项未被 24 色
重建改变，未重测。

## 4. 结论

- **占比数据已按 24 色口径重建**：W17–W24 八个新色在画面上全部有真实占比
  （0.033%–0.523%），24 色 ADR 的「新增材料/光色进入画面」这一设计目标有像素证据。
- **R1 数字作废**：原文档 §2 的识别色分布（W11 amber 0.262% / W10 trim 0.981% 等）
  是 16 色口径，不再代表当前画面；对比需求一律以本文为准。
- **人类项仍待评审**：3 秒指认主角 / 三栋 / overlay 观感，R2 未涉及，维持 R1 的
  「待用户评审」状态。
- 范围声明同 R1：本文仅为画面像素统计，不构成 live 台账单驱动世界的结论。
