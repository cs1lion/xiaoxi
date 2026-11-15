# 本轮收口（P1–P4）— SPEC

- 日期：2026-11-14
- 目的：把本轮（2026-11-13 ~ 11-14）的改动、证据、读数一次性归位；**不提交**，不把无关改动混进来。
- 范围：P1 生产词表评估、P2 过敏指代救援、P3 浏览器/性能基线、P4 live 定向实跑，以及本轮新增的流程文档。

## Do
- 固化 `git status` / `git diff --stat` 快照（`git-status.txt` / `git-diff-stat.txt`）。
- 跑通用验证并落盘同口径读数：`check_protocol.py`、`test_core.py`、`npm test`。
- 按「本轮改动 / 既存无关改动」分类，并给出提交分组建议（只建议，不执行）。

## Don't
- 不 `git add` / `commit` / `push`；不动 `config/persona.yaml`、`web/app.js`、`web/index.html`、`web/style.css`（非本轮修改）。
- 不改协议、不改 `min_similarity` 硬门槛、不改负样本约束。
- 不把 `.env`（含 Key）写进任何证据文件。

## 判定
- 每个验证命令有 exit code 与输出文件；分类表能逐文件对上 `git status`。
