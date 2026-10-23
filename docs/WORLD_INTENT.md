# 像素世界 · 意图模型接入

- **状态**：已实施（世界侧 + 服务端 + 离线测试）
- **日期**：2026-10-23
- **代码**：`pixel-world/src/world/intent-{schema,prompt,router,client}.ts`、`server/world_intent.py`
- **一句话**：规则优先，模型兜底；模型失败等于没听懂，绝不猜。

## 一、为什么不是"全交给模型"

三条硬理由，按重要性排序：

1. **离线可跑。** 没有 `LLM_API_KEY` 时世界必须还能用。全模型化会让原型在没有网络时变成一块砖，测试也没法跑。
2. **确定性优先。** 「下雨吧」「交付」「建个工坊」这类规则 100% 认得出。为它们付一次网络往返、承担模型抖动，没有收益。
3. **接缝纪律。** 已经能被规则稳定识别的行为，不该因为接入模型而改变。规则命中的结果与接入前逐位相同——这是回归测试锁住的。

模型要解决的只有一个问题：**同义改写**。规则的短板是词表覆盖，
比如「种棵树」（词表是「种树 / 种一棵树 / 栽树」）、
「那栋抄书斋拆了吧」（名词在前、动词在后）。补词表会无限膨胀，这类活该由模型接。

## 二、管道

```
用户输入
   │
   ▼
Simulation.speak()  ← 同步，绝不 await
   │
   ├─ parseOracleBuild  ─┐
   ├─ parseOracleMiracle ├─ 规则三连，认得出就结算，不进下一步
   └─ parseOracle       ─┘
   │
   │ 认不出，且配了模型
   ▼
classifyLater()  ← 异步，不阻塞任何一拍
   │
   ▼
POST /api/world/intent   { utterance, templates[] }
   │
   ▼
server/world_intent.py
   ├─ 无 Key        → 503（不返回猜的意图）
   ├─ 模型失败       → 502
   ├─ 非 JSON       → 502
   └─ 成功          → { intent, model, source }
   │
   ▼
intent-schema.ts 校验  ← 世界侧，唯一权威
   │
   ├─ 合法 → enqueue({ kind: 'intent' }) → 下一拍结算
   └─ 不合法 → unknown，世界不动
```

**结果回队而不是直接改世界**：模型是异步的，回来时世界可能已经走了几拍。
回队让它的时序与玩家同步输入完全一致——石板、事件顺序、排队位次都不特殊。

## 三、契约

### 3.1 请求

```http
POST /api/world/intent
Content-Type: application/json

{
  "utterance": "把那份季度报告弄一下",
  "templates": [
    { "id": "workshop_plot", "displayName": "新工坊", "boundTask": null },
    { "id": "crawler_mine",  "displayName": "爬虫矿洞", "boundTask": "research" }
  ]
}
```

`templates` 由前端从蓝图注册表现场取，**不是服务端硬编码**。
世界的能力表只有一份（`prefabs.json`），服务端抄一份必然漂移。

### 3.2 响应

```json
{ "intent": { "kind": "dispatch", "taskKind": "writing", "summary": "弄一下报告" },
  "model": "deepseek-chat",
  "source": "model" }
```

`intent` 是**未校验**的原始 JSON。校验在世界侧（`intent-schema.ts`），
因为能力表（模板、动词、坐标范围）都在世界那边。

### 3.3 状态码

| 码 | 含义 | 世界的行为 |
|---|---|---|
| 200 | 成功 | 校验后结算 |
| 422 | 请求不合法（空句子） | 没听懂 |
| 502 | 模型失败 / 非 JSON | 没听懂 |
| 503 | 没配 `LLM_API_KEY` | 没听懂 |

**503 是设计选择，不是缺陷。** 主产品的 `LLM._mock` 在无 Key 时返回一句闲聊——
那对对话是对的（总比不说话强），但对意图是灾难：世界会照着一句闲聊动。

## 四、意图 schema

八种，与 `WorldCommand` 一一对应：

| kind | 字段 | 说明 |
|---|---|---|
| `dispatch` | `taskKind`, `summary` | 派活。`taskKind` ∈ code/research/writing/quick |
| `complete` | — | 交付 |
| `cancelPending` | — | 撤销排队中的神谕 |
| `setWeather` | `weather` ∈ clear/rain | 改天气 |
| `build` | `templateId`, `at?` | 建楼。`at` 省略由世界选址 |
| `demolish` | `templateId` | 拆楼 |
| `miracle` | `miracle` ∈ light/tree/cat, `at?` | 点灯 / 种树 / 召唤猫 |
| `unknown` | `reason` | **一等公民**：听不出就说听不出 |

