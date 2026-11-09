# playback_done 最小集 + durable 空转档（2026-11-09）

基线：`52bb778`（= origin/master，干净工作树）。本目录只收本轮四项批准的实测读数。

## 1. 8000 冷启动与记忆端点

| 项 | 改前（12:2x 探测） | 改后 |
|---|---|---|
| 8000 监听 | 无（连接被拒） | `scripts/run.py --port 8000`，3s 内 LISTEN |
| `GET /api/memories` | 连不上（报告中的 500 属旧进程） | **200**，返回 3 条记忆 |
| `GET /api/memories/context?query=沙盒` | 连不上 | **200**，命中 id=524 |
| 不带 query | — | 422（`query` 是必填参数，不是 500） |

原始读数见 `runtime.txt`。

副作用披露：`/api/memories/context` 是「检索即记账」的读接口，这次探测把 id=524 的
`use_count` 从 0 推到 1、写了 `last_used_at`。属接口设计行为，未手工回滚。

## 2. playback_done（Role 0 批准：最小集 + 空闲基准）

- 协议：`docs/PROTOCOL.md` §六新增事件说明（turn_id + status，单向不回包）。
- 服务端：`Connection.note_playback`（`server/app.py`），只认当前轮/最近一次 `done`；
  `completed|stopped` 才置确认并推进 `last_spoke_at`，`failed` 只记录；
  `done.delivery.playback_confirmed` 仍恒 `False`。
- 前端：`web/app.js` 在队列自然清空（completed）、`stopPlayback`（stopped）、
  队列解码/播放失败（failed）时上报一次。
- 静态检查：`check_protocol.py` 新增三方认账（PROTOCOL.md / app.js / app.py）。

先红后绿证据：`test_playback_done.py` 首跑 8 条全 `AttributeError:
'Connection' object has no attribute 'note_playback'`；实现后 8/8 OK。

浏览器夹具新增两条可观测断言（`scripts/test-voice-browser.mjs`）：
自然播完上报 `completed`、被打断上报 `stopped`；`npm run test:voice:browser` exit=0。

## 3. durable 空转档

- `config/agents_roster.yaml` 的 `durable.switch_agents: [scribe]` 会在启用时把
  scribe 切到 durable；实测默认启用 = **已绑定 1 个 Worker**（不是空转）。
- 空转档追加本地 `.env`：`XIAOXI_DURABLE_ENABLED=1` +
  `XIAOXI_DURABLE_SWITCH_AGENTS=none`。重启后读数：
  `[durable] 执行内核就绪 http://127.0.0.1:53851；已绑定 0 个 Worker`。
- sidecar 进程存活（`node --experimental-strip-types agent-runtime/src/main.ts`），
  `data/durable/sidecar.err.log` 有 `skills=0 mcp=echo` 就绪行。
- 本轮不产生任何真实派单（roster 无 `adapter: durable`，切换名单为空）。

## 4. procedural_memory_eval 20 条 SKIP

实测 `Ran 24 tests / OK (skipped=20)`。裁决：维持 SKIP。`human_card` 是金标准，
合成它等于自编参考答案；已在 `scripts/run_gates.py` 的 gate 条目和
`scripts/test_procedural_memory_eval.py` 注释里写明「政策阻塞，非能力阻塞」。

## 5. 本轮验证读数（同一台机、同一工作树）

| 命令 | 读数 |
|---|---|
| `python scripts/check_protocol.py` | PASS（22 消息类型 / 122 路由 / ui_nav 一致） |
| `python scripts/test_playback_done.py` | Ran 8, OK |
| `python scripts/test_gate_coverage.py` | 磁盘 183 = 登记 179 + 清单 4，PASS |
| `python scripts/test_voice_turns.py` | Ran 35, OK |
| `python scripts/test_run_gates.py` | Ran 23, OK |
| `python scripts/test_core.py` | 134 passed, 0 failed |
| `npm test` | exit=0 |
| `python scripts/run_evals.py` | 18 passed, 0 failed |
| `python scripts/run_gates.py --only protocol,playback_done,core` | 3 跑过并断言 / 0 失败 / 0 跳过 |
| `python scripts/run_gates.py`（全量 218 条） | **216 跑过并断言 / 1 失败 / 1 跳过**：失败是 `doc_drift`（本轮插入 `note_playback` 使 `TESTING.md` 的 5 个行号锚漂移）；已按 `ast` 实测重锚，单跑 `test_doc_drift.py` 7/7 OK。跳过仍是 `procedural_memory_eval`（已知政策阻塞） |
| `npm run test:voice:browser` | exit=0（含两条新 playback_done 断言） |

原始输出：`gates-selected.txt`、`gates-full.txt`、`voice-browser.txt`、`runtime.txt`。

## 6. 未验证 / 边界

- 未跑真实 LLM/TTS/ASR：StepFun `step_plan` 订阅未激活，凭据仍会 400。
  `playback_done` 的服务端行为由离线契约测试覆盖，未做真人 `--mic` 端到端。
- 浏览器断言只证明「事件按终态发出」，不证明 ambient 触发时刻的听感收益。
- `.env` 两行是本机配置，未入库；回滚 = 删除 `XIAOXI_DURABLE_ENABLED` 与
  `XIAOXI_DURABLE_SWITCH_AGENTS` 两行后重启 8000。

## 7. 合并状态与库三值（2026-11-09 复核）

- `master` = `origin/master` = `52bb778`，0/0；`604994e / 38ce3f3 / c0c0df1 /
  c00cdb3 / 3895ef0 / efa1050` 全部是 master 祖先。
- `run_gates.py` 登记数 218 = 交接单的 216 + `store_import_inert`（efa1050 新增）
  + `playback_done`（本轮新增）。
- `data/memory.db` 三值：`user_version=5`、`size=286720`（未变）、
  `mtime_ns=1794198977950429500`。
  - **`user_version` 期望值是 5，不是交接单里的 4**：`server/memory/store.py:33`
    的 `SCHEMA_VERSION = 5`，启动新代码时正常迁移到 5。4 是旧基线（对应
    249a071/86fafa4 时代）的读数。
  - `mtime` 变化有两个来源：启动时迁移 + `/api/memories/context` 的检索记账
    （use_count）。`size` 未变，说明没有重建库、没有追加整表数据。
  - `import` 不建库这一点由 `store_import_inert` 门禁单独守，不靠本文件的三值。

