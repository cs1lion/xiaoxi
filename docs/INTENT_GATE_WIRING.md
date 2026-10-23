# 第 9 步接线方案：`app.py:740` 从正则换成 IntentGate

**状态：方案，未实施。** 改的是正在跑的服务（`server/app.py`），按红线要先批准。

第 1 步已落地：`server/contract/parser.py`（自由文本 → `Draft`），
判据验过（held-out 15/15）。本文件只讲**怎么把它接进去**。

---

## 0. 先说结论：这不是「换一行」

规范里写「替换 `app.py` 的正则路由」，听起来是一行。实测核过，实际是两件事：

| 事 | 规模 | 风险 |
|---|---|---|
| ① 换那一跳判断 | 约 30 行（含兜底） | 中 |
| ② **新增会话级追问状态** | 约 40 行 + `Connection` 加字段 | **高**（现在完全没有） |

**② 才是隐藏工作量。** `app.py` 全仓搜 `round_index` / `clarif` **零命中**，
`Connection`（`app.py:289`）上也没有任何追问相关字段。而
`gate.evaluate(round_index=…)` 靠这个数区分「第几轮追问」——不传它，
每次都当第 0 轮，追问永远到不了上限、也永远不降级。

---

## 1. 现状：那一跳长什么样

`server/app.py:738-782`（实测行号，规范写的 `:736` 已漂 4 行）：

```python
# V3 台前快环：委托接单 / 进度追问 / API 战况 / 验收反馈。
route = orchestrator.executive_route(text, executive, memory_enabled=manager.enabled)   # :740
if route is not None:
    store_message(conn.session_id, "user", text, mode)
    ... ui_nav / aqi_blocks / _present_reply ...
    kind = route.get("kind")
    if kind == "delegation":
        conn.spawn(_executive_delegate(route.get("brief") or text))   # :772
    elif kind in ("accept", "reject"):
        conn.spawn(_executive_finish(...))
    ...
    return
```

`executive_route` 内部（`orchestrator.py:1462-1550`）是六条分支，按序短路：
`starmap_view` → `notes_view` → 待验收（accept/reject/clarify）→
`progress` → `delegation` → 最后 `return None`（落普通对话）。

**delegation 分支的判据就是正则**（`:1539` `if delegation_intent(text)`），
不命中就一路 `return None`，静默落普通对话——这就是那个老毛病。

---

## 2. 接线点选在哪：**不换 `:740`，换 `:1539`**

这是本方案最关键的一个决定，和规范的字面说法不同，理由如下。

**规范说「替换 `app.py:740` 的调用」。但 `:740` 只是调用点，真正决定
「派不派单」的是 `orchestrator.py:1539` 那条正则。** 如果换 `:740`，等于
把整个 `executive_route` 绕开——那会一并丢掉它前面五条分支
（星图、便笺、待验收、进度、战况），那些都不是本次改造范围。

所以正确的接线点是**只换 delegation 那一支**：

```
orchestrator.executive_route(...)                       # :740 不动
  ├ starmap_view      ← 不动
  ├ notes_view        ← 不动
  ├ 待验收 accept/reject/clarify  ← 不动
  ├ progress          ← 不动
  ├ delegation        ← ★ 换成 parse → evaluate → 按 status 分派
  └ return None       ← 不动（普通对话兜底）
```

**好处**：改动面被关在一条分支里，前面五条分支的行为一个字不变，
「不能倒退」这条约束自动满足大半（见 §5）。

---

## 3. 改完的 delegation 分支长什么样

```python
if delegation_intent(text) or _gate_might_accept(text):
    # 第一道：现有正则先跑，命中就不必调模型（省钱、且保证不倒退）
    # 第二道：正则没命中时，才调解析器（这一步要花一次模型调用）
    ...
```

但**「先正则后模型」这个顺序有个问题**：正则会命中「去把便笺整理成三版」，
但按第 1 步的验证，那句话解析出来是 `needs_clarification/G3`——
也就是**正则命中 ≠ 信息完备**。

