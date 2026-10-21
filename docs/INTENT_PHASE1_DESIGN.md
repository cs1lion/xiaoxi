# 意图识别 · 阶段一设计（兜底提示）

- 分支基线：`feat/intent-routing-baseline`（`b3602e8`）
- 状态：**设计已验证，待用户批准后实施**
- 前置事实见 [INTENT_ROUTING_BASELINE.md](INTENT_ROUTING_BASELINE.md)

## 一、目标修正

立项时的说法是「同义改写静默降级」。实测推翻（真实 API，`step-3.7-flash`）：

| 用户说 | 正则 | 她的回复 |
|---|---|---|
| 帮我把那份季度报告生成一下 | MISS | **接单了。** 把数据源和格式要求扔过来，我让Worker跑 |
| 把这活儿派下去吧 | MISS | **接单了。** 老板，这活儿的brief呢？ |
| 帮我把测试跑一遍 | MISS | **接单了**，已经丢给Worker跑全量 |

**不是静默，是虚报。** 所以阶段一的目标不是「补正则提高召回」，是「**不许虚报**」。

## 二、方案

在**不改变任何路由判定**的前提下，只影响措辞：当「疑似委托/进度但正则未命中」时，往动态提示词里插一句负向约束。

### 2.1 三层结构不变

```
executive_route()   ← 不变（台账双约束，本来就是对的）
should_route()      ← 不变（纯正则）
──────────────────────────────────────────
新增：怀疑层            ← 只产出一句约束文本，不参与路由
  ↓ 插入 dynamic_prompt()
stream_respond()    ← 措辞改变，路径不变
```

**关键不变量：怀疑层不改 `executive_route`/`should_route` 的任何返回值。** 路由结果与今天逐位相同，这是 B 组语料能证明的。

### 2.2 怀疑层正则（实测召回）

候选实现（`_SUSPECT`），实测：

| 集合 | 召回 | 说明 |
|---|---|---|
| 委托同义改写（12 条） | **11/12** | 唯一漏的「你去弄一下这个事情」 |
| 规范说法（6 条） | 6/6 | 已被真正则接住，抓不抓都无妨 |
| 闲聊（11 条） | 误报 **1/11** | 「把第二段改自然一点」——误报代价见 2.4 |

进度类同义改写（8 条）：独立规则**召回 8/8、闲聊误报 0**。

### 2.3 约束措辞（A/B 实测）

**V1（弃用）**：提到「督办单/接单通道」→ 误报时她会说「这单没接住」，把台账词带进闲聊。

**V2（采用）**：
```
这一轮你手上没有真的新建任何工作单，也没有派给谁。
不许说"接单了"或任何表示已经派下去的话；先把要做什么问清楚。
```

### 2.4 实测结果（真实 API，A/B 各 1 次）

**目标集 · 不虚报（5/5 干净）**

| 用户说 | V2 下的回复 |
|---|---|
| 帮我把那份季度报告生成一下 | 季度报告？我这边没存底，你说一下具体要哪些数据、什么格式？ |
| 把这活儿派下去吧 | 等等，我手里没单子啊。你想派什么活？先说清楚。 |
| 帮我把测试跑一遍 | 什么测试？你项目里的单元测试还是之前提的接口回归测试？ |
| 你找人把服务器日志清一下 | （空） |
| 这份活儿交出去吧 | 话先说清楚，你想交什么？ |

**误报集 · 不带偏（6/6 正常）**

| 用户说 | V2 下的回复 |
|---|---|
| 帮我理一理思路 | 先说说，什么事？写东西卡壳了，还是工作上的烂摊子？ |
| 把第二段改自然一点 | 第二段？你现在发的是空白。把原文贴上来。 |
| 我有点累 | 累了就歇，别硬扛。 |
| 今天天气不错啊 | 确实，适合摸鱼的天气。你那边风大吗？ |
| 你说我该不该换工作 | 这我哪知道啊，你现在工作是钱不到位还是干得天天憋气啊？ |
| 就是第二个 | 这什么第二个？我这边没上下文，你再说一遍。 |

**误报的代价可接受**：多一句「先把要做什么问清楚」，对话照常继续，没有跑偏、没有台账词泄漏。

### 2.5 大样本复测（12 条 × 3 次 = 各 36 次）

首轮只做了各 1 次采样，不足以断言稳定性。扩到 3 次后：

