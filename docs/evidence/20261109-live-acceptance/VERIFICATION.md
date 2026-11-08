# 2026-11-09 · 真环境验收向导落地证据

- 分支：`feat/live-acceptance-20261109`
- 解释器：`D:\project\neurosama\.venv\Scripts\python.exe`
- 说明：本目录的 `run-*` 是脚本真实跑出的体检证据；本文件是当天命令与结论的索引。
  所有 live 检查点（B1–B3）**尚未在真实 Key / 真人麦克风环境跑过**，这里不冒充。

## 1. 向导自身门禁（离线）

| 命令 | 结果 |
| --- | --- |
| `.\.venv\Scripts\python.exe scripts\test_live_acceptance.py` | `Ran 19 tests … OK`（2026-11-09，本分支复跑） |
| `.\.venv\Scripts\python.exe scripts\run_gates.py --only live_acceptance_guard` | `[PASS] exit=0 12.2s`（2026-11-09 复跑） |
| `.\.venv\Scripts\python.exe scripts\test_gate_coverage.py` | `PASS`（磁盘 180 / 已登记 178 / 清单 2，覆盖度棘轮成立） |

守的边界：无 Key 判未就绪、上游 400/401 判 blocked、凭据不进报告、
Mock 沙盘服务在 `L0` 拒跑。

## 2. 无 Key 的真实体检输出

命令（仅在当前 shell 临时清空，不改 `.env`）：

```powershell
$env:LLM_API_KEY=''
.\.venv\Scripts\python.exe scripts\live_acceptance.py --doctor
Remove-Item Env:LLM_API_KEY
```

原始结论见 `run-20261109-021708/doctor.md` / `doctor.json`，关键点：

```
[体检] 未就绪；警告 0 项
[体检] 还差：
  - env.key: LLM_API_KEY 未配置 → 在 .env 里填 LLM_API_KEY（见 .env.example；StepFun 还需订阅/模型权限生效）
[体检] Mock 通过不等于真实通过；这里不会继续跑 live。
```

其余检查均为 ok：`.venv` 解释器、ffmpeg/ffplay、14 个麦克风输入设备、证据目录与临时沙盘。
`8000` 当时被用户服务占用，脚本仍只选独立端口（8721–8798），未重启用户服务。

## 3. 当前真实 `.env` 的体检输出（已知外部阻塞）

命令：`.\.venv\Scripts\python.exe scripts\live_acceptance.py --doctor`

原始结论见 `run-20261109-021659/doctor.md` / `doctor.json`，关键点：

```
env.key: LLM_API_KEY 已配置（只看是否存在，不打印值）
llm.upstream: 被上游拒绝（HTTP 400）
excerpt: {"error":{"message":"you have no active step plan subscription ..."}}
[体检] 未就绪
[体检] Mock 通过不等于真实通过；这里不会继续跑 live。
```

这正是已知的 StepFun 订阅未激活；脚本没有因为“Key 存在”就判通过，
也没有降级 Mock 后假装跑通。

## 4. 沙盘机制的内网自检（不是 live 验收）

在没有真实上游的情况下，用独立端口 + 临时 DB 起了 Mock 沙盘，跑了 B4 的 REST 序列：

```
POST /api/memory/manual  → id=1
GET  /api/memories       → count 0 → 1
GET  /api/memories/context?query=周六修自行车 → found=True，上下文含“修自行车”
DELETE /api/memories/1   → removed_rows=1
GET  /api/memories       → count 0
```

结论：B4 的接口序列在沙盘里可执行、可回滚。它**不**证明模型会用记忆，
也**不**证明真实 Key 下的 B1–B3；这部分仍等人跑真机。

## 4.5 全新 clone（无 `.env`）实测

从 HEAD 建 detached worktree，确认没有 `.env`、没有 `.venv`，用主仓库 `.venv`
解释器跑（只是为了能执行脚本）：

```powershell
git worktree add --detach D:/project/neurosama-wt-freshclone HEAD
# 该 worktree 内 .env=False、.venv=False
$env:LLM_API_KEY=''
& D:\project\neurosama\.venv\Scripts\python.exe scripts\live_acceptance.py --doctor
```

输出（`run-20261109-022853/doctor.*`）：

```
[体检] 未就绪；警告 1 项          # 仅 runtime.python=warn（worktree 无 .venv）
[体检] 还差：
  - env.key: LLM_API_KEY 未配置 → 在 .env 里填 LLM_API_KEY …
[体检] Mock 通过不等于真实通过；这里不会继续跑 live。
----EXIT:0----
```

再把 ffmpeg/ffplay 从 `PATH` 移除后重跑（`run-20261109-022900/doctor.*`）：

```
runtime.ffmpeg  否  warn   缺 ffmpeg/ffplay（ffmpeg=None；ffplay=None）
env.key         是  missing LLM_API_KEY 未配置
[体检] 未就绪；警告 2 项
[体检] 还差：仅 env.key
----EXIT:0----
```

结论：缺 ffmpeg 只是 `warn`，不影响 readiness，也不改退出码；**只有 `--strict`
（未就绪）才退 2**。麦克风缺失（`asr.input=warn`）同样不阻塞，由离线用例
`test_doctor_still_exits_zero_without_ffmpeg_or_microphone` 用依赖注入覆盖
（真机上无法在不拔硬件的前提下制造“真的没有麦克风”，这里不伪造）。
`--doctor` 在无 `.env` 时会用 `server/config.py` 的默认引擎（edge_tts + whisper），
本机包与模型都在，所以 `tts.engine` / `asr.engine` 判 ok；它们不是判定项以外的新门槛。

## 4.6 证据目录入库边界

- **入库**：`README.md`、`VERIFICATION.md`、每条 run 的 `doctor.json` / `doctor.md`
  与 live 的 `run.json` / `run.md`（结论与可追溯读数）。
- **不入库**：`run-*/` 下的 `*.wav`、`*.log`、`dialog-events.json`、`tts-chunk-*`
  及临时 DB 沙盘产物（体积大、含本机路径、可重建）。规则在
  `docs/evidence/20261109-live-acceptance/.gitignore`，用 `git check-ignore -v` 可验证命中。

## 5. 当天仓库门禁

| 命令 | 结果 |
| --- | --- |
| `python scripts/check_protocol.py` | exit 0 |
| `python scripts/test_core.py` | exit 0 |
| `npm test` | exit 0 |
| `python scripts/run_evals.py`（离线层） | exit 0 |
| `python scripts/run_gates.py --only doc_drift,process_trail` | 全 PASS |

## 6. 仍需真机才能得出的结论

- B1 真实模型回复质量、情绪标签、首字延迟读数。
- B2 真麦克风采集与转写准确率（当前无 `--mic` / `--wav` 时只能 `skip`）。
- B3 真实 TTS 出声、音色与情绪听感。
- 长期记忆命中率、模型是否遵守负向约束。
- 用户正在用的 8000/8010 会话手感（本向导刻意不碰）。