所以真正的流程是：

```python
# 在 executive_route 的 delegation 分支位置
brief = text
result = await parse_intent(text, session_id)     # 见 §4，内部带缓存
if result.draft is None:
    return None                                    # 不是一单 → 落普通对话（原有兜底）

outcome = gate.evaluate(result.draft, round_index=_rounds(session_id))

if outcome.status == GATE_NEEDS_CLARIFICATION:
    return {"kind": "clarify_task",
            "text": outcome.gap.question,          # 直接念出闸门的问题
            "gap": {...}, "aqi_blocks": [...],
            "ui_nav": ...}
if outcome.status == GATE_REJECTED:
    return {"kind": "task_rejected", "text": ..., "reason": outcome.reason}
# ready
return {"kind": "delegation", "text": commission_accept_line(), "brief": text, ...}
```

**三个新 `kind`**（`clarify_task` / `task_rejected` / 沿用 `delegation`）
都要在 `app.py` 的分派处加分支。

---

## 4. 会话状态：`round_index` 与「待补的 Draft」

`gate.evaluate` 的追问是**有状态**的：第 1 轮问 G3，用户答了，第 2 轮要
拿**补过的 Draft** 再跑。所以除了计数，还要存那份 Draft。

### 存在哪

不建议挂 `Connection`（WebSocket 一断就丢，重连后追问上下文没了）。
建议放 `orchestrator` 已有的 `self.state`（`orchestrator.py:1562` 已在用
`self.state["proactivity_enabled"]`），按 `session_id` 分键：

```python
self.state.setdefault("gate_pending", {})   # session_id -> {draft, rounds, gap_key}
```

### 生命周期

```
第 1 轮  用户说「把便笺整理成三版」
         → parse → Draft(goal=整理便笺, artifact=file/无格式)
         → evaluate(round_index=0) → needs_clarification G3
         → 存 pending = {draft, rounds=0}
         → 念出「文件要什么格式？md、csv、json…」

第 2 轮  用户答「markdown」
         → ★ 这是补槽，不是新任务。要把回答填进 pending.draft 的 artifact.format
         → evaluate(round_index=1) → 若 G4 缺 → 问 G4
         → pending.rounds = 1

第 3 轮  用户答验收条件
         → evaluate(round_index=2) → ready 或再问

第 4 轮  若仍缺 → evaluate(round_index=3) → 走降级：
         填保守默认 + 标 degraded_requires_explicit_confirmation=True
         → **不得自动开工，必须显式确认**
```

### 最容易被忽略的一点

**第 2 轮的「markdown」是个补槽，不是新句子。** 如果把它当新句子再走一遍
`parse_intent`，模型会判它是 `chat`（就一个词），于是 pending 被丢掉、
追问白问了。

所以必须先判断「有没有 pending」：

```python
pending = _pending(session_id)
if pending is not None and _looks_like_slot_answer(text):
    # 补槽：按 pending.gap_key 决定填哪个字段
else:
    # 新句子：走 parse_intent
```

`_looks_like_slot_answer` 的判据要保守：**有 pending 时优先当补槽**，
用户真想换话题就得能逃出去（比如解析出新的 `delegate` 且有 goal）。

---

## 5. 「不能倒退」怎么保证（规范 `:1222`）

规范原话：IntentGate 必须也命中现在正则能命中的全部。

**这条不能靠人眼看 diff，要写成测试。** 具体做法：

```
对 scripts/verify_intent_routing_corpus.py 的 A 组（派单 9 句，现在全 HIT）：
  改造后，同样这 9 句必须仍然走到 delegation 分支
  —— 注意判据是「走到 delegation 分支」，不是「建单成功」。
     改造后它们可能变成 needs_clarification（因为信息不全），
     那是**正确的新行为**，不是倒退。
```

也就是说**「不倒退」的定义要精确到分支，不是精确到结果**：