```
A 现状（无约束）                          B 加约束 V2
  [X-.] 帮我把那份季度报告生成一下            [...] 帮我把那份季度报告生成一下
  [.X-] 你找人把服务器日志清一下              [...] 你找人把服务器日志清一下
  [...] 我想让谁帮我搞一下那个脚本            [...] 我想让谁帮我搞一下那个脚本
  [XX.] 把这个需求落实下去                  [...] 把这个需求落实下去
  [-XX] 这份活儿交出去吧                    [...] 这份活儿交出去吧
  [...] 把那个表格重新做一遍                 [...] 把那个表格重新做一遍
  [.XX] 你去弄一下这个事情                  [...] 你去弄一下这个事情
  [XXX] 帮我跑一下测试                      [...] 帮我跑一下测试
  [XX.] 能不能让谁帮我弄弄这个               [...] 能不能让谁帮我弄弄这个
  [X-X] 这活得有人干一下                    [...] 这活得有人干一下
  [X.X] 麻烦你那边安排一下                  [...] 麻烦你那边安排一下
  [XXX] 叫个人来把这个收拾了                [...] 叫个人来把这个收拾了
  -> 虚报 20/36 | 空回复 4/36               -> 虚报 0/36 | 空回复 0/36
```

（`X`=虚报 · `.`=干净 · `-`=空回复）

**虚报率 56% → 0%。** 12 条同义改写里有 9 条至少虚报过一次——这不是偶发，是常态。

对照数据：同一批句子走**规范说法**命中 `_DELEGATION` 时，`executive_route` 真的建单，所以「接单了」是准确陈述。虚报只发生在正则漏掉的分支。

### 2.6 已知弱点

- **空回复偶发**：A 组出现 4/36、B 组 0/36。查过一句：重跑 4 组全部正常，其中一次是 `reasoning=710 字符、text 为空`——推理吃掉输出预算的已知形态（`llm.py:94` 注释描述过同一现象）。**与约束无关**（A 组无约束也会空），但在生产上表现为一次静音。列为实施后观察项。
- 单次调用 `prompt_tokens` 增量约 **+38**（1964 → 2002），可忽略。
- 采样仍限于单模型（`step-3.7-flash`）。换模型需重跑本组。

## 三、验收判据（三条全过才算完成）

1. **不虚报**：B 组 20 条同义改写，回复中「接单了/已经丢给/我让Worker/派下去了」出现次数 = **0**（现状基线：56% 虚报率）
2. **不误伤**：C 组 11 条闲聊，回复不得出现台账词（单/派/接住/交出去）
3. **零路由漂移**：`scripts/verify_intent_routing_corpus.py` 仍 **PASS 102/102**；`test_executive_v3.py` 仍 242/242；`check_protocol.py` exit 0

## 四、边界

- **不碰** `server/assistant/executive.py`（归用户本人）
- **不新增** WS 事件类型 / API 字段（协议 §24 红线）
- **不改** `executive_route` / `should_route` 的任何返回值
- **不混** D 交付改动（PR #6 独立）

## 五、待定（需用户/架构裁决）

1. 怀疑层放 `service.py` 还是新建模块？倾向新建 `server/dialogue/intent_guard.py`，单文件、纯函数、易测。
2. 约束文本进 `dynamic_prompt()` 的哪个参数？现有参数已很多，可能需要新增一个可选参数。
3. 是否要让提示变成**用户可见的明确提示**（"我没接住"），还是只约束她别虚报？当前设计是后者（更保守，不改输出形态）。
4. 空回复问题（2.5）是否纳入本阶段。

## 六、实施细节（精确到行）

### 6.1 新增模块 `server/dialogue/intent_guard.py`

单文件、纯函数、无依赖、无 IO：

