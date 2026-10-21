# PIXEL_WORKSHOP_SPEC_V2.md 的状态与备份（待用户裁定）

## 当前状态

| 项 | 值 |
|---|---|
| 路径 | `docs/PIXEL_WORKSHOP_SPEC_V2.md` |
| 大小 | 86,949 字节 / 1,274 行 |
| sha256 前 20 位 | `c597c4ee49df7485ffcb` |
| 最后写入 | 2026-10-21 23:58:43 |
| git 状态 | **未跟踪**（不在 `git ls-files` 里） |
| `git clean -ndx` | **会删掉它**（已实测确认） |
| 是否唯一副本 | **是**（外部 Agent 的 worktree 里没有） |

## 仓库外备份

```
C:\Users\18712\.dsh\spec-backup\PIXEL_WORKSHOP_SPEC_V2.md
```

86,949 字节，与源文件逐字节相同（sha 一致）。这个位置在仓库外，
`git clean` / `git checkout` / `git reset` 都碰不到它。

**等用户裁定后，这个备份可以删。**

## 为什么没有把它纳入跟踪

1. **内容未知。** 86,949 字节 / 1,274 行，我只读过前 60 行和附录 B/C 的一部分。
   它覆盖 §0–§9，包括 §4（Task 13 态 / Step 11 态状态机）与 §6（权限 L0–L6），
   **这两块正是用户划红线的「个人 Agent 核心」。**
2. **来源不明。** 它引用了三份不在仓库里的一手文档：
   《AI 原生像素个人助理世界架构设计》（1095 行）、《小汐双系统语音 Agent × 像素工坊 技术设计文档》、
   《委托卷轴》。这不是外部那个 Claude Code 能看到的材料。
3. **它和已冻结的 `docs/CONTRACT_V2_SCOPE.md` 结论相反**：那份说 §4–§9 缺失、一律不实现，
   这份把 §4–§9 全写全了。

把一份内容未审、来源不明、且与已批准决定冲突的文档直接纳入版本库，
等于替用户认可它。**所以只做备份，不做归属。**

## 相关文件

- `docs/CONTRACT_V2_SCOPE_PENDING.md` —— 另一份待裁定内容（`.chief-bak` 的差异，已落成受跟踪副本）
- `scripts/.dialogue-regression-10136-eyes.mjs` —— 01:13 另一个会话写的 Playwright 脚本，同样未跟踪
