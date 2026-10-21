# 契约 v2（§0–§3.7）实施边界与冻结清单

- 规范来源：`小汐 × 像素工坊：契约、状态机与事件协议 v2 规范.md`（草案 v0.1，2026-10-21）
- 实施范围：**仅 §0 – §3.7**
- 决策人：用户（2026-10-21 20:38，「0-3.7 先开工」）
- 状态：实施中

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

## 二、冻结边界（§4–§9 缺失，一律不实现）

**规范原文只写到 §3.7 就结束了。** 以下章节在文档里不存在，任何实现都是臆造，一律冻结：

| 缺失章节 | 本该定义什么 | 现在没有，所以不做 |
|---|---|---|
| §4 | Task / Step **双层状态机**迁移表 | 不做状态机。术语表里有 `Attempt`/`Block`/`Signature`，但**没有迁移表**，无法实现 |
| §4.6 | 失败分类表 | 不做失败分类 |
| §4.8 | Verifier 输入必须不含 Worker 自述 | 只能做一条「Verifier 请求结构里没有 worker_self_report 字段」的结构断言，**不做 Verifier** |
| §4.9 | 交付与 `effect_log` | 不做交付 |
| §4.10 | 记忆晋升与降级 | 不做晋升（那是动记忆系统的核心，且无规范） |
| §5 | 事件协议 v2 | **不做事件**。docx 里有 4 个旧事件（VOICE_STATE / TASK_STATE_CHANGE / TOOL_EXECUTION / VERIFICATION_REPORT），但那是 v1「极简协议」，v2 §2 已明确「不绑定快慢双系统的具体实现」——拿旧表当 v2 用是错的 |
| §6 | 权限与沙箱 | 不做权限。§3.1 有 `permissions_required: string[]` 字段，但 §7 的 L1–L5 等级定义缺失，**只做「字段存在且非空」的形状校验**，不校验等级合法性 |
| §7 | 可观测性 | 不做 |
| §8 | 评测集 | 不做（§3.5 提到「用评测集 A 类样本校准阈值」，但 A 类样本集未定义） |
| §9 | 里程碑验收证据 | 不做 |

### 冻结的具体后果（写清楚，避免以后有人以为是漏掉）

1. **`TaskEngine` / `Verifier` / `EventBus` 三个接口一行不写。** §2.2 给了签名，但没有行为规范；只给签名的空壳会在实现时被迫猜语义。
2. **`permissions_required` 只校验非空，不校验取值。** §3.2 的 G6 写「权限在 7.1 的可授予范围内」，但 §7.1 不存在。所以 G6 降级为「非空 + 元素为非空字符串」。
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

## 五、后续接续

规范补齐 §4–§9 后，按序接：
1. §4 状态机（迁移表是核心，缺它整个 M1 闭环立不起来）
2. §5 事件协议 v2（需要 §4 的 `to` 状态值）
3. §7 权限等级（`permissions_required` 才能真校验）

在此之前，`TaskEngine` 的写入口不存在，所以本包只做「契约能不能成立」的判断，不做「契约怎么被执行」。
