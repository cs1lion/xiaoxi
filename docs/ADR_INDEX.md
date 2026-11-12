# ADR 索引

本仓当前**没有约束中的 ADR**。下面六份都是 `pixel-world` 工程的历史决策，
该工程已于 `7c13fd4` 移出本仓，由
[`cs1lion/pixel-world-engine`](https://github.com/cs1lion/pixel-world-engine) 维护。
文件保留在本仓只为历史追溯（`docs/ADR_PALETTE_24.md` 的先例，见 `AGENTS.md`），
**不要按它们改当前的小溪语音/记忆/交付代码**。

| 文件 | 主题 | 状态 |
| --- | --- | --- |
| [`ADR_AGENT_BRIDGE.md`](ADR_AGENT_BRIDGE.md) | Agent ↔ 像素世界最小桥接 | 归档 |
| [`ADR_CREATION_MODE.md`](ADR_CREATION_MODE.md) | 造物模式（第二阶段） | 归档 |
| [`ADR_DRAFT_WORLD_SEAM.md`](ADR_DRAFT_WORLD_SEAM.md) | pixel-world-engine 与小溪的事件接缝契约（草案） | 归档 |
| [`ADR_PALETTE_24.md`](ADR_PALETTE_24.md) | 世界调色板 16 → 24 色 | 归档 |
| [`ADR_SAVE.md`](ADR_SAVE.md) | 世界存档（D-SAVE-1） | 归档 |
| [`ADR_WORLD_ALIVE.md`](ADR_WORLD_ALIVE.md) | 世界变活（时辰、神谕石板、有来源的摆件） | 归档 |

## 新增 ADR

当前仓库的决策入口仍是 `docs/PROTOCOL.md`、`docs/AGENT_PLAYBOOK.md` 与
`docs/process/ENGINEERING-DISCIPLINE.md`。如果将来确实需要新 ADR，放
`docs/ADR_<主题>.md`，并在本索引登记一行（状态写「生效」或「归档」）。
