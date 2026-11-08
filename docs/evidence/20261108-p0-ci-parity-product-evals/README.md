# P0③ CI 差集收编 + 离线产品维度评测 — 证据

- 分支：`feat/p0-offline-voice-and-eval-20261108`（自 `master` @ `e036a14`，未 push）
- 日期：2026-11-08
- 未新增门禁条目：`GATES` 只收编既有差集，`ALWAYS_REQUIRED` 未变（`--always` 仍 80 条）。

## 1. CI 差集（`scripts/gates_unregistered.txt`）

收编 6 条进 `scripts/run_gates.py::GATES`：`evals_guards`、`intent_parser_e2e`、
`measure_intent_corpus`、`memory_retrieval_quality`、`memory_snapshot`、
`memory_temporal`。全部 `git ls-files` 已跟踪，且在 `run_gates`
注入的 `TEST_SENTINEL=1` 环境里真跑真过。

**不注册** 2 条，理由同一类——「跳过即失败」，登记它们等于自开一个永不判红的洞：

- `test_eye_calibration_analysis`：缺 Pillow 时输出 `OK (skipped=14)`，14 条一条也跑不了。
  补它需要 `requirements-ci.txt` + CI 安装 Pillow，并先确认 14 条真跑（另开任务）。
- `test_pixel_world`：世界源码已随 `7c13fd4` 剥离；仅本机有历史副本时可跑，新 clone 必 SKIP。
  脚本仍留在磁盘，需要时手动跑；`pixel_world_hosting` 保留在 `GATES`（用 TestClient 打
  生产路由，不依赖世界源码）。

理由已写进 `docs/TESTING.md` 与清单注释。

| 同口径命令 | 改前（master e036a14） | 改后（本分支） |
| --- | --- | --- |
| `python scripts/test_gate_coverage.py` | 磁盘 179 / 已登记 171 / 清单 8，PASS | 磁盘 179 / 已登记 177 / 清单 2，PASS |
| `python scripts/run_gates.py --only <6 条>` | 不在本地门禁，无读数 | 6 条全 PASS，0 跳过 |
| `python scripts/run_gates.py --always` | 80 条基线（CI 绿） | 80 跑过并断言，0 失败，0 跳过 |

## 2. 离线产品维度评测（`evals/cases/` 新增 2 条，不新增门禁）

| 用例 | 量什么 | 实跑读数 |
| --- | --- | --- |
| `extraction-quality-offline` | 抽取**后处理**在 14 条合成标注候选（6 应留 / 8 应丢）上的 precision / recall | 实留 6 / 丢弃 8；precision 1.000；recall 1.000 |
| `tts-emotion-routing-offline` | 七档情绪 → `voice/instruction/rate/pitch/volume` 的路由闭合、`voice_profile()` 不回落 base、叠加层不吞参数 | 7 档；instruction 去重 7；参数组合去重 7 |

`python scripts/run_evals.py`：改前 16 passed → 改后 18 passed，0 failed / 0 xfail。

## 未验证（依赖写清，不估算）

| 维度 | 状态 | 依赖 |
| --- | --- | --- |
| ASR 字错率（CER） | 未验证 | 固定音频语料夹具 + 真实 ASR 后端/凭据 + 固定测试环境；Mock 模式 ASR 被 mock，离线量不到。域与条件（方言/口音/噪声/远场）必须随数字一起报。 |
| TTS 情绪「听感」 | 未验证 | TTS provider 凭据 + 固定脚本文案 + 人耳/评测集；本用例只证路由闭合。 |
| 模型抽取质量（live） | 未验证 | `LLM_API_KEY` + 标注语料；本用例只证模型输出之后的后处理判据。 |
| `test_eye_calibration_analysis` | 未验证 | Pillow（`requirements-ci.txt` + CI 安装步骤），补后需确认 14 条真跑。 |
| `test_pixel_world` | 未登记（新 clone 必 SKIP） | 世界源码已随 `7c13fd4` 剥离；仅本机有历史副本时可跑，需要时手动执行。 |

## 其他同口径验收

- `python scripts/check_protocol.py`：PASS（22 消息类型字段全部对得上，未改协议）。
- `python scripts/test_core.py`：134 passed, 0 failed。
- `npm test`：5 pass, 0 fail。
- `python scripts/test_doc_drift.py`：OK（② 在 `app.py` 顶部加 1 行 import、在
  `_transcribe_turn` 前加降级函数后，TESTING.md 的函数行号锚全部 +48，已同步重锚）。
