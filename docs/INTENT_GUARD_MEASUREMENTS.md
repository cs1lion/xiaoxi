# 意图怀疑层 · 阶段一实测记录

- 分支：`feat/intent-guard`（基于 master `291a250`，含 PR #6 / #8）
- 代码：`server/dialogue/intent_guard.py` + `persona.py` + `orchestrator.py` + `scripts/test_intent_guard.py`
- 前置：`docs/INTENT_ROUTING_BASELINE.md`（PR #7）、`docs/INTENT_PHASE1_DESIGN.md`

## 一、虚报率（真实 API `step-3.7-flash`，生产提示词）

12 条同义改写 × 3 次 = 36 次（未命中的句子跳过，实际计 33）：

| 措辞 | 虚报 | 空回复 |
|---|---|---|
| **无约束（改动前）** | **20/36 = 56%** | 4/36 |
| 短措辞（只禁「接单了」） | 1/33 | 2/33 |
| **当前措辞（采用）** | **0/33** | 5/33 |

### 短措辞为什么不够

它只禁「接单了」。实测模型**绕过去**了：

> 用户：帮我跑一下测试
> 回复：测试什么？……给点具体信息，**我让Worker去跑**。不然空靶子一个，跑空气啊。

模型从 `persona.CHIEF_OF_STAFF_BLOCK` 的「交给你麾下的 Worker」推断出一个 Worker 存在，于是换个说法继续宣称。

### 当前措辞

```
这一轮你手上没有真的新建任何工作单，也没有派给谁，更没有任何 Worker 在为你跑东西。
不许说"接单了"、"我让Worker去跑"、"已经丢给谁"或任何表示已经派下去、已经在跑的话。
你这一轮只能问清楚要做什么，然后等老板回答。
```

三句话分别对应三个实测泄漏点：**没有工作单**（堵「接单了」）、**没有 Worker**（堵「我让Worker去跑」——这是短措辞失效的直接原因）、**只能问清楚**（给出唯一允许的动作，避免模型在「不许说什么」里空转）。

## 二、误报代价

闲聊 6 条 × 2 次 = 12 次，**带偏 0/12**。

怀疑层对闲聊的命中率：11 条语料里 1 条（「把第二段改自然一点」），但**它在真实链路上走不到注入点**——`should_route` 的笔记路径先接走（`app.py:779` 走 AgentLoop 并 `return`），`stream_respond` 不执行。测试里把这个事实也锁住了，没有假装它不存在。

## 三、空回复 5/33 的归因（**不是本次改动引入**）

对照实验（同句，有/无约束各 3 次）：

| 句子 | 无约束 | 有约束 |
|---|---|---|
| 你找人把服务器日志清一下 | 30/45/60 字 | 40/18/53 字 |
| 我想让谁帮我搞一下那个脚本 | 26/43/14 字 | **空（reasoning=1483）**/51/38 字 |
| 这份活儿交出去吧 | 39/28/23 字 | 12/43/10 字 |
| 这活得有人干一下 | 16/18/24 字 | 9/24/38 字 |
| 叫个人来把这个收拾了 | 51/34/22 字 | 13/17/18 字 |

**两组都会出现空回复**，成因是推理吃掉了输出预算（`reasoning=1483 字符、text 为空`，`finish_reason` 仍为 `stop`）。这是 `llm.py:94` 注释里描述过的既有形态，**不是约束导致的**。

生产上的表现是一次静音。既有代码对此有兜底（`llm.py` 的救援路径与中性句），本次改动不改变这条路径。

**列为观察项**：若上线后空回复率异常升高，先查 `reasoning_effort` 与 `max_tokens` 的配比，而不是怀疑约束。

## 四、路由零漂移（硬证据）

46 条输入 × 5 个路由函数 = **230 个返回值**，与 master 逐条比对：

```
ZERO DRIFT — 全部 230 个返回值逐条一致
```

比对的是 `delegation_intent` / `progress_query_intent` / `telemetry_query_intent` / `acceptance_intent` / `should_route` 的实际返回值，不是「读代码觉得没改」。

## 五、门禁

```
scripts/test_intent_guard.py        54/54
scripts/test_core.py                64/64
scripts/test_executive_v3.py        242/242
scripts/check_protocol.py           exit 0
路由前后对比                        230/230 一致
```

## 六、已知边界

1. **「你去弄一下这个事情」未召回**——保留在测试语料里，断言它当前是空的。补上了测试会红，那是显式更新期望的信号。
2. **纯函数约束**——`guard_for()` 无状态、无 IO、无计数。`_render_turn_context` 同时是 `limit_context` 的 measure 回调，每轮被反复调用；任何有状态的东西都会重复触发。节流若将来要加，必须放在 `_turn_context` 里（每轮一次），不能进 `guard_for`。
3. **留痕不走 telemetry.db**——`data/intent-guard.jsonl`，对齐 `durable_shadow.py` 先例。`telemetry.py` 的 `summary()` 是全表聚合，喂 L1 预算闸与战况，写进去会污染。
4. **单模型采样**——只有 `step-3.7-flash`。换模型需重跑本文档的实测。
5. **本阶段不覆盖路由执行面**——单测 E 组只证明「疑似走第二条静默路径的句子也进了怀疑层，所以约束会注入」，**没有**跑 `executive_route` 的真实执行面（那需要 stub executive 提供 `pending_reports()`/`open_commissions()` 与空样本池）。
   `executive_route:1483` 在 `commission_accept_line()` 返回空串时 `return None` 是**既有行为**，不由本改动引入，也不由本改动修复。
   执行面覆盖另立 P3；不动 `executive.py` 的测试面是本阶段自设的边界。