三条校验原则（与 `creator-schema.ts` 同源）：

1. **未知字段直接拒绝。** 模型多写 `confidence` 说明它对形状的理解已经偏了。
2. **枚举与模板 id 必须来自注册表。** 模型不能发明 `dragon_tower`。
3. **只管形状，不管可行性。** 能不能建、位置占没占、迷雾揭没揭，都由世界判。

## 五、失败语义

**所有失败都落到 `unknown`，绝不落到"最像的那个"。**

模型超时、返回非法 JSON、网络断了、编造了模板——这些都等于没听懂。
退而求其次猜一个，就是在替用户做决定。回执统一是「没听懂这句，世界没有动。」

模型自己也可以答 `unknown`。那不是失败，是**正确回答**——闲聊、提问、
抱怨都该落到这里。世界宁可不动。

## 六、测试

| 层 | 文件 | 覆盖 |
|---|---|---|
| schema | `pixel-world/test/intent-schema.test.ts` | 20 条：合法/越界/未知字段/枚举 |
| prompt | `pixel-world/test/intent-prompt.test.ts` | 8 条：模板表防漂移、纪律措辞 |
| router | `pixel-world/test/intent-router.test.ts` | 12 条：规则优先、模型兜底、失败落 unknown |
| 异步链路 | `pixel-world/test/intent-async.test.ts` | 9 条：快环不阻塞、结果回队、去重 |
| 客户端 | `pixel-world/test/intent-client.test.ts` | 11 条：请求形状、信封、超时、状态码 |
| 服务端 | `scripts/test_world_intent.py` | 23 条：503、抠 JSON、提示词驱动 |

全部离线，不需要 API Key。

## 七、实测（真实模型）

**语料**：`scripts/eval_world_intent_live.py`，24 条，分三组——
对照组（规则也认得出）、同义改写（模型该接住）、负例（不该动世界）。

**模型**：StepFun `step-3.7-flash`（token plan 通道），温度 0，`max_tokens` 1024，
`reasoning_effort=low`。

| 组 | 内容 | 结果 |
|---|---|---|
| A | 规则也认得出（对照） | **6/6** |
| B | 同义改写（模型该接住） | **9/9** |
| C | 不该动世界（负例） | **9/9** |

平均 3.3 秒一次。全部 17 条同义改写在模型侧都被正确归类，包括
「那栋抄书斋拆了吧」（名词在前、动词在后）、「把天给我弄晴了」、「这单我验收了」。

负例组的关键区分（实测都答对）：

| 说法 | 正确结果 | 说明 |
|---|---|---|
| 别下雨了 | `setWeather: clear` | **不是**撤销——它是在改天气 |
| 别建了 | `cancelPending` | 这才是撤销排队中的建造 |

### 首轮实测暴露的两个真问题（已修）

第一次跑只有 **16/23**，7 条失败全是 `502 意图模型没有返回 JSON`。
日志显示两种不同的正文：

1. **`'{"kind":"dispatch'`** —— 正文被 `max_tokens=200` 截断。
2. **`''`（空字符串）** —— 推理模型把预算全花在思考上，没产出正文。

同一个根因：**200 token 预算太紧**。修法三条：

- `max_tokens` 200 → **1024**。一份意图 JSON 只有几十 token，多给的是留给思考的。
- `reasoning_effort=low`。意图解析是分类，不需要长思考链。
- 改用 `complete_raw` 拿 `finish_reason`，**把「截断」与「没产出正文」分开报**。
  之前两者都报"没有返回 JSON"，排查时分不清是预算问题还是模型问题。

第三处是提示词缺口：「把那份季度报告弄一下」被判成 `unknown`，
理由是"未明确是写、改还是提交"。但**具体类型本来就该由世界的关键词表归类，
不该由模型判断**。提示词补了「分不清细节 = 派活，不是 unknown」一节。

## 八、已知边界

- **两处提示词实现。** `intent-prompt.ts`（给测试与离线推演）与 `world_intent.py`
  的 `build_prompt`（线上）。两边都由模板表驱动，所以模板变了不会漏改；
  但**措辞改动要两边一起改**。彻底消除重复需要把提示词完全搬到服务端，
  代价是前端无法离线推演——这一版选了重复。
- **不做重试。** 重试会让"没听懂"变成"过一会儿突然做了"，比直接失败更困惑。
- **不带世界状态。** 模型只看到这句话与模板表，不知道镇上现在几栋楼、谁在忙。
  「建个工坊」若已有工坊，由世界在结算时拒绝并说清，不靠模型判断。
