# 契约 v2（§0–§3.7）实施边界与冻结清单

- 规范来源：`docs/PIXEL_WORKSHOP_SPEC_V2.md`（v1.0 采纳版；2026-10-22 用户裁定为其本人所写）
- 实施范围：**§0–§3.7 已实施；§4–§9 自 2026-10-22 解冻**（见第二节「解冻记录」）
- 决策人：用户（2026-10-21 20:38「0-3.7 先开工」；2026-10-22「是我做的」→ §4–§9 采纳）
- 状态：§3 实施完毕；§4–§9 开始实施，顺序见 `docs/V2_SPEC_INTEGRATION.md` 第三节

> **2026-10-22 凌晨更新（§3.8 已落地）**：
> - **R1 命令白名单**已做：`config/contract.yaml` 的 `lint.allowed_commands` + `lint.py:_cmd_allowed`，
>   黑名单保留作第二道。测试在 `test_contract.py` 的 `[K]` 节。
> - **R2 纯语义任务判定**已做：`SEMANTIC_ONLY_ARTIFACT_KINDS = {report_md, message, chart}`。
>   原先「只要有 human 标准就放行」是真漏洞——写一条 human 就能绕过 deterministic，已修。
> - **R3 `promotion_weight` 语义**未做：它要接 §5.6 的 `MEMORY_UPDATE` 事件，而那是新增 WS 事件，
>   **必须先过 `[PROTOCOL_REQUEST]`**。语义本身由 §3.8 定死（乘 procedural 经验，不乘 stated 事实），
>   实现等协议放行。
>
> 另：契约层同日**接进派单路径**（`dispatcher.dispatch()` → `_contract_gaps()`），只读旁路、不拦派单。
> 此前 `app.py` 对 `server/contract/` 是 0 处引用，252 条测试覆盖的是死代码。

## 一、可实施清单（§3 完全可判定）

| 章节 | 内容 | 落点 |
|---|---|---|
| §3.1 | `TaskContract` 及全部子结构（Slot / SlotSource / InputRef / ArtifactSpec / Budget / SideEffect / RiskAssessment / Criterion / CheckSpec / CsvRule） | `server/contract/model.py` |
| §3.2 | G1–G6 字段约束 | `server/contract/gate.py` |
| §3.3 | `IntentGate`：G1–G6 检查 + 追问策略（每轮 1 问、按影响排序、上限 3 轮、保守默认值 + highlight） | `server/contract/gate.py` |
| §3.4 | 风险规则表（取副作用最高值）+ 兜底规则 + 按风险的确认策略 | `server/contract/risk.py` |
| §3.5 | 置信度阈值行为表 + `memory` 配置项（silent_fill_min / confirm_fill_min / side_effect_slots_always_show） | `server/contract/confidence.py` + `config/contract.yaml` |
| §3.7 | 验收标准 lint L1–L7 + 内置 CsvRule 规则库最小集（6 条纯函数） | `server/contract/lint.py` + `server/contract/csv_rules.py` |

另外 §2.2 的四个接口签名（`IntentGate` 部分）与 §2.1 的 P1/P2/P3/P4/P5/P10 属于可判定原则，做进单测。

## 二、冻结边界（2026-10-22 解冻）

> **解冻记录（2026-10-22）**：本节冻结的条件是「规范原文只写到 §3.7 就结束了」。
> 用户随后补齐 §4–§9 并裁定为其本人所写，该条件不再成立，**冻结失效**。
> 触发的是本文件第五节自己写下的接续条件（「规范补齐 §4–§9 后，按序接」），不是推翻已批准的决定。
> 下表保留作历史：它记录「当时缺什么、为什么不敢做」，**不是现在的禁令**。
>
> **编号已变**。下表用旧编号，与 v1.0 规范的对应关系：

| 本表旧编号 | v1.0 规范实际位置 | 说明 |
|---|---|---|
| §4.10 记忆晋升与降级 | **§5.6** | 改由 `MEMORY_UPDATE` 事件承载 |
| §7 / §7.1 权限等级（L1–L5） | **§6.2（L0–L6）** | 多出 `L6`「花钱/安装/工作区外写入」，默认不可授予 |
| §4.9 交付与 `effect_log` | **§4.4 T20 + §5.2** | 无独立章节：交付由 T20 迁移与 `DELIVERY_EFFECT` 事件定义 |
| §4.6 失败分类 | §4.6 | 未变 |
| §7 可观测性 | §7 | 未变 |

以下是冻结期的历史原文（当时为什么不做）：

**规范原文只写到 §3.7 就结束了。** 以下章节在文档里不存在，任何实现都是臆造，一律冻结：

| 缺失章节 | 本该定义什么 | 现在没有，所以不做 |
|---|---|---|
| §4 | Task / Step **双层状态机**迁移表 | 不做状态机。术语表里有 `Attempt`/`Block`/`Signature`，但**没有迁移表**，无法实现 |
| §4.6 | 失败分类表 | 不做失败分类 |
| §4.8 | Verifier 输入必须不含 Worker 自述 | 只能做一条「Verifier 请求结构里没有 worker_self_report 字段」的结构断言，**不做 Verifier** |
| §4.9 | 交付与 `effect_log` | 不做交付 |
| §4.10 | 记忆晋升与降级 | 不做晋升（那是动记忆系统的核心，且无规范） |
| §5 | 事件协议 v2 | **不做事件**。docx 里有 4 个旧事件（VOICE_STATE / TASK_STATE_CHANGE / TOOL_EXECUTION / VERIFICATION_REPORT），但那是 v1「极简协议」，v2 §2 已明确「不绑定快慢双系统的具体实现」——拿旧表当 v2 用是错的 |
| §6 | 权限与沙箱 | 不做权限。§3.1 有 `permissions_required: string[]` 字段。~~§7 的 L1–L5 等级定义缺失，只做「字段存在且非空」的形状校验，不校验等级合法性~~ **（已失效：新规范 §6.2 给了 L0–L6 表，取值域可校验。见下方「解冻记录」）** |
| §7 | 可观测性 | 不做 |
| §8 | 评测集 | 不做（§3.5 提到「用评测集 A 类样本校准阈值」，但 A 类样本集未定义） |
| §9 | 里程碑验收证据 | 不做 |

