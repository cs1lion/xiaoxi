# 架构图 v2 · 逐条对照规范

**产物**：`docs/ARCH_V2_DIAGRAM.html`（自包含，直接打开）
**截图**：`docs/assets/arch-v2-{modules,timeline,blocked,map,overview}.png`
**规范基准**：`docs/PIXEL_WORKSHOP_SPEC_V2.md` @ `431ca65`（2026-10-22 10:21 采纳版）
**做法**：推翻 `tmp/arch-v2.html` 重做。判据是「能不能逐条对上规范行号」，不是像不像原图。

---

## 一、附录 C 的七条

| # | 附录 C 原问题 | 规范依据（节 : 行） | 本图怎么改的 | 状态 |
|---|---|---|---|---|
| 1 | 没有 BLOCKED 分支 | 附录 C :1234 · §4.2 :433/:441/:443 · §4.4 T9/T11/T13/T17/T21/T22/T23 · §4.6 :555–568 | 图 C 整幅。**BLOCKED 画成单节点**，下挂 5 个 `reason` 取值块（各带 `resume_to` 与触发它的 T 号），右接 T22 恢复 / T23 取消；另单画 Step `UNKNOWN` 并标明「≠ BLOCKED」 | 已改 |
| 2 | 事件总线只从 Verifier 出来 | 附录 C :1235 · §5.2 :715–725（9 类事件来自 6 个模块） | 图 A 右侧竖带，**8 条上报边**分别来自 IntentGate / MemoryView / System1_Voice / TaskEngine / Worker / Verifier / 交付执行器 / BLOCKED，每条边上标该模块发出的事件名 | 已改 |
| 3 | `Task_Contract` 被画成进程节点 | 附录 C :1236 · §4.1 :419（契约只有一份，Step 不持有副本） | 图 A 用**虚线直角框**（与圆角进程框区分），框内标「数据不执行 / version 冻结后只读」，并注明它是派单的唯一入口（P1） | 已改 |
| 4 | `Sync` 边语义不明 | 附录 C :1237 · §2.3 :96 · §5.2 :719 · §5.6 :813–817 | 拆成两条且时刻不同：**`read`**（MemoryService→MemoryView→IntentGate，派单前补槽，细实线）与 **`promoted/confirmed`**（用户确认后写回 MemoryService，粗实线）。两条都不再叫 `Sync` | 已改 |
| 5 | 五阶段线性播放 | 附录 C :1238 · §5.7 :829–847（e1–e17 带真实 `ts`） | 图 B 换成**时间轴 + 四泳道**：横轴是 §5.7 的相对时刻（0.0–3.5s），纵轴是 用户 / Task / Step / 记忆·交付。同一竖线上的方块即同时发生；两条 Step 并发用绿色竖线标出 | 已改 |
| 6 | 缺「用户」参与者 | 附录 C :1239 · §5.2 :720 · §5.3 :747–754 | 图 A 顶部与图 C 右侧各画「用户 · 神谕祭坛」。连到它的边：`ASK_USER`（出）、`CONFIRM_CONTRACT` / `ANSWER` / `APPROVE_SIDE_EFFECT`（入）、`DELIVERY_EFFECT`（送达） | 已改 |
| 7 | 缺记忆写回的回边 | 附录 C :1240 · §5.6 :811–817（5 个 action） | 图 A 两条**粗细不同**的回边：`distilled` 细虚线（Verifier 打回中提炼，写 `lessons`）· `promoted/confirmed` 粗实线（用户确认后升级来源）。二者触发条件不同，故不合并 | 已改 |

---

## 二、附录 C 没写、但 §2.3 对不上的三条

| # | 问题 | 规范依据（节 : 行） | 本图怎么改的 | 状态 |
|---|---|---|---|---|
| A | `IntentGate` 被 `System1_Voice` 吞掉 | §2.3 :96（`[IntentGate] ◄── [MemoryView]`）· §5.2 :719（`CONTRACT_PROPOSED` 由 IntentGate 发） | 图 A 判断层把它画成**独立节点**，与 System1_Voice 平级但分离；Voice→IntentGate 是「文字」边，IntentGate→契约是「freeze」边 | 已改 |
| B | 缺 `Worker 池` | §2.3 :99（`[TaskEngine] ──派单──► [Worker 池] ──产出物──► [Verifier]`）· §2.1 P3 :54（执行者不自评） | 图 A 执行层画出 Worker 池，链路是 TaskEngine→派单→Worker→产出物→Verifier。**Worker 与 Verifier 分开画，P3 才表达得出来**——画在一起就等于让执行者自评 | 已改 |
| C | 交付不是 Verifier 的出口 | §2.1 P4 :55（副作用后置于 ACCEPTED）· §5.2 :723（`DELIVERY_EFFECT` 由「交付执行器」发） | 图 A 新增独立「交付执行器」节点。入口来自 TaskEngine 的 `ACCEPTED → DELIVERING`，**Verifier 不直接连用户**。交付执行器→用户是 `DELIVERY_EFFECT` | 已改 |

---

## 三、判断「规范和图谁该改」的地方

以下三处，我**没有自行补值**，而是照规范原样画并标出缺口。理由：这三处是规范内部不自洽，改图等于替规范做决定，应当由规范作者裁决。

