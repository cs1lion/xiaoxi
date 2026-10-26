# [PROTOCOL_REQUEST] M5/M6 主体：executive.py 收口扫描、progress 落库与停滞检测

**日期**：2026-10-26
**依据**：`docs/AGENT_PLAYBOOK.md` Global Rule 1（Protocol First：不得自造未注册的 WS 事件/API 字段，装不下就停下发请求）与 Rule 2（Strict File Ownership：越界改动停下发请求，Role 0 裁决）；`AGENTS.md` 项目叠加红线（改 `server/assistant/executive.py` 前必须先问）
**前置**：M1-M4 与 M5/M6 不越线子集已于 2026-10-26 迭代轮落地并复审（见 `docs/TOOL_OPERATION_EXPLORATION_20261024.md`「落地台账」节），门禁全绿。本请求只覆盖被红线门控的主体部分，格式照 `docs/PROTOCOL_REQUEST_STEP8.md`。

---

## 1. 结论：需要 Role 0 放行三组改动，另有一件事需要裁决

| 组 | 涉及文件 | 触碰的红线 | 建议 |
|---|---|---|---|
| A | `server/assistant/executive.py`、`server/app.py` | AGENTS.md：改 executive.py 前必须先问 | 放行（缺口已被测试复现） |
| B | `server/interaction/threads.py`（+ 或 `docs/PROTOCOL.md` §19） | Global Rule 1：commission_events 的 kind 枚举属协议台账语义 | **默认走替代方案，不开协议门** |
| C | `server/assistant/executive.py`、`server/assistant/executive_api.py` | executive.py 红线 + API 面属 Global Rule 1 | 只放行「只报不转」那一步 |
| D | `server/assistant/durable_api.py`、`scripts/check_protocol.py` | Global Rule 1：新 REST 路由属 API 面 | 留后续 effort（M4 复审 MINOR-2） |

### A 组：M5 主体——非 durable 单的收口扫描

**现状（已复现的缺口）**：初审打回把单迁回 running 后，如果驱动来自 answer / resume / reassign / REST `/run` 四条单趟入口（executive.py:488-507、:541-543、executive_api.py:145-152），这一单就 running 且无人驱动；`reconcile_durable` 显式跳过 adapter != durable（executive.py:409-410），周期循环本身也只在 durable 绑定时创建（server/app.py:1634-1637）。缺口由 `scripts/test_stalled_running.py` 第二段钉住（启动收口用过之后，会话内新僵单没人收）。

**建议改动**：

1. `server/assistant/executive.py`：在 `reconcile_durable` 之外加一个不依赖 durable 的收口扫描——running 且 `dispatcher._active_runs` 无租约的单转 blocked（running→blocked 是 §19 合法迁移，threads.py 的 TRANSITIONS）；并把四条单趟入口改走 `drive()` 的重试网（executive.py:269-288），消灭「初审打回后无人驱动」。
2. `server/app.py`：把该扫描从 durable 分支里挪出来，独立 asyncio 任务周期跑；间隔复用 roster 的 `durable.patrol_seconds` 或新增配置键（新增键必须同步 `scripts/audit_config_keys.py` 的访问器白名单，否则静默失效）。

**风险与缓解**：收口阈值必须显著大于适配器 timeout（真机 CLI 单次 420s，config/agents_roster.yaml:98），且只对非 durable 适配器生效；动单前先查 `_active_runs` 租约，否则重复开工。

**验收**：`scripts/test_stalled_running.py` 第二段断言翻转为 blocked；`test_restart_recovery` / `test_durable_reconcile` / `test_core` / `test_executive_v3` 无回归。

### B 组：M6 主体——progress 落库

**现状**：progress 的 detail 只进 WS 不落库，`commission_events` 只追加 opened / transition / step 三种 kind（threads.py:580-582、:676、:735、:749），断线或复盘只剩 step_idx 整数。

**建议改动（二选一）**：

- 替代方案（默认建议，不开协议门）：继续用 M6 子集已落地的只读面满足复盘——`GET /internal/durable/runs/{commission_id}`（durable_api.py）+ durable 适配器的 since 游标断点续读（durable.py）。不落新 kind、不碰 threads.py。
- 落库方案：给 `commission_events` 增第四种 kind 持久化 progress detail。表上 kind 是自由 TEXT、schema 层不需迁移，但语义属 §19 台账规约，按 Global Rule 1 需 Role 0 决定是否在 `docs/PROTOCOL.md` §19 补 kind 枚举说明。

### C 组：M6 主体——停滞检测与 progress 查询暴露

**建议改动**：

1. `server/assistant/executive.py`：停滞检测把「既不重复也不产出」的 running 单在阈值后转 blocked。安全第一步是只报不转——信号并入既有 telemetry 的 `spinning_alerts` 同层面（server/assistant/telemetry.py:282-284），不做状态迁移；若要自动迁移，与 A 组一并批准。
2. `server/assistant/executive_api.py`：把 progress 查询暴露成 REST/WS。任何新路由或新响应字段都属 API 面，按 Global Rule 1 需 Role 0 定义；或明确不做，只保留内部只读面。

### D 组：M4 留账——工具目录的宿主 HTTP 出口

**现状**：M4（issue #63）落地后，sidecar 侧 `GET /v1/runs/{id}/tools` 与宿主侧库方法 `durable` 适配器的 `tools(commission_id)` 都通了，但宿主侧没有 HTTP 出口——排障者想按 commission_id 查工具目录，仍须直连 sidecar 端口并带 Bearer token（M4 复审 MINOR-2）。

**建议改动**：`server/assistant/durable_api.py` 照 `run_view` 形态补 `GET /internal/durable/runs/{commission_id}/tools`（同步 def、`_require_token`、`callable(getattr(adapter, "tools", None))` 守卫 + None→404 语义），`scripts/check_protocol.py` 的 `INTERNAL_ROUTES` 同步登记。约 20 行，不动 executive.py。

---

## 2. 需要 Role 0 裁决的另一件事（不属本迭代）

`gate_coverage` 门禁常驻红：并发会话的 `scripts/test_memory_retrieval_quality.py` 未登记进 `run_gates.GATES` 也未进 `gates_unregistered.txt`（三次复查依旧）。两条合规修法都要动共享文件且涉及对该测试运行方式的判断，归该文件所有者或 Role 0 决定；本迭代不擅自代劳。

---

## 3. 批复方式

逐组给「放行 / 不放行 / 改用替代方案」。放行后按 M 系列既有流程动工（grilling → to-spec 发 issue → implement 驱动 tdd → 子 agent 两轴复审 + 对抗验证）；红线文件由 Role 0 自行修改，或明确授权后由执行方修改。
