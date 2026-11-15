# 本轮收口 — 读数与分类

- 执行时间：2026-11-14（本机，Windows，win32）
- 快照：`git-status.txt`、`git-diff-stat.txt`
- **未执行任何 `git add` / `commit` / `push`。**

## 1. 验证读数（同口径，本轮末次）

| 命令 | 结果 | 证据 |
|---|---:|---|
| `python scripts/check_protocol.py` | **PASS**（exit 0） | `check_protocol.txt` |
| `python scripts/test_core.py` | **134 passed, 0 failed**（exit 0） | `test_core.txt` |
| `npm test` | **26 个 TAP 文件，pass 479 / fail 0**（exit 0） | `npm-test.txt` |
| `python scripts/run_evals.py`（离线） | 18/18 PASS（上一轮已落盘） | `docs/evidence/20261113-p2-allergy-fallback/` |

> 数字均为本次命令真实输出，非推算；`npm test` 的 479 是各 TAP 文件 `# pass` 求和。

## 2. 工作区分类

### A. 本轮改动（应单独成提交）

| 文件 | 归属 | 说明 |
|---|---|---|
| `server/memory/retriever.py` | P2 | `_prohibition_rescue_expansion()` + `search()` 受限救援 |
| `scripts/test_memory_retrieval.py` | P2 | 红→绿用例 `test_referential_prohibition_fallback_finds_allergy_memory` |
| `evals/memory_recall_synonyms.tsv` | P2/P1 | 评测集调整（生产词表 `config/memory_synonyms.tsv` **未改**） |
| `scripts/test-voice-browser.mjs` | P3 | `endpointWaitMs` 期望 900→650，对齐 `43d58e5` 行为 |
| `docs/process/EDD.md` | 流程 | 本轮新增 |
| `docs/process/VERIFICATION_MATRIX.md` | 流程 | 本轮新增 |
| `docs/evidence/20261113-*`（13 个目录） | 证据 | P1–P4、边界、信号审计、Kickoff |
| `docs/evidence/20261114-*`（5 个目录） | 证据 | OpenRouter live、录屏×2、本轮收口、OpenDesign |

### B. 既存无关改动（**非本轮所改，不进本轮提交**）

| 文件 | 状态 | 处理 |
|---|---|---|
| `config/persona.yaml` | M | 留给你按原意图处理 |
| `web/app.js` | M | 同上（本轮只读取过，未编辑） |
| `web/index.html` | M | 同上 |
| `web/style.css` | M | 同上 |

> 校验方式：本轮所有编辑只落在上表 A 区；B 区四个文件在 `git diff --stat` 中的改动均早于本轮（未做逐行归因，如需严格归因需 `git diff` 逐块人工确认）。

## 3. 建议提交分组（仅建议）

1. `fix(memory): 过敏指代受限救援（P2）` — `retriever.py`、`test_memory_retrieval.py`、`evals/memory_recall_synonyms.tsv`
2. `test(voice): 端点等待断言对齐 650ms（P3）` — `scripts/test-voice-browser.mjs`
3. `docs(process+evidence): EDD/验证矩阵与本轮证据` — `docs/process/*`、`docs/evidence/20261113-*`、`docs/evidence/20261114-*`

不含 `config/persona.yaml`、`web/*`。

## 4. 仍未收口的边界（如实记录）

- **P3 8010 链路未覆盖**：仓库内无 8010 服务启动方式，未盲启。
- **d3d11 基线非严格受控**：同机同模式两次复测（FPS 59.00 / 59.06），单样本，`hardwareVerified: false`。
- **P4 live 结论有限**：免费模型波动（`empathy-support-live` 3/3 → 2/3）；含 judge 用例受 `LLMError` 干扰。
- **小溪端到端界面效果未程序化判定**：录屏存在，但"可见回复质量"需人看。