### 3.1 T13 / T17 缺 `resume_to`（图 C 底部红字标出）

§4.2 :441 明确规定「`BLOCKED.resume_to ∈ {SPEC_LOCKED, EXECUTING, VERIFYING, DELIVERING}`，**必须显式给出**：没有 `resume_to` 的阻塞等于把任务丢进黑洞」。

但 §4.4 的迁移表里：

- **T13**（`EXECUTING → BLOCKED`）：只写了 `reason ∈ {retry_exhausted, need_authorization}`，**没写 `resume_to`**
- **T17**（`VERIFYING → BLOCKED`）：连 `reason` 都没写，只有守卫条件

**这直接违反 :441 自己的硬约束。** 建议规范补：T13 补 `resume_to=EXECUTING`，T17 补 `reason=retry_exhausted` + `resume_to=VERIFYING`（或由 human 标准走 `need_clarification`）。

### 3.2 `need_clarification` 没有对应的 Task 迁移行

§4.2 :443 列出 `reason ∈ {need_clarification, need_approval, need_authorization, retry_exhausted, ambiguous_result}`，共 5 个取值。

但 §4.4 迁移表里，`need_clarification` 只出现在 §4.6 :562 的 `E_INPUT` 去向栏（「`BLOCKED(need_clarification)`」），**迁移表里没有任何一行产生它**。

图 C 里我把它画出来了，但标注为「`E_INPUT` 默认去向（§4.6 :562）· 迁移表未单列」。

### 3.3 `E_BUDGET` 的去向是二选一，图里只能画一条

§4.6 :565 写 `E_BUDGET` 的去向是「`FAILED` **或** `BLOCKED(need_approval)`」。这是二选一，不是固定边。图 A 里 FAILED 与 BLOCKED 并列，**没有连线**表达这个二选一，因为画成两条会与「T14 契约违规→FAILED」冲突。

如果规范能定下「什么情况下 `E_BUDGET` 落 `FAILED`、什么情况下落 `BLOCKED`」，图 A 就能补一条确定边。

---

## 四、草稿 `tmp/arch-v2.html` 为什么没用

我核了草稿，七条里它改了 3 条、改错 3 条、缺 1 条：

| 草稿的做法 | 与规范对照 | 判断 |
|---|---|---|
| 画了 `BLOCKED` / `UNKNOWN` / `RETRY_EXHAUSTED` 三个**并列 Task 节点** | §4.2 :433 只有 `BLOCKED` 一个 Task 状态；`UNKNOWN` 是 **Step** 状态（:458）；`RETRY_EXHAUSTED` 根本不是状态，是 `reason` 的取值（:443） | **改错** |
| `Async_Orchestrator` 直接出 artifact 给 Verifier | §2.3 :99 中间有 `Worker 池` | **缺** |
| Verifier→用户 标 `deliver` | §5.2 :723 `DELIVERY_EFFECT` 由「交付执行器」发；P4 :55 副作用后置 | **改错** |
| 只画 `distilled` 一条记忆回边 | §5.6 有 `promoted` / `confirmed` 两条事实线 | **改错**（附录 C 第 7 条明确要求粗细不同两条） |
| 四条横带当泳道 | 附录 C :1238 要的是「时间轴 + 泳道」，横带只表达分层，不表达并发 | **改错** |
| 补了 IntentGate、用户节点、契约卷轴 | 与规范一致 | **对** |

**结论：草稿改对了 3 条（IntentGate、用户、契约形态），改错 4 条。** 改错的那几条都是「把规范里的一个字段当成一个独立节点」——BLOCKED 的 reason 被拆成三个状态、记忆的两个 action 被画成一条边。重做时这两处是最花时间的。

---

## 五、复现

```powershell
# 截图（ego-browser 自连；dpr 必须强制为 1，否则 1600px 宽会被放大 2.25 倍导致截图超时）
ego-browser nodejs -e "await import('file:///C:/Users/18712/.dsh/bot/workspace/ego-scripts/arch-v2-20261022/final-shots.mjs')"
```

**截图踩到的三个坑**（都记在脚本注释里）：

1. **窗口最小化** → 先 `Browser.setWindowBounds` `normal` 再 `maximized`，且**每次截图前都要做一次**（不是只做一次）
2. **`devicePixelRatio` 默认 2.25** → 必须 `Emulation.setDeviceMetricsOverride` 强制 `deviceScaleFactor: 1`
3. **`clip` 参数会稳定超时**（连 200×200 都超）→ 改用「隐藏其他块 + 视口高度设为内容高度 + 不带 clip 的整页截图」

---

## 六、本图没有覆盖的东西

- **§5.7 的 e1–e17 完整回放**：图 B 只画了 Task/Step 的主干，`TOOL_EXECUTION` 与 `AGENT_PRESENCE` 两类「至多一次（可丢）」事件没画，因为它们不影响状态机。
- **§6 权限层（L0–L6）**：完全没画。它属于运行环境约束，不是模块关系，画进来会让图变成两张图的叠加。
- **§4.3 的 13 态 → 台账 7 态投影**：没画。它是单向投影，画出来会让「Task 状态」和「台账状态」两组框混在一起，反而看不清主链路。
