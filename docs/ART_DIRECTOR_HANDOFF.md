# 像素美术导演 — 交接档案（2026-10-19）

> 用途：原「像素美术（Pixel Art Director）」在 2026-10-19 发生不可逆的工具协议错误（模型 agnes 返回 `format v4 tool/result requires toolCallId`），工作改由副本 Bot「像素美术 II」接手。平台不支持克隆聊天历史，本文件 + 新 Bot 的记忆条目就是可交接的工作历史。

## 角色与边界

- 角色：像素美术导演（Pixel Art Director），负责俯视/斜俯视像素世界的美术设计、视觉规范与客观质量评审；直接向 Chief 汇报。
- **不改代码、不 commit**；只在 Chief 明确要求时写 docs 文件。
- **不碰 `docs/ART_PALETTE_AUDIT.md`** 与 `pixel-world/src/**`（历史教训：一次只读复核中越权改文档被 Chief 叫停）。
- 视觉红线：世界 **16 色** + UI **8 色**（共享 1 色 `#e3a13c`，并集 23）；任何加色（第 17/18 色）必须 **ADR + 用户批准**；1.0 不做昼夜，夜景并团留 1.1。
  **（2026-10-21 更新：世界层已由 24 色 ADR 扩至 24，并集 31。本条与下方「夜景」结论均为 2026-10-19 的当时口径 —— 夜景已于 `82696c7` 以纯渲染态落地，自动昼夜循环仍未做。现行预算见 `docs/ADR_PALETTE_24.md` 与 `ART_SPEC.md` §2.1。）**
- 本会话模型**无图像输入能力**：不能直接看 PNG。评审用脚本做像素统计（PIL：唯一色、色带、明度差、区域采样、nearest-token 归因），人类指认一律写「待用户评审」。
- 统计脚本只放 TEMP，不落进 `docs/` 交付目录（历史教训：曾把 `_palette_*.py` 临时脚本留进 `docs/`，被要求清理）。

## 已完成的工作（时间线，2026-10-19）

1. **task-26 调色板审计**（QA 通过）：世界 71 唯一/85 引用、越界 55；UI 17/45、越界 9；共享 2、并集 86；`docs/ART_PALETTE_AUDIT.md` 由脚本 `--md` 生成。
2. **task-27 附录核对**：确认附录 A 与 `--md` 实跑逐行 132/132 全等；补了一条「task-27 已移除 6 色不混入越界 55 行」的口径说明（已入 `5214eb5`）。
3. **task-30 切片评审清单**：`docs/ART_SLICE_REVIEW_CHECKLIST.md`（114 行）——16 色表评审、三处映射修正（`#8f7a4c`→W10、`#2f3b33`→W03、`#6b7c9c`→W14）、workshop 屋顶 W13 裁决、6 张 E-16 必拍清单、人类/客观/试验三类判定。
4. **task-30 证据客观评审**：四栋区分 PASS（剪影 + 识别色分布：W11 0.21%、W10 1.11%、W13 0.72%、W15 0.04%）；雨天 PASS（路径/草地 1.26:1 弱可辨、无 ≥3px 色带）；夜景 FAIL（grass/farm 1.03:1 并团 → 16 色不支持有层次夜景，留 1.1 的 18 色 ADR）；曾因拿错截图误判 workshop，后由 Chief 用 `06-faith.png` 更正。
5. **ART_SPEC v0.1 → v0.2**（`docs/ART_SPEC.md`，213 行）：四节规范（画面/颜色/形状/验收）。v0.2 修正：960×640 元素截图口径、对比度正确配对（timber vs 草地 2.04、vs plaza 1.46、plaza/cloak vs 草地 1.40，不设统一硬阈值）、`pale vs ink` 13.34:1、S3-4 编号、行号改「文件+符号名」。**v0.2 已由用户定稿**（`d14c762`/`2a0e6ea`）。2026-10-19 由像素美术 II 复算修正：1.24→1.40、1.53→1.46。
   - 注意：v0.2 最后三处数据修正由 Chief 在你模型失败后补完，你接手后请做**只读复核**，确认与你原意一致。

## 当前状态（基线 HEAD `2a0e6ea`）

- 世界 16 / UI 8 / 共享 1 / 并集 23；`--strict` exit 0；`test:inprocess` 180/180；主产品既有改动未动。
- 主要产物：`docs/ART_SPEC.md`（定稿）、`docs/ART_SLICE_REVIEW_CHECKLIST.md`、`docs/ART_PALETTE_AUDIT.md`、`docs/ART_DIRECTOR_HANDOFF.md`（本文件）、`pixel-world/eval/screens/slice-*`（E-16 证据，gitignored）。
- 相关源码参考点：`pixel-world/src/world/palette-data.ts`（16 色语义 token）、`prefabs.json`（16 槽）、`docs/DECISION_TRAIL_20261019.md`（用户裁决）。

## 待办（接手后）

1. **只读复核 `docs/ART_SPEC.md` v0.2**（Chief 补的三处 + 整体与你的原意是否一致），有出入报 Chief，不要直接改文件。
2. **S4 演示验收的支持**（计划 `docs/S4_DEMO_PLAN.md`）：当施工产出 `docs/DEMO_RUN_EVIDENCE.md` 与演示截图后，做**客观像素统计复核**（四栋/雨天/夜景/overlay 尺度），人类指认仍写「待用户评审」。
3. 后续若用户批 1.1 夜景 18 色 ADR，由你做色表提案（仍须用户批准）。

## 工作规则（沿用）

- 报告只回 Chief，不发群、不自行建卡、不 commit。
- 任何数字给「命令/文件:行」；未运行、未验证的结论必须标「未验证」。
- 结论按「通过 / 有条件通过 / 不通过」给，带 P0–P3。
- 不替用户做 3 秒指认；不把 AI 判定写成人类样本。
