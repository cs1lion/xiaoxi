# 架构图 v2 · 逐条对照规范

**产物**：`docs/ARCH_V2_DIAGRAM.html`（自包含，直接打开）
**截图**：`docs/assets/arch-v2-{modules,timeline,blocked,map,overview}.png`
**规范基准**：`docs/PIXEL_WORKSHOP_SPEC_V2.md` @ `431ca65`（2026-10-22 10:21 采纳版）
**做法**：推翻 `tmp/arch-v2.html` 重做。判据是「能不能逐条对上规范行号」，不是像不像原图。

> **⚠️ 本图所有 v2 事件名都是「提案，未生效」**（§5 :765 标题即写明）。
> 图 A 里 v2 九类事件画在 EventBus 竖带（标「§5 提案 · 未生效 · 需 PROTOCOL_REQUEST」），
> **当前实际在跑的只有一条通路**：`commission_update`（WS 增量）+ 4s 轮询 + 30s REST 对账
> （`pixel-world/src/world/ledger.ts:56/58`），在图里用**紫色实线**单独画出，与提案态区分。
>
> **为什么必须分开画**：附录 C 那七条已经因为「图比规范新」返工过一次。
> 只画目标态会让读者以为那些事件正在用；只画现状又看不出要换成什么。**两条都画，线型区分。**

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

### 3.1 T13 / T17 缺 `resume_to` —— **已由规范 `6b23691` 补齐**

提出时（对 `431ca65` 核）：§4.2 :441 明确要求 `resume_to` 必须显式给出，但 §4.4 里 **T13** 只写了 `reason`、没写 `resume_to`；**T17** 连 `reason` 都没写。

**2026-10-22 10:46 规范 `6b23691` 的处理（与我建议的有一处不同）：**

| 项 | 我建议 | 规范实际采用 |
|---|---|---|
| T13 的 `resume_to` | `EXECUTING` | **`EXECUTING`** ✓ 一致 |
| T17 的 `reason` | 「`retry_exhausted`，或由 human 标准走 `need_clarification`」 | **两种都写，且规定并存时取 `need_clarification`**（要人来判，比「再试也没用」更具体）——比我的建议细 |
| T17 的 `resume_to` | `VERIFYING` | **`VERIFYING`** ✓ 一致 |
| `need_clarification` 的产生者 | 未提 | **补到 T17**（见 3.2） |
| `resume_to` 集合 | 未提 | **补入 `DISPATCHED`** —— 这是我没扫出来的，见 3.4 |

**最终状态（规范 `6b23691` 之后）**：

```
T13  EXECUTING → BLOCKED   reason ∈ {retry_exhausted, need_authorization}   resume_to=EXECUTING
T17  VERIFYING → BLOCKED   reason=retry_exhausted | need_clarification      resume_to=VERIFYING
                           （两者并存时取 need_clarification）
:441 resume_to ∈ {SPEC_LOCKED, DISPATCHED, EXECUTING, VERIFYING, DELIVERING}
```

图 C 已按 `6b23691` 更新：底部原来那行红字换成一行为绿色（`#008052`）的说明，**实际文本是「2026-10-22 已由规范 6b23691 补齐：」**，其后逐条列出补了什么（`resume_to` 集合补 `DISPATCHED`、T13 补 `resume_to`、T17 补 `reason` 与 `resume_to`）。

### 3.2 `need_clarification` 没有对应的 Task 迁移行 —— **已由 `6b23691` 补到 T17**

提出时：§4.2 :443 列了 5 个 `reason` 取值，但迁移表里**没有任何一行产生 `need_clarification`**，它只出现在 §4.6 :562 的 `E_INPUT` 去向栏。

**规范 `6b23691` 的处理比我查到的更硬**：作者写了机械核对（`reason` 集合 ∩ 每行 T 表文本），原版跑出 `need_clarification <- NONE`，其余四个各有 T9/T11/T13/T21。**补到 T17**，理由是 SM-17 本就要求在 `VERIFYING` 停下等人判，而停下必须落成带 `resume_to` 的 `BLOCKED`，T17 是唯一承载 human 标准的迁移。

**连带改了 SM-17**：原文「停在 `VERIFYING` 等 `ANSWER`」——停在非终态等于没有阻塞记录的悬挂，与 :441 冲突；已改为迁到 `BLOCKED(need_clarification)`。

图 C 里 `need_clarification` 那格已从「迁移表未单列」改为「T17（kind=human 标准）· resume_to=VERIFYING」。

### 3.3 `E_BUDGET` 的去向是二选一 —— **规范标为「待拍」，未替它选**

§4.6 :565 写 `E_BUDGET` 的去向是「`FAILED` **或** `BLOCKED(need_approval)`」。这是二选一，不是固定边。

**图 A 里没有画这个二选一的连线，原因是规范没给分派判据**——不是图的容量限制。同一份输入（`E_BUDGET`）能落两个终点，而规范没写什么情况下落哪个，画任何一条都是替规范做决定。

> **更正**：本节初稿写的理由是「画成两条会与『T14 契约违规→FAILED』冲突」。**这个理由不成立**——T14 存在与画不画这两条边无关；真正的问题是**分派判据缺失**，不是与别的迁移冲突。

**规范 `6b23691` 的处理**：明确写「本轮**没有**替它选」，迁移表保持原样（T14 的 `budget_hard_exhausted` → `FAILED`），`BLOCKED(need_approval)` 那条路的触发条件列入「待拍」。图 C 底部已加「仍待拍」一行，与规范同步。

### 3.4 `resume_to` 集合漏了 `DISPATCHED` —— 我没扫出来，规范自己找出来的

原 §4.2 :441 写 `resume_to ∈ {SPEC_LOCKED, EXECUTING, VERIFYING, DELIVERING}`，**没有 `DISPATCHED`**；而 T11 明写 `resume_to=DISPATCHED`。

**按原集合，T11 产出的状态违反本节自己的约束。** 规范 `6b23691` 把 `DISPATCHED` 补进集合（以 T11 为准，不是删 T11）。

**这条我漏了**：我核了 T13/T17 的 `resume_to` 缺失，但没做「所有 T 行出现的 `resume_to` 值是否都在集合内」这个反向核对。**正向查「缺什么」容易，反向查「多了什么」需要把枚举值全扫一遍。**

> **教训**：判据「每个 `reason` 都要有产生它的迁移行」（正向）和「每个 `resume_to` 值都要在集合内」（反向）是**两个方向**，我做了第一个就以为覆盖完了。

### 3.5 T19 的第三支（`risk==low` 且有副作用）—— 规范补了，图跟着补

**我初版没画这一支**，因为图 A 里 `ACCEPTED → DELIVERING` 只有一条线，没展开 T19 的条件分支。规范 `6b23691` 之后又有新提交补上了这一支：

原文 T19 只写两支：

- `risk==low` 且无副作用 → 直接进入
- `risk∈{medium,high}` → 必须 `APPROVE_SIDE_EFFECT`

**`risk==low` 但有副作用**这个组合落在两支之间的缝里，规范没说怎么办。补的判据是：**要不要批准取决于「有没有副作用」，与 `risk` 无关**——`APPROVE_SIDE_EFFECT` 防的是「会改变世界的那一下」，而 `risk` 是按槽位置信度算的，两者是不同维度（§3.4 与 §6.2 各自定义）。

图 A 已在 `ACCEPTED → DELIVERING` 那条边旁标出三支，第三支注明「2026-10-22 补，规范原文两支之间的缝」。

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