| 句子 | 现在 | 改造后允许 | 改造后不允许 |
|---|---|---|---|
| A 组 9 句 | 进 delegation 分支 | 进 delegation 分支，可能转成 clarify_task | 静默落普通对话 |
| B 组 12 句 | 静默落普通对话 | 进 delegation 分支（这是升级） | 静默落普通对话（没升级） |
| C 组 11 句 | 落普通对话 | **仍落普通对话** | 进任何建单分支 |

这条要写成 `scripts/test_intent_gate_wiring.py`，跑真 `executive_route`，
用 stub 的 `executive` 提供台账。

---

## 6. 失败兜底：模型挂了不能让她变哑巴

现在正则不依赖任何外部服务，永远能答。接上模型后多了一个失败点。

**兜底规则（按顺序）**：

1. 解析器超时/报错 → **退回现有正则判断**（`delegation_intent(text)`），
   行为与今天完全一致。
2. 解析器返回 `draft=None` 且 intent 是 chat/query/edit → 落普通对话（正常）。
3. 解析器返回空内容（推理模型把 token 花光，实测过）→ 同上，退回正则。
4. **任何情况下都不能让这一轮没有回应。**

对应实现：解析调用包在 `try/except` 里，异常时 `route = None` 之后
**不要直接 return**，而是走原来的 `delegation_intent` 判断。

---

## 7. 成本与延迟（实测，这是决定要不要做的数）

现在 delegation 判断是**纯正则，0 成本 0 延迟**。接上模型后多一次调用。

### 实测延迟（2026-10-23，7 句真跑）

| 模型 | 平均 | 中位 | 最慢 | held-out 准确率 |
|---|---|---|---|---|
| `step-3.7-flash` | 7.72s | 8.10s | **10.04s** | 15/15 |
| `step-3.5-flash` | 4.10s | 4.42s | **5.69s** | 15/15 |

（3.7 第二轮测得更差：平均 9.45s、最慢 15.29s——它是推理模型，先想再答，
延迟随句子难度浮动。）

token 消耗（3.7-flash）：prompt 平均 453、completion 平均 558。

### 结论：**同步调用不可行**

台前快环红线是「1~2 秒内说完」。最快的 `step-3.5-flash` 也要 4~6 秒，
是红线的 2~3 倍。**如果同步调模型再回话，现在「1~2 秒接单」的体验会直接坏掉。**

所以第 9 步必须改成**两段式**：

```
第 1 段（0ms，正则）：delegation_intent 命中 → 立刻说「接单了」+ 后台 spawn
第 2 段（4~6s，模型）：异步解析 → 如果信息不全，再补一句追问
```

**代价**：用户会先听到「接单了」，4 秒后又说「不过做出来的东西要什么样？」。
这是**可接受但要说清**的体验：比起现在的「什么都不问、静默不建单」，
它至少把缺口说出来了。

**另一个选项**（需你定）：只在正则**未命中**时调模型。
正则命中的句子走老路（0 延迟），只有「帮我把…」这类静默句才花 4~6 秒。
这样：
- 优点：常用路径不变慢；升级只影响今天本来就静默的句子（4 秒 > 静默，仍是改进）
- 缺点：正则命中的句子（如「去把便笺整理成三版」）享受不到追问，
  而第 1 步实测显示它**也缺信息**（会问 G3）

### 延迟的可选项

| 做法 | 延迟 | 代价 |
|---|---|---|
| 换更快模型（如更小的 flash） | 未测 | 准确率未测 |
| 缩短 system prompt（现在 916 字） | 可能降 1~2s | 判据变弱，准确率可能掉 |
| 两段式（先说接单再追问） | 首字 0ms | 体验有 4s 落差 |
| 只在正则未命中时调 | 常用路径 0ms | A 组句子得不到追问 |

**我不推荐任何一条**——这是体验取舍，得你定。

### 已定：方案 2（只补静默句）+ 二分预判

用户 2026-10-23 19:46 选定方案 2。但方案 2 有个隐含问题必须先解决：

