# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`GLOSSARY.md`** at the repo root, or
- **`GLOSSARY-MAP.md`** at the repo root if it exists: it points at one `GLOSSARY.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (most repos):

```
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

Multi-context repo (presence of `GLOSSARY-MAP.md` at the root):

```
/
├── GLOSSARY-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── GLOSSARY.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── GLOSSARY.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `GLOSSARY.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_

---

## 本仓库现状（single-context）

2026-10-23 由 `/setup-matt-pocock-skills` 探查：

- `GLOSSARY.md` — **不存在**（按上面规则，先静默跳过，等 `/domain-modeling` 需要时再建）
- `GLOSSARY-MAP.md` — 不存在 → 单上下文
- `docs/adr/` — **不存在**。本仓库的架构决策目前散在别处，不在此目录：
  - `docs/ADR_AGENT_BRIDGE.md`
  - `docs/ADR_*` 系列（用 `Get-ChildItem docs -Filter 'ADR*'` 查最新）
  - `docs/CONTRACT_V2_SCOPE.md`（契约 v2 的边界与冻结项）
- monorepo 信号（`pnpm-workspace.yaml` / `package.json` 的 `workspaces` / `packages/*`）— 全无

**给 skill 的一条本地补充**：本仓库的 ADR 命名不是 `docs/adr/NNNN-*.md` 惯例，
而是 `docs/ADR_*.md`。读决策时先看 `docs/ADR_*.md` 与 `docs/CONTRACT_V2_SCOPE.md`，
不要因为 `docs/adr/` 不存在就认为没有 ADR。