### 冻结的具体后果（写清楚，避免以后有人以为是漏掉）

1. **`TaskEngine` / `Verifier` / `EventBus` 三个接口一行不写。** §2.2 给了签名，但没有行为规范；只给签名的空壳会在实现时被迫猜语义。
2. ~~**`permissions_required` 只校验非空，不校验取值。** §3.2 的 G6 写「权限在 7.1 的可授予范围内」，但 §7.1 不存在。所以 G6 降级为「非空 + 元素为非空字符串」。~~ **（已失效，见下方「解冻记录」）**
3. **`max_retries` 等预算字段只做类型与正数校验**，不接任何重试逻辑（重试属于 §4 状态机）。
4. **`CheckSpec` 只做结构与 lint 校验，不执行。** 例如 `exit_code_zero` 的 `cmd` 只检查是非空字符串，**不跑命令**。执行侧属于 §4.9 交付与 Verifier，冻结。
5. **`csv_assert` 的 6 条规则实现为纯函数并单测**——这是 §3.7 明确列出的「M1 最小集」，规范说「每条规则必须是纯函数、无网络、可在 1 秒内跑完」，属于可判定范围。
6. **§3.5 的阈值校准不做**（要评测集）。配置项落地并用规范给的默认值；代码里不写死常量。

## 三、实施纪律

- **不碰** `server/assistant/executive.py`（用户本人管）
- **不改** 现有任何 WS 事件 / API 字段（协议 §六 红线）
- **不改** 现有台账 schema
- 新代码全部在 `server/contract/` 新包内，**不 import 现有业务模块**，保持可单独测试
- 每个 G / L / 风险 / 置信度规则都要有单测，且**必须有反例**（只测正例等于没测）

## 四、验收判据

1. `scripts/test_contract.py` 全绿，覆盖 G1–G6、L1–L7、风险表 8 行 + 兜底、置信度 4 档、CsvRule 6 条
2. 每条规则都有「通过」与「拒绝」两类断言
3. §3.6 的完整示例（竞品表格）能作为一个真实 fixture 跑通 lint，且置信度/风险判定与规范文字一致
4. `python scripts/check_protocol.py` 与 `scripts/test_core.py` 不受影响
5. 本文件第二节列出的冻结项，代码里**一个都不出现**

## 五、后续接续（2026-10-22 已启动）

原计划的顺序照旧，只是「权限」在新规范里是 §6.2：

1. **§4 状态机**（迁移表是核心）— 进行中，落点 `server/task/`
2. §4.6 失败分类 / §4.7 重试 — 同上
3. §6.2 权限等级 L0–L6（`permissions_required` 才能真校验）
4. §7.1 事件写 JSONL（只写本地文件，不发 WS）
5. Verifier（复用 `server/assistant/evaluator.py`，不新造）
6. §4.9 交付与 `effect_log`（幂等）
7. §8 评测集 D 类（§4.10 的 18 条迁移用例）

**仍然各自要门的**：
- §5 事件协议 v2 → 必须 `[PROTOCOL_REQUEST]`

### 解冻记录（2026-10-22）

条件成熟后按本节第五节自己写的接续条款执行，不是推翻已批准的决定。

| 原冻结条目 | 解冻依据 | 现状 |
|---|---|---|
| §6 / 冻结后果 2：`permissions_required` 只校验非空，不校验取值 | 新规范 §6.2（L952–967）给出 L0–L6 七级表，且 **L954 原话下令**：「`permissions_required` 的取值域就在这张表。G6 从『非空字符串』升级为『必须在本表内』。」另：本节第五节第 3 条本就排了这一步 | **3a 测试侧已改**（`scripts/test_contract.py` 双向断言：L0–L6 不产生 gap / 乱码等级产生 gap）；gate 层实现由 Agent 集成工程师落地，**未完成前该套件有 5 条红**，属测试先行的预期状态 |
| 同上，shape 层（`parse_contract`）是否也要同步校验等级 | 架构未裁定（校验放 gate 还是 shape） | **不动**，等裁定后单独一次提交 |

**3b 遗留缺口（不在本次解冻范围，已单独立卡）**：规范 §6.2 L966–767 有一条跨字段硬断言——
「`permissions_required` 里的最高等级，本身就是 `risk` 的下界；L5/L6 出现在契约里，`risk` 不得低于 `high`。
这条是硬断言，不是建议：风险规则算漏了，权限表兜住。」
而 `server/contract/risk.py` 的 `compute_risk` 只收 `side_effects`，不读权限等级，**兜不住**。
只升 G6 取值域不收工，仍会放过 `L6 + risk=low` 这种自相矛盾的契约。
- IntentGate 接进 `_run_turn`（替换 `server/app.py:736` 的正则路由）→ Role 0 批准

在最后这条批准之前，运行时行为不变：本包只判断「契约能不能成立」，不决定「契约怎么被执行」。
