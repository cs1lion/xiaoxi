# web/archive — 历史设计研究与预览页

本目录保存已从主工作台摘牌的历史页面，仅作参考资料，不再从任何入口链接。
服务仍可通过 `/static/archive/<name>` 访问（便于比对历史设计）。

- `blender_study.html` — Blender 工作流学习笔记页（2026-10-14 归档，无入站链接、无测试引用）。
- `avatar_preview.html` — 形象早期预览页（2026-10-14 归档，无入站链接、无测试引用）。
- `card_preview.html`（连同 `card_preview.css` / `card_gloss.css` / `capture-card-preview.mjs`）— 柔光陶瓷卡片设计研究（2026-10-24 归档，UI 分级轮 P3a/issue #37）。捕获工具随页入档；`scripts/test-task-card-integration.mjs` 仍借本页当空白宿主测真实 `task-card.js`，URL 已改指 `/static/archive/`。
- `notepad_preview.html`（连同 `notepad_preview.css` / `notepad_preview.js`）— 一张工作便笺设计研究（2026-10-24 归档，同上）。其 demo 页测试随页删除（测试的只是演示页自身）。
- `design-preview/` — 三方向视觉对比工作区（dir-a/b/c、live-20261017、校验与截图脚本，2026-10-24 整目录归档）。

归档原则见 `docs/POLISH_REPORT.md` R0-3 与交付计划 Phase 3（docs/DELIVERY_PLAN_20261024.md）；
新页面一律禁止再建孤立 HTML（`docs/AGENT_PLAYBOOK.md` 规则 3）。
