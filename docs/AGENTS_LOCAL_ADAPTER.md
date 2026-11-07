# `.agents/` 本机适配层的入库与同步策略

日期 2026-11-05，基线 HEAD `9bf1456`；2026-11-06 补记提交 `41734eb`。用户授权代 Role 0 收口 `AGENTS.md`「待办」第 1 条。

## 为什么单独定策略

`.agents/` 原被 `.gitignore` 整目录忽略（旧 `:57`），但里面有两份**本轮实质产出**、且只存在于本机：

| 文件 | 性质 | 内容 |
|---|---|---|
| `.agents/skills/pstack/DSH.md` | **本机适配层**，不是上游 bundle 的一部分 | 工具名映射（Cursor pstack → DeepSeek Harness）、Graphite / `gt` 归属更正、`bun` 归属拆分、已知残缺计数表、`claude-code.md` 死链说明等 |
| `.agents/skills/pstack/docs/harness/claude-code.md` | bundle 布局内的**指针文件** | 指回 `../../DSH.md`，说明 21 处叶子引用是死链、DSH.md 整体替代它 |

重装 pstack 会用上游 bundle 覆盖整个 `.agents/skills/pstack/`，两份修正会消失。这就是 `AGENTS.md` 待办第 1 条「有时效性」的含义。

## 已落地的做法（2026-11-05）

1. `.gitignore` 把整目录忽略 `.agents/` 改成**逐层「忽略直接子项 → 放行目标目录/文件」**的规则链，只放行上面两个文件；上游 bundle 的其余文件仍被忽略。
   第一次只写 `.agents/*` + 4 条深层 `!` 是无效的：中间目录 `skills/`、`pstack/` 仍被排除，git 根本不遍历进去（`git add` 实测报 `.agents/skills` ignored）。补齐每一层的 `目录/*` 与 `!目录/` 后才生效。
2. 两个文件已于 2026-11-06 随提交 **`41734eb`** 进入 HEAD（只含这 2 个文件、+94 行，未 push）。在此之前它们只是「已加入 git 索引、未 commit」，那时重装 pstack 无法从 git 恢复。`git ls-files .agents/` 恰好返回这两条；`git check-ignore` 对它们无输出，对 `poteto-mode/SKILL.md` 仍报被忽略。
3. 入库前核过内容：两份文件均无本机绝对路径、无凭据；`grep` 命中的 `sk-` 只是工具名 `ask-user` 里的字符，不是密钥。

## 同步策略

- **不同步上游**：`DSH.md` 是本机适配层，不随 pstack 上游升级合并。上游换新时按当前 harness 的实际工具清单重新核一遍映射表，别盲合。
- **重装 pstack 后**：从 git 检出这两份文件即可恢复修正
  `git checkout -- .agents/skills/pstack/DSH.md .agents/skills/pstack/docs/harness/claude-code.md`
  （前提已在 `41734eb` 满足：`scripts/test_poteto_loading.py` 会断言这两个路径各自 `git log -1 -- <path>` 非空。）
- **不要把整个 bundle 入库**：体积大、归属上游、且 `DSH.md:10` 自述 bundle 应视为只读。
- `claude-code.md` 里的 `[../../DSH.md](../../DSH.md)` 从 `docs/harness/` 出发解析正确；`AGENTS.md` 记录的「21 处叶子引用全死链」指的是**叶子 → 指针**的方向，与这个指针自身的链接无关，两个判断别混。

## 判据

`git ls-files .agents/` 只列两个文件；`git check-ignore -v .agents/skills/pstack/DSH.md` 应无输出（未被忽略）；`git log -1 --format=%H -- <两份路径>` 各自非空（已进 HEAD，重装后可检出）。这三条已自动化为 `python scripts/test_poteto_loading.py`，并登记进 `run_gates.py` 的 `ALWAYS_REQUIRED`。不需要真的重装 pstack 来验证存在性。
