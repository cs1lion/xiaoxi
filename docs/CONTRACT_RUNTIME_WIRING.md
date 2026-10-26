# 契约层接进运行时：现状、前提纠正与本轮改动

- 分支：`feat/contract-runtime-wiring`（基于 `master 0bd90d6`）
- 授权：用户 2026-10-26 选定方案 A（把契约栈接进运行时），并确认 pixel-world-engine 分仓不影响本项目主线
- 本轮性质：侦察 + 修一个真缺陷。**没有接线**，原因见第二节

## 一、先纠正我自己给你的前提

我上一轮说「契约机器造好了没通电」，把接线说成接个插头。侦察（两路只读子代理 + 我自己实测）推翻了两处：

**① 规范 §2.2 的 `TaskEngine` 根本没被造出来。**

全仓 `class TaskEngine` 零命中。停放的是**迁移表**，不是引擎：

```
server/task/machine.py:771  TaskMachine.transition(state, rule_id, facts) -> TransitionResult
server/task/machine.py:786  TaskMachine.attempt(state, target, facts)    -> TransitionResult
```

规范里的 `submit(contract) -> TaskId` / `handle(cmd) -> CommandResult` / `view(id) -> TaskView`
只存在于散文（`docs/PIXEL_WORKSHOP_SPEC_V2.md:74-77`）。没有 TaskId 类型、没有任务注册表、没有持久化。
`server/task/__init__.py:9-12` 自己写明不做 §4.9 落账、§5 发事件、§9 Verifier、effect_log 持久化。

所以「接线」的真实工作量是：**造一个引擎**，把 40 字段的 `TaskFacts`（`server/task/model.py:130-173`）
从运行时组装出来，再喂给迁移表。不是接插头。

**② 「通电」这个词掩盖了一个更靠前的真缺陷。** 见第三节，那个我先修了。

## 二、双真相源：接线前必须先裁决的事

运行时用的是台账状态机，**不是** §4.2 那个：

| | 台账（在跑） | 契约层（停放） |
|---|---|---|
| 状态数 | 7（`threads.py:528-529`） | 13（`states.py:11`） |
| 迁移表 | `threads.py:531-538` | `machine.py:553-620`（T1–T24） |
| 谁写 | `dispatcher.py` 21 处 `transition()`；另有 `executive.py:202/214/216/376/381`、`durable_api.py:212` **绕过 dispatcher 直写** | 零生产调用方 |
| 审计 | `dispatcher.py:637` → `evaluator.review()` | `server/verify/`，零调用方 |

`server/task/projection.py` 是 Task→台账的单向投影，7 个目标状态与台账**逐字相同**（`test_task_fsm.py:217-219` 锁着）。但它有两个硬缺口：

1. **不查台账的迁移图**：`project_task()` 能算出台账会拒的非法边（例：台账 `blocked` 只能去 `running/done`）。
2. **不产 `blocked_question`**：台账 `threads.py:705-712` 要求进 `blocked` 必须带非空问题，而投影只有 `blocked_kind`。

另外 `dispatcher.py:277` 的 docstring 自称「唯一写 Commission 状态机的地方」，实测**不成立**（上表有 6 处直写）。接线时这条也得改，否则读代码的人会被误导。

**结论**：不先定「谁是写源」，接上去就是两套状态机抢一张表。这是设计裁决，属 Role 0，我不代签。

## 三、本轮修的真缺陷：契约层对老板写的验收规则是瞎的

### 症状（实测，非推断）

一单写了 3 条真实验收规则，契约层仍报「缺验收标准」：

```
台账 packet.acceptance : 3 条   <- 旧初审看得见
packet.contract_gaps   : 含 G4 missing=['acceptance']   <- 契约层看不见
「有规则」与「没规则」的缺口清单完全相同
```

### 根因

`dispatcher.py` 的 `_acceptance_criteria()` 这样构造：

```python
Criterion(text=text[:400], required=bool(item.get("required", True)))
```

而 `Criterion` 的签名是 `(id, text, kind, check, required, promotion_weight)` —— **缺 3 个必填参数**，
每次抛 `TypeError`，被 `except Exception: continue` 吞掉，**恒返回空列表**。

这条 2026-10-22 就写进了 `docs/CONTRACT_V2_SCOPE.md` 的意图（「把『这单离合格契约还差什么』记进密件，
否则契约层那 252 条测试覆盖的代码在运行时是死的」），但它对最关键的字段恰好是瞎的。

