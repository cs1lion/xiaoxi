# 造物主 JSON schema（L3 的边界）

- **状态**：已实现并有测试（`src/world/creator-schema.ts`，`test/creator-schema.test.ts` 19 项）
- **日期**：2026-10-22
- **上游**：`docs/CREATOR_GAP_ANALYSIS.md`、`docs/PIXEL_WORLD_ARCHITECTURE.md` §4

## 一、这份 schema 管什么、不管什么

**管**：模型输出是不是一份合法的造物**意图**（形状）。

**不管**：这个位置能不能建（空间）。迷雾、占地、留白、门口通行由 `checkPlacement` 判。

两道校验分开，因为它们回答不同的问题。混成一道的后果是错误信息说不清
「是 JSON 写错了」还是「这里放不下」。

## 二、形状

```json
{
  "templateId": "workshop_plot",
  "at": { "x": 32, "y": 12 },
  "palette": { "roof": "terracotta" }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `templateId` | 是 | 必须是 `prefabs.json` 里的 id。**不能发明新模板** |
| `at` | 是 | 整数格坐标，必须在世界范围内（80×60） |
| `palette` | 否 | 四个槽 `body / roof / trim / window`，值必须是 24 色表的 **token 名** |

**顶层只接受这三个字段。** 多写一个就拒——见下节。

## 三、四条硬规则

### 1. 未知字段直接拒绝，不静默忽略

模型多写 `"furniture": ["desk"]`，说明它以为世界有家具层。静默丢掉会让它
一直以为自己写对了，反馈回路断掉。所以：

```
未知字段 "furniture"：造物意图只接受 templateId / at / palette
```

### 2. 颜色只接受 token 名，拒绝十六进制

`"#ff00ff"` 会被拒。理由：24 色是已批准的 ADR，十六进制是绕开预算的后门。

这与 `blueprints.ts` **故意不同**：蓝图解析器接受 `#rrggbb` 旧格式（带警告）
以兼容历史数据；造物主是新的输入口，从一开始就不该允许。

### 3. 调色板可部分省略，其余回落到模板

只给 `{ "roof": "terracotta" }` 是合法的：`body` / `trim` / `window`
用模板自带的。完全省略 `palette` 也是合法的。

### 4. 位置只判范围，不判可建性

`at` 必须是整数、非负、在世界内。能不能建是下一步的事。

## 四、为什么没有 `slots`

`PIXEL_WORLD_ARCHITECTURE.md` §4 写的形状是
`{ templateId, slots, palette, weather }`，这份 schema **没有 `slots`**。

原因：世界**没有家具层**。建筑只有墙、屋顶、门、窗，内部不可见也没有陈设。
定义一套 `slots` 却没有任何东西能填，只会让模型输出一个被忽略的字段——
而那正是上面第 1 条要防的事。

等真有家具层时再加，那时它才有意义。

`weather` 也没有：天气是世界法则（`setWeather`），不是造物的一部分。
让造物顺便改天气是把两件事混在一个意图里。

## 五、玩家操作也走这条 schema

造物模式里玩家选的位置与模板，会先拼成同一份意图、过 `parseCreationPlan`，
再入队落位。这不是为了约束玩家，是为了让**手动造物与将来的 LLM 输出走同一条
校验**——schema 不成为只给 LLM 用的死代码，而是唯一的入口。

玩家的配色同样受 24 色约束，没有特权。

## 六、校验通过之后

```ts
const plan = parseCreationPlan(raw, blueprints, { width: 80, height: 60 });
const placed = placeFromPlan(world, plan, instanceId, blueprints);
if (placed === null) { /* 位置不行：告诉模型换个地方，不是重写 JSON */ }
```

`placeFromPlan` 把意图里的配色**覆盖**到模板上：模板只提供形状，颜色由造物主决定。

## 七、还没做的

| 项 | 状态 |
|---|---|
| 缓存（同一意图复用结果） | 未做。需要先有「什么算同一个意图」的定义 |
| 失败回退到模板 | 未做。`placeFromPlan` 失败返回 null，调用方决定怎么办 |
| 章节 / 频率限制 | 未做。理由见 `ADR_CREATION_MODE.md`：没有「章」的定义 |
| LLM 调用本身 | 未做。理由见 `CREATOR_GAP_ANALYSIS.md`：输入还不存在 |

## 八、测试覆盖

21 项：

- 合法输入：最小输入、调色板省略、部分省略
- 拒绝模板：未知 id、非字符串
- 拒绝位置：越界、小数、缺失
- 拒绝调色板：未知 token、十六进制、未知槽位
- 拒绝多余字段 + 错误类型统一
- 完整链路：意图 → 落位、配色生效、失败不占格
- 真实数据：现有 7 个模板全部通过 schema
- 玩家操作：手动造物的意图同样过 schema，同样受 24 色约束