**正则未命中的句子本来就是普通对话。** 如果每句闲聊都跑一次完整解析
（4~6 秒），普通对话会全部被拖慢——那比现在更糟。

所以把判断拆成两步：

```
第 1 步  二分预判「这是不是一单」——只输出 1 或 0
第 2 步  完整解析（4~6 秒）——只在第 1 步说是时跑
```

### 二分预判的实测（44 句，两个模型）

| 模型 | 平均 | 中位 | 最慢 | 准确率 |
|---|---|---|---|---|
| `step-3.5-flash` | **2.95s** | 2.57s | 5.99s | **44/46** |
| `step-3.7-flash` | 3.59s | 3.06s | 6.22s | 44/46 |

3.5 更快、同准确率，且不会因思维链吃光 `max_tokens` 返回空串。**选 3.5。**

两条错判都在**误报侧**（`帮我理一理思路`、`结尾改具体一点` 被判成一单）——
按不对称判据，误报比漏报贵，所以这两条要在接线时盯住。

### 一个必须知道的坑

`max_tokens=2` 时**所有派单句都返回空串**——推理模型的思维链把额度吃光了。
提到 300 后正常。这不是可调参数，是必须给足。

### 普通对话基线（决定必须并行）

实测普通对话一轮：平均 **3.19s**、最慢 4.20s（只说一句自然回话，
不含人格装配/记忆检索/语音合成）。

串行加预判 = 3.19 + 2.95 = **6.14s**，普通对话会被拖慢一倍。

**所以预判必须与回话并行跑**，不能串行。这是硬约束，不是优化建议。

### 接线封装已就位（9b，不碰 app.py）

`server/contract/bridge.py` 提供三样东西：

| 东西 | 作用 |
|---|---|
| `parse_binary_answer()` | 把 "1"/"0"/空串 解析成 True/False/**None** |
| `GateSession` + `SessionStore` | 按 session_id 存 `round_index` / 待补 Draft / 上一轮问的槽 |
| `decide()` | 预判 + 解析结果 + 会话状态 → 决定（建单/追问/补槽/落普通对话/退回正则） |

**最关键的一条兜底**：`parse_binary_answer("")` 返回 `None` 而不是 `False`。
空串表示「模型没给出答案」，必须退回正则；当成 `False` 会让该建单的句子静默掉。

**补槽判据的坑**（写代码时踩到的真缺陷）：初版用「长度 ≤12 就算补槽」，
实测「今天天气不错啊」（7 字）、「我有点累」（4 字）、「你好」（2 字）全中招——
有 pending 时用户照样能说闲话，不能把闲话填进契约。改成只认与所问槽位相关的内容。

---

## 8. 分几步做（建议）

| 步 | 内容 | 风险 | 要不要批准 |
|---|---|---|---|
| 9a | 写 `scripts/test_intent_gate_wiring.py`：把 §5 那张表写成断言，**先让它红** | 无（只加测试） | ✅ 已做 |
| 9b | `bridge.py` + 会话状态（不动 `app.py`） | 低 | ✅ 已做 |
| 9c | 换 delegation 分支 + `app.py` 加 kind 分派 | 中 | **要** |
| 9d | 并行跑预判 + 实测端到端延迟 | 中 | **要** |

**9a 先做的理由**：现在写「不倒退」的测试，它一定是红的（因为还没接线）。
红了才能证明这条测试有牙——而不是接完线再补一条永远绿的测试。

---

## 9. 这份方案**不决定**的事

- **9c/9d 要不要做**：那两步改的是正在跑的服务，要你点头。
- 追问 UI 长什么样：`clarify_task` 要不要做成可点选项（`gap.options` 已经带了
  `{label, value}`），涉及前端与协议，属第 8 步范围。
- 要不要发 `CONTRACT_PROPOSED` / `ASK_USER` 事件：**属第 8 步**，
  本次接线**不发任何新 WS 事件**，追问只用现有的 `text` + `aqi_blocks`。