### 为什么不假装转换（冻结边界）

台账规则与契约 `CheckSpec` 是**两套不相通的词表**：

| 台账（`evaluator.py:356-363`） | 契约 `CHECK_TYPES`（`model.py`，规范 §3.1） |
|---|---|
| `artifact_exists` / `artifact_name` / `artifact_contains` / `artifact_json` / `artifact_regex` / `artifact_max_bytes` | `file_exists_nonempty` / `schema_valid` / `syntax_ok` / `exit_code_zero` / `csv_assert` / `rubric` / `ask_user` |

六个 kind 没有一个出现在七个 type 里。规范**未定义映射**。按用户口径「规范没写到的先留冻结边界，不臆造」，
所以不硬造转换，改成**如实点名**：

```
G4 missing = ["ledger_rule:artifact_exists", "ledger_rule:artifact_contains", ...]
             未知 kind  -> "unknown_rule:<kind>"
             降级占位    -> 不算老板写的规则（invalid / acceptance_rule_too_large / _overflow）
```

缺的是**映射**，不是**标准**。这句话现在能在落库数据里看到。

### 影响面（实测收小）

| 消费方 | 是否变化 |
|---|---|
| 初审 `dispatcher.py:637`（读 `packet.acceptance`） | **不变**，仍收到 3 条规则（测试第 6 组锁住） |
| 线上 `highlighted_slots`（`v2.py:246`，只用 `check:key`，不读 `missing`） | **不变**，仍是 `"G4:acceptance"` |
| 落库 `packet.contract_gaps[].missing` | 变：`["acceptance"]` → 逐条点名 kind |
| 派单是否被拦 | **不变**，仍是只读旁路（`_contract_gaps` 全异常吞掉） |

删除的死代码：`_acceptance_criteria()`（全仓零引用，含测试）。

### 验证

- 新增 `scripts/test_contract_ledger_rules.py`：**14/14 绿**，已挂 `run_gates` 为 `contract_ledger_rules`
- 第 5 组锁死 `dispatcher._LEDGER_RULE_KINDS` 与 `evaluator._ACCEPTANCE_RULES` **集合相等**，
  防止以后加规则种类时两边漂开
- **变异自证**：删掉桥接段 → 14 条里 5 条变红（`不再谎报缺 acceptance` / `逐条点名台账规则` /
  `两份清单不同` / `未知 kind 标成 unknown_rule` / `落库缺口点名了台账规则`）→ 还原后 14/14
- `test_core.py` 92/92、`check_protocol.py` PASS

## 四、接线还差什么（顺序，不是工作量承诺）

1. **定双真相源裁决**（Role 0）：台账为写源、契约层只投影？还是反过来？还是只让新委托走 FSM？
   不定这条，第 2 步没有落点。
2. **造 `TaskEngine`**：实现规范 §2.2 的 `submit` / `handle` / `view`，负责把 `TaskFacts` 从运行时组装。
   `TaskFacts` 的默认值偏宽松（`worker_available`、`inflight_reconciled`、`frozen_version_current`
   默认 `True`，`risk` 默认 `low`），**裸构造等于什么守卫都过**，必须显式填。
3. **补 `projection.py` 的两个缺口**：查台账迁移图 + 产 `blocked_question`。
4. **Verifier 接进 `VERIFYING`**：`server/verify` 有 3 种确定性 check 可跑、4 种显式跳过
   （`check.py:54-60`）；它 import `server.task`，而 `server/task` 禁止 import 它
   （`test_task_fsm.py:1646-1652`），只能靠**注入 callable**，不能反向 import。
5. **`scripts/test_verify.py` 补上**：`verifier.py:30` 与 `check.py:30` 都声称它机械强制了
   「禁 socket/连库/subprocess」，**该文件不存在**。这条是自述与实盘的不符，接线前顺手补。

## 五、红线与边界（本轮遵守情况）

- `server/assistant/executive.py`：**一行未动**（本轮只改 `dispatcher.py` + `run_gates.py` + 新增测试）
- 不新增 WS 事件 / API 字段：`check_protocol.py` PASS
- 不臆造规范没写的映射：词表不通就点名，不硬转
- 不动像素世界、不动 `pixel-world/`
- 台账 7 库、8000/8010 进程：未触碰