```python
"""Suspicion layer: when an utterance looks like a delegation or a progress
query but the fast-loop regexes missed it, emit one negative constraint so the
persona does not claim 接单了 for a commission that was never created.

This layer never decides routing. executive_route()/should_route() keep their
exact current return values; only the wording of the reply is constrained.
"""
import re

_DELEGATION_SUSPECT = re.compile(
    r'(?:帮我|替我|帮忙|麻烦|劳驾|请)(?:把|给|弄|搞|做|跑|写|改|整理|查|生成|安排|处理|看|清|收拾)'
    r'|(?:把|将)[^，。！？]{1,20}(?:弄|搞|做|改|生成|整理|清|跑|写|安排|处理|落实|派|交|发)(?:一下|一遍|一版|个|下)?'
    r'|(?:派|交|丢|扔|托|委托|安排|分派|下发|布置)(?:下去|出去|给人|个人|一单|下去吧|一下)'
    r'|(?:找人|叫个人|让人|让谁|找谁|安排人|安排个人)'
    r'|(?:这|那)(?:活儿|活|事|事儿|单|单子)[^，。！？]{0,10}(?:得|要|该|谁|交给|派|弄|干|做)'
    r'|(?:落实|执行|推进|跟进|盯)(?:下去|一下|着)'
)
_PROGRESS_SUSPECT = re.compile(
    r'(?:到哪(?:一)?步|到哪了|什么阶段|什么情况了|哪一步了|进展|如何了|还要多久|'
    r'多久能|快好了|搞到哪|办得咋样|办得怎么样|弄到哪)'
)

DELEGATION_GUARD = (
    '这一轮你手上没有真的新建任何工作单，也没有派给谁。'
    '不许说接单了或任何表示已经派下去的话；先把要做什么问清楚。'
)
PROGRESS_GUARD = (
    '这一轮你手上没有正在进行的督办，拿不到任何进度数据。'
    '不许编造进度或阶段；先问清楚是哪一单，或者说明你现在查不到。'
)


def guard_for(text: str) -> str:
    """Constraint text for this turn, or "" when nothing is suspected.

    Failure mode is deliberately silent: this must never raise, because it sits
    on the voice fast loop.
    """
    if not isinstance(text, str) or not text.strip():
        return ""
    try:
        if _PROGRESS_SUSPECT.search(text):
            return PROGRESS_GUARD
        if _DELEGATION_SUSPECT.search(text):
            return DELEGATION_GUARD
    except Exception:
        return ""
    return ""
```

### 6.2 `persona.dynamic_prompt()` 加一个可选参数（`server/persona.py:191`）

```python
    pushback: str = "",
    intent_guard: str = "",        # 新增，默认空 = 行为与今天逐字节相同
) -> str:
```

在 `sections` 组装的最末（所有既有段落之后）追加：

```python
    if intent_guard:
        sections.append(intent_guard)
```

**空串时不追加任何内容**——所以不传这个参数的调用点行为完全不变（`persona.py:401` 的 `build_messages` 就是这种）。

### 6.3 调用点（`server/dialogue/orchestrator.py:461`）

```python
from .intent_guard import guard_for          # 模块顶部
...
dynamic = dynamic_prompt(...,
    mood=data.get('mood') or '',
    intent_guard=guard_for(user_text),       # 新增一行
)
```

### 6.4 为什么放在 `dynamic_prompt` 而不是 `static_system_prompt`

`static_system_prompt()` 是 provider 前缀缓存的对象（`persona.py:158-162` 明确写了）。把每轮变化的内容放进去会让缓存整块失效，成本上升。`dynamic_prompt()` 本来就是 per-turn 块。

### 6.5 改动清单（4 文件，约 +60 行）

| 文件 | 改动 | 是否碰生产逻辑 |
|---|---|---|
| `server/dialogue/intent_guard.py` | **新增**，约 55 行 | 新模块，无调用方时零影响 |
| `server/persona.py` | +3 行（一个参数 + 一个 if） | 默认空串，行为不变 |
| `server/dialogue/orchestrator.py` | +2 行（import + 传参） | 只影响 `_render_turn_context` |
| `scripts/test_intent_guard.py` | **新增**，离线单测 | 无 |

`executive_route()` / `should_route()` / `app.py` **零改动**——路由判定与今天逐位相同。

### 6.6 单测断言（`scripts/test_intent_guard.py`，离线无需 Key）

1. `guard_for` 对 B 组 20 条同义改写返回非空（召回，允许 1 条漏）
2. `guard_for` 对 C 组 11 条闲聊**全部**返回空串（误报锁）
3. `dynamic_prompt(intent_guard="")` 与原输出**逐字节相同**
4. `guard_for` 对 `None`/`""`/非字符串不抛异常
5. `build_messages()` 不传 `intent_guard` 时输出与改动前逐字节相同（快照比对）

### 6.7 风险

| 风险 | 等级 | 缓解 |
|---|---|---|
| 误报导致措辞跑偏 | 低 | 已实测 6/6 正常；单测锁 C 组 |
| 怀疑层抛异常拖垮快环 | 低 | 整个函数包 `try/except` 返回空串；单测覆盖 |
| 前缀缓存失效 | **可忽略** | 放 dynamic 块，本就不缓存 |
| 路由被意外改变 | **零** | 没碰任何路由函数；语料 102 条锁死 |

